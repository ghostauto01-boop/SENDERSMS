import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import SettingsPage from "./SettingsPage";

const apiGet = vi.fn();
const apiPut = vi.fn();
const apiPost = vi.fn();
const apiDelete = vi.fn();

vi.mock("../api/client", () => ({
  default: {
    get: (...args: any[]) => apiGet(...args),
    put: (...args: any[]) => apiPut(...args),
    post: (...args: any[]) => apiPost(...args),
    delete: (...args: any[]) => apiDelete(...args),
  },
  setSitePasswordRequired: () => {},
}));

/**
 * What GET /mcp/connectors returns: one card per AI client, each with its own
 * URL, its own allowed auth modes and the setup steps written for that client.
 */
const CONNECTORS = {
  items: [
    {
      key: "chatgpt", label: "ChatGPT", vendor: "OpenAI",
      blurb: "Hosted connectors sign in with OAuth 2.1 and nothing else.",
      docs_url: "https://developers.openai.com/plugins/build/auth",
      setup_steps: ["ChatGPT → Settings → Connectors → Developer mode.", "Paste the URL."],
      endpoint: "https://app.example.test/connectors/chatgpt/mcp",
      endpoint_ready: true, auth_modes: ["oauth"], toolset: "core", tool_count: 33,
      protected_resource_metadata: "https://app.example.test/.well-known/x",
      authorization_server_metadata: "https://app.example.test/.well-known/y",
      registration_endpoint: "https://app.example.test/connectors/chatgpt/oauth/register",
      authorize_endpoint: "https://app.example.test/connectors/chatgpt/oauth/authorize",
      token_endpoint: "https://app.example.test/connectors/chatgpt/oauth/token",
      redirect_uris: ["https://chatgpt.com/connector_platform_oauth_redirect"],
      allow_loopback_redirects: false, client_id_metadata_documents: true,
      protocol_versions: ["2025-06-18"],
      oauth_enabled: true, default_scope: "write", toolset_active: "core",
      status: "ok", last_test_at: null, last_test_report: [], last_error: null,
      authorize_count: 2, token_count: 1, call_count: 7, last_call_at: null,
    },
    {
      key: "claude", label: "Claude", vendor: "Anthropic",
      blurb: "OAuth with PKCE, or a bearer header in Claude Code.",
      docs_url: null,
      setup_steps: ["Claude → Customize → Connectors."],
      endpoint: "https://app.example.test/connectors/claude/mcp",
      endpoint_ready: true, auth_modes: ["oauth", "bearer"], toolset: "full", tool_count: null,
      protected_resource_metadata: "https://app.example.test/.well-known/x",
      authorization_server_metadata: "https://app.example.test/.well-known/y",
      registration_endpoint: "r", authorize_endpoint: "a", token_endpoint: "t",
      redirect_uris: ["https://claude.ai/api/mcp/auth_callback"],
      allow_loopback_redirects: true, client_id_metadata_documents: false,
      protocol_versions: ["2025-06-18"],
      oauth_enabled: true, default_scope: "write", toolset_active: "full",
      status: "unknown", last_test_at: null, last_test_report: [], last_error: null,
      authorize_count: 0, token_count: 0, call_count: 0, last_call_at: null,
    },
    {
      key: "arena", label: "Arena AI agent", vendor: "Arena.ai",
      blurb: "Agent Mode has no connector screen; it drives the endpoint with curl.",
      docs_url: null,
      setup_steps: ["Read tools/arena-connector/AGENTS.md."],
      endpoint: "https://app.example.test/connectors/arena/mcp",
      endpoint_ready: false, auth_modes: ["bearer", "oauth"], toolset: "full", tool_count: null,
      protected_resource_metadata: "https://app.example.test/.well-known/x",
      authorization_server_metadata: "https://app.example.test/.well-known/y",
      registration_endpoint: "r", authorize_endpoint: "a", token_endpoint: "t",
      redirect_uris: [], allow_loopback_redirects: false,
      client_id_metadata_documents: false, protocol_versions: ["2026-07-28"],
      oauth_enabled: true, default_scope: "write", toolset_active: "full",
      status: "unknown", last_test_at: null, last_test_report: [], last_error: null,
      authorize_count: 0, token_count: 0, call_count: 0, last_call_at: null,
    },
  ],
  public_base_url: "https://app.example.test",
  ready: true,
  problem: null,
  require_login: true,
  protocol_versions: ["2026-07-28", "2025-06-18"],
};

