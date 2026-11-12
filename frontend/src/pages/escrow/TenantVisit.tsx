import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api";
import { ErrorBox, Icon, Pill } from "../../components";
import { money } from "../../format";
import type { VisitOut } from "../../types";
import { currentPosition, usePoll } from "./shared";

const when = (iso: string) =>
  new Date(iso).toLocaleString("en-IN", { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });

function Check({ ok, children }: { ok: boolean; children: string }) {
  return (
    <li className="row small" style={{ gap: 8 }}>
      <span className={`pill ${ok ? "solid" : "grey"}`} style={{ padding: "2px 6px" }}>
        <Icon name={ok ? "check" : "x"} size={12} />
      </span>
      {children}
    </li>
  );
}

/** The tenant's booked visit: PIN, check-in at the flat, then Pass or Lock. The referee decides the money. */
export default function TenantVisit({ visit, token, listingId, title, brokerName, onChange }: {
  visit: VisitOut; token: string; listingId: string; title: string; brokerName: string; onChange: (v: VisitOut) => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [note, setNote] = useState<string | null>(null);
  const photo = useRef<HTMLInputElement>(null);
  const headers = { "X-Hold-Token": token };
  const p = visit.proof;
  const open = visit.status === "COMMITTED";
  const afterGrace = p ? Date.now() >= new Date(p.grace_ends_at).getTime() : false;

  usePoll(async () => {
    try {
      onChange(await api.get<VisitOut>(`/public/visits/${visit.id}`, headers));
    } catch {
      // keep showing the last state; the next poll retries
    }
  }, 5000, open || visit.status === "SETTLING");

  async function run(label: string, call: () => Promise<VisitOut>) {
    setBusy(label);
    setError(null);
    setNote(null);
    try {
      const v = await call();
      onChange(v);
      setNote(v.message);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  }

  const checkin = () => run("gps", async () =>
    api.post<VisitOut>(`/public/visits/${visit.id}/checkin`, await currentPosition(), headers));
  const decide = (choice: "PASS" | "LOCK" | "BROKER_ABSENT") => run(choice, () =>
    api.post<VisitOut>(`/public/visits/${visit.id}/decision`, { choice }, headers));

  async function sendPhoto(file: File) {
    await run("photo", async () => {
      const form = new FormData();
      form.append("file", file);
      try {
        const fix = await currentPosition();
        form.append("latitude", String(fix.latitude));
        form.append("longitude", String(fix.longitude));
        form.append("accuracy_m", String(fix.accuracy_m));
      } catch {
        // no device location: the server falls back to the photo's own GPS (EXIF)
      }
      return api.upload<VisitOut>(`/public/visits/${visit.id}/photo`, form, headers);
    });
  }

  if (!open) {
    const captured = visit.status === "CAPTURED";
    return (
      <section className="card strong" aria-label="How the visit ended">
        <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
          {visit.outcome && <Pill status={visit.outcome} />}
          <Pill status={visit.status} />
        </div>
        <h2>{visit.status === "SETTLING" ? "Settling with PayPal…" : captured ? `${money(visit.fee, visit.currency)} was captured` : "Your hold was released"}</h2>
        {visit.outcome_reason && <p className="small">{visit.outcome_reason}</p>}
        {visit.outcome === "LOCKED" && (
          <p className="small muted">The {money(visit.fee, visit.currency)} is deducted from your move-in invoice, which {brokerName} sends next.</p>
        )}
        <p className="tiny muted">
          PayPal {visit.capture_ref ? `capture ${visit.capture_ref}` : `authorization ${visit.authorization_ref}`}
          {visit.demo_authorization ? " (demo: no real PayPal call)" : ""}
        </p>
        <Link className="btn" to={`/l/${listingId}`}>Back to the listing</Link>
      </section>
    );
  }

  return (
    <div className="stack" style={{ gap: 16 }}>
      <section className="card strong" aria-label="Your visit">
        <span className="pill solid"><Icon name="check" size={12} />BOOKED</span>
        <h2>{when(visit.starts_at)}</h2>
        <p className="small">{title} with {brokerName}. {money(visit.fee, visit.currency)} is held by PayPal, not charged.</p>
        <div className="pin" aria-label={`Your door PIN is ${visit.pin?.split("").join(" ")}`}>
          {(visit.pin ?? "····").split("").map((d, i) => <span key={i}>{d}</span>)}
        </div>
        <p className="tiny muted">Show this PIN to {brokerName} at the door. Once it’s entered you can never be charged as a no-show.</p>
      </section>

      <section className="card" aria-label="At the flat">
        <h2>At the flat</h2>
        <ul className="checks">
          <Check ok={!!p?.pin_entered}>PIN entered by the broker</Check>
          <Check ok={!!p?.tenant_at_flat}>You checked in here (GPS)</Check>
          <Check ok={!!p?.broker_at_flat}>The broker checked in (GPS)</Check>
        </ul>
        <button className="btn" onClick={checkin} disabled={!!busy}>
          <Icon name="pin" size={18} />{busy === "gps" ? "Getting your location…" : "I’m at the flat"}
        </button>
        {note && <div className="alert warn small">{note}</div>}
        <ErrorBox error={error} />
        {p?.waiting_for && <p className="tiny muted">{p.waiting_for}</p>}
      </section>

      {(p?.pin_entered || p?.tenant_at_flat) && (
        <section className="card" aria-label="Your decision">
          <h2>Seen the flat?</h2>
          {p.tenant_choice === "LOCK" && !p.broker_approved_lock && (
            <div className="alert info small">You chose to lock it. Waiting for {brokerName} to approve.</div>
          )}
          <div className="grid2">
            <button className="btn" onClick={() => decide("PASS")} disabled={!!busy}>Not for me</button>
            <button className="btn primary" onClick={() => decide("LOCK")} disabled={!!busy || p.tenant_choice === "LOCK"}>Lock this flat</button>
          </div>
          <span className="tiny muted">Not for me: the hold is released, $0. Lock: the fee becomes your token, credited on the move-in invoice.</span>
        </section>
      )}

      {afterGrace && !p?.pin_entered && !p?.broker_at_flat && (
        <section className="card flat" aria-label="Broker didn't come">
          <h2 style={{ fontSize: 16 }}>The broker didn’t come?</h2>
          <p className="small muted">Check in here, take a live photo at the door, then report it. The hold is released and the listing is taken down.</p>
          <input ref={photo} type="file" accept="image/*" capture="environment" hidden
            onChange={(e) => { const f = e.target.files?.[0]; if (f) void sendPhoto(f); e.target.value = ""; }} />
          <div className="grid2">
            <button className="btn small" onClick={() => photo.current?.click()} disabled={!!busy}>
              <Icon name="camera" size={16} />{busy === "photo" ? "Uploading…" : p?.tenant_photo ? "Photo added" : "Take a photo"}
            </button>
            <button className="btn small dark" onClick={() => decide("BROKER_ABSENT")} disabled={!!busy || !p?.tenant_photo || !p?.tenant_at_flat}>
              Report no-show
            </button>
          </div>
        </section>
      )}
    </div>
  );
}
