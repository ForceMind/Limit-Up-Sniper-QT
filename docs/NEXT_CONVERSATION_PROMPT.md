# 新对话可复制提示词

下面这段可以直接复制到新对话里使用。把最后一行“本轮目标”改成你想继续做的目标即可。

```text
你是 Codex，在本地项目 E:\Privy\Limit-Up-Sniper-QT 中继续开发“涨停狙击手”。

请先阅读并遵守这些文档：
- docs/PROJECT_OPTIMIZATION_MASTER_PLAN.md
- docs/STRATEGY_RUNTIME_REQUIREMENTS.md
- docs/QUANT_LOGIC.md
- docs/SERVER_DEPLOY.md
- docs/SERVER_DATA_SECURITY.md

当前产品核心逻辑：
1. 系统不是只有一个“系统运行策略”。服务器应该持续收集公共数据，并训练/回测出多个可复用策略模型。
2. 用户注册后设置模拟资金，并选择一个策略跟随。
3. 用户账户从注册或切换策略的时间开始运行，不继承策略过去的历史持仓。
4. 系统默认基础参数只用于诊断、默认调参和生成新策略，不代表任何用户正在跟随。
5. 策略模型共用新闻、行情、龙虎榜、AI 分析等公共数据，但每个策略都有自己的基础参数、信号、成交、持仓和账户结果。
6. 小资金策略必须适配 1 万、2 万-5 万、5 万-10 万、10 万以上资金，不要让小资金买不起一手。
7. 前台不能出现后台初始化或管理入口；未登录只能看概览和新闻，登录后才能看账户、策略、持仓、成交等。
8. 后台必须可管理用户、数据、缓存、任务、日志、调试通道和数据库。

当前已完成到 v0.2.137：
- 前台用户 profile 有 simulated_cash、strategy_model_id、follow_started_at、follow_start_date。
- 切换策略或调整模拟资金会重置跟随开始时间。
- 前台账户按 follow_start_date 裁剪，不继承旧持仓；真正为每个用户从注册日完整独立重跑策略暂不开发，等明确要求后再做。
- 策略复盘任务会把每个策略的每日信号、成交、持仓、账户快照和清算写入 strategy_daily_signals、strategy_runtime_trades、strategy_runtime_positions、strategy_runtime_snapshots、strategy_runtime_settlements。
- 前台账户优先读取 user_follow_snapshots；未命中时从 strategy_runtime_*、短缓存、模型记录或即时回放派生，并写入 user_follow_positions、user_follow_trades。
- 用户注册、设置资金或切换策略会写入 user_follow_periods。
- 后台用户管理页展示当前跟随周期、账户快照、持仓和最近成交来源。
- 后台“持仓成交”按策略查看，不再把系统默认基础参数当成一个独立账户。
- 推荐和日计划使用 frontend_payload_cache SQLite 短缓存；缓存未命中默认只返回 pending/disabled，不再自动触发 frontend_payload_precompute，除非显式开启 QT_FRONT_PAYLOAD_AUTO_PRECOMPUTE_ON_MISS。
- `/api/jobs/status` 返回 frontend_payload_policy，后台自动调度器面板会按真实配置显示前台预计算是手动还是自动，不再把默认手动配置误显示为自动预计算。
- `backend/app/routers/admin_jobs.py` 接管任务状态、任务日志、运行日志、调度器启动/停止、任务暂停/恢复/停止路由，原 URL 和参数合同不变。
- 前台轻量快照里的任务摘要使用 `job_manager.frontend_status()`，只读调度器、running 和 paused 三个轻字段，不再触发完整后台任务状态巡检。
- `backend/app/routers/admin_job_runs.py` 接管具体任务执行入口的路由声明，新闻、AI、行情、交易、策略复盘、前台预计算、账户预热、每日交易和系统启动流程的原 URL 不变。
- `backend/app/routers/core_system.py` 接管版本、认证、调试、运行配置和 `/api/status` 路由声明；`/api/status` 只读轻量任务摘要，不再触发完整任务状态巡检。
- `backend/app/routers/quant_basic.py` 接管基础量化读写接口路由声明，dashboard、推荐、日计划、基础参数、事件、新闻、相关性、组合、交易账户、手动运行和新闻历史 URL 不变。
- 前台公开快照和登录快照共用 `front_snapshot_news` 新闻摘要短缓存，默认 `QT_FRONT_SNAPSHOT_NEWS_CACHE_TTL_SECONDS=30`。
- 前台推荐/日计划预计算默认只手动小批量分片：`QT_FRONT_PAYLOAD_PRECOMPUTE_LIMIT_USERS=8`、`QT_FRONT_PAYLOAD_PRECOMPUTE_MAX_SECONDS=20`、`QT_FRONT_PAYLOAD_PRECOMPUTE_INITIAL_DELAY_SECONDS=1800`；轻量快照默认不因新闻/日期缺失回退完整引擎加载。
- 后台慢任务触发接口默认 background=true；策略复盘、模型训练和回测默认只手动触发，策略库查看交割单默认读取已保存模型记录，手动策略复盘和策略进化默认使用独立 Python 子进程运行。
- 独立进程任务会在状态接口读取时自动巡检，异常退出但未写回结果时标记失败并写运行日志。
- 策略复盘使用 QT_STRATEGY_REPLAY_BATCH_DAYS 分批推进，默认 15 天一批，并记录 strategy_replay_cursor；自动调度默认不运行策略复盘。
- 前台和后台会把 Cloudflare/Nginx 的 502/504/524 HTML 错误页转换为中文诊断信息；v0.2.136 起即使代理把 HTML 错误页或静态首页误以 2xx 返回，也会先识别为非业务 JSON，不再把整页代理错误 HTML 或 JSON 解析异常显示到业务页面里。
- 访问审计支持后端分页和用户/IP/路径/状态码筛选；异常访问支持后台一键拉黑全部未封禁异常 IP。
- 前台状态摘要里的最新新闻时间有 `QT_LATEST_NEWS_TIME_CACHE_TTL_SECONDS` 秒级短缓存，默认 5 秒，减少首屏、刷新和切换策略后的重复 SQLite 读取。
- 前台默认日期和跟随窗口裁剪有 `QT_DATA_DATE_CACHE_TTL_SECONDS` 秒级短缓存，默认 10 秒，普通前台请求不再为了最新日期加载完整事件列表。
- 轻量 `/api/front/snapshot?light=true` 读取账户时不再同步写入用户跟随账户派生结果，首屏只读，落库交给账户预热或明确账户接口。
- `/api/front/trading_account` 默认 `QT_FRONT_ACCOUNT_PERSIST_ON_READ=false`，账户 GET 只读；需要补写用户跟随账户时异步排队 `frontend_account_precompute`。
- 前台策略页切换策略只保存跟随关系，不再强制跳过账户缓存或等待账户重建；持仓/成交页再按需读取已落库结果。
- 前台账户接口缓存未命中时默认返回 pending，不再同步即时回放；生产环境用手动策略复盘或策略运行小包生成账户数据。
- 前台会明确展示账户运行结果待生成状态，不再把 pending 账户误显示为真实空仓或 0 收益。
- 前台账户日常路径默认不再读取完整策略模型交割单做同步兜底，避免大体积 `strategy_model_records` 拖慢 API。
- 后台新增 `frontend_account_precompute` 前台账户预热任务，可在策略复盘或导入运行结果后批量生成用户账户快照，默认不触发即时回放。
- 前台账户预热任务支持独立 Python 子进程，后台按钮默认 `process=true`，避免批量账户派生占用 API 进程。
- 用户注册、切换策略或调整模拟资金后会自动排队单用户账户预热；保存接口快速返回，预热默认走独立进程。
- 保存 profile 的账户预热结果区分 `queued` 和 `worker_started`；`account_precompute_queued=true` 表示用户已进入待处理队列。
- 自动账户预热现在先写入 `frontend_account_precompute_queue.json` 队列，再由 `frontend_account_precompute` 批量消费；同名任务运行中时后续用户不会丢失。
- 账户预热队列使用跨进程锁保护，API 进程和独立 worker 同时读写时不会覆盖队列；陈旧锁会按超时恢复。
- 前台账户接口会检查账户预热队列残留并补启动消费 worker，避免 worker 刚退出时新增队列项长期滞留。
- 前台注册、切换策略或调整模拟资金时，profile 保存接口默认只把账户预热写入队列，不再同步启动 worker；账户页读取或后台手动预热任务再消费队列。
- 后台任务状态会显示账户预热队列长度、原因分布、最早/最新排队时间和队列锁状态，方便定位账户预热积压或锁残留。
- 后台手动“前台账户预热”会优先消费已排队用户；队列为空时才执行普通批量用户预热。
- 前台 profile 保存不再清空全局内存缓存，前台快照缓存键纳入跟随开始时间；切换策略不会让覆盖率、策略列表等全局缓存一起冷启动。
- `/api/data/coverage` 缓存未命中时默认返回 pending 并启动 data_coverage 后台任务，后台全量快照也优先走缓存/任务路径。
- `/api/quant/model/backtest?recompute=true&manual=true` 默认返回 pending 并启动 model_backtest 后台任务；完成后读取短缓存，只有 `manual=true&defer=false` 才同步重算。
- `/api/quant/model/backtest?recompute=true` 和 `/api/quant/backtest` 现在要求显式 `manual=true` 才会在缓存未命中时启动重任务；普通刷新或误调用返回 `manual_required`。
- 训练、策略复盘、模型回测、通用回测、时间线回测和参数拟合共享 `QT_HEAVY_JOB_MAX_CONCURRENT` 重任务并发闸门，默认同一时间只允许一个重任务独立进程运行。
- 单模型回测重算和时间线回测默认使用独立 Python 子进程，不再占用 API 进程内后台线程。
- 后台概览和运维页展示重任务槽位、正在运行的重任务、PID 和进度摘要，方便解释 `busy` 和服务器忙碌状态。
- 单模型回测重算和时间线回测的短缓存已从进程内内存迁移到 SQLite `frontend_payload_cache`，独立子进程生成后父 API 进程可读取。
- 前台 profile 保存后的用户跟随周期落库默认异步执行；切换策略不会再等待 `user_follow_periods` 的 SQLite 写锁，响应会带 `follow_period_record` 和 `profile_update_elapsed_ms`。
- 前台 profile 保存后的账户预热队列入队默认异步执行；切换策略不会再等待 `frontend_account_precompute_queue.json` 跨进程锁，响应可能返回 `account_precompute.status=queued_async`。
- 前台账户接口返回 pending 时会异步补当前用户到账户预热队列并启动预热 worker，避免 profile 异步入队失败后账户长期不预热。
- 前台账户 pending 文案会读取 `account_precompute` 状态，区分正在写队列、正在启动 worker、已排队或已启动预热任务。
- 异步账户预热入队有短时间去重，默认 5 秒内相同用户/原因/日期不会重复创建后台入队线程。
- 登录首屏 `/api/front/snapshot` 返回 pending 账户时，也会异步补账户预热队列并启动 worker，不再依赖前端额外请求账户详情接口。
- 前台账户 pending 文案会识别 `account_precompute.deduped=true`，提示已有相同预热请求正在处理。
- `/api/jobs/status` 返回 `frontend_account_precompute_async`，后台运维页展示账户预热异步去重保护和队列锁状态。
- `POST /api/front/profile` 返回 `profile_update_trace` 和 `profile_update_slow_stage`，可直接定位切换策略慢在 profile 保存链路的哪个阶段。
- 前台 profile 保存轻量上下文遇到缺失/过期策略 ID 时不再回退全量策略目录，直接按资金规模推荐资金档策略。
- 前台 profile 保存会复用保存结果构建上下文，不再保存后额外读取一次用户资料文件。
- 前台 profile 保存会在首次写入前把过期、缺失或 `active` 策略 ID 解析为推荐资金档策略，避免二次 profile 写入。
- 前台 profile 的策略目录归一化和保存前解析已迁入 `app.quant.front_profile`，并有独立单测覆盖，后续可继续拆 front router。
- 前台快照不再返回后台运行日志，也不在前台状态摘要里暴露服务器 `data_dir`；前台任务状态只保留轻量摘要并通过 `QT_FRONT_JOBS_CACHE_TTL_SECONDS` 做短缓存。
- 新闻最新时间、新闻列表和结构化事件列表的 SQL 去掉包列排序，并补充 `idx_news_raw_timestamp_date`、`idx_news_raw_date_timestamp`、`idx_news_events_date_impact` 热路径索引；部署脚本会在跳过全量迁移时确认这些索引。
- SQLite schema 已补充前台账户和策略运行热路径复合索引，覆盖 user_follow 快照明细、strategy_runtime 运行读取和 strategy_daily_signals 策略矩阵读取。
- 策略演进 SQLite 连接按数据库路径和 schema 版本缓存 schema 初始化，同一 API 进程内不再每次连接都重复执行完整建表/建索引脚本。
- 策略信号 feed 最新日期定位改为按日期索引倒序 LIMIT 1，不再通过全表 COUNT 判断是否有数据。
- 用户当前跟随周期查询去掉 `COALESCE(ended_at, '')` 包列条件，并新增 `user_follow_periods(username, ended_at, started_at, created_at)` 当前周期索引；历史 `ended_at=NULL` 周期仍会被正确关闭。
- 策略运行快照的 `daily_runtime` 来源过滤改为 source 前缀范围条件，并新增 `idx_strategy_runtime_source`，避免 `LIKE` 前缀匹配在大表上不稳定走索引。
- 回测、拟合和时间线等重任务被并发闸门拦截或暂停时，前台/API 会返回真实的 `busy`/`paused`/`error`，不再统一显示为 pending。
- `/api/quant/timeline` 和 `/api/quant/intraday_timeline` 默认返回 pending 并启动 quant_timeline 后台任务；完成后读取短缓存，只有 defer=false 才同步重算。
- 前台切换策略保存 profile 默认使用轻量目录，不再返回完整策略目录；保存后不自动拉账户接口，只显示待预热账户状态。
- `/api/quant/backtest?manual=true` 默认返回 pending 并启动 quant_backtest 独立进程；完成后读取 SQLite 短缓存，只有 `manual=true&defer=false` 才同步等待。
- 前台切换到策略库模型时，轻量 profile 保存会按模型 ID 单条读取策略参数，不再加载完整策略目录。
- `/api/quant/fit_strategy` 默认返回 pending 并启动 fit_strategy 独立进程；只有 defer=false 才同步等待参数拟合。
- 后台“同步分时行情”和 `/api/data/biying/sync_intraday` 默认启动 market_sync 独立进程；只有 process=false&background=false 才同步抓取。
- 后台“补齐缺失日K”和“拉取龙虎榜”默认启动 kline_fill、lhb_sync 独立进程；系统启动流程内部仍保持顺序执行。
- 后台“运行模拟交易”、`/api/jobs/daily/run` 和自动调度交易循环默认启动 trade_cycle 独立进程；系统启动流程内部仍保持顺序执行。
- 后台“AI 分析新闻”和自动调度 AI 分析默认启动 ai_analysis 独立进程；系统启动流程内部仍保持顺序执行。
- `/api/data/coverage` 数据覆盖率诊断写入 SQLite 短缓存；缓存未命中时默认启动 data_coverage 独立进程。
- 后台“抓取新闻”和自动调度新闻抓取默认启动 news_fetch 独立进程；系统启动流程内部仍保持顺序执行。
- 后台“系统启动”默认启动 system_startup 独立进程；子进程内保持新闻、AI、日K、龙虎榜、分时、交易循环顺序执行，策略复盘、训练和回测仍只手动触发。
- 前台保存 profile 的轻量策略上下文不再读取资金档运行摘要；切换策略只保留定位当前策略所需参数。
- 前台保存或切换策略后，概览页不再自动补拉推荐和日计划；只有当前在买入页才刷新买入相关数据。
- 前台轻量快照默认不加载完整策略目录；进入策略页时再通过 `/api/front/strategy_models` 按需读取完整策略库。
- 数据包导入改为流式合并 SQLite，避免 200MB 级数据库文件在服务器上一次性读入内存。
- 可以用 python scripts/package_strategy_runtime_export.py 生成只包含 strategy_runtime_* 的小包，把本地复盘结果合并到服务器。
- 后台数据库页可以查看和清理缓存。
- 后台新增 `/api/admin/strategy_runtime/matrix` 只读矩阵接口，可查看每个策略的运行结果是否已落库、最新信号、成交、持仓、收益和回撤，用于排查切换策略后账户 pending 或变慢。
- 后台“策略”页已经展示策略运行矩阵，运维可直接在页面确认目标策略是否缺少运行结果。
- 策略运行矩阵的 payload 组装已拆到 `app.quant.strategy_runtime_matrix`，并有服务级单测覆盖去重、上限和状态合并规则。
- `/api/admin/strategy_runtime/matrix` 路由已迁入 `app.routers.admin_strategy_runtime`，保留原 URL 和查询参数，为继续拆 `main.py` 做准备。
- `/api/admin/trading_account` 和 `/api/admin/strategy_runtime/replay` 也已迁入 `app.routers.admin_strategy_runtime` 注册，保留原 URL 和返回结构。
- 前台 profile 保存和注册显式只排队账户预热，不在用户请求里启动账户预热 worker；旧服务器环境变量把 `QT_FRONT_ACCOUNT_START_WORKER_ON_PROFILE=true` 时也不会拖慢切换策略保存链路。
- 轻量 `/api/front/snapshot?light=true` 命中 `strategy_runtime_snapshots` 每日快照时走 `runtime_snapshot_fast_path=true`，不扫描完整 `strategy_runtime_trades` 或重建交割单。
- `/api/front/profile` 的 GET/POST 路由已迁入 `app.routers.frontend_profile`，保留原 URL、`include_catalog` 查询参数和 payload 回调行为。
- `/api/front/public_snapshot`、`/api/front/snapshot`、`/api/front/strategy_models`、`/api/front/trading_account` 路由已迁入 `app.routers.frontend_runtime`，保留原 URL、参数默认值和 payload 回调行为。
- `/api/front/recommendations` 和 `/api/front/daily_plan` 路由已迁入 `app.routers.frontend_signal`，保留原 URL、参数默认值、短缓存和 pending 预计算语义。
- `/api/admin/snapshot` 和 `/api/admin/model_signals` 路由已迁入 `app.routers.admin_overview`，保留原 URL、参数默认值和 payload 回调行为。
- `/api/admin/database/tables`、`/api/admin/database/table/{table_name}`、`/api/admin/cache/status`、`/api/admin/cache/clear` 路由已迁入 `app.routers.admin_data_cache`，保留原 URL、分页参数、scope 参数和 payload 回调行为。
- `/api/admin/access_logs`、`/api/admin/access_security` 和访问安全封禁相关 POST 路由已迁入 `app.routers.admin_access`，保留原 URL、筛选参数、封禁 body 和 payload 回调行为。
- `/api/admin/frontend_users` 及其创建、更新、重置密码、封禁、解封和删除子路径已迁入 `app.routers.admin_frontend_users`，保留原 URL、路径参数、body 和 payload 回调行为。
- 部署脚本会验证版本、接口模块和数据库表结构。
- 前台预计算默认手动化：QT_FRONT_PAYLOAD_PRECOMPUTE_ENABLED=false，后台按钮默认 force=false，推荐/日计划缓存 TTL 默认 1800 秒。

重要安全要求：
- 不要提交 .env、真实数据库、密钥、日志、备份包、服务器运行数据。
- 不要打印或复述临时调试密钥。
- 不要回滚我已有的改动。
- 修改代码前先看 git status --short。

开发要求：
- 用中文和我沟通。
- 先读代码和现有模式，不要重写一套平行实现。
- 每次只聚焦一个可验证目标。
- 改动后更新 VERSION 和相关 docs。
- 跑必要验证：
  python -m pytest backend\tests -q
  python scripts\security_scan.py
  git diff --check
  bash -n qt.sh
  bash -n scripts/qt.sh
  bash -n scripts/common.sh
  bash -n scripts/update_server.sh
- 最后告诉我改了什么、为什么这么改、怎么部署、怎么验证。

本轮目标：把补数诊断和其它可能超过 5 秒的数据接口继续改为后台任务 + 进度 + 缓存读取。
```

