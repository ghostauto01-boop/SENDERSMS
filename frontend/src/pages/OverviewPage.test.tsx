import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import OverviewPage from "./OverviewPage";

/**
 * The application overview is the screen that joins the two campaign systems
 * to the inbox. What matters is that it (a) reports the SAME numbers the
 * inbox would, and (b) gives you a working path from a campaign to its
 * replies.
 */

const apiGet = vi.fn();
const navigate = vi.fn();

vi.mock("../api/client", () => ({
  default: { get: (...a: any[]) => apiGet(...a), post: vi.fn() },
}));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<any>("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

// Recharts needs real layout; jsdom has none. Stub the responsive wrapper.
vi.mock("recharts", async () => {
  const actual = await vi.importActual<any>("recharts");
  return {
    ...actual,
    ResponsiveContainer: ({ children }: any) => (
      <div style={{ width: 800, height: 300 }}>{children}</div>
    ),
  };
});

const METRICS = {
  period_days: 30,
  messaging: {
    sent: 420, delivered: 391, failed: 12, queued: 5, replies: 63,
    delivery_rate: 93.1, reply_rate: 15, failure_rate: 2.8,
  },
  inbox: {
    conversations: 118, replied: 63, unread: 9, interested: 21,
    sentiment: { positive: 40, negative: 8, neutral: 15 },
  },
  audience: { contacts: 512, opted_out: 7, opt_out_rate: 1.4 },
  campaigns: {
    campaigns: 3, live: 2, audience: 500, sent: 420, delivered: 391, failed: 12,
    queued: 5, leads: 118, replied: 63, unread: 9, interested: 21,
    delivery_rate: 93.1, reply_rate: 15,
  },
  top_campaigns: [],
  series: [
    { date: "2026-08-11", sent: 20, replies: 3, delivered: 19, failed: 1 },
    { date: "2026-08-12", sent: 32, replies: 6, delivered: 30, failed: 0 },
  ],
};

const CAMPAIGNS = [
  {
    id: 1, kind: "campaign", name: "Lagos Restaurants", description: "Cold outreach",
    status: "running", is_live: true, audience: 300, sent: 280, delivered: 265,
    failed: 6, queued: 3, leads: 88, replied: 41, unread: 5, interested: 14,
    delivery_rate: 94.6, reply_rate: 14.6,
    scheduled_start_at: null, started_at: null, completed_at: null, updated_at: null,
    inbox_url: "/inbox?campaign_id=1", replies_url: "/inbox?campaign_id=1&replied=1",
  },
  {
    id: 9, kind: "ads", name: "Abuja Retargeting", description: null,
    status: "active", is_live: true, audience: 200, sent: 140, delivered: 126,
    failed: 6, queued: 2, leads: 30, replied: 22, unread: 4, interested: 7,
    delivery_rate: 90, reply_rate: 15.7,
    scheduled_start_at: null, started_at: null, completed_at: null, updated_at: null,
    inbox_url: "/inbox?ads_campaign_id=9", replies_url: "/inbox?ads_campaign_id=9&replied=1",
  },
  {
    id: 4, kind: "campaign", name: "Old Draft", description: null,
    status: "draft", is_live: false, audience: 0, sent: 0, delivered: 0,
    failed: 0, queued: 0, leads: 0, replied: 0, unread: 0, interested: 0,
    delivery_rate: 0, reply_rate: 0,
    scheduled_start_at: null, started_at: null, completed_at: null, updated_at: null,
    inbox_url: "/inbox?campaign_id=4", replies_url: "/inbox?campaign_id=4&replied=1",
  },
];

function renderPage() {
  return render(
    <MemoryRouter>
      <OverviewPage />
    </MemoryRouter>
  );
}

beforeEach(() => {
  apiGet.mockReset();
  navigate.mockReset();
  apiGet.mockImplementation((url: string) => {
    if (url === "/overview/metrics") return Promise.resolve({ data: METRICS });
    if (url === "/overview/campaigns")
      return Promise.resolve({ data: { items: CAMPAIGNS, totals: METRICS.campaigns } });
    return Promise.reject(new Error("unmocked GET " + url));
  });
});

describe("OverviewPage — one screen for campaigns + inbox", () => {
  it("shows the headline metrics from the unified endpoint", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("Lagos Restaurants")).toBeInTheDocument());

    expect(screen.getByText("Live campaigns")).toBeInTheDocument();
    expect(screen.getByText("420")).toBeInTheDocument();      // sent
    expect(screen.getByText("93.1%")).toBeInTheDocument();     // delivery rate
    expect(screen.getByText("Unread in inbox")).toBeInTheDocument();
    expect(screen.getByText("Interested leads")).toBeInTheDocument();
  });

  it("lists BOTH campaign systems in one place", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("Lagos Restaurants")).toBeInTheDocument());

    // Classic campaign and SMS Ads Manager campaign, side by side.
    expect(screen.getByText("Abuja Retargeting")).toBeInTheDocument();
    // Drafts are hidden behind the "All" scope by default.
    expect(screen.queryByText("Old Draft")).not.toBeInTheDocument();
  });

  it("switching to All reveals non-running campaigns", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("Lagos Restaurants")).toBeInTheDocument());

    fireEvent.click(screen.getByText(/^All \(3\)$/));

    await waitFor(() => expect(screen.getByText("Old Draft")).toBeInTheDocument());
  });

  it("clicking a campaign's reply count deep-links into the filtered inbox", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("Lagos Restaurants")).toBeInTheDocument());

    fireEvent.click(screen.getByText("41 replies"));

    expect(navigate).toHaveBeenCalledWith("/inbox?campaign_id=1&replied=1");
  });

  it("clicking the lead count opens the inbox scoped to that campaign", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("Abuja Retargeting")).toBeInTheDocument());

    fireEvent.click(screen.getByText("30 leads"));

    expect(navigate).toHaveBeenCalledWith("/inbox?ads_campaign_id=9");
  });

  it("renders the reply sentiment breakdown", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("How people are replying")).toBeInTheDocument());

    expect(screen.getByText("Positive")).toBeInTheDocument();
    expect(screen.getByText("Negative")).toBeInTheDocument();
    // 40 of 63 classified replies = 63%
    expect(screen.getByText("(63%)")).toBeInTheDocument();
  });

  it("surfaces a retry instead of a blank screen when the API fails", async () => {
    apiGet.mockImplementation(() => Promise.reject({ response: { data: {} } }));
    renderPage();

    await waitFor(() => expect(screen.getByText("Couldn't load the overview")).toBeInTheDocument());
    expect(screen.getByText("Retry")).toBeInTheDocument();
  });
});
