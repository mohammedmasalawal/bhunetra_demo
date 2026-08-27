"""
BhuNetra — Trigger Scoring (Member 2's confidence-fusion step)

Adds a sar_change_score (mean absolute Sentinel-1 VV backscatter change in
dB in a window around each trigger -- SAR corroborates optical NDVI change
since it isn't affected by clouds/vegetation-index quirks) to each trigger
in output/triggers.json, fuses it with change_pct into a confidence_score,
then flips status to PENDING_REVIEW ready for Pair B's POST /trigger
endpoint.

road_access_score is computed from real_data/roads.geojson (extracted
locally via extract_roads.py from an OSM .pbf extract -- no live API
calls, no Overpass). See its docstring below for what this measures and
what it deliberately does not.

Also runs an MMDR Act legality-determination layer per trigger against a
MOCK permit registry (real_data/mock_permits.json) -- see that file's
disclaimer. Two of the four checks (mineral_check, volume_check) are
unimplemented placeholders; see their docstrings for why.
"""
import json
import math
import os
from datetime import datetime, timedelta

import geopandas as gpd
import numpy as np
import rasterio
from dateutil.parser import parse as parse_date
from shapely.geometry import Point

try:
    from aois import data_dir as _aoi_data_dir, get_aoi, DEFAULT_AOI
except ImportError:
    from pipeline.aois import data_dir as _aoi_data_dir, get_aoi, DEFAULT_AOI


TRIGGERS_FILE = "output/triggers.json"
OUTPUT_FILE   = "output/triggers_scored.json"

# All the per-AOI input rasters/vectors live under one directory. Defaults
# to "real_data" (Bailadila); set_data_dir() / --aoi repoints them.
DATA_DIR      = "real_data"
PERMITS_FILE  = "real_data/mock_permits.json"
ROADS_FILE    = "real_data/roads.geojson"

BEFORE_VV_FILE = "real_data/before_vv.tif"
AFTER_VV_FILE  = "real_data/after_vv.tif"
SAR_WINDOW_PX  = 5      # +/- pixels around the trigger centroid
SAR_DB_SCALE   = 8.0    # mean_abs_change_db of this magnitude -> sar_change_score of 1.0
SAR_LINEAR_FLOOR = 1e-5 # clip valid linear backscatter below this before log10

AFTER_RED_FILE  = "real_data/after_red.tif"
AFTER_NIR_FILE  = "real_data/after_nir.tif"
AFTER_BLUE_FILE = "real_data/after_blue.tif"
AFTER_SWIR_FILE = "real_data/after_swir.tif"

BEFORE_NTL_FILE = "real_data/before_ntl.tif"
AFTER_NTL_FILE  = "real_data/after_ntl.tif"


def set_data_dir(d: str):
    """Repoint every input raster/vector path at directory `d`."""
    global DATA_DIR, PERMITS_FILE, ROADS_FILE, BEFORE_VV_FILE, AFTER_VV_FILE
    global AFTER_RED_FILE, AFTER_NIR_FILE, AFTER_BLUE_FILE, AFTER_SWIR_FILE
    global BEFORE_NTL_FILE, AFTER_NTL_FILE
    DATA_DIR = d
    PERMITS_FILE   = f"{d}/mock_permits.json"
    ROADS_FILE     = f"{d}/roads.geojson"
    BEFORE_VV_FILE = f"{d}/before_vv.tif"
    AFTER_VV_FILE  = f"{d}/after_vv.tif"
    AFTER_RED_FILE = f"{d}/after_red.tif"
    AFTER_NIR_FILE = f"{d}/after_nir.tif"
    AFTER_BLUE_FILE = f"{d}/after_blue.tif"
    AFTER_SWIR_FILE = f"{d}/after_swir.tif"
    BEFORE_NTL_FILE = f"{d}/before_ntl.tif"
    AFTER_NTL_FILE  = f"{d}/after_ntl.tif"


def set_aoi(aoi_id: str):
    get_aoi(aoi_id)  # validate
    set_data_dir(_aoi_data_dir(aoi_id))

