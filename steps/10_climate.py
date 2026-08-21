"""STEP 10 — Climate outlook, and which district's climate each one is heading toward.

The grant asks which areas may BECOME suitable for cultivation. Observed trends cannot
answer that — they describe what already happened, and extrapolating a 36-year line is
not a projection. So this uses Cal-Adapt, California's downscaled climate service:
6 km LOCA data, a 32-model ensemble, 1950 to 2099, under two emissions scenarios.

Two design choices worth knowing about.

**District level, not per grid cell.** An earlier attempt queried climate for all 305
map cells at 36 years of daily data each. That is roughly fifteen times more data than
a question asked at region level needs, and it exhausted a free API's daily quota
without finishing. Climate varies smoothly; districts are the unit growers think in.

**Analogs instead of crop thresholds.** Published crop temperature requirements are
contested and vary by cultivar, rootstock and management. Rather than assert them, this
asks a question the data can answer on its own:

    Which district ALREADY has the climate this district is projected to have?

If Sonoma Valley in 2050 looks like somewhere that grows olives well today, that is a
concrete, checkable statement, and it joins directly onto the diversification layer.

    python steps/10_climate.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from tqdm import tqdm

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "docs" / "data"
CACHE = ROOT / "data" / "cache" / "caladapt"
CACHE.mkdir(parents=True, exist_ok=True)

API = "https://api.cal-adapt.org/api/series/{slug}/events/"
KELVIN = 273.15

# Annual means of daily maximum and minimum, 32-model ensemble average.
SERIES = {
    "tmax": ("tasmax_year_ens32avg_historical", "tasmax_year_ens32avg_rcp45",
             "tasmax_year_ens32avg_rcp85"),
    "tmin": ("tasmin_year_ens32avg_historical", "tasmin_year_ens32avg_rcp45",
             "tasmin_year_ens32avg_rcp85"),
    "precip": ("pr_year_ens32avg_historical", "pr_year_ens32avg_rcp45",
               "pr_year_ens32avg_rcp85"),
}

# Cal-Adapt's historical run ends in 2005 — that is how LOCA is constructed. For
# 2006-2025 the two scenarios have barely diverged, so RCP4.5 stands in for "recent".
BASELINE = (1961, 1990)
RECENT = (2006, 2025)
MID = (2040, 2060)
LATE = (2080, 2099)


def fetch(slug: str, lat: float, lon: float) -> pd.Series:
    key = f"{slug}_{lat:.3f}_{lon:.3f}".replace(".", "p")
    path = CACHE / f"{key}.json"
    if path.exists():
        payload = json.loads(path.read_text())
    else:
        for attempt in range(4):
            r = requests.get(API.format(slug=slug),
                             params={"g": f"POINT({lon} {lat})", "imperial": "false"},
                             timeout=90)
            if r.status_code == 200:
                payload = r.json()
                path.write_text(json.dumps(payload))
                break
            time.sleep(3 * (attempt + 1))
        else:
            raise RuntimeError(f"{slug}: no response after 4 attempts")

    years = pd.to_datetime(payload["index"]).year
    return pd.Series(np.asarray(payload["data"], dtype=float), index=years)


def window(s: pd.Series, lo: int, hi: int) -> float:
    sel = s.loc[(s.index >= lo) & (s.index <= hi)]
    return float(sel.mean()) if len(sel) else float("nan")


def profile(lat: float, lon: float) -> dict:
    """Climate now and projected, for one point. Temperatures in Celsius."""
    out = {}
    for var, (hist, r45, r85) in SERIES.items():
        h, a, b = (fetch(hist, lat, lon), fetch(r45, lat, lon), fetch(r85, lat, lon))
        # Cal-Adapt returns Kelvin for temperature even with imperial=false. Precip is
        # a rate, so it is not offset — converting it would be silently wrong.
        off = KELVIN if var in ("tmax", "tmin") else 0.0
        out[var] = {
            "baseline": round(window(h, *BASELINE) - off, 2),
            "recent": round(window(a, *RECENT) - off, 2),
            "mid_rcp45": round(window(a, *MID) - off, 2),
            "mid_rcp85": round(window(b, *MID) - off, 2),
            "late_rcp85": round(window(b, *LATE) - off, 2),
        }
    for var in ("tmax", "tmin"):
        v = out[var]
        v["warming_observed"] = round(v["recent"] - v["baseline"], 2)
        v["warming_mid_rcp45"] = round(v["mid_rcp45"] - v["recent"], 2)
        v["warming_mid_rcp85"] = round(v["mid_rcp85"] - v["recent"], 2)
        v["warming_late_rcp85"] = round(v["late_rcp85"] - v["recent"], 2)
    return out


def main() -> None:
    fields = gpd.read_file(SITE / "fields.geojson").to_crs("EPSG:4326")

    # One climate point per district where districts exist, otherwise per county.
    fields["place"] = fields["district"].where(
        fields["district"].notna(), fields["county"])
    pts = fields.geometry.representative_point()
    fields["lat"], fields["lon"] = pts.y, pts.x

    places = (fields.groupby(["place", "region"])[["lat", "lon"]]
              .mean().reset_index())
    print(f"{len(fields):,} fields group into {len(places)} climate points "
          f"(district where defined, county otherwise)")

    profiles = {}
    for row in tqdm(places.itertuples(), total=len(places), desc="Cal-Adapt"):
        try:
            profiles[row.place] = {"region": row.region, "lat": round(row.lat, 3),
                                   "lon": round(row.lon, 3), **profile(row.lat, row.lon)}
        except Exception as exc:                                   # noqa: BLE001
            tqdm.write(f"  {row.place}: {str(exc)[:90]}")

    # Climate analogs: which place ALREADY has the climate this place is heading for.
    now = {p: v["tmax"]["recent"] for p, v in profiles.items()}
    for place, prof in profiles.items():
        for scenario, key in (("mid_rcp45", "analog_2050_rcp45"),
                              ("mid_rcp85", "analog_2050_rcp85"),
                              ("late_rcp85", "analog_2080_rcp85")):
            target = prof["tmax"][scenario]
            # Nearest current climate among the OTHER places.
            best = min(((abs(v - target), p) for p, v in now.items() if p != place),
                       default=(None, None))
            if best[1] is not None and best[0] <= 1.5:      # only if genuinely close
                prof[key] = {"place": best[1], "gap": round(best[0], 2),
                             "their_today": now[best[1]]}
            else:
                # Nothing in the study area is that warm today — itself a finding.
                prof[key] = {"place": None, "warmer_than_any": True}

    (SITE / "climate.json").write_text(json.dumps(profiles, separators=(",", ":")))
    print(f"\nwrote climate.json  {(SITE/'climate.json').stat().st_size/1024:.0f} KB "
          f"for {len(profiles)} places")

    print(f"\n{'place':<34}{'now':>7}{'2050':>8}{'2080':>8}   analog for 2050 (RCP8.5)")
    print("-" * 96)
    for p, v in sorted(profiles.items(), key=lambda kv: -kv[1]["tmax"]["recent"]):
        a = v.get("analog_2050_rcp85", {})
        label = a.get("place") or "hotter than anywhere here today"
        print(f"{p[:33]:<34}{v['tmax']['recent']:>7.1f}{v['tmax']['mid_rcp85']:>8.1f}"
              f"{v['tmax']['late_rcp85']:>8.1f}   {label}")


if __name__ == "__main__":
    main()
