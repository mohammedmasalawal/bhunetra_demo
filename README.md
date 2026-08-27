# BhuNetra — Detection Engine (Member 1)

## What's in here
- `main.py` — the FastAPI + PostGIS backend (the API Railway deploys).
- `pipeline/` — the detection → scoring → ingest chain, plus the imagery
  scripts that feed it:
  - `pipeline/make_sample_data.py` — generates synthetic before/after
    imagery + a lease boundary, so you can test the pipeline today, before
    real Sentinel-2 data is downloaded.
  - `pipeline/detection.py` — the real detection engine: NDVI change
    detection, blob clustering, lease-boundary check, trigger JSON output.
  - `pipeline/score_triggers.py` — fuses SAR/NTL/road/legality signals
    into each trigger's `confidence_score`.
  - `pipeline/seed_backend.py` — POSTs scored triggers to the live API
    (this is what the daily GitHub Actions job runs).
  - `pipeline/validate.py` — computes Precision / Recall / F1 against a
    manually labeled ground-truth CSV (this is what answers the judges'
    "needs technical validation" feedback).
  - `pipeline/detection_minenetcd.py` — same trigger schema and downstream
    contract as `detection.py`, but the change mask comes from a real
    pretrained deep model (`ericyu/minenetcd-upernet-Swin-Diff-B-Pretrained`)
    instead of an NDVI threshold. Opt-in, separate ML venv required — see
    "MineNetCD deep-learning detector" below.
  - `pipeline/live_monitor.py` — watches for new Sentinel-2 captures
    (~5-day cadence) and runs NDVI+SAR detection automatically. Opt-in,
    needs a one-time credential setup — see "Live Sentinel monitor" below.
- `db/` — one-off database scripts: migrations (`migrate.py`), the
  `alerts.status` CHECK constraint (`add_status_constraint.py`), column
  verification, and seed data (`seed_leases.py`, `load_manual_lease.py`).
  - `db/cluster_sites.py` — DBSCAN groups alerts into physical mining
    sites (`cluster_id`), backing `GET /api/v1/sites`. Re-run after
    ingesting new triggers — new alerts start with `cluster_id` NULL and
    are invisible to that endpoint until this has run.
  - `db/generate_briefs.py` — batch-pregenerates the LLM officer briefing
    for every alert so the demo never depends on a live model call.
    **Run this again right before the Wed 21:00 freeze**, and after any
    new ingestion.
- `output/triggers.json` — pipeline output, ready to hand to Member 2
  (Verification/Scoring).

## Quickstart
```bash
pip install -r requirements.txt
python3 pipeline/make_sample_data.py     # only needed once, or to regenerate test data
python3 pipeline/detection.py            # runs detection, writes output/triggers.json
python3 pipeline/validate.py             # prints Precision/Recall/F1
```

## Multi-region: onboarding a new mining region

Every region the system monitors is one entry in `pipeline/aois.py` (the
AOI registry) and one row in the `aois` DB table. No code changes.

1. **Add the region to `pipeline/aois.py`** — `name`, `state`, `district`,
   `mineral`, a tight `bbox` (west/south/east/north, EPSG:4326, keep it
   snug around the pit cluster — cost scales with area), `center`, and
   either a `lease_file` path + `lease_boundary_valid: True`, or
   `lease_file: None` + `lease_boundary_valid: False` (every trigger then
   comes back `boundary_unknown`).
2. **Sync the DB**: `python db/seed_aois.py` (needs `DATABASE_URL`).
   `GET /api/v1/aois` now lists it; the frontend region selector shows it
   with a zero count.
3. **Seed a "before" baseline** (only for the live monitor): put
   `before_red.tif` / `before_nir.tif` / `before_vv.tif` (+ optional
   `before_blue.tif`, `before_ntl.tif`) under
   `real_data_<aoi_id_lower_snake>/` — one historical Sentinel scene for
   that bbox. `pipeline/download_sentinel.py` with `BHUNETRA_AOI=<id>` set
   fetches these.
