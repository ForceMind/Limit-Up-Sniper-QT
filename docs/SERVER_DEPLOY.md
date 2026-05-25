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
QT_NEWS_FETCH_PROCESS_ENABLED=true
AI_ANALYSIS_INTERVAL_SECONDS=3600
QT_AI_ANALYSIS_PROCESS_ENABLED=true
MARKET_SYNC_INTERVAL_SECONDS=300
QT_MARKET_SYNC_PROCESS_ENABLED=true
QT_KLINE_FILL_PROCESS_ENABLED=true
QT_LHB_SYNC_PROCESS_ENABLED=true
TRADE_CYCLE_INTERVAL_SECONDS=300
QT_TRADE_CYCLE_PROCESS_ENABLED=true
STRATEGY_REPLAY_ENABLED=false
STRATEGY_REPLAY_START_DATE=2026-03-01
STRATEGY_REPLAY_INTERVAL_SECONDS=3600
STRATEGY_REPLAY_MODE=intraday
QT_RESEARCH_TASKS_MANUAL_ONLY=true
QT_STRATEGY_REPLAY_PROCESS_ENABLED=true
QT_STRATEGY_REPLAY_BATCH_DAYS=15
QT_STRATEGY_REPLAY_MAX_MODELS=24
QT_SYSTEM_STARTUP_PROCESS_ENABLED=true
QT_SYSTEM_STARTUP_RUN_STRATEGY_REPLAY=false
STRATEGY_EVOLUTION_ENABLED=false
STRATEGY_EVOLUTION_INTERVAL_SECONDS=21600
STRATEGY_EVOLUTION_GENERATIONS=1
STRATEGY_EVOLUTION_POPULATION_SIZE=16
STRATEGY_EVOLUTION_MODE=intraday
STRATEGY_EVOLUTION_APPLY_BEST=false
QT_STRATEGY_EVOLUTION_PROCESS_ENABLED=true
QT_FRONT_ACCOUNT_DEFER_MISSES=true
QT_FRONT_ACCOUNT_PERSIST_ON_READ=false
QT_FRONT_ACCOUNT_MODEL_RECORDS_FALLBACK=false
QT_FRONT_PAYLOAD_PRECOMPUTE_ENABLED=false
QT_FRONT_PAYLOAD_AUTO_PRECOMPUTE_ON_MISS=false
QT_FRONT_PAYLOAD_PRECOMPUTE_PROCESS_ENABLED=true
QT_FRONT_PAYLOAD_PRECOMPUTE_INTERVAL_SECONDS=1800
QT_FRONT_PAYLOAD_PRECOMPUTE_INITIAL_DELAY_SECONDS=1800
QT_FRONT_PAYLOAD_PRECOMPUTE_LIMIT_USERS=8
QT_FRONT_PAYLOAD_PRECOMPUTE_MAX_SECONDS=20
QT_FRONT_RECOMMENDATIONS_CACHE_TTL_SECONDS=1800
QT_FRONT_DAILY_PLAN_CACHE_TTL_SECONDS=1800
QT_FRONT_ACCOUNT_AUTO_PRECOMPUTE_ENABLED=true
QT_FRONT_ACCOUNT_PRECOMPUTE_QUEUE_ASYNC_ON_PROFILE=true
QT_FRONT_ACCOUNT_PRECOMPUTE_ASYNC_WORKERS=4
QT_FRONT_ACCOUNT_PRECOMPUTE_ASYNC_DEBOUNCE_SECONDS=5
QT_FRONT_ACCOUNT_PRECOMPUTE_ASYNC_DISPATCH_DELAY_MS=25
QT_FRONT_ACCOUNT_START_WORKER_ON_PROFILE=false
QT_FRONT_ACCOUNT_PRECOMPUTE_PROCESS_ENABLED=true
QT_FRONT_ACCOUNT_PRECOMPUTE_LIMIT=160
QT_FRONT_ACCOUNT_PRECOMPUTE_QUEUE_BATCH_USERS=50
QT_FRONT_ACCOUNT_PRECOMPUTE_QUEUE_MAX_USERS=500
QT_FRONT_ACCOUNT_PRECOMPUTE_QUEUE_MAX_BATCHES=20
QT_FRONT_ACCOUNT_PRECOMPUTE_QUEUE_IDLE_GRACE_MS=500
QT_FRONT_ACCOUNT_PRECOMPUTE_QUEUE_LOCK_TIMEOUT_MS=5000
QT_FRONT_ACCOUNT_PRECOMPUTE_QUEUE_LOCK_STALE_MS=30000
QT_FRONT_JOBS_CACHE_TTL_SECONDS=3
QT_FRONT_SNAPSHOT_NEWS_CACHE_TTL_SECONDS=30
QT_FRONT_SNAPSHOT_LIGHT_NEWS_NO_ENGINE_FALLBACK=true
QT_LATEST_NEWS_TIME_CACHE_TTL_SECONDS=5
QT_DATA_DATE_CACHE_TTL_SECONDS=10
QT_DATA_DATE_ENGINE_FALLBACK_ENABLED=false
QT_DATA_COVERAGE_DEFER_MISSES=true
QT_DATA_COVERAGE_CACHE_TTL_SECONDS=60
QT_DATA_COVERAGE_PROCESS_ENABLED=true
QT_MODEL_BACKTEST_DEFER_RECOMPUTE=true
QT_MODEL_BACKTEST_CACHE_TTL_SECONDS=600
QT_TIMELINE_DEFER_MISSES=true
QT_TIMELINE_PROCESS_ENABLED=true
QT_TIMELINE_REQUIRE_MANUAL_TRIGGER=true
QT_TIMELINE_CACHE_TTL_SECONDS=600
QT_BACKTEST_DEFER_MISSES=true
QT_BACKTEST_PROCESS_ENABLED=true
QT_BACKTEST_CACHE_TTL_SECONDS=600
QT_FIT_STRATEGY_DEFER_MISSES=true
QT_FIT_STRATEGY_PROCESS_ENABLED=true
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

v0.2.48 起，前台推荐和日计划缓存未命中时可以转入 `frontend_payload_precompute` 后台任务，接口返回 pending，不再同步等待慢计算。v0.2.129 起生产默认不再自动排队；只有同时开启 `QT_FRONT_PAYLOAD_PRECOMPUTE_ENABLED=true` 和 `QT_FRONT_PAYLOAD_AUTO_PRECOMPUTE_ON_MISS=true` 时，前台缓存未命中才会自动启动预计算。后台“运维”页仍可手动执行“预计算前台缓存”；调度器开启时会按 `QT_FRONT_PAYLOAD_PRECOMPUTE_INTERVAL_SECONDS` 自动预计算，v0.2.135 起首次运行由 `QT_FRONT_PAYLOAD_PRECOMPUTE_INITIAL_DELAY_SECONDS` 控制，默认 1800 秒。默认使用独立进程，可用 `QT_FRONT_PAYLOAD_PRECOMPUTE_PROCESS_ENABLED=false` 临时改回线程模式；如需让未命中接口恢复同步计算，可设置 `QT_FRONT_PAYLOAD_DEFER_MISSES=false` 后重启。

