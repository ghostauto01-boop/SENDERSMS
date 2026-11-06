import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import toast from "react-hot-toast";
import {
  AlertTriangle, Archive, ArrowLeft, Inbox, Loader2, Mail, MailOpen, Paperclip,
  RefreshCw, Reply, Search, Send, Star, Trash2, Users, X,
} from "lucide-react";
import {
  FOLDER_LABELS,
  mailApi,
  type MailAccount,
  type MailFolder,
  type MailFolderKey,
  type MailMessage,
  type MailOverview,
  type MailThread,
} from "../api/mail";

/**
 * MAIL — a real mail client over every connected Gmail account.
 *
 * The Email Inbox page answers "what did my prospects reply?". This page
 * answers a different question: *what is in my mailboxes?* Same accounts, two
 * different jobs, which is why it is its own screen rather than a tab.
 *
 * How it is laid out, and why:
 *
 *   • **The rail** lists every connected account, each expandable into its own
 *     Inbox / Sent / Spam / Drafts / Trash, with live unread counts. Above it
 *     sits *All inboxes*, which merges every account into one list — and in
 *     that view every row names its account, because a merged list that hides
 *     where a mail landed is a list you cannot trust.
 *   • **The list** is a mail list, not a table: sender, subject, snippet, time,
 *     star, attachment, unread weight. It pages, and it never carries bodies —
 *     a 2,000-mail folder has to open as fast as a 20-mail one.
 *   • **The reading pane** shows the whole thread and the reply box. Replying
 *     here sends from the account that *received* the mail. That is not a
 *     preference the UI happens to have; the backend enforces it, because a
 *     reply leaving from a Brevo sender is how a thread gets filed as spam.
 *
 * Gmail stays the source of truth: starring, moving and trashing are applied
 * there first, and the mirror only reflects what Gmail reports.
 */

const FOLDERS: MailFolderKey[] = ["INBOX", "SENT", "SPAM", "DRAFTS", "TRASH"];

const FOLDER_ICONS: Record<string, typeof Inbox> = {
  ALL: Mail,
  INBOX: Inbox,
  UNREAD: MailOpen,
  STARRED: Star,
  SENT: Send,
  SPAM: AlertTriangle,
  DRAFTS: Archive,
  TRASH: Trash2,
};

const PAGE_SIZE = 50;

/**
 * The name on a row, Gmail-style.
 *
 * In Sent and Drafts the interesting party is the *recipient* — a list of mails
 * you sent that all say your own name is a list you cannot scan.
 */
function who(message: MailMessage): string {
  if (message.folder === "SENT" || message.folder === "DRAFTS") {
    const to = message.to[0];
    return to ? to.split("@")[0] : "me";
  }
  return message.from_name?.trim() || message.from_address || "unknown";
}

function shortDate(iso: string | null): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const now = new Date();
  const sameYear = date.getFullYear() === now.getFullYear();
  if (date.toDateString() === now.toDateString()) {
    return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }
  return date.toLocaleDateString([], {
    day: "2-digit",
    month: "short",
    ...(sameYear ? {} : { year: "numeric" }),
  });
}

function fullDate(iso: string | null): string {
  if (!iso) return "";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString();
}

