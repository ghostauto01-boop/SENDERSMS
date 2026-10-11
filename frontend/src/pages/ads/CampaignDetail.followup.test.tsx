/**
 * The Automation tab is the UI half of the follow-up-steps fix: the editor must
 * send the wait under the documented `delay_hours` name (the old form posted
 * `wait_hours`, which the API ignored in favour of its own default) and offer
 * the conditions the engine actually implements.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import CampaignDetail from "./CampaignDetail";

vi.mock("react-hot-toast", () => ({
  default: { success: vi.fn(), error: vi.fn() },
  Toaster: () => null,
}));

// vi.mock is hoisted above the imports, so the mock object itself has to be
// created with vi.hoisted (a plain top-level const would be undefined).
const api = vi.hoisted(() => ({
  getCampaign: vi.fn(),
  analytics: vi.fn(),
  followupTemplates: vi.fn(),
  createStep: vi.fn(),
  updateStep: vi.fn(),
  deleteStep: vi.fn(),
  createFollowupTemplate: vi.fn(),
}));

vi.mock("../../api/ads", async () => {
  const actual = await vi.importActual<any>("../../api/ads");
  return { ...actual, default: api, adsApi: api };
});

const detail = {
  id: 7,
  name: "Lagos outreach",
  channel: "sms",
  status: "active",
  objective: "replies",
  subject: null,
  daily_limit: 100,
  total_limit: null,
  drip_mode: "off",
  drip_batch_size: 1,
  drip_interval_minutes: 0,
  pacing: "even",
  continuous: true,
  always_on: false,
  optimization_mode: "manual",
  auto_optimize: false,
  queued_edit_policy: "keep",
  priority: "normal",
  test_mode: false,
  sent_count: 0,
  created_at: "2026-09-01T10:00:00Z",
  updated_at: "2026-09-01T10:00:00Z",
  state: "sending",
  followup_steps: [],
  sets: [],
  creatives: [],
};

function renderDetail() {
  return render(
    <CampaignDetail
      campaignId={7}
      reference={{}}
      onClose={() => {}}
      onChanged={() => {}}
      channel="sms"
    />,
  );
}

describe("campaign follow-up editor", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getCampaign.mockResolvedValue(detail);
    api.analytics.mockResolvedValue({ campaign: { assigned: 0, sent: 0 } });
    api.followupTemplates.mockResolvedValue({ items: [], total: 0 });
    api.createStep.mockResolvedValue({ id: 1 });
    api.createFollowupTemplate.mockResolvedValue({ id: 3 });
  });

  it("posts the wait as delay_hours with the chosen condition", async () => {
    const user = userEvent.setup();
    renderDetail();

    await user.click(await screen.findByRole("button", { name: "Automation" }));
    await user.click(await screen.findByRole("button", { name: /Add step/i }));

    const wait = screen.getByLabelText("Wait (hours after the previous message)");
    await user.clear(wait);
    await user.type(wait, "24");
    // paste (not type): userEvent reads "{{" in type() as an escaped "{"
    const message = screen.getByLabelText("Message");
    await user.click(message);
    await user.paste("Hi {{contact}}, following up");
    await user.selectOptions(screen.getByLabelText("Condition"), "clicked_no_reply");
    await user.click(screen.getByRole("button", { name: "Save step" }));

    await waitFor(() => expect(api.createStep).toHaveBeenCalled());
    const [campaignId, payload] = api.createStep.mock.calls[0];
    expect(campaignId).toBe(7);
    expect(payload.delay_hours).toBe(24);
    expect(payload.wait_hours).toBeUndefined();
    expect(payload.condition).toBe("clicked_no_reply");
    expect(payload.action).toBe("send_sms");
    expect(payload.body).toBe("Hi {{contact}}, following up");
  });

  it("saves the step being written as a reusable template", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "prompt").mockReturnValue("24h nudge");
    renderDetail();

    await user.click(await screen.findByRole("button", { name: "Automation" }));
    await user.click(await screen.findByRole("button", { name: /Add step/i }));
    await user.type(screen.getByLabelText("Message"), "Still keen?");
    await user.click(screen.getByRole("button", { name: "Save as template" }));

    await waitFor(() => expect(api.createFollowupTemplate).toHaveBeenCalled());
    const payload = api.createFollowupTemplate.mock.calls[0][0];
    expect(payload.name).toBe("24h nudge");
    expect(payload.body).toBe("Still keen?");
    expect(payload.delay_hours).toBe(48);
  });
});
