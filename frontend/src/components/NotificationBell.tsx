import { useVisiblePolling } from "../hooks/useVisiblePolling";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Bell, BellOff, CheckCheck, Loader2 } from "lucide-react";
import toast from "react-hot-toast";
import api from "../api/client";
import { enablePush, pushState, type PushState } from "../utils/push";
import { notificationDisplay, relativeTime } from "../utils/notificationDisplay";

interface Item {
  id: number;
  event_type: string;
  title: string;
  body: string;
  is_read: boolean;
  url: string | null;
  created_at: string | null;
}

/**
 * The bell.
 *
 * Every row shows a kind, a sender and a preview of what was actually said —
 * an email reply reads the same way an SMS reply does ("Ada Obi · Pricing for
 * 200 covers — Can you send the sheet?"), because the point of a notification
 * is to let you decide whether to act without opening the app.
 *
 * Rows animate in, but briefly and only once: this panel is opened dozens of
 * times a day and must never feel like it is waiting for an animation.
 */
export default function NotificationBell() {
  const [unread, setUnread] = useState(0);
  const [latestId, setLatestId] = useState<number | null>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<PushState>("unknown");
  const [busy, setBusy] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();

  const refreshCount = async () => {
    try {
      const { data } = await api.get("/notifications/unread-count");
      setUnread(data.unread ?? 0);
      setLatestId(data.latest_id ?? null);
    } catch {
      /* offline — keep last value */
    }
  };

  useEffect(() => {
    pushState().then(setState).catch(() => {});
    refreshCount();
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, []);

  // Unread badge: every 30 s while the tab is visible. (It sits on every
  // page, so a background tab must not keep the database awake for it.)
  useVisiblePolling(refreshCount, 30000);

  // A new notification while the tab is open: the badge should not wait 30 s.
  useEffect(() => {
    if (latestId === null) return;
    const timer = window.setTimeout(refreshCount, 1200);
    return () => window.clearTimeout(timer);
  }, [latestId]);

  const openPanel = async () => {
    const next = !open;
    setOpen(next);
    if (next) {
      try {
        const { data } = await api.get("/notifications/", {
          params: { per_page: 8 },
        });
        setItems(data.items ?? []);
        setUnread(data.unread ?? 0);
      } catch {
        /* ignore */
      }
    }
  };

  const markRead = async (id: number, url: string | null) => {
    try {
      await api.post(`/notifications/${id}/read`);
    } catch {
      /* ignore */
    }
    setOpen(false);
    refreshCount();
    if (url) navigate(url);
  };

  const markAll = async () => {
    try {
      await api.post("/notifications/read-all");
      setItems((xs) => xs.map((x) => ({ ...x, is_read: true })));
      setUnread(0);
    } catch {
      toast.error("Could not mark all as read");
    }
  };

  const enable = async () => {
    setBusy(true);
    try {
      const s = await enablePush();
      setState(s);
      if (s === "on") toast.success("Notifications enabled on this device 🔔");
      else if (s === "blocked")
        toast.error("Permission blocked — allow notifications in your browser settings, then retry.");
      else toast.error("Could not enable notifications on this browser.");
    } catch {
      toast.error("Could not enable notifications.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={openPanel}
        aria-label={unread > 0 ? `Notifications, ${unread} unread` : "Notifications"}
        className="relative p-2 rounded-xl text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors active:scale-95"
        title="Notifications"
      >
        {state === "off" || state === "blocked" ? <BellOff size={18} /> : <Bell size={18} />}
        {unread > 0 && (
          <span className="absolute -top-0.5 -right-0.5 min-w-[18px] h-[18px] px-1 rounded-full bg-danger-500 text-white text-[11px] font-bold flex items-center justify-center animate-pop-in shadow-sm">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
        {(state === "off" || state === "blocked") && unread === 0 && (
          <span
            className="absolute top-1 right-1 w-2 h-2 rounded-full bg-warning-400"
            title="Browser notifications off"
          />
        )}
      </button>

      {open && (
        /* On a phone the panel is pinned to the screen edges rather than to the
           bell: anchored to the bell it would hang off the left of the
           viewport (the bell is not the rightmost control in the header), which
           is how a notification list ends up clipped and unusable. */
        <div className="absolute right-0 mt-2 w-[22rem] max-w-[92vw] max-sm:fixed max-sm:inset-x-2 max-sm:top-[4.5rem] max-sm:mt-0 max-sm:w-auto max-sm:max-w-none bg-white dark:bg-gray-800 border border-gray-200/80 dark:border-gray-700 rounded-2xl shadow-pop z-50 overflow-hidden animate-pop-in origin-top-right">
          <div className="flex items-center justify-between px-4 py-2.5 border-b border-gray-100 dark:border-gray-700">
            <span className="font-semibold text-sm text-gray-900 dark:text-white flex items-center gap-2">
              Notifications
              {unread > 0 && (
                <span className="badge-blue">{unread} new</span>
              )}
            </span>
            {unread > 0 && (
              <button
                onClick={markAll}
                className="text-xs text-primary-600 dark:text-primary-400 hover:underline flex items-center gap-1"
              >
                <CheckCheck size={13} /> Mark all read
              </button>
            )}
          </div>

          {(state === "off" || state === "unknown") && (
            <div className="px-4 py-3 bg-warning-50 dark:bg-warning-950/30 border-b border-gray-100 dark:border-gray-700">
              <p className="text-xs text-gray-700 dark:text-gray-300 mb-2">
                Get new-reply &amp; campaign alerts on this phone — free, no app needed.
              </p>
              <button onClick={enable} disabled={busy} className="btn-primary btn-sm w-full">
                {busy ? <Loader2 size={14} className="animate-spin" /> : <Bell size={14} />}
                Enable notifications
              </button>
            </div>
          )}
          {state === "blocked" && (
            <div className="px-4 py-3 bg-danger-50 dark:bg-danger-950/30 border-b border-gray-100 dark:border-gray-700">
              <p className="text-xs text-gray-700 dark:text-gray-300">
                Notifications are <b>blocked</b> for this site. Tap the 🔒/ⓘ icon in your
                browser's address bar → Permissions → allow Notifications.
              </p>
            </div>
          )}
          {state === "unsupported" && (
            <div className="px-4 py-3 border-b border-gray-100 dark:border-gray-700">
              <p className="text-xs text-gray-500">
                This browser can't receive push alerts — events still appear here.
              </p>
            </div>
          )}

          <div className="max-h-80 overflow-y-auto stagger">
            {items.length === 0 && (
              <p className="text-sm text-gray-500 text-center py-8">No notifications yet.</p>
            )}
            {items.map((it) => {
              const d = notificationDisplay(it);
              return (
                <button
                  key={it.id}
                  onClick={() => markRead(it.id, d.href)}
                  className={`stagger-item w-full text-left px-3 py-2.5 border-b border-gray-50 dark:border-gray-700/60 last:border-0 transition-colors hover:bg-gray-50 dark:hover:bg-gray-700/40 ${
                    it.is_read ? "" : "bg-primary-50/60 dark:bg-primary-950/20"
                  }`}
                >
                  <div className="flex gap-2.5 items-start">
                    <span
                      className={`mt-0.5 w-8 h-8 rounded-full flex items-center justify-center shrink-0 ${d.iconClass}`}
                    >
                      <d.Icon size={15} />
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-baseline gap-2">
                        <p className="text-sm font-semibold text-gray-900 dark:text-white truncate">
                          {d.sender}
                        </p>
                        <span className="text-[10px] text-gray-400 shrink-0 ml-auto">
                          {relativeTime(it.created_at)}
                        </span>
                      </div>
                      {d.preview && (
                        <p className="text-xs text-gray-600 dark:text-gray-400 line-clamp-2 mt-0.5">
                          {d.preview}
                        </p>
                      )}
                      {d.channel && (
                        <p className="text-[10px] uppercase tracking-wide text-gray-400 mt-1">
                          {d.channel}
                        </p>
                      )}
                    </div>
                    {!it.is_read && (
                      <span className="mt-2 w-2 h-2 rounded-full bg-primary-500 shrink-0" />
                    )}
                  </div>
                </button>
              );
            })}
          </div>

          <Link
            to="/notifications"
            onClick={() => setOpen(false)}
            className="block text-center text-sm font-medium text-primary-600 dark:text-primary-400 hover:bg-gray-50 dark:hover:bg-gray-700/40 py-2.5 border-t border-gray-100 dark:border-gray-700 transition-colors"
          >
            View all notifications
          </Link>
        </div>
      )}
    </div>
  );
}
