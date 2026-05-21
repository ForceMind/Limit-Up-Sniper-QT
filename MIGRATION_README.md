# 涨停狙击手迁移包

这个目录是从当前项目整理出来的干净迁移包，可直接移动到新项目目录并初始化 Git。

## 包含内容

- `backend/app`：后端服务、量化引擎、任务调度、AI分析、交易模拟接口
- `backend/tests`：当前量化引擎回归测试
- `frontend`：前台交易终端和后台管理页面
- `qt.sh`：根目录统一部署和更新入口
- `scripts`：安全扫描、部署/辅助脚本
- `deploy`：systemd 与 nginx 示例配置
- `docs`：需求、开发计划、量化逻辑、部署、安全与 Codex 交接文档
- `.env.example`：服务器环境变量模板
- `backend/data/config.example.json`：本地 JSON 配置模板
- `backend/data` 中少量 `600001/600002` 样例新闻、AI分析、日K、分时数据：仅用于新仓库开箱测试，不包含真实密钥或你的历史数据

## 未包含内容

以下内容刻意没有放进迁移包，避免上传 GitHub 时泄露密钥或携带本地运行数据：

- `.git`
- `.env`
- `.venv`
- `.pytest_cache`
- `backend/data/config.json`
- `backend/data/admin_credentials.json`
- `backend/data/ws_token_secret.txt`
- `backend/data/*.jsonl`
- 你的真实 `backend/data/news_history.json`
- 你的真实 `backend/data/news_analysis_records.json`
- `backend/data/quant_events_cache.json`
- `backend/data/kline_day_cache`
- 其他本地缓存、日志、数据库、截图产物

## 新项目使用方式

在新目录中执行：

```powershell
git init
git add .
git commit -m "Initial clean migration"
```

服务器部署前：

```powershell
Copy-Item .env.example .env
```

然后在 `.env` 中填写 DeepSeek、必赢数据、邮件 SMTP 等真实配置。不要提交 `.env`。

首次部署：

```bash
bash qt.sh install
```

后续更新：

```bash
bash qt.sh
```

## 历史数据迁移

如果要保留旧项目积累的新闻、AI分析、行情缓存和模拟交易状态，请从旧项目手动备份：

```text
E:\Privy\Limit-Up-Sniper-Commercial\backend\data
```

建议只在服务器或本地私有备份中保存，不要上传到公开 GitHub。恢复时把需要的数据文件复制到新项目的 `backend/data`。

注意：迁移包内自带的 `600001/600002` 数据是测试样例。部署到真实服务器后，可以删除这些样例，或让系统用真实新闻和行情覆盖运行数据。

## 迁移后检查

```powershell
python -m py_compile backend\app\quant\engine.py backend\app\quant\jobs.py backend\app\main.py
python -m pytest backend\tests\test_quant_engine.py
python scripts\security_scan.py
```
