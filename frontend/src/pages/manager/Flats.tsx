import { useEffect, useState, type FormEvent } from "react";
import { api, apiUrl } from "../../api";
import { ErrorBox, Loading, useLoad } from "../../components";
import { plain, shortDate } from "../../format";
import type { Broker, Furnishing, Mandate, Property } from "../../types";
import ManagerLayout from "./Layout";

interface BaselinePhoto { id: string; property_id: string; room_type: string; captured_at: string }

const NEW_FLAT = {
  id: "", owner_name: "", address: "", city: "", state: "", postal_code: "", latitude: "", longitude: "",
  furnishing: "Unfurnished" as Furnishing,
};

export default function ManagerFlats() {
  const flats = useLoad(() => api.get<Property[]>("/manager/properties"));
  const brokers = useLoad(() => api.get<Broker[]>("/manager/brokers"));
  const mandates = useLoad(() => api.get<Mandate[]>("/manager/mandates"));
  const [selected, setSelected] = useState<string | null>(null);
  const [showNew, setShowNew] = useState(false);

  useEffect(() => {
    if (!selected && flats.data?.length) setSelected(flats.data[0].id);
  }, [flats.data, selected]);

  const brokerName = (id: string) => brokers.data?.find((b) => b.id === id)?.name ?? id;
  const flat = flats.data?.find((f) => f.id === selected) ?? null;
  const activeFor = (pid: string) => (mandates.data ?? []).filter((m) => m.property_id === pid && m.status === "ACTIVE");

  return (
    <ManagerLayout>
      <div className="between" style={{ flexWrap: "wrap" }}>
        <div className="stack" style={{ gap: 4 }}>
          <h1 style={{ fontSize: 26 }}>Flats</h1>
          <p className="muted">The owner’s facts here are what every listing is checked against.</p>
        </div>
        <button className="btn primary" onClick={() => setShowNew((v) => !v)}>{showNew ? "Close" : "Register a flat"}</button>
      </div>

      <section className="kpis" aria-label="At a glance">
        <div className="stat tint"><span className="n">{flats.data?.length ?? "–"}</span><span className="l">Flats registered</span></div>
        <div className="stat"><span className="n">{(mandates.data ?? []).filter((m) => m.status === "ACTIVE").length}</span><span className="l">Active mandates</span></div>
        <div className="stat"><span className="n">{brokers.data?.length ?? "–"}</span><span className="l">Brokers</span></div>
        <div className="stat"><span className="n">{brokers.data?.length ? Math.round(brokers.data.reduce((s, b) => s + b.trust_score, 0) / brokers.data.length) : "–"}</span><span className="l">Average broker trust</span></div>
      </section>

      {showNew &&<RegisterFlat onDone={(id) => { setShowNew(false); flats.reload(); setSelected(id); }} />}

      {flats.loading && !flats.data && <Loading />}
      <ErrorBox error={flats.error ?? brokers.error ?? mandates.error} />
      {flats.data && (
        <div className="table-box">
          <table>
            <thead>
              <tr><th>Flat</th><th>Owner</th><th>Furnishing</th><th>Brokers with a mandate</th><th><span className="visually-hidden">Select</span></th></tr>
            </thead>
            <tbody>
              {flats.data.map((f) => (
                <tr key={f.id} className={f.id === selected ? "selected" : ""}>
                  <td><strong>{f.id}</strong><div className="muted">{f.address}, {f.city}</div></td>
                  <td>{f.owner_name}</td>
                  <td>{f.furnishing}</td>
                  <td>{activeFor(f.id).map((m) => brokerName(m.broker_id)).join(" · ") || <span className="muted">none</span>}</td>
                  <td><button className="btn small" onClick={() => setSelected(f.id)} aria-pressed={f.id === selected}>Manage</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {flat && brokers.data && (
        <div className="cards">
          <GiveMandate flat={flat} brokers={brokers.data} onDone={mandates.reload} />
          <div className="stack" style={{ gap: 20 }}>
            <OwnerPhotos flat={flat} />
            <section className="card">
              <h2>Mandates · {flat.id}</h2>
              {(mandates.data ?? []).filter((m) => m.property_id === flat.id).map((m) => (
                <MandateLine key={m.id} m={m} broker={brokerName(m.broker_id)} onDone={mandates.reload} />
              ))}
              {(mandates.data ?? []).filter((m) => m.property_id === flat.id).length === 0 && <p className="muted small">No mandates yet.</p>}
            </section>
          </div>
        </div>
      )}
    </ManagerLayout>
  );
}

function MandateLine({ m, broker, onDone }: { m: Mandate; broker: string; onDone: () => void }) {
  const [error, setError] = useState<unknown>(null);
  async function revoke() {
    if (!window.confirm(`Revoke ${broker}’s mandate? Their listing of this flat leaves the public page.`)) return;
    try {
      await api.post(`/manager/mandates/${m.id}/revoke`);
      onDone();
    } catch (e) {
      setError(e);
    }
  }
  return (
    <div className="stack" style={{ gap: 4 }}>
      <div className="between small">
        <span><strong>{broker}</strong> · {plain(m.min_rent)}–{plain(m.max_rent)} · deposit ≤ {plain(m.max_deposit)} · until {shortDate(m.valid_until)}</span>
        {m.status === "ACTIVE" ? <button className="btn small" onClick={revoke}>Revoke</button> : <span className="pill grey">{m.status}</span>}
      </div>
      <ErrorBox error={error} />
    </div>
  );
}

function GiveMandate({ flat, brokers, onDone }: { flat: Property; brokers: Broker[]; onDone: () => void }) {
  const [form, setForm] = useState({ broker_id: "", min_rent: "", max_rent: "", max_deposit: "", max_brokerage_months: "1", valid_until: "" });
  const [error, setError] = useState<unknown>(null);
  const [done, setDone] = useState(false);
  const set = (k: keyof typeof form, v: string) => { setForm((f) => ({ ...f, [k]: v })); setDone(false); };

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api.post("/manager/mandates", {
        ...form,
        property_id: flat.id,
        // The API needs a timezone: the end of that day in India.
        valid_until: `${form.valid_until}T23:59:00+05:30`,
      });
      setDone(true);
      onDone();
    } catch (err) {
      setError(err);
    }
  }

  return (
    <form className="card" onSubmit={submit}>
      <div className="stack" style={{ gap: 4 }}>
        <h2>Give a broker a mandate · {flat.id}</h2>
        <p className="small muted">The owner’s limits. Every ad for this flat is checked against these.</p>
      </div>
      <div className="field">
        <label htmlFor="broker">Broker</label>
        <select id="broker" value={form.broker_id} onChange={(e) => set("broker_id", e.target.value)} required>
          <option value="">Choose a broker</option>
          {brokers.map((b) => <option key={b.id} value={b.id}>{b.name}{b.agency ? ` · ${b.agency}` : ""} (trust {b.trust_score})</option>)}
        </select>
      </div>
      <div className="grid2">
        <div className="field"><label htmlFor="minr">Min rent (₹)</label><input id="minr" inputMode="numeric" value={form.min_rent} onChange={(e) => set("min_rent", e.target.value)} required /></div>
        <div className="field"><label htmlFor="maxr">Max rent (₹)</label><input id="maxr" inputMode="numeric" value={form.max_rent} onChange={(e) => set("max_rent", e.target.value)} required /></div>
        <div className="field"><label htmlFor="maxd">Max deposit (₹)</label><input id="maxd" inputMode="numeric" value={form.max_deposit} onChange={(e) => set("max_deposit", e.target.value)} required /></div>
        <div className="field"><label htmlFor="brk">Max brokerage (months)</label><input id="brk" inputMode="decimal" value={form.max_brokerage_months} onChange={(e) => set("max_brokerage_months", e.target.value)} required /></div>
      </div>
      <div className="field"><label htmlFor="until">Valid until</label><input id="until" type="date" value={form.valid_until} onChange={(e) => set("valid_until", e.target.value)} required /></div>
      <ErrorBox error={error} />
      {done && <p className="alert info" role="status">Mandate given.</p>}
      <button className="btn primary">Give mandate</button>
    </form>
  );
}

function OwnerPhotos({ flat }: { flat: Property }) {
  const photos = useLoad(() => api.get<BaselinePhoto[]>(`/manager/properties/${flat.id}/photos`), [flat.id]);
  const [room, setRoom] = useState("living");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function upload(files: FileList | null) {
    if (!files?.length) return;
    setBusy(true);
    setError(null);
    const form = new FormData();
    Array.from(files).forEach((f) => form.append("files", f));
    form.append("room_type", room);
    try {
      await api.upload(`/manager/properties/${flat.id}/photos`, form);
      await photos.reload();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <div className="stack" style={{ gap: 4 }}>
        <h2>Owner photos · {flat.id}</h2>
        <p className="small muted">Shown to tenants instead of the broker’s photos, and used to check that extra photos show this flat.</p>
      </div>
      <div className="photo-grid" style={{ gridTemplateColumns: "repeat(3, minmax(0, 1fr))" }}>
        {photos.data?.map((p) => (
          <figure key={p.id} style={{ margin: 0 }}>
            <img src={apiUrl(`/public/photos/${p.id}`)} alt={`Owner photo: ${p.room_type}`} />
            <figcaption className="tiny muted">{p.room_type}</figcaption>
          </figure>
        ))}
      </div>
      <div className="row" style={{ flexWrap: "wrap" }}>
        <div className="field" style={{ flex: "1 1 140px" }}>
          <label htmlFor="room">Room</label>
          <select id="room" value={room} onChange={(e) => setRoom(e.target.value)}>
            {["living", "bedroom", "kitchen", "bathroom", "balcony", "general"].map((r) => <option key={r}>{r}</option>)}
          </select>
        </div>
        <input id="owner-photos" className="visually-hidden" type="file" accept="image/jpeg,image/png,image/webp" multiple
          onChange={(e) => { upload(e.target.files); e.currentTarget.value = ""; }} />
        <label htmlFor="owner-photos" className="btn" style={{ alignSelf: "flex-end" }}>{busy ? "Uploading…" : "Add photos"}</label>
      </div>
      <ErrorBox error={error ?? photos.error} />
    </section>
  );
}

function RegisterFlat({ onDone }: { onDone: (id: string) => void }) {
  const [form, setForm] = useState(NEW_FLAT);
  const [error, setError] = useState<unknown>(null);
  const set = (k: keyof typeof form, v: string) => setForm((f) => ({ ...f, [k]: v }));

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api.post("/manager/properties", { ...form, id: form.id.trim().toUpperCase() });
      onDone(form.id.trim().toUpperCase());
    } catch (err) {
      setError(err);
    }
  }

  const text = (k: keyof typeof form, label: string, extra: Record<string, string> = {}) => (
    <div className="field">
      <label htmlFor={`nf-${k}`}>{label}</label>
      <input id={`nf-${k}`} value={form[k]} onChange={(e) => set(k, e.target.value)} required {...extra} />
    </div>
  );

  return (
    <form className="card" onSubmit={submit}>
      <h2>Register a flat</h2>
      <p className="small muted">Take the GPS pin at the flat: every commute and photo check measures from it.</p>
      <div className="cards" style={{ gap: 12 }}>
        {text("id", "Flat ID", { placeholder: "PROP-BLR-003", pattern: "[A-Za-z0-9-]{3,50}" })}
        {text("owner_name", "Owner’s name")}
        {text("address", "Address")}
        {text("city", "City")}
        {text("state", "State", { placeholder: "Karnataka" })}
        {text("postal_code", "PIN code")}
        {text("latitude", "Latitude", { inputMode: "decimal", placeholder: "12.9718910" })}
        {text("longitude", "Longitude", { inputMode: "decimal", placeholder: "77.6411510" })}
        <div className="field">
          <label htmlFor="nf-furnishing">Furnishing</label>
          <select id="nf-furnishing" value={form.furnishing} onChange={(e) => set("furnishing", e.target.value)}>
            <option>Unfurnished</option><option>Semi-Furnished</option><option>Fully Furnished</option>
          </select>
        </div>
      </div>
      <ErrorBox error={error} />
      <button className="btn primary">Register flat</button>
    </form>
  );
}
