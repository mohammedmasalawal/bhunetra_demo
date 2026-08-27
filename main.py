import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any, Dict

import requests
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import Column, DateTime, Float, Integer, String, Text, create_engine, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, declarative_base, sessionmaker
from geoalchemy2 import Geometry
from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import mapping, shape
from jose import JWTError, jwt
from passlib.context import CryptContext

# Load variables from the .env file into the system environment
load_dotenv()

# ==========================================
# 1. CONFIGURATION & RAILWAY TRAP FIX
# ==========================================
# Railway connection strings start with 'postgres://', but SQLAlchemy requires 'postgresql://'.
raw_db_url = os.getenv("DATABASE_URL", "postgresql://postgres:password@localhost:5432/postgres")
if raw_db_url.startswith("postgres://"):
    raw_db_url = raw_db_url.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    raw_db_url,
    pool_pre_ping=True,  # Checks if the connection is alive before querying
    pool_recycle=300,    # Automatically refreshes connections every 5 minutes
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ==========================================
# 2. DATABASE MODELS (PostGIS Schema)
# ==========================================
class Lease(Base):
    __tablename__ = "leases"
    id              = Column(Integer, primary_key=True, index=True)
    source          = Column(String)
    lessee_name     = Column(String)
    mineral_type    = Column(String)
    state           = Column(String)
    district        = Column(String)
    status          = Column(String, default="UNKNOWN")
    expiry_date     = Column(DateTime, nullable=True)
    geometry        = Column(Geometry(geometry_type='MULTIPOLYGON', srid=4326))


class Officer(Base):
    __tablename__ = "officers"
    id              = Column(Integer, primary_key=True, index=True)
    name            = Column(String, nullable=False)
    email           = Column(String, unique=True, nullable=False, index=True)
    password_hash   = Column(String, nullable=False)
    role            = Column(String, nullable=False)
    district        = Column(String, nullable=True)
    state           = Column(String, nullable=True)
    fcm_token       = Column(String, nullable=True)
    is_active       = Column(Integer, default=1)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id              = Column(Integer, primary_key=True, index=True)
    alert_id        = Column(Integer, nullable=False)
    officer_id      = Column(Integer, nullable=True)
    previous_status = Column(String, nullable=True)
    new_status      = Column(String, nullable=True)
    action          = Column(String, nullable=False)
    notes           = Column(String, nullable=True)
    timestamp       = Column(DateTime, default=datetime.utcnow)


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, index=True)
    location_name = Column(String, index=True)
    risk_score = Column(Float)
    status = Column(String, default="PENDING_OFFICER")  # PENDING_OFFICER, ESCALATED_DGM, RESOLVED
    created_at = Column(DateTime, default=datetime.utcnow)
    sla_deadline = Column(DateTime)
    trigger_id = Column(String, unique=True, index=True)
    site_id = Column(String)
    change_pct = Column(Float)
    boundary_status = Column(String)
    sar_change_score = Column(Float)
    sar_mean_abs_change_db = Column(Float)
    confidence_score = Column(Float)
    confidence_tier = Column(String)
    disturbance_area_m2 = Column(Float)
    ntl_delta = Column(Float)
    legality_flag = Column(String)
    legality_assessment = Column(JSONB)

    # Physical-site grouping (B3, db/cluster_sites.py) -- DISTINCT from
    # site_id above. site_id is the AOI identifier the pipeline sets on
    # every row for a site (e.g. "AOI-07-BALAGHAT"); cluster_id is the
    # DBSCAN grouping *within* an AOI, since one AOI can contain several
    # physically separate excavation fronts. Never overwrite site_id with
    # this.
    cluster_id = Column(Integer, nullable=True)

    # LLM officer briefing (B4) -- cached so the demo never depends on a
    # live model call. See build_brief_prompt()/call_gemini() below.
    brief_text = Column(Text, nullable=True)
    brief_generated_at = Column(DateTime, nullable=True)

    # PostGIS Spatial Column
    geometry = Column(Geometry(geometry_type='POLYGON', srid=4326))


