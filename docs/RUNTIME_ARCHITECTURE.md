# 运行架构边界

本项目的产品主线是：服务器维护公共数据和一组已训练策略，日常轻量生成策略买卖结果；用户在前台选择一个策略跟随；训练、优化、回测只作为后台手动维护任务运行。

## 目标形态

- 默认目标策略数：20 个，可通过 `QT_TARGET_STRATEGY_COUNT` 调整。
- 日常运行只做公共数据收集、AI 分析、行情同步、补数、单日策略刷新和交易循环。
- 用户前台只读取已有策略运行结果、用户跟随快照或 pending 状态。
- 策略复盘、策略进化、模型回测、通用回测、参数拟合默认只手动触发。
- 研究重任务必须进入独立进程，并受 `QT_HEAVY_JOB_MAX_CONCURRENT` 并发闸门控制。
- 前台缓存/账户预热使用独立的前台运行进程池，受 `QT_FRONT_RUNTIME_JOB_MAX_CONCURRENT` 和 `QT_FRONT_RUNTIME_JOB_CPU_THREADS` 控制，不占用研究重任务槽位。

## 分区

| 分区 | 职责 | 代表任务 |
| --- | --- | --- |
| 公共数据层 | 新闻、AI、行情、日K、分时、龙虎榜入库 | `news_fetch`、`ai_analysis`、`market_sync`、`kline_fill`、`lhb_sync` |
| 日常策略运行层 | 用已训练策略和最新数据生成、读取买卖运行结果 | `strategy_daily_refresh`、`trade_cycle` |
| 用户跟随层 | 用户资金、策略 ID、跟随开始日派生账户视图 | `frontend_account_precompute` |
| 前台缓存层 | 推荐和日计划短缓存预热 | `frontend_payload_precompute` |
| 研究优化层 | 策略复盘、训练、回测、拟合 | `strategy_replay`、`strategy_evolution`、`model_backtest`、`quant_timeline`、`quant_backtest`、`fit_strategy` |
| 诊断层 | 数据覆盖率、缓存和运行状态检查 | `data_coverage` |

## 代码契约

运行边界集中在 `backend/app/quant/runtime_policy.py`。后台任务状态会返回 `runtime_policy`，用于展示：

- `target_strategy_count`
- `research_tasks_manual_only`
- `daily_runtime_jobs`
- `research_manual_jobs`
- `research_process_jobs`
- `frontend_runtime_process_jobs`
- `heavy_process_jobs`
- `heavy_process_limit`
- `running_heavy_jobs`
- `frontend_runtime_process_limit`
- `running_frontend_runtime_jobs`
- `process_pools`
- `stop_controls`

新增任务时必须先归类到上述分区，再决定是否允许自动调度、是否必须走独立进程、进入哪个进程池、是否受研究重任务并发闸门控制、停止语义是当前进程终止还是检查点退出。

策略运行就绪度由后台只读矩阵和摘要判断：

- `/api/admin/strategy_runtime/matrix` 返回每个策略的运行区间、成交数、信号数和 `overview`。
- `/api/status` 和后台 job status 返回 `jobs.strategy_runtime` / `strategy_runtime`，用于轻量展示目标策略数、已准备数、缺失数、过期数和 `ready_for_frontend`。
- `overview.ready_count / overview.target_strategy_count` 是前台可跟随策略是否准备好的核心指标。
- `overview.ready_for_frontend=true` 表示目标策略数都已有可读运行结果，且没有早于请求日期的过期运行结果。
- 缺失策略应通过后台手动策略复盘或导入运行结果补齐，不应由前台用户请求同步重算。

日常策略运行分两步：`strategy_daily_refresh` 用小滚动窗口刷新目标日期的 20 个目标策略运行结果，并以 `daily_strategy_runtime` 来源写入 `strategy_daily_signals`、`strategy_runtime_trades`、持仓、结算和快照表；`trade_cycle` 通过 `backend/app/quant/strategy_daily_runtime.py` 读取目标策略集和运行摘要，只有 `ready_for_frontend=true` 时才汇总当天买卖并发送通知。默认 `QT_TRADE_CYCLE_REQUIRE_STRATEGY_RUNTIME_READY=1` 时，目标策略日运行未就绪会返回 `skipped` 和 `reason=strategy_runtime_not_ready`，不发送 partial 通知、不把未完整覆盖的成交计入本轮交易循环。`trade_cycle` 的结果里会包含 `strategy_runtime`，用于判断这轮日常运行是否具备 20 个策略的可跟随结果。旧的全局纸面账户路径默认关闭，只保留显式兼容开关，不作为前台用户跟随账户的数据来源。

单日策略刷新必须能在轻量窗口里生成目标日买入候选和卖出动作。日线信号如果没有已加载的下一行情日期，引擎会按交易日历推算 `execute_on`，让前台能看到“今天筛选、下个交易日执行”的信号；卖出动作通过 `QT_STRATEGY_DAILY_REFRESH_LOOKBACK_DAYS` 控制的小回看窗口恢复最近持仓状态，而不需要扩大成完整历史复盘。

`strategy_runtime.status_summary()` 是状态面专用入口，只读取目标策略目录和运行摘要，不读取信号、成交，不触发复盘、训练、进化或回测。

前台日结果读取必须按目标日期和同一运行批次对齐：如果目标日新刷新没有信号或成交，读取层应返回目标日的空信号/空成交，而不能回退显示旧日期信号或旧批次成交。`strategy_daily_runtime.daily_result()` 会忽略 `model_signal_feed` 的旧日期 fallback，并用运行批次 `generated_at` 限定目标日成交。

