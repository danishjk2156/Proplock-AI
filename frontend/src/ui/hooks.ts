import { useEffect, useState } from "react";

/** Per-route document title: "<page> · PropLock". */
export function useTitle(title: string | null | undefined) {
  useEffect(() => {
    document.title = title ? `${title} · PropLock` : "PropLock";
  }, [title]);
}

/** Share a link: the Web Share API where the device has it, else copy to the clipboard. */
export function useShare() {
  const [status, setStatus] = useState<string | null>(null);
  useEffect(() => {
    if (!status) return;
    const t = window.setTimeout(() => setStatus(null), 3000);
    return () => window.clearTimeout(t);
  }, [status]);

  async function share(url: string, title: string) {
    try {
      if (navigator.share) {
        await navigator.share({ title, url });
        return;
      }
      await navigator.clipboard.writeText(url);
      setStatus("Link copied");
    } catch (e) {
      // the person closed the share sheet: nothing to report
      if (e instanceof DOMException && e.name === "AbortError") return;
      setStatus(`Couldn’t share. The link is ${url}`);
    }
  }
  return { share, status };
}

const ROOMS: Record<string, string> = {
  living: "Living room", bedroom: "Bedroom", kitchen: "Kitchen", bathroom: "Bathroom",
  balcony: "Balcony", exterior: "Outside", hall: "Hall", dining: "Dining area",
};
export function roomLabel(room: string): string {
  return ROOMS[room] ?? room.charAt(0).toUpperCase() + room.slice(1).replace(/_/g, " ");
}
