"""STEP 0 — Prove the credentials work, end to end, before spending anything.

Runs four checks in order, each one a prerequisite for the next:

  1. Are both credentials present in .env?          (prints lengths only, never values)
  2. Does the OAuth client exchange for a token?    (Insights / Sentinel Hub auth)
  3. Which collections can we actually see?         (Catalog API)
  4. Does a real NDVI request return real numbers?  (Statistical API, one real field)

Check 4 is the one that matters. It takes an actual almond field out of the DWR crop
map and asks Planet for a year of monthly NDVI over it. If that prints a plausible
seasonal curve, every assumption in the project is validated and we can scale up.

    python steps/00_check_access.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import geopandas as gpd
import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

# Planet's Insights deployment is US-West, but EU accounts exist too. Try both rather
# than assume — a wrong base URL looks exactly like a bad credential.
DEPLOYMENTS = {
    "us-west (Oregon)": "https://services-uswest2.sentinel-hub.com",
    "eu (Frankfurt)": "https://services.sentinel-hub.com",
}

FRESNO_2023 = ROOT / "data" / "raw" / "dwr_2023" / "i15_Crop_Mapping_2023_Final.gdb"

# Monthly maximum NDVI, cloud-masked via the Sentinel-2 scene classification layer.
# `dataMask` is mandatory — it tells the Statistical API which pixels to count.
EVALSCRIPT = """//VERSION=3
function setup() {
  return {
    input: [{ bands: ["B04", "B08", "SCL", "dataMask"] }],
    output: [
      { id: "ndvi", bands: 1, sampleType: "FLOAT32" },
      { id: "dataMask", bands: 1 }
    ],
    mosaicking: "ORBIT"
  };
}
function isClear(scl) { return scl === 4 || scl === 5 || scl === 7; }
function evaluatePixel(samples) {
  var best = -1.0, valid = 0;
  for (var i = 0; i < samples.length; i++) {
    var s = samples[i];
    if (s.dataMask !== 1 || !isClear(s.SCL)) continue;
    var d = s.B08 + s.B04;
    if (d <= 0) continue;
    var v = (s.B08 - s.B04) / d;
    if (v > best) best = v;
    valid = 1;
  }
  return { ndvi: [valid ? best : 0], dataMask: [valid] };
}
"""


def check_env() -> tuple[str, str]:
    print("1. credentials in .env")
    print("   " + "-" * 56)
    cid = os.getenv("SH_CLIENT_ID", "").strip()
    sec = os.getenv("SH_CLIENT_SECRET", "").strip()
    key = os.getenv("PL_API_KEY", "").strip()
    for label, val in (("SH_CLIENT_ID", cid), ("SH_CLIENT_SECRET", sec), ("PL_API_KEY", key)):
        state = f"set ({len(val)} chars)" if val else "EMPTY"
        print(f"   {label:<20} {state}")
    if not (cid and sec):
        print("\n   OAuth client missing. insights.planet.com -> Account -> Overview")
        print("   -> OAuth Clients -> Create New")
        sys.exit(1)
    return cid, sec


def get_token(cid: str, sec: str) -> tuple[str, str]:
    print("\n2. OAuth token exchange")
    print("   " + "-" * 56)
    for label, base in DEPLOYMENTS.items():
        url = f"{base}/auth/realms/main/protocol/openid-connect/token"
        try:
            r = requests.post(
                url,
                data={"grant_type": "client_credentials", "client_id": cid,
                      "client_secret": sec},
                timeout=30,
            )
        except requests.RequestException as exc:
            print(f"   {label:<20} network error: {exc}")
            continue
        if r.status_code == 200:
            payload = r.json()
            print(f"   {label:<20} OK — token valid {payload['expires_in']}s")
            return payload["access_token"], base
        print(f"   {label:<20} {r.status_code} {r.text[:90]}")

    print("\n   No deployment accepted these credentials.")
    sys.exit(1)


def check_catalog(token: str, base: str) -> None:
    print("\n3. collections visible to this account")
    print("   " + "-" * 56)
    r = requests.get(
        f"{base}/api/v1/catalog/1.0.0/collections",
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    if r.status_code != 200:
        print(f"   catalog returned {r.status_code}: {r.text[:200]}")
        return
    for c in r.json().get("collections", []):
        print(f"   - {c.get('id')}")


def sample_almond_field() -> tuple[dict, float]:
    """Pull one real almond field out of the DWR map to test against."""
    # Two pyogrio traps, both of which fail SILENTLY by returning zero rows:
    #   1. `columns=` is applied BEFORE `where`, so every column named in the filter
    #      must also be listed in `columns` — COUNTY here.
    #   2. Numeric comparisons in the OGR SQL `where` match nothing against this
    #      geodatabase, so acreage is filtered in pandas afterwards.
    gdf = gpd.read_file(
        FRESNO_2023,
        where="COUNTY = 'Fresno' AND CROPTYP2 = 'D12'",
        columns=["ACRES", "CROPTYP2", "COUNTY"],
    ).to_crs("EPSG:4326")
    sized = gdf[(gdf["ACRES"] > 60) & (gdf["ACRES"] < 120)]
    if sized.empty:
        raise RuntimeError(f"no almond field in the 60–120 ac range ({len(gdf)} total)")
    field = sized.iloc[0]

    # Reproject into the local UTM zone so the request's resx/resy are in metres.
    # California spans zones 10N (EPSG:32610) and 11N (EPSG:32611).
    lon = field.geometry.centroid.x
    epsg = 32610 if lon < -120 else 32611
    projected = gpd.GeoSeries([field.geometry], crs="EPSG:4326").to_crs(f"EPSG:{epsg}")

    geometry = json.loads(projected.to_json())["features"][0]["geometry"]
    return geometry, float(field["ACRES"]), epsg


def check_statistics(token: str, base: str) -> None:
    print("\n4. Statistical API — one year of NDVI on a real almond field")
    print("   " + "-" * 56)
    geometry, acres, epsg = sample_almond_field()
    print(f"   test field: {acres:.0f} acres of almonds, Fresno County (EPSG:{epsg})")

    body = {
        "input": {
            "bounds": {
                "geometry": geometry,
                # resx/resy are expressed in the units of THIS crs. In CRS84 those
                # units are degrees, so resx=10 means 10 degrees and the whole field
                # collapses to one pixel — statistics still come back and still look
                # plausible, which is what makes the mistake dangerous. A metre-based
                # projection makes resx=10 mean what we intend.
                "properties": {"crs": f"http://www.opengis.net/def/crs/EPSG/0/{epsg}"},
            },
            "data": [{"type": "sentinel-2-l2a"}],
        },
        "aggregation": {
            "timeRange": {"from": "2023-01-01T00:00:00Z", "to": "2023-12-31T23:59:59Z"},
            "aggregationInterval": {"of": "P1M"},
            "evalscript": EVALSCRIPT,
            "resx": 10,
            "resy": 10,
        },
    }

    r = requests.post(
        f"{base}/api/v1/statistics",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body,
        timeout=180,
    )
    if r.status_code != 200:
        print(f"   FAILED {r.status_code}: {r.text[:600]}")
        sys.exit(1)

    # Sentinel Hub reports what each request cost. This is the number that decides how
    # many fields the project can afford — measure it, never estimate it.
    spent = r.headers.get("x-processingunits-spent")

    payload = r.json()
    print(f"\n   {'month':<10} {'NDVI':>7}  {'pixels':>7}")
    print("   " + "-" * 30)
    got = 0
    for interval in payload.get("data", []):
        stats = (interval.get("outputs", {}).get("ndvi", {})
                 .get("bands", {}).get("B0", {}).get("stats", {}))
        month = interval["interval"]["from"][:7]
        if not stats or stats.get("sampleCount", 0) == 0 or stats.get("mean") is None:
            print(f"   {month:<10} {'—':>7}  {'cloud':>7}")
            continue
        n = stats.get("sampleCount", 0) - stats.get("noDataCount", 0)
        print(f"   {month:<10} {stats['mean']:>7.3f}  {n:>7,}")
        got += 1

    print(f"\n   {got} of 12 months returned usable data")

    if spent:
        pu = float(spent)
        monthly_budget = 400_000
        per_field_9yr = pu * 9          # 9 seasons instead of the 1 tested here
        print(f"\n   cost: {pu:.3f} PU for 12 monthly intervals on one field")
        print(f"   → ~{per_field_9yr:.2f} PU per field for a full 2017–2025 series")
        print(f"   → ~{int(monthly_budget / per_field_9yr):,} fields/month "
              f"within a {monthly_budget:,} PU allowance")

    if got >= 8:
        print("\n   PASS — the full chain works. Ready to scale up.")
    else:
        print("\n   Thin coverage. Check the evalscript's cloud mask before scaling.")


def main() -> None:
    print("Planet access check")
    print("=" * 62 + "\n")
    cid, sec = check_env()
    token, base = get_token(cid, sec)
    check_catalog(token, base)
    check_statistics(token, base)


if __name__ == "__main__":
    main()
