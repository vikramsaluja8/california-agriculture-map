"""STEP 9 — Climate context: the *why* behind the satellite signal.

Everything else in this project measures what the ground looks like. Nothing explains
it. This adds the climate each district has actually experienced, over a 36-year record
— four times the depth of the satellite series, which matters because nine years is too
short to call anything a climate trend.

Source: Open-Meteo's historical archive. Free, no key, ERA5-based at roughly 9 km,
36 years of daily data per location in about five seconds.

WHAT THIS DELIBERATELY DOES NOT COMPUTE: chill hours. Chill depends on winter minimum
temperatures, and a 1 degree bias in daily minimum moves accumulated chill by 22%. A
9 km reanalysis grid cannot resolve the valley cold-air pooling that sets those minima
in Napa or the Central Valley — tested against this source, chill came out RISING 2%,
which contradicts the literature and is an artefact. Chill needs gridMET at 4 km, which
is a separate and much slower pass.

    python steps/09_climate.py
"""

from __future__ import annotations

import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import sha1
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from tqdm import tqdm

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "docs" / "data"
CACHE = ROOT / "data" / "cache" / "climate"
CACHE.mkdir(parents=True, exist_ok=True)

START, END = "1990-01-01", "2025-12-31"
API = "https://archive-api.open-meteo.com/v1/archive"

GDD_BASE = 10.0          # degrees C, standard for most California crops
HEAT_THRESHOLD = 38.0    # ~100 F, the damage threshold growers talk about
SEASON = range(4, 11)    # April-October

_lock = threading.Lock()

# ERA5 resolves about 9-11 km; our grid cells are 12 km. Neighbouring cells therefore
# often fall inside the SAME climate pixel, so requesting each separately asks for
# detail the source does not have — and burns rate limit doing it. Rounding to 0.1
# degrees (~11 km) and de-duplicating matches the request grid to the data's real
# resolution: fewer calls, and no information lost.
# Set to 0.1 (matching ERA5's ~11 km) at first, but the free tier rate-limited hard:
# 305 requests of 36 years each degraded to 67 s per request with escalating backoff and
# a 5-hour ETA. 0.25 deg (~28 km) cuts it to 111 requests and roughly an hour.
#
# What that costs: 28 km blurs the Napa valley floor against its hills, and coastal
# Salinas against inland. Those are real agronomic gradients. It is a genuine loss,
# accepted because a run that does not finish is worth less than a slightly coarse one
# that does. Recorded here so the limitation travels with the data.
CLIMATE_GRID = 0.25


def snap(lat: float, lon: float) -> tuple[float, float]:
    return (round(lat / CLIMATE_GRID) * CLIMATE_GRID,
            round(lon / CLIMATE_GRID) * CLIMATE_GRID)


def fetch_daily(lat: float, lon: float) -> pd.DataFrame:
    lat, lon = snap(lat, lon)
    key = sha1(f"{lat:.2f},{lon:.2f}|{START}|{END}|v1".encode()).hexdigest()[:16]
    path = CACHE / f"{key}.json"
    if path.exists():
        payload = json.loads(path.read_text())
    else:
        payload = None
        for attempt in range(6):
            r = requests.get(API, params={
                "latitude": round(lat, 2), "longitude": round(lon, 2),
                "start_date": START, "end_date": END,
                "daily": ("temperature_2m_min,temperature_2m_max,precipitation_sum,"
                          "et0_fao_evapotranspiration"),
                "timezone": "America/Los_Angeles"}, timeout=180)
            if r.status_code == 200:
                payload = r.json()
                break
            if r.status_code in (429, 502, 503, 504):
                # A free public API. Back off generously rather than hammering it.
                time.sleep(20 * (attempt + 1))
                continue
            r.raise_for_status()
        if payload is None:
            raise RuntimeError("rate limited after 6 attempts")
        with _lock:
            path.write_text(json.dumps(payload))

    d = pd.DataFrame(payload["daily"])
    d["time"] = pd.to_datetime(d["time"])
    return d.rename(columns={"temperature_2m_min": "tmin",
                             "temperature_2m_max": "tmax",
                             "precipitation_sum": "precip",
                             "et0_fao_evapotranspiration": "et0"})


