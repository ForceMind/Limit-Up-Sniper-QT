# 服务器部署与试运行

本项目当前分支已经收敛为“涨停狙击手”量化分析系统。服务端负责自动抓取新闻、同步行情、调用 AI 做事件分析、生成买入计划、模拟成交、展示持仓和交割单。

## 目录约定

建议部署到：

```bash
/opt/qt
```

核心目录：

```text
backend/app              后端 API 与量化引擎
backend/data             SQLite 主库、历史新闻、AI分析记录、K线兼容缓存、运行状态
frontend                 前台交易终端
frontend/admin           后台管理
qt.sh                    根目录统一命令入口
scripts                  安装、更新、备份、重启脚本
deploy/systemd           systemd 模板
deploy/nginx             Nginx 反代模板
```

`backend/data` 是生产数据目录，不能在更新时删除。里面会持续积累新闻、AI 结构化结果、分时 K 线、交易回放状态和自动任务状态。

## 环境配置

首次部署先复制环境变量模板：

```bash
cp .env.example .env
```

必须检查这些配置：

```bash
QUANT_HOST=0.0.0.0
QUANT_PORT=8000
QUANT_DATA_DIR=
QT_READ_LEGACY_KLINE_JSON_CACHE=false
DEEPSEEK_API_KEY=
DEEPSEEK_MODEL=deepseek-v4-flash
BIYING_ENABLED=true
BIYING_LICENSE_KEY=
BIYING_ENDPOINT=https://api.biyingapi.com
BIYING_MINUTE_LIMIT=3000
EMAIL_ENABLED=false
SMTP_SERVER=
SMTP_PORT=465
SMTP_USER=
SMTP_PASSWORD=
EMAIL_TO=
NEWS_FETCH_INTERVAL_SECONDS=3600
AI_ANALYSIS_INTERVAL_SECONDS=3600
MARKET_SYNC_INTERVAL_SECONDS=300
TRADE_CYCLE_INTERVAL_SECONDS=300
STRATEGY_REPLAY_ENABLED=false
STRATEGY_REPLAY_START_DATE=2026-03-01
STRATEGY_REPLAY_INTERVAL_SECONDS=3600
STRATEGY_REPLAY_MODE=intraday
QT_STRATEGY_REPLAY_BATCH_DAYS=15
QT_STRATEGY_REPLAY_MAX_MODELS=24
QT_SYSTEM_STARTUP_RUN_STRATEGY_REPLAY=false
STRATEGY_EVOLUTION_ENABLED=false
STRATEGY_EVOLUTION_INTERVAL_SECONDS=21600
STRATEGY_EVOLUTION_GENERATIONS=1
STRATEGY_EVOLUTION_POPULATION_SIZE=16
STRATEGY_EVOLUTION_MODE=intraday
STRATEGY_EVOLUTION_APPLY_BEST=false
DATA_BACKFILL_MAX_CODES=160
QT_MEMORY_GUARD_ENABLED=true
QT_MEMORY_GUARD_PERCENT=88
QT_MEMORY_GUARD_AVAILABLE_MB=1024
QT_KLINE_CACHE_MAX_CODES=480
QT_INTRADAY_CACHE_MAX_KEYS=120
QT_FACTOR_CACHE_MAX_ITEMS=6000
QT_FUTURE_RETURN_CACHE_MAX_ITEMS=12000
QT_LHB_CACHE_MAX_DATES=3
QT_LHB_FACTOR_LOOKBACK_DAYS=45
QT_LHB_FACTOR_MAX_ROWS=50000
QT_EVENTS_CACHE_MAX_ITEMS=30000
QT_REPLAY_HISTORY_EVENT_LIMIT=800
QT_STRATEGY_EVOLUTION_MAX_GENERATIONS=8
QT_STRATEGY_EVOLUTION_MAX_POPULATION=32
TRADING_HOLIDAYS=
TRADING_EXTRA_DAYS=
```

服务端优先读取 `.env`，再读取运行数据目录里的 `config.json`。`QUANT_DATA_DIR` 留空时使用 `backend/data`；如果生产数据放在单独磁盘或从旧项目迁移，可以把它指向对应的 `backend/data` 目录。生产服务器不要提交 `.env`。

如果从旧部署迁移，确认 `.env` 里已经改成：

```bash
QUANT_APP_DIR=/opt/qt
QUANT_SERVICE_NAME=qt
```

## 一键安装

```bash
cd /opt/qt
bash qt.sh install
```

脚本会执行：

