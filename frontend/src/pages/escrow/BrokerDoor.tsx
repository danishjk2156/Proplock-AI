import { useState } from "react";
import type { FormEvent } from "react";
import { useParams } from "react-router-dom";
import { api } from "../../api";
import { AppBar, ErrorBox, Icon, Loading, Pill, useLoad } from "../../components";
import { money } from "../../format";
import type { CalendarOut, VisitOut } from "../../types";
import { currentPosition, usePoll } from "./shared";

const time = (iso: string) => new Date(iso).toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" });

/** The broker at the door: check in, type the tenant's PIN, approve a lock, or claim a no-show. */
export default function BrokerDoor() {
  const { visitId = "" } = useParams();
  const cal = useLoad(() => api.get<CalendarOut>("/visits"));
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [note, setNote] = useState<string | null>(null);
  const v = cal.data?.visits.find((x) => x.id === visitId) ?? null;
  usePoll(() => void cal.reload(), 4000, v?.status === "COMMITTED" || v?.status === "SETTLING");

  async function run(label: string, call: () => Promise<VisitOut>) {
    setBusy(label);
    setError(null);
    setNote(null);
    try {
      const out = await call();
      setNote(out.message);
      await cal.reload();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  }

  function submitPin(e: FormEvent) {
    e.preventDefault();
    void run("pin", () => api.post<VisitOut>(`/broker/visits/${visitId}/pin`, { pin }));
    setPin("");
  }

  if (cal.loading && !cal.data) return <div className="phone"><div className="content"><Loading /></div></div>;
  if (!v) {
    return (
      <div className="phone">
        <AppBar title="Visit" back="/b/visits" />
        <main className="content"><p className="muted">This visit isn’t on your calendar.</p><ErrorBox error={cal.error} /></main>
      </div>
    );
  }

  const p = v.proof;
  const open = v.status === "COMMITTED";
  const afterGrace = p ? Date.now() >= new Date(p.grace_ends_at).getTime() : false;
  return (
    <div className="phone">
      <AppBar title="At the door" subtitle={`${v.listing_title} · ${time(v.starts_at)}`} back="/b/visits" />
      <main className="content">
        <section className="card" aria-label="Visit">
          <div className="between">
            <strong>{v.tenant_name}</strong>
            <Pill status={v.outcome ?? v.status} />
          </div>
          <span className="small muted">
            {time(v.starts_at)} – {time(v.ends_at)} · {money(v.fee, v.currency)}{" "}
            {v.status === "CAPTURED" ? "captured" : v.status === "VOIDED" ? "released to the tenant" : "held by PayPal"}
          </span>
          {v.outcome_reason && <p className="small">{v.outcome_reason}</p>}
          {v.capture_ref && (
            <p className="tiny muted">
              PayPal capture {v.capture_ref}.{" "}
              {v.outcome === "LOCKED" ? "It’s the token deducted on the move-in invoice." : "It compensates you for the wasted trip (paid out by the agency)."}
            </p>
          )}
        </section>

        {open && p && (
          <>
            <form className="card strong" onSubmit={submitPin} aria-label="Tenant's PIN">
              <h2>Tenant’s PIN</h2>
              {p.pin_entered ? (
                <div className="alert info small"><Icon name="check" size={14} /> PIN accepted: the visit is checked in.</div>
              ) : (
                <>
                  <label className="visually-hidden" htmlFor="pin">4-digit PIN</label>
                  <input id="pin" className="pin-input" inputMode="numeric" autoComplete="off" pattern="\d{4}" maxLength={4}
                    value={pin} onChange={(e) => setPin(e.target.value.replace(/\D/g, ""))} placeholder="····" />
                  <button className="btn primary" disabled={pin.length !== 4 || !!busy}>{busy === "pin" ? "Checking…" : "Enter PIN"}</button>
                  <span className="tiny muted">{p.pin_attempts_left} tries left. The tenant shows it on their phone.</span>
                </>
              )}
            </form>

            <section className="card" aria-label="Check in">
              <div className="between">
                <h2>You at the flat</h2>
                {p.broker_at_flat && <span className="pill thin"><Icon name="check" size={12} />CHECKED IN</span>}
              </div>
              <button className="btn" onClick={() => run("gps", async () =>
                api.post<VisitOut>(`/broker/visits/${visitId}/checkin`, await currentPosition()))} disabled={!!busy}>
                <Icon name="pin" size={18} />{busy === "gps" ? "Getting your location…" : "I’m here"}
              </button>
              <span className="tiny muted">Needed to claim a no-show: your own GPS must place you at the flat.</span>
            </section>

            {p.tenant_choice === "LOCK" && !p.broker_approved_lock && (
              <section className="card strong" aria-label="Approve the lock">
                <h2>{v.tenant_name} wants to lock the flat</h2>
                <button className="btn primary" onClick={() => run("lock", () => api.post<VisitOut>(`/broker/visits/${visitId}/approve-lock`))} disabled={!!busy}>
                  Approve: capture {money(v.fee, v.currency)} as the token
                </button>
              </section>
            )}

            {!p.pin_entered && !p.tenant_at_flat && (
              <section className="card flat" aria-label="Tenant didn't come">
                <h2 style={{ fontSize: 16 }}>Tenant didn’t come?</h2>
                <button className="btn small dark" onClick={() => run("noshow", () => api.post<VisitOut>(`/broker/visits/${visitId}/claim-no-show`))}
                  disabled={!!busy || !afterGrace || !p.broker_at_flat}>
                  Claim no-show
                </button>
                <span className="tiny muted">
                  {!afterGrace ? `Available from ${time(p.grace_ends_at)} (30 minutes after the start).`
                    : p.broker_at_flat ? "You’re checked in at the flat, so you can claim it." : "Needs your GPS check-in above."}
                </span>
              </section>
            )}

            {note && <div className="alert warn small">{note}</div>}
            <ErrorBox error={error} />
            {p.waiting_for && <p className="tiny muted">{p.waiting_for}</p>}
          </>
        )}
      </main>
    </div>
  );
}
