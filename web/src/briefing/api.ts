import { API_BASE } from "../config";
import type { Briefing, Health, HomeAirport, Settings } from "./types";

/**
 * Client for the `api/` service (FastAPI, `127.0.0.1:8000`, proxied at `/api`). Single
 * user, tailnet only, no auth. The briefing itself is never read through here: it is the
 * static file `/data/briefing.json`.
 */

/** A 422 from `PUT /api/settings`, unpacked into lines the settings form can show. */
export class ValidationError extends Error {
  constructor(readonly problems: string[]) {
    super(problems.join("\n") || "The service rejected these settings.");
    this.name = "ValidationError";
  }
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number | null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const TIMEOUT_MS = 8000;

async function request(path: string, init?: RequestInit): Promise<Response> {
  const ctrl = new AbortController();
  const timer = window.setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  try {
    return await fetch(`${API_BASE}${path}`, {
      ...init,
      signal: ctrl.signal,
      cache: "no-store",
      headers: { Accept: "application/json", ...(init?.headers ?? {}) },
    });
  } catch (err) {
    throw new ApiError(err instanceof Error ? err.message : "request failed", null);
  } finally {
    clearTimeout(timer);
  }
}

async function json<T>(res: Response): Promise<T> {
  try {
    return (await res.json()) as T;
  } catch {
    throw new ApiError("the service returned something that is not JSON", res.status);
  }
}

/**
 * `null` means "briefing service not reachable"; the caller hides settings and refresh
 * behind a notice and leaves the card showing the last briefing.
 */
export async function getHealth(): Promise<Health | null> {
  try {
    const res = await request("/health");
    if (!res.ok) return null;
    const body = await json<Health>(res);
    return body.ok === false ? null : body;
  } catch {
    return null;
  }
}

export async function getSettings(): Promise<Settings> {
  const res = await request("/settings");
  if (!res.ok) throw new ApiError(`settings unavailable (HTTP ${res.status})`, res.status);
  return json<Settings>(res);
}

/** The contract says PUT takes the full object; partial updates are not a thing. */
export async function putSettings(settings: Settings): Promise<Settings> {
  const res = await request("/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings),
  });
  if (res.status === 422) throw new ValidationError(await readProblems(res));
  if (!res.ok) throw new ApiError(`could not save settings (HTTP ${res.status})`, res.status);
  return json<Settings>(res);
}

/** `null` when the service does not know that identifier (404). */
export async function lookupAirport(ident: string): Promise<HomeAirport | null> {
  const res = await request(`/airports/${encodeURIComponent(ident.trim().toUpperCase())}`);
  if (res.status === 404) return null;
  if (!res.ok) throw new ApiError(`airport lookup failed (HTTP ${res.status})`, res.status);
  return json<HomeAirport>(res);
}

/** Runs the briefing now and returns the new `briefing.json` body. */
export async function refreshBriefing(): Promise<Briefing> {
  const res = await request("/briefing/refresh", { method: "POST" });
  if (!res.ok) throw new ApiError(`refresh failed (HTTP ${res.status})`, res.status);
  return json<Briefing>(res);
}

/**
 * FastAPI's 422 body is `{"detail": [{"loc": [...], "msg": "..."}]}`; a hand-rolled
 * validator may send `{"detail": "..."}` instead. Both are unpacked into plain lines, and
 * anything else falls back to a generic message rather than printing raw JSON at the user.
 */
async function readProblems(res: Response): Promise<string[]> {
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    return ["The service rejected these settings."];
  }
  const detail = (body as { detail?: unknown })?.detail;
  if (typeof detail === "string") return [detail];
  if (Array.isArray(detail)) {
    const lines = detail.map((item) => {
      if (typeof item === "string") return item;
      const entry = item as { loc?: unknown[]; msg?: unknown };
      const field = Array.isArray(entry.loc)
        ? entry.loc.filter((p) => p !== "body").join(".")
        : "";
      const msg = typeof entry.msg === "string" ? entry.msg : "is not valid";
      return field ? `${field}: ${msg}` : msg;
    });
    if (lines.length > 0) return lines;
  }
  return ["The service rejected these settings."];
}
