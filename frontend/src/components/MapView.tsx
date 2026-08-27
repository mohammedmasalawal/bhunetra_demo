"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  MapContainer,
  TileLayer,
  Polygon,
  CircleMarker,
  Tooltip,
  Popup,
  useMap,
  useMapEvents,
} from "react-leaflet";
import type { LatLngBoundsExpression, LatLngTuple } from "leaflet";
import type { AlertFeature, Aoi, LeaseFeature, Site } from "@/lib/types";
import { polygonToLatLngs, polygonCentroid, formatCoordinates, copyCoordinatesToClipboard } from "@/lib/geo";
import { formatArea, formatPercent, formatScore, legalityMeta } from "@/lib/format";
import { LayersIcon, CopyIcon, CheckIcon, CrosshairIcon, InfoIcon } from "./icons";



// Pre-fit view only -- FitBounds immediately reframes to the actual data.
// [22, 79] ≈ geographic centre of India for the "All Regions" view.
const NATIONAL_CENTER: LatLngTuple = [22.0, 79.0];

type BaseMapType = "satellite" | "osm" | "dark";

const BASEMAPS: Record<
  BaseMapType,
  { name: string; url: string; attribution: string; maxZoom: number; maxNativeZoom: number }
> = {
  satellite: {
    name: "Satellite",
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    attribution:
      "Tiles &copy; Esri &mdash; Source: Esri, i-cubed, USDA, USGS, AEX, GeoEye, Getmapping, Aerogrid, IGN, IGP, UPR-EGP, and the GIS User Community",
    maxZoom: 20,
    maxNativeZoom: 17,
  },
  osm: {
    name: "Street Map",
    url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    attribution: "&copy; OpenStreetMap contributors",
    maxZoom: 20,
    maxNativeZoom: 19,
  },
  dark: {
    name: "Dark Canvas",
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}",
    attribution: "Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ",
    maxZoom: 20,
    maxNativeZoom: 16,
  },
};


function FitBounds({ bounds }: { bounds: LatLngBoundsExpression | null }) {
  const map = useMap();
  useEffect(() => {
    if (bounds) {
      map.fitBounds(bounds, { padding: [50, 50], maxZoom: 15 });
    }
  }, [bounds, map]);
  return null;
}


function LiveCoordinateTracker({ onCoordChange }: { onCoordChange: (lat: number, lon: number, zoom: number) => void }) {
  const map = useMapEvents({
    mousemove(e) {
      onCoordChange(e.latlng.lat, e.latlng.lng, map.getZoom());
    },
    zoomend() {
      const center = map.getCenter();
      onCoordChange(center.lat, center.lng, map.getZoom());
    },
  });
  return null;
}

