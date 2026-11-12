// Feature 3 helpers shared by the tenant's and the broker's visit screens.
import { useEffect, useRef, useState } from "react";
import { api } from "../../api";
import type { OrderOut, PaymentsConfig } from "../../types";

export interface Fix {
  latitude: number;
  longitude: number;
  accuracy_m: number;
}

/** One GPS fix from the browser. Rejects with a sentence the page can show. */
export function currentPosition(): Promise<Fix> {
  return new Promise((resolve, reject) => {
    if (!("geolocation" in navigator)) return reject(new Error("This browser can't share its location."));
    navigator.geolocation.getCurrentPosition(
      (p) => resolve({
        latitude: Number(p.coords.latitude.toFixed(7)),
        longitude: Number(p.coords.longitude.toFixed(7)),
        accuracy_m: Math.round(p.coords.accuracy),
      }),
      (e) => reject(new Error(e.code === e.PERMISSION_DENIED
        ? "Location access was blocked. Allow it for this site and try again."
        : "Couldn't get your location. Try again outdoors or near a window.")),
      { enableHighAccuracy: true, timeout: 15_000, maximumAge: 0 },
    );
  });
}

/** Re-runs `tick` every `ms` while `active` (the other side's taps show up without a reload). */
export function usePoll(tick: () => void, ms: number, active: boolean) {
  const saved = useRef(tick);
  saved.current = tick;
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => saved.current(), ms);
    return () => clearInterval(t);
  }, [ms, active]);
}

type PayPalNamespace = {
  Buttons: (opts: {
    style?: Record<string, string>;
    createOrder: () => Promise<string>;
    onApprove: (data: { orderID: string }) => Promise<void>;
    onError: (err: unknown) => void;
  }) => { render: (el: HTMLElement) => Promise<void>; close?: () => void };
};

let sdk: Promise<PayPalNamespace> | null = null;

function loadSdk(clientId: string, currency: string): Promise<PayPalNamespace> {
  if (!sdk) {
    sdk = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = `https://www.paypal.com/sdk/js?client-id=${encodeURIComponent(clientId)}&intent=authorize&currency=${currency}&components=buttons`;
      s.onload = () => {
        const paypal = (window as unknown as { paypal?: PayPalNamespace }).paypal;
        if (paypal) resolve(paypal);
        else reject(new Error("PayPal didn't load"));
      };
      s.onerror = () => {
        sdk = null;
        reject(new Error("Couldn't load PayPal. Check your connection or ad blocker."));
      };
      document.head.appendChild(s);
    });
  }
  return sdk;
}

/**
 * The visit fee as a PayPal authorization hold. With sandbox keys this is PayPal's own button
 * (the tenant approves in PayPal's popup); in demo mode a plain button, and no PayPal call is made.
 * Either way the server creates the order (the amount comes from the visit row) and authorises it.
 */
export function PayPalHoldButton({ visitId, token, label, onAuthorized, onError }: {
  visitId: string; token: string; label: string;
  onAuthorized: (orderId: string) => Promise<void>; onError: (e: unknown) => void;
}) {
  const [mode, setMode] = useState<"loading" | "demo" | "paypal">("loading");
  const [busy, setBusy] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const createOrder = async () =>
    (await api.post<OrderOut>(`/public/visits/${visitId}/paypal-order`, {}, { "X-Hold-Token": token })).order_id;

  useEffect(() => {
    let closed = false;
    let buttons: { close?: () => void } | null = null;
    api.get<PaymentsConfig>("/public/payments/config").then(async (c) => {
      if (c.demo || !c.paypal_client_id) {
        if (!closed) setMode("demo");
        return;
      }
      setMode("paypal");
      const paypal = await loadSdk(c.paypal_client_id, c.currency);
      if (closed || !box.current) return;
      buttons = paypal.Buttons({
        style: { layout: "vertical", shape: "pill", label: "paypal" },
        createOrder,
        onApprove: async (data) => { await onAuthorized(data.orderID); },
        onError,
      });
      await (buttons as { render: (el: HTMLElement) => Promise<void> }).render(box.current);
    }).catch(onError);
    return () => {
      closed = true;
      buttons?.close?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visitId]);

  async function demo() {
    setBusy(true);
    try {
      await onAuthorized(await createOrder());
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  }

  if (mode === "demo") {
    return (
      <div className="stack" style={{ gap: 6 }}>
        <button className="btn primary" onClick={demo} disabled={busy}>{busy ? "Authorising…" : label}</button>
        <span className="tiny muted">Demo mode: no PayPal keys, so the hold is simulated (FAKE- ids).</span>
      </div>
    );
  }
  return (
    <div className="stack" style={{ gap: 6 }}>
      {mode === "loading" && <p className="muted small" role="status">Loading PayPal…</p>}
      <div ref={box} aria-label="Pay with PayPal" />
    </div>
  );
}

const KEY = "proplock.visits";

/** The tenant's hold token for each visit, kept so the visit page survives a reload. */
export function rememberVisit(listingId: string, visit: { id: string; hold_token: string } | null): void {
  try {
    const all = JSON.parse(window.localStorage.getItem(KEY) ?? "{}") as Record<string, unknown>;
    if (visit) all[listingId] = { id: visit.id, token: visit.hold_token };
    else delete all[listingId];
    window.localStorage.setItem(KEY, JSON.stringify(all));
  } catch {
    // storage unavailable: the visit lives only in this tab
  }
}

export function rememberedVisit(listingId: string): { id: string; token: string } | null {
  try {
    const all = JSON.parse(window.localStorage.getItem(KEY) ?? "{}") as Record<string, { id: string; token: string }>;
    return all[listingId] ?? null;
  } catch {
    return null;
  }
}
