import { beforeEach, describe, expect, it, vi } from "vitest";
import api from "./client";
import { agentApi, type AgentHealth } from "./aiAgent";

vi.mock("./client", () => ({ default: { get: vi.fn() } }));

const get = vi.mocked(api.get);

const health: AgentHealth = {
  providers: [],
  active_provider: null,
  active_model: null,
  state: "down",
  tools: 4,
  tool_areas: [],
  checked_at: "2026-11-04T12:00:00Z",
};

beforeEach(() => {
  get.mockReset();
});

describe("agent health response", () => {
  it("keeps the current list of tool areas", async () => {
    get.mockResolvedValue({ data: { ...health, tool_areas: ["contacts", "campaigns"] } });

    await expect(agentApi.health()).resolves.toMatchObject({
      tool_areas: ["contacts", "campaigns"],
    });
  });

  it("normalises the legacy area-count map instead of crashing the Agent page", async () => {
    get.mockResolvedValue({
      data: { ...health, tool_areas: { contacts: 3, campaigns: 1 } },
    });

    await expect(agentApi.health()).resolves.toMatchObject({
      tool_areas: ["contacts", "campaigns"],
    });
  });
});
