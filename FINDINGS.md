# Verified facts

Things we have actually checked, as opposed to assumed. Every line here was confirmed
by running something. Last updated 2026-08-17.

Keep this file honest — when an assumption gets tested, move it up here with the
evidence, and when something here turns out wrong, correct it rather than adding a
contradicting line further down.

## Your Planet account

| Fact | Value | How we know |
|---|---|---|
| Plan | Planet Insights Platform — Enterprise Small | account UI |
| Valid until | 30 December 2026 | account UI |
| Processing units | 400,000 / month | account UI |
| Statistics API | ✅ included | listed in plan, and used successfully |
| Catalog / Process / OGC / Batch | ✅ included | listed in plan |
| **Deployment** | **EU (Frankfurt)** | US-West returns 503; Frankfurt issues tokens |
| Collections visible | `sentinel-2-l2a`, `sentinel-2-l1c`, `sentinel-1-grd` | Catalog API |
| PlanetScope via Statistical API | ❌ **not visible** | absent from the Catalog API listing |
| PlanetScope Area Under Management | 996 km² | account UI |
| PlanetScope archive (Data API) | 2014 → 2025 | `/data/v1/stats`, Fresno test box |
| RapidEye archive (Data API) | 2009 → 2020 | same |
| Basemaps / Mosaics | ❌ no entitlement | API returns empty list |
| PlanetScope Tropical Basemaps | entitled but **useless here** | tropics only; California is 32–42°N |

### The open question for Planet

PlanetScope does not appear in the Statistical API catalog, but you hold 996 km² of
PlanetScope Area Under Management. Those two facts need reconciling. Ask:

> "I have 996 km² of PlanetScope Area Under Management, but the Catalog API only
> returns the Sentinel collections. How do I make PlanetScope available to the
> Statistical API — does it need a subscription or a BYOC collection set up first?"

Until that is answered, treat PlanetScope as unavailable for per-field statistics and
build everything on Sentinel-2.

## Measured cost

One 75-acre field, 12 monthly NDVI intervals, Sentinel-2 L2A at 10 m:

```
1.652 PU
```

Which scales to roughly:

- **~15 PU** per field for a full 2017–2025 monthly series
- **~27,000 fields/month** within the 400,000 PU allowance

Processing units are not a meaningful constraint for Sentinel-2 work. A 5,000-field
study across all four regions costs about 75,000 PU — under a fifth of one month.

## Traps found the hard way

**pyogrio applies `columns=` before `where=`.** A column used in the filter but missing
from `columns` returns **zero rows with no error**. Always include filter columns.

**Numeric comparisons in the OGR SQL `where` match nothing** against these
geodatabases. `ACRES > 60` silently returns empty. Filter numerics in pandas instead.

**`resx`/`resy` are in the units of the request's CRS.** With CRS84 bounds, `resx: 10`
means ten *degrees*, and a whole field collapses to one pixel. The API still returns
plausible-looking statistics — our test field reported a July NDVI of 0.89 from one
pixel versus 0.58 for the true field average. Always send geometry in a metre-based
projection (UTM 10N/11N for California) and sanity-check the pixel count: a 75-acre
field at 10 m should be about 3,000 pixels.

**DWR crop codes are not guessable.** Verified against the data:

| Code | Crop | Note |
|---|---|---|
| `D12` | Almonds | |
| `D13` | **Walnuts** | I originally guessed pistachios — wrong |
| `D14` | Pistachios | |
| `V` | Grapes | |
| `T8` | Lettuce / leafy greens | I originally guessed `T9` — wrong |
| `T9` | Melons, squash, cucumbers | |
| `P1` | Alfalfa | |
| `X`, `I1`, `I4` | Idle | not a crop |
| `YP` | Young Perennials | not a crop — a newly planted, unidentifiable orchard |

**`Crop2016` gives readable names; 2023 does not.** The 2023 `MAIN_CROP` column just
repeats the code, so we learn the vocabulary from the 2016 vintage and apply it to both.

## Study regions

DWR's 2023 file has a `HYDRO_RGN` column matching your paper's four regions directly —
no hand-built county mapping needed:

```
Sacramento River · San Joaquin River · Tulare Lake · Central Coast
```

Fresno County straddles **San Joaquin River** and **Tulare Lake**, so county-level
grouping would misplace it. Use `HYDRO_RGN`.

Note: DWR's other `Region` column is their *administrative office* region (all of
Fresno is `SCRO`) and is not what you want.

## First real result — Fresno County, 2016 → 2023

36,952 fields matched (97%). California open data only, no Planet access needed.

| Crop | Still same | Changed | Net |
|---|---|---|---|
| Grapes | 68% | 32% | **−52,211 ac** |
| Almonds | 82% | 18% | **+71,987 ac** |

The largest single conversion in the county was **grapes → almonds, 31,434 ac**.
Secondary signals: almonds → pistachios (5,861 ac), grapes → pistachios (5,441 ac),
almonds → idle (12,359 ac).

### Caveats that must travel with these numbers

1. **`Young Perennials → Almonds` (23,878 ac) is largely an artefact.** YP means a
   newly planted orchard too immature to classify; much of it was already almonds.
   The true conversion-into-almonds figure is well below the headline.
2. **Economics is doing much of this work.** The Fresno raisin market has contracted
   for years. These transitions are not purely climate-driven, and the report must
   say so plainly.
3. **Point-in-polygon matching approximates acreage** where field boundaries were
   redrawn between vintages. Fine for "what are people planting instead"; redo as a
   polygon intersection if the paper needs exact acreage.

## Cohort analysis — Fresno, 240 fields, 2017–2025

Measured cost: **6,046 PU** for 162 new field-series (78 were already cached). That is
~37 PU per field for a nine-year, three-index series — higher than the 15 PU estimated
from the one-year test, because a nine-year request does proportionally more work.
Still only 1.5% of a monthly allowance.

