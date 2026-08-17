#!/usr/bin/env python3
"""Fix known layout artifacts in migrated Markdown and staged static-site CSS.

Repairs image-to-next-list-item boundaries in generated Markdown so a following
numbered/bulleted item cannot render beside an image. Optionally patches the
coding-blog stylesheet to keep Markdown loose lists visually compact while
rendering post images as block elements.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

DEFAULT_MARKDOWN_DIR = Path("generated-markdown")

IMAGE_ONLY_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?:\[)?!\[[^\]]*\]\([^\n]+\)(?:\]\([^\n]+\))?\s*$"
)
LIST_ITEM_RE = re.compile(r"^(?P<indent>[ \t]*)(?:[-+*]|\d+[.)])\s+")

CSS_RULES = r'''

/* Migrated post content: keep loose Markdown lists compact and images block-level. */
.post-body li { margin: .35rem 0; }
.post-body li > p { margin: .35rem 0; }
.post-body li img { display: block; margin: .85rem 0; }
'''
CSS_MARKER = "Migrated post content: keep loose Markdown lists compact"


def normalize_markdown(text: str) -> tuple[str, int]:
    lines = text.splitlines()
    out: list[str] = []
    inserted = 0

    for i, line in enumerate(lines):
        out.append(line)
        image = IMAGE_ONLY_RE.match(line)
        if not image or i + 1 >= len(lines):
            continue

        next_line = lines[i + 1]
        list_item = LIST_ITEM_RE.match(next_line)
        if not list_item:
            continue

        image_indent = len(image.group("indent").expandtabs(4))
        list_indent = len(list_item.group("indent").expandtabs(4))
        if list_indent <= image_indent and (not out or (i + 1 < len(lines) and next_line.strip())):
            # A blank line is required here for Python-Markdown to terminate the
            # image-containing list item before beginning the next list item.
            if line.strip() and (i + 1 >= len(lines) or lines[i + 1].strip()):
                out.append("")
                inserted += 1

    suffix = "\n" if text.endswith("\n") else ""
    return "\n".join(out) + suffix, inserted


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
    inserted_boundaries = 0
    for path in files:
        original = path.read_text(encoding="utf-8")
        updated, inserted = normalize_markdown(original)
        if updated != original:
            path.write_text(updated, encoding="utf-8")
            changed_files += 1
            inserted_boundaries += inserted

    css_changed = False
    if args.destination:
        css_changed = patch_css(args.destination.resolve())

    print(f"Markdown files checked:   {len(files)}")
    print(f"Markdown files changed:   {changed_files}")
    print(f"List boundaries inserted: {inserted_boundaries}")
    if args.destination:
        print(f"Site CSS updated:          {'yes' if css_changed else 'already current'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
