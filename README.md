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
