"""
BhuNetra — Detection Engine (Member 1)

Pipeline:
  1. Load before/after Red + NIR bands
  2. Compute NDVI for each date
  3. Diff the two NDVI rasters -> flag vegetation-loss regions
  4. Cluster changed pixels into discrete blobs (connected components)
  5. Convert each blob's centroid to real lat/lon
  6. Check each blob against the lease-boundary polygon
  7. Emit trigger objects matching the team's agreed JSON contract

Real-data swap-in: point RED/NIR paths below at your real Sentinel-2 band
files (B04 = red, B08 = nir). Everything downstream is unchanged.
"""
import hashlib
import json
from datetime import datetime, timezone

import numpy as np
import cv2
import rasterio
import geopandas as gpd
from shapely.geometry import Point

try:
    from aois import get_aoi, data_dir, DEFAULT_AOI
except ImportError:  # imported as a package (pipeline.detection)
    from pipeline.aois import get_aoi, data_dir, DEFAULT_AOI

# ---- config -----------------------------------------------------------
# These default to the Bailadila AOI. run_detection() reads them as module
# globals at call time, so they can be overridden from outside (the README
# documents this) -- and set_aoi() below repoints all of them at once from
# the pipeline/aois.py registry.
BEFORE_RED = "real_data/before_red.tif"
BEFORE_NIR = "real_data/before_nir.tif"
AFTER_RED  = "real_data/after_red.tif"
AFTER_NIR  = "real_data/after_nir.tif"
# Hand-traced from satellite imagery for prototype purposes; would be
# replaced by official IBM/state DMG lease shapefiles in production.
LEASE_FILE = "real_data/lease_boundary.geojson"

# lease_boundary.geojson is traced for the Bailadila site and is
# meaningless for any other AOI (e.g. Singrauli) -- set_aoi() sets this to
# False for any region whose registry entry has lease_boundary_valid=False.
LEASE_BOUNDARY_VALID_FOR_SITE = True

NDVI_DROP_THRESHOLD = 0.25   # flag pixels where NDVI fell by more than this
MIN_BLOB_AREA_PX    = 20     # discard change regions smaller than this (noise)
SITE_ID             = "AOI-07-BAILADILA"


def set_aoi(aoi_id: str) -> dict:
    """Repoint the band paths, lease config and SITE_ID at another region
    from the pipeline/aois.py registry. Returns the AOI config dict."""
    global BEFORE_RED, BEFORE_NIR, AFTER_RED, AFTER_NIR
    global LEASE_FILE, LEASE_BOUNDARY_VALID_FOR_SITE, SITE_ID
    cfg = get_aoi(aoi_id)
    d = data_dir(aoi_id)
    BEFORE_RED = f"{d}/before_red.tif"
    BEFORE_NIR = f"{d}/before_nir.tif"
    AFTER_RED  = f"{d}/after_red.tif"
    AFTER_NIR  = f"{d}/after_nir.tif"
    LEASE_FILE = cfg.get("lease_file") or LEASE_FILE
    LEASE_BOUNDARY_VALID_FOR_SITE = bool(cfg.get("lease_boundary_valid"))
    SITE_ID = aoi_id
    return cfg


# ---- helpers ------------------------------------------------------------
def load_band(path):
    with rasterio.open(path) as src:
        return src.read(1).astype("float32"), src.transform

def compute_ndvi(red, nir):
    # standard NDVI formula; epsilon avoids divide-by-zero on flat pixels
    return (nir - red) / (nir + red + 1e-6)

def pixel_to_lonlat(transform, row, col):
    lon, lat = rasterio.transform.xy(transform, row, col)
    return lon, lat

# ---- pipeline ------------------------------------------------------------
def run_detection(aoi_id: str | None = None):
    """Run NDVI change detection. If aoi_id is given, set_aoi() is called
    first to point the band paths / lease config at that region; otherwise
    the current module globals are used unchanged (back-compat)."""
    if aoi_id:
        set_aoi(aoi_id)
    before_red, transform = load_band(BEFORE_RED)
    before_nir, _ = load_band(BEFORE_NIR)
    after_red, _  = load_band(AFTER_RED)
    after_nir, _  = load_band(AFTER_NIR)

    ndvi_before = compute_ndvi(before_red, before_nir)
    ndvi_after  = compute_ndvi(after_red, after_nir)
    ndvi_drop   = ndvi_before - ndvi_after   # positive = vegetation loss

    # binary change mask
    change_mask = (ndvi_drop > NDVI_DROP_THRESHOLD).astype("uint8")

    # cluster into discrete blobs (this is what turns a noisy pixel mask
    # into distinct "candidate trigger" regions)
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(
        change_mask, connectivity=8
    )

    lease_gdf = gpd.read_file(LEASE_FILE) if LEASE_BOUNDARY_VALID_FOR_SITE else None

    triggers = []
    for label_id in range(1, num_labels):  # 0 = background
        area = stats[label_id, cv2.CC_STAT_AREA]
        if area < MIN_BLOB_AREA_PX:
            continue

        cx, cy = centroids[label_id]          # pixel-space centroid (col, row)
        lon, lat = pixel_to_lonlat(transform, cy, cx)

        blob_mask = labels == label_id
        mean_drop = float(ndvi_drop[blob_mask].mean())
        change_pct = round(mean_drop * 100, 1)

        if LEASE_BOUNDARY_VALID_FOR_SITE:
            point = Point(lon, lat)
            inside_lease = lease_gdf.contains(point).any()
            boundary_status = "within_lease_expansion" if inside_lease else "boundary_violation"
        else:
            boundary_status = "NOT_EVALUATED_NO_LEASE_BOUNDARY_FOR_SITE"

        px_row, px_col = int(round(cy)), int(round(cx))
        id_seed = f"{SITE_ID}-{px_row}-{px_col}"

        trigger = {
            "trigger_id": "MSS-" + hashlib.md5(id_seed.encode()).hexdigest()[:6].upper(),
            "site_id": SITE_ID,
            "lat": round(lat, 5),
            "lon": round(lon, 5),
            "change_pct": change_pct,
            "area_px": int(area),
            "boundary_status": boundary_status,
            "detected_at": datetime.now(timezone.utc).isoformat(),
            "source": "Sentinel-2 NDVI change detection (synthetic demo data)",
            "status": "PENDING_SCORING",   # Member 2's scoring stage picks this up next
        }
        triggers.append(trigger)

    return triggers


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="BhuNetra NDVI change detection")
    ap.add_argument("--aoi", default=DEFAULT_AOI,
                    help=f"AOI id from pipeline/aois.py (default: {DEFAULT_AOI})")
    ap.add_argument("--out", default="output/triggers.json",
                    help="output path (default: output/triggers.json)")
    args = ap.parse_args()

    try:
        get_aoi(args.aoi)
    except KeyError as e:
        raise SystemExit(str(e))

    results = run_detection(aoi_id=args.aoi if args.aoi != DEFAULT_AOI else None)
    with open(args.out, "w") as f:
        json.dump(results, f, indent=2)

    print(f"Detected {len(results)} candidate trigger(s) for {args.aoi}:\n")
    for t in results:
        print(f"  {t['trigger_id']}  |  {t['boundary_status']:24s}  |  "
              f"change={t['change_pct']}%  |  ({t['lat']}, {t['lon']})")
    print(f"\nWritten to {args.out} -- hand this off to Member 2's scoring script.")
