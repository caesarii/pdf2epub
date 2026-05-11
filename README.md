# pdf2epub

Incremental tools for converting scanned PDFs into EPUB files. The workflow renders PDF pages to images, uses a vision model to OCR each page into Markdown, lets you review the Markdown, then builds an EPUB.

The current OCR backend is Kimi `kimi-k2.5` through a Zode OpenAI-compatible proxy. The script also keeps a Claude fallback when `ANTHROPIC_API_KEY` is configured and no Zode key is present.

## Features

- Incremental screenshot generation: existing `images/page_XXXX.png` files are skipped.
- Incremental OCR: existing `md/page_XXXX.md` files are skipped.
- Missing screenshots are generated automatically before OCR.
- Per-page Markdown output for easier review and retry.
- EPUB build from reviewed Markdown files.
- Codex/Zode skill metadata under `skills/skilled-epub/`.

## Requirements

- Python 3.11+
- A Zode key for Kimi OCR, or an Anthropic API key for Claude fallback

Install dependencies:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

## Configuration

Create a local environment file. Do not commit real keys.

```bash
cp .env.example output/.env
```

Then edit `output/.env`:

```env
key=zode_xxx
```

Accepted Zode key variable names are `key`, `ZODE_KEY`, or `ZODE_API_KEY`.

## Usage

This repository is organized around the `skilled-epub` skill. It is intended to be consumed by open-claw or other skills-aware agent runtimes from:

```text
skills/skilled-epub/
  SKILL.md
  agents/openai.yaml
  scripts/scan_pdf_to_epub.py
```

In a skills runtime, ask the agent to use `skilled-epub` for scanned PDF conversion. Typical prompts:

```text
Use skilled-epub to screenshot pages 1-10 of input/book.pdf into output/book.
Use skilled-epub to OCR page 5 of input/book.pdf into output/book/md/page_0005.md.
Use skilled-epub to OCR all missing pages for input/book.pdf, skipping existing Markdown.
Use skilled-epub to build output/book into an EPUB titled "Book Title".
```

The skill is incremental by design:

- `screenshot` skips existing `images/page_XXXX.png` files.
- `ocr` skips existing `md/page_XXXX.md` files.
- `ocr` automatically screenshots a page first when its image is missing.
- `router` generates `router.json` so EPUB readers can navigate chapters.
- `build` reads reviewed `md/page_*.md` files in order.

### Manual CLI

The skill delegates deterministic work to a local script. You can also run it directly for debugging or batch execution:

```bash
.venv/bin/python skills/skilled-epub/scripts/scan_pdf_to_epub.py --help
```

Screenshot pages:

```bash
.venv/bin/python skills/skilled-epub/scripts/scan_pdf_to_epub.py screenshot input/book.pdf --output-dir output/book --start 5
.venv/bin/python skills/skilled-epub/scripts/scan_pdf_to_epub.py screenshot input/book.pdf --output-dir output/book --end 10
.venv/bin/python skills/skilled-epub/scripts/scan_pdf_to_epub.py screenshot input/book.pdf --output-dir output/book --start 5 --end 10
.venv/bin/python skills/skilled-epub/scripts/scan_pdf_to_epub.py screenshot input/book.pdf --output-dir output/book --all
```

OCR pages with the same range semantics as `screenshot`:

```bash
.venv/bin/python skills/skilled-epub/scripts/scan_pdf_to_epub.py ocr input/book.pdf --output-dir output/book --start 5
.venv/bin/python skills/skilled-epub/scripts/scan_pdf_to_epub.py ocr input/book.pdf --output-dir output/book --end 10
.venv/bin/python skills/skilled-epub/scripts/scan_pdf_to_epub.py ocr input/book.pdf --output-dir output/book --start 5 --end 10
.venv/bin/python skills/skilled-epub/scripts/scan_pdf_to_epub.py ocr input/book.pdf --output-dir output/book --all
```

OCR one image:

```bash
.venv/bin/python skills/skilled-epub/scripts/scan_pdf_to_epub.py ocr-image output/book/images/page_0005.png
```

Build EPUB after reviewing `output/book/md/page_*.md`:

```bash
.venv/bin/python skills/skilled-epub/scripts/scan_pdf_to_epub.py router output/book
.venv/bin/python skills/skilled-epub/scripts/scan_pdf_to_epub.py build output/book --title "Book Title" --author "Author Name" --output output/book.epub
```

Generate a browser preview from an unpacked EPUB directory:

```bash
.venv/bin/python skills/skilled-epub/scripts/scan_pdf_to_epub.py preview output/book_epub_unpacked --title "Book Title"
```

## Output Layout

```text
output/book/
  images/
    page_0001.png
    page_0002.png
  md/
    page_0001.md
    page_0002.md
  cover.png
```

## Notes

- `input/`, `output/`, PDFs, EPUBs, and `.env` files are ignored by Git.
- OCR quality depends on screenshot quality and model behavior. Review Markdown before building the final EPUB.
- Only process documents you have the right to transform.

## License

MIT. See [LICENSE](LICENSE).
