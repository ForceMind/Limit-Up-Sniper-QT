# 涨停狙击手项目完整优化目标与执行计划

日期：2026-05-23  
当前本地版本：0.2.137
定位：A 股新闻驱动量化研究、策略训练、模拟跟随与运维管理系统。当前只做研究、回测和模拟交易，不连接真实券商账户，不真实下单。

## 1. 总目标

把项目从“能运行的单机量化工具”升级为“长期可维护、可迁移、可复用的量化策略平台”。

最终系统应该具备：

- 数据持续收集：新闻、AI 分析、日 K、分钟 K、龙虎榜、行业/板块、市场情绪、后续更多因子。
- 策略持续生产：服务器基于公共数据训练和回测多个策略模型，而不是只维护一个全局策略。
- 策略独立运行：每个策略有自己的基础参数、信号、成交、持仓、账户快照、交割单、资金流水和训练轨迹。
- 用户独立跟随：用户注册后设置模拟资金并选择策略；账户从注册或切换策略那天开始运行，不继承策略过去的旧持仓。
- 前台轻量查看：未登录只看概览和新闻；登录后看自己的策略、资金、持仓、成交、买入信号和新闻。
- 后台完整运维：管理数据、策略、用户、访问审计、缓存、任务、日志、配置、调试通道和部署状态。
- 服务器稳定：重计算后台化，前台接口缓存优先，任务可暂停，日志中文化，内存和 CPU 可控。

## 2. 当前架构成熟度判断

当前已经建立了正确方向，但还不是最终成熟架构。

已经理顺的部分：

