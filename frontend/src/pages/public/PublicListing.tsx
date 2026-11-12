import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, apiUrl } from "../../api";
import { ErrorBox, Icon, isGone, Loading, MODE_ICON, Ring, useLoad } from "../../components";
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
        <main className="content" style={{ paddingTop: 48 }}>
          <span className="avatar" style={{ width: 56, height: 56 }} aria-hidden="true"><Icon name="house" /></span>
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

  const depositMonths = Number((Number(l.deposit) / Number(l.rent)).toFixed(1));
  const current = l.photos[photo];
  return (
    <div className="phone">
      <div className="hero-wrap">
        {current ? (
          <img className="hero" src={apiUrl(current.url)} alt={`Owner’s photo: ${current.room_type}`} />
        ) : (
          <div className="hero"><Icon name="house" size={56} /></div>
        )}
        <div className="over" style={{ top: 14, left: 14 }}>
          <Link to={`/v/${l.audit_id}`} className="glass" style={{ textDecoration: "none", padding: "8px 12px" }}>
            <Icon name="shield" size={16} /> Verified by PropLock
          </Link>
        </div>
        {l.photos.length > 0 && (
          <div className="over" style={{ bottom: 34, left: 14 }}>
            <span className="glass">Owner’s photos · {photo + 1}/{l.photos.length}</span>
          </div>
        )}
        {l.photos.length > 1 && (
          <div className="over" style={{ bottom: 30, right: 14 }}>
            <button className="btn small" onClick={() => setPhoto((photo + 1) % l.photos.length)}>Next photo</button>
          </div>
        )}
      </div>

      <main className="content" style={{ marginTop: -20, background: "var(--bg)", borderRadius: "24px 24px 0 0", paddingTop: 22, position: "relative" }}>
        <div className="stack" style={{ gap: 6 }}>
          <h1>{l.title}</h1>
          <span className="muted row small" style={{ gap: 4 }}><Icon name="pin" size={16} />{l.city}</span>
          <span className="price" style={{ fontSize: 26 }}>{rupees(l.rent)} <small>/ month</small></span>
        </div>

        <div className="chips">
          <span className="chip"><Icon name="sofa" size={15} />{l.furnishing}</span>
          <span className="chip"><Icon name="wallet" size={15} />{depositMonths} months deposit</span>
          <span className="chip"><Icon name="calendar" size={15} />Confirmed {daysAgo(l.last_confirmed_at)}</span>
        </div>

        <p>{l.description}</p>

        {l.commute.length > 0 && (
          <section aria-label="Commute" className="card">
            <div className="between">
              <h2>Commute</h2>
              <span className="tiny muted">checked at weekday 8:30 AM</span>
            </div>
            {l.commute.map((c) => (
              <div key={`${c.destination}-${c.mode}`} className="feature">
                <span className="ic"><Icon name={MODE_ICON[c.mode] ?? "pin"} size={20} /></span>
                <div className="stack" style={{ gap: 0, flex: 1 }}>
                  <strong className="small">{c.destination}</strong>
                  <span className="tiny muted">{c.minutes} min {MODE_WORDS[c.mode]}{c.checked_at_rush_hour ? "" : " · checked by the agency"}</span>
                </div>
                {c.checked_at_rush_hour && <span className="pill thin"><Icon name="check" size={12} />CHECKED</span>}
              </div>
            ))}
          </section>
        )}

        <section aria-label="Move-in cost" className="card">
          <h2>What you pay to move in</h2>
          <div className="between small"><span className="muted">Rent (first month)</span><span>{rupees(l.rent)}</span></div>
          <div className="between small"><span className="muted">Security deposit ({depositMonths} months)</span><span>{rupees(l.deposit)}</span></div>
          <div className="between small"><span className="muted">Brokerage (one-time)</span><span>{rupees(l.brokerage_fee)}</span></div>
          <div className="between" style={{ borderTop: "1px solid var(--line)", paddingTop: 10 }}>
            <strong>Total</strong><span className="price">{rupees(l.move_in_total)}</span>
          </div>
          <span className="tiny muted">Within the owner’s agreed limits. No other charges.</span>
        </section>

        {l.advisories.length > 0 && (
          <section className="alert warn">
            {l.advisories.map((a) => <p key={a} className="small">{a}</p>)}
          </section>
        )}

        <section aria-label="Broker" className="card row">
          <span className="avatar" aria-hidden="true">{l.broker.name.slice(0, 1)}</span>
          <div className="stack" style={{ gap: 2, flex: 1, minWidth: 0 }}>
            <strong>{l.broker.name}</strong>
            <span className="tiny muted">{[l.broker.agency, l.broker.licence_number].filter(Boolean).join(" · ")}</span>
          </div>
          <Ring value={l.broker.trust_score} max={120} size={48} label={`Trust score ${l.broker.trust_score}`} />
        </section>

        <p className="tiny muted">
          Version {l.version_no} of this ad{l.paypal_product_id && ` · PayPal product ${l.paypal_product_id}${l.demo_product ? " (demo)" : ""}`} ·{" "}
          <Link to={`/v/${l.audit_id}`}>check it yourself</Link>
        </p>
      </main>

      <div className="footer-bar" style={{ gridAutoFlow: "row" }}>
        <Link className="btn primary" to={`/l/${l.listing_id}/visit`}><Icon name="calendar" size={18} />Book a visit</Link>
        <span className="tiny muted" style={{ textAlign: "center" }}>Ask about the flat, then pick a time. PayPal holds a small visit fee; you aren’t charged to book.</span>
      </div>
    </div>
  );
}
