"""Build ``docs/_site`` from the documentation sources."""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

import markdown

ROOT = Path(__file__).resolve().parents[2]
DOCS_SOURCE = ROOT / "docs"
SOURCES = {
    "javascript/src": ROOT / "javascript" / "src",
}
IGNORED = shutil.ignore_patterns("_build", "README.md", ".DS_Store", "LICENSE", "__pycache__")
REQUIRED = (
    "index.html",
    "api/index.html",
    "api/write/index.html",
    "api/read/index.html",
    "api/style.css",
    "api/app.js",
    "spec/index.html",
    "spec/extensions/stac.html",
    "spec/assets/datamodel.png",
    "playground/index.html",
    "playground/app.js",
    "javascript/src/index.js",
    "onepager/index.html",
    "onepager/style.css",
)

SPEC_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="stylesheet" href="{stylesheet}">
</head>
<body>
<main>
{content}
</main>
</body>
</html>
"""


class DocumentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.references: list[str] = []

    def handle_starttag(self, _tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.ids.add(values["id"] or "")
        for name in ("href", "src"):
            if values.get(name):
                self.references.append(values[name] or "")

    handle_startendtag = handle_starttag


def prepare_output(output: Path, *, clean: bool) -> None:
    if output.exists() and not output.is_dir():
        raise ValueError(f"output path is not a directory: {output}")
    if not output.exists():
        return
    if any(output.iterdir()):
        if not clean:
            raise FileExistsError(f"output directory is not empty: {output}")
        if output.name != "_site":
            raise ValueError(f"refusing to clean output not named _site: {output}")
        shutil.rmtree(output)
        return
    output.rmdir()


def assert_required(output: Path) -> None:
    missing = [name for name in REQUIRED if not (output / name).is_file()]
    if missing:
        raise FileNotFoundError(f"site build is missing: {', '.join(missing)}")


def assert_local_links(output: Path) -> None:
    root = output.resolve()
    documents: dict[Path, DocumentParser] = {}
    for page in output.rglob("*.html"):
        parser = DocumentParser()
        parser.feed(page.read_text(encoding="utf-8"))
        documents[page.resolve()] = parser

    errors: list[str] = []
    for page, parser in documents.items():
        for reference in parser.references:
            parsed = urlsplit(reference)
            if parsed.scheme or parsed.netloc or reference.startswith(("mailto:", "data:", "#", "javascript:")):
                continue
            base = page.parent
            target = page if not parsed.path else (base / unquote(parsed.path)).resolve()
            if not target.is_relative_to(root):
                errors.append(f"{page.relative_to(root)} escapes the site: {reference}")
                continue
            if target.is_dir():
                target /= "index.html"
            if not target.is_file():
                errors.append(f"{page.relative_to(root)} has missing target: {reference}")
                continue
            if parsed.fragment and target.suffix == ".html":
                document = documents.get(target.resolve())
                if document is None or unquote(parsed.fragment) not in document.ids:
                    errors.append(f"{page.relative_to(root)} has missing anchor: {reference}")
    if errors:
        raise ValueError("; ".join(errors))


# A relative link to another Markdown page, which the site serves as HTML.
MARKDOWN_LINK = re.compile(r'href="([^":#]+)\.md(#[^"]*)?"')


def render_markdown(source: Path, target: Path, *, title: str, stylesheet: str) -> None:
    content = markdown.markdown(
        source.read_text(encoding="utf-8"),
        extensions=["fenced_code", "sane_lists", "tables", "toc"],
        output_format="html5",
    )
    content = MARKDOWN_LINK.sub(lambda match: f'href="{match[1]}.html{match[2] or ""}"', content)
    page = SPEC_TEMPLATE.format(title=title, stylesheet=stylesheet, content=content)
    target.write_text(page, encoding="utf-8")


def build_spec(output: Path) -> None:
    source = DOCS_SOURCE / "spec" / "SPEC.md"
    if not source.is_file():
        raise FileNotFoundError(f"missing specification source: {source}")
    render_markdown(source, output / "spec" / "index.html", title="TACO Specification", stylesheet="style.css")
    # Each extension document becomes a page beside its schema and examples.
    for document in sorted((DOCS_SOURCE / "spec" / "extensions").glob("*.md")):
        heading = next(
            (line[2:] for line in document.read_text(encoding="utf-8").splitlines() if line.startswith("# ")), ""
        )
        render_markdown(
            document,
            output / "spec" / "extensions" / f"{document.stem}.html",
            title=f"TACO {heading or document.stem}",
            stylesheet="../style.css",
        )


def build(output: Path, *, clean: bool = False) -> None:
    prepare_output(output, clean=clean)
    if not DOCS_SOURCE.is_dir():
        raise FileNotFoundError(f"missing documentation source: {DOCS_SOURCE}")
    shutil.copytree(DOCS_SOURCE, output, ignore=IGNORED)
    for name, source in SOURCES.items():
        if not source.is_dir():
            raise FileNotFoundError(f"missing site source: {source}")
        shutil.copytree(source, output / name, ignore=IGNORED)
    build_spec(output)
    (output / ".nojekyll").touch()
    assert_required(output)
    assert_local_links(output)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "_site")
    parser.add_argument("--clean", action="store_true", help="replace an existing _site")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        build(args.output.resolve(), clean=args.clean)
    except (FileExistsError, FileNotFoundError, ValueError) as exc:
        print(f"site build failed: {exc}", file=sys.stderr)
        return 1
    print(f"built the website in {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
