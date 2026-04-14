# Wardrobe App（衣橱管理）

一个最小可用（MVP）的衣橱管理网站：
- 记录衣服：`style / color / price / brand / notes` 等字段（可编辑）
- 上传图片：自动推断 **主色** 与 **风格标签**（零样本分类），并生成用于相似度的图片向量
- 相似度/得分：上传新图片后，可与已录入衣服对比，得到“相似度百分比”和按权重计算的得分

## 运行方式（Windows / PowerShell）

在本目录执行：

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn wardrobe_app.main:app --reload
```

打开浏览器访问：`http://127.0.0.1:8000`

## 说明（重要）

- **price/brand** 很难仅凭图片可靠识别（除非拍到吊牌/价签/Logo 并做 OCR/Logo 检测）。本 MVP 会把它们作为可编辑字段；图片分析阶段主要做：颜色 + 风格标签 + 图像相似度向量。
- 第一次运行会自动下载模型（可能较大，取决于网络环境）。

## 部署（Render 等）与注册 500 排查

本应用默认使用 **SQLite**（`wardrobe_app/data/` 或环境变量 `WARDROBE_DATA_DIR` 指定目录）。

1. **进程数必须为 1**：多个 worker 会同时写同一 SQLite 文件，易出现 `database is locked` 或注册失败。Render 上请使用单进程启动，例如：
   - Start Command：`uvicorn wardrobe_app.main:app --host 0.0.0.0 --port $PORT --workers 1`
   - 若使用 Gunicorn：`WEB_CONCURRENCY=1`（或等价配置为 1 worker）。
2. **可写数据目录（推荐）**：在 Render 环境变量中设置 `WARDROBE_DATA_DIR=/tmp/wardrobe_data`，确保数据库与上传目录在可写路径（部分镜像对代码目录只读）。
3. **HTTPS 会话**：若站点为 HTTPS 且 Cookie 无法写入，可设置 `WARDROBE_SESSION_HTTPS_ONLY=1`（需反向代理正确传递 `X-Forwarded-Proto`，Uvicorn 默认会处理）。
4. **试用口令**：若设置了 `WARDROBE_TRIAL_CODE`，必须先访问 `/trial` 通过口令，再注册；且代码中 **SessionMiddleware 必须晚于 TrialGateMiddleware 注册**（当前仓库已按此顺序）。
5. **密钥**：生产环境务必设置 `WARDROBE_SECRET_KEY`（随机长字符串）。
