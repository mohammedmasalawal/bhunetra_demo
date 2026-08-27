"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import * as api from "./api";
import type { AlertFeature, AlertStatus, Aoi, AuditLogEntry, Site } from "./types";
import { useAuth } from "./auth";

const AOI_PARAM = "aoi";

function initialAoi(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return new URLSearchParams(window.location.search).get(AOI_PARAM);
  } catch {
    return null;
  }
}

function syncAoiToUrl(aoi: string | null) {
  if (typeof window === "undefined") return;
  try {
    const url = new URL(window.location.href);
    if (aoi) url.searchParams.set(AOI_PARAM, aoi);
    else url.searchParams.delete(AOI_PARAM);
    window.history.replaceState(null, "", url.toString());
  } catch {
    /* no-op */
  }
}

export function useDashboard() {
  const { session } = useAuth();
  const token = session?.token ?? null;

  const [aois, setAois] = useState<Aoi[]>([]);
  const [selectedAoi, setSelectedAoiState] = useState<string | null>(initialAoi);

  const [alerts, setAlerts] = useState<AlertFeature[] | null>(null);
  const [sites, setSites] = useState<Site[] | null>(null);
  const [auditLogs, setAuditLogs] = useState<AuditLogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [auditLoading, setAuditLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const setSelectedAoi = useCallback((aoi: string | null) => {
    setSelectedAoiState(aoi);
    syncAoiToUrl(aoi);
  }, []);

  const loadAuditLogs = useCallback(async () => {
    setAuditLoading(true);
    try {
      const res = await api.getAuditLogs(token ?? "");
      setAuditLogs(res.audit_logs);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't load audit history.");
    } finally {
      setAuditLoading(false);
    }
  }, [token]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [aoisRes, alertsRes, sitesRes, auditRes] = await Promise.all([
        api.getAois(token ?? ""),
        api.getAlerts(token ?? "", selectedAoi),
        api.getSites(token ?? "", selectedAoi),
        api.getAuditLogs(token ?? ""),
      ]);
      setAois(aoisRes.aois);
      setAlerts(alertsRes.features);
      setSites(sitesRes.sites);
      setAuditLogs(auditRes.audit_logs);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't load triage data.");
    } finally {
      setLoading(false);
    }
  }, [token, selectedAoi]);

  useEffect(() => {
    let ignore = false;
    (async () => {
      setLoading(true);
      try {
        const [aoisRes, alertsRes, sitesRes, auditRes] = await Promise.all([
          api.getAois(token ?? ""),
          api.getAlerts(token ?? "", selectedAoi),
          api.getSites(token ?? "", selectedAoi),
          api.getAuditLogs(token ?? ""),
        ]);
        if (ignore) return;
        setAois(aoisRes.aois);
        setAlerts(alertsRes.features);
        setSites(sitesRes.sites);
        setAuditLogs(auditRes.audit_logs);
      } catch (err) {
        if (!ignore) {
          setError(err instanceof Error ? err.message : "Couldn't load triage data.");
        }
      } finally {
        if (!ignore) setLoading(false);
      }
    })();
    return () => {
      ignore = true;
    };
  }, [token, selectedAoi]);

  const currentAoi = useMemo(
    () => aois.find((a) => a.id === selectedAoi) ?? null,
    [aois, selectedAoi]
  );

  const alertsById = useMemo(() => {
    const map = new Map<number, AlertFeature>();
    for (const a of alerts ?? []) map.set(a.properties.id, a);
    return map;
  }, [alerts]);

  const alertsBySite = useMemo(() => {
    const map = new Map<number, AlertFeature[]>();
    for (const a of alerts ?? []) {
      const cid = a.properties.cluster_id;
      if (cid == null) continue;
      if (!map.has(cid)) map.set(cid, []);
      map.get(cid)!.push(a);
    }
    return map;
  }, [alerts]);

  function patchAlert(id: number, patch: Partial<AlertFeature["properties"]>) {
    setAlerts((prev) =>
      prev
        ? prev.map((a) =>
            a.properties.id === id
              ? { ...a, properties: { ...a.properties, ...patch } }
              : a
          )
        : prev
    );
  }

  async function generateBrief(alertId: number) {
    const res = await api.generateBrief(alertId, token ?? "");
    patchAlert(alertId, { brief_text: res.brief_text, brief_generated_at: res.generated_at });
    return res;
  }

  async function submitAction(alertId: number, newStatus: AlertStatus, notes: string) {
    patchAlert(alertId, { status: newStatus });
    await api.updateAlertAction(alertId, newStatus, notes, token ?? "");
    await loadAuditLogs();
  }

  return {
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
    auditLoading,
    error,
    reload: load,
    reloadAuditLogs: loadAuditLogs,
    generateBrief,
    submitAction,
  };
}
