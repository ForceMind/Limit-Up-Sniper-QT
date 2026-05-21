# 涨停狙击手

涨停狙击手是一个面向 A 股研究的 AI 量化分析与模拟交易系统。它把新闻抓取、DeepSeek 结构化分析、行情同步、Agent 评分、买卖回放、模拟账户和后台运维放在同一个 FastAPI 服务里，目标是形成可追溯的研究闭环。

当前版本只做研究、回放和模拟交易，不连接真实券商账户，不会真实下单。任何策略输出都不构成投资建议。

## 系统流程

```text
新闻抓取 -> AI结构化分析 -> 行情同步 -> 多Agent评分
-> 每日买入计划 -> 分时/日K回放 -> 模拟成交和交割
-> 收益复盘 -> 参数拟合和进化
```

## 当前能力

- 自动新闻抓取：抓取财联社电报，并和本地历史新闻库去重合并。
- AI 事件分析：使用 DeepSeek `deepseek-v4-flash` 把新闻拆成事件类型、情绪、影响行业、影响个股、利好利空和强度。
- 多 Agent 评分：新闻情绪、事件影响、技术走势、风险控制共同给出短期买入评分。
- 买卖回放：按“当日已知信息生成次日计划”的口径回放，避免未来函数。
- 分时模拟：使用必赢 5 分钟 K 线模拟更接近盘中的买入、卖出和估值。
- 模拟账户：展示现金、持仓、冻结数量、历史成交、交割单、手续费、印花税和收益表现。
- T+1 处理：当天买入显示冻结，次日才进入可卖数量。
- 邮件通知：模拟买入、卖出触发后可发送邮件提醒。
- 参数拟合：支持历史复盘、参数调整和遗传进化，按收益、回撤、胜率筛选参数。
- 前后台页面：前台是交易终端视图，后台用于任务触发、参数调整、数据同步、日志和模型拟合。
- 服务器运维：提供安装、更新、重启、备份、恢复、systemd 和 Nginx 模板。

## 技术栈

- 后端：Python、FastAPI、Uvicorn
- 数据：本地 JSON/CSV 文件，保存在 `backend/data`
- AI：DeepSeek API
- 行情：必赢数据接口
- 前端：静态 HTML/CSS/JavaScript，由 FastAPI 直接托管
- 部署：systemd、Nginx、Bash 脚本

## 目录结构

```text
backend/app/main.py              FastAPI 入口、API 路由、静态页面托管
backend/app/quant/engine.py      量化事件、评分、回放、账户和策略参数核心逻辑
backend/app/quant/jobs.py        定时任务、任务状态、运行日志
backend/app/quant/news_fetcher.py 新闻抓取和去重
backend/app/quant/ai_analyzer.py DeepSeek 结构化分析
backend/app/quant/biying_sync.py 必赢行情同步
backend/app/quant/notifier.py    邮件通知
backend/data                     样例数据和本地运行数据目录
backend/tests                    回归测试
frontend                         前台交易终端页面
frontend/admin                   后台管理页面
qt.sh                            根目录统一命令入口
scripts                          安装、更新、备份、扫描等底层脚本
deploy                           systemd 和 Nginx 示例配置
docs                             需求、逻辑、部署、安全和交接文档
```

## 本地启动

建议使用 Python 3.10 或更高版本。

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Windows PowerShell 可改用：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
Copy-Item .env.example .env
Set-Location backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

浏览器访问：

```text
http://127.0.0.1:8000
http://127.0.0.1:8000/admin
```

## 配置说明

首次运行先复制环境变量模板：

```bash
cp .env.example .env
```

生产配置只写入 `.env` 或服务器本地的 `backend/data/config.json`，不要提交真实密钥。常用配置包括：

- `DEEPSEEK_API_KEY`：DeepSeek API Key
- `DEEPSEEK_MODEL`：默认 `deepseek-v4-flash`
- `BIYING_ENABLED`：是否启用必赢行情同步
- `BIYING_LICENSE_KEY`：必赢数据授权
- `EMAIL_ENABLED`：是否启用邮件通知
- `SMTP_SERVER`、`SMTP_USER`、`SMTP_PASSWORD`、`EMAIL_TO`：邮件配置
- `NEWS_FETCH_INTERVAL_SECONDS`、`AI_ANALYSIS_INTERVAL_SECONDS`、`MARKET_SYNC_INTERVAL_SECONDS`：自动任务间隔
- `QT_AUTH_TOKEN_TTL_SECONDS`：前台/后台登录 token 有效期，默认 43200 秒

首次部署后先访问 `/admin`，后台会要求初始化两个账号：后台管理员账号和前台交易终端账号。后台账号可以修改配置和触发运维任务；前台账号只用于查看交易终端数据。账号密码哈希保存在服务器本地 `backend/data/auth.json`，真实运行配置保存在 `backend/data/config.json`，这两个文件都不应提交到 Git。

