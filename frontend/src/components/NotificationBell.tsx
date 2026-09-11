import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Bell, BellOff, CheckCheck, Loader2 } from "lucide-react";
import toast from "react-hot-toast";
import api from "../api/client";
import { enablePush, pushState, type PushState } from "../utils/push";

interface Item {
  id: number;
  event_type: string;
  title: string;
  body: string;
  is_read: boolean;
  url: string | null;
  created_at: string | null;
}

const ICONS: Record<string, string> = {
  new_reply: "📱",
  campaign_completed: "✅",
  campaign_failed: "❌",
  missed_call: "📞",
  followup_due: "⏰",
  test: "🔔",
};

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
    const t = setInterval(refreshCount, 15000);
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => {
      clearInterval(t);
      document.removeEventListener("mousedown", onClick);
    };
  }, []);

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
        className="relative p-2 rounded-lg text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700"
        title="Notifications"
      >
        {state === "off" || state === "blocked" ? <BellOff size={18} /> : <Bell size={18} />}
        {unread > 0 && (
          <span className="absolute -top-0.5 -right-0.5 min-w-[18px] h-[18px] px-1 rounded-full bg-red-500 text-white text-[11px] font-bold flex items-center justify-center">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
        {(state === "off" || state === "blocked") && unread === 0 && (
          <span className="absolute top-1 right-1 w-2 h-2 rounded-full bg-amber-400" title="Browser notifications off" />
        )}
      </button>

      {open && (
        <div className="absolute right-0 mt-2 w-80 max-w-[90vw] bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl shadow-xl z-50 overflow-hidden">
          <div className="flex items-center justify-between px-4 py-2.5 border-b border-gray-200 dark:border-gray-700">
            <span className="font-semibold text-sm text-gray-900 dark:text-white">
              Notifications {unread > 0 && <span className="text-red-500">({unread})</span>}
            </span>
            {unread > 0 && (
              <button
                onClick={markAll}
                className="text-xs text-primary-600 hover:underline flex items-center gap-1"
              >
                <CheckCheck size={13} /> Mark all read
              </button>
            )}
          </div>

          {(state === "off" || state === "unknown") && (
            <div className="px-4 py-3 bg-amber-50 dark:bg-amber-900/20 border-b border-gray-200 dark:border-gray-700">
              <p className="text-xs text-gray-700 dark:text-gray-300 mb-2">
                Get new-reply & campaign alerts on this phone — free, no app needed.
              </p>
              <button onClick={enable} disabled={busy} className="btn-primary btn-sm w-full">
                {busy ? <Loader2 size={14} className="animate-spin inline mr-1" /> : <Bell size={14} className="inline mr-1" />}
                Enable notifications
              </button>
            </div>
          )}
          {state === "blocked" && (
            <div className="px-4 py-3 bg-red-50 dark:bg-red-900/20 border-b border-gray-200 dark:border-gray-700">
              <p className="text-xs text-gray-700 dark:text-gray-300">
                Notifications are <b>blocked</b> for this site. Tap the 🔒/ⓘ icon in your
                browser's address bar → Permissions → allow Notifications.
              </p>
            </div>
          )}
          {state === "unsupported" && (
            <div className="px-4 py-3 border-b border-gray-200 dark:border-gray-700">
              <p className="text-xs text-gray-500">
                This browser can't receive push alerts — events still appear here.
              </p>
            </div>
          )}

          <div className="max-h-80 overflow-y-auto">
            {items.length === 0 && (
              <p className="text-sm text-gray-500 text-center py-6">No notifications yet.</p>
            )}
            {items.map((it) => (
              <button
                key={it.id}
                onClick={() => markRead(it.id, it.url)}
                className={`w-full text-left px-4 py-2.5 border-b border-gray-100 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-700/50 ${
                  it.is_read ? "" : "bg-primary-50/50 dark:bg-primary-900/10"
                }`}
              >
                <div className="flex gap-2 items-start">
                  <span className="text-base leading-5">{ICONS[it.event_type] ?? "🔔"}</span>
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-gray-900 dark:text-white truncate">
                      {it.title}
                    </p>
                    {it.body && (
                      <p className="text-xs text-gray-500 dark:text-gray-400 truncate">{it.body}</p>
                    )}
                  </div>
                  {!it.is_read && <span className="ml-auto mt-1.5 w-2 h-2 rounded-full bg-primary-500 shrink-0" />}
                </div>
              </button>
            ))}
          </div>

          <Link
            to="/notifications"
            onClick={() => setOpen(false)}
            className="block text-center text-sm text-primary-600 hover:underline py-2.5 border-t border-gray-200 dark:border-gray-700"
          >
            View all notifications
          </Link>
          {latestId === null && <span className="hidden" />}
        </div>
      )}
    </div>
  );
}
