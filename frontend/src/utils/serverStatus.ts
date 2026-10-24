/**
 * "Is the API awake?" — shared state for cold starts.
 *
 * The app runs on Render's free plan, which stops the instance after ~15
 * minutes of no traffic. The next visitor does not see an error page from
 * Render; they see *nothing* for 30–60 seconds while the container boots, and
 * then every widget that fired a request at t=0 has already failed.
 *
 * That silence is the worst part of the experience — a payment link that looks
 * broken is worse still. So the API client reports what it saw here:
 *
 *   ok      a real answer arrived (any HTTP status at all proves the server is up)
 *   waking  no answer: network error, or a 502/503/504 from the edge while the
 *           instance is still booting (a 503 with a *database* body is a
 *           different problem and is handled by dbStatus)
 *   down    waking for longer than COLD_START_PATIENCE, so say so honestly
 *
 * The banner and the public invoice page subscribe to this, retry on their own,
 * and the notice disappears the moment a request succeeds.
 */

export type ServerState = "ok" | "waking" | "down";

export interface ServerStatus {
  state: ServerState;
  /** When the current non-ok state started. */
  since: number;
  /** Consecutive failed attempts since the last success. */
  attempts: number;
}

/** How long a cold start may take before the copy stops saying "waking up". */
export const COLD_START_PATIENCE = 75_000;

type Listener = (status: ServerStatus) => void;

let current: ServerStatus = { state: "ok", since: 0, attempts: 0 };
const listeners = new Set<Listener>();

function emit() {
  listeners.forEach((listener) => listener(current));
}

export function getServerStatus(): ServerStatus {
  return current;
}

export function subscribeServerStatus(listener: Listener): () => void {
  listeners.add(listener);
  listener(current);
  return () => {
    listeners.delete(listener);
  };
}

/** The API stopped answering (or the edge said the instance is not up yet). */
export function reportServerUnreachable() {
  const attempts = current.state === "ok" ? 1 : current.attempts + 1;
  const since = current.state === "ok" ? Date.now() : current.since;
  const state: ServerState =
    Date.now() - since > COLD_START_PATIENCE ? "down" : "waking";
  if (current.state === state && current.attempts === attempts) return;
  current = { state, since, attempts };
  emit();
}

/** Any HTTP response at all — even a 404 — proves the instance is awake. */
export function reportServerAwake() {
  if (current.state === "ok") {
    // Keep the attempt counter fresh for the next cold start without a repaint.
    current = { state: "ok", since: 0, attempts: 0 };
    return;
  }
  current = { state: "ok", since: 0, attempts: 0 };
  emit();
}

/**
 * True for the failures a cold start produces: no response at all, or one of
 * the gateway statuses Render's proxy answers while the service boots.
 */
export function looksLikeColdStart(error: any): boolean {
  if (!error) return false;
  const response = error.response;
  if (!response) {
    // No response: DNS/offline/edge timeout. Ignore an explicit abort of our
    // own making (page navigation) — that is not a sleeping server.
    return error.code !== "ERR_CANCELED";
  }
  const status = response.status;
  if (status === 502 || status === 503 || status === 504) {
    // A 503 that names the database is a database problem, not a cold start.
    const data = response.data;
    if (data?.error_kind === "database" || data?.db) return false;
    return true;
  }
  return false;
}
