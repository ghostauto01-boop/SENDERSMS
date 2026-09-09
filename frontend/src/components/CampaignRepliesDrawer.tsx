import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import api from "../api/client";
import toast from "react-hot-toast";
import {
  ArrowRight, Inbox as InboxIcon, MessageCircle, Search, Star, ThumbsDown,
  TrendingUp, Users, X,
} from "lucide-react";
import type { CampaignConversation } from "../types";

/**
 * CAMPAIGN -> REPLIES -> ONE CHAT
 *
 * The drill-down the campaign list was missing. From a campaign you can now:
 *
 *   1. see every lead it produced, and which of them replied
 *   2. read the reply inline, with its automatic sentiment
 *   3. click a person to land in that exact chat in the inbox
 *
 * Step 3 is a deep link (`/inbox?campaign_id=…&contact_id=…`), so the inbox
 * opens already filtered to this campaign with the right chat selected —
 * which is what "click individual to take me to my chats with them" means.
 */

const sentimentChip = (value?: string | null) => {
  const v = (value || "").toLowerCase();
  if (v === "positive") return { cls: "bg-green-100 text-green-700 dark:bg-green-900/50 dark:text-green-300", icon: TrendingUp, label: "positive" };
  if (v === "negative") return { cls: "bg-red-100 text-red-700 dark:bg-red-900/50 dark:text-red-300", icon: ThumbsDown, label: "negative" };
  return null;
};

const when = (iso: string | null) => {
  if (!iso) return "";
  const d = new Date(iso);
  const hours = (Date.now() - d.getTime()) / 3600000;
  if (hours < 24) return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (hours < 24 * 7) return d.toLocaleDateString([], { weekday: "short" });
  return d.toLocaleDateString([], { day: "numeric", month: "short" });
};

