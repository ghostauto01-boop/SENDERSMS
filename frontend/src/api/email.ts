import api from "./client";

/**
 * Typed client for the Email channel API (/api/v1/email).
 *
 * Mirrors `api/ads.ts`: every call goes through the shared axios instance, so
 * auth, 401 redirects and 422 message normalisation behave exactly like the SMS
 * pages.
 */

export type EmailAccount = {
  id: number;
  name: string;
  provider: string;
  from_name: string;
  from_email: string;
  reply_to?: string | null;
  is_active: boolean;
  is_default: boolean;
  has_api_key: boolean;
  api_key_masked: string;
  daily_limit?: number | null;
  sent_today: number;
  total_sent: number;
  track_opens: boolean;
  track_clicks: boolean;
  connection_status?: string | null;
  last_tested_at?: string | null;
  last_error?: string | null;
  webhook_url?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  // account_stats() adds these
  sent_total?: number;
  failed_total?: number;
  campaigns_using?: number;
};

export type EmailAccountInput = {
  name: string;
  from_name: string;
  from_email: string;
  api_key?: string;
  reply_to?: string | null;
  daily_limit?: number | null;
  is_active?: boolean;
  is_default?: boolean;
  track_opens?: boolean;
  track_clicks?: boolean;
};

export type EmailMessage = {
  id: number;
  conversation_id?: number | null;
  contact_id?: number | null;
  campaign_id?: number | null;
  ads_campaign_id?: number | null;
  direction: "incoming" | "outgoing";
  channel: string;
  subject?: string | null;
  body: string;
  html_body?: string | null;
  from_address?: string | null;
  to_address?: string | null;
  email_account_id?: number | null;
  email_account_name?: string | null;
  status: string;
  provider?: string | null;
  provider_message_id?: string | null;
  open_count: number;
  click_count: number;
  opened_at?: string | null;
  clicked_at?: string | null;
  bounced_at?: string | null;
  bounced_hard: boolean;
  is_auto_reply: boolean;
  //: Attachment metadata (name/content_type/size) — never the payload.
  attachments?: { name?: string; content_type?: string; size?: number; url?: string }[];
  cc?: string[];
  bcc?: string[];
  rfc_message_id?: string | null;
  in_reply_to?: string | null;
  retry_count: number;
  last_error?: string | null;
  sent_at?: string | null;
  delivered_at?: string | null;
  failed_at?: string | null;
  created_at?: string | null;
  contact_name?: string | null;
  contact_email?: string | null;
  contact_phone?: string | null;
};

export type EmailTotals = {
  period_days: number;
  sent: number;
  delivered: number;
  failed: number;
  queued: number;
  replies: number;
  opened: number;
  clicked: number;
  opens: number;
  clicks: number;
  unsubscribed: number;
  delivery_rate: number;
  open_rate: number;
  click_rate: number;
  reply_rate: number;
  failure_rate: number;
  series?: { date: string; sent: number; opened: number; clicks: number; replies: number }[];
};

export type EmailOverview = {
  days: number;
  totals: EmailTotals;
  series: { date: string; sent: number; opened: number; clicks: number; replies: number }[];
  accounts: EmailAccount[];
  default_account_id?: number | null;
  unread_conversations: number;
  suppressed_total: number;
  recent_campaigns: {
    id: number;
    name: string;
    status: string;
    subject?: string | null;
    messages_sent: number;
    messages_delivered: number;
    messages_failed: number;
    replies: number;
    created_at?: string | null;
  }[];
  recent_events: {
    id: number;
    event_type: string;
    email_address?: string | null;
    subject?: string | null;
    detail?: string | null;
    created_at?: string | null;
  }[];
};

export type EmailConversation = {
  id: number;
  contact_id: number;
  contact_name?: string | null;
  contact_email?: string | null;
  contact_phone?: string | null;
  subject?: string | null;
  preview?: string | null;
  status: string;
  unread_count: number;
  message_count: number;
  last_message_at?: string | null;
  email_account_id?: number | null;
  email_account_name?: string | null;
  campaign_id?: number | null;
  ads_campaign_id?: number | null;
};

export type EmailSuppression = {
  id: number;
  email_address: string;
  contact_id?: number | null;
  reason?: string | null;
  source?: string | null;
  hard_bounce: boolean;
  opt_out_keyword?: string | null;
  created_at?: string | null;
};

