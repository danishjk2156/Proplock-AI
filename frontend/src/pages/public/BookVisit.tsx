import { useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import { useParams } from "react-router-dom";
import { api, ApiError } from "../../api";
import { AppBar, ErrorBox, Icon, isGone, Loading, useLoad } from "../../components";
import { money, rupees } from "../../format";
import type {
  AssistantOut,
  ChatTurn,
  Collected,
  HoldOut,
  PublicListing,
  Qualification,
  SlotsOut,
  VisitOut,
  VisitSlot,
} from "../../types";
import { PayPalHoldButton, rememberedVisit, rememberVisit } from "../escrow/shared";
import TenantVisit from "../escrow/TenantVisit";

/** Ticks every second while `active`, so countdowns re-render. */
export function useNow(active = true): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [active]);
  return now;
}

export function countdown(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

const dayLabel = (iso: string) => new Date(iso).toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" });
const timeLabel = (iso: string) => new Date(iso).toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" });

function SlotPicker({ slots, preferred, chosen, onChoose }: {
  slots: VisitSlot[]; preferred?: string | null; chosen: VisitSlot | null; onChoose: (s: VisitSlot) => void;
}) {
  const days = useMemo(() => {
    const out = new Map<string, VisitSlot[]>();
    for (const s of slots) out.set(dayLabel(s.starts_at), [...(out.get(dayLabel(s.starts_at)) ?? []), s]);
    return [...out.entries()];
  }, [slots]);
  const preferredAt = preferred ? new Date(preferred).getTime() : null;
  if (slots.length === 0) return <p className="muted small">No open times in the booking window. Ask the broker for more.</p>;
  return (
    <div className="stack" style={{ gap: 14 }}>
      {days.map(([day, list]) => (
        <div key={day} className="stack" style={{ gap: 8 }}>
          <strong className="small">{day}</strong>
          <div className="slots">
            {list.map((s) => {
              const isPreferred = preferredAt !== null && Math.abs(new Date(s.starts_at).getTime() - preferredAt) < 30 * 60_000;
              return (
                <button key={s.starts_at} type="button" className="slot" aria-pressed={chosen?.starts_at === s.starts_at}
                  onClick={() => onChoose(s)}>
                  <span>{timeLabel(s.starts_at)}</span>
                  <span className="tiny muted">{money(s.fee, s.currency)} hold{isPreferred ? " · your pick" : ""}</span>
                </button>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

function Checklist({ q, rent }: { q: Qualification | null; rent: string }) {
  const c: Collected = q?.collected ?? {};
  const rows: [string, string | null][] = [
    ["Budget", c.budget ? `${rupees(c.budget)} / month` : null],
    ["Move-in", c.move_in_date ?? null],
    ["Who’s moving", c.profile ?? null],
    ["Visit time", c.preferred_slot ? `${dayLabel(c.preferred_slot)}, ${timeLabel(c.preferred_slot)}` : null],
  ];
  return (
    <section className="card flat" aria-label="What the assistant has so far">
      <div className="between">
        <h2 className="small" style={{ fontSize: 14 }}>Before you book</h2>
        {q?.ready_to_book ? <span className="pill solid"><Icon name="check" size={12} />READY</span> : <span className="pill grey">IN PROGRESS</span>}
      </div>
      <div className="grid2 small">
        {rows.map(([label, value]) => (
          <div key={label} className="stack" style={{ gap: 0 }}>
            <span className="tiny muted">{label}</span>
            <span>{value ?? "—"}</span>
          </div>
        ))}
      </div>
      {q && q.problems.length > 0 && !q.ready_to_book && (
        <span className="tiny muted">Still needed: {q.problems.join("; ")}. Rent is {rupees(rent)} / month.</span>
      )}
    </section>
  );
}

export default function BookVisit() {
  const { listingId = "" } = useParams();
  const listing = useLoad(() => api.get<PublicListing>(`/public/listings/${listingId}`), [listingId]);
  const slots = useLoad(() => api.get<SlotsOut>(`/public/listings/${listingId}/slots`), [listingId]);

  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [draft, setDraft] = useState("");
  const [thinking, setThinking] = useState(false);
  const [q, setQ] = useState<Qualification | null>(null);
  const [offline, setOffline] = useState(false);
  const [skipChat, setSkipChat] = useState(false);
  const [chatError, setChatError] = useState<unknown>(null);

  const [chosen, setChosen] = useState<VisitSlot | null>(null);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [visit, setVisit] = useState<HoldOut | null>(null);
  const [busy, setBusy] = useState(false);
  const [bookError, setBookError] = useState<unknown>(null);
  const endRef = useRef<HTMLDivElement>(null);

  const now = useNow(visit?.status === "SOFT_HOLD");
  const left = visit ? new Date(visit.expires_at).getTime() - now : 0;
  const expired = visit?.status === "SOFT_HOLD" && left <= 0;

  async function send(message: string, history: ChatTurn[]) {
    setThinking(true);
    setChatError(null);
    try {
      const out = await api.post<AssistantOut>(`/public/listings/${listingId}/assistant`, {
        message, history, collected: q?.collected ?? {},
      });
      setQ(out.reply);
      setOffline(!out.available);
      const reply = [out.reply.answer, out.reply.ready_to_book ? null : out.reply.next_question].filter(Boolean).join("\n\n");
      setTurns([...history, ...(message ? [{ role: "user" as const, content: message }] : []), { role: "assistant", content: reply }]);
    } catch (e) {
      setChatError(e);
      if (message) setTurns([...history, { role: "user", content: message }]);
    } finally {
      setThinking(false);
    }
  }

  // The greeting comes from the server (no model call), so it names the verified listing.
  useEffect(() => {
    if (listing.data && turns.length === 0) void send("", []);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listing.data]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "nearest" });
  }, [turns, thinking]);

  function submit(e: FormEvent) {
    e.preventDefault();
    const text = draft.trim();
    if (!text || thinking) return;
    setDraft("");
    setTurns([...turns, { role: "user", content: text }]);
    void send(text, turns);
  }

  async function holdSlot(e: FormEvent) {
    e.preventDefault();
    if (!chosen) return;
    setBusy(true);
    setBookError(null);
    try {
      const v = await api.post<HoldOut>(`/public/listings/${listingId}/visits`, {
        tenant_name: name.trim(), tenant_email: email.trim(), starts_at: chosen.starts_at, ends_at: chosen.ends_at,
        collected: q?.collected ?? null,
      });
      setVisit(v);
      rememberVisit(listingId, v);
    } catch (err) {
      setBookError(err);
      if (err instanceof ApiError && err.status === 409) { setChosen(null); slots.reload(); }
    } finally {
      setBusy(false);
    }
  }

  async function release() {
    if (!visit) return;
    setBusy(true);
    setBookError(null);
    try {
      const v = await api.post<VisitOut>(`/public/visits/${visit.id}/release`, {}, { "X-Hold-Token": visit.hold_token });
      setVisit({ ...visit, ...v });
      setChosen(null);
      slots.reload();
    } catch (err) {
      setBookError(err);
    } finally {
      setBusy(false);
    }
  }

  /** After PayPal approval (or the demo button): the server authorises the order and books the visit. */
  async function authorized(orderId: string) {
    if (!visit) return;
    setBookError(null);
    try {
      const v = await api.post<VisitOut>(`/public/visits/${visit.id}/authorize`, { order_id: orderId },
        { "X-Hold-Token": visit.hold_token });
      setVisit({ ...visit, ...v });
    } catch (err) {
      setBookError(err);
      if (err instanceof ApiError && err.status === 409) {
        const v = await api.get<VisitOut>(`/public/visits/${visit.id}`, { "X-Hold-Token": visit.hold_token }).catch(() => null);
        if (v) setVisit({ ...visit, ...v });
      }
    }
  }

  // Coming back to the page (a reload, or the day of the visit): pick up this tenant's visit.
  useEffect(() => {
    const saved = rememberedVisit(listingId);
    if (!saved) return;
    api.get<VisitOut>(`/public/visits/${saved.id}`, { "X-Hold-Token": saved.token })
      .then((v) => {
        if (v.status === "EXPIRED" || v.status === "RELEASED") rememberVisit(listingId, null);
        else setVisit({ ...v, hold_token: saved.token });
      })
      .catch(() => rememberVisit(listingId, null));
  }, [listingId]);

  function startOver() {
    rememberVisit(listingId, null);
    setVisit(null);
    setChosen(null);
    setBookError(null);
    slots.reload();
  }

  const l = listing.data;
  if (listing.loading && !l) return <div className="phone"><div className="content"><Loading /></div></div>;
  if (!l) {
    return (
      <div className="phone">
        <AppBar title="Book a visit" back={`/l/${listingId}`} />
        <main className="content">
          <h1>{isGone(listing.error, 410) ? "This listing is no longer available" : "This listing can’t be booked"}</h1>
          <p className="muted">Only verified, live listings take visit bookings.</p>
        </main>
      </div>
    );
  }

  const canPick = q?.ready_to_book || offline || skipChat;
  const windowDays = slots.data?.window_days ?? 3;
  const holdMinutes = slots.data?.hold_minutes ?? 15;

  return (
    <div className="phone">
      <AppBar title="Book a visit" subtitle={l.title} back={`/l/${listingId}`} />
      <main className="content">
        {!visit && (
          <>
            <section className="card chat" aria-label="Booking assistant">
              <div className="between">
                <h2 className="row" style={{ gap: 6, fontSize: 16 }}><Icon name="spark" size={18} />Ask about this flat</h2>
                <span className="tiny muted">answers from the verified listing only</span>
              </div>
              <div className="bubbles" aria-live="polite">
                {turns.map((t, i) => (
                  <p key={i} className={`bubble${t.role === "user" ? " me" : ""}`}>{t.content}</p>
                ))}
                {thinking && <p className="bubble muted">Checking the verified listing…</p>}
                <div ref={endRef} />
              </div>
              <ErrorBox error={chatError} />
              {offline ? (
                <div className="alert warn small">The assistant is offline. You can still pick a time below.</div>
              ) : (
                <form className="row" onSubmit={submit}>
                  <label className="visually-hidden" htmlFor="chat-input">Message</label>
                  <input id="chat-input" value={draft} onChange={(e) => setDraft(e.target.value)} maxLength={1000}
                    placeholder="Budget, move-in date, who’s moving, a visit time…" disabled={thinking} />
                  <button className="icon-btn" type="submit" aria-label="Send" disabled={thinking || !draft.trim()}>
                    <Icon name="send" size={18} />
                  </button>
                </form>
              )}
            </section>

            <Checklist q={q} rent={l.rent} />

            {!canPick && (
              <button className="linkish small" onClick={() => setSkipChat(true)}>Skip the questions and pick a time</button>
            )}

            {canPick && (
              <section className="card" aria-label="Pick a visit time">
                <div className="between">
                  <h2>Pick a time</h2>
                  <span className="tiny muted">next {windowDays} days</span>
                </div>
                <span className="tiny muted">The hold is a PayPal authorization, not a charge. Earlier bookings cost less.</span>
                {slots.loading && !slots.data && <Loading />}
                <ErrorBox error={slots.error} />
                {slots.data && <SlotPicker slots={slots.data.slots} preferred={q?.collected.preferred_slot} chosen={chosen} onChoose={setChosen} />}
              </section>
            )}

            {chosen && (
              <form className="card strong" onSubmit={holdSlot} aria-label="Your details">
                <h2>{dayLabel(chosen.starts_at)}, {timeLabel(chosen.starts_at)}</h2>
                <div className="field">
                  <label htmlFor="v-name">Your name</label>
                  <input id="v-name" value={name} onChange={(e) => setName(e.target.value)} required minLength={2} maxLength={150} autoComplete="name" />
                </div>
                <div className="field">
                  <label htmlFor="v-email">Email</label>
                  <input id="v-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required maxLength={255} autoComplete="email" />
                  <span className="hint">The broker sees this. The final invoice goes here if you take the flat.</span>
                </div>
                <ErrorBox error={bookError} />
                <button className="btn primary" disabled={busy}>
                  {busy ? "Holding…" : `Hold this time for ${holdMinutes} minutes`}
                </button>
              </form>
            )}
          </>
        )}

        {visit && visit.status === "SOFT_HOLD" && !expired && (
          <section className="card strong" aria-label="Your held visit">
            <div className="between">
              <h2>Held for you</h2>
              <span className={`pill ${left < 120_000 ? "dashed" : "outline"} mono`} role="timer">{countdown(left)}</span>
            </div>
            <p className="small">{dayLabel(visit.starts_at)}, {timeLabel(visit.starts_at)} – {timeLabel(visit.ends_at)} at {l.title}.</p>
            <p className="small muted">
              PayPal places a {money(visit.fee, visit.currency)} authorization hold. You aren’t charged to book;
              the hold is released if no visit happens.
            </p>
            <ErrorBox error={bookError} />
            <PayPalHoldButton visitId={visit.id} token={visit.hold_token} label={`Authorise ${money(visit.fee, visit.currency)} with PayPal`}
              onAuthorized={authorized} onError={setBookError} />
            <button className="btn ghost small" onClick={release} disabled={busy}>Let this time go</button>
          </section>
        )}

        {visit && (expired || visit.status === "EXPIRED") && visit.outcome !== "TOO_LATE" && (
          <section className="alert warn" aria-label="Hold expired">
            <p className="small"><strong>The hold ran out</strong> and the time went back on the calendar.</p>
            <button className="btn small" style={{ marginTop: 10 }} onClick={startOver}>Pick another time</button>
          </section>
        )}

        {visit?.status === "RELEASED" && (
          <section className="alert info">
            <p className="small">You let that time go.</p>
            <button className="btn small" style={{ marginTop: 10 }} onClick={startOver}>Pick another time</button>
          </section>
        )}

        {visit && ["COMMITTED", "SETTLING", "CAPTURED", "VOIDED"].includes(visit.status) && visit.authorization_ref && (
          <TenantVisit visit={visit} token={visit.hold_token} listingId={listingId} title={l.title} brokerName={l.broker.name}
            onChange={(v) => setVisit({ ...visit, ...v })} />
        )}
        {visit?.outcome === "TOO_LATE" && (
          <section className="alert warn">
            <p className="small">{visit.outcome_reason}</p>
            <button className="btn small" style={{ marginTop: 10 }} onClick={startOver}>Pick another time</button>
          </section>
        )}
      </main>
    </div>
  );
}