v0.2.49 起，策略复盘、模型训练和回测默认只手动触发。`STRATEGY_REPLAY_ENABLED=false` 时自动调度器不会跑策略复盘；v0.2.135 起建议保持 `QT_RESEARCH_TASKS_MANUAL_ONLY=true`，即使误开 `STRATEGY_REPLAY_ENABLED` 或 `STRATEGY_EVOLUTION_ENABLED`，调度器也不会自动启动研究重任务。`QT_SYSTEM_STARTUP_RUN_STRATEGY_REPLAY=false` 时后台“系统启动”只执行新闻、AI、补数、行情和模拟交易，不会顺手跑复盘。策略库查看交割单默认读取已保存模型记录，只有点击“手动重新回测”才会实时重算。日常生产建议保持这些值为保守默认，只在需要更新模型运行结果时手动点击“运行策略复盘”或“启动进化”。

v0.2.50 起，前台和后台不会再把 Cloudflare 或 Nginx 返回的整页 HTML 错误页原样展示给用户。v0.2.136 起，前端 API helper 对 2xx 响应也会先识别 HTML/非 JSON，再解析业务 JSON，避免代理误把错误页或静态首页以 200 返回时触发不友好的解析异常。看到 `源站网关错误（502）` 时，含义是浏览器和 Cloudflare 边缘节点已经连通，但源站主机没有返回可用响应，通常是后端服务未启动、正在重启、监听端口和 Nginx 上游不一致、进程被重任务或内存压力杀掉，或 Nginx 无法连接 API。优先在服务器执行：

```bash
qt status
qt logs
curl -i http://127.0.0.1:${QUANT_PORT:-8000}/api/version
sudo nginx -t
sudo systemctl status ${QUANT_SERVICE_NAME:-qt}
```

如果本机 `curl` 也连不上，先重启后端：`qt restart`；如果本机可连但公网 502，重点检查 Nginx upstream、端口、防火墙、Cloudflare TLS 模式和服务日志。

v0.2.52 起，前台用户在策略页点击“跟随”时只保存 profile 和跟随周期，不再同步等待账户重建，也不再用 `force=true` 绕过缓存。持仓和成交页打开时会优先按 `user_follow_snapshots`、`strategy_runtime_*`、SQLite 账户缓存读取；如果服务器没有预先跑出策略运行结果，会显示待生成提示，所以生产环境应先手动运行策略复盘或导入本地策略运行小包。

v0.2.53 起，前台账户接口默认 `QT_FRONT_ACCOUNT_DEFER_MISSES=true`。当 `user_follow_snapshots`、`strategy_runtime_*`、SQLite 账户缓存和模型交割单都缺失时，接口返回 `pending` 和中文提示，不再同步触发 `walk_forward` 即时回放。生产环境应保持该值为 true；只有本地排查或明确需要同步重算时，才临时请求 `/api/front/trading_account?force=true` 或设置 `QT_FRONT_ACCOUNT_DEFER_MISSES=false` 后重启。

v0.2.54 起，前台会把上述 `pending` 账户明确显示为“账户运行结果待生成”，持仓、成交和交割页不会再显示成普通“暂无持仓/暂无成交”。如果用户看到该提示，优先在后台手动运行策略复盘，或用 `python scripts/package_strategy_runtime_export.py` 从本地导入已跑好的策略运行结果小包。

v0.2.55 起，前台账户接口默认 `QT_FRONT_ACCOUNT_MODEL_RECORDS_FALLBACK=false`，不会在普通用户请求中读取完整 `strategy_model_records` 并同步派生账户。策略模型历史交割单仍可在后台策略库查看；只有本地排查时才临时开启该开关，或显式使用 `force=true`。

v0.2.56 起，后台“运维”页新增“预热用户账户”，也可调用：
```bash
curl -X POST -H "Authorization: Bearer $QT_ADMIN_TOKEN" "http://127.0.0.1:8000/api/jobs/frontend/account_precompute?background=true&process=true&force=false&limit_users=50&limit=160"
```
建议在手动策略复盘完成、或导入 `strategy_runtime_*` 运行结果小包之后执行一次。该任务默认只读取已有用户快照、策略运行表和账户缓存来生成用户账户快照，不触发即时回放或完整模型交割单兜底；生产环境不要把 `force=true` 当作日常预热方式。

v0.2.57 起，账户预热默认受 `QT_FRONT_ACCOUNT_PRECOMPUTE_PROCESS_ENABLED=true` 控制转入独立 Python 子进程执行。保持该值为 true，可以避免批量用户账户派生占用 API 进程；只有本地调试时才建议临时传 `process=false`。

v0.2.58 起，用户注册、切换策略或调整模拟资金后，后端会在保存 profile 后自动为该用户排队一次账户预热。该行为由 `QT_FRONT_ACCOUNT_AUTO_PRECOMPUTE_ENABLED=true` 控制，单次默认只处理当前用户，返回给前台的保存接口不会等待账户生成完成。

v0.2.59 起，保存 profile 返回的账户预热结果会区分 `queued` 和 `worker_started`。v0.2.60 后，`account_precompute_queued=true` 表示用户已进入待处理队列；`worker_started=true` 表示本次请求同时启动了独立进程或后台线程。

v0.2.60 起，自动账户预热会先写入运行数据文件 `backend/data/frontend_account_precompute_queue.json`，再由 `frontend_account_precompute` 独立进程批量消费。这样同名预热任务正在运行时，后续用户切换策略不会丢失；该 JSON 是服务器运行状态，不要提交 Git。

v0.2.61 起，账户预热队列读写会使用 `backend/data/frontend_account_precompute_queue.lock` 做跨进程互斥。默认等待锁 5 秒，锁文件超过 30 秒会被视为陈旧锁并自动恢复；对应环境变量是 `QT_FRONT_ACCOUNT_PRECOMPUTE_QUEUE_LOCK_TIMEOUT_MS` 和 `QT_FRONT_ACCOUNT_PRECOMPUTE_QUEUE_LOCK_STALE_MS`。

