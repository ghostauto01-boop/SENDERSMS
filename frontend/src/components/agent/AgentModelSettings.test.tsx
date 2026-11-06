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
  default: { success: vi.fn(), error: vi.fn(), default: vi.fn() },
}));

const MODELS = [
  {
    id: "openai/gpt-oss-20b",
    label: "OpenAI: GPT-OSS 20B",
    tools: true,
    context: 131072,
    recommended: true,
    vendor: "OpenAI",
    input_modalities: ["text", "image"],
  },
  {
    id: "anthropic/claude-sonnet-4.5",
    label: "Anthropic: Claude Sonnet 4.5",
    tools: true,
    context: 200000,
    recommended: false,
    vendor: "Anthropic",
    input_modalities: ["text"],
  },
];

beforeEach(() => {
  vi.clearAllMocks();
  api.settings.mockResolvedValue({
    openrouter_configured: true,
    openrouter_hint: "sk-or-v***wxyz",
    console_url: "https://openrouter.ai/settings/keys",
    preset_console_url: "https://openrouter.ai/settings/presets",
    vendor: "",
    preset: "",
    models: { openrouter: "openai/gpt-oss-20b" },
    default_provider: "openrouter",
    auto_fallback: true,
    auto_approve: false,
  });
  api.models.mockResolvedValue({
    provider: "openrouter",
    models: MODELS,
    vendors: [
      { vendor: "OpenAI", count: 1 },
      { vendor: "Anthropic", count: 1 },
    ],
    selected: "openai/gpt-oss-20b",
    selected_vendor: "",
    preset: "",
    total: 2,
    error: null,
  });
  api.presets.mockResolvedValue({ available: false, items: [], saved: "" });
  api.saveSettings.mockResolvedValue({ success: true });
  api.savePresets.mockResolvedValue({ success: true, preset: "", vendor: "", warnings: [] });
});

describe("AgentModelSettings compact mode", () => {
  it("keeps the live model selector available without spending another health request", async () => {
    render(<AgentModelSettings compact />);

    // The catalogue is read live, grouped by the lab behind each model.
    const selector = await screen.findByRole("button", { name: /openai\/gpt-oss-20b/ });
    expect(api.models).toHaveBeenCalledWith("openrouter", false);
    expect(api.health).not.toHaveBeenCalled();
    fireEvent.click(selector);

    const model = await screen.findByRole("button", { name: /anthropic\/claude-sonnet-4\.5/ });
    fireEvent.click(model);

    await waitFor(() => expect(api.saveSettings).toHaveBeenCalledWith({
      provider: "openrouter",
      model: "anthropic/claude-sonnet-4.5",
    }));
    expect(api.health).not.toHaveBeenCalled();
  });

  it("groups the catalogue by vendor and filters it", async () => {
    render(<AgentModelSettings compact />);
    await screen.findByRole("button", { name: /openai\/gpt-oss-20b/ });
    fireEvent.click(screen.getByRole("button", { name: /openai\/gpt-oss-20b/ }));

    // The vendor headings come from the live catalogue, not a hard-coded list.
    const headings = await screen.findAllByText(/^(OpenAI|Anthropic)$/);
    expect(headings.map((h) => h.textContent)).toEqual(expect.arrayContaining(["OpenAI", "Anthropic"]));

    fireEvent.change(screen.getByLabelText("Provider"), { target: { value: "Anthropic" } });
    // The picker narrows to one lab. The saved model stays named on the closed
    // selector either way — filtering must not appear to change the model.
    await waitFor(() =>
      expect(screen.getAllByRole("button", { name: /openai\/gpt-oss-20b/ }).length).toBe(1),
    );
    // One in the picker; the closed selector still names the saved model.
    expect(screen.getAllByRole("button", { name: /anthropic\/claude-sonnet-4\.5/ }).length).toBe(1);
  });

  it("attaches an OpenRouter preset by name after OpenRouter accepts it", async () => {
    render(<AgentModelSettings compact />);
    await screen.findByRole("button", { name: /openai\/gpt-oss-20b/ });

    fireEvent.change(screen.getByPlaceholderText("my-sales-preset"), {
      target: { value: "warm-outreach" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save the preset" }));

    await waitFor(() => expect(api.savePresets).toHaveBeenCalledWith({
      preset: "warm-outreach",
      check: true,
    }));
  });

  it("pins routing to one vendor", async () => {
    render(<AgentModelSettings compact />);
    await screen.findByRole("button", { name: /openai\/gpt-oss-20b/ });

    fireEvent.change(screen.getByLabelText(/Pin the lab/), { target: { value: "Anthropic" } });
    await waitFor(() => expect(api.savePresets).toHaveBeenCalledWith({ vendor: "Anthropic" }));
  });

  it("refuses a preset name OpenRouter rejects", async () => {
    api.savePresets.mockResolvedValue({
      success: false,
      preset: "nope",
      vendor: "",
      warnings: [],
      error: "OpenRouter does not know a preset called nope.",
    });

    render(<AgentModelSettings compact />);
    await screen.findByRole("button", { name: /openai\/gpt-oss-20b/ });

    fireEvent.change(screen.getByPlaceholderText("my-sales-preset"), { target: { value: "nope" } });
    fireEvent.click(screen.getByRole("button", { name: "Save the preset" }));

    await waitFor(() => expect(api.savePresets).toHaveBeenCalled());
    // The saved value is never written locally when OpenRouter refused it.
    await waitFor(() => expect(api.presets).toHaveBeenCalledTimes(1));
  });

  it("does not read the catalogue before a key exists", async () => {
    api.settings.mockResolvedValue({
      openrouter_configured: false,
      openrouter_hint: null,
      models: { openrouter: null },
      default_provider: "openrouter",
      auto_fallback: true,
      auto_approve: false,
    });

    render(<AgentModelSettings compact />);
    await screen.findByText("Not set");

    expect(api.models).not.toHaveBeenCalled();
    expect(screen.getByText(/Save a key to see the live catalogue\./)).toBeInTheDocument();
  });
});
