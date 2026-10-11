import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import GoogleOAuthSetupCard from "./GoogleOAuthSetupCard";

/**
 * GMAIL REPLIES — GOOGLE SIGN-IN.
 *
 * The card exists because "Connect with Google" used to fail with a message
 * telling the operator to go and set two environment variables. These tests pin
 * the three things that make it usable instead:
 *
 * 1. it shows the exact callback URL Google has to be given, before anything
 *    is configured — that value is the one people get wrong;
 * 2. the client ID and secret are saved from here, so no server access is
 *    needed, and the secret is never read back out;
 * 3. connecting is repeatable, because several Gmail inboxes is the point.
 */

const { apiGet, apiPut, apiPost } = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPut: vi.fn(),
  apiPost: vi.fn(),
}));

vi.mock("../api/client", () => ({
  default: {
    get: (...args: any[]) => apiGet(...args),
    put: (...args: any[]) => apiPut(...args),
    post: (...args: any[]) => apiPost(...args),
  },
}));

const NOT_CONFIGURED = {
  client_id: "",
  client_id_masked: "",
  has_secret: false,
  configured: false,
  source: "none",
  redirect_uri: "https://crm.example.test/api/v1/mailbox/google/callback",
  callback_path: "/api/v1/mailbox/google/callback",
  javascript_origin: "https://crm.example.test",
  scope: "https://mail.google.com/",
  public_base_url: "https://crm.example.test",
  public_base_source: "env",
  gmail_mailboxes: [],
  mailbox_count: 0,
};

const CONFIGURED = {
  ...NOT_CONFIGURED,
  client_id: "1234567890-abcdefghijklmnop.apps.googleusercontent.com",
  client_id_masked: "12345678…mnop.apps.googleusercontent.com",
  has_secret: true,
  configured: true,
  source: "db",
  gmail_mailboxes: [
    {
      id: 1,
      name: "Replies — ada@gmail.com",
      provider: "gmail_api",
      email_address: "ada@gmail.com",
      folders: ["INBOX", "[Gmail]/Spam"],
      poll_interval: 60,
      is_active: true,
      total_replies: 12,
      last_sync_at: new Date().toISOString(),
      has_credential: true,
    },
    {
      id: 2,
      name: "Replies — sales@gmail.com",
      provider: "gmail_api",
      email_address: "sales@gmail.com",
      folders: ["INBOX", "[Gmail]/Spam"],
      poll_interval: 120,
      is_active: true,
      total_replies: 3,
      last_sync_at: null,
      last_error: "Google said: invalid_grant",
      has_credential: true,
    },
  ],
  mailbox_count: 2,
};

