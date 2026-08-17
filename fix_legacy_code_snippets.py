#!/usr/bin/env python3
"""Repair legacy line-numbered code snippets in generated Markdown.

Some old BlogEngine/Windows Live Writer snippets were emitted as visible line
numbers ("1. ...", "2. ...") rather than semantic <pre><code> blocks. After
HTML->Markdown conversion those become Markdown ordered lists. This script
recognizes a "Code Snippet" label followed by numbered lines, strips the
legacy line numbers, infers a language, and replaces the block with a fenced
code block.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

DEFAULT_DIR = Path("generated-markdown")
NUMBERED = re.compile(r"^(?P<indent>\s*)(?P<num>\d+)\.\s?(?P<text>.*)$")


def infer_language(lines: list[str]) -> str:
    text = "\n".join(lines)
    lower = text.lower()
    if "<asp:" in lower or "runat=\"server\"" in lower or re.search(r"</?[a-z][^>]*>", text, re.I):
        if "<script" in lower or "function " in lower:
            return "javascript"
        return "xml"
    if re.search(r"\b(protected|public|private|class|void|string|bool|servervalidateeventargs|customvalidator)\b", text):
        return "csharp"
    if re.search(r"\b(function|document\.getelementbyid|var\s+|args\.isvalid|innertext)\b", lower):
        return "javascript"
    return "text"


def clean_code_line(line: str) -> str:
    # markdownify escapes underscores in normal prose; those escapes are not
    # wanted once the content is restored to a code fence.
    line = line.replace("\\_", "_")
    # Legacy snippets sometimes encoded explicit visual line breaks as <br />.
    if re.fullmatch(r"\s*<br\s*/?>\s*", line, re.I):
        return ""
    return re.sub(r"<br\s*/?>", "", line, flags=re.I)


def repair(text: str) -> tuple[str, int]:
    lines = text.splitlines()
    out: list[str] = []
    repaired = 0
    i = 0

    while i < len(lines):
        if lines[i].strip().lower() != "code snippet":
            out.append(lines[i])
            i += 1
            continue

        out.append(lines[i])
        i += 1
        while i < len(lines) and not lines[i].strip():
            out.append(lines[i])
            i += 1

        start = i
        numbered_lines: list[str] = []
        while i < len(lines):
            match = NUMBERED.match(lines[i])
            if not match:
                break
            numbered_lines.append(clean_code_line(match.group("text")))
            i += 1

        if len(numbered_lines) < 2:
            # Not one of the legacy numbered snippet blocks.
            out.extend(lines[start:i])
            continue

        # Trim only leading/trailing blank lines introduced by legacy <br>s.
        while numbered_lines and not numbered_lines[0].strip():
            numbered_lines.pop(0)
        while numbered_lines and not numbered_lines[-1].strip():
            numbered_lines.pop()

        language = infer_language(numbered_lines)
        out.append(f"```{language}")
        out.extend(numbered_lines)
        out.append("```")
        repaired += 1

    result = "\n".join(out)
    if text.endswith("\n"):
        result += "\n"
    return result, repaired


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--markdown-dir", type=Path, default=DEFAULT_DIR)
    args = parser.parse_args()

    total = 0
    changed_files = 0
    for path in sorted(args.markdown_dir.glob("*.md")):
        original = path.read_text(encoding="utf-8")
        updated, count = repair(original)
        if count:
            path.write_text(updated, encoding="utf-8")
            changed_files += 1
            total += count
            print(f"{path.name}: repaired {count} code snippet(s)")

    print(f"Files changed:            {changed_files}")
    print(f"Code snippets repaired:   {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
