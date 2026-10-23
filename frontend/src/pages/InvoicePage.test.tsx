import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import InvoicePage from "./InvoicePage";
import invoicesApi from "../api/invoices";

/**
 * The invoice studio.
 *
 * These tests pin the behaviour the user asked for, not the pixels: an invoice
 * list with real money in it, analytics that load on demand, and a working
 * "New invoice" path. The API is mocked at the module boundary — exactly the
 * layer the page talks to — so a change in the wire shape fails here rather
 * than in production.
 */

vi.mock("../api/invoices", () => ({
  default: {
    settings: vi.fn(),
    list: vi.fn(),
    analytics: vi.fn(),
    allPayments: vi.fn(),
    currencies: vi.fn(),
    get: vi.fn(),
    remove: vi.fn(),
    get_public: vi.fn(),
  },
}));

vi.mock("react-hot-toast", () => {
  const toast: any = vi.fn();
  toast.success = vi.fn();
  toast.error = vi.fn();
  return { default: toast };
});

const settings = {
  business_name: "Pikbites Kitchen",
  business_email: "billing@pikbites.test",
  business_phone: "+234 800 000 0000",
  business_address: "12 Ademola Way, Lagos",
  business_website: "",
  business_logo_url: "",
  prefix: "INV",
  default_currency: "NGN",
  default_tax_rate: 7.5,
  default_terms: "Pay within 14 days",
  default_notes: "",
  footer: "Thank you",
  currencies: ["NGN", "USD"],
  paystack: {
    configured: true,
    secret_key_masked: "sk_test_••••1234",
    public_key: "pk_test_1234",
    channels: ["card"],
    webhook_url: "https://example.test/api/v1/invoices/paystack/webhook",
    mode: "test",
  },
};

const invoice = {
  id: 7,
  number: "INV-2026-0007",
  public_token: "tok_abc",
  public_url: "https://example.test/i/tok_abc",
  status: "sent",
  status_label: "Sent",
  stored_status: "sent",
  currency: "NGN",
  currency_symbol: "₦",
  customer: {
    contact_id: null,
    name: "Ada Obi",
    email: "ada@example.test",
    phone: "+234 801 111 2222",
    address: "Lekki Phase 1",
    business: "Ada Studio",
  },
  issue_date: "2026-10-01",
  due_date: "2026-10-15",
  days_until_due: -8,
  subtotal_minor: 250_000_00,
  discount_minor: 0,
  tax_rate: 7.5,
  tax_minor: 18_750_00,
  total_minor: 268_750_00,
  amount_paid_minor: 0,
  balance_minor: 268_750_00,
  notes: null,
  terms: null,
  footer: null,
  items: [
    { description: "Dinner for two", quantity: 1, unit_price_minor: 250_000_00, amount_minor: 250_000_00 },
  ],
  payment_link: "https://example.test/i/tok_abc",
  paystack_reference: null,
  created_at: "2026-10-01T10:00:00Z",
  updated_at: "2026-10-01T10:00:00Z",
  sent_at: "2026-10-01T10:00:00Z",
  viewed_at: null,
  paid_at: null,
  last_reminder_at: null,
  reminders_sent: 0,
  payments: [],
  events: [],
  business: { name: "Pikbites Kitchen", email: "", phone: "", address: "", website: "", logo_url: "" },
  created_by: null,
};

const summary = {
  invoiced_minor: 268_750_00,
  paid_minor: 0,
  outstanding_minor: 268_750_00,
  overdue_minor: 268_750_00,
  draft_minor: 0,
  invoice_count: 1,
  paid_count: 0,
  overdue_count: 1,
  draft_count: 0,
  partial_count: 0,
  average_invoice_minor: 268_750_00,
  by_status: { sent: 1 },
};

const analytics = {
  currency: "NGN",
  summary,
  monthly: [
    { month: "2026-09", label: "Sep", invoiced_minor: 0, collected_minor: 0 },
    { month: "2026-10", label: "Oct", invoiced_minor: 268_750_00, collected_minor: 0 },
  ],
  top_customers: [
    { customer_name: "Ada Obi", invoiced_minor: 268_750_00, paid_minor: 0, invoices: 1 },
  ],
  recent_payments: [],
  due_in_14_days_minor: 0,
  generated_at: "2026-10-23T09:00:00Z",
};

const mocked = invoicesApi as unknown as Record<string, ReturnType<typeof vi.fn>>;

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/invoices"]}>
      <InvoicePage />
    </MemoryRouter>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  window.history.pushState({}, "", "/invoices");
  mocked.settings.mockResolvedValue(settings);
  mocked.list.mockResolvedValue({ items: [invoice], total: 1, page: 1, per_page: 20, summary });
  mocked.analytics.mockResolvedValue(analytics);
  mocked.allPayments.mockResolvedValue({
    items: [],
    total: 0,
    page: 1,
    per_page: 10,
    total_collected_minor: 0,
    by_provider: [],
  });
  mocked.currencies.mockResolvedValue({ currencies: [], default: "NGN" });
});

describe("InvoicePage", () => {
  it("lists invoices with their money, status and customer", async () => {
    renderPage();

    // The first paint may briefly be the loading skeleton (settings resolve and
    // trigger a currency-scoped refetch), so assert everything in one wait.
    await waitFor(() => {
      expect(screen.getByText(/INV-2026-0007/)).toBeInTheDocument();
      expect(screen.getByText("Ada Obi")).toBeInTheDocument();
      // Money is formatted from minor units, in Naira.
      expect(screen.getAllByText("₦268,750.00").length).toBeGreaterThan(0);
    });
    expect(screen.getByRole("button", { name: /new invoice/i })).toBeInTheDocument();
    expect(mocked.list).toHaveBeenCalled();
  });

  it("loads analytics only when its tab is chosen", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText(/INV-2026-0007/)).toBeInTheDocument());
    expect(mocked.analytics).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /analytics/i }));

    await waitFor(() => expect(mocked.analytics).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByText(/Revenue analytics/i)).toBeInTheDocument());
    expect(screen.getByText("Top customers")).toBeInTheDocument();
  });

  it("opens the editor from New invoice", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText(/INV-2026-0007/)).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /new invoice/i }));

    await waitFor(() => expect(screen.getByText("Save draft")).toBeInTheDocument());
    expect(screen.getByText("Line items")).toBeInTheDocument();
  });

  it("filters the list by status", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText(/INV-2026-0007/)).toBeInTheDocument());
    mocked.list.mockClear();

    fireEvent.click(screen.getByRole("button", { name: "Paid" }));

    await waitFor(() =>
      expect(mocked.list).toHaveBeenCalledWith(
        expect.objectContaining({ status: "paid", page: 1 })
      )
    );
  });
});
