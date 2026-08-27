"""
BhuNetra — seed the live backend (Railway) with our scored triggers.

Reads output/triggers_scored.json (written by score_triggers.py) and POSTs
each trigger to POST /api/v1/triggers on the live backend, in the exact
widened schema main.py's TriggerPayload expects. Most fields
(trigger_id, site_id, change_pct, boundary_status, sar_change_score,
sar_mean_abs_change_db, confidence_score, confidence_tier,
disturbance_area_m2, ntl_delta, legality_flag, legality_assessment) are a
1:1 passthrough of score_triggers.py's output -- the field names already
match. Three fields TriggerPayload requires that the scoring pipeline
doesn't produce are synthesized here:
  - location_name: built from the site label + trigger_id
  - risk_score: legacy 0-100 field kept for the existing map UI; see
    SCALE NOTE below
  - geojson_polygon: the pipeline only emits a point centroid, so this
    wraps it in a small square polygon (see square_polygon())

SCALE NOTE (flagged explicitly, not assumed): main.py's TriggerPayload
declares risk_score as a bare `float` -- nothing in the schema enforces a
0-1 or 0-100 range. The existing live sample data ("Sample Region Alpha",
"Singrauli Test Site") uses 0-100 (confirmed by querying GET /api/v1/alerts
directly: risk_score 92.5 and 91.0), while confidence_score is 0-1. This
script converts by multiplying by 100 so new alerts don't display as
broken outliers next to the existing samples -- a judgment call based on
observed data, not something the backend schema requires or validates.
confidence_score can be None (SAR_UNAVAILABLE triggers) -- falls back to
change_pct in that case, since that's always present.

Only ADDS new alerts via POST -- never touches existing rows. A 409
(duplicate trigger_id -- see main.py's IntegrityError handling) means this
trigger was already ingested on a previous run and is treated as success,
not failure -- this is what lets the daily GitHub Actions job re-run
against the same static demo imagery without failing on days it finds
nothing new.

Safety: defaults to a dry run (prints payloads, sends nothing). Pass --live
to actually POST. Exits non-zero if any POST fails with something other
than 200/201/409, so a CI job picks up real failures.
"""
import argparse
import json
import math
import sys

import requests

try:
    from aois import AOIS, get_aoi, DEFAULT_AOI
except ImportError:
    from pipeline.aois import AOIS, get_aoi, DEFAULT_AOI

TRIGGERS_FILE = "output/triggers_scored.json"
API_BASE_URL = "https://bhunetra-demo-rosy.vercel.app"
TRIGGERS_ENDPOINT = f"{API_BASE_URL}/api/v1/triggers"

# Fallback display label; per-trigger label is looked up from the AOI
# registry by the trigger's own site_id in build_payload().
SITE_LABEL = "Bailadila AOI-07"


def _label_for(site_id):
    cfg = AOIS.get(site_id)
    return cfg["name"] if cfg else SITE_LABEL
RISK_SCORE_SCALE = 100  # confidence_score (0-1) * this -> matches live sample data's 0-100 convention

# Same local-latitude degree-to-meter approximation used elsewhere in this
# project (disturbance_area_m2, nearest_road) -- not a full geodesic
# reprojection, but consistent with how the rest of the codebase handles this.
EARTH_METERS_PER_DEG_LAT = 111320.0
POLYGON_HALF_SIDE_M = 50  # ~100m per side


def square_polygon(lat, lon, half_side_m=POLYGON_HALF_SIDE_M):
    """Raw GeoJSON Polygon geometry (no Feature wrapper) -- a closed ring
    approximately half_side_m*2 meters per side, centered on (lat, lon)."""
    meters_per_deg_lon = EARTH_METERS_PER_DEG_LAT * math.cos(math.radians(lat))
    dlat = half_side_m / EARTH_METERS_PER_DEG_LAT
    dlon = half_side_m / meters_per_deg_lon

    ring = [
        [lon - dlon, lat - dlat],
        [lon + dlon, lat - dlat],
        [lon + dlon, lat + dlat],
        [lon - dlon, lat + dlat],
        [lon - dlon, lat - dlat],  # closed ring, first == last
    ]
    return {"type": "Polygon", "coordinates": [ring]}


def build_payload(trigger):
    risk_basis = trigger["confidence_score"] if trigger["confidence_score"] is not None \
        else trigger["change_pct"] / 100
    return {
        "location_name": f"{_label_for(trigger.get('site_id'))} — {trigger['trigger_id']}",
        "risk_score": round(risk_basis * RISK_SCORE_SCALE, 1),
        "geojson_polygon": square_polygon(trigger["lat"], trigger["lon"]),
        "trigger_id": trigger["trigger_id"],
        "site_id": trigger["site_id"],
        "change_pct": trigger["change_pct"],
        "boundary_status": trigger["boundary_status"],
        "sar_change_score": trigger["sar_change_score"],
        "sar_mean_abs_change_db": trigger["sar_mean_abs_change_db"],
        "confidence_score": trigger["confidence_score"],
        "confidence_tier": trigger["confidence_tier"],
        "disturbance_area_m2": trigger["disturbance_area_m2"],
        "ntl_delta": trigger["ntl_delta"],
        "legality_flag": trigger["legality_flag"],
        "legality_assessment": trigger["legality_assessment"],
        "sla_hours": trigger.get("sla_hours"),
        "sla_deadline": trigger.get("sla_deadline"),
    }



def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true",
                         help="Actually POST to the backend. Without this flag, dry-run only.")
    parser.add_argument("--triggers", default=TRIGGERS_FILE,
                         help=f"scored-triggers JSON to ingest (default: {TRIGGERS_FILE})")
    parser.add_argument("--aoi", default=None,
                         help="Optional: validate every trigger's site_id belongs to this "
                              "AOI id from pipeline/aois.py before sending.")
    args = parser.parse_args()

    if args.aoi:
        get_aoi(args.aoi)  # raises on unknown id

    triggers = json.load(open(args.triggers))
    if args.aoi:
        wrong = {t.get("site_id") for t in triggers} - {args.aoi}
        if wrong:
            raise SystemExit(f"{args.triggers} contains triggers for {wrong}, not {args.aoi}")
    payloads = [build_payload(t) for t in triggers]

    if not args.live:
        print(f"DRY RUN -- {len(payloads)} payload(s) that WOULD be POSTed to {TRIGGERS_ENDPOINT}. "
              f"Nothing sent. Re-run with --live to actually send.\n")
        for p in payloads:
            print(json.dumps(p, indent=2))
            print()
        return

    print(f"Sending {len(payloads)} trigger(s) to {TRIGGERS_ENDPOINT} ...\n")
    failures = []
    for p in payloads:
        resp = requests.post(TRIGGERS_ENDPOINT, json=p, timeout=30)
        if resp.status_code == 409:
            print(f"  {p['trigger_id']}  ->  HTTP 409 (already ingested, skipping)")
        elif resp.status_code in (200, 201):
            print(f"  {p['trigger_id']}  ->  HTTP {resp.status_code}  {resp.text}")
        else:
            print(f"  {p['trigger_id']}  ->  HTTP {resp.status_code}  {resp.text}  <-- FAILED")
            failures.append((p['trigger_id'], resp.status_code, resp.text))

    if failures:
        print(f"\n{len(failures)} trigger(s) failed to ingest.")
        sys.exit(1)


if __name__ == "__main__":
    main()
