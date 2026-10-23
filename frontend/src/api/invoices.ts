import api from "./client";

/**
 * Invoice studio types + calls.
 *
 * Every amount is a number of MINOR units (kobo/cents) — exactly what the API
 * stores and what Paystack charges. Formatting happens at the edge of the UI
 * (`utils/money.ts`), never by dividing in a component and hoping.
 */

export type InvoiceStatus =
  | "draft"
  | "sent"
  | "viewed"
  | "partial"
  | "paid"
  | "overdue"
  | "void";

export type CurrencyCode = "NGN" | "USD" | "GHS" | "ZAR" | "KES" | "XOF" | "EGP";

export interface InvoiceItem {
  id?: number;
  description: string;
  quantity: number;
  unit_price_minor: number;
  amount_minor?: number;
  position?: number;
}

export interface InvoiceCustomer {
  contact_id: number | null;
  name: string;
  email: string | null;
  phone: string | null;
  address: string | null;
  business: string | null;
}

export interface InvoicePaymentRow {
  id: number;
  provider: string;
  reference: string;
  amount_minor: number;
  currency: string;
  status: "pending" | "success" | "failed" | "reversed";
  channel: string | null;
  gateway_id: string | null;
  payer_email: string | null;
  note: string | null;
  paid_at: string | null;
  created_at: string | null;
}

export interface InvoiceEventRow {
  id: number;
  event_type: string;
  title: string;
  body: string | null;
  created_at: string | null;
}

export interface Invoice {
  id: number;
  number: string;
  public_token: string;
  public_url: string;
  status: InvoiceStatus;
  status_label: string;
  stored_status: string;
  currency: CurrencyCode | string;
  currency_symbol: string;
  customer: InvoiceCustomer;
  issue_date: string;
  due_date: string;
  days_until_due: number | null;
  subtotal_minor: number;
  discount_minor: number;
  tax_rate: number;
  tax_minor: number;
  total_minor: number;
  amount_paid_minor: number;
  balance_minor: number;
  notes: string | null;
  terms: string | null;
  footer: string | null;
  items: InvoiceItem[];
  payment_link: string;
  paystack_reference: string | null;
  created_at: string;
  updated_at: string;
  sent_at: string | null;
  viewed_at: string | null;
  paid_at: string | null;
  last_reminder_at: string | null;
  reminders_sent: number;
  payments?: InvoicePaymentRow[];
  events?: InvoiceEventRow[];
  business?: {
    name: string;
    email: string;
    phone: string;
    address: string;
    website: string;
    logo_url: string;
  };
  created_by?: string | null;
}

export interface InvoiceSummary {
  invoiced_minor: number;
  paid_minor: number;
  outstanding_minor: number;
  overdue_minor: number;
  draft_minor: number;
  invoice_count: number;
  paid_count: number;
  overdue_count: number;
  draft_count: number;
  partial_count: number;
  average_invoice_minor: number;
  by_status: Record<string, number>;
}

export interface InvoiceSettings {
  business_name: string;
  business_email: string;
  business_phone: string;
  business_address: string;
  business_website: string;
  business_logo_url: string;
  prefix: string;
  default_currency: CurrencyCode | string;
  default_tax_rate: number;
  default_terms: string;
  default_notes: string;
  footer: string;
  currencies: string[];
  paystack: {
    configured: boolean;
    secret_key_masked: string;
    public_key: string;
    channels: string[];
    webhook_url: string;
    mode: "test" | "live" | "not_configured";
  };
}

export interface InvoiceAnalytics {
  currency: string;
  summary: InvoiceSummary;
  monthly: {
    month: string;
    label: string;
    invoiced_minor: number;
    collected_minor: number;
  }[];
  recent_payments: {
    id: number;
    invoice_id: number;
    invoice_number: string;
    customer_name: string;
    amount_minor: number;
    currency: string;
    provider: string;
    channel: string | null;
    paid_at: string | null;
  }[];
  top_customers: {
    customer_name: string;
    invoiced_minor: number;
    paid_minor: number;
    invoices: number;
  }[];
  due_in_14_days_minor: number;
  generated_at: string;
}

export interface InvoiceInput {
  customer_name: string;
  customer_email?: string | null;
  customer_phone?: string | null;
  customer_address?: string | null;
  customer_business?: string | null;
  contact_id?: number | null;
  currency: string;
  issue_date?: string | null;
  due_date?: string | null;
  items: { description: string; quantity: number; unit_price_minor: number }[];
  discount_minor?: number;
  tax_rate?: number;
  notes?: string | null;
  terms?: string | null;
  footer?: string | null;
  send?: boolean;
  send_email?: boolean;
}

export interface PaymentInput {
  amount_minor: number;
  provider?: string;
  reference?: string | null;
  status?: string;
  note?: string | null;
  paid_at?: string | null;
}

