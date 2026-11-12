import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api";
import { ErrorBox, Icon, Loading, Pill, useLoad } from "../../components";
import { money, rupees } from "../../format";
import { countdown, useNow } from "../public/BookVisit";
import type { CalendarOut, CalendarVisit } from "../../types";

const POLL_MS = 3000;
const HOUR = 3_600_000;

/** Wall-clock parts in the listings' time zone (the server's visit_utc_offset_minutes), so a
 * visit lands in the same day and hour wherever the viewer's browser is. */
function local(iso: string, offsetMin: number) {
  const d = new Date(new Date(iso).getTime() + offsetMin * 60_000);
  return { day: d.toISOString().slice(0, 10), hours: d.getUTCHours() + d.getUTCMinutes() / 60, date: d };
}

const dayTitle = (key: string) =>
  new Date(`${key}T00:00:00Z`).toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "short", timeZone: "UTC" });
const clock = (h: number) => `${Math.floor(h) % 12 || 12}${h >= 12 ? "pm" : "am"}`;
const span = (v: CalendarVisit, off: number) => {
  const a = local(v.starts_at, off).date, b = local(v.ends_at, off).date;
  const t = (d: Date) => d.toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit", timeZone: "UTC" });
  return `${t(a)} – ${t(b)}`;
};

function Lane({ visits, open, close, offset, buffer, now }: {
  visits: CalendarVisit[]; open: number; close: number; offset: number; buffer: number; now: number;
}) {
  const width = close - open;
  const pct = (h: number) => `${(Math.min(Math.max(h, open), close) - open) / width * 100}%`;
  return (
    <div className="lane" role="presentation">
      {Array.from({ length: width + 1 }, (_, i) => (
        <span key={i} className="tick" style={{ left: `${(i / width) * 100}%` }}>{i < width ? clock(open + i) : ""}</span>
      ))}
      {visits.filter((v) => ["SOFT_HOLD", "COMMITTED", "SETTLING", "CAPTURED"].includes(v.status)).map((v) => {
        const s = local(v.starts_at, offset).hours, e = local(v.ends_at, offset).hours;
        const b = buffer / 60;
        return <span key={`b-${v.id}`} className="buffer" style={{ left: pct(s - b), width: `calc(${pct(e + b)} - ${pct(s - b)})` }} />;
      })}
      {visits.map((v) => {
        const s = local(v.starts_at, offset).hours, e = local(v.ends_at, offset).hours;
        const left = new Date(v.expires_at).getTime() - now;
        return (
          <span key={v.id} className={`block ${v.status.toLowerCase()}`} style={{ left: pct(s), width: `calc(${pct(e)} - ${pct(s)})` }}
            title={`${v.listing_title} · ${v.tenant_name} · ${v.status}`}>
            {v.status === "SOFT_HOLD" ? countdown(left) : v.tenant_name.split(" ")[0]}
          </span>
        );
      })}
    </div>
  );
}

