# 策略运行架构需求整理

## 用户确认的产品目标

系统不是只运行一个“系统策略”。正确目标是：

- 服务器持续收集和整理新闻、行情、龙虎榜、AI 分析等公共数据。
- 服务器基于公共数据训练和回测出多个基础策略模型。
- 每个策略模型有独立基础参数、收益、回撤、胜率、成交、交割单、资金流水和训练淘汰轨迹。
- 用户注册后设置模拟金额，并选择一个策略跟随。
- 用户账户从注册时间或切换策略时间开始运行，不继承策略过去的历史持仓。
- 用户看到的是自己跟随策略产生的持仓、成交、交割单和计划。
- 不同用户可以选择不同策略和资金规模，共用同一份新闻行情数据，但账户状态相互独立。

## 需要废弃或降级的旧概念

- “系统运行策略”不应作为产品主概念。
- 后台“持仓成交”不再是统一基准账户，应该按策略查看不同策略的持仓、成交、交割单和资金流水。
- 系统默认基础参数只是人工调参、诊断和生成新策略的模板，不是一个可跟随账户。
- 策略库模型不应只停留在回测展示；它必须能被用户选择并驱动用户账户。

## 目标架构

```text
公共数据层
  新闻 / AI分析 / 日K / 分钟K / 龙虎榜 / 因子

策略工厂
  进化训练 / 参数拟合 / 回测 / 淘汰记录 / 模型入库

策略运行层
  每个策略独立保存基础参数，并生成每日信号、目标仓位、成交和账户快照

用户跟随层
  用户资金 + 跟随策略 + 跟随开始日期 => 用户账户视图

展示层
  前台：用户自己的账户
  后台：策略库、训练轨迹、用户跟随、数据质量、任务进度
```

## 数据库方向

现有 `strategy_models`、`strategy_model_records`、`strategy_candidates` 是基础。下一步应补齐：

- `strategy_runtime_snapshots`：每个策略每日账户快照，v0.2.24 已完成第一版；同时保留前台账户短缓存。
- `strategy_daily_signals`：每个策略每日信号，v0.2.23 已完成第一版。
- `strategy_runtime_positions`：每个策略每日持仓，v0.2.23 已完成第一版。
- `strategy_runtime_trades`：每个策略运行成交，v0.2.23 已完成第一版。
- `strategy_runtime_settlements`：每个策略每日清算结果，v0.2.24 已完成第一版。
- `user_follow_periods`：用户注册、设置资金或切换策略形成的跟随周期，v0.2.33 已完成第一版。
- `user_follow_snapshots`：用户跟随账户快照，v0.2.32 已完成第一版。
- `user_follow_positions`：用户跟随持仓快照，v0.2.32 已完成第一版。
- `user_follow_trades`：用户跟随成交和交割单，v0.2.32 已完成第一版。

短期内用户视图已可从 `user_follow_snapshots` 命中；没有用户快照时继续按 `follow_start_date` 从策略运行表或回放派生，并写回用户跟随表。真正为每个用户从注册日开始重建完整独立策略运行会显著消耗服务器资源，暂时只列入计划，等明确要求后再开发。

## 性能原则

- 首屏只返回轻量摘要。
- 日计划、推荐、覆盖率、回测这类慢计算要缓存或后台任务化；v0.2.129 起推荐和日计划缓存未命中默认只返回 pending/disabled，不自动排队批量预计算，v0.2.66 起覆盖率冷启动默认转入后台任务，不再同步阻塞前台请求。
- 任务状态接口不能返回巨大历史 payload。
- 前台账户优先读用户跟随快照；没有用户快照时才读策略运行表或增量回放。
- 多策略运行共享事件和行情缓存，不能每个策略重复读全量数据。
- 训练、策略复盘和模型回测是维护动作，不属于日常自动链路；v0.2.49 起默认只手动触发，v0.2.135 起 `QT_RESEARCH_TASKS_MANUAL_ONLY=true` 会拦住调度器误触发策略复盘/进化，策略库查看交割单默认读已保存记录，日常运行使用已训练模型做新闻分析、买卖信号和账户更新。

## 当前已落地的调整

