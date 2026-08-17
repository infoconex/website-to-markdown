#!/usr/bin/env python3
"""Stage finalized historical blog content into a clean static-site repository.

Destination layout:

  posts/<slug>/index.md
  posts/<slug>/images/*
  assets/images/
  assets/js/
  assets/css/

Post-specific images are moved beside each post and Markdown references are
rewritten to relative images/... paths. Global assets remain under assets/.
No framework-specific files, middleware, or public/ copies are created.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

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
        raise SystemExit("finalization-report.json not found; run finalize_blog.py --check-only first")
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


def rewrite_post_image_references(text: str, slug: str) -> str:
    """Rewrite generated absolute post-image URLs to local relative paths."""
    prefixes = (
        f"/images/posts/{slug}/",
        f"images/posts/{slug}/",
    )
    for prefix in prefixes:
        text = text.replace(prefix, "images/")
    return text


def ensure_global_asset_dirs(dest: Path) -> None:
    # Git does not track empty directories, so use .gitkeep placeholders.
    for relative in (Path("assets/images"), Path("assets/js"), Path("assets/css")):
        directory = dest / relative
        directory.mkdir(parents=True, exist_ok=True)
        keep = directory / ".gitkeep"
        if not keep.exists():
            keep.write_text("", encoding="utf-8")


def stage_posts(dest: Path, clean_posts: bool) -> tuple[int, int, dict[str, str]]:
    posts_root = dest / "posts"
    if clean_posts and posts_root.exists():
        shutil.rmtree(posts_root)
    posts_root.mkdir(parents=True, exist_ok=True)

    md_files = sorted(SOURCE_MD.glob("*.md"))
    if len(md_files) != EXPECTED_POSTS:
        raise SystemExit(f"Expected {EXPECTED_POSTS} Markdown files in {SOURCE_MD}, found {len(md_files)}")

    redirects: dict[str, str] = {}
    image_count = 0

    for src in md_files:
        original = src.read_text(encoding="utf-8")
        slug = frontmatter_value(original, "slug") or src.stem
        paths = legacy_paths(original)
        if not paths:
            raise SystemExit(f"Missing legacyPaths in {src}")

        post_dir = posts_root / slug
        post_dir.mkdir(parents=True, exist_ok=True)

        rewritten = rewrite_post_image_references(original, slug)
        (post_dir / "index.md").write_text(rewritten, encoding="utf-8")

        source_images = SOURCE_ASSETS / slug
        if source_images.exists():
            destination_images = post_dir / "images"
            if destination_images.exists():
                shutil.rmtree(destination_images)
            shutil.copytree(source_images, destination_images)
            image_count += sum(1 for p in destination_images.rglob("*") if p.is_file())

        for legacy in paths:
            existing = redirects.get(legacy)
            destination = f"/post/{slug}"
            if existing and existing != destination:
                raise SystemExit(f"Legacy path collision: {legacy} -> {existing} and {destination}")
            redirects[legacy] = destination

    return len(md_files), image_count, redirects


def audit_destination(dest: Path, redirects: dict[str, str]) -> None:
    post_files = sorted((dest / "posts").glob("*/index.md"))
    if len(post_files) != EXPECTED_POSTS:
        raise SystemExit(f"Destination audit expected {EXPECTED_POSTS} posts, found {len(post_files)}")

    bad_refs: list[str] = []
    missing_images: list[str] = []
    for index_md in post_files:
        text = index_md.read_text(encoding="utf-8")
        if "/images/posts/" in text:
            bad_refs.append(str(index_md))

        for match in re.finditer(r'(?:!\[[^\]]*\]\(|(?:src|href)=["\'])(images/[^)"\'\s]+)', text, re.I):
            relative = match.group(1)
            target = index_md.parent / relative
            if not target.exists():
                missing_images.append(f"{index_md}: {relative}")

    if bad_refs:
        raise SystemExit("Found unre-written /images/posts references:\n" + "\n".join(bad_refs))
    if missing_images:
        raise SystemExit("Found missing local post images:\n" + "\n".join(missing_images[:20]))

    (dest / "legacy-redirects.json").write_text(
        json.dumps(dict(sorted(redirects.items())), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    dest = args.destination.resolve()
    validate_source()

    if not (dest / ".git").exists():
        raise SystemExit(f"Destination is not a Git checkout: {dest}")

    ensure_global_asset_dirs(dest)
    post_count, image_count, redirects = stage_posts(dest, args.clean_posts)
    audit_destination(dest, redirects)

    print(f"Staged posts:            {post_count}")
    print(f"Copied post images:      {image_count}")
    print(f"Legacy redirects:        {len(redirects)}")
    print(f"Posts root:              {dest / 'posts'}")
    print(f"Global assets root:      {dest / 'assets'}")
    print(f"Redirect manifest:       {dest / 'legacy-redirects.json'}")
    print("Post layout:              posts/<slug>/index.md + images/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
