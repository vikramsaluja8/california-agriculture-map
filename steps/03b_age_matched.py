"""STEP 3b — Control for tree age, so the switching result becomes usable.

Step 3 found that fields which moved from grapes to almonds show NDVI rising three
times faster than vineyards that stayed vineyards. That result is unusable as it
stands, because those almond orchards were planted around 2020 and a young orchard's
canopy fills in for 5-7 years regardless of climate. We measured trees growing up.

This step removes the confounder by holding crop AND age constant and varying only
what was there before:

    almonds planted 2018-2022 that REPLACED ALMONDS
    almonds planted 2018-2022 that REPLACED GRAPES

Same species, same age, same county, same years observed. Both canopies are filling
in at the same rate, so that effect cancels. Any remaining difference is attributable
to the ground itself and its history — which is the question a grower is actually
asking when they say "should I put almonds here".

Two mature reference cohorts are included as well. Comparing crops against each other
is only fair once both are grown, so mature-vs-mature is the ONLY place in this project
where a cross-crop level comparison is legitimate.

    python steps/03b_age_matched.py --per-cohort 40
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
YOUNG = (2018, 2022)
MATURE = (1950, 2011)

# (label, 2016 codes, 2023 codes, planting-year window)
COHORTS = [
    ("almond '18-22, was almond",   {"D12"},        {"D12"}, YOUNG),
    ("almond '18-22, was grape",    {"V"},          {"D12"}, YOUNG),
    ("pistachio '18-22, was almond", {"D12"},       {"D14"}, YOUNG),
    ("pistachio '18-22, was grape",  {"V"},         {"D14"}, YOUNG),
    ("almond mature (pre-2012)",    {"D12", "V"},   {"D12"}, MATURE),
    ("pistachio mature (pre-2012)", {"D12", "V", "D14"}, {"D14"}, MATURE),
]

# The pairs worth reading, and why each one is legitimate.
COMPARISONS = [
    ("almond '18-22, was almond", "almond '18-22, was grape",
     "Same crop, same age. Difference = the ground and its history."),
    ("pistachio '18-22, was almond", "pistachio '18-22, was grape",
     "Same crop, same age. Difference = the ground and its history."),
    ("almond mature (pre-2012)", "pistachio mature (pre-2012)",
     "Both mature — the only fair crop-vs-crop comparison here."),
]


def pick(fields: gpd.GeoDataFrame, per_cohort: int, seed: int,
         min_acres: float, max_acres: float) -> gpd.GeoDataFrame:
    fields = fields.copy()
    fields["planted"] = pd.to_numeric(fields["planted"], errors="coerce")
    sized = fields[(fields["acres"] >= min_acres) & (fields["acres"] <= max_acres)]

    picked = []
    for label, from_codes, to_codes, (lo, hi) in COHORTS:
        sel = sized[
            sized["code_2016"].isin(from_codes)
            & sized["code_2023"].isin(to_codes)
            & sized["planted"].between(lo, hi)
        ]
        if sel.empty:
            print(f"  {label:<30} none available — skipped")
            continue
        take = sel.sample(min(len(sel), per_cohort), random_state=seed).copy()
        take["cohort"] = label
        picked.append(take)
        print(f"  {label:<30} {len(take):>3} of {len(sel):>4}   "
              f"median planted {take['planted'].median():.0f}")
    if not picked:
        sys.exit("no cohorts available")
    return gpd.GeoDataFrame(pd.concat(picked, ignore_index=True), crs=fields.crs)


def report(trends: pd.DataFrame, county: str) -> None:
    print(f"\n{'=' * 80}")
    print(f"AGE-MATCHED COMPARISON — {county} County, 2017–2025")
    print("=" * 80)

    for idx in INDEX_NAMES:
        print(f"\n{idx.upper()}")
        print(f"  {'cohort':<30} {'n':>4} {'mean':>8} {'recent':>8} "
              f"{'slope/yr':>10} {'declining':>10}")
        print("  " + "-" * 74)
        for cohort, g in trends.groupby("cohort"):
            dec = ((g[f"{idx}_slope"] < 0) & (g[f"{idx}_p"] < 0.05)).mean() * 100
            print(f"  {cohort:<30} {len(g):>4} {g[f'{idx}_mean'].mean():>8.3f} "
                  f"{g[f'{idx}_recent'].mean():>8.3f} "
                  f"{g[f'{idx}_slope'].median():>10.4f} {dec:>9.0f}%")

    print(f"\n{'=' * 80}")
    print("CONTROLLED COMPARISONS")
    print("=" * 80)
    for a_label, b_label, why in COMPARISONS:
        a = trends[trends["cohort"] == a_label]
        b = trends[trends["cohort"] == b_label]
        if a.empty or b.empty:
            continue
        print(f"\n  {a_label}   vs   {b_label}")
        print(f"  {why}")
        print(f"    (n={len(a)} vs {len(b)})")
        for idx in INDEX_NAMES:
            ma, mb = a[f"{idx}_recent"].mean(), b[f"{idx}_recent"].mean()
            sa, sb = a[f"{idx}_slope"].median(), b[f"{idx}_slope"].median()
            print(f"    {idx.upper():<5} recent {ma:>7.3f} vs {mb:>7.3f} "
                  f"({mb - ma:+.3f})   slope {sa:+.4f} vs {sb:+.4f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--county", default="Fresno")
    ap.add_argument("--per-cohort", type=int, default=40)
    ap.add_argument("--min-acres", type=float, default=40.0)
    ap.add_argument("--max-acres", type=float, default=160.0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    path = PROCESSED / f"transitions_{args.county.lower()}_2016_2023.gpkg"
    fields = gpd.read_file(path)
    print(f"{len(fields):,} matched fields\n\ncohort sampling:")
    sample = pick(fields, args.per_cohort, args.seed, args.min_acres, args.max_acres)

    client = PlanetStats()
    print(f"\nfetching {len(sample)} field series, {START[:4]}–{END[:4]}…")
    series = fetch_cohorts(sample, client, START, END, args.workers)
    print(f"\n{client.summary()}")
    if series.empty:
        sys.exit("nothing returned")

    yearly = annual(series)
    trends = field_trends(yearly)
    print(f"{len(trends)} fields trended")

    # County-scoped filenames so a replication in another county cannot silently
    # overwrite the original result it is meant to be checked against.
    tag = args.county.lower()
    series.to_parquet(PROCESSED / f"agematch_{tag}_monthly.parquet", index=False)
    yearly.to_parquet(PROCESSED / f"agematch_{tag}_annual.parquet", index=False)
    trends.to_parquet(PROCESSED / f"agematch_{tag}_trends.parquet", index=False)
    sample.to_file(PROCESSED / f"agematch_{tag}_fields.gpkg", driver="GPKG")

    report(trends, args.county)
    print(f"\n\nsaved to {PROCESSED}")


if __name__ == "__main__":
    main()