v0.2.62 起，前台账户接口会在返回账户时检查预热队列是否有残留。如果有，会快速尝试启动 `drain_queue=true` 的账户预热 worker；这不会在用户请求里同步回放，只负责唤起后台消费。

v0.2.63 起，生产默认 `QT_FRONT_ACCOUNT_START_WORKER_ON_PROFILE=false`：注册、切换策略或调整模拟资金时，profile 保存接口只写入账户预热队列并立即返回，不再同步启动或巡检 worker。这样前台切换策略更接近 O(1)；队列会在用户进入账户页时由 `/api/front/trading_account` 自愈唤起，或由后台“前台账户预热”手动任务消费。只有本地排查自动预热链路时，才建议临时设为 `true`。

v0.2.64 起，后台任务状态接口会返回 `frontend_account_precompute_queue`，包含当前排队数量、排队原因、最早/最新排队时间和锁状态。线上排查“切换策略后账户一直待生成”时，先看后台任务卡片：队列数量持续增长说明 worker 没有及时消费；锁显示陈旧时可触发账户页刷新或后台“预热用户账户”让锁恢复逻辑介入。

v0.2.65 起，后台“预热用户账户”会优先处理 `frontend_account_precompute_queue.json` 里的排队用户；队列为空时才按普通批量用户预热。接口层同样如此：未显式传 `drain_queue` 且队列非空时，`/api/jobs/frontend/account_precompute` 会自动按 `drain_queue=true` 启动。若排查时必须跳过队列，可显式传 `drain_queue=false`。

v0.2.66 起，用户切换策略保存 profile 时不再清空全局内存缓存，前台快照缓存键会包含跟随开始时间。线上如果仍然觉得切换策略慢，优先看 `/api/front/profile` 和 `/api/front/trading_account` 的耗时，而不是先重启服务；切换策略本身应该只是写 profile、记录跟随周期、写入账户预热队列。

v0.2.66 起，数据覆盖率诊断默认 `QT_DATA_COVERAGE_DEFER_MISSES=true`。`/api/data/coverage` 缓存未命中时会返回 `pending` 并启动 `data_coverage` 后台任务；需要人工同步刷新时才临时调用 `?force=true`。如果后台“数据与AI”页首次打开看到覆盖率待生成，稍后刷新即可读取缓存。

v0.2.67 起，单模型回测重算默认 `QT_MODEL_BACKTEST_DEFER_RECOMPUTE=true`。后台“重新回测/后台重算选中策略”会让 `/api/quant/model/backtest?recompute=true` 返回 `pending` 并启动 `model_backtest` 任务，完成后短缓存 10 分钟。只有本地排查才建议调用 `?recompute=true&defer=false` 同步等待。

v0.2.68 起，通用时间线回测默认 `QT_TIMELINE_DEFER_MISSES=true`。v0.2.135 起生产建议保持 `QT_TIMELINE_REQUIRE_MANUAL_TRIGGER=true`：`/api/quant/timeline` 和 `/api/quant/intraday_timeline` 缓存未命中时普通刷新只返回 `manual_required`，不会自动启动 `quant_timeline`；后台或本地排查必须显式传 `manual=true` 才会排队，且默认通过 `QT_TIMELINE_PROCESS_ENABLED=true` 进入独立进程。只有本地排查才建议再叠加 `defer=false` 同步等待。

v0.2.69 起，`POST /api/front/profile` 默认 `include_catalog=false`。切换资金档策略时，后端只用轻量资金档目录定位当前策略，不再强制读取完整策略模型目录；前台保存后也不自动请求 `/api/front/trading_account`，只显示待预热账户状态。线上排查“切换策略慢”时，先分别看 profile 保存耗时、账户页刷新耗时和 `frontend_account_precompute_queue` 是否积压。

v0.2.70 起，通用 `/api/quant/backtest` 默认 `QT_BACKTEST_DEFER_MISSES=true` 且 `QT_BACKTEST_PROCESS_ENABLED=true`。缓存未命中时接口返回 `pending`，并启动 `quant_backtest` 独立 Python 子进程把结果写入 `frontend_payload_cache` 短缓存。只有本地排查才建议调用 `?defer=false` 同步等待；线上不应让普通回测请求占住 API 进程。

v0.2.71 起，切换到策略库模型时，轻量 profile 保存会按模型 ID 单条读取策略参数，不再为了定位当前策略加载完整策略目录。线上如果仍慢，优先检查是否有 `frontend_payload_precompute`、`frontend_account_precompute`、`strategy_replay` 或 `strategy_evolution` 进程抢占 CPU，而不是把 profile 保存当作回测链路排查。

v0.2.72 起，后台“参数拟合”调用 `/api/quant/fit_strategy` 默认 `QT_FIT_STRATEGY_DEFER_MISSES=true` 且 `QT_FIT_STRATEGY_PROCESS_ENABLED=true`。该接口会立即返回 `pending` 并启动 `fit_strategy` 独立 Python 子进程；如果选择“拟合并应用”，应用动作在子进程完成后写入服务器状态文件。只有本地排查才建议调用 `?defer=false` 同步等待。

v0.2.73 起，后台“同步分时行情”和 `/api/data/biying/sync_intraday` 默认 `QT_MARKET_SYNC_PROCESS_ENABLED=true`。接口立即返回 `market_sync` 任务启动状态，实际必盈分时抓取在独立 Python 子进程里执行。只有本地排查小样本时才建议传 `process=false&background=false` 同步等待；线上大批量补分时不要占用 API 进程。

v0.2.74 起，后台“补齐缺失日K”和“拉取龙虎榜”默认 `QT_KLINE_FILL_PROCESS_ENABLED=true`、`QT_LHB_SYNC_PROCESS_ENABLED=true`。接口立即返回 `kline_fill` 或 `lhb_sync` 任务启动状态，实际补数在独立 Python 子进程里执行。系统启动流程内部仍按顺序执行这些步骤，避免一键准备流程在子任务未完成时继续后续步骤。

v0.2.75 起，后台“运行模拟交易”、`/api/jobs/daily/run` 和自动调度交易循环默认 `QT_TRADE_CYCLE_PROCESS_ENABLED=true`。接口或调度器只启动 `trade_cycle` 独立 Python 子进程，实际买入、卖出、资金更新和通知在子进程里执行。系统启动流程内部仍按顺序同步执行交易步骤，避免一键准备流程在交易未完成时继续后续步骤。

