import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, ApiError, apiUrl } from "../../api";
import { Icon } from "../../components";
import { MODE_WORDS, rupees } from "../../format";
import type { ExploreItem, ExplorePage } from "../../types";
import { useTitle } from "../../ui/hooks";
import { Masonry, Pin } from "../../ui/Pin";
import { SkeletonPins } from "../../ui/Skeleton";

/** Load pages of LIVE listings from GET /public/listings. Never fills in anything the API didn't send. */
function useFeed(query: string) {
  const [items, setItems] = useState<ExploreItem[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function load(from: string | null, reset: boolean) {
    setBusy(true);
    try {
      const qs = new URLSearchParams(query);
      if (from) qs.set("cursor", from);
      const page = await api.get<ExplorePage>(`/public/listings${qs.toString() ? `?${qs}` : ""}`);
      setItems((prev) => (reset ? page.items : [...prev, ...page.items]));
      setCursor(page.next_cursor);
      setDone(!page.next_cursor);
      setError(null);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    setItems([]);
    load(null, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);
  return { items, busy, error, done, more: () => load(cursor, false) };
}

function ExplorePin({ l, listItem }: { l: ExploreItem; listItem?: boolean }) {
  return (
    <Pin listItem={listItem} to={`/l/${l.listing_id}`} title={l.title}
      image={l.photo_url ? { src: apiUrl(l.photo_url), alt: `${l.title}: owner’s photo`, width: l.photo_width ?? undefined, height: l.photo_height ?? undefined } : null}
      badges={<span className="pill solid"><Icon name="check" size={12} />Verified</span>}
      tag={<>{rupees(l.rent)} <small>/ mo</small></>}
      meta={<>{l.furnishing} · {l.city}{l.commute ? ` · ${l.commute.minutes} min ${MODE_WORDS[l.commute.mode]} to ${l.commute.destination}` : ""}</>} />
  );
}

/** S02: the explore feed (behind VITE_EXPLORE until GET /public/listings exists). */
export default function Explore() {
  useTitle("Verified flats");
  const [params] = useSearchParams();
  const q = (params.get("q") ?? "").trim().toLowerCase();
  const [city, setCity] = useState<string | null>(null);
  const [maxRent, setMaxRent] = useState<number | null>(null);
  const [walk, setWalk] = useState(false);
  const feed = useFeed("");

  // Filters run on the client until the API takes query parameters (wireframe S02).
  const cities = useMemo(() => Array.from(new Set(feed.items.map((i) => i.city))).sort(), [feed.items]);
  const shown = feed.items.filter((i) =>
    (!q || `${i.title} ${i.city} ${i.furnishing}`.toLowerCase().includes(q)) &&
    (!city || i.city === city) &&
    (!maxRent || Number(i.rent) <= maxRent) &&
    (!walk || i.commute?.mode === "walking"));
  const notReady = feed.error instanceof ApiError && [404, 405, 501].includes(feed.error.status);

  return (
    <main className="explore">
      <section className="explore-hero">
        <p className="eyebrow">Verified rentals · India</p>
        <h1>Every flat here was <em>checked</em>.</h1>
        <p>Rent, deposit, commute and photos were checked against the owner’s facts before the ad went live.</p>
      </section>

      <div className="chip-row" role="group" aria-label="Filters">
        <button className="chip" aria-pressed={!city && !maxRent && !walk} onClick={() => { setCity(null); setMaxRent(null); setWalk(false); }}>All</button>
        {cities.map((c) => <button key={c} className="chip" aria-pressed={city === c} onClick={() => setCity(city === c ? null : c)}>{c}</button>)}
        <button className="chip" aria-pressed={walk} onClick={() => setWalk(!walk)}>Walk to work</button>
        <button className="chip" aria-pressed={maxRent === 35000} onClick={() => setMaxRent(maxRent === 35000 ? null : 35000)}>Under ₹35k</button>
      </div>
      {q && <p className="small muted">Showing flats matching “{params.get("q")}”.</p>}

      {notReady ? (
        <div className="empty-state">
          <span className="empty-ic" aria-hidden="true"><Icon name="grid" size={28} /></span>
          <h2>The verified-flats feed isn’t switched on yet</h2>
          <p className="muted">Brokers share each verified listing by link for now. Ask yours for the link.</p>
        </div>
      ) : feed.error ? (
        <div className="alert" role="alert">{feed.error instanceof Error ? feed.error.message : String(feed.error)}</div>
      ) : feed.items.length === 0 && feed.busy ? (
        <SkeletonPins count={8} label="Loading verified flats…" />
      ) : shown.length === 0 ? (
        <div className="empty-state">
          <span className="empty-ic" aria-hidden="true"><Icon name="search" size={28} /></span>
          <h2>No verified flats match</h2>
          <button className="btn dark" onClick={() => { setCity(null); setMaxRent(null); setWalk(false); }}>Clear filters</button>
        </div>
      ) : (
        <Masonry label="Verified flats">{shown.map((l) => <ExplorePin key={l.listing_id} l={l} listItem />)}</Masonry>
      )}

      {!feed.done && !feed.error && feed.items.length > 0 && (
        <button className="btn load-more" onClick={feed.more} disabled={feed.busy}>{feed.busy ? "Loading more…" : "Load more"}</button>
      )}
    </main>
  );
}

/** Under a close-up: more LIVE flats in the same city. Hidden entirely if the feed can't load. */
export function MoreVerifiedFlats({ city, exclude }: { city: string; exclude: string }) {
  const feed = useFeed(`city=${encodeURIComponent(city)}`);
  const rows = feed.items.filter((i) => i.listing_id !== exclude);
  if (feed.error || rows.length === 0) return null;
  return (
    <section className="more-flats" aria-labelledby="more-h">
      <h2 id="more-h" className="section-label">More verified flats</h2>
      <Masonry label="More verified flats">{rows.map((l) => <ExplorePin key={l.listing_id} l={l} listItem />)}</Masonry>
    </section>
  );
}
