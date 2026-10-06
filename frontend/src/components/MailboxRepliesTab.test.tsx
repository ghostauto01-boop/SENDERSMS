import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import MailboxRepliesTab from "./MailboxRepliesTab";

/**
 * The tab links to the Email Inbox, so it renders inside the router it lives in
 * in the app — a bare `render()` would throw on `useContext(Router)`.
 */
const renderTab = (props: Record<string, unknown> = {}) =>
  render(
    <MemoryRouter>
      <MailboxRepliesTab {...(props as any)} />
    </MemoryRouter>
  );

/**
 * The Replies tab — the screen behind the complaint "the reply went to spam and
 * never showed up in the app".
 *
 * These tests pin the three things that make it useful:
 *
 * 1. It says *why* in plain words (a freemail Reply-To on mail Brevo sent breaks
 *    DMARC, so Gmail files the reply as spoofed) instead of showing DNS records.
 * 2. It shows which of the three doors a reply can come through, and which are
 *    open.
 * 3. Connecting a mailbox is one button, and the "never send it to Spam" control
 *    is honest about needing the Gmail API rather than failing silently.
 */

const apiGet = vi.fn();
const apiPost = vi.fn();
const apiPatch = vi.fn();
const apiDelete = vi.fn();
const openWindow = vi.fn();

vi.mock("../api/client", () => ({
  default: {
    get: (...a: any[]) => apiGet(...a),
    post: (...a: any[]) => apiPost(...a),
    patch: (...a: any[]) => apiPatch(...a),
    delete: (...a: any[]) => apiDelete(...a),
  },
  setSitePasswordRequired: () => {},
}));

const FREEMAIL_REPORT = {
  findings: [
    {
      severity: "high",
      where: "Sender 'Main' — Reply-To",
      value: "me@gmail.com",
      problem:
        "Reply-To is a free webmail address, but the mail is actually sent by Brevo. Gmail cannot " +
        "verify a gmail.com From on mail Brevo signed, so DMARC fails and the whole thread is " +
        "treated as spoofed — which is why the prospect's reply lands in your Spam folder.",
      fix: "Connect that mailbox below so replies are read (and sent) from it directly.",
    },
    {
      severity: "high",
      where: "Reply inbox",
      value: "no mailbox connected",
      problem: "Nothing is reading your mailbox, so a reply can only reach this app through Brevo.",
      fix: "Connect your Gmail below.",
    },
  ],
  healthy: false,
  mailboxes: [],
  connected: 0,
  replies_imported: 0,
  rescued_from_spam: 0,
  last_sync_at: null,
  brevo_inbound_ready: false,
  paths: [
    { id: "brevo", label: "Brevo inbound parsing", status: "not configured", detail: "Needs a domain." },
    { id: "gmail_api", label: "Gmail API (OAuth)", status: "not connected", detail: "Reads INBOX and Spam." },
    { id: "imap", label: "IMAP + app password", status: "not connected", detail: "The five-minute option." },
  ],
};

const MAILBOX = {
  id: 4,
  name: "Replies — me@gmail.com",
  provider: "imap",
  email_address: "me@gmail.com",
  folders: ["INBOX", "[Gmail]/Spam"],
  import_all: false,
  rescue_from_spam: true,
  never_spam_filter: false,
  send_replies: true,
  poll_interval: 120,
  is_active: true,
  last_sync_at: "2026-10-06T09:00:00Z",
  last_sync_status: "ok",
  last_error: null,
  total_synced: 12,
  total_replies: 3,
  total_rescued: 1,
  total_sent: 2,
  filters_installed: 0,
  label_id: null,
  has_credential: true,
  created_at: "2026-10-01T09:00:00Z",
};

beforeEach(() => {
  apiGet.mockReset();
  apiPost.mockReset();
  apiPatch.mockReset();
  apiDelete.mockReset();
  openWindow.mockReset();
  (window as any).open = openWindow;

  apiGet.mockImplementation((url: string) => {
    if (url === "/mailbox/report") return Promise.resolve({ data: FREEMAIL_REPORT });
    if (url === "/mailbox") {
      return Promise.resolve({
        data: { items: [], count: 0, google_oauth_ready: false, poll_interval: 60 },
      });
    }
    return Promise.reject(new Error("unmocked GET " + url));
  });
});

