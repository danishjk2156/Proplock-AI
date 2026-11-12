import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, apiUrl } from "../../api";
import { useAuth } from "../../auth";
import { ErrorBox, Icon, Loading, Pill, Ring, ThemeToggle, useLoad } from "../../components";
import { daysUntil, plain, rupees, shortDate } from "../../format";
import type { BrokerListing, BrokerMandate, ListingOutLite } from "./shared";

function listingHint(l: BrokerListing): string {
  switch (l.status) {
    case "DRAFT":
      return l.has_checkin ? "Ready to write the ad" : "Needs a check-in photo";
    case "NEEDS_FIX":
      return `${l.problems} thing${l.problems === 1 ? "" : "s"} to fix · see suggestions`;
    case "IN_REVIEW":
      return `The agency is checking ${l.open_questions} thing${l.open_questions === 1 ? "" : "s"} by hand`;
    case "VERIFIED":
      return "Verified · publishing to PayPal";
    case "LIVE": {
      const days = daysUntil(l.expires_at);
      return days !== null && days <= 0 ? "Confirm it’s still available today" : `Confirm it’s still available in ${days} days`;
    }
    case "EXPIRED":
      return "Expired: confirm it’s available to bring it back";
    case "RENTED":
      return "Rented";
    default:
      return l.status;
  }
}

function greeting(): string {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}

export default function BrokerHome() {
  const { me, signOut, refresh } = useAuth();
  const navigate = useNavigate();
  const mandates = useLoad(() => api.get<BrokerMandate[]>("/broker/mandates"));
  const listings = useLoad(async () => {
    const rows = await api.get<BrokerListing[]>("/broker/listings");
    refresh(); // the trust score may have changed since sign-in
    return rows;
  });
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function start(m: BrokerMandate) {
    setError(null);
    const open = listings.data?.find((l) => l.id === m.listing_id && ["DRAFT", "NEEDS_FIX"].includes(l.status));
    if (open) return navigate(`/b/listings/${open.id}`);
    setBusy(m.mandate_id);
    try {
      const created = await api.post<ListingOutLite>("/broker/listings", { property_id: m.property_id });
      navigate(`/b/listings/${created.id}/checkin`);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  }

  const rows = listings.data ?? [];
  const count = (...s: string[]) => rows.filter((l) => s.includes(l.status)).length;

  return (
    <div className="phone">
      <header className="hello">
        <span className="avatar" aria-hidden="true">{me?.name.slice(0, 1)}</span>
        <div className="who">
          <p>{greeting()}</p>
          <h1>{me?.name}</h1>
        </div>
        <Ring value={me?.trust_score ?? 0} max={120} size={52} label={`Trust score ${me?.trust_score}`} />
        <nav className="hello-actions" aria-label="Broker menu">
          <Link className="btn small" to="/b/visits"><Icon name="calendar" size={16} />Visits</Link>
          <Link className="btn small" to="/b/ledger"><Icon name="ledger" size={16} />Ledger</Link>
          <ThemeToggle />
          <button className="icon-btn" aria-label="Sign out" title="Sign out" onClick={signOut}><Icon name="out" size={18} /></button>
        </nav>
      </header>

      <main className="content">
        <section className="stats" aria-label="Your listings at a glance">
          <div className="stat tint"><span className="n">{count("LIVE", "VERIFIED")}</span><span className="l">Live</span></div>
          <div className="stat warn"><span className="n">{count("NEEDS_FIX")}</span><span className="l">Need a fix</span></div>
          <div className="stat"><span className="n">{count("IN_REVIEW")}</span><span className="l">In review</span></div>
        </section>

        <ErrorBox error={error} />

        <section className="stack">
          <div className="between">
            <h2 className="section-label">Flats you can list</h2>
            {mandates.data && <span className="small muted">{mandates.data.length} mandate{mandates.data.length === 1 ? "" : "s"}</span>}
          </div>
          {mandates.loading && !mandates.data && <Loading />}
          <ErrorBox error={mandates.error} />
          {mandates.data?.length === 0 && <p className="muted small">No active mandates. Ask the agency desk to give you one.</p>}
          {mandates.data?.map((m) => (
            <article key={m.mandate_id} className="card photo-card">
              <div className="media">
                {m.photo_url ? <img src={apiUrl(m.photo_url)} alt={`Owner’s photo of ${m.address}`} /> : <Icon name="house" size={44} />}
                <div className="over tl"><span className="glass">{m.furnishing}</span></div>
                <div className="over br"><span className="glass">{m.city}</span></div>
              </div>
              <div className="body">
                <div className="stack" style={{ gap: 2 }}>
                  <h3>{m.address}</h3>
                  <span className="small muted">{m.property_id} · mandate until {shortDate(m.valid_until)}</span>
                </div>
                <div className="between">
                  <span className="price">{rupees(m.min_rent)}–{plain(m.max_rent)} <small>/ month</small></span>
                </div>
                <div className="chips">
                  <span className="chip"><Icon name="wallet" size={15} />Deposit ≤ {rupees(m.max_deposit)}</span>
                  <span className="chip">Brokerage ≤ {Number(m.max_brokerage_months)} mo</span>
                </div>
                <button className="btn primary" onClick={() => start(m)} disabled={busy === m.mandate_id}>
                  {busy === m.mandate_id ? "Opening…" : "Start a listing"}
                </button>
              </div>
            </article>
          ))}
        </section>

        <section className="stack">
          <h2 className="section-label">Your listings</h2>
          {listings.loading && !listings.data && <Loading />}
          <ErrorBox error={listings.error} />
          {rows.length === 0 && listings.data && <p className="muted small">No listings yet.</p>}
          {rows.map((l) => (
            <Link key={l.id} to={`/b/listings/${l.id}`} className={`card${l.status === "DRAFT" ? " dashed" : ""}`}>
              <div className="row" style={{ alignItems: "flex-start" }}>
                <div className="thumb" aria-hidden="true"><Icon name="house" size={24} /></div>
                <div className="stack" style={{ gap: 4, flex: 1, minWidth: 0 }}>
                  <div className="between" style={{ alignItems: "flex-start" }}>
                    <strong>{l.ad?.title ?? l.address}</strong>
                    <Pill status={l.status} />
                  </div>
                  <span className="small muted">{listingHint(l)}</span>
                  {l.ad && <span className="small"><strong>{rupees(l.ad.rent)}</strong><span className="muted"> / month · v{l.version_no}</span></span>}
                </div>
              </div>
            </Link>
          ))}
        </section>
      </main>
    </div>
  );
}
