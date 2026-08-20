"""Talking to Planet's Statistical API, safely and only once per field.

Three things this module exists to get right, all of which we learned the hard way:

1. **Geometry must go out in metres.** `resx`/`resy` are interpreted in the units of
   the request CRS. Send CRS84 and `resx: 10` means ten *degrees* — the field collapses
   to one pixel and the API returns confident nonsense. Everything here is reprojected
   to the local UTM zone first, and every response is checked against the pixel count
   we expect from the field's acreage.

2. **Never pay twice.** Every response is cached to disk by field and time range, so
   reruns cost nothing and a crash resumes where it stopped.

3. **Know what it cost.** Planet reports processing units spent per request; we add
   them up and report the total rather than guessing.
"""

from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import sha1
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv
from shapely.geometry import mapping

from .indices import INDEX_NAMES, TRIPLE_INDEX

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "cache"
CACHE.mkdir(parents=True, exist_ok=True)

load_dotenv(ROOT / ".env")

# Verified: this account lives on the EU deployment. US-West 503s. Tried in order.
DEPLOYMENTS = (
    "https://services.sentinel-hub.com",
    "https://services-uswest2.sentinel-hub.com",
)

# One 10 m pixel is 100 m²; one acre is 4046.86 m².
PIXELS_PER_ACRE_10M = 4046.86 / 100.0


def _as_float(value) -> float | None:
    """Coerce a Statistical API number to float, or None if it is not a real value.

    JSON has no NaN literal, so Sentinel Hub serialises missing statistics as the
    STRING "NaN". Left alone that poisons the whole column to object dtype, which
    then breaks aggregation and parquet writes a long way from the cause.
    """
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if not np.isfinite(out) else out


