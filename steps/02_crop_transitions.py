"""STEP 2 — What did farmers actually switch to?

This is the "what to switch to" layer, and it needs no Planet data at all. DWR mapped
every field in California in 2016 and again in 2023. Overlay the two and you can read
off the real decisions real growers made on real ground.

Method, stated plainly enough to go in the paper:

  Take a representative interior point of every 2023 field, find which 2016 field
  polygon contains it, and pair up the crop codes. Sum 2023 acreage by
  (2016 crop -> 2023 crop).

Why interior points and not a full polygon intersection: field boundaries get redrawn
between vintages — blocks split, neighbours merge — so an exact intersection produces
thousands of meaningless slivers. A point match asks the cleaner question, "what is
growing where this field's middle is now?" The cost is that acreage is approximate
where boundaries shifted a lot. For a "what are people planting instead" signal that
is a fair trade; if the paper needs precise acreage, redo it as an intersection.

    python steps/02_crop_transitions.py --county Fresno --crop Almonds

No Planet credentials needed.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "processed"
OUT.mkdir(parents=True, exist_ok=True)

GDB = {
    2016: RAW / "dwr_2016" / "i15_Crop_Mapping_2016_GDB" / "i15_Crop_Mapping_2016.gdb",
    2023: RAW / "dwr_2023" / "i15_Crop_Mapping_2023_Final.gdb",
}

# California Albers — equal-area, so acreage and interior points are both trustworthy.
ALBERS = "EPSG:3310"

# DWR renames columns between vintages (2016 "County"/"Acres" became 2023
# "COUNTY"/"ACRES") and changes CRS (3857 -> 4269). Resolve names case-insensitively
# rather than hardcoding either year's spelling — assuming one vintage's schema is
# exactly what broke the first version of this script.
WANTED = {
    "county": ("county",),
    "code": ("croptyp2",),
    "acres": ("acres",),
    "region": ("hydro_rgn",),      # 2023 only — DWR's hydrologic region
    "planted": ("yr_planted",),    # 2023 only — orchard planting year, our age control
    "cropname": ("crop2016", "crop2018", "crop2019"),   # 2016-era readable name
}

# Codes that are not a crop. Kept in the analysis — going idle IS a transition, and an
# important one — but flagged so they're never presented as an agronomic suggestion.
NON_CROP = {"X", "I", "I1", "I4", "I5", "I6", "YP", "S", "U", "Z", "E", "NC"}


def resolve_columns(path: Path) -> dict[str, str]:
    """Map our canonical names onto whatever this vintage actually calls them."""
    actual = list(pyogrio.read_info(path)["fields"])
    lower = {c.lower(): c for c in actual}
    found = {}
    for canonical, candidates in WANTED.items():
        for cand in candidates:
            if cand in lower:
                found[canonical] = lower[cand]
                break
    if "code" not in found or "acres" not in found:
        raise RuntimeError(f"{path.name}: missing crop/acres column. Has: {actual}")
    return found


def load_year(year: int, county: str) -> gpd.GeoDataFrame:
    path = GDB[year]
    cols = resolve_columns(path)

    gdf = gpd.read_file(
        path,
        where=f"{cols['county']} = '{county}'",
        columns=list(cols.values()),
    )
    gdf = gdf.rename(columns={v: k for k, v in cols.items()})
    for optional in ("region", "cropname", "planted"):
        if optional not in gdf.columns:
            gdf[optional] = None

    return gdf.to_crs(ALBERS)[
        ["county", "code", "cropname", "region", "planted", "acres", "geometry"]
    ]


def code_to_name(*frames: gpd.GeoDataFrame) -> dict[str, str]:
    """Build a code -> readable name lookup from whichever vintage supplies names.

    2016 ships a `Crop2016` column with real names ("Almonds"); 2023 does not — its
    MAIN_CROP field just repeats the code. So we learn the vocabulary from 2016 and
    apply it to both years.
    """
    lookup: dict[str, str] = {}
    for f in frames:
        named = f[f["cropname"].notna()]
        for code, name in zip(named["code"], named["cropname"]):
            if isinstance(code, str) and isinstance(name, str):
                lookup.setdefault(code, name)
    return lookup


def build_transitions(county: str) -> pd.DataFrame:
    print(f"loading {county} County…")
    a = load_year(2016, county)
    b = load_year(2023, county)
    print(f"  2016: {len(a):>7,} fields")
    print(f"  2023: {len(b):>7,} fields")
    if b["region"].notna().any():
        print(f"  hydrologic regions present: {sorted(b['region'].dropna().unique())}")

    names = code_to_name(a, b)
    print(f"  crop vocabulary: {len(names)} named codes")

    # Rename before joining so there are no colliding columns and therefore no
    # guessing about which suffix pandas applied.
    later = b[["code", "acres", "region", "planted", "geometry"]].rename(
        columns={"code": "code_2023", "acres": "acres_2023", "region": "hydro_region"}
    ).copy()
    later["geometry"] = later.geometry.representative_point()

    earlier = a[["code", "geometry"]].rename(columns={"code": "code_2016"})

    joined = gpd.sjoin(later, earlier, how="inner", predicate="within")
    print(f"  matched: {len(joined):,} of {len(b):,} 2023 fields "
          f"({len(joined) / len(b) * 100:.0f}%)")

    # sjoin kept `later`'s index, which is `b`'s index, so we can recover each match's
    # original polygon rather than the interior point we joined on. Step 3 needs the
    # real boundary to ask Planet for statistics over the field.
    out = gpd.GeoDataFrame(
        {
            "code_2016": joined["code_2016"].values,
            "code_2023": joined["code_2023"].values,
            "hydro_region": joined["hydro_region"].values,
            "planted": pd.to_numeric(joined["planted"], errors="coerce").values,
            "acres": joined["acres_2023"].values,
        },
        geometry=b.loc[joined.index, "geometry"].values,
        crs=b.crs,
    )
    out["crop_2016"] = out["code_2016"].map(names).fillna(out["code_2016"])
    out["crop_2023"] = out["code_2023"].map(names).fillna(out["code_2023"])
    out["field_id"] = [f"fr{i:06d}" for i in range(len(out))]
    return out


def report(tr: pd.DataFrame, crop: str, min_acres: float = 200.0) -> None:
    from_crop = tr[tr["crop_2016"] == crop]
    if from_crop.empty:
        print(f"\nno 2016 fields named {crop!r}. Available names:")
        print(sorted(tr["crop_2016"].dropna().unique().tolist())[:40])
        return

    total = from_crop["acres"].sum()
    stayed = from_crop[from_crop["crop_2023"] == crop]["acres"].sum()
    changed = total - stayed

    print(f"\n{'=' * 66}")
    print(f"{crop}: what 2016 {crop.lower()} ground was growing in 2023")
    print("=" * 66)
    print(f"2016 {crop.lower()} acreage matched : {total:>10,.0f} ac")
    print(f"still {crop.lower()} in 2023        : {stayed:>10,.0f} ac  ({stayed/total*100:.0f}%)")
    print(f"became something else         : {changed:>10,.0f} ac  ({changed/total*100:.0f}%)")

    moved = (
        from_crop[from_crop["crop_2023"] != crop]
        .groupby(["code_2023", "crop_2023"])["acres"].sum()
        .sort_values(ascending=False).reset_index()
    )
    moved = moved[moved["acres"] >= min_acres]

    print(f"\nwhere it went  (≥{min_acres:.0f} ac)")
    print(f"  {'now growing':<36} {'acres':>9}  {'share':>7}")
    print("  " + "-" * 62)
    for r in moved.itertuples():
        flag = "   ← not a crop" if r.code_2023 in NON_CROP else ""
        print(f"  {str(r.crop_2023)[:35]:<36} {r.acres:>9,.0f}  "
              f"{r.acres/changed*100:>6.0f}%{flag}")

    into = (
        tr[(tr["crop_2023"] == crop) & (tr["crop_2016"] != crop)]
        .groupby(["code_2016", "crop_2016"])["acres"].sum()
        .sort_values(ascending=False).reset_index()
    )
    into = into[into["acres"] >= min_acres]
    gained = into["acres"].sum()
    print(f"\nwhat became {crop.lower()} by 2023  (≥{min_acres:.0f} ac, {gained:,.0f} ac total)")
    print(f"  {'was in 2016':<36} {'acres':>9}")
    print("  " + "-" * 47)
    for r in into.itertuples():
        flag = "   ← not a crop" if r.code_2016 in NON_CROP else ""
        print(f"  {str(r.crop_2016)[:35]:<36} {r.acres:>9,.0f}{flag}")

    print(f"\nnet change in {crop.lower()} ground: {gained - changed:+,.0f} ac")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--county", default="Fresno")
    ap.add_argument("--crop", default="Almonds")
    args = ap.parse_args()

    tr = build_transitions(args.county)

    stem = f"transitions_{args.county.lower()}_2016_2023"
    # Table for analysis, geopackage for anything that needs the field boundaries.
    pd.DataFrame(tr.drop(columns="geometry")).to_parquet(OUT / f"{stem}.parquet", index=False)
    tr.to_crs("EPSG:4326").to_file(OUT / f"{stem}.gpkg", driver="GPKG")

    report(tr, args.crop)

    print(f"\n\nsaved {len(tr):,} matched fields to {stem}.{{parquet,gpkg}}")
    print(f"({tr.groupby(['crop_2016', 'crop_2023']).ngroups:,} distinct crop pairs)")


if __name__ == "__main__":
    main()
