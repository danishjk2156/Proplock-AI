import { useEffect, useMemo, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { api } from "../../api";
import { ErrorBox, Icon, Loading, Pill, useLoad } from "../../components";
import { dateTime, kindLabel, money } from "../../format";
import type { ClosableListing, ClosingDetail, CopilotOut, LedgerOut } from "../../types";
import ClosingDrawer from "./ClosingDrawer";
import { ClosingsGrid, EntriesGrid } from "./LedgerGrid";

export interface LedgerConfig {
  demo_mode: boolean;
  currency: string;
}

function useNarrow(width = 760): boolean {
  const query = `(max-width: ${width}px)`;
  const [narrow, setNarrow] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const on = () => setNarrow(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [query]);
  return narrow;
}

const EXAMPLES = ["Which deposits are unpaid after 3 days?", "Biggest outstanding balances first", "Which closings are disputed?"];

/** The ledger cockpit, shared by the broker (own closings) and the manager (whole agency). */
export default function Cockpit({ isManager }: { isManager: boolean }) {
  const ledger = useLoad(() => api.get<LedgerOut>("/ledger"));
  const config = useLoad(() => api.get<LedgerConfig>("/ledger/config"));
  const narrow = useNarrow();
  const [tab, setTab] = useState<"closings" | "entries">("closings");
  const [quick, setQuick] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const location = useLocation();
  const handed = location.state as { copilot?: CopilotOut; create?: string } | null;
  // An answer asked in the nav search arrives with the navigation and filters the grid like a local one.
  const [copilot, setCopilot] = useState<CopilotOut | null>(handed?.copilot ?? null);
  useEffect(() => {
    if (handed?.copilot) setCopilot(handed.copilot);
  }, [handed]);

  const data = ledger.data;
  const titles = useMemo(() => new Map((data?.closings ?? []).map((c) => [c.id, `${c.tenant_name} · ${c.property_id}`])), [data]);
  // Copilot results filter the table they belong to (closing ids or entry ids), nothing else.
  const highlight = useMemo(() => {
    if (!copilot || copilot.refused || copilot.target !== tab) return null;
    return new Set(copilot.rows.map((r) => String(r.id)));
  }, [copilot, tab]);

  useEffect(() => {
    if (copilot?.target && !copilot.refused) setTab(copilot.target);
  }, [copilot]);

  const totals = useMemo(() => {
    const rows = data?.closings ?? [];
    const sum = (f: (c: (typeof rows)[number]) => string) => rows.reduce((s, c) => s + Number(f(c)), 0);
    return {
      count: rows.length,
      outstanding: sum((c) => c.outstanding),
      received: sum((c) => c.received),
      unpaid: rows.filter((c) => c.invoice && ["PENDING", "DRAFT", "SENT"].includes(c.invoice.status)).length,
      disputed: rows.filter((c) => c.status === "DISPUTED").length,
    };
  }, [data]);
  const currency = config.data?.currency ?? "USD";

  return (
    <div className="stack" style={{ gap: 20 }}>
      <div className="between" style={{ flexWrap: "wrap", alignItems: "flex-end" }}>
        <div className="stack" style={{ gap: 4 }}>
          <h1 style={{ fontSize: 26 }}>Money ledger</h1>
          <p className="muted small">Every token, invoice, payment and dispute, in one append-only ledger. Balances are added up from the entries below.</p>
        </div>
        <button className="btn small" onClick={() => ledger.reload()}><Icon name="refresh" size={16} /> Reload</button>
      </div>

      {config.data && (
        <p className="tiny muted">
          Sandbox invoices are in {currency} (PayPal has no domestic INR between Indian accounts); the figures are the ₹ amounts verified in the listing.
          {config.data.demo_mode && " DEMO MODE: visit tokens are simulated and ids start with FAKE-."}
        </p>
      )}

      <div className="kpis">
        <div className="stat"><span className="n">{totals.count}</span><span className="l">Closings</span></div>
        <div className="stat tint"><span className="n">{money(totals.received, currency)}</span><span className="l">Received</span></div>
        <div className={`stat${totals.outstanding > 0 ? " warn" : ""}`}><span className="n">{money(totals.outstanding, currency)}</span><span className="l">Outstanding · {totals.unpaid} unpaid</span></div>
        <div className="stat"><span className="n">{totals.disputed}</span><span className="l">Disputed</span></div>
      </div>

      <CloseFlat startOpen={handed?.create === "close"} isManager={isManager} demo={config.data?.demo_mode ?? false} onClosed={(c) => { ledger.reload(); setOpen(c.id); }} />

      <Copilot onAnswer={setCopilot} answer={copilot} />

      <section className="stack" aria-label="Ledger">
        <div className="between" style={{ flexWrap: "wrap" }}>
          <div className="seg" role="tablist" aria-label="View">
            <button role="tab" aria-selected={tab === "closings"} onClick={() => setTab("closings")}>Closings</button>
            <button role="tab" aria-selected={tab === "entries"} onClick={() => setTab("entries")}>All money events</button>
          </div>
          <div className="row" style={{ flex: "1 1 220px", maxWidth: 360 }}>
            <label htmlFor="quick" className="visually-hidden">Filter</label>
            <input id="quick" placeholder="Filter: tenant, invoice, PayPal id…" value={quick} onChange={(e) => setQuick(e.target.value)} />
          </div>
        </div>
        {highlight && (
          <p className="alert info small">Showing the {highlight.size} row{highlight.size === 1 ? "" : "s"} the copilot found. <button className="linkish" onClick={() => setCopilot(null)}>Show all</button></p>
        )}
        {ledger.loading && !data && <Loading />}
        <ErrorBox error={ledger.error} />
        {data && !narrow && tab === "closings" && (
          <ClosingsGrid rows={data.closings} quickFilter={quick} onOpen={setOpen} showBroker={isManager} highlight={highlight} />
        )}
        {data && !narrow && tab === "entries" && (
          <EntriesGrid rows={data.entries} quickFilter={quick} titles={titles} highlight={highlight} />
        )}
        {data && narrow && <PhoneCards data={data} tab={tab} quick={quick} titles={titles} highlight={highlight} onOpen={setOpen} />}
      </section>

      {open && <ClosingDrawer closingId={open} isManager={isManager} demo={config.data?.demo_mode ?? false}
        onClose={() => setOpen(null)} onChanged={() => ledger.reload()} />}
    </div>
  );
}

/** Below 760 px a grid is unreadable, so the same rows become cards. */
function PhoneCards({ data, tab, quick, titles, highlight, onOpen }: {
  data: LedgerOut; tab: "closings" | "entries"; quick: string; titles: Map<string, string>;
  highlight: Set<string> | null; onOpen: (id: string) => void;
}) {
  const q = quick.trim().toLowerCase();
  const match = (o: object) => !q || JSON.stringify(o).toLowerCase().includes(q);
  if (tab === "closings") {
    const rows = data.closings.filter((c) => match(c) && (!highlight || highlight.has(c.id)));
    if (!rows.length) return <div className="card dashed small muted">No closings yet.</div>;
    return (
      <div className="stack">
        {rows.map((c) => (
          <button key={c.id} className="card" style={{ textAlign: "left", font: "inherit", color: "inherit", cursor: "pointer" }} onClick={() => onOpen(c.id)}>
            <div className="between"><strong>{c.tenant_name}</strong>
              <Pill status={c.invoice && !["DISPUTED", "CANCELLED"].includes(c.status) ? c.invoice.status : c.status} />
            </div>
            <span className="small muted">{c.title} · {c.invoice?.number ?? "no invoice"}</span>
            <div className="between small"><span>Outstanding</span><strong>{money(c.outstanding, c.currency)}</strong></div>
          </button>
        ))}
      </div>
    );
  }
  const rows = data.entries.filter((e) => match(e) && (!highlight || highlight.has(e.id)));
  return (
    <ol className="timeline card">
      {rows.map((e) => (
        <li key={e.id} className={`dir-${e.direction}`}>
          <div className="between small"><strong>{kindLabel(e.kind)}</strong><span>{money(e.amount, e.currency)}</span></div>
          <span className="tiny muted">{dateTime(e.occurred_at)} · {e.closing_id ? titles.get(e.closing_id) : "before closing"}</span>
        </li>
      ))}
    </ol>
  );
}

function Copilot({ answer, onAnswer }: { answer: CopilotOut | null; onAnswer: (a: CopilotOut | null) => void }) {
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function ask(q: string) {
    if (q.trim().length < 3) return;
    setQuestion(q);
    setBusy(true);
    setError(null);
    try {
      onAnswer(await api.post<CopilotOut>("/ledger/copilot", { question: q }));
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card" aria-label="Ask the ledger">
      <div className="row"><Icon name="spark" size={18} /><strong>Ask the ledger</strong><span className="tiny muted">read-only: it can search, never change anything</span></div>
      <form className="row" onSubmit={(e) => { e.preventDefault(); ask(question); }}>
        <label htmlFor="ask" className="visually-hidden">Question</label>
        <input id="ask" placeholder="Which deposits are unpaid after 3 days?" value={question} onChange={(e) => setQuestion(e.target.value)} maxLength={500} />
        <button className="btn primary" disabled={busy || question.trim().length < 3}>{busy ? "Asking…" : "Ask"}</button>
      </form>
      <div className="chips">
        {EXAMPLES.map((q) => <button key={q} className="chip" style={{ cursor: "pointer" }} onClick={() => ask(q)}>{q}</button>)}
      </div>
      <ErrorBox error={error} />
      {answer && (
        <div className={`alert ${answer.refused ? "warn" : "info"} small`} role="status">
          <pre className="answer">{answer.answer}</pre>
          <span className="tiny muted">Search written by {answer.source}; rows found and counted by PropLock’s own code.</span>
        </div>
      )}
    </section>
  );
}

/** Lock a flat for a tenant: pick a LIVE listing and a captured visit token, then invoice. */
function CloseFlat({ isManager, demo, onClosed, startOpen }: { isManager: boolean; demo: boolean; onClosed: (c: ClosingDetail) => void; startOpen?: boolean }) {
  const closable = useLoad(() => api.get<ClosableListing[]>("/ledger/closable"));
  const [listingId, setListingId] = useState("");
  const [captureRef, setCaptureRef] = useState("");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [override, setOverride] = useState(false);
  const [deposit, setDeposit] = useState("");
  const [brokerage, setBrokerage] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [expanded, setExpanded] = useState(!!startOpen);
  useEffect(() => {
    if (startOpen) setExpanded(true);
  }, [startOpen]);

  const listing = closable.data?.find((l) => l.listing_id === listingId) ?? null;
  useEffect(() => {
    if (!listingId && closable.data?.length) setListingId(closable.data[0].listing_id);
  }, [closable.data, listingId]);
  useEffect(() => {
    setCaptureRef(listing?.holds[0]?.capture_ref ?? "");
  }, [listing]);
  // A token from a booked visit already knows the tenant.
  useEffect(() => {
    const hold = listing?.holds.find((h) => h.capture_ref === captureRef);
    if (hold?.tenant_name) setName(hold.tenant_name);
    if (hold?.tenant_email) setEmail(hold.tenant_email);
  }, [listing, captureRef]);

  if (!closable.data?.length && !closable.loading) return null;

  async function close() {
    setBusy(true);
    setError(null);
    try {
      const body: Record<string, string> = { tenant_name: name, tenant_email: email, capture_ref: captureRef };
      if (isManager && override) Object.assign(body, deposit && { deposit }, brokerage && { brokerage }, { override_note: note });
      const out = await api.post<ClosingDetail>(`/${isManager ? "manager" : "broker"}/listings/${listingId}/close`, body);
      setName(""); setEmail(""); setOverride(false); setNote("");
      await closable.reload();
      onClosed(out);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card" aria-label="Lock a flat">
      <button className="between linkish" style={{ width: "100%" }} aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
        <strong>Tenant locked a flat? Send the move-in invoice</strong>
        <span className="small">{closable.data?.length ?? 0} live listing{closable.data?.length === 1 ? "" : "s"} {expanded ? "▴" : "▾"}</span>
      </button>
      {expanded && listing && (
        <div className="stack">
          <div className="field">
            <label htmlFor="cl-listing">Listing</label>
            <select id="cl-listing" value={listingId} onChange={(e) => setListingId(e.target.value)}>
              {closable.data?.map((l) => <option key={l.listing_id} value={l.listing_id}>{l.title} · {l.property_id}{isManager ? ` · ${l.broker_name}` : ""}</option>)}
            </select>
            <span className="hint">Verified: deposit {money(listing.deposit)} + brokerage {money(listing.brokerage)}. The invoice uses exactly these.</span>
          </div>
          <div className="field">
            <label htmlFor="cl-hold">Visit token (captured PayPal hold)</label>
            {listing.holds.length ? (
              <select id="cl-hold" value={captureRef} onChange={(e) => setCaptureRef(e.target.value)}>
                {listing.holds.map((h) => <option key={h.capture_ref} value={h.capture_ref}>{h.capture_ref} · {money(h.amount, h.currency)} · {dateTime(h.occurred_at)}</option>)}
              </select>
            ) : (
              <p className="small muted">
                No captured visit token for this listing yet. The tenant books a visit from the listing page;
                {demo && isManager
                  ? <> once they have visited, capture it from the <Link to="/m/visits">visit calendar</Link> (demo).</>
                  : " Feature 3 captures it when the visit happens."}
              </p>
            )}
          </div>
          <div className="grid2">
            <div className="field"><label htmlFor="cl-name">Tenant name</label><input id="cl-name" value={name} onChange={(e) => setName(e.target.value)} autoComplete="off" /></div>
            <div className="field"><label htmlFor="cl-email">Tenant email</label><input id="cl-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="off" /></div>
          </div>
          {isManager && (
            <label className="row small"><input type="checkbox" checked={override} onChange={(e) => setOverride(e.target.checked)} style={{ width: 18, minHeight: 18 }} /> Invoice different amounts (override, needs a note)</label>
          )}
          {isManager && override && (
            <div className="stack">
              <div className="grid2">
                <div className="field"><label htmlFor="cl-dep">Deposit</label><input id="cl-dep" inputMode="decimal" value={deposit} onChange={(e) => setDeposit(e.target.value)} placeholder={listing.deposit} /></div>
                <div className="field"><label htmlFor="cl-brk">Brokerage</label><input id="cl-brk" inputMode="decimal" value={brokerage} onChange={(e) => setBrokerage(e.target.value)} placeholder={listing.brokerage} /></div>
              </div>
              <div className="field"><label htmlFor="cl-note">Why (recorded in the ledger)</label><textarea id="cl-note" rows={2} value={note} onChange={(e) => setNote(e.target.value)} /></div>
            </div>
          )}
          <ErrorBox error={error} />
          <button className="btn primary" disabled={busy || !captureRef || name.trim().length < 2 || !email.includes("@") || (override && note.trim().length < 3)} onClick={close}>
            Create and send the PayPal invoice
          </button>
          <p className="tiny muted">Total = deposit + brokerage − the visit token. Saved first, then sent through PayPal; a retry never sends twice.</p>
        </div>
      )}
    </section>
  );
}