class Aoi(Base):
    """A monitored mining region (Area Of Interest). One row per region the
    pipeline watches. Alert.site_id references Aoi.id. Kept in sync with
    pipeline/aois.py (same source data) -- db/seed_aois.py upserts from it."""
    __tablename__ = "aois"
    id          = Column(String, primary_key=True)   # e.g. "AOI-07-BAILADILA"
    name        = Column(String, nullable=False)     # "Bailadila Iron Ore Complex"
    state       = Column(String)
    district    = Column(String)
    mineral     = Column(String)
    center_lat  = Column(Float)
    center_lon  = Column(Float)
    bbox_w      = Column(Float, nullable=True)
    bbox_s      = Column(Float, nullable=True)
    bbox_e      = Column(Float, nullable=True)
    bbox_n      = Column(Float, nullable=True)
    has_imagery = Column(Integer, default=0)
    lease_source = Column(String, nullable=True)


# Create tables in Railway if they don't exist
Base.metadata.create_all(bind=engine)

# ==========================================
# 3. PYDANTIC SCHEMAS (API Contracts)
# ==========================================
class TriggerPayload(BaseModel):
    """Pair A sends this exactly to POST /api/v1/triggers"""

    location_name: str
    risk_score: float
    geojson_polygon: Dict[str, Any]
    trigger_id: str
    site_id: str
    change_pct: float
    boundary_status: str
    sar_change_score: float | None = None          # optional — SAR can be unavailable
    sar_mean_abs_change_db: float | None = None
    confidence_score: float | None = None
    ntl_delta: float | None = None
    disturbance_area_m2: float
    confidence_tier: str
    legality_flag: str
    legality_assessment: Dict[str, Any]
    sla_hours: int | None = None
    sla_deadline: datetime | None = None


class LoginRequest(BaseModel):
    email: str
    password: str


class AlertActionRequest(BaseModel):
    new_status: str
    notes: str


class SlaUpdateRequest(BaseModel):
    sla_deadline: datetime | None = None
    extension_hours: int | None = None
    reason: str = "Field officer schedule adjustment"


# ==========================================
# 4. SLA ESCALATION ENGINE (APScheduler)
# ==========================================
def check_and_escalate_slas():
    """Background worker - keeps alerts in manual triage mode unless explicitly escalated."""
    return



@asynccontextmanager
async def lifespan(app: FastAPI):
    # No background workers — all escalation is manual via officer UI
    yield

# ==========================================
# 5. FASTAPI APPLICATION SETUP
# ==========================================
app = FastAPI(title="BHUNETRA Spatial API", lifespan=lifespan)

# Allow Pair C (Frontend) to fetch data without CORS blocks.
# allow_origins=["*"] + allow_credentials=True is spec-invalid (browsers must
# reject a wildcard origin on a credentialed request) and some browsers will
# silently drop the response. Auth here is a Bearer token in the
# Authorization header, not a cookie, so credentials aren't needed —
# dropping allow_credentials keeps the wildcard origin working for Pair C
# without hitting that browser-side rejection.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/")
def root():
    """Plain landing response so hitting the bare service URL in a browser
    shows the API is alive, not FastAPI's default {"detail":"Not Found"}."""
    return {
        "service": "BHUNETRA Spatial API",
        "status": "ok",
        "docs": "/docs",
    }


@app.get("/health")
def health():
    return {"status": "ok"}


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()
JWT_SECRET = os.getenv("JWT_SECRET", "bhunetra_secret_key_2026")
JWT_ALGORITHM = "HS256"


