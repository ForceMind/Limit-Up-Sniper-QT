# 量化系统当前买卖逻辑

本文描述的是当前已经实现和正在收敛的逻辑。系统已经支持基于本地 5 分钟 K 线的分时回放，但还没有接入盘口快照、逐笔成交和真实委托撮合。

## 1. 数据输入

系统共用一套数据底座：

- `backend/data/quant_data.sqlite3`：新闻、AI 分析、日 K、分钟 K、龙虎榜、策略模型、训练轨迹的主存储。
- `backend/data/news_history.json`、`news_analysis_records.json`：历史兼容和迁移来源。
- `backend/data/biying_stock_list.json`：股票代码和名称映射。
- `backend/data/kline_day_cache/*.json`、`kline_cache/*.csv`：旧 K 线缓存，只作为兼容读取和迁移来源。

当前没有真实账户接口，不会真实下单；所有账户都是纸面撮合账户。

## 2. 产品账户模型

正确的产品模型是“共用数据、多个策略、用户独立跟随”：

- 数据服务持续抓新闻、行情、AI 分析、龙虎榜，并补齐缺失数据。
- 训练服务用同一份数据生产多个策略模型，每个模型有自己的基础参数、收益、回撤、胜率、成交、交割单和训练淘汰记录。
- 用户注册后选择模拟资金和一个跟随策略；账户从注册日或切换策略日开始，用该策略独立运行。
- 用户只看到自己跟随策略对应的持仓、成交、交割单和资金流水。
- 系统默认基础参数只用于人工调参、默认计算、生成新策略和诊断；它不是产品主账户，也不代表任何用户正在跟随。

因此，后台“持仓成交”应该按策略查看；策略库里的每个模型才是用户可跟随的策略；前台用户账户等于“用户资金 + 用户跟随策略 + 跟随开始日期”。

用户跟随信息保存在前台账号 profile 中：

- `simulated_cash`：用户模拟资金。
- `strategy_model_id`：用户当前跟随策略。
- `follow_started_at` / `follow_start_date`：本次跟随开始时间。新注册时取注册时间；切换策略或调整模拟资金时重置。

v0.2.32 起，前台账户派生结果会按用户、策略、模拟资金、跟随开始日和 `as_of` 写入 `user_follow_snapshots`、`user_follow_positions`、`user_follow_trades`。这些表是用户账户视图的物化结果；策略级别的公共运行结果仍保存在 `strategy_runtime_*` 表中。
v0.2.33 起，`user_follow_periods` 记录用户注册、设置资金或切换策略形成的跟随周期，后台用户管理页会展示当前周期、账户快照、持仓和最近成交来源。
当前阶段不做“每个用户从注册日开始重建完整独立策略运行”的重计算版本；前台先按用户资金和跟随开始日读取/裁剪策略级运行结果，真正的用户独立重跑保留在后续计划中。

## 3. 策略模型

策略分三类：

- 策略库模型：进化/回测后保存的参数组合，是主要可跟随对象。
- 资金档策略：1万、2万-5万、5万-10万、10万以上等资金约束预设，控制最大持仓、单票资金和可买一手能力。
- 系统默认基础参数：后台手动参数或拟合结果，只用于诊断、生成新策略和作为人工调参模板，不直接代表产品账户。

策略库模型只有被用户选择跟随后，才影响该用户账户。每个策略模型都必须保存自己的基础参数；后台把某个模型“复制为系统默认基础参数”不会改变任何用户已经选择的跟随策略。

## 4. 新闻事件结构化

每条新闻或 AI 分析记录会被转成 `NewsEvent`：

- `event_type`：政策催化、业绩财报、订单合作、产品技术、板块异动、宏观市场、风险事件等。
- `industry`：AI算力、半导体、电力能源、新能源、汽车、机器人、消费零售等。
- `code/name`：影响个股。
- `sentiment`：情绪分数，范围约为 `-1` 到 `1`。
- `impact_score`：事件影响分，范围 `0` 到 `100`。
- `ai_score`：历史 AI 分析里已有的个股分数。

## 5. Agent 评分

每只股票在某个日期由四个内部 Agent 打分：

- 新闻情绪 Agent：正负面关键词、历史 AI 分、新闻情绪强度。
- 事件影响 Agent：事件类型、行业、事件影响分、同类历史兑现表现。
- 技术走势 Agent：当时日 K 技术状态，包括 3日/5日/20日收益、量比、波动率、回撤。
- 风控 Agent：波动率、回撤、过热程度、负面事件。

最终买入分：

```text
buy_score =
  新闻情绪分 * sentiment_weight
  + 事件影响分 * event_weight
  + 技术走势分 * technical_weight
  + 风控可交易分 * risk_weight
```

## 6. 买入规则

静态推荐接口：

```text
GET /api/quant/recommendations
GET /api/quant/daily_plan
```

规则：