# The AFTER scene's actual acquisition date, as resolved by
# download_sentinel.py for this site (printed there as "after: using scene
# from ..."). Every trigger here comes from the same before/after image
# pair, so they all share this one reference date. NOT derived
# automatically -- detection.py doesn't currently persist the real scene
# date into triggers.json (it stamps detected_at with the wall-clock time
# the script was run instead), so this must be kept in sync by hand if the
# site or date range changes.
AFTER_SCENE_DATE = "2024-01-05"
DISPATCH_WINDOW_DAYS = 15
MINERAL_RATIO_EPS = 1e-6

EARTH_METERS_PER_DEG_LAT = 111320.0  # good enough approximation for a small AOI

ROAD_ACCESS_FULL_SCORE_M = 500     # road_access_score = 1.0 within this distance
ROAD_ACCESS_ZERO_SCORE_M = 1500    # road_access_score = 0.0 at/beyond this distance

CHANGE_PCT_WEIGHT = 0.65
SAR_SCORE_WEIGHT  = 0.35

# Provisionally calibrated to this single 9-trigger validation set's observed
# 2-signal confidence_score range (0.292-0.506) -- not statistically robust,
# and will need recalibration against a larger labeled dataset before
# production use.
CONFIDENCE_TIERS = (
    (0.45, "OFFICER_REVIEW"),
    (0.32, "WATCHLIST"),
)
DEFAULT_TIER = "MONITORING_ONLY"
SAR_UNAVAILABLE_TIER = "SAR_UNAVAILABLE"


def confidence_tier(score):
    for threshold, tier in CONFIDENCE_TIERS:
        if score >= threshold:
            return tier
    return DEFAULT_TIER


def load_triggers():
    with open(TRIGGERS_FILE) as f:
        return json.load(f)


def load_vv_band(path):
    """Read the VV band as float64, replacing nodata pixels with NaN so
    they can never be silently treated as real (near-)zero backscatter."""
    with rasterio.open(path) as src:
        arr = src.read(1).astype("float64")
        if src.nodata is not None:
            arr[arr == src.nodata] = np.nan
        return arr, src.transform


def linear_to_db(x):
    # NaN (nodata) propagates through maximum/log10 rather than being
    # floored into a fake reading; np.errstate silences the resulting
    # (expected, harmless) invalid-value warning.
    with np.errstate(invalid="ignore"):
        return 10 * np.log10(np.maximum(x, SAR_LINEAR_FLOOR))


def extract_window(arr, transform, lat, lon):
    row, col = rasterio.transform.rowcol(transform, lon, lat)
    r0, r1 = max(row - SAR_WINDOW_PX, 0), min(row + SAR_WINDOW_PX + 1, arr.shape[0])
    c0, c1 = max(col - SAR_WINDOW_PX, 0), min(col + SAR_WINDOW_PX + 1, arr.shape[1])
    return arr[r0:r1, c0:c1]


def sar_change_score(lat, lon, before_vv, before_transform, after_vv, after_transform):
    """Returns (score, mean_abs_change_db) or (None, None) if the window
    isn't fully covered by valid (non-nodata) SAR data in both scenes --
    scoring an unavailable window as 0.0 would wrongly read as "no change"
    instead of "unknown"."""
    before_window = extract_window(before_vv, before_transform, lat, lon)
    after_window  = extract_window(after_vv, after_transform, lat, lon)

    # windows are extracted independently per raster's own transform, so if a
    # trigger sits at the very edge they could clip to different shapes
    r = min(before_window.shape[0], after_window.shape[0])
    c = min(before_window.shape[1], after_window.shape[1])
    if r == 0 or c == 0:
        return None, None

    before_window = before_window[:r, :c]
    after_window  = after_window[:r, :c]
    if np.isnan(before_window).any() or np.isnan(after_window).any():
        return None, None

    db_before = linear_to_db(before_window)
    db_after  = linear_to_db(after_window)
    mean_abs_change_db = float(np.mean(np.abs(db_after - db_before)))

    score = round(min(mean_abs_change_db / SAR_DB_SCALE, 1.0), 3)
    return score, round(mean_abs_change_db, 3)


# ---- mineral indicator (spectral heuristic, NOT classification) -----------
def load_band_and_transform(path):
    with rasterio.open(path) as src:
        return src.read(1).astype("float64"), src.transform