- 前台用户 profile 增加 `follow_started_at` 和 `follow_start_date`。
- 新用户注册时跟随开始时间等于注册时间。
- 用户切换策略时重置跟随开始时间。
- 前台交易账户按跟随开始日期运行，不继承旧历史持仓。
- 前台交易账户增加 `strategy_runtime_snapshots` SQLite 缓存，同一策略、参数、资金、日期和返回条数在缓存有效期内不重复回放。
- 后台策略复盘任务会按资金档预设和策略库模型批量运行，并写入 `strategy_daily_signals`、`strategy_runtime_positions`、`strategy_runtime_trades`、`strategy_runtime_snapshots`、`strategy_runtime_settlements`。
- 资金档策略名称已调整为 `小资金策略`、`短线稳健策略`、`均衡轮动策略`、`趋势多仓策略`，资金范围放在标签和说明里；并从运行表汇总收益、回撤、胜率、交易数，复盘数据未生成时明确显示等待复盘，而不是误导性全 0。
- 前台交易账户会优先读取 `strategy_runtime_trades`，按用户 `follow_start_date` 和模拟资金派生账户；没有运行表数据时回退短缓存、模型记录或即时回放。
- v0.2.32 起，前台交易账户会先读 `user_follow_snapshots`；未命中时再从策略运行表、短缓存、模型记录或即时回放派生，并写入 `user_follow_positions`、`user_follow_trades`。
- v0.2.33 起，用户注册、设置资金或切换策略会记录到 `user_follow_periods`；模拟资金变化也会开启新跟随周期。
- v0.2.33 起，后台用户管理页会展示用户当前账户快照、跟随周期、持仓和最近成交来源。
- 缓存清理只清理 `strategy_runtime_snapshots` 里的短缓存行，不删除 `daily_runtime:*` 正式每日快照。
- 前台推荐和日计划增加 `frontend_payload_cache` SQLite 短缓存，减少登录后连续拉取慢接口造成的等待和服务器压力。
- v0.2.48 起，新增 `frontend_payload_precompute` 后台任务和独立进程入口，前台推荐/日计划优先读取 `frontend_payload_cache`；v0.2.129 起未命中时默认返回 pending/disabled，不自动排队生成缓存，除非显式开启自动补算。
- v0.2.52 起，前台切换策略只更新 profile 和跟随周期；账户同步改为按需读取缓存，不再强制跳过 `user_follow_snapshots`、`strategy_runtime_*` 和 SQLite 账户缓存。
- v0.2.53 起，前台账户缓存未命中时默认返回 pending，不再同步即时回放；只有显式 `force=true` 或关闭 `QT_FRONT_ACCOUNT_DEFER_MISSES` 时才回到同步重算。
- v0.2.54 起，前台会把 pending 账户展示为“账户运行结果待生成”，不会误导用户认为策略真实空仓。
- v0.2.55 起，前台账户接口默认不再读取完整策略模型交割单做同步兜底；如需本地排查，可临时开启 `QT_FRONT_ACCOUNT_MODEL_RECORDS_FALLBACK=true` 或使用 `force=true`。
- v0.2.56 起，后台提供 `frontend_account_precompute` 手动任务，用已有用户快照、策略运行表或账户缓存为用户预热账户快照；生产默认 `force=false`，不应在该任务里触发重型回放。
- v0.2.57 起，`frontend_account_precompute` 支持独立进程执行，生产默认 `QT_FRONT_ACCOUNT_PRECOMPUTE_PROCESS_ENABLED=true`，避免批量账户预热占用 API 进程。
- v0.2.58 起，用户注册、切换策略或调整模拟资金后会按单用户排队账户预热；这只派生已有运行结果，不触发训练、回测或即时回放。
- v0.2.59 起，自动账户预热响应区分 `queued` 和 `worker_started`；v0.2.60 后 `queued=true` 表示用户已进入待处理队列。
- v0.2.60 起，自动账户预热先进入运行数据目录下的待处理队列，`frontend_account_precompute` 任务批量消费队列；任务运行期间新增的用户会保留在队列中等待同一轮或下一轮消费。
- v0.2.61 起，账户预热待处理队列使用跨进程锁保护，API 进程和独立 worker 不会并发覆盖队列文件；旧锁会按超时自动恢复。
- v0.2.62 起，前台账户请求会自愈检查账户预热队列残留，必要时补启动独立 worker 消费队列。
- v0.2.63 起，前台注册、切换策略或调整模拟资金时，profile 保存请求默认只写入账户预热队列，不再同步启动或巡检 worker；队列由账户读取自愈或后台手动预热任务消费。
- v0.2.64 起，后台任务状态暴露账户预热队列摘要，包括排队数量、排队原因分布、最早/最新排队时间和队列锁是否陈旧，方便定位队列积压和锁残留。
- v0.2.65 起，后台手动账户预热会优先消费已有队列项；队列为空时才按普通用户批量预热，避免手动排查时绕过真正积压的用户。
- v0.2.66 起，前台 profile 保存不再清空全局内存缓存，前台快照缓存键加入跟随开始时间；切换策略只改变当前用户的新缓存键，不会让后台覆盖率、策略列表等全局缓存一起冷启动。
- v0.2.66 起，`/api/data/coverage` 缓存未命中时默认返回 pending 并启动 `data_coverage` 后台任务；后台全量快照同样走缓存/任务路径，避免数据覆盖诊断阻塞 API。
- v0.2.67 起，`/api/quant/model/backtest?recompute=true` 默认只启动 `model_backtest` 后台任务并返回 pending，计算完成后读短缓存；只有显式 `defer=false` 才同步重算。
- v0.2.68 起，`/api/quant/timeline` 和 `/api/quant/intraday_timeline` 默认只启动 `quant_timeline` 后台任务并返回 pending，计算完成后读短缓存；只有显式 `defer=false` 才同步重算。
- v0.2.69 起，前台切换策略的 profile 保存默认不返回完整策略目录；资金档策略使用轻量目录定位当前策略，保存后只进入账户预热队列，不自动请求账户接口。
- v0.2.70 起，`/api/quant/backtest` 默认只启动 `quant_backtest` 独立进程并返回 pending，计算完成后读 SQLite 短缓存；只有显式 `defer=false` 才同步等待。
- v0.2.71 起，前台轻量 profile 保存遇到策略库模型时优先按模型 ID 单条读取策略参数，不再回退加载完整策略目录；切换进化策略也应保持快速返回。
- v0.2.72 起，`/api/quant/fit_strategy` 默认只启动 `fit_strategy` 独立进程并返回 pending；参数拟合和应用属于手动维护动作，不再占用 API 请求线程同步等待。
- v0.2.73 起，后台“同步分时行情”和 `/api/data/biying/sync_intraday` 默认只启动 `market_sync` 独立进程；大批量分时补数不再占用 API 请求线程同步等待。
- v0.2.74 起，后台“补齐缺失日K”和“拉取龙虎榜”默认只启动 `kline_fill`、`lhb_sync` 独立进程；大批量补数不再占用 API 请求线程同步等待。
- v0.2.75 起，后台“运行模拟交易”、`/api/jobs/daily/run` 和自动调度交易循环默认只启动 `trade_cycle` 独立进程；日常买卖信号更新不再占用 API 请求线程同步等待。
- v0.2.76 起，后台“AI 分析新闻”和自动调度 AI 分析默认只启动 `ai_analysis` 独立进程；新闻结构化和入库不再占用 API 请求线程同步等待。
- v0.2.77 起，`/api/data/coverage` 数据覆盖率诊断改为 SQLite 短缓存，缓存未命中时默认只启动 `data_coverage` 独立进程；覆盖诊断不再占用 API 请求线程同步等待。
- v0.2.78 起，后台“抓取新闻”和自动调度新闻抓取默认只启动 `news_fetch` 独立进程；外部新闻源访问和入库不再占用 API 请求线程同步等待。
- v0.2.79 起，后台“系统启动”默认只启动 `system_startup` 独立进程；新闻、AI、日K、龙虎榜、分时和交易循环仍在子进程内顺序执行，策略复盘、训练和回测仍然只手动触发。
- v0.2.80 起，前台保存 profile 的轻量策略模型上下文不再读取资金档运行摘要；资金档运行摘要只在策略列表需要展示时加载。
- v0.2.81 起，前台保存 profile 后不再在概览页自动补拉推荐和日计划；只有当前在买入页时才刷新买入相关数据，减少切换策略后的连带请求。
- v0.2.82 起，前台轻量快照默认不加载完整策略目录；只有进入策略页时才通过 `/api/front/strategy_models` 拉取完整策略库，首屏只保留当前策略和资金档基础信息。
- v0.2.83 起，`/api/quant/backtest` 和 `/api/quant/model/backtest?recompute=true` 必须显式 `manual=true` 才会在缓存未命中时启动重计算；否则返回 `manual_required`，保证回测只来自后台按钮或明确 API 调用。
- v0.2.84 起，模型维护类独立进程共用 `QT_HEAVY_JOB_MAX_CONCURRENT` 并发闸门，默认只允许一个训练/复盘/回测/拟合重任务运行；日常新闻、AI、行情和交易循环不受该闸门影响。
- v0.2.85 起，`model_backtest` 和 `quant_timeline` 默认使用独立 Python 子进程，避免单模型交割单重算或时间线缓存生成占用 API 进程线程。
- v0.2.86 起，后台会展示 `heavy_process_slots`，包括重任务槽位占用、运行任务、PID 和进度摘要；当任务返回 `busy` 时可直接从页面判断原因。
- v0.2.87 起，`model_backtest` 和 `quant_timeline` 的结果缓存写入 SQLite `frontend_payload_cache`，确保独立子进程和 API 父进程共享同一份短缓存。
- v0.2.88 起，前台保存 profile 后的 `user_follow_periods` 记录默认异步执行；切换策略的热路径只保存用户资料和账户预热队列，不等待 SQLite 跟随周期写入。重任务投递失败或并发满时，前端接口按 `busy`/`paused`/`error` 返回真实状态。
- v0.2.89 起，切换策略热路径中的账户预热入队也默认异步执行，受 `QT_FRONT_ACCOUNT_PRECOMPUTE_QUEUE_ASYNC_ON_PROFILE=true` 控制；即使账户预热队列文件锁被 worker 占用，profile 保存也能先返回。
- v0.2.90 起，`/api/front/trading_account` 遇到 `frontend_account_deferred` 时会异步补当前用户入队并启动预热 worker，避免 profile 异步入队失败后用户账户长期停留 pending。
- v0.2.91 起，前台账户 pending 文案会根据 `account_precompute` 状态展示是否已排队、正在写队列或正在启动 worker；用户不再只看到笼统的“待生成”。
- v0.2.92 起，异步账户预热入队受 `QT_FRONT_ACCOUNT_PRECOMPUTE_ASYNC_DEBOUNCE_SECONDS` 去重保护，默认 5 秒内相同用户/原因/日期不重复创建入队线程。
- v0.2.93 起，`/api/front/snapshot` 与 `/api/front/trading_account` 共用 pending 账户自愈逻辑；首屏快照带 pending 账户时也会异步补队列并启动预热 worker。
- v0.2.94 起，前台会把 `account_precompute.deduped=true` 展示为“已有相同预热请求”，说明系统正在去重保护而不是没有触发预热。
- v0.2.95 起，`/api/jobs/status` 返回 `frontend_account_precompute_async`，只包含去重窗口、待保护数量和原因/模式计数；后台运维页展示该摘要，不暴露用户名。
- v0.2.96 起，前台 profile 保存响应包含 `profile_update_trace` 和 `profile_update_slow_stage`，只记录阶段名和毫秒耗时，用于定位切换策略慢点。
- v0.2.97 起，前台 profile 保存使用轻量策略上下文；当用户保存了不存在的策略 ID 时，不再为了兜底加载完整策略目录，而是按资金规模推荐资金档策略。
- v0.2.98 起，前台 profile 保存后的上下文直接复用保存结果，不再二次调用 `frontend_user_profile` 读取用户资料。
- v0.2.105 起，策略运行快照的 daily runtime 过滤使用 `source >= 'daily_runtime' AND source < 'daily_runtimf'`，替代 `LIKE` 前缀匹配，并新增 `idx_strategy_runtime_source` 复合索引。
- v0.2.104 起，`user_follow_periods` 当前周期读取和关闭旧周期时使用 `(ended_at IS NULL OR ended_at = '')`，并新增 `(username, ended_at, started_at, created_at)` 复合索引；旧数据里的 NULL 结束时间仍会被识别为活跃周期。
- v0.2.103 起，`model_signal_feed` 最新日期定位改为 `ORDER BY date DESC LIMIT 1` 和 `WHERE date <= ? ORDER BY date DESC LIMIT 1`，配合 `strategy_daily_signals(date, ...)` 索引读取，不再先做全表 `COUNT(*)`。
- v0.2.102 起，`StrategyEvolution._connect_db()` 会在同一进程内按数据库路径和 schema 版本缓存 schema 初始化；连接仍设置轻量 `PRAGMA synchronous=NORMAL`，但不再每次重复跑完整 DDL。
- v0.2.101 起，策略运行表和用户跟随表补充按实际查询路径设计的复合索引：运行结果按 `model_id/params_hash/generated_at/date` 读取，用户快照明细按 `snapshot_id` 读取或替换，策略信号矩阵按 `date/model_id/generated_at` 读取。
- v0.2.100 起，前台 profile 的策略目录归一化和保存前解析迁入 `app.quant.front_profile`，接口层只负责装配 loader 和返回响应，便于后续拆 router。
- v0.2.99 起，前台 profile 保存会先把过期、缺失或 `active` 策略 ID 解析为推荐资金档策略，再执行唯一一次 profile 写入；保存前已查到的可复用模型会传给上下文构建，避免同请求重复模型查询。
- v0.2.25 起，后台页面手动触发慢任务时默认使用 `background=true`，接口立即返回任务状态，实际计算继续写入任务日志和进度。
- v0.2.28 起，策略复盘支持按 `QT_STRATEGY_REPLAY_BATCH_DAYS` 分批推进，默认每批 15 天，并记录 `strategy_replay_cursor`。
- v0.2.29 起，数据包导入的 SQLite 合并改为流式临时文件方案，避免上传包内 200MB 级数据库被一次性读入内存。
- v0.2.30 起，手动策略复盘和策略进化默认由独立 Python 子进程执行，避免重任务占用 API 进程；状态仍通过现有任务状态和日志查看。
- v0.2.31 起，独立进程任务会在状态接口读取时自动校验进程是否仍存活；异常退出但未写回结果的任务会被标记失败。
- v0.2.49 起，自动调度默认不再运行策略复盘，系统启动流程默认跳过策略复盘；需要刷新策略运行结果时在后台手动触发。
- 后台数据库管理页增加缓存状态和清理入口，可以看到推荐/日计划缓存、账户回放缓存、过期缓存数量，并清理过期或全部缓存。
- 后台文案把“系统运行策略”降级为“系统默认基础参数”，并把“持仓成交”改为按策略查看。

