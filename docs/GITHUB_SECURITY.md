# GitHub 上传前安全检查

## 当前规则

- `.env` 不入库。
- `backend/data/**` 默认不入库。
- GitHub 只保留公开安全的模板和 fixture 样例数据。
- 真实生产配置放在服务器 `.env` 或本地 `backend/data/config.json`。

允许入库的 `backend/data` 文件：

```text
backend/data/.gitkeep
backend/data/config.example.json
backend/data/biying_stock_list.json
backend/data/news_history.json
backend/data/news_analysis_records.json
backend/data/kline_day_cache/600001.json
backend/data/kline_day_cache/600002.json
backend/data/kline_cache/600001_2026-05-19.csv
backend/data/kline_cache/600002_2026-05-19.csv
```

这些文件必须保持为 `Fixture` 样例数据，不得替换成真实历史新闻、真实 AI 记录或生产行情缓存。

禁止入库的敏感文件：

```text
.env
.env.*
backend/data/config.json
backend/data/auth.json
backend/data/admin_credentials.json
backend/data/ws_token_secret.txt
backend/data/*.jsonl
backend/data/quant_*.json
backend/backups/
backups/
*.pem
*.p12
*.pfx
id_rsa
id_dsa
id_ecdsa
id_ed25519
```

## 上传前检查

```bash
python scripts/security_scan.py
python scripts/server_data_audit.py
git status --short --ignored
git ls-files backend/data
```

期望结果：

```text
No obvious secrets found in candidate files.
backend/data/.gitkeep
backend/data/biying_stock_list.json
backend/data/config.example.json
backend/data/kline_cache/600001_2026-05-19.csv
backend/data/kline_cache/600002_2026-05-19.csv
backend/data/kline_day_cache/600001.json
backend/data/kline_day_cache/600002.json
backend/data/news_analysis_records.json
backend/data/news_history.json
```

同时确认 `git status --short --ignored` 中的敏感文件只出现在 `!!` 忽略列表里，不出现在 staged 或 tracked 列表里。

`server_data_audit.py` 用于服务器和本地运行目录体检。它会列出 SQLite 大小、主要表行数、敏感配置、旧账号文件、备份包，并在发现 `password_plain` 等明文密码字段时返回非 0。生产服务器上可以直接执行：

```bash
qt data-audit
qt data-audit --fix-permissions
```

## 重要说明

如果你要把“当前这个已有 Git 历史的仓库”直接推到公开 GitHub，旧提交历史里可能仍然保存过密钥或生产数据。最稳妥的做法是：

```bash
mkdir ../qt-clean
rsync -a --exclude .git --exclude .venv --exclude backend/data --exclude .env ./ ../qt-clean/
cd ../qt-clean
git init
git add .
python scripts/security_scan.py
git commit -m "initial clean quant system"
```

如果必须保留当前仓库历史，需要先做历史清理，并且已经进入历史的密钥应直接作废重置。