def read_pixel(arr, transform, lat, lon):
    row, col = rasterio.transform.rowcol(transform, lon, lat)
    if 0 <= row < arr.shape[0] and 0 <= col < arr.shape[1]:
        return float(arr[row, col])
    return None


def mineral_indicator(lat, lon, after_red, after_nir, after_blue, after_swir, transform):
    """iron_oxide_ratio (Red/Blue) and ferrous_mineral_ratio (SWIR/NIR) are
    established band-ratio heuristics from geological remote sensing --
    elevated values CAN indicate exposed iron-bearing rock/soil, but this is
    NOT confirmed mineral identification. True classification needs
    hyperspectral imagery (dozens of narrow, calibrated bands) to resolve
    specific mineral absorption features; multispectral Sentinel-2 bands are
    far too coarse spectrally to do that reliably. Treat as a coarse,
    unverified spectral clue, not evidence."""
    red  = read_pixel(after_red, transform, lat, lon)
    nir  = read_pixel(after_nir, transform, lat, lon)
    blue = read_pixel(after_blue, transform, lat, lon)
    swir = read_pixel(after_swir, transform, lat, lon)
    if None in (red, nir, blue, swir):
        return None

    return {
        "iron_oxide_ratio": round(red / (blue + MINERAL_RATIO_EPS), 4),
        "ferrous_mineral_ratio": round(swir / (nir + MINERAL_RATIO_EPS), 4),
    }


# ---- nighttime-lights delta (output field only -- NOT yet weighted into
# confidence_score or tiers; values need to be reviewed first) -------------
def ntl_delta(lat, lon, before_ntl, before_transform, after_ntl, after_transform):
    """VIIRS Black Marble is ~500m/pixel, far coarser than the Sentinel
    imagery, so many nearby triggers will legitimately share the same NTL
    pixel (and therefore the same ntl_delta) -- that's a resolution
    limitation, not a bug. before/after are read against their own
    transforms since each was reprojected independently, same as the VV
    bands above."""
    before_val = read_pixel(before_ntl, before_transform, lat, lon)
    after_val  = read_pixel(after_ntl, after_transform, lat, lon)
    if before_val is None or after_val is None:
        return None
    return round(after_val - before_val, 3)


# ---- disturbance area (2D area proxy, NOT excavated volume) ---------------
def pixel_area_m2(transform, lat):
    """Raster is reprojected to EPSG:4326 (degrees), so pixel size must be
    converted to meters using the local latitude -- a "square" pixel in
    degrees is NOT square in meters away from the equator."""
    pixel_width_deg  = abs(transform.a)
    pixel_height_deg = abs(transform.e)
    meters_per_deg_lon = EARTH_METERS_PER_DEG_LAT * math.cos(math.radians(lat))
    pixel_width_m  = pixel_width_deg * meters_per_deg_lon
    pixel_height_m = pixel_height_deg * EARTH_METERS_PER_DEG_LAT
    return pixel_width_m * pixel_height_m


# ---- road access (static OSM infrastructure proximity, NOT vehicle activity)
def load_roads():
    """real_data/roads.geojson, extracted locally by extract_roads.py from
    an OSM .pbf (no live API calls). Returns None if the file doesn't
    exist yet -- callers must treat that as "unavailable", not "no roads
    found"; those are different claims."""
    if not os.path.exists(ROADS_FILE):
        return None
    return gpd.read_file(ROADS_FILE)


def nearest_road(lat, lon, roads_gdf):
    """Distance (meters) and highway= type of the nearest road to (lat, lon).

    Degree-to-meter conversion uses the SAME simple local-latitude scaling
    as pixel_area_m2 (not a full metric-CRS reprojection) -- roads_gdf's
    geometry distance() is computed in raw degree space, then scaled by the
    average of the lat/lon meters-per-degree factors at this point. That's
    an approximation, not geodesically exact, but consistent with how
    disturbance_area_m2 already handles this elsewhere in this file.
    """
    if roads_gdf is None or len(roads_gdf) == 0:
        return None, None

    point = Point(lon, lat)
    distances_deg = roads_gdf.geometry.distance(point)
    nearest_idx = distances_deg.idxmin()
    dist_deg = distances_deg.loc[nearest_idx]

    meters_per_deg_lon = EARTH_METERS_PER_DEG_LAT * math.cos(math.radians(lat))
    meters_per_deg = (EARTH_METERS_PER_DEG_LAT + meters_per_deg_lon) / 2
    dist_m = round(dist_deg * meters_per_deg, 1)

    road_type = roads_gdf.loc[nearest_idx].get("highway")
    return dist_m, road_type


