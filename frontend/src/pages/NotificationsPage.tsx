import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Bell, BellOff, CheckCheck, Loader2, Send } from "lucide-react";
import toast from "react-hot-toast";
import api from "../api/client";
import { disablePush, enablePush, pushState, sendTestPush, type PushState } from "../utils/push";
import { notificationDisplay, relativeTime } from "../utils/notificationDisplay";

interface Item {
  id: number;
  event_type: string;
  title: string;
  body: string;
  status: string;
  is_read: boolean;
  url: string | null;
  created_at: string | null;
}

/**
 * The notification centre.
 *
 * Rows are grouped by day — today, yesterday, earlier — because "did this
 * arrive while I was on the phone?" is the question you actually have. Each
 * row shows the same sender/preview pair as the bell and the phone push, so a
 * notification looks identical wherever it is read.
 */
export default function NotificationsPage() {
  const [items, setItems] = useState<Item[]>([]);
  const [unread, setUnread] = useState(0);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [loading, setLoading] = useState(true);
  const [state, setState] = useState<PushState>("unknown");
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const PER = 20;

  const load = async (p = page, uo = unreadOnly) => {
    setLoading(true);
    try {
      const { data } = await api.get("/notifications/", {
        params: { page: p, per_page: PER, unread_only: uo },
      });
      setItems(data.items ?? []);
      setUnread(data.unread ?? 0);
      setTotal(data.total ?? 0);
    } catch {
      toast.error("Could not load notifications");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    pushState().then(setState).catch(() => {});
    load(1, false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggleFilter = () => {
    const next = !unreadOnly;
    setUnreadOnly(next);
    setPage(1);
    load(1, next);
  };

  const openItem = async (it: Item, href: string | null) => {
    if (!it.is_read) {
      try {
        await api.post(`/notifications/${it.id}/read`);
      } catch {
        /* ignore */
      }
    }
    if (href) navigate(href);
    else load();
  };

  const markAll = async () => {
    try {
      await api.post("/notifications/read-all");
      load();
    } catch {
      toast.error("Could not mark all as read");
    }
  };

  const togglePush = async () => {
    setBusy(true);
    try {
      if (state === "on") {
        setState(await disablePush());
        toast.success("Browser notifications turned off on this device");
      } else {
        const s = await enablePush();
        setState(s);
        if (s === "on") toast.success("Notifications enabled on this device 🔔");
        else if (s === "blocked")
          toast.error("Permission blocked — allow notifications in your browser settings, then retry.");
        else toast.error("Could not enable notifications on this browser.");
      }
    } finally {
      setBusy(false);
    }
  };

  const sendTest = async () => {
    setBusy(true);
    try {
      const r = await sendTestPush();
      toast.success(r.note);
      load(1, unreadOnly);
    } catch {
      toast.error("Test notification failed");
    } finally {
      setBusy(false);
    }
  };

  const pages = Math.max(1, Math.ceil(total / PER));

  /** Group rows under Today / Yesterday / Earlier, preserving API order. */
  const groups = (() => {
    const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
    const today = startOfDay(new Date());
    const buckets: { label: string; rows: Item[] }[] = [];
    for (const item of items) {
      const stamp = item.created_at ? new Date(item.created_at) : null;
      const day = stamp ? startOfDay(stamp) : today;
      const label =
        day >= today ? "Today" : day >= today - 86400000 ? "Yesterday" : "Earlier";
      const bucket = buckets.find((b) => b.label === label);
      if (bucket) bucket.rows.push(item);
      else buckets.push({ label, rows: [item] });
    }
    return buckets;
  })();

  return (
    <div className="max-w-3xl mx-auto space-y-4 pb-tabbar">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="page-title">Notifications</h1>
          <p className="page-subtitle">
            {unread > 0 ? `${unread} unread` : "You are all caught up"}
            {total ? ` · ${total} in total` : ""}
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={toggleFilter}
            className={unreadOnly ? "btn-primary btn-sm" : "btn-secondary btn-sm"}
          >
            {unreadOnly ? "Showing unread" : "Unread only"}
          </button>
          {unread > 0 && (
            <button onClick={markAll} className="btn-secondary btn-sm">
              <CheckCheck size={14} /> Mark all read
            </button>
          )}
        </div>
      </div>

      <div className="card p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3 min-w-0">
            <span
              className={`w-10 h-10 rounded-full flex items-center justify-center shrink-0 ${
                state === "on"
                  ? "bg-success-50 text-success-600 dark:bg-success-950/60 dark:text-success-300"
                  : "bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400"
              }`}
            >
              {state === "on" ? <Bell size={18} /> : <BellOff size={18} />}
            </span>
            <div className="min-w-0">
              <p className="font-semibold text-sm text-gray-900 dark:text-white">
                {state === "on" && "Push alerts ON for this browser"}
                {state === "off" && "Push alerts OFF for this browser"}
                {state === "blocked" && "Push alerts BLOCKED by browser"}
                {state === "unsupported" && "Push not supported by this browser"}
                {state === "unknown" && "Checking push status…"}
              </p>
              <p className="text-xs text-gray-500 dark:text-gray-400">
                Free forever — new SMS and email replies, campaign results and missed calls land on
                your phone, with the sender and a preview.
              </p>
            </div>
          </div>
          <div className="flex gap-2">
            {(state === "off" || state === "unknown") && (
              <button onClick={togglePush} disabled={busy} className="btn-primary btn-sm">
                {busy ? <Loader2 size={14} className="animate-spin" /> : "Enable notifications"}
              </button>
            )}
            {state === "on" && (
              <>
                <button onClick={sendTest} disabled={busy} className="btn-secondary btn-sm">
                  <Send size={13} /> Send test
                </button>
                <button onClick={togglePush} disabled={busy} className="btn-secondary btn-sm">
                  Turn off
                </button>
              </>
            )}
            {state === "blocked" && (
              <span className="text-xs text-gray-500 max-w-[220px]">
                Tap the 🔒/ⓘ icon in the address bar → Permissions → allow Notifications, then
                reload.
              </span>
            )}
          </div>
        </div>
      </div>

      {loading ? (
        <div className="space-y-2">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="card p-4 flex gap-3 items-start">
              <div className="skeleton w-9 h-9 rounded-full" />
              <div className="flex-1 space-y-2">
                <div className="skeleton h-3.5 w-40" />
                <div className="skeleton h-3 w-full" />
              </div>
            </div>
          ))}
        </div>
      ) : items.length === 0 ? (
        <div className="card p-10 text-center">
          <span className="mx-auto w-12 h-12 rounded-full bg-primary-50 text-primary-600 dark:bg-primary-950/60 dark:text-primary-300 flex items-center justify-center mb-3">
            <Bell size={22} />
          </span>
          <p className="font-medium text-gray-800 dark:text-gray-200">
            {unreadOnly ? "No unread notifications" : "No notifications yet"}
          </p>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
            Replies to your campaigns, finished sends and missed calls appear here — and on this
            device once you enable them.
          </p>
        </div>
      ) : (
        <div className="space-y-4">
          {groups.map((group) => (
            <div key={group.label}>
              <p className="text-xs font-semibold uppercase tracking-wide text-gray-400 px-1 mb-2">
                {group.label}
              </p>
              <div className="card divide-y divide-gray-100 dark:divide-gray-700/70 overflow-hidden stagger">
                {group.rows.map((it) => {
                  const d = notificationDisplay(it);
                  return (
                    <button
                      key={it.id}
                      onClick={() => openItem(it, d.href)}
                      className={`stagger-item w-full text-left px-4 py-3 flex gap-3 items-start transition-colors hover:bg-gray-50 dark:hover:bg-gray-700/40 ${
                        it.is_read ? "" : "bg-primary-50/50 dark:bg-primary-950/20"
                      }`}
                    >
                      <span
                        className={`mt-0.5 w-9 h-9 rounded-full flex items-center justify-center shrink-0 ${d.iconClass}`}
                      >
                        <d.Icon size={16} />
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-baseline gap-2">
                          <p className="text-sm font-semibold text-gray-900 dark:text-white truncate">
                            {d.sender}
                          </p>
                          <span className="ml-auto text-xs text-gray-400 shrink-0">
                            {relativeTime(it.created_at)}
                          </span>
                        </div>
                        {d.preview && (
                          <p className="text-sm text-gray-600 dark:text-gray-400 mt-0.5 line-clamp-2">
                            {d.preview}
                          </p>
                        )}
                        <p className="text-[11px] text-gray-400 mt-1 flex items-center gap-2">
                          {d.channel && <span className="uppercase tracking-wide">{d.channel}</span>}
                          {it.status === "failed" && (
                            <span className="text-danger-500">push failed</span>
                          )}
                          {it.created_at && (
                            <span className="hidden sm:inline">
                              {new Date(it.created_at).toLocaleString()}
                            </span>
                          )}
                        </p>
                      </div>
                      {!it.is_read && (
                        <span className="mt-3 w-2 h-2 rounded-full bg-primary-500 shrink-0" />
                      )}
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}

      {pages > 1 && (
        <div className="flex items-center justify-center gap-3">
          <button
            disabled={page <= 1}
            onClick={() => {
              setPage(page - 1);
              load(page - 1);
            }}
            className="btn-secondary btn-sm"
          >
            ← Prev
          </button>
          <span className="text-sm text-gray-500">
            Page {page} of {pages}
          </span>
          <button
            disabled={page >= pages}
            onClick={() => {
              setPage(page + 1);
              load(page + 1);
            }}
            className="btn-secondary btn-sm"
          >
            Next →
          </button>
        </div>
      )}
    </div>
  );
}