### Trends by cohort (NDVI slope per year, and share statistically declining)

| Cohort | planted | NDVI slope | NDVI declining | NDMI slope | NDMI declining |
|---|---|---|---|---|---|
| almonds → almonds | ~2008 | +0.0016 | 8% | −0.0040 | 25% |
| almonds → pistachios | ~2019 | +0.0120 | 8% | +0.0036 | 20% |
| almonds → idle | n/a | **−0.0542** | 38% | **−0.0501** | **82%** |
| grapes → grapes | n/a | −0.0113 | 25% | −0.0065 | 40% |
| grapes → almonds | ~2020 | +0.0351 | 2% | +0.0217 | 0% |
| grapes → pistachios | ~2021 | −0.0206 | 10% | −0.0157 | 15% |

### What can and cannot be concluded

**Defensible now:**

- *Mature almonds in Fresno are stable, not declining.* The almonds→almonds cohort
  (median planting 2008) is essentially flat, with only 8% declining. This contradicts
  a simple "climate is killing the almonds" narrative.
- *Land going idle shows a strong water-stress signature.* 82% of almonds→idle fields
  show statistically clear falling NDMI, against 25% for almonds that stayed. NDVI
  falls and NDWI rises, consistent with canopy removal exposing bare soil. This is the
  clearest climate/groundwater signal in the data.
- *Vineyards that stayed vineyards are drifting down* — 25% declining NDVI, 40%
  declining NDMI.

**NOT yet defensible — the tree-age confounder:**

Every switched cohort was replanted, so its trees are young: grapes→almonds median
planting 2020, almonds→pistachios 2019, grapes→pistachios 2021. Their rising NDVI is
substantially **canopy establishment, not improving conditions**. A newly planted
orchard's NDVI rises for 5–7 years regardless of climate.

So the headline "grapes→almonds is improving 3× faster than staying in grapes" is
**confounded and must not be published as an adaptation recommendation.**

### The fix — step 3b

Compare each switched field against an **age-matched control**: almonds planted ~2020
that were already almonds in 2016, versus almonds planted ~2020 that replaced grapes.
Same crop, same age, different history. `YR_PLANTED` is populated for every almond and
pistachio field, so this is buildable with data already downloaded.

## Performance

Serial fetching ran at **2 fields/minute** — a nine-year monthly request is ~30s of
server-side work. `PlanetStats.fetch_many()` runs 8 concurrent requests and reaches
**~17 fields/minute**. At the serial rate a 5,000-field four-region study would take
~42 hours; concurrent it is about 5.

Watch for accidental duplicate runs — two concurrent copies of the same job compete
for rate limit and double the API load without finishing any sooner.

## Step 3b — age-matched comparison (the confounder removed)

Cohorts hold crop AND planting-year constant and vary only the prior land use, so
canopy establishment cancels out between the two sides of each comparison.

| Cohort | n | median planted |
|---|---|---|
| almond '18–22, was almond | 40 | 2019 |
| almond '18–22, was grape | 40 | 2020 |
| pistachio '18–22, was almond | 39 | 2019 |
| pistachio '18–22, was grape | 39 | 2021 |
| almond mature (pre-2012) | 40 | 2006 |
| pistachio mature (pre-2012) | 40 | 2008 |

### Result 1 — the earlier headline was indeed the confounder

Both young almond cohorts rise at almost the same rate regardless of what preceded
them (NDVI slope +0.0299 after almonds, +0.0237 after grapes). That near-identical
climb is canopy establishment. Step 3's "grapes→almonds improving 3× faster than
staying in grapes" was measuring tree growth, and is now retired.

Prior land use has a small effect, and it slightly favours replanting the same crop:
almonds following almonds sit +0.056 NDVI and +0.036 NDMI above almonds following
grapes at the same age.

### Result 2 — the real finding, and it is age-controlled

Mature almonds versus mature pistachios, both planted pre-2012, same county, same years:

| | NDVI recent | NDVI slope | NDMI recent | NDMI slope |
|---|---|---|---|---|
| almond mature | 0.698 | **−0.0020** | 0.153 | **−0.0087** |
| pistachio mature | 0.708 | **+0.0197** | 0.209 | **+0.0110** |

At equivalent maturity the two crops carry near-identical canopy density, but
**pistachios hold materially more moisture (+0.056 NDMI) and are trending up on both
indices while almonds trend flat-to-down.** This is the only legitimate crop-vs-crop
comparison in the project — both cohorts mature, so no establishment artefact — and it
is the first result that genuinely supports an adaptation recommendation.

It also agrees with the independent transition evidence: growers moved 5,861 acres of
almonds and 5,441 acres of grapes into pistachios over the same period. Satellite
measurement and revealed grower preference point the same way.

### Caveats

- n=40 per cohort, one county. Needs replication in Tulare Lake and Sacramento Valley
  before it can be published as advice.
- Pistachios are often sited on more saline or marginal ground than almonds. That
  would bias *against* pistachios, so it likely understates rather than inflates the
  effect — but it should be checked against soil data.
- Still vegetation indices, not yield.

## Map views

Two views, switchable:

- **Individual farms** — every sampled field, dots at low zoom, real parcel boundaries
  from zoom 12.
- **Whole area** — a 12 km grid; each cell shows the median of the fields inside it
  (minimum 2). This is the landscape read, and the same code will serve four regions
  as it serves one county.

Colour ramp is **brown → tan → green**, not red → green. Brown is what the low end
physically is (bare soil, removed canopy); red asserts an alarm the data does not
always earn. NDWI keeps a neutral dry→wet ramp because standing water is information,
not a verdict.

## Replication — Tulare County (3,948 PU)