def road_access_score(dist_m):
    """1.0 within ROAD_ACCESS_FULL_SCORE_M, linearly down to 0.0 at
    ROAD_ACCESS_ZERO_SCORE_M, 0.0 beyond that.

    This measures STATIC road infrastructure proximity from OpenStreetMap
    -- it says a road exists near this location, nothing about whether any
    vehicle has actually used it recently. Real-time vehicle movement would
    need GPS/RFID-tracked transit-pass data from state e-permit systems
    (mandated under state mining rules for mineral-carrying vehicles) --
    documented future scope, not implemented here.
    """
    if dist_m is None:
        return None
    if dist_m <= ROAD_ACCESS_FULL_SCORE_M:
        return 1.0
    if dist_m >= ROAD_ACCESS_ZERO_SCORE_M:
        return 0.0
    span = ROAD_ACCESS_ZERO_SCORE_M - ROAD_ACCESS_FULL_SCORE_M
    return round(1.0 - (dist_m - ROAD_ACCESS_FULL_SCORE_M) / span, 3)


# ---- MMDR Act legality-determination layer --------------------------------
def load_permits():
    with open(PERMITS_FILE) as f:
        return json.load(f)["leases"]


def find_lease(leases, boundary_status):
    """This mock registry has exactly one lease, so a trigger inside the
    (single) traced lease boundary is that lease; a trigger outside it has
    no matching lease at all. A production system would instead spatially
    match the trigger against each lease's own polygon in a multi-lease
    registry, rather than assuming a single site-wide lease."""
    if boundary_status != "within_lease_expansion" or not leases:
        return None
    return leases[0]


def spatial_check(boundary_status):
    # Reuses detection.py's boundary_status -- derived from the real,
    # hand-traced lease_boundary.geojson, not the mock permit registry.
    return "WITHIN_LEASE" if boundary_status == "within_lease_expansion" else "OUTSIDE_LEASE"


def temporal_check(lease, reference_date_str):
    """reference_date_str is the real satellite observation date (AFTER
    scene acquisition), not trigger["detected_at"] -- that field is a
    pipeline-processing audit timestamp (when detection.py was run), not
    when the imagery was actually captured, and lease validity must be
    checked against the latter."""
    if lease is None:
        return "NO_LEASE_FOUND"
    if lease.get("status") == "SUSPENDED":
        return "SUSPENDED"

    detected_date = parse_date(reference_date_str).date()
    valid_from = parse_date(lease["valid_from"]).date()
    valid_until = parse_date(lease["valid_until"]).date()
    if lease.get("status") == "ACTIVE" and valid_from <= detected_date <= valid_until:
        return "VALID"
    return "LAPSED"


def dispatch_check(lease, reference_date_str):
    """Genuine date-window match: PASSES_PRESENT only if a dated
    transit-pass record with count > 0 falls within DISPATCH_WINDOW_DAYS
    of reference_date_str (the AFTER scene's acquisition date)."""
    if lease is None:
        return "DATA_UNAVAILABLE"

    reference_date = parse_date(reference_date_str).date()
    for record in lease.get("transit_passes", []):
        record_date = parse_date(record["date"]).date()
        if abs((record_date - reference_date).days) <= DISPATCH_WINDOW_DAYS and record.get("count", 0) > 0:
            return "PASSES_PRESENT"
    return "NO_PASSES_FOUND"


def mineral_check():
    """Confirming the extracted mineral matches permitted_mineral still
    requires hyperspectral or calibrated multispectral classification --
    the band-ratio mineral_indicator computed alongside this is only a
    coarse spectral heuristic, not that classification. Always
    HEURISTIC_ESTIMATE, never a real confirm/deny."""
    return "HEURISTIC_ESTIMATE"


