"""STEP 3 — Did switching crops actually work?

This is the question the whole project turns on. Step 2 told us that between 2016 and
2023, Fresno growers moved 31,434 acres from grapes into almonds and 5,861 acres from
almonds into pistachios. What it cannot tell us is whether those were good decisions.

So we take matched groups of fields — ones that stayed put, ones that switched — and
measure nine years of vigour and moisture on each. If the switchers now outperform the
stayers on comparable ground, that is an evidence-backed transition recommendation.
If they do not, that is a finding too, and a more honest one than most reports carry.

Cohorts are compared on the SAME index over the SAME years, so the only difference
between them is what the grower planted.

    python steps/03_cohort_analysis.py --per-cohort 50

Costs roughly 15 PU per field. 6 cohorts x 50 fields is about 4,500 PU — around 1% of
a monthly allowance. Run with --per-cohort 5 first if you want to watch it work.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from farms.indices import INDEX_NAMES  # noqa: E402
from farms.planet import PlanetStats  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"

START, END = "2017-01-01", "2025-12-31"
SEASON = range(3, 11)          # March–October
MIN_MONTHS_PER_YEAR = 4
MIN_YEARS = 6

IDLE = {"X", "I", "I1", "I4", "I5", "I6"}

# The comparisons worth making. Each is (label, 2016 code(s), 2023 code(s)).
# Pairing a "stayed" cohort against a "switched" cohort is what makes the
# result interpretable — without the control, a declining trend could just be
# a declining decade.
COHORTS = [
    ("almonds → almonds",    {"D12"}, {"D12"}),
    ("almonds → pistachios", {"D12"}, {"D14"}),
    ("almonds → idle",       {"D12"}, IDLE),
    ("grapes → grapes",      {"V"},   {"V"}),
    ("grapes → almonds",     {"V"},   {"D12"}),
    ("grapes → pistachios",  {"V"},   {"D14"}),
]


def load_fields(county: str) -> gpd.GeoDataFrame:
    path = PROCESSED / f"transitions_{county.lower()}_2016_2023.gpkg"
    if not path.exists():
        print(f"missing {path}\nrun: python steps/02_crop_transitions.py --county {county}")
        sys.exit(1)
    return gpd.read_file(path)


def pick_cohorts(fields: gpd.GeoDataFrame, per_cohort: int, seed: int,
                 min_acres: float, max_acres: float) -> gpd.GeoDataFrame:
    """Stratified sample per cohort, size-matched so cohorts stay comparable.

    Field size is constrained to a band rather than left free because a 500-acre block
    and a 15-acre block are not the same measurement — bigger fields average over more
    variation. Holding size roughly constant removes that as a confounder.
    """
    sized = fields[(fields["acres"] >= min_acres) & (fields["acres"] <= max_acres)]
    picked = []
    for label, from_codes, to_codes in COHORTS:
        sel = sized[sized["code_2016"].isin(from_codes) & sized["code_2023"].isin(to_codes)]
        if sel.empty:
            print(f"  {label:<24} no fields in range — skipped")
            continue
        take = sel.sample(min(len(sel), per_cohort), random_state=seed).copy()
        take["cohort"] = label
        picked.append(take)
        planted = pd.to_numeric(take.get("planted"), errors="coerce").dropna()
        planted = planted[(planted > 1950) & (planted < 2026)]
        age = f"planted ~{planted.median():.0f}" if len(planted) else "planting yr n/a"
        print(f"  {label:<24} {len(take):>3} of {len(sel):>6,} available   {age}")
    if not picked:
        print("no cohorts had fields. Widen the acreage band.")
        sys.exit(1)
    return gpd.GeoDataFrame(pd.concat(picked, ignore_index=True), crs=fields.crs)


def fetch(sample: gpd.GeoDataFrame, client: PlanetStats, workers: int) -> pd.DataFrame:
    jobs = [
        {"field_id": r.field_id, "geometry": r.geometry,
         "start": START, "end": END, "acres": r.acres}
        for r in sample.itertuples()
    ]
    cohort_of = dict(zip(sample["field_id"], sample["cohort"]))

    bar = tqdm(total=len(jobs), desc=f"fetching ({workers} at a time)")
    frames, errors = client.fetch_many(jobs, workers=workers, on_result=bar.update)
    bar.close()

    for field_id, message in errors:
        print(f"  {field_id}: {message[:120]}")
    if errors:
        print(f"  {len(errors)} field(s) failed")

    out = []
    for field_id, df in frames:
        df = df.copy()
        df["cohort"] = cohort_of.get(field_id)
        out.append(df)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def annual(series: pd.DataFrame) -> pd.DataFrame:
    """Collapse monthly values to one growing-season figure per field per year.

    NDVI uses the season peak — the standard canopy-vigour proxy, insensitive to
    planting date. NDMI and NDWI use the season mean, because for water the sustained
    condition matters more than the single best day.
    """
    df = series.copy()
    df["year"] = df["date"].dt.year
    df = df[df["date"].dt.month.isin(SEASON)]

    # A fully clouded month comes back as None, which makes the whole column object
    # dtype and silently breaks max/mean. Coerce first, then let pandas skip the NaNs.
    for idx in INDEX_NAMES:
        df[idx] = pd.to_numeric(df[idx], errors="coerce")

    out = df.groupby(["cohort", "field_id", "year"]).agg(
        ndvi=("ndvi", "max"),
        ndmi=("ndmi", "mean"),
        ndwi=("ndwi", "mean"),
        months=("ndvi", "count"),
    ).reset_index()
    return out[out["months"] >= MIN_MONTHS_PER_YEAR]


def theil_sen(x: np.ndarray, y: np.ndarray) -> float:
    slopes = []
    for i in range(len(x) - 1):
        dx, dy = x[i + 1:] - x[i], y[i + 1:] - y[i]
        ok = dx != 0
        slopes.extend((dy[ok] / dx[ok]).tolist())
    return float(np.median(slopes)) if slopes else 0.0


def field_trends(yearly: pd.DataFrame) -> pd.DataFrame:
    import pymannkendall as mk

    rows = []
    for (cohort, field_id), g in yearly.groupby(["cohort", "field_id"]):
        g = g.sort_values("year")
        if len(g) < MIN_YEARS:
            continue
        years = g["year"].to_numpy(float)
        rec = {"cohort": cohort, "field_id": field_id, "n_years": len(g),
               "first_year": int(years[0]), "last_year": int(years[-1])}
        for idx in INDEX_NAMES:
            vals = g[idx].to_numpy(float)
            if np.isnan(vals).any():
                rec[f"{idx}_slope"] = np.nan
                rec[f"{idx}_p"] = np.nan
                rec[f"{idx}_mean"] = np.nan
                continue
            rec[f"{idx}_slope"] = theil_sen(years, vals)
            rec[f"{idx}_p"] = float(mk.original_test(vals).p)
            rec[f"{idx}_mean"] = float(vals.mean())
            rec[f"{idx}_recent"] = float(vals[-3:].mean())
        rows.append(rec)
    return pd.DataFrame(rows)


def report(trends: pd.DataFrame) -> None:
    print(f"\n{'=' * 78}")
    print("COHORT COMPARISON — Fresno County, 2017–2025")
    print("=" * 78)

    for idx in INDEX_NAMES:
        print(f"\n{idx.upper()}")
        print(f"  {'cohort':<24} {'n':>4} {'mean':>8} {'recent':>8} "
              f"{'slope/yr':>10} {'% declining':>12}")
        print("  " + "-" * 72)
        for cohort, g in trends.groupby("cohort"):
            slope = g[f"{idx}_slope"]
            p = g[f"{idx}_p"]
            declining = ((slope < 0) & (p < 0.05)).mean() * 100
            print(f"  {cohort:<24} {len(g):>4} {g[f'{idx}_mean'].mean():>8.3f} "
                  f"{g[f'{idx}_recent'].mean():>8.3f} "
                  f"{slope.median():>10.4f} {declining:>11.0f}%")

    print(f"\n{'=' * 78}")
    print("THE COMPARISON THAT MATTERS")
    print("=" * 78)
    print("""
  Read TRENDS, not levels. Absolute index values are not comparable between
  crops — a trellised vineyard carries bare soil between rows and will always
  read lower than a closed almond canopy, whatever its health. What IS
  comparable is the direction each cohort is moving in.

  Tree age is the other confounder. A field that switched crops was replanted,
  so it holds young trees whose canopy is still filling in. Rising NDVI on a
  switched cohort may be trees growing up rather than conditions improving.
  Check the planting years printed above before reading anything into a slope.
