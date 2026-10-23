import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import PaymentLinkSheet from "./PaymentLinkSheet";
import invoicesApi from "../../api/invoices";

/**
 * The payment-link flow the operator asked for: a product, an amount, a link.
 *
 * These tests pin the promise of the screen — no customer details are needed
 * to create a link, the link is shown (and copyable) straight away, and the
 * API is called with exactly the money that was typed.
 */

vi.mock("../../api/invoices", () => ({
  default: { createLink: vi.fn() },
}));

vi.mock("react-hot-toast", () => {
  const toast: any = vi.fn();
  toast.success = vi.fn();
  toast.error = vi.fn();
  return { default: toast };
});

const mocked = invoicesApi as unknown as { createLink: ReturnType<typeof vi.fn> };

const settings = {
  default_currency: "NGN",
  currencies: ["NGN", "USD"],
  paystack: { configured: true },
} as any;

const createdInvoice = {
  id: 31,
  number: "INV-2026-0031",
  public_token: "tok_link",
  public_url: "https://example.test/i/tok_link",
  kind: "link",
  link_title: "Logo design",
  title: "Logo design",
  status: "sent",
  currency: "NGN",
  currency_symbol: "₦",
  total_minor: 5_000_000,
  balance_minor: 5_000_000,
  customer: { name: "Customer", email: null, phone: null, address: null, business: null, contact_id: null },
  items: [],
} as any;

beforeEach(() => {
  vi.clearAllMocks();
  mocked.createLink.mockResolvedValue({
    success: true,
    invoice: createdInvoice,
    public_url: createdInvoice.public_url,
    message: "Payment link ready — copy it or share it.",
  });
});

describe("PaymentLinkSheet", () => {
  it("creates a link from a title and an amount, with no customer details", async () => {
    const onCreated = vi.fn();
    render(<PaymentLinkSheet settings={settings} onClose={() => {}} onCreated={onCreated} />);

    fireEvent.change(screen.getByPlaceholderText(/logo design/i), {
      target: { value: "Logo design" },
    });
    fireEvent.change(screen.getByLabelText("Amount"), { target: { value: "50000" } });
    fireEvent.click(screen.getByRole("button", { name: /create payment link/i }));

    await waitFor(() => expect(mocked.createLink).toHaveBeenCalledTimes(1));
    expect(mocked.createLink).toHaveBeenCalledWith(
      expect.objectContaining({
        title: "Logo design",
        amount_minor: 5_000_000,
        currency: "NGN",
        customer_name: null,
        customer_email: null,
      })
    );
    expect(onCreated).toHaveBeenCalledWith(createdInvoice, expect.any(String));
  });

  it("shows the finished link with a share action ready", async () => {
    render(<PaymentLinkSheet settings={settings} onClose={() => {}} onCreated={() => {}} />);

    fireEvent.change(screen.getByPlaceholderText(/logo design/i), {
      target: { value: "Logo design" },
    });
    fireEvent.change(screen.getByLabelText("Amount"), { target: { value: "50000" } });
    fireEvent.click(screen.getByRole("button", { name: /create payment link/i }));

    await waitFor(() => expect(screen.getByDisplayValue(createdInvoice.public_url)).toBeInTheDocument());
    expect(screen.getByText(/₦50,000/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /whatsapp/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /done/i })).toBeInTheDocument();
  });

  it("will not create a link without an amount", () => {
    render(<PaymentLinkSheet settings={settings} onClose={() => {}} onCreated={() => {}} />);
    fireEvent.change(screen.getByPlaceholderText(/logo design/i), {
      target: { value: "Logo design" },
    });
    const button = screen.getByRole("button", { name: /create payment link/i });
    expect(button).toBeDisabled();
    expect(mocked.createLink).not.toHaveBeenCalled();
  });
});
