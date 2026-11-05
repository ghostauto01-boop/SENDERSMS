import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import CampaignBuilder from "./CampaignBuilder";

const { updateCampaign, toastSuccess } = vi.hoisted(() => ({
  updateCampaign: vi.fn(),
  toastSuccess: vi.fn(),
}));

vi.mock("../../api/ads", () => ({
  default: {
    updateCampaign,
    createCampaign: vi.fn(),
  },
}));

vi.mock("react-hot-toast", () => ({
  default: { success: toastSuccess, error: vi.fn() },
}));

describe("CampaignBuilder dialog", () => {
  it("keeps edit actions visible outside the scroll area and submits their form", async () => {
    updateCampaign.mockResolvedValue({ id: 12 });
    const saved = vi.fn();
    render(
      <CampaignBuilder
        campaign={{ id: 12, name: "Reachable campaign", channel: "sms", objective: "replies" }}
        objectives={[{ value: "replies", label: "Replies" }]}
        close={vi.fn()}
        saved={saved}
      />,
    );

    const dialog = screen.getByRole("dialog", { name: "Edit campaign" });
    const save = screen.getByRole("button", { name: "Save changes" });
    const scrollingFields = dialog.children[1];

    expect(scrollingFields.contains(save)).toBe(false);
    expect(save).toHaveAttribute("form", "campaign-builder-form");
    expect(dialog.querySelector("form#campaign-builder-form")).not.toBeNull();
    expect(dialog.parentElement?.parentElement).toBe(document.body);

    fireEvent.click(save);
    await waitFor(() => expect(updateCampaign).toHaveBeenCalledWith(
      12,
      expect.objectContaining({ name: "Reachable campaign", channel: "sms" }),
    ));
    expect(saved).toHaveBeenCalledWith(12);
  });
});
