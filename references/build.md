# 构建

用于 `build` 动作和 `pdf2epub` 的 EPUB 装配阶段。

- 基于已有页面图片和 OCR 片段装配 EPUB。
- 将页面片段合并为正文，再补充样式、spine、导航和目录。
- 直接从 `files/<resource>/output/images/page_*.png` 重建，不重新渲染页面。
- 默认把 `output/` 当作资源的工作根目录，除非调用方指定其他输出路径。
- 构建阶段应汇总页面数、OCR 请求数、缓存命中数、纯图页、图题页、空白/印章页和耗时。
