"use client";

import Link from "next/link";
import { useAuth } from "@/lib/auth";
import { ThemeToggle } from "./ThemeToggle";
import { RegionSelect } from "./RegionSelect";
import { LogoutIcon, RefreshIcon, ClockIcon, TableIcon, ListIcon, InfoIcon, BuildingIcon } from "./icons";



import type { Aoi, DashboardViewMode } from "@/lib/types";

const ROLE_LABEL: Record<string, string> = {
  FIELD_OFFICER: "Field Officer",
  DGM_ADMIN: "DGM / IBM HQ",
};

export function TopBar({
  onRefresh,
  refreshing,
  viewMode = "sites",
  onViewModeChange,
  onOpenAuditLogs,
  auditLogCount = 0,
  aois = [],
  selectedAoi = null,
  onSelectAoi,
}: {
  onRefresh: () => void;
  refreshing: boolean;
  viewMode?: DashboardViewMode;
  onViewModeChange?: (mode: DashboardViewMode) => void;
  onOpenAuditLogs?: () => void;
  auditLogCount?: number;
  aois?: Aoi[];
  selectedAoi?: string | null;
  onSelectAoi?: (aoi: string | null) => void;
}) {
  const { session, logout } = useAuth();

  return (
    <header className="flex items-center justify-between gap-3 border-b border-border bg-surface px-3.5 py-2.5 shrink-0 z-20">
      {/* Brand & Role */}
      <div className="flex items-center gap-2.5 min-w-0">
        <div className="font-display font-bold text-lg tracking-tight text-text shrink-0 flex items-center gap-1.5">
          <span className="text-accent">◈</span>
          <span>BHUNETRA</span>
        </div>
        {session && (
          <span
            className="hidden sm:inline-flex items-center rounded-md px-2 py-0.5 text-[11px] font-display font-semibold uppercase tracking-wide"
            style={{ background: "var(--unverified-bg)", color: "var(--unverified)" }}
          >
            {ROLE_LABEL[session.role] ?? session.role}
          </span>
        )}

        {onSelectAoi && aois.length > 0 && (
          <RegionSelect aois={aois} value={selectedAoi} onChange={onSelectAoi} />
        )}
      </div>

      {/* Center View Mode Switcher (Desktop) */}
      {onViewModeChange && (
        <div className="hidden md:flex items-center p-1 rounded-xl bg-bg border border-border">
          <button
            onClick={() => onViewModeChange("sites")}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-display transition-all ${
              viewMode === "sites"
                ? "bg-surface text-text font-bold shadow-xs border border-border"
                : "text-text-muted hover:text-text"
            }`}
          >
            <ListIcon size={14} />
            <span>Site Clusters</span>
          </button>

          <button
            onClick={() => onViewModeChange("triggers")}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-display transition-all ${
              viewMode === "triggers"
                ? "bg-surface text-text font-bold shadow-xs border border-border"
                : "text-text-muted hover:text-text"
            }`}
          >
            <TableIcon size={14} />
            <span>Trigger Column</span>
          </button>
        </div>
      )}

      {/* Right Controls */}
      <div className="flex items-center gap-2 shrink-0">
        {/* Audit Log Ledger Button */}
        {onOpenAuditLogs && (
          <button
            onClick={onOpenAuditLogs}
            className="flex items-center gap-1.5 px-2.5 py-2 rounded-lg border border-border bg-surface text-xs font-display font-semibold text-text hover:border-accent active:scale-95 transition-all"
            title="Open Enforcement Audit Ledger"
          >
            <ClockIcon size={15} className="text-accent" />
            <span className="hidden sm:inline">Audit Logs</span>
            {auditLogCount > 0 && (
              <span className="ml-0.5 px-1.5 py-0.2 rounded-full bg-accent text-accent-text text-[10px] font-bold">
                {auditLogCount}
              </span>
            )}
          </button>
        )}

        {/* DGM Portal Link */}
        <Link
          href="/dgm"
          className="flex items-center gap-1.5 px-2.5 py-2 rounded-lg border border-amber-500/40 bg-amber-500/10 hover:bg-amber-500/20 text-xs font-display font-semibold text-amber-500 active:scale-95 transition-all"
          title="Directorate of Geology & Mining Escalation & Legal Action Portal"
        >
          <BuildingIcon size={14} />
          <span className="hidden md:inline">DGM Portal</span>
        </Link>

        {/* About Mission & Docs */}
        <Link
          href="/about"
          className="flex items-center gap-1.5 px-2.5 py-2 rounded-lg border border-border bg-surface text-xs font-display font-semibold text-text hover:border-accent active:scale-95 transition-all"
          title="About BhuNetra Spaceborne Surveillance Architecture"
        >
          <InfoIcon size={15} className="text-accent" />
          <span className="hidden md:inline">About</span>
        </Link>


        {session && (
          <span className="hidden lg:block text-xs text-text-muted truncate max-w-[130px] font-display">
            {session.name}
          </span>
        )}

        <button
          onClick={onRefresh}
          aria-label="Refresh"
          className="grid h-9 w-9 place-items-center rounded-lg border border-border text-text-muted hover:text-text active:scale-95 transition-transform"
        >
          <RefreshIcon size={16} className={refreshing ? "animate-spin" : ""} />
        </button>

        <ThemeToggle />


        <button
          onClick={logout}
          aria-label="Log out"
          className="grid h-9 w-9 place-items-center rounded-lg border border-border text-text-muted hover:text-text active:scale-95 transition-transform"
        >
          <LogoutIcon size={16} />
        </button>
      </div>
    </header>
  );
}

