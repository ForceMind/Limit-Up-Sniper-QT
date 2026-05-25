# 服务器数据安全说明

本文定义服务器运行数据的边界、风险和检查方式。原则是：代码可以进 Git，生产数据、账号、密钥、数据库、日志和备份不进 Git。

## v0.2.135 研究重任务状态安全边界

- `QT_RESEARCH_TASKS_MANUAL_ONLY`、`QT_STRATEGY_REPLAY_PROCESS_ENABLED`、`QT_STRATEGY_EVOLUTION_PROCESS_ENABLED`、`QT_TIMELINE_REQUIRE_MANUAL_TRIGGER` 和 `QT_FRONT_PAYLOAD_PRECOMPUTE_INITIAL_DELAY_SECONDS` 是部署配置，不包含密钥。
- 研究重任务、前台预计算和账户预热产生的缓存、任务状态、运行日志、策略运行表和 SQLite 结果仍属于服务器运行数据，不应提交 Git。
- 任务状态接口只应暴露任务名、进程状态、时间、进度和摘要，不应返回调试密钥、原始请求头、完整账户流水或生产数据库内容。

## 数据分层

### 可以进 Git

- 源码、脚本、文档。
- `.env.example`、`backend/data/config.example.json` 这类模板。
- 明确标记为 `Fixture` 的小样例数据。

### 不能进 Git

