import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AIAgentPage from "./AIAgentPage";

const api = vi.hoisted(() => ({
  sessions: vi.fn(), settings: vi.fn(), health: vi.fn(), skills: vi.fn(),
  createSession: vi.fn(), session: vi.fn(), activity: vi.fn(), memory: vi.fn(),
}));
vi.mock("../api/aiAgent", () => ({ agentApi: api, streamChat: vi.fn() }));
vi.mock("../components/agent/AgentModelSettings", async () => {
  const { useEffect } = await import("react");
  const MockAgentModelSettings = ({ onConfigured }: { onConfigured?: (ready: boolean) => void }) => {
    useEffect(() => { onConfigured?.(true); }, [onConfigured]);
    return null;
  };
  return {
    default: MockAgentModelSettings,
    ProviderStatusPill: () => null,
  };
});

beforeEach(() => {
  vi.clearAllMocks();
  api.sessions.mockResolvedValue([]);
  api.settings.mockResolvedValue({ openrouter_configured: true, preset: "" });
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
  it("shows one OpenRouter preset settings button and no model/provider picker", async () => {
    api.sessions.mockResolvedValue([{ id: "session-1", name: "Chat" }]);
    api.settings.mockResolvedValue({
      openrouter_configured: true,
      preset: "support-ops",
    });
    api.session.mockResolvedValue({ session: { id: "session-1" }, messages: [], pending_actions: [], files: [] });

    render(<MemoryRouter><AIAgentPage /></MemoryRouter>);

    const chip = await screen.findByRole("button", { name: "Open OpenRouter preset settings" });
    expect(screen.getByText("OpenRouter · preset support-ops")).toBeInTheDocument();
    expect(screen.queryByText(/anthropic\/claude/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Use Groq" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Use Gemini" })).not.toBeInTheDocument();

    fireEvent.click(chip);
    expect(screen.queryByLabelText("Provider")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Model")).not.toBeInTheDocument();
    await waitFor(() => expect(api.settings).toHaveBeenCalledTimes(2));
  });

  it("does not create a new conversation if the list request fails", async () => {
    api.sessions.mockRejectedValue(new Error("offline"));
    render(<MemoryRouter><AIAgentPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByRole("button", { name: "Record a voice note" })).toBeDisabled());
    expect(api.createSession).not.toHaveBeenCalled();
  });
});