4. **First run**: `python pipeline/live_monitor.py --aoi <id>` (add
   `--live` to ingest). Downloads the latest Sentinel-2/1 scene for the
   bbox, runs the real NDVI+SAR detection, ingests real triggers.
5. **Automate**: add `<id>` to the `matrix.aoi` list in
   `.github/workflows/live-monitor.yml`. The daily cron then monitors it
   (no-op most days, real work every ~5 days on a new pass).

Rough cost: ~15–30 min of config for a region without a real lease
polygon; a couple of hours if you need to source/trace the official
boundary. Compute per cycle is ~5–15 min and free (CDSE + GitHub Actions
free tiers).

Every pipeline entry point takes `--aoi <id>` (`detection.py`,
`score_triggers.py`, `seed_backend.py`, `live_monitor.py`);
`AOI-07-BAILADILA` stays the default so existing invocations are
unchanged.

## Swapping in REAL Sentinel-2 data
1. Download Sentinel-2 L2A imagery for your demo site, two dates (before/after).
2. Extract Band 4 (Red) and Band 8 (NIR) as GeoTIFFs for each date.
3. Replace the 4 file paths at the top of `pipeline/detection.py`:
   ```python
   BEFORE_RED = "your_real_data/before_B04.tif"
   BEFORE_NIR = "your_real_data/before_B08.tif"
   AFTER_RED  = "your_real_data/after_B04.tif"
   AFTER_NIR  = "your_real_data/after_B08.tif"
   ```
4. Replace `sample_data/lease_boundaries.geojson` with your real lease
   boundary polygon (from IBM/state DMG data, or manually digitized from
   Bhuvan/Google Earth for the demo — see the earlier chat for that path).
5. Run `pipeline/detection.py` again — no other code changes needed.

## Re-validating on real data
Once you run detection on real imagery:
1. Open `output/triggers.json` and look at each detected trigger's
   coordinates on the before/after imagery yourself.
2. For each `trigger_id`, decide by eye: is this really new disturbance,
   or is it noise (farming, cloud shadow, seasonal vegetation change)?
3. Write those judgments into a new CSV (copy `ground_truth_demo.csv`'s
   format) — `TRUE` for real disturbance, `FALSE` for false positives.
4. If you can see real disturbance in the imagery that your pipeline
   *didn't* flag, add its coordinates as a `TRUE` row too, with a
   `trigger_id` that doesn't match anything detected — `pipeline/validate.py`
   counts these as false negatives automatically.
5. Point `GROUND_TRUTH_FILE` in `pipeline/validate.py` at your new CSV and rerun.

Expect real-imagery numbers to be lower and messier than this synthetic
100% — that's normal and expected. A real, honest number like "78%
precision on manually verified ground truth" is a far stronger claim
for judges than an unvalidated system, even though it's not 100%.

## MineNetCD deep-learning detector (optional, opt-in)

