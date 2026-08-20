"""STEP 7 — Build the map from every county analysed, not just one.

Step 4 was hardwired to the Fresno cohort run. This finds every finished analysis in
data/processed and merges them, so adding a county means running steps 2 and 3b for it
and rerunning this — no code change.

Two kinds of analysis are merged:

  cohort_*      step 3   — six transition cohorts, Fresno only
  agematch_*    step 3b  — age-controlled cohorts, every county

They coexist because they answer different questions, and a field can legitimately
appear in both. `analysis` on each feature says which run it came from, so nothing is
silently averaged across two different study designs.

    python steps/07_build_basin_map.py
"""

from __future__ import annotations

import json
import re
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

GRID_KM = 12
MIN_FIELDS_PER_CELL = 2

SCALES = {
    "ndvi": {"kind": "good_bad", "label": "less green → greener"},
    "ndmi": {"kind": "good_bad", "label": "drier plants → wetter plants"},
    "ndwi": {"kind": "dry_wet",  "label": "dry ground → standing water"},
}

# Which hydrologic regions belong to which study area. Fields carry HYDRO_RGN from DWR,
# so the grouping comes from the data rather than from a county list we maintain.
REGION_OF = {
    "Tulare Lake": "Tulare Lake Basin",
    "San Joaquin River": "San Joaquin Valley",
    "Sacramento River": "Sacramento Valley",
    "Central Coast": "Salinas Valley",
    # Wine country straddles two DWR regions — Sonoma is mostly North Coast, Napa
    # mostly San Francisco Bay — so both map onto the study area growers recognise.
    "North Coast": "Napa / Sonoma wine country",
    "San Francisco Bay": "Napa / Sonoma wine country",
}


def discover() -> list[tuple[str, str, Path, Path, Path]]:
    """Find every (analysis, county) with a complete set of outputs."""
    found = []
    for gpkg in sorted(PROCESSED.glob("*_fields.gpkg")):
        stem = gpkg.name.replace("_fields.gpkg", "")
        m = re.match(r"^(cohort|agematch|condition)_?(.*)$", stem)
        if not m:
            continue
        kind, county = m.group(1), m.group(2) or "fresno"
        trends = PROCESSED / f"{stem}_trends.parquet"
        annual = PROCESSED / f"{stem}_annual.parquet"
        if trends.exists() and annual.exists():
            found.append((kind, county.title(), gpkg, trends, annual))
    return found


def global_crop_names() -> dict[str, str]:
    """One code -> name lookup for the whole state.

    Step 2 learns names per county from that county's own 2016 records, so a code that
    happens to be unnamed there stays a bare code. Rice is "Rice" in Colusa and "R1" in
    Sutter — which would split one crop into two cohorts, put both in the map's filter,
    and break any cross-county comparison without ever erroring. Reading the vocabulary
    once, statewide, removes the possibility.
    """
    gdb = ROOT / "data" / "raw" / "dwr_2016" / "i15_Crop_Mapping_2016_GDB" / "i15_Crop_Mapping_2016.gdb"
    g = gpd.read_file(gdb, columns=["CROPTYP2", "Crop2016"], ignore_geometry=True)
    names = {}
    for code, name in zip(g["CROPTYP2"], g["Crop2016"]):
        if isinstance(code, str) and isinstance(name, str):
            names.setdefault(code, name)
    return names


def load_all():
    parts, annuals = [], []
    for kind, county, gpkg, trends_p, annual_p in discover():
        fields = gpd.read_file(gpkg).to_crs("EPSG:4326")
        trends = pd.read_parquet(trends_p)
        annual = pd.read_parquet(annual_p)

        merged = fields.merge(trends, on="field_id", how="inner", suffixes=("", "_t"))
        merged = merged.drop(columns=[c for c in merged.columns if c.endswith("_t")])
        merged["county"] = county
        if "district" not in merged.columns:
            merged["district"] = None
        merged["analysis"] = {"cohort": "transitions",
                              "agematch": "age-matched",
                              "condition": "condition sample"}[kind]

        # field_id is only unique within a county's run, so namespace it before merging
        # — otherwise Kings fr000123 would collide with Kern fr000123 and one would win.
        uid = f"{kind[:1]}{county[:2].lower()}_"
        merged["field_id"] = uid + merged["field_id"].astype(str)
        annual = annual.copy()
        annual["field_id"] = uid + annual["field_id"].astype(str)

        parts.append(merged)
        annuals.append(annual)
        print(f"  {county:<10} {kind:<9} {len(merged):>4} fields")

    if not parts:
        sys.exit("no analyses found — run steps 2 and 3b for at least one county")

    fields = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs="EPSG:4326")
    annual = pd.concat(annuals, ignore_index=True)

    # Normalise crop labels statewide so one crop is one label everywhere.
    names = global_crop_names()
    renamed = 0
    for col in ("crop_2016", "crop_2023", "cohort"):
        if col not in fields.columns:
            continue
        before = fields[col].copy()
        fields[col] = fields[col].map(lambda v: names.get(v, v))
        renamed += int((before != fields[col]).sum())
    if "cohort" in annual.columns:
        annual["cohort"] = annual["cohort"].map(lambda v: names.get(v, v))
    if renamed:
        print(f"  normalised {renamed:,} crop labels to statewide names")

    return fields, annual