## 可替换的本轮目标

```text
本轮目标：把补数诊断和其它可能超过 5 秒的数据接口继续改为后台任务 + 进度 + 缓存读取。
```

```text
本轮目标：增加后台“策略运行矩阵”，展示每个策略最近运行日期、信号数、成交数、收益、回撤和运行数据是否缺失。
```

```text
本轮目标：拆分 backend/app/main.py，把前台、后台、任务、数据、量化和策略接口逐步迁移到独立 router，保持行为不变并补测试。
```
## v0.2.106 最新状态补充

- 访问审计默认异步写入 `access_logs.json`，避免普通 API 响应等待访问日志整文件重写。
- `frontend_payload_precompute` 和 `frontend_account_precompute` 已纳入重任务并发闸门，和策略复盘、训练、回测、拟合同样受 `QT_HEAVY_JOB_MAX_CONCURRENT` 控制。
- 日常生产链路使用已训练模型做新闻分析、买卖信号和账户更新；训练、复盘、回测、参数拟合只作为后台手动维护动作。

## v0.2.107 最新状态补充

- 访问审计异步线程会按 `QT_ACCESS_LOG_BATCH_SIZE` 和 `QT_ACCESS_LOG_BATCH_WINDOW_MS` 批量写入 `access_logs.json`，减少高频请求下的日志写放大。

