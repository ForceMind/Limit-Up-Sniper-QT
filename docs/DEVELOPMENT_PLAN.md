# 涨停狙击手量化版开发计划

## 1. 开发原则

- 先保证系统能持续跑，再追求模型复杂度。
- 自动化链路优先级高于单次手动分析。
- 每个推荐必须可追溯：新闻、AI结构化、Agent评分、行情、交易结果。
- 数据层要逐步收敛，不再让业务逻辑散落读取 JSON。
- 不破坏已有历史数据，所有迁移必须可回滚。
- 先模拟交易，不做真实下单。

## 2. 旧项目可复用部分

旧项目中值得迁移的能力：

- `data_provider.py`：必盈接口配置、限流、缓存、股票列表、全市场行情、分时和日K拉取。
- `news_analyzer.py`：财联社新闻抓取、新闻历史保存、DeepSeek调用、AI分析记录。
- `market_scanner.py`：盘中异动、涨停池、炸板池、市场情绪。
- `config_manager.py`：智能调度时间表、API配置、成本配置。
- 旧后台：配置、日志、数据导出、运行状态页面的思路。
- 旧部署脚本：安装、更新、重启、systemd 托管的脚本结构。

需要重做或重构的部分：

- 商业会员、支付、邀请、设备授权先从量化核心中剥离。
- 旧前台涨停池页面不作为主界面，只作为后台或专题模块。
- AI输出必须改为严格结构化 JSON，不能只保存自然语言总结。
- 自动任务必须有统一状态和错误队列，不能只靠临时后台线程。

## 3. 里程碑规划

### M0：基线整理和安全收敛

目标：当前分支可以稳定启动、数据不丢、密钥不泄露。

任务：

- 整理当前活跃代码目录。
- 明确保留数据文件和废弃文件。
- 增加 `.env.example`。
- 配置读取改为“环境变量优先，本地配置兜底”。
- 文档标注敏感配置不得提交。
- 补充基础健康检查。

验收：

- 本地 `python -m pytest backend/tests/test_quant_engine.py` 通过。
- 服务启动后 `/api/status` 返回当前日期、数据日期、AI模型、任务状态。
- 新开发者能按 README 启动。

### M1：自动新闻抓取

目标：不用手动导入新闻，系统持续获得当天新闻。

任务：

- 新建 `backend/app/quant/news_fetcher.py`。
- 迁移财联社抓取逻辑。
- 支持东方财富/本地历史数据统一读取。
- 实现新闻去重、增量保存、失败退避。
- 增加 `POST /api/jobs/news/fetch`。
- 增加 `GET /api/quant/news` 的来源筛选和关键词筛选。
- 后台增加新闻源状态、手动抓取按钮、最近新闻列表。

建议文件：

- `backend/app/quant/news_fetcher.py`
- `backend/app/quant/repositories.py`
- `backend/app/main.py`
- `frontend/admin/index.html`
- `frontend/index.html`

验收：

- 盘中每 1-5 分钟自动抓取一次新闻。
- 抓取失败不会影响前台。
- 首页能看到当天最新新闻；无当天新闻时明确提示。

### M2：行情同步任务

目标：行情数据覆盖推荐、持仓和回放需要。

任务：

- 新建 `backend/app/quant/market_sync.py`。
- 迁移必盈限流、重试、缓存状态。
- 同步股票列表。
- 同步日K。
- 同步5分钟K。
- 同步推荐池、持仓池、事件池。
- 增加数据覆盖率接口。
- 后台增加行情同步状态和手动同步。

建议接口：

- `POST /api/jobs/market/sync`
- `GET /api/data/coverage`
- `GET /api/data/biying/status`

验收：

- 系统能自动补齐当天推荐股票分时K。
- 后台能看到每个日期/股票的日K和5分钟K覆盖情况。
- 必盈接口被限流保护，不会无控制打满额度。

### M3：AI增量分析队列

目标：新新闻自动变成结构化事件。

任务：

- 新建 `backend/app/quant/ai_analyzer.py`。
- 设计严格 JSON Schema。
- DeepSeek模型默认 `deepseek-v4-flash`。
- 支持缓存、重试、失败降级。
- 保存AI调用成本和Token。
- 支持多Agent分析：情绪、事件、个股映射、技术、风控、合议。
- 后台增加AI分析队列、成本统计、失败记录。

建议接口：

- `POST /api/jobs/ai/analyze`
- `GET /api/ai/usage`
- `GET /api/ai/records`
- `GET /api/ai/failures`

验收：

- 新新闻进入系统后自动完成结构化分析。
- 每个推荐股票能追溯到具体AI分析结果。
- AI接口失败时系统继续运行。

### M4：交易模拟和模型闭环

目标：模拟账户真正成为模型训练反馈源。

任务：

- 完善成交规则：T+1、涨跌停、滑点、手续费、仓位限制。
- 每日账户快照落盘。
- 每笔信号保存生命周期：生成、买入、持仓、卖出、未成交。
- 参数拟合加入手续费后收益。
- 增加按行业、事件类型、情绪强度、模型版本的表现统计。
- 自动生成卖出建议。

建议文件：

- `backend/app/quant/engine.py`
- `backend/app/quant/broker.py`
- `backend/app/quant/model_fit.py`
- `backend/app/quant/repositories.py`

验收：

- 切任意日期都能还原当时持仓和成交。
- 首页能显示当日买入、持仓、卖出建议。
- 后台能看到模型参数变更前后的回放差异。

### M5：前后台产品化

目标：用户能像使用交易软件一样查看和控制系统。

前台任务：

