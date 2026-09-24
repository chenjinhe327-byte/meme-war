"""Build a single-file version of the frontend.

The site is four files, and deploying only `index.html` leaves the page with a
stylesheet and a script that 404 - no CSS and, worse, no JavaScript at all, so
every button is dead. That is not hypothetical: it is exactly how the first
Netlify deploy went out.

`frontend/standalone.html` inlines the stylesheet and the module script and bakes
in the contract address, so dropping that one file anywhere produces a working
page. The multi-file version stays the source of truth and is what GitHub Pages
publishes.

    python tools/build_standalone.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
OUT = FRONTEND / "standalone.html"


def build() -> int:
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    css = (FRONTEND / "styles.css").read_text(encoding="utf-8")
    js = (FRONTEND / "app.js").read_text(encoding="utf-8")

    record = FRONTEND / "deployment.json"
    address = ""
    if record.is_file():
        address = json.loads(record.read_text(encoding="utf-8")).get("address", "")

    # Bake the address in: a standalone file has no deployment.json to fetch.
    if address:
        pattern = re.compile(r'let CONTRACT_ADDRESS = params\.get\("address"\) \|\| "";')
        replacement = (
            f'let CONTRACT_ADDRESS = params.get("address") || "{address}";'
        )
        js, count = pattern.subn(replacement, js)
        if count != 1:
            print("could not bake the contract address into app.js", file=sys.stderr)
            return 1

    html = html.replace(
        '<link rel="stylesheet" href="./styles.css" />',
        f"<style>\n{css}\n</style>",
    )
    html = html.replace(
        '<script type="module" src="./app.js"></script>',
        f'<script type="module">\n{js}\n</script>',
    )

    if "./app.js" in html or "./styles.css" in html:
        print("a local asset reference survived inlining", file=sys.stderr)
        return 1

    OUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUT}  ({len(html.encode('utf-8'))} bytes)")
    print(f"  contract address baked in: {address or '(none - deploy first)'}")
    print("  deploy this one file anywhere; it needs no sibling files")
    return 0


if __name__ == "__main__":
    sys.exit(build())
