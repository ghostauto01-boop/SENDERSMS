import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams, Link } from "react-router-dom";
import toast from "react-hot-toast";
import {
  ArrowLeft,
  ArrowUpRight,
  CheckCheck,
  ChevronDown,
  ChevronUp,
  Clock,
  Inbox,
  Mail,
  MailOpen,
  MousePointerClick,
  Paperclip,
  RefreshCw,
  Reply,
  Search,
  Send,
  Sparkles,
  Star,
  X,
  XCircle,
} from "lucide-react";
import emailApi, { EmailConversation, EmailMessage } from "../api/email";
import RichEmailEditor, { AttachmentPayload } from "../components/RichEmailEditor";

/**
 * EMAIL INBOX — the Gmail-shaped one.
 *
 * What makes an inbox usable at a glance, and what this page does:
 *
 * * the list reads like Gmail's: sender in bold while unread, then the subject
 *   and the first line of the message on one line, with the time on the right
 *   and a coloured dot for unread — so scanning 50 threads takes seconds;
 * * the reading pane puts the subject first, then the conversation as
 *   individual messages with the sender's name, address, time and the
 *   delivery/open/click state for anything this app sent;
 * * on a phone it is one column: the list fills the screen, tapping a thread
 *   slides the conversation in full-width with a back button, and the composer
 *   stays above the keyboard;
 * * replies send from the same Brevo sender (or the connected mailbox) the
 *   thread started on, in the same mail thread the prospect sees in Gmail.
 *
 * Status, search and the open thread all live in the URL, so a notification
 * can deep-link straight to a conversation and a refresh keeps your place.
 */

const STATUS_FILTERS = [
  { key: "all", label: "All" },
  { key: "unread", label: "Unread" },
  { key: "active", label: "Open" },
  { key: "interested", label: "Interested" },
  { key: "not_interested", label: "Not interested" },
  { key: "closed", label: "Closed" },
];

const STATUS_TONE: Record<string, string> = {
  interested: "badge-green",
  active: "badge-blue",
  unread: "badge-blue",
  not_interested: "badge-gray",
  closed: "badge-gray",
};

/** One colour per contact, derived from the address so it never changes. */
const AVATAR_TONES = [
  "bg-primary-100 text-primary-700 dark:bg-primary-950/70 dark:text-primary-300",
  "bg-accent-100 text-accent-700 dark:bg-accent-950/70 dark:text-accent-300",
  "bg-success-100 text-success-700 dark:bg-success-950/70 dark:text-success-300",
  "bg-warning-100 text-warning-700 dark:bg-warning-950/70 dark:text-warning-300",
];

function initialsOf(name?: string | null, email?: string | null): string {
  const source = (name || email || "?").trim();
  if (name) {
    const parts = source.split(/\s+/).filter(Boolean);
    return ((parts[0]?.[0] || "") + (parts[1]?.[0] || "")).toUpperCase() || "?";
  }
  return source.slice(0, 2).toUpperCase();
}

function toneFor(key: string): string {
  let hash = 0;
  for (let i = 0; i < key.length; i += 1) hash = (hash * 31 + key.charCodeAt(i)) % 997;
  return AVATAR_TONES[hash % AVATAR_TONES.length];
}

function when(value?: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const now = new Date();
  const sameDay = date.toDateString() === now.toDateString();
  if (sameDay) return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (date.toDateString() === yesterday.toDateString()) return "Yesterday";
  if (date.getFullYear() === now.getFullYear()) {
    return date.toLocaleDateString([], { day: "numeric", month: "short" });
  }
  return date.toLocaleDateString([], { day: "numeric", month: "short", year: "2-digit" });
}