def create_token(officer_id: int, role: str, state: str) -> str:
    payload = {
        "officer_id": officer_id,
        "role": role,
        "state": state or "",
        "exp": datetime.utcnow() + timedelta(hours=24)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def get_current_officer(
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    try:
        payload = jwt.decode(
            credentials.credentials,
            JWT_SECRET,
            algorithms=[JWT_ALGORITHM]
        )
        return payload
    except JWTError:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token. Please log in again."
        )

# ==========================================
# 6. LLM OFFICER BRIEFING (B4)
# ==========================================
# Calls Gemini from the backend only -- the key never leaves this process,
# never appears in a response body, and is never referenced from the
# frontend. Read from an environment variable only, same as JWT_SECRET.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
GEMINI_ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
GEMINI_TIMEOUT_S = 60  # gemini-3.6-flash is a "thinking" model; observed latency can exceed 20s

# A brief is considered stale (and eligible for regeneration) after this
# long. Nothing in the app currently mutates confidence_tier/legality_flag/
# disturbance_area_m2/boundary_status/legality_assessment on an existing
# alert post-ingest, so in practice this TTL -- not a change in the
# underlying data -- is what "stale" means today. If a re-scoring path is
# ever added, it should also clear brief_generated_at to force a refresh.
BRIEF_STALE_AFTER = timedelta(hours=24)


class BriefGenerationError(Exception):
    """Raised when the LLM call fails for any reason (missing key, timeout,
    rate limit, non-200 response, unexpected response shape). Callers turn
    this into a clear HTTP error instead of a 500."""


def is_brief_stale(alert: "Alert") -> bool:
    if not alert.brief_text or not alert.brief_generated_at:
        return True
    return datetime.utcnow() - alert.brief_generated_at > BRIEF_STALE_AFTER


def build_brief_prompt(alert: "Alert") -> str:
    """Builds the officer-briefing prompt. Must surface every
    legality_assessment check with its data_source tag (REAL vs MOCK) so
    the model can tell the officer which findings rest on real satellite
    geometry versus the mock permit registry -- that distinction is the
    whole point of this feature, not a nice-to-have."""
    assessment = alert.legality_assessment or {}
    check_lines = [
        f"- {check_name}: {details['value']} (data_source: {details['data_source']})"
        for check_name, details in assessment.items()
        if isinstance(details, dict) and "value" in details and "data_source" in details
    ]
    checks_block = "\n".join(check_lines) if check_lines else "No legality_assessment checks are recorded for this alert."

    return f"""You are drafting a short briefing note for a field officer reviewing a satellite-detected mining disturbance alert. Base your summary strictly on the data below -- do not add outside knowledge or speculation.

ALERT DATA
- confidence_tier: {alert.confidence_tier}
- legality_flag: {alert.legality_flag}
- disturbance_area_m2: {alert.disturbance_area_m2}
- boundary_status: {alert.boundary_status}

LEGALITY ASSESSMENT CHECKS (each tagged with its data source):
{checks_block}

INSTRUCTIONS
- Explicitly state which findings above rest on REAL satellite/geometry data and which rest on the MOCK permit registry (data_source: MOCK) -- the officer must be able to tell these apart at a glance.
- Write 3-5 sentences, plain language, for a field officer with no GIS background.
- HARD RULE: never write "illegal mining detected," "illegal mining confirmed," or any other assertion that mining here is illegal. This system flags candidates for human verification only -- it does not make legal determinations. Use hedged language only: "flagged for verification," "warrants an on-site check," "requires officer review," or equivalent.
- Do not invent facts that are not present in the data above.
"""


def call_gemini(prompt: str) -> str:
    if not GEMINI_API_KEY:
        raise BriefGenerationError("GEMINI_API_KEY is not configured on the server")

    try:
        resp = requests.post(
            GEMINI_ENDPOINT,
            params={"key": GEMINI_API_KEY},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=GEMINI_TIMEOUT_S,
        )
    except requests.RequestException as e:
        raise BriefGenerationError(f"Request to Gemini failed: {e}") from e

    if resp.status_code != 200:
        raise BriefGenerationError(f"Gemini returned HTTP {resp.status_code}: {resp.text[:300]}")

    try:
        data = resp.json()
        parts = data["candidates"][0]["content"]["parts"]
        # gemini-3.6-flash is a "thinking" model and can return more than
        # one part (e.g. an internal thought part alongside the final
        # answer) -- join every part that actually carries visible text
        # rather than assuming the answer is always parts[0].
        text = "".join(p["text"] for p in parts if "text" in p)
    except (KeyError, IndexError, ValueError) as e:
        raise BriefGenerationError(f"Unexpected Gemini response shape: {e}") from e

    if not text.strip():
        raise BriefGenerationError("Gemini returned no visible text in its response")

    return text.strip()

# ==========================================
# 7. FASTAPI ENDPOINTS
# ==========================================
@app.post("/api/v1/triggers")
def ingest_trigger(payload: TriggerPayload, db: Session = Depends(get_db)):
    """Pair A (Data Pipeline) uses this to insert detected anomalies."""

    # Convert incoming GeoJSON directly to Shapely shape, then to PostGIS WKB
    shapely_geom = shape(payload.geojson_polygon)
    postgis_geom = from_shape(shapely_geom, srid=4326)

    # Set SLA deadline based on payload-supplied deadline, hours, or multi-tiered severity:
    # Tier 1: POTENTIAL_VIOLATION with High Risk (>=75) or High Drop (>=50%) -> 24 Hours SLA
    # Tier 2: POTENTIAL_VIOLATION (Standard Encroachment) -> 48 Hours SLA
    # Tier 3: APPEARS_COMPLIANT / Routine Mine Expansion -> 72 Hours SLA
    if payload.sla_deadline is not None:
        deadline = payload.sla_deadline
    elif payload.sla_hours is not None:
        deadline = datetime.utcnow() + timedelta(hours=payload.sla_hours)
    else:
        if payload.legality_flag == "POTENTIAL_VIOLATION" and (payload.risk_score >= 75.0 or payload.change_pct >= 50.0):
            hours = 24
        elif payload.legality_flag == "POTENTIAL_VIOLATION":
            hours = 48
        else:
            hours = 72
        deadline = datetime.utcnow() + timedelta(hours=hours)


    new_alert = Alert(
        location_name=payload.location_name,
        risk_score=payload.risk_score,
        sla_deadline=deadline,
        geometry=postgis_geom,
        trigger_id=payload.trigger_id,
        site_id=payload.site_id,
        change_pct=payload.change_pct,
        boundary_status=payload.boundary_status,
        sar_change_score=payload.sar_change_score,
        sar_mean_abs_change_db=payload.sar_mean_abs_change_db,
        confidence_score=payload.confidence_score,
        confidence_tier=payload.confidence_tier,
        disturbance_area_m2=payload.disturbance_area_m2,
        ntl_delta=payload.ntl_delta,
        legality_flag=payload.legality_flag,
        legality_assessment=payload.legality_assessment,
    )
    db.add(new_alert)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"Trigger {payload.trigger_id} already ingested")

    db.refresh(new_alert)
    return {"status": "success", "alert_id": new_alert.id}