beforeEach(() => {
  apiGet.mockReset();
  apiPut.mockReset();
  apiPost.mockReset();
  apiDelete.mockReset();

  apiGet.mockImplementation((url: string) => {
    if (url === "/settings/notifications") {
      return Promise.resolve({
        data: {
          providers: [{
            id: 1, provider: "pushover", is_enabled: true,
            notify_new_reply: true, notify_campaign_completed: false,
            notify_campaign_failed: true, notify_gateway_offline: true,
            notify_followup_due: true, notify_system_error: true,
          }],
        },
      });
    }
    if (url === "/settings/compliance") return Promise.resolve({ data: {} });
    if (url === "/settings/sending-rules") return Promise.resolve({ data: {} });
    if (url === "/settings/gateway") return Promise.resolve({ data: { configured: false, sim_number: 1 } });
    if (url === "/settings/gateway/webhooks") return Promise.resolve({ data: { configured: false, webhooks: [] } });
    if (url === "/settings/notifications/muted-senders") {
      return Promise.resolve({ data: { senders: ["MTN", "AIRTEL"] } });
    }
    if (url === "/settings/access") {
      return Promise.resolve({ data: { password_required: true, password_set: false } });
    }
    if (url === "/mcp/tokens") {
      return Promise.resolve({
        data: {
          items: [
            {
              id: 1, name: "ChatGPT", prefix: "mcp_abc12345", scope: "write",
              is_active: true, call_count: 12, last_used_at: null, last_error: null,
              created_at: null,
            },
            {
              id: 2, name: "Old agent", prefix: "mcp_zzz99999", scope: "read",
              is_active: false, call_count: 3, last_used_at: null, last_error: null,
              created_at: null,
            },
          ],
        },
      });
    }
    if (url === "/mcp/connectors") {
      return Promise.resolve({ data: CONNECTORS });
    }
    if (url === "/mcp/clients") {
      return Promise.resolve({
        data: {
          items: [
            {
              id: 9, client_id: "https://chatgpt.com/oauth/client.json",
              client_name: "ChatGPT", connector: "chatgpt", source: "cimd",
              redirect_uris: ["https://chatgpt.com/connector_platform_oauth_redirect"],
              auth_method: "none", scope: "read write", is_active: true,
              active_grants: 1, created_at: null, last_used_at: null,
            },
          ],
        },
      });
    }
    if (url === "/mcp/activity") {
      return Promise.resolve({
        data: {
          total: 15,
          items: [
            { id: 1, tool: "send_email_now", token_name: "ChatGPT", request: "POST /api/v1/email/send",
              ok: true, status_code: 200, error: null, arguments: "{}", duration_ms: 240,
              created_at: null },
            { id: 2, tool: "create_contact", token_name: "ChatGPT", request: "POST /api/v1/contacts/",
              ok: false, status_code: 422, error: "HTTP 422: phone_number is required",
              arguments: "{}", duration_ms: 30, created_at: null },
          ],
        },
      });
    }
    return Promise.reject(new Error("unmocked GET " + url));
  });

  apiPut.mockResolvedValue({ data: { success: true } });
});