- `.env`、`.env.*`。
- `backend/data/config.json`：包含 DeepSeek、必盈、邮件等服务器本地配置。
- `backend/data/auth.json`：前后台账号哈希和 token secret。
- `backend/data/admin_credentials.json`：旧版账号文件，如存在应删除。
- `backend/data/ws_token_secret.txt`。
- `backend/data/quant_data.sqlite3`、`*.sqlite3`、`*.db`。
- `backend/data/quant_*.json`、`*.jsonl`、运行日志、任务状态。
- `backend/data/frontend_account_precompute_queue.json` 和对应 `.lock` 文件：前台账户预热待处理队列，属于服务器运行状态。
- `data_coverage` 后台任务结果只写入运行状态和进程内缓存；如果未来改为持久化文件，也应按服务器运行数据处理，不提交 Git。
- v0.2.69 起，前台切换策略的 profile 保存默认不返回完整策略目录，只返回当前用户 profile、当前策略和参数；不会新增需要提交的持久化文件。
- v0.2.70 起，`quant_backtest` 结果写入 `frontend_payload_cache` 短缓存，属于运行数据库内容，不导出到 Git。
- v0.2.71 起，前台切换到策略库模型时只按模型 ID 读取单条策略参数；该读取仍来自运行 SQLite，不产生新的仓库文件，也不应提交策略数据库。
- v0.2.72 起，`fit_strategy` 参数拟合默认在独立进程里运行；如选择应用最佳参数，会写入服务器本地状态文件，属于运行配置结果，不应提交 Git。
- v0.2.73 起，`market_sync` 分时行情同步默认在独立进程里运行；抓取到的分钟行情和任务日志属于服务器运行数据，不应提交 Git。
- v0.2.74 起，`kline_fill` 和 `lhb_sync` 补数任务默认在独立进程里运行；写入的日K、龙虎榜和任务日志都属于服务器运行数据，不应提交 Git。
- v0.2.75 起，`trade_cycle` 模拟交易循环默认在独立进程里运行；生成的模拟持仓、成交、通知日志和任务日志属于服务器运行数据，不应提交 Git。
- v0.2.76 起，`ai_analysis` 新闻结构化默认在独立进程里运行；写入的 AI 分析记录、缓存和任务日志属于服务器运行数据，不应提交 Git。
- v0.2.77 起，`data_coverage` 数据覆盖率诊断结果写入 `frontend_payload_cache` SQLite 短缓存；该缓存属于服务器运行数据，不应提交 Git。
- v0.2.78 起，`news_fetch` 新闻抓取默认在独立进程里运行；写入的新闻、事件缓存和任务日志属于服务器运行数据，不应提交 Git。
- v0.2.79 起，`system_startup` 系统启动流程默认在独立进程里运行；该流程写入的新闻、AI、行情、交易结果、任务状态和日志都属于服务器运行数据，不应提交 Git。
- v0.2.83 起，`quant_backtest` 和 `model_backtest` 只有显式 `manual=true` 才会在缓存未命中时启动重任务；生成的回测短缓存、任务状态和日志仍属于服务器运行数据，不应提交 Git。
- v0.2.84 起，重任务并发闸门状态会出现在任务状态里；它只暴露任务名、进程号和进度摘要，不应包含密钥或业务明细。
- v0.2.85 起，`model_backtest` 和 `quant_timeline` 独立进程会写入同一套任务状态和运行日志；这些仍是服务器运行数据，不应提交 Git。
- v0.2.86 起，后台页面展示的重任务槽位只来自任务状态摘要，不展示请求载荷明细或调试密钥。
- v0.2.87 起，`model_backtest` 和 `quant_timeline` 结果会写入 SQLite 短缓存表；该缓存属于服务器运行数据，不应提交 Git。
- v0.2.88 起，前台 profile 保存会返回 `follow_period_record` 和耗时诊断；这些字段只包含用户名、原因和来源摘要，不应包含密钥。异步写入的 `user_follow_periods`、任务 `busy` 状态和运行日志仍属于服务器运行数据。
- v0.2.89 起，前台 profile 保存可能返回 `account_precompute.status=queued_async`；该状态只表示服务器后台线程会写入账户预热队列，不新增可提交文件，`frontend_account_precompute_queue.json` 仍是运行数据。
- v0.2.90 起，前台账户 pending 自愈会返回 `account_precompute` 摘要并写任务日志；仍只包含用户名、原因和任务状态，不应包含密钥或完整账户明细。
- v0.2.91 起，前台会展示 `account_precompute` 的排队/启动状态；这些展示字段只来自任务摘要，不展示策略明细、密钥或数据库内容。
- v0.2.92 起，`account_precompute.deduped` 只表示重复异步入队被去重，不包含敏感数据；去重状态仍属于运行诊断摘要。
- v0.2.93 起，首屏 snapshot 的 `trading_account.account_precompute` 也只暴露预热任务摘要，不包含完整账户流水或服务器密钥。
- v0.2.94 起，前台展示的 `deduped` 文案仍只使用任务摘要字段，不新增任何服务器数据暴露面。
- v0.2.95 起，`frontend_account_precompute_async` 只暴露去重窗口、保护数量、原因和模式计数，不包含用户名、账户流水或密钥。
- v0.2.96 起，`profile_update_trace` 和 `profile_update_slow_stage` 只包含阶段名和耗时毫秒数，不包含用户名、IP、密钥、策略明细或账户流水。
- v0.2.97 起，profile 保存轻量兜底只返回推荐后的策略 ID、资金档标签和阶段耗时，不额外暴露完整策略目录。
- v0.2.98 起，profile 保存响应补齐 `created_at` 和 `profile_updated_at` 以复用保存结果构建上下文；这些字段为账号元数据，不包含密码、密钥或账户流水。
- v0.2.105 起，新增的策略运行快照 source 索引只包含策略 ID、source、生成时间和日期，不新增账户流水、原始策略 payload、用户密码或调试密钥。
- v0.2.104 起，新增的 `user_follow_periods` 当前周期索引只包含用户名、结束时间和排序时间字段；不新增密码、调试密钥、账户流水或策略原始 payload。
- v0.2.103 起，策略信号 feed 最新日期定位只读取 `strategy_daily_signals.date`，不会额外暴露信号内容、账户流水或用户资料。
- v0.2.102 起，schema 初始化缓存只记录本进程内的数据库路径和 schema 版本，不记录用户、策略明细、调试密钥或账户流水。
- v0.2.101 起，新增的 SQLite 复合索引只包含策略 ID、日期、生成时间、参数哈希、快照 ID、资金和排序字段，不新增敏感数据字段。
- v0.2.100 起，profile 保存前解析逻辑迁入 `app.quant.front_profile`，该 service 只接收调用方传入的策略摘要 loader 和单模型 loader，不直接读取用户账号、日志或账户流水。
- v0.2.99 起，profile 保存前的策略 ID 解析只使用策略 ID、资金和轻量模型摘要；解析到的单个可复用模型只在本次请求内复用，不返回完整策略目录或账户流水。
- `backups/`、`backend/backups/`、后台导出的迁移数据包。

