import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError } from "./api";
import { CHECK_LABELS, CHECK_ORDER, fieldLabel, readable } from "./format";
import type { CheckResult, ListingStatus } from "./types";

// ---------- icons (inline stroke SVG, decorative) ----------
const PATHS: Record<string, ReactNode> = {
  back: <path d="M15 5 8 12l7 7" />,
  check: <path d="m5 12 5 5 9-10" />,
  pin: (<><path d="M12 21s-7-6.2-7-11.5A7 7 0 0 1 19 9.5C19 14.8 12 21 12 21z" /><circle cx="12" cy="9.5" r="2.5" /></>),
  camera: (<><path d="M4 8h3l2-3h6l2 3h3v12H4z" /><circle cx="12" cy="13" r="3.5" /></>),
  shield: (<><path d="M12 3 4 6v6c0 4.5 3.4 8.3 8 9 4.6-.7 8-4.5 8-9V6z" /><path d="m8.5 12 2.5 2.5 4.5-5" /></>),
  warn: (<><path d="M12 3 2 20h20z" /><path d="M12 10v4M12 17v.5" /></>),
  home: <path d="M3 10.5 12 3l9 7.5V21H3z" />,
  out: (<><path d="M15 4h4v16h-4" /><path d="M10 8l-4 4 4 4M6 12h10" /></>),
  x: <path d="M6 6l12 12M18 6 6 18" />,
  house: (<><path d="M3 11 12 4l9 7" /><path d="M5 10v10h14V10" /><path d="M10 20v-5h4v5" /></>),
  walk: (<><circle cx="13" cy="4.5" r="1.8" /><path d="M10 21l2-6 3 3v3M8 12l3-4 3 2 3 1M12 8l-1 6" /></>),
  car: (<><path d="M5 16V11l2-5h10l2 5v5" /><path d="M3 16h18v3H3z" /><circle cx="7.5" cy="13" r="1" /><circle cx="16.5" cy="13" r="1" /></>),
  train: (<><rect x="6" y="3" width="12" height="13" rx="3" /><path d="M6 10h12M9 20l-2 1M15 20l2 1M9 16l-1 4M15 16l1 4" /></>),
  sofa: (<><path d="M3 18V7M21 18v-5a3 3 0 0 0-3-3h-8v6" /><path d="M3 13h18" /><circle cx="6.5" cy="10.5" r="1.8" /></>),
  wallet: (<><rect x="3" y="6" width="18" height="13" rx="3" /><path d="M3 10h18M16 14.5h2" /></>),
  calendar: (<><rect x="4" y="5" width="16" height="15" rx="2" /><path d="M8 3v4M16 3v4M4 10h16" /></>),
  share: (<><circle cx="6" cy="12" r="2.5" /><circle cx="18" cy="6" r="2.5" /><circle cx="18" cy="18" r="2.5" /><path d="m8.2 10.8 7.6-3.6M8.2 13.2l7.6 3.6" /></>),
  grid: (<><rect x="4" y="4" width="7" height="7" rx="1.5" /><rect x="13" y="4" width="7" height="7" rx="1.5" /><rect x="4" y="13" width="7" height="7" rx="1.5" /><rect x="13" y="13" width="7" height="7" rx="1.5" /></>),
  inbox: (<><path d="M4 13 6 5h12l2 8v6H4z" /><path d="M4 13h5l1 2h4l1-2h5" /></>),
};

export const MODE_ICON: Record<string, string> = { walking: "walk", driving: "car", transit: "train" };

/** A ring chart for one value out of a maximum (trust score, days left). Decorative: the number is in the text. */
export function Ring({ value, max, size = 56, label }: { value: number; max: number; size?: number; label: string }) {
  const r = (size - 8) / 2;
  const c = 2 * Math.PI * r;
  const share = Math.max(0, Math.min(1, value / max));
  return (
    <div style={{ position: "relative", width: size, height: size, flex: "none" }} role="img" aria-label={label}>
      <svg width={size} height={size} aria-hidden="true">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--surface-2)" strokeWidth="6" />
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--accent)" strokeWidth="6" strokeLinecap="round"
          strokeDasharray={`${share * c} ${c}`} transform={`rotate(-90 ${size / 2} ${size / 2})`} />
      </svg>
      <span style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", font: `800 ${Math.round(size / 3.6)}px var(--display)` }}>
        {value}
      </span>
    </div>
  );
}

