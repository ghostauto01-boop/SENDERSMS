import { useEffect, useState } from "react";
import { Coffee, RefreshCw, WifiOff } from "lucide-react";
import api from "../api/client";
import {
  COLD_START_PATIENCE,
  ServerStatus,
  getServerStatus,
  subscribeServerStatus,
} from "../utils/serverStatus";

/**
 * "The server is waking up" — one honest little notice instead of a dead page.
 *
 * Render's free instance sleeps after ~15 idle minutes. Booting takes 30–60 s,
 * during which nothing answers. This banner counts the seconds out loud, keeps
 * poking `/health` every few seconds, and removes itself the moment the API
 * answers — so a customer opening a payment link knows to wait rather than to
 * phone you and say the site is broken.
 *
 * `silent` renders nothing on success (the normal case) and is what the app
 * shell mounts. `inline` is the same notice without the sticky chrome, used by
 * pages that own the whole screen (the public invoice).
 */
export default function ServerWakeBanner({
  inline = false,
}: {
  inline?: boolean;
}) {
  const [status, setStatus] = useState<ServerStatus>(getServerStatus);
  const [now, setNow] = useState(Date.now());
  const [checking, setChecking] = useState(false);

  useEffect(() => subscribeServerStatus(setStatus), []);

  // A one-second clock only while it matters.
  useEffect(() => {
    if (status.state === "ok") return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [status.state]);

  // Poke the server on our own, so the banner clears without the user
  // reloading (and so the boot is triggered at all if the first request died).
  useEffect(() => {
    if (status.state === "ok") return;
    let cancelled = false;
    const tick = async () => {
      if (typeof document !== "undefined" && document.hidden) return;
      try {
        await api.get("/health", { timeout: 20_000 });
      } catch {
        /* still booting — the client records the failure itself */
      } finally {
        if (!cancelled) setNow(Date.now());
      }
    };
    const timer = window.setInterval(tick, 5_000);
    void tick();
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [status.state]);

  if (status.state === "ok") return null;

  const elapsed = Math.max(0, Math.round((now - status.since) / 1000));
  const down = status.state === "down" || elapsed * 1000 > COLD_START_PATIENCE;

  const recheck = async () => {
    if (checking) return;
    setChecking(true);
    try {
      await api.get("/health", { timeout: 20_000 });
    } catch {
      /* reported by the client */
    } finally {
      setChecking(false);
      setNow(Date.now());
    }
  };

  const body = (
    <div
      role="status"
      aria-live="polite"
      data-testid="server-wake-banner"
      className={`flex items-start gap-3 text-sm ${
        inline ? "" : "mx-auto max-w-6xl px-4 py-2.5"
      }`}
    >
      {down ? (
        <WifiOff className="mt-0.5 h-4 w-4 flex-shrink-0" />
      ) : (
        <Coffee className="mt-0.5 h-4 w-4 flex-shrink-0 animate-pulse" />
      )}
      <div className="min-w-0 flex-1">
        <p className="font-semibold">
          {down ? "The server is not answering yet" : "Waking the server up…"}
        </p>
        <p className="mt-0.5 break-words opacity-90">
          {down
            ? "It has been more than a minute. Your data is safe — keep this page open, it reconnects by itself."
            : "The app sleeps when nobody uses it for a while. This first request takes about 30–60 seconds — no need to refresh."}
          {elapsed > 0 && <span className="opacity-70"> ({elapsed}s)</span>}
        </p>
      </div>
      <button
        type="button"
        onClick={recheck}
        disabled={checking}
        className="inline-flex flex-shrink-0 items-center gap-1 rounded-md border px-3 py-1.5 text-xs font-medium disabled:opacity-60"
      >
        <RefreshCw className={`h-3.5 w-3.5 ${checking ? "animate-spin" : ""}`} />
        {checking ? "Checking…" : "Check now"}
      </button>
    </div>
  );

  if (inline) return body;

  return (
    <div className="sticky top-0 z-[65] border-b border-primary-200 bg-primary-50 text-primary-900 dark:border-primary-800 dark:bg-primary-950 dark:text-primary-100">
      {body}
    </div>
  );
}
