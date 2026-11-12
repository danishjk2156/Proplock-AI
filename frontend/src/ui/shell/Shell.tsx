import { createContext, useContext, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api } from "../../api";
import { useAuth } from "../../auth";
import { useLoad } from "../../components";
import { daysUntil } from "../../format";
import { openOrStartListing } from "../../pages/broker/shared";
import { applyTheme, loadTheme, type ThemeChoice } from "../../theme";
import type { BrokerListing, BrokerMandate, CopilotOut, ReviewItem, Role } from "../../types";
import { Dialog } from "../Dialog";
import { Icon } from "../Icon";

/* S00: the app shell. Top nav on wide screens; a bottom tab bar under 760 px for signed-in users.
   Tenants get the bare shell (logo, theme), never the ledger, "+" or bell. */

export const EXPLORE_ENABLED = import.meta.env.VITE_EXPLORE === "true";

// ---------- shell context: lets a page keep the nav badge in step with its own data ----------
interface ShellState {
  setReviewCount: (n: number | undefined) => void;
  reloadAlerts: () => void;
}
const ShellContext = createContext<ShellState>({ setReviewCount: () => undefined, reloadAlerts: () => undefined });
export const useShell = () => useContext(ShellContext);

// ---------- alerts: real counts only ----------
interface AlertItem { key: string; to: string; title: string; detail: string }

function useAlerts(role: Role | undefined) {
  const { pathname } = useLocation();
  const [items, setItems] = useState<AlertItem[]>([]);
  const [reviewOverride, setReviewOverride] = useState<number | undefined>(undefined);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let live = true;
    async function load() {
      try {
        if (role === "MANAGER") {
          const rows = await api.get<ReviewItem[]>("/manager/reviews");
          if (live) setItems(rows.map((r) => ({
            key: r.audit_id, to: "/m/reviews",
            title: `Review ${r.property_id} · version ${r.version_no}`,
            detail: `${r.broker_name} · ${r.open_questions.length} open question${r.open_questions.length === 1 ? "" : "s"}`,
          })));
        } else if (role === "BROKER") {
          const rows = await api.get<BrokerListing[]>("/broker/listings");
          const out: AlertItem[] = [];
          for (const l of rows) {
            if (l.status === "NEEDS_FIX") {
              out.push({ key: l.id, to: l.audit_id ? `/b/audits/${l.audit_id}` : `/b/listings/${l.id}`, title: `Needs a fix · ${l.address}`,
                detail: `${l.problems} thing${l.problems === 1 ? "" : "s"} to fix, with suggestions` });
            } else if (l.status === "EXPIRED" || (l.status === "LIVE" && (daysUntil(l.expires_at) ?? 99) <= 1)) {
              out.push({ key: l.id, to: `/b/listings/${l.id}`, title: `Confirm availability · ${l.address}`,
                detail: l.status === "EXPIRED" ? "Expired: confirm to bring it back" : "Confirm it’s still available today" });
            }
          }
          if (live) setItems(out);
        }
      } catch {
        // the bell is a convenience: if it can't load, it shows nothing rather than an error
        if (live) setItems([]);
      }
    }
    if (role) load();
    return () => { live = false; };
  }, [role, pathname, tick]);

  const count = role === "MANAGER" && reviewOverride !== undefined ? reviewOverride : items.length;
  return { items, count, setReviewOverride, reload: () => setTick((t) => t + 1) };
}

// ---------- pieces ----------
export function Logo({ to, compact }: { to?: string; compact?: boolean }) {
  const inner = (
    <>
      <span className="logo-mark" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
          <path d="M4 20V10l8-6 8 6v10z" /><path d="m8.5 13.5 2.5 2.5 4.5-5" />
        </svg>
      </span>
      <span className={`logo-word${compact ? " hide-phone" : ""}`}>PropLock<b>.</b></span>
    </>
  );
  return to ? <Link to={to} className="logo" aria-label="PropLock home">{inner}</Link> : <span className="logo">{inner}</span>;
}

const COPILOT_EXAMPLES = ["Which deposits are unpaid after 3 days?", "Biggest outstanding balances first", "Which closings are disputed?"];