- “系统运行策略”不再作为产品主概念，已降级为系统默认基础参数；它只用于调参、诊断和生成新策略。
- 前台用户 profile 已包含 `simulated_cash`、`strategy_model_id`、`follow_started_at`、`follow_start_date`。
- 用户切换策略会重置跟随开始时间。
- 前台账户按跟随开始日裁剪，不继承旧历史持仓。
- 策略模型、策略基础参数、策略成交记录、训练候选、训练轨迹已进入 SQLite。
- 交易账户、推荐、日计划已加入短缓存。
- 策略复盘任务已能把每个策略的每日信号、成交和持仓写入 `strategy_daily_signals`、`strategy_runtime_trades`、`strategy_runtime_positions`。
- 策略复盘任务已能把每个策略的每日账户快照和清算结果写入 `strategy_runtime_snapshots`、`strategy_runtime_settlements`。
- 资金档策略已改名为更明确的资金规模策略，并从 `strategy_runtime_*` 表汇总收益、回撤、胜率和成交数；本地或服务器复盘完成后不再显示全 0。
- 前台账户会优先读取已落库策略运行成交，再按用户 `follow_start_date` 派生账户视图。
- v0.2.32 起，前台账户会优先读取 `user_follow_snapshots`，并把派生后的用户持仓和成交写入 `user_follow_positions`、`user_follow_trades`。
- v0.2.33 起，用户注册、设置资金或切换策略会写入 `user_follow_periods`，后台用户管理页展示当前账户快照、持仓和最近成交来源。
- v0.2.38 起，后台“持仓成交”改为按策略查看持仓、成交、交割单和资金流水，不再展示一个统一基准账户。
- v0.2.40 起，后台策略库列表直接展示每个策略的基础参数摘要，避免把策略误解成只有收益指标、没有独立参数。
- v0.2.41 起，后台“信号与新闻”默认按单个策略筛选信号；“回测复盘”也按所选策略加载，不再显示全局默认参数的复盘结果。
- v0.2.42 起，服务重启默认进入手动任务模式，不自动启动调度器；后台任务按钮和任务摘要会写清楚暂停的是哪个任务、策略复盘的策略范围和日期窗口。
- v0.2.43 起，后台任务区区分“暂停后续调度”和“停止当前任务”；独立进程任务可直接终止，普通后台任务会记录停止请求并在检查点结束。
- v0.2.44 起，后台把自动调度器拆成独立总开关；手动任务按钮只负责立即执行任务，自动调度启动/停止通过调度器面板控制。
- v0.2.45 起，后台登录后只加载轻量快照，不再默认触发回测复盘时间线；复盘数据只在进入“回测复盘”页时加载。
- v0.2.46 起，后台运维操作按一键流程、数据准备、策略模拟、数据迁移、系统维护分组；每个按钮都有用途说明，任务状态改为中文解释。
- v0.2.47 起，“回测复盘”默认读取 `strategy_runtime_*` 已落库结果，不再自动触发实时重算；“数据与AI”改为缓存化分块加载；访问审计新增异常 IP 识别、自动封禁、手动拉黑和解除封禁。
- v0.2.48 起，前台推荐和日计划新增后台预计算任务；缓存未命中时可返回 pending，不再长时间等待同步计算。
- v0.2.49 起，策略复盘、模型训练和回测默认只允许手动触发，不再随自动调度或系统启动流程运行；策略库查看交割单默认读取已保存模型记录，日常自动链路只做新闻、AI、行情和交易，前台预计算由管理员按配置或手动触发。
- v0.2.50 起，前台和后台会把 Cloudflare/Nginx 返回的 HTML 错误页转换为简短中文诊断信息，不再把 502/504/524 的整页 HTML 原样展示给用户。
- v0.2.51 起，访问审计支持后端分页和用户/IP/路径/状态码筛选；异常访问支持后台一键拉黑全部未封禁异常 IP。
- v0.2.52 起，前台切换策略只保存跟随关系，不再强制跳过账户缓存或立即刷新完整快照；账户会在持仓/成交页按需读取已落库结果。
- v0.2.53 起，前台账户接口缓存未命中时默认返回轻量 `pending`，不再同步即时回放；需要人工排查时才用 `force=true` 强制重算。
- v0.2.54 起，前台会明确展示账户运行结果待生成状态，不再把 pending 账户误显示为真实空仓或 0 收益。
- v0.2.55 起，前台账户接口默认不再加载完整策略模型交割单做同步兜底；生产路径只读用户快照、策略运行表和账户缓存。
- v0.2.56 起，后台新增“前台账户预热”任务，可为前台用户从已有 `user_follow_snapshots`、策略运行表或账户缓存生成账户快照；默认不触发即时回放或完整模型交割单兜底。
- v0.2.57 起，`frontend_account_precompute` 支持独立 Python 子进程，后台按钮默认传 `process=true`，批量账户预热不再占用 API 进程。
- v0.2.58 起，前台用户注册、切换策略或调整模拟资金后会自动排队单用户账户预热；保存操作快速返回，预热默认进入独立进程。
- v0.2.59 起，前台 profile 保存接口返回显式 `queued`/`worker_started` 语义，区分账户预热请求是否被接收以及 worker 是否实际启动。
- v0.2.60 起，自动账户预热改为先写入 `frontend_account_precompute_queue.json` 待处理队列，再由独立进程批量消费；同名预热任务运行中时，后续用户不会丢失。
- v0.2.61 起，账户预热队列读写增加跨进程锁和陈旧锁恢复，避免 API 进程和独立 worker 同时读写队列导致丢项。
- v0.2.62 起，前台账户接口发现账户预热队列残留时会尝试补启动消费 worker，避免 worker 刚退出时新增队列项长期滞留。
- v0.2.63 起，前台注册、切换策略或调整资金只负责把账户预热写入队列，默认不在 profile 保存请求里启动 worker；账户页读取或后台手动任务再唤起队列消费，进一步降低切换策略延迟。
- v0.2.64 起，后台任务状态会显示前台账户预热队列长度、最早/最新排队时间、原因分布和队列锁状态，便于判断切换策略慢是队列积压、锁残留还是 worker 未运行。
- v0.2.65 起，后台手动“前台账户预热”会在存在排队项时默认优先消费账户预热队列；没有排队项时仍按普通批量用户预热运行。
- v0.2.66 起，前台 profile 保存不再清空全局内存缓存，前台缓存键纳入跟随开始时间；数据覆盖率冷启动默认转入 `data_coverage` 后台任务，避免诊断页拖住 API。
- v0.2.67 起，单模型回测重算 `recompute=true` 默认转入 `model_backtest` 后台任务并写入短缓存；后台按钮不再同步等待 `walk_forward_intraday`。
- v0.2.68 起，通用 `/api/quant/timeline` 与 `/api/quant/intraday_timeline` 默认缓存未命中返回 pending，并启动 `quant_timeline` 后台任务。
- v0.2.69 起，前台切换策略默认使用轻量 profile 保存链路：资金档策略不再强制读取完整策略目录，保存后不自动拉取账户接口，只显示待预热账户状态。
- v0.2.70 起，通用 `/api/quant/backtest` 默认缓存未命中返回 pending，并启动 `quant_backtest` 独立进程写入 SQLite 短缓存；只有 `defer=false` 才同步等待。
- v0.2.71 起，前台切换到策略库模型时，轻量 profile 保存优先按模型 ID 单条读取策略，不再为了定位当前模型回退加载完整策略目录，降低切换策略时的 API 延迟。
- v0.2.72 起，后台参数拟合 `/api/quant/fit_strategy` 默认只启动 `fit_strategy` 独立进程并返回 pending；只有显式 `defer=false` 才同步等待拟合完成。
- v0.2.73 起，后台“同步分时行情”和 `/api/data/biying/sync_intraday` 默认启动 `market_sync` 独立进程；只有显式 `process=false&background=false` 才在请求内同步抓取。
- v0.2.74 起，后台“补齐缺失日K”和“拉取龙虎榜”默认分别启动 `kline_fill`、`lhb_sync` 独立进程；只有显式 `process=false&background=false` 才在请求内同步补数。
- v0.2.75 起，后台“运行模拟交易”、`/api/jobs/daily/run` 和自动调度交易循环默认启动 `trade_cycle` 独立进程；系统启动流程内部仍按顺序同步执行交易步骤。
- v0.2.76 起，后台“AI 分析新闻”和自动调度 AI 分析默认启动 `ai_analysis` 独立进程；系统启动流程内部仍按顺序同步执行 AI 分析步骤。
- v0.2.77 起，`/api/data/coverage` 数据覆盖率诊断改为 SQLite 短缓存，缓存未命中时默认启动 `data_coverage` 独立进程；子进程计算完成后父进程可直接读取缓存结果。
- v0.2.78 起，后台“抓取新闻”和自动调度新闻抓取默认启动 `news_fetch` 独立进程；系统启动流程内部仍按顺序同步执行新闻抓取步骤。
- v0.2.79 起，后台“系统启动”整体默认启动 `system_startup` 独立进程；内部步骤仍按新闻、AI、日K、龙虎榜、分时、交易循环顺序执行，策略复盘、训练和回测继续默认只手动触发。
- v0.2.80 起，前台轻量 profile 保存不再加载资金档运行摘要；策略运行摘要只在策略目录/后台视图需要时读取，进一步降低用户切换策略延迟。
- v0.2.81 起，前台保存或切换策略后，只有当前停留在“买入”页才会自动补拉推荐和日计划；概览页不再立即触发额外推荐预计算请求。
- v0.2.82 起，前台轻量 `/api/front/snapshot` 默认不加载完整策略目录；策略页进入时再调用 `/api/front/strategy_models` 按需读取完整策略库。
- v0.2.83 起，通用回测和单模型回测重算在缓存未命中时需要显式 `manual=true` 才会启动任务；普通刷新只返回已保存结果、短缓存或 `manual_required`，避免旧页面/误请求自动排重计算。
- v0.2.84 起，训练、策略复盘、模型回测、通用回测、时间线回测和参数拟合共享重任务进程并发闸门，默认 `QT_HEAVY_JOB_MAX_CONCURRENT=1`，避免多个手动重任务同时抢占 CPU 和 SQLite。
- v0.2.85 起，单模型回测重算和时间线回测默认也进入独立 Python 子进程，不再使用 API 进程内后台线程；结果继续写入短缓存并受重任务并发闸门控制。
- v0.2.86 起，后台概览和运维页展示重任务并发槽位、正在运行的训练/复盘/回测任务和 busy 原因，便于判断服务器当前为什么拒绝启动新的重任务。
- v0.2.87 起，单模型回测重算和时间线回测的短缓存从进程内内存改为 SQLite `frontend_payload_cache`，独立子进程生成的结果可被 API 父进程刷新读取。
- v0.2.88 起，前台 profile 保存后的用户跟随周期落库改为异步维护，切换策略不会再因 SQLite 写锁等待而拖慢；回测/拟合/时间线任务被重任务并发闸门拦截时会直接返回 `busy`。
- v0.2.89 起，前台注册、切换策略或调整资金时，账户预热队列入队也默认异步执行；profile 保存请求不再等待 `frontend_account_precompute_queue.json` 跨进程锁。
- v0.2.90 起，前台账户接口返回 pending 时会异步把当前用户补入账户预热队列并启动预热 worker；即使 profile 保存时的异步入队未完成或失败，账户页也能自愈触发预热。
- v0.2.91 起，前台 pending 账户提示会读取 `account_precompute` 状态，区分“后台写入队列中”“预热任务启动中”“预热已排队/已启动”，减少用户误判为服务器卡死。
- v0.2.92 起，账户预热异步入队增加短时间去重，默认 5 秒内同一用户/原因/日期只启动一个后台入队线程，避免账户页频繁刷新放大 API 进程线程数。
- v0.2.93 起，登录首屏 `/api/front/snapshot` 如果返回 pending 账户，也会触发同一套异步账户预热自愈；不再依赖前端额外请求 `/api/front/trading_account`。
- v0.2.94 起，前台 pending 文案会识别 `account_precompute.deduped=true`，提示已有相同账户预热请求正在处理，避免用户误以为刷新无效。
- v0.2.95 起，任务状态新增 `frontend_account_precompute_async` 摘要，后台运维页展示账户预热异步入队去重保护和队列锁状态，便于排查频繁刷新是否被保护。
- v0.2.96 起，`POST /api/front/profile` 返回 `profile_update_trace` 分阶段耗时和 `profile_update_slow_stage`，用于判断切换策略慢在保存资料、构建策略上下文、记录跟随周期还是账户预热入队。
- v0.2.97 起，前台 profile 保存的轻量上下文在策略 ID 缺失或过期时不再回退加载完整策略目录，而是直接回到推荐资金档策略；完整策略库继续由策略页按需读取。
- v0.2.98 起，前台 profile 保存会复用 `update_frontend_user_profile` 返回的新 profile payload 构建上下文，不再保存后额外读取一次用户资料文件，减少切换策略路径的文件锁等待。
- v0.2.105 起，策略运行快照的 `daily_runtime` 来源过滤从 `source LIKE 'daily_runtime%'` 改为前缀范围条件，并补充 `(model_id, source, generated_at, as_of)` 索引，让策略运行账户和策略摘要读取更稳定走索引。
- v0.2.104 起，当前跟随周期查询去掉 `COALESCE(ended_at, '')` 包列条件并补充 `user_follow_periods` 当前周期复合索引，兼容历史 `ended_at=NULL` 数据的同时更利于 SQLite 使用索引。
- v0.2.103 起，策略信号 feed 不再用 `COUNT(*) + MAX(date)` 判断最新信号日，改为按日期索引倒序 `LIMIT 1`，避免信号表变大后概览/矩阵读取先扫全表。
- v0.2.102 起，策略演进 SQLite 连接按数据库路径和 schema 版本缓存 schema 初始化，避免 API 热路径每次连接都重复执行整段建表/建索引脚本。
- v0.2.101 起，SQLite schema 补充前台账户和策略运行热路径复合索引，覆盖用户快照、快照明细、运行信号、成交、持仓、清算和策略矩阵读取。
- v0.2.100 起，前台 profile 的策略目录归一化和保存前解析已从 `main.py` 迁入独立 service，后续前台 router 拆分可以复用同一套热路径逻辑。
- v0.2.99 起，前台 profile 保存会在首次写入前解析 `strategy_model_id`；过期、缺失或 `active` 策略 ID 会先替换为推荐资金档策略，并复用已查到的模型结果，避免 context 纠正时触发第二次 profile 写入或同请求重复查模型。
- 策略复盘保留按日期窗口分批推进能力，手动触发时默认每批 15 天、最多 24 个策略，避免一次性全量重跑。
- 数据包导入的 SQLite 合并已改为流式落临时库后 ATTACH 合并，避免 200MB 级 SQLite 在服务器内存中被一次性读入。
- 新增策略运行结果小包导出脚本，只打包 `strategy_runtime_*` 表，用于把本地跑好的资金档/策略复盘结果合并到服务器。
- 后台数据库页可查看 SQLite 表、缓存状态和清理缓存。
- 部署脚本能验证版本、关键模块接口和数据库表结构。