## v0.2.108 最新状态补充

- `auth.json` 读取增加进程内缓存，按文件路径、mtime 和大小自动失效，写入后刷新缓存；前台 profile 保存和登录态校验减少重复文件读取。

## v0.2.109 最新状态补充

- 前台 scope 鉴权不再构建完整 `auth_status()` 摘要；只有后台 admin scope 仍执行 setup 检查，减少切换策略和前台快照请求的认证固定开销。

## v0.2.110 最新状态补充

- `/api/front/public_snapshot` 和 `/api/front/snapshot` 不再返回后台 `logs`，前台页面也不再展示后台运行日志；日志、数据目录和完整运维状态继续留在后台管理侧。
- 新增 `QT_FRONT_JOBS_CACHE_TTL_SECONDS`，默认 3 秒，用于前台轻量任务状态短缓存，减少首屏和切换策略后快照请求的固定状态读取开销。

## v0.2.111 最新状态补充

- 新闻仓库热路径 SQL 改为索引友好排序，新增测试固定查询计划命中新闻索引且不使用临时排序。
- `scripts/common.sh` 的 SQLite 表结构验证会补齐并确认新闻热路径索引，确保已有服务器数据库不用强制全量迁移也能获得该优化。

## v0.2.112 最新状态补充

- 前台快照和状态摘要复用最新新闻时间短缓存，默认 5 秒，避免用户切换策略后连续刷新时每个快照请求都查 SQLite 最新新闻时间。
- 该缓存只保存公开时间字符串，不影响策略、账户、新闻内容或后台运维安全边界。

