#!/usr/bin/env python3
"""Normalize migrated list layout to canonical four-space Markdown nesting.

The historical BlogEngine/Windows Live Writer content contains numbered steps,
nested bullet lists, paragraphs, command lines, and screenshots. markdownify
preserves most of that hierarchy but may emit inconsistent indentation and
missing blank separators.

This pass standardizes the source Markdown independent of any renderer:

* continuation content beneath a top-level numbered item -> 4 spaces
* nested unordered-list items -> 4 spaces
* continuation content beneath a nested unordered-list item -> 8 spaces
* sibling nested bullets are separated after block content such as screenshots

The result is canonical source Markdown. Presentation belongs to the publishing
system and should not be encoded here with renderer-specific CSS workarounds.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

DEFAULT_MARKDOWN_DIR = Path("generated-markdown")
TOP_LEVEL_NUMBERED_RE = re.compile(r"^\d+[.)]\s+")
UNORDERED_RE = re.compile(r"^(?P<indent> +)[-+*]\s+")
IMAGE_RE = re.compile(r"^(?:\[)?!\[[^\]]*\]\([^\n]+\)(?:\]\([^\n]+\))?\s*$")


def leading_spaces(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def with_indent(line: str, spaces: int) -> str:
    return " " * spaces + line.lstrip(" ")


def normalize_indentation(lines: list[str]) -> tuple[list[str], int]:
    """Normalize one- and two-level list indentation to 4/8 spaces.

    The pass is idempotent: already-normalized 4/8-space content remains
    unchanged on subsequent runs.
    """
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

            # Any indented unordered-list marker inside the numbered item is a
            # second-level bullet and belongs at the first four-space level.
            if unordered and indent > 0:
                new_line = with_indent(line, 4)
                if new_line != line:
                    out[i] = new_line
                    changed += 1
                in_nested_item = True
                i += 1
                continue

            # Other indented lines are continuation content. Once a nested
            # bullet has begun, its continuation belongs at the second nesting
            # level; otherwise it belongs directly to the numbered item.
            if indent > 0:
                target = 8 if in_nested_item else 4
                new_line = with_indent(line, target)
                if new_line != line:
                    out[i] = new_line
                    changed += 1

            i += 1

    return out, changed


def insert_nested_list_boundaries(lines: list[str]) -> tuple[list[str], int]:
    """Keep sibling nested bullets distinct after image/block continuation."""
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
                previous_is_block = (
                    leading_spaces(previous) >= 8
                    or bool(IMAGE_RE.match(previous.lstrip(" ")))
                )
                if previous_is_block and out and out[-1].strip():
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--markdown-dir", type=Path, default=DEFAULT_MARKDOWN_DIR)
    # Retained for compatibility with earlier commands. No destination CSS is
    # modified; this script now operates only on canonical Markdown source.
    parser.add_argument("--destination", type=Path, help=argparse.SUPPRESS)
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

    print(f"Markdown files checked:   {len(files)}")
    print(f"Markdown files changed:   {changed_files}")
    print(f"List lines normalized:    {changed_lines_total}")
    print(f"Nested boundaries added:  {boundaries_total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
