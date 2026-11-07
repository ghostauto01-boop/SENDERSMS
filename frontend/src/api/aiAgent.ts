import api from "./client";

/**
 * AI Agent API.
 *
 * One rule shapes this file: the browser never sees an API key. The settings
 * endpoints return masked hints only, and the chat stream carries text, tool
 * steps and approvals — never provider credentials.
 */

/**
 * OpenRouter is the only AI integration. Users choose OpenRouter presets only;
 * each preset owns its model, provider routing, and fallback configuration.
 */
export type Provider = "openrouter";

/** A preset created at openrouter.ai/settings/presets. */
export type PresetOption = {
  /** Preset slug used in OpenRouter's `@preset/<slug>` model reference. */
  name: string;
  label?: string;
  description?: string | null;
  system_prompt?: boolean;
};

export type PresetList = {
  /** False when OpenRouter does not list presets for this key — then the name
   *  is typed by hand and proved by a real request. */
  available: boolean;
  items: PresetOption[];
  saved: string;
  error?: string;
};

export type ProviderState =
  | "ok"
  | "key_missing"
  | "key_invalid"
  | "model_unavailable"
  | "rate_limited"
  | "timeout"
  | "safety_blocked"
  | "provider_down"
  | "bad_request"
  | "upstream_error";

export type ProviderCheck = {
  provider: Provider;
  label: string;
  state: ProviderState;
  ok: boolean;
  message: string;
  hint?: string | null;
  model?: string | null;
  latency_ms?: number | null;
  models_available?: number | null;
};

export type AgentHealth = {
  providers: ProviderCheck[];
  active_provider: Provider | null;
  active_model: string | null;
  state: "ok" | "degraded" | "down";
  tools: number;
  tool_areas: string[];
  checked_at: string;
};

type ToolAreas = string[] | Record<string, number>;
type AgentHealthResponse = Omit<AgentHealth, "tool_areas"> & {
  tool_areas?: ToolAreas | null;
};

/** Older backends returned { area: toolCount }; the UI only needs the names. */
const toolAreaNames = (areas: ToolAreas | null | undefined): string[] =>
  Array.isArray(areas) ? areas : Object.keys(areas || {});

export type AgentSettings = {
  openrouter_configured: boolean;
  openrouter_hint: string | null;
  /** Where the key is managed, and where a preset is created. */
  console_url?: string;
  preset_console_url?: string;
  /** Selected OpenRouter preset slug, or an empty string when unset. */
  preset?: string;
  auto_fallback: boolean;
  auto_approve: boolean;
  voice: { auto_send: boolean; language: string; read_aloud: boolean };
};

export type ConfirmationCard = {
  kind: "confirmation";
  action_id: number;
  tool: string;
  title: string;
  summary: string;
  details: Record<string, any>;
};

export type AgentSession = {
  id: string;
  name: string;
  skills?: string[];
  message_count?: number;
  last_message?: string;
  created_at?: string;
  updated_at?: string;
};

export type AgentFile = {
  id: number;
  filename: string;
  size: number;
  summary?: string | null;
  preview?: {
    columns?: { name: string; type: string; samples?: string[] }[];
    suggested_mapping?: Record<string, string>;
    total_rows_estimate?: number;
    rows?: Record<string, any>[];
    summary?: string;
    kind?: string;
    mime_type?: string;
    entries?: { name: string; kind?: string; summary?: string; total_rows?: number; headers?: string[] }[];
    primary_entry?: string;
  } | null;
  created_at?: string;
};

export type AgentMessage = {
  id?: number;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  created_at?: string;
  tool_calls?: any;
  error?: { code: string; message: string; hint?: string; retryable?: boolean };
};

export type ActionRecord = {
  id: number;
  session_id?: string | null;
  tool: string;
  args?: Record<string, any>;
  summary?: string;
  status: "pending" | "running" | "done" | "failed" | "cancelled";
  error?: string | null;
  duration_ms?: number | null;
  undoable?: boolean;
  undone_at?: string | null;
  created_at?: string;
};

export type Skill = {
  id: number;
  name: string;
  description?: string | null;
  content?: string;
  is_active?: boolean;
};