def build_grid(fields: gpd.GeoDataFrame, annual: pd.DataFrame, trends: pd.DataFrame):
    from shapely.geometry import box

    metric = fields.to_crs("EPSG:3310")
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
        return None, {}, {}

    ann = annual.copy()
    ann["cell_id"] = ann["field_id"].map(cell_of)
    ann = ann[ann["cell_id"].isin(keep)]
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

    t = trends.copy()
    t["cell_id"] = t["field_id"].map(cell_of)
    agg = t[t["cell_id"].isin(keep)].groupby("cell_id")
    for idx in INDEX_NAMES:
        out[f"{idx}_slope"] = out["cell_id"].map(agg[f"{idx}_slope"].median()).round(4)
        out[f"{idx}_mean"] = out["cell_id"].map(agg[f"{idx}_mean"].median()).round(3)

    # Label each cell with the study region most of its fields sit in.
    region = fields.set_index("field_id")["hydro_region"].to_dict()
    reg_of_cell = {}
    for fid, cid in cell_of.items():
        if cid in keep:
            reg_of_cell.setdefault(cid, []).append(region.get(fid))
    out["region"] = out["cell_id"].map(
        {c: REGION_OF.get(pd.Series(v).mode().iloc[0] if any(pd.notna(v)) else None, "—")
         for c, v in reg_of_cell.items()})

    return out.to_crs("EPSG:4326"), series, cell_of


