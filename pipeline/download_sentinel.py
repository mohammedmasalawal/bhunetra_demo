"""
BhuNetra — Real Sentinel-2 L2A downloader (Copernicus Data Space Ecosystem)

Downloads Band 4 (Red), Band 8 (NIR), Band 2 (Blue) and Band 11 (SWIR,
resampled 20m->10m bilinear), cloud-filtered, for a BEFORE and an AFTER
date range, and saves them as GeoTIFFs under real_data/ using the exact
filenames detection.py / score_triggers.py expect. Point detection.py's
BEFORE_RED / BEFORE_NIR / AFTER_RED / AFTER_NIR at these files (see
README) to run the pipeline on real imagery instead of the synthetic
demo data. Blue/SWIR feed score_triggers.py's mineral_indicator heuristic.

Auth: logs in via openEO's default authenticate_oidc() flow, which on a
headless/non-browser session falls back to the OIDC device-code grant --
it prints a verification URL + short code, you approve it in a browser,
and the call unblocks once approved. No stored credentials needed for
login itself.

CRS note: Sentinel-2 L2A's native CRS is UTM, but detection.py's lease-
boundary check assumes the raster's transform is in WGS84 lon/lat (to
match sample_data/lease_boundaries.geojson). This script reprojects each
band to EPSG:4326 on download so the output drops straight into the
existing pipeline.
"""
import os
from datetime import datetime, timedelta
from pathlib import Path

import openeo
import rasterio
import requests
from dateutil.parser import parse as parse_date
from dotenv import load_dotenv

try:
    from aois import get_aoi, data_dir, DEFAULT_AOI
except ImportError:  # imported as a package (pipeline.download_sentinel)
    from pipeline.aois import get_aoi, data_dir, DEFAULT_AOI

# ---- config -------------------------------------------------------------
# BBOX / MAX_CLOUD_COVER / OUTPUT_DIR default to the Bailadila AOI so old
# callers keep working unchanged; set_aoi() repoints them for any other
# region from the pipeline/aois.py registry. Entry points call set_aoi()
# (or the BHUNETRA_AOI env var, honoured at import) before downloading.
BBOX = {"west": 81.22, "south": 18.65, "east": 81.245, "north": 18.67}
# Actual pixel size (degrees) of the 10m bands (B02/B04/B08) once reprojected
# to EPSG:4326 at this bbox/latitude -- measured from a downloaded band's
# transform, not a nominal "10m" converted by a fixed constant, since
# reprojection to a geographic CRS doesn't yield exactly-square meter pixels.
TEN_METER_PIXEL_DEG = 9.311494714577639e-05
BEFORE_DATE_RANGE = ("2020-01-01", "2020-02-28")
AFTER_DATE_RANGE  = ("2024-01-01", "2024-02-28")
MAX_CLOUD_COVER   = 20   # percent

OPENEO_BACKEND = "https://openeo.dataspace.copernicus.eu"
COLLECTION     = "SENTINEL2_L2A"
S1_COLLECTION  = "SENTINEL1_GRD"
OUTPUT_DIR     = Path("real_data")

CURRENT_AOI = DEFAULT_AOI


def set_aoi(aoi_id: str) -> dict:
    """Repoint BBOX / MAX_CLOUD_COVER / OUTPUT_DIR at another region from
    the pipeline/aois.py registry. Returns the AOI config dict."""
    global BBOX, MAX_CLOUD_COVER, OUTPUT_DIR, CURRENT_AOI
    cfg = get_aoi(aoi_id)
    BBOX = dict(cfg["bbox"])
    MAX_CLOUD_COVER = cfg.get("max_cloud_cover", MAX_CLOUD_COVER)
    OUTPUT_DIR = Path(data_dir(aoi_id))
    CURRENT_AOI = aoi_id
    return cfg


if os.getenv("BHUNETRA_AOI") and os.getenv("BHUNETRA_AOI") != DEFAULT_AOI:
    set_aoi(os.environ["BHUNETRA_AOI"])

# CDSE's public product catalog (OData) -- read-only metadata search, no
# auth, no pixel data. Used only to discover which sat:orbit_state /
# sat:relative_orbit combination actually exists for this bbox before
# asking openEO for pixels, so before/after SAR scenes share viewing
# geometry (same track). ascending is tried first, falling back to
# descending only if ascending has no coverage for BOTH date ranges.
ODATA_PRODUCTS_URL   = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
ORBIT_STATE_CANDIDATES = ("ascending", "descending")

# ---- helpers --------------------------------------------------------------
def connect():
    connection = openeo.connect(OPENEO_BACKEND)
    connection.authenticate_oidc()
    return connection


