import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import toast from "react-hot-toast";
import { Link } from "react-router-dom";
import {
  ArrowLeft,
  CheckCheck,
  Mail,
  MousePointerClick,
  Paperclip,
  RefreshCw,
  Search,
  Send,
  Sparkles,
  XCircle,
} from "lucide-react";
import emailApi, { EmailConversation, EmailMessage } from "../api/email";
import { Badge, Empty, fmtDate } from "./ads/ui";
import RichEmailEditor, { AttachmentPayload } from "../components/RichEmailEditor";

/**
 * EMAIL INBOX
 *
 * The email twin of the SMS inbox: threads on the left, the conversation on the
 * right, and a reply box that sends from the same Brevo sender the thread
 * started on. Opens and clicks are shown per message so the state of every
 * lead is obvious without leaving the thread.
 */

const STATUS_FILTERS = [
  { key: "all", label: "All" },
  { key: "unread", label: "Unread" },
  { key: "active", label: "Open" },
  { key: "interested", label: "Interested" },
  { key: "not_interested", label: "Not interested" },
  { key: "closed", label: "Closed" },
];

/** Collapsible "what happened to this email" panel, per message.
 *
 * Brevo reports opens and clicks per recipient; this shows the same thing in
 * the thread, including which link was clicked, loaded only when expanded.
 */
function MessageActivity({
  message,
  onOpen,
  outgoing,
}: {
  message: EmailMessage;
  onOpen: () => Promise<any>;
  outgoing: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);

  const opens = message.open_count || 0;
  const clicks = message.click_count || 0;
  if (!outgoing || (opens === 0 && clicks === 0 && message.status !== "delivered")) {
    return null;
  }

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
        className={`text-[11px] underline decoration-dotted ${
          outgoing ? "text-primary-100" : "text-gray-500"
        }`}
      >
        {opens > 0 ? `${opens} open${opens === 1 ? "" : "s"}` : "no opens yet"}
        {" · "}
        {clicks > 0 ? `${clicks} click${clicks === 1 ? "" : "s"}` : "no clicks yet"}
        {open ? " ▲" : " ▼"}
      </button>
      {open && (
        <div className="mt-1 space-y-1">
          {loading && <p className="text-[11px] opacity-70">Reading Brevo…</p>}
          {(data?.events || []).map((e: any) => (
            <p key={e.id} className="text-[11px] opacity-80 truncate">
              {e.event_type}
              {e.link ? `: ${e.link}` : ""}
            </p>
          ))}
          {(data?.summary?.unique_links_clicked || 0) > 0 && (
            <p className="text-[11px] opacity-70">
              {data.summary.unique_links_clicked} different link(s) clicked
            </p>
          )}
        </div>
      )}
    </div>
  );
}

