import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";
import SMSManagerPage from "./SMSManagerPage";

vi.mock("react-hot-toast", () => ({
  default: { success: vi.fn(), error: vi.fn() },
  Toaster: () => null,
}));

const campaign = {
  id: 1,
  name: "Lagos outreach",
  description: "Jewelry businesses",
  objective: "replies",
  status: "active",
  daily_limit: 200,
  total_limit: null,
  drip_mode: "off",
  drip_batch_size: 1,
  drip_interval_minutes: 0,
  pacing: "even",
  continuous: true,
  always_on: false,
  optimization_mode: "manual",
  queued_edit_policy: "keep",
  priority: "normal",
  test_mode: false,
  sent_count: 40,
  created_at: "2026-09-01T10:00:00Z",
  updated_at: "2026-09-01T10:00:00Z",
  state: "sending",
  score: 12.5,
  stats: {
    assigned: 100,
    sent: 40,
    delivered: 38,
    failed: 1,
    pending: 60,
    skipped: 0,
    replies: 8,
    positive_replies: 5,
    negative_replies: 1,
    conversions: 0,
    opt_outs: 1,
    delivery_rate: 95,
    reply_rate: 20,
    positive_reply_rate: 12.5,
    negative_reply_rate: 2.5,
    conversion_rate: 0,
    opt_out_rate: 2.5,
    failure_rate: 2.5,
  },
};

const overview = {
  active_campaigns: 1,
  total_campaigns: 1,
  totals: campaign.stats,
  followups_due: 3,
  meetings: 2,
  suppressed: 4,
  credits_used: 40,
  series: [{ date: "2026-09-01", sent: 40, replies: 8 }],
};

const reference = {
  lists: [{ id: 1, name: "Jewelry Lagos", count: 500 }],
  tags: ["Jewelry"],
  statuses: ["new"],
  total_contacts: 500,
  objectives: [{ value: "replies", label: "Get replies" }],
};

vi.mock("../api/ads", () => {
  const api = {
    listCampaigns: vi.fn(() => Promise.resolve({ total: 1, items: [campaign] })),
    overview: vi.fn(() => Promise.resolve(overview)),
    reference: vi.fn(() => Promise.resolve(reference)),
    activity: vi.fn(() => Promise.resolve({ items: [] })),
    followups: vi.fn(() => Promise.resolve({ items: [] })),
    calendar: vi.fn(() => Promise.resolve({ items: [] })),
    suppression: vi.fn(() => Promise.resolve({ items: [], total: 0 })),
    exportUrl: (k: string) => `/api/v1/ads/export/${k}`,
  };
  return { default: api, adsApi: api };
});

describe("SMS Ads Manager", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows the overview with real campaign metrics", async () => {
    render(<SMSManagerPage />);
    expect(await screen.findByText("SMS Ads Manager")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("Active campaigns")).toBeInTheDocument());
    // Campaign card from the "Running now" list.
    expect(await screen.findByText("Lagos outreach")).toBeInTheDocument();
    expect(screen.getByText("Delivery rate")).toBeInTheDocument();
  });

  it("navigates to the campaigns section and filters", async () => {
    const user = userEvent.setup();
    render(<SMSManagerPage />);
    await screen.findByText("SMS Ads Manager");
    await user.click(screen.getByRole("button", { name: /Campaigns/i }));
    const search = await screen.findByPlaceholderText("Search campaigns…");
    await user.type(search, "nothing-matches");
    await waitFor(() => expect(screen.getByText("No campaigns yet")).toBeInTheDocument());
  });

  it("shows the suppression section explaining send-time checks", async () => {
    const user = userEvent.setup();
    render(<SMSManagerPage />);
    await screen.findByText("SMS Ads Manager");
    await user.click(screen.getByRole("button", { name: /Suppression/i }));
    expect(await screen.findByText(/blocked at send time/i)).toBeInTheDocument();
  });
});
