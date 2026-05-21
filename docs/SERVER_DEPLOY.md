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
backend/data             历史新闻、AI分析记录、K线缓存、运行状态
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
TRADING_HOLIDAYS=
TRADING_EXTRA_DAYS=
```

服务端优先读取 `.env`，再读取 `backend/data/config.json`。生产服务器不要提交 `.env`。

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

`bash qt.sh` 等同于 `bash qt.sh update`，会先备份 `backend/data`，再执行 `git pull --ff-only`、更新依赖并重启服务。

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

## 服务器 qt 命令

根目录只需要记一个入口：`qt.sh`。

底层的安装、更新、备份、恢复、重启脚本仍放在 `scripts/`，原因是：

- 根目录应该只保留项目入口、配置模板和文档，避免运维脚本散落在最外层。
- `scripts/` 是脚本实现目录，方便维护、复用和权限管理。
- `qt.sh` 是人用入口，负责把命令转发给 `scripts/qt.sh`；`scripts/qt.sh` 提供中文帮助、步骤日志、状态查看和部署环境检查。

`install_server.sh` 会尽量把 `scripts/qt.sh` 安装为 `/usr/local/bin/qt`。安装成功后，可以直接使用：

```bash
qt status
qt doctor
qt restart
qt update
qt backup
qt logs
qt scan
```

如果服务器没有 sudo/root 权限，可以手动创建软链接：

```bash
sudo ln -sf /opt/qt/scripts/qt.sh /usr/local/bin/qt
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

正式域名和 TLS 证书按服务器实际情况调整。

## 页面入口

前台交易终端：

```text
http://服务器IP:8000/
```

后台管理：

```text
http://服务器IP:8000/admin
```

首次打开后台会进入初始化页，需要创建两个账号：后台管理员账号和前台交易终端账号。后台账号用于配置密钥、触发任务和运维操作；前台账号只用于查看交易终端。账号哈希保存在 `backend/data/auth.json`，运行配置保存在 `backend/data/config.json`，二者都属于服务器本地文件，不要提交到 Git。

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
- 行情同步：仅交易日 09:30-11:30、13:00-15:00 使用必盈接口补充分时 K 线；周末和非开盘时间不触发行情同步
- 模拟交易：按当前模型触发买入/卖出，触发后可通过 SMTP 发送邮件
- 遗传进化：多组参数并行回放，按收益、回撤、胜率选择最优参数，后台可手动应用
- 模型回放：前台和后台按全周期线性回放计算买入、卖出、收益和交割单

后台管理页可以查看任务状态，也可以手动触发新闻、AI、行情同步。

## 生产注意事项

- `.env` 和 `backend/data/config.json` 里可能包含接口密钥，服务器权限要收紧。
- 更新前必须保留 `backend/data`；不要用空目录覆盖生产数据。
- 先在测试服务器跑通新闻、AI、行情同步，再开放公网访问。
- 当前系统是策略研究和模拟盘系统，不应直接作为真实交易下单系统使用。