- 账户总览。
- 最新新闻。
- 今日买入。
- 当前持仓。
- 卖出建议。
- 历史成交。
- 交割单。
- 历史每日计划收益。
- 推荐详情弹窗：新闻、事件、Agent拆分、历史表现。

后台任务：

- 数据源配置。
- 自动任务状态。
- 策略参数。
- 模型拟合。
- AI成本。
- 日志和错误。
- 数据覆盖率。
- 备份和恢复。

验收：

- 前台不需要解释文字也能完成日常查看。
- 后台能完成所有关键配置和手动触发。
- 所有失败状态都有明确原因。

### M6：一键部署和持续运行

目标：服务器上可长期试运行，更新不丢数据。

任务：

- `scripts/install_server.sh`
- `scripts/update_server.sh`
- `scripts/restart_server.sh`
- `scripts/backup_data.sh`
- `scripts/restore_data.sh`
- `deploy/systemd/qt.service`
- `deploy/nginx/qt.conf`
- 健康检查脚本。
- 日志轮转说明。
- 更新前自动备份。

验收：

- 新服务器一条命令完成安装。
- 更新脚本不覆盖 `backend/data` 和 `.env`。
- 服务异常后 systemd 自动拉起。
- 更新失败可以恢复备份。

## 4. 建议目录结构

```text
backend/
  app/
    main.py
    quant/
      engine.py
      news_fetcher.py
      market_sync.py
      ai_analyzer.py
      scheduler.py
      broker.py
      repositories.py
      models.py
      biying_sync.py
frontend/
  index.html
  admin/
    index.html
scripts/
  install_server.sh
  update_server.sh
  restart_server.sh
  backup_data.sh
  restore_data.sh
deploy/
  systemd/
    qt.service
  nginx/
    qt.conf
docs/
  PRODUCT_REQUIREMENTS.md
  DEVELOPMENT_PLAN.md
  QUANT_LOGIC.md
  SERVER_DEPLOY.md
```

## 5. 自动任务设计

任务表：

| 任务 | 盘前 | 盘中 | 盘后 | 夜间 |
|---|---:|---:|---:|---:|
| 新闻抓取 | 5分钟 | 1-3分钟 | 10分钟 | 30-60分钟 |
| AI分析 | 增量 | 增量 | 批量补齐 | 低频补齐 |
| 行情快照 | 5分钟 | 30-60秒 | 停止 | 停止 |
| 分时K同步 | 推荐/持仓 | 推荐/持仓 | 当日补齐 | 历史补齐 |
| 日K同步 | 盘前补缺 | 低频 | 收盘固化 | 历史补齐 |
| 模拟交易 | 生成计划 | 分时风控 | 收盘结算 | 回放拟合 |
| 备份 | - | - | 1次 | 1次 |

每个任务需要记录：

- `job_name`
- `status`
- `enabled`
- `last_started_at`
- `last_finished_at`
- `next_run_at`
- `duration_ms`
- `success_count`
- `failure_count`
- `last_error`
- `last_payload`

## 6. 数据迁移计划

第一阶段继续兼容 JSON/CSV：

- `news_history.json`
- `news_analysis_records.json`
- `kline_day_cache/*.json`
- `kline_cache/*.csv`
- `quant_state.json`

第二阶段增加 SQLite：

- 先只新增写入，不删除旧文件。
- 提供导入脚本。
- 接口读取优先 SQLite，缺失时 fallback 到 JSON。
- 稳定后再停止散落 JSON 写入。

当前进度：日 K 同步已经直接写入 SQLite `market_daily_bars`，读取时 SQLite 优先、JSON 只补缺口；默认已经停止继续写 `kline_day_cache/*.json`，仅保留 `QT_WRITE_KLINE_JSON_CACHE=true` 作为旧脚本兼容开关。

## 7. 测试计划

单元测试：

- 新闻去重。
- 新闻日期回退。
- AI结构化解析。
- 行情格式标准化。
- 交易费用。
- T+1可用数量。
- 日期切换不显示未来持仓。

集成测试：

- 抓新闻 -> AI分析 -> 推荐 -> 模拟买入。
- 同步分时 -> 触发止盈/止损。
- 运行一天调度任务。
- 重启后状态恢复。

验收测试：

- 选择 `2026-05-20`，如果无当天新闻，页面必须提示数据实际日期。
- 有当天新闻后，前台最新新闻必须显示当天数据。
- 运行模拟交易后，成交、持仓、交割单一致。
- 服务器更新后，历史数据和配置不丢。

## 8. 风险和处理

| 风险 | 处理 |
|---|---|
| 新闻源接口变化 | 多来源、失败降级、后台可见错误 |
| 必盈额度/限流 | 全局限流、缓存、分批任务、失败退避 |
| AI输出不稳定 | JSON Schema 校验、重试、规则降级 |
| 回放时间穿越 | 所有接口强制 `as_of`，测试覆盖 |
| 生产数据丢失 | 更新前备份、数据目录保护、恢复脚本 |
| 密钥泄露 | 环境变量优先、`.env` 不提交、日志脱敏 |
| 页面变复杂 | 前台交易终端聚焦使用，后台承载配置和排障 |

## 9. 优先级队列

P0：

- 自动新闻抓取。
- 自动行情同步。
- AI增量分析。
- 调度器和任务状态。
- 前台最新新闻、买入、持仓、成交、交割单。
- 一键部署/更新。

P1：

- 数据覆盖率看板。
- AI成本看板。
- 卖出建议强化。
- 手续费后参数拟合。
- SQLite数据层。

P2：

- 更多新闻源。
- 通知系统。
- 行业/概念热度图。
- 模型版本对比。
- 旧商业功能按需恢复。
