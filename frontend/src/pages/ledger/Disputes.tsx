import { useEffect, useState } from "react";
import { api } from "../../api";
import { ErrorBox, Loading, Pill, useLoad } from "../../components";
import { kindLabel, money, shortDate } from "../../format";
import type { DisputeOut } from "../../types";
import ManagerLayout from "../manager/Layout";

const CITE = /\[([a-z_]+:[A-Za-z0-9-]+)\]/g;

/** Dispute defense: the evidence pack, a cited draft, and a person's approval before anything is sent. */
export default function Disputes() {
  const list = useLoad(() => api.get<DisputeOut[]>("/manager/disputes"));
  const [selected, setSelected] = useState<string | null>(null);
  useEffect(() => {
    if (!selected && list.data?.length) setSelected(list.data[0].id);
  }, [list.data, selected]);
  const dispute = list.data?.find((d) => d.id === selected) ?? null;

  return (
    <ManagerLayout>
      <div className="stack" style={{ gap: 4 }}>
        <h1 style={{ fontSize: 26 }}>Disputes</h1>
        <p className="muted">Evidence comes from PropLock’s own signed records. The AI drafts; you approve the exact words before anything reaches PayPal.</p>
      </div>
      {list.loading && !list.data && <Loading />}
      <ErrorBox error={list.error} />
      <div className="row" style={{ alignItems: "flex-start", flexWrap: "wrap", gap: 20 }}>
        <section aria-label="Disputes" className="stack" style={{ flex: "1 1 260px" }}>
          {list.data?.map((d) => (
            <button key={d.id} onClick={() => setSelected(d.id)} aria-pressed={d.id === selected}
              className={`card${d.id === selected ? " strong" : ""}`} style={{ textAlign: "left", font: "inherit", cursor: "pointer", color: "inherit" }}>
              <div className="between"><strong className="small mono">{d.paypal_dispute_id}</strong><Pill status={d.status} /></div>
              <span className="small">{kindLabel(d.reason)}</span>
              <span className="small muted">{money(d.amount, d.currency)} · opened {shortDate(d.created_at)}</span>
            </button>
          ))}
          {list.data?.length === 0 && <div className="card dashed small muted">No disputes. They appear here when PayPal reports one.</div>}
        </section>
        {dispute && <DisputeDetail key={dispute.id} dispute={dispute} onChanged={(d) => list.setData((rows) => rows?.map((r) => (r.id === d.id ? d : r)) ?? null)} />}
      </div>
    </ManagerLayout>
  );
}

function DisputeDetail({ dispute, onChanged }: { dispute: DisputeOut; onChanged: (d: DisputeOut) => void }) {
  const [text, setText] = useState(dispute.final_response ?? dispute.draft_response ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const items = dispute.evidence_pack?.items ?? [];
  const known = new Set(items.map((i) => i.source_id));
  const cited = [...text.matchAll(CITE)].map((m) => m[1]);
  const unknown = cited.filter((c) => !known.has(c));
  const editable = dispute.status === "OPEN";

  async function run(fn: () => Promise<DisputeOut>) {
    setBusy(true);
    setError(null);
    try {
      const d = await fn();
      onChanged(d);
      setText(d.final_response ?? d.draft_response ?? "");
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card" style={{ flex: "999 1 520px", minWidth: 0, padding: 24, gap: 18 }} aria-label="Dispute detail">
      <div className="between" style={{ flexWrap: "wrap" }}>
        <h2>{kindLabel(dispute.reason)} · {money(dispute.amount, dispute.currency)}</h2>
        <Pill status={dispute.status} />
      </div>

      <div className="stack">
        <h3 className="section-label">Evidence pack · {items.length} records</h3>
        <ul className="evidence">
          {items.map((i) => (
            <li key={i.source_id}>
              <span className="mono tiny chip">{i.source_id.slice(0, 22)}</span>
              <span className="small">{i.summary}</span>
            </li>
          ))}
        </ul>
        {dispute.evidence_pack?.missing.map((m) => <p key={m} className="card dashed small">Not available: {m}</p>)}
      </div>

      <div className="stack" style={{ borderTop: "1px solid var(--line)", paddingTop: 18 }}>
        <div className="between" style={{ flexWrap: "wrap" }}>
          <label htmlFor="resp">Response to PayPal</label>
          {dispute.draft_source && <span className="tiny muted">Draft by {dispute.draft_source}</span>}
        </div>
        <textarea id="resp" rows={12} value={text} readOnly={!editable} onChange={(e) => setText(e.target.value)}
          placeholder="Draft a response from the evidence, then edit it." />
        <span className="tiny muted">{text.length} / 2000 characters · {cited.length} citation{cited.length === 1 ? "" : "s"}
          {unknown.length > 0 && <strong className="warn-text"> · {unknown.length} cite records not in the pack</strong>}</span>
        <ErrorBox error={error} />
        {editable ? (
          <div className="row" style={{ flexWrap: "wrap" }}>
            <button className="btn" disabled={busy} onClick={() => run(() => api.post<DisputeOut>(`/manager/disputes/${dispute.id}/draft`))}>
              {dispute.draft_response ? "Redraft from the evidence" : "Draft from the evidence"}
            </button>
            <button className="btn primary" disabled={busy || text.trim().length < 20 || text.length > 2000 || unknown.length > 0}
              onClick={() => window.confirm("Send this exact text to PayPal as your dispute evidence?") &&
                run(() => api.post<DisputeOut>(`/manager/disputes/${dispute.id}/approve`, { response: text }))}>
              Approve and submit to PayPal
            </button>
          </div>
        ) : (
          <p className="alert info small">
            {dispute.status === "SUBMITTED" ? `Submitted to PayPal ${shortDate(dispute.submitted_at)}.` :
              dispute.status === "APPROVED" ? "Approved; waiting for PayPal (the outbox retries until it’s accepted)." : "Resolved by PayPal."}
          </p>
        )}
      </div>
    </section>
  );
}