日常运行落库也必须按“同模型、同来源、同目标日期范围”替换旧批次，不按旧的 `start_date + params_hash` 窄范围残留多套数据。这样 `QT_STRATEGY_DAILY_REFRESH_LOOKBACK_DAYS` 或策略参数变化后，目标日的空信号/空成交也会真实覆盖旧结果。

`strategy_daily_refresh` 不是完整历史复盘，不训练、不进化、不回测，也不属于 `HEAVY_PROCESS_JOBS`。完整历史窗口补齐仍通过后台手动 `/api/jobs/strategy/replay` 进入研究优化层；日常补跑单日结果走 `/api/jobs/strategy/daily_refresh`。

进程池边界必须保持清楚：`HEAVY_PROCESS_JOBS` 只代表研究优化层的复盘、进化、回测、拟合；`frontend_payload_precompute` 和 `frontend_account_precompute` 进入 `frontend_runtime_process_slots`，只做前台缓存/用户账户预热。两个池互不占用并发槽，只有全局 `QT_MEMORY_GUARD_*` 内存守卫是共享保护线。`/api/status` 同时暴露 `heavy_job_observability`、`frontend_runtime_observability` 和 `daily_runtime_observability`，后台页面必须按对应分区显示进度、ETA、CPU 和内存。

管理页的策略库/策略运行矩阵必须把 `strategy_daily_refresh` 作为日常运行层的一等入口展示：显示目标策略日运行 `ready_count / target_strategy_count`、缺失/过期数量、任务进度、ETA、CPU/内存守卫和手动“刷新今日 20 策略”按钮。这个入口只刷新当天目标策略信号和成交，不允许顺手启动 `strategy_replay`、`strategy_evolution`、`model_backtest` 或其他研究优化任务。

`strategy_daily_refresh` 的 payload 和结果必须包含 `data_dependencies`，用轻量 SQLite 计数说明目标日期的原始新闻、结构化新闻事件、日 K、分时行情和龙虎榜覆盖情况。该字段只作为可见性和诊断信息，不自动触发补数、新闻抓取或重任务；缺失数据应显示为 `partial` 和 warnings，由数据采集层或后台手动补齐。默认 `QT_STRATEGY_DAILY_REFRESH_REQUIRE_READY_DATA=1` 时，目标日期新闻/行情依赖未就绪会直接返回 `skipped` 和 `reason=data_dependencies_not_ready`，不运行策略、不写入当天空信号/空成交，避免把“数据缺失”误显示成“今天无操作”。

调度器显式启用时，`strategy_daily_refresh` 会作为日常策略运行层任务在交易日自动刷新单日结果；如果刷新任务仍在执行，`trade_cycle` 会延后，避免读取半更新的策略运行表。该任务的调度由 `QT_STRATEGY_DAILY_REFRESH_ENABLED`、`QT_STRATEGY_DAILY_REFRESH_INTERVAL_SECONDS`、`QT_STRATEGY_DAILY_REFRESH_INITIAL_DELAY_SECONDS`、`QT_STRATEGY_DAILY_REFRESH_MODE` 和 `QT_STRATEGY_DAILY_REFRESH_PROCESS_ENABLED` 控制，和研究优化层的 `strategy_replay` / `strategy_evolution` 开关相互独立。

## 不允许的路径

- 前台用户请求触发策略训练、策略进化或大回测。
- 普通首屏快照同步写入大量账户派生结果。
- 自动调度器在默认配置下运行策略复盘、训练、回测或参数拟合。
- 研究任务绕过 `QT_HEAVY_JOB_MAX_CONCURRENT` 并发限制。

## `strategy_runtime.daily_result`

`trade_cycle` 返回的 `strategy_runtime.daily_result` 是前台“每天买卖情况”的轻量入口。它只读取已经落库的 `strategy_daily_signals` 和 `strategy_runtime_trades`，不会触发训练、进化、回测或复盘。

关键字段：

- `target_strategy_count`：目标可跟随策略数量，默认 20。
- `data_date`：本次读取到的信号/成交日期；如果请求日期没有数据，可能回落到最近有数据的日期，并通过 `fallback_latest` 标记。
- `signal_count`、`trade_count`、`buy_count`、`sell_count`：目标策略集合内当天汇总。
- `ready_model_count`、`missing_model_count`、`stale_model_count`：目标策略集合里当天可跟随、运行表缺失、运行结果过期的数量。
- `no_signal_model_count`、`no_trade_model_count`：可跟随策略里当天没有信号或没有成交的数量；这表示“今天无操作”，不是运行缺失。
- `readiness`：按 `ready_models`、`missing_models`、`stale_models`、`no_signal_models`、`no_trade_models` 列出最多 20 个模型，供后台和前台明确解释 partial 状态。
- `items[]`：按目标策略列出 `model_id`、运行状态、`ready_for_follow`、`blocking_reasons`、`signal_status`、`trade_status`、信号列表和成交列表。

前台页面需要展示某个用户跟随策略的买卖时，应优先读取用户跟随快照；需要展示“20 个策略今天整体是否可跟随/有没有信号”时，读取 `strategy_runtime.daily_result` 和 `strategy_runtime.overview`。前台跟随策略的日买卖入口会返回 `follow_readiness`，其中 `missing_runtime` / `stale_runtime` 是不可跟随原因，`no_signal` / `no_trade` 是当天无操作说明。

## 前台轻量读取入口

