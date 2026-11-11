// Shapes returned by the FastAPI backend. Money arrives as strings ("35000.00") so it stays exact.

export type Role = "MANAGER" | "BROKER";
export type Furnishing = "Unfurnished" | "Semi-Furnished" | "Fully Furnished";
export type CommuteMode = "walking" | "transit" | "driving";
export type ListingStatus =
  | "DRAFT" | "SUBMITTED" | "NEEDS_FIX" | "IN_REVIEW" | "VERIFIED" | "LIVE" | "EXPIRED" | "RENTED";
export type Verdict = "PASS" | "FAIL" | "UNVERIFIABLE";
export type Decision = "NEEDS_FIX" | "IN_REVIEW" | "VERIFIED";

export interface Me {
  id: string;
  name: string;
  role: Role;
  agency: string | null;
  trust_score: number;
}

export interface CommuteClaim {
  destination: string;
  minutes: number;
  mode: CommuteMode;
}

export interface ClaimedCommute extends CommuteClaim {
  source: "FORM" | "EXTRACTED";
}

export interface Evidence {
  field: string;
  claimed: string;
  allowed: string;
  ok: boolean | null;
  note: string;
  suggestion: string;
}

export interface CheckResult {
  check: "claims" | "photos" | "commute" | "compliance" | "ground_truth";
  verdict: Verdict;
  evidence: Evidence[];
  source: string;
  summary: string;
}

export interface Ad {
  title: string;
  description: string;
  rent: string;
  deposit: string;
  brokerage_fee: string;
  furnishing: Furnishing;
  commute_claims: CommuteClaim[];
}

export interface AuditReport {
  listing_id: string;
  property_id: string;
  version_no: number;
  decision: Decision;
  ruleset_version: string;
  checks: CheckResult[];
  commute_claims: ClaimedCommute[];
  text_furnishing: Furnishing | null;
  photos: { id: string; kind: "CHECKIN" | "EXTRA"; phash: string }[];
  terms: Omit<Ad, "commute_claims"> | null;
}

export interface AuditOut {
  id: string;
  status: string;
  created_at: string;
  signature: string | null;
  paypal_product_id: string | null;
  report: AuditReport;
}

export interface BrokerMandate {
  mandate_id: string;
  property_id: string;
  address: string;
  city: string;
  state: string;
  furnishing: Furnishing;
  latitude: string;
  longitude: string;
  min_rent: string;
  max_rent: string;
  max_deposit: string;
  max_brokerage_months: string;
  valid_until: string;
  listing_id: string | null;
  photo_url: string | null;
}

export interface BrokerListing {
  id: string;
  property_id: string;
  address: string;
  status: ListingStatus;
  version_no: number | null;
  audit_id: string | null;
  audit_status: string | null;
  problems: number;
  open_questions: number;
  photos: number;
  has_checkin: boolean;
  last_confirmed_at: string | null;
  expires_at: string | null;
  created_at: string;
  ad: Ad | null;
}

export interface Photo {
  id: string;
  kind: "CHECKIN" | "EXTRA";
  phash: string;
  latitude: string | null;
  longitude: string | null;
  gps_source: "device" | "exif" | null;
  captured_at: string | null;
}

export interface SubmitOut {
  audit_id: string;
  listing_status: ListingStatus;
  report: AuditReport;
}

export interface PrecheckOut {
  would_be: Decision;
  checks: CheckResult[];
  commute_claims: ClaimedCommute[];
  suggestions: string[];
  note: string;
}

export interface PublicListing {
  listing_id: string;
  property_id: string;
  city: string;
  title: string;
  description: string;
  rent: string;
  deposit: string;
  brokerage_fee: string;
  move_in_total: string;
  furnishing: Furnishing;
  commute: (CommuteClaim & { checked_at_rush_hour: boolean })[];
  advisories: string[];
  photos: { url: string; room_type: string }[];
  broker: { name: string; agency: string | null; licence_number: string | null; trust_score: number };
  audit_id: string;
  verify_url: string;
  paypal_product_id: string | null;
  demo_product: boolean;
  verified_at: string;
  last_confirmed_at: string | null;
  expires_at: string | null;
  version_no: number;
}

