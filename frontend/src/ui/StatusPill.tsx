import type { ListingStatus } from "../types";
import { Icon } from "./Icon";

// Status language (plan F8): shape + colour + icon, never colour alone. Same map as before the redesign.
const PILL: Record<string, { label: string; style: string }> = {
  LIVE: { label: "LIVE", style: "solid" },
  VERIFIED: { label: "VERIFIED", style: "solid" },
  NEEDS_FIX: { label: "NEEDS FIX", style: "outline" },
  REJECTED: { label: "SENT BACK", style: "outline" },
  IN_REVIEW: { label: "IN REVIEW", style: "dashed" },
  PENDING_MINT: { label: "PUBLISHING", style: "dashed" },
  DRAFT: { label: "DRAFT", style: "grey" },
  EXPIRED: { label: "EXPIRED", style: "grey" },
  RENTED: { label: "RENTED", style: "grey" },
  PASS: { label: "PASS", style: "thin" },
  FAIL: { label: "FAIL", style: "ink" },
  UNVERIFIABLE: { label: "CAN’T CHECK", style: "dashed" },
  // Feature 4: closings, invoices, disputes
  OPEN: { label: "OPEN", style: "grey" },
  INVOICED: { label: "INVOICED", style: "dashed" },
  PENDING: { label: "QUEUED", style: "grey" },
  SENT: { label: "SENT · UNPAID", style: "dashed" },
  PAID: { label: "PAID", style: "solid" },
  CANCELLED: { label: "CANCELLED", style: "grey" },
  REFUNDED: { label: "REFUNDED", style: "outline" },
  DISPUTED: { label: "DISPUTED", style: "ink" },
  APPROVED: { label: "APPROVED", style: "thin" },
  SUBMITTED: { label: "EVIDENCE SENT", style: "thin" },
  RESOLVED: { label: "RESOLVED", style: "solid" },
  // Feature 2: visits (EXPIRED is shared with listings)
  SOFT_HOLD: { label: "HELD", style: "dashed" },
  COMMITTED: { label: "BOOKED", style: "solid" },
  RELEASED: { label: "LET GO", style: "grey" },
  // Feature 3: the PayPal hold after the referee decided
  SETTLING: { label: "SETTLING", style: "dashed" },
  CAPTURED: { label: "FEE CAPTURED", style: "ink" },
  VOIDED: { label: "HOLD RELEASED", style: "thin" },
  PASSED: { label: "VISITED · PASSED", style: "thin" },
  LOCKED: { label: "LOCKED", style: "solid" },
  TENANT_NO_SHOW: { label: "TENANT NO-SHOW", style: "ink" },
  BROKER_NO_SHOW: { label: "BROKER NO-SHOW", style: "outline" },
  NO_PROOF: { label: "NO PROOF", style: "grey" },
  TOO_LATE: { label: "APPROVED TOO LATE", style: "grey" },
};
const PILL_ICON: Record<string, string> = { LIVE: "check", VERIFIED: "check", PASS: "check", FAIL: "x", PAID: "check", RESOLVED: "check", DISPUTED: "warn", COMMITTED: "check", LOCKED: "check", BROKER_NO_SHOW: "warn" };

export function StatusPill({ status }: { status: ListingStatus | string }) {
  const p = PILL[status] ?? { label: status, style: "grey" };
  const icon = PILL_ICON[status];
  return <span className={`pill ${p.style}`}>{icon && <Icon name={icon} size={12} />}{p.label}</span>;
}