- `/api/front/strategy_daily`：返回当前登录用户跟随策略的当天信号、成交和轻量新闻。它复用 `strategy_runtime.daily_result`，只读已落库结果，不会同步计算账户、不触发训练、不触发回测。
- 登录用户的前台买入/成交视图必须只使用 `/api/front/strategy_daily` 已落库信号和成交；缺少日运行结果时显示 pending/待运行/今日无操作，不回退展示 `/api/front/recommendations`、`/api/front/daily_plan` 或账户历史成交。
- `/api/front/snapshot?light=true`：登录用户首屏会内嵌同一份 `strategy_daily`，前台只有在快照缺失或日期不一致时才单独补拉 `/api/front/strategy_daily`。
- `/api/front/trading_account`：返回当前用户跟随策略的账户视图；运行结果缺失时可以进入预热队列，但默认不应在请求链路里重算大回放。即使前台传入 `force=true` 或 `defer=false`，也只有显式开启 `QT_FRONT_ACCOUNT_SYNC_COMPUTE_ENABLED` 后才允许同步补算。
- `/api/front/strategy_models`：返回可选择的目标策略列表和当前跟随策略。列表总量按 `QT_TARGET_STRATEGY_COUNT` 收敛，先包含资金档策略，再补进化模型；超过目标集合的模型不作为前台可跟随选项。

前台策略目录中的资金档策略和策略库模型都必须携带 `follow_readiness` / `ready_for_follow`，说明该策略是否已有可读取的运行结果、运行区间、信号数和成交数。用户仍可选择目标目录内的策略，但页面必须明确显示“可跟随 / 待运行 / 运行过期”等状态，避免把没有日运行结果的策略误认为当天有买卖数据。

前台 `strategy_daily` 读取会通过 `QT_FRONT_STRATEGY_DAILY_CACHE_TTL_SECONDS` 做短 TTL 合并，默认 10 秒，避免多个用户同时刷新时反复装配同一天 20 个策略的日信号和成交。
前台资料更新和轻量读取都会用同一份目标策略目录校验 `strategy_model_id`。旧账号如果保存了非目标模型 ID，会自动回到按资金和目标目录推荐的策略，避免用户跟随一个不会被 `strategy_daily_refresh` 日常刷新的模型。
前台“策略相关新闻/已关联”只能显示 `/api/front/strategy_daily` 返回的 `related_news`，不能在为空时回退到全局结构化新闻事件；否则用户会误以为非跟随策略的新闻和当前策略买卖有关。全局新闻仍可在普通新闻视图展示。

`strategy_daily_refresh` 的目标模型目录必须和 `/api/front/strategy_models` 的可跟随目录保持同一裁剪规则：先放入资金档策略，再用剩余名额从策略库补可复用模型；不可复用模型和目标数量之外的模型不能进入日常刷新集合。`QuantJobManager.run_strategy_daily_refresh()` 必须通过 `strategy_daily_runtime.target_models()` 取日常目标目录，不能复用研究复盘用的 `_strategy_replay_targets()`。这样用户不会选到一个日常策略日刷新不会覆盖的模型。

代码归属：

- `backend/app/quant/frontend_snapshot.py` 负责前台快照读取编排和前台 runtime router 的位置参数契约：公开快照、登录用户快照、新闻短缓存、轻量账户嵌入、推荐/日计划缓存拼接和账号预热挂载；`main.py` 直接把服务方法接给路由。
- `backend/app/quant/frontend_strategy_daily.py` 负责把当前用户跟随策略映射到当天信号、成交和新闻。`/api/front/strategy_daily` 是前台用户跟随策略的日常只读入口，只读取 `strategy_daily_runtime` 已落盘结果和轻量新闻快照，不训练、不复盘、不触发账户重算；响应需保留原始 `daily`，并提供平铺的 `signals`、`trades`、`buy_trades`、`sell_trades`、`trade_summary` 和按当天信号/成交股票筛出的 `related_news`。
- `backend/app/quant/strategy_daily_dependencies.py` 负责 `strategy_daily_refresh` 的轻量数据依赖快照：原始新闻、结构化事件、日 K、分时行情和龙虎榜日期覆盖情况。它只做诊断读取，不抓取、不补数、不启动重任务。
- `backend/app/quant/quant_backtest_service.py` 负责通用回测的路由参数适配、短缓存、手动触发门禁、后台任务/独立进程派发和 pending 响应。`main.py` 只把服务方法接给路由，普通刷新不应绕过该服务直接启动回测。
- `backend/app/quant/quant_basic_service.py` 负责基础量化读接口和显式手动运行入口的 payload 编排：dashboard、推荐、日计划、策略参数、事件、新闻、组合、纸面账户和 `/api/quant/run`。`main.py` 只把服务方法接给路由。
- `backend/app/quant/quant_strategy_research_service.py` owns the admin strategy-research route composition: fit strategy, evolution status/trace/control, model catalog reads, stored-vs-recompute model backtest selection, strategy model lookup 404 mapping, model apply 404 mapping, and strategy evolution dispatch. It is the HTTP-facing boundary before the manual/deferred/process guards in the underlying research services.
- `backend/app/quant/quant_research_composition.py` owns construction of the quant/research partition: basic quant reads, generic backtest, model backtest, strategy lookup, timeline replay, fit optimization, evolution control, and the HTTP-facing research service.
- `backend/app/quant/application_research_bootstrap.py` owns top-level research partition wiring for the app runtime: quant/research services, model lookup/backtest services, fit/evolution services, and their explicit heavy-job route services. It depends on already-built frontend read helpers for saved catalog/news reads but does not build frontend runtime state.
- `backend/app/quant/strategy_model_backtest_service.py` 负责单个策略模型的已保存回测读取、手动重算门禁、短缓存和后台/独立进程派发。模型详情页默认只能读保存结果，重算必须显式手动触发。
- `backend/app/quant/quant_timeline_service.py` 负责策略时间线/分时回放的路由参数适配、模型参数解析、模型查找 404 映射、短缓存、手动触发门禁、后台任务/独立进程派发和 pending 响应。普通读取不能绕过它直接启动 timeline 重算。
- `backend/app/quant/fit_strategy_service.py` 负责策略参数拟合优化的手动触发门禁、同步执行、结果压缩、后台任务/独立进程派发和 pending 响应。默认 `QT_FIT_STRATEGY_REQUIRE_MANUAL_TRIGGER=1` 时，未带 `manual=true` 的调用只能返回 `manual_required`，不能排队或执行拟合优化。
- `backend/app/quant/strategy_evolution_service.py` 负责策略进化优化的运行中门禁、独立进程派发、后台启动、同步执行分流，以及策略模型/资金档预设应用到系统默认策略参数的写入编排。`main.py` 只把路由参数转交给该服务。
- `backend/app/quant/system_startup_service.py` 负责服务器启动/每日启动编排：新闻、AI 分析、日 K、龙虎榜、分时行情和轻量交易循环；策略复盘、训练和回测默认跳过，只有显式 `run_strategy_replay` 才进入重任务路径。
- `backend/app/quant/admin_job_run_service.py` 负责后台显式手动 job 启动入口：新闻抓取、行情同步、AI 分析、交易循环、策略复盘、前台 payload 预热和前台账户预热。这些入口是正常轻量运行和手动重任务之间的边界。
- `backend/app/quant/system_control_service.py` 负责系统控制入口：API 重启开关、重启脚本/运行时可用性检查、后台重启任务安排、通知状态和通知测试。`main.py` 只把服务方法接给路由。
- `backend/app/quant/data_collection_service.py` 负责公共数据采集入口：BIYING/分时行情状态和同步、K 线补数、龙虎榜状态和同步。`main.py` 只把服务方法接给路由。
- `backend/app/quant/data_coverage_service.py` 负责数据覆盖率诊断的路由参数适配、日期归一、短缓存、后台/独立进程派发、压缩任务结果和 pending 响应；`main.py` 只把服务方法接给路由。
- `backend/app/quant/operations_composition.py` owns construction of the operations partition: data coverage diagnostics, public data collection controls, and lightweight AI monitoring reads.
- `backend/app/quant/application_operations_bootstrap.py` owns top-level operations partition wiring for the app runtime: data coverage diagnostics, public data collection controls, BIYING/LHB/intraday status and sync, and lightweight AI monitoring reads.
- `backend/app/main.py` 应只保留请求、缓存和依赖接线；新增前台业务拼装时优先放入上述模块。

