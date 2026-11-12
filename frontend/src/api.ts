// Thin fetch wrapper for the PropLock API. Every call sends the stored API key; errors carry
// the backend's own message ("You have no active mandate for PROP-CHN-002") for the UI to show.

const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "/api";
const KEY_STORAGE = "proplock.apiKey";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

// localStorage can be missing or throw (private windows, blocked storage); the app then just
// asks for the key again on reload. A demo-grade choice: a real login would use a session cookie.
export function loadKey(): string | null {
  try {
    return window.localStorage.getItem(KEY_STORAGE);
  } catch {
    return null;
  }
}

export function saveKey(key: string | null): void {
  try {
    if (key) window.localStorage.setItem(KEY_STORAGE, key);
    else window.localStorage.removeItem(KEY_STORAGE);
  } catch {
    // storage unavailable: the key lives only in memory for this tab
  }
}

let memoryKey: string | null = loadKey();

export function setKey(key: string | null): void {
  memoryKey = key;
  saveKey(key);
}

export function apiUrl(path: string): string {
  return `${BASE}${path}`;
}

function messageFrom(body: unknown, status: number): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      // FastAPI validation errors: [{loc: [...], msg: "..."}]
      return detail
        .map((d: { loc?: unknown[]; msg?: string }) => `${(d.loc ?? []).slice(1).join(".")}: ${d.msg}`)
        .join("; ");
    }
  }
  return `Request failed (HTTP ${status})`;
}

async function request<T>(
  method: string, path: string, body?: unknown, form?: FormData, extra?: Record<string, string>,
): Promise<T> {
  const headers: Record<string, string> = { ...extra };
  if (memoryKey) headers["X-API-Key"] = memoryKey;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const resp = await fetch(apiUrl(path), {
    method,
    headers,
    body: form ?? (body !== undefined ? JSON.stringify(body) : undefined),
  });
  if (resp.status === 204) return undefined as T;
  const data = await resp.json().catch(() => null);
  if (!resp.ok) throw new ApiError(resp.status, messageFrom(data, resp.status));
  return data as T;
}

export const api = {
  // `headers` carries extras such as a tenant's X-Hold-Token (never put secrets in the URL).
  get: <T>(path: string, headers?: Record<string, string>) => request<T>("GET", path, undefined, undefined, headers),
  post: <T>(path: string, body?: unknown, headers?: Record<string, string>) =>
    request<T>("POST", path, body ?? {}, undefined, headers),
  upload: <T>(path: string, form: FormData, headers?: Record<string, string>) => request<T>("POST", path, undefined, form, headers),
  del: (path: string) => request<void>("DELETE", path),
};
