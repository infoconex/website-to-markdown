#!/usr/bin/env python3
"""Rewrite known historical post aliases to canonical /post/<slug> links.

Unlike finalize_blog.py's primary route map, this includes discovered historical
URLs that were unavailable at crawl time when their title resolves to a captured
post slug. This covers known aliases such as the unavailable 2023 SOLID URL that
belongs to the recovered 2025 article.
"""

from __future__ import annotations

import html as html_lib
import json
import re
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

CRAWL = Path("crawl-output/manifest.json")
GENERATED = Path("generated-manifest.json")
MARKDOWN_DIR = Path("generated-markdown")
LEGACY_HOSTS = {"coding.infoconex.com", "www.coding.infoconex.com"}


def slugify(value: str) -> str:
    value = html_lib.unescape(value or "")
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = value.lower().replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return re.sub(r"-{2,}", "-", value).strip("-") or "post"


def normalize_legacy_path(value: str) -> str | None:
    value = html_lib.unescape((value or "").strip())
    if not value:
        return None
    if value.startswith("//"):
        value = "http:" + value
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        if (parsed.hostname or "").lower() not in LEGACY_HOSTS:
            return None
        path = parsed.path
    else:
        path = value.split("?", 1)[0].split("#", 1)[0]
    if not re.match(r"^/post/\d{4}/\d{2}/\d{2}/", path, re.I):
        return None
    path = re.sub(r"/{2,}", "/", path).rstrip("/")
    return re.sub(r"\.aspx$", "", path, flags=re.I)


def main() -> int:
    crawl = json.loads(CRAWL.read_text(encoding="utf-8"))
    generated = json.loads(GENERATED.read_text(encoding="utf-8"))
    captured_slugs = {
        str(e.get("slug"))
        for e in generated
        if e.get("conversion_status") == "ok" and e.get("slug")
    }

    route_map: dict[str, str] = {}
    for entry in crawl:
        slug = slugify(str(entry.get("title") or ""))
        path = normalize_legacy_path(str(entry.get("url") or ""))
        if slug in captured_slugs and path:
            route_map[path.lower()] = f"/post/{slug}"

    md_pattern = re.compile(
        r"(?P<prefix>!?\[[^\]]*\]\()(?P<url>(?:https?://(?:www\.)?coding\.infoconex\.com)?/post/[^)\s]+)",
        re.I,
    )
    html_pattern = re.compile(
        r'(?P<prefix>href=["\'])(?P<url>(?:https?://(?:www\.)?coding\.infoconex\.com)?/post/[^"\']+)(?P<suffix>["\'])',
        re.I,
    )

    total = 0
    files_changed = 0
    for path in sorted(MARKDOWN_DIR.glob("*.md")):
        original = path.read_text(encoding="utf-8")

        def md_repl(match: re.Match[str]) -> str:
            nonlocal total
            legacy = normalize_legacy_path(match.group("url"))
            destination = route_map.get((legacy or "").lower())
            if not destination:
                return match.group(0)
            total += 1
            return match.group("prefix") + destination

        def html_repl(match: re.Match[str]) -> str:
            nonlocal total
            legacy = normalize_legacy_path(match.group("url"))
            destination = route_map.get((legacy or "").lower())
            if not destination:
                return match.group(0)
            total += 1
            return match.group("prefix") + destination + match.group("suffix")

        updated = md_pattern.sub(md_repl, original)
        updated = html_pattern.sub(html_repl, updated)
        if updated != original:
            path.write_text(updated, encoding="utf-8")
            files_changed += 1

    print(f"Known alias routes:       {len(route_map)}")
    print(f"Files changed:            {files_changed}")
    print(f"Links rewritten:          {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