/** The one big search field. Signed in: the read-only ledger copilot. Tenant: flat search (behind VITE_EXPLORE). */
function SearchBar({ role }: { role: Role | null }) {
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState<CopilotOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const copilot = role !== null;
  const ledgerPath = role === "MANAGER" ? "/m/ledger" : "/b/ledger";

  useEffect(() => {
    if (!open) return;
    function away(e: MouseEvent) { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); }
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);

  async function ask(question: string) {
    if (question.trim().length < 3) return;
    setQ(question);
    setOpen(true);
    setBusy(true);
    setError(null);
    setAnswer(null);
    try {
      setAnswer(await api.post<CopilotOut>("/ledger/copilot", { question }));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    if (copilot) ask(q);
    else navigate(`/explore?q=${encodeURIComponent(q.trim())}`);
  }

  return (
    <div className="search" ref={box} onKeyDown={(e) => { if (e.key === "Escape" && open) { e.stopPropagation(); setOpen(false); } }}>
      <form role="search" onSubmit={submit} className="search-field">
        <Icon name={copilot ? "spark" : "search"} size={18} />
        <label htmlFor="nav-search" className="visually-hidden">{copilot ? "Ask the ledger (read-only)" : "Search verified flats"}</label>
        <input id="nav-search" value={q} onChange={(e) => setQ(e.target.value)} onFocus={() => copilot && setOpen(true)}
          placeholder={copilot ? "Ask the ledger…" : "Search verified flats"} maxLength={500}
          aria-expanded={copilot ? open : undefined} aria-controls={copilot ? "copilot-panel" : undefined} autoComplete="off" />
        {copilot && <span className="ai-tag" aria-hidden="true">AI · read-only</span>}
      </form>
      {copilot && open && (
        <div id="copilot-panel" className="search-panel" role="region" aria-label="Ledger copilot">
          <p className="tiny muted">Read-only: it searches your ledger and can never change anything.</p>
          {!answer && !busy && !error && (
            <div className="chips">
              {COPILOT_EXAMPLES.map((ex) => <button key={ex} type="button" className="chip" onClick={() => ask(ex)}>{ex}</button>)}
            </div>
          )}
          <div aria-live="polite">
            {busy && <p className="small muted">Asking…</p>}
            {error && <div className="alert" role="alert">{error}</div>}
            {answer && (
              <div className={`alert ${answer.refused ? "warn" : "info"} small`}>
                <pre className="answer">{answer.answer}</pre>
                <span className="tiny muted">Search written by {answer.source}; rows found and counted by PropLock’s own code.</span>
              </div>
            )}
          </div>
          {answer && !answer.refused && answer.target && (
            <button type="button" className="btn small primary" onClick={() => { setOpen(false); navigate(ledgerPath, { state: { copilot: answer } }); }}>
              Show {answer.rows.length} row{answer.rows.length === 1 ? "" : "s"} in the ledger <Icon name="arrow" size={16} />
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/** "+ Create". Broker: start a listing from one of their mandates. Manager: register / mandate / close. */
function CreateDialog({ role, open, onClose }: { role: Role; open: boolean; onClose: () => void }) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const data = useLoad(async () => {
    if (role !== "BROKER" || !open) return null;
    const [mandates, listings] = await Promise.all([
      api.get<BrokerMandate[]>("/broker/mandates"),
      api.get<BrokerListing[]>("/broker/listings"),
    ]);
    return { mandates, listings };
  }, [role, open]);

  function go(path: string, state?: unknown) {
    onClose();
    navigate(path, state ? { state } : undefined);
  }

  async function start(m: BrokerMandate) {
    setBusy(m.mandate_id);
    setError(null);
    try {
      go(await openOrStartListing(m, data.data?.listings ?? []));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Dialog open={open} onClose={onClose} title={role === "BROKER" ? "Start a listing" : "Create"} variant="menu">
      {role === "MANAGER" ? (
        <div className="menu-list">
          <button className="menu-item" onClick={() => go("/m", { create: "flat" })}>
            <Icon name="house" /><span><strong>Register a flat</strong><span className="tiny muted">The owner’s facts every ad is checked against</span></span>
          </button>
          <button className="menu-item" onClick={() => go("/m", { create: "mandate" })}>
            <Icon name="shield" /><span><strong>Give a mandate</strong><span className="tiny muted">Let a broker list one of your flats</span></span>
          </button>
          <button className="menu-item" onClick={() => go("/m/ledger", { create: "close" })}>
            <Icon name="wallet" /><span><strong>Close a flat</strong><span className="tiny muted">Lock it for a tenant and send the invoice</span></span>
          </button>
        </div>
      ) : (
        <div className="stack">
          <p className="small muted">Pick a flat you hold a mandate for.</p>
          {data.loading && <p className="small muted" role="status">Loading your mandates…</p>}
          {data.error != null && <div className="alert" role="alert">{data.error instanceof Error ? data.error.message : String(data.error)}</div>}
          {error && <div className="alert" role="alert">{error}</div>}
          {data.data?.mandates.length === 0 && <p className="small">No active mandates. Ask the agency desk to give you one.</p>}
          <div className="menu-list">
            {data.data?.mandates.map((m) => (
              <button key={m.mandate_id} className="menu-item" disabled={busy !== null} onClick={() => start(m)}>
                <Icon name="house" />
                <span><strong>{m.address}</strong><span className="tiny muted">{m.city} · {m.property_id}{busy === m.mandate_id ? " · opening…" : ""}</span></span>
              </button>
            ))}
          </div>
        </div>
      )}
    </Dialog>
  );
}

function AlertsDialog({ open, onClose, items, role }: { open: boolean; onClose: () => void; items: AlertItem[]; role: Role }) {
  return (
    <Dialog open={open} onClose={onClose} title={role === "MANAGER" ? "Waiting for review" : "Needs you now"} variant="menu">
      {items.length === 0 ? (
        <p className="small muted">{role === "MANAGER" ? "Nothing in the review queue." : "Nothing needs you right now."}</p>
      ) : (
        <div className="menu-list">
          {items.map((a) => (
            <Link key={a.key} to={a.to} className="menu-item" onClick={onClose}>
              <Icon name={role === "MANAGER" ? "inbox" : "warn"} />
              <span><strong>{a.title}</strong><span className="tiny muted">{a.detail}</span></span>
            </Link>
          ))}
        </div>
      )}
    </Dialog>
  );
}

function ThemePicker() {
  const [theme, setTheme] = useState<ThemeChoice>(loadTheme);
  const pick = (t: ThemeChoice) => { setTheme(t); applyTheme(t); };
  return (
    <fieldset className="theme-pick">
      <legend className="eyebrow">Theme</legend>
      <div className="seg">
        {(["system", "light", "dark"] as ThemeChoice[]).map((t) => (
          <button key={t} type="button" aria-pressed={theme === t} onClick={() => pick(t)}>
            {t === "system" ? "System" : t === "light" ? "Light" : "Dark"}
          </button>
        ))}
      </div>
    </fieldset>
  );
}

function MeDialog({ open, onClose, links }: { open: boolean; onClose: () => void; links: NavItem[] }) {
  const { me, signOut } = useAuth();
  const navigate = useNavigate();
  if (!me) return null;
  return (
    <Dialog open={open} onClose={onClose} title="Your account" variant="menu">
      <div className="stack" style={{ gap: 16 }}>
        <div className="row">
          <span className="avatar" aria-hidden="true">{me.name.slice(0, 1)}</span>
          <div className="stack" style={{ gap: 0 }}>
            <strong>{me.name}</strong>
            <span className="tiny muted">
              {me.role === "BROKER" ? "Broker" : "Agency manager"}{me.agency ? ` · ${me.agency}` : ""}
              {me.role === "BROKER" ? ` · trust ${me.trust_score}` : ""}
            </span>
          </div>
        </div>
        {links.length > 0 && (
          <nav className="menu-list" aria-label="More">
            {links.map((l) => (
              <Link key={l.to} to={l.to} className="menu-item" onClick={onClose}><Icon name={l.icon} /><span><strong>{l.label}</strong></span></Link>
            ))}
          </nav>
        )}
        <ThemePicker />
        <button className="btn" onClick={() => { onClose(); signOut(); navigate("/"); }}><Icon name="out" size={18} /> Sign out</button>
      </div>
    </Dialog>
  );
}

// ---------- navigation sets ----------
interface NavItem { to: string; label: string; icon: string; end?: boolean; badge?: boolean }

const BROKER_NAV: NavItem[] = [
  { to: "/b", label: "Listings", icon: "grid", end: true },
  { to: "/b/visits", label: "Visits", icon: "calendar" },
  { to: "/b/ledger", label: "Ledger", icon: "ledger" },
];
const MANAGER_NAV: NavItem[] = [
  { to: "/m", label: "Flats", icon: "grid", end: true },
  { to: "/m/reviews", label: "Reviews", icon: "inbox", badge: true },
  { to: "/m/visits", label: "Visits", icon: "calendar" },
  { to: "/m/ledger", label: "Ledger", icon: "ledger" },
  { to: "/m/disputes", label: "Disputes", icon: "scale" },
];

/** Scroll to #id after navigation (React Router doesn't), else to the top. */
function ScrollManager() {
  const { pathname, hash } = useLocation();
  useEffect(() => {
    if (hash) document.getElementById(hash.slice(1))?.scrollIntoView();
    else window.scrollTo(0, 0);
  }, [pathname, hash]);
  return null;
}

function SignedInShell({ role, children }: { role: Role; children: ReactNode }) {
  const { me } = useAuth();
  const alerts = useAlerts(role);
  const [sheet, setSheet] = useState<"create" | "alerts" | "me" | null>(null);
  const close = () => setSheet(null);
  const nav = role === "MANAGER" ? MANAGER_NAV : BROKER_NAV;
  const home = role === "MANAGER" ? "/m" : "/b";
  // On phones the tab bar has five slots; the rest move into the "Me" sheet.
  const phoneTabs = role === "MANAGER" ? [MANAGER_NAV[0], MANAGER_NAV[3]] : [BROKER_NAV[0], BROKER_NAV[2]];
  const moreLinks = nav.filter((n) => !phoneTabs.includes(n));
  const badge = alerts.count > 0 ? <span className="badge" aria-label={`${alerts.count} waiting`}>{alerts.count}</span> : null;

  return (
    <ShellContext.Provider value={{ setReviewCount: alerts.setReviewOverride, reloadAlerts: alerts.reload }}>
      <div className={`shell shell-${role.toLowerCase()}`}>
        <a className="skip" href="#main">Skip to content</a>
        <header className="topnav">
          <div className="topnav-in">
            <Logo to={home} compact />
            <nav className="navtabs" aria-label="Main">
              {nav.map((n) => (
                <NavLink key={n.to} to={n.to} end={n.end} className="navtab">
                  {n.label}{n.badge && role === "MANAGER" && badge}
                </NavLink>
              ))}
            </nav>
            <SearchBar role={role} />
            <div className="topnav-actions">
              <button className="btn primary create-btn" onClick={() => setSheet("create")}>
                <Icon name="plus" size={18} /> {role === "BROKER" ? "New" : "Create"}
              </button>
              <button className="icon-btn plain bell" aria-label={`Alerts${alerts.count ? `, ${alerts.count} waiting` : ""}`} onClick={() => setSheet("alerts")}>
                <Icon name="bell" size={21} />{alerts.count > 0 && <span className="dot-badge" aria-hidden="true">{alerts.count}</span>}
              </button>
              <button className="avatar-btn" aria-label="Your account" onClick={() => setSheet("me")}>
                <span className="avatar small" aria-hidden="true">{me?.name.slice(0, 1)}</span>
              </button>
            </div>
          </div>
        </header>

        <div id="main" className="shell-main">{children}</div>

        <nav className="tabbar" aria-label="Main">
          <NavLink to={phoneTabs[0].to} end className="tab"><Icon name={phoneTabs[0].icon} /><span>{phoneTabs[0].label}</span></NavLink>
          <NavLink to={phoneTabs[1].to} className="tab"><Icon name={phoneTabs[1].icon} /><span>{phoneTabs[1].label}</span></NavLink>
          <button className="tab tab-create" onClick={() => setSheet("create")} aria-label={role === "BROKER" ? "New listing" : "Create"}>
            <span className="plus"><Icon name="plus" /></span><span>New</span>
          </button>
          <button className="tab" onClick={() => setSheet("alerts")}>
            <span className="tab-ic"><Icon name="bell" />{alerts.count > 0 && <span className="dot-badge" aria-hidden="true">{alerts.count}</span>}</span>
            <span>{role === "MANAGER" ? "Reviews" : "Alerts"}{alerts.count > 0 && <span className="visually-hidden">, {alerts.count} waiting</span>}</span>
          </button>
          <button className="tab" onClick={() => setSheet("me")}><Icon name="user" /><span>Me</span></button>
        </nav>

        <CreateDialog role={role} open={sheet === "create"} onClose={close} />
        <AlertsDialog role={role} open={sheet === "alerts"} onClose={close} items={alerts.items} />
        <MeDialog open={sheet === "me"} onClose={close} links={moreLinks} />
        <ScrollManager />
      </div>
    </ShellContext.Provider>
  );
}

// ---------- layouts (route elements) ----------
export function BrokerLayout() {
  return <SignedInShell role="BROKER"><Outlet /></SignedInShell>;
}

export function ManagerLayout() {
  return <SignedInShell role="MANAGER"><main className="web-main"><Outlet /></main></SignedInShell>;
}

/** Tenants: no sign-in, no ledger, no "+", no bell. Logo and a theme switch; search only with the explore feed. */
export function PublicLayout() {
  const [theme, setTheme] = useState<ThemeChoice>(loadTheme);
  const dark = theme === "dark" || (theme === "system" && window.matchMedia?.("(prefers-color-scheme: dark)").matches);
  return (
    <div className="shell shell-public">
      <a className="skip" href="#main">Skip to content</a>
      <header className="topnav">
        <div className="topnav-in">
          <Logo to={EXPLORE_ENABLED ? "/explore" : undefined} />
          {EXPLORE_ENABLED ? <SearchBar role={null} /> : <span className="spacer" />}
          <button className="icon-btn plain" aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
            onClick={() => { const t: ThemeChoice = dark ? "light" : "dark"; setTheme(t); applyTheme(t); }}>
            <Icon name={dark ? "sun" : "moon"} size={20} />
          </button>
        </div>
      </header>
      <div id="main" className="shell-main"><Outlet /></div>
      <ScrollManager />
    </div>
  );
}
