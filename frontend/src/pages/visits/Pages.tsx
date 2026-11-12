import { Link } from "react-router-dom";
import { AppBar } from "../../components";
import ManagerLayout from "../manager/Layout";
import VisitCalendar from "./Calendar";

export function ManagerVisits() {
  return (
    <ManagerLayout>
      <div className="stack" style={{ gap: 4 }}>
        <h1>Visit calendar</h1>
        <p className="muted small">Every broker’s held and booked site visits, live.</p>
      </div>
      <VisitCalendar isManager />
    </ManagerLayout>
  );
}

export function BrokerVisits() {
  return (
    <div className="wide">
      <AppBar title="My visits" subtitle="Tenants who booked a visit to your listings" back="/b"
        right={<Link className="btn small" to="/b">Listings</Link>} />
      <main className="content">
        <VisitCalendar isManager={false} />
      </main>
    </div>
  );
}
