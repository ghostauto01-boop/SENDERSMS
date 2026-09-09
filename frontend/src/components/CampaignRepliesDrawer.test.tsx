import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import CampaignRepliesDrawer from "./CampaignRepliesDrawer";

/**
 * The chain the user asked for:
 *   campaign -> its replies -> click a person -> that person's chat.
 * These tests pin the last two links.
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

const PERFORMANCE = {
  sent: 120, delivered: 112, failed: 4, queued: 2,
  leads: 40, replied: 2, unread: 1, interested: 1, opted_out: 0,
  sentiment: { positive: 1, negative: 1, neutral: 0 },
  delivery_rate: 93.3, reply_rate: 5, positive_rate: 50,
  failure_rate: 3.3, opt_out_rate: 0,
};

const REPLIED = [
  {
    conversation_id: 11, contact_id: 501, contact_name: "Chidi Okafor",
    contact_phone: "+2348012345678", lead_status: "new", status: "interested",
    unread_count: 1, message_count: 3,
    last_message_preview: "Yes please send details",
    last_message_at: "2026-09-08T10:00:00Z",
    has_replied: true,
    last_reply: {
      body: "Yes please send details", created_at: "2026-09-08T10:00:00Z",
      ai_sentiment: "positive", ai_intent: "interested",
    },
  },
  {
    conversation_id: 12, contact_id: 502, contact_name: "Amaka Obi",
    contact_phone: "+2348098765432", lead_status: "new", status: "open",
    unread_count: 0, message_count: 2,
    last_message_preview: "Not interested, stop",
    last_message_at: "2026-09-07T09:00:00Z",
    has_replied: true,
    last_reply: {
      body: "Not interested, stop", created_at: "2026-09-07T09:00:00Z",
      ai_sentiment: "negative", ai_intent: "opt_out",
    },
  },
];

const NO_REPLY = {
  conversation_id: 13, contact_id: 503, contact_name: "Bola Ade",
  contact_phone: "+2348055555555", lead_status: "new", status: "open",
  unread_count: 0, message_count: 1,
  last_message_preview: "Hi, we help restaurants…",
  last_message_at: "2026-09-06T09:00:00Z",
  has_replied: false, last_reply: null,
};

function renderDrawer(props: Partial<React.ComponentProps<typeof CampaignRepliesDrawer>> = {}) {
  return render(
    <MemoryRouter>
      <CampaignRepliesDrawer
        campaignId={7}
        campaignName="Lagos Restaurants"
        onClose={props.onClose || vi.fn()}
        {...props}
      />
    </MemoryRouter>
  );
}

beforeEach(() => {
  apiGet.mockReset();
  navigate.mockReset();
  apiGet.mockImplementation((url: string, cfg: any) => {
    if (url === "/campaigns/7/performance") return Promise.resolve({ data: PERFORMANCE });
    if (url === "/campaigns/7/conversations") {
      const items = cfg?.params?.replied_only ? REPLIED : [...REPLIED, NO_REPLY];
      return Promise.resolve({ data: { total: items.length, items, page: 1, per_page: 200 } });
    }
    return Promise.reject(new Error("unmocked GET " + url));
  });
});

describe("CampaignRepliesDrawer — campaign to reply to chat", () => {
  it("opens on the replies and shows what each person actually said", async () => {
    renderDrawer();
    await waitFor(() => expect(screen.getByText("Chidi Okafor")).toBeInTheDocument());

    expect(screen.getByText("“Yes please send details”")).toBeInTheDocument();
    expect(screen.getByText("“Not interested, stop”")).toBeInTheDocument();
    // Reply sentiment is surfaced so you can triage without opening each one.
    expect(screen.getByText("positive")).toBeInTheDocument();
    expect(screen.getByText("negative")).toBeInTheDocument();
  });

  it("only fetches replies on the replies tab", async () => {
    renderDrawer();
    await waitFor(() => expect(screen.getByText("Chidi Okafor")).toBeInTheDocument());

    expect(apiGet).toHaveBeenCalledWith(
      "/campaigns/7/conversations",
      expect.objectContaining({ params: expect.objectContaining({ replied_only: true }) })
    );
    expect(screen.queryByText("Bola Ade")).not.toBeInTheDocument();
  });

  it("the All leads tab includes people who never replied", async () => {
    renderDrawer();
    await waitFor(() => expect(screen.getByText("Chidi Okafor")).toBeInTheDocument());

    fireEvent.click(screen.getByText("All leads (40)"));

    await waitFor(() => expect(screen.getByText("Bola Ade")).toBeInTheDocument());
    expect(apiGet).toHaveBeenCalledWith(
      "/campaigns/7/conversations",
      expect.objectContaining({ params: expect.objectContaining({ replied_only: false }) })
    );
  });

  it("clicking a lead opens THAT person's chat in the inbox", async () => {
    renderDrawer();
    await waitFor(() => expect(screen.getByText("Chidi Okafor")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Chidi Okafor"));

    // contact_id selects the thread, campaign_id keeps the inbox in context.
    expect(navigate).toHaveBeenCalledWith("/inbox?campaign_id=7&contact_id=501");
  });

  it("shows the campaign's headline performance above the list", async () => {
    renderDrawer();
    await waitFor(() => expect(screen.getByText("Chidi Okafor")).toBeInTheDocument());

    expect(screen.getByText("120")).toBeInTheDocument();   // sent
    expect(screen.getByText("40")).toBeInTheDocument();    // leads
    expect(screen.getByText("5%")).toBeInTheDocument();    // reply rate
  });

  it("filters the list as you search", async () => {
    renderDrawer();
    await waitFor(() => expect(screen.getByText("Chidi Okafor")).toBeInTheDocument());

    fireEvent.change(screen.getByPlaceholderText("Search name, number or reply…"), {
      target: { value: "amaka" },
    });

    expect(screen.queryByText("Chidi Okafor")).not.toBeInTheDocument();
    expect(screen.getByText("Amaka Obi")).toBeInTheDocument();
  });

  it("searches reply text, not just names", async () => {
    renderDrawer();
    await waitFor(() => expect(screen.getByText("Chidi Okafor")).toBeInTheDocument());

    fireEvent.change(screen.getByPlaceholderText("Search name, number or reply…"), {
      target: { value: "send details" },
    });

    expect(screen.getByText("Chidi Okafor")).toBeInTheDocument();
    expect(screen.queryByText("Amaka Obi")).not.toBeInTheDocument();
  });

  it("explains itself when a campaign has no replies yet", async () => {
    apiGet.mockImplementation((url: string) => {
      if (url === "/campaigns/7/performance")
        return Promise.resolve({ data: { ...PERFORMANCE, replied: 0 } });
      return Promise.resolve({ data: { total: 0, items: [], page: 1, per_page: 200 } });
    });
    renderDrawer();

    await waitFor(() =>
      expect(screen.getByText("No replies to this campaign yet")).toBeInTheDocument()
    );
  });

  it("closes on Escape", async () => {
    const onClose = vi.fn();
    renderDrawer({ onClose });
    await waitFor(() => expect(screen.getByText("Chidi Okafor")).toBeInTheDocument());

    fireEvent.keyDown(window, { key: "Escape" });

    expect(onClose).toHaveBeenCalled();
  });
});
