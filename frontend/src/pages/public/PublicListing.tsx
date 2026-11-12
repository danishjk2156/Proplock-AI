import { Link, useParams } from "react-router-dom";
import { api, apiUrl } from "../../api";
import { ErrorBox, Icon, isGone, MODE_ICON, Ring, useLoad } from "../../components";
import { daysAgo, MODE_WORDS, rupees } from "../../format";
import type { PublicListing as Listing } from "../../types";
import { Carousel } from "../../ui/Carousel";
import { roomLabel, useShare, useTitle } from "../../ui/hooks";
import { SkeletonLines } from "../../ui/Skeleton";
import { EXPLORE_ENABLED } from "../../ui/shell/Shell";
import { MoreVerifiedFlats } from "./Explore";

/** S03: the listing close-up. Image left / details right on desktop; full-bleed photo and a sheet on phones. */
export default function PublicListing() {
  const { listingId = "" } = useParams();
  const { data: l, error, loading } = useLoad(() => api.get<Listing>(`/public/listings/${listingId}`), [listingId]);
  const { share, status } = useShare();
  useTitle(l ? l.title : isGone(error, 410) ? "Listing no longer available" : error ? "Listing not verified" : "Verified listing");

  if (loading && !l) {
    return (
      <div className="closeup">
        <div className="closeup-media"><div className="carousel"><div className="carousel-frame skel" /></div></div>
        <div className="closeup-detail"><SkeletonLines lines={5} label="Loading the listing…" /></div>
      </div>
    );
  }
  if (!l) {
    const gone = isGone(error, 410);
    return (
      <main className="empty-state">
        <span className="empty-ic" aria-hidden="true"><Icon name="house" size={28} /></span>
        <h1>{gone ? "This listing is no longer available" : "This listing isn’t verified"}</h1>
        <p className="muted">
          {gone
            ? "It was rented, or the broker stopped confirming it’s available."
            : "PropLock only shows listings that passed every check. Ask the broker for a verified link."}
        </p>
        {!isGone(error, 404, 410) && <ErrorBox error={error} />}
      </main>
    );
  }

  const depositMonths = Number((Number(l.deposit) / Number(l.rent)).toFixed(1));
  const slides = l.photos.map((p, k) => ({
    src: apiUrl(p.url),
    caption: roomLabel(p.room_type),
    alt: `${roomLabel(p.room_type)}, owner’s photo ${k + 1} of ${l.photos.length}`,
  }));
  const pageUrl = `${window.location.origin}/l/${l.listing_id}`;

  return (
    <>
      <div className="closeup">
        <div className="closeup-media">
          <Carousel slides={slides} label="Owner’s photos" />
          <p className="tiny muted closeup-media-note">Photos uploaded by the owner’s agency, not the broker.</p>
        </div>

        <main className="closeup-detail">
          <div className="between">
            <Link to={`/v/${l.audit_id}`} className="pill solid verified-link"><Icon name="check" size={12} />Verified by PropLock</Link>
            <button className="btn small" onClick={() => share(pageUrl, l.title)}><Icon name="share" size={16} />Share</button>
          </div>
          {status && <p className="tiny" role="status">{status}</p>}

          <div className="stack" style={{ gap: 6 }}>
            <h1 className="closeup-title">{l.title}</h1>
            <p className="price-lg">{rupees(l.rent)} <small>/ month</small></p>
          </div>

          <div className="chips">
            <span className="chip"><Icon name="pin" size={15} />{l.city}</span>
            <span className="chip"><Icon name="sofa" size={15} />{l.furnishing}</span>
            <span className="chip"><Icon name="calendar" size={15} />Confirmed {daysAgo(l.last_confirmed_at)}</span>
          </div>

          <div className="closeup-cta">
            <Link className="btn primary" to={`/l/${l.listing_id}/visit`}><Icon name="calendar" size={18} />Book a visit</Link>
            <span className="tiny muted">Ask about the flat, then pick a time. PayPal holds a small visit fee; you aren’t charged to book.</span>
          </div>

          {l.description && <p className="closeup-desc">{l.description}</p>}

          {l.commute.length > 0 && (
            <section aria-labelledby="commute-h" className="stack" style={{ gap: 10 }}>
              <div className="between">
                <h2 id="commute-h" className="section-label">Commute</h2>
                <span className="eyebrow">Weekday 8:30 AM</span>
              </div>
              {l.commute.map((c) => (
                <div key={`${c.destination}-${c.mode}`} className="feature">
                  <span className="ic"><Icon name={MODE_ICON[c.mode] ?? "pin"} size={20} /></span>
                  <div className="stack" style={{ gap: 0, flex: 1, minWidth: 0 }}>
                    <strong className="small">{c.minutes} min {MODE_WORDS[c.mode]} to {c.destination}</strong>
                    <span className="tiny muted">{c.checked_at_rush_hour ? "Checked against rush-hour routes" : "No rush-hour route data: the agency reviewed it by hand"}</span>
                  </div>
                  {c.checked_at_rush_hour
                    ? <span className="pill thin"><Icon name="check" size={12} />Checked</span>
                    : <span className="pill dashed">Not checked</span>}
                </div>
              ))}
            </section>
          )}

          <section aria-labelledby="movein-h" className="card movein">
            <h2 id="movein-h" className="section-label">What you pay to move in</h2>
            <dl className="money-rows">
              <div><dt>Rent (first month)</dt><dd>{rupees(l.rent)}</dd></div>
              <div><dt>Security deposit ({depositMonths} months)</dt><dd>{rupees(l.deposit)}</dd></div>
              <div><dt>Brokerage (one-time)</dt><dd>{rupees(l.brokerage_fee)}</dd></div>
              <div className="total"><dt>Total</dt><dd>{rupees(l.move_in_total)}</dd></div>
            </dl>
            <span className="tiny muted">Within the owner’s agreed limits. No other charges.</span>
          </section>

          {l.advisories.length > 0 && (
            <section className="alert warn" aria-label="Things to know">
              {l.advisories.map((a) => <p key={a} className="small">{a}</p>)}
            </section>
          )}

          <section aria-label="Broker" className="card row broker-card">
            <span className="avatar" aria-hidden="true">{l.broker.name.slice(0, 1)}</span>
            <div className="stack" style={{ gap: 2, flex: 1, minWidth: 0 }}>
              <strong>{l.broker.name}</strong>
              <span className="tiny muted">{[l.broker.agency, l.broker.licence_number].filter(Boolean).join(" · ")}</span>
              <span className="tiny muted">Listing confirmed {daysAgo(l.last_confirmed_at)}</span>
            </div>
            <div className="stack" style={{ gap: 2, alignItems: "center" }}>
              <Ring value={l.broker.trust_score} max={120} size={48} label={`Trust score ${l.broker.trust_score}`} />
              <span className="eyebrow">Trust</span>
            </div>
          </section>

          <Link to={`/v/${l.audit_id}`} className="card verify-link">
            <Icon name="shield" size={22} />
            <span className="stack" style={{ gap: 0, flex: 1 }}>
              <strong>Is this genuine?</strong>
              <span className="tiny muted">Check PropLock’s signature on this ad yourself</span>
            </span>
            <Icon name="arrow" size={18} />
          </Link>

          <p className="tiny muted">
            Version {l.version_no} of this ad
            {l.paypal_product_id && ` · PayPal product ${l.paypal_product_id}${l.demo_product ? " (demo)" : ""}`}
          </p>
        </main>
      </div>

      {EXPLORE_ENABLED && <MoreVerifiedFlats city={l.city} exclude={l.listing_id} />}

      {/* Phones: the primary action stays in reach. */}
      <div className="footer-bar closeup-footer">
        <Link className="btn primary" to={`/l/${l.listing_id}/visit`}><Icon name="calendar" size={18} />Book a visit</Link>
      </div>
    </>
  );
}