def connect_service_account():
    """Non-interactive auth for unattended runs (CI, live_monitor.py) --
    requires CDSE_CLIENT_ID / CDSE_CLIENT_SECRET env vars. Get these once,
    yourself, at https://shapps.dataspace.copernicus.eu/dashboard (self-
    service OAuth client registration, ~5 min, no support ticket needed).
    This is a documented, real feature (openEO's client-credentials grant),
    not something invented for this project -- see
    https://documentation.dataspace.copernicus.eu/APIs/openEO/authentication/client_credentials.html
    """
    load_dotenv()
    client_id = os.environ.get("CDSE_CLIENT_ID")
    client_secret = os.environ.get("CDSE_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError(
            "CDSE_CLIENT_ID and CDSE_CLIENT_SECRET must be set for non-interactive "
            "auth. Register a service account at "
            "https://shapps.dataspace.copernicus.eu/dashboard and set these as "
            "environment variables (or GitHub Actions secrets)."
        )
    connection = openeo.connect(OPENEO_BACKEND)
    connection.authenticate_oidc_client_credentials(client_id=client_id, client_secret=client_secret)
    return connection


def find_scene_date(connection, date_range, collection=COLLECTION, bands=("B04",), properties=None):
    """Return the earliest acquisition date in date_range that matches the
    given properties filter, using a cheap dimension_labels lookup (no pixel
    data pulled yet)."""
    cube = connection.load_collection(
        collection,
        spatial_extent=BBOX,
        temporal_extent=date_range,
        bands=list(bands),
        properties=properties or {},
    )
    dates = cube.dimension_labels("t").execute()
    if not dates:
        raise RuntimeError(
            f"No {collection} scenes found in {date_range} over the given bbox "
            f"(properties={properties}) -- widen the date range or filter."
        )

    # dimension_labels() format isn't guaranteed to be plain ISO 8601 (seen:
    # RFC-1123-style "Fri, 31 Jan 2020 ..."), so parse each entry into a real
    # datetime before sorting -- sorting the raw strings would order them
    # lexicographically, not chronologically -- then normalize to YYYY-MM-DD.
    parsed_dates = sorted(parse_date(str(d)) for d in dates)
    scene_date = parsed_dates[0].strftime("%Y-%m-%d")
    print(f"  [find_scene_date:{collection}] raw dimension_labels()[0]: {dates[0]!r} "
          f"(type={type(dates[0]).__name__}) -> normalized {scene_date!r}")
    return scene_date


def download_band(connection, band, scene_date, out_path, collection=COLLECTION,
                   properties=None, resolution=0, method=None):
    day_after = (
        datetime.strptime(scene_date, "%Y-%m-%d") + timedelta(days=1)
    ).strftime("%Y-%m-%d")

    cube = connection.load_collection(
        collection,
        spatial_extent=BBOX,
        temporal_extent=[scene_date, day_after],
        bands=[band],
        properties=properties or {},
    )
    cube = cube.reduce_dimension(dimension="t", reducer="first")
    # Only pass method= when actually resampling (e.g. B11 20m->10m) --
    # passing it unconditionally (even "near" at resolution=0, a no-op
    # resample) triggers a NaN/IllegalArgumentException on CDSE's backend.
    resample_kwargs = {"resolution": resolution, "projection": "EPSG:4326"}
    if method is not None:
        resample_kwargs["method"] = method
    cube = cube.resample_spatial(**resample_kwargs)
    cube.download(str(out_path), format="GTiff")


def report(out_path, scene_date):
    with rasterio.open(out_path) as src:
        print(
            f"  {out_path}  |  acquired {scene_date}  |  "
            f"{src.width}x{src.height} px  |  CRS {src.crs}"
        )


def download_pair(connection, label, date_range):
    scene_date = find_scene_date(
        connection, date_range,
        properties={"eo:cloud_cover": lambda c: c <= MAX_CLOUD_COVER},
    )
    print(f"\n{label}: using scene from {scene_date}")
    cloud_filter = {"eo:cloud_cover": lambda c: c <= MAX_CLOUD_COVER}

    red_path   = OUTPUT_DIR / f"{label}_red.tif"
    nir_path   = OUTPUT_DIR / f"{label}_nir.tif"
    blue_path  = OUTPUT_DIR / f"{label}_blue.tif"
    swir_path  = OUTPUT_DIR / f"{label}_swir.tif"

    download_band(connection, "B04", scene_date, red_path, properties=cloud_filter)
    download_band(connection, "B08", scene_date, nir_path, properties=cloud_filter)
    download_band(connection, "B02", scene_date, blue_path, properties=cloud_filter)
    # B11 is natively 20m -- explicitly resample to match the 10m bands'
    # pixel grid (bilinear). resample_spatial's resolution= is in the
    # TARGET projection's units -- EPSG:4326 is degrees, not meters, so
    # this must be the 10m bands' actual degree pixel size (confirmed from
    # a downloaded 10m band's transform), not the literal value "10".
    download_band(
        connection, "B11", scene_date, swir_path,
        properties=cloud_filter, resolution=TEN_METER_PIXEL_DEG, method="bilinear",
    )

    report(red_path, scene_date)
    report(nir_path, scene_date)
    report(blue_path, scene_date)
    report(swir_path, scene_date)