""")

    for stayed, switched in [("almonds \u2192 almonds", "almonds \u2192 pistachios"),
                             ("grapes \u2192 grapes", "grapes \u2192 almonds"),
                             ("almonds \u2192 almonds", "almonds \u2192 idle")]:
        a = trends[trends["cohort"] == stayed]
        b = trends[trends["cohort"] == switched]
        if a.empty or b.empty:
            continue
        print(f"  {stayed}  vs  {switched}   (n={len(a)} vs {len(b)})")
        for idx in INDEX_NAMES:
            sa, sb = a[f"{idx}_slope"].median(), b[f"{idx}_slope"].median()
            da = ((a[f"{idx}_slope"] < 0) & (a[f"{idx}_p"] < 0.05)).mean() * 100
            db = ((b[f"{idx}_slope"] < 0) & (b[f"{idx}_p"] < 0.05)).mean() * 100
            verdict = "switched improving faster" if sb > sa else "stayers doing better"
            print(f"    {idx.upper():<5} slope/yr  stayed {sa:+.4f}  switched {sb:+.4f}   "
                  f"declining {da:.0f}% vs {db:.0f}%   {verdict}")
        print()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--county", default="Fresno")
    ap.add_argument("--per-cohort", type=int, default=50)
    ap.add_argument("--min-acres", type=float, default=40.0)
    ap.add_argument("--max-acres", type=float, default=160.0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--workers", type=int, default=8,
                    help="concurrent API requests")
    args = ap.parse_args()

    fields = load_fields(args.county)
    print(f"{len(fields):,} matched fields available\n")
    print("cohort sampling:")
    sample = pick_cohorts(fields, args.per_cohort, args.seed, args.min_acres, args.max_acres)

    client = PlanetStats()
    print(f"\nfetching {len(sample)} field series, 2017–2025…")
    series = fetch(sample, client, args.workers)
    print(f"\n{client.summary()}")

    if series.empty:
        print("nothing returned")
        sys.exit(1)

    yearly = annual(series)
    trends = field_trends(yearly)
    print(f"{len(trends)} fields had a long enough record to trend")

    series.to_parquet(PROCESSED / "cohort_monthly.parquet", index=False)
    yearly.to_parquet(PROCESSED / "cohort_annual.parquet", index=False)
    trends.to_parquet(PROCESSED / "cohort_trends.parquet", index=False)
    sample.to_file(PROCESSED / "cohort_fields.gpkg", driver="GPKG")

    report(trends)
    print(f"\n\nsaved to {PROCESSED}")


if __name__ == "__main__":
    main()
