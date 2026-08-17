#!/usr/bin/env python3
"""Enumerate legacy BlogEngine posts and optionally save their raw HTML.

Designed initially for http://coding.infoconex.com.

Examples:
    python crawl_blog.py
    python crawl_blog.py --max-pages 50
    python crawl_blog.py --save-html
    python crawl_blog.py --base-url http://coding.infoconex.com --output crawl-output
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "http://coding.infoconex.com"
USER_AGENT = (
    "Mozilla/5.0 (compatible; website-to-markdown/0.1; "
    "+https://coding.infoconex.com)"
)
POST_PATH_RE = re.compile(r"^/post(?:/|$)", re.IGNORECASE)


@dataclass(frozen=True)
class Post:
    url: str
    discovered_on: str
    title: str | None = None
    status: int | None = None
    html_file: str | None = None


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str | None]] = []
        self._current_href: str | None = None
        self._current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attrs_dict = dict(attrs)
        href = attrs_dict.get("href")
        if href:
            self._current_href = href
            self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._current_href is not None:
            self._current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._current_href is not None:
            text = " ".join("".join(self._current_text).split()) or None
            self.links.append((self._current_href, text))
            self._current_href = None
            self._current_text = []


def normalize_url(url: str) -> str:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    hostname = (parts.hostname or "").lower()
    port = parts.port

    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        port = None

    netloc = hostname
    if port:
        netloc = f"{hostname}:{port}"

    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if path != "/":
        path = path.rstrip("/")

    return urlunsplit((scheme, netloc, path, "", ""))


def same_host(url: str, base_url: str) -> bool:
    return (urlsplit(url).hostname or "").lower() == (urlsplit(base_url).hostname or "").lower()


def is_post_url(url: str, base_url: str) -> bool:
    parts = urlsplit(url)
    return same_host(url, base_url) and bool(POST_PATH_RE.match(parts.path))


def fetch(url: str, timeout: float) -> tuple[str, int, str]:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.8",
            "Connection": "close",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        raw = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
        try:
            text = raw.decode(charset, errors="replace")
        except LookupError:
            text = raw.decode("utf-8", errors="replace")
        return text, response.status, response.geturl()


def write_text_exact(path: Path, text: str) -> None:
    """Write decoded response text without platform newline translation.

    On Windows, Path.write_text() can translate existing CRLF input into CRCRLF.
    That is significant inside <pre> blocks, where it becomes a blank line between
    every source-code line. Writing bytes preserves the response text exactly.
    """
    path.write_bytes(text.encode("utf-8"))


def extract_post_links(html: str, page_url: str, base_url: str) -> dict[str, str | None]:
    parser = LinkParser()
    parser.feed(html)

    posts: dict[str, str | None] = {}
    for href, text in parser.links:
        absolute = normalize_url(urljoin(page_url, href))
        if is_post_url(absolute, base_url):
            posts.setdefault(absolute, text)
    return posts


def safe_html_filename(url: str) -> str:
    path = urlsplit(url).path.strip("/")
    slug = path.split("/")[-1] if path else "index"
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", slug).strip("-._") or "post"
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    return f"{slug[:100]}-{digest}.html"


def write_outputs(posts: Iterable[Post], output_dir: Path) -> None:
    ordered = sorted(posts, key=lambda p: p.url)
    output_dir.mkdir(parents=True, exist_ok=True)

    (output_dir / "urls.txt").write_text(
        "".join(f"{post.url}\n" for post in ordered),
        encoding="utf-8",
    )
    (output_dir / "manifest.json").write_text(
        json.dumps([asdict(post) for post in ordered], indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def crawl(args: argparse.Namespace) -> int:
    base_url = args.base_url.rstrip("/")
    output_dir = Path(args.output)
    html_dir = output_dir / "html"
    if args.save_html:
        html_dir.mkdir(parents=True, exist_ok=True)

    discovered: dict[str, Post] = {}
    consecutive_without_new = 0

    print(f"Base URL: {base_url}")
    print(f"Output:   {output_dir}")
    print()

    for page_number in range(args.start_page, args.max_pages + 1):
        page_url = f"{base_url}/?page={page_number}"
        print(f"[page {page_number}] {page_url}")

        try:
            html, status, final_url = fetch(page_url, args.timeout)
        except HTTPError as exc:
            print(f"  HTTP {exc.code}: {exc.reason}", file=sys.stderr)
            consecutive_without_new += 1
            if consecutive_without_new >= args.stop_after_empty:
                print("Stopping after repeated pages with no new posts.")
                break
            continue
        except URLError as exc:
            print(f"  request failed: {exc.reason}", file=sys.stderr)
            return 2
        except TimeoutError:
            print("  request timed out", file=sys.stderr)
            return 2

        links = extract_post_links(html, final_url, base_url)
        new_count = 0
        for url, title in links.items():
            if url not in discovered:
                discovered[url] = Post(url=url, discovered_on=page_url, title=title)
                new_count += 1

        print(f"  HTTP {status}; {len(links)} post links; {new_count} new; {len(discovered)} total")

        if new_count == 0:
            consecutive_without_new += 1
        else:
            consecutive_without_new = 0

        write_outputs(discovered.values(), output_dir)

        if consecutive_without_new >= args.stop_after_empty:
            print("Stopping after repeated pages with no new posts.")
            break

        if args.delay:
            time.sleep(args.delay)

    if not discovered:
        print("\nNo /post/ URLs were discovered.", file=sys.stderr)
        return 1

    if args.save_html:
        print(f"\nFetching {len(discovered)} discovered posts...")
        updated: dict[str, Post] = {}
        for index, post in enumerate(sorted(discovered.values(), key=lambda p: p.url), start=1):
            print(f"[{index}/{len(discovered)}] {post.url}")
            try:
                html, status, final_url = fetch(post.url, args.timeout)
                filename = safe_html_filename(post.url)
                write_text_exact(html_dir / filename, html)
                updated[post.url] = Post(
                    url=normalize_url(final_url),
                    discovered_on=post.discovered_on,
                    title=post.title,
                    status=status,
                    html_file=str(Path("html") / filename),
                )
                print(f"  HTTP {status} -> {filename}")
            except HTTPError as exc:
                updated[post.url] = Post(
                    url=post.url,
                    discovered_on=post.discovered_on,
                    title=post.title,
                    status=exc.code,
                )
                print(f"  HTTP {exc.code}: {exc.reason}", file=sys.stderr)
            except (URLError, TimeoutError) as exc:
                updated[post.url] = post
                print(f"  request failed: {exc}", file=sys.stderr)

            write_outputs(updated.values(), output_dir)
            if args.delay:
                time.sleep(args.delay)

        discovered = updated

    write_outputs(discovered.values(), output_dir)
    print(f"\nDone. Discovered {len(discovered)} unique posts.")
    print(f"URL list:  {output_dir / 'urls.txt'}")
    print(f"Manifest:  {output_dir / 'manifest.json'}")
    if args.save_html:
        print(f"Raw HTML:  {html_dir}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Enumerate BlogEngine-style /post/ URLs from paginated blog index pages."
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--output", default="crawl-output")
    parser.add_argument("--start-page", type=int, default=1)
    parser.add_argument("--max-pages", type=int, default=100)
    parser.add_argument(
        "--stop-after-empty",
        type=int,
        default=2,
        help="Stop after this many consecutive listing pages add no new posts (default: 2).",
    )
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Seconds to wait between requests (default: 0.5).",
    )
    parser.add_argument(
        "--save-html",
        action="store_true",
        help="Fetch each discovered post and save its raw HTML under OUTPUT/html/.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.start_page < 1 or args.max_pages < args.start_page:
        print("Invalid page range.", file=sys.stderr)
        return 2
    if args.stop_after_empty < 1:
        print("--stop-after-empty must be at least 1.", file=sys.stderr)
        return 2
    return crawl(args)


if __name__ == "__main__":
    raise SystemExit(main())