仍未成熟的部分：

- 策略运行结果已有每日信号、成交、持仓、账户快照和清算结果第一版落库；用户跟随账户已有周期、快照、持仓、成交第一版落库。
- 后台用户账户诊断已有第一版，后续还需要更完整的周期详情页和单用户追溯视图。
- 推荐、日计划、数据覆盖率、单模型回测重算和通用时间线回测已完成后台预计算第一版；其它数据诊断慢接口还没有全部任务化。
- `backend/app/main.py`、`backend/app/quant/engine.py`、`frontend/admin/index.html` 仍然偏大，需要继续拆分。
- 单机 SQLite 可以支撑当前阶段，但更大数据量和并发下应迁移到 PostgreSQL 或拆出行情列式存储。
- 服务器内存压力需要持续观察，进化任务必须有限流、暂停、分批、进度和运行窗口。

结论：当前是“逻辑正确、单机可运行、可继续优化”的阶段，不是“性能和架构最优”的阶段。

## 3. 可复用目标架构

```text
数据源层
  财联社新闻 / DeepSeek AI / 必盈行情 / 龙虎榜 / 行业板块 / 市场宽度

数据仓库层
  news_raw / news_analysis / news_events
  market_daily_bars / market_minute_bars
  lhb_records / ai_cache / ai_usage_logs
  factor_values / market_calendar / data_quality

因子层
  新闻情绪因子 / 事件影响因子 / 技术动量因子 / 量价因子
  龙虎榜席位因子 / 市场情绪因子 / 行业热度因子 / 风控因子

策略工厂层
  参数拟合 / 遗传进化 / Walk-forward / 分时回放
  候选淘汰记录 / 模型入库 / 模型评分 / 模型版本

策略运行层
  每个策略保存独立基础参数，并每日生成信号、目标仓位、成交、持仓、账户快照、交割单

用户跟随层
  用户资金 + 策略 ID + 跟随开始日 => 用户账户视图和用户成交记录

接口层
  首屏轻量 / 缓存优先 / 慢计算后台任务化 / WebSocket 或增量轮询

运维层
  qt 命令 / systemd / Nginx / 数据备份 / 调试密钥 / 访问审计 / 缓存管理
```

这套结构可以复用到其它“公共数据 + 多模型 + 用户跟随”的量化系统。

## 4. 核心产品规则

必须坚持这些规则，避免系统重新变乱：

- 没有一个全局“系统运行策略”代表所有用户。
- 系统默认基础参数只用于诊断、默认参数、人工调参和生成新策略，不代表用户跟随账户。
- 每个策略都必须有自己的基础参数，策略运行结果必须按策略隔离。
- 策略库里的模型才是用户可选择的跟随对象。
- 用户账户从注册或切换策略时开始，不继承策略历史持仓。
- 当前阶段暂不做“每个用户从注册日完整独立重跑策略”的重计算版本；先使用策略级运行结果，按用户资金和跟随开始日生成轻量账户视图，完整用户独立运行保留在计划中。
- 所有策略共用新闻和行情数据，但参数、信号、成交和账户结果互相独立。
- 小资金策略必须单独适配，1 万、2 万-5 万、5 万-10 万、10 万以上不能混用同一仓位逻辑。
- 资金档策略当前命名为：`小资金策略`、`短线稳健策略`、`均衡轮动策略`、`趋势多仓策略`；具体资金范围放在标签和说明里。
- 买入信号的数字必须标明是评分或概率，不能让用户误解。
- 前台只保留查看和用户自身操作，不能暴露后台初始化或管理入口。
- 后台必须能看到用户、访问记录、IP、浏览器、跟随策略、资金、封禁、密码重置。
- 所有重任务必须有中文日志、进度、状态、暂停/恢复能力。
- 自动调度任务必须优先走增量或分批，不能在常规心跳里反复全量扫描历史数据。
- 默认部署采用手动任务模式，重启后不自动跑新闻、AI、补数、交易循环或策略复盘；即使开启自动调度，策略复盘、模型训练和回测也默认只手动触发。

## 5. 数据目标

### 5.1 必须进入数据库的数据

- 原始新闻：`news_raw`
- AI 分析：`news_analysis`
- 结构化事件：`news_events`
- 日 K：`market_daily_bars`
- 分钟 K：`market_minute_bars`
- 龙虎榜：`lhb_records`
- 策略训练运行：`strategy_runs`
- 训练候选和淘汰记录：`strategy_candidates`
- 策略模型：`strategy_models`
- 策略模型成交记录：`strategy_model_records`
- 前台访问审计：`access_logs`
- 任务日志：`job_logs`
- 策略运行快照：`strategy_runtime_snapshots`
- 策略每日信号：`strategy_daily_signals`
- 策略每日持仓：`strategy_runtime_positions`
- 策略每日成交：`strategy_runtime_trades`
- 策略每日清算：`strategy_runtime_settlements`
- 用户跟随周期：`user_follow_periods`
- 用户跟随快照：`user_follow_snapshots`
- 用户跟随持仓：`user_follow_positions`
- 用户跟随成交：`user_follow_trades`
- 前台短缓存：`frontend_payload_cache`

### 5.2 下一阶段应新增或升级的数据表

- `strategy_daily_signals`：每个策略每天产生的买入/观察/卖出信号。（v0.2.23 第一版已落库）
- `strategy_runtime_positions`：每个策略每天的持仓。（v0.2.23 第一版已落库）
- `strategy_runtime_trades`：每个策略每天的成交。（v0.2.23 第一版已落库）
- `strategy_runtime_snapshots`：每个策略每天的账户快照。（v0.2.24 第一版已落库，并与短缓存共存）
- `strategy_runtime_settlements`：每个策略每天的资金流水和清算。（v0.2.24 第一版已落库）
- `user_follow_periods`：用户资金或策略变更形成的跟随周期。（v0.2.33 第一版已落库）
- `user_follow_snapshots`：用户跟随账户快照。（v0.2.32 第一版已落库）
- `user_follow_positions`：用户跟随持仓快照。（v0.2.32 第一版已落库）
- `user_follow_trades`：用户跟随成交和交割单。（v0.2.32 第一版已落库）
- `factor_values`：股票、日期、因子名、因子值。
- `data_quality_gaps`：缺失数据、补齐状态、失败原因。

### 5.3 JSON 的定位

JSON 只能用于：

- 配置。
- 小型状态。
- 历史兼容迁移来源。
- 本地开发样例。
- 临时导入导出。

