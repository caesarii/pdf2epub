# skilled-epub

用于处理电子书格式转换，核心能力包括扫描版 PDF 转为文字+图片 EPUB、MOBI 转 EPUB。

## 功能特性
- PDF 转 EPUB：将 PDF 页面渲染为图片后 OCR，OCR 结果直接以 HTML 片段进入 EPUB，不再生成 Markdown 中间文件。
- EPUB 正文以可选中/可搜索的文字为主，遇到图题时按现有规则插入对应页面图片。
- `screenshot` 可单独将 PDF 指定页面截图到资源目录。
- `build` 可从已有页面截图重新 OCR 并构建 EPUB。
- MOBI 转 EPUB：通过 Calibre 将 MOBI 电子书转换为 EPUB 格式。
- 仓库根目录包含 `SKILL.md`，可被 open-claw 等支持 skills 的运行时使用。

## 命令行用法

### 目录约定

每个资源独立放在 `files/<资源名>/` 下：

```text
files/book/
  input/          # 原始 PDF/MOBI/EPUB 等输入文件
  output/         # 页面截图、EPUB 等输出
```

当不指定 `--output-dir` 时，`pdf2epub` / `screenshot` 会默认输出到 `files/<输入文件名>/output`；如果输入文件位于 `files/<资源名>/input/`，则默认输出到同一资源目录的 `output/`。

### `pdf2epub`

将 PDF 页面渲染为图片，逐页 OCR 为内存 HTML 片段，并直接打包成文字+图片 EPUB。

```bash
python scripts/scan_pdf_to_epub.py pdf2epub files/book/input/book.pdf --all
python scripts/scan_pdf_to_epub.py pdf2epub files/book/input/book.pdf --start 5
python scripts/scan_pdf_to_epub.py pdf2epub files/book/input/book.pdf --end 10
python scripts/scan_pdf_to_epub.py pdf2epub files/book/input/book.pdf --start 5 --end 10
```

### `screenshot`

将 PDF 指定页面截图到 `files/book/output/images/`，已存在的图片会跳过。

```bash
python scripts/scan_pdf_to_epub.py screenshot files/book/input/book.pdf --start 5
python scripts/scan_pdf_to_epub.py screenshot files/book/input/book.pdf --end 10
python scripts/scan_pdf_to_epub.py screenshot files/book/input/book.pdf --start 5 --end 10
python scripts/scan_pdf_to_epub.py screenshot files/book/input/book.pdf --all
```

### `build`

从 `files/book/output/images/page_*.png` 重新 OCR，并直接构建文字+图片 EPUB。

```bash
python scripts/scan_pdf_to_epub.py build files/book/output --title "书名"
```

### `mobi2epub`

将 MOBI 电子书转换为 EPUB。

```bash
python scripts/scan_pdf_to_epub.py mobi2epub files/book/input/book.mobi
python scripts/scan_pdf_to_epub.py mobi2epub files/book/input/book.mobi --output files/book/output/book.epub
```

## License

MIT，详见 [LICENSE](LICENSE)。
