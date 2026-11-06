import { useEffect, useState } from "react";
import { api } from "../../api";
import { CheckReport, ErrorBox, Loading, Pill, useLoad } from "../../components";
import { rupees, shortDate } from "../../format";
import type { AuditOut, ReviewItem } from "../../types";
import ManagerLayout from "./Layout";

export default function ManagerReviews() {
  const queue = useLoad(() => api.get<ReviewItem[]>("/manager/reviews"));
  const [selected, setSelected] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  useEffect(() => {
    const ids = queue.data?.map((r) => r.audit_id) ?? [];
    if (!selected || !ids.includes(selected)) setSelected(ids[0] ?? null);
  }, [queue.data, selected]);
  const item = queue.data?.find((r) => r.audit_id === selected) ?? null;

  return (
    <ManagerLayout reviewCount={queue.data?.length}>
      <div className="stack" style={{ gap: 4 }}>
        <h1 style={{ fontSize: 26 }}>Review queue</h1>
        <p className="muted">Only what the checks couldn’t decide. Everything else was checked automatically.</p>
      </div>
      {done && <p className="alert info" role="status">{done}</p>}
      {queue.loading && !queue.data && <Loading />}
      <ErrorBox error={queue.error} />

      <div className="row" style={{ alignItems: "flex-start", flexWrap: "wrap", gap: 20 }}>
        <section aria-label="Waiting for review" className="stack" style={{ flex: "1 1 280px" }}>
          {queue.data?.map((r) => (
            <button key={r.audit_id} onClick={() => setSelected(r.audit_id)} aria-pressed={r.audit_id === selected}
              className={`card${r.audit_id === selected ? " strong" : ""}`} style={{ textAlign: "left", font: "inherit", cursor: "pointer", color: "inherit" }}>
              <strong>{r.property_id} · version {r.version_no}</strong>
              <span className="small muted">{r.broker_name} · waiting since {shortDate(r.created_at)}</span>
              <span className="small">{r.open_questions.length} open question{r.open_questions.length === 1 ? "" : "s"}</span>
            </button>
          ))}
          {queue.data?.length === 0 && <div className="card dashed small muted">Nothing is waiting.</div>}
        </section>

        {/* Only render an item that is in the current queue: after a decision the list reloads
            and the decided item disappears, while `selected` may still point at it for a render. */}
        {item && (
          <ReviewDetail
            key={item.audit_id}
            item={item}
            onDecided={(message) => { setDone(message); setSelected(null); queue.reload(); }}
          />
        )}
      </div>
    </ManagerLayout>
  );
}

function ReviewDetail({ item, onDecided }: { item: ReviewItem; onDecided: (message: string) => void }) {
  const audit = useLoad(() => api.get<AuditOut>(`/audits/${item.audit_id}`), [item.audit_id]);
  const [note, setNote] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function decide(approve: boolean) {
    setBusy(true);
    setError(null);
    try {
      const out = await api.post<{ audit_status: string; listing_status: string }>(`/manager/reviews/${item.audit_id}`, { approve, note });
      onDecided(approve
        ? `Approved: the listing is ${out.listing_status === "LIVE" ? "live" : "verified and publishing to PayPal"}.`
        : "Sent back to the broker with your note.");
    } catch (e) {
      setError(e);
      setBusy(false);
    }
  }

  const terms = audit.data?.report.terms;
  return (
    <section aria-label="Review detail" className="card" style={{ flex: "999 1 520px", minWidth: 0, padding: 24, gap: 18 }}>
      {audit.loading && !audit.data && <Loading />}
      <ErrorBox error={audit.error} />
      {audit.data && (
        <>
          <div className="between" style={{ alignItems: "flex-start", flexWrap: "wrap" }}>
            <div className="stack" style={{ gap: 4 }}>
              <h2 style={{ fontSize: 20 }}>{terms?.title}</h2>
              {terms && <span className="small muted">{rupees(terms.rent)} · deposit {rupees(terms.deposit)} · {terms.furnishing} · {item.broker_name}</span>}
            </div>
            <Pill status="IN_REVIEW" />
          </div>

          <div className="stack">
            <h3 className="section-label">Couldn’t be checked</h3>
            {item.open_questions.map((q) => <div key={q} className="card dashed small">{q}</div>)}
          </div>

          <div className="stack">
            <h3 className="section-label">The full report</h3>
            <CheckReport checks={audit.data.report.checks} />
          </div>

          <div className="stack" style={{ borderTop: "1px solid var(--line)", paddingTop: 18 }}>
            <label htmlFor="note">Your note (required, saved next to the signed report)</label>
            <textarea id="note" rows={3} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Called the owner: the metro is 9 min on foot." />
            <ErrorBox error={error} />
            <div className="row" style={{ flexWrap: "wrap" }}>
              <button className="btn primary" disabled={busy || note.trim().length < 3} onClick={() => decide(true)}>Approve and publish</button>
              <button className="btn" disabled={busy || note.trim().length < 3} onClick={() => decide(false)}>Send back to broker</button>
            </div>
            <p className="small muted">Approve: published as a PayPal product and goes live. Send back: the broker sees your note and can fix the ad.</p>
          </div>
        </>
      )}
    </section>
  );
}
