/**
 * UI simulation: Settings -> Sending Rules (P0-3).
 *
 * The protective rules are ON by default and the form must mirror what the
 * server returns -- in particular a deliberately blank sending window must stay
 * blank when the form is saved (it used to snap back to 08:00-20:00).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import SettingsPage from "./SettingsPage";

const apiGet = vi.fn();
const apiPut = vi.fn();

vi.mock("../api/client", () => ({
  default: {
    get: (...a: any[]) => apiGet(...a),
    put: (...a: any[]) => apiPut(...a),
    post: vi.fn(),
    delete: vi.fn(),
  },
  setSitePasswordRequired: () => {},
}));

vi.mock("react-hot-toast", () => ({ default: { success: vi.fn(), error: vi.fn() } }));

const STATUS = {
  allowed_now: true,
  counters: { minute: 0, hour: 0, day: 3 },
  interval_seconds: 30,
  email: {
    mailboxes: 2, per_mailbox: 30, daily_cap: 60, sent_today: 12, in_flight: 4,
    room_today: 44, allowed_now: true,
  },
  breaker: {
    enabled: true,
    tripped: [{ kind: "ads", id: 2, name: "Trybe UGC", reason: "Circuit breaker: bounce rate 5.0%" }],
  },
};

function mockRules(rules: Record<string, any>) {
  apiGet.mockImplementation((url: string) => {
    if (url === "/settings/sending-rules") return Promise.resolve({ data: rules });
    if (url === "/settings/sending-rules/status") return Promise.resolve({ data: STATUS });
    return Promise.resolve({ data: {} });
  });
}

/** The number input under the "Emails per mailbox per day" label. */
function emailCapInput(): HTMLInputElement {
  return screen.getByText("Emails per mailbox per day").parentElement!.querySelector("input")!;
}

async function openRulesTab() {
  render(<SettingsPage />);
  fireEvent.click(await screen.findByText("Sending Rules"));
  await screen.findByText(/Emails per mailbox per day/);
}

beforeEach(() => {
  apiGet.mockReset();
  apiPut.mockReset();
  apiPut.mockResolvedValue({ data: { success: true } });
});

describe("Sending rules tab", () => {
  it("shows the email cap, the breaker and what the throttle is doing", async () => {
    mockRules({
      enable_daily_limit: true, daily_maximum: 1000, sending_start_time: "09:00",
      sending_end_time: "17:00", allow_weekends: false, allow_holidays: true,
      enable_pacing: true, min_delay_seconds: 30,
      email_daily_per_mailbox: 30, breaker_enabled: true, breaker_bounce_pct: 2, breaker_min_sample: 20,
    });
    await openRulesTab();

    expect(emailCapInput().value).toBe("30");
    expect(screen.getByText(/You have 2 mailboxes, so up to 60 a day in total/)).toBeInTheDocument();
    expect(screen.getByText(/Sent today:/)).toBeInTheDocument();
    expect(screen.getByText(/Paused automatically:/)).toBeInTheDocument();
    expect(screen.getByText(/Circuit breaker: bounce rate 5.0%/)).toBeInTheDocument();
  });

  it("saves the email cap and breaker settings with the rest", async () => {
    mockRules({
      enable_daily_limit: true, daily_maximum: 1000, sending_start_time: "09:00",
      sending_end_time: "17:00", allow_weekends: false, enable_pacing: true,
      email_daily_per_mailbox: 30, breaker_enabled: true, breaker_bounce_pct: 2, breaker_min_sample: 20,
    });
    await openRulesTab();

    fireEvent.change(emailCapInput(), { target: { value: "40" } });
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() => expect(apiPut).toHaveBeenCalled());
    const [url, , config] = apiPut.mock.calls[0];
    expect(url).toBe("/settings/sending-rules");
    expect(config.params).toMatchObject({
      email_daily_per_mailbox: 40, breaker_enabled: true, breaker_bounce_pct: 2,
      breaker_min_sample: 20, aw: false, ss: "09:00", se: "17:00",
    });
  });

  it("keeps a deliberately blank sending window blank", async () => {
    mockRules({
      enable_daily_limit: true, daily_maximum: 1000, sending_start_time: "",
      sending_end_time: "", allow_weekends: true, enable_pacing: true,
      email_daily_per_mailbox: 30, breaker_enabled: true,
    });
    await openRulesTab();

    fireEvent.click(screen.getByText("Save"));

    await waitFor(() => expect(apiPut).toHaveBeenCalled());
    const params = apiPut.mock.calls[0][2].params;
    expect(params.ss).toBe("");
    expect(params.se).toBe("");
    expect(params.aw).toBe(true);
  });
});