The Fresno pistachio result was n=40 in one county. Rerun unchanged in Tulare, the
mature crop comparison is the headline:

| | Fresno NDVI slope | Tulare NDVI slope | Fresno NDMI slope | Tulare NDMI slope |
|---|---|---|---|---|
| almond mature | −0.0020 | +0.0024 | **−0.0087** | **−0.0042** |
| pistachio mature | **+0.0197** | **+0.0209** | **+0.0110** | **+0.0128** |

**It replicates, and closely.** In both counties, independently: mature almonds are
flat on vigour and *drying*, while mature pistachios are rising on both vigour and
moisture. The four trend figures line up to within a few thousandths.

Note the index *levels* do not match across counties — Fresno mature pistachios read
above almonds on NDVI, Tulare mature almonds read above pistachios. That is exactly
why this project compares trends and not levels. The direction of travel replicates;
the absolute number does not, and was never supposed to.

### A clean null result

Prior land use has no consistent effect. In Fresno, almonds following almonds beat
almonds following grapes by +0.056 NDVI; in Tulare the sign flips (−0.016). Across two
counties the effect is small and inconsistent, so **what the ground grew before does
not appear to matter much** — the crop you choose does.

### One oddity worth chasing

Tulare's young pistachio cohorts are *declining* (NDVI −0.030 and −0.042), unlike
Fresno's. Small samples (n=11, n=15), and pistachios take 7–10 years to come into
bearing, so a 2019–2021 planting may still be filling in. Worth revisiting with a
larger sample before anyone reads anything into it.

### Where this leaves goal 3

Two counties, age-controlled, agreeing with each other and with what growers actually
did (11,302 acres moved into pistachios over the same period). That is enough to state
carefully in the report:

> In the southern San Joaquin Valley, mature pistachio blocks have held or gained
> canopy moisture over 2017–2025 while mature almond blocks of the same age have lost
> it. This is consistent across Fresno and Tulare counties and matches the direction
> growers have already been moving.

Still not yield, still not advice, still one basin.

## Map features added

- **Index acronyms** on every control — buttons read "Vigour / NDVI", and the help line
  leads with the acronym, so the plain-language name and the technical one travel
  together.
- **Basemaps** switched to Carto Positron (default), Carto Voyager, and Esri imagery
  with a label overlay. Positron is desaturated on purpose so the brown-to-green data
  is the loudest thing on the page.
- **Satellite image chips** — 162 true-colour JPEGs, 18 fields x 9 years, peak season
  (15 Jun – 15 Sep, least cloudy), 160 px. Scrubbing the year slider scrubs the
  imagery, so you can watch an orchard come out or a planting fill in. Cost 15.8 PU.
  Rendered once and bundled, so no credential ever reaches a browser.

Chips use a fixed seasonal window on purpose: comparing a June scene against a
September one shows seasonal difference, which reads deceptively like change over years.

## Standalone build

`site/farms-on-the-move.html` is genuinely self-contained: Leaflet's JS and CSS are
vendored into the page from `vendor/`, and the field data, area grid and 162 satellite
chips are embedded as JSON and data URIs. 1.66 MB.

This matters because "self-contained" was previously untrue — the file still pulled
Leaflet from unpkg, so it failed with `ReferenceError: L is not defined` in any viewer
that blocks external scripts, and offline entirely.

The one thing that cannot be bundled is the basemap tiles, which are served from Carto
and Esri. If they are blocked the field data still renders on a plain background, and
the page now says so rather than showing an unexplained blank.

## Tulare Lake Basin expansion — four counties

Fresno, Tulare, Kings and Kern, all run through the same age-matched design.
943 fields on the map, 94 grid cells.

### The mature almond vs mature pistachio result, in every county

NDMI (crop water content) slope per year, both cohorts planted pre-2012:

| County | almond mature | pistachio mature | NDMI gap (recent) |
|---|---|---|---|
| Fresno | −0.0087 | **+0.0110** | +0.056 |
| Tulare | −0.0042 | **+0.0128** | +0.025 |
| Kings | −0.0086 | **+0.0045** | +0.098 |
| Kern | −0.0080 | **+0.0048** | +0.065 |

NDVI (canopy vigour) slope per year:

| County | almond mature | pistachio mature |
|---|---|---|
| Fresno | −0.0020 | **+0.0197** |
| Tulare | +0.0024 | **+0.0209** |
| Kings | −0.0027 | **+0.0077** |
| Kern | −0.0031 | **+0.0102** |

**Four counties out of four, no exceptions.** Mature almond blocks are losing canopy
moisture (all four negative, −0.004 to −0.009/yr). Mature pistachio blocks of the same
age on the same ground are gaining it (all four positive). NDVI tells the same story:
pistachios out-trend almonds in every county.

This is no longer a one-county curiosity. It is a basin-wide pattern, age-controlled,
consistent in sign across four independent county samples.

### What still does not replicate — and should not

Index *levels* move around between counties. Kings and Tulare mature pistachios read
above almonds on NDVI; Fresno and Kern read below. That inconsistency is expected and
is exactly why the method compares trends. Anyone quoting a level as a finding has
misread the map.

### Small-sample warning

Kings has almost no young switched fields (n=1 for almonds-after-grapes, n=4 for the
pistachio cohorts). Those cells are in the data but must not be interpreted. Only the
mature cohorts (n=32–40 per county) carry weight.

### Regions covered

`HYDRO_RGN` puts these four counties across two study regions:

- **Tulare Lake Basin** — Kings, Tulare, Kern, southern Fresno
- **San Joaquin Valley** — northern Fresno

Sacramento Valley and Salinas Valley remain untouched.

## Map — multi-county build