## 后续开发顺序

1. 后台增加“策略运行矩阵”，能看到每个策略是否运行、进度、最新信号、收益。
2. 慢接口全部改为后台任务 + 进度查询 + 缓存读取。
3. 用户管理页补单用户周期详情和账户追溯。
## v0.2.106 运行隔离补充

- 日常生产链路应使用已训练好的策略模型，自动任务只负责新闻抓取、AI 分析、行情同步、模拟交易和必要的前台缓存预热。
- 策略复盘、训练、模型回测、通用回测、参数拟合以及前台批量预计算都按维护任务处理，受 `QT_HEAVY_JOB_MAX_CONCURRENT` 控制，不应在用户切换策略时同步运行。
- 访问日志默认异步写入；前台 profile 保存的真实性能仍以 `profile_update_trace` 为准，代理层 502/504/524 通常说明源站进程繁忙、崩溃或被长任务拖住。

## v0.2.107 访问审计写放大控制

- 访问审计异步队列新增批量落盘，避免高频前台请求让后台线程持续单条重写日志 JSON。
- 该优化只降低审计日志 I/O，对策略运行表、用户跟随表和账户快照的生成逻辑没有影响。

## v0.2.108 用户资料读取缓存

- 前台 profile 热路径复用 `auth.json` 进程内缓存，减少切换策略、账户读取和登录态校验中的重复认证文件读取。
- 用户跟随周期、账户快照和策略运行表仍以 SQLite 结果为准；auth 缓存只保存用户认证/profile 源文件的当前副本。

