import api from "./client";

/**
 * Typed client for the Mail page (/api/v1/mail).
 *
 * This is the Gmail-shaped reading surface over every connected mailbox. Two
 * things define it, and both are deliberate:
 *
 *   • A *folder* is the operator's mental model — Replies, Sent, Spam, Drafts,
 *     Trash — not a Gmail label id. The backend maps labels to those five, so
 *     a message in a nested label still lands somewhere the operator expects.
 *   • Omitting `mailbox_id` means *all inboxes merged*. The reading pane then
 *     names the account on every row, which is the only way a merged list
 *     stays trustworthy when three addresses are connected.
 *
 * Bodies are never sent with a list: the list carries a snippet, and the
 * reading pane asks for the one message it is showing. A 2,000-mail inbox has
 * to open as fast as a 20-mail one.
 */

export type MailFolder = "INBOX" | "SENT" | "SPAM" | "DRAFTS" | "TRASH";

/** Everything the rail can be pointed at: the five folders plus three views. */
export type MailFolderKey = MailFolder | "ALL" | "STARRED" | "UNREAD";

export const FOLDER_LABELS: Record<MailFolderKey, string> = {
  ALL: "All inboxes",
  INBOX: "Replies",
  UNREAD: "Unread",
  STARRED: "Starred",
  SENT: "Sent",
  SPAM: "Spam",
  DRAFTS: "Drafts",
  TRASH: "Trash",
};

export type MailMessage = {
  id: number;
  mailbox_id: number;
  /** Null when the mailbox was disconnected — the row is still readable. */
  mailbox_email: string | null;
  mailbox_name: string | null;
  provider_message_id: string;
  thread_id: string | null;
  folder: MailFolder;
  labels: string[];
  subject: string;
  from_name: string | null;
  from_address: string | null;
  to: string[];
  cc: string[];
  date: string | null;
  snippet: string | null;
  is_read: boolean;
  is_starred: boolean;
  attachments: number;
  conversation_id: number | null;
  contact_id: number | null;
  /** The mirrored body was cut for storage; `body=true` refetches the rest. */
  is_truncated: boolean;
  body_text?: string | null;
  body_html?: string | null;
};

export type FolderCount = { total: number; unread: number };

export type MailAccount = {
  id: number;
  name: string;
  provider: "imap" | "gmail_api";
  email_address: string;
  is_active: boolean;
  last_sync_at?: string | null;
  last_sync_status?: string | null;
  last_error?: string | null;
  /** Per-folder totals, plus a merged `ALL` that excludes Trash. */
  counts: Partial<Record<MailFolderKey, FolderCount>>;
  /** The Gmail grant expired — reading needs a reconnect, not a retry. */
  needs_reconnect: boolean;
};

export type MailOverview = {
  accounts: MailAccount[];
  /** Counts across every account — the numbers behind "All inboxes". */
  all: Partial<Record<MailFolderKey, FolderCount>>;
  total_unread: number;
  folder_keys: MailFolderKey[];
};

export type MailListResponse = {
  items: MailMessage[];
  /** Matches in the folder, so the list can page without asking twice. */
  total: number;
  limit: number;
  offset: number;
  folder: MailFolderKey;
};

/**
 * One message *is* its thread: the payload is the open message with a `thread`
 * array of its siblings, oldest first, across every connected account.
 */
export type MailThread = MailMessage & {
  thread: MailMessage[];
};

export type MailRefreshResult = {
  mailbox_id: number;
  email: string;
  /** Replies threaded into CRM conversations by this pass. */
  replies: number;
  /** Mails pulled out of Spam and into the Inbox. */
  rescued: number;
  /** Rows written to the mail mirror. */
  mirrored: number;
  errors: string[];
};

export type MailRefreshResponse = {
  success: boolean;
  accounts: MailRefreshResult[];
  replies: number;
  mirrored: number;
  /** Fresh counts, so the rail updates without a second round trip. */
  overview: MailOverview;
};

export type MailQuery = {
  folder?: MailFolderKey;
  mailbox_id?: number;
  q?: string;
  limit?: number;
  offset?: number;
  conversation_id?: number;
  contact_id?: number;
};

export const mailApi = {
  overview: () => api.get<MailOverview>("/mail/overview").then((r) => r.data),

  /** Omit `mailbox_id` for the merged all-inboxes view. */
  messages: (query: MailQuery = {}) => {
    const params = new URLSearchParams();
    if (query.folder) params.set("folder", query.folder);
    if (query.mailbox_id) params.set("mailbox_id", String(query.mailbox_id));
    if (query.q) params.set("q", query.q);
    if (query.limit) params.set("limit", String(query.limit));
    if (query.offset) params.set("offset", String(query.offset));
    if (query.conversation_id) params.set("conversation_id", String(query.conversation_id));
    if (query.contact_id) params.set("contact_id", String(query.contact_id));
    return api.get<MailListResponse>(`/mail/messages?${params.toString()}`).then((r) => r.data);
  },

  /** One message with its body, plus the rest of its thread. */
  message: (mailboxId: number, providerMessageId: string) =>
    api
      .get<MailThread>(`/mail/messages/${mailboxId}/${encodeURIComponent(providerMessageId)}?body=true`)
      .then((r) => r.data),

  /** The backend's field names are `read` / `starred`; omitted fields are left alone. */
  setFlags: (mailboxId: number, providerMessageId: string, body: { read?: boolean; starred?: boolean }) =>
    api
      .post<MailMessage>(`/mail/messages/${mailboxId}/${encodeURIComponent(providerMessageId)}/flags`, body)
      .then((r) => r.data),

  move: (mailboxId: number, providerMessageId: string, folder: MailFolder) =>
    api
      .post<MailMessage>(`/mail/messages/${mailboxId}/${encodeURIComponent(providerMessageId)}/move`, { folder })
      .then((r) => r.data),

  /** Delete is trash — reversible from the Trash folder. */
  remove: (mailboxId: number, providerMessageId: string) =>
    api
      .post<{ message: MailMessage }>(`/mail/messages/${mailboxId}/${encodeURIComponent(providerMessageId)}/delete`)
      .then((r) => r.data),

  /**
   * Answers from the account that received the mail.
   *
   * The mailbox id is the receiving account's, so the reply cannot drift to a
   * Brevo sender — which is the whole point of the route existing.
   */
  reply: (
    mailboxId: number,
    providerMessageId: string,
    body: { body: string; html?: string; to_all?: boolean },
  ) =>
    api
      .post<{ success: boolean; message?: MailMessage; error?: string }>(
        `/mail/messages/${mailboxId}/${encodeURIComponent(providerMessageId)}/reply`,
        body,
      )
      .then((r) => r.data),

  /**
   * Pull new mail now instead of waiting for the poll.
   *
   * `mailbox_id` is a query parameter, not a body field — omit it to refresh
   * every connected account at once.
   */
  refresh: (body: { mailbox_id?: number; full?: boolean } = {}) => {
    const params = new URLSearchParams();
    if (body.mailbox_id) params.set("mailbox_id", String(body.mailbox_id));
    const query = params.toString();
    return api
      .post<MailRefreshResponse>(
        `/mail/refresh${query ? `?${query}` : ""}`,
        { full: Boolean(body.full) },
        // A full re-mirror walks every folder of every account, so this is the
        // one call on the page allowed to outlast the default timeout.
        { timeout: 120_000 },
      )
      .then((r) => r.data);
  },
};

export default mailApi;
