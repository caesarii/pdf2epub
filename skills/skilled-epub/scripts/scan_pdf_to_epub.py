#!/usr/bin/env python3
"""
扫描版 PDF 转 EPUB 工具
- screenshot: PDF 页面 → PNG 截图（增量）
- ocr:        PDF 页面截图 → 逐页 Markdown（增量，缺截图时自动补图）
- ocr-image:  单张图片 → Markdown
- build:      Markdown → EPUB

OCR 优先使用 Zode 中转 Kimi（读取 ./output/.env 中的 key），保留 Claude 和外部回调作为兼容模式。
"""
import os
import sys
import argparse
import base64
import glob
import json
import re
import time
from html.parser import HTMLParser

import fitz
from ebooklib import epub
from dotenv import load_dotenv

load_dotenv()
load_dotenv(os.path.join(os.getcwd(), "output", ".env"), override=False)

import requests

KIMI_ENDPOINT = "https://zode.qa.qima-inc.com/api/proxy/forward/chat/completions"
KIMI_MODEL = "kimi-k2.5"

OCR_PROMPT = """请仔细识别这张扫描页面的所有文字内容，输出为 Markdown 格式。要求：
1. 保持原文的段落结构和层级关系
2. 章节标题用 # ## ### 标记
3. 表格用 Markdown 表格语法
4. 公式保持原样输出
5. 页码不输出
6. 只输出识别到的文字，不要添加任何解释说明"""


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
        raise RuntimeError("未找到 Zode Key，请在 ./output/.env 中配置 key=... 或设置 ZODE_KEY")

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


def default_md_path_for_image(image_path: str) -> str:
    image_dir = os.path.dirname(image_path)
    output_dir = os.path.dirname(image_dir)
    md_dir = os.path.join(output_dir, "md")
    base = os.path.splitext(os.path.basename(image_path))[0] + ".md"
    return os.path.join(md_dir, base)


def cmd_ocr_image(args):
    ocr_image_to_md(args.image, args.output, model=args.model)


def ocr_image_to_md(image_path: str, output: str | None = None, model: str | None = None) -> str:
    output = output or default_md_path_for_image(image_path)
    os.makedirs(os.path.dirname(output), exist_ok=True)
    md_text = _kimi_ocr([image_path], model=model or os.getenv("OCR_MODEL", KIMI_MODEL))
    with open(output, "w", encoding="utf-8") as f:
        f.write(md_text)
        if not md_text.endswith("\n"):
            f.write("\n")
    print(f"[+] 已输出: {output}")
    return output


def cmd_screenshot(args):
    output_dir = args.output_dir or os.path.splitext(os.path.basename(args.input_pdf))[0]
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


def cmd_ocr(args):
    output_dir = args.output_dir or os.path.splitext(os.path.basename(args.input_pdf))[0]
    os.makedirs(output_dir, exist_ok=True)
    image_dir = os.path.join(output_dir, "images")
    md_dir = os.path.join(output_dir, "md")
    os.makedirs(image_dir, exist_ok=True)
    os.makedirs(md_dir, exist_ok=True)

    total = _get_pdf_total_pages(args.input_pdf)
    try:
        start, end = _resolve_screenshot_range(total, args.start, args.end, args.all)
    except ValueError as exc:
        print(f"[!] {exc}")
        sys.exit(1)

    print(f"[*] PDF 共 {total} 页，本次 OCR 第 {start + 1}-{end} 页")

    for page_num in range(start, end):
        image_path = os.path.join(image_dir, f"page_{page_num + 1:04d}.png")
        md_path = os.path.join(md_dir, f"page_{page_num + 1:04d}.md")
        if os.path.exists(md_path):
            print(f"[+] 跳过（已存在）: {md_path}")
            continue
        if not os.path.exists(image_path):
            print(f"[-] 缺少截图，先生成第 {page_num + 1} 页截图...")
            render_pages_to_images(args.input_pdf, page_num, page_num + 1, image_dir)
        print(f"[-] OCR 第 {page_num + 1} 页...")
        ocr_image_to_md(image_path, md_path, model=args.model)

    print(f"\n[*] 本次 OCR 完成。请检查 {md_dir}/*.md，然后运行 build 命令。")


def _md_files(output_dir: str) -> list[str]:
    return sorted(glob.glob(os.path.join(output_dir, "md", "page_*.md")))


