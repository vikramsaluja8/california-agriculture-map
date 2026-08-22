# California Agriculture Map

**Farms on the Move: Using Satellite Imagery to Map Shifting Agriculture in a Changing Climate**

Vikram Saluja · Menlo School Class of 2027 · Social Entrepreneurship in Action Grant 2026
Built with Planet Labs' Insights Platform, in partnership with Community Alliance with
Family Farmers (CAFF).

An interactive map of how California farmland is changing — where crops are under
stress, what growers are switching to, and what else could grow in a given district.
Built for farmers to use, not for researchers to read.

🗺️ **[Open the map](https://vikramsaluja8.github.io/california-agriculture-map/)**
*(replace with your URL once GitHub Pages is enabled)*

---

## What it shows

**3,727 fields · 15 counties · 4 study regions · 2017–2025**

| Region | Counties |
|---|---|
| Tulare Lake Basin | Fresno, Kings, Tulare, Kern |
| San Joaquin Valley | Madera, Merced, Stanislaus, San Joaquin |
| Sacramento Valley | Butte, Colusa, Glenn, Sutter, Yolo |
| Napa / Sonoma wine country | 3 clustered districts |

Three satellite indices, each measuring something different:

- **NDVI** — canopy vigour, how much healthy green growth
- **NDMI** — water *inside* the plants; the crop water-stress signal
- **NDWI** — standing water on the ground; flooding and flood irrigation

Plus, for every field: a nine-year trend, a rank against comparable fields nearby, its
worst year, true-colour satellite imagery you can scrub through by year, and a list of
crops proven to grow in its district.

## Findings

**Mature pistachios are holding moisture where mature almonds are losing it.**
Age-controlled, across seven counties and two hydrologic basins, almond canopy moisture
trends negative in every county and pistachio moisture trends positive in every county.
This agrees with what growers have independently been doing — 11,302 acres moved into
pistachios in Fresno alone.

**The 2022 rice fallowing is visible from orbit, and it traces a water-rights boundary.**
Sacramento Valley rice NDVI fell 72% in Colusa and 71% in Glenn against their own
normals, 40% in Yolo — and 1% in Butte, which draws on a different river system with
more senior rights. Full recovery by 2023.

**Wine country's problem is not visible to this instrument.** Napa and Sonoma vineyards
are stable to slightly improving on every index. The surplus growers describe is a
market condition, not an agronomic one, and the map says so rather than implying
otherwise.

Full detail, including what is *not* defensible and why, is in
**[FINDINGS.md](FINDINGS.md)**.

## How it works

```
steps/00_check_access.py      verify credentials, measure cost
steps/01_download_crop_maps.py DWR crop maps (free, public)
steps/02_crop_transitions.py   what became what, 2016 → 2023
steps/03_cohort_analysis.py    switchers vs stayers
steps/03b_age_matched.py       same crop, same age, different history
steps/03c_condition_sample.py  for regions where cohorts do not apply
steps/04 / 07_build_*_map.py   bake the analysis into static files
steps/06_image_chips.py        pre-render satellite imagery
steps/08_diversification.py    what else grows in this district
steps/05_standalone.py         single-file offline build
```

**The published map never calls Planet.** Everything is precomputed into static files,
so no credential ever reaches a browser, page views cost nothing, and the site keeps
working after the grant period ends.

## Reproducing this

```bash
python -m venv .venv && .venv/bin/pip install -e .
cp .env.example .env      # then add your Planet credentials
.venv/bin/python steps/00_check_access.py
```

Then run the steps in order. Planet responses are cached in `data/cache/`, so reruns
cost nothing.

## Data sources

- **Satellite** — Sentinel-2 L2A via [Planet Insights Platform](https://insights.planet.com) (Statistical API)
- **Field boundaries and crop types** — [California DWR Statewide Crop Mapping](https://data.cnra.ca.gov/dataset/statewide-crop-mapping), produced by Land IQ. Public, free, citable.
- **Basemaps** — CARTO and Esri

## Limitations

Please read these before acting on anything here.

- These are **vegetation indices, not yield**. They correlate; they are not the same.
- **Index levels are not comparable between crops.** A trellised vineyard reads lower
  than an almond canopy whatever its health. Compare trends, not levels.
- Results come from a **stratified sample**, not a census. Your field is probably not
  in it.
- Crop acreage trends **conflate climate, water, market and policy**. They cannot be
  separated with this data.
- "This crop grows here" is not "this crop will pay." Water rights, contracts,
  processing access and prices are outside this dataset entirely.

Intended to start a conversation with local expertise, not to replace it.