class PlanetStats:
    """A thin, cached client for the Statistical API."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._token: str | None = None
        self._token_expires: float = 0.0
        self.base: str | None = None
        self.pu_spent: float = 0.0
        self.requests_made: int = 0
        self.cache_hits: int = 0

    # ---------------------------------------------------------------- auth
    def token(self) -> str:
        if self._token and time.time() < self._token_expires - 60:
            return self._token

        cid = os.getenv("SH_CLIENT_ID", "").strip()
        sec = os.getenv("SH_CLIENT_SECRET", "").strip()
        if not (cid and sec):
            raise RuntimeError(
                "SH_CLIENT_ID / SH_CLIENT_SECRET missing from .env. Create an OAuth "
                "client at insights.planet.com -> Account -> Overview -> OAuth Clients."
            )

        errors = []
        for base in DEPLOYMENTS:
            url = f"{base}/auth/realms/main/protocol/openid-connect/token"
            try:
                r = requests.post(
                    url,
                    data={"grant_type": "client_credentials",
                          "client_id": cid, "client_secret": sec},
                    timeout=30,
                )
            except requests.RequestException as exc:
                errors.append(f"{base}: {exc}")
                continue
            if r.status_code == 200:
                payload = r.json()
                self._token = payload["access_token"]
                self._token_expires = time.time() + payload["expires_in"]
                self.base = base
                return self._token
            errors.append(f"{base}: {r.status_code} {r.text[:120]}")

        raise RuntimeError("No deployment accepted the credentials:\n  " + "\n  ".join(errors))

    # ------------------------------------------------------------ geometry
    @staticmethod
    def utm_epsg(lon: float) -> int:
        """California spans UTM zones 10N and 11N."""
        return 32610 if lon < -120 else 32611

    # ------------------------------------------------------------- fetching
    def _cache_path(self, field_id: str, start: str, end: str, interval: str) -> Path:
        key = sha1(f"{field_id}|{start}|{end}|{interval}|v2-triple".encode()).hexdigest()[:16]
        return CACHE / f"{key}.json"

    def field_series(
        self,
        field_id: str,
        geometry,
        start: str,
        end: str,
        interval: str = "P1M",
        resolution: float = 10.0,
        acres: float | None = None,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """Monthly NDVI/NDMI/NDWI for one field, as tidy rows."""
        cache_file = self._cache_path(field_id, start, end, interval)
        if use_cache and cache_file.exists():
            with self._lock:
                self.cache_hits += 1
            return self._parse(json.loads(cache_file.read_text()), field_id, acres, resolution)

        lon = geometry.centroid.x
        epsg = self.utm_epsg(lon)
        projected = gpd.GeoSeries([geometry], crs="EPSG:4326").to_crs(f"EPSG:{epsg}").iloc[0]

        body = {
            "input": {
                "bounds": {
                    "geometry": mapping(projected),
                    "properties": {"crs": f"http://www.opengis.net/def/crs/EPSG/0/{epsg}"},
                },
                "data": [{"type": "sentinel-2-l2a"}],
            },
            "aggregation": {
                "timeRange": {"from": f"{start}T00:00:00Z", "to": f"{end}T23:59:59Z"},
                "aggregationInterval": {"of": interval},
                "evalscript": TRIPLE_INDEX,
                "resx": resolution,
                "resy": resolution,
            },
        }

        payload = self._post(body)
        cache_file.write_text(json.dumps(payload))
        return self._parse(payload, field_id, acres, resolution)

    def _post(self, body: dict, attempts: int = 4) -> dict:
        url = f"{self.token() and self.base}/api/v1/statistics"
        for attempt in range(attempts):
            r = requests.post(
                url,
                headers={"Authorization": f"Bearer {self.token()}",
                         "Content-Type": "application/json"},
                json=body,
                timeout=300,
            )
            if r.status_code == 200:
                with self._lock:
                    self.requests_made += 1
                    spent = r.headers.get("x-processingunits-spent")
                    if spent:
                        self.pu_spent += float(spent)
                return r.json()
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"Statistical API {r.status_code}: {r.text[:400]}")
        raise RuntimeError(f"Statistical API still failing after {attempts} attempts")

    # -------------------------------------------------------------- parsing
    @staticmethod
    def _parse(payload: dict, field_id: str, acres: float | None,
               resolution: float) -> pd.DataFrame:
        rows = []
        for interval in payload.get("data", []):
            bands = interval.get("outputs", {})
            date = interval["interval"]["from"][:10]
            row = {"field_id": field_id, "date": date}
            usable = False
            for name in INDEX_NAMES:
                stats = bands.get(name, {}).get("bands", {}).get("B0", {}).get("stats", {})
                value = _as_float(stats.get("mean")) if stats else None
                if value is None or stats.get("sampleCount", 0) == 0:
                    row[name] = np.nan
                    continue
                row[name] = value
                row["pixels"] = stats.get("sampleCount", 0) - stats.get("noDataCount", 0)
                usable = True
            if usable:
                rows.append(row)

        df = pd.DataFrame(rows)
        if df.empty:
            return df
        df["date"] = pd.to_datetime(df["date"])

        # Sanity check the geometry actually resolved. A field measured at one pixel
        # means the request went out in degrees — the failure mode that returns
        # plausible-looking numbers, so it must be caught here, not by eye.
        if acres and resolution == 10.0 and "pixels" in df:
            expected = acres * PIXELS_PER_ACRE_10M
            got = df["pixels"].max()
            if got < expected * 0.25:
                raise RuntimeError(
                    f"{field_id}: got {got:.0f} pixels for a {acres:.0f}-acre field, "
                    f"expected ~{expected:.0f}. Geometry is probably not in metres."
                )
        return df


    def fetch_many(self, jobs, workers: int = 8, on_result=None):
        """Fetch many field series concurrently.

        A nine-year monthly request is ~30 seconds of server-side work, so fetching
        serially caps out around 2 fields/minute — fine for a trial, hopeless for a
        study of thousands. These requests are almost entirely wait, so threads are
        the right tool. Rate-limit responses (429) are already handled with backoff
        in _post, so oversubscribing degrades gracefully rather than failing.

        `jobs` is an iterable of dicts accepted by field_series(). Returns
        (list_of_dataframes, list_of_(field_id, error)).
        """
        self.token()          # warm the token once, before threads race for it
        frames, errors = [], []

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self.field_series, **job): job["field_id"]
                       for job in jobs}
            for future in as_completed(futures):
                field_id = futures[future]
                try:
                    df = future.result()
                except Exception as exc:            # noqa: BLE001 - report, don't abort
                    errors.append((field_id, str(exc)))
                    continue
                if df is not None and not df.empty:
                    frames.append((field_id, df))
                if on_result:
                    on_result()
        return frames, errors

    def summary(self) -> str:
        return (f"{self.requests_made} requests · {self.cache_hits} cached · "
                f"{self.pu_spent:.1f} PU spent")
