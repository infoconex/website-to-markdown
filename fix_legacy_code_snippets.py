#!/usr/bin/env python3
"""Restore Windows Live Writer code snippets from archived source HTML.

Older posts contain Windows Live Writer ``wlWriterSmartContent`` blocks whose
code is represented as an ordered list: each ``<li>`` is one source line and
leading indentation is encoded with non-breaking spaces. A normal
HTML-to-Markdown conversion turns those blocks into Markdown numbered lists and
loses the significance of the leading spaces.

This repair pass uses generated-manifest.json to find each post's archived HTML,
extracts the original code lines (including leading indentation), and replaces
the corresponding ``Code Snippet`` section in generated Markdown with a fenced
code block. It is safe to run after the older numbered-list repair too: an
existing fenced block following ``Code Snippet`` is replaced from source.
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import re
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, Tag

DEFAULT_MARKDOWN_DIR = Path("generated-markdown")
DEFAULT_MANIFEST = Path("generated-manifest.json")
DEFAULT_CRAWL_ROOT = Path("crawl-output")
NUMBERED = re.compile(r"^\s*\d+\.\s?.*$")
FENCE = re.compile(r"^```(?P<language>[A-Za-z0-9_+.#-]*)\s*$")


def infer_language(lines: list[str]) -> str:
    text = "\n".join(lines)
    lower = text.lower()

    if "<asp:" in lower or "runat=\"server\"" in lower:
        return "xml"
    if "<script" in lower:
        return "html"
    if re.search(
        r"\b(protected|public|private|internal|class|void|string|bool|"
        r"servervalidateeventargs|customvalidator)\b",
        text,
    ):
        return "csharp"
    if re.search(r"\b(function|document\.getelementbyid|var\s+|args\.isvalid|innertext)\b", lower):
        return "javascript"
    if re.search(r"</?[A-Za-z][^>]*>", text):
        return "xml"
    return "text"


def resolve_html_path(manifest_path: Path, html_file: str) -> Path:
    # generated-manifest stores paths relative to crawl-output and may contain
    # Windows separators even when this script is run elsewhere.
    relative = Path(html_file.replace("\\", "/"))
    return DEFAULT_CRAWL_ROOT / relative


def is_code_snippet(container: Tag) -> bool:
    if "wlWriterSmartContent" not in (container.get("class") or []):
        return False
    ol = container.find("ol")
    if not ol:
        return False
    # The first nested div is the Live Writer title strip in these widgets.
    return any(
        node.get_text(" ", strip=True).lower() == "code snippet"
        for node in container.find_all("div")
    )


def extract_source_snippets(html_path: Path) -> list[tuple[str, list[str]]]:
    raw = html_path.read_text(encoding="utf-8", errors="replace")
    # Older crawl output may contain doubled carriage returns. Normalizing here
    # keeps this repair independent of normalize_captured_html.py.
    raw = raw.replace("\r\r\n", "\r\n").replace("\r\r", "\r")
    soup = BeautifulSoup(raw, "html.parser")
    article = soup.select_one("article.post")
    body = article.select_one(".post-body") if article else None
    if not body:
        return []

    snippets: list[tuple[str, list[str]]] = []
    for container in body.select("div.wlWriterSmartContent"):
        if not is_code_snippet(container):
            continue
        ol = container.find("ol")
        if not ol:
            continue

        lines: list[str] = []
        for li in ol.find_all("li", recursive=False):
            # separator='' is deliberate: styled spans are fragments of the same
            # source line and should not acquire spaces between them.
            line = li.get_text("", strip=False)
            line = html_lib.unescape(line).replace("\xa0", " ")
            # Live Writer commonly appends one layout space to every line. Keep
            # leading indentation exactly, but discard trailing layout whitespace.
            lines.append(line.rstrip())

        # Preserve internal blank lines but remove widget-only blank padding.
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        if lines:
            snippets.append((infer_language(lines), lines))

    return snippets


def replace_markdown_snippets(text: str, snippets: list[tuple[str, list[str]]]) -> tuple[str, int]:
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    snippet_index = 0
    repaired = 0

    while i < len(lines):
        if lines[i].strip().lower() != "code snippet" or snippet_index >= len(snippets):
            out.append(lines[i])
            i += 1
            continue

        out.append(lines[i])
        i += 1

        # Preserve one logical blank separator after the label, regardless of
        # how many the prior conversion produced.
        while i < len(lines) and not lines[i].strip():
            i += 1
        out.append("")

        language, source_lines = snippets[snippet_index]

        # Remove either the old numbered-list representation or a fenced block
        # produced by an earlier version of this repair script.
        if i < len(lines) and FENCE.match(lines[i]):
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                i += 1
            if i < len(lines):
                i += 1
        else:
            while i < len(lines) and NUMBERED.match(lines[i]):
                i += 1

        out.append(f"```{language}")
        out.extend(source_lines)
        out.append("```")
        out.append("")
        snippet_index += 1
        repaired += 1

    result = "\n".join(out)
    if text.endswith("\n"):
        result += "\n"
    return result, repaired


def load_manifest(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("generated manifest must contain a list")
    return data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--markdown-dir", type=Path, default=DEFAULT_MARKDOWN_DIR)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--crawl-root", type=Path, default=DEFAULT_CRAWL_ROOT)
    args = parser.parse_args()

    global DEFAULT_CRAWL_ROOT
    DEFAULT_CRAWL_ROOT = args.crawl_root

    manifest = load_manifest(args.manifest)
    by_slug = {
        str(entry.get("slug")): entry
        for entry in manifest
        if entry.get("conversion_status") == "ok" and entry.get("slug")
    }

    changed_files = 0
    total_repaired = 0
    source_snippets = 0
    mismatches: list[str] = []

    for markdown_path in sorted(args.markdown_dir.glob("*.md")):
        entry = by_slug.get(markdown_path.stem)
        if not entry or not entry.get("html_file"):
            continue

        html_path = resolve_html_path(args.manifest, str(entry["html_file"]))
        if not html_path.exists():
            mismatches.append(f"{markdown_path.name}: archived HTML not found: {html_path}")
            continue

        snippets = extract_source_snippets(html_path)
        if not snippets:
            continue
        source_snippets += len(snippets)

        original = markdown_path.read_text(encoding="utf-8")
        updated, repaired = replace_markdown_snippets(original, snippets)
        if repaired != len(snippets):
            mismatches.append(
                f"{markdown_path.name}: source has {len(snippets)} snippet(s), Markdown matched {repaired}"
            )
        if updated != original:
            markdown_path.write_text(updated, encoding="utf-8")
            changed_files += 1
        total_repaired += repaired
        print(f"{markdown_path.name}: restored {repaired}/{len(snippets)} source snippet(s)")

    print(f"Files changed:            {changed_files}")
    print(f"Source snippets found:    {source_snippets}")
    print(f"Code snippets restored:   {total_repaired}")
    print(f"Mismatches:               {len(mismatches)}")
    for item in mismatches:
        print(f"WARNING: {item}")

    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