- 创建 `.venv`
- 安装 `backend/requirements.txt`
- 创建 `backend/data` 和备份目录
- 如果服务器支持 systemd，会自动安装并启动 `${QUANT_SERVICE_NAME:-qt}` 服务

如果不使用 systemd，可手动启动：

```bash
bash scripts/restart_server.sh
```

## 更新发布

```bash
cd /opt/qt
bash qt.sh
```

`bash qt.sh` 等同于 `bash qt.sh update`；服务器快捷命令 `qt` 面板里的 `1) 一键更新部署` 也走同一套流程。它会先备份当前运行数据目录，再执行 `git pull --ff-only`、更新依赖、按需把 JSON/CSV 运行数据合并进 SQLite、验证关键数据表，并重启服务。默认 `QT_AUTO_MIGRATE_MODE=smart`，首次、数据库缺失或迁移脚本变化时才全量迁移；如需强制迁移执行 `qt migrate` 或 `QT_FORCE_AUTO_MIGRATE=1 qt update`，如需临时跳过设置 `QT_SKIP_AUTO_MIGRATE=true`。

## 重启服务

```bash
bash qt.sh restart
```

如果 systemd 服务存在，脚本会走 `systemctl restart`；否则会使用 `nohup` 后台启动，并写入：

```text
backend/data/qt.pid
backend/data/qt.out.log
backend/data/qt.err.log
```

后台网页里的“重启服务”默认是关闭的。如果看到“后台重启被拦截：服务器未启用 API 重启”，说明服务器 `.env` 没有设置 `QUANT_ALLOW_API_RESTART=1`。这是安全保护，避免任何拿到后台会话的人都能通过 HTTP 重启生产服务。推荐仍使用 SSH 执行 `qt restart` 或 `bash qt.sh restart`；只有在确认后台入口、账号和调试通道都受控时，才临时开启 `QUANT_ALLOW_API_RESTART=1` 并重启服务让配置生效。

## 服务器 qt 命令

根目录只需要记一个入口：`qt.sh`。

底层的安装、更新、备份、恢复、重启脚本仍放在 `scripts/`，原因是：

- 根目录应该只保留项目入口、配置模板和文档，避免运维脚本散落在最外层。
- `scripts/` 是脚本实现目录，方便维护、复用和权限管理。
- `qt.sh` 是人用入口，负责把命令转发给 `scripts/qt.sh`；`scripts/qt.sh` 提供中文帮助、步骤日志、状态查看和部署环境检查。

`install_server.sh` 会尽量把 `scripts/qt.sh` 安装为 `/usr/local/bin/qt`。安装成功后，可以直接使用：

```bash
qt
qt status
qt auth
qt doctor
qt restart
qt update
qt backup
qt logs
qt scan
qt data-audit
qt data-audit --fix-permissions
qt architecture
qt debug-status
qt debug-key
qt debug-on
qt debug-off
```

直接输入 `qt` 会打开中文交互式运维面板，可执行更新、重启、停止、日志、备份、恢复、安全扫描、服务器数据安全体检、项目架构体检和账号密码管理。`qt auth` 会直接进入账号密码管理，可以初始化、修改后台账号、修改前台账号，或删除认证文件回到网页首次初始化。

如果服务器没有 sudo/root 权限，可以手动创建软链接：

```bash
sudo ln -sf /opt/qt/scripts/qt.sh /usr/local/bin/qt
```

## 临时调试通道

需要远程协助排查服务器接口时，可以临时开启专用调试密钥。它不复用后台账号密码，默认只能读接口，所有请求仍会写入访问日志。

在服务器执行：

```bash
qt debug-key
```

把脚本输出的 `QT_DEBUG_API_ENABLED=true`、`QT_DEBUG_API_KEY_SHA256=...`、`QT_DEBUG_API_ALLOW_WRITE=false`、`QT_DEBUG_API_SUBJECT=codex-debug` 写入服务器 `.env`，然后重启。也可以直接执行 `qt debug-on` 自动写入 `.env`，但原始密钥仍只会在终端显示一次。

```bash
qt restart
```

调试请求使用请求头：

```bash
curl -H "X-QT-Debug-Key: 原始密钥" https://qt.zhangting.ai/api/debug/status
```

默认 `QT_DEBUG_API_ALLOW_WRITE=false`，只能访问 `GET`/`HEAD`/`OPTIONS` 接口。确实需要测试写接口时，临时改成 `true`，测试完成后立刻改回 `false`。完全关闭调试通道时执行：

```bash
qt debug-off
qt restart
```

查看当前开关状态：

```bash
qt debug-status
```

## 备份与恢复