export default function MapView({
  alerts,
  sites,
  leases,
  selectedSiteId,
  selectedAlertId,
  onSelectSite,
  onSelectAlert,
  region = null,
  allRegions = [],
}: {
  alerts: AlertFeature[];
  sites: Site[];
  leases: LeaseFeature[];
  selectedSiteId: number | null;
  selectedAlertId: number | null;
  onSelectSite: (id: number) => void;
  onSelectAlert: (id: number) => void;
  region?: Aoi | null;
  allRegions?: Aoi[];
}) {
  const mapCenter: LatLngTuple =
    region && region.center.lat != null && region.center.lon != null
      ? [region.center.lat, region.center.lon]
      : NATIONAL_CENTER;
  const regionLabel = region
    ? [region.name, region.district, region.state].filter(Boolean).join(" · ")
    : (() => {
        const states = new Set(
          allRegions.filter((a) => a.alert_count > 0 && a.state).map((a) => a.state)
        );
        const siteTotal = allRegions.reduce((n, a) => n + a.site_count, 0);
        return states.size
          ? `All Regions — ${siteTotal} site${siteTotal === 1 ? "" : "s"} across ${states.size} state${states.size === 1 ? "" : "s"}`
          : "All Regions";
      })();
  const [basemap, setBasemap] = useState<BaseMapType>("satellite");
  const [showLabels, setShowLabels] = useState(true);
  const [showLeases, setShowLeases] = useState(true);
  const [showTriggers, setShowTriggers] = useState(true);
  const [showPolygons, setShowPolygons] = useState(true);
  const [showLayersMenu, setShowLayersMenu] = useState(false);

  const [cursorCoords, setCursorCoords] = useState<{ lat: number; lon: number; zoom: number }>({
    lat: mapCenter[0],
    lon: mapCenter[1],
    zoom: 13,
  });
  const [copiedCoord, setCopiedCoord] = useState(false);

  const initialBounds = useMemo<LatLngBoundsExpression | null>(() => {
    const pts: LatLngTuple[] = [];
    for (const a of alerts) {
      for (const ring of polygonToLatLngs(a.geometry)) {
        for (const p of ring as LatLngTuple[]) pts.push(p);
      }
    }
    for (const l of leases) {
      for (const ring of polygonToLatLngs(l.geometry)) {
        for (const p of ring as LatLngTuple[]) pts.push(p);
      }
    }
    if (pts.length === 0) {
      const points: LatLngTuple[] = sites.map((s) => [s.centroid.lat, s.centroid.lon]);
      return points.length ? (points as LatLngBoundsExpression) : null;
    }
    return pts as LatLngBoundsExpression;
  }, [alerts, leases, sites]);

  const selectedSiteMembers = useMemo(
    () => (selectedSiteId != null ? alerts.filter((a) => a.properties.cluster_id === selectedSiteId) : []),
    [alerts, selectedSiteId]
  );

  const selectedBounds = useMemo<LatLngBoundsExpression | null>(() => {
    if (selectedSiteMembers.length === 0) return null;
    const pts: LatLngTuple[] = [];
    for (const a of selectedSiteMembers) {
      for (const ring of polygonToLatLngs(a.geometry)) {
        for (const p of ring as LatLngTuple[]) pts.push(p);
      }
    }
    return pts.length ? (pts as LatLngBoundsExpression) : null;
  }, [selectedSiteMembers]);

  const currentBasemap = BASEMAPS[basemap];

  async function handleCopyCurrent() {
    const success = await copyCoordinatesToClipboard(cursorCoords.lat, cursorCoords.lon);
    if (success) {
      setCopiedCoord(true);
      setTimeout(() => setCopiedCoord(false), 2000);
    }
  }

  return (
    <div className="relative h-full w-full overflow-hidden">
      {/* Top Location & State HUD Banner */}
      <div className="absolute top-3 left-14 z-[1000] flex items-center gap-2 px-3 py-1.5 rounded-xl bg-surface/90 backdrop-blur-md border border-border shadow-md pointer-events-auto">
        <span className="text-accent text-xs">📍</span>
        <div className="font-display text-xs flex items-center gap-1.5 flex-wrap">
          <span className="font-bold text-text">{regionLabel}</span>
          {region?.mineral && (
            <>
              <span className="text-text-muted">&middot;</span>
              <span className="px-1.5 py-0.5 rounded bg-accent/10 text-accent text-[10px] font-bold uppercase tracking-wider">
                {region.mineral}
              </span>
            </>
          )}
        </div>
      </div>

      <MapContainer
        center={mapCenter}
        zoom={region ? 13 : 5}

        maxZoom={19}
        minZoom={4}
        className="h-full w-full z-0"
        zoomControl={true}
        attributionControl={true}
      >
        <TileLayer
          key={basemap}
          url={currentBasemap.url}
          maxZoom={currentBasemap.maxZoom}
          maxNativeZoom={currentBasemap.maxNativeZoom}
          attribution={currentBasemap.attribution}
        />

        {/* State Names, District Names, Towns, and Boundary Reference Overlays */}
        {showLabels && basemap === "satellite" && (
          <>
            <TileLayer
              key="satellite-boundaries-places"
              url="https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}"
              maxZoom={20}
              maxNativeZoom={17}
              opacity={0.95}
              zIndex={10}
            />
            <TileLayer
              key="satellite-roads"
              url="https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Transportation/MapServer/tile/{z}/{y}/{x}"
              maxZoom={20}
              maxNativeZoom={17}
              opacity={0.85}
              zIndex={9}
            />
          </>
        )}

        {showLabels && basemap === "dark" && (
          <TileLayer
            key="dark-labels"
            url="https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}"
            maxZoom={20}
            maxNativeZoom={16}
            opacity={0.95}
            zIndex={10}
          />
        )}


        <LiveCoordinateTracker
          onCoordChange={(lat, lon, zoom) => setCursorCoords({ lat, lon, zoom })}
        />

        <FitBounds bounds={selectedBounds ?? initialBounds} />

        {/* Legal Lease Boundaries */}
        {showLeases &&
          leases.map((lease) => (
            <Polygon
              key={`lease-${lease.properties.id}`}
              positions={polygonToLatLngs(lease.geometry)}
              pathOptions={{
                color: "#10b981",
                weight: 2,
                dashArray: "6 6",
                fillColor: "#10b981",
                fillOpacity: 0.06,
              }}
            >
              <Tooltip direction="top" opacity={0.9} sticky>
                <div className="font-display text-xs p-1">
                  <div className="font-bold text-emerald-400">LEGAL MINING LEASE</div>
                  <div className="text-white">{lease.properties.lessee_name ?? `Lease #${lease.properties.id}`}</div>
                  <div className="text-white/70">{lease.properties.mineral_type ?? "—"} &middot; {lease.properties.status}</div>
                </div>
              </Tooltip>
            </Polygon>
          ))}

        {/* Site Markers (when not drilling down) */}
        {selectedSiteId == null &&
          sites.map((site) => {
            const meta = legalityMeta(site.legality_flag);
            const isViolation = site.legality_flag === "POTENTIAL_VIOLATION";

            return (
              <CircleMarker
                key={`site-${site.cluster_id}`}
                center={[site.centroid.lat, site.centroid.lon]}
                radius={Math.min(28, 14 + Math.sqrt(site.member_count) * 4)}
                pathOptions={{
                  color: isViolation ? "#ff3333" : meta.color,
                  weight: 3,
                  fillColor: meta.color,
                  fillOpacity: 0.55,
                }}
                eventHandlers={{ click: () => onSelectSite(site.cluster_id) }}
              >
                <Tooltip direction="top" offset={[0, -10]} opacity={0.98}>
                  <div className="font-display text-xs p-1 space-y-1 min-w-[170px]">
                    <div className="flex items-center justify-between gap-2 border-b border-border/80 pb-1">
                      <span className="font-bold text-text flex items-center gap-1.5">
                        <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ backgroundColor: meta.color }} />
                        Site {String(site.cluster_id).padStart(2, "0")}
                      </span>
                      <span
                        className="px-1.5 py-0.5 rounded text-[9px] uppercase font-bold tracking-wider"
                        style={{ background: meta.bg, color: meta.color }}
                      >
                        {meta.label}
                      </span>
                    </div>
                    <div className="text-text font-medium text-xs">
                      {site.member_count} trigger{site.member_count === 1 ? "" : "s"} &middot; {formatArea(site.total_disturbance_area_m2)}
                    </div>
                    <div className="text-[10px] text-text-muted flex items-center gap-1">
                      <span>📍</span>
                      <span>{formatCoordinates(site.centroid.lat, site.centroid.lon, 4)}</span>
                    </div>
                    <div className="text-[10px] text-text-faint font-mono">
                      {formatCoordinates(site.centroid.lat, site.centroid.lon, 4)}
                    </div>
                    <div className="text-[9px] text-accent font-semibold pt-0.5">
                      Click to focus site
                    </div>
                  </div>
                </Tooltip>
              </CircleMarker>
            );
          })}


        {/* Detailed Trigger Points & Polygons across all or selected site */}
        {showPolygons &&
          (selectedSiteId != null ? selectedSiteMembers : alerts).map((alert) => {
            const p = alert.properties;
            const meta = legalityMeta(p.legality_flag);
            const isSelectedAlert = p.id === selectedAlertId;
            const isViolation = p.legality_flag === "POTENTIAL_VIOLATION";

            return (
              <Polygon
                key={`alert-poly-${p.id}`}
                positions={polygonToLatLngs(alert.geometry)}
                pathOptions={{
                  color: isSelectedAlert ? "#ffffff" : isViolation ? "#ff2222" : meta.color,
                  weight: isSelectedAlert ? 4 : isViolation ? 2.5 : 1.5,
                  fillColor: meta.color,
                  fillOpacity: isSelectedAlert ? 0.7 : isViolation ? 0.45 : 0.25,
                }}
                eventHandlers={{ click: () => onSelectAlert(p.id) }}
              />
            );
          })}

        {/* High-Visibility Trigger Point Markers */}
        {showTriggers &&
          (selectedSiteId != null ? selectedSiteMembers : alerts).map((alert) => {
            const p = alert.properties;
            const meta = legalityMeta(p.legality_flag);
            const isSelectedAlert = p.id === selectedAlertId;
            const isViolation = p.legality_flag === "POTENTIAL_VIOLATION";
            const [lat, lon] = polygonCentroid(alert.geometry);

            return (
              <CircleMarker
                key={`alert-marker-${p.id}`}
                center={[lat, lon]}
                radius={isSelectedAlert ? 9 : isViolation ? 7 : 5}
                pathOptions={{
                  color: isSelectedAlert ? "#ffffff" : isViolation ? "#ff1111" : meta.color,
                  weight: isSelectedAlert ? 3 : 2,
                  fillColor: isViolation ? "#ff3333" : meta.color,
                  fillOpacity: 1,
                }}
                eventHandlers={{ click: () => onSelectAlert(p.id) }}
              >
                <Popup className="bhunetra-map-popup">
                  <div className="p-2 space-y-1.5 min-w-[200px] text-xs font-display">
                    <div className="flex items-center justify-between border-b border-border/60 pb-1">
                      <span className="font-bold text-text text-sm">{p.trigger_id ?? `Alert #${p.id}`}</span>
                      <span
                        className="px-1.5 py-0.5 rounded text-[10px] uppercase font-bold"
                        style={{ background: meta.bg, color: meta.color }}
                      >
                        {meta.label}
                      </span>
                    </div>

                    <div className="space-y-1 text-text-muted">
                      <div className="flex justify-between">
                        <span>Coordinates:</span>
                        <strong className="text-text">{formatCoordinates(lat, lon, 4)}</strong>
                      </div>
                      <div className="flex justify-between">
                        <span>Risk Score:</span>
                        <strong className="text-text">{formatScore(p.risk_score, 1)}</strong>
                      </div>
                      <div className="flex justify-between">
                        <span>NDVI Loss:</span>
                        <strong className="text-text">{formatPercent(p.change_pct)}</strong>
                      </div>
                      <div className="flex justify-between">
                        <span>Disturbance:</span>
                        <strong className="text-text">{formatArea(p.disturbance_area_m2)}</strong>
                      </div>
                      <div className="text-[10px] text-text-faint pt-1 border-t border-border/40 flex items-center gap-1">
                        <span>📍</span>
                        <span>{p.site_id ?? "—"}</span>
                      </div>
                    </div>

                    <button
                      onClick={() => onSelectAlert(p.id)}
                      className="w-full mt-1.5 py-1 rounded bg-accent text-accent-text text-center font-bold text-xs hover:opacity-90 transition-opacity"
                    >
                      Inspect & Triage
                    </button>
                  </div>
                </Popup>
              </CircleMarker>
            );
          })}
      </MapContainer>

      {/* Floating Layer & Basemap Switcher Control */}
      <div className="absolute top-3.5 right-3.5 z-[1000] flex flex-col items-end gap-2">
        <div className="flex items-center gap-2">
          {/* About BhuNetra Button on Map Side */}
          <Link
            href="/about"
            className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-surface/90 hover:bg-surface backdrop-blur-md border border-border text-text shadow-lg transition-all active:scale-95 font-display text-xs font-semibold hover:border-accent hover:text-accent"
            title="About BhuNetra Spaceborne Architecture & Docs"
          >
            <InfoIcon size={15} className="text-accent" />
            <span className="hidden sm:inline">About</span>
          </Link>

          <button
            onClick={() => setShowLayersMenu((v) => !v)}
            className="flex items-center gap-2 px-3 py-2 rounded-xl bg-surface/90 hover:bg-surface backdrop-blur-md border border-border text-text shadow-lg transition-transform active:scale-95 font-display text-xs font-semibold"
          >
            <LayersIcon size={16} className="text-accent" />
            <span>Map Controls</span>
          </button>
        </div>


        {showLayersMenu && (
          <div className="w-56 p-3 rounded-2xl bg-surface/95 backdrop-blur-md border border-border shadow-2xl text-xs font-display space-y-3 animate-fadeIn">
            {/* Basemap selection */}
            <div>
              <div className="text-[10px] uppercase font-bold text-text-faint mb-1.5 tracking-wider">
                Basemap
              </div>
              <div className="grid grid-cols-3 gap-1">
                {(Object.keys(BASEMAPS) as BaseMapType[]).map((bm) => (
                  <button
                    key={bm}
                    onClick={() => setBasemap(bm)}
                    className={`py-1.5 rounded-lg text-[11px] font-semibold transition-all ${
                      basemap === bm
                        ? "bg-accent text-accent-text shadow-xs font-bold"
                        : "bg-bg hover:bg-surface-raised text-text-muted border border-border"
                    }`}
                  >
                    {bm === "satellite" ? "Sat" : bm === "osm" ? "Street" : "Dark"}
                  </button>
                ))}
              </div>
            </div>

            {/* Overlays toggle */}
            <div className="border-t border-border pt-2.5 space-y-2">
              <div className="text-[10px] uppercase font-bold text-text-faint tracking-wider">
                Layers & Overlays
              </div>

              <label className="flex items-center justify-between cursor-pointer">
                <span className="text-text">State & Place Labels</span>
                <input
                  type="checkbox"
                  checked={showLabels}
                  onChange={(e) => setShowLabels(e.target.checked)}
                  className="rounded accent-accent"
                />
              </label>

              <label className="flex items-center justify-between cursor-pointer">
                <span className="text-text">Lease Boundaries</span>
                <input
                  type="checkbox"
                  checked={showLeases}
                  onChange={(e) => setShowLeases(e.target.checked)}
                  className="rounded accent-accent"
                />
              </label>

              <label className="flex items-center justify-between cursor-pointer">
                <span className="text-text">Trigger Markers</span>
                <input
                  type="checkbox"
                  checked={showTriggers}
                  onChange={(e) => setShowTriggers(e.target.checked)}
                  className="rounded accent-accent"
                />
              </label>

              <label className="flex items-center justify-between cursor-pointer">
                <span className="text-text">Disturbance Polygons</span>
                <input
                  type="checkbox"
                  checked={showPolygons}
                  onChange={(e) => setShowPolygons(e.target.checked)}
                  className="rounded accent-accent"
                />
              </label>
            </div>
          </div>
        )}
      </div>

      {/* Floating Live Coordinates & GIS Readout HUD */}
      <div className="absolute bottom-3.5 left-3.5 z-[1000] flex items-center gap-2 bg-black/80 backdrop-blur-md px-3 py-1.5 rounded-xl border border-white/15 text-white shadow-xl text-xs font-display">
        <CrosshairIcon size={14} className="text-accent shrink-0" />
        <span className="font-semibold tracking-wide">
          {formatCoordinates(cursorCoords.lat, cursorCoords.lon, 4)}
        </span>
        <span className="text-white/50 text-[10px]">Z:{cursorCoords.zoom}</span>
        <button
          onClick={handleCopyCurrent}
          className="p-1 rounded hover:bg-white/20 text-white/80 hover:text-white transition-colors"
          title="Copy cursor coordinates"
        >
          {copiedCoord ? <CheckIcon size={12} className="text-green-400" /> : <CopyIcon size={12} />}
        </button>
      </div>
    </div>
  );
}
