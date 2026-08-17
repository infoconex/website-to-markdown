#!/usr/bin/env python3
"""Fix known layout artifacts in migrated Markdown and staged static-site CSS.

Repairs image/list boundaries in generated Markdown. BlogEngine content often
places screenshots inside ordered-list items. markdownify commonly emits those
continuation blocks with three leading spaces; Python-Markdown can then treat a
following sibling list item as inline continuation text. This pass normalizes
block content inside list items to four-space continuation indentation and
ensures a blank line before the next sibling item.
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

CSS_RULES = r'''

/* Migrated post content: keep loose Markdown lists compact and images block-level. */
.post-body li { margin: .35rem 0; }
.post-body li > p { margin: .35rem 0; }
.post-body li img { display: block; margin: .85rem 0; }
'''
CSS_MARKER = "Migrated post content: keep loose Markdown lists compact"


def leading_spaces(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def normalize_markdown(text: str) -> tuple[str, int, int]:
    """Normalize screenshot blocks that occur inside top-level list items.

    The specific broken migration shape is:

        1. item text

           [![image](thumb)](full)
        2. next item

    The three-space continuation is changed to four spaces, and a blank line is
    guaranteed before the next top-level item:

        1. item text

            [![image](thumb)](full)

        2. next item
    """
    lines = text.splitlines()
    out: list[str] = []
    reindented = 0
    inserted = 0

    for i, original_line in enumerate(lines):
        line = original_line
        image = IMAGE_ONLY_RE.match(line)

        # markdownify frequently emits three spaces for content nested beneath a
        # top-level list item. Four spaces is the unambiguous Markdown block
        # continuation indentation and works consistently with Python-Markdown.
        if image and leading_spaces(line) == 3:
            line = " " + line
            image = IMAGE_ONLY_RE.match(line)
            reindented += 1

        out.append(line)

        if not image or i + 1 >= len(lines):
            continue

        next_line = lines[i + 1]
        next_item = TOP_LEVEL_LIST_RE.match(next_line)
        if not next_item or leading_spaces(next_line) != 0:
            continue

        # Only insert when there is no blank source line already between the
        # screenshot and its next sibling list item.
        if next_line.strip():
            out.append("")
            inserted += 1

    suffix = "\n" if text.endswith("\n") else ""
    return "\n".join(out) + suffix, reindented, inserted


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
    reindented_total = 0
    inserted_total = 0
    for path in files:
        original = path.read_text(encoding="utf-8")
        updated, reindented, inserted = normalize_markdown(original)
        if updated != original:
            path.write_text(updated, encoding="utf-8")
            changed_files += 1
        reindented_total += reindented
        inserted_total += inserted

    css_changed = False
    if args.destination:
        css_changed = patch_css(args.destination.resolve())

    print(f"Markdown files checked:   {len(files)}")
    print(f"Markdown files changed:   {changed_files}")
    print(f"Image blocks reindented:  {reindented_total}")
    print(f"List boundaries inserted: {inserted_total}")
    if args.destination:
        print(f"Site CSS updated:          {'yes' if css_changed else 'already current'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
