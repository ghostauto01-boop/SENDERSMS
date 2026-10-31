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
  default: ({ body, onBody, bodyLabel }: any) => (
    <textarea aria-label={bodyLabel || "Email reply editor"} value={body} onChange={(e) => onBody(e.target.value)} />
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

const message = (id: number, direction: "incoming" | "outgoing", body: string) => ({
  id,
  direction,
  channel: "email",
  body,
  subject: "Re: Project introduction",
  from_address: direction === "incoming" ? "ada@acme.example" : "main@acme.io",
  to_address: direction === "incoming" ? "main@acme.io" : "ada@acme.example",
  status: direction === "incoming" ? "received" : "delivered",
  provider: direction === "incoming" ? "gmail" : "brevo",
  open_count: 0,
  click_count: 0,
  bounced_hard: false,
  is_auto_reply: false,
  retry_count: 0,
  created_at: "2026-10-30T10:00:00Z",
});

const threadDetail = {
  ...contactDetail,
  messages: [
    message(1, "outgoing", "Original campaign email"),
    message(2, "incoming", "An earlier answer from Ada"),
    message(3, "incoming", "Thursday works for me. Please send the full proposal."),
  ],
};

async function openThread(url = "/email-inbox") {
  renderEmailInbox(url);
  fireEvent.click(await screen.findByRole("button", { name: /Ada Lovelace/ }));
  return screen.findByRole("button", { name: "Reply" });
}

describe("EmailInboxPage — reading first, reply on its own page", () => {
  beforeEach(() => {
    emailApi.conversation.mockResolvedValue(threadDetail);
  });

  it("gives the reading pane to the latest email, without an inline editor or forced scrolling", async () => {
    await openThread();
    expect(await screen.findByText(/Thursday works for me. Please send the full proposal/)).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "Reply message" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Send reply" })).not.toBeInTheDocument();
    expect(screen.getByText("An earlier answer from Ada")).not.toBeVisible();
    expect(screen.getByText("Earlier messages (2)")).toBeInTheDocument();
    expect(Element.prototype.scrollIntoView).not.toHaveBeenCalled();
    expect(screen.getByRole("article", { name: "Current email" })).toHaveClass("flex-1");
  });

  it("keeps earlier messages small until explicitly expanded", async () => {
    await openThread();
    fireEvent.click(await screen.findByText("Earlier messages (2)"));
    const older = screen.getByRole("button", { name: "Expand email from Ada Lovelace" });
    expect(older).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(older);
    expect(screen.getByText("An earlier answer from Ada")).toBeInTheDocument();
  });

  it("opens a full-page reply with the actual recipient and a deep link back to this thread", async () => {
    fireEvent.click(await openThread());
    expect(await screen.findByRole("main", { name: "Reply page" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Reply", level: 1 })).toBeInTheDocument();
    expect(screen.getByTitle("ada@acme.example")).toHaveTextContent("ada@acme.example");
    expect(screen.getByLabelText("Reply subject")).toHaveTextContent("Re: Project introduction");
    expect(screen.getByRole("textbox", { name: "Reply message" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Inbox", level: 1 })).not.toBeInTheDocument();
  });

  it("returns to the email without losing an unfinished reply", async () => {
    fireEvent.click(await openThread());
    fireEvent.change(await screen.findByRole("textbox", { name: "Reply message" }), { target: { value: "Here is the proposal." } });
    fireEvent.click(screen.getByRole("button", { name: "Back to email" }));
    expect(await screen.findByText(/Thursday works for me. Please send the full proposal/)).toBeInTheDocument();
    expect(emailApi.reply).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Reply" }));
    expect(await screen.findByRole("textbox", { name: "Reply message" })).toHaveValue("Here is the proposal.");
  });

  it("sends through the existing thread endpoint and returns to the refreshed email", async () => {
    emailApi.reply.mockResolvedValue({ success: true, message: { ...message(4, "outgoing", "Here is the proposal."), status: "sent" } });
    fireEvent.click(await openThread());
    const send = await screen.findByRole("button", { name: "Send reply" });
    expect(send).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox", { name: "Reply message" }), { target: { value: "Here is the proposal." } });
    fireEvent.click(screen.getByRole("button", { name: "Cc" }));
    fireEvent.change(screen.getByLabelText("Cc"), { target: { value: "team@acme.io, boss@acme.io" } });
    fireEvent.click(send);
    await waitFor(() => expect(emailApi.reply).toHaveBeenCalledWith(77, expect.objectContaining({
      body: "Here is the proposal.", cc: ["team@acme.io", "boss@acme.io"],
    })));
    expect(await screen.findByRole("button", { name: "Reply" })).toBeInTheDocument();
    expect(screen.queryByRole("main", { name: "Reply page" })).not.toBeInTheDocument();
    expect(emailApi.conversation).toHaveBeenCalledTimes(2);
  });

  it("keeps the draft and shows the reason when the provider returns success=false", async () => {
    emailApi.reply.mockResolvedValue({ success: false, error: "Connect a sender in Email Manager." });
    fireEvent.click(await openThread());
    fireEvent.change(await screen.findByRole("textbox", { name: "Reply message" }), { target: { value: "Do not lose this" } });
    fireEvent.click(screen.getByRole("button", { name: "Send reply" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Connect a sender in Email Manager.");
    expect(screen.getByRole("textbox", { name: "Reply message" })).toHaveValue("Do not lose this");
    expect(screen.getByRole("main", { name: "Reply page" })).toBeInTheDocument();
  });

  it("keeps the draft after a network failure and never sends it twice on a double click", async () => {
    let rejectSend: (error: Error) => void = () => {};
    emailApi.reply.mockImplementation(() => new Promise((_, reject) => { rejectSend = reject; }));
    fireEvent.click(await openThread());
    fireEvent.change(await screen.findByRole("textbox", { name: "Reply message" }), { target: { value: "Keep this draft" } });
    const send = screen.getByRole("button", { name: "Send reply" });
    fireEvent.click(send);
    fireEvent.click(send);
    expect(emailApi.reply).toHaveBeenCalledTimes(1);
    rejectSend(new Error("Network unavailable"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Network unavailable");
    expect(screen.getByRole("textbox", { name: "Reply message" })).toHaveValue("Keep this draft");
  });

  it("requires confirmation before discarding, and clears only after confirmation", async () => {
    fireEvent.click(await openThread());
    fireEvent.change(await screen.findByRole("textbox", { name: "Reply message" }), { target: { value: "A thoughtful reply" } });
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(screen.getByRole("alertdialog", { name: "Discard this reply?" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Keep writing" }));
    expect(screen.getByRole("textbox", { name: "Reply message" })).toHaveValue("A thoughtful reply");
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    fireEvent.click(screen.getByRole("button", { name: "Discard reply" }));
    await screen.findByRole("button", { name: "Reply" });
    fireEvent.click(screen.getByRole("button", { name: "Reply" }));
    expect(await screen.findByRole("textbox", { name: "Reply message" })).toHaveValue("");
  });

  it("does not allow replies to an unsubscribed contact", async () => {
    emailApi.conversation.mockResolvedValue({ ...threadDetail, contact: { ...contactDetail.contact, is_email_opted_out: true } });
    const replyButton = await openThread();
    expect(replyButton).toBeDisabled();
    expect(screen.getByText(/This contact unsubscribed/)).toBeInTheDocument();
  });

  it("supports a refreshable reply-page URL", async () => {
    renderEmailInbox("/email-inbox?conversation_id=77&reply=1&view=replied");
    expect(await screen.findByRole("main", { name: "Reply page" })).toBeInTheDocument();
    expect(emailApi.conversation).toHaveBeenCalledWith(77);
    fireEvent.click(screen.getByRole("button", { name: "Back to email" }));
    expect(await screen.findByRole("heading", { name: "Conversations", level: 1 })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reply" })).toBeInTheDocument();
  });
});