`pipeline/detection_minenetcd.py` runs a real pretrained change-detection
model (`ericyu/minenetcd-upernet-Swin-Diff-B-Pretrained`, from
[EricYu97/MineNetCD](https://github.com/EricYu97/MineNetCD), IEEE TGRS 2024)
against our own real Bailadila imagery, instead of the NDVI threshold
`detection.py` uses. It writes `output/triggers_minenetcd.json` in the exact
same schema, so it's a drop-in alternative (or a second opinion to run
alongside `detection.py`) for the scoring/ingest stages.

**Setup (separate venv — do NOT add these to the main `requirements.txt`,**
**they pull in torch which the FastAPI app and rest of `pipeline/` never need):**
```bash
python -m venv .venv-minenetcd
.venv-minenetcd/Scripts/pip install -r requirements.txt -r requirements-ml.txt
# CPU: pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
# CUDA (much faster): pip install torch torchvision --index-url https://download.pytorch.org/whl/cuXXX
#   (pick the cuXXX tag matching your GPU driver -- an RTX 50-series/Blackwell
#   card needs cu128 or newer; nvidia-smi's "CUDA Version" line tells you the ceiling)

.venv-minenetcd/Scripts/python.exe pipeline/detection_minenetcd.py
```

**Benchmark validation** (proves the pretrained weights themselves are real
and competent, independent of our own imagery): the full held-out test
split of `HZDR-FWGEL/MineNetCD256` (19,355 images, domain-leakage-checked
clean across all 71,711 examples — 100 unique global mining sites, zero
domain overlap between train/val/test), using the official repo's own
`test.py` eval logic verbatim:

| Metric | Value |
|---|---|
| Overall Accuracy | 0.9227 |
| Precision | 0.7249 |
| Recall | 0.6238 |
| F1 | 0.6706 |
| cIoU | 0.5044 |

**Applying it to OUR imagery is harder than the benchmark, and here's the
real, measured story:**
1. **Resolution mismatch.** MineNetCD trains at ~1.2m/px (Google Earth
   imagery); our `real_data/*.tif` is raw Sentinel-2 at ~10m/px — an ~8.6x
   gap. Feeding the model raw 256x256 windows of our data (each covering
   ~8.6x more ground than a training patch) made it collapse to a
   confident "no change" everywhere (max change-probability ~5%) even on
   windows with obvious real pixel differences — **0 triggers**, despite
   `detection.py`'s NDVI approach finding 9 real ones on the same imagery.
   Fix: crop native windows sized to match the training patch's *real-world
   footprint* (not its pixel count) and upsample to 256x256 before
   inference — this restored real detection (verified: change-probability
   reached 99.8% on a genuinely changed area once footprint-matched).
2. **Tiling-grid sensitivity.** The first working version tiled
   non-overlapping windows; shifting the tiling grid by half a window on
   the *identical* scene only got 0.41 IoU between the two runs — a real
   robustness problem. Fixed by switching to 50%-overlap tiling with
   per-pixel probability averaging (standard practice for tiled semantic
   segmentation) — re-measured IoU under the same grid-shift test: **0.915**.
3. **No Green band, and the model is genuinely sensitive to how it's
   approximated.** `real_data/` has red/nir/blue/swir/vv but no green; the
   model expects natural RGB, so we approximate a Green channel. Measured
   the actual sensitivity to that choice rather than assuming it's minor:
   on the identical scene, `G=(Red+NIR)/2` (our default) flags 11.1% of
   pixels changed, `G=Red` flags 23.0%, `G=NIR` flags only 0.9%. That's a
   >25x swing in detected area from a preprocessing choice alone — this is
   the single biggest source of result variance found in this integration,
   bigger than the tiling-grid or resolution effects. `(Red+NIR)/2` was
   kept as the default because it's the least extreme of the three (a
   genuine average, not a duplicate of an existing band), but this is a
   real, unresolved limitation: **there is no principled way to pick a
   Green-band approximation without either a real Green band or ground
   truth to calibrate against.** Flag this explicitly in any evaluation
   context.
