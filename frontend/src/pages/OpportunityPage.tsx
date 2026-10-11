import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Briefcase, RefreshCw, Search, Undo2, X } from "lucide-react";
import api from "../api/client";
import toast from "react-hot-toast";
import ConversationRow from "../components/ConversationRow";

type Scope = "all" | "sms" | "email";

const SCOPES: { key: Scope; label: string }[] = [
  { key: "all", label: "All" },
  { key: "sms", label: "SMS" },
  { key: "email", label: "Email" },
];

/**
 * Opportunity: the prospects you have marked as interested enough to chase.
 * Holds chats from both channels. Move a chat in from its inbox (the chat
 * actions) and take it out again from here.
 */
export default function OpportunityPage() {
  const [params, setParams] = useSearchParams();
  const rawScope = params.get("channel");
  const scope: Scope = rawScope === "sms" || rawScope === "email" ? rawScope : "all";
  const [rows, setRows] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [debounced, setDebounced] = useState("");
  const [busy, setBusy] = useState<number | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  useEffect(() => {
    const t = setTimeout(() => setDebounced(search.trim()), 300);
    return () => clearTimeout(t);
  }, [search]);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get("/inbox/conversations", {
        params: {
          status: "opportunity",
          channel: scope === "all" ? "all" : scope,
          per_page: 500,
          search: debounced || undefined,
        },
      });
      setRows(data.items || []);
    } catch {
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, [scope, debounced]);

  useEffect(() => { load(); }, [load]);

  const chooseScope = (next: Scope) => {
    const q = new URLSearchParams(params);
    if (next === "all") q.delete("channel");
    else q.set("channel", next);
    setParams(q, { replace: true });
  };

  const open = (conv: any) => {
    if (conv.channel === "email") navigate(`/email-inbox?view=replied&conversation_id=${conv.id}`);
    else navigate(`/inbox?contact_id=${conv.contact_id}`);
  };

  // Taking a chat out of Opportunity keeps its unread state (the server decides).
  const remove = async (conv: any) => {
    setBusy(conv.id);
    try {
      await api.post(`/inbox/conversations/${conv.id}/status`, { status: "active" });
      setRows((rs) => rs.filter((r) => !(r.id === conv.id && r.channel === conv.channel)));
      toast.success("Removed from Opportunity");
    } catch {
      toast.error("Could not change that chat");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="max-w-4xl mx-auto">
      <div className="flex flex-wrap items-start justify-between gap-3 mb-4">
        <div className="min-w-0">
          <p className="text-xs font-semibold tracking-wide text-warning-600 dark:text-warning-400 flex items-center gap-1.5">
            <Briefcase size={14} /> PIPELINE
          </p>
          <h1 className="page-title mt-1">Opportunity</h1>
          <p className="page-subtitle">
            {loading && rows.length === 0
              ? "Loading…"
              : rows.length === 0
              ? "No prospects here yet. Open an interested chat and choose Move to Opportunity."
              : `${rows.length} interested prospect${rows.length === 1 ? "" : "s"} you are following up`}
          </p>
        </div>
        <div className="inline-flex gap-1 p-1 rounded-xl bg-gray-100 dark:bg-gray-700/70" role="tablist" aria-label="Channel">
          {SCOPES.map((s) => (
            <button
              key={s.key}
              role="tab"
              aria-selected={scope === s.key}
              onClick={() => chooseScope(s.key)}
              className={`px-3.5 py-2 rounded-lg text-sm font-medium transition-colors ${
                scope === s.key
                  ? "bg-white dark:bg-gray-800 shadow-sm text-primary-600"
                  : "text-gray-600 dark:text-gray-300 hover:text-gray-900 dark:hover:text-white"
              }`}
            >
              {s.label}
            </button>
          ))}
        </div>
      </div>

      <section className="card p-0 overflow-hidden">
        <div className="p-3 border-b border-gray-100 dark:border-gray-700/70 flex items-center gap-2">
          <div className="relative flex-1">
            <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
            <input
              ref={searchRef}
              className="input pl-9 pr-9"
              placeholder="Search opportunities…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              aria-label="Search opportunities"
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
            aria-label="Refresh opportunities"
          >
            <RefreshCw size={15} className={loading ? "animate-spin" : ""} />
          </button>
        </div>

        <div className="divide-y divide-gray-50 dark:divide-gray-700/50">
          {loading && rows.length === 0 ? (
            <div className="p-4 space-y-3">
              {[0, 1, 2].map((i) => (
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
              <span className="mx-auto w-12 h-12 rounded-full bg-warning-100 text-warning-600 dark:bg-warning-900/30 dark:text-warning-400 flex items-center justify-center mb-3">
                <Briefcase size={22} />
              </span>
              <p className="font-medium text-gray-800 dark:text-gray-200">
                {debounced ? "No opportunity matches that search" : "Nothing in Opportunity yet"}
              </p>
              <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
                Interested prospects go here so they do not get lost in the inbox.
              </p>
            </div>
          ) : (
            rows.map((conv) => (
              <ConversationRow
                key={`${conv.channel}-${conv.id}`}
                conv={conv}
                onOpen={() => open(conv)}
                actions={
                  <button
                    onClick={() => remove(conv)}
                    disabled={busy === conv.id}
                    title="Remove from Opportunity"
                    aria-label="Remove from Opportunity"
                    className="p-2 rounded-lg text-gray-400 hover:text-danger-600 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-50"
                  >
                    <Undo2 size={15} />
                  </button>
                }
              />
            ))
          )}
        </div>
      </section>
    </div>
  );
}
