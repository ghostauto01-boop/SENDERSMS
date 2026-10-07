import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import EmailInboxPage from "./EmailInboxPage";

const { emailApi, apiGet } = vi.hoisted(() => ({
  emailApi: {
    conversations: vi.fn(),
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

function renderEmailInbox() {
  return render(
    <MemoryRouter initialEntries={["/email-inbox"]}>
      <EmailInboxPage />
    </MemoryRouter>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  emailApi.conversations.mockResolvedValue(conversationList);
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