function answer(config: Record<string, unknown>) {
  apiGet.mockImplementation((url: string) => {
    if (url === "/settings/google-oauth") return Promise.resolve({ data: config });
    return Promise.reject(new Error(`unmocked GET ${url}`));
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  answer(NOT_CONFIGURED);
  apiPut.mockResolvedValue({
    data: { success: true, warnings: [], configured: true, source: "db" },
  });
  apiPost.mockResolvedValue({ data: { seen: 0, stored: 0, rescued: 0, errors: [] } });
});

describe("GoogleOAuthSetupCard — nothing configured yet", () => {
  it("shows the callback URL to paste into the Google console", async () => {
    render(<GoogleOAuthSetupCard />);

    // It appears twice on purpose: once in the copy-this box, once inside the
    // console step that says where to paste it.
    await waitFor(() =>
      expect(
        screen.getAllByText("https://crm.example.test/api/v1/mailbox/google/callback").length,
      ).toBeGreaterThan(0),
    );
    expect(screen.getByText("not set up yet")).toBeInTheDocument();
    expect(screen.getAllByText("https://mail.google.com/").length).toBeGreaterThan(0);
  });

  it("opens the step-by-step and walks through the console in order", async () => {
    render(<GoogleOAuthSetupCard />);

    await screen.findByText("Setting it up in the Google Cloud Console");

    // The steps that actually trip people up are all spelled out.
    expect(screen.getByText("Create (or open) a Google Cloud project")).toBeInTheDocument();
    expect(screen.getByText("Enable the Gmail API")).toBeInTheDocument();
    expect(screen.getByText("Configure the OAuth consent screen")).toBeInTheDocument();
    expect(screen.getByText("Create the OAuth client ID")).toBeInTheDocument();
    expect(screen.getByText(/Web application/)).toBeInTheDocument();
    expect(screen.getByText(/Test users:/)).toBeInTheDocument();
    expect(screen.getByText("Add every other Gmail inbox the same way")).toBeInTheDocument();
    // And the failures it prevents are named, with what to do about them.
    expect(screen.getAllByText(/redirect_uri_mismatch/).length).toBeGreaterThan(0);
  });

  it("will not start a sign-in it cannot finish", async () => {
    render(<GoogleOAuthSetupCard />);

    const connect = await screen.findByRole("button", { name: /Connect a Gmail inbox/ });
    expect(connect).toBeDisabled();
    expect(connect).toHaveAttribute(
      "title",
      "Save your Google OAuth client under Settings → Gmail replies first",
    );
  });

  it("saves the client ID and secret from the app", async () => {
    render(<GoogleOAuthSetupCard />);
    await screen.findByText("Setting it up in the Google Cloud Console");

    fireEvent.change(
      screen.getByPlaceholderText("1234567890-abcdefghijklmnop.apps.googleusercontent.com"),
      { target: { value: "1234567890-abcdefghijklmnop.apps.googleusercontent.com" } },
    );
    fireEvent.change(screen.getByPlaceholderText("GOCSPX-…"), {
      target: { value: "GOCSPX-secret-value" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save OAuth client" }));

    await waitFor(() =>
      expect(apiPut).toHaveBeenCalledWith("/settings/google-oauth", {
        client_id: "1234567890-abcdefghijklmnop.apps.googleusercontent.com",
        client_secret: "GOCSPX-secret-value",
      }),
    );
  });
});

describe("GoogleOAuthSetupCard — configured, with inboxes connected", () => {
  beforeEach(() => answer(CONFIGURED));

  it("lists every Gmail inbox and offers to add another", async () => {
    render(<GoogleOAuthSetupCard />);

    expect(await screen.findByText("ada@gmail.com")).toBeInTheDocument();
    expect(screen.getByText("sales@gmail.com")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Add another/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Connect a Gmail inbox/ })).toBeEnabled();
    // A mailbox that is failing says so, on the card, in its own words.
    expect(screen.getByText(/invalid_grant/)).toBeInTheDocument();
  });

  it("never shows the secret it was given, only that one is saved", async () => {
    render(<GoogleOAuthSetupCard />);

    await screen.findByText("ada@gmail.com");

    const secret = screen.getByPlaceholderText("•••••••••••• saved");
    expect(secret).toHaveAttribute("type", "password");
    expect(secret).toHaveValue("");
    expect(screen.queryByText(/GOCSPX-secret/)).not.toBeInTheDocument();
    // The client ID is public (it is in every consent URL) so it can be shown.
    expect(screen.getByDisplayValue("1234567890-abcdefghijklmnop.apps.googleusercontent.com"))
      .toBeInTheDocument();
  });

  it("asks Google for the account chooser, and to come back here", async () => {
    apiGet.mockImplementation((url: string, cfg?: any) => {
      if (url === "/settings/google-oauth") return Promise.resolve({ data: CONFIGURED });
      if (url === "/mailbox/google/start") {
        return Promise.resolve({
          data: { url: "https://accounts.google.com/o/oauth2/v2/auth?x=1", redirect_uri: "" },
        });
      }
      return Promise.reject(new Error(`unmocked GET ${url}`));
    });
    // The card navigates the top window to Google; jsdom logs that and moves on,
    // so the assertion is about the request, not the navigation.
    render(<GoogleOAuthSetupCard returnTo="/settings?tab=gmail" />);

    fireEvent.click(await screen.findByRole("button", { name: /Add another/ }));

    await waitFor(() =>
      expect(apiGet).toHaveBeenCalledWith(
        "/mailbox/google/start",
        expect.objectContaining({
          params: expect.objectContaining({ return_to: "/settings?tab=gmail" }),
        }),
      ),
    );
  });

  it("imports one inbox's replies on demand", async () => {
    render(<GoogleOAuthSetupCard />);
    await screen.findByText("ada@gmail.com");

    fireEvent.click(screen.getAllByRole("button", { name: /Import now/ })[0]);

    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/mailbox/1/sync"));
  });
});
