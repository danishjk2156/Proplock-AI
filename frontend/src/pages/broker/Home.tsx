import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../../api";
import { useAuth } from "../../auth";
import { AppBar, ErrorBox, Icon, Loading, Pill, useLoad } from "../../components";
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

  return (
    <div className="phone">
      <AppBar
        title="PropLock"
        subtitle={me?.agency ?? undefined}
        right={
          <div className="row" style={{ gap: 6 }}>
            <span className="chip" title="Trust score">Trust {me?.trust_score}</span>
            <button className="icon-btn" aria-label="Sign out" onClick={signOut}><Icon name="out" /></button>
          </div>
        }
      />
      <main className="content">
        <ErrorBox error={error} />
        <section className="stack">
          <h2 className="section-label">Flats you can list</h2>
          {mandates.loading && <Loading />}
          <ErrorBox error={mandates.error} />
          {mandates.data?.length === 0 && <p className="muted small">No active mandates. Ask the agency desk to give you one.</p>}
          {mandates.data?.map((m) => (
            <div key={m.mandate_id} className="card">
              <div className="row" style={{ alignItems: "flex-start" }}>
                <div className="thumb">Photo</div>
                <div className="stack" style={{ gap: 2, minWidth: 0 }}>
                  <strong>{m.address}</strong>
                  <span className="small muted">{m.property_id} · {m.city} · {m.furnishing}</span>
                </div>
              </div>
              <div className="limits">
                Owner’s limits: rent {rupees(m.min_rent)}–{plain(m.max_rent)} · deposit up to {rupees(m.max_deposit)} ·
                brokerage up to {Number(m.max_brokerage_months)} month · mandate until {shortDate(m.valid_until)}
              </div>
              <button className="btn primary" onClick={() => start(m)} disabled={busy === m.mandate_id}>
                {busy === m.mandate_id ? "Opening…" : "Start a listing"}
              </button>
            </div>
          ))}
        </section>

        <section className="stack">
          <h2 className="section-label">Your listings</h2>
          {listings.loading && <Loading />}
          <ErrorBox error={listings.error} />
          {listings.data?.length === 0 && <p className="muted small">No listings yet.</p>}
          {listings.data?.map((l) => (
            <Link key={l.id} to={`/b/listings/${l.id}`} className={`card${l.status === "DRAFT" ? " dashed" : ""}`}>
              <div className="between">
                <strong>{l.ad?.title ?? l.address}</strong>
                <Pill status={l.status} />
              </div>
              <span className="small muted">{listingHint(l)}</span>
            </Link>
          ))}
        </section>
      </main>
    </div>
  );
}
