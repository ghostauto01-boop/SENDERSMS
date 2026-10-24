import { useEffect } from "react";
import api from "../api/client";

/**
 * Keeps a sleeping instance awake while somebody is actually using the app.
 *
 * Render's free plan stops a service after ~15 minutes without traffic. A
 * dashboard tab that sits open (the normal way an operator works) used to go
 * quiet the moment they stopped clicking, so the *next* click paid the 30–60 s
 * boot cost. One tiny `/health` request every four minutes while the tab is
 * visible costs nothing (the endpoint is side-effect free by design) and keeps
 * send windows, pollers and the next click instant.
 *
 * It deliberately stops when the tab is hidden: a forgotten background tab must
 * not hold the instance open all night, and the visibility listener re-arms it
 * the moment the user comes back.
 */
const INTERVAL_MS = 4 * 60 * 1000;

export default function useServerKeepAlive() {
  useEffect(() => {
    let timer: number | undefined;

    const ping = () => {
      if (document.hidden) return;
      // Failures are reported by the client and shown by ServerWakeBanner.
      api.get("/health", { timeout: 20_000 }).catch(() => {});
    };

    const start = () => {
      window.clearInterval(timer);
      ping();
      timer = window.setInterval(ping, INTERVAL_MS);
    };

    const onVisibility = () => {
      if (document.hidden) {
        window.clearInterval(timer);
      } else {
        start();
      }
    };

    start();
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("online", start);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("online", start);
    };
  }, []);
}
