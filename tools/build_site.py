from __future__ import annotations

import argparse
import shutil
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    "spec": ROOT / "spec",
    "deck": ROOT / "deck",
    "onepager": ROOT / "onepager",
}
IGNORED = shutil.ignore_patterns("README.md", ".DS_Store", "LICENSE", "__pycache__")
REQUIRED = (
    "index.html",
    "spec/index.html",
    "spec/assets/datamodel.png",
    "deck/overview/index.html",
    "deck/playground/index.html",
    "deck/playground/app.js",
    "onepager/index.html",
    "onepager/style.css",
)

LANDING = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TACO</title>
<style>
  :root { color-scheme: light dark; }
  body { margin: 0; font: 16px/1.5 system-ui, sans-serif; background: #0f1115; color: #e8e8e8; display: grid; place-items: center; min-height: 100vh; }
  main { max-width: 40rem; padding: 2rem; }
  h1 { font-size: 2.5rem; margin: 0 0 .25rem; }
  p { color: #b5b5b5; margin: 0 0 1.5rem; }
  ul { list-style: none; padding: 0; margin: 0; display: grid; gap: .75rem; }
  a { display: block; padding: 1rem 1.25rem; border: 1px solid #2c3140; border-radius: 12px; color: inherit; text-decoration: none; }
  a:hover { border-color: #f2b134; }
  a span { display: block; color: #9a9a9a; font-size: .9rem; }
</style>
</head>
<body>
<main>
  <h1>TACO</h1>
  <p>Transparent Access to Cloud-Optimized datasets. A specification for Earth Observation datasets.</p>
  <ul>
    <li><a href="spec/">Specification<span>TACO v3.0.0 normative document</span></a></li>
    <li><a href="deck/overview/">Overview deck<span>What TACO is and how the pipeline works</span></a></li>
    <li><a href="deck/playground/">cozip playground<span>Plan and write a cloud-optimized ZIP in the browser</span></a></li>
    <li><a href="onepager/">One pager<span>Print-ready summary for sharing</span></a></li>
    <li><a href="https://github.com/asterisk-labs/taco">Source code<span>Writer package, spec and site</span></a></li>
  </ul>
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
            # deck/*/sections/*.html are fragments injected into the deck's
            # index.html, so their relative paths resolve from the deck root.
            base = page.parent.parent if page.parent.name == "sections" else page.parent
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


def build(output: Path, *, clean: bool = False) -> None:
    prepare_output(output, clean=clean)
    output.mkdir(parents=True)
    for name, source in SOURCES.items():
        if not source.is_dir():
            raise FileNotFoundError(f"missing site source: {source}")
        shutil.copytree(source, output / name, ignore=IGNORED)
    (output / "index.html").write_text(LANDING, encoding="utf-8")
    (output / ".nojekyll").touch()
    cname = ROOT / "CNAME"
    if cname.is_file():
        shutil.copy(cname, output / "CNAME")
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
