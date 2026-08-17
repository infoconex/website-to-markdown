#!/usr/bin/env python3
"""Validate finalized historical blog Markdown before staging it into coding-blog."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import yaml

MARKDOWN_DIR = Path("generated-markdown")
ASSET_ROOT = Path("generated-assets/images/posts")
REQUIRED_FIELDS = ("title", "date", "description", "tags", "slug", "author", "originalUrl", "legacyPaths")
DATED_POST_RE = re.compile(r"^/post/\d{4}/\d{2}/\d{2}/[^/]+/?$", re.I)
MD_LINK_RE = re.compile(r"!?\[[^\]]*\]\((?P<url>[^)\s]+)")
HTML_SRC_RE = re.compile(r"(?:src|href)=[\"'](?P<url>[^\"']+)[\"']", re.I)
LEGACY_HOSTS = {"coding.infoconex.com", "www.coding.infoconex.com"}


def read_frontmatter(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError("missing YAML frontmatter")
    parts = text.split("---", 2)
    if len(parts) != 3:
        raise ValueError("invalid YAML frontmatter delimiters")
    data = yaml.safe_load(parts[1]) or {}
    if not isinstance(data, dict):
        raise ValueError("frontmatter is not a mapping")
    return data, parts[2].lstrip("\r\n")


def local_urls(body: str) -> list[str]:
    urls = [m.group("url") for m in MD_LINK_RE.finditer(body)]
    urls.extend(m.group("url") for m in HTML_SRC_RE.finditer(body))
    return urls


def is_legacy_host_link(url: str) -> bool:
    value = (url or "").strip()
    if value.startswith("//"):
        value = "http:" + value
    parsed = urlsplit(value)
    return (parsed.hostname or "").lower() in LEGACY_HOSTS and parsed.path.lower().startswith("/post/")


def route_key(path: str) -> str:
    clean = path.split("#", 1)[0].split("?", 1)[0].rstrip("/")
    clean = re.sub(r"\.aspx$", "", clean, flags=re.I)
    return clean.lower()


def main() -> int:
    if not MARKDOWN_DIR.exists():
        print(f"ERROR: Markdown directory not found: {MARKDOWN_DIR}")
        return 2

    files = sorted(MARKDOWN_DIR.glob("*.md"))
    errors: list[str] = []
    warnings: list[str] = []
    slugs: list[str] = []
    legacy_owners: dict[str, list[str]] = defaultdict(list)
    bodies: dict[str, str] = {}
    public_routes: dict[str, str] = {}

    for path in files:
        try:
            data, body = read_frontmatter(path)
        except Exception as exc:
            errors.append(f"{path.name}: {exc}")
            continue

        bodies[path.name] = body
        missing = [field for field in REQUIRED_FIELDS if field not in data]
        if missing:
            errors.append(f"{path.name}: missing frontmatter fields: {', '.join(missing)}")

        slug = str(data.get("slug") or "")
        if not slug:
            errors.append(f"{path.name}: empty slug")
        else:
            slugs.append(slug)
            if path.stem != slug:
                errors.append(f"{path.name}: filename stem does not match slug {slug!r}")

        raw_date = str(data.get("date") or "")
        try:
            date.fromisoformat(raw_date)
        except ValueError:
            errors.append(f"{path.name}: invalid ISO date {raw_date!r}")

        if not isinstance(data.get("tags"), list):
            errors.append(f"{path.name}: tags must be a list")

        legacy_paths = data.get("legacyPaths")
        if not isinstance(legacy_paths, list) or not legacy_paths:
            errors.append(f"{path.name}: legacyPaths must be a non-empty list")
            legacy_paths = []
        for legacy in legacy_paths:
            legacy = str(legacy)
            if not DATED_POST_RE.match(legacy):
                errors.append(f"{path.name}: legacyPath is not dated BlogEngine form: {legacy}")
            legacy_owners[legacy].append(slug or path.stem)

        original_url = str(data.get("originalUrl") or "")
        parsed = urlsplit(original_url)
        if parsed.hostname not in LEGACY_HOSTS:
            errors.append(f"{path.name}: unexpected originalUrl host: {original_url}")
        elif not DATED_POST_RE.match(parsed.path):
            errors.append(f"{path.name}: originalUrl is not a dated BlogEngine post URL: {original_url}")
        else:
            key = route_key(parsed.path)
            owner = public_routes.get(key)
            if owner and owner != path.name:
                errors.append(f"historical public path collision: {parsed.path} -> {owner}, {path.name}")
            public_routes[key] = path.name

        for url in local_urls(body):
            clean = url.split("#", 1)[0].split("?", 1)[0]
            if not clean:
                continue
            if re.match(r"^[A-Za-z]:\\", clean):
                errors.append(f"{path.name}: absolute Windows path in content: {url}")
            if clean.startswith("/images/posts/"):
                slug_prefix = f"/images/posts/{slug}/"
                if not clean.startswith(slug_prefix):
                    errors.append(f"{path.name}: post image points at another slug or malformed path: {url}")
                else:
                    rel = clean[len(slug_prefix):]
                    asset = ASSET_ROOT / slug / rel
                    if not asset.is_file():
                        errors.append(f"{path.name}: missing image asset: {clean}")
            if is_legacy_host_link(url):
                errors.append(f"{path.name}: historical-host link should be root-relative: {url}")

    for slug, count in Counter(slugs).items():
        if count > 1:
            errors.append(f"duplicate slug: {slug}")

    for legacy, owners in sorted(legacy_owners.items()):
        if len(set(owners)) > 1:
            errors.append(f"legacyPath collision: {legacy} -> {', '.join(sorted(set(owners)))}")

    for name, body in bodies.items():
        for url in local_urls(body):
            clean = url.split("#", 1)[0].split("?", 1)[0]
            if DATED_POST_RE.match(clean) and route_key(clean) not in public_routes:
                errors.append(f"{name}: broken historical internal link: {url}")
            elif re.match(r"^/post/[^/]+/?$", clean, re.I):
                errors.append(f"{name}: non-historical canonical post link remains: {url}")

    print(f"Posts checked:             {len(files)}")
    print(f"Unique slugs:              {len(set(slugs))}")
    print(f"Confirmed public paths:    {len(public_routes)}")
    print(f"Unique legacy paths:       {len(legacy_owners)}")
    print(f"Errors:                    {len(errors)}")
    print(f"Warnings:                  {len(warnings)}")

    for message in warnings:
        print(f"WARNING: {message}")
    for message in errors:
        print(f"ERROR: {message}")

    if errors:
        print("Validation:                FAILED")
        return 1
    print("Validation:                PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