v0.2.76 起，后台“AI 分析新闻”和自动调度 AI 分析默认 `QT_AI_ANALYSIS_PROCESS_ENABLED=true`。接口或调度器只启动 `ai_analysis` 独立 Python 子进程，实际新闻结构化、缓存和入库在子进程里执行。系统启动流程内部仍按顺序同步执行 AI 分析步骤，避免一键准备流程在 AI 结果未写入时继续补数和交易。

v0.2.77 起，`/api/data/coverage` 默认 `QT_DATA_COVERAGE_PROCESS_ENABLED=true`。数据覆盖率诊断结果写入 `frontend_payload_cache` SQLite 短缓存，缓存未命中时接口返回 pending 并启动 `data_coverage` 独立 Python 子进程；子进程完成后父进程可直接读取缓存结果。只有本地排查才建议传 `force=true&defer=false` 同步计算。

v0.2.78 起，后台“抓取新闻”和自动调度新闻抓取默认 `QT_NEWS_FETCH_PROCESS_ENABLED=true`。接口或调度器只启动 `news_fetch` 独立 Python 子进程，实际外部新闻源访问、新闻入库和事件刷新在子进程里执行。系统启动流程内部仍按顺序同步执行新闻抓取步骤，避免一键准备流程在新闻和事件未写入时继续 AI 分析。

v0.2.79 起，后台“系统启动”默认 `QT_SYSTEM_STARTUP_PROCESS_ENABLED=true`。接口立即返回 `system_startup` 任务启动状态，完整准备流程在独立 Python 子进程里顺序执行：新闻抓取、AI 分析、日K补齐、龙虎榜同步、分时行情、模拟交易。策略复盘、模型训练和回测仍不会自动执行；只有本地诊断才建议传 `process=false`。

v0.2.80 起，前台切换策略的 profile 保存路径进一步轻量化：`include_catalog=false` 时只构造资金档基础目录和当前策略参数，不再读取资金档运行摘要。线上排查仍应分开看 `/api/front/profile` 保存耗时、`/api/front/trading_account` 账户读取耗时和账户预热队列积压。

v0.2.81 起，前台保存或切换策略后，如果用户停留在概览页，不再自动请求推荐和日计划；只有停留在“买入”页时才补拉这两类数据。线上排查切换策略慢时，先确认浏览器 Network 里是否只剩 `POST /api/front/profile`，账户和推荐请求应由用户进入对应页面或手动刷新触发。

v0.2.82 起，`/api/front/snapshot?light=true` 默认不加载完整策略目录，只返回资金档和当前跟随策略所需信息。前台进入“策略”页时再请求 `/api/front/strategy_models`。线上排查首屏慢时，Network 里不应在普通概览首屏看到完整策略库加载耗时。

v0.2.83 起，通用 `/api/quant/backtest` 和单模型 `/api/quant/model/backtest?recompute=true` 在缓存未命中时默认要求 `manual=true`。后台按钮会自动带上该参数；旧页面刷新、误调用或普通 GET 请求只会返回 `manual_required`，不会启动 `quant_backtest` 或 `model_backtest` 重任务。若需本地排查同步执行，必须同时显式传 `manual=true&defer=false`。

v0.2.84 起，训练/复盘/回测/拟合类独立进程受 `QT_HEAVY_JOB_MAX_CONCURRENT` 控制，默认值为 `1`。当已有 `strategy_replay`、`strategy_evolution`、`model_backtest`、`quant_timeline`、`quant_backtest` 或 `fit_strategy` 在运行时，再启动同类重任务会返回 `busy`，不会再生成新的 Python 子进程。线上不建议调高该值，除非服务器 CPU、内存和 SQLite 写入压力已经确认充足。

v0.2.85 起，`model_backtest` 和 `quant_timeline` 默认也使用独立 Python 子进程，分别由 `QT_MODEL_BACKTEST_PROCESS_ENABLED=true` 和 `QT_TIMELINE_PROCESS_ENABLED=true` 控制。它们不再占用 API 进程内后台线程；完成后仍写入短缓存，前台/后台刷新时读取缓存或 pending 状态。

v0.2.86 起，后台概览和运维页会显示重任务槽位，例如 `1/1` 表示已有一个训练/复盘/回测类任务占满默认槽位。若点击新的复盘、回测或训练按钮返回 `busy`，先等待当前任务完成，或在任务卡片里停止当前任务后再启动新的重任务。

v0.2.87 起，`model_backtest` 和 `quant_timeline` 的完成结果会写入 SQLite `frontend_payload_cache`。这解决了独立子进程只写内存缓存导致父 API 进程刷新仍然看不到结果的问题；部署后无需额外迁移，首次命中会自动创建或复用该缓存表。

v0.2.88 起，`POST /api/front/profile` 默认用 `QT_FRONT_PROFILE_FOLLOW_PERIOD_ASYNC=true` 异步记录用户跟随周期，前台切换策略不再等待 `user_follow_periods` 的 SQLite 写锁。接口响应会带 `follow_period_record` 和 `profile_update_elapsed_ms` 便于排查；如需调试同步写入，可临时设为 `false`。重任务并发满或暂停时，相关接口会返回 `busy`/`paused`，页面应提示稍后再试或先停止当前重任务。

v0.2.89 起，`POST /api/front/profile` 默认还会用 `QT_FRONT_ACCOUNT_PRECOMPUTE_QUEUE_ASYNC_ON_PROFILE=true` 异步写入 `frontend_account_precompute_queue.json`。这避免账户预热 worker 持有队列锁时拖慢用户切换策略；响应中的 `account_precompute.status=queued_async` 表示请求已接受，队列写入会在 API 进程后台线程完成。线上排查时，如果 profile 保存仍慢，应优先看 `profile_update_elapsed_ms`、认证/profile JSON 写入和当前服务器 CPU，而不是账户预热队列锁。

v0.2.90 起，`/api/front/trading_account` 如果返回的是 pending 账户，会异步把当前用户补入账户预热队列，并在后台线程里启动 `frontend_account_precompute` worker。这是 profile 异步入队之外的自愈路径：即使用户切换策略时 API 进程重启或队列写入失败，用户进入账户页也会再次触发预热，不需要手工重新保存 profile。

v0.2.91 起，前台账户 pending 提示会读取响应里的 `account_precompute` 字段。`queued_async` 表示正在后台写队列，`worker_start_pending=true` 表示会在后台启动预热 worker，`worker_started=true` 表示预热任务已启动。线上排查用户反馈“还是 pending”时，先看这个字段判断是正常预热等待，还是确实没有策略运行结果。

