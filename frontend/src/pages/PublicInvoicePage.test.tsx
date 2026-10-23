import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import PublicInvoicePage from "./PublicInvoicePage";
import invoicesApi from "../api/invoices";

/**
 * The customer's invoice page.
 *
 * It must work with no session, show the amount that is actually due, and only
 * ever send the customer to a server-created Paystack checkout. The returning
 * `?reference=` path is verified before the page claims the invoice is paid.
 */

vi.mock("../api/invoices", () => ({
  default: {
    publicGet: vi.fn(),
    publicPay: vi.fn(),
    publicVerify: vi.fn(),
  },
}));

vi.mock("react-hot-toast", () => {
  const toast: any = vi.fn();
  toast.success = vi.fn();
  toast.error = vi.fn();
  return { default: toast };
});

const invoice = {
  id: 12,
  number: "INV-2026-0012",
  public_token: "token_xyz",
  public_url: "https://example.test/i/token_xyz",
  status: "sent",
  status_label: "Sent",
  stored_status: "sent",
  currency: "NGN",
  currency_symbol: "₦",
  customer: {
    contact_id: null,
    name: "Chidi Nwosu",
    email: "chidi@example.test",
    phone: "+234 802 000 0000",
    address: "Ikoyi, Lagos",
    business: null,
  },
  issue_date: "2026-10-05",
  due_date: "2026-10-19",
  days_until_due: 2,
  subtotal_minor: 120_000_00,
  discount_minor: 0,
  tax_rate: 0,
  tax_minor: 0,
  total_minor: 120_000_00,
  amount_paid_minor: 0,
  balance_minor: 120_000_00,
  notes: "Thanks for your order.",
  terms: null,
  footer: null,
  items: [
    { description: "Catering — 40 guests", quantity: 1, unit_price_minor: 120_000_00, amount_minor: 120_000_00 },
  ],
  payment_link: "https://example.test/i/token_xyz",
  paystack_reference: null,
  created_at: "2026-10-05T08:00:00Z",
  updated_at: "2026-10-05T08:00:00Z",
  sent_at: "2026-10-05T08:00:00Z",
  viewed_at: null,
  paid_at: null,
  last_reminder_at: null,
  reminders_sent: 0,
  payments: [],
  business: {
    name: "Pikbites Kitchen",
    email: "hello@pikbites.test",
    phone: "+234 700 000 0000",
    address: "12 Ademola Way, Lagos",
    website: "",
    logo_url: "",
  },
};

const mocked = invoicesApi as unknown as Record<string, ReturnType<typeof vi.fn>>;

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/i/:token" element={<PublicInvoicePage />} />
      </Routes>
    </MemoryRouter>
  );
}

const originalLocation = window.location;

beforeEach(() => {
  vi.clearAllMocks();
  mocked.publicGet.mockResolvedValue(invoice);
});
afterEach(() => {
  Object.defineProperty(window, "location", { value: originalLocation, writable: true });
});

describe("PublicInvoicePage", () => {
  it("shows the invoice without any login", async () => {
    renderAt("/i/token_xyz");

    await waitFor(() => expect(screen.getAllByText(/INV-2026-0012/).length).toBeGreaterThan(0));
    expect(screen.getByText("Pikbites Kitchen")).toBeInTheDocument();
    expect(screen.getByText(/Catering — 40 guests/)).toBeInTheDocument();
    // Amount due, line total and subtotal all read the same in this invoice.
    expect(screen.getAllByText("₦120,000.00").length).toBeGreaterThan(0);
    expect(mocked.publicGet).toHaveBeenCalledWith("token_xyz");
  });

  it("starts a Paystack checkout with the customer email", async () => {
    mocked.publicPay.mockResolvedValue({
      success: true,
      authorization_url: "https://checkout.paystack.com/abc123",
      reference: "INV-12-ABC",
    });
    // jsdom refuses real navigation; record the assignment instead.
    const hrefSetter = vi.fn();
    Object.defineProperty(window, "location", {
      value: { ...originalLocation, href: "", search: "" },
      writable: true,
    });
    Object.defineProperty(window.location, "href", {
      set: hrefSetter,
      get: () => "http://localhost/",
      configurable: true,
    });

    renderAt("/i/token_xyz");
    await waitFor(() => expect(screen.getAllByText(/INV-2026-0012/).length).toBeGreaterThan(0));

    fireEvent.click(screen.getByRole("button", { name: /pay ₦120,000.00/i }));

    await waitFor(() => expect(mocked.publicPay).toHaveBeenCalledWith("token_xyz", "chidi@example.test"));
    await waitFor(() =>
      expect(hrefSetter).toHaveBeenCalledWith("https://checkout.paystack.com/abc123")
    );
  });

  it("confirms the payment when Paystack returns with a reference", async () => {
    mocked.publicVerify.mockResolvedValue({
      success: true,
      gateway_status: "success",
      invoice: { ...invoice, status: "paid", amount_paid_minor: 120_000_00, balance_minor: 0, paid_at: "2026-10-20T10:00:00Z" },
    });

    renderAt("/i/token_xyz?reference=INV-12-ABC");

    await waitFor(() =>
      expect(mocked.publicVerify).toHaveBeenCalledWith("token_xyz", "INV-12-ABC")
    );
    await waitFor(() => expect(screen.getByText(/Paid in full/i)).toBeInTheDocument());
  });

  it("explains an expired link instead of showing an empty invoice", async () => {
    mocked.publicGet.mockRejectedValue({ response: { status: 404 } });

    renderAt("/i/gone");

    await waitFor(() => expect(screen.getByText(/Invoice unavailable/i)).toBeInTheDocument());
    expect(screen.getByText(/not valid/i)).toBeInTheDocument();
  });
});
