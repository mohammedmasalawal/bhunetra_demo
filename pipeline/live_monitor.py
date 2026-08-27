"""
BhuNetra -- live Sentinel-2/1 monitoring loop.

Meant to run on a schedule (see .github/workflows/live-monitor.yml, daily
cron -- cheap no-op most days). Each run:

  1. Looks for a Sentinel-2 L2A scene newer than the last one this script
     successfully processed (tracked in real_data_live/state.json),
     cloud-filtered to <= MAX_CLOUD_COVER.
  2. If nothing new/usable yet, prints why and exits 0 -- NOT an error.
     Sentinel-2's real revisit is ~5 days at this latitude, and clouds can
     make even a fresh capture useless (see monsoon_comparison.py for a
     97%-cloud real example) -- a "nothing to do today" exit is the
     expected common case, not a failure.
  3. If found: downloads optical (red/nir/blue/swir) + SAR (VV) bands into
     real_data_live/<date>/, runs the SAME NDVI detection detection.py
     uses (imported, not reimplemented) comparing against the PREVIOUS
     processed scene (or the original validated real_data/ 2020-01 scene,
     on the very first live run -- so cycle 1 produces real output instead
     of a wasted bootstrap pass), adds SAR corroboration via
     score_triggers.py's sar_change_score() (imported, not reimplemented),
     and writes output/triggers_live.json.
  4. Updates state.json so next run's "previous" is this run's "new".

SCOPE -- deliberately NOT everything score_triggers.py does, picked signal
by signal rather than wired in wholesale:
  - change_pct (NDVI), sar_change_score/sar_mean_abs_change_db, road_access_score/
    nearest_road_distance_m/nearest_road_type, disturbance_area_m2, and a
    confidence_score/confidence_tier blend -- INCLUDED. Reuses
    score_triggers.py's own functions and calibrated weights directly
    (imported, not reimplemented), so a live trigger's confidence_score is
    computed exactly the same way a static-pipeline one is.
  - ntl_delta -- INCLUDED, but on its OWN cadence, not the 5-day one.
    Correction from an earlier draft of this docstring: this project's NTL
    source is VNP46A4, the ANNUAL Black Marble composite (see
    download_nightlights.py's own docstring -- deliberately annual, to
    average out cloud/moonlight/seasonal noise), not monthly. Re-fetching
    it every 5-day cycle would be pointless (nothing new exists that
    often) AND there's real processing lag (a given year's annual
    composite isn't produced until well after that year ends) -- so
    ensure_ntl() checks once per run whether a newer *fully-elapsed* year's
    composite is available (target = today's year minus 1) and only
    downloads when the target year advances past what's cached, tracked in
    state.json's ntl_year_fetched, independent of last_scene_date. The
    "before" side stays fixed at the original 2020 baseline
    (real_data/before_ntl.tif) permanently -- that's a legitimate
    historical anchor, not something that needs refreshing. Every trigger
    carries ntl_before_year/ntl_after_year explicitly so nobody downstream
    mistakes an annual average for "tonight's lights." Needs
    EARTHDATA_TOKEN (same one download_nightlights.py uses) -- if unset,
    NTL fields come back null with a clear warning, the rest of the cycle
    still runs (NTL is additive, not a hard dependency).
  - legality_assessment/legality_flag -- NOT computed from real_data/mock_permits.json,
    on purpose. That file is explicitly mock data (score_triggers.py's own
    docstring says so), and an unattended loop with no human-review gate
    is exactly where a fabricated-looking "legal determination" is most
    dangerous -- it would look authoritative to whoever reads it
    downstream. Roads and SAR are real, time-invariant-or-live signals;
    permits here are not, so they don't get the same treatment.
    HOWEVER: these fields can't just be omitted or null -- confirmed via a
    real 422 from the live API that main.py's TriggerPayload declares them
    as required str/dict (stricter than the nullable DB column). Set to
    "INSUFFICIENT_DATA" (the SAME value main.py's own site_legality_flag()
    already uses for "no legality_flag recorded" -- reusing an existing
    convention, not inventing one) plus a plain explanatory note dict,
    confirmed safe against build_brief_prompt() (which only renders
    {"value":..., "data_source":...}-shaped entries, silently skipping
    anything else).
status is set to "PENDING_REVIEW" (same as score_triggers.py) since a
NDVI+SAR+road-corroborated trigger is genuinely ready for a human officer
to look at -- the honest "no legality check run" label doesn't block that.
output/triggers_live.json is a superset-compatible schema, not a
byte-identical replacement for output/triggers_scored.json.

SEASONALITY CAVEAT, MEASURED NOT ASSUMED: the original real_data/ pair was
deliberately both Januaries (2020-01 vs 2024-01) specifically to control
for seasonal NDVI variation. This script's steady-state comparisons (each
new capture vs the immediately-previous one, ~5 days apart) stay
same-season automatically. The ONE exception is the bootstrap cycle
(comparing the fixed 2024-01-05 baseline against whatever season the first
live capture happens to land in) -- tested this directly using
real_data_2026/'s 2026-06 scene as a stand-in and got 25 triggers, notably
more than the 14 a same-month-controlled 2020-vs-2026 comparison produced
-- consistent with some of that being January-vs-June seasonal vegetation
change, not mining disturbance. Treat the FIRST live cycle's output with
extra skepticism; subsequent cycles shouldn't have this problem.

Auth: non-interactive service-account credentials (CDSE_CLIENT_ID /
CDSE_CLIENT_SECRET env vars) via download_sentinel.connect_service_account()
-- required because this runs unattended. See that function's docstring
for the one-time setup step (a human does this once, not per-run).

MAP INTEGRATION (--live flag): by default this script only writes
output/triggers_live.json locally -- it does NOT touch the real API or
database unless you pass --live. With --live, after writing the local
file it also:
  5. POSTs each trigger to the real backend (reusing seed_backend.py's own
     build_payload()/endpoint -- not reimplemented, so a live-ingested
     trigger is built exactly the same way a static-pipeline one is). A
     409 (already ingested) is treated as success, same convention as
     seed_backend.py.
  6. Re-runs db/cluster_sites.py (DBSCAN) afterward. This is NOT optional
     cosmetics -- confirmed by reading MapView.tsx directly: a freshly
     ingested alert has cluster_id=NULL, and the map only renders a site
     if it has a cluster_id, and only renders an individual alert once a
     site is selected by matching that same cluster_id. Skipping this step
     means a real, correctly-stored alert that is simply invisible on the
     map -- re-clustering is what makes it actually show up.
"""
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