备份：

```bash
bash qt.sh backup
```

恢复：

```bash
bash qt.sh restore /path/to/backend_data_YYYYmmdd_HHMMSS.tar.gz
```

恢复脚本会先自动备份当前 `backend/data`，再替换为指定备份。

## Nginx 反向代理

模板文件：

```bash
deploy/nginx/qt.conf
```

典型做法：

```bash
sudo cp deploy/nginx/qt.conf /etc/nginx/sites-available/qt.conf
sudo ln -s /etc/nginx/sites-available/qt.conf /etc/nginx/sites-enabled/qt.conf
sudo nginx -t
sudo systemctl reload nginx
```

数据包上传会经过 Nginx，配置里必须允许足够大的请求体。项目模板默认：

```nginx
client_max_body_size 1024m;
proxy_request_buffering off;
proxy_send_timeout 1800;
proxy_read_timeout 1800;
```

如果上传时报 `413 Request Entity Too Large`，说明当前服务器实际生效的 Nginx 上传大小太小；如果报 `504 Gateway Time-out`，说明 Nginx 等后端响应超时。上传或导入场景先执行：

```bash
qt nginx-upload
sudo nginx -T | grep -nE "client_max_body_size|proxy_read_timeout|proxy_send_timeout"
```

`qt install` 和 `qt update` 也会自动尝试把指向本服务端口的正式 Nginx 配置更新为 `QT_NGINX_UPLOAD_MAX_SIZE` 和 `QT_NGINX_PROXY_TIMEOUT`，默认 `1024m` 和 `1800` 秒。脚本会跳过 `.qt_upload_backup_*` 历史备份文件，并默认清理 7 天前或重复嵌套的上传配置备份。

v0.2.25 起，后台页面手动触发新闻抓取、AI 分析、行情同步、日K补齐、龙虎榜同步、交易循环、策略复盘和系统启动时，接口默认只提交后台任务并立即返回。普通任务按钮如果仍反复出现 504，优先确认已经执行 `qt update` 或 `qt restart` 让新版后端生效，再查看后台任务日志和服务日志。

v0.2.30 起，后台手动触发的策略复盘和策略进化默认使用独立 Python 子进程运行。API 进程只负责鉴权、记录任务状态和启动子进程；子进程继续写入 `quant_job_state.json` 和运行日志。需要临时回到线程模式时，可在接口上显式传 `process=false`，或设置 `QT_HEAVY_JOB_PROCESS_ENABLED=false` 后重启。

v0.2.31 起，`/api/jobs/status` 会自动巡检独立进程任务。如果子进程已经退出但没有写回完成状态，任务会被标记为失败并写入日志，后台按钮不会永久停在“暂停/运行中”。刚启动进程有默认 8 秒宽限期，可用 `QT_JOB_PROCESS_START_GRACE_SECONDS` 调整。

v0.2.32 起，前台账户会优先读取 `user_follow_snapshots`。如果用户跟随快照不存在，后端会从 `strategy_runtime_*` 运行表、短缓存、模型记录或即时回放派生账户，并写入 `user_follow_positions`、`user_follow_trades`。该快照默认最多缓存 86400 秒，可用 `QT_USER_FOLLOW_ACCOUNT_CACHE_TTL_SECONDS` 调整；接口传 `force=true` 会跳过用户跟随快照并重新派生。

v0.2.33 起，用户注册、设置模拟资金或切换策略会写入 `user_follow_periods`。后台“用户管理”页会显示用户当前跟随周期、账户快照、持仓和最近成交来源；如果账户快照为空，先让该用户打开前台账户页，或用前台账户接口带 `force=true` 生成一次。

v0.2.48 起，前台推荐和日计划缓存未命中时默认触发 `frontend_payload_precompute` 后台任务，接口返回 pending，不再同步等待慢计算。后台“运维”页可以手动执行“预计算前台缓存”；调度器开启时也会按 `QT_FRONT_PAYLOAD_PRECOMPUTE_INTERVAL_SECONDS` 自动预计算。默认使用独立进程，可用 `QT_FRONT_PAYLOAD_PRECOMPUTE_PROCESS_ENABLED=false` 临时改回线程模式；如需让未命中接口恢复同步计算，可设置 `QT_FRONT_PAYLOAD_DEFER_MISSES=false` 后重启。