## 重任务资源控制

训练、优化、复盘和回测类任务必须按“手动触发 + 独立进程 + 资源预算 + 可观察进度”运行。

代码归属：

- `backend/app/quant/runtime_policy.py` 定义任务分区、目标策略数量、手动研究任务开关和资源预算策略。
- `backend/app/quant/job_runtime_control.py` 负责重任务槽位、准入结果、进程启动命令/环境、进度 ETA、进程内存快照和内存守卫判断。
- `backend/app/quant/job_process_launcher.py` 负责独立任务进程 payload、命令、环境变量和 `Popen` 启动细节；`jobs.py` 只保留状态机和准入编排。
- `backend/app/quant/job_process_lifecycle.py` 负责独立任务进程存活检测、进程树终止和陈旧进程状态修复规则。
- `backend/app/quant/job_scheduler_policy.py` 负责调度频率、前台预计算策略、研究任务手动模式和策略数量/批量上限等运行策略默认值。
- `backend/app/quant/job_scheduler_status.py` 负责 scheduler 心跳状态快照字段和下一次运行时间格式化，避免调度循环混入状态展示规则。
- `backend/app/quant/jobs.py` 只负责调度、状态落盘、日志、停止/恢复和把具体任务函数接入上述资源控制边界。
- `backend/app/quant/data_import_service.py` 负责后台数据导入/迁移维护：作业状态、进度、上传流接收、大小限制、包校验、备份、导出包、样例状态清理、合并执行和导入后的量化缓存刷新；`backend/app/main.py` 只保留 HTTP 异常映射、文件响应和路由接线。
- `backend/app/quant/app_lifecycle.py` 负责 FastAPI lifespan 内的调度器启停策略，默认保持手动任务/轻量运行，只有显式开启调度时才启动后台 scheduler。

状态入口：

- 后台任务状态返回 `runtime_policy.resource_controls`，包含 `max_concurrent`、`cpu_threads`、内存保护阈值和进程启动宽限时间。
- 后台任务状态同时返回 `runtime_policy.daily_strategy_resource_controls`，用于 `strategy_daily_refresh` 这类正常日常策略运行；它默认独立进程、较小 CPU 线程预算，不进入手动研究重任务并发槽位。
- `heavy_process_slots` 返回当前运行中的重任务、可用槽位、同一份资源控制信息、当前 `memory_guard` 快照和可用平台上的运行进程内存采样。
- `runtime_policy.stop_controls` 和 `heavy_job_observability[].stop_policy` 返回每个任务的停止语义：任务分区、是否属于手动研究层、是否属于日常运行层、是否支持独立进程终止、是否支持检查点停止。
- `heavy_job_observability` 返回每个手动重任务的统一展示行：分区、状态、进度、启动/结束时间、已耗时、预计总耗时、剩余时间、ETA、进程 PID、资源控制、内存守卫状态、运行进程内存采样和停止能力。管理页应优先用该字段展示优化/回测任务进度，并在运行中显示停止按钮。
- `daily_runtime_observability` 返回日常策略运行层的统一展示行，当前覆盖 `strategy_daily_refresh` 和 `trade_cycle`。`strategy_daily_refresh` 可以独立进程运行并显示 PID、ETA、CPU 预算、内存守卫、进程内存采样和停止能力，但它不进入 `heavy_process_slots`，不会挤占手动训练/复盘/回测并发槽位。
- 手动启动的重任务会在落盘状态里写入本次 `resource_controls`、启动时的 `memory_guard`、`estimated_total_seconds` 和 `estimate_source`；进度尚未推进时也能用初始任务画像给出 ETA。
- 运行中的重任务会返回 `progress_pct`、`progress_message`、`elapsed_seconds`、`estimated_total_seconds`、`eta_seconds`、`eta_at` 和 `estimate_source`；进度超过 1% 后 ETA 会按实时进度重算，完成后以实际耗时覆盖估算。

