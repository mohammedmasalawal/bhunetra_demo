"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { useDashboard } from "@/lib/useDashboard";
import * as api from "@/lib/api";
import type { LeaseFeature, DashboardViewMode } from "@/lib/types";
import { TopBar } from "@/components/TopBar";
import { SiteList } from "@/components/SiteList";
import { SitePanel } from "@/components/SitePanel";
import { AlertPanel } from "@/components/AlertPanel";
import { TriggerTable } from "@/components/TriggerTable";
import { AuditLogModal } from "@/components/AuditLogModal";
import { SiteListSkeleton } from "@/components/Skeletons";
import { ErrorBanner } from "@/components/ErrorBanner";
import MapView from "@/components/MapViewLoader";
import { ListIcon, MapIcon, TableIcon, ClockIcon, InfoIcon } from "@/components/icons";


export default function DashboardPage() {
  const { session, loading: authLoading } = useAuth();
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
    alertsBySite,
    loading,
    error,
    reload,
    generateBrief,
    submitAction,
  } = useDashboard();

  const [leases, setLeases] = useState<LeaseFeature[]>([]);
  const [selectedSiteId, setSelectedSiteId] = useState<number | null>(null);
  const [selectedAlertId, setSelectedAlertId] = useState<number | null>(null);
  const [sheetExpanded, setSheetExpanded] = useState(false);
  const [viewMode, setViewMode] = useState<DashboardViewMode>("sites");
  const [mobileTab, setMobileTab] = useState<"map" | "list" | "triggers" | "audit">("list");
  const [auditModalOpen, setAuditModalOpen] = useState(false);

  useEffect(() => {
    if (!authLoading && !session) router.replace("/login");
  }, [authLoading, session, router]);

  useEffect(() => {
    if (!session) return;
    api
      .getLeases(session.token, selectedAoi)
      .then((res) => setLeases(res.features))
      .catch(() => setLeases([])); // lease overlay is a nice-to-have, never blocks triage
  }, [session, selectedAoi]);

  const sitesList = useMemo(() => sites ?? [], [sites]);
  const alertsList = useMemo(() => alerts ?? [], [alerts]);

  const selectedSite = useMemo(
    () => sitesList.find((s) => s.cluster_id === selectedSiteId) ?? null,
    [sitesList, selectedSiteId]
  );
  const selectedSiteMembers = useMemo(
    () => (selectedSiteId != null ? alertsBySite.get(selectedSiteId) ?? [] : []),
    [selectedSiteId, alertsBySite]
  );
  const selectedAlert = useMemo(
    () => (selectedAlertId != null ? alertsById.get(selectedAlertId) ?? null : null),
    [selectedAlertId, alertsById]
  );


  function handleSelectSite(id: number) {
    setSelectedSiteId(id);
    setSelectedAlertId(null);
    setSheetExpanded(true);
    setMobileTab("map");
  }

  function handleSelectAlert(id: number) {
    const alert = alertsById.get(id);
    if (alert && alert.properties.cluster_id != null) {
      setSelectedSiteId(alert.properties.cluster_id);
    }
    setSelectedAlertId(id);
    setSheetExpanded(true);
    setMobileTab("map");
  }

  function backToSites() {
    setSelectedSiteId(null);
    setSelectedAlertId(null);
  }

  function backToSite() {
    setSelectedAlertId(null);
  }

  if (authLoading || !session) {
    return (
      <div className="grid h-dvh place-items-center bg-bg">
        <div className="font-display text-sm tracking-widest text-text-faint uppercase">
          Loading Field Command…
        </div>
      </div>
    );
  }

  const panelContent = selectedAlert ? (
    <AlertPanel
      alert={selectedAlert}
      auditLogs={auditLogs}
      onBack={backToSite}
      onGenerateBrief={() => generateBrief(selectedAlert.properties.id)}
      onSubmitAction={(status, notes) => submitAction(selectedAlert.properties.id, status, notes)}
    />
  ) : selectedSite ? (
    <SitePanel
      site={selectedSite}
      members={selectedSiteMembers}
      onBack={backToSites}
      onSelectAlert={handleSelectAlert}
    />
  ) : loading ? (
    <SiteListSkeleton />
  ) : error ? (
    <div className="p-3.5">
      <ErrorBanner message={error} onRetry={reload} />
    </div>
  ) : (
    <SiteList
      sites={sitesList}
      alertsBySite={alertsBySite}
      selectedSiteId={selectedSiteId}
      onSelectSite={handleSelectSite}
    />
  );

  return (
    <div className="flex flex-col h-dvh bg-bg overflow-hidden">
      <TopBar
        onRefresh={reload}
        refreshing={loading}
        viewMode={viewMode}
        onViewModeChange={setViewMode}
        onOpenAuditLogs={() => setAuditModalOpen(true)}
        auditLogCount={auditLogs.length}
        aois={aois}
        selectedAoi={selectedAoi}
        onSelectAoi={setSelectedAoi}
      />

      {/* Desktop Layout */}
      <div className="hidden md:flex flex-1 min-h-0 relative">
        {viewMode === "triggers" ? (
          /* Full-width Trigger Column Table View with Slide-Over Detail Drawer */
          <div className="flex-1 min-w-0 bg-bg overflow-hidden flex flex-col relative">
            <TriggerTable
              alerts={alertsList}
              selectedAlertId={selectedAlertId}
              onSelectAlert={handleSelectAlert}
              onOpenMap={() => setViewMode("sites")}
            />

            {/* Slide-over Detail Drawer when an alert is selected */}
            {selectedAlert && (
              <>
                {/* Backdrop */}
                <div
                  onClick={backToSite}
                  className="fixed inset-0 top-[53px] bg-black/45 backdrop-blur-[2px] z-20 transition-opacity"
                  aria-label="Close detail panel"
                />

                {/* Drawer Container */}
                <div className="fixed right-0 top-[53px] bottom-0 w-[520px] max-w-[92vw] bg-bg border-l border-border shadow-[-12px_0_40px_rgba(0,0,0,0.5)] z-30 flex flex-col overflow-hidden animate-fadeIn">
                  <AlertPanel
                    alert={selectedAlert}
                    auditLogs={auditLogs}
                    onBack={backToSite}
                    onGenerateBrief={() => generateBrief(selectedAlert.properties.id)}
                    onSubmitAction={(status, notes) => submitAction(selectedAlert.properties.id, status, notes)}
                  />
                </div>
              </>
            )}
          </div>
        ) : (
          /* Standard Map + Side Panel Layout */
          <div className="flex-1 flex min-h-0">
            <div className="flex-1 min-w-0">
              <MapView
                alerts={alertsList}
                sites={sitesList}
                leases={leases}
                selectedSiteId={selectedSiteId}
                selectedAlertId={selectedAlertId}
                onSelectSite={handleSelectSite}
                onSelectAlert={handleSelectAlert}
                region={currentAoi}
                allRegions={aois}
              />
            </div>
            <div className="w-[380px] lg:w-[430px] shrink-0 border-l border-border bg-bg overflow-hidden">
              {panelContent}
            </div>
          </div>
        )}
      </div>


      {/* Mobile Layout */}
      <div className="md:hidden flex-1 min-h-0 relative">
        {/* Map tab */}
        <div className={`absolute inset-x-0 top-0 bottom-14 ${mobileTab === "map" ? "" : "hidden"}`}>
          <MapView
            alerts={alertsList}
            sites={sitesList}
            leases={leases}
            selectedSiteId={selectedSiteId}
            selectedAlertId={selectedAlertId}
            onSelectSite={handleSelectSite}
            onSelectAlert={handleSelectAlert}
          />

          {(selectedSite || selectedAlert) && (
            <div
              className="absolute left-0 right-0 bottom-0 rounded-t-2xl border-t border-border bg-bg shadow-[0_-4px_24px_rgba(0,0,0,0.35)] transition-[height] duration-200 flex flex-col z-[1000]"
              style={{ height: sheetExpanded ? "92%" : "52%" }}
            >
              <button
                onClick={() => setSheetExpanded((v) => !v)}
                className="w-full flex justify-center py-2 shrink-0 bg-surface rounded-t-2xl"
                aria-label="Toggle sheet size"
              >
                <span className="h-1.5 w-10 rounded-full bg-border" />
              </button>
              <div className="flex-1 min-h-0">{panelContent}</div>
            </div>
          )}
        </div>

        {/* Sites List tab */}
        <div className={`absolute inset-x-0 top-0 bottom-14 overflow-y-auto ${mobileTab === "list" ? "" : "hidden"}`}>
          {panelContent}
        </div>

        {/* Triggers Table tab */}
        <div className={`absolute inset-x-0 top-0 bottom-14 overflow-y-auto ${mobileTab === "triggers" ? "" : "hidden"}`}>
          <TriggerTable
            alerts={alertsList}
            selectedAlertId={selectedAlertId}
            onSelectAlert={handleSelectAlert}
          />
        </div>

        {/* Bottom Tab Bar */}
        <div className="absolute left-0 right-0 bottom-0 h-14 flex border-t border-border bg-surface z-[1000]">
          <button
            onClick={() => setMobileTab("map")}
            className={`flex-1 flex flex-col items-center justify-center gap-0.5 ${
              mobileTab === "map" ? "text-accent font-bold" : "text-text-muted"
            }`}
          >
            <MapIcon size={18} />
            <span className="text-[10px] font-display uppercase tracking-wide">Map</span>
          </button>
          <button
            onClick={() => {
              setMobileTab("list");
              setSheetExpanded(false);
            }}
            className={`flex-1 flex flex-col items-center justify-center gap-0.5 ${
              mobileTab === "list" ? "text-accent font-bold" : "text-text-muted"
            }`}
          >
            <ListIcon size={18} />
            <span className="text-[10px] font-display uppercase tracking-wide">Sites</span>
          </button>
          <button
            onClick={() => {
              setMobileTab("triggers");
              setSheetExpanded(false);
            }}
            className={`flex-1 flex flex-col items-center justify-center gap-0.5 ${
              mobileTab === "triggers" ? "text-accent font-bold" : "text-text-muted"
            }`}
          >
            <TableIcon size={18} />
            <span className="text-[10px] font-display uppercase tracking-wide">Triggers</span>
          </button>
          <button
            onClick={() => setAuditModalOpen(true)}
            className="flex-1 flex flex-col items-center justify-center gap-0.5 text-text-muted"
          >
            <ClockIcon size={18} />
            <span className="text-[10px] font-display uppercase tracking-wide">Audit</span>
          </button>
          <Link
            href="/about"
            className="flex-1 flex flex-col items-center justify-center gap-0.5 text-text-muted hover:text-accent transition-colors"
          >
            <InfoIcon size={18} />
            <span className="text-[10px] font-display uppercase tracking-wide">About</span>
          </Link>
        </div>

      </div>

      {/* Global Audit Log Modal */}
      <AuditLogModal
        isOpen={auditModalOpen}
        onClose={() => setAuditModalOpen(false)}
        auditLogs={auditLogs}
      />
    </div>
  );
}
