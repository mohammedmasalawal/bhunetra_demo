"use client";

import Link from "next/link";
import {
  SatelliteIcon,
  ShieldCheckIcon,
  GlobeIcon,
  CpuIcon,
  DatabaseIcon,
  CheckCircleIcon,
  ClockIcon,
  CrosshairIcon,
} from "@/components/icons";
import { ThemeToggle } from "@/components/ThemeToggle";

export default function AboutPage() {
  return (
    <div className="min-h-screen bg-bg text-text flex flex-col selection:bg-accent selection:text-accent-text">
      {/* Navigation Header */}
      <header className="sticky top-0 z-50 border-b border-border bg-surface/85 backdrop-blur-md px-4 sm:px-8 py-3.5 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Link
            href="/dashboard"
            className="font-display font-bold text-xl tracking-tight text-text flex items-center gap-2 hover:opacity-90 transition-opacity"
          >
            <span className="text-accent text-2xl leading-none">◈</span>
            <span className="bg-gradient-to-r from-text to-text-muted bg-clip-text">BHUNETRA</span>
          </Link>
          <span className="hidden sm:inline-block px-2 py-0.5 rounded-full border border-border text-[10px] font-display uppercase tracking-widest text-text-faint bg-surface-raised">
            Mission Architecture & Docs
          </span>
        </div>

        <div className="flex items-center gap-3">
          <ThemeToggle />
          <Link
            href="/dashboard"
            className="flex items-center gap-1.5 px-4 py-1.5 rounded-xl bg-accent text-accent-text text-xs font-display font-bold hover:opacity-95 active:scale-95 transition-all shadow-md"
          >
            <span>Live Dashboard</span>
            <span>&rarr;</span>
          </Link>
        </div>
      </header>

      {/* Main Content Container */}
      <main className="flex-1 max-w-6xl w-full mx-auto px-4 sm:px-8 py-10 sm:py-16 space-y-16 sm:space-y-24">
        {/* Hero Section */}
        <section className="text-center space-y-6 max-w-3xl mx-auto">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full border border-accent/30 bg-accent/10 text-accent text-xs font-display font-semibold uppercase tracking-wider animate-fadeIn">
            <span className="w-2 h-2 rounded-full bg-accent animate-pulse" />
            <span>Autonomous Multi-Spectral Earth Observation</span>
          </div>

          <h1 className="text-3xl sm:text-5xl lg:text-6xl font-display font-extrabold tracking-tight text-text leading-[1.15]">
            Next-Gen Spaceborne Surveillance for <span className="text-accent">Mining Governance</span>
          </h1>

          <p className="text-base sm:text-lg text-text-muted leading-relaxed">
            <strong className="text-text font-semibold">BhuNetra (भू-नेत्र)</strong> fuses ESA Sentinel-2 multi-spectral optical imagery, Sentinel-1 all-weather SAR radar, and NASA/NOAA VIIRS nighttime radiance to autonomously detect illegal mining.
          </p>


          {/* Quick Metrics Strip */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 pt-4 text-left">
            <div className="p-4 rounded-2xl border border-border bg-surface shadow-xs">
              <div className="text-[11px] font-display uppercase text-text-faint tracking-wider">Spatial Resolution</div>
              <div className="text-2xl font-display font-bold text-text mt-1">10 Meters</div>
              <div className="text-[11px] text-text-muted mt-0.5">Sentinel-2 Optical & SAR</div>
            </div>

            <div className="p-4 rounded-2xl border border-border bg-surface shadow-xs">
              <div className="text-[11px] font-display uppercase text-text-faint tracking-wider">Constellations</div>
              <div className="text-2xl font-display font-bold text-accent mt-1">3 Satellite APIs</div>
              <div className="text-[11px] text-text-muted mt-0.5">S2 MSI + S1 SAR + VIIRS</div>
            </div>

            <div className="p-4 rounded-2xl border border-border bg-surface shadow-xs">
              <div className="text-[11px] font-display uppercase text-text-faint tracking-wider">Monsoon Coverage</div>
              <div className="text-2xl font-display font-bold text-[var(--compliant)] mt-1">100% Cloud-Free</div>
              <div className="text-[11px] text-text-muted mt-0.5">C-Band Synthetic Radar</div>
            </div>

            <div className="p-4 rounded-2xl border border-border bg-surface shadow-xs">
              <div className="text-[11px] font-display uppercase text-text-faint tracking-wider">Enforcement SLA</div>
              <div className="text-2xl font-display font-bold text-[var(--unverified)] mt-1">48h – 72h</div>
              <div className="text-[11px] text-text-muted mt-0.5">Immutable Audit Trail</div>
            </div>
          </div>
        </section>

        {/* Multi-Sensor Fusion Deep Dive */}
        <section className="space-y-8">
          <div className="space-y-2 text-center max-w-2xl mx-auto">
            <h2 className="text-2xl sm:text-3xl font-display font-bold text-text">
              Multi-Spectral Sensor Fusion Architecture
            </h2>
            <p className="text-sm text-text-muted">
              Why single-satellite monitoring fails and how BhuNetra solves cloud cover, night excavation, and legal boundary disputes.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
            {/* Sensor 1: Optical Sentinel-2 */}
            <div className="rounded-2xl border border-border bg-surface p-6 space-y-4 hover:border-accent/50 transition-all flex flex-col justify-between shadow-xs">
              <div className="space-y-3">
                <div className="w-10 h-10 rounded-xl bg-emerald-500/10 text-emerald-500 flex items-center justify-center font-display font-bold">
                  <GlobeIcon size={22} />
                </div>
                <h3 className="text-lg font-display font-bold text-text">
                  Sentinel-2 MSI (Optical & SWIR)
                </h3>
                <p className="text-xs text-text-muted leading-relaxed">
                  Calculates Normalized Difference Vegetation Index (NDVI) loss alongside Short-Wave Infrared mineral band ratios (<code className="px-1 py-0.5 rounded bg-bg text-[11px]">B4/B2</code> Iron Oxide & <code className="px-1 py-0.5 rounded bg-bg text-[11px]">B12/B8A</code> Ferrous Indices) to pinpoint exposed ore pits and canopy clearing.
                </p>
              </div>

              <div className="pt-3 border-t border-border/60 text-[11px] font-display text-text-faint space-y-1">
                <div className="flex justify-between">
                  <span>Revisit Rate:</span>
                  <strong className="text-text">5 Days</strong>
                </div>
                <div className="flex justify-between">
                  <span>Bands Used:</span>
                  <strong className="text-text">B4, B8, B11, B12</strong>
                </div>
              </div>
            </div>

            {/* Sensor 2: Sentinel-1 SAR Radar */}
            <div className="rounded-2xl border border-border bg-surface p-6 space-y-4 hover:border-accent/50 transition-all flex flex-col justify-between shadow-xs">
              <div className="space-y-3">
                <div className="w-10 h-10 rounded-xl bg-blue-500/10 text-blue-500 flex items-center justify-center font-display font-bold">
                  <SatelliteIcon size={22} />
                </div>
                <h3 className="text-lg font-display font-bold text-text">
                  Sentinel-1 SAR (Radar Backscatter)
                </h3>
                <p className="text-xs text-text-muted leading-relaxed">
                  Uses C-Band Synthetic Aperture Radar (VV + VH polarization) with Lee speckle filtering to penetrate heavy monsoon cloud cover and tropical smoke. Measures surface roughness & elevation alteration in real-time.
                </p>
              </div>

              <div className="pt-3 border-t border-border/60 text-[11px] font-display text-text-faint space-y-1">
                <div className="flex justify-between">
                  <span>Cloud Penetration:</span>
                  <strong className="text-blue-400 font-bold">100% All-Weather</strong>
                </div>
                <div className="flex justify-between">
                  <span>Polarization:</span>
                  <strong className="text-text">Dual VV / VH</strong>
                </div>
              </div>
            </div>

            {/* Sensor 3: VIIRS Day/Night Band */}
            <div className="rounded-2xl border border-border bg-surface p-6 space-y-4 hover:border-accent/50 transition-all flex flex-col justify-between shadow-xs">
              <div className="space-y-3">
                <div className="w-10 h-10 rounded-xl bg-amber-500/10 text-amber-500 flex items-center justify-center font-display font-bold">
                  <CpuIcon size={22} />
                </div>
                <h3 className="text-lg font-display font-bold text-text">
                  VIIRS DNB (Night Radiance)
                </h3>
                <p className="text-xs text-text-muted leading-relaxed">
                  Captures nocturnal anthropogenic activity (500-900 nm radiance band) in nano-Watts. Detects illegal night-shift excavators, heavy haulage truck lights, and diesel generator clusters operating in unpermitted zones.
                </p>
              </div>

              <div className="pt-3 border-t border-border/60 text-[11px] font-display text-text-faint space-y-1">
                <div className="flex justify-between">
                  <span>Temporal Revisit:</span>
                  <strong className="text-text">Nightly (~1:30 AM)</strong>
                </div>
                <div className="flex justify-between">
                  <span>Detection Type:</span>
                  <strong className="text-amber-400 font-bold">Nocturnal Excavation</strong>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* Spatial Intelligence & Cadastral Verification */}
        <section className="rounded-3xl border border-border bg-surface-raised p-6 sm:p-10 space-y-8">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-8 items-center">
            <div className="space-y-4">
              <div className="inline-flex items-center gap-1.5 px-3 py-1 rounded-md bg-[var(--violation-bg)] text-[var(--violation)] text-xs font-display font-bold uppercase tracking-wider">
                <ShieldCheckIcon size={14} />
                <span>Automated Cadastral Legality Engine</span>
              </div>

              <h2 className="text-2xl sm:text-3xl font-display font-bold text-text">
                Separating Permitted Mining from Illegal Encroachment
              </h2>

              <p className="text-sm text-text-muted leading-relaxed">
                A simple satellite change alert is not enough: legitimate leaseholders regularly excavate within their approved coordinates. BhuNetra cross-references every multi-sensor trigger against official state mining lease boundaries with sub-meter cadastral geometry.
              </p>

              <div className="space-y-2.5 pt-2">
                <div className="flex items-start gap-2 text-xs text-text">
                  <CheckCircleIcon size={16} className="text-[var(--violation)] shrink-0 mt-0.5" />
                  <span><strong>Potential Violation:</strong> Disruption detected strictly outside legal lease boundary or in buffer zones (e.g. reserve forests).</span>
                </div>
                <div className="flex items-start gap-2 text-xs text-text">
                  <CheckCircleIcon size={16} className="text-[var(--compliant)] shrink-0 mt-0.5" />
                  <span><strong>Appears Compliant:</strong> Disruption occurred entirely within active, licensed mining lease boundaries with valid environmental clearance.</span>
                </div>
                <div className="flex items-start gap-2 text-xs text-text">
                  <CheckCircleIcon size={16} className="text-[var(--unverified)] shrink-0 mt-0.5" />
                  <span><strong>DBSCAN Spatial Clustering:</strong> Groups contiguous detection polygons into physical mining site clusters for route-optimized field officer inspections.</span>
                </div>
              </div>
            </div>

            {/* Architecture Flow Box */}
            <div className="rounded-2xl border border-border bg-bg p-5 font-display text-xs space-y-3">
              <div className="text-[11px] font-bold text-text uppercase tracking-wider pb-2 border-b border-border flex items-center justify-between">
                <span>Autonomous Triage Flow</span>
                <span className="text-accent">Stage 1 &rarr; 5</span>
              </div>

              <div className="space-y-2">
                <div className="p-2.5 rounded-lg border border-border bg-surface flex items-center gap-3">
                  <span className="w-5 h-5 rounded-full bg-accent/10 text-accent font-bold flex items-center justify-center text-[10px]">1</span>
                  <div>
                    <strong className="text-text">Satellite Ingestion & Preprocessing</strong>
                    <div className="text-[11px] text-text-muted">Orthorectification & Co-Registration</div>
                  </div>
                </div>

                <div className="p-2.5 rounded-lg border border-border bg-surface flex items-center gap-3">
                  <span className="w-5 h-5 rounded-full bg-accent/10 text-accent font-bold flex items-center justify-center text-[10px]">2</span>
                  <div>
                    <strong className="text-text">Multi-Spectral Change Scoring</strong>
                    <div className="text-[11px] text-text-muted">NDVI Drop + SAR dB + VIIRS Night Radiance</div>
                  </div>
                </div>

                <div className="p-2.5 rounded-lg border border-border bg-surface flex items-center gap-3">
                  <span className="w-5 h-5 rounded-full bg-accent/10 text-accent font-bold flex items-center justify-center text-[10px]">3</span>
                  <div>
                    <strong className="text-text">Cadastral Boundary Intersection</strong>
                    <div className="text-[11px] text-text-muted">Point-in-Polygon Lease Spatial Validation</div>
                  </div>
                </div>

                <div className="p-2.5 rounded-lg border border-border bg-surface flex items-center gap-3">
                  <span className="w-5 h-5 rounded-full bg-accent/10 text-accent font-bold flex items-center justify-center text-[10px]">4</span>
                  <div>
                    <strong className="text-text">Field Officer Alert & SLA Clock</strong>
                    <div className="text-[11px] text-text-muted">48-Hour Enforced Inspection Countdown</div>
                  </div>
                </div>

                <div className="p-2.5 rounded-lg border border-border bg-surface flex items-center gap-3">
                  <span className="w-5 h-5 rounded-full bg-accent/10 text-accent font-bold flex items-center justify-center text-[10px]">5</span>
                  <div>
                    <strong className="text-text">Tamper-Proof Audit Logging</strong>
                    <div className="text-[11px] text-text-muted">Immutable Ledger & Legal FIR Export</div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* Enforcement SLA & Immutable Audit Ledger */}
        <section className="space-y-6">
          <div className="space-y-2 text-center max-w-2xl mx-auto">
            <h2 className="text-2xl sm:text-3xl font-display font-bold text-text">
              Accountability & Chain-of-Custody
            </h2>
            <p className="text-sm text-text-muted">
              Built specifically for Indian Bureau of Mines (IBM) and Directorate of Geology and Mining (DGM) legal compliance standards.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
            <div className="p-5 rounded-2xl border border-border bg-surface space-y-2">
              <ClockIcon size={20} className="text-accent" />
              <h3 className="font-display font-bold text-text text-sm">Enforced SLA Clocks</h3>
              <p className="text-xs text-text-muted leading-relaxed">
                Automated countdown timers (Calm &rarr; Warning &rarr; Critical &rarr; Breached). Alerts escalate directly to DGM headquarters if uninspected past 48 hours.
              </p>
            </div>

            <div className="p-5 rounded-2xl border border-border bg-surface space-y-2">
              <DatabaseIcon size={20} className="text-emerald-500" />
              <h3 className="font-display font-bold text-text text-sm">Immutable Audit Logs</h3>
              <p className="text-xs text-text-muted leading-relaxed">
                Every login, triage review, spot fine, and status change is hashed and stored in an append-only audit trail with 1-click CSV/JSON export for legal proceedings.
              </p>
            </div>

            <div className="p-5 rounded-2xl border border-border bg-surface space-y-2">
              <CrosshairIcon size={20} className="text-blue-500" />
              <h3 className="font-display font-bold text-text text-sm">Precision GPS & GIS</h3>
              <p className="text-xs text-text-muted leading-relaxed">
                Direct Decimal Degrees (DD) and Degrees-Minutes-Seconds (DMS) export with integrated 1-click Google Maps navigation routing for ground patrol officers.
              </p>
            </div>
          </div>
        </section>

        {/* CTA Bottom Banner */}
        <section className="rounded-3xl border border-border bg-gradient-to-br from-surface to-surface-raised p-8 sm:p-12 text-center space-y-6 shadow-xl">
          <h2 className="text-2xl sm:text-4xl font-display font-extrabold text-text">
            Ready to Inspect Live Detections?
          </h2>
          <p className="text-sm text-text-muted max-w-xl mx-auto">
            Experience the full field command dashboard with satellite layer toggles, multi-spectral image comparisons, and trigger inspection.
          </p>

          <div className="flex flex-wrap items-center justify-center gap-3">
            <Link
              href="/dashboard"
              className="px-6 py-3 rounded-xl bg-accent text-accent-text font-display font-bold text-sm hover:opacity-95 active:scale-95 transition-all shadow-lg"
            >
              Open Command Dashboard &rarr;
            </Link>
            <Link
              href="/login"
              className="px-6 py-3 rounded-xl border border-border bg-surface hover:bg-surface-raised font-display font-semibold text-sm text-text transition-all"
            >
              Officer Login
            </Link>
          </div>
        </section>
      </main>

      {/* Footer */}
      <footer className="border-t border-border bg-surface py-6 px-4 sm:px-8 text-center text-xs font-display text-text-faint">
        <div className="max-w-6xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <span className="text-accent font-bold">◈</span>
            <span className="text-text font-semibold">BhuNetra</span>
            <span>&middot; Autonomous Mining Surveillance System</span>
          </div>
          <div>
            <span>Multi-region satellite monitoring &middot; India</span>
          </div>
        </div>
      </footer>
    </div>
  );
}
