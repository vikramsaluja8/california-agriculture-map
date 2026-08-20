"""STEP 1 — Get California's crop maps and find out what's actually in them.

Why this is step one: everything in the "what to switch to" layer comes from this
one dataset. If we get the crop codes wrong here, every number downstream is wrong
and it will look plausible anyway. So we download two years, print the real schema,
and read the real codes before writing a line of analysis.

No Planet credentials needed. This is all public California data.

    python steps/01_download_crop_maps.py

Source: https://data.cnra.ca.gov/dataset/statewide-crop-mapping (Land IQ for DWR)
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import geopandas as gpd
import pyogrio
import requests

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
RAW.mkdir(parents=True, exist_ok=True)

CKAN = "https://data.cnra.ca.gov/api/3/action/package_show?id=statewide-crop-mapping"

# Two years, seven years apart. Far enough to see real transitions, and 2016 is the
# earliest year with the modern attribute schema.
YEARS = [2016, 2023]

COUNTY = "Fresno"


def resource_url(year: int) -> str:
    """Find the geodatabase download for a given year."""
    resources = requests.get(CKAN, timeout=60).json()["result"]["resources"]
    matches = [
        r for r in resources
        if str(year) in (r.get("name") or "")
        and "eodatabase" in (r.get("name") or "")
    ]
    if not matches:
        raise RuntimeError(f"no geodatabase resource listed for {year}")
    return matches[0]["url"]


def download(year: int) -> Path:
    """Download and unzip one year's geodatabase. Skips if already present."""
    out = RAW / f"dwr_{year}"
    if out.exists() and any(out.iterdir()):
        print(f"  {year}: already downloaded")
        return out

    url = resource_url(year)
    archive = RAW / f"dwr_{year}.zip"
    print(f"  {year}: downloading {url}")

    with requests.get(url, stream=True, timeout=1800) as resp:
        resp.raise_for_status()
        got = 0
        with archive.open("wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 22):
                fh.write(chunk)
                got += len(chunk)
                print(f"\r     {got/1e6:>6.0f} MB", end="", flush=True)
    print()

    out.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(out)
    archive.unlink()
    return out


def find_dataset(directory: Path) -> Path:
    """Locate the .gdb directory or .shp file inside an extracted download."""
    for pattern in ("*.gdb", "*.shp"):
        hits = sorted(directory.rglob(pattern))
        if hits:
            return hits[0]
    raise RuntimeError(f"no .gdb or .shp under {directory}")


def main() -> None:
    print("Downloading DWR crop maps")
    print("=" * 70)
    paths = {year: find_dataset(download(year)) for year in YEARS}

    for year, path in paths.items():
        print(f"\n\n{'=' * 70}")
        print(f"{year}  —  {path.name}")
        print("=" * 70)

        # Read the schema WITHOUT loading 100 MB of geometry into memory.
        info = pyogrio.read_info(path)
        print(f"features statewide : {info['features']:,}")
        print(f"CRS                : {info['crs']}")
        print(f"\ncolumns:")
        for name, dtype in zip(info["fields"], info["dtypes"]):
            print(f"  {name:<20} {dtype}")

        # Now load just one county so we can look at real values.
        county_col = next(
            (c for c in info["fields"] if c.upper() == "COUNTY"), None
        )
        if county_col is None:
            print("\n!! no COUNTY column — will need a spatial filter instead")
            continue

        gdf = gpd.read_file(path, where=f"{county_col} = '{COUNTY}'")
        print(f"\n{COUNTY} County: {len(gdf):,} fields")

        # Which column holds the crop code? Print candidates with sample values so
        # we can see the vocabulary rather than assume it.
        for col in info["fields"]:
            if col.upper().startswith(("CROPTYP", "CLASS", "SUBCLASS")):
                vals = gdf[col].dropna().astype(str)
                top = vals.value_counts().head(8)
                print(f"\n  {col}  ({vals.nunique()} distinct)")
                for v, n in top.items():
                    print(f"    {v:<12} {n:>8,}")


if __name__ == "__main__":
    main()
