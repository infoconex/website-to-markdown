#!/usr/bin/env python3
"""
Convert saved BlogEngine post HTML into Markdown and migrate in-article images.

Input:
  crawl-output/manifest.json
  crawl-output/html/*.html

Output:
  generated-markdown/*.md
  generated-assets/images/posts/<slug>/*
  generated-manifest.json

The generated Markdown image paths are rooted for the destination Next.js blog:
  /images/posts/<slug>/<filename>

Copy generated-assets/images/posts/* to coding-blog/public/images/posts/*
when promoting the migration.
"""

from __future__ import annotations

import argparse
import hashlib
import html as html_lib
import json
import mimetypes
import re
import shutil
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
import yaml
from bs4 import BeautifulSoup, NavigableString, Tag
from markdownify import MarkdownConverter


DEFAULT_MANIFEST = Path("crawl-output/manifest.json")
DEFAULT_MD_DIR = Path("generated-markdown")
DEFAULT_ASSET_DIR = Path("generated-assets/images/posts")
DEFAULT_REPORT = Path("generated-manifest.json")
USER_AGENT = "website-to-markdown/1.0 (+https://github.com/infoconex/website-to-markdown)"


@dataclass
class ImageResult:
    source: str
    resolved_url: str
    markdown_path: str | None
    local_file: str | None
    status: int | None
    error: str | None


def slugify(value: str) -> str:
    value = html_lib.unescape(value or "")
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = value.lower()
    value = value.replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value or "post"


def yaml_quote(value: str) -> str:
    return yaml.safe_dump(value, allow_unicode=True, default_flow_style=True).strip()


def extract_iso_date(url: str, display_date: str | None) -> str:
    m = re.search(r"/post/(\d{4})/(\d{2})/(\d{2})/", url)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    if display_date:
        for fmt in ("%d %B %Y", "%d %b %Y"):
            try:
                return datetime.strptime(display_date.strip(), fmt).strftime("%Y-%m-%d")
            except ValueError:
                pass
    return ""


def first_description(body: Tag, limit: int = 220) -> str:
    for element in body.find_all(["p", "div", "li"], recursive=True):
        text = " ".join(element.stripped_strings)
        if not text or len(text) < 20:
            continue
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > limit:
            text = text[: limit - 1].rstrip() + "…"
        return text
    return ""


def get_tags(article: Tag) -> list[str]:
    footer = article.select_one(".post-tags")
    if not footer:
        return []
    tags: list[str] = []
    for a in footer.find_all("a"):
        text = " ".join(a.stripped_strings).strip()
        if text and text not in tags:
            tags.append(text)
    return tags


def resolve_saved_html(base_dir: Path, manifest_value: str) -> Path:
    normalized = manifest_value.replace("\\", "/")
    return base_dir / normalized


def likely_code_div(tag: Tag) -> bool:
    if tag.name != "div":
        return False
    style = (tag.get("style") or "").lower()
    klass = " ".join(tag.get("class") or []).lower()
    text = tag.get_text("\n", strip=True)
    if not text:
        return False
    if "syntaxhighlighter" in klass or "code" in klass:
        return True
    if ("background-color" in style and "border" in style) or "font-family: courier" in style:
        return True
    return False


def infer_language(tag: Tag) -> str:
    classes = " ".join(tag.get("class") or []).lower()
    text = tag.get_text("\n", strip=True)
    candidates = {
        "csharp": "csharp",
        "c#": "csharp",
        "javascript": "javascript",
        "js": "javascript",
        "xml": "xml",
        "html": "html",
        "sql": "sql",
        "bash": "bash",
        "shell": "bash",
        "powershell": "powershell",
        "vb": "vbnet",
    }
    for key, lang in candidates.items():
        if key in classes:
            return lang
    if re.search(r"\b(using|namespace|public class|private class|static void)\b", text):
        return "csharp"
    if re.search(r"(^|\n)\s*(sudo |yum |apt |mkdir |openssl |proftpd |lvm |fsck |nano |diskpart )", text, re.I):
        return "bash"
    if re.search(r"<[/!A-Za-z][^>]*>", text):
        return "xml"
    return ""


def normalize_body(body: Tag) -> None:
    for junk in body.find_all(["script", "style", "noscript"]):
        junk.decompose()

    for tag in list(body.find_all(["pre", "div"])):
        if tag.name == "pre" or likely_code_div(tag):
            text = tag.get_text("\n", strip=False)
            text = html_lib.unescape(text).replace("\xa0", " ")
            text = re.sub(r"\n{3,}", "\n\n", text).strip("\n")
            if not text.strip():
                continue
            pre = body.new_tag("pre")
            code = body.new_tag("code")
            lang = infer_language(tag)
            if lang:
                code["class"] = [f"language-{lang}"]
            code.string = text
            pre.append(code)
            tag.replace_with(pre)

    for tag in body.find_all(True):
        for attr in ("style", "width", "height", "align", "border", "cellpadding", "cellspacing"):
            tag.attrs.pop(attr, None)

    for p in list(body.find_all("p")):
        if not p.get_text(strip=True) and not p.find("img"):
            p.decompose()


