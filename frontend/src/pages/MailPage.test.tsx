import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import MailPage from "./MailPage";

const api = vi.hoisted(() => ({
  overview: vi.fn(),
  messages: vi.fn(),
  message: vi.fn(),
  setFlags: vi.fn(),
  move: vi.fn(),
  remove: vi.fn(),
  reply: vi.fn(),
  refresh: vi.fn(),
}));

vi.mock("../api/mail", async () => {
  const actual = await vi.importActual<typeof import("../api/mail")>("../api/mail");
  return { ...actual, mailApi: api, default: api };
});

const ACCOUNT = {
  id: 7,
  name: "Sales",
  provider: "gmail_api" as const,
  email_address: "sales@example.com",
  is_active: true,
  last_sync_at: null,
  last_sync_status: null,
  last_error: null,
  counts: {
    ALL: { total: 12, unread: 3 },
    INBOX: { total: 8, unread: 3 },
    SENT: { total: 4, unread: 0 },
    SPAM: { total: 0, unread: 0 },
    DRAFTS: { total: 0, unread: 0 },
    TRASH: { total: 0, unread: 0 },
  },
  needs_reconnect: false,
};

const OVERVIEW = {
  accounts: [ACCOUNT],
  all: { ALL: { total: 12, unread: 3 }, INBOX: { total: 8, unread: 3 } },
  total_unread: 3,
  folder_keys: ["INBOX", "SENT", "SPAM", "DRAFTS", "TRASH", "ALL", "STARRED", "UNREAD"],
};

const INBOX_MAIL = {
  id: 101,
  mailbox_id: 7,
  mailbox_email: "sales@example.com",
  mailbox_name: "Sales",
  provider_message_id: "18f2c",
  thread_id: "thread-1",
  folder: "INBOX" as const,
  labels: ["INBOX"],
  subject: "Re: Your proposal",
  from_name: "Ada Lovelace",
  from_address: "ada@acme.test",
  to: ["sales@example.com"],
  cc: [],
  date: new Date().toISOString(),
  snippet: "Thanks — can you send the pricing over?",
  is_read: false,
  is_starred: false,
  attachments: 1,
  conversation_id: 55,
  contact_id: 900,
  is_truncated: false,
};

function renderPage(route = "/mail") {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <MailPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  api.overview.mockResolvedValue(OVERVIEW);
  api.messages.mockResolvedValue({
    items: [INBOX_MAIL],
    total: 1,
    limit: 50,
    offset: 0,
    folder: "INBOX",
  });
  api.message.mockResolvedValue({
    ...INBOX_MAIL,
    body_text: "Thanks — can you send the pricing over?",
    body_html: null,
    thread: [INBOX_MAIL],
  });
  api.setFlags.mockResolvedValue({ success: true });
  api.move.mockResolvedValue({ success: true });
  api.remove.mockResolvedValue({ success: true });
  api.reply.mockResolvedValue({ success: true });
  api.refresh.mockResolvedValue({
    success: true,
    accounts: [{ mailbox_id: 7, email: "sales@example.com", replies: 2, rescued: 0, mirrored: 5, errors: [] }],
    replies: 2,
    mirrored: 5,
    overview: OVERVIEW,
  });
});

describe("Mail page", () => {
  it("lists the account's replies and opens one in the reading pane", async () => {
    renderPage();

    expect(await screen.findByText("Re: Your proposal")).toBeInTheDocument();
    expect(screen.getByText("Ada Lovelace")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Re: Your proposal"));

    // The body is only fetched for the message being read.
    await waitFor(() => expect(api.message).toHaveBeenCalledWith(7, "18f2c"));
    // Scoped to the reading pane: the list row carries the same words as a
    // snippet, so an unscoped match would pass even with no body rendered.
    const pane = await screen.findByText("received on sales@example.com");
    await waitFor(() =>
      expect(
        within(pane.closest("div[class*='flex-[1.4]']") as HTMLElement).getByText(
          /can you send the pricing over/,
        ),
      ).toBeInTheDocument(),
    );
  });

  it("names the receiving account on the reply box, never a Brevo sender", async () => {
    renderPage();
    fireEvent.click(await screen.findByText("Re: Your proposal"));

    // The reply is sent *as* the mailbox that received the mail.
    expect(await screen.findByText("sales@example.com", { selector: "strong" })).toBeInTheDocument();
    expect(screen.getByText(/Replying as/)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Reply body"), { target: { value: "Pricing attached." } });
    fireEvent.click(screen.getByRole("button", { name: /Send reply/ }));

    await waitFor(() =>
      expect(api.reply).toHaveBeenCalledWith(7, "18f2c", { body: "Pricing attached.", to_all: false }),
    );
  });

  it("reads the folder of the account chosen in the rail", async () => {
    renderPage("/mail?mailbox=7&folder=SENT");

    await waitFor(() =>
      expect(api.messages).toHaveBeenCalledWith(
        expect.objectContaining({ folder: "SENT", mailbox_id: 7 }),
      ),
    );
    expect(screen.getByText("Sent")).toBeInTheDocument();
  });

  it("merges every account when no mailbox is chosen", async () => {
    renderPage("/mail?mailbox=all&folder=ALL");

    // Omitting mailbox_id is what makes the list a merged one.
    await waitFor(() =>
      expect(api.messages).toHaveBeenCalledWith(expect.not.objectContaining({ mailbox_id: expect.anything() })),
    );
    // And in that view every row carries its own account, so a merged list
    // still tells you where a mail landed.
    const row = (await screen.findByText("Re: Your proposal")).closest("div");
    expect(within(row as HTMLElement).getByText("sales@example.com")).toBeInTheDocument();
  });

  it("stars a message in Gmail and rolls back when Gmail refuses", async () => {
    api.setFlags.mockRejectedValueOnce({ response: { data: { detail: "Gmail refused." } } });
    renderPage();

    const star = await screen.findByRole("button", { name: "Star" });
    fireEvent.click(star);

    await waitFor(() => expect(api.setFlags).toHaveBeenCalledWith(7, "18f2c", { starred: true }));
    // The optimistic star is undone, so the row never lies about Gmail's state.
    await waitFor(() => expect(screen.getByRole("button", { name: "Star" })).toHaveAttribute("aria-pressed", "false"));
  });

  it("refreshes one account and reuses the counts the refresh returns", async () => {
    renderPage("/mail?mailbox=7&folder=INBOX");
    await screen.findByText("Re: Your proposal");

    fireEvent.click(screen.getByTitle("Pull new mail now"));

    await waitFor(() => expect(api.refresh).toHaveBeenCalledWith({ mailbox_id: 7, full: false }));
    // The response already carries an overview — a second request would be
    // the list asking the server for something it was just handed.
    await waitFor(() => expect(api.overview).toHaveBeenCalledTimes(1));
  });

  it("says when an account needs reconnecting instead of showing an empty inbox", async () => {
    api.overview.mockResolvedValue({
      ...OVERVIEW,
      accounts: [{ ...ACCOUNT, needs_reconnect: true, last_error: "invalid_grant" }],
    });
    renderPage();

    expect(await screen.findByText(/needs reconnecting/)).toBeInTheDocument();
  });

  it("explains what to do when nothing is connected", async () => {
    api.overview.mockResolvedValue({ ...OVERVIEW, accounts: [], total_unread: 0 });
    api.messages.mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0, folder: "INBOX" });
    renderPage();

    expect(await screen.findByText(/No mailbox is connected yet/)).toBeInTheDocument();
  });
});
