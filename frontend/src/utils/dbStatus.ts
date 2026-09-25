/**
 * Shared "is the database reachable?" state.
 *
 * The API answers `503 {error_kind: "database", db: {kind, message, hint}}`
 * whenever Postgres itself is down (most often: the Neon free plan has
 * suspended the project for the month). The axios interceptor feeds those
 * responses in here and the `DbStatusBanner` shows a single, explained
 * notice instead of every page failing on its own.
 */

export interface DbOutage {
  kind: string;
  message: string;
  hint?: string | null;
  since: number;
}

type Listener = (outage: DbOutage | null) => void;

let current: DbOutage | null = null;
const listeners = new Set<Listener>();

function emit() {
  listeners.forEach((l) => l(current));
}

export function getDbOutage(): DbOutage | null {
  return current;
}

export function subscribeDbStatus(listener: Listener): () => void {
  listeners.add(listener);
  listener(current);
  return () => {
    listeners.delete(listener);
  };
}

/** Called by the API client for any 503 carrying database details. */
export function reportDbOutage(db: { kind?: string; message?: string; hint?: string | null }) {
  const kind = db?.kind || "unknown";
  const message = db?.message || "The database is not reachable.";
  if (current && current.kind === kind && current.message === message) return;
  current = { kind, message, hint: db?.hint ?? null, since: Date.now() };
  emit();
}

/** Called by the API client on any successful data response. */
export function reportDbOk() {
  if (!current) return;
  current = null;
  emit();
}

/** Extracts the database block from an axios error, if it is one. */
export function dbOutageFromError(err: any): DbOutage | null {
  const data = err?.response?.data;
  if (err?.response?.status !== 503 || !data) return null;
  // Regular routes: {error_kind: "database", db: {...}}
  if (data.error_kind === "database" && data.db) {
    return { kind: data.db.kind, message: data.db.message, hint: data.db.hint, since: Date.now() };
  }
  // GET /health/db: {ok: false, kind, message, hint}
  if (data.ok === false && data.kind) {
    return { kind: data.kind, message: data.message, hint: data.hint, since: Date.now() };
  }
  return null;
}

/** Short human title for a failure kind. */
export function dbOutageTitle(kind: string): string {
  switch (kind) {
    case "quota_exceeded":
      return "Database paused by Neon: monthly free quota used up";
    case "suspended":
      return "Database is suspended";
    case "auth_failed":
      return "Database rejected the credentials";
    case "timeout":
      return "Database is not answering";
    case "unreachable":
      return "Database cannot be reached";
    case "schema":
      return "Database schema is out of date";
    default:
      return "Database unavailable";
  }
}
