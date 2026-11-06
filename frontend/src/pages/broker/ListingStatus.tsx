import { useState } from "react";
import { Link, Navigate, useParams } from "react-router-dom";
import { api } from "../../api";
import { AppBar, ErrorBox, Icon, Loading, Pill, Ring, useLoad } from "../../components";
import { daysUntil, rupees } from "../../format";
import type { BrokerListing } from "../../types";

const CONFIRM_DAYS = 7;

/** /b/listings/:id: sends drafts and failed audits to the right step; shows live listings. */
export default function ListingStatusPage() {
  const { listingId = "" } = useParams();
  const { data: listing, error, loading, reload } = useLoad(
    () => api.get<BrokerListing>(`/broker/listings/${listingId}`),
    [listingId],
  );
  const [actionError, setActionError] = useState<unknown>(null);
  const [copied, setCopied] = useState(false);

  if (loading && !listing) return <div className="phone"><AppBar title="Your listing" back="/b" /><div className="content"><Loading /></div></div>;
  if (error || !listing) return <div className="phone"><AppBar title="Your listing" back="/b" /><div className="content"><ErrorBox error={error} /></div></div>;

  if (listing.status === "DRAFT") {
    return <Navigate to={`/b/listings/${listing.id}/${listing.has_checkin ? "ad" : "checkin"}`} replace />;
  }
  if ((listing.status === "NEEDS_FIX" || listing.status === "IN_REVIEW") && listing.audit_id) {
    return <Navigate to={`/b/audits/${listing.audit_id}`} replace />;
  }

  const shareUrl = `${window.location.origin}/l/${listing.id}`;
  const days = daysUntil(listing.expires_at);
  const elapsed = days === null ? 0 : Math.min(CONFIRM_DAYS, Math.max(0, CONFIRM_DAYS - days));

  async function act(path: string) {
    setActionError(null);
    try {
      await api.post(`/broker/listings/${listing!.id}/${path}`);
      await reload();
    } catch (e) {
      setActionError(e);
    }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(shareUrl);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  const live = listing.status === "LIVE";
  return (
    <div className="phone">
      <AppBar title="Your listing" back="/b" />
      <main className="content">
        <ErrorBox error={actionError} />
        <div className="card">
          <div className="row">
            <Pill status={listing.status} />
            <span className="small muted">Version {listing.version_no} · signed</span>
          </div>
          <h1 style={{ fontSize: 20 }}>{listing.ad?.title}</h1>
          {listing.ad && (
            <>
              <span className="price">{rupees(listing.ad.rent)} <small>/ month</small></span>
              <div className="chips">
                <span className="chip"><Icon name="sofa" size={15} />{listing.ad.furnishing}</span>
                <span className="chip"><Icon name="pin" size={15} />{listing.address}</span>
              </div>
            </>
          )}
          {listing.status === "VERIFIED" && (
            <p className="small">Publishing to PayPal: it goes live as soon as the product is created.</p>
          )}
          {listing.audit_id && <Link className="small" to={`/b/audits/${listing.audit_id}`}>See the signed audit</Link>}
        </div>

        {live && (
          <section className="card">
            <strong>Share with tenants</strong>
            <div className="row">
              <input aria-label="Listing link" readOnly value={shareUrl} onFocus={(e) => e.currentTarget.select()} />
              <button className="btn" onClick={copy}>{copied ? "Copied" : "Copy"}</button>
            </div>
            <a className="btn primary" href={`https://wa.me/?text=${encodeURIComponent(`${listing.ad?.title ?? "Verified listing"}: ${shareUrl}`)}`} target="_blank" rel="noreferrer">
              Share on WhatsApp
            </a>
            <Link className="small" to={`/l/${listing.id}`}>Open the tenant’s view</Link>
          </section>
        )}

        {(live || listing.status === "EXPIRED") && (
          <section className="card">
            <div className="row" style={{ alignItems: "center", gap: 14 }}>
              <Ring value={live ? Math.max(0, CONFIRM_DAYS - elapsed) : 0} max={CONFIRM_DAYS} size={64}
                label={listing.status === "EXPIRED" ? "Expired" : `${Math.max(0, CONFIRM_DAYS - elapsed)} days left`} />
              <div className="stack" style={{ gap: 2, flex: 1 }}>
                <h2>Still available?</h2>
                <span className="small muted">
                  {listing.status === "EXPIRED" ? "Expired: confirm to bring it back" : days !== null && days <= 0 ? "Due today" : `Days left before it expires`}
                </span>
              </div>
            </div>
            <p className="small muted">Confirm every 7 days. If you don’t, the listing expires and you lose 3 trust points.</p>
            <button className="btn primary" onClick={() => act("confirm-availability")}>Yes, still available</button>
          </section>
        )}

        {listing.status === "RENTED" && <p className="alert info">Marked as rented. It no longer shows to tenants.</p>}
      </main>
      {(live || listing.status === "EXPIRED") && (
        <div className="footer-bar">
          <button
            className="btn"
            onClick={() => {
              if (window.confirm("Mark this flat as rented? It will leave the public page.")) act("mark-rented");
            }}
          >
            Mark as rented
          </button>
        </div>
      )}
    </div>
  );
}
