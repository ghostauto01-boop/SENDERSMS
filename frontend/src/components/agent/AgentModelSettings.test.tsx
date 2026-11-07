import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AgentModelSettings from "./AgentModelSettings";

const api = vi.hoisted(() => ({
  settings: vi.fn(),
  health: vi.fn(),
  models: vi.fn(),
  presets: vi.fn(),
  saveSettings: vi.fn(),
  savePresets: vi.fn(),
  testKey: vi.fn(),
}));

vi.mock("../../api/aiAgent", () => ({ agentApi: api }));
vi.mock("react-hot-toast", () => ({
  default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }),
}));

beforeEach(() => {
  vi.clearAllMocks();
  api.settings.mockResolvedValue({
    openrouter_configured: true,
    openrouter_hint: "sk-or-v***wxyz",
    console_url: "https://openrouter.ai/settings/keys",
    preset_console_url: "https://openrouter.ai/settings/presets",
    preset: "",
    auto_fallback: true,
    auto_approve: false,
    voice: { auto_send: false, language: "", read_aloud: false },
  });
  api.presets.mockResolvedValue({
    available: true,
    items: [
      { name: "sales-voice", label: "Sales voice", description: "Warm and concise" },
      { name: "support-agent", label: "Support agent" },
    ],
    saved: "",
  });
  api.saveSettings.mockResolvedValue({ success: true });
  api.savePresets.mockResolvedValue({ success: true, preset: "sales-voice", warnings: [] });
});

describe("AgentModelSettings preset-only settings", () => {
  it("offers preset selection only and does not load the model catalogue", async () => {
    render(<AgentModelSettings compact />);

    const preset = await screen.findByRole("combobox", { name: "OpenRouter preset" });
    expect(screen.getByText(/there are no separate model or provider choices/i)).toBeInTheDocument();
    expect(screen.queryByLabelText("Provider")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Model")).not.toBeInTheDocument();
    expect(api.models).not.toHaveBeenCalled();
    expect(api.health).not.toHaveBeenCalled();
    expect(api.presets).toHaveBeenCalledTimes(1);

    fireEvent.change(preset, { target: { value: "sales-voice" } });
    fireEvent.click(screen.getByRole("button", { name: "Select preset" }));

    await waitFor(() => expect(api.savePresets).toHaveBeenCalledWith({
      preset: "sales-voice",
      check: true,
    }));
  });

  it("allows a preset slug when OpenRouter does not list presets", async () => {
    api.presets.mockResolvedValue({ available: false, items: [], saved: "" });
    render(<AgentModelSettings compact />);

    fireEvent.change(await screen.findByLabelText("OpenRouter preset slug"), {
      target: { value: "warm-outreach" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Select preset" }));

    await waitFor(() => expect(api.savePresets).toHaveBeenCalledWith({
      preset: "warm-outreach",
      check: true,
    }));
  });

  it("does not attach a preset OpenRouter rejects", async () => {
    api.savePresets.mockResolvedValue({
      success: false,
      preset: "",
      warnings: [],
      error: "OpenRouter does not know a preset called nope.",
    });
    render(<AgentModelSettings compact />);

    fireEvent.change(await screen.findByRole("combobox", { name: "OpenRouter preset" }), {
      target: { value: "support-agent" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Select preset" }));

    await waitFor(() => expect(api.savePresets).toHaveBeenCalled());
    expect(screen.queryByText("Currently selected: support-agent")).not.toBeInTheDocument();
  });

  it("saves an API key without presenting provider or model selectors", async () => {
    render(<AgentModelSettings compact />);

    await screen.findByRole("button", { name: "Save key" });
    fireEvent.change(screen.getAllByLabelText("OpenRouter API key")[1], {
      target: { value: "sk-or-v1-new-test-key" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save key" }));

    await waitFor(() => expect(api.saveSettings).toHaveBeenCalledWith({
      api_key: "sk-or-v1-new-test-key",
    }));
    expect(api.models).not.toHaveBeenCalled();
  });

  it("does not request presets until a key exists", async () => {
    api.settings.mockResolvedValue({
      openrouter_configured: false,
      openrouter_hint: null,
      preset: "",
      auto_fallback: true,
      auto_approve: false,
      voice: { auto_send: false, language: "", read_aloud: false },
    });

    render(<AgentModelSettings compact />);
    await screen.findByText("Not set");

    expect(api.presets).not.toHaveBeenCalled();
    expect(api.models).not.toHaveBeenCalled();
    expect(screen.getByLabelText("OpenRouter preset slug")).toBeDisabled();
  });
});