## v0.2.113 最新状态补充

- 前台 `as_of`、回放窗口和跟随开始日裁剪使用 SQLite 日期边界短缓存，默认 10 秒，避免快照和账户请求调用完整事件集合来推导最新日期。
- 新增测试确认 SQLite 有日期数据时，前台日期判断不会调用 `quant_engine.latest_event_date()` 或 `quant_engine.first_data_date()`。

## v0.2.114 最新状态补充

- 轻量前台快照读取账户结果时传入 `persist_derived=false`，避免运行表命中后在首屏请求内同步写入账户缓存或 `user_follow_*`。
- 新增测试确认轻量快照传参和运行表命中只读行为；账户派生落库继续由账户预热、显式账户接口或后台维护任务完成。

## v0.2.115 最新状态补充

- 前台账户 GET 默认只读，`persist_derived=false` 且不记录跟随周期；运行表或缓存命中后直接返回页面结果。
- 如果用户跟随账户还需要落库，接口通过 `user_follow_persist_deferred` 触发账户预热队列，后台 worker 负责补写。

## v0.2.116 最新状态补充

- 后台新增 `/api/admin/strategy_runtime/matrix` 只读接口，按资金档策略和策略库模型汇总 `strategy_runtime_*` 与 `strategy_daily_signals` 的落库状态。
- 该接口用于确认切换策略后账户慢是运行结果缺失、信号缺失还是账户预热滞后；它不触发训练、复盘、回测、账户预热或即时回放。