v0.2.92 起，异步账户预热入队增加 `QT_FRONT_ACCOUNT_PRECOMPUTE_ASYNC_DEBOUNCE_SECONDS`，默认 `5` 秒。相同用户、相同触发原因和相同日期的重复请求会返回 `deduped=true`，不会重复创建后台入队线程。线上如果账户页被频繁刷新，应保持该值大于 0，避免 pending 自愈机制反过来放大线程数。

v0.2.93 起，`/api/front/snapshot` 和 `/api/front/trading_account` 共用 pending 账户自愈逻辑。登录首屏如果已经拿到 pending 账户对象，后端也会异步补账户预热队列并启动 worker；不再依赖前端继续调用账户详情接口。线上排查首屏 pending 时，看 snapshot 响应里的 `trading_account.account_precompute`。

v0.2.94 起，前台会把 `account_precompute.deduped=true` 显示为已有相同账户预热请求正在处理。用户频繁刷新账户页时看到该提示是正常保护行为；如果长时间仍 pending，再检查后台“前台账户预热”任务和策略运行结果是否存在。

v0.2.95 起，`/api/jobs/status` 会返回 `frontend_account_precompute_async`，后台“运维”页也会显示“账户预热异步保护”。它只展示当前去重窗口、保护中的请求数量、原因分布和启动模式计数，不展示用户名。线上如果用户反馈频繁刷新仍 pending，可同时查看该面板和 `frontend_account_precompute_queue` 队列长度。

v0.2.96 起，`POST /api/front/profile` 返回 `profile_update_trace` 和 `profile_update_slow_stage`。线上排查“切换策略慢”时，先看浏览器 Network 里该响应的阶段耗时，再对照 `/api/jobs/status` 的账户预热队列和异步保护状态。

v0.2.97 起，前台 profile 保存不会在轻量模式下因为过期策略 ID 回退读取完整策略目录。线上如果老用户保存了已删除模型，接口会直接按资金规模回到推荐资金档策略，策略页进入时再按需加载完整策略库。

v0.2.98 起，`POST /api/front/profile` 保存后会复用保存结果构建策略上下文，不再额外读取一次前台用户资料文件。排查切换慢时，`profile_update_trace` 里的 `build_profile_context` 理论上应更稳定；如果仍慢，重点看策略模型单条读取或 SQLite 写锁。

v0.2.105 起，策略运行快照 source 过滤从 `LIKE 'daily_runtime%'` 改为范围条件，并新增 source 复合索引。更新后策略运行账户、策略库资金档摘要读取会更稳定地区分正式每日运行快照和短缓存快照。

v0.2.104 起，`user_follow_periods` 增加当前周期复合索引，切换策略或调整资金时关闭旧跟随周期会兼容历史 `ended_at=NULL` 行，并减少函数包列导致的索引失效风险。

v0.2.103 起，后台概览和策略信号矩阵读取最新信号日期时不再全表 `COUNT(*)`；如果 `strategy_daily_signals` 很大，更新后该接口应更依赖日期索引而不是全表扫描。

v0.2.102 起，API 进程内同一个 `quant_data.sqlite3` 路径只会按 schema 版本执行一次策略演进建表/建索引脚本；后续连接只保留轻量 PRAGMA。更新后首次触发数据库连接仍可能创建索引，之后不会在每个请求里重复跑完整 schema 初始化。

v0.2.101 起，启动或迁移 SQLite schema 时会自动补充前台账户和策略运行热路径复合索引。更新后如果服务器首次访问较慢，通常是在为已有大表创建索引；后续前台账户、策略运行矩阵和模型信号读取应减少大表扫描。

v0.2.100 起，前台 profile 保存前的策略目录整理和策略 ID 解析逻辑迁入 `app.quant.front_profile`，部署验证仍以 `POST /api/front/profile` 的 `profile_update_trace` 为准；接口行为不变，方便后续把前台 router 从 `main.py` 拆出。

v0.2.99 起，`POST /api/front/profile` 会在写入前先解析传入的策略 ID。过期模型、空 ID 或 `active` 会直接替换为当前资金规模推荐策略；已查到的可复用模型会传给上下文构建，因此不会再出现“先写入旧策略，再由上下文纠正并二次写入”或同请求重复查模型的保存链路。

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

部署后如果页面仍显示样例数据，说明生产数据链路还没有跑起来。先在后台“配置与安全”填写 DeepSeek、必盈、邮件等服务器本地配置，再到“运维”点击“系统启动”。该按钮会把完整准备流程提交到独立进程，并按顺序执行新闻抓取、AI 分析、行情同步和交易循环；策略复盘、模型训练和回测需要单独手动触发，运行日志会在右侧日志栏显示中文状态。

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
curl -H "Authorization: Bearer $QT_ADMIN_TOKEN" -X POST "http://127.0.0.1:8000/api/jobs/market/sync?source=auto&max_codes=80&include_latest=true&process=true"
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
## v0.2.106 性能与慢请求说明

- `QT_ACCESS_LOG_ASYNC=true` 为生产默认值：API 请求结束时只把访问审计记录放入内存队列，不再同步等待 `backend/data/access_logs.json` 整文件重写。后台“访问日志”读取时会短暂刷新队列，并返回 `async.queue_size`、`async.queue_max`、`async.dropped`。
- `QT_ACCESS_LOG_QUEUE_MAX=2000` 控制访问审计异步队列长度。队列满时会丢弃新的访问日志条目，避免访问审计反过来拖慢前台用户操作；这不影响业务数据、策略结果或用户账户。
- `frontend_payload_precompute` 和 `frontend_account_precompute` 已纳入 `QT_HEAVY_JOB_MAX_CONCURRENT` 重任务并发闸门。线上如果已有策略复盘、训练、回测、拟合或前台批量预计算进程在运行，新重任务会返回 `busy`，避免继续抢占 CPU 和 SQLite 写锁。
- 前台切换策略慢时，先看 `POST /api/front/profile` 响应里的 `profile_update_trace`；如果各阶段耗时不高但浏览器整体等待长，重点排查访问日志队列、Nginx/Cloudflare 代理等待、服务器 CPU 和正在运行的重任务。

## v0.2.107 访问日志批量写入

- 访问审计异步线程会按 `QT_ACCESS_LOG_BATCH_SIZE=50` 和 `QT_ACCESS_LOG_BATCH_WINDOW_MS=200` 批量落盘，减少高频访问时 `access_logs.json` 的重写次数。
- 后台访问日志接口返回的 `async` 字段会包含 `batch_size` 和 `batch_window_ms`，用于确认生产配置是否生效。

## v0.2.108 auth 文件缓存