如果部署后仍看到样例数据，通常是还没有在后台“配置与安全”里填写 DeepSeek、必赢、邮件等服务器本地配置，也没有触发真实数据同步。配置完成后进入“运维”，点击“系统启动”，系统会按顺序执行新闻抓取、AI 分析、行情同步和交易循环。

## 数据与 Git 规则

`backend/data` 是运行数据目录。真实配置、密钥、日志、任务状态、缓存和历史生产数据默认不入库。

仓库中只保留几类公开安全文件：

- `backend/data/.gitkeep`
- `backend/data/config.example.json`
- `backend/data/biying_stock_list.json`
- `backend/data/news_history.json`
- `backend/data/news_analysis_records.json`
- `backend/data/kline_day_cache/600001.json`
- `backend/data/kline_day_cache/600002.json`
- `backend/data/kline_cache/600001_2026-05-19.csv`
- `backend/data/kline_cache/600002_2026-05-19.csv`

这些文件是 `Fixture` 样例数据，只用于新仓库开箱展示和测试，不包含真实密钥或个人历史运行数据。

## 关键 API

- 状态与任务：`GET /api/status`、`GET /api/jobs/status`、`GET /api/jobs/logs`
- 新闻与 AI：`GET /api/quant/news`、`POST /api/jobs/news/fetch`、`POST /api/jobs/ai/analyze`、`GET /api/ai/records`
- 行情数据：`POST /api/jobs/market/sync`、`GET /api/data/biying/status`、`GET /api/data/coverage`
- 量化结果：`GET /api/quant/daily_plan`、`GET /api/quant/recommendations`、`GET /api/quant/timeline`、`GET /api/quant/intraday_timeline`
- 账户回放：`GET /api/quant/trading_account`、`POST /api/jobs/trading/run`、`POST /api/quant/backtest`
- 参数进化：`GET /api/quant/strategy_params`、`POST /api/quant/fit_strategy`、`POST /api/quant/evolve_strategy`
- 通知与运维：`GET /api/notifications/status`、`POST /api/notifications/test`、`POST /api/admin/backup`

完整路由可在服务启动后访问：

```text
http://127.0.0.1:8000/docs
```

## 测试和上传前检查

提交或推送到 GitHub 前建议执行：

```bash
python -m py_compile backend/app/quant/engine.py backend/app/quant/jobs.py backend/app/main.py
python -m pytest backend/tests/test_quant_engine.py
python scripts/security_scan.py
git diff --check
git status --short --ignored
git ls-files backend/data
```

安全扫描只检查 Git 候选文件。若本地存在被忽略的 `.env`、`backend/data/config.json`、`admin_credentials.json` 等敏感文件，不要手动 `git add -f`。

## 服务器部署

根目录只需要记一个入口：`qt.sh`。

首次部署：

```bash
cp .env.example .env
# 编辑 .env，填入 DeepSeek、必赢、SMTP 等真实配置
bash qt.sh install
```

日常更新：

```bash
bash qt.sh
```

`bash qt.sh` 等同于 `bash qt.sh update`，会先备份 `backend/data`，再 `git pull --ff-only`、安装依赖并重启服务。

常用运维命令：

```bash
bash qt.sh doctor
bash qt.sh status
bash qt.sh restart
bash qt.sh logs
bash qt.sh backup
bash qt.sh restore <backup-file>
bash qt.sh scan
```

为什么底层脚本仍放在 `scripts/`，而不是全部放根目录：

- 根目录只保留项目入口、配置模板和文档，避免一堆运维脚本混在代码入口旁边。
- `scripts/` 里的 `install_server.sh`、`update_server.sh`、`backup_data.sh`、`restore_data.sh` 是实现细节，方便维护和复用。
- 根目录 `qt.sh` 是给人使用的统一入口，它会转发到 `scripts/qt.sh`；`scripts/qt.sh` 提供中文帮助、步骤日志、状态查看和部署环境检查。
- 首次安装后，服务器会尽量创建系统快捷命令 `/usr/local/bin/qt`，以后也可以直接用：

```bash
qt
qt status
qt auth
qt doctor
qt update
qt restart
qt logs
```

直接输入 `qt` 会打开中文交互式运维面板，可执行更新、重启、日志、备份、恢复、安全扫描和账号密码管理。`qt auth` 会直接进入账号密码管理，可以初始化、修改后台账号、修改前台账号，或删除认证文件回到网页首次初始化。

详细部署说明见 [docs/SERVER_DEPLOY.md](docs/SERVER_DEPLOY.md)。

## 文档

- [产品需求文档](docs/PRODUCT_REQUIREMENTS.md)
- [开发计划](docs/DEVELOPMENT_PLAN.md)
- [买卖与回放逻辑](docs/QUANT_LOGIC.md)
- [服务器部署说明](docs/SERVER_DEPLOY.md)
- [GitHub 上传安全检查](docs/GITHUB_SECURITY.md)
- [Codex 交接记录](docs/CODEX_HANDOFF.md)