长期增长数据不应继续依赖 JSON。

## 6. 性能目标

### 6.1 用户体验目标

- 公开首页：1 秒内返回概览和新闻摘要。
- 登录状态检查：1 秒内完成。
- 前台账户缓存命中：1 秒内返回。
- 前台推荐缓存命中：1 秒内返回。
- 前台日计划缓存命中：1 秒内返回。
- 缓存未命中但数据量正常：尽量 5 秒内返回；超过 5 秒应改为后台任务。
- 重型回测、补数据、进化训练：必须后台任务化，不通过 Cloudflare/Nginx 长连接等待。

### 6.2 当前已完成的性能措施

- `/api/jobs/status` 默认轻量化。
- `/api/front/snapshot` 默认轻量，不直接拉重型账户、推荐、日计划。
- 前台账户接入 `strategy_runtime_snapshots` 缓存。
- 后台策略复盘按资金档预设和策略库模型批量写入每日信号、成交、持仓、快照、清算运行表。
- 前台账户优先用 `strategy_runtime_trades` 派生用户跟随账户，缺失时回退短缓存或即时回放。
- 前台账户优先读取 `user_follow_snapshots`；未命中时从策略运行表或回放派生，并写入用户跟随快照、持仓和成交表。
- 缓存清理会保护 `daily_runtime:*` 正式每日快照，避免把运行结果当短缓存误删。
- 前台推荐和日计划接入 `frontend_payload_cache` 缓存。
- 后台数据库页可以查看和清理缓存。
- 推荐相关的部分历史统计有样本上限，避免全量扫描导致卡死。
- v0.2.25 起，后台手动触发的新闻抓取、AI 分析、行情同步、日K补齐、龙虎榜同步、交易循环、策略复盘和系统启动默认转入后台运行，HTTP 请求只返回任务启动状态，避免 Nginx 长时间等待。
- v0.2.30 起，后台手动触发的策略复盘和策略进化可转入独立 Python 子进程运行，避免重计算长期占用 API 进程。
- v0.2.31 起，任务状态接口会自动巡检独立进程任务；如果子进程异常退出且未写回完成状态，会标记失败并写入运行日志，避免任务永久卡在运行中。
- v0.2.32 起，用户跟随账户按用户名、策略、资金、跟随开始日和 as_of 落库，减少同一用户同一周期的重复缩放和裁剪。
- v0.2.33 起，后台用户管理页读取用户跟随周期和账户诊断，不再只能看到 profile 字段。
- v0.2.48 起，前台推荐和日计划缓存未命中时可触发 `frontend_payload_precompute`，同步请求只返回轻量 pending 状态；v0.2.129 起生产默认不自动触发，后台可手动预计算，显式开启后调度器也可按间隔触发。
- v0.2.49 起，自动调度默认不再触发 `strategy_replay`，系统启动流程默认跳过策略复盘；如需刷新模型运行结果，必须在后台手动点“运行策略复盘”或显式开启对应环境开关。
- v0.2.50 起，前后台 API 错误处理会识别 Cloudflare 502、Nginx 504、Cloudflare 524 和其它源站连接错误，页面只显示排查提示，避免异常代理页撑满业务界面。
- v0.2.52 起，用户在前台策略页切换跟随策略时不再传 `force=true` 重建账户，也不再立即请求包含账户摘要的全量快照，避免把一次选择操作变成即时回放。
- v0.2.53 起，`/api/front/trading_account` 默认 `QT_FRONT_ACCOUNT_DEFER_MISSES=true`，缓存和策略运行结果都缺失时返回待生成状态，避免前台请求触发 `walk_forward`。
- v0.2.54 起，前台持仓、成交、交割页会复用账户 pending 消息，提示先手动运行策略复盘或导入策略运行小包。
- v0.2.55 起，`QT_FRONT_ACCOUNT_MODEL_RECORDS_FALLBACK=false` 为生产默认值，避免前台账户请求读取大体积 `strategy_model_records` 并同步派生账户。
- v0.2.56 起，后台“运维”可手动触发 `frontend_account_precompute`，在用户访问前批量补齐用户账户快照，降低切换策略后的首次账户读取延迟。
- v0.2.57 起，账户预热默认由 `QT_FRONT_ACCOUNT_PRECOMPUTE_PROCESS_ENABLED=true` 控制为独立进程执行，避免批量用户账户派生拖住后端 API。
- v0.2.58 起，`QT_FRONT_ACCOUNT_AUTO_PRECOMPUTE_ENABLED=true` 时，用户跟随周期变化会自动排队该用户的账户预热任务；重复任务会被任务管理器按同名任务运行状态拦截。
- v0.2.59 起，自动账户预热响应包含显式 `queued` 语义；v0.2.60 后该字段表示请求已进入待处理队列，`worker_started` 表示本次是否启动了进程或后台线程。
- v0.2.60 起，账户预热队列支持 `QT_FRONT_ACCOUNT_PRECOMPUTE_QUEUE_BATCH_USERS`、`QT_FRONT_ACCOUNT_PRECOMPUTE_QUEUE_MAX_USERS` 和 `QT_FRONT_ACCOUNT_PRECOMPUTE_QUEUE_MAX_BATCHES` 控制批量消费规模。
- v0.2.61 起，账户预热队列锁支持 `QT_FRONT_ACCOUNT_PRECOMPUTE_QUEUE_LOCK_TIMEOUT_MS` 和 `QT_FRONT_ACCOUNT_PRECOMPUTE_QUEUE_LOCK_STALE_MS`，用于跨进程互斥和崩溃恢复。
- v0.2.62 起，`/api/front/trading_account` 会在返回账户时检查预热队列是否有残留；如有则快速触发 `drain_queue=true` worker，不做同步回放。
- v0.2.63 起，`QT_FRONT_ACCOUNT_START_WORKER_ON_PROFILE=false` 为生产默认值，profile 保存路径只入队不启 worker；如需恢复旧行为可临时设为 `true`。
- v0.2.64 起，`/api/jobs/status` 返回 `frontend_account_precompute_queue` 轻量摘要；后台“前台账户预热”任务卡片会展示队列数量和锁状态。
- v0.2.65 起，`/api/jobs/frontend/account_precompute` 在未显式传 `drain_queue` 且队列非空时会自动 `drain_queue=true`，后台按钮也会显式传入该参数。
- v0.2.66 起，用户切换策略不再导致全局内存缓存失效；`/api/data/coverage` 默认在缓存未命中时返回 pending 并启动 `data_coverage` 后台任务，后台全量快照也优先走该缓存/任务路径。
- v0.2.67 起，`/api/quant/model/backtest?recompute=true` 默认返回 pending 并启动 `model_backtest` 后台任务；需要同步排查时才显式传 `defer=false`。
- v0.2.68 起，`/api/quant/timeline` 和 `/api/quant/intraday_timeline` 默认走 `quant_timeline` 后台任务和短缓存；需要同步排查时才显式传 `defer=false`。
- v0.2.69 起，`POST /api/front/profile` 默认 `include_catalog=false`，切换资金档策略不再读取完整策略目录；前台保存后不自动请求账户接口，避免把账户预热或进程启动压到用户点击链路上。
- v0.2.70 起，`/api/quant/backtest` 默认走 `quant_backtest` 独立进程和 `frontend_payload_cache` 短缓存；需要同步排查时才显式传 `defer=false`。

### 6.3 下一步性能重点

- 推荐和日计划生成已改为后台预计算第一版，生产默认改为手动触发；前台缓存未命中不再自动排队，除非显式开启 `QT_FRONT_PAYLOAD_AUTO_PRECOMPUTE_ON_MISS`。
- 把补数等剩余慢路径全部改为任务 + 进度 + 结果缓存。
- 对 SQLite 加合理索引，重点是日期、股票代码、策略 ID、用户 ID。
- 对进化任务限制并发、批量大小、运行时间窗口和最大内存占用。
- 对前端详情页改为分页和懒加载，避免登录后连续请求全部重接口。