v0.2.49 起，策略复盘、模型训练和回测默认只手动触发。`STRATEGY_REPLAY_ENABLED=false` 时自动调度器不会跑策略复盘；`QT_SYSTEM_STARTUP_RUN_STRATEGY_REPLAY=false` 时后台“系统启动”只执行新闻、AI、补数、行情和模拟交易，不会顺手跑复盘。策略库查看交割单默认读取已保存模型记录，只有点击“手动重新回测”才会实时重算。日常生产建议保持这两个值为 false，只在需要更新模型运行结果时手动点击“运行策略复盘”或“启动进化”。

v0.2.50 起，前台和后台不会再把 Cloudflare 或 Nginx 返回的整页 HTML 错误页原样展示给用户。看到 `源站网关错误（502）` 时，含义是浏览器和 Cloudflare 边缘节点已经连通，但源站主机没有返回可用响应，通常是后端服务未启动、正在重启、监听端口和 Nginx 上游不一致、进程被重任务或内存压力杀掉，或 Nginx 无法连接 API。优先在服务器执行：

```bash
qt status
qt logs
curl -i http://127.0.0.1:${QUANT_PORT:-8000}/api/version
sudo nginx -t
sudo systemctl status ${QUANT_SERVICE_NAME:-qt}
```

如果本机 `curl` 也连不上，先重启后端：`qt restart`；如果本机可连但公网 502，重点检查 Nginx upstream、端口、防火墙、Cloudflare TLS 模式和服务日志。

v0.2.29 起，后台上传合并数据包时，`quant_data.sqlite3` 不再一次性读入内存，而是流式写入临时 SQLite 后再合并。58MB 这类压缩包本身没有超过上传限制；如果旧版本合并失败，先更新到 v0.2.29 再重新上传。

如果只是迁移本地跑好的策略运行结果，优先使用 `python scripts/package_strategy_runtime_export.py` 生成小包。该小包只包含 `strategy_runtime_*` 运行表，不包含新闻、行情、K 线 JSON 和账号配置，适合把本地资金档复盘结果合并到服务器。

正式域名和 TLS 证书按服务器实际情况调整。

## 页面入口

前台交易终端：

```text
http://服务器IP:8000/
```

后台管理：

```text
先执行 qt admin-path 或 bash qt.sh admin-path 查看随机后台入口，例如 http://服务器IP:8000/admin-a1b2c3d4
```

首次部署会自动生成随机后台入口，固定 `/admin` 不再公开。首次打开后台会进入初始化页，需要创建两个账号：后台管理员账号和前台交易终端账号。后台账号用于配置密钥、触发任务和运维操作；前台账号只用于查看交易终端。账号哈希保存在 `backend/data/auth.json`，运行配置保存在 `backend/data/config.json`，二者都属于服务器本地文件，不要提交到 Git。

部署后如果页面仍显示样例数据，说明生产数据链路还没有跑起来。先在后台“配置与安全”填写 DeepSeek、必盈、邮件等服务器本地配置，再到“运维”点击“系统启动”。该按钮会按顺序执行新闻抓取、AI 分析、行情同步和交易循环；策略复盘、模型训练和回测需要单独手动触发，运行日志会在右侧日志栏显示中文状态。

检查服务器是否已经保留从 3 月开始的新闻和行情：

```bash
python scripts/check_data_coverage.py
python scripts/check_data_coverage.py /path/to/other/backend/data
```

如果 `news_history.json` 最早日期晚于 `2026-03-01`，服务器没有完整的 3 月以来新闻。此时策略复盘会运行，但只能用已有新闻和行情样本，结果不能代表 3 月以来完整表现。

把旧数据目录整理进 SQLite：

```bash
python scripts/migrate_data_to_sqlite.py --source /path/to/old/backend/data --db backend/data/quant_data.sqlite3
```

`backend/data/quant_data.sqlite3` 属于服务器本地运行数据，不提交 Git。迁移脚本会创建策略运行和用户跟随账户相关表，并导入新闻、AI 缓存、结构化事件、行情、模拟账户、策略进化、访问日志和任务日志；它不会导入账号、密钥和运行配置。日 K 的长期主存储是 SQLite 的 `market_daily_bars` 表，旧的 `kline_day_cache/*.json` 只作为兼容缓存和迁移来源。

`qt install` 和 `qt update` 会执行智能迁移逻辑。手动 `qt migrate` 用于强制重新合并当前数据目录里的 JSON/CSV，或者在服务器外单独整理数据包。

迁移服务器时优先使用后台页面，不要通过 GitHub 上传真实数据：

```text
旧服务器 后台入口 -> 运维 -> 下载数据包
新服务器 后台入口 -> 运维 -> 上传合并数据
```

