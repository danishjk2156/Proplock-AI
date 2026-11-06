import { useNavigate, useParams } from "react-router-dom";
import { api } from "../../api";
import { AppBar, ErrorBox, Icon, isGone, Loading, useLoad } from "../../components";
import type { VerifyOut } from "../../types";

export default function Verify() {
  const { auditId = "" } = useParams();
  const navigate = useNavigate();
  const { data: v, error, loading, reload } = useLoad(() => api.get<VerifyOut>(`/public/audits/${auditId}/verify`), [auditId]);

  const genuine = v?.signature_valid && v.matches_current_data;
  return (
    <div className="phone">
      <AppBar title="Is this listing genuine?" right={<button className="btn small ghost" onClick={() => navigate(-1)}>Back</button>} />
      <main className="content">
        <p className="muted small">When the ad was checked, PropLock sealed its report. Anyone can check that nothing has changed since.</p>
        {loading && !v && <Loading />}
        {isGone(error, 404) ? <p className="alert">No audit with this id exists.</p> : <ErrorBox error={error} />}

        {v && genuine && (
          <section className="card strong" aria-live="polite">
            <div className="row"><Icon name="shield" size={28} /><h1 style={{ fontSize: 18 }}>Genuine, unchanged</h1></div>
            <div className="row small"><Icon name="check" size={18} /><span>The seal matches the checked report</span></div>
            <div className="row small"><Icon name="check" size={18} /><span>Rent, photos and owner’s terms are as checked</span></div>
            <span className="tiny muted">Status {v.status}{v.paypal_product_id ? ` · PayPal product ${v.paypal_product_id}` : ""}</span>
          </section>
        )}

        {v && !genuine && (
          <section className="card strong" style={{ borderWidth: 3, padding: 0, overflow: "hidden" }} aria-live="polite">
            <div className="card-head dark" style={{ margin: 0, borderRadius: 0 }}>
              <div className="row"><Icon name="warn" /><strong>{v.signature_valid ? "Changed after it was checked" : "The report has been tampered with"}</strong></div>
            </div>
            <div className="stack" style={{ padding: 14 }}>
              <div className="row small"><Icon name={v.signature_valid ? "check" : "x"} size={18} /><span>{v.signature_valid ? "The seal matches the checked report" : "The seal does not match the report"}</span></div>
              {v.signature_valid && <div className="row small"><Icon name="x" size={18} /><span>The live data no longer matches</span></div>}
              {v.mismatches.map((m) => <div key={m} className="alert info mono small">{m}</div>)}
              <p className="small muted">Don’t pay anything. Ask the broker or the agency why the ad changed.</p>
            </div>
          </section>
        )}

        {v && <button className="btn" onClick={reload}>Check again</button>}
      </main>
    </div>
  );
}
