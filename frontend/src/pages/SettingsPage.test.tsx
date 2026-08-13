import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import SettingsPage from "./SettingsPage";

const apiGet = vi.fn();
const apiPut = vi.fn();

vi.mock("../api/client", () => ({
  default: {
    get: (...args: any[]) => apiGet(...args),
    put: (...args: any[]) => apiPut(...args),
    post: () => Promise.resolve({ data: {} }),
  },
}));

beforeEach(() => {
  apiGet.mockReset();
  apiPut.mockReset();

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
    return Promise.reject(new Error("unmocked GET " + url));
  });

  apiPut.mockResolvedValue({ data: { success: true } });
});

describe("SettingsPage — Pushover muted senders", () => {
  it("loads the muted-sender list (MTN, AIRTEL) into the Pushover tab", async () => {
    render(<SettingsPage />);
    await waitFor(() => expect(screen.getByText("Pushover")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Pushover"));

    // Muted senders input shows the carrier list that suppresses alerts
    await waitFor(() => {
      const input = screen.getByDisplayValue("MTN, AIRTEL") as HTMLInputElement;
      expect(input).toBeTruthy();
    });
  });

  it("saves an edited muted-sender list through the API", async () => {
    render(<SettingsPage />);
    await waitFor(() => expect(screen.getByText("Pushover")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Pushover"));

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
