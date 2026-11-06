import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";
import { ErrorBox, Icon } from "../components";

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
    <div className="phone">
      <main className="content" style={{ justifyContent: "center", gap: 24 }}>
        <div className="stack" style={{ gap: 8 }}>
          <div className="row"><Icon name="shield" size={32} /><h1 style={{ fontSize: 28 }}>PropLock</h1></div>
          <p className="muted">Rental listings checked against the owner’s terms, real commute times, deposit rules and photos taken at the flat.</p>
        </div>
        <form className="card" onSubmit={submit}>
          <div className="field">
            <label htmlFor="key">Your API key</label>
            <input id="key" value={key} onChange={(e) => setKey(e.target.value)} placeholder="pl_broker_…" autoComplete="off" required />
            <span className="hint">For the demo, run <span className="mono">python -m app.db.seed</span> in backend/ and paste a key it prints.</span>
          </div>
          <ErrorBox error={error} />
          <button className="btn primary" disabled={busy || !key.trim()}>{busy ? "Signing in…" : "Sign in"}</button>
        </form>
        <p className="small muted">Tenants don’t sign in: brokers share a listing link with them.</p>
      </main>
    </div>
  );
}