def _page_number_from_md(path: str) -> int:
    return int(os.path.splitext(os.path.basename(path))[0].split("_")[1])


def _clean_heading(line: str) -> tuple[int, str] | None:
    match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
    if not match:
        return None
    title = match.group(2).strip().strip("*_` ")
    return len(match.group(1)), title


def _is_router_title(title: str) -> bool:
    title = title.strip()
    if re.search(r"\s\d{1,3}$", title):
        return False
    if re.fullmatch(r"附录\s*\d+", title):
        return False
    patterns = [
        r"^推荐序$",
        r"^中文版序$",
        r"^序言$",
        r"^第\s*\d+\s*章\b",
        r"^附录\s*\d+\b",
        r"^译后记$",
    ]
    return any(re.search(pattern, title) for pattern in patterns)


def _router_key(title: str) -> str:
    title = re.sub(r"<[^>]+>", "", title)
    title = title.replace("｜", "|")
    title = re.sub(r"^\|\s*", "", title)
    title = re.sub(r"\s*\|\s*", "|", title)
    title = title.replace("|", "")
    title = re.sub(r"\s+", "", title)
    title = title.replace("NO", "NO")
    return title


def _append_title_continuation(title: str, extra: str) -> str:
    if not extra:
        return title
    max_overlap = min(len(title), len(extra))
    for size in range(max_overlap, 0, -1):
        if title.endswith(extra[:size]):
            return title + extra[size:]
    return title + extra


def _normalize_toc_title(title: str, next_line: str = "") -> str | None:
    title = title.strip().strip("*_` ")
    if not title or title.startswith("目录"):
        return None
    title = re.sub(r"^\|\s*", "", title)
    title = re.sub(r"\s*\|\s*", " | ", title)
    title = re.sub(r"\s+", " ", title).strip()
    title = re.sub(r"\s+\d{1,3}$", "", title).strip()
    if next_line and not _clean_heading(next_line):
        extra = re.sub(r"\s+\d{1,3}$", "", next_line.strip()).strip()
        if extra and len(extra) <= 12 and not re.search(r"[。！？；：]", extra):
            title = _append_title_continuation(title, extra)
    return title


def _toc_display_title(title: str) -> str:
    title = re.sub(r"\s+\d{1,3}$", "", title.strip()).strip()
    return title


def _extract_original_toc(output_dir: str) -> list[dict]:
    toc_entries = []
    in_toc = False
    toc_anchor_added = False
    for path in _md_files(output_dir):
        page = _page_number_from_md(path)
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        toc_heading_index = next(
            (index for index, line in enumerate(lines[:3]) if line.strip().lstrip("# ").strip().startswith("目录")),
            None,
        )
        if toc_heading_index is not None:
            in_toc = True
        if not in_toc:
            continue
        if toc_heading_index is not None and not toc_anchor_added:
            toc_entries.append({
                "title": "目录",
                "level": 1,
                "toc_page": page,
                "toc_line": toc_heading_index,
                "page": page,
                "line": toc_heading_index,
                "is_toc": True,
            })
            toc_anchor_added = True
        for index, line in enumerate(lines):
            heading = _clean_heading(line)
            if not heading:
                continue
            level, title = heading
            normalized = _normalize_toc_title(title, lines[index + 1] if index + 1 < len(lines) else "")
            if not normalized:
                continue
            toc_entries.append({"title": normalized, "level": min(level, 2), "toc_page": page, "toc_line": index})
        if in_toc and page > 24:
            break
    return toc_entries


def _first_toc_page(output_dir: str) -> int | None:
    for path in _md_files(output_dir):
        page = _page_number_from_md(path)
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        if any(line.strip().lstrip("# ").strip().startswith("目录") for line in lines[:3]):
            return page
    return None


def _is_preface_router_title(title: str, level: int) -> bool:
    title = title.strip()
    if title.startswith("目录"):
        return False
    if level == 1:
        return True
    return title in {"编辑手记"}


def _extract_preface_entries(output_dir: str) -> list[dict]:
    first_toc_page = _first_toc_page(output_dir)
    if first_toc_page is None:
        return []
    entries = []
    for path in _md_files(output_dir):
        page = _page_number_from_md(path)
        if page >= first_toc_page:
            break
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        for index, line in enumerate(lines):
            heading = _clean_heading(line)
            if not heading:
                continue
            level, title = heading
            if not _is_preface_router_title(title, level):
                continue
            entries.append({
                "title": title,
                "page": page,
                "line": index,
                "level": min(level, 2),
                "is_preface": True,
            })
    return entries


