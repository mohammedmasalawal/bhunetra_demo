"""
BhuNetra -- AOI (Area Of Interest) registry.

The single source of truth for every mining region the system monitors.
Onboarding a new region is an entry here + a row in the `aois` table
(db/seed_aois.py upserts from this same dict) -- no other code changes.

NOTE: main.py's AOI_SEED duplicates the shared fields of these entries
(the API bundle .vercelignore's out pipeline/, so it can't import this).
Keep the two lists in agreement when adding a region.

Each entry:
    name                 human label shown in the UI
    state, district      administrative location
    mineral              primary commodity
    bbox                 {west, south, east, north} in EPSG:4326 -- the
                         rectangle download_sentinel.py pulls scenes for.
                         Keep it tight around the actual pit cluster;
                         processing cost scales with area.
    center               {lat, lon} -- map default view before it fits to data
    lease_file           path to a lease-boundary GeoJSON, or None
    lease_boundary_valid True only if lease_file is a real boundary for THIS
                         site (detection.py uses it for in/out-of-lease
                         classification; False => every trigger is
                         "boundary_unknown")
    max_cloud_cover      Sentinel-2 cloud-cover ceiling (%) for scene search
    has_imagery          whether a curated evidence-imagery set is published
                         for this AOI (see main.py IMAGERY_BY_AOI)

Only AOI-07-BAILADILA is data-populated today. The others are real mining
belts, pre-configured so onboarding is `python pipeline/live_monitor.py
--aoi <id>` once the credentials are in place -- they carry zero alerts
until that runs.
"""
from __future__ import annotations

DEFAULT_AOI = "AOI-07-BAILADILA"

AOIS: dict[str, dict] = {
    "AOI-07-BAILADILA": {
        "name": "Bailadila Iron Ore Complex",
        "state": "Chhattisgarh",
        "district": "Dantewada",
        "mineral": "Iron Ore",
        "bbox": {"west": 81.22, "south": 18.65, "east": 81.245, "north": 18.67},
        "center": {"lat": 18.6585, "lon": 81.2305},
        "lease_file": "real_data/lease_boundary.geojson",
        "lease_boundary_valid": True,
        "max_cloud_cover": 40,
        "has_imagery": True,
    },
    "AOI-BELLARY-SANDUR": {
        "name": "Sandur-Hospet Iron Ore Belt",
        "state": "Karnataka",
        "district": "Ballari",
        "mineral": "Iron Ore",
        "bbox": {"west": 76.53, "south": 15.05, "east": 76.63, "north": 15.15},
        "center": {"lat": 15.10, "lon": 76.58},
        "lease_file": None,
        "lease_boundary_valid": False,
        "max_cloud_cover": 30,
        "has_imagery": False,
    },
    "AOI-KEONJHAR-JODA": {
        "name": "Joda-Barbil Iron & Manganese Belt",
        "state": "Odisha",
        "district": "Keonjhar",
        "mineral": "Iron / Manganese",
        "bbox": {"west": 85.38, "south": 21.98, "east": 85.48, "north": 22.08},
        "center": {"lat": 22.03, "lon": 85.43},
        "lease_file": None,
        "lease_boundary_valid": False,
        "max_cloud_cover": 30,
        "has_imagery": False,
    },
    "AOI-KORBA-COALFIELD": {
        "name": "Korba Coalfield",
        "state": "Chhattisgarh",
        "district": "Korba",
        "mineral": "Coal",
        "bbox": {"west": 82.58, "south": 22.33, "east": 82.72, "north": 22.44},
        "center": {"lat": 22.38, "lon": 82.65},
        "lease_file": None,
        "lease_boundary_valid": False,
        "max_cloud_cover": 35,
        "has_imagery": False,
    },
    "AOI-JHARIA-COALFIELD": {
        "name": "Jharia Coalfield",
        "state": "Jharkhand",
        "district": "Dhanbad",
        "mineral": "Coal",
        "bbox": {"west": 86.38, "south": 23.72, "east": 86.48, "north": 23.82},
        "center": {"lat": 23.77, "lon": 86.43},
        "lease_file": None,
        "lease_boundary_valid": False,
        "max_cloud_cover": 35,
        "has_imagery": False,
    },
}


def get_aoi(aoi_id: str) -> dict:
    """Return the config dict for an AOI id, or raise with a helpful list."""
    try:
        return AOIS[aoi_id]
    except KeyError:
        known = ", ".join(sorted(AOIS))
        raise KeyError(f"Unknown AOI '{aoi_id}'. Known AOIs: {known}") from None


def data_dir(aoi_id: str) -> str:
    """Per-AOI working directory for downloaded bands / state.

    AOI-07-BAILADILA keeps its historical 'real_data' path so nothing that
    already points there breaks; every other AOI gets its own folder."""
    if aoi_id == DEFAULT_AOI:
        return "real_data"
    return "real_data_" + aoi_id.lower().replace("-", "_")


def bbox_tuple(aoi_id: str) -> dict:
    return get_aoi(aoi_id)["bbox"]
