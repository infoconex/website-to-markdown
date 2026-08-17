#!/usr/bin/env python3
"""Fix known layout artifacts in migrated Markdown and staged static-site CSS.

BlogEngine/markdownify commonly emits content beneath a top-level numbered list
item with three leading spaces. Python-Markdown is sensitive to the content
column of nested list items, so migrated content can escape its parent list or
become an indented code block.

This pass normalizes migrated list structure explicitly:

* continuation content under a top-level numbered item -> 4 spaces
* nested unordered-list items -> 4 spaces
* continuation content (including images) under a nested item -> 6 spaces
  (the content column of ``    - item``)
* sibling nested bullets are separated after block content such as screenshots

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
IMAGE_RE = re.compile(r"^(?:\[)?!\[[^\]]*\]\([^\n]+\)(?:\]\([^\n]+\))?\s*$")

CSS_START = "/* BEGIN migrated post layout */"
CSS_END = "/* END migrated post layout */"
CSS_RULES = r'''
/* BEGIN migrated post layout */
.post-body li { margin: .35rem 0; }
.post-body li > p { margin: .35rem 0; }
.post-body li img { display: block; margin: .85rem 0; }

/*
 * Python-Markdown may emit an image-only continuation either as a direct
 * anchor beneath the li or as a paragraph containing that anchor. Indent the
 * wrapper rather than the image so both shapes line up at the same content
 * column beneath the nested bullet text.
 */
.post-body ol ul > li > a:has(> img),
.post-body ol ul > li > p:has(> a > img) {
    display: block;
    margin-left: 1rem;
}
.post-body ol ul > li > a:has(> img) > img,
.post-body ol ul > li > p:has(> a > img) img {
    margin-left: 0;
}
/* END migrated post layout */
'''


def leading_spaces(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def with_indent(line: str, spaces: int) -> str:
    return " " * spaces + line.lstrip(" ")


def normalize_indentation(lines: list[str]) -> tuple[list[str], int]:
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

            if unordered and indent >= 3:
                new_line = with_indent(line, 4)
                if new_line != line:
                    out[i] = new_line
                    changed += 1
                in_nested_item = True
                i += 1
                continue

            if indent >= 3:
                target = 6 if in_nested_item else 4
                new_line = with_indent(line, target)
                if new_line != line:
                    out[i] = new_line
                    changed += 1

            i += 1

    return out, changed


def insert_nested_list_boundaries(lines: list[str]) -> tuple[list[str], int]:
    """Insert a blank before a sibling nested bullet after block continuation."""
    out: list[str] = []
    inserted = 0

    for line in lines:
        is_nested_bullet = bool(UNORDERED_RE.match(line)) and leading_spaces(line) == 4
        if is_nested_bullet:
            j = len(out) - 1
            while j >= 0 and not out[j].strip():
                j -= 1
            if j >= 0:
                previous = out[j]
                previous_indent = leading_spaces(previous)
                previous_is_block = previous_indent >= 6 or IMAGE_RE.match(previous.lstrip(" "))
                if previous_is_block and (not out or out[-1].strip()):
                    out.append("")
                    inserted += 1
        out.append(line)

    return out, inserted


def normalize_markdown(text: str) -> tuple[str, int, int]:
    lines = text.splitlines()
    lines, changed = normalize_indentation(lines)
    lines, inserted = insert_nested_list_boundaries(lines)
    suffix = "\n" if text.endswith("\n") else ""
    return "\n".join(lines) + suffix, changed, inserted


def patch_css(destination: Path) -> bool:
    css = destination / "assets" / "css" / "site.css"
    if not css.exists():
        raise SystemExit(f"Stylesheet not found: {css}")
    text = css.read_text(encoding="utf-8")

    managed = re.compile(
        re.escape(CSS_START) + r".*?" + re.escape(CSS_END),
        re.S,
    )
    if managed.search(text):
        updated = managed.sub(CSS_RULES.strip(), text)
    else:
        old = re.compile(
            r"\n?/\* Migrated post content: keep loose Markdown lists compact and images block-level\. \*/\n"
            r"\.post-body li \{ margin: \.35rem 0; \}\n"
            r"\.post-body li > p \{ margin: \.35rem 0; \}\n"
            r"\.post-body li img \{ display: block; margin: \.85rem 0; \}\n?",
            re.S,
        )
        if old.search(text):
            updated = old.sub("\n" + CSS_RULES.strip() + "\n", text)
        else:
            updated = text.rstrip() + "\n\n" + CSS_RULES.strip() + "\n"

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
    changed_lines_total = 0
    boundaries_total = 0
    for path in files:
        original = path.read_text(encoding="utf-8")
        updated, changed_lines, boundaries = normalize_markdown(original)
        if updated != original:
            path.write_text(updated, encoding="utf-8")
            changed_files += 1
        changed_lines_total += changed_lines
        boundaries_total += boundaries

    css_changed = False
    if args.destination:
        css_changed = patch_css(args.destination.resolve())

    print(f"Markdown files checked:   {len(files)}")
    print(f"Markdown files changed:   {changed_files}")
    print(f"List lines normalized:    {changed_lines_total}")
    print(f"Nested boundaries added:  {boundaries_total}")
    if args.destination:
        print(f"Site CSS updated:          {'yes' if css_changed else 'already current'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
