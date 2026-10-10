/**
 * UI simulation: campaign lifecycle buttons (P0-1).
 *
 * Validate is a report and must never move a campaign; a scheduled campaign
 * must offer a real way out (pause, cancel schedule, delete); and a paused
 * campaign must say why it is paused.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import CampaignsPage from "./CampaignsPage";

const apiGet = vi.fn();
const apiPost = vi.fn();
const apiDelete = vi.fn();
const toastSuccess = vi.fn();
const toastError = vi.fn();

vi.mock("../api/client", () => ({
  default: {
    get: (...a: any[]) => apiGet(...a),
    post: (...a: any[]) => apiPost(...a),
    delete: (...a: any[]) => apiDelete(...a),
  },
}));

vi.mock("react-hot-toast", () => ({
  default: {
    success: (...a: any[]) => toastSuccess(...a),
    error: (...a: any[]) => toastError(...a),
  },
}));

function campaign(over: Record<string, any>) {
  return {
    id: 1,
    name: "Campaign",
    description: null,
    status: "draft",
    channel: "sms",
    list_id: 1,
    template_id: null,
    message_body: "hi",
    sequence_id: null,
    gateway_setting_id: null,
    total_contacts: 0,
    messages_sent: 0,
    messages_delivered: 0,
    messages_failed: 0,
    replies: 0,
    interested: 0,
    scheduled_start_at: null,
    paused_from: null,
    paused_at: null,
    paused_reason: null,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    ...over,
  };
}

const ITEMS = [
  campaign({ id: 1, name: "Draft one", status: "draft" }),
  campaign({ id: 2, name: "Scheduled one", status: "scheduled", scheduled_start_at: "2030-01-01T09:00:00Z" }),
  campaign({
    id: 3,
    name: "Held one",
    status: "paused",
    paused_from: "scheduled",
    paused_reason: "Paused manually",
    scheduled_start_at: "2030-01-01T09:00:00Z",
  }),
  campaign({
    id: 4,
    name: "Tripped one",
    status: "paused",
    messages_sent: 40,
    paused_reason: "Circuit breaker: bounce rate 5.0% (3 of 60) is above 2.0%",
  }),
];

function rowOf(name: string): HTMLElement {
  return screen.getByText(name).closest("div.card") as HTMLElement;
}

async function renderPage() {
  render(
    <MemoryRouter>
      <CampaignsPage />
    </MemoryRouter>,
  );
  await waitFor(() => expect(screen.getByText("Draft one")).toBeInTheDocument());
}

beforeEach(() => {
  [apiGet, apiPost, apiDelete, toastSuccess, toastError].forEach((m) => m.mockReset());
  apiGet.mockImplementation((url: string) => {
    if (url === "/campaigns/") return Promise.resolve({ data: { total: ITEMS.length, items: ITEMS } });
    if (url === "/overview/campaigns") return Promise.resolve({ data: { items: [] } });
    return Promise.resolve({ data: {} });
  });
  apiPost.mockResolvedValue({ data: { success: true, message: "ok" } });
  apiDelete.mockResolvedValue({ data: {} });
});

describe("CampaignsPage lifecycle", () => {
  it("Validate only reports: it calls /validate and never starts or schedules", async () => {
    apiPost.mockResolvedValueOnce({
      data: { valid: false, errors: ["No contact list selected"], message: "1 problem(s) found." },
    });
    await renderPage();

    fireEvent.click(within(rowOf("Draft one")).getByText("Validate"));

    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/campaigns/1/validate"));
    expect(apiPost).not.toHaveBeenCalledWith("/campaigns/1/start");
    expect(apiPost).not.toHaveBeenCalledWith("/campaigns/1/schedule", expect.anything());
    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith(
        expect.stringContaining("No contact list selected"),
        expect.anything(),
      ),
    );
  });

  it("a valid result is reported as 'nothing was changed'", async () => {
    apiPost.mockResolvedValueOnce({
      data: { valid: true, errors: [], message: "Campaign is valid. Nothing was changed." },
    });
    await renderPage();

    fireEvent.click(within(rowOf("Draft one")).getByText("Validate"));

    await waitFor(() =>
      expect(toastSuccess).toHaveBeenCalledWith(expect.stringContaining("Nothing was changed")),
    );
  });

  it("a draft can be started directly (the server validates inline)", async () => {
    await renderPage();
    fireEvent.click(within(rowOf("Draft one")).getByText("Start now"));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/campaigns/1/start"));
  });

  it("a scheduled campaign can be paused", async () => {
    await renderPage();
    fireEvent.click(within(rowOf("Scheduled one")).getByText("Pause"));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/campaigns/2/pause"));
  });

  it("a scheduled campaign can cancel its schedule, which returns it to draft", async () => {
    await renderPage();
    fireEvent.click(within(rowOf("Scheduled one")).getByText("Cancel schedule"));
    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith("/campaigns/2/schedule", { scheduled_start_at: null }),
    );
  });

  it("a scheduled campaign that has sent nothing can be deleted", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    await renderPage();
    fireEvent.click(within(rowOf("Scheduled one")).getByTitle("Delete campaign"));
    await waitFor(() => expect(apiDelete).toHaveBeenCalledWith("/campaigns/2"));
  });

  it("explains a paused campaign, including an automatic pause", async () => {
    await renderPage();
    expect(within(rowOf("Held one")).getByText(/Scheduled launch on hold/)).toBeInTheDocument();
    expect(within(rowOf("Tripped one")).getByText(/Circuit breaker: bounce rate 5.0%/)).toBeInTheDocument();
    expect(within(rowOf("Tripped one")).getByText("Resume")).toBeInTheDocument();
  });

  it("only offers delete on a paused campaign that was paused before it sent", async () => {
    await renderPage();
    expect(within(rowOf("Held one")).queryByTitle("Delete campaign")).not.toBeNull();
    expect(within(rowOf("Tripped one")).queryByTitle("Delete campaign")).toBeNull();
  });
});