export default function CampaignRepliesDrawer({
  campaignId,
  campaignName,
  initialTab = "replies",
  onClose,
}: {
  campaignId: number;
  campaignName: string;
  initialTab?: "replies" | "leads";
  onClose: () => void;
}) {
  const [tab, setTab] = useState<"replies" | "leads">(initialTab);
  const [items, setItems] = useState<CampaignConversation[]>([]);
  const [performance, setPerformance] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const navigate = useNavigate();

  const load = useCallback(async () => {
    try {
      setLoading(true);
      const [conv, perf] = await Promise.all([
        api.get(`/campaigns/${campaignId}/conversations`, {
          params: { replied_only: tab === "replies", per_page: 200 },
        }),
        api.get(`/campaigns/${campaignId}/performance`),
      ]);
      setItems(conv.data.items || []);
      setPerformance(perf.data);
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not load this campaign's leads");
    } finally {
      setLoading(false);
    }
  }, [campaignId, tab]);

  useEffect(() => { load(); }, [load]);

  // Escape closes, like every other overlay in the app.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const filtered = query.trim()
    ? items.filter((i) => {
        const q = query.toLowerCase();
        return (
          i.contact_name.toLowerCase().includes(q) ||
          i.contact_phone.toLowerCase().includes(q) ||
          (i.last_reply?.body || "").toLowerCase().includes(q) ||
          (i.last_message_preview || "").toLowerCase().includes(q)
        );
      })
    : items;

  /** Open this person's chat, with the inbox already scoped to the campaign. */
  const openChat = (row: CampaignConversation) => {
    navigate(`/inbox?campaign_id=${campaignId}&contact_id=${row.contact_id}`);
  };

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/50" onClick={onClose}>
      <div
        className="bg-white dark:bg-gray-800 w-full sm:max-w-2xl h-full flex flex-col shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="px-4 sm:px-5 py-4 border-b border-gray-200 dark:border-gray-700 flex-shrink-0">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-xs text-primary-600 font-medium">CAMPAIGN RESULTS</p>
              <h2 className="text-lg font-semibold truncate">{campaignName}</h2>
            </div>
            <button onClick={onClose} className="btn-ghost btn-sm flex-shrink-0" aria-label="Close">
              <X size={18} />
            </button>
          </div>

          {performance && (
            <div className="grid grid-cols-4 gap-2 mt-3">
              {[
                { label: "Sent", value: performance.sent },
                { label: "Leads", value: performance.leads },
                { label: "Replied", value: performance.replied },
                { label: "Reply rate", value: `${performance.reply_rate}%` },
              ].map((s) => (
                <div key={s.label} className="rounded-lg bg-gray-50 dark:bg-gray-700/50 px-2 py-2 text-center">
                  <div className="text-base font-bold">{s.value}</div>
                  <div className="text-[10px] text-gray-500">{s.label}</div>
                </div>
              ))}
            </div>
          )}

          <div className="flex gap-1 p-1 mt-3 rounded-lg bg-gray-100 dark:bg-gray-700">
            {([
              { key: "replies", label: `Replies${performance ? ` (${performance.replied})` : ""}`, icon: MessageCircle },
              { key: "leads", label: `All leads${performance ? ` (${performance.leads})` : ""}`, icon: Users },
            ] as const).map((t) => (
              <button
                key={t.key}
                onClick={() => setTab(t.key)}
                className={`flex-1 px-3 py-1.5 text-sm rounded-md font-medium transition-colors flex items-center justify-center gap-1.5 ${
                  tab === t.key
                    ? "bg-white dark:bg-gray-800 shadow-sm text-primary-600"
                    : "text-gray-600 dark:text-gray-300"
                }`}
              >
                <t.icon size={14} /> {t.label}
              </button>
            ))}
          </div>

          <div className="relative mt-3">
            <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
            <input
              className="input !pl-9 py-1.5 text-sm"
              placeholder="Search name, number or reply…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
        </div>

        {/* List */}
        <div className="flex-1 overflow-y-auto">
          {loading ? (
            <div className="p-4 space-y-3">
              {[...Array(6)].map((_, i) => (
                <div key={i} className="skeleton h-16 w-full rounded-lg" />
              ))}
            </div>
          ) : filtered.length === 0 ? (
            <div className="text-center py-16 px-6">
              <MessageCircle size={40} className="mx-auto mb-3 text-gray-300" />
              <p className="font-medium">
                {query
                  ? "Nothing matches that search"
                  : tab === "replies"
                    ? "No replies to this campaign yet"
                    : "This campaign has no leads yet"}
              </p>
              <p className="text-sm text-gray-500 mt-1">
                {tab === "replies" && !query
                  ? "Replies appear here as soon as contacts write back."
                  : "Leads appear once the campaign starts sending."}
              </p>
            </div>
          ) : (
            <div className="divide-y divide-gray-100 dark:divide-gray-700">
              {filtered.map((row) => {
                const chip = sentimentChip(row.last_reply?.ai_sentiment);
                return (
                  <button
                    key={row.conversation_id}
                    onClick={() => openChat(row)}
                    className="w-full text-left px-4 sm:px-5 py-3 hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors group"
                    title={`Open your chat with ${row.contact_name}`}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="font-medium text-sm truncate">{row.contact_name}</span>
                          {row.unread_count > 0 && (
                            <span className="bg-primary-600 text-white text-[10px] font-medium px-1.5 py-px rounded-full">
                              {row.unread_count} new
                            </span>
                          )}
                          {row.status === "interested" && (
                            <span className="inline-flex items-center gap-0.5 text-[10px] text-yellow-600">
                              <Star size={10} /> interested
                            </span>
                          )}
                          {chip && (
                            <span className={`inline-flex items-center gap-1 text-[10px] px-1.5 py-px rounded-full ${chip.cls}`}>
                              <chip.icon size={9} /> {chip.label}
                            </span>
                          )}
                        </div>
                        <p className="text-xs text-gray-400 mt-0.5">{row.contact_phone}</p>
                        {row.last_reply ? (
                          <p className="text-sm text-gray-700 dark:text-gray-300 mt-1.5 line-clamp-2 bg-gray-50 dark:bg-gray-700/60 rounded-lg px-2.5 py-1.5">
                            “{row.last_reply.body}”
                          </p>
                        ) : (
                          <p className="text-sm text-gray-400 mt-1 line-clamp-1 italic">
                            {row.last_message_preview || "No messages yet"}
                          </p>
                        )}
                      </div>
                      <div className="flex flex-col items-end gap-1 flex-shrink-0">
                        <span className="text-[11px] text-gray-400">
                          {when(row.last_reply?.created_at || row.last_message_at)}
                        </span>
                        <ArrowRight
                          size={15}
                          className="text-gray-300 group-hover:text-primary-600 transition-colors"
                        />
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-4 sm:px-5 py-3 border-t border-gray-200 dark:border-gray-700 flex gap-2 flex-shrink-0">
          <button
            onClick={() => navigate(`/inbox?campaign_id=${campaignId}${tab === "replies" ? "&replied=1" : ""}`)}
            className="btn-primary btn-sm flex-1 justify-center"
          >
            <InboxIcon size={14} className="mr-1" /> Open in inbox
          </button>
          <button onClick={onClose} className="btn-secondary btn-sm">Close</button>
        </div>
      </div>
    </div>
  );
}
