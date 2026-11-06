// Display helpers. Indian digit grouping (1,40,000) because the listings are in India.

const inr = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

export function rupees(value: string | number): string {
  return `₹${inr.format(Number(value))}`;
}

export function plain(value: string | number): string {
  return inr.format(Number(value));
}

export function shortDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
}

export function daysUntil(iso: string | null): number | null {
  if (!iso) return null;
  return Math.ceil((new Date(iso).getTime() - Date.now()) / 86_400_000);
}

export function daysAgo(iso: string | null): string {
  if (!iso) return "never";
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  return days <= 0 ? "today" : days === 1 ? "yesterday" : `${days} days ago`;
}

/** Distance in metres along the Earth's surface (the backend uses the same formula). */
export function metresBetween(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const rad = (d: number) => (d * Math.PI) / 180;
  const dp = rad(lat2 - lat1);
  const dl = rad(lon2 - lon1);
  const h = Math.sin(dp / 2) ** 2 + Math.cos(rad(lat1)) * Math.cos(rad(lat2)) * Math.sin(dl / 2) ** 2;
  return 2 * 6_371_000 * Math.asin(Math.sqrt(h));
}

export const CHECK_LABELS: Record<string, string> = {
  ground_truth: "D · Owner’s terms",
  commute: "B · Commute at rush hour",
  compliance: "C · Deposit rules",
  photos: "A · Photos",
  claims: "Ad text read",
};

export const CHECK_ORDER = ["ground_truth", "commute", "compliance", "photos", "claims"];

const FIELD_LABELS: Record<string, string> = {
  rent: "Rent",
  deposit: "Deposit",
  furnishing: "Furnishing",
  brokerage_fee: "Brokerage",
  furnishing_in_text: "Furnishing in the ad text",
  deposit_months: "Deposit in months",
  "deposit_months (advisory)": "Deposit in months (advisory)",
};

/** Evidence field names are the backend's; show people words ("commute: X (walking)" → "X (walking)"). */
export function fieldLabel(field: string): string {
  return FIELD_LABELS[field] ?? field.replace(/^commute: /, "").replace(/^photo (\d+) /, "Photo $1 ");
}

/** "40000.00" → "40,000"; leaves text that isn't a plain amount alone. */
export function readable(value: string): string {
  return value.replace(/\b\d{4,}(?:\.\d{2})?\b/g, (n) => plain(n));
}

export const MODE_WORDS: Record<string, string> = { walking: "walk", driving: "drive", transit: "by transit" };
