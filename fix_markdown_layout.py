#!/usr/bin/env python3
"""Fix known layout artifacts in migrated Markdown and staged static-site CSS.

BlogEngine/markdownify commonly emits content beneath a top-level numbered list
item with three leading spaces. Python-Markdown uses four-space nesting levels,
so migrated list content can escape its parent item or break after an image.

This pass normalizes migrated list structure explicitly:

* continuation content under a top-level numbered item -> 4 spaces
* nested unordered-list items -> 4 spaces
* continuation content (including images) under a nested item -> 8 spaces

That keeps paragraphs, commands, nested bullets, and screenshots attached to the
same semantic list items rather than relying on CSS to hide malformed markup.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

DEFAULT_MARKDOWN_DIR = Path("generated-markdown")
TOP_LEVEL_NUMBERED_RE = re.compile(r"^\d+[.)]\s+")
UNORDERED_RE = re.compile(r"^(?P<indent> +)[-+*]\s+")

CSS_RULES = r'''

/* Migrated post content: keep loose Markdown lists compact and images block-level. */
.post-body li { margin: .35rem 0; }
.post-body li > p { margin: .35rem 0; }
.post-body li img { display: block; margin: .85rem 0; }
'''
CSS_MARKER = "Migrated post content: keep loose Markdown lists compact"


def leading_spaces(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def with_indent(line: str, spaces: int) -> str:
    return " " * spaces + line.lstrip(" ")


def normalize_markdown(text: str) -> tuple[str, int]:
    lines = text.splitlines()
    out = list(lines)
    changed = 0

    i = 0
    while i < len(out):
        if not TOP_LEVEL_NUMBERED_RE.match(out[i]):
            i += 1
            continue

        i += 1
        in_nested_item = False

        while i < len(out) and not TOP_LEVEL_NUMBERED_RE.match(out[i]):
            line = out[i]
            if not line.strip():
                i += 1
                continue

            indent = leading_spaces(line)
            unordered = UNORDERED_RE.match(line)

            # A markdownify nested bullet typically arrives with three spaces.
            # Python-Markdown requires four spaces to nest it beneath the
            # surrounding numbered item.
            if unordered and indent >= 3:
                new_line = with_indent(line, 4)
                if new_line != line:
                    out[i] = new_line
                    changed += 1
                in_nested_item = True
                i += 1
                continue

            if indent >= 3:
                target = 8 if in_nested_item else 4
                # Preserve any indentation beyond the migrated baseline while
                # enforcing a valid four-space nesting level.
                if in_nested_item:
                    extra = max(0, indent - 5)
                    target += extra
                else:
                    extra = max(0, indent - 3)
                    target += extra

                new_line = with_indent(line, target)
                if new_line != line:
                    out[i] = new_line
                    changed += 1

            i += 1

    suffix = "\n" if text.endswith("\n") else ""
    return "\n".join(out) + suffix, changed


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
    changed_lines_total = 0
    for path in files:
        original = path.read_text(encoding="utf-8")
        updated, changed_lines = normalize_markdown(original)
        if updated != original:
            path.write_text(updated, encoding="utf-8")
            changed_files += 1
        changed_lines_total += changed_lines

    css_changed = False
    if args.destination:
        css_changed = patch_css(args.destination.resolve())

    print(f"Markdown files checked:   {len(files)}")
    print(f"Markdown files changed:   {changed_files}")
    print(f"List lines normalized:    {changed_lines_total}")
    if args.destination:
        print(f"Site CSS updated:          {'yes' if css_changed else 'already current'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
