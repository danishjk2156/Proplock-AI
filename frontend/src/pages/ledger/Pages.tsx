import { Link } from "react-router-dom";
import { AppBar } from "../../components";
import ManagerLayout from "../manager/Layout";
import Cockpit from "./Cockpit";

export function ManagerLedger() {
  return (
    <ManagerLayout>
      <Cockpit isManager />
    </ManagerLayout>
  );
}

/** Brokers get the same cockpit, wider than their phone screens, with their own closings only. */
export function BrokerLedger() {
  return (
    <div className="wide">
      <AppBar title="My money ledger" subtitle="Tokens, invoices and payments for your closings" back="/b"
        right={<Link className="btn small" to="/b">Listings</Link>} />
      <main className="content">
        <Cockpit isManager={false} />
      </main>
    </div>
  );
}
