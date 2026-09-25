import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, act } from "@testing-library/react";
import DbStatusBanner from "./DbStatusBanner";
import { reportDbOk, reportDbOutage, getDbOutage } from "../utils/dbStatus";

const apiGet = vi.fn();

vi.mock("../api/client", () => ({
  default: {
    get: (...args: any[]) => apiGet(...args),
  },
}));

const quota = {
  kind: "quota_exceeded",
  message:
    "Your account or project has exceeded the compute time quota. Upgrade your plan to increase limits.",
  hint: "The database provider (Neon free plan) has suspended the database because its monthly compute quota is used up.",
};

beforeEach(() => {
  apiGet.mockReset();
  act(() => reportDbOk());
});

describe("DbStatusBanner", () => {
  it("renders nothing while the database is fine", () => {
    render(<DbStatusBanner />);
    expect(screen.queryByTestId("db-status-banner")).toBeNull();
  });

  it("explains a Neon quota suspension in plain words", () => {
    render(<DbStatusBanner />);
    act(() => reportDbOutage(quota));
    const banner = screen.getByTestId("db-status-banner");
    expect(banner).toHaveTextContent("Database paused by Neon: monthly free quota used up");
    expect(banner).toHaveTextContent("exceeded the compute time quota");
    expect(banner).toHaveTextContent("Neon free plan");
    expect(banner).toHaveTextContent("Nothing has been lost");
  });

  it("clears itself when the API answers again", async () => {
    render(<DbStatusBanner />);
    act(() => reportDbOutage(quota));
    expect(screen.getByTestId("db-status-banner")).toBeInTheDocument();
    act(() => reportDbOk());
    await waitFor(() => expect(screen.queryByTestId("db-status-banner")).toBeNull());
    expect(getDbOutage()).toBeNull();
  });

  it("'Check again' probes /health/db and hides the banner on success", async () => {
    apiGet.mockResolvedValue({ data: { ok: true } });
    render(<DbStatusBanner />);
    act(() => reportDbOutage(quota));
    fireEvent.click(screen.getByRole("button", { name: /check again/i }));
    await waitFor(() => expect(apiGet).toHaveBeenCalledWith("/health/db"));
    await waitFor(() => expect(screen.queryByTestId("db-status-banner")).toBeNull());
  });

  it("'Check again' keeps the banner (with fresh detail) while still down", async () => {
    apiGet.mockRejectedValue({
      response: {
        status: 503,
        data: { ok: false, kind: "unreachable", message: "connection refused", hint: "Check the host." },
      },
    });
    render(<DbStatusBanner />);
    act(() => reportDbOutage(quota));
    fireEvent.click(screen.getByRole("button", { name: /check again/i }));
    await waitFor(() =>
      expect(screen.getByTestId("db-status-banner")).toHaveTextContent("Database cannot be reached")
    );
    expect(screen.getByTestId("db-status-banner")).toHaveTextContent("connection refused");
  });
});