def yearly_metrics(d: pd.DataFrame) -> pd.DataFrame:
    d = d.copy()
    d["year"] = d["time"].dt.year
    d["month"] = d["time"].dt.month
    d["gdd"] = np.clip((d["tmin"] + d["tmax"]) / 2 - GDD_BASE, 0, None)
    d["hot"] = (d["tmax"] > HEAT_THRESHOLD).astype(int)

    season = d[d["month"].isin(SEASON)]
    out = pd.DataFrame({
        "gdd": season.groupby("year")["gdd"].sum(),
        "heat_days": season.groupby("year")["hot"].sum(),
        "et0": season.groupby("year")["et0"].sum(),
        "precip": d.groupby("year")["precip"].sum(),      # water year approximated by calendar
    }).reset_index()
    return out


def theil_sen(x: np.ndarray, y: np.ndarray) -> float:
    slopes = []
    for i in range(len(x) - 1):
        dx, dy = x[i + 1:] - x[i], y[i + 1:] - y[i]
        ok = dx != 0
        slopes.extend((dy[ok] / dx[ok]).tolist())
    return float(np.median(slopes)) if slopes else 0.0


def summarise(y: pd.DataFrame) -> dict:
    """Trend, plus first-decade against last-decade, which is what people can read."""
    yrs = y["year"].to_numpy(float)
    out = {"years": [int(v) for v in y["year"]]}
    for col in ("gdd", "heat_days", "precip", "et0"):
        vals = pd.to_numeric(y[col], errors="coerce").to_numpy(float)
        early = float(np.nanmean(vals[:10]))
        late = float(np.nanmean(vals[-10:]))
        out[col] = {
            "series": [None if np.isnan(v) else round(float(v), 1) for v in vals],
            "early": round(early, 1), "late": round(late, 1),
            "change_pct": round((late - early) / early * 100, 1) if early else None,
            "slope": round(theil_sen(yrs, vals), 3),
        }
    return out


def main() -> None:
    cells = gpd.read_file(SITE / "areas.geojson").to_crs("EPSG:4326")
    pts = cells.geometry.representative_point()
    cell_pt = {int(c): snap(float(p.y), float(p.x))
               for c, p in zip(cells["cell_id"], pts)}
    unique = sorted(set(cell_pt.values()))
    print(f"{len(cells)} grid cells collapse to {len(unique)} climate points "
          f"at {CLIMATE_GRID} deg ({START[:4]}-{END[:4]} daily)")
    jobs = [(pt, pt[0], pt[1]) for pt in unique]

    results, errors = {}, []
    bar = tqdm(total=len(jobs), desc="climate")

    def work(job):
        pt, lat, lon = job
        d = fetch_daily(lat, lon)
        return pt, summarise(yearly_metrics(d))

    # Modest concurrency — this is a free public API, not something to hammer.
    by_point = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(work, j): j[0] for j in jobs}
        for fut in as_completed(futures):
            try:
                pt, summary = fut.result()
                by_point[pt] = summary
            except Exception as exc:                      # noqa: BLE001
                errors.append((futures[fut], str(exc)[:90]))
            bar.update()
    bar.close()

    # Fan the point results back out to every grid cell that shares that pixel.
    for cell_id, pt in cell_pt.items():
        if pt in by_point:
            results[str(cell_id)] = by_point[pt]

    for cid, msg in errors[:5]:
        print(f"  cell {cid}: {msg}")
    if errors:
        print(f"  {len(errors)} cells failed")

    (SITE / "climate.json").write_text(json.dumps(results, separators=(",", ":")))
    size = (SITE / "climate.json").stat().st_size / 1024
    print(f"\nwrote climate.json  {size:.0f} KB  for {len(results)} cells")

    # Headline numbers, so the run reports something meaningful rather than just a size.
    if results:
        heat = [(v["heat_days"]["early"], v["heat_days"]["late"]) for v in results.values()]
        gdd = [(v["gdd"]["early"], v["gdd"]["late"]) for v in results.values()]
        print(f"  days above {HEAT_THRESHOLD:.0f}C : "
              f"{np.mean([e for e, _ in heat]):.1f} -> {np.mean([l for _, l in heat]):.1f}")
        print(f"  growing degree days: "
              f"{np.mean([e for e, _ in gdd]):.0f} -> {np.mean([l for _, l in gdd]):.0f}")


if __name__ == "__main__":
    main()
