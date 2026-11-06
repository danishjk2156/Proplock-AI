import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../../api";
import { AppBar, ErrorBox, Icon, Loading, useLoad } from "../../components";
import { metresBetween } from "../../format";
import type { Photo } from "../../types";
import { loadListingWithMandate } from "./shared";

type Fix = { lat: number; lng: number; accuracy: number } | null;
const MAX_METRES = 100;

export default function Checkin() {
  const { listingId = "" } = useParams();
  const info = useLoad(() => loadListingWithMandate(listingId), [listingId]);
  const photos = useLoad(() => api.get<Photo[]>(`/broker/listings/${listingId}/photos`), [listingId]);
  const [fix, setFix] = useState<Fix>(null);
  const [gpsError, setGpsError] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [previews, setPreviews] = useState<Record<string, string>>({});
  const checkinInput = useRef<HTMLInputElement>(null);
  const extraInput = useRef<HTMLInputElement>(null);

  // Watch the phone's location while this screen is open; it's sent with each upload.
  useEffect(() => {
    if (!("geolocation" in navigator)) {
      setGpsError("This browser can’t share its location.");
      return;
    }
    const id = navigator.geolocation.watchPosition(
      (p) => { setFix({ lat: p.coords.latitude, lng: p.coords.longitude, accuracy: p.coords.accuracy }); setGpsError(null); },
      (e) => setGpsError(e.code === e.PERMISSION_DENIED ? "Location access is blocked. Allow it to take the check-in photo." : "Can’t get your location yet."),
      { enableHighAccuracy: true, maximumAge: 10_000, timeout: 20_000 },
    );
    return () => navigator.geolocation.clearWatch(id);
  }, []);

  const mandate = info.data?.mandate;
  const distance = fix && mandate ? metresBetween(fix.lat, fix.lng, Number(mandate.latitude), Number(mandate.longitude)) : null;
  const nearFlat = distance !== null && distance <= MAX_METRES;
  const active = photos.data ?? [];
  const hasCheckin = active.some((p) => p.kind === "CHECKIN");

  async function upload(files: FileList | null, kind: "CHECKIN" | "EXTRA") {
    if (!files || files.length === 0) return;
    setBusy(true);
    setError(null);
    const form = new FormData();
    Array.from(files).forEach((f) => form.append("files", f));
    form.append("kind", kind);
    if (fix) {
      form.append("latitude", fix.lat.toFixed(7));
      form.append("longitude", fix.lng.toFixed(7));
    }
    try {
      const created = await api.upload<Photo[]>(`/broker/listings/${listingId}/photos`, form);
      const urls: Record<string, string> = {};
      created.forEach((p, i) => { if (files[i]) urls[p.id] = URL.createObjectURL(files[i]); });
      setPreviews((prev) => ({ ...prev, ...urls }));
      await photos.reload();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
      if (checkinInput.current) checkinInput.current.value = "";
      if (extraInput.current) extraInput.current.value = "";
    }
  }

  async function remove(photo: Photo) {
    setError(null);
    try {
      await api.del(`/broker/listings/${listingId}/photos/${photo.id}`);
      await photos.reload();
    } catch (e) {
      setError(e);
    }
  }

  return (
    <div className="phone">
      <AppBar title="Check-in photo" subtitle={info.data?.listing.address} back="/b" />
      <main className="content">
        {info.loading && <Loading />}
        <ErrorBox error={info.error} />

        <div className="card row" aria-live="polite">
          <Icon name="pin" size={28} />
          <div className="stack" style={{ gap: 2, flex: 1 }}>
            {distance !== null ? (
              <strong>You’re {Math.round(distance)} m from the flat</strong>
            ) : (
              <strong>{gpsError ?? "Finding your location…"}</strong>
            )}
            <span className="small muted">Must be within {MAX_METRES} m of the agency’s pin{fix ? ` · GPS accuracy ±${Math.round(fix.accuracy)} m` : ""}</span>
          </div>
          {distance !== null && <span className={`pill ${nearFlat ? "solid" : "outline"}`}>{nearFlat ? "OK" : "TOO FAR"}</span>}
        </div>

        <p className="small muted">
          The photo records where and when it was taken and stays valid for 30 days. A photo used for any other flat is rejected.
          If your browser can’t share its location, the photo’s own GPS data is used instead.
        </p>

        <ErrorBox error={error} />

        <input ref={checkinInput} id="checkin" className="visually-hidden" type="file" accept="image/jpeg,image/png,image/webp" capture="environment"
          onChange={(e) => upload(e.target.files, "CHECKIN")} />
        <label htmlFor="checkin" className="btn primary" style={{ minHeight: 56 }} aria-disabled={busy}>
          <Icon name="camera" /> {busy ? "Uploading…" : hasCheckin ? "Retake check-in photo" : "Take check-in photo"}
        </label>

        <section className="stack">
          <div className="between">
            <strong className="small">Your photos</strong>
            <span className="tiny muted">extra photos are compared with the owner’s</span>
          </div>
          <div className="photo-grid">
            {active.map((p) => (
              <figure key={p.id} style={{ margin: 0, position: "relative" }}>
                {previews[p.id] ? <img src={previews[p.id]} alt={`${p.kind.toLowerCase()} photo`} /> : <div className="ph" />}
                <figcaption className="tiny muted">{p.kind === "CHECKIN" ? "Check-in" : "Extra"}{p.gps_source ? ` · GPS ${p.gps_source}` : " · no GPS"}</figcaption>
                <button className="icon-btn" aria-label="Remove photo" onClick={() => remove(p)}
                  style={{ position: "absolute", top: 2, right: 2, width: 32, height: 32, background: "var(--bg)" }}>
                  <Icon name="x" size={16} />
                </button>
              </figure>
            ))}
            <input ref={extraInput} id="extra" className="visually-hidden" type="file" accept="image/jpeg,image/png,image/webp" multiple
              onChange={(e) => upload(e.target.files, "EXTRA")} />
            <label htmlFor="extra" className="btn" style={{ aspectRatio: "1", borderStyle: "dashed", fontSize: 22 }} aria-label="Add extra photos">+</label>
          </div>
        </section>
      </main>
      <div className="footer-bar">
        {hasCheckin ? (
          <Link className="btn primary" to={`/b/listings/${listingId}/ad`}>Next: write the ad</Link>
        ) : (
          <button className="btn primary" disabled>Take the check-in photo first</button>
        )}
      </div>
    </div>
  );
}
