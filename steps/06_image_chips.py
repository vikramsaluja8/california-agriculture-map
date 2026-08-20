"""STEP 6 — Pre-render true-colour satellite chips, one per field per year.

The point is to let someone scrub the year slider and WATCH a block change: an orchard
being pulled, ground going fallow, a young planting filling in. A number falling from
0.7 to 0.3 is an argument; seeing the trees disappear is not.

Why pre-render instead of loading imagery live: a live map would have to carry the
OAuth secret into every visitor's browser and would burn processing units on every
page view. Rendering once, here, means the published map is static files with no key
and no running cost — and it still works when the grant period is over.

Chips are small JPEGs, not PNGs. At 160 px a JPEG is roughly a fifth the size, and
these are photographs, which is exactly what JPEG is for. Size matters because the
whole map has to open on a phone in the middle of an orchard.

    python steps/06_image_chips.py --per-cohort 3

Costs a fraction of a PU per chip.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from farms.planet import PlanetStats  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
CHIPS = ROOT / "docs" / "chips"

YEARS = range(2017, 2026)
# Peak season. Wide enough to almost always find a clear scene, narrow enough that
# every year's chip shows the crop at the same growth stage — otherwise you would be
# looking at seasonal difference and reading it as change over years.
WINDOW = ("06-15", "09-15")
SIZE = 160
PAD = 0.30            # show some surrounding ground for context

TRUE_COLOUR = """//VERSION=3
function setup() {
  return { input: ["B02", "B03", "B04"], output: { bands: 3 } };
}
function evaluatePixel(s) {
  // 2.5 is the conventional stretch from Sentinel-2 reflectance to display range.
  return [2.5 * s.B04, 2.5 * s.B03, 2.5 * s.B02];
}
"""


def chip(client: PlanetStats, geometry, year: int, out: Path) -> bool:
    if out.exists():
        return True

    lon = geometry.centroid.x
    epsg = client.utm_epsg(lon)
    proj = gpd.GeoSeries([geometry], crs="EPSG:4326").to_crs(f"EPSG:{epsg}").iloc[0]

    minx, miny, maxx, maxy = proj.bounds
    w, h = maxx - minx, maxy - miny
    # Square the box so the chip is not stretched, then pad for context.
    side = max(w, h) * (1 + PAD)
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    bbox = [cx - side / 2, cy - side / 2, cx + side / 2, cy + side / 2]

    body = {
        "input": {
            "bounds": {"bbox": bbox,
                       "properties": {"crs": f"http://www.opengis.net/def/crs/EPSG/0/{epsg}"}},
            "data": [{
                "type": "sentinel-2-l2a",
                "dataFilter": {
                    "timeRange": {"from": f"{year}-{WINDOW[0]}T00:00:00Z",
                                  "to": f"{year}-{WINDOW[1]}T23:59:59Z"},
                    "mosaickingOrder": "leastCC",
                },
            }],
        },
        "output": {
            "width": SIZE, "height": SIZE,
            "responses": [{"identifier": "default", "format": {"type": "image/jpeg"}}],
        },
        "evalscript": TRUE_COLOUR,
    }

    r = requests.post(
        f"{client.base}/api/v1/process",
        headers={"Authorization": f"Bearer {client.token()}",
                 "Content-Type": "application/json", "Accept": "image/jpeg"},
        json=body, timeout=180,
    )
    if r.status_code != 200:
        tqdm.write(f"  {out.name}: {r.status_code} {r.text[:120]}")
        return False

    spent = r.headers.get("x-processingunits-spent")
    if spent:
        client.pu_spent += float(spent)
    out.write_bytes(r.content)
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-cohort", type=int, default=3,
                    help="fields per cohort to render chips for")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    fields = gpd.read_file(PROCESSED / "cohort_fields.gpkg").to_crs("EPSG:4326")

    picked = (fields.groupby("cohort", group_keys=False)
              .apply(lambda g: g.sample(min(len(g), args.per_cohort),
                                        random_state=args.seed)))
    print(f"rendering {len(picked)} fields x {len(YEARS)} years "
          f"= {len(picked) * len(YEARS)} chips")

    CHIPS.mkdir(parents=True, exist_ok=True)
    client = PlanetStats()
    client.token()

    manifest: dict[str, list[int]] = {}
    jobs = [(r, y) for r in picked.itertuples() for y in YEARS]
    for row, year in tqdm(jobs, desc="chips"):
        out = CHIPS / f"{row.field_id}_{year}.jpg"
        if chip(client, row.geometry, year, out):
            manifest.setdefault(row.field_id, []).append(year)

    (ROOT / "docs" / "data" / "chips.json").write_text(
        json.dumps(manifest, separators=(",", ":")))

    total_kb = sum(f.stat().st_size for f in CHIPS.glob("*.jpg")) / 1024
    print(f"\n{len(list(CHIPS.glob('*.jpg')))} chips, {total_kb:.0f} KB total")
    print(f"{client.pu_spent:.2f} PU spent")
    print(f"fields with imagery: {len(manifest)}")


if __name__ == "__main__":
    main()
