#!/usr/bin/env python3
import sys
from pathlib import Path
from ebooklib import epub
import zipfile
import shutil

def mobi_to_epub(mobi_path, epub_path):
    """Convert MOBI to EPUB using ebooklib"""
    mobi_path = Path(mobi_path)
    epub_path = Path(epub_path)

    if not mobi_path.exists():
        print(f"Error: {mobi_path} not found")
        sys.exit(1)

    try:
        # Read MOBI file
        book = epub.read_epub(str(mobi_path))

        # Write EPUB file
        epub.write_epub(str(epub_path), book)
        print(f"✓ Converted: {mobi_path} → {epub_path}")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python convert_mobi.py <mobi_file> [epub_file]")
        sys.exit(1)

    mobi_file = sys.argv[1]
    epub_file = sys.argv[2] if len(sys.argv) > 2 else Path(mobi_file).with_suffix('.epub')

    mobi_to_epub(mobi_file, epub_file)