export default function VisitCalendar({ isManager }: { isManager: boolean }) {
  const cal = useLoad(() => api.get<CalendarOut>("/visits"));
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const now = useNow();

  // Live updates: a tenant's hold shows up (and counts down) without a reload.
  const { reload } = cal;
  useEffect(() => {
    const t = setInterval(() => void reload(), POLL_MS);
    return () => clearInterval(t);
  }, [reload]);

  const data = cal.data;
  const days = useMemo(() => {
    if (!data) return [];
    const off = data.utc_offset_minutes;
    const today = local(data.now, off).day;
    const keys = Array.from({ length: data.window_days + 1 }, (_, i) =>
      new Date(new Date(`${today}T00:00:00Z`).getTime() + i * 24 * HOUR).toISOString().slice(0, 10));
    return keys.map((day) => {
      const visits = data.visits.filter((v) => local(v.starts_at, off).day === day);
      const brokers = [...new Map(visits.map((v) => [v.broker_id, v.broker_name])).entries()];
      return { day, visits, brokers };
    });
  }, [data]);

  async function settleNow(v: CalendarVisit) {
    setBusy(v.id);
    setActionError(null);
    try {
      await api.post(`/manager/visits/${v.id}/settle-now`);
      await reload();
    } catch (e) {
      setActionError(e);
    } finally {
      setBusy(null);
    }
  }

  if (cal.loading && !data) return <Loading />;
  if (!data) return <ErrorBox error={cal.error} />;
  const [open, close] = data.visit_hours.split("-").map(Number);
  const off = data.utc_offset_minutes;
  const booked = data.visits.filter((v) => v.status === "COMMITTED").length;
  const settled = data.visits.filter((v) => v.status === "CAPTURED" || v.status === "VOIDED").length;
  const held = data.visits.filter((v) => v.status === "SOFT_HOLD").length;

  return (
    <div className="stack" style={{ gap: 20 }}>
      <section className="kpis" aria-label="Visits at a glance">
        <div className="stat tint"><span className="n">{booked}</span><span className="l">Booked visits</span></div>
        <div className="stat warn"><span className="n">{held}</span><span className="l">Held, awaiting PayPal</span></div>
        <div className="stat"><span className="n">{settled}</span><span className="l">Settled by the referee</span></div>
        <div className="stat"><span className="n">{data.transit_buffer_minutes} min</span><span className="l">Travel gap between flats</span></div>
      </section>

      <div className="row small legend" aria-label="Legend">
        <span className="row" style={{ gap: 6 }}><span className="block committed sw" />Booked (fee authorised)</span>
        <span className="row" style={{ gap: 6 }}><span className="block soft_hold sw" />Held, with time left</span>
        <span className="row" style={{ gap: 6 }}><span className="block captured sw" />Fee captured</span>
        <span className="row" style={{ gap: 6 }}><span className="block expired sw" />Released, expired or let go</span>
        <span className="row" style={{ gap: 6 }}><span className="buffer sw" />Travel gap</span>
        <span className="tiny muted" style={{ marginLeft: "auto" }}>Updates every {POLL_MS / 1000}s</span>
      </div>

      <ErrorBox error={cal.error ?? actionError} />

      {days.map(({ day, visits, brokers }) => (
        <section key={day} className="card" aria-label={dayTitle(day)}>
          <div className="between">
            <h2>{dayTitle(day)}</h2>
            <span className="tiny muted">{visits.length} visit{visits.length === 1 ? "" : "s"}</span>
          </div>
          {visits.length === 0 && <p className="small muted">Nothing on the calendar.</p>}
          {brokers.map(([id, name]) => (
            <div key={id} className="stack" style={{ gap: 4 }}>
              {isManager && <strong className="small">{name}</strong>}
              <Lane visits={visits.filter((v) => v.broker_id === id)} open={open} close={close} offset={off}
                buffer={data.transit_buffer_minutes} now={now} />
            </div>
          ))}
          {visits.length > 0 && (
            <ul className="visit-list">
              {visits.map((v) => (
                <li key={v.id}>
                  <div className="between" style={{ alignItems: "flex-start" }}>
                    <div className="stack" style={{ gap: 2, minWidth: 0 }}>
                      <strong className="small">{span(v, off)} · {v.listing_title}</strong>
                      <span className="tiny muted">
                        {v.tenant_name} · {v.tenant_email}{isManager ? ` · with ${v.broker_name}` : ""}
                      </span>
                      {v.qualification && (
                        <span className="tiny muted">
                          {[v.qualification.budget && `budget ${rupees(v.qualification.budget)}`,
                            v.qualification.move_in_date && `moves in ${v.qualification.move_in_date}`,
                            v.qualification.profile].filter(Boolean).join(" · ")}
                        </span>
                      )}
                      <span className="tiny muted">
                        {money(v.fee, v.currency)} hold
                        {v.authorization_ref && ` · ${v.authorization_ref}`}
                        {v.capture_ref && ` · captured ${v.capture_ref}`}
                      </span>
                    </div>
                    <div className="stack" style={{ gap: 6, alignItems: "flex-end", flex: "none" }}>
                      <Pill status={v.status} />
                      {v.outcome && <Pill status={v.outcome} />}
                      {v.status === "SOFT_HOLD" && <span className="tiny mono">{countdown(new Date(v.expires_at).getTime() - now)} left</span>}
                    </div>
                  </div>
                  {v.outcome_reason && <span className="tiny">{v.outcome_reason}</span>}
                  {v.status === "COMMITTED" && v.proof?.waiting_for && <span className="tiny muted">{v.proof.waiting_for}</span>}
                  <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
                    {!isManager && v.status === "COMMITTED" && (
                      <Link className="btn small primary" to={`/b/visits/${v.id}`}><Icon name="pin" size={16} />At the door</Link>
                    )}
                    {isManager && v.status === "COMMITTED" && (
                      <button className="btn small" onClick={() => settleNow(v)} disabled={busy === v.id}>
                        {busy === v.id ? "Settling…" : "Settle now (referee's default)"}
                      </button>
                    )}
                    {v.capture_ref && v.outcome === "LOCKED" && (
                      <Link className="small" to={isManager ? "/m/ledger" : "/b/ledger"}>Close the flat with this token in the money ledger</Link>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      ))}
    </div>
  );
}
