import api from "./client";

/**
 * Typed client for the MCP (AI assistant) surface.
 *
 * Three different things live here, and they answer three different questions:
 *
 * * **Connectors** (`/mcp/connectors`) — one per AI client (ChatGPT, Claude,
 *   Arena, generic). Each has its own URL, its own allowed auth modes and its
 *   own tool set, because each client's published requirements differ: ChatGPT
 *   accepts *only* OAuth 2.1, Claude additionally accepts a bearer header and
 *   insists on a real 401 challenge, Arena Agent Mode has no connector UI at all
 *   and drives the endpoint with curl or a stdio bridge.
 * * **Connection test** (`/mcp/connectors/{key}/test`) — replays the exact
 *   handshake that client performs, in-process, and returns one line per step
 *   with a fix. This is what turns "the connector is not working" into a
 *   sentence you can act on.
 * * **Tokens** (`/mcp/tokens`) — static bearer tokens for the clients that
 *   accept a header (Claude Code, Cursor, the Arena bridge).
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

/** One line of a connection test. `fix` is only set on a failing step. */
export type ConnectorTestStep = {
  step: string;
  ok: boolean;
  /** False marks advice: reported with a fix, but it does not fail the handshake. */
  fatal?: boolean;
  status?: number | null;
  detail: string;
  fix?: string;
};

export type ConnectorTestReport = {
  connector: string;
  label: string;
  endpoint: string;
  status: "ok" | "error";
  error?: string | null;
  passed: number;
  total: number;
  /** Steps that are worth fixing but did not break anything (e.g. PUBLIC_BASE_URL unset). */
  advice?: number;
  ran_at: string;
  steps: ConnectorTestStep[];
};

export type Connector = {
  key: string;
  label: string;
  vendor: string;
  blurb: string;
  docs_url?: string | null;
  /** The steps, written for this specific client, in the order it performs them. */
  setup_steps: string[];
  endpoint: string;
  endpoint_ready: boolean;
  auth_modes: string[];
  toolset: "core" | "full";
  tool_count?: number | null;
  protected_resource_metadata: string;
  authorization_server_metadata: string;
  registration_endpoint: string;
  authorize_endpoint: string;
  token_endpoint: string;
  redirect_uris: string[];
  allow_loopback_redirects: boolean;
  client_id_metadata_documents: boolean;
  protocol_versions: string[];
  // live state
  oauth_enabled: boolean;
  default_scope: "read" | "write";
  toolset_active: "core" | "full";
  status: "unknown" | "ok" | "error";
  last_test_at?: string | null;
  last_test_report: ConnectorTestStep[];
  last_error?: string | null;
  authorize_count: number;
  token_count: number;
  call_count: number;
  last_call_at?: string | null;
};

export type ConnectorBridge = {
  available: boolean;
  download_url: string;
  script: string;
  endpoint: string;
  run: string;
  config: Record<string, unknown>;
};

export type ConnectorList = {
  items: Connector[];
  public_base_url?: string | null;
  /** env | render | request | none — where the base URL came from. */
  base_url_source?: string;
  ready: boolean;
  problem?: string | null;
  /** Set when the base URL was detected from this browser session. */
  note?: string | null;
  require_login: boolean;
  protocol_versions: string[];
  bridge?: ConnectorBridge;
};

export type McpClientRow = {
  id: number;
  client_id: string;
  client_name?: string | null;
  connector?: string | null;
  source?: string | null;
  redirect_uris: string[];
  auth_method?: string | null;
  scope?: string | null;
  is_active: boolean;
  active_grants: number;
  created_at?: string | null;
  last_used_at?: string | null;
};

export const mcpApi = {
  /** The legacy single endpoint. Prefer `connector.endpoint` per client. */
  endpoint: () => `${window.location.origin}/mcp`,

  listConnectors: () => api.get<ConnectorList>("/mcp/connectors").then((r) => r.data),

  updateConnector: (
    key: string,
    patch: { oauth_enabled?: boolean; default_scope?: "read" | "write"; toolset?: "core" | "full" },
  ) => api.post<Connector>(`/mcp/connectors/${key}`, patch).then((r) => r.data),

  testConnector: (key: string) =>
    api.post<ConnectorTestReport>(`/mcp/connectors/${key}/test`).then((r) => r.data),

  revokeConnector: (key: string) =>
    api.post<{ revoked: number }>(`/mcp/connectors/${key}/revoke`).then((r) => r.data),

  listClients: () => api.get<{ items: McpClientRow[] }>("/mcp/clients").then((r) => r.data),

  removeClient: (id: number) => api.delete(`/mcp/clients/${id}`),

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
