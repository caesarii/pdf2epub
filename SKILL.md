---
name: pdf2epub
description: 以截图、OCR、构建和后处理流水线将扫描版 PDF 转成 EPUB；也支持把 MOBI 转成 EPUB。
metadata:
  short-description: 扫描版 PDF 转 EPUB
---

# PDF 转 EPUB

用于本仓库的电子书转换流程。优先使用 `scripts/scan_pdf_to_epub.py`，并保持 `files/<resource>/{input,output}` 目录结构。不生成 Markdown 中间文件。

## 动作

- `pdf2epub`: 完整 PDF 转 EPUB 流水线。阅读 [渲染](references/render.md)、[OCR](references/ocr.md)、[构建](references/build.md)、[后处理总览](references/postprocess.md)。
- `screenshot`: 只渲染指定 PDF 页面。阅读 [渲染](references/render.md)。
- `build`: 基于已有页面图片重新构建 EPUB。阅读 [构建](references/build.md) 和 [后处理总览](references/postprocess.md)。
- `mobi2epub`: 独立的 MOBI 转 EPUB 动作。阅读 [MOBI](references/mobi.md)。

## 常用命令

```bash
python scripts/scan_pdf_to_epub.py pdf2epub files/书名/input/book.pdf --all
python scripts/scan_pdf_to_epub.py screenshot files/书名/input/book.pdf --all
python scripts/scan_pdf_to_epub.py build files/书名/output --title "书名"
python scripts/scan_pdf_to_epub.py mobi2epub files/书名/input/book.mobi
```