export interface PaymentsHistory {
  total: number;
  page: number;
  per_page: number;
  items: (InvoicePaymentRow & {
    invoice_id: number;
    invoice_number: string;
    customer_name: string;
    invoice_status: string;
  })[];
  total_collected_minor: number;
  by_provider: { provider: string; amount_minor: number }[];
}

const invoicesApi = {
  list: (params: {
    page?: number;
    per_page?: number;
    status?: string;
    currency?: string;
    q?: string;
    contact_id?: number;
  }) =>
    api
      .get<{
        total: number;
        page: number;
        per_page: number;
        items: Invoice[];
        summary: InvoiceSummary | null;
      }>("/invoices", { params })
      .then((r) => r.data),

  get: (id: number) => api.get<Invoice>(`/invoices/${id}`).then((r) => r.data),

  create: (body: InvoiceInput) =>
    api
      .post<{
        success: boolean;
        invoice: Invoice;
        message: string;
        email_sent?: boolean;
        email_message?: string;
        mailto_url?: string | null;
      }>("/invoices", body)
      .then((r) => r.data),

  update: (id: number, body: Partial<InvoiceInput> & { status?: string }) =>
    api.patch<Invoice>(`/invoices/${id}`, body).then((r) => r.data),

  remove: (id: number) =>
    api.delete<{ success: boolean; number: string }>(`/invoices/${id}`).then((r) => r.data),

  duplicate: (id: number) =>
    api.post<{ success: boolean; invoice: Invoice }>(`/invoices/${id}/duplicate`).then((r) => r.data),

  send: (id: number, emailCustomer = true) =>
    api
      .post<{
        success: boolean;
        email_sent: boolean;
        email_message: string;
        mailto_url: string | null;
        public_url: string;
        message: string;
        invoice: Invoice;
      }>(`/invoices/${id}/send`, null, { params: { email_customer: emailCustomer } })
      .then((r) => r.data),

  remind: (id: number) =>
    api
      .post<{
        success: boolean;
        email_sent: boolean;
        message: string;
        mailto_url: string | null;
        invoice: Invoice;
      }>(`/invoices/${id}/remind`)
      .then((r) => r.data),

  void: (id: number) =>
    api.post<{ success: boolean; invoice: Invoice }>(`/invoices/${id}/void`).then((r) => r.data),

  rotateLink: (id: number) =>
    api
      .post<{ success: boolean; public_url: string; invoice: Invoice }>(
        `/invoices/${id}/rotate-link`
      )
      .then((r) => r.data),

  pay: (id: number, email?: string) =>
    api
      .post<{
        success: boolean;
        authorization_url: string;
        access_code?: string;
        reference: string;
        invoice: Invoice;
      }>(`/invoices/${id}/pay`, { email })
      .then((r) => r.data),

  recordPayment: (id: number, body: PaymentInput) =>
    api
      .post<{ success: boolean; payment: InvoicePaymentRow; invoice: Invoice }>(
        `/invoices/${id}/payments`,
        body
      )
      .then((r) => r.data),

  payments: (id: number) =>
    api
      .get<{ items: InvoicePaymentRow[] }>(`/invoices/${id}/payments`)
      .then((r) => r.data),

  events: (id: number) =>
    api
      .get<{ items: InvoiceEventRow[] }>(`/invoices/${id}/events`)
      .then((r) => r.data),

  allPayments: (params: {
    page?: number;
    per_page?: number;
    currency?: string;
    provider?: string;
    status?: string;
  }) => api.get<PaymentsHistory>("/invoices/payments", { params }).then((r) => r.data),

  analytics: (currency: string, days = 365) =>
    api
      .get<InvoiceAnalytics>("/invoices/analytics", { params: { currency, days } })
      .then((r) => r.data),

  currencies: () =>
    api
      .get<{ currencies: { code: string; symbol: string }[]; default: string }>(
        "/invoices/currencies"
      )
      .then((r) => r.data),

  settings: () => api.get<InvoiceSettings>("/invoices/settings").then((r) => r.data),

  saveSettings: (body: Partial<InvoiceSettings> & { paystack_secret_key?: string }) =>
    api.put<InvoiceSettings>("/invoices/settings", body).then((r) => r.data),

  testPaystack: () =>
    api
      .post<{
        success: boolean;
        error?: string;
        message?: string;
        balances?: { currency: string; balance_minor: number }[];
      }>("/invoices/settings/test")
      .then((r) => r.data),

  // ---- Public (no session) ----
  publicGet: (token: string) =>
    api.get<Invoice>(`/invoices/public/${token}`).then((r) => r.data),

  publicPay: (token: string, email?: string) =>
    api
      .post<{
        success: boolean;
        authorization_url: string;
        reference: string;
      }>(`/invoices/public/${token}/pay`, { email })
      .then((r) => r.data),

  publicVerify: (token: string, reference: string) =>
    api
      .get<{ success: boolean; gateway_status: string; invoice: Invoice }>(
        `/invoices/public/${token}/verify`,
        { params: { reference } }
      )
      .then((r) => r.data),
};

export default invoicesApi;
