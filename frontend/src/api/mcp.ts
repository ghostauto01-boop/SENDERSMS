import api from "./client";

/**
 * Typed client for the MCP (AI assistant) surface.
 *
 * `mcpApi` (token management) goes through the normal logged-in axios instance.
 * The MCP endpoint itself — `${origin}/mcp` — is what an assistant is pointed
 * at, authenticated with one of these tokens.
 */

export type McpToken = {
  id: number;
  name: string;
  prefix: string;
  scope: "read" | "write";
  is_active: boolean;
  call_count: number;
  last_used_at?: string | null;
  last_error?: string | null;
  created_at?: string | null;
};

export type McpCallRow = {
  id: number;
  tool: string;
  token_name?: string | null;
  request?: string | null;
  ok: boolean;
  status_code?: number | null;
  error?: string | null;
  arguments?: string | null;
  duration_ms?: number | null;
  created_at?: string | null;
};

export const mcpApi = {
  endpoint: () => `${window.location.origin}/mcp`,

  listTokens: () => api.get<{ items: McpToken[] }>("/mcp/tokens").then((r) => r.data),

  createToken: (name: string, scope: "read" | "write" = "write") =>
    api
      .post<{ id: number; name: string; scope: string; prefix: string; token: string }>(
        "/mcp/tokens",
        { name, scope },
      )
      .then((r) => r.data),

  revokeToken: (id: number) => api.delete(`/mcp/tokens/${id}`),

  activity: (limit = 50) =>
    api.get<{ total: number; items: McpCallRow[] }>("/mcp/activity", { params: { limit } })
      .then((r) => r.data),
};

export default mcpApi;