4. **Cross-validated against independent real signals, on TWO separate real
   before/after pairs** (not cherry-picked — every real optical pair this
   repo has access to):

   | Pair | NDVI triggers | MineNetCD triggers | Boundary-status agreement | Median distance |
   |---|---|---|---|---|
   | `real_data/` (2020-01 vs 2024-01) | 9 | 4 | 4/4 | ~500m |
   | `real_data_2026/` (2020-01 vs 2026-06) | 14 | 6 | 5/6 | ~430m |

   (These are the current numbers, after the overlap-averaging fix in point 2
   above — that fix also consolidated the original pair's triggers from 8
   fragmented ones down to 4 more solid ones, which is why this table won't
   match an earlier run.)

   "Boundary-status agreement" = each MineNetCD trigger lands on the same
   side of the lease boundary (`boundary_violation` vs
   `within_lease_expansion`) as its nearest independent NDVI trigger — not
   guaranteed by chance, since the NDVI triggers split roughly evenly
   between the two statuses in both pairs. **9/10 (90%) combined.**
   Centroid-level distance is looser (~450-500m median) — expected, since
   the scale-matched window itself is ~300m wide, so sub-100m centroid
   agreement isn't a realistic bar for this method. Visual overlays:
   `real_data/triggers_comparison.png`, `real_data_2026/triggers_comparison.png`
   (red X = NDVI, cyan + = MineNetCD). Both show the same qualitative
   pattern independently: NDVI clusters tightly around the pit's vegetation-
   loss edge; MineNetCD additionally flags a cluster northwest of the pit in
   *both* independent time pairs. Checked this directly rather than leaving
   it as speculation: NDVI at that location is 0.70-0.74 in *both* dates
   (dense, healthy vegetation) with only a 0.04 NDVI drop — nowhere near
   `detection.py`'s 0.25 disturbance threshold. **This specific cluster is
   very likely a false positive**, not a real disturbance NDVI's threshold
   missed — consistent across two independent time-pairs, but consistent
   doesn't mean correct; it just means whatever it's reacting to (lighting,
   the Green-band approximation, or something in this specific landscape
   feature) is stable across dates, not that it's real mining change.
