/** Sending restrictions belong to individual campaigns, not global Settings. */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import SettingsPage from "./SettingsPage";

const apiGet = vi.fn();
const apiPut = vi.fn();

vi.mock("../api/client", () => ({
  default: {
    get: (...args: any[]) => apiGet(...args),
    put: (...args: any[]) => apiPut(...args),
    post: vi.fn(),
    delete: vi.fn(),
  },
  setSitePasswordRequired: () => {},
}));

vi.mock("react-hot-toast", () => ({ default: { success: vi.fn(), error: vi.fn() } }));

beforeEach(() => {
  apiGet.mockReset();
  apiPut.mockReset();
  apiGet.mockResolvedValue({ data: { providers: [] } });
});

describe("global sending restrictions", () => {
  it("does not expose sending rules in Settings or load the retired API", async () => {
    render(<SettingsPage />);
    await waitFor(() => expect(screen.getByText("Settings")).toBeInTheDocument());

    expect(screen.queryByText("Sending Rules")).not.toBeInTheDocument();
    expect(apiGet).not.toHaveBeenCalledWith("/settings/sending-rules");
    expect(apiGet).not.toHaveBeenCalledWith("/settings/sending-rules/status");
  });
});
