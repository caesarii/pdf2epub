#!/usr/bin/env python3
"""
两阶段扫描版 PDF 转 EPUB 工具
- ocr:   PDF → 分批 Markdown（发送截图给模型识别，供人工审核）
- build: Markdown → EPUB

OCR 优先使用 Zode 中转 Kimi（读取 ./output/.env 中的 key），保留 Claude 和外部回调作为兼容模式。
"""
import os
import sys
import argparse
import base64
import glob
import json
import time

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


def cmd_build(args):
    md_files = sorted(glob.glob(os.path.join(args.output_dir, "md", "page_*.md")))
    if not md_files:
        print(f"[!] 未找到 md/page_*.md 文件")
        sys.exit(1)

    full_md = ""
    for path in md_files:
        with open(path, encoding="utf-8") as f:
            content = f.read()
        content = "\n".join(l for l in content.splitlines() if not l.startswith("<!--"))
        full_md += content.strip() + "\n\n"

    title = args.title or os.path.basename(args.output_dir)
    image_dir = os.path.join(args.output_dir, "images")
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

    if os.path.isdir(image_dir):
        for root, _, files in os.walk(image_dir):
            for fname in files:
                fpath = os.path.join(root, fname)
                rel = os.path.relpath(fpath, image_dir)
                epub_path = "images/" + rel.replace("\\", "/")
                ext = os.path.splitext(fname)[1].lower()
                mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                        ".png": "image/png", ".gif": "image/gif",
                        ".webp": "image/webp"}.get(ext, "image/jpeg")
                with open(fpath, "rb") as f:
                    img = epub.EpubImage()
                    img.file_name = epub_path
                    img.media_type = mime
                    img.content = f.read()
                    book.add_item(img)

    import markdown as md_lib
    html_content = md_lib.markdown(full_md, extensions=["tables", "fenced_code"])
    chapter = epub.EpubHtml(title=title, file_name="content.xhtml", lang="zh")
    chapter.content = f"<html><body>{html_content}</body></html>"
    book.add_item(chapter)
    book.toc = [epub.Link("content.xhtml", title, "content")]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", chapter]

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
