import { api } from "../../api";
import type { BrokerListing, BrokerMandate } from "../../types";

export type { BrokerListing, BrokerMandate } from "../../types";

export interface ListingOutLite {
  id: string;
  property_id: string;
  status: string;
}

/** The listing plus the mandate (owner's limits and pin) it was opened under. */
export async function loadListingWithMandate(listingId: string): Promise<{ listing: BrokerListing; mandate: BrokerMandate | null }> {
  const [listing, mandates] = await Promise.all([
    api.get<BrokerListing>(`/broker/listings/${listingId}`),
    api.get<BrokerMandate[]>("/broker/mandates"),
  ]);
  const mandate = mandates.find((m) => m.property_id === listing.property_id) ?? null;
  return { listing, mandate };
}

/**
 * "Start a listing" from a mandate: reopen the broker's draft or needs-fix listing for that flat if
 * there is one, else create a listing and go to its check-in. Returns the path to open.
 * Used by the home screen's dashed pins and by the "+ New" create menu.
 */
export async function openOrStartListing(m: BrokerMandate, listings: BrokerListing[]): Promise<string> {
  const open = listings.find((l) => l.id === m.listing_id && ["DRAFT", "NEEDS_FIX"].includes(l.status));
  if (open) return `/b/listings/${open.id}`;
  const created = await api.post<ListingOutLite>("/broker/listings", { property_id: m.property_id });
  return `/b/listings/${created.id}/checkin`;
}
