---
name: skilled-epub
description: Use when converting scanned PDFs to EPUB through an incremental workflow: render PDF pages to screenshots, OCR screenshots to per-page Markdown with Kimi via Zode, review Markdown, and build EPUB output. Also use for MOBI to EPUB conversion and single-page screenshot/OCR operations in this repository.
---

# Skilled EPUB

扫描版 PDF 转 EPUB 的增量工作流：截图 PDF 页面、用 Kimi OCR 为逐页 Markdown、人工审核后打包 EPUB。也支持通过 Calibre 将 MOBI 转为 EPUB。

OCR 默认通过 Zode 中转调用 Kimi `kimi-k2.5`，Zode Key 从项目根目录 `./output/.env` 读取。

## 用法

```bash
# 先配置 Zode Key（二选一，推荐 key，与现有 ./output/.env 兼容）
echo 'key=zode_xxx' > ./output/.env

# 增量 OCR：缺少截图时会先自动截图，已存在的 md/page_XXXX.md 会自动跳过
python skills/skilled-epub/scripts/scan_pdf_to_epub.py ocr <input.pdf> --output-dir output/书名 --start 5
python skills/skilled-epub/scripts/scan_pdf_to_epub.py ocr <input.pdf> --output-dir output/书名 --end 10
python skills/skilled-epub/scripts/scan_pdf_to_epub.py ocr <input.pdf> --output-dir output/书名 --start 5 --end 10
python skills/skilled-epub/scripts/scan_pdf_to_epub.py ocr <input.pdf> --output-dir output/书名 --all

# 增量截图：已存在的 page_XXXX.png 会自动跳过
python skills/skilled-epub/scripts/scan_pdf_to_epub.py screenshot <input.pdf> --output-dir output/书名 --start 1 --end 10
python skills/skilled-epub/scripts/scan_pdf_to_epub.py screenshot <input.pdf> --output-dir output/书名 --start 11
python skills/skilled-epub/scripts/scan_pdf_to_epub.py screenshot <input.pdf> --output-dir output/书名 --end 10
python skills/skilled-epub/scripts/scan_pdf_to_epub.py screenshot <input.pdf> --output-dir output/书名 --all

# 单张图片 OCR：默认输出到 images 同级 md 目录
python skills/skilled-epub/scripts/scan_pdf_to_epub.py ocr-image output/书名/images/page_0005.png

# 阶段二：Markdown → EPUB
python skills/skilled-epub/scripts/scan_pdf_to_epub.py build <output_dir> [--title "书名"] [--author "作者"]

# MOBI → EPUB
python skills/skilled-epub/scripts/scan_pdf_to_epub.py mobi2epub <input.mobi> [--output output/book.epub]
```

## 工作流

1. `screenshot` 命令：按页渲染 PDF 截图到 `images/`，每次执行都会跳过已存在截图
2. `ocr` 命令：逐页识别截图为 `md/page_XXXX.md`，缺少截图时会先自动截图，已存在 Markdown 会跳过
3. 人工检查并编辑 `output_dir/md/*.md` 文件
4. `build` 命令：生成 `router.json`，并将所有 `md/page_*.md` 按顺序合并打包为带导航目录的 EPUB

## OCR 模式

### 内置 Kimi OCR（默认）

在 `./output/.env` 中配置任一变量即可：

```bash
key=zode_xxx
# 或
ZODE_KEY=zode_xxx
```

脚本会请求：

```text
POST https://zode.qa.qima-inc.com/api/proxy/forward/chat/completions
model: kimi-k2.5
```

可通过 `--model` 参数或 `OCR_MODEL` 环境变量指定模型，默认 `kimi-k2.5`。

### 内置 Claude API（后备）

设置 `ANTHROPIC_API_KEY` 环境变量：

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python skills/skilled-epub/scripts/scan_pdf_to_epub.py ocr book.pdf
```

只有未配置 Zode Key 时才会回退到 Claude。

### 外部回调（open-claw 集成）

```python
from scan_pdf_to_epub import register_ocr_callback

def my_ocr(image_paths: list[str]) -> str:
    """接收页面截图路径列表，返回识别的 Markdown 文本。"""
    ...

register_ocr_callback(my_ocr)
```

## 输出目录结构

```
output_dir/
  images/
    page_0001.png      # 按需渲染的截图
    ...
  md/
    page_0001.md       # 单页 OCR 结果
    page_0002.md
    ...
  cover.png
```