## v0.2.109 前台请求鉴权边界

- frontend scope 请求不再触发完整后台认证状态汇总；切换策略、账户读取和前台快照只做必要的 token/debug 校验。
- admin scope 仍保留 setup_required 检查，避免影响后台初始化和管理入口安全。

## v0.2.110 前台快照边界

- 前台快照不再读取或返回后台运行日志；日志仍只通过后台管理接口查看。
- 前台快照里的任务状态只保留轻量摘要并做短缓存，用户切换策略后的首屏不应为了展示运维日志而增加文件 I/O。
- 前台状态摘要不再暴露服务器数据目录；用户侧只需要看到跟随策略、账户、持仓、成交、新闻和必要运行状态。

## v0.2.111 新闻读取热路径

- 前台状态摘要里的最新新闻时间、前台新闻列表和结构化事件列表都应走 SQLite 索引读取，不应在大新闻表上触发 `COALESCE(timestamp, 0)` 临时排序。
- 新闻热路径索引属于公共数据读取优化，不改变任何策略参数、信号、成交、持仓或用户账户结果。

## v0.2.112 状态摘要短缓存

- 前台状态摘要里的最新新闻时间允许使用秒级进程内缓存，默认 5 秒，避免切换策略后的快照刷新反复查询同一条 SQLite 最新新闻时间。
- 该缓存只影响页面显示的新鲜度提示，不参与买卖评分、策略复盘、训练、回测、成交、持仓或用户跟随账户派生。

