import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api";
import { ErrorBox, Icon, Loading, Pill, useLoad } from "../../components";
import { dateTime, kindLabel, money, shortDate } from "../../format";
import type { ClosingDetail, DisputeOut } from "../../types";

/** One closing: the invoice breakdown, PayPal link + QR, and every ledger entry behind the balance. */
export default function ClosingDrawer({ closingId, isManager, demo, onClose, onChanged }: {
  closingId: string;
  isManager: boolean;
  demo: boolean;
  onClose: () => void;
  onChanged: () => void;
}) {
  const detail = useLoad(() => api.get<ClosingDetail>(`/closings/${closingId}`), [closingId]);
  const [qr, setQr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function act(name: string, run: () => Promise<unknown>, done: string) {
    setBusy(name);
    setError(null);
    setNotice(null);
    try {
      const out = await run();
      if (out && typeof out === "object" && "entries" in out) detail.setData(out as ClosingDetail);
      else await detail.reload();
      setNotice(done);
      onChanged();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  }

  const c = detail.data;
  const inv = c?.invoice;
  return (
    <div className="drawer-scrim" onClick={onClose}>
      <aside className="drawer" role="dialog" aria-modal="true" aria-label="Closing detail" onClick={(e) => e.stopPropagation()}>
        <div className="between">
          <h2>{c ? c.tenant_name : "Closing"}</h2>
          <button className="icon-btn plain" aria-label="Close" onClick={onClose}><Icon name="x" /></button>
        </div>
        {detail.loading && !c && <Loading />}
        <ErrorBox error={detail.error} />
        {c && (
          <>
            <div className="row" style={{ flexWrap: "wrap" }}>
              <Pill status={c.status} />
              {inv && <Pill status={inv.status} />}
              {inv?.demo && <span className="pill dashed">DEMO · FAKE-INV</span>}
            </div>
            <p className="small muted">{c.title} · {c.property_id} · broker {c.broker_name} · {c.tenant_email}</p>
            <Link className="small" to={`/v/${c.audit_id}`}>Signed listing audit the amounts come from</Link>

            {inv && (
              <section className="card flat" aria-label="Invoice">
                <div className="between"><strong>Invoice {inv.number}</strong>
                  <span className={`tiny ${inv.signature_valid ? "" : "warn-text"}`}>{inv.signature_valid ? "Signature valid" : "Signature INVALID"}</span>
                </div>
                <table className="sums">
                  <tbody>
                    <tr><td>Security deposit</td><td>{money(inv.deposit, inv.currency)}</td></tr>
                    <tr><td>Brokerage fee</td><td>{money(inv.brokerage, inv.currency)}</td></tr>
                    <tr><td>Visit token already paid <span className="tiny muted mono">{c.capture_ref}</span></td><td>− {money(inv.token_deduction, inv.currency)}</td></tr>
                    <tr className="total"><td>Invoice total</td><td>{money(inv.total, inv.currency)}</td></tr>
                  </tbody>
                </table>
                {inv.override_note && <p className="alert warn small">Manager override: {inv.override_note}</p>}
                <div className="grid2 small">
                  <span className="muted">Due</span><span>{shortDate(inv.due_date)}</span>
                  <span className="muted">Sent</span><span>{dateTime(inv.sent_at)}</span>
                  <span className="muted">Paid</span><span>{dateTime(inv.paid_at)}</span>
                  <span className="muted">Reminders</span><span>{inv.reminders_sent} of 3</span>
                  <span className="muted">PayPal id</span><span className="mono tiny">{inv.paypal_invoice_id ?? "not created yet (queued)"}</span>
                </div>
                <div className="row" style={{ flexWrap: "wrap" }}>
                  {inv.payer_view_url && !inv.demo && (
                    <a className="btn small primary" href={inv.payer_view_url} target="_blank" rel="noreferrer">Tenant’s PayPal pay page</a>
                  )}
                  {inv.paypal_invoice_id && (
                    <button className="btn small" disabled={busy !== null}
                      onClick={() => act("qr", async () => setQr((await api.get<{ data_url: string }>(`/closings/${c.id}/qr`)).data_url), "QR code loaded.")}>
                      Show QR code
                    </button>
                  )}
                  <button className="btn small" disabled={busy !== null}
                    onClick={() => act("refresh", () => api.post<ClosingDetail>(`/closings/${c.id}/refresh`), "Checked with PayPal.")}>
                    <Icon name="refresh" size={16} /> Check PayPal now
                  </button>
                </div>
                {qr && <img src={qr} alt={`QR code to pay invoice ${inv.number}`} width={180} height={180} style={{ borderRadius: 12, background: "#fff" }} />}
                {qr && inv.demo && <p className="tiny muted">Demo invoice: this placeholder is not a real pay link.</p>}
              </section>
            )}

            <section className="stack" aria-label="Balance">
              <div className="kpis">
                <div className="stat"><span className="n">{money(c.gross, c.currency)}</span><span className="l">Deposit + brokerage</span></div>
                <div className="stat tint"><span className="n">{money(c.received, c.currency)}</span><span className="l">Received (from the ledger)</span></div>
                <div className={`stat${Number(c.outstanding) > 0 ? " warn" : ""}`}><span className="n">{money(c.outstanding, c.currency)}</span><span className="l">Outstanding</span></div>
              </div>
            </section>

            <section className="stack" aria-label="Ledger entries">
              <h3 className="section-label">Ledger · {c.entries.length} entries</h3>
              <ol className="timeline">
                {c.entries.map((e) => (
                  <li key={e.id} className={`dir-${e.direction}`}>
                    <div className="between small">
                      <strong>{kindLabel(e.kind)}</strong>
                      <span className={e.direction === "info" ? "muted" : ""}>
                        {e.direction === "out" ? "− " : e.direction === "in" ? "+ " : ""}{money(e.amount, e.currency)}
                      </span>
                    </div>
                    <span className="tiny muted">{dateTime(e.occurred_at)} · {e.source}{e.paypal_ref ? ` · ${e.paypal_ref}` : ""}</span>
                    {e.note && <span className="tiny">{e.note}</span>}
                  </li>
                ))}
              </ol>
            </section>

            {notice && <p className="alert info small" role="status">{notice}</p>}
            <ErrorBox error={error} />
            {isManager && inv && ["PENDING", "DRAFT", "SENT"].includes(inv.status) && (
              <button className="btn" disabled={busy !== null}
                onClick={() => window.confirm(`Cancel invoice ${inv.number}? PayPal tells the tenant.`) &&
                  act("cancel", () => api.post<ClosingDetail>(`/closings/${c.id}/cancel`), "Invoice cancelled.")}>
                Cancel invoice
              </button>
            )}
            {isManager && demo && inv && inv.paypal_invoice_id && !c.dispute_status && (
              <button className="btn ghost small" disabled={busy !== null}
                onClick={() => act("dispute", () => api.post<DisputeOut>(`/dev/disputes/simulate?closing_id=${c.id}`), "Demo dispute opened: see Disputes.")}>
                Simulate a PayPal dispute (demo)
              </button>
            )}
            {c.dispute_status && isManager && <Link className="btn small" to="/m/disputes">Open the dispute ({c.dispute_status})</Link>}
          </>
        )}
      </aside>
    </div>
  );
}
