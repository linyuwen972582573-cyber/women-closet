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

### 本地出现 `Internal Server Error`（尤其注册/登录）

1. **先关掉所有占用 8000 端口的旧进程**（多个 `uvicorn` 或僵尸进程会让浏览器打到错误实例，表现为注册 POST 返回 500）：
   ```powershell
   Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue |
     ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
   ```
   然后再启动：`uvicorn wardrobe_app.main:app --host 127.0.0.1 --port 8000 --reload`
2. 若仍异常，删除本地库后重试（会清空衣橱数据）：删除 `wardrobe_app\data\wardrobe.sqlite3`（及同目录下 `-wal` / `-shm` 若存在），重启服务后会自动建表。
3. 若设置了环境变量 `WARDROBE_TRIAL_CODE`，须先打开 **`http://127.0.0.1:8000/trial`** 输入口令，再注册。

## 说明（重要）

- **price/brand** 很难仅凭图片可靠识别（除非拍到吊牌/价签/Logo 并做 OCR/Logo 检测）。本 MVP 会把它们作为可编辑字段；图片分析阶段主要做：颜色 + 风格标签 + 图像相似度向量。
- 第一次运行会自动下载模型（可能较大，取决于网络环境）。

## 部署到 Render（推荐流程）

仓库根目录已包含 **`render.yaml`**（Blueprint）与 **`requirements-render.txt`**（不含 PyTorch，免费套餐可稳定构建；线上默认关闭 CLIP，避免内存与冷启动下载模型）。

### 方式 A：一键 Blueprint（推荐）

1. 登录 [Render](https://render.com)，**New → Blueprint**。
2. 连接 GitHub 仓库 **`linyuwen972582573-cyber/women-closet`**，分支选 **`main`**。
3. 确认 Render 读取到根目录的 **`render.yaml`**，应用名称可按需修改后 **Apply**。
4. 等待首次 **Build + Deploy** 完成（几分钟量级；若改用完整 `requirements.txt` 含 PyTorch，可能需十几分钟且易在免费实例 OOM）。
5. 打开 Render 给出的 **`https://你的服务.onrender.com`**，若未设置试用口令可直接注册；若 Blueprint 里自行加了 `WARDROBE_TRIAL_CODE`，需先访问 **`/trial`**。

### 方式 B：手动创建 Web Service

1. **New → Web Service**，连接同一 GitHub 仓库，`Branch` = `main`。
2. **Runtime**：Python 3.11。
3. **Build Command**：`pip install --upgrade pip && pip install -r requirements-render.txt`  
   （若要启用 CLIP，可改为 `pip install -r requirements.txt`，并把实例升级到更大内存，且不要设置 `WARDROBE_DISABLE_CLIP`。）
4. **Start Command**：`uvicorn wardrobe_app.main:app --host 0.0.0.0 --port $PORT --workers 1`  
   **必须** `--workers 1`，否则 SQLite 会锁库报错。
5. **Environment**（在 Dashboard → Environment）建议至少配置：

| 变量名 | 示例值 | 说明 |
|--------|--------|------|
| `WARDROBE_DATA_DIR` | `/tmp/wardrobe_data` | 数据库与上传目录放在可写临时目录（免费 Web 磁盘易失，重启后数据会清空，属 SQLite 演示预期）。 |
| `WARDROBE_SECRET_KEY` | 随机长字符串 | 会话 Cookie 签名；勿泄露。 |
| `WARDROBE_DISABLE_CLIP` | `1` | 与 `requirements-render.txt` 配套，跳过 CLIP。 |
| `WARDROBE_TRIAL_CODE` | （可选） | 设置后访客须先打开 `/trial` 输入口令。 |

6. **Health Check Path**：`/healthz`（可选，便于 Render 判断存活）。

### Render 与 SQLite 注意事项

- **免费实例休眠**：一段时间无访问会睡眠，首次唤醒较慢；SQLite 数据在 **`/tmp`** 时，**每次休眠/重部署可能清空**，仅适合演示。正式持久化需改用 Render **PostgreSQL** 等（需改代码，本 MVP 未内置）。
- **Cookie / HTTPS**：若登录态异常，可尝试增加环境变量 `WARDROBE_SESSION_HTTPS_ONLY=1`。
- **试用口令**：若设置了 `WARDROBE_TRIAL_CODE`，须先访问 **`/trial`** 再注册。

## 部署（通用）与注册 500 排查

本应用默认使用 **SQLite**（`wardrobe_app/data/` 或环境变量 `WARDROBE_DATA_DIR` 指定目录）。

1. **进程数必须为 1**：多个 worker 会同时写同一 SQLite 文件。启动命令务必带 **`--workers 1`**（见上文）。
2. **可写数据目录**：生产/Render 推荐 `WARDROBE_DATA_DIR=/tmp/wardrobe_data`。
3. **HTTPS 会话**：见上表 `WARDROBE_SESSION_HTTPS_ONLY`。
4. **试用口令**：须先 `/trial`；本仓库已保证 **SessionMiddleware 晚于 TrialGateMiddleware** 注册。
5. **密钥**：务必设置 **`WARDROBE_SECRET_KEY`**。