导入前新服务器会自动备份当前 `backend/data`。导入不是整包覆盖，而是按数据类型去重合并；上传包可以只包含新闻、只包含行情或只包含 SQLite。账号、密钥和运行配置不会被导入。如果需要从 Windows 本机直接推送到服务器，也可以在项目根目录运行：

如果旧服务曾经用样例数据跑出持仓，上传后仍显示“样例算力”，在后台 `运维` 点击 `清理样例持仓`，再执行 `立即AI分析` 和 `运行交易循环`。

```powershell
.\upload-data.ps1 -Server root@服务器IP
```

如果服务器项目目录不是 `/root/Limit-Up-Sniper-QT`：

```powershell
.\upload-data.ps1 -Server root@服务器IP -RemoteDir /你的项目目录
```

脚本会自动整理 SQLite、生成数据包、上传、服务器备份、解压、重启和检查数据覆盖。这个安全数据包只包含新闻、AI/事件、行情、策略状态和 SQLite，不包含 `.env`、`config.json`、`auth.json`、`admin_credentials.json`、`admin_sessions.json`、`ws_token_secret.txt`。

## 健康检查

```bash
curl http://127.0.0.1:8000/api/auth/status
curl -H "Authorization: Bearer $QT_ADMIN_TOKEN" http://127.0.0.1:8000/api/status
curl -H "Authorization: Bearer $QT_ADMIN_TOKEN" http://127.0.0.1:8000/api/jobs/status
curl -H "Authorization: Bearer $QT_ADMIN_TOKEN" http://127.0.0.1:8000/api/config/status
```

手动触发任务：

```bash
curl -H "Authorization: Bearer $QT_ADMIN_TOKEN" -X POST "http://127.0.0.1:8000/api/jobs/news/fetch?hours=12&pages=5&page_size=20"
curl -H "Authorization: Bearer $QT_ADMIN_TOKEN" -X POST "http://127.0.0.1:8000/api/jobs/ai/analyze?max_items=8&batch_size=4"
curl -H "Authorization: Bearer $QT_ADMIN_TOKEN" -X POST "http://127.0.0.1:8000/api/jobs/market/sync?source=auto&max_codes=80&include_latest=true"
curl -H "Authorization: Bearer $QT_ADMIN_TOKEN" -X POST "http://127.0.0.1:8000/api/jobs/trading/run?notify=true"
curl -H "Authorization: Bearer $QT_ADMIN_TOKEN" -X POST "http://127.0.0.1:8000/api/quant/evolve_strategy?generations=4&population_size=16&apply_best=false"
```

## 自动任务

服务启动后会启动后台任务管理器：

- 新闻抓取：24 小时运行，默认每 1 小时抓取一次财联社电报并合并到 `news_history.json`
- AI 分析：默认每 1 小时增量调用 DeepSeek，将新闻结构化为事件、行业、个股、利好利空和影响强度
- 行情同步：仅交易日 09:30-11:30、13:00-15:00 使用必盈接口补充分时 K 线；日 K 补齐使用必盈历史行情并写入 SQLite；周末和非开盘时间不触发盘中行情同步
- 模拟交易：按当前模型触发买入/卖出，触发后可通过 SMTP 发送邮件
- 策略复盘：默认只手动触发；手动运行时可从 `2026-03-01` 开始按 `QT_STRATEGY_REPLAY_BATCH_DAYS` 分批推进，默认每批 15 天，并用 `strategy_replay_cursor` 记录下一批起点；v0.2.24 起会按资金档预设和策略库模型批量写入 `strategy_daily_signals`、`strategy_runtime_trades`、`strategy_runtime_positions`、`strategy_runtime_snapshots`、`strategy_runtime_settlements`，可用 `QT_STRATEGY_REPLAY_MAX_MODELS` 限制单轮最多复盘策略数
- 遗传进化：多组参数并行回放，按收益、回撤、胜率选择最优参数，后台可手动应用
- 模型回放：前台和后台按全周期线性回放计算买入、卖出、收益和交割单

后台管理页可以查看任务状态，也可以手动触发新闻、AI、行情同步。手动触发的慢任务默认进入后台运行，页面通过任务状态、进度和日志确认完成情况；需要同步等待旧行为时，可在接口上显式传 `background=false`。

## 生产注意事项

- `.env` 和 `backend/data/config.json` 里可能包含接口密钥，服务器权限要收紧。
- 更新前必须保留 `backend/data`；不要用空目录覆盖生产数据。
- 先在测试服务器跑通新闻、AI、行情同步，再开放公网访问。
- 当前系统是策略研究和模拟盘系统，不应直接作为真实交易下单系统使用。
