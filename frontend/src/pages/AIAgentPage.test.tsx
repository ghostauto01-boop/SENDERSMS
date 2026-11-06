import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AIAgentPage from "./AIAgentPage";

const api = vi.hoisted(() => ({
  sessions: vi.fn(), settings: vi.fn(), health: vi.fn(), skills: vi.fn(),
  createSession: vi.fn(), session: vi.fn(), activity: vi.fn(), memory: vi.fn(), updateSessionProvider: vi.fn(),
}));
vi.mock("../api/aiAgent", () => ({ agentApi: api, streamChat: vi.fn() }));
vi.mock("../components/agent/AgentModelSettings", () => ({ default: () => null, ProviderStatusPill: () => null }));

beforeEach(() => {
  vi.clearAllMocks();
  api.sessions.mockResolvedValue([]);
  api.settings.mockResolvedValue({ openrouter_configured: true, models: { openrouter: "openai/gpt-oss-20b" } });
  api.health.mockResolvedValue(null);
  api.skills.mockResolvedValue([]);
  api.createSession.mockResolvedValue({ id: "new", name: "New chat" });
  api.session.mockResolvedValue({ session: {}, messages: [], pending_actions: [], files: [] });
  api.activity.mockResolvedValue([]);
  api.memory.mockResolvedValue("");
  api.updateSessionProvider.mockResolvedValue({ success: true });
});

describe("AI agent startup", () => {
  it("creates and loads a conversation for a new account", async () => {
    render(<MemoryRouter><AIAgentPage /></MemoryRouter>);
    await waitFor(() => expect(api.createSession).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(api.session).toHaveBeenCalledWith("new"));
    expect(await screen.findByLabelText("Message the agent")).toBeEnabled();
  });
  it("shows one OpenRouter model chip instead of a two-provider toggle", async () => {
    api.sessions.mockResolvedValue([{ id: "session-1", name: "Chat", model: "openrouter" }]);
    api.settings.mockResolvedValue({
      openrouter_configured: true,
      default_provider: "openrouter",
      models: { openrouter: "anthropic/claude-sonnet-4.5" },
    });
    api.session.mockResolvedValue({ session: { id: "session-1", model: "openrouter" }, messages: [], pending_actions: [], files: [] });

    render(<MemoryRouter><AIAgentPage /></MemoryRouter>);

    const chip = await screen.findByRole("button", { name: "Change the AI model" });
    // The chosen model is visible in the header, and there is no second provider
    // to switch to any more.
    expect(await screen.findByText(/anthropic\/claude-sonnet-4\.5/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Use Groq" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Use Gemini" })).not.toBeInTheDocument();

    // Pressing it opens the settings panel where the live catalogue is picked.
    fireEvent.click(chip);
    await waitFor(() => expect(api.updateSessionProvider).not.toHaveBeenCalled());
  });

  it("does not create a new conversation if the list request fails", async () => {
    api.sessions.mockRejectedValue(new Error("offline"));
    render(<MemoryRouter><AIAgentPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByRole("button", { name: "Record a voice note" })).toBeDisabled());
    expect(api.createSession).not.toHaveBeenCalled();
  });
});