- `QT_AUTH_FILE_CACHE_ENABLED=true` 为生产默认值。API 进程会缓存 `auth.json`，并按文件路径、mtime 和大小检测外部修改；后台或前台写入用户资料、登录状态、密码等信息后会立即刷新缓存。
- 如果你在服务器上手工编辑 `backend/data/auth.json`，下一次请求会因为文件签名变化自动重新读取；排查极端缓存问题时可临时设为 `QT_AUTH_FILE_CACHE_ENABLED=false` 并重启服务。
- 该缓存不会改变 token、密码哈希或 profile 文件格式，只减少前台切换策略、登录态校验等热路径的重复文件读取。

## v0.2.109 前台鉴权热路径

- 前台 `/api/front/*` 和其它 frontend scope 请求只校验 token/debug scope，不再额外调用完整 `auth_status()` 构建后台认证摘要。
- 后台 admin scope 仍会检查是否需要初始化，部署和安全语义不变；该调整只减少普通前台请求的固定认证开销。

## v0.2.110 前台快照与任务状态

- 前台快照不再返回 `logs`，也不会把服务器 `data_dir` 放入前台 `status_payload`；后台日志继续通过后台管理页查看。
- `QT_FRONT_JOBS_CACHE_TTL_SECONDS=3` 控制前台轻量任务状态短缓存。调大可进一步减少高频首屏/刷新读取任务状态的开销，调小可获得更即时的调度器显示。
- 部署后如果仍出现 502/504/524，应继续优先排查后端进程是否崩溃、CPU/内存是否被重任务占用、Nginx 到 Uvicorn 是否可连通，而不是从前台日志组件定位。

## v0.2.111 新闻热路径索引部署

- `qt update` 会在 SQLite 表结构验证阶段确认 `idx_news_raw_timestamp_date`、`idx_news_raw_date_timestamp` 和 `idx_news_events_date_impact` 存在；即使跳过全量 JSON/CSV 迁移，也会补齐这些索引。
- 这些索引用于前台快照最新新闻时间、新闻列表和结构化事件读取。首次在大库上创建索引可能让更新阶段多等待一小段时间，完成后前台读取更稳定。
- 如果手工部署未执行 `qt update`，请至少执行一次 `qt migrate` 或 `qt version`/`qt verify` 触发表结构验证。

## v0.2.112 最新新闻时间短缓存部署

- `QT_LATEST_NEWS_TIME_CACHE_TTL_SECONDS=5` 控制前台状态摘要里的最新新闻时间短缓存。线上高频刷新时可适当调大，排查新闻新鲜度时可临时设为 `0` 后重启服务。
- 该参数只影响状态摘要展示，不影响新闻抓取、AI 分析、策略信号、交易模拟或账户结果。

## v0.2.113 前台数据日期边界缓存部署

- `QT_DATA_DATE_CACHE_TTL_SECONDS=10` 控制前台默认日期和跟随窗口裁剪的短缓存。线上可以保持默认；如果刚完成大规模数据导入并需要立即看到新日期，可临时设为 `0` 或重启服务清空进程内缓存。
- 部署后前台快照、账户读取和切换策略后的刷新不应因为获取最新日期而触发完整事件缓存重建。

## v0.2.114 轻量快照账户只读部署

- 部署后 `/api/front/snapshot?light=true` 不再同步写入用户跟随账户派生结果。切换策略后如账户仍显示 pending，使用后台“前台账户预热”任务或等待预热队列消费。
- 如果线上仍有 SQLite 写锁导致首屏慢，优先查看正在运行的策略复盘、导入、账户预热和其它重任务，而不是让轻量快照承担补写职责。

## v0.2.115 前台账户 GET 只读部署

- `QT_FRONT_ACCOUNT_PERSIST_ON_READ=false` 为生产推荐默认值。前台账户接口读到可展示结果后立即返回；如果需要补写 `user_follow_*`，会异步排队账户预热。
- 本地排查可临时设置 `QT_FRONT_ACCOUNT_PERSIST_ON_READ=true` 或使用 `force=true`，但线上日常不建议让用户 GET 请求同步写账户派生表。

## v0.2.116 策略运行矩阵部署

- `qt update` 后可通过后台受控接口检查策略运行结果矩阵：
  ```bash
  curl -H "Authorization: Bearer $QT_ADMIN_TOKEN" "http://127.0.0.1:8000/api/admin/strategy_runtime/matrix?limit_models=80"
  ```
- 该接口不需要新增环境变量，也不触发迁移之外的重计算；部署脚本的 SQLite 表结构校验会继续确认策略运行表和信号表存在。
- 排查“切换策略慢、账户 pending、Nginx/Cloudflare 超时”时，先看矩阵里的 `runtime_status`、`missing_count`、`signal_count` 和 `ready_count`。缺少运行结果时应在后台手动执行策略复盘或导入小包，然后再运行前台账户预热。

## v0.2.117 策略运行矩阵页面部署

- `qt update` 并重启后，后台“策略”页会显示“策略运行矩阵”面板；不需要新增环境变量或额外迁移。
- 如果页面矩阵为空但 curl 接口有结果，优先清浏览器缓存或确认前端静态文件已经随服务更新；如果接口本身返回 404，说明后端还未更新到 v0.2.116+ 或服务未重启。
- 线上看到切换策略慢时，先在该面板确认目标策略是否 `已落库`。如果是 `缺运行结果`，不要让前台用户请求等待重算，应在后台手动复盘或导入运行结果。

## v0.2.118 策略运行矩阵服务拆分部署

- 本版本新增 `backend/app/quant/strategy_runtime_matrix.py`，无需新增环境变量、数据库迁移或服务器数据操作。
- 部署后继续通过后台“策略”页或 `/api/admin/strategy_runtime/matrix` 验证矩阵；如果导入模块失败，说明代码未完整更新或服务未重启。
- 该拆分只改变后端代码组织，不改变 Nginx、Cloudflare、SQLite 表结构或后台任务运行方式。

## v0.2.119 后台策略运行 router 部署

- 本版本新增 `backend/app/routers/admin_strategy_runtime.py` 和 router 包初始化文件，无需新增环境变量或数据库迁移。
- 部署后仍访问原接口 `/api/admin/strategy_runtime/matrix`；如果该接口 404，说明服务未重启或新 router 文件未部署完整。
- 该调整不改变 Nginx、Cloudflare、鉴权或后台任务配置。

## v0.2.120 后台策略运行接口 router 部署

- `/api/admin/trading_account` 和 `/api/admin/strategy_runtime/replay` 也由 `admin_strategy_runtime` router 注册，部署方式不变。
- 部署后后台“持仓成交”和“复盘分析”页面应继续正常读取；如果只有这两条接口 404，优先确认 `backend/app/routers/admin_strategy_runtime.py` 已部署且服务已重启。
- 不需要新增环境变量、数据库迁移或 Nginx 配置。