export interface VerifyOut {
  audit_id: string;
  status: string;
  paypal_product_id: string | null;
  signature_valid: boolean;
  matches_current_data: boolean;
  mismatches: string[];
}

export interface Property {
  id: string;
  owner_name: string;
  address: string;
  city: string;
  state: string;
  postal_code: string;
  latitude: string;
  longitude: string;
  furnishing: Furnishing;
  created_at: string;
}

export interface Broker {
  id: string;
  name: string;
  agency: string | null;
  licence_number: string | null;
  trust_score: number;
}

export interface Mandate {
  id: string;
  property_id: string;
  broker_id: string;
  min_rent: string;
  max_rent: string;
  max_deposit: string;
  max_brokerage_months: string;
  valid_until: string;
  status: "ACTIVE" | "REVOKED" | "EXPIRED";
  created_at: string;
}

export interface ReviewItem {
  audit_id: string;
  listing_id: string;
  property_id: string;
  broker_name: string;
  version_no: number;
  created_at: string;
  open_questions: string[];
}

// ---------- Feature 4: closings, invoices, ledger, disputes ----------
export type ClosingStatus = "OPEN" | "INVOICED" | "PAID" | "CANCELLED" | "DISPUTED";
export type InvoiceStatus = "PENDING" | "DRAFT" | "SENT" | "PAID" | "CANCELLED" | "REFUNDED";
export type DisputeStatus = "OPEN" | "APPROVED" | "SUBMITTED" | "RESOLVED";

export interface LedgerEntry {
  id: string;
  listing_id: string;
  closing_id: string | null;
  kind: string;
  amount: string;
  currency: string;
  direction: "in" | "out" | "info";
  paypal_ref: string | null;
  source: "feature3" | "webhook" | "api" | "manual";
  note: string | null;
  occurred_at: string;
}

export interface InvoiceOut {
  id: string;
  number: string;
  paypal_invoice_id: string | null;
  status: InvoiceStatus;
  deposit: string;
  brokerage: string;
  token_deduction: string;
  total: string;
  currency: string;
  due_date: string;
  sent_at: string | null;
  paid_at: string | null;
  reminders_sent: number;
  payer_view_url: string | null;
  override_note: string | null;
  signature_valid: boolean;
  demo: boolean;
}

export interface ClosingSummary {
  id: string;
  listing_id: string;
  property_id: string;
  title: string;
  broker_name: string;
  tenant_name: string;
  tenant_email: string;
  capture_ref: string;
  audit_id: string;
  status: ClosingStatus;
  created_at: string;
  currency: string;
  gross: string;
  received: string;
  outstanding: string;
  invoice: InvoiceOut | null;
  dispute_status: DisputeStatus | null;
  entry_count: number;
}

export interface ClosingDetail extends ClosingSummary {
  entries: LedgerEntry[];
}

export interface LedgerOut {
  closings: ClosingSummary[];
  entries: LedgerEntry[];
  totals: Record<string, string>;
}

export interface Hold {
  capture_ref: string;
  amount: string;
  currency: string;
  occurred_at: string;
}

export interface ClosableListing {
  listing_id: string;
  property_id: string;
  title: string;
  broker_name: string;
  deposit: string;
  brokerage: string;
  audit_id: string;
  holds: Hold[];
}

export interface CopilotOut {
  answer: string;
  refused: boolean;
  query: Record<string, unknown> | null;
  target: "closings" | "entries" | null;
  rows: Record<string, unknown>[];
  source: string;
}

export interface EvidenceItem {
  source_id: string;
  kind: string;
  summary: string;
  facts: Record<string, unknown>;
}

export interface DisputeOut {
  id: string;
  paypal_dispute_id: string;
  closing_id: string | null;
  reason: string;
  amount: string;
  currency: string;
  status: DisputeStatus;
  evidence_pack: { items: EvidenceItem[]; missing: string[] } | null;
  draft_response: string | null;
  draft_source: string | null;
  final_response: string | null;
  approved_by: string | null;
  submitted_at: string | null;
  created_at: string;
}