def _toc_pages(output_dir: str) -> set[int]:
    return {entry["toc_page"] for entry in _extract_original_toc(output_dir)}


def _find_toc_target(output_dir: str, title: str) -> tuple[int, int, int] | None:
    target_key = _router_key(title)
    toc_pages = _toc_pages(output_dir)
    for path in _md_files(output_dir):
        page = _page_number_from_md(path)
        if page in toc_pages:
            continue
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        if any(line.strip().lstrip("# ").strip().startswith("目录") for line in lines[:3]):
            continue
        for line_index, line in enumerate(lines):
            heading = _clean_heading(line)
            if not heading:
                continue
            level, candidate = heading
            candidate_key = _router_key(candidate)
            if candidate_key == target_key or candidate_key.startswith(target_key) or target_key.startswith(candidate_key):
                return page, line_index, min(level, 2)
    return None


def _is_uncle_frank_heading(line: str) -> bool:
    text = re.sub(r"^[#>*\-\s]+", "", line.strip()).strip("*_` ")
    return bool(re.fullmatch(r"弗兰克叔叔[如是]*说……", text))


def _normalize_uncle_frank_blocks(lines: list[str]) -> list[str]:
    normalized = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith(">") or not _is_uncle_frank_heading(line):
            normalized.append(line)
            i += 1
            continue

        heading = re.sub(r"^[#>*\-\s]+", "", line.strip()).strip("*_` ")
        normalized.append(f"> **{heading}**")
        i += 1
        while i < len(lines) and not lines[i].strip():
            normalized.append(">")
            i += 1
        if i < len(lines) and not lines[i].lstrip().startswith(">"):
            normalized.append(f"> {lines[i].strip()}")
            i += 1
        continue
    return normalized


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

    return re.sub(
        r'<p><a id="([^"]+)" class="chapter-anchor"\s*(?:/>|></a>)</p>\s*<(h[1-6])>(.*?)</\2>',
        repl,
        html_content,
        flags=re.S,
    )


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


def _replace_last_footnote_ref(html_content: str, mark: str, replacement: str) -> str:
    candidates = []
    for match in re.finditer(re.escape(mark), html_content):
        index = match.start()
        if _inside_html_tag(html_content, index):
            continue
        if _inside_element(html_content, index, "sup", "footnote-ref"):
            continue
        if _inside_element(html_content, index, "p", "footnote"):
            continue
        candidates.append(index)
    if not candidates:
        return html_content
    index = candidates[-1]
    return html_content[:index] + replacement + html_content[index + len(mark):]


def _inline_footnotes(html_content: str) -> str:
    note_pattern = re.compile(
        rf"<p>([{FOOTNOTE_MARKS}])\s*(.*?——(?:译者|编者)注)(.*?)</p>",
        re.S,
    )
    result = html_content

    while True:
        match = note_pattern.search(result)
        if not match:
            break
        mark = match.group(1)
        body = match.group(2).strip()
        rest = match.group(3).strip()
        note_html = f'<sup class="footnote-ref">{mark}</sup><span class="inline-note">{body}</span>'
        before = _replace_last_footnote_ref(result[:match.start()], mark, note_html)
        after = result[match.end():]
        if rest:
            after = f"\n<p>{rest}</p>" + after
        result = before + after
    return result


def _should_join_pages(previous: str, current: str) -> bool:
    if not previous or not current:
        return False
    previous_line = previous.rstrip().splitlines()[-1].strip()
    current_line = current.lstrip().splitlines()[0].strip()
    if not previous_line or not current_line:
        return False
    if previous_line.startswith("#"):
        return False
    if current_line.startswith((">", "|", "- ", "* ")):
        return False
    if current_line.startswith("#"):
        heading = re.sub(r"^#+\s*", "", current_line).strip()
        return bool(heading and len(heading) <= 30 and not re.match(r"^(第\s*\d+\s*章|推荐序|中文版序|序言|附录|译后记)", heading))
    if previous_line.endswith(("。", "！", "？", "：", "；", ".”", "。")):
        return False
    if re.match(r"^[，。！？；：、）】》〉」』,.!?;:]", current_line):
        return True
    if re.search(r"[\u4e00-\u9fffA-Za-z0-9]$", previous_line) and re.match(r"^[\u4e00-\u9fffA-Za-z0-9]", current_line):
        return True
    return False