/** One line of the ndjson chat stream. */
export type StreamEvent =
  | { type: "turn_start"; turn_id: string; model?: string; provider?: Provider | "local" }
  | { type: "status"; phase?: string; text: string; model?: string; step?: number; tool?: string }
  | { type: "step"; step: number; max: number; model?: string; provider?: Provider | "local" }
  | { type: "thinking"; text: string; step?: number }
  | { type: "text"; text: string; step?: number }
  | { type: "notice"; kind: "recovery" | "fallback" | "info"; text: string; provider?: Provider; from_model?: string; model?: string }
  | { type: "tool_start"; tool: string; label: string; args?: Record<string, any> }
  | { type: "tool_end"; tool: string; status: string; label: string; duration_ms?: number; data?: any }
  | (ConfirmationCard & { type: "confirmation" })
  | { type: "error"; error: { code: ProviderState; message: string; hint?: string; retryable?: boolean; provider?: string } }
  | { type: "done"; awaiting_approval?: ConfirmationCard[] | number[]; elapsed_ms?: number; provider?: Provider | "local"; model?: string };

export const agentApi = {
  // -- status ---------------------------------------------------------------
  health: () =>
    api.get<AgentHealthResponse>("/ai-agent/health").then((r) => ({
      ...r.data,
      // Normalising here protects every consumer, including while an older
      // backend is still running during a rolling deployment.
      tool_areas: toolAreaNames(r.data.tool_areas),
    })),
  /** The operator's OpenRouter presets, and the one currently attached. */
  presets: () => api.get<PresetList>("/ai-agent/presets").then((r) => r.data),
  /** Select or clear an OpenRouter preset. Presets own model/provider routing. */
  savePresets: (body: { preset?: string; check?: boolean }) =>
    api
      .put<{
        success: boolean;
        preset: string;
        warnings: string[];
        error?: string;
      }>("/ai-agent/presets", body)
      .then((r) => r.data),
  settings: () => api.get<AgentSettings>("/ai-agent/settings").then((r) => r.data),
  saveSettings: (body: Partial<{
    api_key: string;
    clear_key: boolean;
    auto_fallback: boolean;
    auto_approve: boolean;
  }>) => api.put("/ai-agent/settings", body).then((r) => r.data),
  testKey: (body: { api_key?: string } = {}) =>
    api.post("/ai-agent/settings/test", body).then((r) => r.data),
  saveVoice: (body: { auto_send?: boolean; language?: string; read_aloud?: boolean }) =>
    api.put("/ai-agent/settings/voice", body).then((r) => r.data),

  // -- sessions -------------------------------------------------------------
  sessions: () => api.get<{ sessions: AgentSession[] }>("/ai-agent/sessions").then((r) => r.data.sessions),
  createSession: (body: { name?: string } = {}) =>
    api.post<{ session: AgentSession }>("/ai-agent/sessions", body).then((r) => r.data.session),
  session: (id: string) =>
    api
      .get<{
        session: AgentSession & { memory?: string; system_prompt?: string | null };
        messages: AgentMessage[];
        files: AgentFile[];
        pending_actions: ConfirmationCard[];
      }>(`/ai-agent/sessions/${id}`)
      .then((r) => r.data),
  renameSession: (id: string, name: string) =>
    api.patch(`/ai-agent/sessions/${id}`, { name }).then((r) => r.data),
  deleteSession: (id: string) => api.delete(`/ai-agent/sessions/${id}`).then((r) => r.data),

  // -- chat -----------------------------------------------------------------
  chatJson: (id: string, message: string, file_ids: number[] = []) =>
    api
      .post<{ response: string; messages: AgentMessage[]; events: StreamEvent[]; error: any }>(
        `/ai-agent/sessions/${id}/chat`,
        { message, file_ids, stream: false },
        { timeout: 180_000 },
      )
      .then((r) => r.data),

  streamChat: streamChat,

  approve: (actionId: number) => api.post(`/ai-agent/actions/${actionId}/approve`).then((r) => r.data),
  cancel: (actionId: number) => api.post(`/ai-agent/actions/${actionId}/cancel`).then((r) => r.data),

  // -- activity -------------------------------------------------------------
  activity: (sessionId?: string) =>
    api
      .get<{ actions: ActionRecord[] }>(`/ai-agent/activity${sessionId ? `?session_id=${sessionId}` : ""}`)
      .then((r) => r.data.actions),
  undo: (actionId: number) => api.post(`/ai-agent/activity/${actionId}/undo`).then((r) => r.data),

  // -- files ----------------------------------------------------------------
  upload: (sessionId: string, file: File, onProgress?: (percent: number) => void) => {
    const form = new FormData();
    form.append("file", file);
    return api
      .post<{ file: AgentFile }>(`/ai-agent/sessions/${sessionId}/files`, form, {
        headers: { "Content-Type": "multipart/form-data" },
        timeout: 120_000,
        onUploadProgress: (event) => {
          if (onProgress && event.total) onProgress(Math.round((event.loaded / event.total) * 100));
        },
      })
      .then((r) => r.data.file);
  },
  fileRows: (fileId: number, offset = 0, limit = 50) =>
    api.get(`/ai-agent/files/${fileId}?offset=${offset}&limit=${limit}`).then((r) => r.data),
  deleteFile: (fileId: number) => api.delete(`/ai-agent/files/${fileId}`).then((r) => r.data),

  // -- voice ----------------------------------------------------------------
  transcribe: (blob: Blob, filename = "voice.webm") => {
    const form = new FormData();
    form.append("audio", blob, filename);
    return api
      .post<{ text: string; provider: string; model?: string }>("/ai-agent/transcribe", form, {
        headers: { "Content-Type": "multipart/form-data" },
        timeout: 120_000,
      })
      .then((r) => r.data);
  },

  // -- memory & skills ------------------------------------------------------
  memory: (scope: "global" | string) =>
    api.get<{ text: string }>(`/ai-agent/memory/${scope}`).then((r) => r.data.text),
  saveMemory: (scope: "global" | string, text: string) =>
    api.put(`/ai-agent/memory/${scope}`, { text }).then((r) => r.data),
  clearMemory: (scope: "global" | string) => api.delete(`/ai-agent/memory/${scope}`).then((r) => r.data),
  skills: () => api.get<{ skills: Skill[] }>("/ai-agent/skills").then((r) => r.data.skills),
  createSkill: (body: { name: string; content: string; description?: string }) =>
    api.post("/ai-agent/skills/json", body).then((r) => r.data),
  skill: (id: number) => api.get(`/ai-agent/skills/${id}`).then((r) => r.data),
  deleteSkill: (id: number) => api.delete(`/ai-agent/skills/${id}`).then((r) => r.data),
  tools: () =>
    api
      .get<{ tools: { name: string; area: string; description: string; danger: boolean }[]; areas: string[] }>(
        "/ai-agent/tools",
      )
      .then((r) => r.data),
};

