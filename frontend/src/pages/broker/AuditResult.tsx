import { Link, useParams } from "react-router-dom";
import { api } from "../../api";
import { AppBar, CheckReport, ErrorBox, Loading, Pill, useLoad } from "../../components";
import type { AuditOut } from "../../types";

const HEADLINE: Record<string, (n: number) => string> = {
  NEEDS_FIX: (n) => `${n} thing${n === 1 ? "" : "s"} don’t match`,
  IN_REVIEW: () => "The agency is checking this by hand",
  REJECTED: () => "Sent back by the agency",
  PENDING_MINT: () => "Verified · publishing to PayPal",
  VERIFIED: () => "Verified and live",
};

export default function AuditResult() {
  const { auditId = "" } = useParams();
  const { data: audit, error, loading } = useLoad(() => api.get<AuditOut>(`/audits/${auditId}`), [auditId]);

  if (loading && !audit) return <div className="phone"><AppBar title="Audit result" back="/b" /><div className="content"><Loading /></div></div>;
  if (!audit) return <div className="phone"><AppBar title="Audit result" back="/b" /><div className="content"><ErrorBox error={error} /></div></div>;

  const r = audit.report;
  const problems = r.checks.flatMap((c) => c.evidence).filter((e) => e.ok === false).length;
  const listingId = r.listing_id;
  return (
    <div className="phone">
      <AppBar title={`Audit result · version ${r.version_no}`} back="/b" />
      <main className="content">
        <div className="card strong">
          <Pill status={audit.status} />
          <h1 style={{ fontSize: 18 }}>{(HEADLINE[audit.status] ?? (() => audit.status))(problems)}</h1>
          <p className="small muted">
            {audit.status === "NEEDS_FIX" && "Fix them and submit again. This version stays on record as signed evidence."}
            {audit.status === "IN_REVIEW" && "Nothing is wrong, but some things couldn’t be checked automatically. You’ll see the decision here."}
            {audit.status === "REJECTED" && "Fix the ad and submit again."}
            {(audit.status === "VERIFIED" || audit.status === "PENDING_MINT") && "Every check passed."}
          </p>
        </div>

        <CheckReport checks={r.checks} />

        <div className="stack small">
          <Link to={`/v/${audit.id}`}>Check the signature (the tenant’s view)</Link>
          {audit.signature && <span className="tiny muted mono">signature {audit.signature.slice(0, 16)}…</span>}
        </div>
      </main>
      <div className="footer-bar">
        {["NEEDS_FIX", "REJECTED"].includes(audit.status) && (
          <Link className="btn primary" to={`/b/listings/${listingId}/ad`}>Fix and resubmit</Link>
        )}
        {["VERIFIED", "PENDING_MINT"].includes(audit.status) && (
          <Link className="btn primary" to={`/b/listings/${listingId}`}>Open the listing</Link>
        )}
        {audit.status === "IN_REVIEW" && <Link className="btn" to="/b">Back to my listings</Link>}
      </div>
    </div>
  );
}
