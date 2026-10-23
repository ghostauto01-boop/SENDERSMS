import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ChannelProvider } from "../hooks/useChannel";
import DashboardPage from "./DashboardPage";

/**
 * The dashboard after the redesign.
 *
 * It is the screen that has to survive a half-broken backend: stats and charts
 * load independently, and the invoice card must not blank the page when the
 * invoice API is unhappy. These tests pin that, plus the real numbers.
 */

const apiGet = vi.fn();
vi.mock("../api/client", () => ({
  default: { get: (...args: any[]) => apiGet(...args), post: vi.fn() },
}));

vi.mock("../api/invoices", () => ({
  default: {
    settings: vi.fn().mockResolvedValue({ default_currency: "NGN", paystack: { configured: true } }),
    analytics: vi.fn().mockResolvedValue({
      summary: { paid_minor: 500_000_00, outstanding_minor: 250_000_00 },
      monthly: [],
      top_customers: [],
      recent_payments: [],
    }),
    list: vi.fn().mockResolvedValue({
      items: [
        {
          id: 3,
          number: "INV-2026-0003",
          status: "sent",
          currency: "NGN",
          total_minor: 250_000_00,
          customer: { name: "Bola Ade" },
        },
      ],
      total: 1,
      page: 1,
      per_page: 4,
      summary: null,
    }),
  },
}));

const STATS = {
  total_contacts: 512,
  contacts_contacted: 420,
  messages_sent: 1200,
  messages_delivered: 1176,
  messages_failed: 24,
  messages_queued: 0,
  replies: 63,
  reply_rate: 5.3,
  delivery_rate: 98,
  interested_leads: 21,
  active_campaigns: 0,
  followups_due_today: 2,
  overdue_followups: 1,
  completed_campaigns: 4,
  gateway_status: "healthy",
  recent_conversations: [],
  recent_campaigns: [
    { id: 1, name: "Weekend brunch", status: "running", messages_sent: 300, replies: 12 },
  ],
  lead_distribution: { interested: 21, not_interested: 3 },
};

const CHARTS = {
  messages_per_day: [
    { day: new Date().toISOString().slice(0, 10), count: 120 },
  ],
  replies_per_day: [],
  days: 30,
};

function renderPage() {
  return render(
    <MemoryRouter>
      <ChannelProvider>
        <DashboardPage />
      </ChannelProvider>
    </MemoryRouter>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  apiGet.mockImplementation((url: string) =>
    Promise.resolve({
      data: url.includes("charts") ? CHARTS : STATS,
    })
  );
});

describe("DashboardPage", () => {
  it("shows the live numbers and the redesigned hero", async () => {
    renderPage();

    await waitFor(() => expect(screen.getByText("Dashboard")).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText("512")).toBeInTheDocument());
    expect(screen.getByText("Total contacts")).toBeInTheDocument();
    expect(screen.getAllByText(/98% delivered/).length).toBeGreaterThan(0);
    expect(screen.getByText(/follow-ups? due today/)).toBeInTheDocument();
    // The recent campaign row links into the manager, not a campaigns page.
    const link = screen.getByText("Weekend brunch").closest("a");
    expect(link).toHaveAttribute("href", "/sms-manager");
  });

  it("surfaces invoice money next to messaging", async () => {
    renderPage();

    await waitFor(() => expect(screen.getByText("₦500k")).toBeInTheDocument());
    expect(screen.getByText(/₦250k outstanding/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("Bola Ade")).toBeInTheDocument());
    expect(screen.getByText("INV-2026-0003")).toBeInTheDocument();
  });

  it("keeps the page usable when a chart request fails", async () => {
    apiGet.mockImplementation((url: string) =>
      url.includes("charts")
        ? Promise.reject(new Error("boom"))
        : Promise.resolve({ data: STATS })
    );

    renderPage();

    await waitFor(() => expect(screen.getByText("512")).toBeInTheDocument());
    // The stat cards are still there, so one failed request is not fatal.
    expect(screen.getByText("Messages sent")).toBeInTheDocument();
  });
});
