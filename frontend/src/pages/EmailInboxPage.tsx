import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams, Link } from "react-router-dom";
import toast from "react-hot-toast";
import {
  ArrowLeft,
  ArrowUpRight,
  Building2,
  CalendarPlus,
  CheckCheck,
  ChevronDown,
  ChevronUp,
  Clock,
  Globe,
  Inbox,
  Mail,
  MailOpen,
  MapPin,
  MessageCircle,
  MousePointerClick,
  Paperclip,
  Phone,
  RefreshCw,
  Reply,
  Search,
  Send,
  Sparkles,
  Star,
  Briefcase,
  Video,
  X,
  XCircle,
} from "lucide-react";
import api from "../api/client";
import emailApi, { EmailContactDetails, EmailConversation, EmailMessage } from "../api/email";
import MeetingModal from "../components/MeetingModal";
import RichEmailEditor, { AttachmentPayload } from "../components/RichEmailEditor";
import { openWebsite, openWhatsappChat, openWhatsappForCall, startCall, websiteUrl } from "../utils/call";
import type { Meeting } from "../types";

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
 *
 * TWO PAGES IN ONE
 * ----------------
 * `?view=replied` is the **Conversations** page: only the people who actually
 * wrote back, i.e. the threads you are in a conversation with. Everything the
 * campaign sent and nobody answered stays on the default page, so "who am I
 * talking to?" is one click and never a scroll through fifty one-way sends.
 * The same URL takes `campaign_id` and `contact_id`, which is where a campaign's
 * reply list sends you — the list arrives pre-filtered and that person's thread
 * opens itself.
 */

const STATUS_FILTERS = [
  { key: "all", label: "All" },
  { key: "unread", label: "Unread" },
  { key: "active", label: "Open" },
  { key: "interested", label: "Interested" },
  { key: "opportunity", label: "Opportunity" },
  { key: "not_interested", label: "Not interested" },
  { key: "closed", label: "Closed" },
];