import download_sentinel as ds
import detection as d
import score_triggers as st
import seed_backend as sb

try:
    from aois import get_aoi, data_dir, DEFAULT_AOI
except ImportError:
    from pipeline.aois import get_aoi, data_dir, DEFAULT_AOI

# --- per-AOI paths (defaults = Bailadila; _configure_for_aoi() repoints) --
AOI_ID = DEFAULT_AOI
STATE_FILE = Path("real_data_live/state.json")
LIVE_DIR = Path("real_data_live")
OUTPUT_FILE = Path("output/triggers_live.json")
NTL_DIR = Path("real_data_live/ntl")

# The 2020 baseline NTL composite -- fixed forever, a legitimate historical
# anchor, not something that needs (or should) refresh.
NTL_BEFORE_FILE = "real_data/before_ntl.tif"
NTL_BEFORE_YEAR = 2020


def _configure_for_aoi(aoi_id: str):
    """Point every per-AOI path at `aoi_id`'s working dir and repoint the
    download_sentinel bbox. AOI-07-BAILADILA keeps its historical
    real_data_live/ layout so nothing that already exists breaks; other
    AOIs get real_data_<aoi>_live/. Their first run needs a
    real_data_<aoi>/before_*.tif baseline seeded (same idea as the lease
    file) -- see README 'Onboarding a new region'."""
    global AOI_ID, STATE_FILE, LIVE_DIR, OUTPUT_FILE, NTL_DIR
    global NTL_BEFORE_FILE, BOOTSTRAP_BASELINE
    AOI_ID = aoi_id
    ds.set_aoi(aoi_id)
    if aoi_id == DEFAULT_AOI:
        return
    base = data_dir(aoi_id)                 # e.g. real_data_aoi_korba_coalfield
    live = f"{base}_live"
    LIVE_DIR = Path(live)
    STATE_FILE = Path(f"{live}/state.json")
    NTL_DIR = Path(f"{live}/ntl")
    OUTPUT_FILE = Path(f"output/triggers_{aoi_id.lower().replace('-', '_')}_live.json")
    NTL_BEFORE_FILE = f"{base}/before_ntl.tif"
    BOOTSTRAP_BASELINE = {
        "red": f"{base}/before_red.tif",
        "nir": f"{base}/before_nir.tif",
        "blue": f"{base}/before_blue.tif",
        "vv": f"{base}/before_vv.tif",
        "date": "2020-01-05",
    }

