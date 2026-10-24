import axios from "axios";
import { dbOutageFromError, reportDbOk, reportDbOutage } from "../utils/dbStatus";
import {
  looksLikeColdStart,
  reportServerAwake,
  reportServerUnreachable,
} from "../utils/serverStatus";

const api = axios.create({
  baseURL: "/api/v1",
  withCredentials: true,
  headers: { "Content-Type": "application/json" },
  // Render's free instance stops after ~15 idle minutes and takes 30–60 s to
  // boot. Without a timeout the browser gives up first and every request looks
  // like a hard failure instead of a slow start.
  timeout: 60_000,
});

/**
 * FastAPI returns a *string* detail for HTTPException but an *array of error
 * objects* for 422 request-validation failures. Pages all render
 * `err.response.data.detail` directly, which turns the array into
 * "[object Object]". Flatten it here so every caller gets a readable string.
 */
const normaliseDetail = (data: any) => {
  const d = data?.detail;
  if (!Array.isArray(d)) return;
  const msg = d
    .map((e: any) => {
      const field = Array.isArray(e?.loc)
        ? e.loc.filter((p: any) => p !== "body").join(".")
        : "";
      const text = e?.msg || "Invalid value";
      return field ? `${field}: ${text}` : text;
    })
    .join("; ");
  if (msg) data.detail = msg;
};

/**
 * How long to wait before replaying a request that was refused with 429.
 *
 * The API's global ceiling is deliberately generous (600 requests/minute), but
 * a burst — a dashboard firing several calls at once, then a navigation before
 * they settle — can still trip it. Retrying once, after the server's own
 * `Retry-After`, turns what used to be a visible "could not load" into a
 * half-second pause. It never retries a non-idempotent call that might have
 * been applied, and never loops.
 */
const RATE_LIMIT_MAX_RETRIES = 1;
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Cold-start retries.
 *
 * A sleeping instance answers nothing for up to a minute. Retrying a GET a few
 * times, with the gaps growing, turns "the site is down" into "it took 20
 * seconds" — which is exactly what people reported when opening a payment link.
 * Sends (POST/PUT/PATCH/DELETE) are never replayed here: a retry that might
 * charge somebody twice is worse than an error message.
 */
const COLD_START_DELAYS = [1_500, 4_000, 9_000];

const retryAfterMs = (error: any): number => {
  const raw = error?.response?.headers?.["retry-after"];
  const seconds = Number(raw);
  if (Number.isFinite(seconds) && seconds > 0) return Math.min(seconds, 5) * 1000;
  return 800;
};

api.interceptors.response.use(
  async (response) => {
    // Any real answer from the API means both the server and the database are
    // back (a 4xx still came from a live server).
    reportServerAwake();
    if (!String(response.config?.url || "").startsWith("/health")) {
      reportDbOk();
    }
    return response;
  },
  async (error) => {
    if (error.response?.data) {
      normaliseDetail(error.response.data);
    }

    // The instance is asleep (or still booting). Say so, and replay idempotent
    // reads while it wakes up.
    if (looksLikeColdStart(error)) {
      reportServerUnreachable();
      const cfg = error.config || {};
      const method = (cfg.method || "get").toLowerCase();
      const attempts = cfg.__coldStartRetries || 0;
      if (method === "get" && attempts < COLD_START_DELAYS.length) {
        cfg.__coldStartRetries = attempts + 1;
        await sleep(COLD_START_DELAYS[attempts]);
        return api.request(cfg);
      }
    }

    // 429: wait for the window the server named and replay once. Only GETs are
    // replayed — repeating a send because of a throttle is not something the
    // user asked for.
    const config = error.config || {};
    if (
      error.response?.status === 429 &&
      (config.method || "get").toLowerCase() === "get" &&
      (config.__rateLimitRetries || 0) < RATE_LIMIT_MAX_RETRIES
    ) {
      config.__rateLimitRetries = (config.__rateLimitRetries || 0) + 1;
      await sleep(retryAfterMs(error));
      return api.request(config);
    }

    // 503 + error_kind "database": Postgres itself is down (e.g. the Neon
    // free plan suspended the project). Tell the banner; pages still get
    // the rejection so their own loading states settle.
    const outage = dbOutageFromError(error);
    if (outage) {
      reportDbOutage(outage);
    } else if (
      error.response &&
      error.response.status < 500 &&
      error.response.status !== 401
    ) {
      // A 4xx (other than a cookie-less 401, which never reaches the
      // database) proves the API and its database answered.
      reportDbOk();
    }
    if (error.response?.status === 401) {
      const currentPath = window.location.pathname;
      if (currentPath !== "/login") {
        window.location.href = "/login";
      }
    }
    return Promise.reject(error);
  }
);

export default api;