def main() -> None:
    SITE.mkdir(parents=True, exist_ok=True)
    print("merging analyses:")
    fields, annual = load_all()
    print(f"\ntotal {len(fields):,} fields across "
          f"{fields['county'].nunique()} counties")

    fields["region"] = fields["hydro_region"].map(REGION_OF).fillna("—")

    keep = ["field_id", "county", "region", "district", "analysis", "cohort",
            "crop_2016", "crop_2023", "acres", "planted", "hydro_region",
            "n_years", "first_year", "last_year", "worst_year", "worst_year_drop"]
    for idx in INDEX_NAMES:
        keep += [f"{idx}_slope", f"{idx}_p", f"{idx}_mean", f"{idx}_recent"]
    keep = [c for c in keep if c in fields.columns]

    out = fields[keep + ["geometry"]].copy()

    # Worst-year anomaly. A nine-year trend describes gradual drift well and hides
    # shocks completely: Colusa rice ran a flat +0.0003/yr NDVI trend across a year when
    # half the county went unplanted, because it recovered. For annual crops especially,
    # the question "what was the worst year here, and how bad" is the one the data can
    # actually answer, and a slope will never surface it.
    ann = annual.copy()
    ann["ndvi"] = pd.to_numeric(ann["ndvi"], errors="coerce")
    normal = ann.groupby("field_id")["ndvi"].median()
    ann["normal"] = ann["field_id"].map(normal)
    ann["shortfall"] = (ann["normal"] - ann["ndvi"]) / ann["normal"] * 100
    worst = ann.loc[ann.groupby("field_id")["shortfall"].idxmax()]
    out["worst_year"] = out["field_id"].map(dict(zip(worst["field_id"], worst["year"])))
    out["worst_year_drop"] = out["field_id"].map(
        dict(zip(worst["field_id"], worst["shortfall"]))).round(0)
    # Only worth showing when the year genuinely stands out from the field's own record.
    out.loc[out["worst_year_drop"] < 15, ["worst_year", "worst_year_drop"]] = None
    n_anom = int(out["worst_year"].notna().sum())
    print(f"flagged a stand-out bad year for {n_anom:,} of {len(out):,} fields")

    # Benchmark percentiles. A field's own trend is hard to judge in isolation — is
    # -0.004/yr bad? Ranking it against comparable fields answers the question a grower
    # actually asks: "how am I doing compared with everyone else growing this here?"
    #
    # Peers are the same crop in the same county. Crop matters because index levels are
    # not comparable across crops; county keeps climate and water district roughly
    # constant. Groups with too few members get no percentile rather than a meaningless
    # one — a rank out of four is noise dressed as a statistic.
    MIN_PEERS = 8
    peer_key = ["county", "crop_2023"]
    out["n_peers"] = out.groupby(peer_key)["field_id"].transform("size")
    for metric in ("ndvi_recent", "ndmi_recent", "ndvi_slope", "ndmi_slope"):
        if metric not in out.columns:
            continue
        pct = out.groupby(peer_key)[metric].rank(pct=True, method="average") * 100
        out[f"pct_{metric}"] = pct.round(0).where(out["n_peers"] >= MIN_PEERS)
    n_ranked = int(out["pct_ndmi_recent"].notna().sum()) if "pct_ndmi_recent" in out else 0
    print(f"benchmarked {n_ranked:,} of {len(out):,} fields "
          f"({out.groupby(peer_key).ngroups} crop x county groups)")

    series = {}
    for field_id, g in annual.groupby("field_id"):
        g = g.sort_values("year")
        entry = {"years": [int(y) for y in g["year"]]}
        for idx in INDEX_NAMES:
            entry[idx] = [None if pd.isna(v) else round(float(v), 3) for v in g[idx]]
        series[str(field_id)] = entry
    (SITE / "series.json").write_text(json.dumps(series, separators=(",", ":")))

    cells, cell_series, cell_of = build_grid(fields, annual, fields)
    # Carry the cell id onto every field so a grower who taps their own block can still
    # be shown the diversification options for their area.
    out["cell_id"] = out["field_id"].map(cell_of)

    for col in out.columns:
        if out[col].dtype.kind == "f":
            out[col] = out[col].round(4)
    out["geometry"] = out.geometry.simplify(0.00005, preserve_topology=True)
    # GeoJSON defaults to ~15 significant digits per coordinate — nanometre precision on
    # a field boundary, at roughly double the file size. Five decimals is about a metre,
    # far finer than the source mapping, and the payload is what decides whether this
    # opens on a phone in an orchard.
    out.to_file(SITE / "fields.geojson", driver="GeoJSON", COORDINATE_PRECISION=5)

    if cells is not None:
        cells.to_file(SITE / "areas.geojson", driver="GeoJSON", COORDINATE_PRECISION=5)
        (SITE / "area_series.json").write_text(
            json.dumps(cell_series, separators=(",", ":")))

    ranges = {}
    for idx in INDEX_NAMES:
        vals = pd.to_numeric(annual[idx], errors="coerce").dropna()
        slopes = pd.to_numeric(fields[f"{idx}_slope"], errors="coerce").dropna()
        smax = float(np.nanpercentile(np.abs(slopes), 90)) if len(slopes) else .02
        ranges[idx] = {
            "value_min": round(float(np.nanpercentile(vals, 5)), 3),
            "value_max": round(float(np.nanpercentile(vals, 95)), 3),
            "slope_abs": round(max(smax, 0.005), 4),
        }

    years = sorted(int(y) for y in annual["year"].unique())
    counties = sorted(out["county"].dropna().unique().tolist())
    meta = {
        "title": "Farms on the Move",
        "subtitle": f"{', '.join(counties)} · crop transitions and field condition, "
                    f"{years[0]}–{years[-1]}",
        "n_fields": int(len(out)),
        "years": years,
        "counties": counties,
        "districts": sorted(out["district"].dropna().unique().tolist()),
        "regions": sorted(out["region"].dropna().unique().tolist()),
        "indices": [
            {"key": k, "label": INDEX_LABELS[k], "help": INDEX_HELP[k],
             "scale": SCALES[k]["kind"], "scale_label": SCALES[k]["label"], **ranges[k]}
            for k in INDEX_NAMES
        ],
        "cohorts": sorted(out["cohort"].dropna().unique().tolist()),
        "grid_km": GRID_KM,
        "n_cells": 0 if cells is None else int(len(cells)),
        "center": [float(out.geometry.centroid.x.mean()),
                   float(out.geometry.centroid.y.mean())],
        "zoom": 8,
        "method": (
            "Sentinel-2 L2A via Planet's Statistical API. For each month we take the "
            "clearest observation and read all three indices from that same scene. "
            "Growing season is March–October; NDVI uses the season peak, NDMI and NDWI "
            "the season mean. Trends are Theil–Sen slopes tested with Mann–Kendall."
        ),
        "caveats": [
            "Index levels are NOT comparable between different crops, or between "
            "counties. A trellised vineyard reads lower than an almond canopy whatever "
            "its health. Compare trends, not levels.",
            "Fields that switched crops were replanted, so they hold young trees whose "
            "canopy is still filling in. The age-matched cohorts control for this; the "
            "transition cohorts do not.",
            "These are observed vegetation trends, not yield, and not a recommendation. "
            "They are a starting point for a conversation with local expertise.",
        ],
    }
    (SITE / "meta.json").write_text(json.dumps(meta, indent=2))

    print(f"\nfields.geojson  {(SITE / 'fields.geojson').stat().st_size / 1024:>8.1f} KB")
    print(f"areas.geojson   {(SITE / 'areas.geojson').stat().st_size / 1024:>8.1f} KB "
          f"({0 if cells is None else len(cells)} cells)")
    print(f"counties        {', '.join(counties)}")
    print(f"regions         {', '.join(meta['regions'])}")


if __name__ == "__main__":
    main()