控制入口：

- `QT_HEAVY_JOB_MAX_CONCURRENT`：重任务最大并发，默认 1。
- `QT_HEAVY_JOB_CPU_THREADS`：单个重任务可用 CPU 线程数，会写入 `OMP_NUM_THREADS`、`OPENBLAS_NUM_THREADS`、`MKL_NUM_THREADS`、`NUMEXPR_NUM_THREADS`。
- `QT_MEMORY_GUARD_ENABLED`、`QT_MEMORY_GUARD_PERCENT`、`QT_MEMORY_GUARD_AVAILABLE_MB`：内存保护和任务后缓存清理阈值。
- `QT_STRATEGY_DAILY_REFRESH_ENABLED`：scheduler 是否自动运行单日策略刷新，默认开启；只影响后台 scheduler，不影响手动接口。
- `QT_STRATEGY_DAILY_REFRESH_INTERVAL_SECONDS`：scheduler 中单日策略刷新的间隔；默认交易时段 1800 秒，非交易时段 3600 秒。
- `QT_STRATEGY_DAILY_REFRESH_INITIAL_DELAY_SECONDS`：scheduler 启动后首次单日策略刷新延迟，默认 45 秒，优先于首轮交易循环。
- `QT_STRATEGY_DAILY_REFRESH_MODE`：单日策略刷新模式，默认 `daily`，可显式设为 `intraday`。
- `QT_STRATEGY_DAILY_REFRESH_LOOKBACK_DAYS`：日常策略刷新用于恢复最近持仓和卖出动作的小回看窗口，默认按最大持仓天数加缓冲，最小 1 天，最大 120 天。
- `QT_STRATEGY_DAILY_REFRESH_PROCESS_ENABLED`：后台单日日常策略刷新是否默认走独立进程，默认开启。
- `QT_DAILY_STRATEGY_CPU_THREADS`：单日策略刷新独立进程可用 CPU 线程数，默认最多 2。
- `QT_STRATEGY_DAILY_REFRESH_WAIT_HEAVY_JOBS`：自动单日策略刷新是否避让正在运行的手动训练/复盘/回测任务，默认开启。
- `QT_TRADE_CYCLE_REQUIRE_STRATEGY_RUNTIME_READY`：交易循环是否要求 20 个目标策略日运行结果全部就绪后才汇总/通知，默认开启。
- `QT_TRADE_CYCLE_LEGACY_PAPER_ENABLED`：旧全局纸面账户回放兼容开关，默认关闭；正常日常运行应读取已落库的 `strategy_runtime` 结果。
- `QT_FRONT_STRATEGY_DAILY_CACHE_TTL_SECONDS`：前台跟随策略日买卖读取的短缓存 TTL，默认 10 秒。

默认情况下 `QT_RESEARCH_TASKS_MANUAL_ONLY=1`，调度器不会自动启动 `strategy_replay`、`strategy_evolution`、`model_backtest`、`quant_timeline`、`quant_backtest`、`fit_strategy`。即使 `STRATEGY_REPLAY_ENABLED` 或 `STRATEGY_EVOLUTION_ENABLED` 被单独打开，调度器也必须先通过 `JobSchedulerPolicy.strategy_replay_auto_enabled()` / `strategy_evolution_auto_enabled()`，只有手动研究层禁用后才允许自动跑研究任务。
手动启动重任务时还会先经过内存守卫：如果当前内存压力超过阈值，任务返回 `busy` 并携带 `memory_guard` 与 `resource_controls`，不会创建独立进程。通过准入的任务会立即返回 PID、资源预算和初始 ETA，后续状态查询读取同一份进度状态。
手动停止任务统一走 `/api/jobs/{job_name}/stop`。如果任务有独立进程，停止只终止该任务的进程树并把状态写为 stopped；如果是普通后台任务，状态写入 `stop_requested`，任务函数只能在检查点退出。停止响应和状态落盘都必须携带 `zone` / `stop_policy`，避免把研究优化层停止按钮和日常策略刷新混成同一类。

独立进程 worker 对通用研究任务也必须写入阶段进度：`model_backtest`、`quant_timeline`、`quant_backtest`、`fit_strategy`、`data_coverage` 和 `frontend_account_precompute` 至少写入 prepare / running / finalizing 三个检查点。更细粒度的策略复盘和单日策略刷新仍由各自模型循环写入逐模型进度。

## Frontend Strategy Catalog Boundary