const STATUS_TONE: Record<string, string> = {
  interested: "badge-green",
  opportunity: "badge-green",
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

function hasSmsPhone(phone?: string | null): boolean {
  const value = (phone || "").trim();
  return Boolean(value && !value.toLowerCase().startsWith("email:") && /\d/.test(value));
}

function meetingTime(value?: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString([], {
    weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
  });
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
          <div className="email-body-content rounded-xl p-3.5 text-sm">
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
  /** Multi-inbox: "" = every sender, otherwise one unified sender id. */
  const [senderFilter, setSenderFilter] = useState("");
  const [senders, setSenders] = useState<{ id: number; name: string; from_email: string }[]>([]);
  const [showContactInfo, setShowContactInfo] = useState(false);
  const [showBooking, setShowBooking] = useState(false);
  const [editingMeetingId, setEditingMeetingId] = useState<number | null>(null);
  const [contactMeetings, setContactMeetings] = useState<Meeting[]>([]);
  /** Threads the contact replied to — the number on the Conversations tab. */
  const [repliedTotal, setRepliedTotal] = useState<number | null>(null);
  /** Every thread, unfiltered — the number on the All mail tab. */
  const [threadTotal, setThreadTotal] = useState<number | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  /** Which deep link already opened its thread, so it only happens once. */
  const autoOpened = useRef<string | null>(null);

  const deepLinkedId = params.get("conversation_id");
  /** "all" = every thread, "replied" = the Conversations page. */
  const view: "all" | "replied" = params.get("view") === "replied" ? "replied" : "all";
  const campaignFilter = params.get("campaign_id");
  const contactFilter = params.get("contact_id");

  /** Switch page without losing the rest of the URL (search lives in state). */
  const setView = (next: "all" | "replied") => {
    const updated = new URLSearchParams(params);
    if (next === "replied") updated.set("view", "replied");
    else updated.delete("view");
    setParams(updated);
  };

  /** Drop the campaign/person a deep link arrived with. */
  const clearScope = () => {
    const updated = new URLSearchParams(params);
    updated.delete("campaign_id");
    updated.delete("contact_id");
    setParams(updated);
  };

  const load = useCallback(
    async (opts: { quiet?: boolean } = {}) => {
      if (!opts.quiet) setLoading(true);
      try {
        const data = await emailApi.conversations({
          status: status === "all" ? undefined : status,
          search: search || undefined,
          replied_only: view === "replied" || undefined,
          campaign_id: campaignFilter ? Number(campaignFilter) : undefined,
          contact_id: contactFilter ? Number(contactFilter) : undefined,
          account_id: senderFilter ? Number(senderFilter) : undefined,
          per_page: 50,
        });
        setConversations(data.items);
        if (typeof data.replied_total === "number") setRepliedTotal(data.replied_total);
        if (typeof data.thread_total === "number") setThreadTotal(data.thread_total);
      } catch {
        if (!opts.quiet) toast.error("Could not load the email inbox");
      } finally {
        setLoading(false);
      }
    },
    [status, search, view, campaignFilter, contactFilter, senderFilter]
  );

  // The senders behind the multi-inbox filter (Brevo accounts + mailboxes).
  useEffect(() => {
    emailApi
      .listSenders()
      .then((data) => setSenders(data.items || []))
      .catch(() => setSenders([]));
  }, []);

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
      setShowContactInfo(false);
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

  // Deep link by *person*: a campaign's reply list knows the contact, not the
  // thread id, so open their conversation as soon as the filtered list lands.
  useEffect(() => {
    if (openId || loading || deepLinkedId) return;
    if (!contactFilter && !campaignFilter) return;
    const key = `${contactFilter || ""}|${campaignFilter || ""}`;
    if (autoOpened.current === key) return;
    const match = contactFilter
      ? conversations.find((c) => String(c.contact_id) === contactFilter)
      : conversations[0];
    if (!match) return;
    autoOpened.current = key;
    void open(match.id);
  }, [conversations, contactFilter, campaignFilter, loading, openId, deepLinkedId, open]);

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

  const loadContactMeetings = useCallback(async (contactId: number) => {
    try {
      const { data } = await api.get("/calendar/upcoming", {
        params: { contact_id: contactId, limit: 5 },
      });
      setContactMeetings(data.items || []);
    } catch {
      setContactMeetings([]);
    }
  }, []);

  useEffect(() => {
    const contactId = Number(detail?.contact?.id || 0);
    if (!contactId) {
      setContactMeetings([]);
      return;
    }
    let cancelled = false;
    api.get("/calendar/upcoming", { params: { contact_id: contactId, limit: 5 } })
      .then(({ data }) => { if (!cancelled) setContactMeetings(data.items || []); })
      .catch(() => { if (!cancelled) setContactMeetings([]); });
    return () => { cancelled = true; };
  }, [detail?.conversation?.id, detail?.contact?.id]);

  const unreadTotal = useMemo(
    () => conversations.reduce((sum, c) => sum + (c.unread_count || 0), 0),
    [conversations]
  );

  const contactName = detail?.contact?.name || detail?.contact?.email || "";
  const contact: EmailContactDetails | null = detail?.contact || null;
  const threadMessages: EmailMessage[] = detail?.messages || [];
  const contactHasPhone = hasSmsPhone(contact?.phone_number);

  return (
    <div className="email-inbox max-w-7xl mx-auto rounded-2xl">
      {/* ---------- Header: title, count, mailbox links ---------- */}
      <div className={`flex flex-wrap items-start justify-between gap-3 mb-4 ${openId ? "hidden lg:flex" : ""}`}>
        <div className="min-w-0">
          <p className="text-xs font-semibold tracking-wide text-primary-600 dark:text-primary-400 flex items-center gap-1.5">
            <Mail size={14} /> EMAIL CHANNEL
          </p>
          <h1 className="page-title mt-1">{view === "replied" ? "Conversations" : "Inbox"}</h1>
          <p className="page-subtitle">
            {loading
              ? "Loading threads…"
              : view === "replied"
              ? `${conversations.length} of ${repliedTotal ?? conversations.length} people who wrote back — everyone here is a live conversation`
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
            SMS Inbox
          </Link>
        </div>
      </div>

      {/* ---------- The two pages of the email inbox ----------
          All mail is everything that happened; Conversations is only the people
          who replied, which is the list you actually work from. Both are the
          same screen with one filter, so a thread you open keeps its place. */}
      <div className={`mb-4 ${openId ? "hidden lg:block" : ""}`}>
        <div className="inline-flex gap-1 p-1 rounded-xl bg-gray-100 dark:bg-gray-700/70 w-full sm:w-auto">
          {([
            {
              key: "all" as const,
              label: "All mail",
              count: threadTotal,
              icon: Inbox,
              title: "Every email thread, including the ones nobody answered",
            },
            {
              key: "replied" as const,
              label: "Conversations",
              count: repliedTotal,
              icon: MessageCircle,
              title: "Only people who replied — the conversations you are in",
            },
          ]).map((tab) => (
            <button
              key={tab.key}
              onClick={() => setView(tab.key)}
              title={tab.title}
              aria-pressed={view === tab.key}
              className={`flex-1 sm:flex-none px-3.5 py-2 rounded-lg text-sm font-medium transition-colors flex items-center justify-center gap-1.5 ${
                view === tab.key
                  ? "bg-white dark:bg-gray-800 shadow-sm text-primary-600"
                  : "text-gray-600 dark:text-gray-300 hover:text-gray-900 dark:hover:text-white"
              }`}
            >
              <tab.icon size={14} />
              {tab.label}
              {tab.count !== null && tab.count !== undefined && (
                <span
                  className={`text-[11px] px-1.5 rounded-full ${
                    view === tab.key
                      ? "bg-primary-100 text-primary-700 dark:bg-primary-950/70 dark:text-primary-300"
                      : "bg-gray-200 text-gray-600 dark:bg-gray-600 dark:text-gray-200"
                  }`}
                >
                  {tab.count}
                </span>
              )}
            </button>
          ))}
        </div>
        {view === "replied" && (
          <p className="text-xs text-gray-500 dark:text-gray-400 mt-2">
            Only people who wrote back are on this page. Campaign mail nobody answered is still on
            the All mail tab —{" "}
            <button className="text-primary-600 hover:underline" onClick={() => setView("all")}>
              see every thread
            </button>
            .
          </p>
        )}
        {(campaignFilter || contactFilter) && (
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs rounded-xl border border-primary-200 dark:border-primary-900 bg-primary-50 dark:bg-primary-950/30 px-3 py-2">
            <span className="text-primary-700 dark:text-primary-300 font-medium">
              {contactFilter
                ? "Showing one person's thread"
                : `Showing the threads from campaign #${campaignFilter}`}
            </span>
            <span className="text-gray-500 dark:text-gray-400">
              {conversations.length} shown{view === "replied" ? " · only the ones who replied" : ""}
            </span>
            <button onClick={clearScope} className="chip ml-auto">
              <X size={12} /> Show every thread
            </button>
          </div>
        )}
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

            {senders.length > 1 && (
              <select
                className="input py-1.5 text-sm"
                value={senderFilter}
                onChange={(e) => setSenderFilter(e.target.value)}
                aria-label="Filter inbox by sender"
              >
                <option value="">All senders (every inbox)</option>
                {senders.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name} — {s.from_email}
                  </option>
                ))}
              </select>
            )}

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
                  {search
                    ? "No thread matches that search"
                    : view === "replied"
                    ? "No conversations yet"
                    : "No email threads yet"}
                </p>
                <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
                  {search
                    ? "Try a different name, subject or word."
                    : view === "replied"
                    ? "This page only holds people who wrote back. As soon as a reply arrives it appears here — and if your prospects reply to a Gmail address, connect that inbox under Settings → Gmail replies so nothing is missed."
                    : "Replies to your campaigns land here. Send one from the Email Manager, or connect a Gmail mailbox under Settings → Gmail replies."}
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
                    className={`email-list-item stagger-item w-full text-left px-3 py-3 flex gap-3 items-start transition-colors ${
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
                      <span className="email-body-preview block text-xs truncate mt-0.5">
                        <span className={`email-subject ${unread ? "font-semibold" : ""}`}>
                          {c.subject || "(no subject)"}
                        </span>
                        {c.preview ? <span> — {c.preview}</span> : null}
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
                    className={detail.conversation.status === "opportunity" ? "chip-active" : "chip"}
                    onClick={() =>
                      setConversationStatus(detail.conversation.status === "opportunity" ? "active" : "opportunity")
                    }
                    title={detail.conversation.status === "opportunity" ? "Remove from Opportunity" : "Move to Opportunity"}
                  >
                    <Briefcase size={12} />
                    {detail.conversation.status === "opportunity" ? "In Opportunity" : "Opportunity"}
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
                <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
                  <div role="tablist" aria-label="Email conversation views" className="flex gap-1 rounded-xl bg-gray-100 dark:bg-gray-900 p-1">
                    <button
                      type="button"
                      role="tab"
                      aria-selected={!showContactInfo}
                      onClick={() => setShowContactInfo(false)}
                      className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
                        !showContactInfo ? "bg-white dark:bg-gray-700 text-primary-700 dark:text-primary-300 shadow-sm" : "text-gray-500 hover:text-gray-800 dark:hover:text-gray-200"
                      }`}
                    >Conversation</button>
                    <button
                      type="button"
                      role="tab"
                      aria-selected={showContactInfo}
                      onClick={() => setShowContactInfo(true)}
                      className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
                        showContactInfo ? "bg-white dark:bg-gray-700 text-primary-700 dark:text-primary-300 shadow-sm" : "text-gray-500 hover:text-gray-800 dark:hover:text-gray-200"
                      }`}
                    >Contact information</button>
                  </div>
                  <button
                    type="button"
                    onClick={() => setShowBooking(true)}
                    className="btn-primary py-2 px-3 text-xs"
                    title="Book a meeting"
                  >
                    <CalendarPlus size={14} /> Book meeting
                  </button>
                </div>
              </div>

              {showContactInfo ? (
                <div className="flex-1 overflow-y-auto p-4 sm:p-5 space-y-5 bg-gray-50/60 dark:bg-gray-900/30 wa-scrollbar" role="tabpanel" aria-label="Contact information">
                  {contact ? (
                    <>
                      <section className="rounded-2xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 p-4">
                        <div className="flex items-start gap-3">
                          <span className={`w-12 h-12 rounded-2xl flex items-center justify-center text-sm font-bold shrink-0 ${toneFor(contact.email || String(contact.id))}`}>
                            {initialsOf(contact.name, contact.email)}
                          </span>
                          <div className="min-w-0 flex-1">
                            <h3 className="font-semibold text-gray-900 dark:text-white">{contact.name}</h3>
                            {contact.business_name && <p className="text-sm text-gray-500 dark:text-gray-400 flex items-center gap-1.5"><Building2 size={13} /> {contact.business_name}</p>}
                            <div className="flex flex-wrap gap-1.5 mt-2">
                              {contact.lead_status && <span className="badge-blue capitalize">{contact.lead_status.replace(/_/g, " ")}</span>}
                              {contact.email_status && <span className="badge-gray capitalize">Email {contact.email_status}</span>}
                              {contact.is_email_undeliverable && <span className="badge-red">Undeliverable</span>}
                            </div>
                          </div>
                        </div>
                        <div className="flex flex-wrap gap-2 mt-4">
                          {hasSmsPhone(contact.phone_number) && (
                            <>
                              <button type="button" onClick={() => startCall({ contact_id: contact.id })} className="chip text-primary-700 dark:text-primary-300"><Phone size={13} /> Call</button>
                              <button type="button" onClick={() => openWhatsappChat(contact.phone_number!)} className="chip text-primary-700 dark:text-primary-300"><MessageCircle size={13} /> WhatsApp</button>
                              <button type="button" onClick={() => openWhatsappForCall(contact.phone_number!, contact.name)} className="chip"><Video size={13} /> WhatsApp call</button>
                            </>
                          )}
                          {websiteUrl(contact.website) && (
                            <button type="button" onClick={() => openWebsite(contact.website)} className="chip text-primary-700 dark:text-primary-300"><Globe size={13} /> Website</button>
                          )}
                        </div>
                      </section>

                      <section className="rounded-2xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 p-4">
                        <h3 className="text-sm font-semibold text-gray-900 dark:text-white mb-3">Contact details</h3>
                        <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-3 text-sm">
                          {contact.phone_number && hasSmsPhone(contact.phone_number) && (
                            <div><dt className="text-xs text-gray-400">Phone</dt><dd className="mt-0.5 text-gray-800 dark:text-gray-200">{contact.phone_number}</dd></div>
                          )}
                          {contact.email && (
                            <div><dt className="text-xs text-gray-400">Primary email</dt><dd className="mt-0.5 break-all"><a className="text-primary-600 dark:text-primary-400 hover:underline" href={`mailto:${contact.email}`}>{contact.email}</a></dd></div>
                          )}
                          {(contact.email_aliases || []).length > 0 && (
                            <div className="sm:col-span-2"><dt className="text-xs text-gray-400">Alternate email addresses</dt><dd className="mt-1 flex flex-wrap gap-1.5">
                              {(contact.email_aliases || []).map((address) => <a key={address} href={`mailto:${address}`} className="chip text-primary-600 dark:text-primary-300 break-all">{address}<span className="text-[10px] text-gray-400">used in replies</span></a>)}
                            </dd></div>
                          )}
                          {contact.website && <div><dt className="text-xs text-gray-400">Website</dt><dd className="mt-0.5 break-all"><a className="text-primary-600 dark:text-primary-400 hover:underline" href={websiteUrl(contact.website) || undefined} target="_blank" rel="noreferrer">{contact.website}</a></dd></div>}
                          {contact.industry && <div><dt className="text-xs text-gray-400">Industry</dt><dd className="mt-0.5 text-gray-800 dark:text-gray-200">{contact.industry}</dd></div>}
                          {(contact.city || contact.state || contact.country) && <div><dt className="text-xs text-gray-400 flex items-center gap-1"><MapPin size={11} /> Location</dt><dd className="mt-0.5 text-gray-800 dark:text-gray-200">{[contact.city, contact.state, contact.country].filter(Boolean).join(", ")}</dd></div>}
                          {contact.source && <div><dt className="text-xs text-gray-400">Source</dt><dd className="mt-0.5 text-gray-800 dark:text-gray-200">{contact.source}</dd></div>}
                        </dl>
                        {contact.notes && <div className="mt-4 pt-3 border-t border-gray-100 dark:border-gray-700"><p className="text-xs text-gray-400">Notes</p><p className="mt-1 text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap">{contact.notes}</p></div>}
                        {Object.keys(contact.custom_fields || {}).length > 0 && (
                          <div className="mt-4 pt-3 border-t border-gray-100 dark:border-gray-700">
                            <p className="text-xs text-gray-400 mb-2">Other saved details</p>
                            <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2 text-sm">
                              {Object.entries(contact.custom_fields || {}).map(([key, value]) => (
                                <div key={key}><dt className="text-xs text-gray-400 capitalize">{key.replace(/_/g, " ")}</dt><dd className="mt-0.5 break-words text-gray-800 dark:text-gray-200">{typeof value === "string" ? value : JSON.stringify(value)}</dd></div>
                              ))}
                            </dl>
                          </div>
                        )}
                        {(contact.tags || []).length > 0 && (
                          <div className="mt-4 pt-3 border-t border-gray-100 dark:border-gray-700">
                            <p className="text-xs text-gray-400 mb-2">Tags</p>
                            <div className="flex flex-wrap gap-1.5">{(contact.tags || []).map((tag) => <span key={tag} className="badge-gray">{tag}</span>)}</div>
                          </div>
                        )}
                      </section>

                      <section className="rounded-2xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 p-4">
                        <div className="flex items-center justify-between gap-2 mb-3">
                          <div><h3 className="text-sm font-semibold text-gray-900 dark:text-white">Upcoming meetings</h3><p className="text-xs text-gray-400 mt-0.5">Bookings linked to this contact</p></div>
                          <button type="button" onClick={() => setShowBooking(true)} className="btn-primary py-2 px-3 text-xs"><CalendarPlus size={14} /> Book</button>
                        </div>
                        {contactMeetings.length === 0 ? (
                          <p className="text-sm text-gray-500 dark:text-gray-400 py-2">No upcoming meetings booked.</p>
                        ) : (
                          <div className="space-y-2">
                            {contactMeetings.map((meeting) => (
                              <button key={meeting.id} type="button" onClick={() => setEditingMeetingId(meeting.id)} className="w-full rounded-xl border border-gray-100 dark:border-gray-700 px-3 py-2.5 text-left hover:bg-gray-50 dark:hover:bg-gray-700/50 flex items-center justify-between gap-3">
                                <span className="min-w-0"><span className="block text-sm font-medium text-gray-900 dark:text-white truncate">{meeting.title}</span><span className="block text-xs text-gray-500 dark:text-gray-400 mt-0.5">{meetingTime(meeting.starts_at)}{meeting.location ? ` · ${meeting.location}` : ""}</span></span>
                                <span className="badge-gray capitalize shrink-0">{meeting.status.replace(/_/g, " ")}</span>
                              </button>
                            ))}
                          </div>
                        )}
                      </section>
                    </>
                  ) : (
                    <p className="text-sm text-gray-500">No contact record is linked to this conversation.</p>
                  )}
                </div>
              ) : (
                <>
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
              )}
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

      {showBooking && contact && (
        <MeetingModal
          initial={{
            contactIds: [contact.id],
            conversationId: openId || undefined,
            sendInvite: contactHasPhone,
            sendReminder: contactHasPhone,
          }}
          onClose={() => setShowBooking(false)}
          onSaved={() => {
            setShowBooking(false);
            void loadContactMeetings(contact.id);
          }}
        />
      )}
      {editingMeetingId !== null && (
        <MeetingModal
          meetingId={editingMeetingId}
          onClose={() => setEditingMeetingId(null)}
          onSaved={() => {
            setEditingMeetingId(null);
            if (contact) void loadContactMeetings(contact.id);
          }}
        />
      )}
    </div>
  );
}
