# OCR

用于 `pdf2epub` 和 `build` 的 OCR 阶段。

- 将页面图片 OCR 为内存中的 HTML 片段。
- 不写 Markdown 中间文件。
- 优先使用 `scripts/scan_pdf_to_epub.py` 里的默认 OCR 路径。
- OCR 结果缓存到 `output/ocr_html/`，已有缓存时直接复用。
- 输出只保留正文片段，去掉 `html`、`head`、`body` 等外壳。
- OCR 缓存写入必须先写临时文件，再原子替换正式缓存。
- OCR 请求失败时按 `OCR_MAX_RETRIES` 和 `OCR_RETRY_BASE_DELAY` 重试。
- OCR 阶段应输出并发数、完成进度、成功页数、失败页数和耗时。
