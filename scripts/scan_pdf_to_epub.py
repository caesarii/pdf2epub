#!/usr/bin/env python3
"""
扫描版 PDF 转 EPUB 工具
- screenshot: PDF 页面 → PNG 截图（增量）
- pdf2epub:   PDF 页面截图 → HTML OCR → EPUB
- build:      页面图片 → HTML OCR → EPUB
- mobi2epub:  MOBI → EPUB

OCR 优先使用 Zode 中转 Kimi（读取 ./files/.env 中的 key），保留 Claude 和外部回调作为兼容模式。
"""
import os
import sys
import argparse
import base64
import glob
import json
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser

import fitz
from ebooklib import epub
from dotenv import load_dotenv

load_dotenv()
load_dotenv(os.path.join(os.getcwd(), "files", ".env"), override=False)
load_dotenv(os.path.join(os.getcwd(), "output", ".env"), override=False)

import requests

KIMI_ENDPOINT = "https://zode.qa.qima-inc.com/api/proxy/forward/chat/completions"
KIMI_MODEL = "kimi-k2.5"
FILES_DIR = "files"

OCR_PROMPT = """请仔细识别这张扫描页面的内容，输出可直接放入 EPUB 正文的 HTML 片段。要求：
1. 只输出 <h1>、<h2>、<h3>、<p>、<blockquote>、<ul>、<ol>、<li>、<table>、<thead>、<tbody>、<tr>、<th>、<td>、<sup>、<sub>、<em>、<strong>、<br> 等正文片段标签
2. 不要输出 <html>、<head>、<body>、``` 代码围栏或任何解释说明
3. 保持原文段落、标题层级、表格、列表、注释角标、公式和重要排版关系
4. 页码、页眉页脚、水印和明显扫描噪声不要输出
5. 如果页面包含图题（例如"图x—y ..."），保留图题为独立 <p>，不要尝试复原图内复杂图形
6. 尽量保留原书语义，不要求复刻全部视觉细节
7. 区分原文中的不同字体：中文正文与楷体/仿宋等特殊字体用不同标签区分（如楷体用 <blockquote> 或 <span class="kaiti">，仿宋用 <span class="fangsong">），英文和数字用 <span class="latin"> 包裹以与中文字体区分
8. 如果页面是封面、封底、纯插图等几乎无文字的纯图页面，只输出单个空 <p></p> 即可"""


def _resource_name(path: str) -> str:
    base = os.path.basename(os.path.normpath(path))
    name, ext = os.path.splitext(base)
    return name if ext else base


def _resource_dir(input_path: str) -> str:
    parent = os.path.basename(os.path.dirname(os.path.normpath(input_path)))
    resource_dir = os.path.dirname(os.path.dirname(os.path.normpath(input_path)))
    if parent == "input" and os.path.basename(os.path.dirname(resource_dir)) == FILES_DIR:
        return resource_dir
    return os.path.join(FILES_DIR, _resource_name(input_path))


def _default_output_dir(input_path: str) -> str:
    return os.path.join(_resource_dir(input_path), "output")