## v0.2.121 前台切换策略性能部署

- 更新后仍建议保持 `QT_FRONT_ACCOUNT_START_WORKER_ON_PROFILE=false`。本版本在代码调用层也会强制 profile 保存和注册只排队账户预热，不在用户请求里启动 worker，因此旧服务器 `.env` 残留为 `true` 时不会再拖慢切换策略。
- 部署后检查浏览器 Network：`POST /api/front/profile?include_catalog=false` 的 `account_precompute.worker_start_deferred` 应为 `true`，`worker_start_pending` 应为 `false`；如果 `profile_update_trace` 仍显示慢，继续按阶段耗时定位认证文件、策略模型单条读取或 SQLite 写锁。
- `/api/front/snapshot?light=true` 命中 `strategy_runtime_snapshots` 时会走 `runtime_snapshot_fast_path=true`，首屏不再扫描完整 `strategy_runtime_trades`。若仍 pending，先在后台策略运行矩阵确认目标策略是否缺运行结果，再手动复盘、导入运行小包或运行前台账户预热。

## v0.2.122 前台 profile router 部署

- 本版本新增 `backend/app/routers/frontend_profile.py`，无需新增环境变量、数据库迁移或 Nginx 配置。
- 部署后 `GET /api/front/profile` 和 `POST /api/front/profile` URL 不变；如果这两条接口 404，优先确认新 router 文件已部署、服务已重启，并通过 `qt version` 检查 OpenAPI 路由表。
- 该拆分只改变代码组织，不影响 `profile_update_trace`、`include_catalog=false` 默认值、账户预热队列或切换策略性能诊断方式。

## v0.2.123 前台运行视图 router 部署

- 本版本新增 `backend/app/routers/frontend_runtime.py`，无需新增环境变量、数据库迁移或 Nginx 配置。
- 部署后前台首屏、策略页和账户页仍访问原 URL：`/api/front/public_snapshot`、`/api/front/snapshot`、`/api/front/strategy_models`、`/api/front/trading_account`。
- 如果这些接口出现 404，优先确认新 router 文件已部署、服务已重启，并通过 `qt version` 或 `/openapi.json` 检查路由表；如果接口慢，仍按原方式查看 `profile_update_trace`、`runtime_snapshot_fast_path`、账户预热队列和重任务槽位。

## v0.2.124 前台买入视图 router 部署

- 本版本新增 `backend/app/routers/frontend_signal.py`，无需新增环境变量、数据库迁移或 Nginx 配置。
- 部署后前台买入页仍访问原 URL：`/api/front/recommendations` 和 `/api/front/daily_plan`。
- 如果这两条接口 404，优先确认新 router 文件已部署并重启服务；如果接口返回 pending/disabled，先确认是否已手动触发前台缓存预计算，或是否显式开启了 `QT_FRONT_PAYLOAD_AUTO_PRECOMPUTE_ON_MISS`。

## v0.2.125 后台概览 router 部署

- 本版本新增 `backend/app/routers/admin_overview.py`，无需新增环境变量、数据库迁移或 Nginx 配置。
- 部署后后台概览和模型信号仍访问原 URL：`/api/admin/snapshot` 和 `/api/admin/model_signals`。
- 如果这两条接口 404，优先确认新 router 文件已部署、服务已重启，并通过 `qt version` 或 `/openapi.json` 检查路由表。

## v0.2.126 后台数据库与缓存 router 部署

- 本版本新增 `backend/app/routers/admin_data_cache.py`，无需新增环境变量、数据库迁移或 Nginx 配置。
- 部署后后台数据库页和缓存页仍访问原 URL：`/api/admin/database/tables`、`/api/admin/database/table/{table_name}`、`/api/admin/cache/status`、`/api/admin/cache/clear`。
- 如果这些接口 404，优先确认新 router 文件已部署、服务已重启，并通过 `qt version` 或 `/openapi.json` 检查路由表。

## v0.2.127 后台访问审计 router 部署

- 本版本新增 `backend/app/routers/admin_access.py`，无需新增环境变量、数据库迁移或 Nginx 配置。
- 部署后后台访问审计和访问安全仍访问原 URL：`/api/admin/access_logs`、`/api/admin/access_security`、`/api/admin/access_security/block`、`/api/admin/access_security/unblock`、`/api/admin/access_security/block_all`。
- 如果这些接口 404，优先确认新 router 文件已部署、服务已重启，并通过 `qt version` 或 `/openapi.json` 检查路由表。

## v0.2.128 后台用户管理 router 部署

- 本版本新增 `backend/app/routers/admin_frontend_users.py`，无需新增环境变量、数据库迁移或 Nginx 配置。
- 部署后后台用户管理仍访问原 URL：`/api/admin/frontend_users`、`/api/admin/frontend_users/{username}`、`/api/admin/frontend_users/{username}/password`、`/api/admin/frontend_users/{username}/ban`、`/api/admin/frontend_users/{username}/unban`。
- 如果这些接口 404，优先确认新 router 文件已部署、服务已重启，并通过 `qt version` 或 `/openapi.json` 检查路由表。

## v0.2.129 前台预计算部署建议

- 生产默认建议保持 `QT_FRONT_PAYLOAD_PRECOMPUTE_ENABLED=false` 和 `QT_FRONT_PAYLOAD_AUTO_PRECOMPUTE_ON_MISS=false`。这样重启或用户访问推荐/日计划时不会自动启动批量预计算，避免 1 核服务器被 `frontend_payload_precompute` 打满。
- 推荐和日计划缓存默认 TTL 均为 1800 秒；如果显式开启调度器预计算，建议让 `QT_FRONT_PAYLOAD_PRECOMPUTE_INTERVAL_SECONDS` 和 `QT_FRONT_PAYLOAD_PRECOMPUTE_INITIAL_DELAY_SECONDS` 不短于 1800 秒，避免重启后立刻刷新和重复刷新。
- 后台“预计算前台缓存”按钮默认 `force=false`，适合在策略复盘完成后手动执行；v0.2.134 起默认只处理 8 个用户、30 天日计划、最多 20 秒，适合作为分批缓存刷新。如果确实要忽略缓存强制重算，只在低峰期通过接口显式传 `force=true`。

## v0.2.130 前台预计算排查方式

