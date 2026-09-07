# 后处理总览

在 OCR 之后、最终打包 EPUB 之前使用。

推荐顺序：
1. [合并段落](references/postprocess/merge.md)
2. [处理数学、拉丁文本和表格](references/postprocess/layout.md)
3. [标记图像和图题](references/postprocess/media.md)
4. [标记章节标题和锚点](references/postprocess/headings.md)
5. [抽取章末注释](references/postprocess/footnotes.md)

这些规则共同负责把 OCR 输出整理成适合 EPUB 的正文结构，并生成目录、注释和图文关系。
