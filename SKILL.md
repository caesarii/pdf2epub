---
name: skilled-epub
description: Use when converting scanned PDFs to EPUB through an HTML OCR workflow: render PDF pages to screenshots, OCR screenshots into in-memory HTML fragments, and package searchable text plus selected figures into EPUB without Markdown files. Also use for MOBI to EPUB conversion and single-page screenshot operations in this repository.
---

# Skilled EPUB

扫描版 PDF 转 EPUB 的 HTML OCR 工作流：先按页渲染 PDF 截图，再将截图 OCR 为内存 HTML 片段，最后直接打包为文字+图片 EPUB。不再生成 Markdown 中间产物。也支持通过 Calibre 将 MOBI 转为 EPUB。

## 用法

```bash
# 一步转换：PDF → 页面截图 → HTML OCR → EPUB
python scripts/scan_pdf_to_epub.py pdf2epub files/书名/input/book.pdf --all
python scripts/scan_pdf_to_epub.py pdf2epub files/书名/input/book.pdf --start 5
python scripts/scan_pdf_to_epub.py pdf2epub files/书名/input/book.pdf --end 10
python scripts/scan_pdf_to_epub.py pdf2epub files/书名/input/book.pdf --start 5 --end 10

# 单独截图：已存在的 page_XXXX.png 会自动跳过
python scripts/scan_pdf_to_epub.py screenshot files/书名/input/book.pdf --start 1 --end 10
python scripts/scan_pdf_to_epub.py screenshot files/书名/input/book.pdf --all

# 从已有页面图片重新 OCR 并构建 EPUB
python scripts/scan_pdf_to_epub.py build files/书名/output --title "书名" [--author "作者"]

# MOBI → EPUB
python scripts/scan_pdf_to_epub.py mobi2epub files/书名/input/book.mobi [--output files/书名/output/book.epub]
```

## 工作流

1. `pdf2epub` 命令：按页渲染 PDF 到 `output/images/page_XXXX.png`
2. 对页面截图逐页 OCR，模型直接返回 HTML 片段，不落盘 Markdown
3. 将 HTML 片段合并、套用 EPUB 样式，并按图题插入对应页面图片
4. `build` 命令可跳过截图步骤，直接基于已有 `output/images/page_*.png` 重新 OCR 生成 EPUB

## 目录结构

```
files/书名/
  input/
    book.pdf
  output/
    images/
      page_0001.png    # 按需渲染的页面截图
      page_0002.png
      ...
    cover.png          # 可选封面
    书名.epub
```

## 规则

- 页面截图采用增量生成，已存在的 `page_XXXX.png` 会跳过
- 不指定 `--output-dir` 时，默认输出到 `files/<资源名>/output`
- 如果输入文件位于 `files/<资源名>/input/`，输出会自动落到同一资源目录的 `output/`
- PDF 转 EPUB 不再生成 `md/page_XXXX.md`
- HTML 只是内存中的中间表达，不会默认保存到文件
