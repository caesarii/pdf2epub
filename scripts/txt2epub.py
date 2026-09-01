#!/usr/bin/env python3
"""
纯文本转 EPUB 工具
- txt2epub: 纯文本 → HTML → EPUB
"""
import os
import sys
import argparse
import re
from ebooklib import epub


BOOK_CSS = """
p {
  text-indent: 2em;
  margin: 0.35em 0;
  line-height: 1.85;
}
h1, h2, h3, h4, h5, h6 {
  text-indent: 0;
}
.chapter-title {
  break-before: page;
  page-break-before: always;
  margin-top: 0;
  text-align: center;
  font-size: 1.3em;
  font-weight: bold;
  margin: 1.5em 0 1em;
}
.chapter-title:first-child {
  break-before: auto;
  page-break-before: auto;
}
body {
  font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif;
  max-width: 800px;
  margin: 0 auto;
  padding: 1em;
}
""".strip()


def parse_txt_to_chapters(txt_path: str) -> list[tuple[str, str]]:
    """解析纯文本，返回 (章节标题, 内容) 列表"""
    with open(txt_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 章节标题模式：第X章 ...
    chapter_pattern = re.compile(r'^第[一二三四五六七八九十百零〇\d]+章\s+.+$', re.MULTILINE)

    # 找到所有章节位置
    chapter_matches = list(chapter_pattern.finditer(content))

    if not chapter_matches:
        # 没有章节，整个文件作为一章
        return [("正文", content)]

    chapters = []
    for i, match in enumerate(chapter_matches):
        title = match.group(0).strip()
        start = match.start()
        end = chapter_matches[i + 1].start() if i + 1 < len(chapter_matches) else len(content)
        chapter_content = content[start:end].strip()
        chapters.append((title, chapter_content))

    return chapters


def text_to_html(text: str) -> str:
    """将纯文本转换为 HTML 段落"""
    lines = text.splitlines()
    html_lines = []
    for line in lines:
        line = line.strip()
        if line:
            # 转义 HTML 特殊字符
            line = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            html_lines.append(f"<p>{line}</p>")
        else:
            html_lines.append("<p></p>")
    return "\n".join(html_lines)


def txt2epub(txt_path: str, title: str | None = None, author: str | None = None, output: str | None = None) -> str:
    """纯文本转 EPUB"""
    title = title or os.path.splitext(os.path.basename(txt_path))[0]
    output = output or os.path.join(os.path.dirname(txt_path), f"{title}.epub")

    chapters = parse_txt_to_chapters(txt_path)

    book = epub.EpubBook()
    book.set_identifier(f"id_{title}")
    book.set_title(title)
    book.set_language("zh")
    if author:
        book.add_author(author)

    # 添加样式
    style = epub.EpubItem(
        uid="book-style",
        file_name="style/book.css",
        media_type="text/css",
        content=BOOK_CSS,
    )
    book.add_item(style)

    # 添加章节
    epub_chapters = []
    toc_links = []

    for i, (chapter_title, chapter_text) in enumerate(chapters, start=1):
        # 移除标题行（已在 h1 中显示）
        lines = chapter_text.splitlines()
        if lines and re.match(r'^第[一二三四五六七八九十百零〇\d]+章', lines[0]):
            content_text = "\n".join(lines[1:]).strip()
        else:
            content_text = chapter_text

        html_content = text_to_html(content_text)
        chapter_html = f'<h1 class="chapter-title">{chapter_title}</h1>\n{html_content}'

        file_name = f"chapter_{i:03d}.xhtml"
        epub_chapter = epub.EpubHtml(title=chapter_title, file_name=file_name, lang="zh")
        epub_chapter.content = f"<html><body>{chapter_html}</body></html>"
        epub_chapter.add_item(style)
        book.add_item(epub_chapter)
        epub_chapters.append(epub_chapter)
        toc_links.append(epub.Link(file_name, chapter_title, file_name))

    # 目录
    book.toc = toc_links
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    # 书脊
    book.spine = epub_chapters

    # 写入 EPUB
    epub.write_epub(output, book)
    print(f"[+] EPUB 已生成: {output}")
    return output


def main():
    parser = argparse.ArgumentParser(description="纯文本转 EPUB")
    parser.add_argument("input_txt", help="输入文本文件")
    parser.add_argument("--title", help="书名（默认使用文件名）")
    parser.add_argument("--author", help="作者")
    parser.add_argument("--output", "-o", help="输出 EPUB 路径")

    args = parser.parse_args()
    txt2epub(args.input_txt, title=args.title, author=args.author, output=args.output)


if __name__ == "__main__":
    main()