/**
 * Read the ndjson chat stream.
 *
 * Deliberately uses fetch + a body reader rather than EventSource: the request
 * is a POST with a JSON body and a session cookie, neither of which
 * EventSource supports. Every line is one JSON object; a malformed line is
 * skipped rather than killing the turn.
 */
export async function streamChat(
  sessionId: string,
  message: string,
  options: {
    file_ids?: number[];
    signal?: AbortSignal;
    onEvent: (event: StreamEvent) => void;
  },
): Promise<void> {
  const response = await fetch(`/api/v1/ai-agent/sessions/${sessionId}/chat`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", Accept: "application/x-ndjson" },
    body: JSON.stringify({ message, file_ids: options.file_ids || [], stream: true }),
    signal: options.signal,
  });

  if (!response.ok || !response.body) {
    // Surface the API's own wording when there is one (422, 429, 503 …).
    let detail = `The agent is unavailable (HTTP ${response.status}).`;
    try {
      const body = await response.json();
      detail = body?.detail || body?.message || detail;
    } catch {
      /* not JSON */
    }
    options.onEvent({
      type: "error",
      error: { code: "upstream_error", message: String(detail), retryable: response.status >= 500 },
    });
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const flush = (chunk: string) => {
    const line = chunk.trim();
    if (!line) return;
    try {
      options.onEvent(JSON.parse(line) as StreamEvent);
    } catch {
      /* a partial line at the very end; nothing to show */
    }
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let newline = buffer.indexOf("\n");
    while (newline !== -1) {
      flush(buffer.slice(0, newline));
      buffer = buffer.slice(newline + 1);
      newline = buffer.indexOf("\n");
    }
  }
  buffer += decoder.decode();
  flush(buffer);
}
