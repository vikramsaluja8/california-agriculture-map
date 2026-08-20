"""STEP 3c — Condition trends where the cohort method does not apply.

Steps 3 and 3b compare growers who switched crops against growers who did not, with
planting year controlling for tree age. Neither works in wine country:

  * Napa vineyards barely change hands between crops — 94% of 2016 grape ground was
    still grapes in 2023, and most of the 6% that moved went idle rather than to
    another crop. There is no "switched" cohort to compare against.
  * DWR records no planting year for vineyards. Zero of 24,496 Napa/Sonoma grape
    fields have one, so the age control is simply unavailable.

So this step samples the dominant crops in a county and measures condition and trend
directly, without cohort framing. Instead of planting year it uses a control derived
from the imagery itself:

  MATURE AT START — a field whose first three years already carry a full canopy was
  not planted during the record, so its trend cannot be canopy establishment.

That is a weaker control than a known planting date, but it is derived from the same
data everywhere, so it works for vineyards, for orchards with missing records, and for
any region added later.

    python steps/03c_condition_sample.py --county Napa --per-crop 60
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from farms.analysis import annual, fetch_cohorts, field_trends  # noqa: E402
from farms.indices import INDEX_NAMES  # noqa: E402
from farms.planet import PlanetStats  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"

START, END = "2017-01-01", "2025-12-31"

# Crops worth sampling. Anything below this share of a county's farmland is too minor
# to carry a meaningful sample.
MIN_COUNTY_ACRES = 1500

NON_CROP = {"X", "I", "I1", "I2", "I4", "I5", "I6", "YP", "S", "U", "Z", "E", "NC", "W"}

# Named growing districts, for regions where a sample spread across whole counties is
# unreadable. Wine country is the case in point: vineyards run from the Napa floor to
# the Sonoma coast, and a scattered sample raises more questions than it answers. These
# are the districts growers actually name, so a clustered sample lands on ground they
# recognise. (lat, lon, radius_km)
DISTRICT_SETS = {
    "wine": {
        "Napa Valley (Napa–St. Helena)":      (38.400, -122.380, 15),
        "Healdsburg (Dry Creek / Alexander)": (38.620, -122.870, 12),
        "Sonoma Valley":                      (38.340, -122.490, 10),
    },
}

EARTH_KM = 111.32


def assign_district(fields: gpd.GeoDataFrame, districts: dict) -> gpd.GeoDataFrame:
    """Label each field with its nearest district, dropping anything outside them all.

    Nearest-centre rather than overlapping boxes: Napa Valley and Sonoma Valley are only
    about 12 km apart, so any radius wide enough to cover each would double-count the
    ground between them. Assigning to the closest centre resolves that deterministically.
    """
    import numpy as np

    pts = fields.to_crs("EPSG:4326").geometry.representative_point()
    lat = pts.y.to_numpy()
    lon = pts.x.to_numpy()

    names, best_km = [], np.full(len(fields), np.inf)
    chosen = [None] * len(fields)
    for name, (dlat, dlon, radius) in districts.items():
        # Local flat-earth approximation is ample at these distances.
        dy = (lat - dlat) * EARTH_KM
        dx = (lon - dlon) * EARTH_KM * np.cos(np.radians(dlat))
        km = np.hypot(dx, dy)
        take = (km < best_km) & (km <= radius)
        best_km = np.where(take, km, best_km)
        for i in np.nonzero(take)[0]:
            chosen[i] = name
        names.append(name)

    out = fields.copy()
    out["district"] = chosen
    out["district_km"] = np.where(np.isfinite(best_km), best_km.round(1), None)
    return out[out["district"].notna()]

# A field already carrying this much canopy in its first three seasons was mature when
# the record began. Set below a closed orchard/vineyard canopy but well above bare
# ground or a young planting.
MATURE_NDVI = 0.45

# The maturity filter only means something for perennials, where low early-years NDVI
# says "planted during the record". For an annual crop the same signal says "fallow
# that year" — and excluding those fields would quietly delete the fallowing signal,
# which in the Sacramento Valley is the whole story. DWR class letters: D deciduous,
# C citrus/subtropical, V vineyard.
PERENNIAL_CLASSES = {"D", "C", "V"}


def pick(fields: gpd.GeoDataFrame, per_crop: int, seed: int,
         min_acres: float, max_acres: float, min_acres_group: float) -> gpd.GeoDataFrame:
    sized = fields[(fields["acres"] >= min_acres) & (fields["acres"] <= max_acres)].copy()
    sized = sized[~sized["code_2023"].isin(NON_CROP)]

    # Sample within each district separately so every district gets real coverage,
    # rather than the largest one swamping the others.
    groups = ([(d, g) for d, g in sized.groupby("district")]
              if "district" in sized.columns else [(None, sized)])

    picked = []
    for district, block in groups:
        if district:
            print(f"\n  {district}  ({len(block):,} fields available)")
        by_crop = (block.groupby(["code_2023", "crop_2023"])["acres"]
                   .sum().sort_values(ascending=False))
        for (code, name), acres in by_crop.items():
            if acres < min_acres_group:
                continue
            sel = block[block["code_2023"] == code]
            take = sel.sample(min(len(sel), per_crop), random_state=seed).copy()
            take["cohort"] = name
            picked.append(take)
            print(f"    {name[:28]:<29} {len(take):>3} of {len(sel):>5,} fields "
                  f"({acres:>7,.0f} ac)")
    if not picked:
        sys.exit("no crop cleared the acreage threshold")
    return gpd.GeoDataFrame(pd.concat(picked, ignore_index=True), crs=fields.crs)


def flag_mature(yearly: pd.DataFrame, trends: pd.DataFrame,
                sample: gpd.GeoDataFrame) -> pd.DataFrame:
    """Mark perennial fields that already had a full canopy in the first three seasons.

    Annual crops are always marked mature: there is nothing to control for, because an
    annual is replanted every year regardless. Applying the canopy test to them would
    drop fields that were fallow early in the record — precisely the fields a study of
    fallowing needs to keep.
    """
    first = (yearly.sort_values("year").groupby("field_id").head(3)
             .groupby("field_id")["ndvi"].mean())
    code_of = dict(zip(sample["field_id"], sample["code_2023"]))

    trends = trends.copy()
    trends["start_ndvi"] = trends["field_id"].map(first).round(3)
    trends["crop_class"] = trends["field_id"].map(
        lambda f: (code_of.get(f) or "?")[0].upper())
    trends["perennial"] = trends["crop_class"].isin(PERENNIAL_CLASSES)
    trends["mature_at_start"] = (~trends["perennial"]) | (trends["start_ndvi"] >= MATURE_NDVI)
    return trends


def report(trends: pd.DataFrame, county: str) -> None:
    print(f"\n{'=' * 78}")
    print(f"CONDITION BY CROP — {county} County, 2017–2025")
    print("=" * 78)
    mature = trends[trends["mature_at_start"]]
    n_per = int(trends["perennial"].sum())
    dropped = int((~trends["mature_at_start"]).sum())
    print(f"{len(mature)} of {len(trends)} fields trended below. "
          f"{n_per} are perennial, of which {dropped} were dropped as planted during "
          f"the record. Annual crops are never dropped — a low early NDVI there means "
          f"fallow, not young.")

    for idx in INDEX_NAMES:
        print(f"\n{idx.upper()}")
        print(f"  {'crop':<26} {'n':>4} {'mean':>8} {'recent':>8} {'slope/yr':>10} "
              f"{'declining':>10}")
        print("  " + "-" * 70)
        for crop, g in mature.groupby("cohort"):
            if len(g) < 5:
                continue
            dec = ((g[f"{idx}_slope"] < 0) & (g[f"{idx}_p"] < 0.05)).mean() * 100
            print(f"  {crop[:25]:<26} {len(g):>4} {g[f'{idx}_mean'].mean():>8.3f} "
                  f"{g[f'{idx}_recent'].mean():>8.3f} "
                  f"{g[f'{idx}_slope'].median():>10.4f} {dec:>9.0f}%")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--county", required=True)
    ap.add_argument("--per-crop", type=int, default=60)
    ap.add_argument("--min-acres", type=float, default=8.0)
    ap.add_argument("--max-acres", type=float, default=160.0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--cluster", choices=sorted(DISTRICT_SETS),
                    help="restrict the sample to named growing districts")
    ap.add_argument("--min-group-acres", type=float, default=None,
                    help="acreage a crop needs within its group to be sampled")
    args = ap.parse_args()

    path = PROCESSED / f"transitions_{args.county.lower()}_2016_2023.gpkg"
    if not path.exists():
        sys.exit(f"missing {path} — run steps/02_crop_transitions.py --county {args.county}")
    fields = gpd.read_file(path)
    print(f"{len(fields):,} matched fields in {args.county}")

    if args.cluster:
        districts = DISTRICT_SETS[args.cluster]
        fields = assign_district(fields, districts)
        if fields.empty:
            print(f"  no fields in {args.county} fall inside the {args.cluster} districts")
            return
        print(f"  {len(fields):,} fall inside the {args.cluster} districts:")
        for d, n in fields["district"].value_counts().items():
            print(f"    {d:<38} {n:>6,}")

    floor = args.min_group_acres
    if floor is None:
        floor = 400.0 if args.cluster else MIN_COUNTY_ACRES
    print("\nsampling:")
    sample = pick(fields, args.per_crop, args.seed, args.min_acres, args.max_acres, floor)

    client = PlanetStats()
    print(f"\nfetching {len(sample)} field series…")
    series = fetch_cohorts(sample, client, START, END, args.workers)
    print(f"\n{client.summary()}")
    if series.empty:
        sys.exit("nothing returned")

    yearly = annual(series)
    trends = flag_mature(yearly, field_trends(yearly), sample)
    print(f"{len(trends)} fields trended")

    tag = args.county.lower()
    series.to_parquet(PROCESSED / f"condition_{tag}_monthly.parquet", index=False)
    yearly.to_parquet(PROCESSED / f"condition_{tag}_annual.parquet", index=False)
    trends.to_parquet(PROCESSED / f"condition_{tag}_trends.parquet", index=False)
    sample.to_file(PROCESSED / f"condition_{tag}_fields.gpkg", driver="GPKG")

    report(trends, args.county)
    print(f"\n\nsaved to {PROCESSED}")


if __name__ == "__main__":
    main()
