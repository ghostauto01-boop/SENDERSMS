import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import InboxPage from "./InboxPage";

/**
 * Campaign attribution in the inbox.
 *
 * The user's ask: "an indicator that shows me which campaign this lead came
 * from", plus the ability to arrive from a campaign already filtered, and to
 * be dropped straight into one person's chat.
 *
 * Kept separate from InboxPage.test.tsx so the original messaging tests stay
 * focused on messaging.
 */

const apiGet = vi.fn();
const apiPost = vi.fn();

vi.mock("../api/client", () => ({
  default: {
    get: (...args: any[]) => apiGet(...args),
    post: (...args: any[]) => apiPost(...args),
  },
}));

const toastFn = vi.fn();
vi.mock("react-hot-toast", () => {
  const t: any = (...a: any[]) => toastFn(...a);
  t.success = vi.fn();
  t.error = vi.fn();
  t.loading = vi.fn();
  t.dismiss = vi.fn();
  return { default: t, toast: t, Toaster: () => null };
});

vi.mock("../hooks/useAuth", () => ({
  useAuth: () => ({
    user: { id: 1, username: "ade", display_name: "Ade", role: "admin", is_active: true },
    logout: vi.fn().mockResolvedValue(undefined),
  }),
}));

const RESTAURANTS = { id: 7, kind: "campaign", name: "Lagos Restaurants", status: "running" };
const RETARGET = { id: 9, kind: "ads", name: "Abuja Retargeting", status: "active" };

const CONVS = [
  {
    id: 1, contact_id: 10, contact_name: "Ada Obi", contact_phone: "+2348012345678",
    status: "interested", unread_count: 0, message_count: 6,
    last_message_preview: "Nice, send the menu",
    last_message_at: new Date().toISOString(),
    contact: { phone_number: "+2348012345678", business_name: null },
    campaign: RESTAURANTS, last_campaign: RESTAURANTS,
  },
  {
    id: 2, contact_id: 11, contact_name: "Tunde Bakare", contact_phone: "+2348098765432",
    status: "unread", unread_count: 2, message_count: 2,
    last_message_preview: "Whats the price?",
    last_message_at: new Date().toISOString(),
    contact: { phone_number: "+2348098765432", business_name: null },
    campaign: RETARGET, last_campaign: RETARGET,
  },
  {
    id: 3, contact_id: 12, contact_name: "Walk In", contact_phone: "+2348055555555",
    status: "open", unread_count: 0, message_count: 1,
    last_message_preview: "Hi",
    last_message_at: new Date().toISOString(),
    contact: { phone_number: "+2348055555555", business_name: null },
    campaign: null, last_campaign: null,
  },
];

const FILTERS = {
  items: [
    { id: 7, kind: "campaign", name: "Lagos Restaurants", status: "running", leads: 88, replies: 41, unread: 3 },
    { id: 9, kind: "ads", name: "Abuja Retargeting", status: "active", leads: 30, replies: 22, unread: 2 },
  ],
  unattributed: { leads: 12, replies: 4, unread: 1 },
  total_conversations: 130,
};

const MESSAGES = [
  { id: 101, direction: "incoming", body: "Whats the price?", created_at: new Date().toISOString(), status: "delivered" },
];

function renderInbox(entry = "/inbox") {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <InboxPage />
    </MemoryRouter>
  );
}

let lastConvParams: any = null;

beforeEach(() => {
  apiGet.mockReset();
  apiPost.mockReset();
  toastFn.mockReset();
  lastConvParams = null;

  apiGet.mockImplementation((url: string, cfg?: any) => {
    if (url === "/inbox/conversations") {
      lastConvParams = cfg?.params || {};
      let items = CONVS;
      // Query params arrive as strings; the backend coerces them. Do the same.
      const cid = cfg?.params?.campaign_id, aid = cfg?.params?.ads_campaign_id;
      if (cid) items = CONVS.filter((c) => c.campaign?.kind === "campaign" && c.campaign?.id === Number(cid));
      if (aid) items = CONVS.filter((c) => c.campaign?.kind === "ads" && c.campaign?.id === Number(aid));
      return Promise.resolve({ data: { items } });
    }
    if (url === "/inbox/campaign-filters") return Promise.resolve({ data: FILTERS });
    if (url.startsWith("/inbox/conversations/")) {
      return Promise.resolve({
        data: { messages: MESSAGES, status: "unread", campaign: RETARGET, last_campaign: RETARGET },
      });
    }
    if (url === "/templates/") return Promise.resolve({ data: { items: [] } });
    return Promise.reject(new Error("unmocked GET " + url));
  });

  apiPost.mockResolvedValue({ data: { success: true } });
});