def _strip_leading_heading_marker(text: str) -> str:
    return re.sub(r"^\s*#{1,6}\s+", "", text, count=1)


def _is_table_separator(line: str) -> bool:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return bool(cells) and all(re.match(r"^:?-{3,}:?$", cell) for cell in cells)


def _is_table_row(line: str) -> bool:
    return line.lstrip().startswith("|") and line.rstrip().endswith("|")


def _last_table_width(lines: list[str]) -> int:
    for line in reversed(lines):
        if _is_table_row(line):
            return len(line.strip().strip("|").split("|"))
        if line.strip():
            return 0
    return 0


def _table_width(line: str) -> int:
    return len(line.strip().strip("|").split("|"))


def _figure_caption(line: str) -> str | None:
    text = line.strip()
    if re.match(r"^图\s*\d+[—-]\d+\s+\S", text):
        return text
    return None


def _is_noise_line(line: str) -> bool:
    text = re.sub(r"\s+", "", line.strip())
    watermarks = {
        "好学近乎知",
        "好学近知",
        "張爰之印",
        "张爰之印",
    }
    if text in watermarks:
        return True
    if "好学" in text and ("近知" in text or "近乎知" in text):
        return True
    return "空白页" in text and "印章" in text


def _is_blank_stamp_page(lines: list[str]) -> bool:
    text = "".join(re.sub(r"\s+", "", line) for line in lines)
    if "好学" in text and ("近知" in text or "近乎知" in text):
        return True
    return "空白页" in text and ("印章" in text or "无文字内容" in text or "无可见文字内容" in text)


def _epub_image_name(image_path: str) -> str:
    return f"images/{os.path.basename(image_path)}"


def _drop_trailing_fenced_block(lines: list[str]) -> bool:
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines or lines[-1].strip() != "```":
        return False
    lines.pop()
    while lines:
        line = lines.pop()
        if line.strip() == "```":
            break
    return True


def _trim_figure_ocr_before_caption(lines: list[str]) -> None:
    while lines and not lines[-1].strip():
        lines.pop()

    if _drop_trailing_fenced_block(lines):
        return

    tail = []
    while lines:
        text = lines[-1].strip()
        if not text:
            lines.pop()
            continue
        if text.startswith("<a id="):
            break
        if re.search(r"[。！？；：]$", text):
            break
        tail.append(lines.pop())
        if len(tail) >= 12:
            break

    if tail:
        compact = "".join(line.strip() for line in tail)
        if len(tail) == 1 and len(compact) > 60:
            lines.extend(reversed(tail))
            return
        while lines and not lines[-1].strip():
            lines.pop()
        return

    while lines:
        text = lines[-1].strip()
        if text and len(text) <= 42 and not re.search(r"[。！？；：]$", text):
            lines.pop()
            continue
        break


def _merge_continued_tables(md_text: str) -> str:
    lines = md_text.splitlines()
    result = []
    i = 0
    while i < len(lines):
        marker = lines[i].strip().strip("*").strip()
        if marker != "续前表":
            result.append(lines[i])
            i += 1
            continue

        i += 1
        while i < len(lines) and not lines[i].strip():
            i += 1

        # Some OCR pages repeat the table caption before the continued rows.
        skipped_caption = False
        if i < len(lines) and not _is_table_row(lines[i]):
            next_non_empty = i + 1
            while next_non_empty < len(lines) and not lines[next_non_empty].strip():
                next_non_empty += 1
            if next_non_empty < len(lines) and _is_table_row(lines[next_non_empty]):
                i = next_non_empty
                skipped_caption = True

        previous_width = _last_table_width(result)
        if i >= len(lines) or not _is_table_row(lines[i]):
            continue

        # Keep the previous table and continued rows contiguous for Markdown.
        while result and not result[-1].strip():
            result.pop()

        if i + 1 < len(lines) and _is_table_separator(lines[i + 1]):
            i += 2
        elif skipped_caption and previous_width and _table_width(lines[i]) != previous_width:
            result.append("")

        while i < len(lines) and _is_table_row(lines[i]):
            result.append(lines[i])
            i += 1
    return "\n".join(result)


