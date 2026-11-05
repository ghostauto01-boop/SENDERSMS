import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AgentModelSettings from "./AgentModelSettings";

const api = vi.hoisted(() => ({
  settings: vi.fn(),
  health: vi.fn(),
  models: vi.fn(),
  saveSettings: vi.fn(),
  testKey: vi.fn(),
}));

vi.mock("../../api/aiAgent", () => ({ agentApi: api }));
vi.mock("react-hot-toast", () => ({
  default: { success: vi.fn(), error: vi.fn() },
}));

beforeEach(() => {
  vi.clearAllMocks();
  api.settings.mockResolvedValue({
    groq_configured: true,
    gemini_configured: false,
    groq_hint: "gsk_***test",
    gemini_hint: null,
    models: { groq: "openai/gpt-oss-20b", gemini: null },
    default_provider: "groq",
    auto_fallback: true,
    auto_approve: false,
  });
  api.models.mockResolvedValue({
    models: [{
      id: "openai/gpt-oss-20b",
      label: "openai/gpt-oss-20b",
      tools: true,
      context: 131072,
      recommended: true,
    }],
  });
  api.saveSettings.mockResolvedValue({ success: true });
});

describe("AgentModelSettings compact mode", () => {
  it("keeps the live model selector available without spending another health request", async () => {
    render(<AgentModelSettings compact />);

    const selector = await screen.findByRole("button", { name: "Choose Groq model" });
    expect(api.health).not.toHaveBeenCalled();
    fireEvent.click(selector);

    const model = await screen.findByRole("button", { name: /openai\/gpt-oss-20b/ });
    fireEvent.click(model);

    await waitFor(() => expect(api.saveSettings).toHaveBeenCalledWith({
      provider: "groq",
      model: "openai/gpt-oss-20b",
    }));
    expect(api.health).not.toHaveBeenCalled();
  });
});
