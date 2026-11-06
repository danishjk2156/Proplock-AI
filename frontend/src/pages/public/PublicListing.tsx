import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, apiUrl } from "../../api";
import { ErrorBox, Icon, isGone, Loading, useLoad } from "../../components";
import { daysAgo, MODE_WORDS, rupees } from "../../format";
import type { PublicListing as Listing } from "../../types";

export default function PublicListing() {
  const { listingId = "" } = useParams();
  const { data: l, error, loading } = useLoad(() => api.get<Listing>(`/public/listings/${listingId}`), [listingId]);
  const [photo, setPhoto] = useState(0);

  if (loading && !l) return <div className="phone"><div className="content"><Loading /></div></div>;
  if (!l) {
    return (
      <div className="phone">
        <main className="content">
          <h1>{isGone(error, 410) ? "This listing is no longer available" : "This listing isn’t verified"}</h1>
          <p className="muted">
            {isGone(error, 410)
              ? "It was rented, or the broker stopped confirming it’s available."
              : "PropLock only shows listings that passed every check. Ask the broker for a verified link."}
          </p>
          {!isGone(error, 404, 410) && <ErrorBox error={error} />}
        </main>
      </div>
    );
  }

  const deposit_months = Number(l.deposit) / Number(l.rent);
  return (
    <div className="phone">
      <div className="appbar" style={{ padding: "10px 16px" }}>
        <Icon name="shield" size={24} />
        <div className="title">
          <strong style={{ fontSize: 14 }}>Verified by PropLock</strong>
          <span>Checked against the owner’s terms, maps and the law</span>
        </div>
        <Link className="small" style={{ fontWeight: 600 }} to={`/v/${l.audit_id}`}>Check it</Link>
      </div>

      {l.photos.length > 0 ? (
        <div style={{ position: "relative" }}>
          <img className="hero" src={apiUrl(l.photos[photo].url)} alt={`Owner’s photo: ${l.photos[photo].room_type}`} />
          <span className="pill grey" style={{ position: "absolute", left: 12, bottom: 12 }}>
            Owner’s photos · {photo + 1} of {l.photos.length}
          </span>
          {l.photos.length > 1 && (
            <button className="btn small" style={{ position: "absolute", right: 12, bottom: 8 }}
              onClick={() => setPhoto((photo + 1) % l.photos.length)}>Next photo</button>
          )}
        </div>
      ) : (
        <div className="hero">The agency hasn’t added photos yet</div>
      )}

      <main className="content">
        <div className="stack" style={{ gap: 4 }}>
          <h1>{l.title}</h1>
          <span className="muted">{l.city} · {l.furnishing}</span>
        </div>
        <p>{l.description}</p>

        <section aria-label="Move-in cost" className="card">
          <strong>What you pay to move in</strong>
          <div className="between"><span>Rent (first month)</span><span>{rupees(l.rent)}</span></div>
          <div className="between"><span>Security deposit ({Number(deposit_months.toFixed(1))} months)</span><span>{rupees(l.deposit)}</span></div>
          <div className="between"><span>Brokerage (one-time)</span><span>{rupees(l.brokerage_fee)}</span></div>
          <div className="between" style={{ borderTop: "1px solid var(--line)", paddingTop: 8, fontWeight: 700, fontSize: 16 }}>
            <span>Total</span><span>{rupees(l.move_in_total)}</span>
          </div>
          <span className="tiny muted">Within the owner’s agreed limits. No other charges.</span>
        </section>

        {l.advisories.length > 0 && (
          <section className="alert info">
            {l.advisories.map((a) => <p key={a} className="small">{a}</p>)}
          </section>
        )}

        {l.commute.length > 0 && (
          <section aria-label="Commute" className="stack">
            <strong>Commute, checked at weekday 8:30 AM</strong>
            {l.commute.map((c) => (
              <div key={`${c.destination}-${c.mode}`} className="row small">
                <Icon name={c.checked_at_rush_hour ? "check" : "warn"} size={20} />
                <span>{c.minutes} min {MODE_WORDS[c.mode]} to {c.destination}{c.checked_at_rush_hour ? "" : " (checked by the agency)"}</span>
              </div>
            ))}
          </section>
        )}

        <section aria-label="Broker" className="card row">
          <div className="thumb" style={{ width: 48, height: 48, borderRadius: "50%" }} aria-hidden="true" />
          <div className="stack" style={{ gap: 2, flex: 1, minWidth: 0 }}>
            <strong>{l.broker.name}</strong>
            <span className="small muted">{[l.broker.agency, l.broker.licence_number].filter(Boolean).join(" · ")}</span>
          </div>
          <span className="chip">Trust {l.broker.trust_score}</span>
        </section>

        <p className="small muted">
          Broker confirmed it’s available {daysAgo(l.last_confirmed_at)} · version {l.version_no} of this ad
          {l.paypal_product_id && ` · PayPal product ${l.paypal_product_id}${l.demo_product ? " (demo)" : ""}`}
        </p>

        <div className="stack" style={{ gap: 6 }}>
          <button className="btn" disabled>Book a visit with a ₹500 refundable hold</button>
          <span className="tiny muted" style={{ textAlign: "center" }}>Coming next: PayPal holds the ₹500 and releases it if you visit and pass.</span>
        </div>
      </main>
    </div>
  );
}
