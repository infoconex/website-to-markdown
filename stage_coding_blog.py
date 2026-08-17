#!/usr/bin/env python3
"""Stage finalized historical blog content into a GitHub Pages source repository.

Destination layout mirrors the confirmed historical BlogEngine hierarchy:

  posts/YYYY/MM/DD/<historical-slug>/index.md
  posts/YYYY/MM/DD/<historical-slug>/images/*
  assets/images/
  assets/js/
  assets/css/

Post-specific images remain beside each post. Each staged Markdown file receives
an exact permalink derived from originalUrl so the published GitHub Pages URL can
match the historical URL rather than requiring a redirect.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path
from urllib.parse import urlsplit

SOURCE_MD = Path("generated-markdown")
SOURCE_ASSETS = Path("generated-assets/images/posts")
REPORT = Path("finalization-report.json")
EXPECTED_POSTS = 61


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("destination", type=Path, help="Path to local coding-blog checkout")
    p.add_argument("--clean-posts", action="store_true", help="Remove destination posts/ before staging")
    return p.parse_args()


def validate_source() -> None:
    if not REPORT.exists():
        raise SystemExit("finalization-report.json not found; run finalize_blog.py first")
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    if report.get("duplicate_slugs"):
        raise SystemExit("Migration report still has duplicate slugs")
    if report.get("unresolved_legacy_links"):
        raise SystemExit("Migration report still has unresolved legacy links")
    if report.get("generated_markdown_file_count") != EXPECTED_POSTS:
        raise SystemExit(
            f"Expected {EXPECTED_POSTS} generated Markdown files, "
            f"found {report.get('generated_markdown_file_count')}"
        )


def frontmatter_value(text: str, key: str) -> str | None:
    m = re.search(rf'^\s*{re.escape(key)}:\s*["\']?([^"\'\n]+)["\']?\s*$', text, re.M)
    return m.group(1).strip() if m else None


def legacy_paths(text: str) -> list[str]:
    inline = re.search(r"^legacyPaths:\s*(\[[^\n]*\])\s*$", text, re.M)
    if inline:
        try:
            value = json.loads(inline.group(1))
            if isinstance(value, list):
                return [str(item) for item in value if item]
        except json.JSONDecodeError:
            pass

    block = re.search(r"^legacyPaths:\s*\n(?P<body>(?:\s+-\s+.*\n?)+)", text, re.M)
    if not block:
        return []
    out: list[str] = []
    for line in block.group("body").splitlines():
        m = re.match(r'\s*-\s*["\']?(.*?)["\']?\s*$', line)
        if m and m.group(1):
            out.append(m.group(1))
    return out


def exact_public_path(text: str) -> str:
    original_url = frontmatter_value(text, "originalUrl")
    if not original_url:
        raise SystemExit("Missing originalUrl; cannot preserve historical public URL")
    path = urlsplit(original_url).path
    if not re.match(r"^/post/\d{4}/\d{2}/\d{2}/[^/]+$", path, re.I):
        raise SystemExit(f"Unexpected originalUrl path: {path}")
    return path


def historical_source_path(text: str) -> str:
    """Return /post/YYYY/MM/DD/slug for the source folder hierarchy."""
    path = exact_public_path(text)
    return re.sub(r"\.aspx$", "", path, flags=re.I).rstrip("/")


def set_permalink(text: str, permalink: str) -> str:
    line = f'permalink: "{permalink}"'
    existing = re.compile(r"^permalink:\s*.*$", re.M)
    if existing.search(text):
        return existing.sub(line, text, count=1)

    original_url = re.compile(r"^(originalUrl:\s*.*)$", re.M)
    if not original_url.search(text):
        raise SystemExit("Cannot add permalink: originalUrl frontmatter not found")
    return original_url.sub(r"\1\n" + line, text, count=1)


def rewrite_post_image_references(text: str, slug: str) -> str:
    prefixes = (
        f"/images/posts/{slug}/",
        f"images/posts/{slug}/",
    )
    for prefix in prefixes:
        text = text.replace(prefix, "images/")
    return text


def ensure_global_asset_dirs(dest: Path) -> None:
    for relative in (Path("assets/images"), Path("assets/js"), Path("assets/css")):
        directory = dest / relative
        directory.mkdir(parents=True, exist_ok=True)
        keep = directory / ".gitkeep"
        if not keep.exists():
            keep.write_text("", encoding="utf-8")


def source_dir_for_historical_path(posts_root: Path, historical_path: str) -> Path:
    parts = [part for part in historical_path.strip("/").split("/") if part]
    if len(parts) != 5 or parts[0].lower() != "post":
        raise SystemExit(f"Unexpected historical post path: {historical_path}")
    _, year, month, day, historical_slug = parts
    return posts_root / year / month / day / historical_slug


def stage_posts(dest: Path, clean_posts: bool) -> tuple[int, int]:
    posts_root = dest / "posts"
    if clean_posts and posts_root.exists():
        shutil.rmtree(posts_root)
    posts_root.mkdir(parents=True, exist_ok=True)

    md_files = sorted(SOURCE_MD.glob("*.md"))
    if len(md_files) != EXPECTED_POSTS:
        raise SystemExit(f"Expected {EXPECTED_POSTS} Markdown files in {SOURCE_MD}, found {len(md_files)}")

    image_count = 0
    seen_public_paths: set[str] = set()

    for src in md_files:
        original = src.read_text(encoding="utf-8")
        slug = frontmatter_value(original, "slug") or src.stem
        public_path = exact_public_path(original)
        source_path = historical_source_path(original)

        key = public_path.lower()
        if key in seen_public_paths:
            raise SystemExit(f"Historical public path collision: {public_path}")
        seen_public_paths.add(key)

        post_dir = source_dir_for_historical_path(posts_root, source_path)
        post_dir.mkdir(parents=True, exist_ok=True)

        rewritten = rewrite_post_image_references(original, slug)
        rewritten = set_permalink(rewritten, public_path)
        (post_dir / "index.md").write_text(rewritten, encoding="utf-8")

        source_images = SOURCE_ASSETS / slug
        if source_images.exists():
            destination_images = post_dir / "images"
            if destination_images.exists():
                shutil.rmtree(destination_images)
            shutil.copytree(source_images, destination_images)
            image_count += sum(1 for p in destination_images.rglob("*") if p.is_file())

    return len(md_files), image_count


def audit_destination(dest: Path) -> None:
    post_files = sorted((dest / "posts").glob("*/*/*/*/index.md"))
    if len(post_files) != EXPECTED_POSTS:
        raise SystemExit(f"Destination audit expected {EXPECTED_POSTS} posts, found {len(post_files)}")

    bad_refs: list[str] = []
    missing_images: list[str] = []
    missing_permalinks: list[str] = []

    for index_md in post_files:
        text = index_md.read_text(encoding="utf-8")
        if not frontmatter_value(text, "permalink"):
            missing_permalinks.append(str(index_md))
        if "/images/posts/" in text:
            bad_refs.append(str(index_md))

        for match in re.finditer(r'(?:!\[[^\]]*\]\(|(?:src|href)=["\'])(images/[^)"\'\s]+)', text, re.I):
            relative = match.group(1)
            target = index_md.parent / relative
            if not target.exists():
                missing_images.append(f"{index_md}: {relative}")

    if missing_permalinks:
        raise SystemExit("Found staged posts without permalink:\n" + "\n".join(missing_permalinks))
    if bad_refs:
        raise SystemExit("Found unre-written /images/posts references:\n" + "\n".join(bad_refs))
    if missing_images:
        raise SystemExit("Found missing local post images:\n" + "\n".join(missing_images[:20]))

    redirect_manifest = dest / "legacy-redirects.json"
    if redirect_manifest.exists():
        redirect_manifest.unlink()


def main() -> int:
    args = parse_args()
    dest = args.destination.resolve()
    validate_source()

    if not (dest / ".git").exists():
        raise SystemExit(f"Destination is not a Git checkout: {dest}")

    ensure_global_asset_dirs(dest)
    post_count, image_count = stage_posts(dest, args.clean_posts)
    audit_destination(dest)

    print(f"Staged posts:            {post_count}")
    print(f"Copied post images:      {image_count}")
    print(f"Posts root:              {dest / 'posts'}")
    print(f"Global assets root:      {dest / 'assets'}")
    print("Redirect manifest:       not generated")
    print("Public URLs:              preserved from originalUrl via permalink")
    print("Post layout:              posts/YYYY/MM/DD/<historical-slug>/index.md + images/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
