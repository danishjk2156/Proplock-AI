import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";
import { ErrorBox, Icon, ThemeToggle } from "../components";

const FEATURES = [
  { icon: "shield", title: "Verified listings", text: "Every ad is checked against the owner’s terms, rush-hour commutes, deposit rules and photos taken at the flat." },
  { icon: "spark", title: "Booking assistant", text: "Answers tenants only from the signed audit, then holds a visit slot with a travel gap between flats." },
  { icon: "wallet", title: "Visit escrow", text: "PayPal holds a small visit fee. A door PIN and GPS decide whether it is released or captured." },
  { icon: "ledger", title: "Invoice ledger", text: "The move-in invoice deducts the visit token, and every payment lands in an append-only ledger." },
];

export default function Login() {
  const { me, loading, signIn } = useAuth();
  const navigate = useNavigate();
  const [key, setKey] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  if (!loading && me) return <Navigate to={me.role === "MANAGER" ? "/m" : "/b"} replace />;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const who = await signIn(key);
      navigate(who.role === "MANAGER" ? "/m" : "/b");
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="landing">
      <div className="hero-band">
        <nav className="landing-nav" aria-label="PropLock">
          <span className="logo"><span className="mark"><Icon name="shield" size={20} /></span><strong>PropLock</strong></span>
          <ThemeToggle />
        </nav>
        <div className="hero-inner">
          <div>
            <p className="eyebrow">rentals you can check for yourself</p>
            <h1 className="hero-title">Verified flats, <em>locked</em> with PayPal</h1>
            <p className="hero-sub">
              PropLock checks a broker’s ad against facts they can’t change, refunds a wasted visit by rule,
              and invoices the move-in amounts exactly as they were verified.
            </p>
          </div>
          <form className="card strong" onSubmit={submit} aria-label="Sign in">
            <div className="stack" style={{ gap: 2 }}>
              <span className="eyebrow">brokers and the agency desk</span>
              <h2 className="caps" style={{ fontSize: 15 }}>Sign in</h2>
            </div>
            <div className="field">
              <label htmlFor="key">Your API key</label>
              <input id="key" value={key} onChange={(e) => setKey(e.target.value)} placeholder="pl_broker_…" autoComplete="off" required />
              <span className="hint">For the demo, run <span className="mono">python -m app.db.seed</span> in backend/ and paste a key it prints.</span>
            </div>
            <ErrorBox error={error} />
            <button className="btn primary" disabled={busy || !key.trim()}>{busy ? "Signing in…" : "Sign in"}</button>
            <span className="tiny muted">Tenants don’t sign in: brokers share a verified listing link with them.</span>
          </form>
        </div>
      </div>

      <section className="section" aria-label="What PropLock does">
        <span className="eyebrow">how every deal stays honest</span>
        <h2>Four safeguards</h2>
        <div className="features">
          {FEATURES.map((f) => (
            <div key={f.title} className="feature-col">
              <span className="ring" aria-hidden="true"><Icon name={f.icon} size={26} /></span>
              <h3>{f.title}</h3>
              <span className="dot-rule" aria-hidden="true" />
              <p>{f.text}</p>
            </div>
          ))}
        </div>
      </section>

      <footer className="landing-foot">
        <span>PropLock · PayPal sandbox demo</span>
        <span>Nothing is guessed: what can’t be checked goes to a person.</span>
      </footer>
    </div>
  );
}