- `backend/app/quant/frontend_strategy_models.py` owns the read-side strategy catalog shown to frontend users, including configured cache/runtime-summary wiring through `FrontendStrategyModelsService`.
- `backend/app/quant/strategy_model_lookup_service.py` owns shared strategy-model lookup before timeline/backtest/research work: stored model lookup, catalog fallback, ID normalization, and not-found signaling. HTTP-facing services map that domain miss to 404; `main.py` only wires the configured lookup service.
- `backend/app/quant/frontend_follow.py` owns frontend follow-context resolution and `FrontendProfileReadService`: request user resolution, profile payload reads, selected strategy context, and `/api/front/strategy_models` payload composition. Frontend snapshot/runtime/signal services wire directly to this read service instead of routing profile context through `main.py`.
- `backend/app/quant/frontend_account.py` owns the frontend follow-account read path and its read-service composition: user snapshot, runtime snapshot, short memory cache, account cache, runtime trade hydration, and pending-on-miss behavior. Frontend snapshot/runtime/precompute services wire directly to this read service instead of routing strategy-account reads through `main.py`.
- `backend/app/quant/frontend_runtime_read_service.py` owns frontend runtime read route composition and router positional contracts: read-only followed account payloads, pending account precompute attachment, and followed-strategy daily signal/news payloads. It must not trigger replay/backtest/training work from normal frontend reads.
- `backend/app/quant/frontend_account_precompute_service.py` owns the frontend follow-account precompute queue and runtime binding: queue files, stale lock recovery, async debounce, async worker dispatch, frontend snapshot precompute attachment, manual job payload assembly, drain-queue detection, and isolated/background/sync dispatch. `backend/app/main.py` wires the configured service directly instead of keeping queue/precompute wrappers.
- `backend/app/quant/frontend_follow_period_service.py` owns frontend follow-period change classification, configured follow-period persistence, and async follow-period recording when users change strategy or simulated cash. `main.py` wires the service directly instead of keeping record/queue wrappers.
- `backend/app/quant/frontend_profile_update_service.py` owns frontend profile update route orchestration: request user binding, strategy/cash update resolution, follow context rebuild, follow-period recording queue, account precompute queue, and timing trace response. `main.py` wires the configured service directly.
- `backend/app/quant/frontend_user_lifecycle_service.py` owns registration/admin-user side effects and admin-user route composition: follow-period recording, cache clearing after user mutations, admin list diagnostics/count enrichment, and account-precompute enqueueing when a user's follow profile is created or changed.
- `backend/app/quant/frontend_date_service.py` owns frontend read date normalization: capping requested `as_of` to available data, deriving replay start dates, and bounding user follow start dates to available market data. `main.py` wires the configured service directly instead of keeping date helper wrappers.
- `backend/app/quant/frontend_composition.py` owns construction of the frontend base partition: follow-period recording, profile reads/updates, strategy catalog reads, followed-account reads, admin frontend-user lifecycle, and account precompute runtime callbacks. `main.py` should depend on the composed services, not on individual frontend service constructors.
- `backend/app/quant/application_frontend_bootstrap.py` owns top-level frontend partition wiring for the app runtime: frontend base services, followed-account precompute runtime state, frontend read services, frontend static responses, and light dashboard exposure. It keeps frontend wiring outside the admin/runtime bootstrap path.
- `backend/app/quant/frontend_read_composition.py` owns construction of the frontend read partition: lightweight news, public/private snapshots, followed account reads, followed-strategy daily reads, recommendation/daily-plan cache reads, frontend static responses, and the light dashboard read model. It keeps frontend reads cache/pending-first and outside the research/backtest/training partition.
- `backend/app/quant/frontend_runtime_read_service.py` owns `/api/front/trading_account` and `/api/front/strategy_daily` read orchestration. Trading-account reads must stay cache/runtime-table first; user-supplied `force` or `defer=false` is ignored unless `QT_FRONT_ACCOUNT_SYNC_COMPUTE_ENABLED` is explicitly enabled.
- `backend/app/quant/frontend_news_read.py` owns lightweight frontend/admin news reads and market sentiment summarization; `main.py` wires this service directly instead of keeping news helper wrappers. Frontend light paths return a pending empty snapshot instead of falling back to heavy engine reads.
- `backend/app/quant/ai_monitoring_read_service.py` owns lightweight AI monitoring reads: usage summary, analysis records, failure feed, and their short status cache keys. It must not trigger AI analysis work.
- `backend/app/quant/frontend_payload_read_service.py` owns frontend recommendations/daily-plan read orchestration: cache keys, TTLs, pending payloads, optional precompute queueing on misses, deferred job response mapping, cached recommendations+plan pairing, simulated-cash affordability enrichment, and the `QT_FRONT_PAYLOAD_SYNC_COMPUTE_ENABLED` guard. `main.py` wires this service directly instead of keeping payload/precompute/pending wrappers. It must not synchronously run recommendations or daily-plan recomputation from the light frontend read path unless that guard is explicitly enabled.
- `backend/app/quant/frontend_signal_read_service.py` owns the `/api/front/recommendations` and `/api/front/daily_plan` route composition and router positional contracts: frontend follow context, date normalization, temporary strategy parameter scope, and delegation to `FrontendPayloadReadService` so normal frontend reads stay cache/pending-first.
- `backend/app/quant/frontend_static_response.py` owns frontend/admin static entry file responses and missing-file JSON fallbacks; `backend/app/routers/frontend_static.py` owns path routing for `/`, `/index.html`, and the configured admin entry. The application router registry keeps these static routes after all API routers.
- `backend/app/quant/light_dashboard_read.py` owns lightweight dashboard/overview reads from SQLite counts, existing model signal feeds, and already-loaded news payloads; it must not trigger recommendations/backtests/replay work.
- `backend/app/quant/memory_payload_cache.py` owns the in-process short-TTL payload cache used by lightweight frontend/admin/status reads; `backend/app/main.py` wires the configured cache service directly instead of keeping cache helper wrappers.
- `backend/app/quant/server_read_status.py` owns lightweight server read status helpers and payloads: latest news timestamp, SQLite data-date bounds, first/latest data date, git ref metadata, `/api/status` composition, frontend light job status caching, and admin job status queue attachments. `main.py` wires this service directly instead of keeping date/news/git read wrappers.
- `backend/app/quant/server_status_composition.py` owns construction of the server status partition: `ServerReadStatusService` first for shared data/news/git bounds, then `RuntimeStatusPayloadService` after frontend account precompute exists so status payloads can attach queue/async state without making frontend reads depend on admin runtime services.
- `backend/app/quant/core_system_service.py` owns core system payload composition: version/debug payloads, debug route summaries, auth login/register delegation, frontend-register side effects, and runtime config update logging.
- `backend/app/quant/core_app_composition.py` owns construction of the process-level app partition: FastAPI shell/auth-audit middleware wiring, data import service construction, core system payload service construction, frontend-register lifecycle wiring, and data-import cache refresh boundaries.
- `backend/app/quant/application_core_bootstrap.py` owns top-level core process wiring for the app runtime: runtime path/version config, shared in-process cache, route runtime defaults, server/runtime status services, and the FastAPI process shell/core system service. It is the only app bootstrap module that creates the FastAPI app.
- `backend/app/quant/application_bootstrap.py` owns top-level application assembly: runtime config, cache service, status/frontend/core/research/operations/admin partition construction, route registration, and the compatibility exports consumed by `app.main`. Its `build_application_runtime` function should stay as a short ordered sequence of partition builders rather than accumulating inline service wiring.
- `backend/app/quant/admin_snapshot_service.py` owns admin snapshot aggregation: light snapshot cache, admin model signals, runtime overview, diagnostics, and fallback dashboard payloads. The light path should compose already-available read models and must not trigger replay/backtest/training work.
- `backend/app/quant/admin_data_maintenance_service.py` owns admin data-maintenance route composition: backup/export response assembly, import status/upload error mapping, sample-state clearing, database table payload delegation, and cache status/clear delegation.
- `backend/app/quant/admin_data_cache_service.py` owns admin database/cache reads: database overview short cache, table row access, runtime cache status, in-process payload cache status, and cache clear scope handling.
- `backend/app/quant/admin_data_composition.py` owns construction of the admin data-maintenance partition, keeping the cache service and maintenance service wired together outside `main.py`.
- `backend/app/quant/admin_runtime_composition.py` owns construction of the admin runtime partition: manual job launch adapters, strategy runtime read models, admin snapshot aggregation, startup orchestration, and system control. It keeps backend/admin reads and explicit control entries separate from frontend reads and research/backtest execution services.
- `backend/app/quant/application_admin_bootstrap.py` owns top-level admin partition wiring for the app runtime: admin runtime services, admin data maintenance/cache services, and admin access/security orchestration. It depends on already-built frontend read/precompute services instead of building frontend services itself.
- `backend/app/quant/admin_access_service.py` owns admin access/security orchestration: access log reads, suspicious access summaries, single IP block/unblock payloads, and bulk blocking from suspicious summaries. `backend/app/routers/admin_access.py` maps validation failures to HTTP errors so `main.py` only wires the service.
- `backend/app/quant/strategy_runtime_account.py` owns runtime/follow account helpers: cache keys, runtime table date filters, trade scaling, fast snapshot payloads, and TTL freshness checks.
- `backend/app/quant/strategy_runtime_admin.py` owns admin strategy runtime reads and their read-service composition: model signal enrichment, runtime matrix/overview, runtime-backed admin trading accounts, and replay-style performance summaries. `backend/app/routers/admin_strategy_runtime.py` maps missing-model domain errors to 404 responses. It must read saved runtime rows instead of starting replay/backtest work.
- `backend/app/quant/strategy_runtime_repository.py` owns daily runtime result persistence and read models: signals, trades, positions, settlements, snapshots, model signal feeds, runtime summaries, and account snapshot cache.
- `backend/app/quant/strategy_follow_repository.py` owns user-follow snapshots, positions, trades, follow periods, and follow diagnostics persistence.
- `backend/app/quant/strategy_evolution_schema.py` owns the SQLite schema for strategy optimization, runtime result tables, and user-follow tables.
- `backend/app/quant/strategy_evolution_repository.py` owns persistence for strategy evolution runs, candidate traces, model catalog rows, and model replay records.
- `backend/app/quant/accounting.py` owns pure account settlement helpers shared by frontend follow accounts, strategy replay, and model backtests.
- `backend/app/quant/performance.py` owns pure performance metrics shared by walk-forward replay, intraday replay, and backtests.
- `backend/app/quant/replay_context.py` owns replay-only correlation state, empty replay payloads, and final replay metric aggregation used by daily and intraday walk-forward runs.
- `backend/app/quant/replay_execution.py` owns replay execution helpers for cash-to-lot sizing, position snapshots, and daily valuation points shared by daily and intraday walk-forward runs.
- `backend/app/quant/replay_signals.py` owns replay signal planning: news-event candidate scoring shape, buy/watch/avoid actions, next-session orders, and intraday signal orders.
- `backend/app/quant/backtest_research.py` owns pure research backtest scoring, event-outcome summaries, score buckets, and data coverage diagnostics.
- `backend/app/quant/event_classifier.py` owns keyword-based event classification, industry tagging, sentiment scoring, and impact scoring for raw news text.
- `backend/app/quant/event_models.py` owns shared event data shapes such as `NewsEvent`; event records should be modeled there before they are scored, replayed, or exposed to frontends.
- `backend/app/quant/event_repository.py` owns read-side event inputs: news history merge/dedupe, AI analysis records, LHB records, source mtimes, and lightweight LHB summaries. `engine.py` should call this repository instead of embedding file/SQLite/CSV read rules.
- `backend/app/quant/market_read_repository.py` owns read-side market inputs and derived market reads: daily K-line merge/dedupe, intraday CSV/SQLite merge, available intraday date discovery, first data date, latest price, forward return windows, and trading date sets. `engine.py` keeps cache orchestration only.
- `backend/app/quant/market_data_preparation.py` owns event-driven market data preparation, including selecting impacted stocks and expanding the fetch window needed for forward-return replay.
- `backend/app/quant/engine_runtime_cache.py` owns the in-memory runtime caches used by `engine.py`: K-line rows, row maps, intraday bars, future returns, correlations, factors, and LHB snapshots. New cache limits or trimming behavior should be added there, not inside strategy scoring or replay code.
- `backend/app/quant/correlation_analysis.py` owns historical event-return correlation aggregation used by scoring and diagnostics. `engine.py` provides event streams, forward returns, and cache lookup only.
- `backend/app/quant/replay_context.py` owns replay correlation state, final replay metrics, empty replay payload shape, and historical outcome selection for replay context. Main replay loops should call it instead of rebuilding research samples inline.
- `backend/app/quant/news_feed_payload.py` owns heavy/fallback news feed payload assembly: source/keyword/code filters, date fallback, available dates, and event attachment. `engine.py` supplies data sources only.
- `backend/app/quant/stock_universe.py` owns stock-name/code universe loading, SQLite name backfill, A-share tradability filtering, and news text mention extraction.
- `backend/app/quant/quant_paths.py` owns configured data directory and canonical runtime file/cache paths. Modules that only need paths should import it directly instead of importing `engine.py`.
- `backend/app/quant/engine_utils.py` owns generic numeric, env, time, sample-marker, JSON IO, and compact hash helpers used by `engine.py`; `engine.py` should not grow new shared utility code.
- `backend/app/quant/strategy_defaults.py` owns default AI model, broker fee defaults, and baseline strategy parameters. Runtime, security, AI analysis, and accounting code should import these defaults directly instead of importing `engine.py` for constants.
- `backend/app/quant/strategy_state.py` owns strategy state loading, sample-state cleanup, parameter normalization, source metadata, and update/reset mutations. `engine.py` should orchestrate persistence only.
- `backend/app/quant/strategy_evolution_core.py` owns pure strategy optimization mechanics: gene bounds, mutation, population rollout, candidate records, model payload building, and objective calculation.
- It builds the active baseline model, capital preset models, catalog inclusion flag, and optional runtime summary labels.
- Frontend light paths call it with `include_catalog=false` so they do not load model records or runtime summaries.
- Full catalog reads are reserved for explicit strategy selection/admin views and stay separate from research/backtest execution.

