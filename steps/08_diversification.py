"""STEP 8 — What else could grow here?

Built after talking to growers, who said the useful question is not "should I tear out
my almonds" but "what else could I put in alongside them" — for climate reasons and,
just as pressing, market ones. Napa and Healdsburg growers described surplus grapes
with nowhere to go as the wine market softened. Diversification is a hedge against both
kinds of risk at once.

The method avoids modelling climate entirely, because it does not need to:

  A crop growing in a place is proof that it grows in that place.

So for each grid cell we take the full crop inventory of its NEIGHBOURHOOD — every cell
within NEIGHBOUR_KM — and look for crops that are well established nearby but barely
present in this cell. Those are the diversification candidates. They are already proven
in the local growing environment, on comparable ground, under the same water regime,
by growers who are presumably not losing money on them.

Two signals are attached to every candidate:

  reach    how many nearby cells grow it. A crop in twenty cells is robust to local
           soil quirks; a crop in two might just be one operation's experiment.
  trend    change in its acreage across the study area, 2016 to 2023. Expanding means
           growers keep choosing it; contracting is a warning.

The trend is the closest thing to a market signal available without price data, and it
conflates climate, water, market and policy. That limit is stated on the map itself.

    python steps/08_diversification.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"
SITE = ROOT / "docs" / "data"

GDB_2023 = RAW / "dwr_2023" / "i15_Crop_Mapping_2023_Final.gdb"
GDB_2016 = RAW / "dwr_2016" / "i15_Crop_Mapping_2016_GDB" / "i15_Crop_Mapping_2016.gdb"

# Study areas, and the counties in each. Crop acreage trends are computed WITHIN a
# study area, never across all of them. Grapes fell 27% in Fresno as the raisin market
# contracted and barely moved in Napa; showing a Napa grower the Fresno number would be
# actively misleading about their own market.
REGIONS = {
    "Tulare Lake Basin": ["Fresno", "Kings", "Tulare", "Kern"],
    "San Joaquin Valley": ["Madera", "Merced", "Stanislaus", "San Joaquin"],
    "Sacramento Valley": ["Butte", "Colusa", "Glenn", "Sutter", "Yolo"],
    "Napa / Sonoma wine country": ["Napa", "Sonoma"],
}
COUNTIES = [c for cs in REGIONS.values() for c in cs]

NEIGHBOUR_KM = 30          # how far afield a grower would consider "around here"
MIN_CANDIDATE_ACRES = 400  # ignore trial plots; this must be a real local industry
MIN_REACH = 3              # present in at least this many nearby cells
ALREADY_HERE = 0.02        # >2% of a cell's acreage means it is not a diversification
TOP_CANDIDATES = 14
PER_CLASS = 4
TOP_CURRENT = 6

# Not crops. Never offer these as something to plant.
NON_CROP = {"X", "I", "I1", "I2", "I4", "I5", "I6", "YP", "S", "U", "Z", "E", "NC",
            "UL", "UR", "UC", "UI", "UV", "NV", "NB", "NR", "NS", "NW", "W"}

# DWR's first letter is the crop class. Grouping by it matters because ranking purely
# on acreage hands an almond grower "cotton" and "hay" — technically true, useless as
# advice. A grower diversifying wants options at a comparable level of capital and
# intensity, so the map presents each class separately and lets them choose the row.
CLASS_LABEL = {
    "D": "Orchard — deciduous fruit & nuts",
    "C": "Orchard — citrus & subtropical",
    "V": "Vineyard",
    "T": "Row & truck crops",
    "F": "Field crops",
    "G": "Grain & hay",
    "P": "Pasture & forage",
    "R": "Rice",
}
# Roughly how much capital and how long before a return. Shown so nobody reads a
# perennial suggestion as something you can try for one season.
CLASS_COMMITMENT = {
    "D": "perennial — years to bearing",
    "C": "perennial — years to bearing",
    "V": "perennial — years to bearing",
    "T": "annual — can trial in one season",
    "F": "annual — can trial in one season",
    "G": "annual — can trial in one season",
    "P": "multi-year stand",
    "R": "annual, needs flood infrastructure",
}


def crop_class(code: str) -> str:
    return code[0].upper() if isinstance(code, str) and code else "?"


def display_name(code: str, names: dict) -> str:
    """A readable label even for codes the 2016 vintage never named."""
    if code in names:
        return names[code]
    label = CLASS_LABEL.get(crop_class(code))
    return f"{label} ({code})" if label else code


def crop_names() -> dict[str, str]:
    """Full code -> readable name lookup. Only the 2016 vintage ships names."""
    g = gpd.read_file(GDB_2016, columns=["CROPTYP2", "Crop2016"], ignore_geometry=True)
    out = {}
    for code, name in zip(g["CROPTYP2"], g["Crop2016"]):
        if isinstance(code, str) and isinstance(name, str):
            out.setdefault(code, name)
    return out


def load_inventory() -> gpd.GeoDataFrame:
    frames = []
    for county in COUNTIES:
        g = gpd.read_file(
            GDB_2023,
            where=f"COUNTY = '{county}'",
            columns=["COUNTY", "CROPTYP2", "ACRES", "HYDRO_RGN"],
        )
        frames.append(g)
        print(f"  {county:<8} {len(g):>7,} fields")
    gdf = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=frames[0].crs)
    return gdf.rename(columns={"COUNTY": "county", "CROPTYP2": "code",
                               "ACRES": "acres", "HYDRO_RGN": "region"})


def region_trends() -> dict[str, dict[str, float]]:
    """Percent change in each crop's acreage 2016 -> 2023, computed per study area."""
    out = {}
    for region, counties in REGIONS.items():
        totals = {}
        for year, gdb in ((2016, GDB_2016), (2023, GDB_2023)):
            county_col = "County" if year == 2016 else "COUNTY"
            acres_col = "Acres" if year == 2016 else "ACRES"
            clause = " OR ".join(f"{county_col} = '{c}'" for c in counties)
            g = gpd.read_file(gdb, where=clause,
                              columns=[county_col, "CROPTYP2", acres_col],
                              ignore_geometry=True)
            totals[year] = g.groupby("CROPTYP2")[acres_col].sum()

        trend = {}
        for code in set(totals[2016].index) | set(totals[2023].index):
            before = float(totals[2016].get(code, 0.0))
            after = float(totals[2023].get(code, 0.0))
            if before < 500:        # too small a base for a percentage to mean anything
                continue
            trend[code] = round((after - before) / before * 100, 1)
        out[region] = trend
        print(f"  {region:<28} {len(trend)} crops with a usable trend")
    return out


