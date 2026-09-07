# 渲染

用于 `screenshot` 动作和 `pdf2epub` 的渲染阶段。

- 将 PDF 页面渲染为 `files/<resource>/output/images/page_XXXX.png`。
- 页面图片已存在时直接复用。
- 保持默认资源目录：如果输入位于 `files/<resource>/input/`，输出也写到同一资源的 `output/`。
- 严格尊重请求页码范围；`--all` 表示整本。
- 渲染后的图片文件名就是后续 OCR 和构建阶段使用的页面标识。
- 截图阶段应输出进度计数，并在结束时汇总新建页数、复用页数和耗时。
