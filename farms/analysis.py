"""Shared analysis: monthly series -> annual figures -> per-field trends.

Lives here rather than in a step script so that step 3 and step 3b cannot quietly
diverge in how they compute a trend. If the two steps disagreed on method, their
results would not be comparable and nobody would notice.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
from tqdm import tqdm

from .indices import INDEX_NAMES
from .planet import PlanetStats

SEASON = range(3, 11)            # March–October growing season
MIN_MONTHS_PER_YEAR = 4
MIN_YEARS = 6
P_THRESHOLD = 0.05


def existing_sample_ids(path) -> set:
    """Field ids from a previous run of this step, if there is one.

    Increasing a sample size normally REDRAWS it — pandas' sample(80) is not a superset
    of sample(40) — which would re-fetch fields already paid for and cached. Seeding the
    new draw with the old members makes growth additive: you pay only for the top-up.
    """
    import geopandas as _gpd
    if not path.exists():
        return set()
    try:
        return set(_gpd.read_file(path)["field_id"].astype(str))
    except Exception:
        return set()


def top_up(pool, per_group: int, seed: int, keep: set):
    """Take everything already sampled from this pool, then fill to `per_group`."""
    have = pool[pool["field_id"].astype(str).isin(keep)]
    if len(have) >= per_group:
        return have.head(per_group)
    rest = pool[~pool["field_id"].astype(str).isin(keep)]
    need = per_group - len(have)
    if len(rest) == 0:
        return have
    add = rest.sample(min(len(rest), need), random_state=seed)
    import pandas as _pd
    return _pd.concat([have, add])


def fetch_cohorts(sample: gpd.GeoDataFrame, client: PlanetStats,
                  start: str, end: str, workers: int = 8,
                  interval: str = "P1M") -> pd.DataFrame:
    jobs = [
        {"field_id": r.field_id, "geometry": r.geometry,
         "start": start, "end": end, "acres": r.acres, "interval": interval}
        for r in sample.itertuples()
    ]
    cohort_of = dict(zip(sample["field_id"], sample["cohort"]))

    bar = tqdm(total=len(jobs), desc=f"fetching ({workers} at a time)")
    frames, errors = client.fetch_many(jobs, workers=workers, on_result=bar.update)
    bar.close()

    for field_id, message in errors[:10]:
        print(f"  {field_id}: {message[:110]}")
    if errors:
        print(f"  {len(errors)} field(s) failed")

    out = []
    for field_id, df in frames:
        df = df.copy()
        df["cohort"] = cohort_of.get(field_id)
        out.append(df)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def annual(series: pd.DataFrame, min_obs: int = MIN_MONTHS_PER_YEAR) -> pd.DataFrame:
    """One growing-season figure per field per year.

    NDVI takes the season peak — the standard canopy-vigour proxy, insensitive to
    planting date. NDMI and NDWI take the season mean, because for water the sustained
    condition matters more than the single best day.
    """
    df = series.copy()
    df["year"] = df["date"].dt.year
    df = df[df["date"].dt.month.isin(SEASON)]

    # A fully clouded month returns None, which makes the column object dtype and
    # silently breaks max/mean far from the cause.
    for idx in INDEX_NAMES:
        df[idx] = pd.to_numeric(df[idx], errors="coerce")

    out = df.groupby(["cohort", "field_id", "year"]).agg(
        ndvi=("ndvi", "max"), ndmi=("ndmi", "mean"), ndwi=("ndwi", "mean"),
        months=("ndvi", "count"),
    ).reset_index()
    return out[out["months"] >= min_obs]


def theil_sen(x: np.ndarray, y: np.ndarray) -> float:
    """Median of all pairwise slopes — robust to a single anomalous year."""
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
                rec.update({f"{idx}_slope": np.nan, f"{idx}_p": np.nan,
                            f"{idx}_mean": np.nan, f"{idx}_recent": np.nan})
                continue
            rec[f"{idx}_slope"] = theil_sen(years, vals)
            rec[f"{idx}_p"] = float(mk.original_test(vals).p)
            rec[f"{idx}_mean"] = float(vals.mean())
            rec[f"{idx}_recent"] = float(vals[-3:].mean())
        rows.append(rec)
    return pd.DataFrame(rows)
