import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError } from "./api";
import { CHECK_LABELS, CHECK_ORDER, fieldLabel, readable } from "./format";
import type { CheckResult } from "./types";
import { Icon } from "./ui/Icon";
import { StatusPill } from "./ui/StatusPill";

export { Icon, StatusPill };
/** Kept for the screens not yet moved to StatusPill. */
export const Pill = StatusPill;

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