## 7. 策略与模型目标

### 7.1 策略模型必须具备

- 模型 ID。
- 参数 JSON。
- 训练 run ID。
- 生成时间。
- 资金适配范围。
- 回测收益。
- 最大回撤。
- 胜率。
- 交易次数。
- 交割单。
- 资金曲线。
- 训练候选来源和淘汰原因。

### 7.2 遗传进化必须记录

- 总共评估了多少候选策略。
- 每代种群大小。
- 每代最佳收益、回撤、胜率、目标函数。
- 哪些候选被保留。
- 哪些候选被淘汰。
- 淘汰原因：收益为负、回撤过大、样本不足、成交过少、目标函数低等。
- 最终进入策略库的模型。

### 7.3 因子扩展方向

优先加入这些因子：

- 新闻情绪强度。
- 新闻来源权重。
- 事件类型。
- 行业热度。
- 个股历史事件兑现率。
- 3/5/10/20 日动量。
- 成交量放大倍数。
- 波动率。
- 最大回撤。
- 涨停/炸板/连板特征。
- 龙虎榜净买入。
- 机构席位、游资席位、营业部席位。
- 市场上涨家数、涨停家数、跌停家数。
- 用户资金规模可买一手约束。

## 8. 用户跟随目标

用户侧最终应该是：

1. 用户注册。
2. 设置模拟资金。
3. 系统推荐一个适合资金规模的策略。
4. 用户可以选择其它策略跟随。
5. 从注册或切换策略时间开始，生成该用户自己的模拟账户。
6. 用户可以看到：
   - 当前跟随策略。
   - 跟随开始日期。
   - 当前持仓。
   - 今日成交。
   - 历史成交。
   - 交割单。
   - 资金流水。
   - 买入信号。
   - 策略说明和风险指标。

关键点：选择跟随策略后，持仓和成交必须同步变化；否则用户会认为功能无效。

## 9. 后台目标

后台必须逐步形成这些独立模块：

- 系统概览：版本、任务、数据日期、缓存状态、内存提示。
- 用户管理：前台账号、资金、跟随策略、封禁、密码重置、访问记录。
- 策略库：模型列表、收益、回撤、交割单、训练轨迹。
- 策略运行矩阵：每个策略是否运行、最新信号、最新收益、缓存状态。
- 数据管理：新闻、K 线、龙虎榜、缺失补齐、导入导出。
- 数据库管理：表结构、行数、表数据分页查看、缓存清理。
- 运维管理：启动、暂停、重启、备份、调试密钥、日志。
- 安全审计：IP、浏览器、路径、耗时、状态码、用户。

## 10. 部署与安全目标

- `qt` 是服务器统一入口。
- `qt` 一键更新必须：
  - 备份数据。
  - 拉取代码。
  - 安装依赖。
  - 智能迁移 SQLite。
  - 验证表结构。
  - 验证版本。
  - 验证模块化接口。
  - 重启服务。
- 不能每次重复全量迁移。
- 不能每次反复修改 Nginx 备份文件。
- 生产数据、`.env`、密钥、数据库、日志、备份包不能提交 Git。
- 临时调试通道必须可开可关，默认禁写。
- 调试完成后执行：`qt debug-off && qt restart`。

## 11. 分阶段计划

### P0：稳定当前线上版本

目标：避免服务器卡死，保证可以登录、查看、更新和回滚。

任务：

- 部署 `0.2.23`。
- 在后台数据库页确认 `strategy_runtime_snapshots` 和 `frontend_payload_cache` 正常建表。
- 测试同一账户连续两次打开交易账户、推荐、日计划，确认第二次明显变快。
- 观察服务器内存、CPU、接口耗时。
- 确认调试通道可关闭。

验收：

- `/api/version` 前后端版本一致。
- 后台缓存状态能显示行数。
- 登录后不再长时间停留在“正在检查登录状态”。
- 服务器内存不再因一次更新或一次页面打开长期打满。

### P1：策略运行结果按天落库

目标：用户打开页面只读策略运行结果，不再临时完整回放。

任务：

- 新增 `strategy_daily_signals`。
- 新增 `strategy_runtime_positions`。
- 新增 `strategy_runtime_trades`。
- 后台策略复盘任务按策略批量生成运行结果。（v0.2.23 已完成第一版）
- 前台账户优先从运行成交派生。（v0.2.23 已完成第一版）
- 新增 `strategy_runtime_settlements`。（v0.2.24 已完成第一版）
- 将现有 `strategy_runtime_snapshots` 从短缓存升级为每日快照。（v0.2.24 已完成第一版，短缓存仍保留）

验收：

- 每个策略有每日运行结果。
- 策略库能查看每个策略自己的交割单。
- 用户切换策略后账户数据明显变化。
- 前台账户接口缓存未命中时也不需要全量回放。

### P2：用户跟随账户落库

目标：用户账户成为一等实体。

任务：

- 新增 `user_follow_snapshots`。
- 新增 `user_follow_trades`。
- 新增 `user_follow_positions`。
- 前台账户优先读取用户跟随快照，未命中时派生并写回。（v0.2.32 已完成第一版）
- 新增用户跟随策略变更记录。（v0.2.33 已完成第一版）
- 用户设置资金或切换策略时生成新的跟随周期。（v0.2.33 已完成第一版）
- 后台用户管理页展示用户当前账户、跟随开始日、最近访问。（v0.2.33 已完成第一版）

验收：

- 同一策略下不同资金用户得到不同仓位。
- 同一用户切换策略后历史周期保留，当前周期重新开始。
- 后台能定位某个用户为什么持有某只股票。

### P3：慢接口任务化

目标：所有可能超过 5 秒的接口不直接阻塞 HTTP。

任务：

- 推荐预计算。（v0.2.48 已完成第一版）
- 日计划预计算。（v0.2.48 已完成第一版）
- 数据覆盖率预计算。（v0.2.66 已完成第一版）
- 单模型回测重算任务化。（v0.2.67 已完成第一版）
- 通用时间线回测任务化。（v0.2.68 已完成第一版）
- 前台策略切换链路轻量化。（v0.2.69 已完成第一版）
- 通用 backtest 任务化和进程化。（v0.2.70 已完成第一版）
- 其它回测任务化。
- 数据补齐任务化。
- 前台只轮询进度或读缓存。

验收：

- 不再出现 Cloudflare 524、Nginx 504。
- 上传、合并、补数、回测都有浮窗进度。
- 任务状态返回轻量摘要，不返回巨大 payload。

### P4：代码结构拆分

目标：降低维护难度和 BUG 风险。

拆分顺序：

1. API router：`auth`、`front`、`admin`、`jobs`、`data`、`quant`、`strategy`。
2. Service：账户、策略、数据、任务、用户、缓存。
3. Repository：新闻、行情、策略、用户、运行结果。
4. 前端模块：前台和后台分别拆组件或至少拆 JS 模块。

验收：

- `backend/app/main.py` 只保留 app 创建、中间件和路由注册。
- `engine.py` 不再承载所有业务。
- 新增功能能放到明确模块，而不是继续堆大文件。

### P5：更大规模迁移

目标：支持更长历史、更高频行情、更大策略池。

任务：

- 评估 PostgreSQL。
- 行情数据可拆到专用存储或分区表。
- 策略训练任务可拆独立 worker。
- 缓存可迁移到 Redis。
- 任务队列可迁移到 Celery/RQ/Arq。

验收：

- 历史数据扩展到多年不会拖慢前台。
- 策略数量增加到数百个仍可后台运行。
- 服务器迁移可通过数据包或数据库备份恢复。

