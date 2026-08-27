"""
BhuNetra -- seed / update the `aois` registry table from pipeline/aois.py.

The AOI table (main.py `Aoi` model) and pipeline/aois.py must agree; this
script is the one-way sync: pipeline/aois.py is authored by hand, this
upserts it into the DB so `GET /api/v1/aois` can serve it.

Idempotent -- safe to re-run after adding a region to pipeline/aois.py.
Never deletes rows (a region pulled from the config keeps its DB row and
its alerts; remove it by hand if you really mean to).

Run with DATABASE_URL set, same as db/migrate.py:
    python db/seed_aois.py
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

# pipeline/aois.py lives under the repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.aois import AOIS  # noqa: E402

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is missing!")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)

UPSERT = text("""
    INSERT INTO aois (id, name, state, district, mineral, center_lat, center_lon,
                      bbox_w, bbox_s, bbox_e, bbox_n, has_imagery, lease_source)
    VALUES (:id, :name, :state, :district, :mineral, :center_lat, :center_lon,
            :bbox_w, :bbox_s, :bbox_e, :bbox_n, :has_imagery, :lease_source)
    ON CONFLICT (id) DO UPDATE SET
        name = EXCLUDED.name,
        state = EXCLUDED.state,
        district = EXCLUDED.district,
        mineral = EXCLUDED.mineral,
        center_lat = EXCLUDED.center_lat,
        center_lon = EXCLUDED.center_lon,
        bbox_w = EXCLUDED.bbox_w,
        bbox_s = EXCLUDED.bbox_s,
        bbox_e = EXCLUDED.bbox_e,
        bbox_n = EXCLUDED.bbox_n,
        has_imagery = EXCLUDED.has_imagery,
        lease_source = EXCLUDED.lease_source;
""")


def run_seed():
    with engine.begin() as conn:
        for aoi_id, cfg in AOIS.items():
            bbox = cfg.get("bbox") or {}
            conn.execute(UPSERT, {
                "id": aoi_id,
                "name": cfg["name"],
                "state": cfg.get("state"),
                "district": cfg.get("district"),
                "mineral": cfg.get("mineral"),
                "center_lat": cfg.get("center", {}).get("lat"),
                "center_lon": cfg.get("center", {}).get("lon"),
                "bbox_w": bbox.get("west"),
                "bbox_s": bbox.get("south"),
                "bbox_e": bbox.get("east"),
                "bbox_n": bbox.get("north"),
                "has_imagery": 1 if cfg.get("has_imagery") else 0,
                "lease_source": "hand-traced" if cfg.get("lease_boundary_valid") else None,
            })
            print(f"  upserted {aoi_id}  ({cfg['name']})")
    print(f"Seeded {len(AOIS)} AOIs.")


if __name__ == "__main__":
    run_seed()