export default function EmailInboxPage() {
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
  const [sending, setSending] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await emailApi.conversations({
        status: status === "all" ? undefined : status,
        search: search || undefined,
        per_page: 50,
      });
      setConversations(data.items);
    } catch {
      toast.error("Could not load the email inbox");
    } finally {
      setLoading(false);
    }
  }, [status, search]);

  useEffect(() => {
    load();
  }, [load]);

  // Light polling keeps the unread badge and new mail fresh without a socket.
  useEffect(() => {
    const timer = window.setInterval(() => {
      emailApi
        .conversations({ status: status === "all" ? undefined : status, search: search || undefined, per_page: 50 })
        .then((data) => setConversations(data.items))
        .catch(() => {});
    }, 30000);
    return () => window.clearInterval(timer);
  }, [status, search]);

  const open = useCallback(
    async (id: number) => {
      setOpenId(id);
      setLoadingDetail(true);
      setReply("");
      setReplyHtml("");
      setReplyAttachments([]);
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

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [detail]);

  const sendReply = async () => {
    if (!reply.trim() || !openId) return;
    setSending(true);
    try {
      await emailApi.reply(openId, {
        body: reply,
        html_body: replyHtml.trim() || null,
        attachments: replyAttachments.length ? replyAttachments : undefined,
      });
      setReply("");
      setReplyHtml("");
      setReplyAttachments([]);
      toast.success("Reply sent");
      await open(openId);
      load();
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
      load();
    } catch {
      toast.error("Could not change the status");
    }
  };

  const unreadTotal = useMemo(
    () => conversations.reduce((sum, c) => sum + (c.unread_count || 0), 0),
    [conversations]
  );

  return (
    <div className="space-y-5 max-w-6xl mx-auto">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm font-medium text-primary-600 mb-1 flex items-center gap-1">
            <Mail size={15} /> EMAIL CHANNEL · BREVO
          </p>
          <h1 className="text-2xl sm:text-3xl font-bold">Email Inbox</h1>
          <p className="text-gray-500 mt-1">
            Every reply lands here. {unreadTotal > 0 ? `${unreadTotal} unread.` : "You are all caught up."}
          </p>
        </div>
        <div className="flex gap-2">
          <Link to="/email-manager" className="btn-secondary">
            Email Manager
          </Link>
          <Link to="/inbox" className="btn-secondary">
            SMS inbox
          </Link>
        </div>
      </div>

      <div className="grid lg:grid-cols-[320px_1fr] gap-4">
        {/* Thread list */}
        <div className="card p-0 overflow-hidden">
          <div className="p-3 border-b border-gray-100 dark:border-gray-800 space-y-2">
            <div className="relative">
              <Search size={15} className="absolute left-3 top-2.5 text-gray-400" />
              <input
                className="input pl-9"
                placeholder="Search email threads…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
            <div className="flex gap-1 overflow-x-auto scrollbar-none">
              {STATUS_FILTERS.map((f) => (
                <button
                  key={f.key}
                  onClick={() => setStatus(f.key)}
                  className={`px-2 py-1 rounded text-xs whitespace-nowrap ${
                    status === f.key ? "bg-primary-600 text-white" : "text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800"
                  }`}
                >
                  {f.label}
                </button>
              ))}
              <button className="ml-auto text-gray-400 hover:text-primary-600 px-1" onClick={load}>
                <RefreshCw size={14} />
              </button>
            </div>
          </div>

          <div className="max-h-[70vh] overflow-y-auto divide-y divide-gray-100 dark:divide-gray-800">
            {loading ? (
              <div className="p-6 text-center text-sm text-gray-500">Loading…</div>
            ) : conversations.length === 0 ? (
              <div className="p-6">
                <Empty
                  title="No email threads"
                  body="Replies to your campaigns and inbound mail land here once a Brevo webhook is pointed at this app."
                />
              </div>
            ) : (
              conversations.map((c) => (
                <button
                  key={c.id}
                  onClick={() => open(c.id)}
                  className={`w-full text-left p-3 hover:bg-gray-50 dark:hover:bg-gray-800/50 ${
                    openId === c.id ? "bg-primary-50 dark:bg-primary-900/20" : ""
                  }`}
                >
                  <div className="flex items-start justify-between gap-2">
                    <p className="font-medium truncate text-sm">{c.contact_name || c.contact_email}</p>
                    <span className="text-[11px] text-gray-400 shrink-0">{fmtDate(c.last_message_at)}</span>
                  </div>
                  <p className="text-xs text-gray-500 truncate">{c.subject || "(no subject)"}</p>
                  <p className="text-xs text-gray-400 truncate">{c.preview}</p>
                  <div className="flex items-center gap-2 mt-1">
                    {c.unread_count > 0 && <span className="badge-red">{c.unread_count} new</span>}
                    {c.status === "interested" && <span className="badge-green">interested</span>}
                    {c.status === "not_interested" && <span className="badge-gray">not interested</span>}
                    {c.email_account_name && (
                      <span className="text-[11px] text-gray-400 truncate">{c.email_account_name}</span>
                    )}
                  </div>
                </button>
              ))
            )}
          </div>
        </div>

        {/* Thread */}
        <div className="card p-0 overflow-hidden">
          {!openId ? (
            <div className="p-10">
              <Empty title="Pick a thread" body="Choose an email on the left to read it and reply." />
            </div>
          ) : loadingDetail ? (
            <div className="p-10 text-center text-gray-500">Loading conversation…</div>
          ) : detail ? (
            <div className="flex flex-col max-h-[70vh]">
              <div className="p-4 border-b border-gray-100 dark:border-gray-800 space-y-2">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <h2 className="font-semibold truncate">
                      {detail.contact?.name || detail.contact?.email}
                    </h2>
                    <p className="text-sm text-gray-500 truncate">
                      {detail.contact?.email} ·{" "}
                      {detail.conversation.email_account_name || "no sender set"}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <button className="lg:hidden btn-secondary text-sm" onClick={() => setOpenId(null)}>
                      <ArrowLeft size={14} />
                    </button>
                    <select
                      className="input text-sm py-1"
                      value={detail.conversation.status}
                      onChange={(e) => setConversationStatus(e.target.value)}
                    >
                      {STATUS_FILTERS.filter((f) => !["all", "unread"].includes(f.key)).map((f) => (
                        <option key={f.key} value={f.key}>
                          {f.label}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
                {detail.contact?.is_email_opted_out && (
                  <p className="text-xs text-amber-600 flex items-center gap-1">
                    <XCircle size={13} /> This contact unsubscribed — replies will be blocked until they
                    are opted back in on the Contacts page.
                  </p>
                )}
              </div>

              <div className="flex-1 overflow-y-auto p-4 space-y-4 bg-gray-50 dark:bg-gray-900/40">
                {(detail.messages || []).map((m: EmailMessage) => (
                  <div
                    key={m.id}
                    className={`flex ${m.direction === "outgoing" ? "justify-end" : "justify-start"}`}
                  >
                    <div
                      className={`max-w-[85%] rounded-lg p-3 text-sm shadow-sm ${
                        m.direction === "outgoing"
                          ? "bg-primary-600 text-white"
                          : "bg-white dark:bg-gray-800"
                      }`}
                    >
                      <div className="flex items-center justify-between gap-3 mb-1">
                        <p className={`text-xs font-medium ${m.direction === "outgoing" ? "text-primary-100" : "text-gray-500"}`}>
                          {m.direction === "outgoing"
                            ? `From ${m.from_address || "you"}`
                            : `From ${m.from_address || m.contact_email}`}
                        </p>
                        <span className={`text-[10px] ${m.direction === "outgoing" ? "text-primary-100" : "text-gray-400"}`}>
                          {fmtDate(m.created_at)}
                        </span>
                      </div>
                      {m.subject && (
                        <p className={`font-semibold mb-1 ${m.direction === "outgoing" ? "text-white" : ""}`}>
                          {m.subject}
                        </p>
                      )}
                      {m.html_body ? (
                        <div
                          className="prose prose-sm max-w-none dark:prose-invert break-words"
                          dangerouslySetInnerHTML={{ __html: m.html_body }}
                        />
                      ) : (
                        <p className="whitespace-pre-wrap break-words">{m.body}</p>
                      )}
                      {(m.attachments?.length || 0) > 0 && (
                        <div className="mt-2 space-y-1">
                          {m.attachments!.map((a, i) => (
                            <div
                              key={`${a.name}-${i}`}
                              className={`flex items-center gap-1.5 text-[11px] ${
                                m.direction === "outgoing" ? "text-primary-100" : "text-gray-500"
                              }`}
                            >
                              <Paperclip size={11} />
                              {a.url ? (
                                <a
                                  href={a.url}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="truncate underline"
                                >
                                  {a.name}
                                </a>
                              ) : (
                                <span className="truncate">{a.name}</span>
                              )}
                              {a.size ? <span>({Math.max(1, Math.round(a.size / 1024))} KB)</span> : null}
                            </div>
                          ))}
                        </div>
                      )}
                      <MessageActivity
                        message={m}
                        onOpen={() => emailApi.messageEvents(m.id)}
                        outgoing={m.direction === "outgoing"}
                      />
                      <div className="flex flex-wrap items-center gap-2 mt-2">
                        <span
                          className={`text-[10px] ${
                            m.direction === "outgoing" ? "text-primary-100" : "text-gray-400"
                          }`}
                        >
                          {m.status}
                        </span>
                        {m.is_auto_reply && (
                          <span className={`text-[10px] ${m.direction === "outgoing" ? "text-primary-100" : "text-gray-400"}`}>
                            auto-reply
                          </span>
                        )}
                        {m.open_count > 0 && (
                          <span className="text-[10px] flex items-center gap-1 text-sky-500">
                            <CheckCheck size={11} /> opened {m.open_count}x
                          </span>
                        )}
                        {m.click_count > 0 && (
                          <span className="text-[10px] flex items-center gap-1 text-green-500">
                            <MousePointerClick size={11} /> clicked {m.click_count}x
                          </span>
                        )}
                        {m.bounced_hard && <span className="text-[10px] text-red-400">hard bounce</span>}
                      </div>
                    </div>
                  </div>
                ))}
                <div ref={bottomRef} />
              </div>

              <div className="p-3 border-t border-gray-100 dark:border-gray-800 space-y-2">
                {/* Replies use the same editor as a campaign: formatting,
                    links, images and attachments, from the same sender the
                    thread started on — and the same mail thread in Gmail. */}
                <RichEmailEditor
                  body={reply}
                  onBody={setReply}
                  html={replyHtml}
                  onHtml={setReplyHtml}
                  attachments={replyAttachments}
                  onAttachments={setReplyAttachments}
                />
                <div className="flex items-center justify-between gap-2">
                  <p className="text-xs text-gray-400 flex items-center gap-1">
                    <Sparkles size={12} /> Ctrl/⌘ + Enter to send · sent from{" "}
                    {detail.conversation.email_account_name || "the default sender"}
                  </p>
                  <button className="btn-primary" onClick={sendReply} disabled={sending || !reply.trim()}>
                    <Send size={15} className="mr-1" /> {sending ? "Sending…" : "Send reply"}
                  </button>
                </div>
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
