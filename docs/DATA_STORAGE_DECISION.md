# 数据存储决策

本项目当前仍允许 JSON 文件作为开发期和单机部署期的落地格式，但下列数据会持续增长、需要按日期/股票/用户/策略查询，应迁移到数据库，首选 SQLite 起步，后续可平滑迁移到 PostgreSQL。

真实运行数据可以通过 `QUANT_DATA_DIR` 指向服务器本地或独立磁盘的数据目录，避免覆盖仓库里的样例数据。

## 必须进数据库

| 数据 | 当前文件 | 数据库表建议 | 原因 |
| --- | --- | --- | --- |
| 原始新闻 | `backend/data/news_history.json` | `news_raw` | 需要按时间、来源、关键词、股票回放，不能只靠整文件读写 |
| AI 分析记录 | `backend/data/news_analysis_records.json` | `news_analysis` | 需要复用分析结果，避免重复调用模型 |
| 结构化事件 | `backend/data/quant_events_cache.json` | `news_events` | 策略输入核心数据，需要按日期/股票/事件类型查询 |
| 日线行情 | `backend/data/kline_day_cache/*.json` | `market_daily_bars` | 复盘必须按股票和交易日快速查询 |
| 分钟行情 | `backend/data/kline_cache/*.csv` | `market_minute_bars` | 高频/盘中策略必须按股票、日期、时间窗口查询 |
| 模拟交易状态 | `backend/data/quant_state.json` | `paper_accounts`, `paper_positions`, `paper_trades` | 资金、持仓、成交必须可审计、可恢复、可分账户 |
| 策略复盘结果 | `backend/data/quant_job_state.json` 中的 `strategy_replay` | `strategy_runs`, `strategy_equity`, `strategy_trades` | 需要保存每次策略参数、收益曲线、交易明细，方便比较模型 |
| 遗传进化模型 | `backend/data/strategy_evolution_state.json` | `strategy_models`, `strategy_model_metrics` | 会同时跑很多策略，前台要可查看和对比 |
| 访问审计 | `backend/data/access_logs.json` | `access_logs` | 后台要查用户、IP、浏览器、路径和时间范围 |
| 任务日志 | `backend/data/quant_runtime_logs.jsonl` | `job_logs` | 服务器长期运行时需要检索、分页、按任务过滤 |

## 可以继续用 JSON 或文件

| 数据 | 当前文件 | 决策 |
| --- | --- | --- |
| 运行配置 | `backend/data/config.json` | 保留 JSON，属于小体积配置；不要提交 Git |
| 账号密钥 | `backend/data/auth.json` | 短期保留 JSON，必须忽略；后续多用户成熟后迁移数据库 |
| 示例配置 | `backend/data/config.example.json` | 可以提交，用于部署说明 |
| 临时缓存 | `backend/data/*_state.json` 中非业务明细 | 可保留，作为任务游标和轻量状态 |
| 备份包 | `backups/*.tar.gz` | 文件存储，不进数据库，不提交 Git |

## 迁移顺序

1. 先建 SQLite 基础表：`news_raw`、`news_analysis`、`news_events`、`market_daily_bars`、`market_minute_bars`。
2. 把策略复盘写入 `strategy_runs`、`strategy_equity`、`strategy_trades`，前台读取数据库结果。
3. 再迁移访问审计和任务日志，避免长期 JSON 文件过大。
4. 最后把前台多用户从 `auth.json` 迁移到 `users` 表。

## 服务器数据检查

在服务器项目根目录执行：

```bash
python scripts/check_data_coverage.py
```

如果 `news_history.json` 的最早日期晚于 `2026-03-01`，说明服务器没有完整保留从 3 月开始的新闻，需要重新补拉或导入历史新闻。

## JSON/CSV 入库

旧项目或服务器数据目录可以直接迁移到当前项目 SQLite：

```bash
python scripts/migrate_data_to_sqlite.py --source /path/to/old/backend/data --db backend/data/quant_data.sqlite3
```

迁移脚本使用 `INSERT OR REPLACE` 和业务主键去重，可以重复执行。已迁移的表包括：

- `news_raw`、`news_analysis`、`ai_cache`、`ai_usage_logs`
- `news_events`
- `market_daily_bars`、`market_minute_bars`、`lhb_records`
- `market_snapshot_rows`、`market_pool_items`、`watchlist_items`
- `paper_accounts`、`paper_positions`、`paper_trades`
- `strategy_runs`、`strategy_model_metrics`
- `access_logs`、`job_logs`

不会迁移 `.env`、`config.json`、`auth.json`、`admin_credentials.json`、`ws_token_secret.txt` 等账号、密钥和配置文件。
