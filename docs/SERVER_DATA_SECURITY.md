# 服务器数据安全说明

本文定义服务器运行数据的边界、风险和检查方式。原则是：代码可以进 Git，生产数据、账号、密钥、数据库、日志和备份不进 Git。

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