def generate_router(output_dir: str) -> list[dict]:
    entries = []
    seen_keys = set()
    router_sources = _extract_preface_entries(output_dir) + _extract_original_toc(output_dir)
    for toc_entry in router_sources:
        key = _router_key(toc_entry["title"])
        if key in seen_keys:
            continue
        if toc_entry.get("is_preface"):
            page, line_index, level = toc_entry["page"], toc_entry["line"], toc_entry["level"]
        elif toc_entry.get("is_toc"):
            page, line_index, level = toc_entry["page"], toc_entry["line"], toc_entry["level"]
        else:
            target = _find_toc_target(output_dir, toc_entry["title"])
            if not target:
                continue
            page, line_index, level = target
        seen_keys.add(key)
        entry = {
            "title": toc_entry["title"],
            "page": page,
            "line": line_index,
            "level": min(toc_entry.get("level") or level, 2),
            "anchor": f"route-{len(entries) + 1:03d}",
        }
        if "toc_page" in toc_entry and "toc_line" in toc_entry:
            entry["toc_page"] = toc_entry["toc_page"]
            entry["toc_line"] = toc_entry["toc_line"]
        entries.append(entry)
    return entries


def router_path(output_dir: str) -> str:
    return os.path.join(output_dir, "router.json")


def load_router(output_dir: str) -> list[dict]:
    path = router_path(output_dir)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_router(output_dir: str) -> list[dict]:
    entries = generate_router(output_dir)
    output = router_path(output_dir)
    with open(output, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"[+] 已生成目录: {output}")
    return entries


def cmd_build(args):
    md_files = _md_files(args.output_dir)
    if not md_files:
        print(f"[!] 未找到 md/page_*.md 文件")
        sys.exit(1)

    routes = write_router(args.output_dir)
    route_by_source = {(entry["page"], entry["line"]): entry for entry in routes}
    toc_link_by_source = {
        (entry["toc_page"], entry["toc_line"]): entry
        for entry in routes
        if "toc_page" in entry and "toc_line" in entry
    }
    full_md = ""
    figure_images = []
    for path in md_files:
        page = _page_number_from_md(path)
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        if _is_blank_stamp_page(lines):
            continue
        lines = _normalize_uncle_frank_blocks(lines)
        content_lines = []
        skip_fence = False
        for line_index, line in enumerate(lines):
            if line.startswith("<!--"):
                continue
            if _is_noise_line(line):
                continue
            if skip_fence:
                if line.strip() == "```":
                    skip_fence = False
                continue
            route = route_by_source.get((page, line_index))
            if route:
                content_lines.append(f'<a id="{route["anchor"]}" class="chapter-anchor"></a>')
            toc_route = toc_link_by_source.get((page, line_index))
            if toc_route:
                heading = _clean_heading(line)
                if heading:
                    display_title = _toc_display_title(heading[1])
                    if toc_route["title"] == "目录":
                        hashes = "#" * heading[0]
                        line = f'{hashes} <a class="toc-link" href="#{toc_route["anchor"]}">{display_title}</a>'
                    else:
                        line = f'<p class="toc-item toc-level-{toc_route["level"]}"><a class="toc-link" href="#{toc_route["anchor"]}">{display_title}</a></p>'
            caption = _figure_caption(line)
            if caption:
                _trim_figure_ocr_before_caption(content_lines)
                if content_lines and content_lines[-1].strip():
                    content_lines.append("")
            content_lines.append(line)
            if caption:
                image_path = os.path.join(args.output_dir, "images", f"page_{page:04d}.png")
                if os.path.exists(image_path):
                    epub_name = _epub_image_name(image_path)
                    figure_images.append((image_path, epub_name))
                    content_lines.append("")
                    content_lines.append(f'![{caption}]({epub_name})')
                    next_index = line_index + 1
                    while next_index < len(lines) and not lines[next_index].strip():
                        next_index += 1
                    if next_index < len(lines) and lines[next_index].strip() == "```":
                        skip_fence = True
        content = "\n".join(content_lines).strip()
        if _should_join_pages(full_md, content):
            full_md = full_md.rstrip() + _strip_leading_heading_marker(content).lstrip() + "\n\n"
        else:
            full_md += content + "\n\n"

    title = args.title or os.path.basename(args.output_dir)
    cover_path = os.path.join(args.output_dir, "cover.png")

    book = epub.EpubBook()
    book.set_identifier(f"id_{title}")
    book.set_title(title)
    book.set_language("zh")
    if args.author:
        book.add_author(args.author)

    if os.path.exists(cover_path):
        with open(cover_path, "rb") as f:
            book.set_cover("cover.png", f.read())

    import markdown as md_lib
    full_md = _merge_continued_tables(full_md)
    html_content = md_lib.markdown(full_md, extensions=["tables", "fenced_code"])
    html_content = _render_math_blocks(html_content)
    html_content = _wrap_latin_text(html_content)
    html_content = _wrap_tables(html_content)
    html_content = _mark_figure_paragraphs(html_content)
    html_content = _mark_chapter_headings(html_content)
    html_content = _inline_footnotes(html_content)
    style = epub.EpubItem(
        uid="book-style",
        file_name="style/book.css",
        media_type="text/css",
        content="""
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
.inline-note {
  display: inline;
  margin-left: 0.15em;
  font-size: 0.86em;
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
""".strip(),
    )
    book.add_item(style)
    seen_figure_images = set()
    for image_path, epub_name in figure_images:
        if epub_name in seen_figure_images:
            continue
        seen_figure_images.add(epub_name)
        with open(image_path, "rb") as f:
            book.add_item(epub.EpubItem(
                uid=f"figure-{os.path.splitext(os.path.basename(image_path))[0]}",
                file_name=epub_name,
                media_type="image/png",
                content=f.read(),
            ))
    chapter = epub.EpubHtml(title=title, file_name="content.xhtml", lang="zh")
    chapter.content = f"<html><body>{html_content}</body></html>"
    chapter.add_item(style)
    book.add_item(chapter)
    if routes:
        book.toc = [epub.Link(f"content.xhtml#{entry['anchor']}", entry["title"], entry["anchor"]) for entry in routes]
    else:
        book.toc = [epub.Link("content.xhtml", title, "content")]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = [chapter]

    output = args.output or os.path.join(args.output_dir, f"{title}.epub")
    epub.write_epub(output, book)
    print(f"[+] EPUB 已生成: {output}")


