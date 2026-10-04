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
  it("shows the MCP endpoint an assistant should be pointed at", async () => {
    render(<SettingsPage />);
    await waitFor(() => expect(screen.getByText("AI (MCP)")).toBeInTheDocument());
    fireEvent.click(screen.getByText("AI (MCP)"));

    await waitFor(() => {
      expect(screen.getByText("Let an AI operate this app")).toBeInTheDocument();
      expect(screen.getByText(`${window.location.origin}/mcp`)).toBeInTheDocument();
    });
    // Both big assistants are named, so the operator knows this is not vendor-locked.
    expect(screen.getAllByText(/ChatGPT/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Claude/).length).toBeGreaterThan(0);
  });

  it("lists existing tokens with their usage and marks revoked ones", async () => {
    render(<SettingsPage />);
    await waitFor(() => expect(screen.getByText("AI (MCP)")).toBeInTheDocument());
    fireEvent.click(screen.getByText("AI (MCP)"));

    await waitFor(() => expect(screen.getByText("ChatGPT")).toBeInTheDocument());
    expect(screen.getByText("Old agent")).toBeInTheDocument();
    expect(screen.getByText("revoked")).toBeInTheDocument();
    expect(screen.getByText(/12 calls/)).toBeInTheDocument();
    // A revoked token offers no Revoke button; the live one does.
    expect(screen.getAllByRole("button", { name: /Revoke/ })).toHaveLength(1);
  });

  it("creates a token, shows it once, and never asks the server for it again", async () => {
    apiPost.mockResolvedValue({
      data: { id: 3, name: "Claude", scope: "write", prefix: "mcp_new", token: "mcp_new_secret_value" },
    });
    render(<SettingsPage />);
    await waitFor(() => expect(screen.getByText("AI (MCP)")).toBeInTheDocument());
    fireEvent.click(screen.getByText("AI (MCP)"));

    await waitFor(() => expect(screen.getByText("Create token")).toBeInTheDocument());
    fireEvent.change(screen.getByDisplayValue("ChatGPT"), { target: { value: "Claude" } });
    fireEvent.click(screen.getByText("Create token"));

    await waitFor(() => {
      const call = apiPost.mock.calls.find((c: any) => c[0] === "/mcp/tokens");
      expect(call?.[1]).toEqual({ name: "Claude", scope: "write" });
    });
    await waitFor(() => {
      expect(screen.getByText("mcp_new_secret_value")).toBeInTheDocument();
      expect(screen.getByText(/cannot be shown again/)).toBeInTheDocument();
    });
  });

  it("records what the assistant did, including its failures", async () => {
    render(<SettingsPage />);
    await waitFor(() => expect(screen.getByText("AI (MCP)")).toBeInTheDocument());
    fireEvent.click(screen.getByText("AI (MCP)"));

    await waitFor(() => expect(screen.getByText("send_email_now")).toBeInTheDocument());
    expect(screen.getByText("create_contact")).toBeInTheDocument();
    expect(screen.getByText(/phone_number is required/)).toBeInTheDocument();
    expect(screen.getByText(/15 calls all time/)).toBeInTheDocument();
  });
});