## v0.2.117 最新状态补充

- 后台“策略”页新增“策略运行矩阵”面板，展示策略数量、已落库、缺运行结果、有信号以及逐策略运行区间、信号、成交、持仓、收益和资金档。
- 页面刷新矩阵只读取 `/api/admin/strategy_runtime/matrix`，不触发训练、复盘、回测、账户预热或即时回放。

## v0.2.118 最新状态补充

- 策略运行矩阵的组装逻辑已迁入 `app.quant.strategy_runtime_matrix`，`main.py` 只负责取策略目录、运行汇总和信号 feed。
- 新增 `backend/tests/test_strategy_runtime_matrix.py`，固定矩阵目录去重、上限裁剪、状态判断和信号摘要合并行为，为后续拆后台 router 做准备。

## v0.2.119 最新状态补充

- 新增 `backend/app/routers/admin_strategy_runtime.py`，`/api/admin/strategy_runtime/matrix` 由独立 router 注册，`main.py` 只保留 payload 回调和 router 注册。
- 新增 `backend/tests/test_admin_strategy_runtime_router.py`，确认矩阵路由保留 `as_of`、`limit_models`、`include_signals` 查询参数合同。

## v0.2.120 最新状态补充

- `backend/app/routers/admin_strategy_runtime.py` 继续接管 `/api/admin/trading_account` 和 `/api/admin/strategy_runtime/replay`。
- 路由测试扩展到三条后台策略运行接口，确认策略账户和回放接口的 `as_of`、`model_id`、`initial_cash`、`start_date`、`limit` 查询参数合同不变。

