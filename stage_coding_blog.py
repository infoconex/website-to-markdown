#!/usr/bin/env python3
"""Stage the finalized historical blog migration into a local coding-blog checkout.

Copies generated Markdown and image assets, switches article-detail links to
/post/<slug>, preserves /blog as the listing route, redirects old /blog/<slug>
article URLs, and generates middleware redirects from legacyPaths frontmatter.
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


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("destination", type=Path, help="Path to local infoconex/coding-blog checkout")
    return p.parse_args()


def validate_source() -> None:
    if not REPORT.exists():
        raise SystemExit("finalization-report.json not found; run finalize_blog.py --check-only first")
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    if report.get("duplicate_slugs"):
        raise SystemExit("Migration report still has duplicate slugs")
    if report.get("unresolved_legacy_links"):
        raise SystemExit("Migration report still has unresolved legacy links")
    if report.get("generated_markdown_file_count") != 61:
        raise SystemExit(f"Expected 61 generated Markdown files, found {report.get('generated_markdown_file_count')}")


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


def copy_content(dest: Path) -> dict[str, str]:
    posts_dest = dest / "content" / "posts"
    posts_dest.mkdir(parents=True, exist_ok=True)
    redirects: dict[str, str] = {}

    md_files = sorted(SOURCE_MD.glob("*.md"))
    if len(md_files) != 61:
        raise SystemExit(f"Expected 61 Markdown files in {SOURCE_MD}, found {len(md_files)}")

    for src in md_files:
        text = src.read_text(encoding="utf-8")
        slug = frontmatter_value(text, "slug") or src.stem
        paths = legacy_paths(text)
        if not paths:
            raise SystemExit(f"Missing legacyPaths in {src}")
        for legacy in paths:
            redirects[legacy] = f"/post/{slug}"
        shutil.copy2(src, posts_dest / src.name)

    if SOURCE_ASSETS.exists():
        assets_dest = dest / "public" / "images" / "posts"
        assets_dest.mkdir(parents=True, exist_ok=True)
        shutil.copytree(SOURCE_ASSETS, assets_dest, dirs_exist_ok=True)

    return redirects


def stage_routes(dest: Path, redirects: dict[str, str]) -> None:
    old_route = dest / "app" / "blog" / "[slug]"
    new_route = dest / "app" / "post" / "[slug]"
    if not old_route.exists():
        raise SystemExit(f"Expected existing article route: {old_route}")

    if new_route.exists():
        shutil.rmtree(new_route)
    shutil.copytree(old_route, new_route)

    new_page = new_route / "page.tsx"
    text = new_page.read_text(encoding="utf-8")
    text = text.replace("/blog/${post?.slug}", "/post/${post?.slug}")
    new_page.write_text(text, encoding="utf-8")

    # Keep /blog as the listing page, but redirect the old detail URL.
    old_page = old_route / "page.tsx"
    old_page.write_text(
        "import { redirect } from 'next/navigation';\n\n"
        "export default async function LegacyBlogPost({ params }: { params: Promise<{ slug: string }> }) {\n"
        "  const { slug } = await params;\n"
        "  redirect(`/post/${slug}`);\n"
        "}\n",
        encoding="utf-8",
    )

    # Update generated article links but leave the /blog listing route alone.
    candidates = list((dest / "components").rglob("*.tsx")) + list((dest / "app").rglob("*.tsx"))
    candidates += list((dest / "scripts").rglob("*.js")) if (dest / "scripts").exists() else []
    for path in candidates:
        data = path.read_text(encoding="utf-8")
        updated = data.replace("/blog/${", "/post/${")
        if updated != data:
            path.write_text(updated, encoding="utf-8")

    # Expose redirect metadata to the app as a static artifact.
    redirect_file = dest / "legacy-redirects.json"
    redirect_file.write_text(json.dumps(dict(sorted(redirects.items())), indent=2) + "\n", encoding="utf-8")

    middleware = dest / "middleware.ts"
    middleware.write_text(
        "import { NextRequest, NextResponse } from 'next/server';\n"
        "import legacyRedirects from './legacy-redirects.json';\n\n"
        "const redirects = legacyRedirects as Record<string, string>;\n\n"
        "export function middleware(request: NextRequest) {\n"
        "  const destination = redirects[request.nextUrl.pathname];\n"
        "  if (!destination) return NextResponse.next();\n"
        "  return NextResponse.redirect(new URL(destination, request.url), 308);\n"
        "}\n\n"
        "export const config = { matcher: ['/post/:path*'] };\n",
        encoding="utf-8",
    )

    # Make legacyPaths available in blog metadata for future use/debugging.
    blog_lib = dest / "lib" / "blog.ts"
    if blog_lib.exists():
        data = blog_lib.read_text(encoding="utf-8")
        if "legacyPaths?: string[];" not in data:
            interface_marker = "  readingTime: string;\n"
            data = data.replace(interface_marker, interface_marker + "  legacyPaths?: string[];\n")
            data = data.replace(
                "      readingTime: stats?.text ?? '1 min read',\n",
                "      readingTime: stats?.text ?? '1 min read',\n      legacyPaths: data?.legacyPaths ?? [],\n",
            )
            blog_lib.write_text(data, encoding="utf-8")


def main() -> int:
    args = parse_args()
    dest = args.destination.resolve()
    validate_source()
    if not (dest / "package.json").exists() or not (dest / ".git").exists():
        raise SystemExit(f"Destination does not look like a coding-blog Git checkout: {dest}")

    redirects = copy_content(dest)
    stage_routes(dest, redirects)

    print(f"Staged 61 historical Markdown posts into {dest / 'content' / 'posts'}")
    print(f"Generated {len(redirects)} legacy redirects in {dest / 'legacy-redirects.json'}")
    print(f"Copied assets into {dest / 'public' / 'images' / 'posts'}")
    print("Canonical article route: /post/<slug>")
    print("Legacy /blog/<slug> detail route now redirects to /post/<slug>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