## 当前存储策略

- SQLite 是长期主存储，默认文件是 `backend/data/quant_data.sqlite3`。
- JSON/CSV 只作为历史兼容、迁移来源或轻量状态文件。
- 策略进化完整模型、成交、交割单和资金流水应在 SQLite 的 `strategy_models`、`strategy_model_records`、`strategy_runs` 表里。
- `strategy_evolution_state.json` 只保留轻量当前状态；大文件会归档成 `strategy_evolution_state.archived-*.json`，`qt migrate` 会把归档里能入库的记录继续合并进 SQLite。

## 服务器检查命令

```bash
qt data-audit
qt data-audit --fix-permissions
```

该命令会检查：

- SQLite 是否存在、大小和主要表行数。
- 敏感文件是否存在，例如 `config.json`、`auth.json`、`admin_credentials.json`、`ws_token_secret.txt`。
- 运行数据、日志、备份包是否被 Git 跟踪。
- JSON 中是否残留 `password_plain` 等明文密码字段。
- Linux 服务器上敏感文件权限是否过宽。

`--fix-permissions` 不删除数据，只会在 Linux 服务器上把 `backend/data` 和备份目录收紧为 `700`，把数据库、配置、日志、备份包等运行文件收紧为 `600`。

本地也可以运行：

```bash
python scripts/server_data_audit.py
python scripts/server_data_audit.py --fix-permissions
python scripts/server_data_audit.py /path/to/backend/data
```

## 风险处理

### 旧明文账号文件

如果审计看到：

```text
backend/data/admin_credentials.json 包含明文密码字段 password_plain
```

说明旧版账号文件还在。当前系统使用 `backend/data/auth.json` 的 PBKDF2 哈希认证，不需要保存明文密码。处理方式：

```bash
qt auth
# 确认 auth.json 里的后台和前台账号都已配置
rm -f backend/data/admin_credentials.json
```

### 生产配置文件

`backend/data/config.json` 允许存在于服务器，但不能提交 Git。它可能包含 API Key、必盈授权、邮箱密码等。服务器上应保证：

```bash
chmod 600 backend/data/config.json backend/data/auth.json backend/data/ws_token_secret.txt 2>/dev/null || true
```

### 临时调试密钥

调试密钥只用于临时远程排查，不是常驻管理员账号。生成方式：

```bash
qt debug-key
# 或自动写入 .env：
qt debug-on
```

服务器 `.env` 只保存 `QT_DEBUG_API_KEY_SHA256`，不要保存或提交原始密钥。默认配置应保持：

```bash
QT_DEBUG_API_ENABLED=false
QT_DEBUG_API_ALLOW_WRITE=false
```

需要调试时短期开启 `QT_DEBUG_API_ENABLED=true` 并重启服务，请求通过 `X-QT-Debug-Key` 请求头认证；可用 `qt debug-status` 查看当前状态，调试完成后执行 `qt debug-off && qt restart`。只有确实要排查写接口时才临时设置 `QT_DEBUG_API_ALLOW_WRITE=true`，并在完成后立刻关闭。

API 触发服务重启同样默认关闭。`QUANT_ALLOW_API_RESTART=1` 只应在受控运维窗口短期开启；常规重启优先通过 SSH 执行 `qt restart`，不要把该开关长期放在公开服务器环境里。

### 备份和迁移包

后台下载的数据包可能包含新闻、行情、AI 缓存、策略模型和日志。它们只能用于迁移服务器，不要提交 Git，也不要放在公开目录。

建议：

- 更新前自动备份保留在 `backups/`。
- 定期把旧备份转移到私有存储或删除。
- 不通过 GitHub 传生产数据。

## Git 上传前检查

```bash
python scripts/security_scan.py
git status --short --ignored
git ls-files backend/data
```

`security_scan.py` 会拦截：