@app.get("/api/v1/alerts")
def get_alerts(aoi: str | None = None, db: Session = Depends(get_db)):
    """Pair C (Frontend) uses this to populate the Next.js/Leaflet map.
    Optional ?aoi=<id> narrows to one region (matches Alert.site_id)."""

    query = db.query(Alert)
    if aoi:
        query = query.filter(Alert.site_id == aoi)
    alerts = query.all()
    feature_collection = {"type": "FeatureCollection", "features": []}

    for alert in alerts:
        shapely_geom = to_shape(alert.geometry)
        geom_geojson = mapping(shapely_geom)

        feature = {
            "type": "Feature",
            "geometry": geom_geojson,
            "properties": {
                "id": alert.id,
                "trigger_id": alert.trigger_id,
                "site_id": alert.site_id,
                "cluster_id": alert.cluster_id,
                "location_name": alert.location_name,
                "risk_score": alert.risk_score,
                "change_pct": alert.change_pct,
                "boundary_status": alert.boundary_status,
                "sar_change_score": alert.sar_change_score,
                "confidence_score": alert.confidence_score,
                "confidence_tier": alert.confidence_tier,
                "disturbance_area_m2": alert.disturbance_area_m2,
                "ntl_delta": alert.ntl_delta,
                "legality_flag": alert.legality_flag,
                "legality_assessment": alert.legality_assessment,
                "status": alert.status,
                "sla_deadline": alert.sla_deadline.isoformat(),
                "brief_text": alert.brief_text,
                "brief_generated_at": alert.brief_generated_at.isoformat() if alert.brief_generated_at else None,
            }
        }
        feature_collection["features"].append(feature)

    return feature_collection


@app.get("/api/v1/aois")
def get_aois(db: Session = Depends(get_db)):
    """The registry of monitored mining regions, each with live alert
    counts. The frontend region selector renders this. Regions with no
    alerts yet (just onboarded) still appear, with zero counts."""
    aois = db.query(Aoi).all()
    alerts = db.query(Alert).all()

    by_site: Dict[str, list] = {}
    for a in alerts:
        by_site.setdefault(a.site_id or "", []).append(a)

    out = []
    for r in aois:
        members = by_site.get(r.id, [])
        clusters = {m.cluster_id for m in members if m.cluster_id is not None}
        out.append({
            "id": r.id,
            "name": r.name,
            "state": r.state,
            "district": r.district,
            "mineral": r.mineral,
            "center": {"lat": r.center_lat, "lon": r.center_lon},
            "bbox": (
                {"west": r.bbox_w, "south": r.bbox_s, "east": r.bbox_e, "north": r.bbox_n}
                if r.bbox_w is not None else None
            ),
            "has_imagery": bool(r.has_imagery),
            "alert_count": len(members),
            "site_count": len(clusters),
            "escalated_count": sum(1 for m in members if m.status == "ESCALATED_DGM"),
        })
    out.sort(key=lambda x: x["name"])
    return {"aois": out}


