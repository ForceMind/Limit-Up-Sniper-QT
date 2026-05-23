# 架构优化与持续进化计划

本文是当前项目从“能跑”进入“长期可复用量化系统”的执行计划。目标不是把所有逻辑堆到页面按钮里，而是形成稳定的数据层、策略层、回测层、进化层和展示层。

## 0. 代码结构体检

体检时间：2026-05-23。

当前规模最大的文件：

| 文件 | 行数 | 判断 |
|---|---:|---|
| `backend/app/quant/engine.py` | 4249 | 量化核心过于集中，已经混合数据读取、事件识别、因子、评分、回放、账户和策略参数 |
| `frontend/admin/index.html` | 2889 | 后台单文件过大，用户管理、数据管理、任务日志、策略模型和配置混在一个页面 |
| `backend/app/main.py` | 1994 | FastAPI 入口承担了 88 个路由装饰器、认证中间件、数据导入任务、WebSocket 和静态托管 |
| `frontend/index.html` | 1641 | 前台终端把登录、概览、账户、策略、新闻和移动端状态放在一个文件里 |
| `scripts/migrate_data_to_sqlite.py` | 1181 | 迁移脚本可用，但数据源解析、去重、入库和校验混在一起 |
| `backend/app/quant/evolution.py` | 960 | 进化、回测结果保存、模型排序和状态文件兼容逻辑开始变重 |
| `backend/app/quant/jobs.py` | 861 | 调度、日志、内存保护和任务执行都在一个模块里 |
| `scripts/common.sh` | 684 | 部署公共脚本已经包含 Nginx、systemd、SQLite、接口验证和中文输出 |

可以用以下命令重新生成体检报告：

```bash
python scripts/architecture_report.py
```

### 0.1 不合理的功能设计

- `backend/app/main.py` 不应该继续新增业务逻辑。入口文件只应创建 FastAPI、注册中间件、注册路由和托管静态文件；认证、前台、后台、任务、数据、量化接口要拆到独立 router。
- `backend/app/quant/engine.py` 不应该同时做数据仓库、因子计算、策略评分、回放和账户。长期看会导致一个修复影响全系统，也很难定位服务器性能问题。
- 前后台页面不应该继续堆单文件 Vue。后台用户管理已经需要独立弹窗或页面，后续数据管理、策略模型、任务日志也应该有单独模块。
- 自动任务不应该只靠进程内线程表达状态。系统要长期运行进化模型，任务状态、进度、最后错误和下一次运行时间必须落库。
- 数据层不应该继续“SQLite + 散落 JSON 双主”。JSON 只能作为迁移来源和兼容缓存，长期主存储应收敛到 SQLite，未来数据量扩大后再迁 PostgreSQL。
- 部署更新不应该每次重复做全量迁移或反复改 Nginx。日常 `qt update` 应默认做智能验证，只有首次、数据库缺失、迁移脚本变化或显式 `qt migrate` 才全量合并历史文件。
- 前台接口不应该登录后一次性拿全量数据。首页只拿概览、当前账户、当前策略和少量新闻；成交、交割单、回测明细、模型记录按需分页加载。
- “样例数据”过滤不应该散落在页面和多个接口里。样例识别应集中在数据层或仓库层，业务接口默认不返回样例，后台保留清理入口和诊断入口。

### 0.2 目标目录拆分

第一阶段只移动边界，不改变行为：

```text
backend/app/
  main.py                  只保留 app 创建、中间件、路由注册、静态托管
  api/
    auth.py                登录、注册、状态、用户资料
    front.py               前台概览、账户、推荐、日计划
    admin.py               后台快照、用户管理、备份、导入、重启
    jobs.py                任务状态、任务触发、日志
    data.py                必盈、K线、龙虎榜、数据覆盖率
    quant.py               回测、策略、模型、组合、时间线
  services/
    auth_service.py
    strategy_service.py
    account_service.py
    data_import_service.py
    job_service.py
  repositories/
    sqlite.py
    news_repository.py
    market_repository.py
    strategy_repository.py
  quant/
    engine.py              保留兼容 facade，逐步变薄
    factors.py
    backtest.py
    accounting.py
    event_classifier.py
```

拆分顺序必须先 API router，再 service，再 repository。这样每一步都能用现有页面和接口验证，不需要一次性大改。

## 1. 当前问题判断

### 1.1 16 个策略收益没有差异的原因

已确认的代码级原因：

- 遗传进化会变异 `sentiment_weight`、`event_weight`、`technical_weight`、`risk_weight`。
- 但实际 Agent 打分里使用的是全局 `model_weights()`，没有读取候选策略自己的临时参数。
- 因此候选策略的权重基因没有真正参与回测，只剩阈值、止盈、止损、持仓天数等少量参数在变化。
- 如果服务器数据覆盖不足、闭环交易数过少，多个候选很容易全部收益为 `0` 或收益接近。

已修正方向：

- Agent 打分改为使用当前策略参数中的四类权重。
- 遗传进化默认使用分时回放，保留完整成交、交割单和资金曲线。
- 引入第一批扩展因子，让候选策略不仅调阈值，也能调因子偏好。

### 1.2 系统没有持续进化的原因

当前调度器已经周期运行新闻、AI、日K补齐、龙虎榜、模拟交易和策略复盘，但策略进化只在后台点击按钮时运行，没有纳入常驻调度。

已修正方向：

- 新增 `strategy_evolution` 调度任务。
- 默认不自动开启 `STRATEGY_EVOLUTION_ENABLED`，避免小服务器刚启动就运行重型回测导致内存占满；确认数据覆盖和内存稳定后再显式开启。
- 默认每 6 小时运行一次，可用环境变量调整。
- 进化运行中或暂停时不会重复启动。

