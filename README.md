# Wardrobe App（衣橱管理）

一个基于 **FastAPI + SQLite** 的衣橱管理网站（中文 UI，移动端友好，支持 PWA）。

## 功能

- **账号系统**：注册 / 登录 / 退出，数据按用户隔离
- **衣物管理**：新增 / 编辑 / 删除；字段包括：类别、风格、颜色、季节、材质、版型、品牌、价格、备注
- **图片上传**：保存到本地数据目录；页面可预览
- **图片识别（可选）**：使用 CLIP 推断类别/风格/季节/材质/版型 + 相似度向量（部署资源不足时可关闭）
- **试用口令（可选）**：设置口令后，访客需先通过 `/trial`

## 快速开始（本地 Windows / PowerShell）

在仓库根目录执行：

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn wardrobe_app.main:app --reload
```

打开：`http://127.0.0.1:8000`

### 本地常见问题

- **注册/登录出现 500**：
  - 先清理 8000 端口占用，再启动服务：
    ```powershell
    Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue |
      ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
    ```
    然后执行：`uvicorn wardrobe_app.main:app --host 127.0.0.1 --port 8000 --reload`
  - 仍异常可删除本地库重试（会清空数据）：`wardrobe_app\data\wardrobe.sqlite3`（以及 `-wal` / `-shm`）
- **识别结果都是 unknown**：说明 CLIP 被关闭（详见下文 `WARDROBE_DISABLE_CLIP` 与 `requirements-render.txt`）

## 环境变量（配置项）

| 变量名 | 示例值 | 作用 |
|------|------|------|
| `WARDROBE_SECRET_KEY` | 随机长字符串 | 会话 Cookie 签名（生产必须设置） |
| `WARDROBE_DATA_DIR` | `wardrobe_app/data` 或 `/var/data` | 数据目录（SQLite + uploads） |
| `WARDROBE_DISABLE_CLIP` | `1` / `0` | `1` 关闭 CLIP（轻量部署）；`0` 启用识别 |
| `WARDROBE_TRIAL_CODE` | `123456` | 试用口令（不设则关闭） |
| `WARDROBE_SESSION_HTTPS_ONLY` | `1` | HTTPS 场景下强制 Secure Cookie（一般不需要） |
| `WARDROBE_DEBUG` | `1` | 开启后将把异常信息返回到页面（仅排错，排完务必关闭） |

## 部署到 Render（推荐）

仓库根目录已包含：
- **`render.yaml`**：Blueprint（可一键创建服务）
- **`requirements-render.txt`**：轻量依赖（不含 PyTorch），适合免费实例
- **`runtime.txt`**：固定 Python 到 `python-3.11.9`（避免过新 Python 导致模板/Jinja2 异常）

### 方式 A：Blueprint（推荐）

1. Render：**New → Blueprint**
2. 连接 GitHub 仓库并 Apply
3. 等 Build/Deploy 完成，打开 `https://xxx.onrender.com`

### 方式 B：手动 Web Service（照抄配置）

- **Build Command**
  - 轻量（免费稳定）：`pip install --upgrade pip && pip install -r requirements-render.txt`
  - 启用 CLIP（更慢更吃内存）：`pip install --upgrade pip && pip install -r requirements.txt`
- **Start Command**（SQLite 必须单进程）
  - `uvicorn wardrobe_app.main:app --host 0.0.0.0 --port $PORT --workers 1`
- **Environment（建议最少 3 个）**
  - `WARDROBE_SECRET_KEY`：生成随机值
  - `WARDROBE_DATA_DIR`：`/tmp/wardrobe_data`（演示）或持久盘挂载目录（见下一节）
  - `WARDROBE_DISABLE_CLIP=1`（使用轻量依赖时）

### Render 数据持久化（强烈推荐）

如果你把 `WARDROBE_DATA_DIR` 指到 `/tmp/...`，服务休眠/重启后**账号与数据会丢**。解决方法：

1. Render 服务里添加 **Persistent Disk**
2. 例如挂载到 `/var/data`
3. 把环境变量改为：`WARDROBE_DATA_DIR=/var/data`
4. 重新部署

## 生产部署建议（云服务器）

如果你希望 **不休眠、数据长期保存、可自定义域名**，推荐用云服务器（VPS）部署，并把 `WARDROBE_DATA_DIR` 指到服务器磁盘目录（例如 `/var/lib/wardrobe_data`）。
