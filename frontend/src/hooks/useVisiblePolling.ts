import { useEffect, useRef } from "react";

/**
 * `setInterval` that only fires while the tab is visible.
 *
 * Several screens refresh themselves every few seconds. A tab left open in
 * the background all day therefore kept the API — and through it the
 * database — busy around the clock, which on the free hosting stack burns
 * the database's monthly compute allowance for nothing anybody sees.
 *
 * The callback is skipped while `document.hidden`; when the tab comes back
 * it runs once immediately so the screen is fresh, then resumes the cadence.
 *
 * Pass `null` as `intervalMs` to pause entirely.
 */
export function useVisiblePolling(callback: () => void, intervalMs: number | null) {
  const saved = useRef(callback);
  saved.current = callback;

  useEffect(() => {
    if (intervalMs === null) return;
    const tick = () => {
      if (typeof document !== "undefined" && document.hidden) return;
      saved.current();
    };
    const id = setInterval(tick, intervalMs);
    const onVisible = () => {
      if (typeof document !== "undefined" && !document.hidden) saved.current();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [intervalMs]);
}

export default useVisiblePolling;