## v0.2.113 前台日期边界读取

- 前台 `as_of`、回放窗口和跟随开始日裁剪优先使用 SQLite 数据日期边界短缓存，不应为了一个最新日期调用完整事件集合加载。
- 日期边界缓存只决定页面请求默认日期和裁剪范围；账户结果仍来自 `user_follow_*`、`strategy_runtime_*`、短缓存或受控后台任务。

## v0.2.114 轻量快照账户写入边界

- 轻量前台快照允许读取 `user_follow_*`、`strategy_runtime_*` 或账户缓存结果，但不应在首屏请求里同步写入派生账户数据。
- 用户账户派生落库属于账户预热或明确账户读取职责，避免普通快照请求和后台复盘/导入争抢 SQLite 写锁。

## v0.2.115 前台账户读取写入边界

- 前台账户 GET 默认只读；读到可展示账户结果时不代表必须在同一个请求里完成用户跟随账户落库。
- 缺失的用户跟随账户派生应通过 `frontend_account_precompute` 队列补齐，确保用户请求不和策略复盘、导入、预热 worker 同步争抢写锁。

## v0.2.116 后台策略运行矩阵

- 后台新增只读接口 `/api/admin/strategy_runtime/matrix`，按资金档策略和策略库模型汇总运行结果是否存在、运行日期范围、成交数、持仓数、收益、回撤和最新信号。
- 该接口只读取策略目录、`strategy_runtime_*` 汇总和 `strategy_daily_signals`，不触发训练、复盘、回测、账户预热或即时回放。
- 前台切换策略后账户仍然 pending 或变慢时，先用矩阵确认目标策略是否已经有每日运行结果；如果 `runtime_status=missing`，应手动运行策略复盘或导入运行结果，而不是让用户请求同步重算。

