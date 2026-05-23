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
4. 后台“基准参数”只用于诊断和默认调参，不代表任何用户正在跟随。
5. 策略模型共用新闻、行情、龙虎榜、AI 分析等公共数据，但参数、信号、成交、持仓和账户结果应该相互独立。
6. 小资金策略必须适配 1 万、2 万-5 万、5 万-10 万、10 万以上资金，不要让小资金买不起一手。
7. 前台不能出现后台初始化或管理入口；未登录只能看概览和新闻，登录后才能看账户、策略、持仓、成交等。
8. 后台必须可管理用户、数据、缓存、任务、日志、调试通道和数据库。

当前已完成到 v0.2.30：
- 前台用户 profile 有 simulated_cash、strategy_model_id、follow_started_at、follow_start_date。
- 切换策略会重置跟随开始时间。
- 前台账户按 follow_start_date 裁剪，不继承旧持仓。
- 交易账户使用 strategy_runtime_snapshots SQLite 缓存。
- 策略复盘任务会把每个策略的每日信号、成交和持仓写入 strategy_daily_signals、strategy_runtime_trades、strategy_runtime_positions。
- 策略复盘任务会把每个策略的每日账户快照和清算写入 strategy_runtime_snapshots、strategy_runtime_settlements。
- 前台账户优先读取 strategy_runtime_trades，并按 follow_start_date 和模拟资金派生账户。
- 推荐和日计划使用 frontend_payload_cache SQLite 短缓存。
- 资金档策略已改名为 小资金、短线稳健、均衡轮动、趋势多仓，并从 strategy_runtime_* 运行表汇总收益、回撤、胜率和成交数。
- 后台数据库页可以查看和清理缓存。
- 后台慢任务触发接口默认 background=true，新闻抓取、AI 分析、行情同步、日K补齐、龙虎榜、交易循环、策略复盘和系统启动会立即返回任务状态并在后台继续运行。
- 自动策略复盘使用 `QT_STRATEGY_REPLAY_BATCH_DAYS` 分批推进，默认 15 天一批，并记录 `strategy_replay_cursor`，避免服务器每小时全量重跑历史。
- 数据包导入改为流式合并 SQLite，避免 200MB 级数据库文件在服务器上一次性读入内存导致合并失败。
- 手动策略复盘和策略进化默认使用独立 Python 子进程运行，避免重计算长期占用 API 进程。
- 可以用 `python scripts/package_strategy_runtime_export.py` 生成只包含 `strategy_runtime_*` 的小包，把本地复盘结果合并到服务器，避免上传完整新闻/行情包。
- 部署脚本会验证版本、接口模块和数据库表结构。

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

本轮目标：开始 P2 用户跟随账户落库：新增 user_follow_accounts、user_follow_trades 和跟随周期记录，让用户账户成为独立实体。
```

## 可替换的本轮目标

如果你想分阶段做，可以把提示词最后一行换成下面任意一个：

```text
本轮目标：只做 P1 的数据库表和 repository，不改前台页面；新增 strategy_daily_signals、strategy_runtime_positions、strategy_runtime_trades，并补迁移、表结构验证和单元测试。
```

```text
本轮目标：让后台策略复盘任务把每个策略的每日信号、成交、持仓写入 SQLite，并在后台显示策略运行矩阵。
```

```text
本轮目标：让前台用户账户优先从策略运行结果派生，只有缺失时才回放，并显示跟随开始日、策略来源、缓存/快照命中状态。
```

```text
本轮目标：优化服务器性能，定位登录后慢接口和内存高的原因；只做可测量的轻量化、缓存或后台任务化改动。
```

```text
本轮目标：拆分 backend/app/main.py，把前台、后台、任务、数据、量化和策略接口逐步迁移到独立 router，保持行为不变并补测试。
```
