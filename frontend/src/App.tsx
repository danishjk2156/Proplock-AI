import type { ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import { Loading, NotFound } from "./components";
import Login from "./pages/Login";
import BrokerHome from "./pages/broker/Home";
import Checkin from "./pages/broker/Checkin";
import Draft from "./pages/broker/Draft";
import AuditResult from "./pages/broker/AuditResult";
import ListingStatusPage from "./pages/broker/ListingStatus";
import ManagerFlats from "./pages/manager/Flats";
import ManagerReviews from "./pages/manager/Reviews";
import PublicListing from "./pages/public/PublicListing";
import Verify from "./pages/public/Verify";
import { BrokerLedger, ManagerLedger } from "./pages/ledger/Pages";
import Disputes from "./pages/ledger/Disputes";
import type { Role } from "./types";

function RequireRole({ role, children }: { role: Role; children: ReactNode }) {
  const { me, loading } = useAuth();
  if (loading) return <div className="content"><Loading /></div>;
  if (!me) return <Navigate to="/" replace />;
  if (me.role !== role) return <Navigate to={me.role === "MANAGER" ? "/m" : "/b"} replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Login />} />

      {/* Broker app */}
      <Route path="/b" element={<RequireRole role="BROKER"><BrokerHome /></RequireRole>} />
      <Route path="/b/listings/:listingId" element={<RequireRole role="BROKER"><ListingStatusPage /></RequireRole>} />
      <Route path="/b/listings/:listingId/checkin" element={<RequireRole role="BROKER"><Checkin /></RequireRole>} />
      <Route path="/b/listings/:listingId/ad" element={<RequireRole role="BROKER"><Draft /></RequireRole>} />
      <Route path="/b/audits/:auditId" element={<RequireRole role="BROKER"><AuditResult /></RequireRole>} />
      <Route path="/b/ledger" element={<RequireRole role="BROKER"><BrokerLedger /></RequireRole>} />

      {/* Agency manager */}
      <Route path="/m" element={<RequireRole role="MANAGER"><ManagerFlats /></RequireRole>} />
      <Route path="/m/reviews" element={<RequireRole role="MANAGER"><ManagerReviews /></RequireRole>} />
      <Route path="/m/ledger" element={<RequireRole role="MANAGER"><ManagerLedger /></RequireRole>} />
      <Route path="/m/disputes" element={<RequireRole role="MANAGER"><Disputes /></RequireRole>} />

      {/* Public: no login */}
      <Route path="/l/:listingId" element={<PublicListing />} />
      <Route path="/v/:auditId" element={<Verify />} />

      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