## 12. 每次开发的固定流程

每次让 Codex 继续开发时，必须要求：

1. 先读本文件。
2. 再看 `git status --short`。
3. 不要回滚用户已有改动。
4. 明确本次只做一个阶段里的一个目标。
5. 代码改动后更新版本号。
6. 更新相关文档。
7. 跑测试：
   - `python -m pytest backend\tests -q`
   - `python scripts\security_scan.py`
   - `git diff --check`
   - `bash -n qt.sh`
   - `bash -n scripts/qt.sh`
   - `bash -n scripts/common.sh`
   - `bash -n scripts/update_server.sh`
8. 最后说明：
   - 改了什么。
   - 为什么这样改。
   - 如何部署。
   - 如何验证。

## 13. 当前下一步推荐目标

继续推进 P3：补数诊断等剩余慢接口任务化和预计算。

原因：

- 用户跟随周期、快照、持仓、成交和后台诊断已经有第一版闭环。
- 登录后推荐、日计划、数据覆盖率、单模型回测重算和通用时间线回测已默认从 HTTP 同步计算中剥离；部分数据诊断仍需要继续任务化。
- 后台预计算可以继续降低 Nginx/Cloudflare 等待后端超时的概率。

建议下一次开发目标：

> 把补数诊断和其它可能超过 5 秒的数据接口继续改为后台任务 + 进度 + 缓存读取。
## v0.2.106 访问日志与重任务闸门

- 访问审计改为默认异步队列写入，避免每个 API 请求在返回前同步读写 `access_logs.json` 大文件。切换策略、登录态检查和轻量快照不再被访问日志整文件重写拖慢。
- 前台推荐/日计划预计算和前台账户预热进程纳入 `QT_HEAVY_JOB_MAX_CONCURRENT`，与策略复盘、训练、回测、拟合同属会抢 CPU/SQLite 的维护任务。
- 当前架构仍不是最终形态：短期用 FastAPI + SQLite + 独立 Python 子进程支撑单机部署；长期最优方向是 API、worker、scheduler、数据库和访问审计存储进一步解耦。

## v0.2.107 访问日志批量落盘

- 访问审计后台线程改为按 `QT_ACCESS_LOG_BATCH_SIZE` 和 `QT_ACCESS_LOG_BATCH_WINDOW_MS` 合并写入，同一批日志只重写一次 `access_logs.json`。
- 这一步继续降低高频请求下的磁盘写放大；访问日志仍是运行审计数据，不参与策略、账户或交易结果。

## v0.2.108 auth/profile 热路径缓存

- `auth.json` 读取增加进程内缓存，按文件路径、mtime 和大小自动失效；`_save_auth()` 写入后会刷新缓存。
- 前台 profile 保存、登录态校验和用户资料读取不再在同一进程内为每次请求重复读认证文件；返回给调用方的是深拷贝，避免误改缓存对象。
- 这是迁移用户资料到更合适存储前的低风险过渡优化；`auth.json` 仍然是敏感运行数据，不进入 Git 或公开数据包。

## v0.2.109 前台 scope 鉴权瘦身

- `require_request_scope()` 只有在 admin scope 下才调用完整 `auth_status()` 检查 setup_required；frontend scope 只做 token/debug 校验。
- 前台切换策略、账户和快照请求减少固定认证开销；后台初始化和管理入口安全边界不变。

## v0.2.110 前台快照运维信息瘦身

- `/api/front/public_snapshot` 和 `/api/front/snapshot` 不再返回后台任务日志，也不再在前台状态摘要里暴露服务器 `data_dir`。
- 前台任务状态只保留调度器、运行中任务和暂停任务的轻量摘要，并通过 `QT_FRONT_JOBS_CACHE_TTL_SECONDS` 做秒级短缓存，减少切换策略后快照请求读取任务状态的固定开销。
- 后台日志和完整运维状态继续留在后台管理接口；前台用户界面只展示账户、策略、持仓、成交、新闻和必要的轻量运行状态。

## v0.2.111 新闻热路径索引

- 最新新闻时间、按日期读取新闻列表和按日期读取结构化事件的 SQL 去掉 `COALESCE(timestamp, 0)` 包列排序，改为可走索引的 `timestamp DESC` 排序。
- SQLite schema 补充 `idx_news_raw_timestamp_date`、`idx_news_raw_date_timestamp` 和 `idx_news_events_date_impact`，降低前台快照状态、新闻列表和事件摘要在新闻表变大后的临时排序开销。
- 部署脚本在跳过全量迁移时也会确认这些新闻热路径索引存在，避免已有服务器数据库因为未强制迁移而缺少索引。

## v0.2.112 最新新闻时间短缓存

- 前台快照和状态摘要里的 `latest_news_time` 增加进程内短缓存，默认 `QT_LATEST_NEWS_TIME_CACHE_TTL_SECONDS=5`，减少首屏、刷新和切换策略后连续请求反复访问 SQLite 最新新闻时间的固定开销。
- 该缓存只保存一个公开新闻时间字符串，过期后重新读取 SQLite；不改变新闻列表、结构化事件、策略信号、成交、持仓或用户账户结果。
- 这一步说明当前架构仍是“可持续优化的单机部署”，不是最终最优形态；后续仍应继续拆 router/service，并把更大规模数据与任务队列迁移到更专业的存储和 worker 架构。

## v0.2.113 前台数据日期边界短缓存

- 前台快照、账户读取和跟随起始日裁剪使用 SQLite `MIN/MAX` 轻量查询获得数据首日和最新日，并通过 `QT_DATA_DATE_CACHE_TTL_SECONDS=10` 做秒级短缓存。
- 只有 SQLite 中没有可用日期时才退回 `quant_engine.first_data_date()` / `quant_engine.latest_event_date()`；普通前台请求不再为了判断 `as_of` 拉起完整事件列表。
- 这一步继续推进“前台只读轻量摘要、重计算留在后台任务”的目标，降低切换策略后快照请求的冷启动和事件缓存重建风险。

## v0.2.114 轻量快照账户只读化

- `/api/front/snapshot?light=true` 读取账户时不再同步把策略运行表或账户缓存派生结果写入 `user_follow_*` 或账户缓存表；轻量快照只负责返回可展示结果。
- 用户跟随账户派生落库继续由 `frontend_account_precompute`、显式账户读取或后台维护任务处理，避免首屏和切换策略后的快照请求遇到 SQLite 写锁。
- 该调整不改变账户计算规则、跟随开始日裁剪或策略运行结果来源，只把写入职责从轻量首屏请求移回后台预热链路。

## v0.2.115 前台账户 GET 默认只读

- `/api/front/trading_account` 默认 `QT_FRONT_ACCOUNT_PERSIST_ON_READ=false`，读取到运行表或账户缓存结果时直接返回给前台，不再同步写入账户缓存或 `user_follow_*`。
- 如果读到账户结果但发现用户跟随账户尚未落库，响应会标记 `user_follow_persist_deferred` 并异步排队 `frontend_account_precompute`，由后台 worker 完成派生写入。
- `force=true` 或显式开启 `QT_FRONT_ACCOUNT_PERSIST_ON_READ=true` 仍可保留旧的读取即写入行为，用于本地排查；生产日常路径应保持 GET 只读。
## v0.2.116 后台策略运行矩阵接口

- 新增只读后台接口 `/api/admin/strategy_runtime/matrix`，汇总资金档策略和策略库模型的运行状态、运行日期、成交数、持仓数、收益、回撤和最新信号。
- 该接口只读取策略目录、`strategy_runtime_*` 汇总和 `strategy_daily_signals` 信号 feed，不触发训练、复盘、回测、账户预热或任何同步重算。
- 线上排查“用户切换策略后账户慢或 pending”时，先看矩阵里的 `runtime_status`、`has_runtime_data`、`signal_count` 和 `missing_count`，确认问题是运行结果缺失、信号缺失还是账户预热队列滞后。

