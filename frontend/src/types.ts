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