# ==========================================
# SATELLITE EVIDENCE IMAGERY
# ==========================================
# The evidence imagery is produced offline by the detection pipeline
# (pipeline/*.py write the PNGs into real_data_bailadila/ etc.); a curated
# set is published as static assets the frontend serves from its own
# /public/imagery/ folder. This endpoint is the authoritative catalogue of
# which bands exist for a given alert's AOI and their metadata -- the
# frontend renders exactly what this returns and shows an empty state when
# there is nothing on file, instead of carrying its own hardcoded list.
#
# Keyed by AOI (Alert.site_id). Today there is a single AOI; add entries
# here as new AOIs get their own imagery.
_BAILADILA_BANDS = [
    {
        "id": "ndvi",
        "title": "NDVI Vegetation Loss",
        "badge": "Optical",
        "badge_color": "#1f7a4d",
        "sensor": "Sentinel-2 MSI (Band 4 Red + Band 8 NIR)",
        "resolution": "10m / px",
        "description": "Normalized Difference Vegetation Index tracking vegetation canopy loss. Red patches indicate severe surface clearing and excavation.",
        "before": "/imagery/ndvi_before.png",
        "after": "/imagery/ndvi_after.png",
        "diff": "/imagery/ndvi_diff.png",
        "composite": "/imagery/ndvi_preview.png",
        "has_comparison": True,
    },
    {
        "id": "ntl",
        "title": "Nighttime Lights (NTL)",
        "badge": "Thermal / Radiance",
        "badge_color": "#b3720c",
        "sensor": "VIIRS Day/Night Band (Black Marble)",
        "resolution": "500m / px",
        "description": "Nighttime radiance emissions indicating night-shift mining activity, generator banks, and heavy mineral transport outside permitted hours.",
        "before": "/imagery/ntl_before.png",
        "after": "/imagery/ntl_after.png",
        "diff": "/imagery/ntl_diff.png",
        "composite": "/imagery/ntl_diff.png",
        "has_comparison": True,
    },
    {
        "id": "sar",
        "title": "SAR Radar (VV Backscatter)",
        "badge": "Radar (Cloud-Penetrating)",
        "badge_color": "#2563eb",
        "sensor": "Sentinel-1 C-Band SAR (Lee-Filtered VV)",
        "resolution": "10m / px",
        "description": "Active microwave radar backscatter in dB. Surface roughness and structural pit excavation alters dielectric scattering regardless of clouds.",
        "before": "/imagery/sar_before.png",
        "after": "/imagery/sar_after.png",
        "diff": "/imagery/sar_diff.png",
        "composite": "/imagery/sar_diff.png",
        "has_comparison": True,
    },
    {
        "id": "monsoon",
        "title": "Monsoon Radar vs Optical",
        "badge": "Cloud Invariance",
        "badge_color": "#7c3aed",
        "sensor": "Sentinel-2 (Cloud-Obscured) vs Sentinel-1 SAR",
        "resolution": "10m / px",
        "description": "Demonstrating SAR radar's ability to maintain detection capability during heavy monsoon cloud cover when optical satellites are blinded.",
        "after": "/imagery/monsoon_detection.png",
        "composite": "/imagery/monsoon_comparison.png",
        "has_comparison": False,
    },
    {
        "id": "overlay",
        "title": "Trigger Anomaly Overlay",
        "badge": "Detections",
        "badge_color": "#d63a1a",
        "sensor": "Multi-Sensor Fusion Engine",
        "resolution": "10m / px",
        "description": "Automated candidate triggers overlaid with bounding boxes and cluster centroids onto real satellite imagery.",
        "composite": "/imagery/triggers_overlay.png",
        "has_comparison": False,
    },
    {
        "id": "full",
        "title": "Multi-Spectral Overview",
        "badge": "Full Evidence",
        "badge_color": "#e8420c",
        "sensor": "Multi-Satellite Composite",
        "resolution": "Multi-resolution",
        "description": "Comprehensive evidence sheet combining NDVI, SAR VV dB, SAR Lee-filtered change, and VIIRS Nighttime Lights radiance.",
        "composite": "/imagery/full_preview.png",
        "has_comparison": False,
    },
]

IMAGERY_BY_AOI = {
    "AOI-07-BAILADILA": _BAILADILA_BANDS,
    "AOI-07-BALAGHAT": _BAILADILA_BANDS,  # legacy site_id, same AOI
}