def main():
    parser = argparse.ArgumentParser(description="扫描版 PDF 转 EPUB（两阶段）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ocr = sub.add_parser("ocr", help="PDF 页面 → Markdown（增量）")
    p_ocr.add_argument("input_pdf")
    p_ocr.add_argument("--output-dir", default=None, help="输出目录（默认使用 PDF 文件名）")
    p_ocr.add_argument("--start", type=int, default=None, help="起始页（1-based；不指定 --end 时只 OCR 该页）")
    p_ocr.add_argument("--end", type=int, default=None, help="结束页（1-based；不指定 --start 时从第一页 OCR 到该页）")
    p_ocr.add_argument("--all", action="store_true", help="OCR 所有页面，不能和 --start / --end 同时使用")
    p_ocr.add_argument("--model", default=None, help="OCR 模型（默认 kimi-k2.5）")

    p_ocr_image = sub.add_parser("ocr-image", help="单张图片 → Markdown")
    p_ocr_image.add_argument("image")
    p_ocr_image.add_argument("--output", "-o", default=None, help="输出 Markdown 路径（默认 images 同级 md 目录）")
    p_ocr_image.add_argument("--model", default=None, help="OCR 模型（默认 kimi-k2.5）")

    p_screenshot = sub.add_parser("screenshot", help="PDF → 页面截图（增量）")
    p_screenshot.add_argument("input_pdf")
    p_screenshot.add_argument("--output-dir", default=None, help="输出目录（默认使用 PDF 文件名）")
    p_screenshot.add_argument("--start", type=int, default=None, help="起始页（1-based；不指定 --end 时只截图该页）")
    p_screenshot.add_argument("--end", type=int, default=None, help="结束页（1-based；不指定 --start 时从第一页截图到该页）")
    p_screenshot.add_argument("--all", action="store_true", help="截图所有页面，不能和 --start / --end 同时使用")

    p_build = sub.add_parser("build", help="Markdown → EPUB")
    p_build.add_argument("output_dir")
    p_build.add_argument("--title")
    p_build.add_argument("--author")
    p_build.add_argument("--output", "-o")

    args = parser.parse_args()
    if args.cmd == "ocr":
        cmd_ocr(args)
    elif args.cmd == "ocr-image":
        cmd_ocr_image(args)
    elif args.cmd == "screenshot":
        cmd_screenshot(args)
    else:
        cmd_build(args)


if __name__ == "__main__":
    main()