def volume_check():
    """PLACEHOLDER: confirming extracted volume against
    approved_annual_volume_tonnes requires DEM-differencing (pre/post
    excavation volumetrics) plus quota reconciliation against dispatch
    records -- out of scope for this prototype. disturbance_area_m2
    (2D area) is computed as an honest proxy, but it is NOT volume.
    Always AREA_PROXY_ONLY, never a real confirm/deny."""
    return "AREA_PROXY_ONLY"


def legality_flag(spatial, temporal, dispatch):
    """Summarizes only the implemented checks (spatial/temporal/dispatch)
    -- mineral_check and volume_check are excluded since they're
    unimplemented placeholders, not real evidence either way."""
    if (spatial == "OUTSIDE_LEASE"
            or temporal in ("LAPSED", "SUSPENDED", "NO_LEASE_FOUND")
            or dispatch == "NO_PASSES_FOUND"):
        return "POTENTIAL_VIOLATION"
    if spatial == "WITHIN_LEASE" and temporal == "VALID" and dispatch == "PASSES_PRESENT":
        return "APPEARS_COMPLIANT"
    return "INSUFFICIENT_DATA"


def assess_legality(trigger, leases, mineral_bands):
    lease = find_lease(leases, trigger["boundary_status"])

    spatial  = spatial_check(trigger["boundary_status"])
    temporal = temporal_check(lease, AFTER_SCENE_DATE)
    dispatch = dispatch_check(lease, AFTER_SCENE_DATE)
    mineral  = mineral_check()
    volume   = volume_check()

    after_red, after_nir, after_blue, after_swir, transform = mineral_bands
    indicator = mineral_indicator(
        trigger["lat"], trigger["lon"], after_red, after_nir, after_blue, after_swir, transform
    )

    return {
        "spatial_check":  {"value": spatial,  "data_source": "REAL"},
        "temporal_check": {"value": temporal, "data_source": "MOCK"},
        "dispatch_check": {"value": dispatch, "data_source": "MOCK"},
        "mineral_check":  {"value": mineral,  "data_source": "DERIVED_FROM_IMAGERY"},
        "mineral_indicator": indicator,
        "volume_check":   {"value": volume,   "data_source": "DERIVED_FROM_IMAGERY"},
    }, legality_flag(spatial, temporal, dispatch)


def score_triggers(triggers):
    before_vv, before_transform = load_vv_band(BEFORE_VV_FILE)
    after_vv, after_transform   = load_vv_band(AFTER_VV_FILE)
    leases = load_permits()
    roads = load_roads()
    if roads is None:
        print(f"  [warn] {ROADS_FILE} not found -- road_access_score, "
              f"nearest_road_distance_m, nearest_road_type will be null "
              f"for all triggers. Run extract_roads.py first.")

    after_red, area_transform = load_band_and_transform(AFTER_RED_FILE)
    after_nir, _  = load_band_and_transform(AFTER_NIR_FILE)
    after_blue, _ = load_band_and_transform(AFTER_BLUE_FILE)
    after_swir, _ = load_band_and_transform(AFTER_SWIR_FILE)
    mineral_bands = (after_red, after_nir, after_blue, after_swir, area_transform)

    before_ntl, before_ntl_transform = load_band_and_transform(BEFORE_NTL_FILE)
    after_ntl, after_ntl_transform   = load_band_and_transform(AFTER_NTL_FILE)

    for t in triggers:
        sar_score, mean_abs_change_db = sar_change_score(
            t["lat"], t["lon"], before_vv, before_transform, after_vv, after_transform
        )

        dist_m, road_type = nearest_road(t["lat"], t["lon"], roads)
        t["nearest_road_distance_m"] = dist_m
        t["nearest_road_type"] = road_type
        t["road_access_score"] = road_access_score(dist_m)
        t["sar_change_score"] = sar_score
        t["sar_mean_abs_change_db"] = mean_abs_change_db

        if sar_score is None:
            t["confidence_score"] = None
            t["confidence_tier"] = SAR_UNAVAILABLE_TIER
        else:
            t["confidence_score"] = round(
                CHANGE_PCT_WEIGHT * (t["change_pct"] / 100) + SAR_SCORE_WEIGHT * sar_score,
                3,
            )
            t["confidence_tier"] = confidence_tier(t["confidence_score"])

        t["disturbance_area_m2"] = round(t["area_px"] * pixel_area_m2(area_transform, t["lat"]), 1)
        t["ntl_delta"] = ntl_delta(
            t["lat"], t["lon"], before_ntl, before_ntl_transform, after_ntl, after_ntl_transform
        )
        t["legality_assessment"], t["legality_flag"] = assess_legality(t, leases, mineral_bands)
        t["status"] = "PENDING_REVIEW"

        # Operational SLA Time Deadline based on MMDR enforcement severity:
        # Tier 1 (Urgent): POTENTIAL_VIOLATION with High Risk / Significant Canopy Drop -> 24 Hours SLA
        # Tier 2 (Standard): POTENTIAL_VIOLATION -> 48 Hours SLA
        # Tier 3 (Routine): APPEARS_COMPLIANT -> 72 Hours SLA
        conf = t.get("confidence_score") or 0
        chg = t.get("change_pct") or 0
        if t["legality_flag"] == "POTENTIAL_VIOLATION" and (conf >= 0.75 or chg >= 50.0):
            sla_h = 24
        elif t["legality_flag"] == "POTENTIAL_VIOLATION":
            sla_h = 48
        else:
            sla_h = 72

        t["sla_hours"] = sla_h
        t["sla_deadline"] = (datetime.utcnow() + timedelta(hours=sla_h)).isoformat()
    return triggers