describe("SettingsPage — site access", () => {
  it("says the login wall is off", async () => {
    render(<SettingsPage />);
    await waitFor(() => expect(screen.getByText("Site access")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Site access"));
    await waitFor(() => {
      expect(screen.getByText(/There is no password wall/)).toBeInTheDocument();
    });
    // The old username/password instruction must be gone.
    expect(
      screen.queryByText(/Everyone signs in with the admin username and password/)
    ).not.toBeInTheDocument();
  });
});

describe("SettingsPage — muted senders", () => {
  it("loads the muted-sender list (MTN, AIRTEL) into the Notifications tab", async () => {
    render(<SettingsPage />);
    await waitFor(() => expect(screen.getByText("Notifications")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Notifications"));

    // Muted senders input shows the carrier list that suppresses alerts
    await waitFor(() => {
      const input = screen.getByDisplayValue("MTN, AIRTEL") as HTMLInputElement;
      expect(input).toBeTruthy();
    });
  });

  it("saves an edited muted-sender list through the API", async () => {
    render(<SettingsPage />);
    await waitFor(() => expect(screen.getByText("Notifications")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Notifications"));

    const input = await screen.findByDisplayValue("MTN, AIRTEL") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "MTN, AIRTEL, GTBANK" } });

    // Two Save buttons exist: Pushover keys (primary) + muted senders (secondary).
    const [, mutedSave] = screen.getAllByRole("button", { name: "Save" });
    fireEvent.click(mutedSave);

    await waitFor(() => {
      const call = apiPut.mock.calls.find((c: any) => c[0] === "/settings/notifications/muted-senders");
      expect(call).toBeTruthy();
      expect((call?.[2] as any)?.params?.senders).toBe("MTN, AIRTEL, GTBANK");
    });
  });
});

describe("SettingsPage — AI (MCP) access", () => {
  /** Open the AI tab and wait for the connector cards. */
  const openTab = async () => {
    render(<SettingsPage />);
    await waitFor(() => expect(screen.getByText("AI (MCP)")).toBeInTheDocument());
    fireEvent.click(screen.getByText("AI (MCP)"));
    await waitFor(() => expect(screen.getByText("Let an AI operate this app")).toBeInTheDocument());
  };

  it("gives each AI client its own connector URL", async () => {
    await openTab();
    // Three separate clients, three separate endpoints — the whole point of the
    // rewrite: one generic URL failed for all of them, silently.
    expect(screen.getByText("https://app.example.test/connectors/chatgpt/mcp")).toBeInTheDocument();
    expect(screen.getByText("https://app.example.test/connectors/claude/mcp")).toBeInTheDocument();
    expect(screen.getByText("https://app.example.test/connectors/arena/mcp")).toBeInTheDocument();
    expect(screen.getByText("Arena AI agent")).toBeInTheDocument();
    expect(screen.getByText("handshake passed")).toBeInTheDocument();
  });

  it("says plainly that ChatGPT cannot take a pasted token", async () => {
    await openTab();
    expect(screen.getByText(/Why there is no token box for ChatGPT/)).toBeInTheDocument();
    expect(screen.getByText(/OAuth 2\.1 with PKCE only/)).toBeInTheDocument();
    // The redirect URI ChatGPT will use is shown, because it is the thing that
    // has to be reachable for the sign-in window to come back. It appears on the
    // connector card and again in the signed-in clients list.
    expect(
      screen.getAllByText(/chatgpt\.com\/connector_platform_oauth_redirect/).length,
    ).toBeGreaterThan(0);
  });

  it("gives Claude Code the exact command, and Arena the repo instructions", async () => {
    await openTab();
    expect(
      screen.getByText(/claude mcp add --transport http sendsms/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Arena Agent Mode has no connector screen/)).toBeInTheDocument();
    expect(screen.getAllByText(/tools\/arena-connector\/AGENTS\.md/).length).toBeGreaterThan(0);
  });

  it("warns when a connector cannot work without PUBLIC_BASE_URL", async () => {
    await openTab();
    // Only the Arena fixture has endpoint_ready:false, so exactly one warning.
    expect(
      screen.getAllByText(/cannot work until PUBLIC_BASE_URL is set/),
    ).toHaveLength(1);
  });

  it("runs the handshake test and shows the fix for a failing step", async () => {
    apiPost.mockImplementation((url: string) => {
      if (url === "/mcp/connectors/claude/test") {
        return Promise.resolve({
          data: {
            connector: "claude", label: "Claude",
            endpoint: "https://app.example.test/connectors/claude/mcp",
            status: "error", error: "401-challenge: no WWW-Authenticate",
            passed: 4, total: 5, ran_at: "2026-01-01T00:00:00Z",
            steps: [
              { step: "public-url", ok: true, status: 200, detail: "PUBLIC_BASE_URL is set", fix: "" },
              {
                step: "401-challenge", ok: false, status: 401,
                detail: "The 401 carried no WWW-Authenticate header",
                fix: "Return WWW-Authenticate: Bearer resource_metadata=\"…\" on the MCP route",
              },
            ],
          },
        });
      }
      return Promise.reject(new Error("unexpected POST " + url));
    });
    await openTab();

    const buttons = screen.getAllByText("Run connection test");
    fireEvent.click(buttons[1]); // the Claude card

    await waitFor(() =>
      expect(screen.getByText("The 401 carried no WWW-Authenticate header")).toBeInTheDocument(),
    );
    expect(screen.getByText(/Return WWW-Authenticate: Bearer resource_metadata/)).toBeInTheDocument();
    expect(screen.getByText("public-url")).toBeInTheDocument();
    expect(apiPost).toHaveBeenCalledWith("/mcp/connectors/claude/test");
  });

  it("lists existing tokens with their usage and marks revoked ones", async () => {
    await openTab();
    await waitFor(() => expect(screen.getByText("Old agent")).toBeInTheDocument());
    expect(screen.getByText("revoked")).toBeInTheDocument();
    expect(screen.getByText(/12 calls/)).toBeInTheDocument();
    // The revoked token offers no Revoke button; the live one does. (The
    // connector cards have their own "Revoke all grants", which is a different
    // action, so match the exact label.)
    expect(screen.getAllByRole("button", { name: "Revoke" })).toHaveLength(1);
    expect(screen.getByRole("button", { name: /Revoke all grants/ })).toBeInTheDocument();
  });

  it("creates a token, shows it once, and never asks the server for it again", async () => {
    apiPost.mockResolvedValue({
      data: { id: 3, name: "Claude Code", scope: "write", prefix: "mcp_new", token: "mcp_new_secret_value" },
    });
    await openTab();

    fireEvent.change(screen.getByDisplayValue("Claude Code"), { target: { value: "Cursor" } });
    fireEvent.click(screen.getByText("Create token"));

    await waitFor(() => {
      const call = apiPost.mock.calls.find((c: any) => c[0] === "/mcp/tokens");
      expect(call?.[1]).toEqual({ name: "Cursor", scope: "write" });
    });
    await waitFor(() => {
      expect(screen.getByText("mcp_new_secret_value")).toBeInTheDocument();
      expect(screen.getByText(/cannot be shown again/)).toBeInTheDocument();
    });
  });

  it("lists the assistants that have signed in over OAuth", async () => {
    await openTab();
    await waitFor(() =>
      expect(screen.getAllByText(/chatgpt\.com\/oauth\/client\.json/).length).toBeGreaterThan(0),
    );
    expect(screen.getByText(/1 live grant · redirects to/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Cut off/ })).toBeInTheDocument();
  });

  it("records what the assistant did, including its failures", async () => {
    await openTab();
    await waitFor(() => expect(screen.getByText("send_email_now")).toBeInTheDocument());
    expect(screen.getByText("create_contact")).toBeInTheDocument();
    expect(screen.getByText(/phone_number is required/)).toBeInTheDocument();
    expect(screen.getByText(/15 calls all time/)).toBeInTheDocument();
  });
});