## v0.2.117 策略运行矩阵后台展示

- 后台“策略”页展示 `/api/admin/strategy_runtime/matrix` 的只读结果，策略库、资金档、信号、成交、持仓和收益状态可以在同一页对照。
- 矩阵页面不承担生成职责；缺失运行结果仍通过后台手动策略复盘、运行结果导入或前台账户预热补齐。
- 这一步把诊断入口从命令行接口迁入后台页面，降低线上排查“切换策略慢”和账户 pending 的操作成本。

## v0.2.118 策略运行矩阵服务边界

- 策略运行矩阵的 payload 组装迁入 `app.quant.strategy_runtime_matrix`，保持“读取依赖”和“组装展示结果”分离。
- 服务层只处理已传入的策略目录、运行汇总和信号 feed，不访问数据库、不触发后台任务、不写入任何运行表。
- 后续拆 router 时，后台接口可以继续复用该服务，避免在 `main.py` 或新 router 里重复实现矩阵合并逻辑。

## v0.2.119 后台策略运行 router 边界

- `/api/admin/strategy_runtime/matrix` 已通过 `app.routers.admin_strategy_runtime` 注册，路由层只负责 HTTP 参数声明和调用 payload 回调。
- 当前只迁移矩阵 endpoint；其它策略运行账户、回放和策略库接口仍在 `main.py`，后续应按同样模式逐步迁移。
- 路由拆分不改变鉴权、缓存、运行表读取或后台任务触发边界。

## v0.2.120 后台策略运行 router 扩展

- `/api/admin/trading_account` 和 `/api/admin/strategy_runtime/replay` 已迁入 `app.routers.admin_strategy_runtime` 注册。
- 这两条接口仍调用原有 payload 回调读取 `strategy_runtime_*` 和派生后台展示账户，不触发训练、复盘、回测或账户预热。
- 后台“持仓成交”和“复盘分析”页面使用的 URL、参数和返回结构保持不变。

## v0.2.121 前台轻量账户读取边界

- 用户注册和 profile 保存只允许把当前用户写入账户预热队列，不在保存请求里同步启动预热 worker；该边界在代码调用层显式传入 `start_worker=false`，避免旧 `.env` 配置把切换策略拖回进程启动路径。
- `/api/front/snapshot?light=true` 读取账户时，如果 `strategy_runtime_snapshots` 已有正式 `daily_runtime` 每日快照，会直接返回快照里的账户摘要、持仓和当日成交，并标记 `runtime_snapshot_fast_path=true`。
- 轻量快照不负责加载完整运行成交历史或重建交割单；完整账户明细继续由账户页、后台策略账户页或 `frontend_account_precompute` 处理。

## v0.2.122 前台 profile 路由边界

- `/api/front/profile` 已由 `app.routers.frontend_profile` 注册，路由层只声明 GET/POST、Body 和 `include_catalog` 查询参数。
- profile 保存、跟随周期记录、账户预热排队和策略上下文构建仍复用原 payload 回调，避免在拆 router 时改变切换策略热路径。
- 后续拆前台 snapshot、账户、推荐和日计划接口时应沿用该模式：先迁 URL/参数，再逐步迁 service，保证每一步可单测验证。

## v0.2.123 前台运行视图路由边界

- `/api/front/public_snapshot`、`/api/front/snapshot`、`/api/front/strategy_models` 和 `/api/front/trading_account` 已由 `app.routers.frontend_runtime` 注册。
- 路由层不读取数据库、不触发任务、不计算账户，只把请求参数传给原 payload 回调；账户快照快路径、pending 自愈、策略目录按需读取等行为保持不变。
- 账户接口的 `defer` 默认值仍由 `QT_FRONT_ACCOUNT_DEFER_MISSES` 在 app 注册 router 时注入，生产默认保持缓存未命中返回 pending。