def bbox_polygon_wkt(bbox):
    w, s, e, n = bbox["west"], bbox["south"], bbox["east"], bbox["north"]
    return f"POLYGON(({w} {s}, {e} {s}, {e} {n}, {w} {n}, {w} {s}))"


def query_s1_grd_scenes(date_range):
    """List (orbit_state, relative_orbit) for every Sentinel-1 GRDH scene
    covering BBOX within date_range, via CDSE's OData product catalog."""
    date_from, date_to = date_range
    filter_str = (
        "Collection/Name eq 'SENTINEL-1' and "
        f"ContentDate/Start gt {date_from}T00:00:00.000Z and "
        f"ContentDate/Start lt {date_to}T00:00:00.000Z and "
        f"OData.CSC.Intersects(area=geography'SRID=4326;{bbox_polygon_wkt(BBOX)}')"
    )
    resp = requests.get(
        ODATA_PRODUCTS_URL,
        params={"$filter": filter_str, "$expand": "Attributes", "$top": 50},
        timeout=60,
    )
    resp.raise_for_status()

    scenes = []
    for p in resp.json().get("value", []):
        if "GRDH" not in p.get("Name", ""):
            continue
        attrs = {a["Name"]: a["Value"] for a in p.get("Attributes", [])}
        orbit_state = (attrs.get("orbitDirection") or "").lower()
        rel_orbit = attrs.get("relativeOrbitNumber")
        if orbit_state and rel_orbit is not None:
            scenes.append((orbit_state, int(rel_orbit)))
    return scenes


def pick_orbit_config(before_range, after_range):
    """Pick an orbit_state common to both date ranges (ascending preferred,
    descending as fallback), then a relative_orbit common to both under
    that state -- so before/after SAR scenes share the same track."""
    before_scenes = query_s1_grd_scenes(before_range)
    after_scenes  = query_s1_grd_scenes(after_range)

    for state in ORBIT_STATE_CANDIDATES:
        before_orbits = {ro for os_, ro in before_scenes if os_ == state}
        after_orbits  = {ro for os_, ro in after_scenes if os_ == state}
        common = before_orbits & after_orbits
        if common:
            relative_orbit = sorted(common)[0]
            print(f"  [pick_orbit_config] orbit_state={state!r}, "
                  f"relative_orbit={relative_orbit} (common to both date ranges)")
            return state, relative_orbit

    raise RuntimeError(
        "No sat:orbit_state + relative_orbit combination is common to both "
        f"BEFORE and AFTER over this bbox. before scenes: {before_scenes}; "
        f"after scenes: {after_scenes}"
    )


def download_vv(connection, label, date_range, orbit_state, relative_orbit):
    """Sentinel-1 GRD VV band -- SAR isn't affected by clouds, so no
    cloud-cover property filter applies here. orbit_state/relative_orbit
    are fixed by pick_orbit_config() so before/after share viewing geometry."""
    properties = {
        "sat:orbit_state": lambda c: c.eq(orbit_state),
        "sat:relative_orbit": lambda c: c.eq(relative_orbit),
    }
    scene_date = find_scene_date(
        connection, date_range,
        collection=S1_COLLECTION, bands=("VV",),
        properties=properties,
    )
    print(f"\n{label} (SAR): using scene from {scene_date} "
          f"[orbit_state={orbit_state}, relative_orbit={relative_orbit}]")

    vv_path = OUTPUT_DIR / f"{label}_vv.tif"
    download_band(
        connection, "VV", scene_date, vv_path,
        collection=S1_COLLECTION,
        properties=properties,
    )
    report(vv_path, scene_date)


# ---- main -----------------------------------------------------------------
if __name__ == "__main__":
    OUTPUT_DIR.mkdir(exist_ok=True)
    conn = connect()

    print("Downloading BEFORE / AFTER Sentinel-2 L2A imagery...")
    download_pair(conn, "before", BEFORE_DATE_RANGE)
    download_pair(conn, "after", AFTER_DATE_RANGE)

    print("\nDownloading BEFORE / AFTER Sentinel-1 GRD (VV) imagery...")
    orbit_state, relative_orbit = pick_orbit_config(BEFORE_DATE_RANGE, AFTER_DATE_RANGE)
    download_vv(conn, "before", BEFORE_DATE_RANGE, orbit_state, relative_orbit)
    download_vv(conn, "after", AFTER_DATE_RANGE, orbit_state, relative_orbit)

    print(f"\nDone -- files written to {OUTPUT_DIR}/. Point detection.py's "
          f"BEFORE_RED/BEFORE_NIR/AFTER_RED/AFTER_NIR at these paths to "
          f"run the pipeline on real imagery.")
