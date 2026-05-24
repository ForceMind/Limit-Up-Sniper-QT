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

当前已完成到 v0.2.48：
- 前台用户 profile 有 simulated_cash、strategy_model_id、follow_started_at、follow_start_date。
- 切换策略或调整模拟资金会重置跟随开始时间。
- 前台账户按 follow_start_date 裁剪，不继承旧持仓；真正为每个用户从注册日完整独立重跑策略暂不开发，等明确要求后再做。
- 策略复盘任务会把每个策略的每日信号、成交、持仓、账户快照和清算写入 strategy_daily_signals、strategy_runtime_trades、strategy_runtime_positions、strategy_runtime_snapshots、strategy_runtime_settlements。
- 前台账户优先读取 user_follow_snapshots；未命中时从 strategy_runtime_*、短缓存、模型记录或即时回放派生，并写入 user_follow_positions、user_follow_trades。
- 用户注册、设置资金或切换策略会写入 user_follow_periods。
- 后台用户管理页展示当前跟随周期、账户快照、持仓和最近成交来源。
- 后台“持仓成交”按策略查看，不再把系统默认基础参数当成一个独立账户。
- 推荐和日计划使用 frontend_payload_cache SQLite 短缓存；缓存未命中时默认触发 frontend_payload_precompute 后台/独立进程任务，前台返回 pending。
- 后台慢任务触发接口默认 background=true；手动策略复盘和策略进化默认使用独立 Python 子进程运行。
- 独立进程任务会在状态接口读取时自动巡检，异常退出但未写回结果时标记失败并写运行日志。
- 自动策略复盘使用 QT_STRATEGY_REPLAY_BATCH_DAYS 分批推进，默认 15 天一批，并记录 strategy_replay_cursor。
- 数据包导入改为流式合并 SQLite，避免 200MB 级数据库文件在服务器上一次性读入内存。
- 可以用 python scripts/package_strategy_runtime_export.py 生成只包含 strategy_runtime_* 的小包，把本地复盘结果合并到服务器。
- 后台数据库页可以查看和清理缓存。
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

本轮目标：把覆盖率、回测详情和其它可能超过 5 秒的诊断接口继续改为后台任务 + 进度 + 缓存读取。
```

## 可替换的本轮目标

```text
本轮目标：把覆盖率、回测详情和其它可能超过 5 秒的诊断接口继续改为后台任务 + 进度 + 缓存读取。
```

```text
本轮目标：增加后台“策略运行矩阵”，展示每个策略最近运行日期、信号数、成交数、收益、回撤和运行数据是否缺失。
```

```text
本轮目标：拆分 backend/app/main.py，把前台、后台、任务、数据、量化和策略接口逐步迁移到独立 router，保持行为不变并补测试。
```
