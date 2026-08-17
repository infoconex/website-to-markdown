#!/usr/bin/env python3
"""Remove provisional publishing artifacts from a staged coding-blog checkout.

The migration repository owns crawling/conversion/staging. The coding-blog
repository should own its eventual publishing stack. This utility removes only
known artifacts created by bootstrap_static_site.py and preserves the content
validator under scripts/.

The staged legacy-redirects.json manifest is also removed because redirect
source-of-truth lives in each post's legacyPaths frontmatter. The eventual
GitHub Pages build can generate whatever redirect artifacts it needs from that
frontmatter.

Dry-run is the default. Pass --apply to make changes.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

BOOTSTRAP_REQUIREMENTS = "Markdown>=3.7,<4\nPyYAML>=6,<7\n"
VALIDATOR_REQUIREMENTS = BOOTSTRAP_REQUIREMENTS

BOOTSTRAP_CSS_MARKERS = (
    'main { max-width: 860px; margin: 0 auto;',
    '.post-list-item > a { font-size: 1.25rem; font-weight: 700; }',
)

BOOTSTRAP_WORKFLOW_MARKERS = (
    'name: Deploy GitHub Pages',
    'run: python build.py',
    'uses: actions/deploy-pages@v4',
)

BOOTSTRAP_BUILD_MARKERS = (
    'SITE_TITLE = os.environ.get("SITE_TITLE", "Coding by Jim Scott")',
    'OUTPUT = ROOT / "_site"',
    'Built posts:',
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path, help="Path to local coding-blog checkout")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes. Without this flag, only print the cleanup plan.",
    )
    return parser.parse_args()


def matches_all(path: Path, markers: tuple[str, ...]) -> bool:
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return False
    return all(marker in text for marker in markers)


def remove_file(path: Path, *, apply: bool, reason: str) -> None:
    if not path.exists():
        return
    print(f"REMOVE  {path}  ({reason})")
    if apply:
        path.unlink()


def remove_tree(path: Path, *, apply: bool, reason: str) -> None:
    if not path.exists():
        return
    print(f"REMOVE  {path}  ({reason})")
    if apply:
        shutil.rmtree(path)


def move_validator(dest: Path, *, apply: bool) -> None:
    source = dest / "validate.py"
    target_dir = dest / "scripts"
    target = target_dir / "validate.py"
    requirements = target_dir / "requirements.txt"

    if source.exists():
        if target.exists():
            raise SystemExit(
                f"Refusing to overwrite existing validator: {target}. "
                "Resolve that file manually and rerun."
            )
        text = source.read_text(encoding="utf-8")
        old_root = 'ROOT = Path(__file__).resolve().parent\nPOSTS = ROOT / "posts"'
        new_root = 'ROOT = Path(__file__).resolve().parent.parent\nPOSTS = ROOT / "posts"'
        if old_root not in text:
            raise SystemExit(
                f"Validator root layout was not recognized: {source}. "
                "No validator changes were made."
            )
        text = text.replace(old_root, new_root, 1)

        print(f"MOVE    {source} -> {target}")
        if apply:
            target_dir.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            source.unlink()

    elif target.exists():
        print(f"KEEP    {target}  (validator already under scripts/)")
    else:
        print("NOTE    no validate.py found; validator move skipped")

    if source.exists() or target.exists():
        print(f"WRITE   {requirements}  (validator dependencies)")
        if apply:
            target_dir.mkdir(parents=True, exist_ok=True)
            requirements.write_text(VALIDATOR_REQUIREMENTS, encoding="utf-8")


def remove_empty_dir(path: Path, *, apply: bool) -> None:
    if not path.is_dir():
        return
    if any(path.iterdir()):
        return
    print(f"REMOVE  {path}  (empty directory)")
    if apply:
        path.rmdir()


def main() -> int:
    args = parse_args()
    dest = args.destination.resolve()
    apply = args.apply

    if not (dest / ".git").exists():
        raise SystemExit(f"Destination is not a Git checkout: {dest}")

    print(f"Coding blog: {dest}")
    print(f"Mode:        {'APPLY' if apply else 'DRY RUN'}")
    print()

    build_py = dest / "build.py"
    if matches_all(build_py, BOOTSTRAP_BUILD_MARKERS):
        remove_file(build_py, apply=apply, reason="provisional Python site builder")
    elif build_py.exists():
        print(f"KEEP    {build_py}  (not recognized as our bootstrap builder)")

    workflow = dest / ".github" / "workflows" / "pages.yml"
    if matches_all(workflow, BOOTSTRAP_WORKFLOW_MARKERS):
        remove_file(workflow, apply=apply, reason="provisional GitHub Pages workflow")
    elif workflow.exists():
        print(f"KEEP    {workflow}  (not recognized as our bootstrap workflow)")

    requirements = dest / "requirements.txt"
    if requirements.is_file():
        text = requirements.read_text(encoding="utf-8")
        if text == BOOTSTRAP_REQUIREMENTS:
            remove_file(requirements, apply=apply, reason="provisional root build dependencies")
        else:
            print(f"KEEP    {requirements}  (contents differ from bootstrap requirements)")

    site_css = dest / "assets" / "css" / "site.css"
    if matches_all(site_css, BOOTSTRAP_CSS_MARKERS):
        remove_file(site_css, apply=apply, reason="provisional site-renderer CSS")
    elif site_css.exists():
        print(f"KEEP    {site_css}  (not recognized as our bootstrap stylesheet)")

    remove_tree(dest / "_site", apply=apply, reason="generated static build output")

    # This manifest is generated from the same legacyPaths frontmatter that the
    # validator already checks. Keep only the canonical frontmatter source; the
    # future Pages build can regenerate redirect artifacts in its own format.
    remove_file(
        dest / "legacy-redirects.json",
        apply=apply,
        reason="generated redirect manifest; legacyPaths frontmatter is canonical",
    )

    move_validator(dest, apply=apply)

    # Only remove directories when the cleanup actually left them empty.
    if apply:
        remove_empty_dir(dest / ".github" / "workflows", apply=True)
        remove_empty_dir(dest / ".github", apply=True)
        remove_empty_dir(dest / "assets" / "css", apply=True)

    print()
    if apply:
        print("Cleanup complete.")
        print(f"Run validation with: python {dest / 'scripts' / 'validate.py'}")
    else:
        print("Dry run only; nothing changed. Rerun with --apply to perform cleanup.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