## v0.2.117 后台策略运行矩阵页面

- 后台“策略”页新增“策略运行矩阵”面板，直接展示矩阵接口返回的策略数量、已落库数量、缺运行结果数量、有信号数量和逐策略明细。
- 页面刷新矩阵只调用 `/api/admin/strategy_runtime/matrix` 只读接口，不触发训练、复盘、回测、账户预热或即时回放。
- 运维排查切换策略慢时可以先在后台页面确认目标策略是否缺少 `strategy_runtime_*` 结果，再决定手动复盘、导入运行结果或运行前台账户预热。

## v0.2.118 策略运行矩阵服务拆分

- 将策略运行矩阵的目录去重、运行汇总合并、信号状态合并和 `runtime_status` 判断迁入 `app.quant.strategy_runtime_matrix`，接口层只负责读取依赖数据和返回结果。
- 新增服务级单测覆盖 `active` 基准参数排除、重复策略去重、模型数量上限、`ready`/`signals_only`/`missing` 状态判断和信号摘要合并。
- 这是后续拆分 `backend/app/main.py` 到独立 router/service 的小步结构优化，不改变矩阵接口、后台页面或前台账户计算行为。

## v0.2.119 后台策略运行 router 起步

- 新增 `app.routers.admin_strategy_runtime`，把 `/api/admin/strategy_runtime/matrix` 的路由定义从 `main.py` 迁出，保留原 URL、查询参数和返回结构不变。
- `main.py` 继续提供矩阵 payload 回调并注册 router；这避免 router 反向导入 `main.py`，为后续继续迁移后台策略运行相关接口打基础。
- 新增 router 级单测确认 `as_of`、`limit_models` 和 `include_signals` 查询参数仍按原合同传入 payload。

## v0.2.120 后台策略运行接口成组迁移

- `app.routers.admin_strategy_runtime` 继续接管 `/api/admin/trading_account` 和 `/api/admin/strategy_runtime/replay`，与矩阵接口组成后台策略运行 router。
- `main.py` 保留 `_admin_strategy_trading_account` 和 `_admin_strategy_runtime_replay` payload 回调，router 只声明 HTTP 参数和 URL，避免当前阶段过度重构业务依赖。
- 路由级单测覆盖矩阵、策略账户和策略回放三条接口的查询参数合同，确认后台页面依赖的原 URL 不变。

## v0.2.121 前台切换策略慢路径继续收口

- 用户注册和前台 profile 保存显式以 `start_worker=false` 排队账户预热；即使旧服务器环境变量仍把 `QT_FRONT_ACCOUNT_START_WORKER_ON_PROFILE` 设为 `true`，切换策略请求也不会在保存链路里启动账户预热 worker。
- 轻量 `/api/front/snapshot?light=true` 读取策略运行账户时会优先使用 `strategy_runtime_snapshots` 的每日账户快照快路径；命中后不扫描整段 `strategy_runtime_trades`，也不在首屏请求中重建完整成交/交割单。
- 完整成交、交割单和用户跟随账户落库仍由 `/api/front/trading_account`、后台“前台账户预热”或后台策略账户页按需处理；切换策略本身只负责保存跟随关系、记录跟随开始日和排队预热。

## v0.2.122 前台 profile router 起步

- 新增 `app.routers.frontend_profile`，将 `/api/front/profile` 的 GET/POST 路由声明从 `main.py` 迁出；router 只负责 HTTP 参数和 URL，保存 profile 的业务逻辑仍由 `main.py` payload 回调承接。
- 新增 router 级单测固定 `include_catalog` 查询参数和请求 payload 透传，确认前台切换策略依赖的原 URL、默认参数和返回合同不变。
- 这是继续拆分前台 router 的小步结构优化，不改变用户跟随开始日、账户预热排队、策略目录轻量读取或账户快照快路径。

## v0.2.123 前台运行视图 router 起步

- 新增 `app.routers.frontend_runtime`，将 `/api/front/public_snapshot`、`/api/front/snapshot`、`/api/front/strategy_models` 和 `/api/front/trading_account` 的路由声明从 `main.py` 迁出。
- router 层只保留 HTTP 参数、默认值和范围校验，业务 payload 继续复用原前台快照、策略目录和账户回调；前台首屏、策略页和账户页 URL 不变。
- 新增 router 合同测试覆盖 `as_of`、`mobile`、`light`、`include_catalog`、`limit`、`force`、`defer` 参数透传，继续降低 `main.py` 体积而不改变热路径行为。

## v0.2.124 前台买入视图 router 起步

- 新增 `app.routers.frontend_signal`，将 `/api/front/recommendations` 和 `/api/front/daily_plan` 的路由声明从 `main.py` 迁出。
- router 层只声明 `as_of`、`lookback_days`、`top_n`、`start_date`、`limit_days`、`force`、`defer` 参数和范围校验；推荐、日计划、短缓存、pending 和预计算任务仍由原 payload 回调处理。
- 新增 router 合同测试确认推荐和日计划参数透传，以及 `QT_FRONT_PAYLOAD_DEFER_MISSES` 对 `defer` 默认值的注入边界不变。

## v0.2.125 后台概览 router 起步

- 新增 `app.routers.admin_overview`，将 `/api/admin/snapshot` 和 `/api/admin/model_signals` 的路由声明从 `main.py` 迁出。
- router 层只保留 `as_of`、`light`、`limit_models`、`limit_per_model` 参数和范围校验，后台概览、模型信号、轻量缓存和状态汇总仍由原 payload 回调处理。
- 新增 router 合同测试确认后台概览和模型信号参数透传，继续按“先迁路由、再拆 service”的节奏降低 `main.py` 体积。

## v0.2.126 后台数据库与缓存 router 起步

- 新增 `app.routers.admin_data_cache`，将 `/api/admin/database/tables`、`/api/admin/database/table/{table_name}`、`/api/admin/cache/status` 和 `/api/admin/cache/clear` 的路由声明从 `main.py` 迁出。
- router 层只保留表名、分页和缓存清理 scope 参数校验；数据库概览、表分页、运行缓存状态、内存缓存清理和 SQLite 短缓存清理仍由原 payload 回调处理。
- 新增 router 合同测试确认数据库/缓存参数透传和表分页范围校验，继续把后台运维接口拆成独立模块。

## v0.2.127 后台访问审计 router 起步

- 新增 `app.routers.admin_access`，将 `/api/admin/access_logs`、`/api/admin/access_security`、`/api/admin/access_security/block`、`/api/admin/access_security/unblock` 和 `/api/admin/access_security/block_all` 的路由声明从 `main.py` 迁出。
- router 层只保留访问日志分页/筛选、异常访问数量上限和封禁请求 body 透传；访问日志读取、异常分类、手动拉黑、解除拉黑和一键拉黑仍由原 payload 回调处理。
- 新增 router 合同测试确认访问审计参数透传和范围校验，后台安全能力继续保留原 admin scope 边界。

## v0.2.128 后台用户管理 router 起步

- 新增 `app.routers.admin_frontend_users`，将 `/api/admin/frontend_users` 及其创建、更新、重置密码、封禁、解封和删除子路径的路由声明从 `main.py` 迁出。
- router 层只保留路径参数、请求 body 和 `Request` 透传；用户创建、profile 更新、跟随周期记录、缓存清理、密码重置、封禁和删除仍由原 payload 回调处理。
- 新增 router 合同测试确认后台用户管理 URL、路径参数和 body 透传不变，后台用户管理继续只在 admin scope 下访问。

## v0.2.129 前台预计算默认手动化

