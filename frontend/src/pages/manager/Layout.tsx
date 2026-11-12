import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { api } from "../../api";
import { useAuth } from "../../auth";
import { Icon, useLoad } from "../../components";
import type { ReviewItem } from "../../types";

/** `reviewCount` lets the review page keep the badge in step with its own (reloading) queue. */
export default function ManagerLayout({ children, reviewCount }: { children: ReactNode; reviewCount?: number }) {
  const { me, signOut } = useAuth();
  const reviews = useLoad(() => api.get<ReviewItem[]>("/manager/reviews"));
  const waiting = reviewCount ?? reviews.data?.length ?? 0;
  return (
    <div className="web">
      <nav aria-label="Agency" className="web-nav">
        <div className="brand">
          <span className="avatar" aria-hidden="true"><Icon name="shield" size={22} /></span>
          <div className="stack" style={{ gap: 0 }}>
            <strong style={{ fontFamily: "var(--display)", fontSize: 18 }}>PropLock</strong>
            <span className="tiny muted">{me?.name}</span>
          </div>
        </div>
        <NavLink to="/m" end className={({ isActive }) => (isActive ? "active" : "")}>
          <Icon name="grid" size={19} /> Flats &amp; mandates
        </NavLink>
        <NavLink to="/m/reviews" className={({ isActive }) => (isActive ? "active" : "")}>
          <Icon name="inbox" size={19} /> Review queue
          {waiting > 0 && <span className="pill solid count">{waiting}</span>}
        </NavLink>
        <NavLink to="/m/visits" className={({ isActive }) => (isActive ? "active" : "")}>
          <Icon name="calendar" size={19} /> Visit calendar
        </NavLink>
        <NavLink to="/m/ledger" className={({ isActive }) => (isActive ? "active" : "")}>
          <Icon name="ledger" size={19} /> Money ledger
        </NavLink>
        <NavLink to="/m/disputes" className={({ isActive }) => (isActive ? "active" : "")}>
          <Icon name="scale" size={19} /> Disputes
        </NavLink>
        <button className="btn ghost" style={{ justifyContent: "flex-start", marginTop: "auto" }} onClick={signOut}>
          <Icon name="out" size={19} /> Sign out
        </button>
      </nav>
      <main className="web-main">{children}</main>
    </div>
  );
}