def _image_to_data_url(path: str) -> str:
    with open(path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode("ascii")
    ext = os.path.splitext(path)[1].lower()
    media = {".png": "image/png", ".jpg": "image/jpeg",
             ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(ext, "image/png")
    return f"data:{media};base64,{img_b64}"


def _extract_chat_content(data: dict) -> str:
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Kimi 响应格式异常: {data}") from exc
    if isinstance(content, list):
        text = "\n".join(item.get("text", "") for item in content if isinstance(item, dict)).strip()
    else:
        text = str(content).strip()
    # Zode proxy 返回的 UTF-8 中文字符串被错误编码为 latin-1，需要转回
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def _extract_chat_content_from_sse(text: str) -> str:
    parts = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line.removeprefix("data:").strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        choice = (data.get("choices") or [{}])[0]
        delta = choice.get("delta") or {}
        message = choice.get("message") or {}
        content = delta.get("content") or message.get("content")
        if content:
            parts.append(str(content))
    return "".join(parts).strip()


def _kimi_ocr(image_paths: list[str], model: str = KIMI_MODEL) -> str:
    """通过 Zode 中转调用 Kimi 识别图片中的文字。"""
    zode_key = os.getenv("ZODE_KEY") or os.getenv("ZODE_API_KEY") or os.getenv("key")
    if not zode_key:
        raise RuntimeError("未找到 Zode Key，请在 ./files/.env 中配置 key=... 或设置 ZODE_KEY")

    content = [{"type": "text", "text": OCR_PROMPT}]
    for path in image_paths:
        content.append({"type": "image_url", "image_url": {"url": _image_to_data_url(path)}})

    response = requests.post(
        KIMI_ENDPOINT,
        headers={
            "Authorization": f"Bearer {zode_key}",
            "Content-Type": "application/json",
        },
        json={"model": model, "messages": [{"role": "user", "content": content}]},
        timeout=180,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Kimi OCR 请求失败: HTTP {response.status_code} {response.text}")
    response.encoding = "utf-8"
    try:
        return _extract_chat_content(response.json())
    except requests.JSONDecodeError:
        # SSE 流式响应：用原始字节 UTF-8 解码，避免 requests 的自动编码检测错误
        content = _extract_chat_content_from_sse(response.content.decode("utf-8"))
        if content:
            return content
        raise RuntimeError(f"Kimi 响应格式异常: {response.text[:500]}")


def _claude_ocr(image_paths: list[str], model: str) -> str:
    """使用 Claude API 识别图片中的文字。"""
    import anthropic
    client = anthropic.Anthropic()
    content = [{"type": "text", "text": OCR_PROMPT}]
    for path in image_paths:
        with open(path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode("ascii")
        ext = os.path.splitext(path)[1].lower()
        media = {".png": "image/png", ".jpg": "image/jpeg",
                 ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(ext, "image/png")
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": media, "data": img_b64},
        })
    resp = client.messages.create(model=model, max_tokens=4096,
                                   messages=[{"role": "user", "content": content}])
    return resp.content[0].text


# OCR 回调，由外部注入或内置模型使用
OCR_CALLBACK = None

def register_ocr_callback(fn):
    """注册 OCR 回调。
    fn 签名: fn(image_paths: list[str]) -> str
    """
    global OCR_CALLBACK
    OCR_CALLBACK = fn


def render_pages_to_images(pdf_path: str, start: int, end: int, image_dir: str) -> list[str]:
    doc = fitz.open(pdf_path)
    os.makedirs(image_dir, exist_ok=True)
    paths = []
    for page_num in range(start, end):
        path = os.path.join(image_dir, f"page_{page_num + 1:04d}.png")
        if not os.path.exists(path):
            page = doc.load_page(page_num)
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            pix.save(path)
            print(f"[+] 已截图: {path}")
        else:
            print(f"[+] 跳过（已存在）: {path}")
        paths.append(path)
    doc.close()
    return paths


def _get_pdf_total_pages(pdf_path: str) -> int:
    doc = fitz.open(pdf_path)
    total = len(doc)
    doc.close()
    return total


def _resolve_screenshot_range(total: int, start_page: int | None, end_page: int | None, all_pages: bool) -> tuple[int, int]:
    if all_pages and (start_page is not None or end_page is not None):
        raise ValueError("--all 不能和 --start / --end 同时使用")

    start = 0 if all_pages or start_page is None else start_page - 1
    if start < 0:
        start = 0
    if start >= total:
        raise ValueError(f"起始页超出范围：PDF 共 {total} 页")

    if all_pages:
        end = total
    elif end_page is not None:
        end = end_page
    else:
        end = start + 1

    if end > total:
        end = total
    if end <= start:
        raise ValueError(f"结束页必须大于等于起始页：start={start + 1}, end={end}")
    return start, end


def _render_latex_expression(expr: str) -> str:
    expr = expr.strip()
    expr = expr.replace(r"\,", " ")
    expr = expr.replace(r"\%", "%")
    expr = expr.replace(r"\$", "$")
    expr = re.sub(
        r"\\frac\{([^{}]+)\}\{([^{}]+)\}",
        r'<span class="frac"><span class="num">\1</span><span class="den">\2</span></span>',
        expr,
    )
    return expr


def _render_math_blocks(html_content: str) -> str:
    def repl(match: re.Match) -> str:
        rendered = _render_latex_expression(match.group(1))
        return f'<div class="math">{rendered}</div>'

    html_content = re.sub(r"<p>\$\$(.*?)\$\$</p>", repl, html_content, flags=re.S)
    return re.sub(
        r"\$(\\frac\{.*?\}.*?[^\\])\$",
        lambda match: f'<span class="inline-math">{_render_latex_expression(match.group(1))}</span>',
        html_content,
    )


class LatinTextWrapper(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.parts = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        attr_text = "".join(f' {name}="{value}"' if value is not None else f" {name}" for name, value in attrs)
        self.parts.append(f"<{tag}{attr_text}>")
        if tag in {"script", "style"}:
            self.skip_depth += 1

    def handle_endtag(self, tag):
        self.parts.append(f"</{tag}>")
        if tag in {"script", "style"} and self.skip_depth:
            self.skip_depth -= 1

    def handle_startendtag(self, tag, attrs):
        attr_text = "".join(f' {name}="{value}"' if value is not None else f" {name}" for name, value in attrs)
        self.parts.append(f"<{tag}{attr_text}/>")

    def handle_data(self, data):
        if self.skip_depth:
            self.parts.append(data)
            return
        self.parts.append(re.sub(r"[A-Za-z0-9$][A-Za-z0-9.,:%+\-/$\s]*[A-Za-z0-9%]", r'<span class="latin">\g<0></span>', data))

    def handle_entityref(self, name):
        self.parts.append(f"&{name};")

    def handle_charref(self, name):
        self.parts.append(f"&#{name};")

    def get_html(self) -> str:
        return "".join(self.parts)


def _wrap_latin_text(html_content: str) -> str:
    parser = LatinTextWrapper()
    parser.feed(html_content)
    return parser.get_html()


def _wrap_tables(html_content: str) -> str:
    return re.sub(r"(<table>.*?</table>)", r'<div class="table-wrap">\1</div>', html_content, flags=re.S)


def _mark_figure_paragraphs(html_content: str) -> str:
    return re.sub(r"<p>([^<\n][^<]*\n)?(<img [^>]+/>)</p>", r'<p class="figure">\1\2</p>', html_content)


def _mark_chapter_headings(html_content: str) -> str:
    def repl(match):
        anchor_id = match.group(1)
        tag = match.group(2)
        body = match.group(3)
        return f'<{tag} id="{anchor_id}" class="chapter-title">{body}</{tag}>'

    html_content = re.sub(
        r'<p><a id="([^"]+)" class="chapter-anchor"\s*(?:/>|></a>)</p>\s*<(h[1-6])>(.*?)</\2>',
        repl,
        html_content,
        flags=re.S,
    )
    # Mark all h1 as chapter-title for page breaks
    html_content = re.sub(
        r'<h1>(.*?)</h1>',
        r'<h1 class="chapter-title">\1</h1>',
        html_content,
        flags=re.S,
    )
    return html_content


FOOTNOTE_MARKS = "①②③④⑤⑥⑦⑧⑨⑩"


def _inside_html_tag(html_content: str, index: int) -> bool:
    return html_content.rfind("<", 0, index) > html_content.rfind(">", 0, index)


def _inside_element(html_content: str, index: int, tag: str, class_name: str | None = None) -> bool:
    start = html_content.rfind(f"<{tag}", 0, index)
    end = html_content.rfind(f"</{tag}>", 0, index)
    if start <= end:
        return False
    if class_name is None:
        return True
    tag_end = html_content.find(">", start, index)
    if tag_end == -1:
        return False
    return class_name in html_content[start:tag_end]


def _replace_last_footnote_ref(html_content: str, mark: str, replacement: str) -> tuple[str, bool]:
    candidates = []
    for match in re.finditer(re.escape(mark), html_content):
        index = match.start()
        if _inside_html_tag(html_content, index):
            continue
        if _inside_element(html_content, index, "sup", "footnote-ref"):
            continue
        if _inside_element(html_content, index, "p", "footnote"):
            continue
        start = index
        end = index + len(mark)
        if _inside_element(html_content, index, "sup"):
            sup_start = html_content.rfind("<sup", 0, index)
            sup_end = html_content.find("</sup>", index)
            if sup_start != -1 and sup_end != -1:
                start = sup_start
                end = sup_end + len("</sup>")
        candidates.append((start, end))
    if not candidates:
        return html_content, False
    start, end = candidates[-1]
    return html_content[:start] + replacement + html_content[end:], True


def _split_footnote_body(body: str) -> tuple[str, str]:
    match = re.search(r"(.*?——(?:译者|编者)注)(.+)$", body, re.S)
    if not match:
        return body.strip(), ""
    return match.group(1).strip(), match.group(2).strip()


def _strip_wrapping_note_span(body: str) -> str:
    body = body.strip()
    match = re.fullmatch(r'<span class="(?:kaiti|fangsong)">(.*?)</span>', body, re.S)
    if match:
        return match.group(1).strip()
    return body


def _append_chapter_footnote(chapter_html: str, note_index: int, mark: str, body: str, rest: str = "") -> str:
    ref_id = f"footnote-ref-{note_index:03d}"
    note_id = f"footnote-{note_index:03d}"
    ref_html = f'<sup class="footnote-ref"><a id="{ref_id}" href="#{note_id}">{mark}</a></sup>'
    chapter_html, replaced = _replace_last_footnote_ref(chapter_html, mark, ref_html)
    if not replaced:
        chapter_html += f"\n<p>{ref_html}</p>"
    if rest:
        chapter_html += f"\n<p>{rest}</p>"
    note_html = (
        f'<p class="endnote" id="{note_id}">'
        f'<a href="#{ref_id}" class="endnote-backref">{mark}</a> {body}'
        f'</p>'
    )
    if '<section class="chapter-endnotes">' in chapter_html:
        return chapter_html.replace("</section>", f"\n{note_html}\n</section>", 1)
    return chapter_html + f'\n<section class="chapter-endnotes"><h2>注释</h2>\n{note_html}\n</section>'


def _chapter_endnotes(html_content: str) -> str:
    note_pattern = re.compile(
        rf'<p(?:\s[^>]*)?>\s*(?:<span class="(?:kaiti|fangsong)">\s*)?(?:<sup(?:\s[^>]*)?>\s*)?([{FOOTNOTE_MARKS}])(?:\s*</sup>)?\s*(.*?)(?:\s*</span>)?\s*</p>',
        re.S,
    )
    chapter_pattern = re.compile(r'<h1\b[^>]*class="[^"]*chapter-title[^"]*"[^>]*>.*?</h1>', re.S)
    chapter_starts = [match.start() for match in chapter_pattern.finditer(html_content)]
    if not chapter_starts or chapter_starts[0] != 0:
        chapter_starts.insert(0, 0)
    chapter_starts.append(len(html_content))

    chapters = [html_content[chapter_starts[index]: chapter_starts[index + 1]] for index in range(len(chapter_starts) - 1)]
    note_index = 0
    processed_chapters: list[str] = []

    for chapter_html in chapters:
        while True:
            match = note_pattern.search(chapter_html)
            if not match:
                break
            note_index += 1
            mark = match.group(1)
            body, rest = _split_footnote_body(_strip_wrapping_note_span(match.group(2)))
            before = chapter_html[:match.start()]
            after = chapter_html[match.end():]
            chapter_html = before + after
            chapter_html = _append_chapter_footnote(chapter_html, note_index, mark, body, rest)
        processed_chapters.append(chapter_html)

    return "".join(processed_chapters)


def _figure_caption(line: str) -> str | None:
    text = line.strip()
    if re.match(r"^图\s*\d+[—-]\d+\s+\S", text):
        return text
    return None


def _is_blank_stamp_page(lines: list[str]) -> bool:
    text = "".join(re.sub(r"\s+", "", line) for line in lines)
    if "好学" in text and ("近知" in text or "近乎知" in text):
        return True
    return "空白页" in text and ("印章" in text or "无文字内容" in text or "无可见文字内容" in text)


def _epub_image_name(image_path: str) -> str:
    return f"images/{os.path.basename(image_path)}"


def _is_image_only_page(html: str) -> bool:
    """检测纯图页面（封面、封底等），OCR 结果几乎没有有效文字。"""
    text = re.sub(r"<[^>]+>", "", html).strip()
    text = re.sub(r"\s+", "", text)
    return len(text) == 0


def _looks_like_full_page_image(image_path: str) -> bool:
    """在 OCR 前检测封面、封底、纯插图等页面，避免无意义 OCR。"""
    try:
        doc = fitz.open(image_path)
        page = doc.load_page(0)
        pix = page.get_pixmap(matrix=fitz.Matrix(0.1, 0.1), alpha=False)
        doc.close()
    except Exception:
        return False

    samples = pix.samples
    components = pix.n
    if not samples or components < 3:
        return False

    total = pix.width * pix.height
    nonwhite = 0
    colorful = 0
    dark = 0
    for offset in range(0, len(samples), components):
        red, green, blue = samples[offset], samples[offset + 1], samples[offset + 2]
        if red < 245 or green < 245 or blue < 245:
            nonwhite += 1
        if max(red, green, blue) - min(red, green, blue) > 20:
            colorful += 1
        if red < 180 and green < 180 and blue < 180:
            dark += 1

    nonwhite_ratio = nonwhite / total
    colorful_ratio = colorful / total
    dark_ratio = dark / total
    return colorful_ratio > 0.03 or dark_ratio > 0.45


def _full_page_image_fragment(epub_name: str) -> str:
    return f'<div class="full-page-image"><img src="{epub_name}" alt="" /></div>'


def _ends_like_complete_sentence(html_fragment: str) -> bool:
    text = re.sub(r"<[^>]+>", "", html_fragment).strip()
    return bool(re.search(r"[。！？!?；;：:]$", text))


def _starts_like_standalone_block(html_fragment: str) -> bool:
    text = re.sub(r"<[^>]+>", "", html_fragment).strip()
    if not text:
        return True
    return bool(re.match(r"^(第[一二三四五六七八九十百零〇]+[章节]|前言|序言|导言|补论|附录|参考文献|索引|目录)$", text))


def _looks_like_footnote_paragraph(html_fragment: str) -> bool:
    return bool(re.match(rf"\s*(?:<span class=\"(?:kaiti|fangsong)\">\s*)?(?:<sup(?:\s[^>]*)?>\s*)?[{FOOTNOTE_MARKS}]", html_fragment, re.S))


def _merge_continued_paragraphs(page_fragments: list[tuple[int, str]]) -> str:
    html_parts: list[str] = []
    paragraph_pattern = re.compile(r"<p(\s[^>]*)?>(.*?)</p>", re.S)

    for _, fragment in page_fragments:
        fragment = fragment.strip()
        if not fragment:
            continue
        if not html_parts:
            html_parts.append(fragment)
            continue

        previous = html_parts[-1]
        previous_matches = list(paragraph_pattern.finditer(previous))
        previous_match = previous_matches[-1] if previous_matches else None
        current_match = re.match(r"^\s*<p(\s[^>]*)?>(.*?)</p>", fragment, re.S)
        if (
            previous_match
            and current_match
            and not (previous_match.group(1) or "").strip()
            and not (current_match.group(1) or "").strip()
            and not _ends_like_complete_sentence(previous_match.group(2))
            and not _starts_like_standalone_block(current_match.group(2))
            and not _looks_like_footnote_paragraph(previous_match.group(2))
            and not _looks_like_footnote_paragraph(current_match.group(2))
        ):
            merged_paragraph = f"<p>{previous_match.group(2).rstrip()}{current_match.group(2).lstrip()}</p>"
            html_parts[-1] = previous[: previous_match.start()] + merged_paragraph
            fragment = fragment[current_match.end():].strip()
            if fragment:
                html_parts[-1] += "\n" + fragment
        else:
            html_parts.append(fragment)

    return "\n\n".join(html_parts)


def _image_media_type(image_path: str) -> str:
    ext = os.path.splitext(image_path)[1].lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(ext, "image/png")


def _clean_ocr_html(html: str) -> str:
    html = html.strip()
    html = re.sub(r"^```(?:html)?\s*", "", html, flags=re.IGNORECASE)
    html = re.sub(r"\s*```$", "", html)
    html = re.sub(r"</?(?:html|head|body)[^>]*>", "", html, flags=re.IGNORECASE)
    html = re.sub(r"<meta[^>]*>", "", html, flags=re.IGNORECASE)
    html = re.sub(r"<title[^>]*>.*?</title>", "", html, flags=re.IGNORECASE | re.DOTALL)
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.IGNORECASE | re.DOTALL)
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.IGNORECASE | re.DOTALL)
    return html.strip()


def _ocr_image_to_html(image_path: str, model: str | None = None) -> str:
    html = _kimi_ocr([image_path], model=model or os.getenv("OCR_MODEL", KIMI_MODEL))
    return _clean_ocr_html(html)


def _ocr_cache_path(output_dir: str, image_path: str) -> str:
    cache_dir = os.path.join(output_dir, "ocr_html")
    return os.path.join(cache_dir, f"{os.path.splitext(os.path.basename(image_path))[0]}.html")


def _read_cached_ocr(output_dir: str, image_path: str) -> str | None:
    cache_path = _ocr_cache_path(output_dir, image_path)
    if not os.path.exists(cache_path):
        return None
    with open(cache_path, "r", encoding="utf-8") as f:
        return f.read()


def _write_cached_ocr(output_dir: str, image_path: str, html: str) -> None:
    cache_path = _ocr_cache_path(output_dir, image_path)
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        f.write(html)


def _ocr_uncached_pages(output_dir: str, image_paths: list[str], model: str | None = None, workers: int | None = None) -> None:
    pending = [path for path in image_paths if _read_cached_ocr(output_dir, path) is None]
    if not pending:
        return

    workers = workers or int(os.getenv("OCR_WORKERS", "8"))
    print(f"[*] 并发 OCR 待处理 {len(pending)} 页，workers={workers}")
    executor = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = {executor.submit(_ocr_image_to_html, path, model): path for path in pending}
        for future in as_completed(futures):
            path = futures[future]
            html = future.result()
            _write_cached_ocr(output_dir, path, html)
            print(f"[+] OCR 完成并缓存: {path}")
    except KeyboardInterrupt:
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    else:
        executor.shutdown(wait=True)


def _page_images(output_dir: str) -> list[str]:
    patterns = ["page_*.png", "page_*.jpg", "page_*.jpeg", "page_*.webp"]
    paths: list[str] = []
    for pattern in patterns:
        paths.extend(glob.glob(os.path.join(output_dir, "images", pattern)))
    return sorted(paths)


def _default_title_from_output_dir(output_dir: str) -> str:
    normalized = os.path.normpath(output_dir)
    base = os.path.basename(normalized)
    if base == "output":
        parent = os.path.basename(os.path.dirname(normalized))
        if parent:
            return parent
    return base


def build_html_epub_from_images(output_dir: str, title: str | None = None, author: str | None = None, output: str | None = None, model: str | None = None) -> str:
    image_paths = _page_images(output_dir)
    if not image_paths:
        print(f"[!] 未找到 images/page_*.png 图片")
        sys.exit(1)

    title = title or _default_title_from_output_dir(output_dir)
    output = output or os.path.join(output_dir, f"{title}.epub")
    os.makedirs(os.path.dirname(os.path.abspath(output)) or ".", exist_ok=True)

    page_fragments: list[tuple[int, str]] = []
    figure_images: list[tuple[str, str]] = []
    full_page_images: list[tuple[str, str]] = []
    ocr_needed_paths: list[str] = []
    full_page_fragments: dict[str, str] = {}

    for index, image_path in enumerate(image_paths, start=1):
        if _looks_like_full_page_image(image_path):
            epub_name = _epub_image_name(image_path)
            full_page_images.append((image_path, epub_name))
            full_page_fragments[image_path] = _full_page_image_fragment(epub_name)
            print(f"[-] 第 {index} 页: 纯图页面，保留原图，跳过 OCR")
            continue
        ocr_needed_paths.append(image_path)

    _ocr_uncached_pages(output_dir, ocr_needed_paths, model=model)

    for index, image_path in enumerate(image_paths, start=1):
        page_number = _page_number_from_image(image_path) or index
        if image_path in full_page_fragments:
            page_fragments.append((page_number, full_page_fragments[image_path]))
            continue

        fragment = _read_cached_ocr(output_dir, image_path)
        if fragment is None:
            print(f"[-] OCR 第 {index} 页: {image_path}")
            fragment = _ocr_image_to_html(image_path, model=model)
            _write_cached_ocr(output_dir, image_path, fragment)
        else:
            print(f"[+] OCR 缓存第 {index} 页: {image_path}")
        if not fragment:
            continue
        if _is_blank_stamp_page(fragment.splitlines()):
            continue

        if _is_image_only_page(fragment):
            epub_name = _epub_image_name(image_path)
            full_page_images.append((image_path, epub_name))
            fragment = _full_page_image_fragment(epub_name)
            print(f"    -> 纯图页面，保留原图")
        else:
            fragment = _inject_figure_images(fragment, page_number, output_dir, figure_images)
        page_fragments.append((page_number, fragment))

    if not page_fragments:
        print("[!] OCR 未生成可用正文")
        sys.exit(1)

    full_html = _merge_continued_paragraphs(page_fragments)
    full_html = _render_math_blocks(full_html)
    full_html = _wrap_latin_text(full_html)
    full_html = _wrap_tables(full_html)
    full_html = _mark_figure_paragraphs(full_html)
    full_html = _mark_chapter_headings(full_html)
    full_html = _chapter_endnotes(full_html)
    full_html = _add_heading_ids(full_html)

    book = epub.EpubBook()
    book.set_identifier(f"id_{title}")
    book.set_title(title)
    book.set_language("zh")
    if author:
        book.add_author(author)

    cover_path = os.path.join(output_dir, "cover.png")
    if os.path.exists(cover_path):
        with open(cover_path, "rb") as f:
            book.set_cover("cover.png", f.read())

    style = epub.EpubItem(
        uid="book-style",
        file_name="style/book.css",
        media_type="text/css",
        content=BOOK_CSS,
    )
    book.add_item(style)

    seen_figure_images = set()
    for image_path, epub_name in figure_images + full_page_images:
        if epub_name in seen_figure_images:
            continue
        seen_figure_images.add(epub_name)
        uid_prefix = "fullpage" if (image_path, epub_name) in full_page_images else "figure"
        with open(image_path, "rb") as f:
            book.add_item(epub.EpubItem(
                uid=f"{uid_prefix}-{os.path.splitext(os.path.basename(image_path))[0]}",
                file_name=epub_name,
                media_type=_image_media_type(image_path),
                content=f.read(),
            ))

    chapter = epub.EpubHtml(title=title, file_name="content.xhtml", lang="zh")
    chapter.content = f"<html><body>{full_html}</body></html>"
    chapter.add_item(style)
    book.add_item(chapter)

    toc = _toc_from_html(full_html)
    book.toc = toc or [epub.Link("content.xhtml", title, "content")]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = [chapter]
    epub.write_epub(output, book)
    print(f"[+] EPUB 已生成: {output}")
    return output


def _page_number_from_image(path: str) -> int | None:
    match = re.search(r"page_(\d+)", os.path.basename(path))
    return int(match.group(1)) if match else None


def _inject_figure_images(html: str, page_number: int, output_dir: str, figure_images: list[tuple[str, str]]) -> str:
    if not _figure_caption(html):
        return html
    image_path = os.path.join(output_dir, "images", f"page_{page_number:04d}.png")
    if not os.path.exists(image_path):
        return html
    epub_name = _epub_image_name(image_path)
    figure_images.append((image_path, epub_name))
    return re.sub(
        r"(<p[^>]*>\s*(?:图|表)\s*\d+[—\-－]\d+.*?</p>)",
        rf"\1\n<p><img src=\"{epub_name}\" alt=\"\" /></p>",
        html,
        count=1,
        flags=re.DOTALL,
    )


class _HeadingCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items: list[tuple[int, str, str]] = []
        self._current_level: int | None = None
        self._current_text: list[str] = []
        self._current_id: str | None = None
        self._counter = 0

    def handle_starttag(self, tag, attrs):
        if re.fullmatch(r"h[1-6]", tag):
            self._current_level = int(tag[1])
            self._current_text = []
            attr_map = dict(attrs)
            self._current_id = attr_map.get("id")

    def handle_data(self, data):
        if self._current_level is not None:
            self._current_text.append(data)

    def handle_endtag(self, tag):
        if self._current_level is not None and tag == f"h{self._current_level}":
            title = "".join(self._current_text).strip()
            if title:
                self._counter += 1
                anchor = self._current_id or f"heading-{self._counter:03d}"
                self.items.append((self._current_level, title, anchor))
            self._current_level = None
            self._current_text = []
            self._current_id = None


def _add_heading_ids(html: str) -> str:
    counter = 0

    def repl(match: re.Match) -> str:
        nonlocal counter
        tag = match.group(1)
        attrs = match.group(2) or ""
        body = match.group(3)
        if re.search(r"\sid=", attrs):
            return match.group(0)
        counter += 1
        return f'<{tag}{attrs} id="heading-{counter:03d}">{body}</{tag}>'

    return re.sub(r"<(h[1-6])([^>]*)>(.*?)</\1>", repl, html, flags=re.S | re.I)


def _toc_from_html(html: str) -> list[epub.Link]:
    collector = _HeadingCollector()
    collector.feed(html)
    return [epub.Link(f"content.xhtml#{anchor}", title, anchor) for _, title, anchor in collector.items]

BOOK_CSS = """
p {
  text-indent: 2em;
  margin: 0.35em 0;
  line-height: 1.85;
}
h1, h2, h3, h4, h5, h6 {
  text-indent: 0;
}
a.toc-link {
  color: inherit;
  text-decoration: none;
}
.toc-item {
  text-indent: 0;
  margin: 0.8em 0 0.25em;
  line-height: 1.45;
}
.toc-level-1 {
  font-size: 1.18em;
  font-weight: 700;
}
.toc-level-2 {
  font-size: 1.05em;
  font-weight: 650;
  margin-left: 1em;
}
.chapter-title {
  break-before: page;
  page-break-before: always;
  margin-top: 0;
}
.chapter-title:first-child {
  break-before: auto;
  page-break-before: auto;
}
blockquote {
  margin: 1.2em 2.4em;
  padding: 0.8em 1.2em;
  border-left: 0.18em dotted #666;
  border-right: 0.18em dotted #666;
  font-family: 'Kaiti SC', 'STKaiti', 'KaiTi', serif;
  font-weight: 600;
  line-height: 1.9;
}
blockquote p,
li p,
td p,
th p,
.table-wrap p,
.math,
.figure {
  text-indent: 0;
}
.footnote-ref {
  font-size: 0.72em;
  line-height: 0;
  vertical-align: super;
}
.footnote-ref a,
.endnote-backref {
  color: inherit;
  text-decoration: none;
}
.chapter-endnotes {
  break-before: page;
  page-break-before: always;
  margin-top: 1em;
}
.chapter-endnotes h2 {
  font-size: 1.12em;
  margin: 0 0 0.8em;
}
.endnote {
  text-indent: 0;
  font-size: 0.86em;
  line-height: 1.7;
  color: #4f4a43;
}
table {
  width: 100%;
  border-collapse: collapse;
  border-spacing: 0;
  margin: 0;
  font-family: 'Helvetica Neue', Arial, sans-serif;
  font-size: 0.72em;
  line-height: 1.5;
  table-layout: auto;
}
th, td {
  border: 1px solid #cfc8bb;
  padding: 0.38em 0.55em;
  vertical-align: top;
  word-break: normal;
  overflow-wrap: anywhere;
}
th {
  background: #efe7d8;
  color: #2f261f;
  font-weight: 700;
}
tbody tr:nth-child(even) td {
  background: #fbf8f1;
}
.table-wrap {
  margin: 1.1em 0;
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
}
.full-page-image {
  break-before: page;
  page-break-before: always;
  text-align: center;
  text-indent: 0;
  margin: 0;
  padding: 0;
}
.full-page-image img {
  max-width: 100%;
  height: auto;
  margin: 0;
}
img {
  display: block;
  max-width: 100%;
  height: auto;
  margin: 0.8em auto 1.2em;
}
.latin {
  font-family: 'Helvetica Neue', Arial, sans-serif;
  font-size: 0.88em;
}
.math {
  margin: 1em 0;
  text-align: center;
  font-family: Georgia, 'Times New Roman', serif;
  font-size: 0.9em;
  line-height: 1.8;
}
.inline-math {
  font-family: Georgia, 'Times New Roman', serif;
  font-size: 0.9em;
}
.frac {
  display: inline-block;
  vertical-align: middle;
  text-align: center;
  line-height: 1.15;
}
.frac .num {
  display: block;
  padding: 0 0.25em 0.08em;
  border-bottom: 1px solid currentColor;
}
.frac .den {
  display: block;
  padding: 0.08em 0.25em 0;
}
""".strip()


def cmd_screenshot(args):
    output_dir = args.output_dir or _default_output_dir(args.input_pdf)
    image_dir = os.path.join(output_dir, "images")
    total = _get_pdf_total_pages(args.input_pdf)
    try:
        start, end = _resolve_screenshot_range(total, args.start, args.end, args.all)
    except ValueError as exc:
        print(f"[!] {exc}")
        sys.exit(1)

    print(f"[*] PDF 共 {total} 页，本次截图第 {start + 1}-{end} 页")
    render_pages_to_images(args.input_pdf, start, end, image_dir)
    print(f"[*] 截图完成: {image_dir}")


def cmd_pdf2epub(args):
    output_dir = args.output_dir or _default_output_dir(args.input_pdf)
    image_dir = os.path.join(output_dir, "images")
    total = _get_pdf_total_pages(args.input_pdf)
    try:
        start, end = _resolve_screenshot_range(total, args.start, args.end, args.all)
    except ValueError as exc:
        print(f"[!] {exc}")
        sys.exit(1)

    print(f"[*] PDF 共 {total} 页，本次转换第 {start + 1}-{end} 页")
    render_pages_to_images(args.input_pdf, start, end, image_dir)
    build_html_epub_from_images(output_dir, title=args.title, author=args.author, output=args.output, model=args.model)


def cmd_build(args):
    build_html_epub_from_images(args.output_dir, title=args.title, author=args.author, output=args.output, model=args.model)


def cmd_mobi2epub(args):
    converter = shutil.which("ebook-convert")
    if not converter:
        raise RuntimeError("未找到 ebook-convert，请先安装 Calibre 并确保 ebook-convert 在 PATH 中")

    input_path = args.input_mobi
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"输入文件不存在: {input_path}")

    output = args.output
    if not output:
        output = os.path.join(_default_output_dir(input_path), f"{_resource_name(input_path)}.epub")

    os.makedirs(os.path.dirname(os.path.abspath(output)) or ".", exist_ok=True)
    cmd = [converter, input_path, output]
    print(f"[*] 转换 MOBI 为 EPUB: {input_path} -> {output}")
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"MOBI 转 EPUB 失败，退出码: {exc.returncode}") from exc
    print(f"[+] EPUB 已生成: {output}")