def filename_from_image_url(url: str, content_type: str | None, index: int) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    candidate = ""
    for key in ("picture", "image", "file", "src"):
        values = query.get(key)
        if values:
            candidate = Path(unquote(values[0])).name
            break
    if not candidate:
        candidate = Path(unquote(parsed.path)).name

    candidate = re.sub(r"[^A-Za-z0-9._-]+", "-", candidate).strip("-._")
    stem = Path(candidate).stem if candidate else f"image-{index:02d}"
    suffix = Path(candidate).suffix.lower() if candidate else ""

    if not suffix or len(suffix) > 6:
        guessed = None
        if content_type:
            guessed = mimetypes.guess_extension(content_type.split(";", 1)[0].strip())
        suffix = guessed or ".bin"

    stem = slugify(stem)
    return f"{stem or f'image-{index:02d}'}{suffix}"


def download_images(
    body: Tag,
    post_url: str,
    slug: str,
    asset_root: Path,
    session: requests.Session,
    timeout: float,
    include_external: bool,
    delay: float,
) -> list[ImageResult]:
    results: list[ImageResult] = []
    post_asset_dir = asset_root / slug
    seen_names: set[str] = set()

    for index, img in enumerate(body.find_all("img"), start=1):
        source = (img.get("src") or "").strip()
        if not source or source.startswith("data:"):
            continue

        resolved = urljoin(post_url, source)
        parsed = urlparse(resolved)
        host = (parsed.hostname or "").lower()

        if host and host not in {"coding.infoconex.com", "www.coding.infoconex.com"} and not include_external:
            results.append(ImageResult(source, resolved, resolved, None, None, "external image left unchanged"))
            img["src"] = resolved
            continue

        status = None
        try:
            response = session.get(resolved, timeout=timeout, allow_redirects=True)
            status = response.status_code
            response.raise_for_status()
            ctype = response.headers.get("content-type")
            filename = filename_from_image_url(response.url, ctype, index)
            base = filename
            n = 2
            while filename.lower() in seen_names:
                p = Path(base)
                filename = f"{p.stem}-{n}{p.suffix}"
                n += 1
            seen_names.add(filename.lower())

            post_asset_dir.mkdir(parents=True, exist_ok=True)
            dest = post_asset_dir / filename
            dest.write_bytes(response.content)

            markdown_path = f"/images/posts/{slug}/{filename}"
            img["src"] = markdown_path
            results.append(ImageResult(source, resolved, markdown_path, dest.as_posix(), status, None))
        except Exception as exc:
            img["src"] = resolved
            results.append(ImageResult(source, resolved, resolved, None, status, str(exc)))

        if delay:
            time.sleep(delay)

    return results


class BlogMarkdownConverter(MarkdownConverter):
    def convert_pre(self, el, text, parent_tags):
        code = el.find("code")
        language = ""
        raw = el.get_text("\n", strip=False)
        if code:
            classes = code.get("class") or []
            for cls in classes:
                if cls.startswith("language-"):
                    language = cls[len("language-") :]
                    break
            raw = code.get_text("\n", strip=False)
        raw = raw.strip("\n")
        return f"\n\n```{language}\n{raw}\n```\n\n"

    def convert_img(self, el, text, parent_tags):
        alt = (el.get("alt") or "").strip().replace("\n", " ")
        src = (el.get("src") or "").strip()
        title = (el.get("title") or "").strip()
        title_part = f' "{title}"' if title else ""
        return f"![{alt}]({src}{title_part})" if src else ""


def to_markdown(body: Tag) -> str:
    converter = BlogMarkdownConverter(heading_style="ATX", bullets="-", strip=["font"])
    markdown = converter.convert_soup(body)
    markdown = markdown.replace("\xa0", " ")
    markdown = re.sub(r"[ \t]+\n", "\n", markdown)
    markdown = re.sub(r"\n{4,}", "\n\n\n", markdown)
    return markdown.strip() + "\n"


def render_frontmatter(title: str, date: str, description: str, tags: list[str], slug: str, author: str, original_url: str) -> str:
    lines = [
        "---",
        f"title: {yaml_quote(title)}",
        f'date: "{date}"',
        f"description: {yaml_quote(description)}",
        "tags: [" + ", ".join(yaml_quote(t) for t in tags) + "]",
        f'slug: "{slug}"',
        f"author: {yaml_quote(author or 'Jim Scott')}",
        f"originalUrl: {yaml_quote(original_url)}",
        "---",
        "",
    ]
    return "\n".join(lines)


