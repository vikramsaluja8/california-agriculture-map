"""The evalscript — a small program Planet runs on every pixel, server-side.

We ask for all three indices in ONE request rather than three, for two reasons.
Cost is the obvious one. The subtler and more important one is consistency: all three
values come from *the same observation on the same day*, so when a field shows high
vigour and low moisture you know that is a real simultaneous condition and not an
artefact of comparing a July NDVI against an August NDMI.

Selection rule: within each month, take the clearest observation with the highest
NDVI, then report all three indices from that scene. Cloud pushes NDVI down, so
"highest NDVI" is a reliable proxy for "least contaminated look at the ground".

The three indices measure genuinely different things:

  NDVI  (B08-B04)/(B08+B04)   canopy vigour — how much green biomass
  NDMI  (B08-B11)/(B08+B11)   water inside the plant — crop water stress
  NDWI  (B03-B08)/(B03+B08)   open water on the surface — flooding, ponding

NDMI is the one people usually mean when they say "is this crop thirsty". NDWI is
about standing water, which is why it matters for rice and for spotting irrigation.
"""

from __future__ import annotations

# Sentinel-2 Scene Classification values we accept as clear ground.
#   4 vegetation · 5 not-vegetated · 7 unclassified
# Rejected: 0 no-data, 1 saturated, 2 dark, 3 cloud shadow, 6 water,
#           8/9 cloud, 10 cirrus, 11 snow
TRIPLE_INDEX = """//VERSION=3
function setup() {
  return {
    input: [{ bands: ["B03", "B04", "B08", "B11", "SCL", "dataMask"] }],
    output: [
      { id: "ndvi", bands: 1, sampleType: "FLOAT32" },
      { id: "ndmi", bands: 1, sampleType: "FLOAT32" },
      { id: "ndwi", bands: 1, sampleType: "FLOAT32" },
      { id: "dataMask", bands: 1 }
    ],
    mosaicking: "ORBIT"
  };
}

function isClear(scl) { return scl === 4 || scl === 5 || scl === 7; }

function nd(a, b) { var d = a + b; return d > 0 ? (a - b) / d : 0; }

function evaluatePixel(samples) {
  // Pick the clearest look at the ground this month, then read all three indices
  // off that single observation so they stay mutually consistent.
  var best = -2.0, chosen = -1;
  for (var i = 0; i < samples.length; i++) {
    var s = samples[i];
    if (s.dataMask !== 1 || !isClear(s.SCL)) continue;
    var v = nd(s.B08, s.B04);
    if (v > best) { best = v; chosen = i; }
  }

  if (chosen < 0) {
    return { ndvi: [0], ndmi: [0], ndwi: [0], dataMask: [0] };
  }

  var s = samples[chosen];
  return {
    ndvi: [nd(s.B08, s.B04)],
    ndmi: [nd(s.B08, s.B11)],
    ndwi: [nd(s.B03, s.B08)],
    dataMask: [1]
  };
}
"""

INDEX_NAMES = ("ndvi", "ndmi", "ndwi")

INDEX_LABELS = {
    "ndvi": "Vigour (NDVI)",
    "ndmi": "Crop moisture (NDMI)",
    "ndwi": "Surface water (NDWI)",
}

INDEX_HELP = {
    "ndvi": "How much healthy green canopy the field is carrying.",
    "ndmi": "Water content inside the plants. Falls when a crop is water-stressed.",
    "ndwi": "Standing water on the ground — flooding, ponding, flood irrigation.",
}
