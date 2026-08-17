#!/usr/bin/env python3
"""Finalize and audit generated historical blog Markdown.

Rewrites internal BlogEngine links to the destination /post/<slug> routes and
writes a reconciliation report. Source URLs that were discovered but could not
be captured (for example HTTP 404) are reported rather than silently ignored.
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


DEFAULT_CRAWL_MANIFEST = Path("crawl-output/manifest.json")
DEFAULT_GENERATED_MANIFEST = Path("generated-manifest.json")
DEFAULT_MARKDOWN_DIR = Path("generated-markdown")
DEFAULT_REPORT = Path("finalization-report.json")
LEGACY_HOSTS = {"coding.infoconex.com", "www.coding.infoconex.com"}


def slugify(value: str) -> str:
    value = html_lib.unescape(value or "")
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = value.lower().replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return re.sub(r"-{2,}", "-", value).strip("-") or "post"


def is_legacy_post_link(url_or_path: str) -> bool:
    """Return True only for old BlogEngine post URLs.

    Absolute links to coding.infoconex.com are legacy. Relative /post/... links
    are legacy only when they use the old dated /post/YYYY/MM/DD/... shape.
    This deliberately treats the new /post/<slug> route as valid destination
    content rather than an unresolved legacy link.
    """
    value = html_lib.unescape((url_or_path or "").strip())
    if not value:
        return False

    if value.startswith("//"):
        value = "http:" + value

    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        return (parsed.hostname or "").lower() in LEGACY_HOSTS and parsed.path.lower().startswith("/post/")

    path = value.split("?", 1)[0].split("#", 1)[0]
    return bool(re.match(r"^/post/\d{4}/\d{2}/\d{2}/", path, re.I))


def normalize_legacy_path(url_or_path: str) -> str | None:
    value = html_lib.unescape((url_or_path or "").strip())
    if not value or not is_legacy_post_link(value):
        return None

    if value.startswith("//"):
        value = "http:" + value

    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        path = parsed.path
    else:
        path = value.split("?", 1)[0].split("#", 1)[0]

    path = re.sub(r"/{2,}", "/", path).rstrip("/")
    path = re.sub(r"\.aspx$", "", path, flags=re.I)
    return path.lower()


def build_route_map(crawl: list[dict[str, Any]]) -> dict[str, str]:
    route_map: dict[str, str] = {}
    for entry in crawl:
        if entry.get("status") != 200 or not entry.get("html_file"):
            continue
        path = normalize_legacy_path(entry.get("url") or "")
        if not path:
            continue
        route_map[path] = f"/post/{slugify(entry.get('title') or '')}"
    return route_map


def rewrite_markdown(text: str, route_map: dict[str, str]) -> tuple[str, int, set[str]]:
    rewritten = 0
    unresolved: set[str] = set()

    md_pattern = re.compile(
        r"(?P<prefix>!?\[[^\]]*\]\()(?P<url>(?:https?://(?:www\.)?coding\.infoconex\.com)?/post/[^)\s]+)",
        re.I,
    )

    def md_repl(match: re.Match[str]) -> str:
        nonlocal rewritten
        raw = match.group("url")
        if not is_legacy_post_link(raw):
            return match.group(0)
        path = normalize_legacy_path(raw)
        if path and path in route_map:
            rewritten += 1
            return match.group("prefix") + route_map[path]
        unresolved.add(raw)
        return match.group(0)

    text = md_pattern.sub(md_repl, text)

    html_pattern = re.compile(
        r'(?P<prefix>href=["\'])(?P<url>(?:https?://(?:www\.)?coding\.infoconex\.com)?/post/[^"\']+)(?P<suffix>["\'])',
        re.I,
    )

    def html_repl(match: re.Match[str]) -> str:
        nonlocal rewritten
        raw = match.group("url")
        if not is_legacy_post_link(raw):
            return match.group(0)
        path = normalize_legacy_path(raw)
        if path and path in route_map:
            rewritten += 1
            return match.group("prefix") + route_map[path] + match.group("suffix")
        unresolved.add(raw)
        return match.group(0)

    text = html_pattern.sub(html_repl, text)
    return text, rewritten, unresolved


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Rewrite internal legacy blog links and audit generated output.")
    p.add_argument("--crawl-manifest", type=Path, default=DEFAULT_CRAWL_MANIFEST)
    p.add_argument("--generated-manifest", type=Path, default=DEFAULT_GENERATED_MANIFEST)
    p.add_argument("--markdown-dir", type=Path, default=DEFAULT_MARKDOWN_DIR)
    p.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    p.add_argument("--check-only", action="store_true", help="Audit without modifying Markdown files.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    for required in (args.crawl_manifest, args.generated_manifest):
        if not required.exists():
            print(f"Required file not found: {required}")
            return 2
    if not args.markdown_dir.exists():
        print(f"Markdown directory not found: {args.markdown_dir}")
        return 2

    crawl = json.loads(args.crawl_manifest.read_text(encoding="utf-8"))
    generated = json.loads(args.generated_manifest.read_text(encoding="utf-8"))
    route_map = build_route_map(crawl)

    slugs = [e.get("slug") for e in generated if e.get("conversion_status") == "ok" and e.get("slug")]
    counts = Counter(slugs)
    duplicate_slugs = sorted(slug for slug, count in counts.items() if count > 1)

    unavailable = [
        {
            "url": e.get("url"),
            "title": e.get("title"),
            "status": e.get("status"),
            "html_file": e.get("html_file"),
        }
        for e in crawl
        if e.get("status") != 200 or not e.get("html_file")
    ]

    markdown_files = sorted(args.markdown_dir.glob("*.md"))
    rewritten_total = 0
    unresolved_by_file: dict[str, list[str]] = {}

    for path in markdown_files:
        original = path.read_text(encoding="utf-8")
        updated, rewritten, unresolved = rewrite_markdown(original, route_map)
        rewritten_total += rewritten
        if unresolved:
            unresolved_by_file[path.name] = sorted(unresolved)
        if updated != original and not args.check_only:
            path.write_text(updated, encoding="utf-8")

    report = {
        "crawl_inventory_count": len(crawl),
        "captured_source_count": sum(1 for e in crawl if e.get("status") == 200 and e.get("html_file")),
        "generated_manifest_count": len(generated),
        "generated_markdown_file_count": len(markdown_files),
        "duplicate_slugs": duplicate_slugs,
        "unavailable_source_entries": unavailable,
        "internal_links_rewritten": rewritten_total,
        "unresolved_legacy_links": unresolved_by_file,
        "check_only": bool(args.check_only),
    }
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"Crawl inventory:         {len(crawl)}")
    print(f"Captured sources:        {report['captured_source_count']}")
    print(f"Generated Markdown:      {len(markdown_files)}")
    print(f"Internal links rewritten:{rewritten_total:>5}")
    print(f"Duplicate slugs:         {len(duplicate_slugs)}")
    print(f"Unavailable sources:     {len(unavailable)}")
    print(f"Files with unresolved legacy links: {len(unresolved_by_file)}")
    print(f"Report:                  {args.report}")

    if duplicate_slugs or unresolved_by_file:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