## v0.2.121 最新状态补充

- `POST /api/front/profile` 和前台注册路径显式传入 `start_worker=false`，账户预热只排队不启动 worker，避免旧配置把切换策略保存拖入进程启动路径。
- `load_runtime_account(..., hydrate_trades=false)` 会优先从 `strategy_runtime_snapshots` 读取每日账户快照，供轻量 `/api/front/snapshot?light=true` 使用；完整账户详情仍保留成交历史水合路径。

## v0.2.122 最新状态补充

- 新增 `backend/app/routers/frontend_profile.py`，将 `/api/front/profile` 的 GET/POST 路由声明从 `main.py` 迁出。
- 新增 `backend/tests/test_frontend_profile_router.py`，确认 profile router 透传请求 payload 和 `include_catalog` 参数，前台切换策略 URL 合同不变。

## v0.2.123 最新状态补充

- 新增 `backend/app/routers/frontend_runtime.py`，将前台 public snapshot、登录 snapshot、策略目录和交易账户路由声明从 `main.py` 迁出。
- 新增 `backend/tests/test_frontend_runtime_router.py`，确认 `as_of`、`mobile`、`light`、`include_catalog`、`limit`、`force`、`defer` 参数合同不变。

## v0.2.124 最新状态补充

- 新增 `backend/app/routers/frontend_signal.py`，将前台推荐和日计划路由声明从 `main.py` 迁出。
- 新增 `backend/tests/test_frontend_signal_router.py`，确认推荐和日计划的查询参数、范围校验和 `defer` 默认值注入合同不变。