def main():
    import argparse
    ap = argparse.ArgumentParser(description="BhuNetra trigger scoring / signal fusion")
    ap.add_argument("--aoi", default=DEFAULT_AOI,
                    help=f"AOI id from pipeline/aois.py (default: {DEFAULT_AOI})")
    ap.add_argument("--in", dest="in_file", default=None, help="input triggers JSON")
    ap.add_argument("--out", dest="out_file", default=None, help="output scored JSON")
    args = ap.parse_args()

    global TRIGGERS_FILE, OUTPUT_FILE
    if args.aoi != DEFAULT_AOI:
        set_aoi(args.aoi)
    if args.in_file:
        TRIGGERS_FILE = args.in_file
    if args.out_file:
        OUTPUT_FILE = args.out_file

    triggers = load_triggers()
    scored = score_triggers(triggers)

    with open(OUTPUT_FILE, "w") as f:
        json.dump(scored, f, indent=2)

    unavailable = [t for t in scored if t["sar_change_score"] is None]
    available   = [t for t in scored if t["sar_change_score"] is not None]

    print("\nScored triggers (highest confidence first):")
    print("-" * 170)
    for t in sorted(available, key=lambda t: t["confidence_score"], reverse=True):
        la = t["legality_assessment"]
        mi = la["mineral_indicator"]
        mi_str = (f"iron_oxide={mi['iron_oxide_ratio']:.3f} ferrous={mi['ferrous_mineral_ratio']:.3f}"
                  if mi else "unavailable")
        print(f"  {t['trigger_id']}  |  change={t['change_pct']:5.1f}%  |  "
              f"sar_change={t['sar_change_score']:.3f} ({t['sar_mean_abs_change_db']:.2f}dB)  |  "
              f"confidence={t['confidence_score']:.3f}  |  "
              f"tier={t['confidence_tier']:16s}  |  "
              f"spatial={la['spatial_check']['value']:13s}  "
              f"temporal={la['temporal_check']['value']:13s}  "
              f"dispatch={la['dispatch_check']['value']:15s}  |  "
              f"legality={t['legality_flag']:19s}  |  "
              f"area={t['disturbance_area_m2']:7.1f}m2  |  {mi_str}  |  "
              f"ntl_delta={t['ntl_delta']}  |  "
              f"road={t['nearest_road_distance_m']}m ({t['nearest_road_type']}, "
              f"access={t['road_access_score']})")

    if unavailable:
        print(f"\n{len(unavailable)} trigger(s) with SAR_UNAVAILABLE "
              f"(window not fully covered by valid SAR data in both scenes):")
        for t in unavailable:
            print(f"  {t['trigger_id']}  |  change={t['change_pct']:5.1f}%  |  "
                  f"({t['lat']}, {t['lon']})")

    print(f"\nWritten to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