export type EmailEvent = {
  id: number;
  event_type: string;
  account_id?: number | null;
  message_id?: number | null;
  contact_id?: number | null;
  campaign_id?: number | null;
  email_address?: string | null;
  subject?: string | null;
  link?: string | null;
  detail?: string | null;
  created_at?: string | null;
};

export type Paged<T> = { total: number; page?: number; per_page?: number; items: T[] };

export type EmailSendInput = {
  contact_id?: number | null;
  email?: string | null;
  list_id?: number | null;
  subject: string;
  body: string;
  html_body?: string | null;
  template_id?: number | null;
  email_account_id?: number | null;
  //: [{"name": "...", "content_base64": "..."}] — ignored when a template
  //: already carries its own attachments.
  attachments?: { name: string; content_base64: string; content_type?: string }[] | null;
  //: Copied recipients, like any mail client.
  cc?: string[] | null;
  bcc?: string[] | null;
  schedule_at?: string | null;
};

const emailApi = {
  // ---- senders -----------------------------------------------------------
  listAccounts: async (): Promise<Paged<EmailAccount>> =>
    (await api.get("/email/accounts")).data,
  createAccount: async (payload: EmailAccountInput): Promise<EmailAccount> =>
    (await api.post("/email/accounts", payload)).data,
  updateAccount: async (id: number, payload: Partial<EmailAccountInput>): Promise<EmailAccount> =>
    (await api.patch(`/email/accounts/${id}`, payload)).data,
  deleteAccount: async (id: number) => (await api.delete(`/email/accounts/${id}`)).data,
  makeDefault: async (id: number): Promise<EmailAccount> =>
    (await api.post(`/email/accounts/${id}/default`)).data,
  testAccount: async (id: number): Promise<{ success: boolean; error?: string; account: EmailAccount }> =>
    (await api.post(`/email/accounts/${id}/test`)).data,
  testSend: async (id: number, to: string, subject?: string) =>
    (await api.post(`/email/accounts/${id}/test-send`, { to, subject })).data,
  //: Mail the composer's current content to one address, exactly as Brevo's
  //: editor does — variables rendered, HTML, attachments.
  testComposer: async (payload: {
    to: string;
    subject?: string | null;
    body?: string | null;
    html_body?: string | null;
    attachments?: { name: string; content_base64: string; content_type?: string }[] | null;
    email_account_id?: number | null;
    template_id?: number | null;
    contact_id?: number | null;
  }): Promise<{ success: boolean; to: string }> =>
    (await api.post("/email/test-send", payload)).data,
  verifiedSenders: async (id: number) =>
    (await api.get(`/email/accounts/${id}/senders`)).data,

  // ---- dashboard ---------------------------------------------------------
  overview: async (params?: { days?: number; account_id?: number }): Promise<EmailOverview> =>
    (await api.get("/email/overview", { params })).data,
  stats: async (params?: { days?: number; account_id?: number }): Promise<EmailTotals> =>
    (await api.get("/email/stats", { params })).data,
  reference: async () =>
    (await api.get("/email/reference")).data as {
      accounts: EmailAccount[];
      default_account_id?: number | null;
      templates: { id: number; name: string; subject?: string | null; category?: string | null }[];
      lists: { id: number; name: string; contact_count?: number | null }[];
      emailable_contacts: number;
    },
  preview: async (payload: {
    subject?: string;
    body?: string;
    html_body?: string;
    template_id?: number;
    contact_id?: number;
  }) => (await api.post("/email/preview", payload)).data as { subject: string; text: string; html: string },

  // ---- sending -----------------------------------------------------------
  send: async (payload: EmailSendInput) => (await api.post("/email/send", payload)).data,
  history: async (params?: {
    page?: number;
    per_page?: number;
    status?: string;
    direction?: string;
    account_id?: number;
    campaign_id?: number;
    search?: string;
  }): Promise<Paged<EmailMessage>> => (await api.get("/email/history", { params })).data,
  retry: async (messageId: number) => (await api.post(`/email/messages/${messageId}/retry`)).data,

  // ---- inbox -------------------------------------------------------------
  conversations: async (params?: {
    page?: number;
    per_page?: number;
    status?: string;
    search?: string;
  }): Promise<Paged<EmailConversation>> =>
    (await api.get("/email/inbox/conversations", { params })).data,
  conversation: async (
    id: number
  ): Promise<{
    conversation: {
      id: number;
      contact_id: number;
      subject?: string | null;
      status: string;
      unread_count: number;
      email_account_id?: number | null;
      email_account_name?: string | null;
    };
    contact: {
      id: number;
      name: string;
      email?: string | null;
      phone_number?: string | null;
      is_email_opted_out: boolean;
      email_status?: string | null;
    } | null;
    messages: EmailMessage[];
  }> => (await api.get(`/email/inbox/conversations/${id}`)).data,
  reply: async (
    id: number,
    payload: {
      body: string;
      html_body?: string | null;
      subject?: string;
      email_account_id?: number;
      attachments?: { name: string; content_base64: string; content_type?: string }[];
      cc?: string[] | null;
      bcc?: string[] | null;
    }
  ) => (await api.post(`/email/inbox/conversations/${id}/reply`, payload)).data,
  markRead: async (id: number) => (await api.post(`/email/inbox/conversations/${id}/read`)).data,
  setStatus: async (id: number, status: string) =>
    (await api.post(`/email/inbox/conversations/${id}/status`, { status })).data,
  unreadCount: async (): Promise<{ unread: number; threads: number }> =>
    (await api.get("/email/inbox/unread-count")).data,

  // ---- suppression / events ---------------------------------------------
  suppression: async (params?: { page?: number; per_page?: number; search?: string }): Promise<Paged<EmailSuppression>> =>
    (await api.get("/email/suppression", { params })).data,
  addSuppression: async (payload: { email_address: string; reason?: string; source?: string }) =>
    (await api.post("/email/suppression", payload)).data,
  removeSuppression: async (id: number) => (await api.delete(`/email/suppression/${id}`)).data,
  //: Check a Brevo key the user just typed: which From addresses it has
  //: verified and whether their domains are authenticated. Nothing is saved.
  sendersPreview: async (apiKey: string): Promise<{
    success: boolean;
    error: string | null;
    senders: {
      email: string;
      name?: string;
      active?: boolean;
      domain?: string;
      domain_verified?: boolean;
      spf?: boolean;
      dkim?: boolean;
      recommended?: boolean;
    }[];
    domains: { domain?: string; verified?: boolean; authenticated?: boolean; dkim?: boolean; spf?: boolean }[];
    domains_error?: string | null;
  }> => (await api.post("/email/senders-preview", { api_key: apiKey })).data,
  //: Per-message activity: delivery, opens, the links that were clicked.
  messageEvents: async (
    messageId: number
  ): Promise<{
    message_id: number;
    summary: {
      status: string;
      open_count: number;
      click_count: number;
      unique_links_clicked: number;
      sent_at?: string | null;
      delivered_at?: string | null;
      first_opened_at?: string | null;
      first_clicked_at?: string | null;
      bounced_at?: string | null;
    };
    events: { id: number; event_type: string; link?: string | null; detail?: string | null; created_at?: string | null }[];
  }> => (await api.get(`/email/messages/${messageId}/events`)).data,
  //: One contact's email history, the way Brevo shows it.
  engagement: async (
    contactId: number
  ): Promise<{
    contact_id: number;
    email?: string | null;
    is_email_opted_out: boolean;
    is_email_undeliverable: boolean;
    email_status?: string | null;
    emails_sent: number;
    last_emailed_at?: string | null;
    totals: { sent: number; opened: number; clicked: number; bounced: number; open_rate: number; click_rate: number };
    links: { url: string; clicks: number }[];
    messages: {
      id: number;
      subject?: string | null;
      direction: string;
      status: string;
      open_count: number;
      click_count: number;
      created_at?: string | null;
    }[];
    events: { id: number; event_type: string; link?: string | null; created_at?: string | null }[];
  }> => (await api.get(`/email/contacts/${contactId}/engagement`)).data,
  deliverability: async (
    accountId: number,
    days = 30
  ): Promise<{
    account: EmailAccount;
    days: number;
    domains: { domain?: string; verified?: boolean; authenticated?: boolean }[];
    domains_error?: string | null;
    metrics: {
      sent: number;
      bounced: number;
      opened: number;
      clicked: number;
      unsubscribed: number;
      bounce_rate: number;
      open_rate: number;
      click_rate: number;
    };
    checks: { key: string; label: string; ok: boolean; detail: string }[];
    score: number;
  }> => (await api.get(`/email/accounts/${accountId}/deliverability`, { params: { days } })).data,
  events: async (params?: {
    page?: number;
    per_page?: number;
    event_type?: string;
    account_id?: number;
  }): Promise<Paged<EmailEvent>> => (await api.get("/email/events", { params })).data,
};

export default emailApi;
