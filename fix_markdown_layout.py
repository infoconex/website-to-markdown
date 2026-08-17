#!/usr/bin/env python3
"""Fix known layout artifacts in migrated Markdown and staged static-site CSS.

BlogEngine/markdownify commonly emits content beneath a top-level numbered list
item with three leading spaces. Python-Markdown requires a four-space
continuation indent for block content in that list item. If only an image is
reindented, it can become an indented code block because the preceding
paragraphs have already escaped the list.

This pass therefore normalizes the *entire continuation block* for each
 top-level numbered item: every nonblank line between that item and the next
 top-level numbered item that is indented three or more spaces is shifted right
by one space. Relative indentation is preserved, so nested bullets and their
images move together.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

DEFAULT_MARKDOWN_DIR = Path("generated-markdown")
TOP_LEVEL_NUMBERED_RE = re.compile(r"^\d+[.)]\s+")

CSS_RULES = r'''

/* Migrated post content: keep loose Markdown lists compact and images block-level. */
.post-body li { margin: .35rem 0; }
.post-body li > p { margin: .35rem 0; }
.post-body li img { display: block; margin: .85rem 0; }
'''
CSS_MARKER = "Migrated post content: keep loose Markdown lists compact"


def leading_spaces(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def normalize_markdown(text: str) -> tuple[str, int]:
    lines = text.splitlines()
    out = list(lines)
    shifted = 0

    i = 0
    while i < len(out):
        if not TOP_LEVEL_NUMBERED_RE.match(out[i]):
            i += 1
            continue

        i += 1
        while i < len(out) and not TOP_LEVEL_NUMBERED_RE.match(out[i]):
            line = out[i]
            if line.strip() and leading_spaces(line) >= 3:
                out[i] = " " + line
                shifted += 1
            i += 1

    suffix = "\n" if text.endswith("\n") else ""
    return "\n".join(out) + suffix, shifted


def patch_css(destination: Path) -> bool:
    css = destination / "assets" / "css" / "site.css"
    if not css.exists():
        raise SystemExit(f"Stylesheet not found: {css}")
    text = css.read_text(encoding="utf-8")
    if CSS_MARKER in text:
        return False
    css.write_text(text.rstrip() + CSS_RULES + "\n", encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--markdown-dir", type=Path, default=DEFAULT_MARKDOWN_DIR)
    parser.add_argument("--destination", type=Path, help="Optional staged coding-blog checkout to patch CSS")
    args = parser.parse_args()

    files = sorted(args.markdown_dir.glob("*.md"))
    if not files:
        raise SystemExit(f"No Markdown files found in {args.markdown_dir}")

    changed_files = 0
    shifted_total = 0
    for path in files:
        original = path.read_text(encoding="utf-8")
        updated, shifted = normalize_markdown(original)
        if updated != original:
            path.write_text(updated, encoding="utf-8")
            changed_files += 1
        shifted_total += shifted

    css_changed = False
    if args.destination:
        css_changed = patch_css(args.destination.resolve())

    print(f"Markdown files checked:   {len(files)}")
    print(f"Markdown files changed:   {changed_files}")
    print(f"Continuation lines shifted: {shifted_total}")
    if args.destination:
        print(f"Site CSS updated:          {'yes' if css_changed else 'already current'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