- `QT_FRONT_PAYLOAD_PRECOMPUTE_ENABLED` 默认改为 `false`，调度器不会在启动后约 100 秒自动跑前台推荐/日计划批量预计算；如需定时批量刷新，必须在线上显式开启。
- 新增 `QT_FRONT_PAYLOAD_AUTO_PRECOMPUTE_ON_MISS`，默认 `false`；前台推荐和日计划缓存未命中时只返回轻量 pending/disabled 状态，不再自动启动 `frontend_payload_precompute` 抢占 CPU。
- 推荐缓存默认 TTL 调整为 1800 秒，与日计划缓存和预计算间隔对齐；后台“预计算前台缓存”按钮默认 `force=false`，会跳过未过期缓存，避免一次点击强制重算所有用户。

## v0.2.130 前台预计算策略可观测

- `/api/jobs/status` 新增 `frontend_payload_policy`，展示前台推荐/日计划预计算是否启用、缓存未命中自动补算是否实际生效、TTL、批量用户上限和是否独立进程运行。
- 后台自动调度器面板会按真实配置显示“前台预计算”为手动或下次运行时间，不再在默认手动配置下误提示调度器会自动跑前台预计算。
- 启动自动调度器的确认文案会区分“前台预计算保持手动”和“按配置自动预计算”，便于线上判断 CPU 忙是否来自管理员手动任务、调度器还是普通用户访问。

## v0.2.131 任务运维 router 起步

- 新增 `app.routers.admin_jobs`，将 `/api/jobs/status`、`/api/jobs/logs`、`/api/logs/runtime`、调度器启动/停止和任务暂停/恢复/停止路由声明从 `main.py` 迁出。
- router 层只保留 HTTP 参数、路径和回调透传，任务状态组装、日志读取、调度器控制和任务控制仍复用原 `job_manager` 与 `_jobs_status_payload`。
- 前台 `front_jobs` 摘要改为使用 `job_manager.frontend_status()`，只读取调度器、running 和 paused 三个轻字段，不再在前台快照缓存未命中时触发完整任务状态巡检。
- 这一步继续收缩 `main.py` 的运维控制面，并把前台状态摘要从后台完整运维状态中解耦，为后续拆分具体任务执行入口和进程策略配置打基础。

## v0.2.132 任务执行 router 起步

- 新增 `app.routers.admin_job_runs`，将新闻抓取、行情同步、AI 分析、交易循环、策略复盘、前台预计算、前台账户预热、每日交易和系统启动流程的路由声明从 `main.py` 迁出。
- `main.py` 继续保留任务 payload 回调和系统启动流程实现，router 只声明 URL、查询参数、默认进程开关和范围校验，避免在拆分时改变任务执行语义。
- 新增 router 合同测试覆盖所有已迁出的任务执行入口，确认原 URL、参数和默认值仍按既有合同传入回调。

## v0.2.133 核心系统 router 起步

- 新增 `app.routers.core_system`，将版本、认证状态、登录/注册、调试状态、运行配置和 `/api/status` 的路由声明从 `main.py` 迁出。
- `/api/status` 的任务摘要改为使用 `job_manager.frontend_status()`，只返回调度器、running 和 paused 轻量字段；完整任务巡检继续保留在后台 `/api/jobs/status`。
- 前台公开快照和登录快照共用 `front_snapshot_news` 新闻摘要短缓存，默认 `QT_FRONT_SNAPSHOT_NEWS_CACHE_TTL_SECONDS=30`，避免登录首屏在账户 pending 时重复读取新闻并计算情绪。
- 新增 router 合同测试和集成测试，确认核心系统 URL、请求 body 透传和状态轻量化边界不变。

## v0.2.134 前台预计算限流与快照冷启动瘦身

- `frontend_payload_precompute` 默认批量用户数从 50 收敛为 8，日计划预热默认范围从 120 天收敛为 30 天，并新增 `QT_FRONT_PAYLOAD_PRECOMPUTE_MAX_SECONDS=20` 时间预算；手动任务会跳过未过期缓存并按预算分批完成，避免一次点击长时间扫完所有用户。
- 前台账户预热异步入队改为可配置 worker 池，默认 `QT_FRONT_ACCOUNT_PRECOMPUTE_ASYNC_WORKERS=4`，并保留 `QT_FRONT_ACCOUNT_PRECOMPUTE_ASYNC_DISPATCH_DELAY_MS=25` 轻微错峰，避免单个慢入队或队列锁等待拖住后续首屏/账户请求。
- 轻量前台快照默认不在新闻 SQLite 读取失败时回退完整 `quant_engine.news_feed()`，由 `QT_FRONT_SNAPSHOT_LIGHT_NEWS_NO_ENGINE_FALLBACK=true` 控制；日期边界默认只读 SQLite，`QT_DATA_DATE_ENGINE_FALLBACK_ENABLED=false` 避免空库或异常库触发完整事件集合加载。
- 后台 `frontend_payload_policy` 会展示前台预计算 `max_seconds`，便于线上判断手动任务是否被时间预算切分。

## v0.2.135 研究重任务手动边界加固

- 新增 `QT_RESEARCH_TASKS_MANUAL_ONLY=true` 默认边界：即使误开 `STRATEGY_REPLAY_ENABLED` 或 `STRATEGY_EVOLUTION_ENABLED`，调度器也不会自动跑策略复盘或策略进化；这些任务应由后台按钮或显式维护命令触发。
- 调度器在确实允许研究任务自动运行时，也会默认使用 `QT_STRATEGY_REPLAY_PROCESS_ENABLED=true` 和 `QT_STRATEGY_EVOLUTION_PROCESS_ENABLED=true` 走独立 Python 子进程，避免训练/复盘卡住 API 进程。
- `/api/quant/timeline` 和 `/api/quant/intraday_timeline` 新增 `QT_TIMELINE_REQUIRE_MANUAL_TRIGGER=true` 默认保护；缓存未命中时普通刷新只返回 `manual_required`，必须显式 `manual=true` 才会启动时间线回测任务。
- 前台推荐/日计划预计算的调度首次运行改为由 `QT_FRONT_PAYLOAD_PRECOMPUTE_INITIAL_DELAY_SECONDS` 控制，默认 1800 秒；即使显式开启定时预计算，也不会在调度器启动约 100 秒后马上抢 CPU。
- 量化引擎单测的回测和 walk-forward 场景固定短日期窗口，不再依赖本地真实历史数据规模，避免测试把全量回测误当作常规快速验证。

## v0.2.136 代理 HTML 响应前端兜底

- 前台和后台的统一 API helper 改为先读取响应文本、识别 Cloudflare/Nginx/HTML 错误页，再执行 `JSON.parse`；即使代理把 HTML 错误页或静态首页误以 2xx 返回，也不会把 HTML 当作业务 JSON 继续处理。
- `/api/auth/status` 初始化路径复用同一套 JSON 读取逻辑，避免登录初始化阶段遇到源站错误时只显示浏览器 JSON 解析异常。
- 这一步不改变后端接口语义，只补齐用户可见错误兜底；502/504/524 的根因仍应在服务进程、Nginx upstream、CPU/内存和重任务状态中排查。

## v0.2.137 量化基础接口 router 拆分

- 新增 `app.routers.quant_basic`，将 `/api/quant/dashboard`、推荐、日计划、基础参数、事件、新闻、相关性、组合、交易账户、手动运行和 `/api/news_history` 的路由声明从 `main.py` 迁出。
- router 层只保留 HTTP 参数、默认值和范围校验；量化计算、新闻兜底、通知、参数更新和账户读取仍由 `main.py` 的 payload 回调承接，避免拆分时改变业务语义。
- 新增 router 合同测试确认 URL、请求体和查询参数透传不变，为后续把研究/回测、数据诊断和 AI 状态继续分模块拆出做准备。
