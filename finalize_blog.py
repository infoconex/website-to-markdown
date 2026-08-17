#!/usr/bin/env python3
"""Finalize and audit generated historical blog Markdown.

Rewrites internal BlogEngine links, including previously generated /post/<slug>
links, to the confirmed historical URL of the captured destination post. Adds
legacyPaths frontmatter for confirmed historical URLs and writes a reconciliation
report. Discovered-but-unavailable URLs are reported but are not treated as
valid historical aliases.
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


def exact_historical_path(url_or_path: str) -> str | None:
    value = html_lib.unescape((url_or_path or "").strip())
    if not value or not is_legacy_post_link(value):
        return None
    if value.startswith("//"):
        value = "http:" + value
    parsed = urlsplit(value)
    path = parsed.path if parsed.scheme or parsed.netloc else value.split("?", 1)[0].split("#", 1)[0]
    return re.sub(r"/{2,}", "/", path).rstrip("/")


def normalize_legacy_path(url_or_path: str) -> str | None:
    path = exact_historical_path(url_or_path)
    if not path:
        return None
    return re.sub(r"\.aspx$", "", path, flags=re.I)


def rewrite_key(url_or_path: str) -> str | None:
    value = html_lib.unescape((url_or_path or "").strip())
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
    path = re.sub(r"/{2,}", "/", path).rstrip("/")
    if re.match(r"^/post/\d{4}/\d{2}/\d{2}/", path, re.I):
        path = re.sub(r"\.aspx$", "", path, flags=re.I)
        return path.lower()
    if re.match(r"^/post/[^/]+$", path, re.I):
        return path.lower()
    return None


def build_route_map(crawl: list[dict[str, Any]]) -> dict[str, str]:
    """Map dated and previously generated canonical routes to exact captured paths."""
    route_map: dict[str, str] = {}
    for entry in crawl:
        if entry.get("status") != 200 or not entry.get("html_file"):
            continue
        raw_url = str(entry.get("url") or "")
        lookup = normalize_legacy_path(raw_url)
        destination = exact_historical_path(raw_url)
        title = str(entry.get("title") or "")
        if lookup and destination:
            route_map[lookup.lower()] = destination
            route_map[f"/post/{slugify(title)}".lower()] = destination
    return route_map


def build_legacy_paths_by_slug(crawl: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Collect confirmed captured historical URLs for each destination slug."""
    result: dict[str, list[str]] = {}
    for entry in crawl:
        if entry.get("status") != 200 or not entry.get("html_file"):
            continue
        title = entry.get("title") or ""
        path = normalize_legacy_path(entry.get("url") or "")
        if not title or not path:
            continue
        slug = slugify(title)
        paths = result.setdefault(slug, [])
        if path not in paths:
            paths.append(path)
    return result


def set_legacy_paths_frontmatter(text: str, paths: list[str]) -> tuple[str, bool]:
    if not text.startswith("---\n"):
        return text, False
    end = text.find("\n---\n", 4)
    if end < 0:
        return text, False
    frontmatter = text[4:end]
    body = text[end + 5 :]
    value = "legacyPaths: [" + ", ".join(json.dumps(p, ensure_ascii=False) for p in paths) + "]"
    existing = re.compile(r"^legacyPaths:\s*.*$", re.M)
    if existing.search(frontmatter):
        updated_frontmatter = existing.sub(value, frontmatter)
    else:
        original_url = re.compile(r"^(originalUrl:\s*.*)$", re.M)
        if original_url.search(frontmatter):
            updated_frontmatter = original_url.sub(r"\1\n" + value, frontmatter, count=1)
        else:
            updated_frontmatter = frontmatter.rstrip() + "\n" + value
    updated = "---\n" + updated_frontmatter + "\n---\n" + body
    return updated, updated != text


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
        key = rewrite_key(raw)
        if not key:
            return match.group(0)
        destination = route_map.get(key)
        if destination:
            replacement = match.group("prefix") + destination
            if replacement != match.group(0):
                rewritten += 1
            return replacement
        if re.match(r"^/post/\d{4}/\d{2}/\d{2}/", urlsplit(raw).path or raw, re.I):
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
        key = rewrite_key(raw)
        if not key:
            return match.group(0)
        destination = route_map.get(key)
        if destination:
            replacement = match.group("prefix") + destination + match.group("suffix")
            if replacement != match.group(0):
                rewritten += 1
            return replacement
        if re.match(r"^/post/\d{4}/\d{2}/\d{2}/", urlsplit(raw).path or raw, re.I):
            unresolved.add(raw)
        return match.group(0)

    text = html_pattern.sub(html_repl, text)
    return text, rewritten, unresolved


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Finalize historical BlogEngine Markdown and audit migration output.")
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
    legacy_paths_by_slug = build_legacy_paths_by_slug(crawl)

    slugs = [e.get("slug") for e in generated if e.get("conversion_status") == "ok" and e.get("slug")]
    counts = Counter(slugs)
    duplicate_slugs = sorted(slug for slug, count in counts.items() if count > 1)

    unavailable = [
        {"url": e.get("url"), "title": e.get("title"), "status": e.get("status"), "html_file": e.get("html_file")}
        for e in crawl
        if e.get("status") != 200 or not e.get("html_file")
    ]

    markdown_files = sorted(args.markdown_dir.glob("*.md"))
    rewritten_total = 0
    legacy_paths_files_updated = 0
    unresolved_by_file: dict[str, list[str]] = {}

    for path in markdown_files:
        original = path.read_text(encoding="utf-8")
        updated, rewritten, unresolved = rewrite_markdown(original, route_map)
        rewritten_total += rewritten
        if unresolved:
            unresolved_by_file[path.name] = sorted(unresolved)

        legacy_paths = legacy_paths_by_slug.get(path.stem, [])
        updated, frontmatter_changed = set_legacy_paths_frontmatter(updated, legacy_paths)
        if frontmatter_changed:
            legacy_paths_files_updated += 1

        if updated != original and not args.check_only:
            path.write_text(updated, encoding="utf-8")

    missing_legacy_paths = sorted(path.name for path in markdown_files if path.stem not in legacy_paths_by_slug)
    report = {
        "crawl_inventory_count": len(crawl),
        "captured_source_count": sum(1 for e in crawl if e.get("status") == 200 and e.get("html_file")),
        "generated_manifest_count": len(generated),
        "generated_markdown_file_count": len(markdown_files),
        "duplicate_slugs": duplicate_slugs,
        "unavailable_source_entries": unavailable,
        "internal_links_rewritten": rewritten_total,
        "legacy_paths_files_updated": legacy_paths_files_updated,
        "missing_legacy_paths": missing_legacy_paths,
        "unresolved_legacy_links": unresolved_by_file,
        "check_only": bool(args.check_only),
    }
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"Crawl inventory:         {len(crawl)}")
    print(f"Captured sources:        {report['captured_source_count']}")
    print(f"Generated Markdown:      {len(markdown_files)}")
    print(f"Internal links rewritten:{rewritten_total:>5}")
    print(f"legacyPaths files updated:{legacy_paths_files_updated:>4}")
    print(f"Missing legacyPaths:     {len(missing_legacy_paths)}")
    print(f"Duplicate slugs:         {len(duplicate_slugs)}")
    print(f"Unavailable sources:     {len(unavailable)}")
    print(f"Files with unresolved legacy links: {len(unresolved_by_file)}")
    print(f"Report:                  {args.report}")

    if duplicate_slugs or unresolved_by_file or missing_legacy_paths:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