## v0.2.125 最新状态补充

- 新增 `backend/app/routers/admin_overview.py`，将后台概览和模型信号路由声明从 `main.py` 迁出。
- 新增 `backend/tests/test_admin_overview_router.py`，确认 `/api/admin/snapshot` 和 `/api/admin/model_signals` 的查询参数合同不变。

## v0.2.126 最新状态补充

- 新增 `backend/app/routers/admin_data_cache.py`，将后台数据库查看和缓存管理路由声明从 `main.py` 迁出。
- 新增 `backend/tests/test_admin_data_cache_router.py`，确认数据库表分页、缓存状态和缓存清理 scope 参数合同不变。

## v0.2.127 最新状态补充

- 新增 `backend/app/routers/admin_access.py`，将后台访问日志、访问安全摘要、手动拉黑、解除拉黑和一键拉黑路由声明从 `main.py` 迁出。
- 新增 `backend/tests/test_admin_access_router.py`，确认访问日志分页/筛选、异常访问摘要和封禁请求参数合同不变。

## v0.2.128 最新状态补充

- 新增 `backend/app/routers/admin_frontend_users.py`，将后台前台用户管理路由声明从 `main.py` 迁出。
- 新增 `backend/tests/test_admin_frontend_users_router.py`，确认用户列表、创建、更新、重置密码、封禁、解封和删除路径合同不变。

## v0.2.129 最新状态补充

- 前台推荐/日计划缓存未命中默认不再自动启动 `frontend_payload_precompute`；需要恢复旧行为必须同时开启 `QT_FRONT_PAYLOAD_PRECOMPUTE_ENABLED=true` 和 `QT_FRONT_PAYLOAD_AUTO_PRECOMPUTE_ON_MISS=true`。
- 后台“预计算前台缓存”按钮默认 `force=false`，已有未过期缓存会跳过；推荐缓存默认 TTL 与日计划对齐为 1800 秒。

## v0.2.130 最新状态补充