# How far back to look for a scene on the very first run (before any state
# exists) -- generous, since we don't know the last real capture date yet.
BOOTSTRAP_LOOKBACK_DAYS = 30

# Original validated pair's "after" scene -- used as the "before" baseline
# for the live pipeline's very first comparison, so cycle 1 produces real
# output instead of "no previous scene yet, skipping."
BOOTSTRAP_BASELINE = {
    "red": "real_data/after_red.tif",
    "nir": "real_data/after_nir.tif",
    "blue": "real_data/after_blue.tif",
    "vv": "real_data/after_vv.tif",
    "date": "2024-01-05",  # score_triggers.py's AFTER_SCENE_DATE for real_data/
}


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return None


def save_state(state):
    LIVE_DIR.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def find_latest_scene_date(connection, since_date, cloud_filter):
    """Like download_sentinel.find_scene_date, but returns the LATEST
    match in [since_date+1day, today], not the earliest -- we want the
    freshest usable capture, not the oldest one in a fixed window.

    CDSE's backend raises OpenEoApiError(code="NoDataAvailable") for a
    genuinely empty result, rather than returning an empty dimension_labels
    list (confirmed via a real live run, during India's monsoon season --
    plausibly zero cloud_cover<=20% scenes existed in the queried window,
    exactly the "clouds make it unusable" case this whole design expects).
    Caught here and treated the same as "nothing found"; any OTHER API
    error code is re-raised, not swallowed."""
    today = date.today().isoformat()
    start = (datetime.strptime(since_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    if start > today:
        return None

    from openeo.rest import OpenEoApiError
    cube = connection.load_collection(
        ds.COLLECTION, spatial_extent=ds.BBOX, temporal_extent=[start, today],
        bands=["B04"], properties=cloud_filter,
    )
    try:
        dates = cube.dimension_labels("t").execute()
    except OpenEoApiError as e:
        if e.code == "NoDataAvailable":
            return None
        raise
    if not dates:
        return None
    from dateutil.parser import parse as parse_date
    latest = max(parse_date(str(dd)) for dd in dates)
    return latest.strftime("%Y-%m-%d")


def ensure_ntl(state):
    """Returns (after_ntl_path, after_ntl_year) for the most recent FULLY
    ELAPSED year's VNP46A4 annual composite, fetching a new one only if the
    target year has advanced past what's cached in state.json. Runs on its
    own yearly cadence, independent of the 5-day optical/SAR loop -- see
    the module docstring's ntl_delta entry for why (annual composite,
    real processing lag, no point checking more often than this).

    Returns (None, None) if EARTHDATA_TOKEN isn't set or the fetch fails --
    NTL is additive, not required for the rest of the cycle to run.
    """
    target_year = date.today().year - 1  # this year's composite won't exist yet
    cached_year = (state or {}).get("ntl_year_fetched")
    cached_path = (state or {}).get("ntl_path")

    if cached_year is not None and cached_year >= target_year and cached_path:
        return cached_path, cached_year

    token = os.environ.get("EARTHDATA_TOKEN")
    if not token:
        print(f"  [warn] EARTHDATA_TOKEN not set -- skipping NTL "
              f"(would fetch {target_year} composite). ntl_delta will be null.")
        return None, None

    try:
        import download_nightlights as dn
        NTL_DIR.mkdir(parents=True, exist_ok=True)
        out_path = NTL_DIR / f"{target_year}_ntl.tif"
        print(f"  Fetching VNP46A4 annual NTL composite for {target_year} "
              f"(cached: {cached_year})...")
        dn.download_ntl(token, target_year, out_path)
        dn.report(out_path, target_year)
        return str(out_path), target_year
    except Exception as e:
        print(f"  [warn] NTL fetch failed, continuing without it: {e}")
        return cached_path, cached_year  # fall back to whatever was cached, if anything


def download_optical_and_sar(connection, scene_date, out_dir, prev_scene_date):
    out_dir.mkdir(parents=True, exist_ok=True)
    cloud_filter = {"eo:cloud_cover": lambda c: c <= ds.MAX_CLOUD_COVER}

    paths = {
        "red": out_dir / "red.tif",
        "nir": out_dir / "nir.tif",
        "blue": out_dir / "blue.tif",
        "swir": out_dir / "swir.tif",
    }
    ds.download_band(connection, "B04", scene_date, paths["red"], properties=cloud_filter)
    ds.download_band(connection, "B08", scene_date, paths["nir"], properties=cloud_filter)
    ds.download_band(connection, "B02", scene_date, paths["blue"], properties=cloud_filter)
    ds.download_band(connection, "B11", scene_date, paths["swir"], properties=cloud_filter,
                      resolution=ds.TEN_METER_PIXEL_DEG, method="bilinear")

    # SAR: share viewing geometry with the PREVIOUS scene. Sentinel-1's
    # revisit cadence isn't daily, so a narrow (or worse, zero-width --
    # confirmed as a real bug via an actual live run: passing the same date
    # twice as (start, end) matches nothing, since CDSE's OData filter is a
    # strict gt/lt) window around each date would very likely find no SAR
    # scene at all. +/-15 days gives a real chance of a match while staying
    # close enough to each optical date to still mean something.
    def plus_minus_days(d_str, days):
        d0 = datetime.strptime(d_str, "%Y-%m-%d")
        return ((d0 - timedelta(days=days)).strftime("%Y-%m-%d"),
                (d0 + timedelta(days=days)).strftime("%Y-%m-%d"))

    prev_range = plus_minus_days(prev_scene_date, 15)
    new_range = plus_minus_days(scene_date, 15)
    try:
        orbit_state, relative_orbit = ds.pick_orbit_config(prev_range, new_range)
        properties = {
            "sat:orbit_state": lambda c: c.eq(orbit_state),
            "sat:relative_orbit": lambda c: c.eq(relative_orbit),
        }
        vv_scene_date = ds.find_scene_date(
            connection, new_range, collection=ds.S1_COLLECTION, bands=("VV",), properties=properties
        )
        paths["vv"] = out_dir / "vv.tif"
        ds.download_band(connection, "VV", vv_scene_date, paths["vv"],
                          collection=ds.S1_COLLECTION, properties=properties)
    except Exception as e:
        # SAR corroboration is a nice-to-have, not required for NDVI
        # detection to work -- don't let a SAR-side failure (no matching
        # orbit that day, etc.) kill the whole cycle.
        print(f"  [warn] SAR (VV) fetch failed, continuing without it: {e}")
        paths["vv"] = None

    for k, p in paths.items():
        if p is not None:
            ds.report(p, scene_date)
    return paths


def run_ndvi_detection(before_paths, after_paths):
    # Pull SITE_ID / LEASE_FILE / lease-validity from the AOI registry,
    # then override only the band paths with this cycle's downloads.
    d.set_aoi(AOI_ID)
    d.BEFORE_RED = str(before_paths["red"])
    d.BEFORE_NIR = str(before_paths["nir"])
    d.AFTER_RED = str(after_paths["red"])
    d.AFTER_NIR = str(after_paths["nir"])
    return d.run_detection()


def add_corroboration(triggers, before_vv_path, after_vv_path, after_red_path, after_ntl_path, after_ntl_year):
    """SAR + roads + NTL + a real confidence blend -- see module docstring's
    SCOPE section for exactly what's included/excluded and why."""
    if before_vv_path is None or after_vv_path is None:
        for t in triggers:
            t["sar_change_score"] = None
            t["sar_mean_abs_change_db"] = None
    else:
        before_vv, before_transform = st.load_vv_band(str(before_vv_path))
        after_vv, after_transform = st.load_vv_band(str(after_vv_path))
        for t in triggers:
            score, mean_abs_db = st.sar_change_score(
                t["lat"], t["lon"], before_vv, before_transform, after_vv, after_transform
            )
            t["sar_change_score"] = score
            t["sar_mean_abs_change_db"] = mean_abs_db

    if after_ntl_path is None:
        for t in triggers:
            t["ntl_delta"] = None
            t["ntl_before_year"] = None
            t["ntl_after_year"] = None
    else:
        before_ntl, before_ntl_transform = st.load_band_and_transform(NTL_BEFORE_FILE)
        after_ntl, after_ntl_transform = st.load_band_and_transform(str(after_ntl_path))
        for t in triggers:
            t["ntl_delta"] = st.ntl_delta(
                t["lat"], t["lon"], before_ntl, before_ntl_transform, after_ntl, after_ntl_transform
            )
            t["ntl_before_year"] = NTL_BEFORE_YEAR
            t["ntl_after_year"] = after_ntl_year

    roads = st.load_roads()
    if roads is None:
        print(f"  [warn] {st.ROADS_FILE} not found -- road fields will be null.")
    _, area_transform = st.load_band_and_transform(str(after_red_path))

    for t in triggers:
        dist_m, road_type = st.nearest_road(t["lat"], t["lon"], roads)
        t["nearest_road_distance_m"] = dist_m
        t["nearest_road_type"] = road_type
        t["road_access_score"] = st.road_access_score(dist_m)
        t["disturbance_area_m2"] = round(t["area_px"] * st.pixel_area_m2(area_transform, t["lat"]), 1)

        # NOT null -- confirmed via a real 422 from the live API that
        # main.py's TriggerPayload declares these as required str/dict
        # (the DB column is nullable, but the API's Pydantic model is
        # stricter than the schema). Reusing "INSUFFICIENT_DATA", the
        # SAME value main.py's own site_legality_flag() already uses for
        # "no legality_flag recorded" (see SITE_LEGALITY_SEVERITY in
        # main.py) -- not inventing a new convention, and not a fabricated
        # legal claim: it honestly means what it says, we ran no
        # mock-permit legality check here. The assessment dict is a plain
        # note, not a fake checks list -- confirmed safe against
        # main.py's build_brief_prompt(), which only renders entries
        # shaped like {"value":..., "data_source":...} and silently skips
        # anything else, falling back to "No legality_assessment checks
        # are recorded for this alert."
        t["legality_flag"] = "INSUFFICIENT_DATA"
        t["legality_assessment"] = {
            "note": "Live monitor (pipeline/live_monitor.py) does not run the "
                     "mock-permit legality layer -- see its module docstring."
        }

        # Same formula and weights score_triggers.py uses -- imported
        # constants, not re-derived -- so a live confidence_score means
        # the same thing as a static-pipeline one.
        if t["sar_change_score"] is None:
            t["confidence_score"] = None
            t["confidence_tier"] = st.SAR_UNAVAILABLE_TIER
        else:
            t["confidence_score"] = round(
                st.CHANGE_PCT_WEIGHT * (t["change_pct"] / 100) + st.SAR_SCORE_WEIGHT * t["sar_change_score"],
                3,
            )
            t["confidence_tier"] = st.confidence_tier(t["confidence_score"])

        t["status"] = "PENDING_REVIEW"

    return triggers


def ingest_and_recluster(triggers):
    """POST each trigger to the real backend (reusing seed_backend.py's
    own build_payload()/endpoint) then re-run DBSCAN clustering -- see the
    module docstring's MAP INTEGRATION section for why the reclustering
    step isn't optional if you actually want these to show up on the map.

    Returns True if the cycle should be considered fully successful (all
    ingests either succeeded or were already-ingested 409s, AND
    reclustering ran without error), False otherwise -- a failure here
    doesn't un-write triggers_live.json or the downloaded imagery, it just
    means the map won't reflect this cycle yet.
    """
    print(f"\nIngesting {len(triggers)} trigger(s) to {sb.TRIGGERS_ENDPOINT} ...")
    failures = []
    for t in triggers:
        payload = sb.build_payload(t)
        resp = requests.post(sb.TRIGGERS_ENDPOINT, json=payload, timeout=30)
        if resp.status_code == 409:
            print(f"  {t['trigger_id']}  ->  HTTP 409 (already ingested, skipping)")
        elif resp.status_code in (200, 201):
            print(f"  {t['trigger_id']}  ->  HTTP {resp.status_code}")
        else:
            print(f"  {t['trigger_id']}  ->  HTTP {resp.status_code}  {resp.text}  <-- FAILED")
            failures.append(t["trigger_id"])

    if failures:
        print(f"\n{len(failures)} trigger(s) failed to ingest: {failures}")

    print("\nRe-clustering (db/cluster_sites.py) so new alerts get a real "
          "cluster_id and become visible on the map...")
    repo_root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, str(repo_root / "db" / "cluster_sites.py")],
        cwd=str(repo_root),
    )
    if result.returncode != 0:
        print(f"  [warn] cluster_sites.py exited with code {result.returncode} -- "
              f"new alerts may still be invisible on the map until it's re-run "
              f"successfully.")
        return False

    return not failures


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--lookback-days", type=int, default=BOOTSTRAP_LOOKBACK_DAYS,
                         help="On the bootstrap (no state yet) run only: how far back to search "
                              "for a usable scene. Useful to widen manually if the default 30 "
                              "days finds nothing (e.g. an extended cloudy/monsoon stretch).")
    parser.add_argument("--live", action="store_true",
                         help="Actually POST triggers to the real backend and re-cluster, so "
                              "they show up on the map. Without this flag: local file only, "
                              "same dry-run-by-default convention as seed_backend.py.")
    parser.add_argument("--limit", type=int, default=None,
                         help="Cap the number of triggers processed (by highest change_pct "
                              "first) -- e.g. for a small, low-footprint --live test instead "
                              "of ingesting a full batch. Applies to the local file too, not "
                              "just ingestion.")
    parser.add_argument("--aoi", default=DEFAULT_AOI,
                         help=f"Region id from pipeline/aois.py (default: {DEFAULT_AOI}).")
    args = parser.parse_args()

    try:
        get_aoi(args.aoi)
    except KeyError as e:
        raise SystemExit(str(e))
    _configure_for_aoi(args.aoi)
    print(f"AOI: {args.aoi}  |  state file: {STATE_FILE}  |  output: {OUTPUT_FILE}")

    load_dotenv()
    state = load_state()

    if state is None:
        print(f"No state file yet -- bootstrapping from {BOOTSTRAP_BASELINE['date']} "
              f"(the original validated real_data/ scene) as the baseline.")
        since_date = (date.today() - timedelta(days=args.lookback_days)).isoformat()
        prev_paths = {k: BOOTSTRAP_BASELINE[k] for k in ("red", "nir", "blue", "vv")}
        prev_scene_date = BOOTSTRAP_BASELINE["date"]
    else:
        since_date = state["last_scene_date"]
        prev_paths = state["paths"]
        prev_scene_date = state["last_scene_date"]

    connection = ds.connect_service_account()
    cloud_filter = {"eo:cloud_cover": lambda c: c <= ds.MAX_CLOUD_COVER}

    scene_date = find_latest_scene_date(connection, since_date, cloud_filter)
    if scene_date is None:
        print(f"No new usable Sentinel-2 scene since {since_date} "
              f"(cloud_cover <= {ds.MAX_CLOUD_COVER}%). Nothing to do this run.")
        return 0

    print(f"New scene found: {scene_date} (previous processed: {prev_scene_date})")
    out_dir = LIVE_DIR / scene_date
    new_paths = download_optical_and_sar(connection, scene_date, out_dir, prev_scene_date)
    ntl_path, ntl_year = ensure_ntl(state)

    triggers = run_ndvi_detection(prev_paths, new_paths)
    for t in triggers:
        t["source"] = f"Sentinel-2 NDVI change detection (live monitor, {prev_scene_date} vs {scene_date})"
    triggers = add_corroboration(
        triggers,
        prev_paths.get("vv"),
        new_paths.get("vv"),
        new_paths["red"],
        ntl_path,
        ntl_year,
    )

    if args.limit is not None:
        triggers = sorted(triggers, key=lambda t: t["change_pct"], reverse=True)[:args.limit]
        print(f"\n--limit {args.limit}: keeping the {len(triggers)} highest-change_pct "
              f"trigger(s) only, out of the full detection.")

    OUTPUT_FILE.parent.mkdir(exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(triggers, indent=2))
    print(f"\n{len(triggers)} trigger(s) written to {OUTPUT_FILE}:")
    for t in triggers:
        sar = f"{t['sar_mean_abs_change_db']:.2f}dB" if t.get("sar_mean_abs_change_db") is not None else "n/a"
        ntl = f"{t['ntl_delta']:.3f} ({t['ntl_before_year']}->{t['ntl_after_year']})" if t.get("ntl_delta") is not None else "n/a"
        conf = f"{t['confidence_score']:.3f} ({t['confidence_tier']})" if t.get("confidence_score") is not None else t["confidence_tier"]
        print(f"  {t['trigger_id']}  |  {t['boundary_status']:24s}  |  "
              f"change={t['change_pct']}%  |  sar={sar}  |  ntl={ntl}  |  confidence={conf}")

    save_state({
        "last_scene_date": scene_date,
        "paths": {k: str(v) if v else None for k, v in new_paths.items()},
        "ntl_year_fetched": ntl_year if ntl_year is not None else (state or {}).get("ntl_year_fetched"),
        "ntl_path": ntl_path if ntl_path is not None else (state or {}).get("ntl_path"),
    })
    print(f"\nState updated: last_scene_date={scene_date}, ntl_year={ntl_year}")

    if args.live:
        ok = ingest_and_recluster(triggers)
        if not ok:
            print("\nIngest/recluster had failures -- see above. Local state is "
                  "still updated (won't re-process this scene next run), but the "
                  "map may not fully reflect this cycle yet.")
            return 1
        print("\nDone -- these triggers are now in the live database and "
              "reclustered. Check the map.")
    else:
        print(f"\nDRY RUN -- nothing sent to the live API. Re-run with --live "
              f"to actually ingest these {len(triggers)} trigger(s) and make "
              f"them appear on the map.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
