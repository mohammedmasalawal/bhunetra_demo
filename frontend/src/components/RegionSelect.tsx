"use client";

import type { Aoi } from "@/lib/types";
import { GlobeIcon } from "./icons";

/**
 * Region (AOI) picker. "All Regions" = no filter. Each option shows the
 * region name, state and its live alert count; an escalated count, if any,
 * is appended. Driven entirely by GET /api/v1/aois.
 */
export function RegionSelect({
  aois,
  value,
  onChange,
}: {
  aois: Aoi[];
  value: string | null;
  onChange: (aoi: string | null) => void;
}) {
  const totalAlerts = aois.reduce((n, a) => n + a.alert_count, 0);

  return (
    <label className="relative flex items-center gap-1.5 rounded-lg border border-border bg-surface pl-2.5 pr-1 py-1.5 text-xs font-display text-text hover:border-accent transition-colors">
      <GlobeIcon size={14} className="text-accent shrink-0" />
      <select
        aria-label="Region"
        className="appearance-none bg-transparent pr-4 text-xs font-display font-semibold text-text outline-none cursor-pointer max-w-[200px]"
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value || null)}
      >
        <option value="">All Regions ({totalAlerts})</option>
        {aois.map((a) => (
          <option key={a.id} value={a.id}>
            {a.name}
            {a.state ? ` · ${a.state}` : ""} ({a.alert_count}
            {a.escalated_count ? `, ${a.escalated_count} escalated` : ""})
          </option>
        ))}
      </select>
      <span className="pointer-events-none absolute right-2 text-text-muted">▾</span>
    </label>
  );
}
