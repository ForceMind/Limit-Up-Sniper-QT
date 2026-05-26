# Go 控制面重构说明

本轮重构把“主逻辑”放到 Go 控制面里，现有 Python/FastAPI 后端继续承载 API、量化计算、抓取和持久化细节。这样先把用户、数据、新闻、行情和策略模型运行的编排边界稳定下来，再逐步迁移底层实现。

## 入口

- `cmd/qtctl/main.go`：Go 主入口，提供 CLI 和本地 HTTP 控制服务。
- `internal/qtcore/orchestrator`：应用编排层，只关心服务边界和任务顺序。
- `internal/qtcore/pythonworker`：Python 任务适配器，封装 `scripts/run_quant_job.py`。
- `internal/qtcore/users`：前台用户文件管理，兼容 `backend/data/auth.json`。
- `internal/qtcore/httpapi`：本地控制 API，默认监听 `127.0.0.1`，可用 `QT_GO_CONTROL_TOKEN` 开启管理 token。

## 服务边界

| 服务 | 职责 | 封装任务 |
| --- | --- | --- |
| `NewsService` | 新闻抓取、AI 结构化分析 | `news_fetch`、`ai_analysis` |
| `DataService` | 数据覆盖率、日 K、龙虎榜 | `data_coverage`、`kline_fill`、`lhb_sync` |
| `MarketService` | 分时/实时行情同步 | `market_sync` |
| `UserRuntimeService` | 用户账户和前台缓存预热 | `frontend_account_precompute`、`frontend_payload_precompute` |
| `StrategyService` | 日常策略、交易循环、研究任务 | `strategy_daily_refresh`、`trade_cycle`、`strategy_replay`、`strategy_evolution`、`model_backtest`、`quant_timeline`、`quant_backtest`、`fit_strategy` |
| `users.Store` | 前台用户增删改查、禁用、重置密码 | `backend/data/auth.json` |

## 启动编排

`qtctl startup` 对齐现有 `SystemStartupService` 的顺序：

1. `news_fetch`
2. `ai_analysis`
3. `kline_fill`
4. `lhb_sync`
5. `market_sync`
6. `strategy_daily_refresh`
7. `trade_cycle`
8. 可选 `strategy_replay`

默认不运行复盘、训练、回测等研究任务，除非显式传 `-replay`。

## 使用方式

```powershell
# 一键启动 FastAPI + Go 控制面
.\start-local.ps1

# 只检查本地环境，不启动服务
.\start-local.ps1 -CheckOnly -SkipInstall

# 单独运行 Go 控制面命令
go run ./cmd/qtctl status
go run ./cmd/qtctl startup -date 2026-05-26 -start-date 2026-03-01
go run ./cmd/qtctl run -job news_fetch -payload-json '{"hours":12,"pages":5}'
go run ./cmd/qtctl users list
go run ./cmd/qtctl serve -host 127.0.0.1 -port 8090
```

可选环境变量：

- `QT_PYTHON`：Python 可执行文件，默认优先用项目 `.venv`。
- `QT_GO_CONTROL_HOST` / `QT_GO_CONTROL_PORT`：HTTP 控制服务地址。
- `QT_GO_CONTROL_TOKEN`：启用后 HTTP 请求需要 `x-qt-control-token`。

`start-local.ps1` 会优先使用 `.tools/go`，其次使用 PATH 里的 `go`。如果两者都不存在，默认从 `go.dev` 下载 Windows amd64 稳定版到 `.tools/go`；传 `-SkipGoInstall` 可关闭自动下载。

## 迁移原则

- Go 层只做控制面：配置、用户、任务编排、服务边界、管理 API。
- Python 层继续做计算面：新闻抓取、行情同步、策略运行、回测、SQLite 写入。
- 每个新能力先进入一个明确服务，再由 `orchestrator.App` 编排，避免重新把逻辑堆回入口。
