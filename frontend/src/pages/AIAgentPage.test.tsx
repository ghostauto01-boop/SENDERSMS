import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AIAgentPage from "./AIAgentPage";

const api = vi.hoisted(() => ({
  sessions: vi.fn(), settings: vi.fn(), health: vi.fn(), skills: vi.fn(),
  createSession: vi.fn(), session: vi.fn(), activity: vi.fn(), memory: vi.fn(),
}));
vi.mock("../api/aiAgent", () => ({ agentApi: api, streamChat: vi.fn() }));
vi.mock("../components/agent/AgentModelSettings", () => ({ default: () => null, ProviderStatusPill: () => null }));

beforeEach(() => {
  vi.clearAllMocks();
  api.sessions.mockResolvedValue([]);
  api.settings.mockResolvedValue({ groq_configured: true });
  api.health.mockResolvedValue(null);
  api.skills.mockResolvedValue([]);
  api.createSession.mockResolvedValue({ id: "new", name: "New chat" });
  api.session.mockResolvedValue({ session: {}, messages: [], pending_actions: [], files: [] });
  api.activity.mockResolvedValue([]);
  api.memory.mockResolvedValue("");
});

describe("AI agent startup", () => {
  it("creates and loads a conversation for a new account", async () => {
    render(<MemoryRouter><AIAgentPage /></MemoryRouter>);
    await waitFor(() => expect(api.createSession).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(api.session).toHaveBeenCalledWith("new"));
    expect(await screen.findByLabelText("Message the agent")).toBeEnabled();
  });
  it("does not create a new conversation if the list request fails", async () => {
    api.sessions.mockRejectedValue(new Error("offline"));
    render(<MemoryRouter><AIAgentPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByRole("button", { name: "Record a voice note" })).toBeDisabled());
    expect(api.createSession).not.toHaveBeenCalled();
  });
});
