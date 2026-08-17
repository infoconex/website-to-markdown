#!/usr/bin/env python3
"""Rewrite confirmed historical post links to their exact captured paths.

Only successfully captured crawl entries are treated as valid historical routes.
Discovered-but-unavailable URLs are not assumed to have been published aliases.
"""

from __future__ import annotations

import html as html_lib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

CRAWL = Path("crawl-output/manifest.json")
MARKDOWN_DIR = Path("generated-markdown")
LEGACY_HOSTS = {"coding.infoconex.com", "www.coding.infoconex.com"}


def exact_path(value: str) -> str | None:
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
    return re.sub(r"/{2,}", "/", path).rstrip("/")


def lookup_key(value: str) -> str | None:
    path = exact_path(value)
    if not path:
        return None
    return re.sub(r"\.aspx$", "", path, flags=re.I).lower()


def main() -> int:
    crawl = json.loads(CRAWL.read_text(encoding="utf-8"))
    route_map: dict[str, str] = {}
    for entry in crawl:
        if entry.get("status") != 200 or not entry.get("html_file"):
            continue
        raw = str(entry.get("url") or "")
        key = lookup_key(raw)
        destination = exact_path(raw)
        if key and destination:
            route_map[key] = destination

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
            destination = route_map.get(lookup_key(match.group("url")) or "")
            if not destination:
                return match.group(0)
            replacement = match.group("prefix") + destination
            if replacement != match.group(0):
                total += 1
            return replacement

        def html_repl(match: re.Match[str]) -> str:
            nonlocal total
            destination = route_map.get(lookup_key(match.group("url")) or "")
            if not destination:
                return match.group(0)
            replacement = match.group("prefix") + destination + match.group("suffix")
            if replacement != match.group(0):
                total += 1
            return replacement

        updated = md_pattern.sub(md_repl, original)
        updated = html_pattern.sub(html_repl, updated)
        if updated != original:
            path.write_text(updated, encoding="utf-8")
            files_changed += 1

    print(f"Confirmed historical routes: {len(route_map)}")
    print(f"Files changed:               {files_changed}")
    print(f"Links rewritten:             {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
