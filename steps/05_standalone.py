"""STEP 5 — Build a single self-contained HTML file.

Two reasons this exists. First, a served directory needs a running web server, and a
dead terminal takes the map with it. Second, when you hand this to a grower or to CAFF,
"double-click this file" beats "run these three commands" every time.

The data is inlined into the page, so it opens straight from disk with no server. It
still pulls Leaflet and the basemap tiles from the internet, so it needs a connection
the first time — for genuinely offline use the tiles would have to be bundled too.

    python steps/05_standalone.py
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "docs"
DATA = SITE / "data"
VENDOR = ROOT / "vendor"


def main() -> None:
    html = (SITE / "index.html").read_text()

    def load(name, fallback):
        path = DATA / name
        return json.loads(path.read_text()) if path.exists() else fallback

    payload = {
        "meta": load("meta.json", {}),
        "fields": load("fields.geojson", {"features": []}),
        "series": load("series.json", {}),
        "areas": load("areas.geojson", {"features": []}),
        "areaSeries": load("area_series.json", {}),
        "chips": load("chips.json", {}),
        "diversify": load("diversification.json", {}),
    }

    # Inline the imagery as data URIs. ~900 KB of JPEG becomes ~1.2 MB base64, which is
    # a fair price for a file that shows real satellite imagery with nothing to install.
    chip_dir = SITE / "chips"
    chip_data = {}
    if chip_dir.exists():
        for f in sorted(chip_dir.glob("*.jpg")):
            encoded = base64.b64encode(f.read_bytes()).decode()
            chip_data[f.stem] = f"data:image/jpeg;base64,{encoded}"
    payload["chipData"] = chip_data

    # </script> anywhere inside the JSON would close the tag early and break the page.
    blob = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    injected = f'<script>window.EMBEDDED={blob};</script>\n'

    # Vendor Leaflet into the page rather than loading it from a CDN. A file called
    # "self-contained" that still needs two external hosts is not self-contained: it
    # breaks in any viewer with a content-security policy, and offline entirely.
    css = (VENDOR / "leaflet.css").read_text()
    for name in ("layers-2x.png", "layers.png"):
        encoded = base64.b64encode((VENDOR / name).read_bytes()).decode()
        css = css.replace(f"url(images/{name})", f"url(data:image/png;base64,{encoded})")
    js = (VENDOR / "leaflet.js").read_text()

    out_html = html.replace(
        '<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">',
        f"<style>\n{css}\n</style>",
    )
    out_html = out_html.replace(
        '<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>',
        f"<script>\n{js}\n</script>\n" + injected,
    )
    assert "window.EMBEDDED" in out_html, "failed to inject the data blob"

    out = SITE / "farms-on-the-move.html"
    out.write_text(out_html)
    print(f"{out}")
    print(f"{out.stat().st_size / 1024 / 1024:.2f} MB — open it by double-clicking")
    print(f"{len(chip_data)} satellite chips embedded")


if __name__ == "__main__":
    main()