## API Router Boundaries

- `backend/app/routers/core_routes.py` owns core system route registration: version, auth status/setup/login/register, debug payloads, runtime config, and `/api/status`.
- `backend/app/routers/quant_strategy.py` owns strategy research API routing: `fit_strategy`, strategy evolution status/control, model catalog, model backtest, model apply, and evolution start. These endpoints remain admin/research surfaces and should keep manual/deferred/process controls explicit.
- `backend/app/routers/quant_timeline.py` owns strategy timeline and intraday timeline API routing. These endpoints must preserve the manual-trigger guard and must not become implicit frontend refresh work.
- `backend/app/routers/quant_backtest.py` owns the generic quant backtest API. It supports both GET and POST for compatibility, but still goes through the manual/deferred/process guard.
- `backend/app/routers/data_collection.py` owns public data collection and diagnostics routing: market sync, K-line fill, LHB sync, BIYING status, and data coverage.
- `backend/app/routers/admin_data_transfer.py` owns server data migration routing: backup, safe export, async import status/upload, and sample-state cleanup.
- `backend/app/routers/system_control.py` owns restart and notification control routing.
- `backend/app/routers/ai_monitoring.py` owns AI usage, record, and failure observability routing.
- `backend/app/routers/frontend_static.py` owns frontend and configured admin static entry routing.
- `backend/app/routers/frontend_routes.py` owns frontend route registration as one read-side partition: profile/follow selection, runtime snapshot/account/daily reads, and recommendation/plan reads.
- `backend/app/routers/app_routes.py` owns top-level application route registration order: core, frontend reads, admin, quant/research, operations, then frontend/admin static entries last so static routes cannot intercept admin, data, or research paths.
- `backend/app/routers/admin_routes.py` owns backend/admin route registration: admin snapshots, strategy runtime reads, live WebSocket, scheduler/job controls, manual job starts, cache/data maintenance, access audit controls, frontend user administration, and system control.
- `backend/app/routers/quant_research_routes.py` owns quant/research route registration: basic quant reads, explicit strategy optimization controls, model backtest, timeline replay, and generic backtest. These remain admin/research surfaces and must keep manual/deferred/process defaults explicit.
- `backend/app/routers/operations_routes.py` owns operational data and AI observability route registration: data coverage, K-line/LHB/intraday sync, BIYING status, AI usage records, and AI failures.
- `backend/app/routers/admin_live.py` owns the admin live WebSocket routing and delta payload loop.
- `backend/app/middleware/auth_audit.py` owns API scope enforcement, optional frontend token hydration, blocked-IP checks, and access-audit recording.
- `backend/app/app_shell.py` owns the FastAPI process shell: app creation, CORS/GZip middleware, static asset mounting, and auth/audit middleware wiring. Domain services should not register these concerns directly.
- `backend/app/quant/app_config.py` owns application paths, version loading, and small environment parsing helpers so `main.py` does not grow runtime configuration logic.
- `backend/app/quant/route_runtime_defaults.py` owns route-level runtime defaults: frontend light-read defer defaults, research/backtest defer/process defaults, daily job process defaults, data-collection process defaults, and the startup guard that keeps strategy replay off unless explicitly requested.
- `backend/app/main.py` is only the uvicorn entrypoint and compatibility export adapter; new routes should be added to a partition router and then mounted through `backend/app/routers/app_routes.py`, not by adding direct `@app.*` decorators.
