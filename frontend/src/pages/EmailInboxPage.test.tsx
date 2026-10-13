import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import EmailInboxPage from "./EmailInboxPage";

const { emailApi, apiGet } = vi.hoisted(() => ({
  emailApi: {
    conversations: vi.fn(),
    listSenders: vi.fn(),
    conversation: vi.fn(),
    markRead: vi.fn(),
    messageEvents: vi.fn(),
    reply: vi.fn(),
    setStatus: vi.fn(),
  },
  apiGet: vi.fn(),
}));

vi.mock("../api/email", () => ({ default: emailApi }));
vi.mock("../api/client", () => ({
  default: {
    get: (...args: any[]) => apiGet(...args),
    post: vi.fn(),
  },
}));
vi.mock("../components/RichEmailEditor", () => ({
  default: ({ body, onBody }: any) => (
    <textarea aria-label="Email reply editor" value={body} onChange={(e) => onBody(e.target.value)} />
  ),
}));
vi.mock("../components/MeetingModal", () => ({
  default: ({ initial, onClose }: any) => (
    <div role="dialog" aria-label="Book a meeting form" data-initial={JSON.stringify(initial)}>
      <button type="button" onClick={onClose}>Close meeting form</button>
    </div>
  ),
}));

const conversationList = {
  items: [{
    id: 77,
    contact_id: 42,
    contact_name: "Ada Lovelace",
    contact_email: "ada@acme.example",
    contact_phone: "email:ada@acme.example",
    subject: "Re: Project introduction",
    preview: "Thursday works for me.",
    status: "unread",
    unread_count: 1,
    message_count: 2,
    last_message_at: new Date().toISOString(),
  }],
};

const contactDetail = {
  conversation: {
    id: 77,
    contact_id: 42,
    subject: "Re: Project introduction",
    status: "unread",
    unread_count: 1,
    email_account_name: "Main sender",
  },
  contact: {
    id: 42,
    name: "Ada Lovelace",
    email: "ada@acme.example",
    email_aliases: ["ada.personal@gmail.com"],
    phone_number: "+2348012345678",
    business_name: "Acme Studio",
    city: "Lagos",
    state: "Lagos State",
    country: "Nigeria",
    website: "acme.example",
    industry: "Design",
    source: "Campaign: Spring launch",
    lead_status: "interested",
    notes: "Prefers Thursday meetings; ask for Ada.",
    custom_fields: { preferred_contact: "WhatsApp", team_size: 12 },
    tags: ["VIP", "Design"],
    is_email_opted_out: false,
    is_email_undeliverable: false,
    email_status: "active",
  },
  messages: [],
};

const meeting = {
  id: 9,
  title: "Discovery call",
  starts_at: new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString(),
  ends_at: new Date(Date.now() + 24 * 60 * 60 * 1000 + 30 * 60 * 1000).toISOString(),
  status: "scheduled",
  location: "Online",
};

function renderEmailInbox(url = "/email-inbox") {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <EmailInboxPage />
    </MemoryRouter>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  emailApi.conversations.mockResolvedValue(conversationList);
  emailApi.listSenders.mockResolvedValue({
    items: [
      { id: 1, name: "Main sender", from_email: "main@acme.io" },
      { id: 2, name: "Backup sender", from_email: "backup@acme.io" },
    ],
  });
  emailApi.conversation.mockResolvedValue(contactDetail);
  emailApi.markRead.mockResolvedValue({ success: true });
  emailApi.messageEvents.mockResolvedValue({ events: [] });
  emailApi.reply.mockResolvedValue({ success: true });
  emailApi.setStatus.mockResolvedValue({ success: true });
  apiGet.mockImplementation((url: string) => {
    if (url === "/calendar/upcoming") {
      return Promise.resolve({ data: { items: [meeting] } });
    }
    return Promise.reject(new Error(`Unmocked GET ${url}`));
  });
});

describe("EmailInboxPage — multi-sender inbox filter", () => {
  it("filters threads to one sender's inbox", async () => {
    renderEmailInbox();
    const select = await screen.findByLabelText("Filter inbox by sender");
    fireEvent.change(select, { target: { value: "2" } });
    await waitFor(() =>
      expect(emailApi.conversations).toHaveBeenCalledWith(
        expect.objectContaining({ account_id: 2 })
      )
    );
  });
});