5. **The paper's ChangeFFT variant scores better on the benchmark but WORSE
   on our real data — verified both ways, don't just trust the leaderboard
   number.** Ran the full 19,355-image benchmark for
   `ericyu/minenetcd-upernet-Swin-Diff-B-Pretrained-ChannelMixing-Dropout`
   (the ChangeFFT/channel-mixing checkpoint) the same way as the baseline:

   | Metric | Baseline | ChangeFFT |
   |---|---|---|
   | F1 | 0.6706 | **0.6931** |
   | cIoU | 0.5044 | **0.5304** |
   | Precision | **0.7249** | 0.6782 |
   | Recall | 0.6238 | **0.7087** |

   Better F1/recall/cIoU on the clean, in-distribution benchmark, as the
   paper claims. But on our actual real Bailadila scene (`real_data/`),
   ChangeFFT produced **35 triggers** (vs baseline's 4) with only
   **57% boundary-status agreement** against the independent NDVI
   signal (barely above what random guessing would get, given NDVI's own
   triggers split roughly 50/50) — vs baseline's **100% (4/4)** agreement.
   Most of those 35 are tiny, scattered blobs (median area share 0.07%),
   consistent with noise amplified by our specific domain mismatch, not
   genuine extra sensitivity. **Recommendation: keep the baseline checkpoint
   as the default for this imagery** (already the default in
   `minenetcd_infer.py`) — ChangeFFT is available via
   `MINENETCD_MODEL_ID=ericyu/minenetcd-upernet-Swin-Diff-B-Pretrained-ChannelMixing-Dropout`
   if you want to experiment, but don't deploy it on this data without
   more scrutiny just because its benchmark numbers are better.

   (While testing this, found and fixed a real bug: `pipeline/minenetcd_model/upernet.py`'s
   ChangeFFT module list was named differently from the official checkpoint's
   saved parameter names, so those weights were silently loading as
   uninitialized garbage instead of the real trained values — confirmed via
   the actual parameter statistics before/after the fix. Already corrected;
   the numbers above are post-fix, verified-correct-loading numbers.)
6. **`real_data_2026/` is genuinely new data**, not a synthetic stand-in:
   fetched via `pipeline/download_sentinel_2026.py` (same OIDC device-code
   flow as `download_sentinel.py`, just a different `AFTER_DATE_RANGE` and
   `OUTPUT_DIR` so it doesn't overwrite the original validated pair) — a
   real Sentinel-2 scene from 2026-06-03 (optical) / 2026-06-11 (SAR),
   ~2.5 months old at the time this was run.

   `detection.py` itself was deliberately left untouched to get the NDVI
   numbers on this new pair (its 9 original triggers must not change) — its
   `run_detection()` reads its file-path constants as module globals at
   call time, so they can be overridden from outside without editing the
   file:
   ```python
   import detection as d
   d.BEFORE_RED, d.BEFORE_NIR = "real_data_2026/before_red.tif", "real_data_2026/before_nir.tif"
   d.AFTER_RED,  d.AFTER_NIR  = "real_data_2026/after_red.tif",  "real_data_2026/after_nir.tif"
   results = d.run_detection()  # 14 triggers on the 2020-vs-2026 pair
   ```
   `pipeline/detection_minenetcd.py` doesn't need this trick — it already
   takes `--data-dir` directly (see the CLI examples above).

**Bottom line:** this is a genuine, working, measured integration — not a
demo stub — but it inherits a real ~8.6x resolution gap between what the
model was trained on and what our current imagery provides. Treat its
output as a second, coarser-grained opinion to corroborate `detection.py`'s
NDVI triggers, not as a higher-precision replacement, until it's been run
against imagery closer to its native ~1-2m resolution.

## Live Sentinel monitor (checks for new imagery every ~5 days, opt-in)

`pipeline/live_monitor.py` + `.github/workflows/live-monitor.yml` watch for
new Sentinel-2 captures over the Bailadila AOI and run NDVI+SAR+roads+NTL
change detection automatically, instead of the existing daily cron
(`trigger-pipeline.yml`) which just re-validates the same static
`real_data/` files every day.

**Two one-time setup steps required before this can run** (human steps,
can't be automated):
1. **Sentinel-2/1 access**: register a non-interactive OAuth client at
   [shapps.dataspace.copernicus.eu/dashboard](https://shapps.dataspace.copernicus.eu/dashboard)
   (self-service, ~5 min, no support ticket) and add the resulting
   `client_id`/`client_secret` as GitHub repo secrets `CDSE_CLIENT_ID` /
   `CDSE_CLIENT_SECRET`. Real, documented openEO feature
   (`authenticate_oidc_client_credentials`) — necessary because the
   existing `download_sentinel.py` auth requires a live browser click,
   which can't run unattended on a schedule.
2. **NTL access** (only needed for `ntl_delta`, everything else works
   without it) — TWO things, not one, confirmed the hard way:
   a. Generate a token at
      [urs.earthdata.nasa.gov/profile](https://urs.earthdata.nasa.gov/profile)
      (same one `download_nightlights.py` already uses) and add it as GitHub
      repo secret `EARTHDATA_TOKEN`.
   b. **Also log in once at [ladsweb.modaps.eosdis.nasa.gov](https://ladsweb.modaps.eosdis.nasa.gov/)**
      with that same Earthdata account. A valid token alone isn't enough —
      NASA's LAADS DAAC (where this NTL data actually lives) requires a
      separate one-time authorization on that specific site, or downloads
      silently redirect to an HTML page instead of data (which the
      `blackmarble` library's own error message misleadingly reports as
      "invalid or expired token" — it isn't; verified by decoding the
      token's JWT payload directly).

**Real Sentinel-2 revisit is ~5 days at this latitude** (2-satellite
constellation, verified — not the "6 days" originally assumed), but usable
cadence is lower: clouds can make a fresh capture worthless (see
`monsoon_comparison.py`'s real 97.45%-cloud example). The workflow checks
**daily** and cleanly no-ops (`exit 0`, not an error) on days with nothing
new/usable — expected to actually do work roughly once a week, not daily.

**Scope, chosen signal-by-signal rather than wired in wholesale:**
produces `change_pct` (NDVI), `sar_change_score`/`sar_mean_abs_change_db`,
`road_access_score`/`nearest_road_distance_m`/`nearest_road_type`,
`disturbance_area_m2`, `ntl_delta`, and a `confidence_score`/
`confidence_tier` blend — all reusing `score_triggers.py`'s own functions
and calibrated weights directly (imported, not reimplemented), so a live
trigger's `confidence_score` means exactly the same thing as a
static-pipeline one.

**`ntl_delta` runs on its own yearly cadence, not the 5-day one** —
correcting an earlier draft of this doc that assumed VIIRS composites were
monthly. They're not: `download_nightlights.py`'s own docstring says the
VNP46A4 product is deliberately **annual** (to average out cloud/moonlight/
seasonal noise). Re-checking every 5-day cycle would find nothing new that
often, and there's real processing lag (a year's composite isn't ready
until well after that year ends) — so `ensure_ntl()` only re-fetches when
the target year (today's year minus 1, since the current year's composite
won't exist yet) advances past what's cached in `state.json`. The "before"
side stays permanently fixed at the original 2020 baseline (a legitimate
historical anchor, not something needing refresh); the "after" side
refreshes yearly. Every trigger carries `ntl_before_year`/`ntl_after_year`
explicitly so nobody mistakes an annual average for "tonight's lights."
Needs `EARTHDATA_TOKEN` (same credential `download_nightlights.py` already
uses) — if unset, NTL fields come back `null` with a clear warning and the
rest of the cycle still runs; NTL is additive, not a hard dependency.

One thing stays deliberately **excluded**:
- **`legality_assessment`/`legality_flag`** — `real_data/mock_permits.json`
  is explicitly mock data (`score_triggers.py`'s own docstring says so). An
  unattended loop with no human-review gate is exactly where a
  fabricated-looking "legal determination" is most dangerous — it would
  read as authoritative to anyone downstream. Every other signal here
  (SAR, roads, NTL) is either genuinely live or a transparently-labeled
  real historical composite; permits here are fabricated, so they don't
  get the same treatment.

`status` is set to `"PENDING_REVIEW"` (same as the static pipeline) since
an NDVI+SAR+road+NTL-corroborated trigger is genuinely ready for a human
officer — the missing legality field doesn't block that, it just means
they won't see a (mock-based) pre-computed verdict, which is correct.
Output goes to `output/triggers_live.json`, a superset-compatible schema,
not a byte-identical replacement for `output/triggers_scored.json`.

**Seasonality caveat, measured not assumed:** the original `real_data/`
pair was deliberately both Januaries specifically to control for seasonal
NDVI variation. Steady-state live cycles (each new capture vs. the
immediately-previous one, ~5 days apart) stay same-season automatically.
The one exception is the *bootstrap* cycle — comparing the fixed 2024-01
baseline against whatever season the first live capture lands in. Tested
this directly (using `real_data_2026/`'s 2026-06 scene as a stand-in for
"first live capture"): got 25 triggers, notably more than the
seasonally-controlled 2020-vs-2026 comparison's 14 — consistent with some
of that gap being January-vs-June vegetation phenology, not real
disturbance. Treat the first live cycle's output with extra skepticism.

**Repo growth:** each cycle's downloaded bands are committed alongside
`state.json` (needed so the next run, on a fresh ephemeral checkout, has
the previous scene to diff against) — measured ~1.1MB/cycle, so roughly
~80MB/year at the real cadence. Fine for now; revisit (pruning, Git LFS) if
it becomes a real problem.

**Verified live against the real CDSE API, not just stand-in data.** First
real run (2026-08-27) found genuinely zero usable Sentinel-2 scenes in the
default 30-day window — correctly diagnosed as monsoon-season cloud cover,
not a bug (the most recent cloud<=20% scene all year, checked directly,
turned out to be 2026-06-05, ~3 months prior). Widening the search
(`--lookback-days`, a real CLI flag, not a hack) to reach that scene and
running the full pipeline against it surfaced and fixed two real bugs:
1. CDSE's backend raises `OpenEoApiError(code="NoDataAvailable")` for a
   genuinely empty result instead of returning an empty list -- now caught
   and treated as "nothing found," same as the empty-list case; any other
   error code still propagates.
2. The SAR "before" window was zero-width (same date passed as both start
   and end of a gt/lt range -- matches nothing), and even non-zero, was far
   too narrow for Sentinel-1's revisit cadence. Fixed to a real +/-15-day
   window on both sides.

After both fixes, a live run produced 29 real triggers against the fresh
2026-06-05 capture, **every one with a real SAR dB value and a real
confidence_score/tier** — the full NDVI+SAR+roads chain confirmed working
against the live API, not a substitute.

**`ntl_delta` is now also fully verified live**, closing the last gap.
Getting there needed one more real fix, not just a token: NASA's LAADS
DAAC (where `download_nightlights.py`'s VNP46A4 data actually lives)
requires a one-time authorization/EULA click on the Earthdata account
*specifically for LAADS* — a valid, unexpired `EARTHDATA_TOKEN` alone
isn't enough. The symptom was exact and traceable: the unauthenticated
manifest search succeeded (found a real 80.3MB file), but the
token-authenticated file download got redirected to an HTML page instead
of the data, which `blackmarble`'s own error message misreports as
"invalid or expired token" (confirmed the token itself was fine by
decoding its JWT payload directly — issued minutes earlier, valid for
another 2 months). Fix was a one-time login at
[ladsweb.modaps.eosdis.nasa.gov](https://ladsweb.modaps.eosdis.nasa.gov/)
to trigger that authorization. After that, a full live run downloaded the
real 2025 VNP46A4 composite (6x5px, matching `download_nightlights.py`'s
own documented "correct, not a bug" expectation for this bbox) and
produced `ntl_delta` values (with correct `2020->2025` year labeling) on
28 of 29 triggers — the 29th correctly shows `null`, a real per-pixel
case where that specific location's NTL data wasn't valid, handled
exactly as designed, not a bug.

**Every signal in this pipeline -- NDVI, SAR, roads, NTL, and the
confidence blend -- is now verified against real live government/agency
APIs** (Copernicus Data Space for Sentinel-2/1, NASA Earthdata for VIIRS
Black Marble), through the actual production entry point
(`pipeline/live_monitor.py`'s `main()`), not substitute data or isolated
function tests.

**The `--live` map-integration path (ingest + re-cluster) is also now
verified end to end, on a real small-batch test, not a full 29-trigger
dump.** Found and fixed one more real bug this way, before it could touch
production data: `main.py`'s `TriggerPayload` Pydantic model declares
`legality_flag`/`legality_assessment` as **required** `str`/`dict` --
stricter than the actual database column (which is nullable). Sending
`null` (the original design) got a real `422` from the live API, caught
by testing 2 triggers first instead of all 29. Fixed by reusing
`"INSUFFICIENT_DATA"` -- the exact same value `main.py`'s own
`site_legality_flag()` already uses for "no legality_flag recorded" -- not
inventing a new convention, and not a fabricated legal claim; it honestly
means "no mock-permit check was run here." The assessment dict is a plain
explanatory note, confirmed safe against `build_brief_prompt()`, which
only renders `{"value":..., "data_source":...}`-shaped entries and
silently skips anything else. After the fix: 2 real alerts ingested
(`HTTP 200`, real new database IDs), re-clustered, and confirmed via a
direct `GET /api/v1/alerts` call to have a real non-null `cluster_id` --
genuinely visible on the map, verified by querying the live API directly,
not assumed.

## Contract with Member 2 (Verification/Scoring)
Each trigger in `output/triggers.json` looks like:
```json
{
  "trigger_id": "MSS-897708",
  "site_id": "AOI-07-BALAGHAT",
  "lat": 21.84675,
  "lon": 80.15425,
  "change_pct": 83.9,
  "area_px": 25,
  "boundary_status": "boundary_violation",
  "detected_at": "2026-08-21T...",
  "source": "Sentinel-2 NDVI change detection",
  "status": "PENDING_SCORING"
}
```
Member 2's scoring script should read this JSON, add `ntl_delta` (VIIRS
nighttime-light signal) and `road_access_score` (OSM proximity signal),
compute a fused `confidence_score`, and flip `status` to `"PENDING_REVIEW"`
before handing off to Pair B's `POST /trigger` endpoint.