## v0.2.124 前台买入视图路由边界

- `/api/front/recommendations` 和 `/api/front/daily_plan` 已由 `app.routers.frontend_signal` 注册。
- 路由层不生成推荐、不生成日计划、不读写 `frontend_payload_cache`，只把查询参数传给原 payload 回调。
- 推荐和日计划的 `defer` 默认值仍由 `QT_FRONT_PAYLOAD_DEFER_MISSES` 在 app 注册 router 时注入，生产默认保持缓存未命中返回 pending/disabled；是否排队预计算由 `QT_FRONT_PAYLOAD_AUTO_PRECOMPUTE_ON_MISS` 控制。

## v0.2.125 后台概览路由边界

- `/api/admin/snapshot` 和 `/api/admin/model_signals` 已由 `app.routers.admin_overview` 注册。
- 路由层不读取数据库、不生成模型信号、不触发任务，只把查询参数传给原 payload 回调；后台概览的轻量缓存、状态汇总和信号 feed 行为保持不变。
- 后续后台数据库、缓存、访问审计和用户管理接口可按同样模式继续拆分，先固定 URL 合同，再逐步迁 service。

## v0.2.126 后台数据库与缓存路由边界

- `/api/admin/database/tables`、`/api/admin/database/table/{table_name}`、`/api/admin/cache/status` 和 `/api/admin/cache/clear` 已由 `app.routers.admin_data_cache` 注册。
- 路由层不直接检查 SQLite、不直接清缓存，只声明 HTTP 参数并调用原 payload 回调；数据库表读取、异常映射、内存缓存摘要和缓存清理语义保持不变。
- 上传导入、导出备份、清理样例状态等文件或破坏性操作仍留在原位置，后续单独拆分，避免和低风险数据库/缓存查询混在一起。

## v0.2.127 后台访问审计路由边界

- `/api/admin/access_logs`、`/api/admin/access_security` 和访问安全封禁相关 POST 接口已由 `app.routers.admin_access` 注册。
- 路由层不读取访问日志文件、不写封禁文件、不做异常分类，只声明 HTTP 参数并调用原 payload 回调。
- 访问审计分页筛选、异常 IP 分类、手动拉黑、一键拉黑和解除拉黑行为保持不变，继续只在后台 admin scope 下访问。

## v0.2.128 后台用户管理路由边界

- `/api/admin/frontend_users` 及其用户创建、更新、重置密码、封禁、解封和删除子路径已由 `app.routers.admin_frontend_users` 注册。
- 路由层不直接读写认证文件、不记录跟随周期、不清缓存，只声明 HTTP 参数并调用原 payload 回调。
- 用户 profile 变更后的跟随周期记录、账户缓存清理、密码重置和封禁语义保持不变，继续只在后台 admin scope 下访问。

## v0.2.129 前台推荐/日计划预计算边界

- 前台推荐和日计划仍优先读取 `frontend_payload_cache`，但缓存未命中不再默认排队批量预计算；自动排队必须同时开启 `QT_FRONT_PAYLOAD_PRECOMPUTE_ENABLED=true` 和 `QT_FRONT_PAYLOAD_AUTO_PRECOMPUTE_ON_MISS=true`。
- `QT_FRONT_PAYLOAD_PRECOMPUTE_ENABLED=false` 现在同时代表调度器不自动跑、前台缓存未命中不自动补算；后台手动接口仍可由管理员显式触发。
- 推荐和日计划默认 TTL 均为 1800 秒，避免推荐缓存早于 30 分钟预计算周期过期后由用户访问触发补算。

## v0.2.130 前台预计算状态合同

- `/api/jobs/status` 会返回 `frontend_payload_policy`，用于后台和运维脚本判断前台预计算当前是手动模式还是调度模式。
- `auto_precompute_on_miss` 是实际生效值，必须同时满足 `QT_FRONT_PAYLOAD_PRECOMPUTE_ENABLED=true` 和 `QT_FRONT_PAYLOAD_AUTO_PRECOMPUTE_ON_MISS=true`；`auto_precompute_on_miss_requested` 只表示环境变量请求值。
- 后台页面只根据该 policy 展示调度器是否会自动跑前台预计算，不用从下次运行时间推断，避免默认手动配置被误读为会自动消耗 CPU。

## v0.2.131 任务运维路由边界

- `/api/jobs/status`、任务日志、调度器启动/停止和任务暂停/恢复/停止已由 `app.routers.admin_jobs` 注册，URL、查询参数和返回结构保持不变。
- 该 router 不直接执行训练、复盘、回测或前台预计算，只把请求交给原状态 payload 和 `job_manager` 控制方法。
- 前台轻量快照中的任务摘要使用 `job_manager.frontend_status()`，不做完整进程巡检、新闻抓取器状态读取或缓存统计；后台 `/api/jobs/status` 仍保留完整运维状态。
- 后续具体任务执行入口仍可按同样方式继续迁移，先固定 HTTP 合同，再拆 service 和 worker 策略。

