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
import BookVisit from "./pages/public/BookVisit";
import { BrokerVisits, ManagerVisits } from "./pages/visits/Pages";
import BrokerDoor from "./pages/escrow/BrokerDoor";
import { BrokerLedger, ManagerLedger } from "./pages/ledger/Pages";
import Disputes from "./pages/ledger/Disputes";
import type { Role } from "./types";
import { BrokerLayout, ManagerLayout, PublicLayout } from "./ui/shell/Shell";

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

      {/* Broker app: phone-first shell with the tab bar */}
      <Route path="/b" element={<RequireRole role="BROKER"><BrokerLayout /></RequireRole>}>
        <Route index element={<BrokerHome />} />
        <Route path="listings/:listingId" element={<ListingStatusPage />} />
        <Route path="listings/:listingId/checkin" element={<Checkin />} />
        <Route path="listings/:listingId/ad" element={<Draft />} />
        <Route path="audits/:auditId" element={<AuditResult />} />
        <Route path="ledger" element={<BrokerLedger />} />
        <Route path="visits" element={<BrokerVisits />} />
        <Route path="visits/:visitId" element={<BrokerDoor />} />
      </Route>

      {/* Agency manager: desktop-first shell, tab bar under 760 px */}
      <Route path="/m" element={<RequireRole role="MANAGER"><ManagerLayout /></RequireRole>}>
        <Route index element={<ManagerFlats />} />
        <Route path="reviews" element={<ManagerReviews />} />
        <Route path="visits" element={<ManagerVisits />} />
        <Route path="ledger" element={<ManagerLedger />} />
        <Route path="disputes" element={<Disputes />} />
      </Route>

      {/* Public: no login */}
      <Route element={<PublicLayout />}>
        <Route path="/l/:listingId" element={<PublicListing />} />
        <Route path="/l/:listingId/visit" element={<BookVisit />} />
        <Route path="/v/:auditId" element={<Verify />} />
      </Route>

      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
