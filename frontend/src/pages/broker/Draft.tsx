import { useEffect, useState, type FormEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../../api";
import { AppBar, ErrorBox, Loading, Pill, useLoad } from "../../components";
import { CHECK_LABELS, CHECK_ORDER, plain, readable } from "../../format";
import type { CommuteClaim, CommuteMode, Furnishing, PrecheckOut, SubmitOut } from "../../types";
import { loadListingWithMandate } from "./shared";

interface Form {
  title: string;
  description: string;
  rent: string;
  deposit: string;
  brokerage_fee: string;
  furnishing: Furnishing;
  commute_claims: CommuteClaim[];
}

const EMPTY: Form = {
  title: "", description: "", rent: "", deposit: "", brokerage_fee: "0", furnishing: "Unfurnished", commute_claims: [],
};
const MODES: CommuteMode[] = ["walking", "driving", "transit"];

/** Strip the "₹" and grouping commas people type, so "35,000" reaches the API as "35000". */
const money = (v: string) => v.replace(/[^\d.]/g, "");

export default function Draft() {
  const { listingId = "" } = useParams();
  const navigate = useNavigate();
  const info = useLoad(() => loadListingWithMandate(listingId), [listingId]);
  const [form, setForm] = useState<Form>(EMPTY);
  const [claim, setClaim] = useState<CommuteClaim>({ destination: "", minutes: 10, mode: "walking" });
  const [pre, setPre] = useState<PrecheckOut | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState<"precheck" | "submit" | null>(null);

  // Prefill: the last submitted version, else the flat's registered furnishing.
  useEffect(() => {
    const ad = info.data?.listing.ad;
    if (ad) {
      setForm({
        ...ad,
        rent: String(Number(ad.rent)),
        deposit: String(Number(ad.deposit)),
        brokerage_fee: String(Number(ad.brokerage_fee)),
      });
    } else if (info.data?.mandate) {
      setForm((f) => ({ ...f, furnishing: info.data!.mandate!.furnishing }));
    }
  }, [info.data]);

  const m = info.data?.mandate;
  const set = <K extends keyof Form>(key: K, value: Form[K]) => {
    setForm((f) => ({ ...f, [key]: value }));
    setPre(null); // an old pre-check no longer describes the edited draft
  };
  const body = () => ({
    ...form,
    rent: money(form.rent),
    deposit: money(form.deposit),
    brokerage_fee: money(form.brokerage_fee) || "0",
  });

  async function precheck() {
    setBusy("precheck");
    setError(null);
    try {
      setPre(await api.post<PrecheckOut>("/broker/precheck", { listing_id: listingId, ad: body() }));
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy("submit");
    setError(null);
    try {
      const out = await api.post<SubmitOut>(`/broker/listings/${listingId}/submit`, body());
      navigate(["LIVE", "VERIFIED"].includes(out.listing_status) ? `/b/listings/${listingId}` : `/b/audits/${out.audit_id}`);
    } catch (err) {
      setError(err);
      setBusy(null);
    }
  }

  function addClaim() {
    if (!claim.destination.trim()) return;
    set("commute_claims", [...form.commute_claims, { ...claim, destination: claim.destination.trim() }]);
    setClaim({ destination: "", minutes: 10, mode: "walking" });
  }

  if (info.loading && !info.data) return <div className="phone"><AppBar title="Write the ad" back="/b" /><div className="content"><Loading /></div></div>;

  return (
    <div className="phone">
      <AppBar
        title="Write the ad"
        subtitle={info.data?.listing.has_checkin ? `Check-in photo added · ${info.data.listing.address}` : info.data?.listing.address}
        back={`/b/listings/${listingId}/checkin`}
      />
      <form className="content" onSubmit={submit}>
        <ErrorBox error={info.error} />
        <div className="field">
          <label htmlFor="title">Title</label>
          <input id="title" value={form.title} onChange={(e) => set("title", e.target.value)} minLength={5} maxLength={255} required />
        </div>
        <div className="field">
          <label htmlFor="desc">Description</label>
          <textarea id="desc" rows={3} value={form.description} onChange={(e) => set("description", e.target.value)} minLength={10} maxLength={5000} required />
        </div>
        <div className="grid2">
          <div className="field">
            <label htmlFor="rent">Rent / month (₹)</label>
            <input id="rent" inputMode="numeric" value={form.rent} onChange={(e) => set("rent", e.target.value)} required />
            {m && <span className="hint">Owner allows {plain(m.min_rent)}–{plain(m.max_rent)}</span>}
          </div>
          <div className="field">
            <label htmlFor="deposit">Deposit (₹)</label>
            <input id="deposit" inputMode="numeric" value={form.deposit} onChange={(e) => set("deposit", e.target.value)} required />
            {m && <span className="hint">Up to {plain(m.max_deposit)}</span>}
          </div>
          <div className="field">
            <label htmlFor="brokerage">Your brokerage (₹)</label>
            <input id="brokerage" inputMode="numeric" value={form.brokerage_fee} onChange={(e) => set("brokerage_fee", e.target.value)} />
            {m && <span className="hint">Up to {Number(m.max_brokerage_months)} month’s rent</span>}
          </div>
          <div className="field">
            <label htmlFor="furnishing">Furnishing</label>
            <select id="furnishing" value={form.furnishing} onChange={(e) => set("furnishing", e.target.value as Furnishing)}>
              <option>Unfurnished</option>
              <option>Semi-Furnished</option>
              <option>Fully Furnished</option>
            </select>
            {m && <span className="hint">Owner registered: {m.furnishing}</span>}
          </div>
        </div>

        <fieldset className="stack" style={{ border: 0, padding: 0, margin: 0 }}>
          <legend style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>Commute claims</legend>
          {form.commute_claims.map((c, i) => (
            <div key={`${c.destination}-${i}`} className="card between" style={{ padding: "6px 6px 6px 12px" }}>
              <span className="small">{c.minutes} min {c.mode} · {c.destination}</span>
              <button type="button" className="icon-btn" aria-label={`Remove ${c.destination}`}
                onClick={() => set("commute_claims", form.commute_claims.filter((_, j) => j !== i))}>×</button>
            </div>
          ))}
          <div className="row" style={{ alignItems: "flex-end", flexWrap: "wrap" }}>
            <div className="field" style={{ flex: "2 1 160px" }}>
              <label htmlFor="dest">Place</label>
              <input id="dest" placeholder="Indiranagar Metro Station" value={claim.destination}
                onChange={(e) => setClaim({ ...claim, destination: e.target.value })} />
            </div>
            <div className="field" style={{ flex: "1 1 70px" }}>
              <label htmlFor="mins">Minutes</label>
              <input id="mins" type="number" min={1} max={600} value={claim.minutes}
                onChange={(e) => setClaim({ ...claim, minutes: Number(e.target.value) })} />
            </div>
            <div className="field" style={{ flex: "1 1 100px" }}>
              <label htmlFor="mode">How</label>
              <select id="mode" value={claim.mode} onChange={(e) => setClaim({ ...claim, mode: e.target.value as CommuteMode })}>
                {MODES.map((mode) => <option key={mode}>{mode}</option>)}
              </select>
            </div>
            <button type="button" className="btn" onClick={addClaim}>Add</button>
          </div>
          <span className="hint">Claims in the title and description are found automatically too.</span>
        </fieldset>

        {pre && (
          <section aria-label="Pre-check result" className="card strong" aria-live="polite">
            <div className="card-head dark">
              <strong className="small">
                Pre-check: {pre.suggestions.length === 0 && pre.would_be === "VERIFIED" ? "all clear" : pre.would_be === "IN_REVIEW" ? "needs a manager" : `${pre.suggestions.length} thing${pre.suggestions.length === 1 ? "" : "s"} to fix`}
              </strong>
              <span className="tiny">nothing saved</span>
            </div>
            {pre.suggestions.map((s) => <p key={s} className="suggest">→ {readable(s)}</p>)}
            {pre.commute_claims.filter((c) => c.source === "EXTRACTED").length > 0 && (
              <p className="small muted">Found in your text: {pre.commute_claims.filter((c) => c.source === "EXTRACTED").map((c) => `${c.minutes} min ${c.mode} to ${c.destination}`).join(" · ")}</p>
            )}
            <div className="stack" style={{ gap: 4 }}>
              {[...pre.checks].sort((a, b) => CHECK_ORDER.indexOf(a.check) - CHECK_ORDER.indexOf(b.check)).map((c) => (
                <div key={c.check} className="between small">
                  <span>{CHECK_LABELS[c.check] ?? c.check}</span>
                  <Pill status={c.verdict} />
                </div>
              ))}
            </div>
            <p className="tiny muted">{pre.note}</p>
          </section>
        )}
        <ErrorBox error={error} />

        <div className="footer-bar" style={{ margin: "0 -16px -16px" }}>
          <button type="button" className="btn" onClick={precheck} disabled={busy !== null}>
            {busy === "precheck" ? "Checking…" : "Run pre-check"}
          </button>
          <button className="btn primary" disabled={busy !== null || !info.data?.listing.has_checkin}>
            {busy === "submit" ? "Checking everything…" : "Submit"}
          </button>
        </div>
      </form>
    </div>
  );
}