def convert_entry(entry: dict[str, Any], manifest_dir: Path, md_dir: Path, asset_root: Path, session: requests.Session, timeout: float, include_external: bool, delay: float) -> dict[str, Any]:
    post_url = entry["url"]
    saved = entry.get("html_file")
    if not saved:
        return {**entry, "conversion_status": "skipped", "conversion_error": "missing html_file"}

    html_path = resolve_saved_html(manifest_dir, saved)
    if not html_path.exists():
        return {**entry, "conversion_status": "error", "conversion_error": f"saved HTML not found: {html_path}"}

    soup = BeautifulSoup(html_path.read_text(encoding="utf-8", errors="replace"), "html.parser")
    article = soup.select_one("article.post")
    if not article:
        return {**entry, "conversion_status": "error", "conversion_error": "article.post not found"}

    title_node = article.select_one(".post-title")
    title = " ".join(title_node.stripped_strings).strip() if title_node else (entry.get("title") or "Untitled")
    date_node = article.select_one(".post-date")
    display_date = " ".join(date_node.stripped_strings).strip() if date_node else ""
    author_node = article.select_one(".post-author")
    author = " ".join(author_node.stripped_strings).strip() if author_node else "Jim Scott"
    if author.lower() == "jscott":
        author = "Jim Scott"

    body = article.select_one(".post-body")
    if not body:
        return {**entry, "conversion_status": "error", "conversion_error": ".post-body not found"}

    tags = get_tags(article)
    date = extract_iso_date(post_url, display_date)
    slug = slugify(title)
    description = first_description(body)

    normalize_body(body)
    images = download_images(body, post_url, slug, asset_root, session, timeout, include_external, delay)
    markdown = to_markdown(body)

    md_dir.mkdir(parents=True, exist_ok=True)
    output = md_dir / f"{slug}.md"
    output.write_text(render_frontmatter(title, date, description, tags, slug, author, post_url) + markdown, encoding="utf-8")

    return {
        **entry,
        "conversion_status": "ok",
        "conversion_error": None,
        "slug": slug,
        "markdown_file": output.as_posix(),
        "date": date,
        "author": author,
        "tags": tags,
        "description": description,
        "images": [r.__dict__ for r in images],
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Convert saved BlogEngine HTML posts to Markdown.")
    p.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    p.add_argument("--markdown-dir", type=Path, default=DEFAULT_MD_DIR)
    p.add_argument("--asset-dir", type=Path, default=DEFAULT_ASSET_DIR)
    p.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    p.add_argument("--limit", type=int, default=None, help="Convert only the first N posts for testing.")
    p.add_argument("--slug", action="append", default=[], help="Only convert matching generated slug(s). Repeatable.")
    p.add_argument("--timeout", type=float, default=20.0)
    p.add_argument("--delay", type=float, default=0.1, help="Delay between image requests.")
    p.add_argument("--include-external-images", action="store_true", help="Download externally hosted images too.")
    p.add_argument("--clean", action="store_true", help="Delete generated output directories before converting.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if not args.manifest.exists():
        print(f"Manifest not found: {args.manifest}", file=sys.stderr)
        return 2

    if args.clean:
        shutil.rmtree(args.markdown_dir, ignore_errors=True)
        shutil.rmtree(args.asset_dir.parent.parent, ignore_errors=True)
        if args.report.exists():
            args.report.unlink()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    entries = [e for e in manifest if e.get("status") == 200 and e.get("html_file")]

    if args.slug:
        wanted = set(args.slug)
        entries = [e for e in entries if slugify(e.get("title") or "") in wanted]
    if args.limit is not None:
        entries = entries[: args.limit]

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    report: list[dict[str, Any]] = []
    failures = 0
    image_total = 0
    image_failures = 0

    print(f"Converting {len(entries)} posts...")
    for i, entry in enumerate(entries, start=1):
        result = convert_entry(entry, args.manifest.parent, args.markdown_dir, args.asset_dir, session, args.timeout, args.include_external_images, args.delay)
        report.append(result)
        if result.get("conversion_status") != "ok":
            failures += 1
            print(f"[{i:02d}/{len(entries):02d}] ERROR {entry.get('url')}: {result.get('conversion_error')}")
            continue

        images = result.get("images") or []
        image_total += len(images)
        image_failures += sum(1 for img in images if img.get("error") and img.get("local_file") is None)
        print(f"[{i:02d}/{len(entries):02d}] OK    {result['slug']} ({len(images)} images)")

    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print()
    print(f"Posts converted: {len(report) - failures}/{len(report)}")
    print(f"Post failures:    {failures}")
    print(f"Images seen:      {image_total}")
    print(f"Image issues:     {image_failures}")
    print(f"Markdown:         {args.markdown_dir}")
    print(f"Assets:           {args.asset_dir}")
    print(f"Report:           {args.report}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