export function Icon({ name, size = 22 }: { name: keyof typeof PATHS | string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {PATHS[name]}
    </svg>
  );
}

// ---------- status ----------
const PILL: Record<string, { label: string; style: string }> = {
  LIVE: { label: "LIVE", style: "solid" },
  VERIFIED: { label: "VERIFIED", style: "solid" },
  NEEDS_FIX: { label: "NEEDS FIX", style: "outline" },
  REJECTED: { label: "SENT BACK", style: "outline" },
  IN_REVIEW: { label: "IN REVIEW", style: "dashed" },
  PENDING_MINT: { label: "PUBLISHING", style: "dashed" },
  DRAFT: { label: "DRAFT", style: "grey" },
  EXPIRED: { label: "EXPIRED", style: "grey" },
  RENTED: { label: "RENTED", style: "grey" },
  PASS: { label: "PASS", style: "thin" },
  FAIL: { label: "FAIL", style: "ink" },
  UNVERIFIABLE: { label: "CAN’T CHECK", style: "dashed" },
};
const PILL_ICON: Record<string, string> = { LIVE: "check", VERIFIED: "check", PASS: "check", FAIL: "x" };

export function Pill({ status }: { status: ListingStatus | string }) {
  const p = PILL[status] ?? { label: status, style: "grey" };
  const icon = PILL_ICON[status];
  return <span className={`pill ${p.style}`}>{icon && <Icon name={icon} size={12} />}{p.label}</span>;
}

// ---------- chrome ----------
export function AppBar({ title, subtitle, back, right }: {
  title: string; subtitle?: string; back?: string; right?: ReactNode;
}) {
  const navigate = useNavigate();
  return (
    <header className="appbar">
      {back && (
        <button className="icon-btn" aria-label="Back" onClick={() => navigate(back)}>
          <Icon name="back" />
        </button>
      )}
      <div className="title">
        <strong>{title}</strong>
        {subtitle && <span>{subtitle}</span>}
      </div>
      {right}
    </header>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  const message = error instanceof Error ? error.message : String(error);
  return <div className="alert" role="alert">{message}</div>;
}

export function Loading() {
  return <p className="muted" role="status">Loading…</p>;
}

/** Load data on mount (and on reload()); keeps the last error for display. */
export function useLoad<T>(load: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const run = useCallback(load, deps);
  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setData(await run());
      setError(null);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, [run]);
  useEffect(() => {
    reload();
  }, [reload]);
  return { data, error, loading, reload, setData };
}

export function isGone(error: unknown, ...statuses: number[]): boolean {
  return error instanceof ApiError && statuses.includes(error.status);
}

// ---------- audit report ----------
export function CheckReport({ checks }: { checks: CheckResult[] }) {
  const ordered = [...checks].sort((a, b) => CHECK_ORDER.indexOf(a.check) - CHECK_ORDER.indexOf(b.check));
  return (
    <div className="stack">
      {ordered.map((check) => {
        const problems = check.evidence.filter((e) => e.ok !== true);
        const advisories = check.evidence.filter((e) => e.ok === true && e.note.startsWith("ADVISORY: "));
        return (
          <section key={check.check} className="card">
            <div className="card-head">
              <strong className="small">{CHECK_LABELS[check.check] ?? check.check}</strong>
              <Pill status={check.verdict} />
            </div>
            {check.summary && <p className="small muted">{check.summary}</p>}
            {problems.map((e) => (
              <div key={e.field} className="stack" style={{ gap: 4 }}>
                <div className="between small">
                  <strong>{fieldLabel(e.field)}</strong>
                  <span style={{ textAlign: "right" }}>{readable(e.claimed)} · allowed {readable(e.allowed)}</span>
                </div>
                <p className="tiny muted">{e.note}</p>
                {e.suggestion && <p className="suggest">→ {readable(e.suggestion)}</p>}
              </div>
            ))}
            {advisories.map((e) => (
              <p key={e.field} className="small muted">{e.note}</p>
            ))}
          </section>
        );
      })}
    </div>
  );
}

export function NotFound() {
  return (
    <div className="phone">
      <div className="content">
        <h1>Page not found</h1>
        <Link to="/">Go to the start page</Link>
      </div>
    </div>
  );
}
