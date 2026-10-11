import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { MessageCircle, RefreshCw, Search, X } from "lucide-react";
import api from "../api/client";
import ChannelPicker, { type InboxChannel } from "../components/ChannelPicker";
import ConversationRow from "../components/ConversationRow";

/**
 * Conversations: only the people who wrote back, for one channel at a time.
 *
 * The SMS | Email choice is in the URL (?channel=sms|email). Opening a row takes
 * you to that thread in its own inbox, where the full chat and reply box live.
 */
export default function ConversationsPage() {
  const [params, setParams] = useSearchParams();
  const channel: InboxChannel = params.get("channel") === "email" ? "email" : "sms";
  const [rows, setRows] = useState<any[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [debounced, setDebounced] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  useEffect(() => {
    const t = setTimeout(() => setDebounced(search.trim()), 300);
    return () => clearTimeout(t);
  }, [search]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/inbox/conversations", {
        params: { channel, replied_only: true, per_page: 200, search: debounced || undefined },
      });
      setRows(data.items || []);
      setTotal(data.total ?? (data.items || []).length);
    } catch {
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, [channel, debounced]);

  useEffect(() => { load(); }, [load]);

  // Keep the list fresh while the page is open, like the inboxes do.
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") load();
    }, 30000);
    return () => window.clearInterval(timer);
  }, [load]);

  const choose = (next: InboxChannel) => {
    const q = new URLSearchParams(params);
    q.set("channel", next);
    setParams(q, { replace: true });
  };

  const open = (conv: any) => {
    // Each channel's thread lives in its own inbox; the deep link opens it there.
    if (conv.channel === "email") navigate(`/email-inbox?view=replied&conversation_id=${conv.id}`);
    else navigate(`/inbox?contact_id=${conv.contact_id}`);
  };

  const noun = channel === "email" ? "email" : "SMS";
  const unread = rows.filter((r) => r.unread_count > 0 || r.status === "unread").length;

  return (
    <div className="max-w-4xl mx-auto">
      <div className="flex flex-wrap items-start justify-between gap-3 mb-4">
        <div className="min-w-0">
          <p className="text-xs font-semibold tracking-wide text-primary-600 dark:text-primary-400 flex items-center gap-1.5">
            <MessageCircle size={14} /> CONVERSATIONS
          </p>
          <h1 className="page-title mt-1">Conversations</h1>
          <p className="page-subtitle">
            {loading && rows.length === 0
              ? "Loading…"
              : `${total ?? rows.length} ${noun} ${(total ?? rows.length) === 1 ? "person" : "people"} who wrote back${
                  unread > 0 ? ` · ${unread} unread` : ""
                }`}
          </p>
        </div>
        <ChannelPicker value={channel} onChange={choose} />
      </div>

      <section className="card p-0 overflow-hidden">
        <div className="p-3 border-b border-gray-100 dark:border-gray-700/70 flex items-center gap-2">
          <div className="relative flex-1">
            <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
            <input
              ref={searchRef}
              className="input pl-9 pr-9"
              placeholder={`Search ${noun} conversations…`}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              aria-label="Search conversations"
            />
            {search && (
              <button
                onClick={() => { setSearch(""); searchRef.current?.focus(); }}
                className="absolute right-2 top-1/2 -translate-y-1/2 p-1.5 rounded-lg text-gray-400 hover:text-gray-600"
                aria-label="Clear search"
              >
                <X size={14} />
              </button>
            )}
          </div>
          <button
            onClick={load}
            className="p-2 rounded-lg text-gray-400 hover:text-primary-600 hover:bg-gray-100 dark:hover:bg-gray-800"
            title="Refresh"
            aria-label="Refresh conversations"
          >
            <RefreshCw size={15} className={loading ? "animate-spin" : ""} />
          </button>
        </div>

        <div className="divide-y divide-gray-50 dark:divide-gray-700/50">
          {loading && rows.length === 0 ? (
            <div className="p-4 space-y-3">
              {[0, 1, 2, 3].map((i) => (
                <div key={i} className="flex gap-3 items-center">
                  <div className="skeleton w-11 h-11 rounded-full" />
                  <div className="flex-1 space-y-2">
                    <div className="skeleton h-3.5 w-40" />
                    <div className="skeleton h-3 w-full" />
                  </div>
                </div>
              ))}
            </div>
          ) : rows.length === 0 ? (
            <div className="p-10 text-center">
              <p className="font-medium text-gray-800 dark:text-gray-200">
                {debounced ? "No conversation matches that search" : `No ${noun} conversations yet`}
              </p>
              <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
                {debounced
                  ? "Try a different name, number or word."
                  : "A conversation shows up here as soon as the person writes back."}
              </p>
            </div>
          ) : (
            rows.map((conv) => (
              <ConversationRow key={`${conv.channel}-${conv.id}`} conv={conv} onOpen={() => open(conv)} />
            ))
          )}
        </div>
      </section>
    </div>
  );
}