export default function MailPage() {
  const [params, setParams] = useSearchParams();

  /** `mailbox=all` is the merged view — the default, like a real mail client. */
  const mailboxId = params.get("mailbox") === "all" || !params.get("mailbox")
    ? null
    : Number(params.get("mailbox"));
  const folder: MailFolderKey = (params.get("folder")?.toUpperCase() as MailFolderKey) || "INBOX";

  const [overview, setOverview] = useState<MailOverview | null>(null);
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});
  const [items, setItems] = useState<MailMessage[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [search, setSearch] = useState(params.get("q") || "");
  const [refreshing, setRefreshing] = useState(false);

  const [open, setOpen] = useState<MailThread | null>(null);
  const [loadingMessage, setLoadingMessage] = useState(false);
  const [draft, setDraft] = useState("");
  const [toAll, setToAll] = useState(false);
  const [sending, setSending] = useState(false);
  /** The thread is collapsed to its newest message until expanded. */
  const [showThread, setShowThread] = useState(false);

  const listRef = useRef<HTMLDivElement>(null);

  const setRoute = useCallback(
    (next: { mailbox?: number | null; folder?: MailFolderKey; q?: string }) => {
      const search_ = new URLSearchParams(params);
      if (next.mailbox !== undefined) {
        search_.set("mailbox", next.mailbox === null ? "all" : String(next.mailbox));
      }
      if (next.folder !== undefined) search_.set("folder", next.folder);
      if (next.q !== undefined) {
        if (next.q) search_.set("q", next.q);
        else search_.delete("q");
      }
      setParams(search_, { replace: true });
    },
    [params, setParams],
  );

  const loadOverview = useCallback(async () => {
    try {
      const data = await mailApi.overview();
      setOverview(data);
    } catch (error: any) {
      setListError(error?.response?.data?.detail || "Could not read the mailboxes.");
    }
  }, []);

  const loadList = useCallback(async (nextOffset = 0) => {
    setLoading(true);
    setListError(null);
    try {
      const data = await mailApi.messages({
        folder,
        mailbox_id: mailboxId ?? undefined,
        q: params.get("q") || undefined,
        limit: PAGE_SIZE,
        offset: nextOffset,
      });
      setItems(data.items);
      setTotal(data.total);
      setOffset(nextOffset);
    } catch (error: any) {
      setItems([]);
      setTotal(0);
      setListError(error?.response?.data?.detail || "Could not read that folder.");
    } finally {
      setLoading(false);
    }
  }, [folder, mailboxId, params]);

  useEffect(() => {
    void loadOverview();
  }, [loadOverview]);

  useEffect(() => {
    void loadList(0);
    setOpen(null);
    setDraft("");
    setShowThread(false);
  }, [loadList]);

  // Keep the rail live the way a mail client does — a light poll, and a hard
  // refresh only when the operator asks for one.
  useEffect(() => {
    const timer = window.setInterval(() => void loadOverview(), 30_000);
    return () => window.clearInterval(timer);
  }, [loadOverview]);

  const accounts = overview?.accounts ?? [];
  const activeAccount = accounts.find((a) => a.id === mailboxId) ?? null;

  const openMessage = async (message: MailMessage) => {
    setLoadingMessage(true);
    setShowThread(false);
    try {
      const thread = await mailApi.message(message.mailbox_id, message.provider_message_id);
      setOpen(thread);
      setDraft("");
      setToAll(false);
      // Reading a message reads it everywhere — list, rail and Gmail.
      if (!message.is_read) {
        setItems((list) =>
          list.map((m) => (m.id === message.id ? { ...m, is_read: true } : m)),
        );
        void loadOverview();
      }
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || "Could not open that message.");
    } finally {
      setLoadingMessage(false);
    }
  };

  const star = async (message: MailMessage, next: boolean) => {
    // Optimistic: a star that waits on a round trip feels broken.
    setItems((list) => list.map((m) => (m.id === message.id ? { ...m, is_starred: next } : m)));
    try {
      await mailApi.setFlags(message.mailbox_id, message.provider_message_id, { starred: next });
      void loadOverview();
    } catch (error: any) {
      setItems((list) => list.map((m) => (m.id === message.id ? { ...m, is_starred: !next } : m)));
      toast.error(error?.response?.data?.detail || "Gmail refused that change.");
    }
  };

  const markUnread = async (message: MailMessage, next: boolean) => {
    setItems((list) => list.map((m) => (m.id === message.id ? { ...m, is_read: !next } : m)));
    try {
      await mailApi.setFlags(message.mailbox_id, message.provider_message_id, { read: !next });
      if (open?.id === message.id) setOpen({ ...open, is_read: !next });
      void loadOverview();
    } catch (error: any) {
      setItems((list) => list.map((m) => (m.id === message.id ? { ...m, is_read: next } : m)));
      toast.error(error?.response?.data?.detail || "Gmail refused that change.");
    }
  };

  const moveTo = async (message: MailMessage, folder: MailFolder) => {
    const label = FOLDER_LABELS[folder];
    const before = items;
    setItems((list) => list.filter((m) => m.id !== message.id));
    if (open?.id === message.id) setOpen(null);
    try {
      await mailApi.move(message.mailbox_id, message.provider_message_id, folder);
      toast.success(`Moved to ${label}`);
      void loadOverview();
    } catch (error: any) {
      setItems(before);
      toast.error(error?.response?.data?.detail || `Could not move that to ${label}.`);
    }
  };

  const remove = async (message: MailMessage) => {
    const before = items;
    setItems((list) => list.filter((m) => m.id !== message.id));
    if (open?.id === message.id) setOpen(null);
    try {
      await mailApi.remove(message.mailbox_id, message.provider_message_id);
      toast.success("Moved to Trash — you can undo that from the Trash folder.");
      void loadOverview();
    } catch (error: any) {
      setItems(before);
      toast.error(error?.response?.data?.detail || "Could not move that to Trash.");
    }
  };

  const sendReply = async () => {
    if (!open) return;
    const body = draft.trim();
    if (!body) {
      toast.error("Write something first.");
      return;
    }
    setSending(true);
    try {
      const result = await mailApi.reply(open.mailbox_id, open.provider_message_id, {
        body,
        to_all: toAll,
      });
      if (!result.success) throw new Error(result.error || "The reply did not go out.");
      toast.success(`Sent from ${open.mailbox_email || "that account"}`);
      setDraft("");
      // The sent copy lands in Sent; show it in the thread straight away.
      const fresh = await mailApi.message(open.mailbox_id, open.provider_message_id);
      setOpen(fresh);
      void loadOverview();
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || error?.message || "The reply did not go out.");
    } finally {
      setSending(false);
    }
  };

  const refresh = async (full = false) => {
    setRefreshing(true);
    try {
      const result = await mailApi.refresh({ mailbox_id: mailboxId ?? undefined, full });
      if (result.success) {
        toast.success(
          result.mirrored || result.replies
            ? `Pulled ${result.mirrored} new ${result.mirrored === 1 ? "mail" : "mails"}.`
            : "Already up to date.",
        );
      } else {
        const failed = result.accounts.filter((a) => a.errors.length > 0);
        toast.error(
          failed.length === 1
            ? `${failed[0].email}: ${failed[0].errors[0]}`
            : `${failed.length} accounts could not be refreshed.`,
        );
      }
      // The refresh response already carries fresh counts — no second call.
      setOverview(result.overview);
      await loadList(offset);
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || "Could not refresh.");
    } finally {
      setRefreshing(false);
    }
  };

  const submitSearch = (event: React.FormEvent) => {
    event.preventDefault();
    setRoute({ q: search.trim() });
  };

  /** Every account that is connected but not working — shown once, at the top. */
  const broken = useMemo(() => accounts.filter((a) => a.needs_reconnect), [accounts]);

  const railCount = (account: MailAccount | null, key: MailFolderKey): FolderCountView => {
    const counts = account ? account.counts : overview?.all;
    const bucket = counts?.[key];
    return { total: bucket?.total ?? 0, unread: bucket?.unread ?? 0 };
  };

  const scopeLabel = activeAccount ? activeAccount.email_address : "All inboxes";

  return (
    <div className="flex h-[calc(100vh-4rem)] flex-col lg:flex-row">
      {/* ---------------- rail ---------------------------------------- */}
      <aside className="shrink-0 overflow-y-auto border-b border-gray-200 bg-gray-50/60 p-2 lg:w-64 lg:border-b-0 lg:border-r dark:border-gray-800 dark:bg-gray-900/40">
        <div className="mb-2 flex items-center justify-between gap-2 px-1">
          <h1 className="flex items-center gap-1.5 text-[13px] font-semibold">
            <Mail size={14} className="text-primary-600" /> Mail
          </h1>
          <button
            type="button"
            className="btn-ghost btn-sm"
            onClick={() => void refresh(false)}
            disabled={refreshing}
            title="Pull new mail now"
          >
            <RefreshCw size={13} className={refreshing ? "animate-spin" : ""} />
          </button>
        </div>

        {broken.length > 0 && (
          <p className="mb-2 flex items-start gap-1.5 rounded-md border border-warning-200 bg-warning-50 p-2 text-[11px] text-warning-800 dark:border-warning-900 dark:bg-warning-950/40 dark:text-warning-200">
            <AlertTriangle size={12} className="mt-0.5 shrink-0" />
            <span>
              {broken.map((a) => a.email_address).join(", ")} needs reconnecting —{" "}
              <Link to="/email-manager" className="underline">
                reconnect
              </Link>
              .
            </span>
          </p>
        )}

        {accounts.length === 0 && (
          <p className="rounded-md border border-dashed border-gray-300 p-3 text-[11.5px] text-gray-500 dark:border-gray-700">
            No mailbox is connected yet. Connect a Gmail account on the{" "}
            <Link to="/email-manager" className="text-primary-600 underline">
              Email Manager
            </Link>{" "}
            and its mail shows up here.
          </p>
        )}

        {/* The merged view first — it is where most reading happens. */}
        <RailRow
          label="All inboxes"
          icon={Mail}
          active={mailboxId === null && folder === "ALL"}
          count={railCount(null, "ALL")}
          onClick={() => setRoute({ mailbox: null, folder: "ALL" })}
        />

        <div className="mt-1 space-y-0.5">
          {accounts.map((account) => {
            const isOpen = Boolean(expanded[account.id]);
            const Icon = isOpen ? MailOpen : Mail;
            const total_ = railCount(account, "ALL");
            return (
              <div key={account.id}>
                <div className="flex items-stretch">
                  <RailRow
                    label={account.name || account.email_address}
                    sub={account.email_address}
                    icon={Icon}
                    active={mailboxId === account.id && folder === "ALL"}
                    count={total_}
                    dim={!account.is_active}
                    onClick={() => setRoute({ mailbox: account.id, folder: "ALL" })}
                    className="min-w-0 flex-1"
                  />
                  <button
                    type="button"
                    aria-label={isOpen ? `Collapse ${account.email_address}` : `Expand ${account.email_address}`}
                    aria-expanded={isOpen}
                    onClick={() => setExpanded((e) => ({ ...e, [account.id]: !isOpen }))}
                    className="shrink-0 px-1.5 text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
                  >
                    {isOpen ? "−" : "+"}
                  </button>
                </div>
                {isOpen && (
                  <div className="ml-3 border-l border-gray-200 pl-1 dark:border-gray-700">
                    {FOLDERS.map((key) => {
                      const Icon = FOLDER_ICONS[key];
                      return (
                        <RailRow
                          key={key}
                          label={FOLDER_LABELS[key]}
                          icon={Icon}
                          small
                          active={mailboxId === account.id && folder === key}
                          count={railCount(account, key)}
                          onClick={() => setRoute({ mailbox: account.id, folder: key })}
                        />
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {overview && overview.total_unread > 0 && (
          <p className="mt-3 px-1 text-[11px] text-gray-400">
            {overview.total_unread} unread across every account.
          </p>
        )}
      </aside>

      {/* ---------------- list + reading pane ------------------------- */}
      <section className="flex min-w-0 flex-1 flex-col lg:flex-row">
        <div
          ref={listRef}
          className="min-w-0 flex-1 overflow-y-auto border-r border-gray-200 dark:border-gray-800 lg:max-w-xl"
        >
          <div className="sticky top-0 z-10 border-b border-gray-200 bg-white px-3 py-2 dark:border-gray-800 dark:bg-gray-900">
            <div className="flex items-center justify-between gap-2">
              <h2 className="min-w-0 truncate text-[13px] font-semibold">
                {FOLDER_LABELS[folder]}
                <span className="ml-1.5 font-normal text-gray-400">{scopeLabel}</span>
              </h2>
              <span className="shrink-0 text-[11px] text-gray-400">{total} total</span>
            </div>
            <form onSubmit={submitSearch} className="mt-1.5 flex gap-1.5">
              <div className="relative min-w-0 flex-1">
                <Search size={13} className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-400" />
                <input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search subject, sender or body"
                  aria-label="Search mail"
                  className="input w-full !py-1 pl-7 text-[12px]"
                />
              </div>
              {params.get("q") && (
                <button type="button" className="btn-ghost btn-sm" onClick={() => setRoute({ q: "" })}>
                  <X size={13} /> Clear
                </button>
              )}
            </form>
          </div>

          {listError && (
            <p className="flex items-center gap-1.5 p-3 text-[12px] text-danger-700 dark:text-danger-400">
              <AlertTriangle size={13} /> {listError}
            </p>
          )}

          {loading ? (
            <p className="flex items-center gap-1.5 p-3 text-[12px] text-gray-500">
              <Loader2 size={13} className="animate-spin" /> Loading…
            </p>
          ) : items.length === 0 ? (
            <p className="p-6 text-center text-[12.5px] text-gray-500">
              {params.get("q")
                ? "Nothing matches that search."
                : `Nothing in ${FOLDER_LABELS[folder]} yet.`}
            </p>
          ) : (
            <ul>
              {items.map((message) => (
                <li key={message.id}>
                  <div
                    className={`group flex cursor-pointer items-start gap-2 border-b border-gray-100 px-3 py-2 hover:bg-gray-50 dark:border-gray-800/70 dark:hover:bg-gray-800/50 ${
                      open?.id === message.id ? "bg-primary-50 dark:bg-primary-950/40" : ""
                    }`}
                    onClick={() => void openMessage(message)}
                  >
                    <button
                      type="button"
                      aria-label={message.is_starred ? "Unstar" : "Star"}
                      aria-pressed={message.is_starred}
                      onClick={(e) => {
                        e.stopPropagation();
                        void star(message, !message.is_starred);
                      }}
                      className="mt-0.5 shrink-0 text-gray-300 hover:text-warning-500"
                    >
                      <Star
                        size={14}
                        className={message.is_starred ? "fill-warning-400 text-warning-500" : ""}
                      />
                    </button>

                    <div className="min-w-0 flex-1">
                      <div className="flex items-baseline gap-2">
                        <span
                          className={`min-w-0 flex-1 truncate text-[12.5px] ${
                            message.is_read
                              ? "text-gray-600 dark:text-gray-400"
                              : "font-semibold text-gray-900 dark:text-gray-100"
                          }`}
                        >
                          {who(message)}
                        </span>
                        <span className="shrink-0 text-[11px] text-gray-400">
                          {shortDate(message.date)}
                        </span>
                      </div>
                      <p
                        className={`truncate text-[12.5px] ${
                          message.is_read ? "text-gray-500" : "font-medium text-gray-800 dark:text-gray-200"
                        }`}
                      >
                        {message.subject}
                      </p>
                      <p className="truncate text-[11.5px] text-gray-400">
                        {message.snippet || " "}
                      </p>
                      <div className="mt-0.5 flex flex-wrap items-center gap-1.5">
                        {/* In the merged view the account is on every row —
                            without it you cannot tell where a mail landed. */}
                        {!activeAccount && message.mailbox_email && (
                          <span className="rounded bg-gray-100 px-1 py-0.5 text-[10px] text-gray-500 dark:bg-gray-800 dark:text-gray-400">
                            {message.mailbox_email}
                          </span>
                        )}
                        {message.attachments > 0 && (
                          <span className="flex items-center gap-0.5 text-[10px] text-gray-400">
                            <Paperclip size={10} /> {message.attachments}
                          </span>
                        )}
                        {message.conversation_id && (
                          <span className="flex items-center gap-0.5 text-[10px] text-primary-500">
                            <Users size={10} /> contact
                          </span>
                        )}
                        {!message.is_read && (
                          <span className="h-1.5 w-1.5 rounded-full bg-primary-500" />
                        )}
                      </div>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}

          {total > PAGE_SIZE && (
            <div className="flex items-center justify-between gap-2 p-2">
              <button
                type="button"
                className="btn-ghost btn-sm"
                disabled={offset === 0 || loading}
                onClick={() => void loadList(Math.max(0, offset - PAGE_SIZE))}
              >
                <ArrowLeft size={13} /> Newer
              </button>
              <span className="text-[11px] text-gray-400">
                {offset + 1}–{Math.min(offset + items.length, total)} of {total}
              </span>
              <button
                type="button"
                className="btn-ghost btn-sm"
                disabled={offset + items.length >= total || loading}
                onClick={() => void loadList(offset + PAGE_SIZE)}
              >
                Older
              </button>
            </div>
          )}
        </div>

        {/* ---------------- reading pane ------------------------------ */}
        <div className="flex min-w-0 flex-[1.4] flex-col overflow-hidden">
          {!open ? (
            <div className="flex flex-1 flex-col items-center justify-center gap-1 p-8 text-center">
              <MailOpen size={26} className="text-gray-300 dark:text-gray-600" />
              <p className="text-[12.5px] text-gray-500">
                {loadingMessage ? "Opening…" : "Pick a message to read it here."}
              </p>
            </div>
          ) : (
            <>
              <div className="min-h-0 flex-1 overflow-y-auto p-3">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <h2 className="text-[14px] font-semibold">{open.subject}</h2>
                    <p className="mt-0.5 text-[11.5px] text-gray-500">
                      {open.from_name ? `${open.from_name} · ` : ""}
                      {open.from_address}
                    </p>
                    <p className="text-[11px] text-gray-400">
                      to {open.to.join(", ") || "me"}
                      {open.cc.length > 0 ? ` · cc ${open.cc.join(", ")}` : ""}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <span className="text-[11px] text-gray-400">{fullDate(open.date)}</span>
                    <button
                      type="button"
                      className="btn-ghost btn-sm"
                      title={open.is_read ? "Mark unread" : "Mark read"}
                      onClick={() => void markUnread(open, open.is_read)}
                    >
                      {open.is_read ? <Mail size={13} /> : <MailOpen size={13} />}
                    </button>
                    <button
                      type="button"
                      className="btn-ghost btn-sm"
                      title={folder === "SPAM" ? "Move to Inbox" : "Move to Spam"}
                      onClick={() => void moveTo(open, folder === "SPAM" ? "INBOX" : "SPAM")}
                    >
                      <AlertTriangle size={13} />
                    </button>
                    <button
                      type="button"
                      className="btn-ghost btn-sm"
                      title="Move to Trash"
                      onClick={() => void remove(open)}
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                </div>

                {/* Sent from this account, so name it on the reply box. */}
                <p className="mt-2 inline-flex items-center gap-1 rounded-full bg-gray-100 px-2 py-0.5 text-[10.5px] text-gray-500 dark:bg-gray-800 dark:text-gray-400">
                  received on {open.mailbox_email || "this account"}
                </p>

                {open.is_truncated && (
                  <p className="mt-2 text-[11px] text-gray-400">
                    Long message — the mirror kept the beginning; Gmail has the rest.
                  </p>
                )}

                <div className="mt-3 space-y-2">
                  {open.thread.length > 1 && (
                    <button
                      type="button"
                      className="btn-ghost btn-sm"
                      onClick={() => setShowThread((v) => !v)}
                    >
                      {showThread ? "Hide" : "Show"} {open.thread.length - 1} earlier{" "}
                      {open.thread.length - 1 === 1 ? "message" : "messages"}
                    </button>
                  )}
                  {(showThread ? open.thread : [open]).map((part) => (
                    <article
                      key={`${part.mailbox_id}:${part.provider_message_id}`}
                      className="rounded-lg border border-gray-200 p-2.5 dark:border-gray-700"
                    >
                      <header className="mb-1.5 flex flex-wrap items-baseline justify-between gap-2 text-[11.5px]">
                        <span className="font-medium">
                          {part.from_name || part.from_address}
                          <span className="ml-1 font-normal text-gray-400">
                            {part.from_address ? `<${part.from_address}>` : ""}
                          </span>
                        </span>
                        <span className="text-gray-400">{fullDate(part.date)}</span>
                      </header>
                      {part.body_html ? (
                        // Quarantined: the sandbox strips scripts and frames, and
                        // images load over https only, so a hostile signature
                        // cannot reach the operator's session.
                        <div
                          className="prose prose-sm max-w-none break-words text-[12.5px] dark:prose-invert"
                          // eslint-disable-next-line react/no-danger
                          dangerouslySetInnerHTML={{ __html: part.body_html }}
                        />
                      ) : (
                        <pre className="whitespace-pre-wrap break-words font-sans text-[12.5px]">
                          {part.body_text || "(no text in this message)"}
                        </pre>
                      )}
                    </article>
                  ))}
                </div>
              </div>

              {/* ---------------- reply ----------------------------- */}
              <div className="shrink-0 border-t border-gray-200 p-2.5 dark:border-gray-800">
                {open.folder === "DRAFTS" ? (
                  <p className="text-[11.5px] text-gray-500">
                    This is a draft — finish it in Gmail and it will appear in Sent.
                  </p>
                ) : (
                  <>
                    <p className="mb-1 flex flex-wrap items-center gap-2 text-[11px] text-gray-500">
                      <Reply size={11} />
                      Replying as{" "}
                      <strong className="font-semibold text-gray-700 dark:text-gray-200">
                        {open.mailbox_email || "the account that received this"}
                      </strong>
                      <label className="ml-auto flex items-center gap-1">
                        <input
                          type="checkbox"
                          checked={toAll}
                          onChange={(e) => setToAll(e.target.checked)}
                        />
                        Reply all
                      </label>
                    </p>
                    <textarea
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                      placeholder="Write a reply…"
                      aria-label="Reply body"
                      rows={3}
                      className="input w-full resize-y text-[12.5px]"
                    />
                    <div className="mt-1.5 flex items-center justify-end gap-1.5">
                      <button
                        type="button"
                        className="btn-primary btn-sm"
                        disabled={sending || !draft.trim()}
                        onClick={() => void sendReply()}
                      >
                        {sending ? <Loader2 size={13} className="animate-spin" /> : <Send size={13} />}
                        Send reply
                      </button>
                    </div>
                  </>
                )}
              </div>
            </>
          )}
        </div>
      </section>
    </div>
  );
}

type FolderCountView = { total: number; unread: number };

function RailRow({
  label,
  sub,
  icon: Icon,
  active,
  count,
  onClick,
  small = false,
  dim = false,
  className = "",
}: {
  label: string;
  sub?: string;
  icon: typeof Inbox;
  active: boolean;
  count: FolderCountView;
  onClick: () => void;
  small?: boolean;
  dim?: boolean;
  className?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? "page" : undefined}
      className={`flex w-full items-center gap-1.5 rounded-md px-2 text-left transition-colors ${
        small ? "py-1" : "py-1.5"
      } ${
        active
          ? "bg-primary-100 text-primary-900 dark:bg-primary-950/60 dark:text-primary-100"
          : "text-gray-700 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800"
      } ${dim ? "opacity-60" : ""} ${className}`}
      title={sub || label}
    >
      <Icon size={small ? 12 : 13} className="shrink-0 text-gray-400" />
      <span className={`min-w-0 flex-1 truncate ${small ? "text-[11.5px]" : "text-[12.5px] font-medium"}`}>
        {label}
      </span>
      {count.unread > 0 ? (
        <span className="shrink-0 rounded-full bg-primary-500 px-1.5 text-[10px] font-semibold text-white">
          {count.unread}
        </span>
      ) : count.total > 0 ? (
        <span className="shrink-0 text-[10px] text-gray-400">{count.total}</span>
      ) : null}
    </button>
  );
}
