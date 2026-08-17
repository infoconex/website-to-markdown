#!/usr/bin/env python3
"""Repair Windows newline translation artifacts in previously captured HTML.

Some crawl-output/html files were written on Windows with Path.write_text() from
response text that already contained CRLF line endings. That produced CRCRLF
(\r\r\n). Inside <pre> code blocks this later became a blank line between every
source-code line during HTML-to-Markdown conversion.

This script replaces CRCRLF with CRLF in-place. It is intentionally narrow: it
does not otherwise reformat or parse the HTML.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument(
        "html_dir",
        nargs="?",
        type=Path,
        default=Path("crawl-output/html"),
        help="Directory containing captured .html files (default: crawl-output/html)",
    )
    p.add_argument("--check", action="store_true", help="Report files needing repair without modifying them")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    root = args.html_dir
    if not root.exists():
        raise SystemExit(f"HTML directory not found: {root}")

    files = sorted(root.glob("*.html"))
    affected = 0
    replacements = 0

    for path in files:
        data = path.read_bytes()
        count = data.count(b"\r\r\n")
        if not count:
            continue
        affected += 1
        replacements += count
        print(f"{path}: {count} CRCRLF sequences")
        if not args.check:
            path.write_bytes(data.replace(b"\r\r\n", b"\r\n"))

    print()
    print(f"HTML files checked:       {len(files)}")
    print(f"Files affected:           {affected}")
    print(f"Newline repairs:          {replacements}")
    print(f"Mode:                     {'CHECK ONLY' if args.check else 'REPAIRED'}")
    return 1 if args.check and affected else 0


if __name__ == "__main__":
    raise SystemExit(main())
