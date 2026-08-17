#!/usr/bin/env python3
"""Install source validation into a staged coding-blog repository."""

from __future__ import annotations

import argparse
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("destination", type=Path, help="Path to local coding-blog checkout")
    return p.parse_args()


VALIDATOR = r'''#!/usr/bin/env python3
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import markdown
import yaml

ROOT = Path(__file__).resolve().parent.parent
POSTS = ROOT / "posts"
REQUIRED_FIELDS = ("title", "date", "description", "tags", "slug", "author", "originalUrl", "legacyPaths")
DATED_LEGACY_RE = re.compile(r"^/post/\d{4}/\d{2}/\d{2}/[^/]+/?$", re.I)
LINK_RE = re.compile(r"!?\[[^\]]*\]\((?P<url>[^)\s]+)")
HTML_URL_RE = re.compile(r"(?:src|href)=[\"'](?P<url>[^\"']+)[\"']", re.I)
LEGACY_HOSTS = {"coding.infoconex.com", "www.coding.infoconex.com"}


def read_post(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError("missing YAML frontmatter")
    parts = text.split("---", 2)
    if len(parts) != 3:
        raise ValueError("invalid YAML frontmatter")
    data = yaml.safe_load(parts[1]) or {}
    if not isinstance(data, dict):
        raise ValueError("frontmatter is not a mapping")
    return data, parts[2].lstrip("\r\n")


def urls(body: str) -> list[str]:
    found = [m.group("url") for m in LINK_RE.finditer(body)]
    found.extend(m.group("url") for m in HTML_URL_RE.finditer(body))
    return found


def is_legacy_host_link(url: str) -> bool:
    parsed = urlsplit(url)
    return (parsed.hostname or "").lower() in LEGACY_HOSTS and parsed.path.lower().startswith("/post/")


def main() -> int:
    files = sorted(POSTS.glob("*/index.md")) if POSTS.exists() else []
    errors: list[str] = []
    warnings: list[str] = []
    slugs: list[str] = []
    legacy_owners: dict[str, list[str]] = defaultdict(list)
    post_bodies: dict[str, str] = {}

    if not files:
        errors.append("no posts/<slug>/index.md files found")

    for index_md in files:
        try:
            data, body = read_post(index_md)
        except Exception as exc:
            errors.append(f"{index_md}: {exc}")
            continue

        post_bodies[str(index_md)] = body
        missing = [field for field in REQUIRED_FIELDS if field not in data]
        if missing:
            errors.append(f"{index_md}: missing fields: {', '.join(missing)}")

        slug = str(data.get("slug") or "")
        slugs.append(slug)
        if not slug:
            errors.append(f"{index_md}: empty slug")
        elif index_md.parent.name != slug:
            errors.append(f"{index_md}: directory name must match slug {slug!r}")

        raw_date = str(data.get("date") or "")
        try:
            date.fromisoformat(raw_date)
        except ValueError:
            errors.append(f"{index_md}: invalid ISO date {raw_date!r}")

        if not isinstance(data.get("tags"), list):
            errors.append(f"{index_md}: tags must be a list")

        legacy_paths = data.get("legacyPaths")
        if not isinstance(legacy_paths, list) or not legacy_paths:
            errors.append(f"{index_md}: legacyPaths must be a non-empty list")
            legacy_paths = []
        for legacy in legacy_paths:
            legacy = str(legacy)
            if not DATED_LEGACY_RE.match(legacy):
                errors.append(f"{index_md}: invalid legacyPath {legacy}")
            legacy_owners[legacy].append(slug or index_md.parent.name)

        original = str(data.get("originalUrl") or "")
        parsed = urlsplit(original)
        if parsed.hostname not in LEGACY_HOSTS:
            errors.append(f"{index_md}: unexpected originalUrl host: {original}")

        try:
            markdown.markdown(body, extensions=["fenced_code", "tables", "sane_lists", "attr_list"], output_format="html5")
        except Exception as exc:
            errors.append(f"{index_md}: Markdown render failed: {exc}")

        for url in urls(body):
            clean = url.split("#", 1)[0].split("?", 1)[0]
            if not clean:
                continue
            if re.match(r"^[A-Za-z]:\\", clean):
                errors.append(f"{index_md}: absolute Windows path: {url}")
            if clean.startswith("images/"):
                target = index_md.parent / clean
                if not target.is_file():
                    errors.append(f"{index_md}: missing local image: {clean}")
            if clean.startswith("/images/posts/"):
                errors.append(f"{index_md}: old generated image URL remains: {url}")
            if re.match(r"^/post/\d{4}/\d{2}/\d{2}/", clean, re.I):
                errors.append(f"{index_md}: dated legacy link remains in body: {url}")
            if is_legacy_host_link(url):
                errors.append(f"{index_md}: legacy-host link remains in body: {url}")

        image_dir = index_md.parent / "images"
        if image_dir.exists():
            referenced = {
                (index_md.parent / u.split("#", 1)[0].split("?", 1)[0]).resolve()
                for u in urls(body)
                if u.startswith("images/")
            }
            for image in image_dir.rglob("*"):
                if image.is_file() and image.resolve() not in referenced:
                    warnings.append(f"{index_md}: orphaned image: {image.relative_to(index_md.parent)}")

    for slug, count in Counter(slugs).items():
        if slug and count > 1:
            errors.append(f"duplicate slug: {slug}")

    for legacy, owners in sorted(legacy_owners.items()):
        if len(set(owners)) > 1:
            errors.append(f"legacyPath collision: {legacy} -> {', '.join(sorted(set(owners)))}")

    known_slugs = {s for s in slugs if s}
    for path, body in post_bodies.items():
        for url in urls(body):
            clean = url.split("#", 1)[0].split("?", 1)[0].rstrip("/")
            match = re.match(r"^/post/([^/]+)$", clean)
            if match and match.group(1) not in known_slugs:
                errors.append(f"{path}: broken canonical internal link: {url}")

    print(f"Posts checked:             {len(files)}")
    print(f"Unique slugs:              {len(known_slugs)}")
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
'''

REQUIREMENTS = "Markdown>=3.7,<4\nPyYAML>=6,<7\n"


def main() -> int:
    args = parse_args()
    dest = args.destination.resolve()
    if not (dest / ".git").exists():
        raise SystemExit(f"Destination is not a Git checkout: {dest}")

    scripts = dest / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "validate.py").write_text(VALIDATOR, encoding="utf-8")
    (scripts / "requirements.txt").write_text(REQUIREMENTS, encoding="utf-8")

    # Remove the old root validator if it was installed by an earlier version.
    old = dest / "validate.py"
    if old.exists():
        old.unlink()

    print(f"Installed source validator: {scripts / 'validate.py'}")
    print(f"Validator dependencies:     {scripts / 'requirements.txt'}")
    print("Run with:                   python scripts/validate.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