describe("InboxPage — which campaign did this lead come from?", () => {
  it("tags each conversation with its campaign", async () => {
    renderInbox();
    await waitFor(() => expect(screen.getByText("Ada Obi")).toBeInTheDocument());

    // Both campaign systems are labelled in the same place, same way.
    expect(screen.getAllByText("Lagos Restaurants").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Abuja Retargeting").length).toBeGreaterThan(0);
  });

  it("leaves organic conversations unlabelled rather than guessing", async () => {
    renderInbox();
    await waitFor(() => expect(screen.getByText("Walk In")).toBeInTheDocument());

    const row = screen.getByText("Walk In").closest("button")!;
    expect(within(row).queryByText("Lagos Restaurants")).not.toBeInTheDocument();
    expect(within(row).queryByText("Abuja Retargeting")).not.toBeInTheDocument();
  });

  it("honours a ?campaign_id deep link from the campaign pages", async () => {
    renderInbox("/inbox?campaign_id=7");
    await waitFor(() => expect(screen.getByText("Ada Obi")).toBeInTheDocument());

    expect(Number(lastConvParams.campaign_id)).toBe(7);
    // Filtered out by the campaign scope.
    expect(screen.queryByText("Tunde Bakare")).not.toBeInTheDocument();
  });

  it("honours ?ads_campaign_id for SMS Ads Manager campaigns", async () => {
    renderInbox("/inbox?ads_campaign_id=9");
    await waitFor(() => expect(screen.getByText("Tunde Bakare")).toBeInTheDocument());

    expect(Number(lastConvParams.ads_campaign_id)).toBe(9);
    expect(screen.queryByText("Ada Obi")).not.toBeInTheDocument();
  });

  it("carries ?replied=1 through so 'N replies' lands on replies only", async () => {
    renderInbox("/inbox?campaign_id=7&replied=1");
    await waitFor(() => expect(screen.getByText("Ada Obi")).toBeInTheDocument());

    expect(Number(lastConvParams.campaign_id)).toBe(7);
    expect(lastConvParams.replied_only).toBe(true);
  });

  it("shows an active-filter bar you can clear", async () => {
    renderInbox("/inbox?campaign_id=7");
    await waitFor(() => expect(screen.getByText("Ada Obi")).toBeInTheDocument());

    const clear = await screen.findByTitle("Clear campaign filter");
    fireEvent.click(clear);

    await waitFor(() => expect(screen.getByText("Tunde Bakare")).toBeInTheDocument());
    expect(lastConvParams.campaign_id).toBeUndefined();
  });

  it("lets you pick a campaign from the inbox itself", async () => {
    renderInbox();
    await waitFor(() => expect(screen.getByText("Ada Obi")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Filter by campaign"));

    // The picker shows lead/reply counts so you can choose meaningfully.
    const option = await screen.findByText("88 leads · 41 replied");
    fireEvent.click(option);

    await waitFor(() => expect(Number(lastConvParams.campaign_id)).toBe(7));
  });

  it("opens one specific chat from ?contact_id — the reply drill-down", async () => {
    renderInbox("/inbox?ads_campaign_id=9&contact_id=11");

    // Lands directly in Tunde's thread, not just a filtered list: the thread
    // for conversation 2 (contact 11) is fetched without anyone clicking.
    await waitFor(() => expect(apiGet).toHaveBeenCalledWith("/inbox/conversations/2"));
    // ...and the composer for that chat is on screen.
    expect(await screen.findByPlaceholderText("Type a message")).toBeInTheDocument();
  });

  it("keeps the campaign visible in the chat header once open", async () => {
    renderInbox("/inbox?ads_campaign_id=9&contact_id=11");
    await waitFor(() => expect(apiGet).toHaveBeenCalledWith("/inbox/conversations/2"));
    await screen.findByPlaceholderText("Type a message");

    // Row chip + header chip: you never lose track of where the lead is from.
    await waitFor(() =>
      expect(screen.getAllByText("Abuja Retargeting").length).toBeGreaterThanOrEqual(2)
    );
  });

  it("does not leave a dead link when the deep-linked chat is filtered out", async () => {
    // contact 99 is not in this campaign's list at all.
    renderInbox("/inbox?campaign_id=7&contact_id=99");
    await waitFor(() => expect(screen.getByText("Ada Obi")).toBeInTheDocument());

    // No chat opens, but the user is told why instead of nothing happening.
    await waitFor(() =>
      expect(toastFn).toHaveBeenCalledWith(
        expect.stringMatching(/isn't in the current view/i),
        expect.anything()
      )
    );
    expect(screen.queryByPlaceholderText("Type a message")).not.toBeInTheDocument();
  });

  it("still renders if the campaign filter endpoint is unavailable", async () => {
    apiGet.mockImplementation((url: string, cfg?: any) => {
      if (url === "/inbox/conversations") {
        lastConvParams = cfg?.params || {};
        return Promise.resolve({ data: { items: CONVS } });
      }
      if (url === "/inbox/campaign-filters") return Promise.reject(new Error("boom"));
      if (url.startsWith("/inbox/conversations/")) return Promise.resolve({ data: { messages: MESSAGES } });
      return Promise.reject(new Error("unmocked GET " + url));
    });

    renderInbox();

    // Degrades to a plain inbox instead of crashing.
    await waitFor(() => expect(screen.getByText("Ada Obi")).toBeInTheDocument());
  });
});
