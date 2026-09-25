import { useEffect, useState } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";
import api from "../api/client";
import {
  DbOutage,
  dbOutageFromError,
  dbOutageTitle,
  reportDbOk,
  reportDbOutage,
  subscribeDbStatus,
} from "../utils/dbStatus";

/**
 * One sticky, explained notice for "the database is down", shown above
 * every page (including the login screen). Replaces the old experience of
 * every widget failing separately with a blank "Internal Server Error".
 *
 * It re-checks `/health/db` every 60 s (and on demand) and disappears on the
 * first successful answer.
 */
export default function DbStatusBanner() {
  const [outage, setOutage] = useState<DbOutage | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkedAt, setCheckedAt] = useState<number | null>(null);

  useEffect(() => subscribeDbStatus(setOutage), []);

  const recheck = async () => {
    if (checking) return;
    setChecking(true);
    try {
      await api.get("/health/db");
      reportDbOk();
    } catch (err: any) {
      const o = dbOutageFromError(err);
      if (o) reportDbOutage(o);
    } finally {
      setChecking(false);
      setCheckedAt(Date.now());
    }
  };

  useEffect(() => {
    if (!outage) return;
    const t = setInterval(() => {
      if (typeof document === "undefined" || !document.hidden) recheck();
    }, 60_000);
    return () => clearInterval(t);
  }, [outage?.kind]);

  if (!outage) return null;

  const isQuota = outage.kind === "quota_exceeded";

  return (
    <div
      role="alert"
      data-testid="db-status-banner"
      className="sticky top-0 z-[60] border-b border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-100"
    >
      <div className="mx-auto flex max-w-6xl items-start gap-3 px-4 py-3 text-sm">
        <AlertTriangle className="mt-0.5 h-5 w-5 flex-shrink-0" />
        <div className="min-w-0 flex-1">
          <p className="font-semibold">{dbOutageTitle(outage.kind)}</p>
          <p className="mt-0.5 break-words opacity-90">{outage.message}</p>
          {outage.hint && <p className="mt-1 opacity-90">{outage.hint}</p>}
          {isQuota && (
            <p className="mt-1 text-xs opacity-80">
              Nothing has been lost: contacts, conversations and campaigns are stored safely and
              reappear as soon as the database is switched back on. Scheduled sends resume then too.
            </p>
          )}
          {checkedAt && (
            <p className="mt-1 text-xs opacity-70">
              Last checked {new Date(checkedAt).toLocaleTimeString()}
            </p>
          )}
        </div>
        <button
          type="button"
          onClick={recheck}
          disabled={checking}
          className="inline-flex flex-shrink-0 items-center gap-1 rounded-md border border-amber-400 bg-white px-3 py-1.5 text-xs font-medium text-amber-900 hover:bg-amber-100 disabled:opacity-60 dark:bg-amber-900 dark:text-amber-50 dark:hover:bg-amber-800"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${checking ? "animate-spin" : ""}`} />
          {checking ? "Checking…" : "Check again"}
        </button>
      </div>
    </div>
  );
}
