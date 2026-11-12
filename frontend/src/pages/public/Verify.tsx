import { useNavigate, useParams } from "react-router-dom";
import { api } from "../../api";
import { ErrorBox, Icon, isGone, useLoad } from "../../components";
import { fieldLabel, plain } from "../../format";
import type { VerifyOut } from "../../types";
import { useTitle } from "../../ui/hooks";
import { SkeletonLines } from "../../ui/Skeleton";

/** "ad rent: signed 35000.00, now 30000.00" → field, value when checked, value now. */
function explain(m: string): { what: string; was?: string; now?: string } {
  const hit = /^(.*?):\s*signed\s+(.*?),\s*now\s+(.*)$/i.exec(m);
  if (!hit) return { what: m };
  const num = (v: string) => (/^-?\d+(\.\d+)?$/.test(v.trim()) ? plain(v) : v);
  const what = hit[1].replace(/^ad\s+/i, "");
  return { what: fieldLabel(what.replace(/\s+/g, "_")), was: num(hit[2]), now: num(hit[3]) };
}

/** S04: anyone can check that the signed report still matches what's live. Two states, readable at a glance. */
export default function Verify() {
  const { auditId = "" } = useParams();
  const navigate = useNavigate();
  const { data: v, error, loading, reload } = useLoad(() => api.get<VerifyOut>(`/public/audits/${auditId}/verify`), [auditId]);
  const genuine = !!v && v.signature_valid && v.matches_current_data;
  useTitle(!v ? "Is this listing genuine?" : genuine ? "Genuine, unchanged" : "Changed after it was checked");

  return (
    <main className="verify">
      <div className="stack" style={{ gap: 6 }}>
        <p className="eyebrow">Check the signature</p>
        <h1>Is this listing genuine?</h1>
        <p className="muted small">When the ad was checked, PropLock sealed its report. Anyone can check that nothing has changed since.</p>
      </div>

      {loading && !v && <SkeletonLines lines={4} label="Checking the seal…" />}
      {isGone(error, 404) ? (
        <div className="empty-state compact">
          <span className="empty-ic" aria-hidden="true"><Icon name="search" size={26} /></span>
          <h2>No audit with this id exists</h2>
          <p className="muted small">Check the link you were sent, or ask the broker for the listing’s own link.</p>
        </div>
      ) : <ErrorBox error={error} />}

      {v && genuine && (
        <section className="verdict ok" aria-live="polite">
          <span className="verdict-ic" aria-hidden="true"><Icon name="shield" size={34} /></span>
          <h2>Genuine, unchanged</h2>
          <dl className="verdict-rows">
            <div><dt>Signature</dt><dd><Icon name="check" size={16} />Valid</dd></div>
            <div><dt>Matches today’s data</dt><dd><Icon name="check" size={16} />Yes</dd></div>
          </dl>
          <p className="tiny">Rent, photos and the owner’s terms are as they were checked.
            {" "}Status {v.status}{v.paypal_product_id ? ` · PayPal product ${v.paypal_product_id}` : ""}</p>
        </section>
      )}

      {v && !genuine && (
        <section className="verdict bad" aria-live="polite">
          <span className="verdict-ic" aria-hidden="true"><Icon name="warn" size={34} /></span>
          <h2>{v.signature_valid ? "Changed after it was checked" : "The report has been tampered with"}</h2>
          <dl className="verdict-rows">
            <div><dt>Signature</dt><dd><Icon name={v.signature_valid ? "check" : "x"} size={16} />{v.signature_valid ? "Valid" : "Doesn’t match the report"}</dd></div>
            <div><dt>Matches today’s data</dt><dd><Icon name={v.matches_current_data ? "check" : "x"} size={16} />{v.matches_current_data ? "Yes" : "No"}</dd></div>
          </dl>
          <p className="verdict-warning">Don’t pay anything. Ask the broker or the agency why the ad changed.</p>
          {v.mismatches.length > 0 && (
            <div className="stack" style={{ gap: 8 }}>
              <h3 className="eyebrow verdict-eyebrow">What changed</h3>
              <ul className="changes">
                {v.mismatches.map((m) => {
                  const c = explain(m);
                  return (
                    <li key={m}>
                      <strong>{c.what}</strong>
                      {c.was !== undefined ? <span>checked <s>{c.was}</s> → now <b>{c.now}</b></span> : null}
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
        </section>
      )}

      <div className="row" style={{ flexWrap: "wrap" }}>
        {v && <button className="btn" onClick={reload}><Icon name="refresh" size={16} />Check again</button>}
        {window.history.length > 1 && <button className="btn ghost" onClick={() => navigate(-1)}><Icon name="back" size={16} />Back</button>}
      </div>
    </main>
  );
}