@app.get("/api/v1/alerts/{alert_id}/imagery")
def get_alert_imagery(alert_id: int, db: Session = Depends(get_db)):
    """Evidence-imagery catalogue for one alert, keyed by its AOI. Returns
    an empty band list (not a 404) when the AOI has no imagery on file so
    the frontend can render a clean empty state."""
    alert = db.query(Alert).filter(Alert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    return {
        "alert_id": alert.id,
        "aoi": alert.site_id,
        "bands": IMAGERY_BY_AOI.get(alert.site_id or "", []),
    }


@app.get("/api/v1/audit-logs")
def get_audit_logs(db: Session = Depends(get_db)):
    """Pair C (Frontend) history view -- permanent record of every
    status/SLA change. The frontend falls back to its local log store if
    this 404s, so returning a real (possibly empty) list keeps the browser
    console clean."""
    rows = db.query(AuditLog).order_by(AuditLog.timestamp.desc()).all()

    officer_names = {o.id: o.name for o in db.query(Officer).all()}
    alerts = {a.id: a for a in db.query(Alert).all()}

    return {
        "audit_logs": [
            {
                "id": r.id,
                "alert_id": r.alert_id,
                "trigger_id": alerts[r.alert_id].trigger_id if r.alert_id in alerts else None,
                "location_name": alerts[r.alert_id].location_name if r.alert_id in alerts else None,
                "officer_id": r.officer_id,
                "officer_name": officer_names.get(r.officer_id),
                "previous_status": r.previous_status,
                "new_status": r.new_status,
                "action": r.action,
                "notes": r.notes,
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            }
            for r in rows
        ]
    }


@app.post("/api/v1/simulate/advance-sla")
def advance_time():
    """DISABLED — this endpoint previously caused mass auto-escalation."""
    return {"status": "disabled", "message": "advance-sla is disabled. Use reset-sla to reset all alerts."}


@app.post("/api/v1/simulate/reset-sla")
def reset_sla(db: Session = Depends(get_db)):
    """Resets all alerts to PENDING_OFFICER with fresh multi-tiered future SLA deadlines (24h/48h/72h)."""
    now = datetime.utcnow()
    alerts = db.query(Alert).all()
    for alert in alerts:
        alert.status = "PENDING_OFFICER"
        if alert.legality_flag == "POTENTIAL_VIOLATION" and ((alert.risk_score or 0) >= 75.0 or (alert.change_pct or 0) >= 50.0):
            hours = 24
        elif alert.legality_flag == "POTENTIAL_VIOLATION":
            hours = 48
        else:
            hours = 72
        alert.sla_deadline = now + timedelta(hours=hours)

    db.commit()
    return {
        "status": "success",
        "message": f"Successfully reset {len(alerts)} alerts to PENDING_OFFICER with active tiered SLA countdown deadlines."
    }



DEMO_OFFICERS = {
    "dgm@bhunetra.gov.in": {"name": "Priya Sharma (DGM Director)", "pass": "dgm123", "role": "DGM_ADMIN", "state": "Chhattisgarh"},
    "dgm.admin@bhunetra.gov.in": {"name": "Priya Sharma (DGM Director)", "pass": "dgm@123", "role": "DGM_ADMIN", "state": "Chhattisgarh"},
    "dgm@bhunetra.demo": {"name": "Priya Sharma (DGM Director)", "pass": "dgm123", "role": "DGM_ADMIN", "state": "Chhattisgarh"},
    "officer@bhunetra.gov.in": {"name": "Field Inspector R. Verma", "pass": "officer123", "role": "FIELD_OFFICER", "state": "Chhattisgarh"},
    "field@bhunetra.demo": {"name": "Rajesh Kumar (Field Officer)", "pass": "field123", "role": "FIELD_OFFICER", "state": "Chhattisgarh"},
    "ibm@bhunetra.demo": {"name": "Anil Mishra (IBM Director)", "pass": "ibm123", "role": "DGM_ADMIN", "state": "Central"},
}


@app.post("/api/v1/auth/login")
def login(request: LoginRequest, db: Session = Depends(get_db)):
    # 1. Check in database first
    officer = db.query(Officer).filter(
        Officer.email == request.email,
        Officer.is_active == 1
    ).first()

    if officer and pwd_context.verify(request.password, officer.password_hash):
        token = create_token(
            officer_id=officer.id,
            role=officer.role,
            state=officer.state
        )
        return {
            "access_token": token,
            "token_type": "bearer",
            "role": officer.role,
            "name": officer.name
        }

    # 2. Check demo accounts and seed into DB on demand
    req_email = request.email.strip().lower()
    if req_email in DEMO_OFFICERS:
        demo = DEMO_OFFICERS[req_email]
        if request.password == demo["pass"]:
            if not officer:
                new_officer = Officer(
                    name=demo["name"],
                    email=req_email,
                    password_hash=pwd_context.hash(demo["pass"]),
                    role=demo["role"],
                    district="Dantewada / Bastar",
                    state=demo["state"],
                    is_active=1
                )
                db.add(new_officer)
                try:
                    db.commit()
                    db.refresh(new_officer)
                    officer_id = new_officer.id
                except Exception:
                    db.rollback()
                    officer_id = 99
            else:
                officer_id = officer.id

            token = create_token(
                officer_id=officer_id,
                role=demo["role"],
                state=demo["state"]
            )
            return {
                "access_token": token,
                "token_type": "bearer",
                "role": demo["role"],
                "name": demo["name"]
            }

    raise HTTPException(
        status_code=401,
        detail="Incorrect email or password"
    )



@app.patch("/api/v1/alerts/{alert_id}/action")
def officer_action(
    alert_id: int,
    request: AlertActionRequest,
    db: Session = Depends(get_db),
    current_officer: dict = Depends(get_current_officer)
):
    # Find the alert in database
    alert = db.query(Alert).filter(Alert.id == alert_id).first()

    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    # Remember what status it was before changing
    previous_status = alert.status

    # Update the status
    alert.status = request.new_status

    # Write to audit log — permanent record of this action
    db.add(AuditLog(
        alert_id=alert_id,
        officer_id=current_officer["officer_id"],
        action="STATUS_UPDATED",
        previous_status=previous_status,
        new_status=request.new_status,
        notes=request.notes,
        timestamp=datetime.utcnow()
    ))

    db.commit()

    return {
        "status": "updated",
        "alert_id": alert_id,
        "previous_status": previous_status,
        "new_status": request.new_status,
        "updated_by": current_officer["officer_id"]
    }


@app.patch("/api/v1/alerts/{alert_id}/sla")
def update_alert_sla(
    alert_id: int,
    request: SlaUpdateRequest,
    db: Session = Depends(get_db),
    current_officer: dict = Depends(get_current_officer)
):
    """Allows field officers or DGM admins to adjust or extend the SLA countdown."""
    alert = db.query(Alert).filter(Alert.id == alert_id).first()

    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    old_deadline = alert.sla_deadline
    if request.sla_deadline is not None:
        alert.sla_deadline = request.sla_deadline
    elif request.extension_hours is not None:
        base = alert.sla_deadline if alert.sla_deadline and alert.sla_deadline > datetime.utcnow() else datetime.utcnow()
        alert.sla_deadline = base + timedelta(hours=request.extension_hours)
    else:
        raise HTTPException(status_code=400, detail="Must provide sla_deadline or extension_hours")

    # Record in tamper-proof audit trail
    db.add(AuditLog(
        alert_id=alert_id,
        officer_id=current_officer["officer_id"],
        action="SLA_DEADLINE_UPDATED",
        previous_status=alert.status,
        new_status=alert.status,
        notes=f"SLA deadline updated from {old_deadline.isoformat() if old_deadline else 'None'} to {alert.sla_deadline.isoformat()}. Reason: {request.reason}",
        timestamp=datetime.utcnow()
    ))

    db.commit()

    return {
        "status": "success",
        "alert_id": alert_id,
        "previous_deadline": old_deadline.isoformat() if old_deadline else None,
        "new_deadline": alert.sla_deadline.isoformat(),
        "updated_by": current_officer["officer_id"]
    }



@app.get("/api/v1/leases")
def get_lease_boundaries(aoi: str | None = None, db: Session = Depends(get_db)):
    """Pair C (Frontend) uses this to draw legal lease-boundary polygons on
    the map. Restored after it was dropped in the widened-schema rewrite —
    it's independent of the trigger/alert legality work (that now lives in
    TriggerPayload.legality_assessment instead of the old inline
    run_legal_check()/legal_status columns, which stay removed).

    Optional ?aoi=<id>: leases carry no site_id, so this narrows by the
    AOI's state/district. An AOI with neither set returns all leases."""

    sql = """
        SELECT id, source, lessee_name, mineral_type, status, state, district,
               ST_AsGeoJSON(geometry) as geom_json
        FROM leases
    """
    params: Dict[str, Any] = {}
    if aoi:
        region = db.query(Aoi).filter(Aoi.id == aoi).first()
        if region and (region.state or region.district):
            clauses = []
            if region.state:
                clauses.append("state = :state")
                params["state"] = region.state
            if region.district:
                clauses.append("district = :district")
                params["district"] = region.district
            sql += " WHERE " + " AND ".join(clauses)
    sql += " LIMIT 500;"

    leases = db.execute(text(sql), params).fetchall()

    feature_collection = {"type": "FeatureCollection", "features": []}

    for lease in leases:
        if lease.geom_json is None:
            continue
        feature_collection["features"].append({
            "type": "Feature",
            "geometry": json.loads(lease.geom_json),
            "properties": {
                "id": lease.id,
                "source": lease.source,
                "lessee_name": lease.lessee_name,
                "mineral_type": lease.mineral_type,
                "status": lease.status,
            }
        })

    return feature_collection


# Most-severe-wins order for site-level legality (B3). A legality_flag that
# isn't one of the three the pipeline emits (e.g. None, from alerts
# ingested before this field existed) is treated as INSUFFICIENT_DATA --
# "we don't know" is the honest default, not "compliant."
SITE_LEGALITY_SEVERITY = ["POTENTIAL_VIOLATION", "INSUFFICIENT_DATA", "APPEARS_COMPLIANT"]


def site_legality_flag(members: list) -> str:
    flags = {
        m.legality_flag if m.legality_flag in SITE_LEGALITY_SEVERITY else "INSUFFICIENT_DATA"
        for m in members
    }
    for level in SITE_LEGALITY_SEVERITY:
        if level in flags:
            return level
    return "APPEARS_COMPLIANT"


@app.get("/api/v1/sites")
def get_sites(aoi: str | None = None, db: Session = Depends(get_db)):
    """Groups alerts into physical mining sites by cluster_id (computed
    offline by db/cluster_sites.py -- see that script for why DBSCAN and
    why eps=400m). Alerts that haven't been clustered yet (cluster_id is
    still NULL) are excluded rather than shown as a misleading site of
    their own. Optional ?aoi=<id> narrows to one region."""
    query = db.query(Alert).filter(Alert.cluster_id.isnot(None))
    if aoi:
        query = query.filter(Alert.site_id == aoi)
    alerts = query.all()

    clusters: Dict[int, list] = {}
    for alert in alerts:
        clusters.setdefault(alert.cluster_id, []).append(alert)

    sites = []
    for cluster_id, members in clusters.items():
        centroids = [to_shape(m.geometry).centroid for m in members]
        lons = [c.x for c in centroids]
        lats = [c.y for c in centroids]

        sites.append({
            "cluster_id": cluster_id,
            "member_count": len(members),
            "alert_ids": [m.id for m in members],
            "trigger_ids": [m.trigger_id for m in members],
            "total_disturbance_area_m2": round(sum(m.disturbance_area_m2 or 0 for m in members), 1),
            "centroid": {
                "lon": round(sum(lons) / len(lons), 6),
                "lat": round(sum(lats) / len(lats), 6),
            },
            # Most-severe-wins: a site straddling the lease boundary is an
            # encroachment, not a compliant site with a rounding error.
            "legality_flag": site_legality_flag(members),
        })

    sites.sort(key=lambda s: s["cluster_id"])
    return {"sites": sites}


@app.post("/api/v1/alerts/{alert_id}/brief")
def generate_brief(alert_id: int, db: Session = Depends(get_db)):
    """Generates (or returns the cached) LLM officer briefing for one
    alert. See build_brief_prompt()/call_gemini() above for the guardrails
    (hedged language only, REAL vs MOCK data-source callouts)."""
    alert = db.query(Alert).filter(Alert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    if not is_brief_stale(alert):
        return {
            "alert_id": alert.id,
            "brief_text": alert.brief_text,
            "generated_at": alert.brief_generated_at.isoformat(),
            "cached": True,
        }

    prompt = build_brief_prompt(alert)
    try:
        brief_text = call_gemini(prompt)
    except BriefGenerationError as e:
        # A failed LLM call is a normal, expected condition (rate limit,
        # timeout, bad key) -- surface it as a clear 503, never a bare 500.
        raise HTTPException(status_code=503, detail=f"Brief generation failed: {e}")

    alert.brief_text = brief_text
    alert.brief_generated_at = datetime.utcnow()
    db.commit()

    return {
        "alert_id": alert.id,
        "brief_text": brief_text,
        "generated_at": alert.brief_generated_at.isoformat(),
        "cached": False,
    }

# ==========================================
# 8. LOCAL SERVER RUNNER
# ==========================================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
