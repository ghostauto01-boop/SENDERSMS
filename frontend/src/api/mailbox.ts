import api from "./client";

/**
 * Typed client for connected mailboxes (/api/v1/mailbox).
 *
 * A "mailbox" is the operator's own inbox — Gmail over IMAP with an app
 * password, or Gmail over the OAuth API. Connecting one is what makes a
 * prospect's reply show up inside this app instead of only in Gmail, and it is
 * what lets a one-to-one reply leave from the same address it was sent from, so
 * the thread stays DMARC-aligned and Google stops filing the replies in Spam.
 */

export type Mailbox = {
  id: number;
  name: string;
  provider: "imap" | "gmail_api";
  email_address: string;
  folders: string[];
  import_all: boolean;
  rescue_from_spam: boolean;
  never_spam_filter: boolean;
  send_replies: boolean;
  poll_interval: number;
  is_active: boolean;
  last_sync_at?: string | null;
  last_sync_status?: string | null;
  last_error?: string | null;
  total_synced: number;
  total_replies: number;
  total_rescued: number;
  total_sent: number;
  filters_installed: number;
  label_id?: string | null;
  /** Always false in a response body — the credential never leaves the server. */
  has_credential: boolean;
  created_at?: string | null;
};

export type MailboxList = {
  items: Mailbox[];
  count: number;
  google_oauth_ready: boolean;
  /** "db" (saved in this app), "env" or "none" — where the OAuth client came from. */
  google_oauth_source?: "db" | "env" | "none";
  google_client_id_masked?: string;
  /** The exact URI Google must be told to redirect to, or null when the
   *  deployment's public address is unknown. */
  redirect_uri?: string | null;
  poll_interval: number;
};

/**
 * The Gmail OAuth client, as Settings → Gmail replies reads and saves it.
 *
 * `client_secret` is deliberately absent from the read: Google's secret is
 * write-only here, exactly like the Brevo keys. The client ID is returned in
 * full because Google treats it as public — it is in every consent URL.
 */
export type GoogleOAuthConfig = {
  client_id: string;
  client_id_masked: string;
  has_secret: boolean;
  configured: boolean;
  source: "db" | "env" | "none";
  redirect_uri: string | null;
  callback_path: string;
  javascript_origin: string | null;
  scope: string;
  public_base_url: string | null;
  public_base_source: "env" | "render" | "request" | "none";
  gmail_mailboxes: Mailbox[];
  mailbox_count: number;
};

export type RoutingFinding = {
  severity: "high" | "medium" | "low";
  where: string;
  value: string;
  problem: string;
  fix: string;
};

export type RoutingPath = {
  id: "brevo" | "gmail_api" | "imap";
  label: string;
  status: string;
  detail: string;
};

export type ReplyRoutingReport = {
  findings: RoutingFinding[];
  healthy: boolean;
  mailboxes: Mailbox[];
  connected: number;
  replies_imported: number;
  rescued_from_spam: number;
  last_sync_at?: string | null;
  brevo_inbound_ready: boolean;
  paths: RoutingPath[];
};

export type MailboxSyncSummary = {
  mailbox_id?: number;
  email?: string;
  provider?: string;
  seen: number;
  replies: number;
  rescued: number;
  stored: number;
  skipped: number;
  errors: string[];
  success?: boolean;
  started_at?: string;
  duration_ms?: number;
};

export type MailboxConnectInput = {
  email_address: string;
  app_password: string;
  name?: string;
  folders?: string;
  import_all?: boolean;
  rescue_from_spam?: boolean;
  send_replies?: boolean;
  poll_interval?: number;
};

export const mailboxApi = {
  report: () => api.get<ReplyRoutingReport>("/mailbox/report").then((r) => r.data),

  list: (includeInactive = false) =>
    api
      .get<MailboxList>("/mailbox", { params: { include_inactive: includeInactive } })
      .then((r) => r.data),

  /**
   * The Google consent URL; open it in a new tab, Google redirects back to us.
   *
   * `returnTo` is the page to land on afterwards (a same-origin path), so
   * connecting from Settings comes back to Settings. `loginHint` pre-fills the
   * Gmail address you mean, which helps once several are signed in — the account
   * chooser still appears, which is what makes a second inbox possible.
   */
  googleStart: (opts?: { loginHint?: string; returnTo?: string }) =>
    api
      .get<{ url: string; redirect_uri: string; scope: string; prompt: string }>(
        "/mailbox/google/start",
        { params: { login_hint: opts?.loginHint, return_to: opts?.returnTo } },
      )
      .then((r) => r.data),

  /** The saved OAuth client + the exact values the Google console needs. */
  googleConfig: () =>
    api.get<GoogleOAuthConfig>("/settings/google-oauth").then((r) => r.data),

  /** Save (or clear, with an empty string) the OAuth client from the app. */
  saveGoogleConfig: (payload: { client_id?: string; client_secret?: string }) =>
    api
      .put<{ success: boolean; warnings: string[]; configured: boolean; source: string }>(
        "/settings/google-oauth",
        payload,
      )
      .then((r) => r.data),

  connect: (payload: MailboxConnectInput) =>
    api
      .post<{ mailbox: Mailbox; first_sync: MailboxSyncSummary }>("/mailbox", payload)
      .then((r) => r.data),

  patch: (id: number, payload: Partial<MailboxConnectInput> & { is_active?: boolean }) =>
    api.patch<{ mailbox: Mailbox }>(`/mailbox/${id}`, payload).then((r) => r.data),

  disconnect: (id: number) =>
    api
      .delete<{ success: boolean; email_address: string; filters_removed: number; detail: string }>(
        `/mailbox/${id}`,
      )
      .then((r) => r.data),

  check: (id: number) =>
    api.post<Record<string, any>>(`/mailbox/${id}/check`).then((r) => r.data),

  sync: (id: number) =>
    api.post<MailboxSyncSummary>(`/mailbox/${id}/sync`).then((r) => r.data),

  /** Gmail "Never send it to Spam" filters (Gmail API connection only). */
  filters: (id: number, install: boolean) =>
    api
      .post<Record<string, any>>(`/mailbox/${id}/filters`, null, { params: { install } })
      .then((r) => r.data),

  testSend: (id: number, to_address: string) =>
    api
      .post<{ success: boolean; provider?: string; provider_message_id?: string }>(
        `/mailbox/${id}/test-send`,
        { to_address },
      )
      .then((r) => r.data),
};

export default mailboxApi;