- 被 Git 候选文件包含的敏感路径。
- 超出样例大小的 `backend/data` 白名单文件。
- `news_history.json`、`news_analysis_records.json` 中非 `Fixture` 或非样例内容。
- 明显的密钥、token、密码赋值。

`git ls-files backend/data` 只应该出现样例文件，不应该出现服务器数据库、配置、日志、备份或迁移包。
## v0.2.106 运行日志与审计数据

- `backend/data/access_logs.json` 仍属于服务器运行数据，不要提交 Git、不要打包到公开产物。
- 访问审计默认通过 `QT_ACCESS_LOG_ASYNC=true` 异步写入，避免普通用户请求等待日志文件整写；队列满时可能丢弃访问日志条目，但不会丢业务数据。
- 后台访问日志接口会返回异步队列摘要，只包含队列长度和丢弃计数，不包含密钥、token 或用户账户明细。

## v0.2.107 访问审计批量写入

- 异步访问审计会批量合并后写入 `access_logs.json`；批量大小和等待窗口只影响审计日志落盘节奏，不改变认证、风控封禁或业务数据写入规则。
- `QT_ACCESS_LOG_BATCH_SIZE` 与 `QT_ACCESS_LOG_BATCH_WINDOW_MS` 可以按服务器磁盘能力调小或调大；不要把访问日志文件提交到 Git。

## v0.2.108 认证文件缓存边界

- `auth.json` 进程内缓存只存在于 API 进程内存中，缓存内容不打印、不导出、不进入迁移包。
- `_save_auth()` 写入后刷新缓存；外部手工修改 `auth.json` 时，下一次读取会根据文件签名变化重新加载。
- `auth.json` 仍包含用户、密码哈希、token secret 等敏感运行数据，继续禁止提交 Git 或放入公开数据包。

## v0.2.109 前台鉴权安全边界

- frontend scope 不再调用完整 `auth_status()`，但仍必须通过 token 或受控 debug header 校验；该调整不放宽前台接口权限。
- admin scope 仍执行 setup_required 检查，后台初始化和管理入口的访问控制不变。

## v0.2.110 前台运维信息暴露边界

- 前台快照不再返回后台运行日志；`quant_runtime_logs.jsonl`、任务日志和访问日志继续属于服务器运行数据，只能通过后台受控接口查看。
- 前台 `status_payload` 不再暴露服务器 `data_dir`，避免把部署路径泄露给普通前台用户。
- 前台任务状态短缓存只保存调度器和运行/暂停任务摘要，不包含密钥、token、数据库路径或账户流水明细。

## v0.2.111 新闻索引安全边界

- 新增的新闻热路径索引只包含公开新闻表里的日期、时间戳、时间文本和事件评分排序字段，不新增密钥、token、密码或账户流水字段。
- 部署脚本自动确认索引时不会导出新闻内容，也不会把 SQLite 数据库、日志或备份包加入 Git。

## v0.2.112 最新新闻时间缓存边界

- 最新新闻时间短缓存只保存在 API 进程内存中，内容是一条公开新闻时间字符串，不包含密钥、token、密码、数据库路径、用户资料或账户流水。
- 缓存不会写入 Git、不会导出到数据包，也不会替代 SQLite 中的新闻原始数据。

## v0.2.113 数据日期边界缓存边界

- 数据日期边界缓存只保存公开数据表的首日和最新日字符串，不包含新闻正文、AI 分析内容、用户资料、token、密码、数据库路径或账户流水。
- 该缓存仅存在于 API 进程内存中，不写入 Git、不导出到数据包；SQLite 数据库本体仍属于服务器运行数据，不能提交。

## v0.2.114 轻量快照写入边界

- 轻量前台快照不再同步写入 `user_follow_*` 或账户缓存派生结果，降低普通用户请求触发服务器运行数据写入的概率。
- 账户派生表和账户缓存仍属于服务器运行数据，不提交 Git，不放入公开数据包；需要迁移时使用受控数据包或数据库备份流程。

## v0.2.115 前台账户只读边界

