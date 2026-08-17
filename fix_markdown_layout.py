#!/usr/bin/env python3
"""Normalize migrated list layout to renderer-neutral Markdown.

The historical BlogEngine/Windows Live Writer content contains numbered steps,
nested bullet lists, paragraphs, code-like command lines, and screenshots.
markdownify generally preserves the intended hierarchy but may emit inconsistent
indentation and missing blank separators.

This pass uses the Markdown list content column rather than renderer-specific
indentation rules:

* continuation content under ``1. item`` -> 3 spaces
* nested bullet beneath a numbered item -> 3 spaces
* continuation content beneath ``   - item`` -> 5 spaces
* sibling nested bullets are separated after block content such as screenshots

The result is intended to be the canonical source Markdown. Presentation belongs
to the eventual publishing system, not this migration step.
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

            if unordered and indent >= 2:
                new_line = with_indent(line, 3)
                if new_line != line:
                    out[i] = new_line
                    changed += 1
                in_nested_item = True
                i += 1
                continue

            if indent >= 2:
                target = 5 if in_nested_item else 3
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
        is_nested_bullet = bool(UNORDERED_RE.match(line)) and leading_spaces(line) == 3
        if is_nested_bullet:
            j = len(out) - 1
            while j >= 0 and not out[j].strip():
                j -= 1
            if j >= 0:
                previous = out[j]
                previous_is_block = (
                    leading_spaces(previous) >= 5
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
    # Retained for command-line compatibility with earlier migration runs. The
    # canonical Markdown normalizer no longer patches destination CSS.
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
