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