- 前台账户 GET 默认不写入账户派生表；异步预热队列只记录用户名、原因和日期等必要调度信息，不包含密钥、token 或密码。
- `user_follow_*`、账户缓存和预热队列继续属于服务器运行数据，禁止提交 Git 或放入公开数据包。

## v0.2.116 策略运行矩阵安全边界

- `/api/admin/strategy_runtime/matrix` 只通过后台鉴权访问，返回策略级汇总字段：模型 ID、名称、日期范围、成交/持仓数量、收益、回撤和最新信号摘要。
- 矩阵不返回用户账号、用户持仓明细、token、密钥、数据库路径、原始新闻正文、AI 提示词或完整运行日志。
- 该接口只读取服务器 SQLite 运行数据，不导出数据库文件；生产库、日志、备份包和运行结果小包仍禁止提交 Git。

## v0.2.117 策略运行矩阵页面安全边界

- 后台页面只展示矩阵接口返回的策略级摘要，不新增普通前台入口，也不向未登录用户暴露策略运行状态。
- 页面刷新矩阵不会下载数据库、日志、备份包或原始运行数据；仍需通过后台鉴权访问。

## v0.2.118 策略运行矩阵服务安全边界

- `app.quant.strategy_runtime_matrix` 是纯 payload 组装模块，不读取 `.env`、认证文件、数据库文件、日志、备份包或服务器路径。
- 服务单测使用内存中的示例策略和信号数据，不引入真实服务器运行数据。

## v0.2.119 后台策略运行 router 安全边界

- `app.routers.admin_strategy_runtime` 只注册后台受控矩阵接口，不新增普通前台入口，也不绕过现有请求 scope 鉴权中间件。
- 路由测试使用内存回调，不读取真实数据库、密钥、日志或服务器运行数据。

## v0.2.120 后台策略账户路由安全边界

- `app.routers.admin_strategy_runtime` 继续注册后台策略账户和策略回放接口，但仍只在后台 admin scope 下访问。
- 路由层不新增数据导出能力，不返回密钥、token、数据库路径、日志文件或备份包。

## v0.2.121 前台快照快路径安全边界

- `runtime_snapshot_fast_path` 只读取已有 `strategy_runtime_snapshots.account_json`，返回当前前台用户可见的账户摘要、持仓和当日成交，不新增数据库导出、日志导出或密钥暴露能力。
- profile 保存和注册只写账户预热队列摘要，队列仍只包含用户名、触发原因和日期等调度字段，不包含密码、token、调试密钥或完整账户流水。

## v0.2.122 前台 profile router 安全边界

- `app.routers.frontend_profile` 只注册原有前台 profile GET/POST 路径，不新增后台入口、调试入口或数据导出接口。
- 路由层仍把请求交给原 scope 鉴权和 payload 回调处理，不读取 `.env`、认证文件、数据库、日志或备份包。

## v0.2.123 前台运行 router 安全边界

- `app.routers.frontend_runtime` 只注册原有前台 public snapshot、登录 snapshot、策略目录和交易账户路径，不新增后台管理入口或数据导出接口。
- router 层只接收请求参数并调用原 payload 回调，不直接读取 `.env`、认证文件、SQLite 数据库、日志、备份包或服务器路径。

## v0.2.124 前台买入 router 安全边界

- `app.routers.frontend_signal` 只注册原有前台推荐和日计划路径，不新增后台入口、调试入口或数据导出接口。
- router 层不直接读取 `frontend_payload_cache`、SQLite 数据库、日志、`.env` 或认证文件；缓存读取和预计算排队仍由原 payload 回调在既有鉴权边界内处理。

## v0.2.125 后台概览 router 安全边界

- `app.routers.admin_overview` 只注册原有后台概览和模型信号路径，不新增前台入口、调试入口或数据导出接口。
- router 层不直接读取 `.env`、认证文件、SQLite 数据库、日志、备份包或服务器路径；后台 admin scope 鉴权和 payload 安全边界保持不变。

## v0.2.126 后台数据库与缓存 router 安全边界

- `app.routers.admin_data_cache` 只注册原有后台数据库查看和缓存管理路径，不新增前台入口、调试入口或数据包导出能力。
- router 层不直接读取 SQLite、日志、`.env`、认证文件或备份包；数据库读取和缓存清理仍在既有后台 admin scope 鉴权边界内执行。

