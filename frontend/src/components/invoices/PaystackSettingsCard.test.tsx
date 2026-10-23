import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import PaystackSettingsCard from "./PaystackSettingsCard";
import invoicesApi from "../../api/invoices";

/**
 * Paystack configuration from the UI.
 *
 * The secret key has three states and getting them wrong is expensive: keeping
 * a saved key must not overwrite it with an empty string, and "Remove" must
 * actually clear it. Both directions are pinned here.
 */

vi.mock("../../api/invoices", () => ({
  default: {
    settings: vi.fn(),
    saveSettings: vi.fn(),
    testPaystack: vi.fn(),
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
  business_website: "pikbites.test",
  business_logo_url: "",
  prefix: "INV",
  default_currency: "NGN",
  default_tax_rate: 7.5,
  default_terms: "Payment is due within 14 days.",
  default_notes: "",
  footer: "",
  currencies: ["NGN", "USD"],
  paystack: {
    configured: true,
    secret_key_masked: "sk_test_••••1234",
    public_key: "pk_test_1234",
    channels: ["card", "bank_transfer"],
    webhook_url: "https://example.test/api/v1/invoices/paystack/webhook",
    mode: "test" as const,
  },
};

const mocked = invoicesApi as unknown as Record<string, ReturnType<typeof vi.fn>>;

beforeEach(() => {
  vi.clearAllMocks();
  mocked.settings.mockResolvedValue(settings);
  mocked.saveSettings.mockResolvedValue(settings);
});

describe("PaystackSettingsCard", () => {
  it("loads the saved business identity and masks the secret", async () => {
    render(<PaystackSettingsCard />);

    await waitFor(() => expect(screen.getByDisplayValue("Pikbites Kitchen")).toBeInTheDocument());
    expect(screen.getByDisplayValue("pk_test_1234")).toBeInTheDocument();
    expect(screen.getByText(/saved sk_test_••••1234/)).toBeInTheDocument();
    // The webhook URL to register in the Paystack dashboard is shown, not hidden.
    expect(screen.getByText(settings.paystack.webhook_url)).toBeInTheDocument();
  });

  it("keeps the saved secret when the field is left blank", async () => {
    render(<PaystackSettingsCard />);
    await waitFor(() => expect(screen.getByDisplayValue("Pikbites Kitchen")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /save settings/i }));

    await waitFor(() => expect(mocked.saveSettings).toHaveBeenCalled());
    const payload = mocked.saveSettings.mock.calls[0][0];
    expect(payload).not.toHaveProperty("paystack_secret_key");
    expect(payload.business_name).toBe("Pikbites Kitchen");
    expect(payload.paystack_public_key).toBe("pk_test_1234");
  });

  it("sends a typed secret as a replacement", async () => {
    render(<PaystackSettingsCard />);
    await waitFor(() => expect(screen.getByDisplayValue("Pikbites Kitchen")).toBeInTheDocument());

    fireEvent.change(screen.getByPlaceholderText(/leave blank to keep saved key/i), {
      target: { value: "sk_live_newkey" },
    });
    fireEvent.click(screen.getByRole("button", { name: /save settings/i }));

    await waitFor(() => expect(mocked.saveSettings).toHaveBeenCalled());
    expect(mocked.saveSettings.mock.calls[0][0].paystack_secret_key).toBe("sk_live_newkey");
  });

  it("clears the secret only when Remove is used", async () => {
    render(<PaystackSettingsCard />);
    await waitFor(() => expect(screen.getByDisplayValue("Pikbites Kitchen")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /^remove$/i }));
    fireEvent.click(screen.getByRole("button", { name: /save settings/i }));

    await waitFor(() => expect(mocked.saveSettings).toHaveBeenCalled());
    expect(mocked.saveSettings.mock.calls[0][0].paystack_secret_key).toBe("");
  });

  it("reports the result of the connection test", async () => {
    mocked.testPaystack.mockResolvedValue({
      success: true,
      balances: [{ currency: "NGN", balance_minor: 1_250_000_00 }],
    });
    render(<PaystackSettingsCard />);
    await waitFor(() => expect(screen.getByDisplayValue("Pikbites Kitchen")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /test paystack connection/i }));

    await waitFor(() => expect(screen.getByText(/Connected/i)).toBeInTheDocument());
    expect(screen.getByText(/1,250,000/)).toBeInTheDocument();
  });
});