`steps/07_build_basin_map.py` replaces the Fresno-only step 4. It discovers every
finished analysis in `data/processed`, merges them, and tags each field with county,
study region and which analysis produced it. Adding a county now means running steps 2
and 3b for it and rerunning step 7 — no code change.

Field ids are namespaced per county on merge. Without that, Kings `fr000123` and Kern
`fr000123` would collide and one would silently overwrite the other.

## Diversification layer (step 8)

Added after grower conversations reframed the question. The useful ask is not "should I
tear out my almonds" but "what else could I put in alongside them" — driven by market
risk as much as climate. Napa and Healdsburg growers described surplus grapes with
nowhere to go as the wine market softened; diversification hedges both risks at once.

### Method — no climate model required

> A crop growing in a place is proof that it grows in that place.

For each 12 km cell we take the crop inventory of every cell within **30 km** and find
crops well established in that neighbourhood but barely present here (<2% of local
acreage). Those are already proven in the local growing environment, on comparable
ground, under the same water regime, by growers who presumably are not losing money.

Two signals ride along:

- **reach** — how many nearby cells grow it. Twenty cells means robust to local soil
  quirks; two might be one operation's experiment. Minimum 3.
- **trend** — change in that crop's acreage across the four counties, 2016→2023. The
  nearest thing to a market signal available without price data.

Built from 54,827 crop fields across Fresno, Kings, Tulare and Kern. 94 cells profiled,
~7 candidates each.

### Grouping by crop class was necessary, not cosmetic