def county_region(county: str) -> str:
    for region, counties in REGIONS.items():
        if county in counties:
            return region
    return "—"


def main() -> None:
    areas_path = SITE / "areas.geojson"
    if not areas_path.exists():
        sys.exit("run steps/07_build_basin_map.py first — this needs its grid")
    cells = gpd.read_file(areas_path).to_crs("EPSG:3310")

    print("loading crop inventory:")
    inv = load_inventory().to_crs("EPSG:3310")
    names = crop_names()
    print(f"  {len(names)} named crop codes")

    print("computing acreage trends 2016 -> 2023, per study area…")
    trends_by_region = region_trends()

    # Assign every field in the study counties to a grid cell.
    pts = inv.copy()
    pts["geometry"] = pts.geometry.representative_point()
    tagged = gpd.sjoin(pts, cells[["cell_id", "geometry"]], predicate="within")
    tagged = tagged[~tagged["code"].isin(NON_CROP)]
    tagged["study_region"] = tagged["county"].map(county_region)
    # A cell takes the study area most of its fields sit in.
    cell_region = tagged.groupby("cell_id")["study_region"].agg(
        lambda v: v.mode().iloc[0] if len(v.mode()) else "—").to_dict()
    print(f"  {len(tagged):,} crop fields fall inside the {len(cells)} grid cells")

    by_cell = (tagged.groupby(["cell_id", "code"])["acres"]
               .agg(acres="sum", fields="size").reset_index())
    cell_total = by_cell.groupby("cell_id")["acres"].sum()

    # Neighbourhood = cells whose centre is within NEIGHBOUR_KM.
    centres = cells.geometry.centroid
    coords = np.c_[centres.x, centres.y]
    ids = cells["cell_id"].to_numpy()
    dist = np.linalg.norm(coords[:, None, :] - coords[None, :, :], axis=-1)
    neighbours = {int(ids[i]): ids[dist[i] <= NEIGHBOUR_KM * 1000].tolist()
                  for i in range(len(ids))}

    lookup = {(int(r.cell_id), r.code): (float(r.acres), int(r.fields))
              for r in by_cell.itertuples()}
    cells_with = by_cell.groupby("code")["cell_id"].apply(set).to_dict()

    # Everything grown anywhere in each study area, with acreage and trend. In a place
    # as monocultural as Napa, the neighbourhood comparison can legitimately return no
    # candidates at all — every crop proven nearby is already grown in the cell. That is
    # a true and useful answer, but an empty panel is not, so every cell also carries
    # the full list of crops proven anywhere in its region.
    region_crops: dict[str, list] = {}
    for region, counties in REGIONS.items():
        # Computed from the FULL county inventory, not from the sampled grid cells.
        # Once the wine sample was clustered into three districts the grid shrank, and
        # taking this from the grid silently cut "crops proven in the region" from 8 to
        # 3 — an artefact of where we chose to sample, not a fact about the region.
        sub = inv[inv["county"].isin(counties) & ~inv["code"].isin(NON_CROP)]
        totals = sub.groupby("code")["acres"].sum().sort_values(ascending=False)
        trend = trends_by_region.get(region, {})
        region_crops[region] = [
            {"code": c, "name": display_name(c, names), "acres": int(round(a)),
             "class_label": CLASS_LABEL.get(crop_class(c), "Other"),
             "commitment": CLASS_COMMITMENT.get(crop_class(c), ""),
             "trend_pct": trend.get(c)}
            for c, a in totals.items() if a >= MIN_CANDIDATE_ACRES
        ]
        print(f"  {region:<28} {len(region_crops[region])} crops proven region-wide")

    out = {}
    for cell_id, near in neighbours.items():
        region = cell_region.get(cell_id, "—")
        trend = trends_by_region.get(region, {})
        near_set = set(int(n) for n in near)
        here_total = float(cell_total.get(cell_id, 0.0))
        if here_total <= 0:
            continue

        here = {code: a for (cid, code), (a, _) in lookup.items() if cid == cell_id}
        current = sorted(here.items(), key=lambda kv: -kv[1])[:TOP_CURRENT]

        # Everything grown anywhere in the neighbourhood.
        nearby: dict[str, float] = {}
        for (cid, code), (a, _) in lookup.items():
            if cid in near_set:
                nearby[code] = nearby.get(code, 0.0) + a

        candidates = []
        for code, near_acres in nearby.items():
            if near_acres < MIN_CANDIDATE_ACRES:
                continue
            reach = len(cells_with.get(code, set()) & near_set)
            if reach < MIN_REACH:
                continue
            # Already a meaningful part of this cell? Then it is not diversification.
            if here.get(code, 0.0) / here_total > ALREADY_HERE:
                continue
            candidates.append({
                "code": code,
                "name": display_name(code, names),
                "class": crop_class(code),
                "class_label": CLASS_LABEL.get(crop_class(code), "Other"),
                "commitment": CLASS_COMMITMENT.get(crop_class(code), ""),
                "nearby_acres": int(round(near_acres)),
                "reach": reach,
                "trend_pct": trend.get(code),
                "expanding": (trend.get(code) or 0) > 10,
            })

        # Rank by how established it is nearby, then by how widely spread.
        candidates.sort(key=lambda c: (-c["nearby_acres"], -c["reach"]))

        # Group by class and keep the strongest few in each, so a grape grower sees
        # other perennials rather than a page of field crops they will never plant.
        grouped: dict[str, list] = {}
        for c in candidates:
            grouped.setdefault(c["class_label"], []).append(c)
        grouped = {k: v[:PER_CLASS] for k, v in grouped.items()}

        out[str(cell_id)] = {
            "by_class": grouped,
            "current": [{"code": c, "name": display_name(c, names),
                         "acres": int(round(a)),
                         "share": round(a / here_total * 100, 1),
                         "trend_pct": trend.get(c)} for c, a in current],
            "total_acres": int(round(here_total)),
            "candidates": candidates[:TOP_CANDIDATES],
            "neighbour_km": NEIGHBOUR_KM,
            "region": region,
            "region_crops": region_crops.get(region, []),
        }

    (SITE / "diversification.json").write_text(json.dumps(out, separators=(",", ":")))

    n_cand = np.mean([len(v["candidates"]) for v in out.values()]) if out else 0
    print(f"\n{len(out)} cells profiled, {n_cand:.1f} candidates each on average")
    print(f"wrote {(SITE / 'diversification.json').stat().st_size / 1024:.0f} KB")

    sample = next(iter(out.values()))
    print("\nexample cell — grown here now:")
    for c in sample["current"]:
        t = f"{c['trend_pct']:+.0f}%" if c["trend_pct"] is not None else "—"
        print(f"  {c['name'][:34]:<35} {c['acres']:>7,} ac  {c['share']:>5.1f}%  {t:>7}")
    print("\n  could also grow here — established nearby, little of it in this cell:")
    for label, group in sample["by_class"].items():
        print(f"\n  {label}")
        for c in group:
            t = f"{c['trend_pct']:+.0f}%" if c["trend_pct"] is not None else "—"
            flag = "  expanding" if c["expanding"] else ""
            print(f"    {c['name'][:32]:<33} {c['nearby_acres']:>7,} ac nearby  "
                  f"in {c['reach']:>2} cells  {t:>7}{flag}")


if __name__ == "__main__":
    main()
