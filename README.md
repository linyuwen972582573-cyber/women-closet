# Wardrobe App（衣橱管理）

一个基于 **FastAPI + SQLite** 的衣橱管理网站（中文 UI，移动端友好，支持 PWA）。

> 说明：本仓库的代码与改动**全程由 Cursor 生成/协助生成**（AI Coding Assistant）。

## 功能

- **账号系统**：注册 / 登录 / 退出，数据按用户隔离
- **衣物管理**：新增 / 编辑 / 删除；字段包括：类别、风格、颜色、季节、材质、版型、品牌、价格、备注
- **图片上传**：保存到本地数据目录；页面可预览
- **图片识别（可选）**：使用 CLIP 推断类别/风格/季节/材质/版型 + 相似度向量（部署资源不足时可关闭）
- **每日穿搭建议**：根据衣橱里的衣服与当天需求（场景/风格/颜色/天气/备注）生成穿搭建议
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
- **识别结果都是 unknown**：
  - 如果你使用完整依赖（`requirements.txt`）且未设置 `WARDROBE_DISABLE_CLIP=1`，则会启用 CLIP 自动推断。
  - 如果你设置了 `WARDROBE_DISABLE_CLIP=1`（或环境内缺少 CLIP 相关依赖），识别会关闭，相关字段会显示 `unknown`，你仍可手动填写。

## 环境变量（配置项）

| 变量名 | 示例值 | 作用 |
|------|------|------|
| `WARDROBE_SECRET_KEY` | 随机长字符串 | 会话 Cookie 签名（生产必须设置） |
| `WARDROBE_DATA_DIR` | `wardrobe_app/data` 或 `/var/data` | 数据目录（SQLite + uploads） |
| `WARDROBE_DISABLE_CLIP` | `1` / `0` | `1` 关闭 CLIP（轻量部署）；`0` 启用识别 |
| `WARDROBE_TRIAL_CODE` | `123456` | 试用口令（不设则关闭） |
| `WARDROBE_SESSION_HTTPS_ONLY` | `1` | HTTPS 场景下强制 Secure Cookie（一般不需要） |
| `WARDROBE_DEBUG` | `1` | 开启后将把异常信息返回到页面（仅排错，排完务必关闭） |
