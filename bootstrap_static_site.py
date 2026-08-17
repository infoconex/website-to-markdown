#!/usr/bin/env python3
"""Bootstrap a simple static Markdown blog in a local coding-blog checkout.

The generated site builder reads posts/<slug>/index.md, converts Markdown to
HTML, copies post-local images beside the generated article, copies global
assets/, and creates static redirect pages from legacyPaths frontmatter.

Build output is written to _site/ for GitHub Pages deployment.
"""

from __future__ import annotations

import argparse
import textwrap
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path, help="Path to local coding-blog checkout")
    return parser.parse_args()


def write_if_missing(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def main() -> int:
    args = parse_args()
    dest = args.destination.resolve()
    if not (dest / ".git").exists():
        raise SystemExit(f"Destination is not a Git checkout: {dest}")

    build_py = r'''#!/usr/bin/env python3
from __future__ import annotations

import html
import os
import shutil
from pathlib import Path
from urllib.parse import quote

import markdown
import yaml

ROOT = Path(__file__).resolve().parent
POSTS = ROOT / "posts"
ASSETS = ROOT / "assets"
OUTPUT = ROOT / "_site"
SITE_TITLE = os.environ.get("SITE_TITLE", "Coding by Jim Scott")
SITE_BASE_URL = os.environ.get("SITE_BASE_URL", "").rstrip("/")


def read_post(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError(f"Missing YAML frontmatter: {path}")
    parts = text.split("---", 2)
    if len(parts) != 3:
        raise ValueError(f"Invalid YAML frontmatter: {path}")
    data = yaml.safe_load(parts[1]) or {}
    return data, parts[2].lstrip("\r\n")


def page(title: str, body: str, description: str = "", canonical: str = "") -> str:
    canonical_tag = f'<link rel="canonical" href="{html.escape(canonical)}">' if canonical else ""
    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} | {html.escape(SITE_TITLE)}</title>
<meta name="description" content="{html.escape(description)}">
{canonical_tag}
<link rel="stylesheet" href="/assets/css/site.css">
</head>
<body>
<header class="site-header"><a href="/">{html.escape(SITE_TITLE)}</a></header>
<main>{body}</main>
</body>
</html>
'''


def redirect_page(destination: str) -> str:
    escaped = html.escape(destination, quote=True)
    js_dest = destination.replace("\\", "\\\\").replace("'", "\\'")
    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="robots" content="noindex">
<link rel="canonical" href="{escaped}">
<meta http-equiv="refresh" content="0; url={escaped}">
<title>Moved</title>
</head>
<body>
<p>This post moved to <a href="{escaped}">{escaped}</a>.</p>
<script>location.replace('{js_dest}');</script>
</body>
</html>
'''


def output_path_for_url(url_path: str) -> Path:
    clean = url_path.split("?", 1)[0].split("#", 1)[0].strip("/")
    return OUTPUT / clean / "index.html"


def main() -> int:
    if not POSTS.exists():
        raise SystemExit(f"Posts directory not found: {POSTS}")

    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    OUTPUT.mkdir(parents=True)
    (OUTPUT / ".nojekyll").write_text("", encoding="utf-8")

    if ASSETS.exists():
        shutil.copytree(ASSETS, OUTPUT / "assets", dirs_exist_ok=True)

    posts = []
    redirects: dict[str, str] = {}

    for index_md in sorted(POSTS.glob("*/index.md")):
        data, source = read_post(index_md)
        slug = str(data.get("slug") or index_md.parent.name)
        title = str(data.get("title") or slug)
        description = str(data.get("description") or "")
        date = str(data.get("date") or "")
        author = str(data.get("author") or "")
        tags = data.get("tags") or []
        legacy_paths = data.get("legacyPaths") or []

        canonical_path = f"/post/{slug}/"
        canonical_url = f"{SITE_BASE_URL}{canonical_path}" if SITE_BASE_URL else canonical_path

        article_html = markdown.markdown(
            source,
            extensions=["fenced_code", "tables", "sane_lists", "attr_list"],
            output_format="html5",
        )
        tag_html = " ".join(f'<span class="tag">{html.escape(str(tag))}</span>' for tag in tags)
        meta = " · ".join(part for part in (date, author) if part)
        body = (
            f'<article><h1>{html.escape(title)}</h1>'
            f'<p class="post-meta">{html.escape(meta)}</p>'
            f'<div class="tags">{tag_html}</div>'
            f'<div class="post-body">{article_html}</div></article>'
        )

        article_out = OUTPUT / "post" / slug
        article_out.mkdir(parents=True, exist_ok=True)
        (article_out / "index.html").write_text(
            page(title, body, description, canonical_url), encoding="utf-8"
        )

        source_images = index_md.parent / "images"
        if source_images.exists():
            shutil.copytree(source_images, article_out / "images", dirs_exist_ok=True)

        for legacy in legacy_paths:
            legacy = str(legacy)
            existing = redirects.get(legacy)
            if existing and existing != canonical_path:
                raise ValueError(f"Legacy redirect collision: {legacy}: {existing} vs {canonical_path}")
            redirects[legacy] = canonical_path

        posts.append({
            "slug": slug,
            "title": title,
            "description": description,
            "date": date,
            "canonical_path": canonical_path,
        })

    posts.sort(key=lambda p: p["date"], reverse=True)

    items = []
    for post in posts:
        items.append(
            '<li class="post-list-item">'
            f'<a href="{html.escape(post["canonical_path"])}">{html.escape(post["title"])}</a>'
            f'<div class="post-date">{html.escape(post["date"])}</div>'
            f'<p>{html.escape(post["description"])}</p>'
            '</li>'
        )
    listing = '<section><h1>Posts</h1><ul class="post-list">' + "".join(items) + '</ul></section>'
    (OUTPUT / "index.html").write_text(page("Posts", listing), encoding="utf-8")
    blog_dir = OUTPUT / "blog"
    blog_dir.mkdir(parents=True, exist_ok=True)
    (blog_dir / "index.html").write_text(page("Posts", listing), encoding="utf-8")

    # Previous canonical detail format also redirects to /post/<slug>/.
    for post in posts:
        redirects[f'/blog/{post["slug"]}'] = post["canonical_path"]

    for legacy, destination in sorted(redirects.items()):
        redirect_out = output_path_for_url(legacy)
        # Do not overwrite a canonical article output.
        canonical_article = OUTPUT / "post" / destination.strip("/").split("/")[-1] / "index.html"
        if redirect_out.resolve() == canonical_article.resolve():
            continue
        redirect_out.parent.mkdir(parents=True, exist_ok=True)
        redirect_out.write_text(redirect_page(destination), encoding="utf-8")

    print(f"Built posts:             {len(posts)}")
    print(f"Generated redirects:     {len(redirects)}")
    print(f"Output:                  {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

    requirements = "Markdown>=3.7,<4\nPyYAML>=6,<7\n"

    css = r'''html { font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #1f2937; }
body { margin: 0; background: #fff; }
.site-header { padding: 1rem 1.5rem; border-bottom: 1px solid #e5e7eb; }
.site-header a { color: inherit; font-weight: 700; text-decoration: none; }
main { max-width: 860px; margin: 0 auto; padding: 2rem 1.5rem 4rem; }
article h1 { line-height: 1.15; }
.post-meta, .post-date { color: #6b7280; }
.tags { margin: .75rem 0 1.5rem; }
.tag { display: inline-block; margin: 0 .4rem .4rem 0; padding: .2rem .5rem; border: 1px solid #d1d5db; border-radius: 999px; font-size: .85rem; }
.post-body { line-height: 1.7; }
.post-body img { max-width: 100%; height: auto; }
.post-body pre { overflow-x: auto; padding: 1rem; background: #f3f4f6; }
.post-body code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.post-list { list-style: none; padding: 0; }
.post-list-item { margin: 0 0 2rem; }
.post-list-item > a { font-size: 1.25rem; font-weight: 700; }
'''

    workflow = r'''name: Deploy GitHub Pages

on:
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read
  pages: write
  id-token: write

concurrency:
  group: pages
  cancel-in-progress: false

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v6
      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.13'
      - name: Install dependencies
        run: pip install -r requirements.txt
      - name: Build static site
        run: python build.py
      - name: Configure Pages
        uses: actions/configure-pages@v5
      - name: Upload Pages artifact
        uses: actions/upload-pages-artifact@v4
        with:
          path: _site

  deploy:
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    runs-on: ubuntu-latest
    needs: build
    steps:
      - name: Deploy to GitHub Pages
        id: deployment
        uses: actions/deploy-pages@v4
'''

    gitignore = "_site/\n.venv/\n__pycache__/\n*.pyc\n"

    write_if_missing(dest / "build.py", build_py)
    write_if_missing(dest / "requirements.txt", requirements)
    write_if_missing(dest / "assets" / "css" / "site.css", css)
    write_if_missing(dest / ".github" / "workflows" / "pages.yml", workflow)
    write_if_missing(dest / ".gitignore", gitignore)

    print(f"Static site bootstrap written to {dest}")
    print("Builder:                 build.py")
    print("Build output:            _site/")
    print("Pages workflow:          .github/workflows/pages.yml")
    print("Markdown renderer:       Python-Markdown")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