## 2. 目标架构

```text
数据源层
  新闻 / AI缓存 / 必盈日K与分钟K / 龙虎榜 / 后续市场宽度和板块数据

数据仓库层
  SQLite 起步，后续可迁 PostgreSQL
  news_raw / news_analysis / news_events
  market_daily_bars / market_minute_bars / lhb_records
  strategy_runs / strategy_trades / strategy_models

因子层
  新闻情绪、事件影响、历史兑现、技术走势、风控
  动量、放量、突破、龙虎榜资金和席位
  后续加入板块强度、涨停梯队、封单、竞价、市场宽度

策略层
  参数化策略模型
  多策略并行回测
  模拟账户跟随某个策略

回测与进化层
  分时优先，日K兜底
  每个策略都有完整成交、交割单、资金曲线
  遗传进化持续运行，保存可复用模型

展示与运维层
  前台只查看、注册、选择跟随策略
  后台管理数据、任务、用户、模型、配置
  qt 命令提供服务器运维面板
```

## 3. 分阶段计划

### P0：首屏稳定与任务拆分

状态：已开始。

任务：

- 首屏接口轻量化。
- 重型接口按模块加载。
- 524/504/413 显示中文错误。
- 登录状态检查不阻塞页面。

验收：

- 前台和后台不会卡在“正在检查登陆状态”。
- Cloudflare 524 不再由首屏大快照触发。

### P1：进化模型真正差异化

状态：本轮执行。

任务：

- 修正候选策略权重没有进入 Agent 打分的问题。
- 遗传进化默认使用分时回放。
- 模型保存完整交易数据、交割单和每日清算。
- 新增可进化因子：动量、放量、突破、龙虎榜。
- 后台和前台展示每个策略的收益、回撤、胜率、闭环交易。

验收：

- 同一次 16 个策略回测中，至少应看到不同的参数、目标函数和收益表现。
- 如果全部为 0，后台要能从数据覆盖和闭环交易数判断是数据问题，而不是进化逻辑没有生效。

### P2：常驻进化闭环

状态：本轮执行。

任务：

- 调度器周期运行 `strategy_evolution`。
- 支持暂停、恢复、自动跳过重复运行。
- 进化前自动补齐事件相关日K。
- 可配置是否自动应用最佳模型，默认不自动应用。
- 后台点击启动进化时只启动后台任务，接口立即返回，避免 Cloudflare 524。

环境变量：

```bash
STRATEGY_EVOLUTION_ENABLED=false
STRATEGY_EVOLUTION_INTERVAL_SECONDS=21600
STRATEGY_EVOLUTION_GENERATIONS=1
STRATEGY_EVOLUTION_POPULATION_SIZE=16
STRATEGY_EVOLUTION_MODE=intraday
STRATEGY_EVOLUTION_APPLY_BEST=false
QT_MEMORY_GUARD_ENABLED=true
QT_MEMORY_GUARD_PERCENT=88
QT_MEMORY_GUARD_AVAILABLE_MB=1024
```

验收：

- 当 `STRATEGY_EVOLUTION_ENABLED=true` 时，服务器启动后不需要手点按钮，也会周期产生模型。
- 进化任务有中文日志、进度、运行状态和下次运行时间。

### P3：数据入库与回测结果持久化

状态：本轮执行。

任务：

- 把策略运行结果从 JSON 状态逐步迁入 SQLite。
- 新增 `strategy_models` 和 `strategy_model_records`，持久保存模型参数、回测摘要、成交、交割单和每日清算。
- `strategy_runs` 和 `strategy_model_metrics` 继续记录每轮进化摘要和每代最优指标。
- 页面读取“当前 JSON 状态 + SQLite 历史模型”的合并结果，不依赖最后一次 JSON 状态。
- 数据迁移脚本把旧的 `strategy_evolution_state.json` 合并进 SQLite，上传数据包后只追加/去重，不覆盖服务器已有结果。

验收：

- 服务器重启后仍能看到历史所有策略模型。
- 每个模型都可以打开完整交割单。
- 数据包上传合并不会覆盖已有策略结果。

### P4：因子体系扩展

第一批已接入：

- 新闻情绪。
- 事件影响。
- 历史兑现。
- 日K动量。
- 放量。
- 20 日高位突破。
- 龙虎榜净买入和活跃席位。

下一批建议：

- 板块强度：同概念股票涨跌幅、涨停数量、连板高度。
- 市场宽度：上涨家数、涨停/跌停、成交额、北向或大盘趋势。
- 竞价因子：开盘涨幅、竞价量比、竞价金额。
- 涨停结构：首板、二板、断板反包、炸板回封。
- 龙虎榜席位画像：席位近期胜率、偏好题材、隔日溢价。
- 新闻传播强度：来源权重、重复报道、时间衰减。

验收：

- 每个因子都有可解释字段。
- 每个因子都能在回测中开关或调权重。
- 不引入未来函数。

## 4. 执行原则

- 数据补齐优先于复杂模型，否则收益全 0 无法判断策略优劣。
- 分时回放优先，日K只做兜底。
- 每个模型必须可复用：参数、收益、回撤、胜率、交易、交割单都要保存。
- 自动进化默认不自动应用最佳模型，避免服务器无人值守时把线上策略突然切换。
- 所有长任务必须有进度和中文日志，不走同步大请求。

## 5. 近期验收命令

```bash
python scripts/architecture_report.py
python -m compileall backend/app
python scripts/check_data_coverage.py
python scripts/security_scan.py
```

后台验收：

- 打开后台策略页，确认进化状态有进度。
- 点击“启动进化”，16 个模型应显示不同的目标值和收益。
- 点击任一模型“查看交割单”，应能看到该模型独立的回测交易数据。