- `buy_score >= buy_threshold`：买入候选。
- `watch_threshold <= buy_score < buy_threshold`：重点观察。
- `buy_score < watch_threshold`：暂不买入。
- 如果 `sell_score >= avoid_sell_threshold` 且 `buy_score < avoid_buy_ceiling`：回避/卖出。

## 7. 全周期回放

回放接口：

```text
GET /api/quant/timeline
GET /api/quant/model/backtest
GET /api/front/trading_account
```

规则：

1. 按交易日从早到晚推进。
2. 当天只能看到当天及以前的新闻、历史样本和当时已有 K 线。
3. 当天新闻产生买入信号。
4. 信号不会当天成交。
5. 下一交易日按开盘价模拟买入。
6. 每天收盘后更新持仓、收益和卖出判断。
7. 用户账户从 `follow_start_date` 开始运行，不继承该日期以前的持仓。

## 8. 卖出规则

当前全周期回放和模拟持仓的卖出条件：

- 止损：持仓收益 `<= stop_loss_pct`。
- 止盈：持仓收益 `>= take_profit_pct`。
- 持仓到期：默认持有 `max_hold_days` 个交易日。
- 卖出评分触发：当日重新评分后 `sell_score >= sell_score_threshold`。

当前卖出价格使用卖出日收盘价；分时模式会优先使用分钟 K 触发价。

## 9. 分时逻辑现状

当前已经接入本地 5 分钟 K 缓存：

- 目录：`backend/data/kline_cache/*.csv`
- 格式：`time,open,close,high,low,volume,amount`
- 接口：`GET /api/quant/intraday_timeline`
- 必盈同步：`POST /api/data/biying/sync_intraday?date=YYYY-MM-DD&source=events&max_codes=200`

分时回放规则：

1. 新闻信号有明确时间戳。
2. 如果该股票当天有 5 分钟 K，买入价使用“信号时间之后下一根 5 分钟 K 的开盘价”。
3. 持仓期间逐根检查 5 分钟 K：
   - `low <= entry_price * (1 + stop_loss_pct / 100)`：按止损价卖出。
   - `high >= entry_price * (1 + take_profit_pct / 100)`：按止盈价卖出。
   - 如果同一根 5 分钟 K 同时触发止盈和止损，保守假设止损先发生。
4. 如果当天没有分钟 K，但开启 `use_daily_fallback=true`，则使用日线开盘/收盘价兜底。

## 10. 下一步

后续架构重点：

- 策略运行状态入库，当前已用 `strategy_runtime_snapshots` 缓存前台账户回放结果，并在 v0.2.23 新增 `strategy_daily_signals`、`strategy_runtime_positions`、`strategy_runtime_trades` 保存每个策略的每日信号、持仓和成交；v0.2.24 起 `strategy_runtime_snapshots` 同时保存 `daily_runtime:*` 正式每日账户快照，并新增 `strategy_runtime_settlements` 保存每日清算。
- 前台账户优先读取 `user_follow_snapshots`；未命中时读取 `strategy_runtime_trades`，再按用户跟随开始日和模拟资金派生账户；没有落库结果时才回退短缓存或即时回放。
- 前台推荐和日计划已先用 `frontend_payload_cache` 做短缓存；v0.2.48 起缓存未命中时默认触发 `frontend_payload_precompute` 后台任务，前台接口返回 pending，不再同步等待完整计算。
- 后台数据库管理页可以查看并清理上述缓存，便于排查服务器接口变慢或缓存失效问题。
- v0.2.25 起，后台慢任务触发接口默认快速返回，实际新闻、AI、行情、补数、交易循环和策略复盘继续在任务线程里运行，避免反向代理等待完整计算。
- v0.2.28 起，服务器自动策略复盘按日期窗口分批推进，默认 15 天一批，避免常规调度每小时全量重跑历史区间。
- v0.2.30 起，手动策略复盘和策略进化可以由独立 Python 子进程执行，降低重计算对 API 进程的影响。
- v0.2.31 起，独立进程任务状态会自动巡检，异常退出时标记失败，避免任务状态永久停留在运行中。
- v0.2.32 起，用户跟随账户快照、持仓和成交入库，避免每次页面打开都重复缩放、裁剪和派生同一周期账户。
- v0.2.33 起，用户跟随周期和后台账户诊断入库/展示，资金变化与策略切换一样会开启新周期。
- v0.2.49 起，策略复盘、模型训练和回测默认只手动触发；策略库查看交割单默认读取已保存模型记录；日常自动链路使用已训练模型，主要执行新闻抓取、AI 分析、行情同步、模拟交易和前台缓存预计算。
- 高频状态用 WebSocket 或增量轮询，重接口拆成后台任务加进度查询。
- 推荐和日计划默认走轻量缓存和后台预计算；覆盖率、回测详情等剩余慢接口后续也应改为后台任务 + 进度 + 缓存读取。