## v0.2.132 任务执行路由边界

- `/api/jobs/news/fetch`、`/api/jobs/market/sync`、`/api/jobs/ai/analyze`、`/api/jobs/trading/run`、`/api/jobs/strategy/replay`、`/api/jobs/frontend/precompute`、`/api/jobs/frontend/account_precompute`、`/api/jobs/daily/run` 和 `/api/admin/system/startup` 已由 `app.routers.admin_job_runs` 注册。
- router 层不直接抓新闻、同步行情、调用 AI、复盘策略或生成账户，只把请求参数传给原 payload 回调。
- 策略复盘、训练、回测和前台预计算是否手动触发、是否默认独立进程运行，仍由既有环境变量和 `job_manager` 控制。

## v0.2.133 核心系统路由边界

- 版本、认证、调试、运行配置和 `/api/status` 已由 `app.routers.core_system` 注册，URL 和请求 body 合同保持不变。
- `/api/status` 不再触发完整任务状态巡检，只返回轻量任务摘要；后台完整任务状态、队列状态和前台预计算 policy 仍通过 `/api/jobs/status` 查看。
- 前台公开快照和登录快照共用新闻摘要短缓存 `front_snapshot_news`，降低首屏和切换策略后重复新闻读取的尾延迟。
- 登录、注册和配置保存仍调用原 payload 回调，注册后的跟随周期记录和账户预热排队语义保持不变。

## v0.2.134 前台预计算和轻量快照边界

- 前台推荐/日计划批量预计算仍是维护任务，不属于用户请求热路径；默认只手动触发，且批量上限为 8 个用户、日计划 30 天、时间预算 20 秒。
- 前台缓存未命中不会因为 `QT_FRONT_PAYLOAD_AUTO_PRECOMPUTE_ON_MISS=true` 单独生效；必须同时开启 `QT_FRONT_PAYLOAD_PRECOMPUTE_ENABLED=true` 才会自动排队，生产默认两者都关闭。
- 即使显式开启定时预计算，首次运行也由 `QT_FRONT_PAYLOAD_PRECOMPUTE_INITIAL_DELAY_SECONDS` 控制，默认 1800 秒，避免调度器启动后很快触发批量推荐/日计划计算。
- 账户预热异步入队使用小型 worker 池，避免某个用户的队列锁等待或慢入队阻塞其他用户的首屏快照。
- 轻量前台快照默认不从缺失的 SQLite 新闻/日期回退完整引擎加载；空数据应返回可展示占位，真正的新闻抓取、策略复盘、训练和回测仍由后台手动任务或调度任务处理。

## v0.2.135 研究重任务手动边界

- `QT_RESEARCH_TASKS_MANUAL_ONLY=true` 是生产默认：调度器即使启动，也不会因为旧配置 `STRATEGY_REPLAY_ENABLED=true` 或 `STRATEGY_EVOLUTION_ENABLED=true` 自动跑策略复盘/进化。
- 如果管理员明确关闭该保护，调度器触发的策略复盘和进化仍默认走独立进程，由 `QT_STRATEGY_REPLAY_PROCESS_ENABLED=true` 和 `QT_STRATEGY_EVOLUTION_PROCESS_ENABLED=true` 控制。
- 时间线回测也纳入手动边界：`QT_TIMELINE_REQUIRE_MANUAL_TRIGGER=true` 时，缓存未命中的 `/api/quant/timeline` 和 `/api/quant/intraday_timeline` 只返回 `manual_required`，必须显式 `manual=true` 才会排队或同步计算。

## v0.2.136 前端 API 响应解析边界

- 前台和后台页面的 API helper 对成功和失败响应都先识别 HTML 错误页，再解析 JSON，避免代理错误页被误当成业务 payload。
- 该边界只改善用户可见错误呈现，不把 502/504/524 归类为业务成功；源站不可用、后端忙、Nginx upstream 错误仍必须通过任务状态和服务日志排查。

## v0.2.137 量化基础接口路由边界

- `/api/quant/dashboard`、推荐、日计划、基础参数、事件、新闻、相关性、组合、交易账户、手动运行和 `/api/news_history` 已由 `app.routers.quant_basic` 注册。
- router 层不直接执行量化计算，只负责参数声明和 URL 合同；业务逻辑继续留在 payload 回调中，便于下一步继续拆出研究/回测和数据诊断模块。