def main():
    parser = argparse.ArgumentParser(description="扫描版 PDF 转图片 EPUB")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_pdf2epub = sub.add_parser("pdf2epub", help="PDF → HTML OCR → EPUB")
    p_pdf2epub.add_argument("input_pdf")
    p_pdf2epub.add_argument("--output-dir", default=None, help="输出目录（默认 files/<资源名>/output）")
    p_pdf2epub.add_argument("--start", type=int, default=None, help="起始页（1-based；不指定 --end 时只转换该页）")
    p_pdf2epub.add_argument("--end", type=int, default=None, help="结束页（1-based；不指定 --start 时从第一页转换到该页）")
    p_pdf2epub.add_argument("--all", action="store_true", help="转换所有页面，不能和 --start / --end 同时使用")
    p_pdf2epub.add_argument("--title")
    p_pdf2epub.add_argument("--author")
    p_pdf2epub.add_argument("--output", "-o")
    p_pdf2epub.add_argument("--model", default=None, help="OCR 模型（默认 kimi-k2.5）")

    p_screenshot = sub.add_parser("screenshot", help="PDF → 页面截图（增量）")
    p_screenshot.add_argument("input_pdf")
    p_screenshot.add_argument("--output-dir", default=None, help="输出目录（默认 files/<资源名>/output）")
    p_screenshot.add_argument("--start", type=int, default=None, help="起始页（1-based；不指定 --end 时只截图该页）")
    p_screenshot.add_argument("--end", type=int, default=None, help="结束页（1-based；不指定 --start 时从第一页截图到该页）")
    p_screenshot.add_argument("--all", action="store_true", help="截图所有页面，不能和 --start / --end 同时使用")

    p_build = sub.add_parser("build", help="页面图片 → HTML OCR → EPUB")
    p_build.add_argument("output_dir")
    p_build.add_argument("--title")
    p_build.add_argument("--author")
    p_build.add_argument("--output", "-o")
    p_build.add_argument("--model", default=None, help="OCR 模型（默认 kimi-k2.5）")

    p_mobi2epub = sub.add_parser("mobi2epub", help="MOBI → EPUB")
    p_mobi2epub.add_argument("input_mobi")
    p_mobi2epub.add_argument("--output", "-o", help="输出 EPUB 路径（默认 files/<资源名>/output/<资源名>.epub）")

    args = parser.parse_args()
    if args.cmd == "pdf2epub":
        cmd_pdf2epub(args)
    elif args.cmd == "screenshot":
        cmd_screenshot(args)
    elif args.cmd == "build":
        cmd_build(args)
    else:
        cmd_mobi2epub(args)


if __name__ == "__main__":
    main()
