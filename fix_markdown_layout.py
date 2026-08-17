#!/usr/bin/env python3
"""Fix known layout artifacts in migrated Markdown and staged static-site CSS.

BlogEngine screenshots inside ordered-list items are commonly emitted by
markdownify with three leading spaces. That form renders as Markdown, but it
needs a blank line before the next sibling list item. Four leading spaces are
*not* used here because Python-Markdown may interpret the image Markdown as an
indented code block.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

DEFAULT_MARKDOWN_DIR = Path("generated-markdown")

IMAGE_ONLY_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?:\[)?!\[[^\]]*\]\([^\n]+\)(?:\]\([^\n]+\))?\s*$"
)
TOP_LEVEL_LIST_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<marker>(?:[-+*]|\d+[.)]))\s+")

CSS_MARKER = "Migrated post content: keep loose Markdown lists compact"
CSS_BLOCK_RE = re.compile(
    r"\n?/\* Migrated post content: keep loose Markdown lists compact and images block-level\. \*/.*?(?=\n/\*|\Z)",
    re.S,
)
CSS_RULES = r'''

/* Migrated post content: keep loose Markdown lists compact and images block-level. */
.post-body li { margin: .35rem 0; }
.post-body li > p { margin: .35rem 0; }
.post-body li img { display: block; margin: .85rem 0; }
.post-body li a:has(> img) { display: block; }
'''


def leading_spaces(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def normalize_markdown(text: str) -> tuple[str, int, int]:
    """Keep list-item images as Markdown and separate the next sibling item."""
    lines = text.splitlines()
    out: list[str] = []
    deindented = 0
    inserted = 0

    for i, original_line in enumerate(lines):
        line = original_line
        image = IMAGE_ONLY_RE.match(line)

        # Repair the previous migration pass, which changed these lines to four
        # spaces and caused Python-Markdown to render the image syntax as code.
        if image and leading_spaces(line) == 4:
            line = line[1:]
            image = IMAGE_ONLY_RE.match(line)
            deindented += 1

        out.append(line)

        if not image or i + 1 >= len(lines):
            continue

        next_line = lines[i + 1]
        next_item = TOP_LEVEL_LIST_RE.match(next_line)
        if not next_item or leading_spaces(next_line) != 0:
            continue

        # A blank line terminates the image-containing list block before the
        # next numbered sibling. Do not add a duplicate if one already exists.
        if line.strip() and next_line.strip():
            out.append("")
            inserted += 1

    suffix = "\n" if text.endswith("\n") else ""
    return "\n".join(out) + suffix, deindented, inserted


def patch_css(destination: Path) -> bool:
    css = destination / "assets" / "css" / "site.css"
    if not css.exists():
        raise SystemExit(f"Stylesheet not found: {css}")
    text = css.read_text(encoding="utf-8")
    if CSS_MARKER in text:
        updated = CSS_BLOCK_RE.sub(CSS_RULES.rstrip(), text)
    else:
        updated = text.rstrip() + CSS_RULES + "\n"
    if updated == text:
        return False
    css.write_text(updated, encoding="utf-8")
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
    deindented_total = 0
    inserted_total = 0
    for path in files:
        original = path.read_text(encoding="utf-8")
        updated, deindented, inserted = normalize_markdown(original)
        if updated != original:
            path.write_text(updated, encoding="utf-8")
            changed_files += 1
        deindented_total += deindented
        inserted_total += inserted

    css_changed = False
    if args.destination:
        css_changed = patch_css(args.destination.resolve())

    print(f"Markdown files checked:   {len(files)}")
    print(f"Markdown files changed:   {changed_files}")
    print(f"Image blocks deindented:  {deindented_total}")
    print(f"List boundaries inserted: {inserted_total}")
    if args.destination:
        print(f"Site CSS updated:          {'yes' if css_changed else 'already current'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