Ranking purely on nearby acreage handed an almond grower "cotton" and "hay" — true, and
useless. DWR's crop code begins with a class letter, so candidates are now grouped:
orchard (deciduous / citrus), vineyard, row & truck, field, grain & hay, pasture, rice.
Each group shows its commitment level ("perennial — years to bearing" versus "annual —
can trial in one season") so a perennial suggestion is never mistaken for something you
can try for a season.

After grouping, an almond grower in the example cell sees pomegranates (+7%) and citrus
(+15%, expanding) under orchard crops — realistic diversifications — with the annual
options listed separately.

### Stated limits

The map says plainly that this shows a crop *grows* in these conditions, not that it
will pay. Water rights, contracts, processing access and prices are not in this data.
The acreage trend conflates climate, water, market and policy.

### Reachable from a field, not just an area

Growers tap their own block, not an abstract grid cell. Every field now carries its
`cell_id`, so the diversification list appears in the field panel too.

### Not yet covering the case that prompted it

Napa and Sonoma are outside the four counties analysed, so the wine-surplus case that
motivated this is not yet served. The method transfers unchanged — it needs those
counties run through steps 2 and 8.

## Rendering change — real parcel vectors at every zoom

Reviewer feedback: show the original field vectors when zoomed out rather than
substituting points. Implemented — there is no longer any circle-marker path in the
code, and 943 real polygons render at every zoom level.

The original reason for the dots was genuine: a 40-acre parcel is only a few pixels
across at valley zoom. The solution that keeps the real geometry is to stroke each
polygon in its own fill colour with a heavier weight (3 px) below zoom 12, so the
outline lends the shape visual weight while what is drawn stays the true boundary.
Above zoom 12, parcels are large enough to read on their own and get a thin white
outline for definition instead.

`smoothFactor` is lowered to 0.3 so Leaflet keeps more of each boundary's vertices;
the default discards them aggressively, which was acceptable when the mark was a dot
and is not when the boundary is the point.

Two bugs surfaced while verifying:

- A stale selection-reset line still hardcoded the zoomed-in outline (`#fff`, 0.7 px).
  Deselecting a parcel at low zoom repainted it with the hairline and it effectively
  disappeared. Now hands the layer back to its own style function via `resetStyle`.
- Leaflet writes `d="M0 0"` for off-screen paths, which made an early check report
  every parcel as a degenerate single-vertex shape. Only on-screen paths are
  meaningful; measured properly, boundaries carry their real vertex counts.

Note for whoever gave the feedback: the app is Leaflet, not Mapbox GL. The request
translated directly, but Mapbox-specific advice will not apply as written.

## Cost structure — measured, and it corrected a wrong assumption

I claimed coarser time aggregation would cut per-field cost roughly twelvefold, making
full coverage affordable. **That was wrong.** Measured on one 57-acre field, 2017–2025,
all three indices:

| Request | PU | intervals | pixels/interval |
|---|---|---|---|
| monthly, 10 m | 20.00 | 106 | 2,480 |
| quarterly, 10 m | 19.50 | 35 | 2,480 |
| yearly, 10 m | 17.37 | 8 | 2,480 |
| monthly, 20 m | 20.00 | 106 | 640 |
| monthly, 60 m | 20.00 | 106 | 65 |

Two things fall out, both counter-intuitive:

**Resolution costs nothing.** 10 m, 20 m and 60 m all bill exactly 20.00 PU. For AOIs
this small the request sits at a floor, so there is no saving in asking for coarser
pixels — and therefore no reason ever to ask for them. Always request 10 m.

**Aggregation interval barely matters.** Monthly to yearly saves 13%, not 90%. The API
still reads every scene in the range; the interval only groups the output.

**Time range is the only real lever**, and it is close to linear:

| Record | PU/field |
|---|---|
| 9 years (2017–2025) | 20.0 |
| 5 years (2021–2025) | 12.2 |
| 3 years (2023–2025) | 7.4 |

So the working rule is roughly **2.2 PU per field per year of record**, regardless of
resolution or aggregation.

### What that means for coverage

105,000 orchard and vineyard fields across the four counties:

| Record | Total PU | Months of allowance |
|---|---|---|
| 9 years | 2,100,000 | 5.2 |
| 5 years | 1,280,000 | 3.2 |
| 3 years | 780,000 | 2.0 |

Full four-county coverage is not achievable inside MTerm at any time depth. What *is*
achievable in roughly one month's allowance:

- **One county, target crops, full record** — Fresno's ~14,700 almond/pistachio/grape
  fields at 9 years ≈ 294,000 PU.
- **Four counties, target crops, 3-year record** ≈ 350,000 PU, but three years is too
  short for a defensible trend.

### The mixed-resolution idea is dead, and that is a good outcome

The original plan was a cheap annual layer for everyone plus monthly for a research
sample. Since coarser aggregation saves almost nothing, there is no reason to build a
two-tier dataset — every field can be treated identically. The concern about some farms
having better data than others does not arise.

## Benchmark percentiles

Each field is now ranked against **the same crop in the same county**, on the currently
selected index, for both recent level and trend. Crop matters because levels are not
comparable across crops; county holds climate and water district roughly constant.
Groups smaller than 8 get no percentile — a rank out of four is noise dressed as a
statistic. 933 of 943 fields ranked, across 12 crop × county groups.

Phrasing is deliberate: "Bottom 18%", "Top 19%", or "Middle of the pack". An earlier
version said "top 48%" for a middling field, which is technically true and misleading.

## Napa / Sonoma wine country

Expanded here first because it is the only region with a deadline — the grant commits
to Week 3 field visits with grape growers — and because the diversification feature was
built on their feedback but did not yet cover their ground.

### Both assumptions the method relies on failed, as predicted

**No planting year.** Zero of 24,496 Napa/Sonoma vineyard fields carry `YR_PLANTED`.
The age control that rescued the Fresno pistachio result cannot be built here.

**No crop switching to compare.** Napa, 2016 → 2023:

```
44,932 ac of grapes matched
  94% still grapes
   6% changed — and 73% of that went idle, not to another crop
  net −887 ac
```

There is no "switched" cohort in wine country. Growers do not move out of Napa
vineyard; the appellation is the asset.

### Step 3c — a control derived from the imagery instead

Since planting year is unavailable, maturity is inferred from the record itself: a
field whose first three seasons already carry a full canopy (NDVI ≥ 0.45) was not
planted during the record, so its trend cannot be establishment. Weaker than a known
planting date, but computable everywhere, and it generalises to any region added later.

59 of 60 Napa fields and 240 of 240 Sonoma fields qualified as mature at start.

### The finding: the vines are fine

| | NDVI slope | % declining | NDMI slope | % declining |
|---|---|---|---|---|
| Napa grapes | +0.0035 | 2% | +0.0046 | 0% |
| Sonoma grapes | +0.0041 | 3% | +0.0017 | 0% |

Mature vineyards in both counties are **stable to slightly improving** on vigour and
moisture. Nothing in the imagery shows stress.

That matters, and it should be said plainly to the growers: **the problem they
described is not visible to this instrument.** Surplus grapes with nowhere to go is a
market condition, not an agronomic one. Satellite vegetation monitoring can tell a
Central Valley grower their block is drying out; it has nothing to tell a Napa grower
about the price of cabernet. Presenting NDVI as though it spoke to wine demand would be
the single easiest way to lose that audience's trust.

### The monoculture, quantified

Crops farmed at scale in each study area:

| Study area | Crops proven region-wide |
|---|---|
| Tulare Lake Basin | 40 |
| Napa / Sonoma wine country | 8 |

Vineyards are 107,157 of roughly 160,000 farmed acres. In the densest cell — 79% grapes
— the neighbourhood comparison returns **no** diversification candidates at all: every
crop established within 30 km is already grown there. That is a true answer, and it is
the structural reason the wine-surplus problem is hard.

The map now carries a third section, **"All crops proven in this region"**, so a grower
always sees the full option set even where the local comparison comes up empty. For
wine country that list is short and honest: grapes, hay and grain, pasture, grasses,
apples.

### Region-scoped trends were necessary

Crop acreage trends are now computed **within** a study area, never across all of them.
Grapes fell 27% in Fresno as the raisin market contracted; in wine country they moved
+1%. Showing a Napa grower the Fresno figure would have been actively misleading about
their own market.

## Coverage after this expansion

1,243 fields, 6 counties, 4 study regions. 118 grid cells profiled for diversification.

## Wine country resampled into growing districts

Feedback from looking at the first wine-country map: vineyards scattered across two
whole counties are hard to read, and the coastal and Santa Rosa fields added confusion
rather than information. Growers do not think in counties, they think in districts.

The sample is now clustered into the three districts that matter, defined as a centre
and a radius, with each field assigned to its **nearest** centre:

| District | Fields | Extent | Centre |
|---|---|---|---|
| Napa Valley (Napa–St. Helena) | 45 | 22 × 21 km | 38.415, −122.358 |
| Healdsburg (Dry Creek / Alexander) | 76 | 23 × 18 km | 38.634, −122.842 |
| Sonoma Valley | 45 | 18 × 12 km | 38.303, −122.490 |

Nearest-centre assignment rather than bounding boxes, because Napa Valley and Sonoma
Valley are only about 12 km apart and any radius wide enough to cover each would
double-count the ground between them.

166 clustered fields replace the previous 300 scattered ones. Everything outside the
three districts — the Sonoma coast, the Santa Rosa plain, Petaluma — is dropped.

Napa Valley floor grapes, resampled: NDVI +0.0059/yr with **0%** declining, NDMI
+0.0036/yr with 0% declining. Tighter and slightly healthier than the scattered sample,
which is what you would expect from restricting to prime valley-floor ground.

### A bug the clustering exposed

"All crops proven in this region" was being computed from the sampled grid cells rather
than the region itself. When the wine sample shrank to three districts, the grid shrank
with it and the list silently fell from 8 crops to 3 — an artefact of where we chose to
sample, presented as a fact about the region. It now reads from the full county
inventory, independent of sampling. Back to 8 for wine country, 41 for Tulare Lake.

This is the same class of error as the earlier tree-age confounder: a number that stays
plausible while quietly meaning something other than what it claims.

### Reusable

`DISTRICT_SETS` in step 3c is generic. Salinas Valley and the Sacramento rice districts
can be clustered the same way when those regions are added, which will matter — both
are long, narrow growing areas where a county-wide scatter would read just as poorly.

## San Joaquin Valley — and the pistachio result holds across seven counties

Madera, Merced, Stanislaus and San Joaquin added. Same crop system as Tulare Lake, so
step 3b ran unchanged. ~20,400 PU.

### Mature almond vs mature pistachio, every county analysed

| County | n (alm/pist) | NDVI almond | NDVI pistachio | NDMI almond | NDMI pistachio |
|---|---|---|---|---|---|
| Fresno | 40/40 | −0.0020 | +0.0197 | **−0.0087** | **+0.0110** |
| Kern | 32/40 | −0.0031 | +0.0102 | **−0.0080** | **+0.0048** |
| Kings | 40/40 | −0.0027 | +0.0077 | **−0.0086** | **+0.0045** |
| Madera | 40/39 | −0.0018 | +0.0128 | **−0.0043** | **+0.0030** |
| Merced | 39/40 | −0.0050 | +0.0281 | **−0.0087** | **+0.0195** |
| Tulare | 40/40 | +0.0024 | +0.0209 | **−0.0042** | **+0.0128** |
| Stanislaus | 40/**1** | −0.0038 | +0.0066 | −0.0089 | +0.0026 |

**Mature almond canopy moisture is falling in every county. Mature pistachio canopy
moisture is rising in every county.** No exceptions.

Discount Stanislaus: n=1 pistachio is not evidence, it just happens to agree. The honest
count is **six counties with adequate samples on both sides, all pointing the same way**,
spanning two hydrologic basins (Tulare Lake and San Joaquin River). NDVI agrees in five
of six; only Tulare shows mature almonds marginally positive on vigour, and even there
moisture falls.

Sample sizes are ~40 per cohort per county, so this is roughly 240 mature almond blocks
against 240 mature pistachio blocks, age-controlled, measured over the same nine years.

### San Joaquin County has no pistachios, and that is the point

Zero mature pistachio fields and two young ones in 20,367 matched fields. Not a data
gap — pistachios need more summer heat than the northern valley delivers, so the crop
is barely grown there. The comparison simply cannot be made at the cool end of the
valley, which is itself a piece of climate information and belongs in the report.

It also bounds the recommendation geographically: the pistachio finding applies to the
southern and central valley, not to San Joaquin County.

### Coverage

1,619 fields · 10 counties · 3 study regions · 159 grid cells profiled.
Roughly 57,000 PU spent of 400,000/month.

Crops proven region-wide: Tulare Lake Basin 41, San Joaquin Valley 36, wine country 8.

## Sacramento Valley — the 2022 rice fallowing, and a water-rights boundary from space

Butte, Colusa, Glenn, Sutter and Yolo added via step 3c (condition sample), since rice
is an annual and neither the cohort nor the planting-year method applies.

### Median rice NDVI by year

| County | 2017 | 2018 | 2019 | 2020 | 2021 | **2022** | 2023 | 2024 | 2025 |
|---|---|---|---|---|---|---|---|---|---|
| Colusa | .923 | .924 | .926 | .926 | .922 | **.255** | .924 | .927 | .932 |
| Glenn | .927 | .933 | .937 | .946 | .930 | **.274** | .934 | .934 | .935 |
| Yolo | .792 | .887 | .881 | .860 | .865 | **.534** | .911 | .905 | .905 |
| Butte | .861 | .874 | .887 | .902 | .888 | **.893** | .910 | .835 | .911 |

Shortfall against each county's own normal: **Colusa −72%, Glenn −71%, Yolo −40%,
Butte −1%.**

This is the 2022 fallowing, when Sacramento River settlement contractors took
near-total surface-water cuts and roughly half the valley's rice went unplanted. Five
years of textbook rice canopy, one year of bare ground, full recovery by 2023.

**The gradient is not soil or climate — it is water rights.** Colusa and Glenn depend on
Sacramento River supplies that were cut hardest; Butte draws on the Feather River system
with more senior rights and was essentially untouched. The map renders a water-contract
boundary from orbit. This is the clearest demonstration in the project of something
satellite monitoring does that no other method does cheaply.

### A methodological gap this exposed

Colusa rice ran a **nine-year NDVI trend of +0.0003/yr** — flat — across the most
disruptive agricultural event in the region this decade, because it recovered. A
trend-only analysis reports "nothing happening".

So the map now carries a **worst-year anomaly**: for each field, the year furthest below
its own median, shown when the shortfall exceeds 15%. Flagged for 2,472 of 3,727 fields.
For rice, 2022 is the worst year in 64 of 173 fields. Trends describe drift; anomalies
catch shocks. Annual crops need both.

### Two bugs fixed

**The maturity filter was about to delete the finding.** "Mature at start" was dropping
fields with low early-years NDVI. For a perennial that means "planted during the record";
for rice it means "fallow that year". Applied to annuals it would have excluded exactly
the fields a fallowing study needs. Now restricted to perennial classes (D, C, V).

**One crop, two labels.** Crop names were learned per county from that county's own 2016
records, so rice was "Rice" in Colusa and "R1" in Sutter — 1,641 fields and 106,826 acres
hiding under a bare code. It would have split one crop into two cohorts, listed both in
the map's filter, and broken cross-county comparison without ever raising an error. The
lookup is now built once, statewide; 77 labels corrected.

Same signature as every serious bug in this project: plausible output, quietly wrong.

## Coverage

**3,727 fields · 15 counties · 4 study regions · 240 grid cells.**
Roughly 115,000 PU of 400,000/month.

| Region | Crops proven region-wide |
|---|---|
| Tulare Lake Basin | 41 |
| San Joaquin Valley | 36 |
| Sacramento Valley | 30 |
| Napa / Sonoma wine country | 8 |

Standalone file is 8.8 MB. Coordinate precision is capped at five decimals (~1 m); past
roughly 10 MB the hosted `site/` build is the better delivery route.

## Salinas Valley — resolving the rotation

Monterey and San Benito. The hardest region methodologically: two to three crops a year
on the same ground means "one growing season per year" describes nothing real. Lettuce
planted in March and again in July is not a trend, it is a rotation.

### The enabler: time resolution is nearly free

Measured on one field, same nine years:

| Interval | PU | Intervals returned |
|---|---|---|
| P1M | 20.00 | 106 |
| P15D | 20.27 | 212 |
| P10D | 20.23 | 308 |

**Ten-day composites cost 1% more than monthly for three times the temporal detail.**
Cost tracks the time range, not the number of outputs. A Salinas lettuce cycle runs
60–90 days, so at monthly resolution two crops blur into one broad peak; at 10 days each
cycle spans 6–9 observations and separates cleanly.

### Counting cropping cycles

A cycle is one rise into a closed canopy followed by a fall back to bare ground.
Detected by hysteresis — count on crossing NDVI 0.45 rising, re-arm on falling below
0.25 — so a series wobbling around one value cannot be counted twice.

| Crop | Cycles/yr | n |
|---|---|---|
| Lettuce / leafy greens | 2.01 | 70 |
| Cole crops | 1.90 | 70 |
| Carrots | 1.85 | 35 |
| Onions and garlic | 1.79 | 35 |
| Misc truck crops | 1.73 | 70 |
| Misc grain and hay | 1.61 | 70 |
| Mixed pasture | 1.40 | 35 |
| Strawberries | 1.39 | 35 |
| **Grapes** | **1.11** | 70 |

**Grapes are the control.** A vineyard leafs out and senesces exactly once a year, so a
detector counting noise would score them 2 or 3. Both counties returned 1.11
independently. Lettuce and cole crops at ~2.0 match Salinas practice.

Cole crops fall from 2.04 in coastal Monterey to 1.76 inland in San Benito — the coastal
fog belt supports more cycles, and the method picks up the gradient.

### Another silent-type bug

`cycles_per_year` exists only for Salinas, so the column was float there and None
everywhere else — object dtype, which GeoJSON writes as **strings**. In the browser
`"1.89".toFixed(1)` throws, which would have taken down the entire detail panel for
every Salinas field while every other region kept working. Every numeric column is now
coerced explicitly before writing, and the map parses defensively.

Third bug in this project with the same signature: output that looks right, is the
wrong type or means the wrong thing, and fails somewhere far from the cause.

## Final coverage

**4,217 fields · 17 counties · 5 study regions · 275 grid cells · 2017–2025**

| Region | Counties | Crops proven region-wide |
|---|---|---|
| Tulare Lake Basin | Fresno, Kings, Tulare, Kern | 41 |
| San Joaquin Valley | Madera, Merced, Stanislaus, San Joaquin | 36 |
| Sacramento Valley | Butte, Colusa, Glenn, Sutter, Yolo | 30 |
| Salinas Valley | Monterey, San Benito | 19 |
| Napa / Sonoma wine country | Napa, Sonoma (3 districts) | 8 |

Roughly 131,000 processing units of a 400,000/month allowance.

## Rebalancing coverage

The sample had grown lopsided. Sacramento Valley was half the entire map, most of it
north of Yuba City, while wine country — the region with field visits scheduled and the
growers whose feedback shaped the diversification layer — was 4%.

| | Before | After |
|---|---|---|
| North of Yuba City | 1,003 (24%) | **172 (5%)** |
| Wine country | 166 (4%) | **491 (15%)** |

Region shares are now 15–27% across all five, against 4–50% before.

| Region | Fields | Share |
|---|---|---|
| Tulare Lake Basin | 890 | 27% |
| Sacramento Valley | 873 | 26% |
| San Joaquin Valley | 564 | 17% |
| Napa / Sonoma wine country | 491 | 15% |
| Salinas Valley | 489 | 15% |

### How

Three new controls in step 3c, all reusable:

- `--max-crops N` — sample only the N largest crops in a group. Northern counties were
  carrying 12 crops each at full sample; now 3–4.
- `--max-lat` / `--min-lat` — a latitude window. Sutter straddles Yuba City, so it is
  cut at 39.14 rather than dropped.
- `DISTRICT_SET_MAX_LAT` — wine country stops at Cloverdale (38.81 N). North of there
  is Mendocino's growing area: a different appellation system and a different
  conversation.

**The rice fallowing finding is preserved.** Colusa and Glenn keep 20–25 rice fields
each, enough to hold the 2022 result, while their non-rice crops were cut hard. The
reduction is in breadth, not in the evidence.

**Cutting back cost nothing.** The smaller northern samples are subsets of fields
already cached, so those runs spent **0 PU**. Only wine country's expansion cost
anything: 2,989 PU.

### Napa now runs the full valley

The old district was centred to cover Napa city to St. Helena and stopped about 5 km
short of Calistoga. It now runs the whole floor — Napa, Yountville, Oakville,
Rutherford, St. Helena, Calistoga — on a 22 km radius.

| District | Fields |
|---|---|
| Napa Valley (Napa–Calistoga) | 198 |
| Healdsburg (Dry Creek / Alexander) | 163 |
| Sonoma Valley | 130 |

Sample latitude runs 38.242 to 38.725, entirely south of Cloverdale.

**3,307 fields · 17 counties · 5 regions · ~134,000 PU spent.**

## Density increase

Coverage deepened within the existing regions — no borders moved — targeting roughly
half the monthly allowance.

**5,148 fields**, up from 3,307. Usage went from 38% to **51.0%** of the 400,000
PU/month allowance, leaving 195,840 PU.

| Region | Before | After | Share |
|---|---|---|---|
| Sacramento Valley | 873 | 1,407 | 27% |
| Tulare Lake Basin | 890 | 1,154 | 22% |
| Salinas Valley | 489 | 972 | 19% |
| San Joaquin Valley | 564 | 920 | 18% |
| Napa / Sonoma wine country | 491 | 695 | 14% |
| *north of Yuba City* | *172* | *172* | *3%* |

North of Yuba City was deliberately held flat, so the earlier rebalance survives the
density increase. Sacramento's growth is entirely in Yolo and southern Sutter.

### Additive sampling — the change that made it affordable

`df.sample(80)` is not a superset of `df.sample(40)`; pandas redraws. Growing every
sample would therefore have re-fetched fields already paid for and cached, roughly
doubling the cost of this step.

Sampling is now **additive**: each run reads the previous sample's field ids, keeps
them, and draws only the top-up. `farms.analysis.top_up()` and `existing_sample_ids()`.

The effect is visible in the run logs — Fresno reused 250 cached fields and fetched 148:

```
Fresno      148 requests · 250 cached ·  5,555 PU
Kings        34 requests · 113 cached ·  1,234 PU
Tulare       73 requests · 162 cached ·  1,932 PU
Kern        124 requests · 228 cached ·  3,791 PU
Napa         46 requests · 154 cached ·  1,268 PU
Sonoma      102 requests · 413 cached ·  3,979 PU
```

Adding 1,841 fields cost **53,783 PU**. Without additive sampling it would have cost
roughly 103,000 — the whole increase would have overshot the target.

Sample sizes now: 80 per cohort for the age-matched regions, 200 per wine district,
70 per crop in Salinas, 65 in Yolo, 45 in southern Sutter.

## Record extended through August 2026

The satellite record now runs 2017 to **August 2026**. 5,511 of 5,530 sampled fields
carry 2026; every county is at 98-100%.

Fetched as its own date range rather than by widening the existing one. The cache key
includes the range, so moving `end` from 2025 to 2026 would have invalidated every
response already paid for — about 20 PU each across 5,300 fields. Requesting 2026
separately and concatenating cost **~2.8 PU per field**, roughly seven times less.
Total: **15,162 PU** across both passes.

2026 appears on the year slider but is **excluded from trend fitting** — a season only
run through August has not necessarily peaked, and including it would drag every trend
down for reasons unrelated to the crop.

## Climate outlook (step 10) — Cal-Adapt

### Why not the obvious approach

The first attempt used Open-Meteo, per 12 km grid cell, 36 years of daily data each.
305 requests. It exhausted the free tier's daily quota without finishing, degrading to
730 seconds per request. Two attempted fixes — deduplicating points, then coarsening
the grid — treated a design problem as a tuning problem.

The design was wrong in two ways. The question was asked at *region* level and answered
at grid-cell level, roughly fifteen times more data than needed. And observed trends
cannot answer "what may BECOME suitable" — extrapolating a 36-year line is not a
projection.

### What works

**Cal-Adapt**, California's downscaled climate service: 6 km LOCA, 32-model ensemble,
1950-2099, RCP4.5 and RCP8.5.

| | Open-Meteo | Cal-Adapt |
|---|---|---|
| Per query | 4.6 s | **0.4 s** |
| Rate limit | exhausted in ~15 requests | fine |
| Record | 1990-2025 | 1950-**2099** |
| Future scenarios | none | **two** |

18 district points, complete in under 90 seconds.

### Climate analogs instead of crop thresholds

Published crop temperature requirements are contested and vary by cultivar, rootstock
and management. Rather than assert them, the map asks a question the data answers on
its own: **which district already has the climate this district is projected to have?**

| District | Now | 2050 | 2080 | 2050 resembles |
|---|---|---|---|---|
| Napa Valley | 22.7 | 24.4 | 26.5 | San Joaquin today |
| Healdsburg | 23.7 | 25.2 | 27.1 | Glenn today |
| Sonoma Valley | 23.9 | 25.5 | 27.5 | Butte today |
| Monterey | 24.1 | 25.6 | 27.7 | Merced today |
| Fresno | 26.0 | 27.5 | 29.7 | Kern today |
| **Kern** | 26.6 | 28.0 | 30.1 | **hotter than anywhere here today** |

Mean annual daily maximum, degrees C, RCP8.5.

Two things fall out. **Napa in 2050 has Stockton's climate today** — a concrete,
checkable statement rather than an abstract temperature rise, and it points at ground a
grower can go and look at. And **Kern runs off the top of the scale**: by 2050 the
southern valley is warmer than anywhere currently farmed in the study area, so there is
no local analog to learn from. That is a finding in itself.

This closes the grant's goal 2 — identifying where conditions may become suitable —
which was the last unmet commitment in the proposal.

### Limits stated on the map

Temperature only. Water rights, soil, winter chill and markets decide what actually
grows, and none are in this projection. Scenario- and model-dependent by construction.

### Chill hours, deliberately omitted

Chill was the most valuable variable for a perennial-crop story and is not included.
A 1 degree bias in daily minimum temperature moves accumulated chill by 22%, and no
reanalysis at 9 km resolves the valley cold-air pooling that sets those minima —
Open-Meteo returned chill *rising* 2% in Napa and Fresno, contradicting the literature.
gridMET at 4 km gives plausible values but takes 22 seconds per year per location.
Left out rather than shipped wrong.
