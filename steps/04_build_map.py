"""STEP 4 — Bake the analysis into the static map.

The published map never calls Planet. If it did, the OAuth secret would ship to every
visitor's browser, every page view would burn processing units, and the site would die
the moment the allowance ran out. So everything is precomputed into small static files
and the site becomes plain HTML that any host will serve for free, indefinitely.

    python steps/04_build_map.py
    python -m http.server 8000 --directory site
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from farms.indices import INDEX_HELP, INDEX_LABELS, INDEX_NAMES  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
SITE = ROOT / "docs" / "data"

# NDVI and NDMI have a clear good/bad direction, so they get the red-to-green ramp the
# eye already reads as bad-to-good. NDWI does not — standing water is neither good nor
# bad, it is just information (flood irrigation, ponding, a rice check) — so it gets a
# neutral dry-to-wet ramp instead. Colouring it red/green would assert a judgement the
# data does not support.
SCALES = {
    "ndvi": {"kind": "good_bad", "label": "less green → greener"},
    "ndmi": {"kind": "good_bad", "label": "drier plants → wetter plants"},
    "ndwi": {"kind": "dry_wet",  "label": "dry ground → standing water"},
}



# Landscape view. Fields answer "how is this block doing"; a farmer or a planner also
# needs "how is this AREA doing", and 240 individual dots do not answer that at a
# glance. Aggregating to a grid gives the rain-map read at regional scale, and it is
# the same code whether there are 240 fields in one county or 20,000 across four
# regions — which is why it is a grid rather than hand-drawn regions.
GRID_KM = 12
MIN_FIELDS_PER_CELL = 2


def build_grid(fields: gpd.GeoDataFrame, annual: pd.DataFrame):
    """Aggregate fields into GRID_KM cells. Returns (cells_gdf, per-cell series)."""
    from shapely.geometry import box

    metric = fields.to_crs("EPSG:3310")          # equal-area, so cells are equal-area
    cell = GRID_KM * 1000
    minx, miny, maxx, maxy = metric.total_bounds

    xs = np.arange(np.floor(minx / cell) * cell, maxx + cell, cell)
    ys = np.arange(np.floor(miny / cell) * cell, maxy + cell, cell)
    cells = gpd.GeoDataFrame(
        {"cell_id": range(len(xs) * len(ys))},
        geometry=[box(x, y, x + cell, y + cell) for x in xs for y in ys],
        crs="EPSG:3310",
    )

    pts = metric.copy()
    pts["geometry"] = pts.geometry.representative_point()
    tagged = gpd.sjoin(pts[["field_id", "geometry"]], cells, predicate="within")
    cell_of = dict(zip(tagged["field_id"], tagged["cell_id"]))

    counts = pd.Series(cell_of).value_counts()
    keep = set(counts[counts >= MIN_FIELDS_PER_CELL].index)
    if not keep:
        return None, {}

    ann = annual.copy()
    ann["cell_id"] = ann["field_id"].map(cell_of)
    ann = ann[ann["cell_id"].isin(keep)]

    # Median rather than mean: one anomalous field should not swing a whole cell.
    per_year = ann.groupby(["cell_id", "year"])[list(INDEX_NAMES)].median().reset_index()

    series = {}
    for cell_id, g in per_year.groupby("cell_id"):
        g = g.sort_values("year")
        entry = {"years": [int(y) for y in g["year"]]}
        for idx in INDEX_NAMES:
            entry[idx] = [None if pd.isna(v) else round(float(v), 3) for v in g[idx]]
        series[str(int(cell_id))] = entry

    out = cells[cells["cell_id"].isin(keep)].copy()
    out["n_fields"] = out["cell_id"].map(counts).astype(int)

    # Cell-level trend is the median of the member fields' trends, not a trend fitted
    # to the median series — the former is far less sensitive to fields dropping in and
    # out of the record between years.
    trends = pd.read_parquet(PROCESSED / "cohort_trends.parquet")
    trends["cell_id"] = trends["field_id"].map(cell_of)
    agg = trends[trends["cell_id"].isin(keep)].groupby("cell_id")
    for idx in INDEX_NAMES:
        out[f"{idx}_slope"] = out["cell_id"].map(agg[f"{idx}_slope"].median()).round(4)
        out[f"{idx}_mean"] = out["cell_id"].map(agg[f"{idx}_mean"].median()).round(3)

    return out.to_crs("EPSG:4326"), series


def main() -> None:
    fields = gpd.read_file(PROCESSED / "cohort_fields.gpkg").to_crs("EPSG:4326")
    annual = pd.read_parquet(PROCESSED / "cohort_annual.parquet")
    trends = pd.read_parquet(PROCESSED / "cohort_trends.parquet")

    SITE.mkdir(parents=True, exist_ok=True)

    geo = fields.merge(trends, on="field_id", how="inner", suffixes=("", "_t"))
    if "cohort_t" in geo.columns:
        geo = geo.drop(columns=["cohort_t"])

    keep = ["field_id", "cohort", "crop_2016", "crop_2023", "acres", "planted",
            "hydro_region", "n_years", "first_year", "last_year"]
    for idx in INDEX_NAMES:
        keep += [f"{idx}_slope", f"{idx}_p", f"{idx}_mean", f"{idx}_recent"]
    keep = [c for c in keep if c in geo.columns]

    out = geo[keep + ["geometry"]].copy()
    for col in out.columns:
        if out[col].dtype.kind == "f":
            out[col] = out[col].round(4)
    # Field edges do not need sub-metre precision at county zoom, and payload size is
    # what makes a map unusable on a phone with two bars in the middle of an orchard.
    out["geometry"] = out.geometry.simplify(0.00005, preserve_topology=True)
    out.to_file(SITE / "fields.geojson", driver="GeoJSON")

    # Per-field yearly values — this is what the time slider scrubs through.
    series: dict[str, dict] = {}
    for field_id, g in annual.groupby("field_id"):
        g = g.sort_values("year")
        entry = {"years": [int(y) for y in g["year"]]}
        for idx in INDEX_NAMES:
            entry[idx] = [None if pd.isna(v) else round(float(v), 3) for v in g[idx]]
        series[str(field_id)] = entry
    (SITE / "series.json").write_text(json.dumps(series, separators=(",", ":")))

    # Colour ranges from the actual data rather than guessed constants, so the gradient
    # uses its full range instead of squashing everything into two shades.
    ranges = {}
    for idx in INDEX_NAMES:
        vals = pd.to_numeric(annual[idx], errors="coerce").dropna()
        slopes = pd.to_numeric(trends[f"{idx}_slope"], errors="coerce").dropna()
        smax = float(np.nanpercentile(np.abs(slopes), 90)) if len(slopes) else 0.02
        ranges[idx] = {
            "value_min": round(float(np.nanpercentile(vals, 5)), 3),
            "value_max": round(float(np.nanpercentile(vals, 95)), 3),
            # Diverging scales must be symmetric about zero or "no change" stops
            # sitting at the neutral colour and the map lies about direction.
            "slope_abs": round(max(smax, 0.005), 4),
        }

    cells, cell_series = build_grid(fields, annual)
    if cells is not None:
        cells.to_file(SITE / "areas.geojson", driver="GeoJSON")
        (SITE / "area_series.json").write_text(
            json.dumps(cell_series, separators=(",", ":")))
        print(f"areas.geojson    {(SITE / 'areas.geojson').stat().st_size / 1024:>7.1f} KB "
              f"({len(cells)} cells of {GRID_KM} km)")

    years = sorted(int(y) for y in annual["year"].unique())
    meta = {
        "title": "Farms on the Move",
        "subtitle": "Fresno County · crop transitions and field condition, 2017–2025",
        "n_fields": int(len(out)),
        "years": years,
        "indices": [
            {"key": k, "label": INDEX_LABELS[k], "help": INDEX_HELP[k],
             "scale": SCALES[k]["kind"], "scale_label": SCALES[k]["label"],
             **ranges[k]}
            for k in INDEX_NAMES
        ],
        "cohorts": sorted(out["cohort"].dropna().unique().tolist()),
        "grid_km": GRID_KM,
        "n_cells": 0 if cells is None else int(len(cells)),
        "center": [-119.9, 36.65],
        "zoom": 9,
        "method": (
            "Sentinel-2 L2A via Planet's Statistical API. For each month we take the "
            "clearest observation and read all three indices from that same scene. "
            "Growing season is March–October; NDVI uses the season peak, NDMI and NDWI "
            "the season mean. Trends are Theil–Sen slopes tested with Mann–Kendall."
        ),
        "caveats": [
            "Index levels are NOT comparable between different crops. A trellised "
            "vineyard carries bare soil between rows and reads lower than an almond "
            "canopy regardless of health. Compare trends, not levels.",
            "Fields that switched crops were replanted, so they hold young trees whose "
            "canopy is still filling in. A rising trend there may be trees growing up "
            "rather than conditions improving.",
            "These are observed vegetation trends, not yield, and not a recommendation. "
            "They are a starting point for a conversation with local expertise.",
        ],
    }
    (SITE / "meta.json").write_text(json.dumps(meta, indent=2))

    size_kb = (SITE / "fields.geojson").stat().st_size / 1024
    print(f"fields.geojson  {size_kb:>8.1f} KB   ({len(out)} fields)")
    print(f"series.json     {(SITE / 'series.json').stat().st_size / 1024:>8.1f} KB")
    print(f"years           {years[0]}–{years[-1]}")
    print(f"\nPreview:  python -m http.server 8000 --directory site")
    if size_kb > 5000:
        print("\nwarning: >5 MB will be slow on a phone. Move to vector tiles.")


if __name__ == "__main__":
    main()
