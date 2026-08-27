"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { useDashboard } from "@/lib/useDashboard";
import * as api from "@/lib/api";
import type { AlertFeature, AlertStatus, LeaseFeature } from "@/lib/types";
import { ThemeToggle } from "@/components/ThemeToggle";
import { RegionSelect } from "@/components/RegionSelect";
import { AuditLogModal } from "@/components/AuditLogModal";
import { SlaCountdown } from "@/components/SlaCountdown";
import { ImageryViewer } from "@/components/ImageryViewer";
import MapView from "@/components/MapViewLoader";
import {
  formatArea,
  formatDateTime,
  formatPercent,
  formatScore,
  legalityMeta,
} from "@/lib/format";
import { formatCoordinates, polygonCentroid } from "@/lib/geo";

import {
  AlertTriangleIcon,
  CheckCircleIcon,
  ClockIcon,
  FileTextIcon,
  GavelIcon,
  ScaleIcon,
  ShieldIcon,
  BuildingIcon,
  DownloadIcon,
  RefreshIcon,
  LogoutIcon,
  InfoIcon,
  SearchIcon,
  CopyIcon,
  CheckIcon,
} from "@/components/icons";

export default function DgmDashboardPage() {
  const { session, loading: authLoading, logout } = useAuth();
  const router = useRouter();
  const {
    aois,
    selectedAoi,
    setSelectedAoi,
    currentAoi,
    alerts,
    sites,
    auditLogs,
    alertsById,
    loading,
    reload,
    reloadAuditLogs,
    submitAction,
  } = useDashboard();



  const [leases, setLeases] = useState<LeaseFeature[]>([]);
  const [selectedAlertId, setSelectedAlertId] = useState<number | null>(null);
  const [activeTab, setActiveTab] = useState<"escalated" | "urgent" | "all" | "map">("escalated");
  const [searchQuery, setSearchQuery] = useState("");
  const [auditModalOpen, setAuditModalOpen] = useState(false);
  const [noticeModalAlert, setNoticeModalAlert] = useState<AlertFeature | null>(null);
  const [actionNotes, setActionNotes] = useState("");
  const [submittingAction, setSubmittingAction] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    if (!authLoading && !session) {
      router.replace("/login");
    }
  }, [authLoading, session, router]);

  useEffect(() => {
    if (!session) return;
    api
      .getLeases(session.token, selectedAoi)
      .then((res) => setLeases(res.features))
      .catch(() => setLeases([]));
  }, [session, selectedAoi]);

  const allAlerts = useMemo(() => alerts ?? [], [alerts]);

  // Latest real escalation record from the audit log, per alert. No entry
  // means no field-officer dispatch note is on record -- callers handle
  // that rather than showing invented text.
  const escalationNotesMap = useMemo(() => {
    const map = new Map<
      number,
      { notes: string | null; officer: string | null; timestamp: string; reason: string | null }
    >();
    if (!auditLogs) return map;

    const sorted = [...auditLogs].sort(
      (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime()
    );

    for (const log of sorted) {
      if (log.new_status === "ESCALATED_DGM" || log.action === "ESCALATED_DGM") {
        map.set(log.alert_id, {
          notes: log.notes ?? null,
          officer: log.officer_name ?? null,
          timestamp: log.timestamp,
          reason: log.notes ? log.notes.split("—")[0].trim() : null,
        });
      }
    }
    return map;
  }, [auditLogs]);

  // Filter alerts by tab and search
  const filteredAlerts = useMemo(() => {
    return allAlerts.filter((a) => {
      const p = a.properties;
      const isEscalated = p.status === "ESCALATED_DGM";
      const isUrgent =
        isEscalated &&
        (p.legality_flag === "POTENTIAL_VIOLATION" || (p.risk_score || 0) >= 60 || (p.change_pct || 0) >= 40);

      let matchesTab = true;
      if (activeTab === "escalated") matchesTab = isEscalated;
      else if (activeTab === "urgent") matchesTab = isUrgent;
      else if (activeTab === "all") matchesTab = true;

      const matchesSearch =
        !searchQuery.trim() ||
        (p.trigger_id && p.trigger_id.toLowerCase().includes(searchQuery.toLowerCase())) ||
        (p.location_name && p.location_name.toLowerCase().includes(searchQuery.toLowerCase())) ||
        (p.site_id && p.site_id.toLowerCase().includes(searchQuery.toLowerCase()));

      return matchesTab && matchesSearch;
    });
  }, [allAlerts, activeTab, searchQuery]);

  const activeAlertId =
    selectedAlertId ?? (filteredAlerts.length > 0 ? filteredAlerts[0].properties.id : null);

  const selectedAlert = useMemo(
    () => (activeAlertId != null ? alertsById.get(activeAlertId) ?? null : null),
    [activeAlertId, alertsById]
  );


  const selectedEscalationInfo = useMemo(() => {
    if (!selectedAlert || selectedAlert.properties.status !== "ESCALATED_DGM") return null;
    return escalationNotesMap.get(selectedAlert.properties.id) ?? null;
  }, [selectedAlert, escalationNotesMap]);


  // Executive KPI summary calculations
  const stats = useMemo(() => {
    const escalatedList = allAlerts.filter((a) => a.properties.status === "ESCALATED_DGM");
    const urgentCount = escalatedList.filter(
      (a) => (a.properties.risk_score || 0) >= 60 || (a.properties.change_pct || 0) >= 40
    ).length;
    const totalDisturbedM2 = escalatedList.reduce(
      (acc, a) => acc + (a.properties.disturbance_area_m2 || 0),
      0
    );
    const resolvedCount = allAlerts.filter((a) => a.properties.status === "RESOLVED").length;

    return {
      escalatedCount: escalatedList.length,
      urgentCount,
      totalDisturbedM2,
      resolvedCount,
    };
  }, [allAlerts]);

  async function handleRefresh() {
    setRefreshing(true);
    try {
      await reload();
      await reloadAuditLogs();
    } finally {
      setRefreshing(false);
    }
  }

  function handleCopyCoords(lat: number, lon: number, key: string) {
    navigator.clipboard.writeText(`${lat.toFixed(6)}, ${lon.toFixed(6)}`);
    setCopiedId(key);
    setTimeout(() => setCopiedId(null), 2000);
  }

  async function handleDgmAction(newStatus: AlertStatus, actionTitle: string) {
    if (!selectedAlert) return;
    setSubmittingAction(true);
    setActionError(null);
    try {
      const fullNotes = actionNotes.trim()
        ? `[DGM Action: ${actionTitle}] ${actionNotes.trim()}`
        : `[DGM Action: ${actionTitle}] Statutory administrative order executed by Directorate HQ.`;
      await submitAction(selectedAlert.properties.id, newStatus, fullNotes);
      setActionNotes("");
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Action failed. Try again.");
    } finally {
      setSubmittingAction(false);
    }
  }

  return (
    <div className="min-h-dvh bg-bg text-text flex flex-col font-sans">
      {/* Top Directorate Bar */}
      <header className="flex items-center justify-between gap-3 border-b border-border bg-surface px-4 py-2.5 shrink-0 z-20 shadow-xs">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 font-display font-extrabold text-lg tracking-tight text-text">
            <span className="grid h-8 w-8 place-items-center rounded-lg bg-amber-500/10 text-amber-500 border border-amber-500/30">
              <BuildingIcon size={18} />
            </span>
            <div className="flex flex-col">
              <div className="flex items-center gap-2">
                <span>BHUNETRA</span>
                <span className="px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider bg-amber-500/15 text-amber-500 border border-amber-500/30">
                  DGM HQ PORTAL
                </span>
              </div>
              <span className="text-[10px] font-mono text-text-muted">
                Directorate of Geology & Mining &middot; State Enforcement Command
              </span>
            </div>
          </div>
        </div>

        {/* Action Controls & Navigation */}
        <div className="flex items-center gap-2">
          {/* Switch to Field Command Dashboard */}
          <Link
            href="/dashboard"
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border bg-surface hover:bg-surface-raised text-xs font-display font-semibold text-text hover:border-accent transition-all"
            title="Switch to Field Officer Triage View"
          >
            <ShieldIcon size={14} className="text-accent" />
            <span className="hidden sm:inline">Field Command</span>
          </Link>

          {/* Audit Ledger Launcher */}
          <button
            onClick={() => setAuditModalOpen(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border bg-surface hover:bg-surface-raised text-xs font-display font-semibold text-text hover:border-amber-500 transition-all"
            title="Open Enforcement Audit Ledger"
          >
            <ClockIcon size={14} className="text-amber-500" />
            <span className="hidden sm:inline">Audit Ledger</span>
            {(auditLogs?.length ?? 0) > 0 && (
              <span className="px-1.5 py-0.2 rounded-full bg-amber-500 text-black text-[10px] font-bold">
                {auditLogs?.length}
              </span>
            )}
          </button>

          {/* Mission Docs */}
          <Link
            href="/about"
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-border bg-surface text-xs font-display font-semibold text-text hover:border-accent transition-all"
          >
            <InfoIcon size={14} className="text-accent" />
            <span className="hidden md:inline">About</span>
          </Link>

          {aois.length > 0 && (
            <RegionSelect aois={aois} value={selectedAoi} onChange={setSelectedAoi} />
          )}

          {session && (
            <div className="hidden lg:flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-surface-raised border border-border text-xs">
              <span className="w-2 h-2 rounded-full bg-emerald-500"></span>
              <span className="font-semibold text-text truncate max-w-[140px]">
                {session.name}
              </span>
            </div>
          )}

          <button
            onClick={handleRefresh}
            aria-label="Refresh Data"
            className="grid h-8 w-8 place-items-center rounded-lg border border-border text-text-muted hover:text-text active:scale-95 transition-transform"
          >
            <RefreshIcon size={15} className={refreshing ? "animate-spin" : ""} />
          </button>

          <ThemeToggle />

          <button
            onClick={logout}
            aria-label="Log out"
            className="grid h-8 w-8 place-items-center rounded-lg border border-border text-text-muted hover:text-text active:scale-95 transition-transform"
          >
            <LogoutIcon size={15} />
          </button>
        </div>
      </header>

      {/* Executive KPI Metrics Bar */}
      <section className="border-b border-border bg-surface-raised/40 px-4 py-3 shrink-0">
        <div className="max-w-7xl mx-auto grid grid-cols-2 md:grid-cols-4 gap-3">
          <div className="p-3 rounded-xl border border-amber-500/30 bg-amber-500/5 flex items-center gap-3">
            <div className="p-2.5 rounded-lg bg-amber-500/15 text-amber-500">
              <AlertTriangleIcon size={20} />
            </div>
            <div>
              <div className="text-[11px] font-display uppercase tracking-wider text-text-muted font-bold">
                Escalated Triggers
              </div>
              <div className="font-display font-extrabold text-2xl text-amber-500">
                {stats.escalatedCount} <span className="text-xs font-normal text-text-muted">pending DGM</span>
              </div>
            </div>
          </div>

          <div className="p-3 rounded-xl border border-red-500/30 bg-red-500/5 flex items-center gap-3">
            <div className="p-2.5 rounded-lg bg-red-500/15 text-red-500">
              <GavelIcon size={20} />
            </div>
            <div>
              <div className="text-[11px] font-display uppercase tracking-wider text-text-muted font-bold">
                Urgent MMDR Violations
              </div>
              <div className="font-display font-extrabold text-2xl text-red-500">
                {stats.urgentCount} <span className="text-xs font-normal text-text-muted">high risk</span>
              </div>
            </div>
          </div>

          <div className="p-3 rounded-xl border border-border bg-surface flex items-center gap-3">
            <div className="p-2.5 rounded-lg bg-accent/10 text-accent">
              <ScaleIcon size={20} />
            </div>
            <div>
              <div className="text-[11px] font-display uppercase tracking-wider text-text-muted font-bold">
                Disturbed Footprint
              </div>
              <div className="font-display font-extrabold text-2xl text-text">
                {formatArea(stats.totalDisturbedM2)}
              </div>
            </div>
          </div>

          <div className="p-3 rounded-xl border border-border bg-surface flex items-center gap-3">
            <div className="p-2.5 rounded-lg bg-emerald-500/10 text-emerald-500">
              <CheckCircleIcon size={20} />
            </div>
            <div>
              <div className="text-[11px] font-display uppercase tracking-wider text-text-muted font-bold">
                Sanctioned / Resolved
              </div>
              <div className="font-display font-extrabold text-2xl text-emerald-500">
                {stats.resolvedCount} <span className="text-xs font-normal text-text-muted">closed cases</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Main Workspace Content */}
      <main className="flex-1 flex flex-col lg:flex-row overflow-hidden">
        {/* Left Column: Escalated Trigger Queue */}
        <section className="w-full lg:w-[420px] xl:w-[460px] border-r border-border bg-surface flex flex-col shrink-0 overflow-hidden">
          {/* Filter Tabs & Search */}
          <div className="p-3 border-b border-border space-y-2 bg-surface">
            <div className="flex items-center gap-1 p-1 rounded-xl bg-bg border border-border text-xs font-display font-semibold">
              <button
                onClick={() => setActiveTab("escalated")}
                className={`flex-1 py-1.5 px-2 rounded-lg text-center transition-all ${
                  activeTab === "escalated"
                    ? "bg-amber-500 text-black font-bold shadow-xs"
                    : "text-text-muted hover:text-text"
                }`}
              >
                Escalated ({stats.escalatedCount})
              </button>
              <button
                onClick={() => setActiveTab("urgent")}
                className={`flex-1 py-1.5 px-2 rounded-lg text-center transition-all ${
                  activeTab === "urgent"
                    ? "bg-red-500 text-white font-bold shadow-xs"
                    : "text-text-muted hover:text-text"
                }`}
              >
                Urgent ({stats.urgentCount})
              </button>
              <button
                onClick={() => setActiveTab("all")}
                className={`flex-1 py-1.5 px-2 rounded-lg text-center transition-all ${
                  activeTab === "all"
                    ? "bg-surface text-text font-bold shadow-xs border border-border"
                    : "text-text-muted hover:text-text"
                }`}
              >
                All ({allAlerts.length})
              </button>
              <button
                onClick={() => setActiveTab("map")}
                className={`lg:hidden flex-1 py-1.5 px-2 rounded-lg text-center transition-all ${
                  activeTab === "map"
                    ? "bg-accent text-accent-text font-bold"
                    : "text-text-muted hover:text-text"
                }`}
              >
                Map
              </button>
            </div>

            <div className="relative">
              <SearchIcon size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search trigger ID, coordinates, location…"
                className="w-full pl-8 pr-3 py-1.5 rounded-lg border border-border bg-bg text-xs text-text outline-none focus:border-amber-500 font-mono"
              />
            </div>
          </div>

          {/* Trigger List Scroll Area */}
          <div className="flex-1 overflow-y-auto divide-y divide-border/60">
            {loading ? (
              <div className="p-8 text-center text-xs text-text-muted">
                <RefreshIcon size={18} className="animate-spin mx-auto mb-2 text-amber-500" />
                Loading DGM Escalation Ledger…
              </div>
            ) : filteredAlerts.length === 0 ? (
              <div className="p-8 text-center text-xs text-text-muted space-y-2">
                <CheckCircleIcon size={24} className="text-emerald-500 mx-auto" />
                <div className="font-display font-bold text-text">No Escalated Triggers Found</div>
                <p className="text-text-faint">
                  All district triggers are either under standard field triage or resolved.
                </p>
              </div>
            ) : (
              filteredAlerts.map((alert) => {
                const p = alert.properties;
                const isSelected = p.id === selectedAlertId;
                const meta = legalityMeta(p.legality_flag);
                const escInfo = escalationNotesMap.get(p.id);
                const isEscalated = p.status === "ESCALATED_DGM";

                return (
                  <div
                    key={p.id}
                    onClick={() => setSelectedAlertId(p.id)}
                    className={`p-3.5 cursor-pointer transition-all ${
                      isSelected
                        ? "bg-amber-500/10 border-l-4 border-l-amber-500"
                        : "hover:bg-surface-raised"
                    }`}
                  >
                    <div className="flex items-start justify-between gap-2 mb-1.5">
                      <div className="flex items-center gap-1.5 min-w-0">
                        <span
                          className="w-2.5 h-2.5 rounded-full shrink-0"
                          style={{ backgroundColor: isEscalated ? "var(--amber, #f59e0b)" : meta.color }}
                        />
                        <span className="font-display font-bold text-sm text-text truncate">
                          {p.trigger_id ?? `Alert #${p.id}`}
                        </span>
                        <span className="text-[10px] font-mono text-text-muted">
                          &middot; {p.site_id ?? "—"}
                        </span>
                      </div>

                      {/* Status Chip */}
                      {isEscalated ? (
                        <span className="px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider bg-amber-500/15 text-amber-500 border border-amber-500/30 flex items-center gap-1">
                          <AlertTriangleIcon size={10} />
                          Escalated
                        </span>
                      ) : p.status === "RESOLVED" ? (
                        <span className="px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider bg-emerald-500/15 text-emerald-500 border border-emerald-500/30">
                          Resolved
                        </span>
                      ) : (
                        <span className="px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider bg-surface-raised text-text-muted border border-border">
                          Pending Officer
                        </span>
                      )}
                    </div>

                    {/* Location & Disturbance */}
                    <div className="text-xs text-text-muted mb-2 truncate">
                      {p.location_name || "—"} &middot;{" "}
                      <strong className="text-text">{formatArea(p.disturbance_area_m2)}</strong>
                    </div>

                    {/* Officer Escalation Message Box */}
                    {isEscalated && escInfo && (escInfo.officer || escInfo.notes) && (
                      <div className="p-2.5 rounded-lg bg-bg border border-amber-500/30 text-xs space-y-1 mb-2">
                        <div className="flex items-center justify-between text-[10px] text-amber-500 font-semibold uppercase tracking-wide">
                          <span>👤 {escInfo.officer ?? "Officer"}</span>
                          <span>{escInfo.timestamp ? formatDateTime(escInfo.timestamp) : ""}</span>
                        </div>
                        {escInfo.notes && (
                          <div className="text-[11px] text-text font-medium leading-relaxed italic">
                            &ldquo;{escInfo.notes}&rdquo;
                          </div>
                        )}
                      </div>
                    )}

                    {/* Metrics Row */}
                    <div className="flex items-center justify-between text-[11px] pt-1 text-text-faint font-display">
                      <div className="flex items-center gap-2">
                        <span>Risk: <strong className="text-text">{formatScore(p.risk_score)}</strong></span>
                        <span>NDVI: <strong className="text-text">{formatPercent(p.change_pct)}</strong></span>
                      </div>
                      <SlaCountdown deadline={p.sla_deadline} status={p.status} />
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </section>

        {/* Right Console: Detailed Evidence & DGM Legal Action */}
        {activeTab === "map" ? (
          <section className="flex-1 min-h-[500px] h-full relative">
            <MapView
              alerts={filteredAlerts}
              sites={sites ?? []}
              leases={leases}
              selectedSiteId={null}
              selectedAlertId={selectedAlertId}
              onSelectAlert={(id) => setSelectedAlertId(id)}
              onSelectSite={() => {}}
              region={currentAoi}
              allRegions={aois}
            />
          </section>
        ) : selectedAlert ? (
          <section className="flex-1 overflow-y-auto p-4 lg:p-6 space-y-5 bg-bg">
            {/* Header / Dossier Overview */}
            <div className="p-5 rounded-2xl border border-border bg-surface shadow-xs space-y-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="flex items-center gap-2 mb-1">
                    <span className="px-2.5 py-0.5 rounded-md text-[11px] font-display font-bold uppercase tracking-wider bg-amber-500/15 text-amber-500 border border-amber-500/30">
                      DGM ENFORCEMENT CASE #{selectedAlert.properties.id}
                    </span>
                    <span className="text-xs font-mono text-text-muted">
                      {selectedAlert.properties.trigger_id}
                    </span>
                  </div>
                  <h1 className="font-display font-extrabold text-xl lg:text-2xl text-text">
                    {selectedAlert.properties.location_name || "—"}
                  </h1>
                  <p className="text-xs text-text-muted mt-0.5">
                    Spatial AOI: {selectedAlert.properties.site_id ?? "—"}
                    {(() => {
                      const r = aois.find((a) => a.id === selectedAlert.properties.site_id);
                      const loc = r ? [r.district, r.state].filter(Boolean).join(", ") : "";
                      return loc ? ` · ${loc}` : "";
                    })()}
                  </p>
                </div>

                {/* Quick Action Button for Show Cause Notice */}
                <button
                  onClick={() => setNoticeModalAlert(selectedAlert)}
                  className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-amber-500 hover:bg-amber-400 text-black font-display font-bold text-xs uppercase tracking-wide shadow-md active:scale-95 transition-all"
                >
                  <GavelIcon size={16} />
                  <span>Draft MMDR Sec 21 Notice</span>
                </button>
              </div>

              {/* Officer Escalation Banner */}
              {selectedAlert.properties.status === "ESCALATED_DGM" && (
                <div className="p-4 rounded-xl border border-amber-500/40 bg-amber-500/10 space-y-2">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 font-display font-bold text-xs uppercase tracking-wide text-amber-500">
                      <AlertTriangleIcon size={15} />
                      <span>Field Officer Escalation Dispatch Message</span>
                    </div>
                    {selectedEscalationInfo?.timestamp && (
                      <span className="text-[11px] font-mono text-text-muted">
                        {formatDateTime(selectedEscalationInfo.timestamp)}
                      </span>
                    )}
                  </div>
                  {selectedEscalationInfo?.notes ? (
                    <p className="text-sm font-medium text-text leading-relaxed">
                      &ldquo;{selectedEscalationInfo.notes}&rdquo;
                    </p>
                  ) : (
                    <p className="text-sm text-text-muted leading-relaxed">
                      No field-officer dispatch note is recorded for this escalation.
                    </p>
                  )}

                  {(selectedEscalationInfo?.officer || selectedEscalationInfo?.reason) && (
                    <div className="text-[11px] text-text-muted flex items-center gap-2 pt-1 border-t border-amber-500/20">
                      {selectedEscalationInfo?.officer && (
                        <span>Submitting Officer: <strong>{selectedEscalationInfo.officer}</strong></span>
                      )}
                      {selectedEscalationInfo?.officer && selectedEscalationInfo?.reason && <span>&middot;</span>}
                      {selectedEscalationInfo?.reason && (
                        <span>Primary Reason: <strong>{selectedEscalationInfo.reason}</strong></span>
                      )}
                    </div>
                  )}
                </div>
              )}


              {/* Coordinates and Metric Strip */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5 pt-2 border-t border-border">
                {(() => {
                  const [lat, lon] = polygonCentroid(selectedAlert.geometry);
                  return (
                    <div className="p-2.5 rounded-lg bg-bg border border-border">
                      <div className="text-[10px] uppercase font-bold text-text-faint">GPS Center</div>
                      <div className="font-mono text-xs font-bold text-text flex items-center justify-between">
                        <span>{formatCoordinates(lat, lon, 4)}</span>
                        <button
                          onClick={() => handleCopyCoords(lat, lon, `coord-${selectedAlert.properties.id}`)}
                          className="text-text-muted hover:text-accent"
                          title="Copy Coordinates"
                        >
                          {copiedId === `coord-${selectedAlert.properties.id}` ? (
                            <CheckIcon size={13} className="text-accent" />
                          ) : (
                            <CopyIcon size={13} />
                          )}
                        </button>
                      </div>
                    </div>
                  );
                })()}

                <div className="p-2.5 rounded-lg bg-bg border border-border">
                  <div className="text-[10px] uppercase font-bold text-text-faint">Disturbance Area</div>
                  <div className="font-display text-xs font-bold text-text">
                    {formatArea(selectedAlert.properties.disturbance_area_m2)}
                  </div>
                </div>

                <div className="p-2.5 rounded-lg bg-bg border border-border">
                  <div className="text-[10px] uppercase font-bold text-text-faint">Vegetation NDVI Loss</div>
                  <div className="font-display text-xs font-bold text-text">
                    {formatPercent(selectedAlert.properties.change_pct)}
                  </div>
                </div>

                <div className="p-2.5 rounded-lg bg-bg border border-border">
                  <div className="text-[10px] uppercase font-bold text-text-faint">SLA Status</div>
                  <div className="text-xs font-bold">
                    <SlaCountdown
                      deadline={selectedAlert.properties.sla_deadline}
                      status={selectedAlert.properties.status}
                      showExactDeadline
                    />
                  </div>
                </div>
              </div>
            </div>

            {/* Satellite Evidence Comparison */}
            <div className="p-5 rounded-2xl border border-border bg-surface shadow-xs space-y-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="p-1.5 rounded bg-accent/10 text-accent font-display text-xs font-bold">🛰️</span>
                  <h3 className="font-display font-bold text-base text-text">
                    Multi-Sensor Satellite Corroboration
                  </h3>
                </div>
                <span className="text-xs text-text-muted">
                  Optical (Sentinel-2) + Radar (Sentinel-1 SAR) + VIIRS Nightlights
                </span>
              </div>
              <ImageryViewer alertId={selectedAlert.properties.id} />
            </div>


            {/* AI Statutory Briefing */}
            {selectedAlert.properties.brief_text && (
              <div className="p-5 rounded-2xl border border-border bg-surface shadow-xs space-y-3">
                <div className="flex items-center gap-2 font-display font-bold text-base text-text">
                  <ScaleIcon size={18} className="text-amber-500" />
                  <span>Statutory Assessment & Prosecution Summary</span>
                </div>
                <div className="text-xs text-text leading-relaxed p-4 rounded-xl bg-bg border border-border/80 whitespace-pre-wrap font-sans">
                  {selectedAlert.properties.brief_text}
                </div>
              </div>
            )}

            {/* DGM Direct Enforcement Action Console */}
            <div className="p-5 rounded-2xl border border-amber-500/40 bg-surface shadow-md space-y-4">
              <div className="flex items-center gap-2">
                <div className="p-2 rounded-lg bg-amber-500/15 text-amber-500">
                  <GavelIcon size={18} />
                </div>
                <div>
                  <h3 className="font-display font-bold text-base text-text">
                    DGM Directorate Enforcement & Sanctions Console
                  </h3>
                  <p className="text-xs text-text-muted">
                    Execute statutory legal actions under Section 21 of the Mines and Minerals (Development and Regulation) Act 1957.
                  </p>
                </div>
              </div>

              {/* Action Notes Input */}
              <div className="space-y-1.5">
                <label className="text-xs font-display font-semibold uppercase tracking-wide text-text-muted">
                  Directorate Directives & Administrative Order Notes
                </label>
                <textarea
                  rows={2}
                  value={actionNotes}
                  onChange={(e) => setActionNotes(e.target.value)}
                  placeholder="e.g. Dispatched Dantewada Mining Flying Squad. Demanded immediate submission of e-transit passes and survey records within 7 working days."
                  className="w-full p-3 rounded-xl border border-border bg-bg text-xs text-text outline-none focus:border-amber-500"
                />
              </div>

              {/* Action Buttons Grid */}
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5">
                <button
                  disabled={submittingAction}
                  onClick={() => setNoticeModalAlert(selectedAlert)}
                  className="p-3 rounded-xl border border-amber-500/50 bg-amber-500/10 hover:bg-amber-500/20 text-amber-500 font-display font-bold text-xs uppercase tracking-wide flex items-center justify-center gap-2 transition-all active:scale-95"
                >
                  <FileTextIcon size={15} />
                  <span>Issue Legal Notice</span>
                </button>

                <button
                  disabled={submittingAction}
                  onClick={() => handleDgmAction("ESCALATED_DGM", "Flying Squad Raid Dispatched")}
                  className="p-3 rounded-xl border border-red-500/50 bg-red-500/10 hover:bg-red-500/20 text-red-500 font-display font-bold text-xs uppercase tracking-wide flex items-center justify-center gap-2 transition-all active:scale-95"
                >
                  <ShieldIcon size={15} />
                  <span>Dispatch Task Force</span>
                </button>

                <button
                  disabled={submittingAction}
                  onClick={() => handleDgmAction("RESOLVED", "Sanctioned / Resolved by DGM")}
                  className="p-3 rounded-xl border border-emerald-500/50 bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-500 font-display font-bold text-xs uppercase tracking-wide flex items-center justify-center gap-2 transition-all active:scale-95"
                >
                  <CheckCircleIcon size={15} />
                  <span>Close & Resolve Case</span>
                </button>
              </div>

              {actionError && (
                <p className="text-xs font-medium text-red-500">{actionError}</p>
              )}
            </div>
          </section>
        ) : (
          <div className="flex-1 grid place-items-center p-8 text-center text-text-muted">
            Select an escalated trigger from the queue to inspect satellite evidence and issue statutory notices.
          </div>
        )}
      </main>

      {/* Official MMDR Section 21 Show Cause Notice Modal */}
      {noticeModalAlert && (
        <div
          className="fixed inset-0 z-[9999] bg-black/80 backdrop-blur-sm flex items-center justify-center p-3 md:p-6 animate-fadeIn"
          onClick={() => setNoticeModalAlert(null)}
        >
          <div
            className="w-full max-w-3xl max-h-[90vh] bg-surface border border-border rounded-2xl shadow-2xl flex flex-col overflow-hidden"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Modal Header */}
            <div className="px-6 py-4 border-b border-border bg-surface-raised flex items-center justify-between">
              <div className="flex items-center gap-2 font-display font-bold text-base text-text">
                <GavelIcon size={18} className="text-amber-500" />
                <span>Statutory Show-Cause Notice Preview (MMDR Section 21)</span>
              </div>
              <button
                onClick={() => setNoticeModalAlert(null)}
                className="text-text-muted hover:text-text text-sm font-bold"
              >
                ✕ Close
              </button>
            </div>

            {/* Document Body (Printable Letterhead Style) */}
            <div className="flex-1 overflow-y-auto p-6 md:p-8 space-y-6 font-serif text-text bg-surface-raised/30">
              <div className="text-center border-b border-border pb-4 space-y-1">
                <div className="font-display font-extrabold text-sm uppercase tracking-widest text-text">
                  GOVERNMENT OF CHHATTISGARH
                </div>
                <div className="font-display font-bold text-xs uppercase tracking-wider text-amber-500">
                  DIRECTORATE OF GEOLOGY & MINING (DGM)
                </div>
                <div className="text-[11px] font-sans text-text-muted">
                  Mining Enforcement Cell &middot; Bastar / Dantewada Division
                </div>
                <div className="text-[10px] font-mono text-text-faint pt-1">
                  Ref No: DGM/ENF/2026/SEC21-{(noticeModalAlert.properties.trigger_id ?? String(noticeModalAlert.properties.id)).replace(/-/g, "")} &middot; Date: {new Date().toLocaleDateString("en-IN", { day: "numeric", month: "long", year: "numeric" })}
                </div>
              </div>

              <div className="text-xs space-y-3 font-sans leading-relaxed">
                <div className="font-bold uppercase tracking-wide text-text">
                  FORMAL NOTICE UNDER SECTION 21 & SECTION 4(1A) OF THE MINES & MINERALS (DEVELOPMENT AND REGULATION) ACT, 1957
                </div>

                <p>
                  <strong>TO:</strong> The Occupier / Leaseholder / Unauthorised Mining Entity operating at:
                  <br />
                  <strong>Location:</strong> {noticeModalAlert.properties.location_name} ({noticeModalAlert.properties.site_id})
                  <br />
                  <strong>GPS Geo-Coordinates:</strong> {(() => {
                    const [lat, lon] = polygonCentroid(noticeModalAlert.geometry);
                    return `${lat.toFixed(6)}° N, ${lon.toFixed(6)}° E`;
                  })()}

                </p>

                <div className="p-3 rounded-lg border border-border bg-surface font-mono text-[11px] space-y-1">
                  <div className="font-bold text-amber-500 uppercase tracking-wider">
                    SATELLITE SURVEILLANCE FINDINGS:
                  </div>
                  <div>&bull; Disturbed Excavation Footprint: <strong>{formatArea(noticeModalAlert.properties.disturbance_area_m2)}</strong></div>
                  <div>&bull; Optical Surface Vegetation Drop: <strong>{formatPercent(noticeModalAlert.properties.change_pct)}</strong></div>
                  <div>&bull; Legality Status: <strong>{noticeModalAlert.properties.legality_flag ?? "—"}</strong></div>
                  {selectedEscalationInfo?.notes && (
                    <div>&bull; Field Officer Dispatch: <strong>&ldquo;{selectedEscalationInfo.notes}&rdquo;</strong></div>
                  )}
                </div>

                <p>
                  WHEREAS multi-spectral spaceborne earth observation by <strong>Project BhuNetra</strong> (Sentinel-2 MSI & Sentinel-1 SAR radar) has corroborated continuous non-compliant earth extraction and pit extension outside the legally sanctioned boundary polygon;
                </p>

                <p>
                  YOU ARE HEREBY DIRECTED TO CEASE AND DESIST all extraction operations forthwith and show cause within <strong>15 (fifteen) days</strong> of receipt of this notice as to why penal proceedings under Section 21 of the MMDR Act, 1957{noticeModalAlert.properties.disturbance_area_m2 != null && (
                    <> and environmental damages of <strong>₹{(noticeModalAlert.properties.disturbance_area_m2 * 1200 + 500000).toLocaleString("en-IN")}</strong></>
                  )} should not be recovered.
                </p>
              </div>

              <div className="pt-6 border-t border-border flex justify-between items-end text-xs font-sans">
                <div className="text-text-faint text-[10px]">
                  BhuNetra Automated Enforcement Dispatch &middot; Digital SHA-256 Verified
                </div>
                <div className="text-right">
                  <div className="font-display font-bold text-text">{session?.name ?? "—"}</div>
                  <div className="text-[11px] text-text-muted">
                    {session?.role === "DGM_ADMIN" ? "Director, Geology & Mining (DGM)" : "Authorised Officer"}
                  </div>
                  <div className="text-[10px] text-text-faint">Government of Chhattisgarh</div>
                </div>
              </div>
            </div>

            {/* Modal Footer Actions */}
            <div className="px-6 py-4 border-t border-border bg-surface flex items-center justify-between">
              <button
                onClick={() => window.print()}
                className="flex items-center gap-1.5 px-4 py-2 rounded-xl border border-border hover:bg-surface-raised text-xs font-display font-bold text-text transition-all"
              >
                <DownloadIcon size={14} />
                <span>Print / Save as PDF</span>
              </button>

              <button
                onClick={async () => {
                  await handleDgmAction("ESCALATED_DGM", "Formal Section 21 Show-Cause Notice Dispatched");
                  setNoticeModalAlert(null);
                }}
                className="flex items-center gap-1.5 px-5 py-2 rounded-xl bg-amber-500 hover:bg-amber-400 text-black font-display font-bold text-xs uppercase tracking-wide shadow-md active:scale-95 transition-all"
              >
                <GavelIcon size={14} />
                <span>Dispatch & Log Notice to Ledger</span>
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Enforcement Audit Ledger Modal */}
      <AuditLogModal
        isOpen={auditModalOpen}
        onClose={() => setAuditModalOpen(false)}
        auditLogs={auditLogs ?? []}
      />
    </div>
  );
}