describe("MailboxRepliesTab", () => {
  it("explains why the reply is in Spam, in words an operator can act on", async () => {
    renderTab();

    await waitFor(() =>
      expect(screen.getByText("Why replies go missing")).toBeInTheDocument(),
    );
    // The diagnosis, not a DNS lecture.
    expect(screen.getByText(/DMARC fails/)).toBeInTheDocument();
    expect(screen.getByText(/Spam folder/)).toBeInTheDocument();
    expect(screen.getByText(/Sender 'Main' — Reply-To/)).toBeInTheDocument();
    expect(screen.getByText(/Connect that mailbox below/)).toBeInTheDocument();
    expect(screen.getByText("no mailbox connected")).toBeInTheDocument();
  });

  it("shows all three doors a reply can come through, and which are open", async () => {
    renderTab();

    await waitFor(() =>
      expect(screen.getByText("The three doors a reply can come through")).toBeInTheDocument(),
    );
    expect(screen.getByText("Brevo inbound parsing")).toBeInTheDocument();
    expect(screen.getByText("Gmail API (OAuth)")).toBeInTheDocument();
    expect(screen.getByText("IMAP + app password")).toBeInTheDocument();
    expect(screen.getAllByText("not connected")).toHaveLength(2);
    expect(screen.getByText("not configured")).toBeInTheDocument();
  });

  it("offers both ways to connect, and tells the truth about Google OAuth", async () => {
    renderTab();

    await waitFor(() => expect(screen.getByText("No mailbox connected")).toBeInTheDocument());
    // One in the header, one in the empty state — both do the same thing.
    expect(screen.getAllByText("Connect with Google").length).toBeGreaterThan(0);
    expect(screen.getByText("Use an app password")).toBeInTheDocument();
    // google_oauth_ready is false in the fixture, so the setup is spelled out
    // (in the connect card and again in the "does Brevo read my Gmail?"
    // explainer, hence getAllByText).
    expect(screen.getAllByText(/GOOGLE_CLIENT_ID/).length).toBeGreaterThan(0);

    fireEvent.click(screen.getByText("Use an app password"));
    await waitFor(() =>
      expect(screen.getByText("Your Gmail password will not work here")).toBeInTheDocument(),
    );
    expect(screen.getByText(/myaccount\.google\.com\/apppasswords/)).toBeInTheDocument();
  });

  it("opens the Google consent URL in a new window", async () => {
    apiPost.mockResolvedValue({ data: {} });
    apiGet.mockImplementation((url: string) => {
      if (url === "/mailbox/google/start") {
        return Promise.resolve({
          data: {
            url: "https://accounts.google.com/o/oauth2/v2/auth?client_id=x",
            redirect_uri: "https://app.example.test/api/v1/mailbox/google/callback",
            scope: "https://mail.google.com/",
          },
        });
      }
      if (url === "/mailbox/report") return Promise.resolve({ data: FREEMAIL_REPORT });
      if (url === "/mailbox") {
        return Promise.resolve({
          data: { items: [], count: 0, google_oauth_ready: true, poll_interval: 60 },
        });
      }
      return Promise.reject(new Error("unmocked GET " + url));
    });

    renderTab();
    await waitFor(() =>
      expect(screen.getAllByText("Connect with Google").length).toBeGreaterThan(0),
    );
    fireEvent.click(screen.getAllByText("Connect with Google")[0]);

    await waitFor(() =>
      expect(openWindow).toHaveBeenCalledWith(
        "https://accounts.google.com/o/oauth2/v2/auth?client_id=x",
        "_blank",
        expect.stringContaining("noopener"),
      ),
    );
  });

  it("imports replies on demand and reports exactly what it did", async () => {
    apiGet.mockImplementation((url: string) => {
      if (url === "/mailbox/report") {
        return Promise.resolve({
          data: { ...FREEMAIL_REPORT, connected: 1, replies_imported: 3, rescued_from_spam: 1,
                  findings: [], healthy: true, mailboxes: [MAILBOX] },
        });
      }
      if (url === "/mailbox") {
        return Promise.resolve({
          data: { items: [MAILBOX], count: 1, google_oauth_ready: false, poll_interval: 60 },
        });
      }
      return Promise.reject(new Error("unmocked GET " + url));
    });
    apiPost.mockImplementation((url: string) => {
      if (url === "/mailbox/4/sync") {
        return Promise.resolve({
          data: { mailbox_id: 4, seen: 5, replies: 2, rescued: 1, stored: 2, skipped: 3,
                  errors: [], success: true },
        });
      }
      return Promise.reject(new Error("unexpected POST " + url));
    });

    renderTab();
    await waitFor(() =>
      expect(screen.getAllByText("me@gmail.com").length).toBeGreaterThan(0),
    );
    // The credential never comes back, and the counters do.
    expect(screen.getByText(/3 replies imported/)).toBeInTheDocument();
    expect(screen.getByText(/1 rescued from Spam/)).toBeInTheDocument();

    fireEvent.click(screen.getByText("Import replies now"));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/mailbox/4/sync"));
    await waitFor(() =>
      expect(screen.getByText(/Saw 5 messages · 2 recognised as replies · 3 skipped · 2 stored/))
        .toBeInTheDocument(),
    );
  });

  it("says the never-spam filters need the Gmail API instead of failing quietly", async () => {
    apiGet.mockImplementation((url: string) => {
      if (url === "/mailbox/report") {
        return Promise.resolve({ data: { ...FREEMAIL_REPORT, connected: 1, mailboxes: [MAILBOX] } });
      }
      if (url === "/mailbox") {
        return Promise.resolve({
          data: { items: [MAILBOX], count: 1, google_oauth_ready: false, poll_interval: 60 },
        });
      }
      return Promise.reject(new Error("unmocked GET " + url));
    });

    renderTab();
    await waitFor(() =>
      expect(screen.getAllByText("me@gmail.com").length).toBeGreaterThan(0),
    );

    const filters = screen.getByText("Never send it to Spam (Gmail filters)").closest("button");
    expect(filters?.getAttribute("aria-checked")).toBe("false");
    expect(filters?.hasAttribute("disabled")).toBe(true);
    expect(screen.getByText(/Needs the Google \(OAuth\) connection/)).toBeInTheDocument();
    // Spam rescue, which works over IMAP, stays available.
    expect(screen.getByText("Rescue replies from Spam")).toBeInTheDocument();
  });

  it("shows the banner the Google callback redirected here with", async () => {
    renderTab({ banner: "me@gmail.com is connected. Replies to it now arrive in the Email Inbox." });
    await waitFor(() =>
      expect(
        screen.getByText(/is connected\. Replies to it now arrive in the Email Inbox/),
      ).toBeInTheDocument(),
    );
  });

  it("disconnects only after the operator confirms, and says what survives", async () => {
    const confirm = vi.fn((_message?: string) => true);
    (window as any).confirm = confirm;
    apiDelete.mockResolvedValue({
      data: { success: true, email_address: "me@gmail.com", filters_removed: 0, detail: "" },
    });
    apiGet.mockImplementation((url: string) => {
      if (url === "/mailbox/report") {
        return Promise.resolve({ data: { ...FREEMAIL_REPORT, connected: 1, mailboxes: [MAILBOX] } });
      }
      if (url === "/mailbox") {
        return Promise.resolve({
          data: { items: [MAILBOX], count: 1, google_oauth_ready: false, poll_interval: 60 },
        });
      }
      return Promise.reject(new Error("unmocked GET " + url));
    });

    renderTab();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Disconnect me@gmail.com" })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Disconnect me@gmail.com" }));
    // The confirm text is what stops this being a destructive one-click action.
    await waitFor(() => expect(confirm).toHaveBeenCalled());
    expect(confirm.mock.calls[0][0]).toMatch(/Imported replies stay in the inbox/);
    await waitFor(() => expect(apiDelete).toHaveBeenCalledWith("/mailbox/4"));
  });
});
