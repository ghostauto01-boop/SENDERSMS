import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Bell, BellOff, CheckCheck, Loader2, Send } from "lucide-react";
import toast from "react-hot-toast";
import api from "../api/client";
import { disablePush, enablePush, pushState, sendTestPush, type PushState } from "../utils/push";

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

const ICONS: Record<string, string> = {
  new_reply: "📱",
  campaign_completed: "✅",
  campaign_failed: "❌",
  missed_call: "📞",
  followup_due: "⏰",
  test: "🔔",
};

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

  const openItem = async (it: Item) => {
    if (!it.is_read) {
      try {
        await api.post(`/notifications/${it.id}/read`);
      } catch {
        /* ignore */
      }
    }
    if (it.url) navigate(it.url);
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

  return (
    <div className="max-w-3xl mx-auto space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-900 dark:text-white">
          Notifications {unread > 0 && <span className="text-sm font-normal text-red-500">({unread} unread)</span>}
        </h1>
        <div className="flex gap-2">
          <button onClick={toggleFilter} className={`btn-sm ${unreadOnly ? "btn-primary" : "btn-secondary"}`}>
            {unreadOnly ? "Show all" : "Unread only"}
          </button>
          {unread > 0 && (
            <button onClick={markAll} className="btn-secondary btn-sm flex items-center gap-1">
              <CheckCheck size={14} /> Mark all read
            </button>
          )}
        </div>
      </div>

      <div className="card p-4">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div className="flex items-center gap-3">
            {state === "on" ? <Bell size={20} className="text-green-600" /> : <BellOff size={20} className="text-gray-400" />}
            <div>
              <p className="font-semibold text-sm text-gray-900 dark:text-white">
                {state === "on" && "Push alerts ON for this browser"}
                {state === "off" && "Push alerts OFF for this browser"}
                {state === "blocked" && "Push alerts BLOCKED by browser"}
                {state === "unsupported" && "Push not supported by this browser"}
                {state === "unknown" && "Checking push status…"}
              </p>
              <p className="text-xs text-gray-500">
                Free forever — new replies, campaign results & missed calls land on your phone.
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
                <button onClick={sendTest} disabled={busy} className="btn-secondary btn-sm flex items-center gap-1">
                  <Send size={13} /> Send test
                </button>
                <button onClick={togglePush} disabled={busy} className="btn-secondary btn-sm">
                  Turn off
                </button>
              </>
            )}
            {state === "blocked" && (
              <span className="text-xs text-gray-500 max-w-[220px]">
                Tap the 🔒/ⓘ icon in the address bar → Permissions → allow Notifications, then reload.
              </span>
            )}
          </div>
        </div>
      </div>

      {loading ? (
        <div className="flex justify-center py-10">
          <Loader2 className="animate-spin text-gray-400" size={28} />
        </div>
      ) : items.length === 0 ? (
        <div className="card p-10 text-center text-gray-500">
          {unreadOnly ? "No unread notifications. 🎉" : "No notifications yet."}
        </div>
      ) : (
        <div className="card divide-y divide-gray-100 dark:divide-gray-700">
          {items.map((it) => (
            <button
              key={it.id}
              onClick={() => openItem(it)}
              className={`w-full text-left px-4 py-3 hover:bg-gray-50 dark:hover:bg-gray-700/50 flex gap-3 items-start ${
                it.is_read ? "" : "bg-primary-50/50 dark:bg-primary-900/10"
              }`}
            >
              <span className="text-xl leading-6">{ICONS[it.event_type] ?? "🔔"}</span>
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-gray-900 dark:text-white">{it.title}</p>
                {it.body && <p className="text-sm text-gray-600 dark:text-gray-400 mt-0.5">{it.body}</p>}
                <p className="text-xs text-gray-400 mt-1">
                  {it.created_at ? new Date(it.created_at).toLocaleString() : ""}
                  {it.status === "failed" ? " · push failed" : ""}
                </p>
              </div>
              {!it.is_read && <span className="mt-2 w-2 h-2 rounded-full bg-primary-500 shrink-0" />}
            </button>
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
