import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter, Routes, Route, useLocation } from "react-router-dom";
import OpportunityPage from "./OpportunityPage";
import ConversationsPage from "./ConversationsPage";

const apiGet = vi.fn();
const apiPost = vi.fn();

vi.mock("../api/client", () => ({
  default: {
    get: (...args: any[]) => apiGet(...args),
    post: (...args: any[]) => apiPost(...args),
  },
}));

vi.mock("react-hot-toast", () => ({
  default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }),
}));

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="location">{loc.pathname + loc.search}</div>;
}

const OPPS = [
  {
    id: 3, channel: "sms", contact_id: 12, contact_name: "Tunde Bakare", contact_phone: "+2348098765432",
    status: "opportunity", unread_count: 2, last_message_preview: "Send me the price list",
    last_message_at: new Date().toISOString(),
  },
  {
    id: 7, channel: "email", contact_id: 19, contact_name: "Chioma Eze", contact_phone: "email:chioma@eze.test",
    status: "opportunity", unread_count: 0, last_message_preview: "Let's talk next week",
    last_message_at: new Date().toISOString(),
  },
];

beforeEach(() => {
  apiGet.mockReset();
  apiPost.mockReset();
});

describe("OpportunityPage", () => {
  it("lists interested prospects from both channels", async () => {
    apiGet.mockResolvedValue({ data: { total: 2, items: OPPS, counts: {} } });
    render(
      <MemoryRouter initialEntries={["/opportunities"]}>
        <OpportunityPage />
      </MemoryRouter>
    );

    expect(await screen.findByText("Tunde Bakare")).toBeInTheDocument();
    expect(screen.getByText("Chioma Eze")).toBeInTheDocument();
    expect(apiGet).toHaveBeenCalledWith(
      "/inbox/conversations",
      expect.objectContaining({ params: expect.objectContaining({ status: "opportunity", channel: "all" }) })
    );
  });

  it("asks only for one channel when the scope changes", async () => {
    apiGet.mockResolvedValue({ data: { total: 2, items: OPPS, counts: {} } });
    render(
      <MemoryRouter initialEntries={["/opportunities"]}>
        <OpportunityPage />
      </MemoryRouter>
    );
    await screen.findByText("Tunde Bakare");

    fireEvent.click(screen.getByRole("tab", { name: "Email" }));

    await waitFor(() =>
      expect(apiGet).toHaveBeenLastCalledWith(
        "/inbox/conversations",
        expect.objectContaining({ params: expect.objectContaining({ channel: "email" }) })
      )
    );
  });

  it("takes a chat out of Opportunity through the status endpoint", async () => {
    apiGet.mockResolvedValue({ data: { total: 2, items: OPPS, counts: {} } });
    apiPost.mockResolvedValue({ data: { success: true, status: "unread" } });
    render(
      <MemoryRouter initialEntries={["/opportunities"]}>
        <OpportunityPage />
      </MemoryRouter>
    );
    await screen.findByText("Tunde Bakare");

    fireEvent.click(screen.getAllByRole("button", { name: "Remove from Opportunity" })[0]);

    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith("/inbox/conversations/3/status", { status: "active" })
    );
    await waitFor(() => expect(screen.queryByText("Tunde Bakare")).not.toBeInTheDocument());
    expect(screen.getByText("Chioma Eze")).toBeInTheDocument();
  });
});

describe("ConversationsPage", () => {
  const REPLIED = [
    {
      id: 3, channel: "sms", contact_id: 12, contact_name: "Tunde Bakare", contact_phone: "+2348098765432",
      status: "unread", unread_count: 2, last_message_preview: "Please deliver to my office",
      last_message_at: new Date().toISOString(),
    },
  ];

  it("defaults to SMS, lists replied threads, and opens the thread in the SMS inbox", async () => {
    apiGet.mockResolvedValue({ data: { total: 1, items: REPLIED, counts: {} } });
    render(
      <MemoryRouter initialEntries={["/conversations"]}>
        <Routes>
          <Route path="/conversations" element={<ConversationsPage />} />
          <Route path="/inbox" element={<LocationProbe />} />
          <Route path="/email-inbox" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>
    );

    expect(await screen.findByText("Tunde Bakare")).toBeInTheDocument();
    expect(apiGet).toHaveBeenCalledWith(
      "/inbox/conversations",
      expect.objectContaining({ params: expect.objectContaining({ channel: "sms", replied_only: true }) })
    );

    fireEvent.click(screen.getByText("Tunde Bakare"));
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/inbox?contact_id=12"));
  });

  it("switches the list to email when Email is chosen", async () => {
    apiGet.mockResolvedValue({ data: { total: 0, items: [], counts: {} } });
    render(
      <MemoryRouter initialEntries={["/conversations"]}>
        <ConversationsPage />
      </MemoryRouter>
    );
    await waitFor(() => expect(apiGet).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("tab", { name: /Email/ }));

    await waitFor(() =>
      expect(apiGet).toHaveBeenLastCalledWith(
        "/inbox/conversations",
        expect.objectContaining({ params: expect.objectContaining({ channel: "email", replied_only: true }) })
      )
    );
  });
});