describe("EmailInboxPage contact information", () => {
  it("shows saved contact details, the alternate reply address, WhatsApp info, and upcoming meetings", async () => {
    renderEmailInbox();
    const thread = await screen.findByRole("button", { name: /Ada Lovelace/ });
    fireEvent.click(thread);

    await screen.findByRole("tab", { name: "Contact information" });
    fireEvent.click(screen.getByRole("tab", { name: "Contact information" }));

    expect(await screen.findByText("Acme Studio")).toBeInTheDocument();
    expect(screen.getAllByText("ada@acme.example").length).toBeGreaterThan(0);
    expect(screen.getByText("ada.personal@gmail.com")).toBeInTheDocument();
    expect(screen.getByText("acme.example")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "WhatsApp" })).toBeInTheDocument();
    expect(screen.getByText("Prefers Thursday meetings; ask for Ada.")).toBeInTheDocument();
    expect(screen.getByText("WhatsApp", { selector: "dd" })).toBeInTheDocument();
    expect(screen.getByText("VIP")).toBeInTheDocument();
    expect(await screen.findByText("Discovery call")).toBeInTheDocument();
  });

  it("books against the selected contact without enabling SMS reminders for email-only contacts", async () => {
    emailApi.conversation.mockResolvedValue({
      ...contactDetail,
      contact: { ...contactDetail.contact, phone_number: "email:ada@acme.example" },
    });
    renderEmailInbox();
    fireEvent.click(await screen.findByRole("button", { name: /Ada Lovelace/ }));
    fireEvent.click(await screen.findByTitle("Book a meeting"));

    const dialog = await screen.findByRole("dialog", { name: "Book a meeting form" });
    await waitFor(() => {
      const initial = JSON.parse(dialog.getAttribute("data-initial") || "{}");
      expect(initial.contactIds).toEqual([42]);
      expect(initial.conversationId).toBe(77);
      expect(initial.sendInvite).toBe(false);
      expect(initial.sendReminder).toBe(false);
    });
  });
});

/**
 * THE CONVERSATIONS PAGE — only the people who replied.
 *
 * Same screen, one filter, and the filter lives in the URL so it can be linked
 * to from the sidebar, from a campaign's reply list and from a notification.
 */
describe("EmailInboxPage — Conversations (only people who replied)", () => {
  it("asks for replied threads only, and says so", async () => {
    emailApi.conversations.mockResolvedValue({
      ...conversationList,
      total: 1,
      thread_total: 9,
      replied_total: 1,
    });
    renderEmailInbox("/email-inbox?view=replied");

    await screen.findByRole("button", { name: /Ada Lovelace/ });

    expect(emailApi.conversations).toHaveBeenCalledWith(
      expect.objectContaining({ replied_only: true }),
    );
    expect(screen.getByRole("heading", { name: "Conversations" })).toBeInTheDocument();
    // Both tab labels carry a real number, from the same response.
    expect(screen.getByRole("button", { name: /All mail/ })).toHaveTextContent("9");
    expect(screen.getByRole("button", { name: /Conversations/ })).toHaveTextContent("1");
  });

  it("the default page is every thread, including the ones nobody answered", async () => {
    emailApi.conversations.mockResolvedValue({
      ...conversationList,
      thread_total: 9,
      replied_total: 1,
    });
    renderEmailInbox();

    await screen.findByRole("button", { name: /Ada Lovelace/ });

    expect(emailApi.conversations).toHaveBeenCalledWith(
      expect.objectContaining({ replied_only: undefined }),
    );
    expect(screen.getByRole("heading", { name: "Inbox" })).toBeInTheDocument();
  });

  it("switching to Conversations re-reads the list with the filter on", async () => {
    emailApi.conversations.mockResolvedValue({
      ...conversationList,
      thread_total: 9,
      replied_total: 1,
    });
    renderEmailInbox();
    await screen.findByRole("button", { name: /Ada Lovelace/ });

    fireEvent.click(screen.getByRole("button", { name: /^Conversations/ }));

    await waitFor(() =>
      expect(emailApi.conversations).toHaveBeenLastCalledWith(
        expect.objectContaining({ replied_only: true }),
      ),
    );
  });

  it("a campaign's deep link scopes the list and opens that person's thread", async () => {
    emailApi.conversations.mockResolvedValue({ ...conversationList, thread_total: 1 });
    renderEmailInbox("/email-inbox?campaign_id=5&contact_id=42");

    // The list is filtered by the campaign and the person the link named…
    await waitFor(() =>
      expect(emailApi.conversations).toHaveBeenCalledWith(
        expect.objectContaining({ campaign_id: 5, contact_id: 42 }),
      ),
    );
    // …and their conversation opens itself, which is the whole point of the link.
    await waitFor(() => expect(emailApi.conversation).toHaveBeenCalledWith(77));
    expect(await screen.findByText("Showing one person's thread")).toBeInTheDocument();
  });

  it("offers a way back to every thread", async () => {
    renderEmailInbox("/email-inbox?campaign_id=5");
    await screen.findByRole("button", { name: /Ada Lovelace/ });

    expect(screen.getByText("Showing the threads from campaign #5")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Show every thread/ })).toBeInTheDocument();
  });
});
