import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { api } from "../../api";
import { useAuth } from "../../auth";
import { useLoad } from "../../components";
import type { ReviewItem } from "../../types";

/** `reviewCount` lets the review page keep the badge in step with its own (reloading) queue. */
export default function ManagerLayout({ children, reviewCount }: { children: ReactNode; reviewCount?: number }) {
  const { me, signOut } = useAuth();
  const reviews = useLoad(() => api.get<ReviewItem[]>("/manager/reviews"));
  const waiting = reviewCount ?? reviews.data?.length ?? 0;
  return (
    <div className="web">
      <nav aria-label="Agency" className="web-nav">
        <div className="stack" style={{ gap: 2, padding: "0 12px 16px" }}>
          <strong style={{ fontSize: 18 }}>PropLock</strong>
          <span className="small muted">{me?.name}</span>
        </div>
        <NavLink to="/m" end className={({ isActive }) => (isActive ? "active" : "")}>Flats &amp; mandates</NavLink>
        <NavLink to="/m/reviews" className={({ isActive }) => (isActive ? "active" : "")}>
          Review queue
          {waiting > 0 && <span className="pill solid">{waiting}</span>}
        </NavLink>
        <button className="btn ghost" style={{ justifyContent: "flex-start", marginTop: "auto" }} onClick={signOut}>Sign out</button>
      </nav>
      <main className="web-main">{children}</main>
    </div>
  );
}