## v0.2.127 后台访问审计 router 安全边界

- `app.routers.admin_access` 只注册原有后台访问日志和访问安全路径，不新增前台入口、调试入口或数据导出能力。
- router 层不直接读取访问日志、封禁 IP 文件、`.env`、认证文件或备份包；日志读取和封禁写入仍在既有后台 admin scope 鉴权边界内执行。

## v0.2.128 后台用户管理 router 安全边界

- `app.routers.admin_frontend_users` 只注册原有后台用户管理路径，不新增前台入口、调试入口或数据导出能力。
- router 层不直接读取认证文件、密码哈希、token、`.env`、日志或备份包；用户创建、密码重置、封禁和删除仍在既有后台 admin scope 鉴权边界内执行。

## v0.2.129 前台预计算触发安全边界

- `QT_FRONT_PAYLOAD_AUTO_PRECOMPUTE_ON_MISS=false` 时，普通前台用户访问推荐或日计划不会启动 `frontend_payload_precompute` 后台任务，只会收到待生成状态；后台手动触发仍需要 admin scope。
- `frontend_payload_cache` 仍属于 SQLite 运行数据，不能提交 Git；TTL 调整不改变缓存的数据安全属性。
- 前台 pending/disabled 响应只暴露任务是否自动排队的轻量状态，不返回 `.env`、调试密钥、数据库路径、日志路径或完整任务 payload。

## v0.2.130 前台预计算策略状态安全边界

- `/api/jobs/status` 的 `frontend_payload_policy` 只暴露布尔开关、TTL、用户上限、间隔和进程模式，不返回 `.env` 原文、密钥、token、数据库路径、日志路径或任务 payload。
- `auto_precompute_on_miss_requested` 只用于解释配置请求值和实际生效值的差异，不代表普通前台用户获得了后台任务管理权限。

## v0.2.131 任务运维 router 安全边界

- `app.routers.admin_jobs` 只注册原有任务状态、日志和控制路径，不新增前台入口、调试入口、数据导出能力或绕过鉴权的任务执行能力。
- router 层不直接读取 `.env`、认证文件、SQLite 数据库、日志文件或备份包；日志读取和任务控制仍在原 `job_manager` 与既有 scope 鉴权边界内执行。
- `job_manager.frontend_status()` 只向前台摘要暴露调度器、running 和 paused 状态，不返回任务日志、完整 payload、服务器路径、密钥或后台缓存统计。

## v0.2.132 任务执行 router 安全边界

- `app.routers.admin_job_runs` 只注册原有后台任务执行入口，不新增普通前台入口、调试入口、数据导出能力或绕过鉴权的执行通道。
- router 层不直接读取 `.env`、认证文件、SQLite 数据库、日志文件或备份包；任务运行、日志写入和进程启动仍由原 `job_manager` 与既有 scope 鉴权边界处理。

## v0.2.133 核心系统 router 安全边界

- `app.routers.core_system` 只注册原有版本、认证、调试、配置和状态路径，不新增调试密钥回显、数据导出能力或绕过鉴权的管理入口。
- `/api/status` 的任务摘要只暴露调度器、running 和 paused 状态，不返回完整任务 payload、运行日志、服务器路径、密钥或后台队列详情。
- `front_snapshot_news` 只缓存前台已可见新闻摘要和情绪摘要，不缓存认证 token、用户资料、账户流水、运行日志或调试密钥。
## v0.2.134 前台限流与轻量快照安全边界

- `frontend_payload_policy` 新增的 `max_seconds`、worker 数和轻量 fallback 开关只暴露布尔/数字配置摘要，不返回 `.env` 原文、密钥、token、数据库路径或任务完整 payload。
- 前台轻量快照在新闻或日期缺失时返回空新闻/日期占位，不导出 SQLite、日志、备份包或服务器运行数据。
- 账户预热异步 worker 池只消费既有队列摘要；后台状态仍只展示计数和原因分布，不向前台暴露用户名列表、账户流水或调试密钥。
