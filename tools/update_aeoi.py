"""Update the vendored AEOI catalogue."""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

SOURCE = (
    "https://github.com/awesome-spectral-indices/awesome-earth-observation-instruments"
    "/raw/refs/heads/main/catalogue/catalogue.json"
)
TARGET = Path(__file__).resolve().parents[1] / "python/taco/extensions/_builtin/instrument/aeoi/catalogue.json"


def main() -> None:
    with urllib.request.urlopen(SOURCE, timeout=60) as response:
        content = response.read()
    data = json.loads(content)
    if not isinstance(data, dict) or not isinstance(data.get("instruments"), dict):
        raise SystemExit("unexpected catalogue layout; nothing was replaced")
    TARGET.write_bytes(content)
    print(f"Updated {data['name']} {data['version']} with {len(data['instruments'])} instruments")
    print(f"Wrote {TARGET}")
    print("Update aeoi/README.md and run the tests")


if __name__ == "__main__":
    main()