function fullTime(value?: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString([], {
    weekday: "short",
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * Collapsible "what happened to this email" panel, per outgoing message.
 *
 * Brevo reports opens and clicks per recipient; this shows the same thing in
 * the thread, including which link was clicked, loaded only when expanded.
 */
function MessageActivity({
  message,
  onOpen,
}: {
  message: EmailMessage;
  onOpen: () => Promise<any>;
}) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);

  const opens = message.open_count || 0;
  const clicks = message.click_count || 0;
  const outgoing = message.direction === "outgoing";
  if (!outgoing) return null;

  const toggle = async () => {
    const next = !open;
    setOpen(next);
    if (next && !data) {
      setLoading(true);
      try {
        setData(await onOpen());
      } catch {
        toast.error("Could not load this email's activity");
      } finally {
        setLoading(false);
      }
    }
  };

  return (
    <div className="mt-2">
      <button
        type="button"
        onClick={toggle}
        className="chip py-1 px-2 text-[11px] text-gray-500 dark:text-gray-400"
      >
        {opens > 0 ? `${opens} open${opens === 1 ? "" : "s"}` : "no opens yet"}
        <span className="text-gray-300 dark:text-gray-600">·</span>
        {clicks > 0 ? `${clicks} click${clicks === 1 ? "" : "s"}` : "no clicks yet"}
        {open ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
      </button>
      {open && (
        <div className="mt-2 rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/40 p-2.5 space-y-1 animate-fade-in">
          {loading && <p className="text-[11px] text-gray-500">Reading Brevo…</p>}
          {(data?.events || []).map((e: any) => (
            <p key={e.id} className="text-[11px] text-gray-600 dark:text-gray-400 break-anywhere">
              <Clock size={10} className="inline mr-1 -mt-0.5" />
              {e.event_type}
              {e.created_at ? ` · ${new Date(e.created_at).toLocaleString()}` : ""}
              {e.link ? ` · ${e.link}` : ""}
            </p>
          ))}
          {!loading && (data?.events || []).length === 0 && (
            <p className="text-[11px] text-gray-500">
              Delivered, but nothing opened yet. Brevo records opens the first time the images load.
            </p>
          )}
          {(data?.summary?.unique_links_clicked || 0) > 0 && (
            <p className="text-[11px] text-gray-500">
              {data.summary.unique_links_clicked} different link(s) clicked
            </p>
          )}
        </div>
      )}
    </div>
  );
}

/** One message in the thread: who, when, what, and what happened to it. */
function MessageCard({
  message,
  contactName,
  defaultOpen,
  onOpenActivity,
}: {
  message: EmailMessage;
  contactName: string;
  defaultOpen: boolean;
  onOpenActivity: () => Promise<any>;
}) {
  const [expanded, setExpanded] = useState(defaultOpen);
  const outgoing = message.direction === "outgoing";
  const from = outgoing
    ? message.from_address || "you"
    : contactName || message.from_address || message.contact_email || "Unknown sender";

  return (
    <article
      className={`card overflow-hidden animate-fade-up ${
        outgoing ? "" : "border-l-2 border-l-primary-500"
      }`}
    >
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="w-full text-left p-3.5 flex items-start gap-3 hover:bg-gray-50/70 dark:hover:bg-gray-700/20 transition-colors"
      >
        <span
          className={`w-9 h-9 rounded-full flex items-center justify-center text-xs font-semibold shrink-0 ${
            outgoing ? "bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-200" : toneFor(from)
          }`}
        >
          {initialsOf(from, message.from_address)}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2 flex-wrap">
            <p className="text-sm font-semibold text-gray-900 dark:text-white truncate">{from}</p>
            <span className="text-xs text-gray-400 truncate">
              {outgoing ? "to " : "to "}
              {outgoing ? message.to_address || message.contact_email : "you"}
            </span>
            <span className="ml-auto text-xs text-gray-400 shrink-0" title={fullTime(message.created_at)}>
              {when(message.created_at)}
            </span>
          </div>
          {!expanded && (
            <p className="text-sm text-gray-500 dark:text-gray-400 truncate mt-0.5">
              {message.body?.trim() || message.subject || "(no text)"}
            </p>
          )}
          {expanded && (
            <div className="mt-1 flex items-center gap-2 flex-wrap">
              {message.is_auto_reply && <span className="badge-gray">auto-reply</span>}
              <span className={outgoing ? "badge-blue" : "badge-gray"}>{message.status}</span>
              {message.provider && (
                <span className="text-[11px] text-gray-400">via {message.provider}</span>
              )}
              {message.open_count > 0 && (
                <span className="text-[11px] text-primary-600 dark:text-primary-400 flex items-center gap-1">
                  <CheckCheck size={11} /> opened {message.open_count}×
                </span>
              )}
              {message.click_count > 0 && (
                <span className="text-[11px] text-success-600 dark:text-success-400 flex items-center gap-1">
                  <MousePointerClick size={11} /> clicked {message.click_count}×
                </span>
              )}
              {message.bounced_hard && (
                <span className="badge-red">hard bounce</span>
              )}
            </div>
          )}
        </div>
        <span className="text-gray-400 shrink-0 mt-1">
          {expanded ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
        </span>
      </button>

      {expanded && (
        <div className="px-3.5 pb-3.5 animate-fade-in">
          <div className="rounded-xl bg-gray-50 dark:bg-gray-900/40 p-3.5 text-sm text-gray-800 dark:text-gray-100">
            {message.html_body ? (
              <div
                className="prose prose-sm max-w-none dark:prose-invert break-anywhere
                           prose-a:text-primary-600 dark:prose-a:text-primary-400"
                dangerouslySetInnerHTML={{ __html: message.html_body }}
              />
            ) : (
              <p className="whitespace-pre-wrap break-anywhere">{message.body}</p>
            )}
          </div>

          {(message.attachments?.length || 0) > 0 && (
            <div className="mt-2 flex flex-wrap gap-2">
              {message.attachments!.map((a, i) => (
                <a
                  key={`${a.name}-${i}`}
                  href={a.url || undefined}
                  target="_blank"
                  rel="noreferrer"
                  className="chip"
                >
                  <Paperclip size={12} />
                  <span className="truncate max-w-[12rem]">{a.name}</span>
                  {a.size ? (
                    <span className="text-gray-400">{Math.max(1, Math.round(a.size / 1024))} KB</span>
                  ) : null}
                </a>
              ))}
            </div>
          )}

          <MessageActivity message={message} onOpen={onOpenActivity} />
        </div>
      )}
    </article>
  );
}

export default function EmailInboxPage() {
  const [params, setParams] = useSearchParams();
  const [conversations, setConversations] = useState<EmailConversation[]>([]);
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState("all");
  const [search, setSearch] = useState("");
  const [openId, setOpenId] = useState<number | null>(null);
  const [detail, setDetail] = useState<any>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [reply, setReply] = useState("");
  const [replyHtml, setReplyHtml] = useState("");
  const [replyAttachments, setReplyAttachments] = useState<AttachmentPayload[]>([]);
  const [cc, setCc] = useState("");
  const [showCc, setShowCc] = useState(false);
  const [sending, setSending] = useState(false);
  const [filterOpen, setFilterOpen] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  const deepLinkedId = params.get("conversation_id");

  const load = useCallback(
    async (opts: { quiet?: boolean } = {}) => {
      if (!opts.quiet) setLoading(true);
      try {
        const data = await emailApi.conversations({
          status: status === "all" ? undefined : status,
          search: search || undefined,
          per_page: 50,
        });
        setConversations(data.items);
      } catch {
        if (!opts.quiet) toast.error("Could not load the email inbox");
      } finally {
        setLoading(false);
      }
    },
    [status, search]
  );

  useEffect(() => {
    load();
  }, [load]);

  // 30 s refresh keeps the unread badge and new mail current without a socket.
  useEffect(() => {
    const timer = window.setInterval(() => load({ quiet: true }), 30000);
    return () => window.clearInterval(timer);
  }, [load]);

  const open = useCallback(
    async (id: number) => {
      setOpenId(id);
      setLoadingDetail(true);
      setReply("");
      setReplyHtml("");
      setReplyAttachments([]);
      setCc("");
      try {
        const data = await emailApi.conversation(id);
        setDetail(data);
        if ((data.conversation?.unread_count || 0) > 0) {
          await emailApi.markRead(id);
          setConversations((prev) =>
            prev.map((c) => (c.id === id ? { ...c, unread_count: 0, status: "active" } : c))
          );
        }
      } catch {
        toast.error("Could not open this conversation");
      } finally {
        setLoadingDetail(false);
      }
    },
    []
  );

  // Deep link: a notification (or a refresh) can name the thread to open.
  useEffect(() => {
    if (!deepLinkedId) return;
    const id = Number(deepLinkedId);
    if (!Number.isFinite(id) || id === openId) return;
    open(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deepLinkedId]);

  const closeThread = () => {
    setOpenId(null);
    setDetail(null);
    if (deepLinkedId) {
      const next = new URLSearchParams(params);
      next.delete("conversation_id");
      setParams(next, { replace: true });
    }
  };

  useEffect(() => {
    if (detail) bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [detail]);

  const sendReply = async () => {
    if (!reply.trim() || !openId) return;
    setSending(true);
    try {
      await emailApi.reply(openId, {
        body: reply,
        html_body: replyHtml.trim() || null,
        attachments: replyAttachments.length ? replyAttachments : undefined,
        cc: cc
          .split(/[,;\s]+/)
          .map((s) => s.trim())
          .filter(Boolean),
      });
      setReply("");
      setReplyHtml("");
      setReplyAttachments([]);
      toast.success("Reply sent");
      await open(openId);
      load({ quiet: true });
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not send the reply");
    } finally {
      setSending(false);
    }
  };

  const setConversationStatus = async (next: string) => {
    if (!openId) return;
    try {
      await emailApi.setStatus(openId, next);
      setDetail((d: any) =>
        d ? { ...d, conversation: { ...d.conversation, status: next } } : d
      );
      load({ quiet: true });
    } catch {
      toast.error("Could not change the status");
    }
  };

  const unreadTotal = useMemo(
    () => conversations.reduce((sum, c) => sum + (c.unread_count || 0), 0),
    [conversations]
  );

  const contactName = detail?.contact?.name || detail?.contact?.email || "";
  const threadMessages: EmailMessage[] = detail?.messages || [];

  return (
    <div className="max-w-7xl mx-auto">
      {/* ---------- Header: title, count, mailbox links ---------- */}
      <div className={`flex flex-wrap items-start justify-between gap-3 mb-4 ${openId ? "hidden lg:flex" : ""}`}>
        <div>
          <p className="text-xs font-semibold tracking-wide text-primary-600 dark:text-primary-400 flex items-center gap-1.5">
            <Mail size={14} /> EMAIL CHANNEL
          </p>
          <h1 className="page-title mt-1">Inbox</h1>
          <p className="page-subtitle">
            {loading
              ? "Loading threads…"
              : unreadTotal > 0
              ? `${unreadTotal} unread · ${conversations.length} thread${conversations.length === 1 ? "" : "s"}`
              : `You are all caught up · ${conversations.length} thread${conversations.length === 1 ? "" : "s"}`}
          </p>
        </div>
        <div className="flex gap-2">
          <Link to="/email-manager" className="btn-secondary btn-sm">
            <Sparkles size={14} /> Senders &amp; tracking
          </Link>
          <Link to="/inbox" className="btn-secondary btn-sm">
            SMS inbox
          </Link>
        </div>
      </div>

      <div className="grid lg:grid-cols-[22rem_1fr] gap-4">
        {/* ---------- Thread list ---------- */}
        <section
          className={`card p-0 overflow-hidden lg:h-[calc(100dvh-11rem)] flex-col ${
            openId ? "hidden lg:flex" : "flex"
          }`}
        >
          <div className="p-3 border-b border-gray-100 dark:border-gray-700/70 space-y-2">
            <div className="relative">
              <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
              <input
                ref={searchRef}
                className="input pl-9 pr-9"
                placeholder="Search people, subject, text…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                aria-label="Search email threads"
              />
              {search && (
                <button
                  onClick={() => {
                    setSearch("");
                    searchRef.current?.focus();
                  }}
                  className="absolute right-2 top-1/2 -translate-y-1/2 p-1.5 rounded-lg text-gray-400 hover:text-gray-600"
                  aria-label="Clear search"
                >
                  <X size={14} />
                </button>
              )}
            </div>

            {/* On a phone six filter chips do not fit: the active one plus a
                picker. On sm+ they are a scrollable row. */}
            <div className="sm:hidden">
              <button className="chip w-full justify-between" onClick={() => setFilterOpen((v) => !v)}>
                <span>
                  {STATUS_FILTERS.find((f) => f.key === status)?.label || "All"}
                  {unreadTotal > 0 && status !== "unread" ? ` · ${unreadTotal} unread` : ""}
                </span>
                <ChevronDown size={14} />
              </button>
              {filterOpen && (
                <div className="mt-2 grid grid-cols-2 gap-1.5 animate-fade-in">
                  {STATUS_FILTERS.map((f) => (
                    <button
                      key={f.key}
                      onClick={() => {
                        setStatus(f.key);
                        setFilterOpen(false);
                      }}
                      className={status === f.key ? "chip-active justify-center" : "chip justify-center"}
                    >
                      {f.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <div className="hidden sm:flex items-center gap-1.5 overflow-x-auto scrollbar-none">
              {STATUS_FILTERS.map((f) => (
                <button
                  key={f.key}
                  onClick={() => setStatus(f.key)}
                  className={status === f.key ? "chip-active" : "chip"}
                >
                  {f.label}
                </button>
              ))}
              <button
                className="ml-auto p-1.5 rounded-lg text-gray-400 hover:text-primary-600 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
                onClick={() => load()}
                title="Refresh"
                aria-label="Refresh threads"
              >
                <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
              </button>
            </div>
          </div>

          <div className="flex-1 overflow-y-auto divide-y divide-gray-50 dark:divide-gray-700/50 stagger">
            {loading && conversations.length === 0 ? (
              <div className="p-3 space-y-3">
                {[0, 1, 2, 3, 4].map((i) => (
                  <div key={i} className="flex gap-3 items-start">
                    <div className="skeleton w-9 h-9 rounded-full" />
                    <div className="flex-1 space-y-2">
                      <div className="skeleton h-3.5 w-32" />
                      <div className="skeleton h-3 w-full" />
                    </div>
                  </div>
                ))}
              </div>
            ) : conversations.length === 0 ? (
              <div className="p-8 text-center">
                <span className="mx-auto w-12 h-12 rounded-full bg-primary-50 text-primary-600 dark:bg-primary-950/60 dark:text-primary-300 flex items-center justify-center mb-3">
                  <Inbox size={22} />
                </span>
                <p className="font-medium text-gray-800 dark:text-gray-200">
                  {search ? "No thread matches that search" : "No email threads yet"}
                </p>
                <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
                  {search
                    ? "Try a different name, subject or word."
                    : "Replies to your campaigns land here. Send one from the Email Manager, or connect a Gmail mailbox under Email Manager → Replies."}
                </p>
              </div>
            ) : (
              conversations.map((c) => {
                const unread = c.unread_count > 0;
                const active = openId === c.id;
                return (
                  <button
                    key={c.id}
                    onClick={() => open(c.id)}
                    className={`stagger-item w-full text-left px-3 py-3 flex gap-3 items-start transition-colors ${
                      active
                        ? "bg-primary-50 dark:bg-primary-950/30"
                        : "hover:bg-gray-50 dark:hover:bg-gray-700/30"
                    }`}
                  >
                    <span className="relative shrink-0">
                      <span
                        className={`w-9 h-9 rounded-full flex items-center justify-center text-xs font-semibold ${toneFor(
                          c.contact_email || String(c.id)
                        )}`}
                      >
                        {initialsOf(c.contact_name, c.contact_email)}
                      </span>
                      {unread && (
                        <span className="absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full bg-primary-500 ring-2 ring-white dark:ring-gray-800" />
                      )}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="flex items-baseline gap-2">
                        <span
                          className={`truncate text-sm ${
                            unread
                              ? "font-bold text-gray-900 dark:text-white"
                              : "font-medium text-gray-700 dark:text-gray-200"
                          }`}
                        >
                          {c.contact_name || c.contact_email || "Unknown"}
                        </span>
                        <span className="ml-auto text-[11px] text-gray-400 shrink-0">
                          {when(c.last_message_at)}
                        </span>
                      </span>
                      <span className="block text-xs text-gray-600 dark:text-gray-400 truncate mt-0.5">
                        <span className={unread ? "font-semibold text-gray-800 dark:text-gray-200" : ""}>
                          {c.subject || "(no subject)"}
                        </span>
                        {c.preview ? <span className="text-gray-400"> — {c.preview}</span> : null}
                      </span>
                      <span className="flex items-center gap-1.5 mt-1 flex-wrap">
                        {unread && <span className="badge-blue">{c.unread_count} new</span>}
                        {c.status !== "active" && c.status !== "unread" && STATUS_TONE[c.status] && (
                          <span className={STATUS_TONE[c.status]}>
                            {STATUS_FILTERS.find((f) => f.key === c.status)?.label || c.status}
                          </span>
                        )}
                        {c.message_count > 1 && (
                          <span className="text-[11px] text-gray-400">{c.message_count} messages</span>
                        )}
                        {c.email_account_name && (
                          <span className="text-[11px] text-gray-400 truncate">
                            {c.email_account_name}
                          </span>
                        )}
                      </span>
                    </span>
                  </button>
                );
              })
            )}
          </div>
        </section>

        {/* ---------- Reading pane ---------- */}
        <section
          className={`card p-0 overflow-hidden lg:h-[calc(100dvh-11rem)] flex-col ${
            openId ? "flex" : "hidden lg:flex"
          }`}
        >
          {!openId ? (
            <div className="flex-1 flex flex-col items-center justify-center text-center p-10">
              <span className="w-14 h-14 rounded-2xl bg-primary-50 text-primary-600 dark:bg-primary-950/60 dark:text-primary-300 flex items-center justify-center mb-4">
                <MailOpen size={26} />
              </span>
              <p className="font-medium text-gray-800 dark:text-gray-200">Pick a conversation</p>
              <p className="text-sm text-gray-500 dark:text-gray-400 mt-1 max-w-sm">
                Choose an email on the left to read the whole thread, see whether it was opened or
                clicked, and answer without leaving the app.
              </p>
            </div>
          ) : loadingDetail && !detail ? (
            <div className="p-4 space-y-3">
              <div className="skeleton h-5 w-2/3" />
              <div className="skeleton h-24 w-full" />
              <div className="skeleton h-24 w-full" />
            </div>
          ) : detail ? (
            <>
              {/* Thread header */}
              <div className="p-3 sm:p-4 border-b border-gray-100 dark:border-gray-700/70 space-y-2">
                <div className="flex items-start gap-2">
                  <button
                    className="lg:hidden p-2 -ml-1 rounded-xl text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800 shrink-0"
                    onClick={closeThread}
                    aria-label="Back to threads"
                  >
                    <ArrowLeft size={18} />
                  </button>
                  <div className="min-w-0 flex-1">
                    <h2 className="font-semibold text-gray-900 dark:text-white break-anywhere">
                      {detail.conversation.subject || detail.contact?.name || "Conversation"}
                    </h2>
                    <p className="text-sm text-gray-500 dark:text-gray-400 flex items-center gap-1.5 flex-wrap">
                      <span className="font-medium text-gray-700 dark:text-gray-300">
                        {detail.contact?.name || "Unknown"}
                      </span>
                      <span className="text-gray-400">&lt;{detail.contact?.email}&gt;</span>
                      {detail.conversation.email_account_name && (
                        <span className="badge-gray">{detail.conversation.email_account_name}</span>
                      )}
                    </p>
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0">
                    <select
                      className="input py-1.5 text-sm w-auto max-w-[9.5rem]"
                      value={detail.conversation.status}
                      onChange={(e) => setConversationStatus(e.target.value)}
                      aria-label="Conversation status"
                    >
                      {STATUS_FILTERS.filter((f) => !["all", "unread"].includes(f.key)).map((f) => (
                        <option key={f.key} value={f.key}>
                          {f.label}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    className="chip"
                    onClick={() => setConversationStatus("interested")}
                    title="Mark as interested"
                  >
                    <Star size={12} /> Interested
                  </button>
                  <button
                    className="chip"
                    onClick={() => setConversationStatus("closed")}
                    title="Mark as closed"
                  >
                    <CheckCheck size={12} /> Done
                  </button>
                  {detail.contact?.phone_number && !detail.contact.phone_number.startsWith("email:") && (
                    <span className="text-[11px] text-gray-400">{detail.contact.phone_number}</span>
                  )}
                </div>
                {detail.contact?.is_email_opted_out && (
                  <p className="text-xs text-warning-600 dark:text-warning-400 flex items-center gap-1">
                    <XCircle size={13} /> This contact unsubscribed — replies are blocked until they
                    are opted back in on the Contacts page.
                  </p>
                )}
              </div>

              {/* Messages */}
              <div className="flex-1 overflow-y-auto p-3 sm:p-4 space-y-3 bg-gray-50/60 dark:bg-gray-900/30 wa-scrollbar">
                {threadMessages.map((m, index) => (
                  <MessageCard
                    key={m.id}
                    message={m}
                    contactName={contactName}
                    // Only the newest message is open by default, like Gmail.
                    defaultOpen={index === threadMessages.length - 1}
                    onOpenActivity={() => emailApi.messageEvents(m.id)}
                  />
                ))}
                <div ref={bottomRef} />
              </div>

              {/* Composer */}
              <div className="border-t border-gray-100 dark:border-gray-700/70 p-3 sm:p-4 space-y-2 safe-bottom">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-xs font-medium text-gray-500 dark:text-gray-400 flex items-center gap-1.5">
                    <Reply size={13} /> Reply
                  </p>
                  <div className="flex items-center gap-2">
                    <button
                      className="text-xs text-gray-500 hover:text-primary-600"
                      onClick={() => setShowCc((v) => !v)}
                    >
                      Cc
                    </button>
                    <span className="text-[11px] text-gray-400 hidden sm:inline">
                      sent from {detail.conversation.email_account_name || "the default sender"}
                    </span>
                  </div>
                </div>
                {showCc && (
                  <input
                    className="input animate-fade-in"
                    placeholder="Cc — separate several addresses with a comma"
                    value={cc}
                    onChange={(e) => setCc(e.target.value)}
                  />
                )}
                <RichEmailEditor
                  body={reply}
                  onBody={setReply}
                  html={replyHtml}
                  onHtml={setReplyHtml}
                  attachments={replyAttachments}
                  onAttachments={setReplyAttachments}
                />
                <div className="flex items-center justify-between gap-3">
                  <p className="text-[11px] text-gray-400 hidden sm:flex items-center gap-1">
                    <Sparkles size={12} /> Ctrl/⌘ + Enter sends
                  </p>
                  <button
                    className="btn-primary w-full sm:w-auto"
                    onClick={sendReply}
                    disabled={sending || !reply.trim()}
                  >
                    {sending ? (
                      <>Sending…</>
                    ) : (
                      <>
                        <Send size={15} /> Send reply
                      </>
                    )}
                  </button>
                </div>
              </div>
            </>
          ) : (
            <div className="flex-1 flex items-center justify-center p-10 text-gray-500">
              <span className="text-sm flex items-center gap-2">
                <XCircle size={15} /> This conversation could not be loaded.
                <button className="text-primary-600 hover:underline" onClick={() => openId && open(openId)}>
                  Retry
                </button>
              </span>
            </div>
          )}
        </section>
      </div>

      <p className="text-xs text-gray-400 mt-3 flex items-center gap-1.5">
        <ArrowUpRight size={12} /> Opens and clicks are recorded by Brevo; when a Gmail mailbox is
        connected under Email Manager → Replies, replies are sent from that mailbox so they thread
        correctly in the prospect's Gmail.
      </p>
    </div>
  );
}