- `/api/jobs/status` 新增 `frontend_payload_policy`，展示前台预计算是否启用、缓存未命中自动补算是否实际生效、TTL、用户上限和进程模式。
- 后台自动调度器面板和启动确认文案会按该 policy 区分“前台预计算保持手动”和“按配置自动预计算”。

## v0.2.131 最新状态补充

- 新增 `backend/app/routers/admin_jobs.py`，将 `/api/jobs/status`、`/api/jobs/logs`、`/api/logs/runtime`、调度器启动/停止和任务暂停/恢复/停止路由声明从 `main.py` 迁出。
- 新增 `backend/tests/test_admin_jobs_router.py`，确认任务状态、日志筛选、调度器控制和任务控制路径参数合同不变。
- 前台 `front_jobs` 缓存未命中时调用 `job_manager.frontend_status()`，避免前台快照为了显示轻量任务摘要而触发完整进程巡检。

## v0.2.132 最新状态补充

- 新增 `backend/app/routers/admin_job_runs.py`，将具体任务执行入口和 `/api/admin/system/startup` 的路由声明从 `main.py` 迁出。
- 新增 `backend/tests/test_admin_job_runs_router.py`，确认新闻、行情、AI、交易、策略复盘、前台预计算、账户预热、每日交易和系统启动流程的查询参数合同不变。

## v0.2.133 最新状态补充

- 新增 `backend/app/routers/core_system.py`，将 `/api/version`、`/api/auth/*`、`/api/debug/*`、`/api/config/*` 和 `/api/status` 路由声明从 `main.py` 迁出。
- `/api/status` 改为使用 `job_manager.frontend_status()` 的轻量任务摘要；完整任务巡检仍通过 `/api/jobs/status`。
- 前台公开快照和登录快照共用 `front_snapshot_news`，减少首屏和切换策略后重复新闻读取。
- 新增 `backend/tests/test_core_system_router.py` 和 `/api/status` 集成测试，确认核心系统 URL、请求 body 和轻量状态边界不变。

## v0.2.134 最新状态补充

- 前台推荐/日计划批量预计算默认收敛为 8 个用户、30 天日计划、20 秒时间预算；后台按钮继续 `force=false`，更适合分批刷新缓存。
- 前台缓存未命中仍默认只返回 pending/disabled；`QT_FRONT_PAYLOAD_PRECOMPUTE_ENABLED=false` 会同时关闭调度预计算和未命中自动补算。
- 账户预热异步入队改为默认 4 个 worker 的小型队列池，避免单个慢队列锁等待拖住后续首屏或账户请求。
- 轻量前台快照默认不在新闻/日期 SQLite 缺失时回退完整引擎加载，避免空库或冷启动触发全量事件/新闻读取。

## v0.2.135 最新状态补充

- `QT_RESEARCH_TASKS_MANUAL_ONLY=true` 默认拦住调度器误触发策略复盘/进化；日常自动链路继续只跑新闻、AI、行情、龙虎榜、交易循环和轻量缓存。
- 调度器在允许研究任务自动运行时，策略复盘和策略进化默认使用独立 Python 子进程，避免回到 API 进程内执行。
- `/api/quant/timeline` 和 `/api/quant/intraday_timeline` 缓存未命中时默认返回 `manual_required`；显式 `manual=true` 才会启动时间线回测任务。
- `QT_FRONT_PAYLOAD_PRECOMPUTE_INITIAL_DELAY_SECONDS=1800` 默认避免显式开启前台预计算后在调度器启动约 100 秒内立即抢 CPU。

## v0.2.136 最新状态补充

- 前台和后台 API helper 统一先读文本、识别 HTML 错误页，再解析 JSON；代理错误页即使以 2xx 返回，也会显示中文诊断而不是整页 HTML 或 `Unexpected token '<'`。
- 该改动只改善前端错误呈现；502/504/524 的根因仍按后端服务、Nginx upstream、CPU/内存和重任务状态排查。

## v0.2.137 最新状态补充

- 新增 `backend/app/routers/quant_basic.py` 和 `backend/tests/test_quant_basic_router.py`，基础量化接口从 `main.py` 迁出路由声明，payload 行为继续复用原逻辑。
- 下一步如果继续拆结构，优先考虑研究/回测接口、数据诊断接口、AI 状态接口或更大的 `engine.py` 服务边界。