- 部署后可在后台自动调度器面板或 `/api/jobs/status?light=true` 查看 `frontend_payload_policy`；默认应显示 `mode=manual`、`precompute_enabled=false`、`auto_precompute_on_miss=false`。
- 如果线上 CPU 被打满，先看后台“正在运行”的重任务和 `frontend_payload_policy`：默认配置下普通用户访问不会启动前台批量预计算，只有后台手动按钮或显式开启调度/未命中补算才会运行。
- 修改 `.env` 中前台预计算相关变量后需要重启后端服务，后台面板文案会随状态接口更新；Nginx 和 Cloudflare 不需要额外配置。

## v0.2.131 任务运维 router 部署

- 本版本新增 `backend/app/routers/admin_jobs.py`，无需新增环境变量、数据库迁移或 Nginx 配置。
- 部署后 `/api/jobs/status`、`/api/jobs/logs`、`/api/logs/runtime`、`/api/jobs/scheduler/start`、`/api/jobs/scheduler/stop` 和任务暂停/恢复/停止路径保持不变。
- 如果后台任务状态或调度器按钮出现 404，优先确认新 router 文件已部署、服务已重启，并通过 `/api/debug/routes` 或 OpenAPI 检查路由表。
- 前台快照里的任务摘要不再调用完整 `/api/jobs/status` 巡检逻辑；如果前台仍慢，应优先看新闻/账户数据热路径，而不是任务状态巡检。

## v0.2.132 任务执行 router 部署

- 本版本新增 `backend/app/routers/admin_job_runs.py`，无需新增环境变量、数据库迁移或 Nginx 配置。
- 部署后后台运维按钮仍访问原 URL，包括新闻抓取、行情同步、AI 分析、交易循环、策略复盘、前台预计算、前台账户预热、每日交易和系统启动流程。
- 如果这些任务入口出现 404，优先确认新 router 文件已部署、服务已重启，并通过 `/api/debug/routes` 或 OpenAPI 检查路由表。

## v0.2.133 核心系统 router 部署

- 本版本新增 `backend/app/routers/core_system.py`，无需新增环境变量、数据库迁移或 Nginx 配置。
- 部署后 `/api/version`、`/api/auth/*`、`/api/debug/*`、`/api/config/*` 和 `/api/status` URL 不变。
- `/api/status` 现在只返回轻量任务摘要；需要完整任务队列、进程巡检和前台预计算策略时继续查看 `/api/jobs/status`。
- `QT_FRONT_SNAPSHOT_NEWS_CACHE_TTL_SECONDS` 默认 30 秒；如新闻刷新非常频繁可调小，但不建议设为 0，否则登录快照会更频繁读取新闻。

## v0.2.134 前台预计算限流部署

- 新增/建议确认：`QT_FRONT_PAYLOAD_PRECOMPUTE_LIMIT_USERS=8`、`QT_FRONT_PAYLOAD_PRECOMPUTE_MAX_SECONDS=20`、`QT_FRONT_PAYLOAD_PRECOMPUTE_INITIAL_DELAY_SECONDS=1800`、`QT_FRONT_ACCOUNT_PRECOMPUTE_ASYNC_WORKERS=4`、`QT_FRONT_ACCOUNT_PRECOMPUTE_ASYNC_DISPATCH_DELAY_MS=25`。
- 生产默认继续保持 `QT_FRONT_PAYLOAD_PRECOMPUTE_ENABLED=false` 和 `QT_FRONT_PAYLOAD_AUTO_PRECOMPUTE_ON_MISS=false`。用户访问推荐/日计划缓存未命中时只返回 pending/disabled，不自动抢 CPU。
- `QT_FRONT_SNAPSHOT_LIGHT_NEWS_NO_ENGINE_FALLBACK=true` 和 `QT_DATA_DATE_ENGINE_FALLBACK_ENABLED=false` 会让轻量前台快照只走 SQLite 轻量数据；如果 SQLite 没有新闻或日期，页面返回空新闻/当前日期占位，不再回落到完整引擎加载。
- 如需一次性给大量用户预热前台推荐缓存，不建议在 1 核服务器高峰期执行；分多次手动点击或临时调大 `limit_users/max_seconds` 后在低峰运行。

## v0.2.135 研究重任务手动部署

- 生产建议新增/确认：`QT_RESEARCH_TASKS_MANUAL_ONLY=true`、`QT_STRATEGY_REPLAY_PROCESS_ENABLED=true`、`QT_STRATEGY_EVOLUTION_PROCESS_ENABLED=true`、`QT_TIMELINE_REQUIRE_MANUAL_TRIGGER=true`、`QT_TIMELINE_PROCESS_ENABLED=true`。
- 日常自动链路只保留新闻、AI、行情、龙虎榜、交易循环和必要的轻量缓存；策略复盘、策略进化、模型回测、通用回测、时间线回测和参数拟合都应由后台按钮或维护命令显式触发。
- 如果确实要让调度器自动复盘或进化，必须先把 `QT_RESEARCH_TASKS_MANUAL_ONLY=false`，并确认服务器 CPU、内存、`QT_HEAVY_JOB_MAX_CONCURRENT` 和运行窗口；不建议在 1 核服务器上这么做。

## v0.2.136 代理 HTML 错误页兜底

- 部署后如果 Nginx 或 Cloudflare 把 HTML 错误页返回给前台接口，页面应显示中文诊断，不应再把整页 HTML 或 `Unexpected token '<'` 暴露给用户。
- 该改动只在前端静态文件中生效；更新后需要确认 `frontend/index.html` 和 `frontend/admin/index.html` 已随后端发布，必要时清浏览器缓存或刷新 Cloudflare 缓存。
- 这不是源站故障的根治方式。反复出现 502/504/524 时，仍按 `qt status`、`qt logs`、`curl 127.0.0.1:${QUANT_PORT:-8000}/api/version` 和 Nginx upstream 检查处理。

## v0.2.137 量化基础接口 router 部署

- 本版本新增 `backend/app/routers/quant_basic.py`，无需新增环境变量、数据库迁移或 Nginx 配置。
- 部署后基础量化接口 URL 保持不变，包括 `/api/quant/dashboard`、`/api/quant/recommendations`、`/api/quant/daily_plan`、`/api/quant/strategy_params`、`/api/quant/events`、`/api/quant/news`、`/api/quant/correlation`、`/api/quant/portfolio`、`/api/quant/trading_account`、`/api/quant/run` 和 `/api/news_history`。
- 如果这些接口出现 404，优先确认新 router 文件已部署并重启服务，再通过 `/openapi.json` 或调试路由检查注册状态；如果接口慢，仍按前台缓存、账户快照、重任务状态和新闻/行情 SQLite 读取路径排查。
