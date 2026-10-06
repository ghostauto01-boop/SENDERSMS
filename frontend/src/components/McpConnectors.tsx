import { useCallback, useEffect, useState } from "react";
import toast from "react-hot-toast";
import {
  Activity,
  AlertTriangle,
  CheckCircle,
  Copy,
  ExternalLink,
  KeyRound,
  Plus,
  RefreshCw,
  Sparkles,
  Terminal,
  Trash2,
  XCircle,
} from "lucide-react";
import mcpApi, {
  Connector,
  ConnectorList,
  ConnectorTestReport,
  McpCallRow,
  McpClientRow,
  McpToken,
} from "../api/mcp";

/**
 * AI CONNECTORS — one card per client, because the clients are not the same.
 *
 * The previous version of this screen told everyone to "paste the URL and choose
 * API key authentication". That is not how any of them works, which is why the
 * connector appeared broken:
 *
 * * **ChatGPT** (hosted connectors) accepts **OAuth 2.1 only** — no API keys, no
 *   client credentials, no pasted bearer token. It discovers the authorization
 *   server from RFC 9728 protected-resource metadata at the URL you give it.
 * * **Claude** (claude.ai) also requires OAuth with PKCE S256, and additionally
 *   refuses to start the flow unless an unauthenticated request gets a real 401
 *   carrying `WWW-Authenticate: Bearer resource_metadata="…"`. Claude *Code* is
 *   the exception: it accepts a configured `Authorization` header.
 * * **Arena Agent Mode** has no custom-connector UI at all. It drives the
 *   endpoint with curl, or through the stdio bridge in `tools/arena-connector/`.
 *
 * So each card is built from that client's own documented requirements, which the
 * server sends down (`setup_steps`, `auth_modes`, `redirect_uris`, `toolset`),
 * and each one can run the exact handshake that client performs and show where it
 * broke, with the fix.
 */

function Code({ value, label, block }: { value: string; label?: string; block?: boolean }) {
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      toast.success(`${label || "Copied"}`);
    } catch {
      toast.error("Copy failed — select the text and copy it manually");
    }
  };
  return (
    <div className={`flex items-start gap-2 ${block ? "" : "items-center"}`}>
      <code
        className={`flex-1 text-xs bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded p-2 overflow-x-auto ${
          block ? "whitespace-pre-wrap break-all" : "whitespace-nowrap"
        }`}
      >
        {value}
      </code>
      <button className="btn-secondary btn-sm shrink-0" onClick={copy} title={`Copy ${label || ""}`}>
        <Copy size={13} />
      </button>
    </div>
  );
}

function StatusPill({ status }: { status: Connector["status"] }) {
  if (status === "ok") return <span className="badge-green">handshake passed</span>;
  if (status === "error") return <span className="badge-red">handshake failed</span>;
  return <span className="badge-gray">not tested</span>;
}

function TestReport({ report }: { report: ConnectorTestReport | { steps: any[] } }) {
  const steps = report.steps || [];
  if (!steps.length) return null;
  // A step that failed without being fatal is advice (e.g. "PUBLIC_BASE_URL is
  // unset but the URL was detected, so this works now"): amber with the fix,
  // never a red cross that sends the operator hunting for a non-existent bug.
  const advice = (s: any) => !s.ok && s.fatal === false;
  const failures = steps.filter((s: any) => !s.ok && s.fatal !== false).length;
  return (
    <div className="space-y-2">
      <p className="text-xs text-gray-500 flex items-center gap-2 flex-wrap">
        <span className="badge-green">
          {steps.filter((s: any) => s.ok).length} of {steps.length} steps pass
        </span>
        {failures > 0 && <span className="badge-red">{failures} blocking</span>}
        {steps.filter(advice).length > 0 && (
          <span className="badge-yellow">
            {steps.filter(advice).length} improvement{steps.filter(advice).length === 1 ? "" : "s"}
          </span>
        )}
      </p>
      <div className="rounded-xl border border-gray-200 dark:border-gray-700 divide-y divide-gray-100 dark:divide-gray-800 max-h-80 overflow-y-auto">
        {steps.map((s: any, i: number) => (
          <div key={i} className="p-3 text-xs flex items-start gap-2">
            {s.ok ? (
              <CheckCircle size={14} className="text-success-500 mt-0.5 shrink-0" />
            ) : advice(s) ? (
              <AlertTriangle size={14} className="text-warning-500 mt-0.5 shrink-0" />
            ) : (
              <XCircle size={14} className="text-danger-500 mt-0.5 shrink-0" />
            )}
            <div className="min-w-0">
              <p className="font-medium">
                {s.step}
                {s.status != null && <span className="text-gray-400"> · HTTP {s.status}</span>}
              </p>
              <p className="text-gray-600 dark:text-gray-400 break-words">{s.detail}</p>
              {s.fix && (
                <p className="text-warning-700 dark:text-warning-300 mt-1">
                  <strong>{advice(s) ? "Improve it" : "Fix"}:</strong> {s.fix}
                </p>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Where the OAuth metadata's absolute URLs come from, and whether that is fine. */
function BaseUrlPanel({ data }: { data: ConnectorList }) {
  const base = data.public_base_url || "";
  const source = data.base_url_source || (data.ready ? "env" : "none");
  const detected = source === "request";
  const configured = source === "env" || source === "render";

  if (!data.ready) {
    return (
      <div className="rounded-xl border border-danger-200 bg-danger-50 p-3 text-sm dark:border-danger-900 dark:bg-danger-950/30">
        <p className="font-medium text-danger-700 dark:text-danger-300 flex items-center gap-2">
          <AlertTriangle size={15} /> No public address could be determined
        </p>
        <p className="mt-1 text-xs text-gray-600 dark:text-gray-300">{data.problem}</p>
      </div>
    );
  }

  return (
    <div
      className={`rounded-xl border p-3 text-sm ${
        configured
          ? "border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/40"
          : "border-primary-200 bg-primary-50 dark:border-primary-900 dark:bg-primary-950/30"
      }`}
    >
      <p className="font-medium flex items-center gap-2 flex-wrap">
        <CheckCircle size={15} className="text-success-500" />
        Server address
        <span className={configured ? "badge-green" : "badge-blue"}>
          {configured ? `configured (${source})` : "detected from this session"}
        </span>
      </p>
      <Code value={base} label="Address copied" />
      {detected && (
        <p className="mt-2 text-xs text-gray-600 dark:text-gray-300">
          The connector works right now — every URL the AI client needs is built from the address
          above, taken from the browser session you are using. To keep it working for a client that
          reaches this app on a different host (a phone on the same Wi-Fi, a custom domain, a
          tunnel), set{" "}
          <code className="rounded bg-white px-1 dark:bg-gray-800">PUBLIC_BASE_URL={base}</code> in
          the server environment and restart.
        </p>
      )}
      {configured && (
        <p className="mt-2 text-xs text-gray-500">
          Set explicitly, so clients that connect from the internet resolve the same address.
        </p>
      )}
    </div>
  );
}

function ConnectorCard({
  connector,
  changed,
  token,
  bridge,
}: {
  connector: Connector;
  changed: () => void;
  token: string | null;
  bridge?: ConnectorList["bridge"];
}) {
  const [testing, setTesting] = useState(false);
  const [report, setReport] = useState<ConnectorTestReport | null>(null);
  const [showSteps, setShowSteps] = useState(false);
  const [busy, setBusy] = useState(false);
  const isChatgpt = connector.key === "chatgpt";
  const isClaude = connector.key === "claude";
  const isArena = connector.key === "arena";
  const bearer = connector.auth_modes.includes("bearer");

  const runTest = async () => {
    setTesting(true);
    setShowSteps(true);
    try {
      setReport(await mcpApi.testConnector(connector.key));
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "The connection test failed");
    } finally {
      setTesting(false);
      changed();
    }
  };

  const patch = async (next: Partial<Connector>) => {
    setBusy(true);
    try {
      await mcpApi.updateConnector(connector.key, next as any);
      toast.success("Saved");
      changed();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not save");
    } finally {
      setBusy(false);
    }
  };

  const lastReport = report || (connector.last_test_report?.length
    ? ({ steps: connector.last_test_report } as any)
    : null);

  return (
    <div className="card p-5 space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="font-semibold flex items-center gap-2 flex-wrap">
            <Sparkles size={16} className="text-primary-600" />
            {connector.label}
            <span className="text-xs font-normal text-gray-400">{connector.vendor}</span>
            <StatusPill status={connector.status} />
          </h3>
          <p className="text-sm text-gray-500 mt-1">{connector.blurb}</p>
          {connector.docs_url && (
            <a
              className="text-xs text-primary-600 hover:underline inline-flex items-center gap-1 mt-1"
              href={connector.docs_url}
              target="_blank"
              rel="noreferrer"
            >
              <ExternalLink size={11} /> What {connector.vendor} documents about this
            </a>
          )}
        </div>
        <div className="flex gap-2">
          <button className="btn-secondary btn-sm" onClick={runTest} disabled={testing}>
            {testing ? "Testing…" : "Run connection test"}
          </button>
        </div>
      </div>

      {!connector.endpoint_ready && (
        <div className="rounded-xl bg-danger-50 dark:bg-danger-950/30 border border-danger-200 dark:border-danger-900 p-3 text-sm">
          <p className="font-medium text-danger-700 dark:text-danger-300 flex items-center gap-2">
            <AlertTriangle size={15} /> This address is not reachable from the internet
          </p>
          <p className="text-xs text-gray-600 dark:text-gray-300 mt-1">
            The URL below is built from the address this browser used. An AI client running in the
            cloud has to be able to reach the same host, so on a local or LAN address set{" "}
            <code className="rounded bg-white px-1 dark:bg-gray-800">
              PUBLIC_BASE_URL=https://your-app.example.com
            </code>{" "}
            and restart the server.
          </p>
        </div>
      )}

      <div className="space-y-1.5">
        <p className="label">MCP server URL — paste this into {connector.label}</p>
        <Code value={connector.endpoint || "(set PUBLIC_BASE_URL)"} label="URL copied" />
        <div className="flex flex-wrap gap-1.5 pt-1">
          {connector.auth_modes.map((m) => (
            <span key={m} className={m === "oauth" ? "badge-blue" : "badge-gray"}>
              {m === "oauth" ? "OAuth 2.1 sign-in" : "Bearer token header"}
            </span>
          ))}
          <span className="badge-gray">
            {connector.toolset_active === "core"
              ? `focused tool set${connector.tool_count ? ` · ${connector.tool_count} tools` : ""}`
              : "full tool set"}
          </span>
          {connector.client_id_metadata_documents && (
            <span className="badge-gray">client_id metadata documents</span>
          )}
          {connector.allow_loopback_redirects && (
            <span className="badge-gray">localhost redirect (CLI)</span>
          )}
        </div>
      </div>

      <div className="space-y-2">
        <p className="label">Set it up</p>
        <ol className="text-sm text-gray-600 dark:text-gray-300 space-y-1.5 list-decimal pl-5">
          {connector.setup_steps.map((step, i) => (
            <li key={i}>{step}</li>
          ))}
        </ol>
      </div>

      {isChatgpt && (
        <div className="rounded-lg bg-primary-50 dark:bg-primary-950/30 border border-primary-200 dark:border-primary-900 p-3 text-xs space-y-1">
          <p className="font-medium text-sm">Why there is no token box for ChatGPT</p>
          <p className="text-gray-600 dark:text-gray-300">
            OpenAI's connector platform accepts OAuth 2.1 with PKCE only. There is no field for an
            API key, and a server that offers one is rejected before it is ever shown. Sign-in
            happens on <em>this</em> app: the page that opens asks for your password (or opens
            straight to consent) and then asks which permissions to grant.
          </p>
          {connector.redirect_uris.length > 0 && (
            <p className="text-gray-500">
              ChatGPT redirects back to: <code>{connector.redirect_uris.join(", ")}</code>
            </p>
          )}
        </div>
      )}

      {isClaude && (
        <div className="space-y-2">
          <p className="label">Claude Code (terminal)</p>
          <Code
            block
            label="Command copied"
            value={
              bearer && token
                ? `claude mcp add --transport http sendsms ${connector.endpoint} --header "Authorization: Bearer ${token}"`
                : `claude mcp add --transport http sendsms ${connector.endpoint}`
            }
          />
          <p className="text-xs text-gray-500">
            Then type <code>/mcp</code> inside Claude Code and choose <em>Authenticate</em> to sign
            in through this app. Add the <code>--header</code> form only if you would rather skip
            OAuth — a header Claude Code cannot use makes the connection fail outright, with no
            fallback.
          </p>
        </div>
      )}

      {isArena && (
        <div className="rounded-lg border border-gray-200 dark:border-gray-700 p-3 space-y-2">
          <p className="font-medium text-sm flex items-center gap-2">
            <Terminal size={14} /> Arena Agent Mode has no connector screen
          </p>
          <p className="text-xs text-gray-600 dark:text-gray-300">
            Agent Mode works in a sandbox with bash and file access, so it reaches this app either
            with plain <code>curl</code> or through the stdio bridge that ships in this repository.
            Create a token below, then give the agent one of these:
          </p>
          <Code
            block
            label="Prompt copied"
            value={
              token
                ? `Read tools/arena-connector/AGENTS.md in this repository and follow it. The MCP server is ${connector.endpoint} and the bearer token is ${token}.`
                : `Read tools/arena-connector/AGENTS.md in this repository and follow it. The MCP server is ${connector.endpoint}; ask me for the bearer token.`
            }
          />
          <Code
            block
            label="curl copied"
            value={`curl -sS ${connector.endpoint} \\\n  -H "Content-Type: application/json" \\\n  -H "Accept: application/json, text/event-stream"${
              token ? ` \\\n  -H "Authorization: Bearer ${token}"` : ""
            } \\\n  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'`}
          />
          {bridge?.available && (
            <>
              <p className="text-xs text-gray-600 dark:text-gray-300">
                The bridge is bundled with the app — download it here, no repository checkout needed:
              </p>
              <a
                className="btn-secondary btn-sm inline-flex"
                href={bridge.download_url}
                download={bridge.script}
              >
                <ExternalLink size={13} /> Download {bridge.script}
              </a>
              <Code block label="Command copied" value={bridge.run} />
              <Code
                block
                label="Config copied"
                value={JSON.stringify(bridge.config, null, 2)}
              />
            </>
          )}
          <p className="text-xs text-gray-500">
            <code>tools/arena-connector/mcp.json</code> is a ready-made config block for any MCP
            client that reads one, and <code>arena_mcp_bridge.py</code> needs nothing but Python 3.
          </p>
        </div>
      )}

      {bearer && (
        <div className="space-y-1.5">
          <p className="label">Bearer token (for clients that cannot do OAuth)</p>
          {token ? (
            <Code value={`Authorization: Bearer ${token}`} label="Header copied" />
          ) : (
            <p className="text-xs text-gray-500">
              Create a token in the <em>Access tokens</em> panel below and it appears here.
            </p>
          )}
        </div>
      )}

      <div className="rounded-lg border border-gray-200 dark:border-gray-700 p-3 space-y-3">
        <p className="label">Permissions</p>
        <div className="flex flex-wrap items-center gap-4">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={connector.oauth_enabled}
              disabled={busy || !connector.auth_modes.includes("oauth")}
              onChange={(e) => patch({ oauth_enabled: e.target.checked } as any)}
            />
            OAuth sign-in on this app
          </label>
          <label className="flex items-center gap-2 text-sm">
            <span className="text-gray-500">Default permission</span>
            <select
              className="input py-1 text-sm"
              value={connector.default_scope}
              disabled={busy}
              onChange={(e) => patch({ default_scope: e.target.value as any })}
            >
              <option value="write">Read &amp; write</option>
              <option value="read">Read only</option>
            </select>
          </label>
          <label className="flex items-center gap-2 text-sm">
            <span className="text-gray-500">Tool set</span>
            <select
              className="input py-1 text-sm"
              value={connector.toolset_active}
              disabled={busy}
              onChange={(e) => patch({ toolset: e.target.value as any })}
            >
              <option value="core">Focused — the tools an assistant actually needs</option>
              <option value="full">Full — every tool this app exposes</option>
            </select>
          </label>
        </div>
        <p className="text-xs text-gray-500">
          The consent screen always shows what is being granted, and the client can never exceed the
          default permission. {connector.authorize_count} sign-in
          {connector.authorize_count === 1 ? "" : "s"} · {connector.token_count} token
          {connector.token_count === 1 ? "" : "s"} issued · {connector.call_count} call
          {connector.call_count === 1 ? "" : "s"}
          {connector.last_call_at ? ` · last ${new Date(connector.last_call_at).toLocaleString()}` : ""}
        </p>
        {(connector.token_count > 0 || connector.status !== "unknown") && (
          <button
            className="btn-secondary btn-sm text-danger-600"
            disabled={busy}
            onClick={async () => {
              if (!confirm(`Revoke every ${connector.label} grant? The assistant stops working until it signs in again.`))
                return;
              const r = await mcpApi.revokeConnector(connector.key);
              toast.success(`${r.revoked} grant(s) revoked`);
              changed();
            }}
          >
            <Trash2 size={13} className="mr-1" /> Revoke all grants
          </button>
        )}
      </div>

      {lastReport && (
        <div className="space-y-2">
          <button className="btn-secondary btn-sm" onClick={() => setShowSteps((v) => !v)}>
            {showSteps ? "Hide" : "Show"} the last handshake
            {connector.last_test_at && (
              <span className="ml-2 text-xs text-gray-400">
                {new Date(connector.last_test_at).toLocaleString()}
              </span>
            )}
          </button>
          {connector.last_error && !report && (
            <p className="text-xs text-danger-600">{connector.last_error}</p>
          )}
          {showSteps && <TestReport report={lastReport} />}
        </div>
      )}
    </div>
  );
}

export default function McpConnectors() {
  const [data, setData] = useState<ConnectorList | null>(null);
  const [clients, setClients] = useState<McpClientRow[]>([]);
  const [tokens, setTokens] = useState<McpToken[]>([]);
  const [calls, setCalls] = useState<McpCallRow[]>([]);
  const [callsTotal, setCallsTotal] = useState(0);
  const [name, setName] = useState("Claude Code");
  const [scope, setScope] = useState<"read" | "write">("write");
  const [creating, setCreating] = useState(false);
  const [fresh, setFresh] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const [c, cl, t, a] = await Promise.all([
        mcpApi.listConnectors(),
        mcpApi.listClients().catch(() => ({ items: [] as McpClientRow[] })),
        mcpApi.listTokens(),
        mcpApi.activity(40),
      ]);
      setData(c);
      setClients(cl.items);
      setTokens(t.items);
      setCalls(a.items);
      setCallsTotal(a.total);
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not load the connector settings");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const create = async () => {
    setCreating(true);
    try {
      const made = await mcpApi.createToken(name.trim() || "AI assistant", scope);
      setFresh(made.token);
      toast.success("Token created — copy it now, it is only shown once");
      await load();
    } catch {
      toast.error("Could not create the token");
    } finally {
      setCreating(false);
    }
  };

  const revoke = async (id: number, label: string) => {
    if (!confirm(`Revoke the token for "${label}"? Whatever is using it stops working immediately.`))
      return;
    try {
      await mcpApi.revokeToken(id);
      toast.success("Token revoked");
      await load();
    } catch {
      toast.error("Could not revoke the token");
    }
  };

  if (loading) {
    return (
      <div className="space-y-4 max-w-4xl">
        <div className="skeleton h-24 w-full" />
        <div className="skeleton h-64 w-full" />
      </div>
    );
  }

  const items = data?.items || [];

  return (
    <div className="space-y-4 max-w-4xl">
      <div className="card p-6 space-y-3">
        <h2 className="text-lg font-semibold flex items-center gap-2">
          <Sparkles size={18} /> Let an AI operate this app
        </h2>
        <div className="grid gap-2 sm:grid-cols-3">
          {[
            {
              n: "1",
              title: "ChatGPT / Claude",
              body: "Copy the connector URL from the card below, paste it into the client, then approve the sign-in window that opens here.",
            },
            {
              n: "2",
              title: "Claude Code / Cursor",
              body: "Create an access token, then add the server with the printed command or config block.",
            },
            {
              n: "3",
              title: "Any agent with bash",
              body: "Download the stdio bridge (Arena card) and point it at the same URL with the same token.",
            },
          ].map((step) => (
            <div
              key={step.n}
              className="rounded-xl border border-gray-200 dark:border-gray-700 p-3 space-y-1 animate-fade-up"
            >
              <p className="text-xs font-semibold text-primary-600 flex items-center gap-2">
                <span className="w-5 h-5 rounded-full brand-gradient text-white text-[11px] flex items-center justify-center">
                  {step.n}
                </span>
                {step.title}
              </p>
              <p className="text-xs text-gray-500 dark:text-gray-400">{step.body}</p>
            </div>
          ))}
        </div>
        <p className="text-sm text-gray-500">
          This app speaks the Model Context Protocol, so ChatGPT, Claude and any other MCP client can
          import contacts, build and send campaigns, answer the inbox and read analytics — the same
          actions you can do here, and every call is logged below.
        </p>
        <p className="text-sm text-gray-500">
          Each client gets its <strong>own</strong> connector, because each one's requirements are
          different and a generic URL fails in a way that produces no error message anywhere.
        </p>
        {data && <div className="mt-2"><BaseUrlPanel data={data} /></div>}
        <div className="flex items-center justify-between pt-2 gap-3 flex-wrap">
          <p className="text-xs text-gray-500">
            Protocol {data?.protocol_versions?.join(", ")} · sign-in through this app
            {data?.require_login ? " asks for your password" : " is one tap"}
          </p>
          <button className="btn-secondary btn-sm" onClick={load}>
            <RefreshCw size={13} className="mr-1" /> Refresh
          </button>
        </div>
      </div>

      {items.map((c) => (
        <ConnectorCard key={c.key} connector={c} changed={load} token={fresh} bridge={data?.bridge} />
      ))}

      <div className="card p-6 space-y-3">
        <h3 className="font-semibold flex items-center gap-2">
          <KeyRound size={16} /> Access tokens
        </h3>
        <p className="text-sm text-gray-500">
          A token is the alternative to OAuth sign-in, for clients that can set an{" "}
          <code>Authorization</code> header: Claude Code, Cursor, Cline, Windsurf and the Arena
          bridge. ChatGPT's hosted connectors ignore it — they only do OAuth.
        </p>
        <div className="flex flex-wrap items-end gap-2">
          <label className="text-sm">
            <span className="block text-gray-500 mb-1">What is it for?</span>
            <input
              className="input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Claude Code"
            />
          </label>
          <label className="text-sm">
            <span className="block text-gray-500 mb-1">Permission</span>
            <select
              className="input"
              value={scope}
              onChange={(e) => setScope(e.target.value as "read" | "write")}
            >
              <option value="write">Read &amp; write — can send</option>
              <option value="read">Read only — cannot change anything</option>
            </select>
          </label>
          <button className="btn-primary btn-sm" onClick={create} disabled={creating}>
            <Plus size={14} className="mr-1" /> {creating ? "Creating…" : "Create token"}
          </button>
        </div>

        {fresh && (
          <div className="rounded-lg bg-warning-50 dark:bg-warning-900/20 border border-warning-200 dark:border-warning-800 p-3 space-y-2">
            <p className="text-sm font-medium text-warning-800 dark:text-warning-200">
              Copy this now — it is not stored and cannot be shown again.
            </p>
            <Code value={fresh} label="Token copied" />
          </div>
        )}

        {tokens.length === 0 ? (
          <p className="text-sm text-gray-500">
            No token yet. OAuth sign-in works without one; header clients need one.
          </p>
        ) : (
          <div className="divide-y divide-gray-100 dark:divide-gray-800">
            {tokens.map((t) => (
              <div key={t.id} className="py-3 flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="font-medium flex items-center gap-2 flex-wrap text-sm">
                    {t.name}
                    <span className={t.scope === "write" ? "badge-blue" : "badge-gray"}>{t.scope}</span>
                    {!t.is_active && <span className="badge-gray">revoked</span>}
                    <code className="text-xs text-gray-400">{t.prefix}…</code>
                  </p>
                  <p className="text-xs text-gray-500">
                    {t.call_count} call{t.call_count === 1 ? "" : "s"}
                    {t.last_used_at
                      ? ` · last used ${new Date(t.last_used_at).toLocaleString()}`
                      : " · never used"}
                  </p>
                  {t.last_error && (
                    <p className="text-xs text-danger-500 mt-1 truncate">Last error: {t.last_error}</p>
                  )}
                </div>
                {t.is_active && (
                  <button
                    className="btn-secondary btn-sm shrink-0 text-danger-600"
                    onClick={() => revoke(t.id, t.name)}
                  >
                    <Trash2 size={13} className="mr-1" /> Revoke
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="card p-6 space-y-3">
        <h3 className="font-semibold">Assistants that have signed in</h3>
        {clients.length === 0 ? (
          <p className="text-sm text-gray-500">
            Nobody yet. When a client completes OAuth it registers itself here, with the permissions
            it was granted.
          </p>
        ) : (
          <div className="divide-y divide-gray-100 dark:divide-gray-800">
            {clients.map((c) => (
              <div key={c.id} className="py-3 flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="font-medium text-sm flex items-center gap-2 flex-wrap">
                    {c.client_name || c.client_id}
                    {c.connector && <span className="badge-gray">{c.connector}</span>}
                    {c.scope && <span className="badge-blue">{c.scope}</span>}
                    {!c.is_active && <span className="badge-red">disabled</span>}
                  </p>
                  <p className="text-xs text-gray-500 break-all">
                    {c.client_id} · {c.source} · {c.auth_method}
                  </p>
                  <p className="text-xs text-gray-500">
                    {/* One expression, so the line is a single text node rather than
                        fragments a screen reader (or a test) has to reassemble. */}
                    {[
                      `${c.active_grants} live grant${c.active_grants === 1 ? "" : "s"}`,
                      c.redirect_uris?.length ? `redirects to ${c.redirect_uris.join(", ")}` : "",
                      c.last_used_at ? `last used ${new Date(c.last_used_at).toLocaleString()}` : "",
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </p>
                </div>
                <button
                  className="btn-secondary btn-sm shrink-0 text-danger-600"
                  onClick={async () => {
                    if (!confirm(`Cut off ${c.client_name || c.client_id}? Its tokens are revoked and it must register again.`))
                      return;
                    await mcpApi.removeClient(c.id);
                    toast.success("Client removed");
                    load();
                  }}
                >
                  <Trash2 size={13} className="mr-1" /> Cut off
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="card p-6 space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="font-semibold flex items-center gap-2">
            <Activity size={16} /> What the AI did
          </h3>
          <div className="flex items-center gap-2">
            <span className="text-xs text-gray-500">
              {callsTotal} call{callsTotal === 1 ? "" : "s"} all time
            </span>
            <button className="btn-secondary btn-sm" onClick={load}>
              <RefreshCw size={13} className="mr-1" /> Refresh
            </button>
          </div>
        </div>
        {calls.length === 0 ? (
          <p className="text-sm text-gray-500">
            Nothing yet. Once an assistant connects, every tool call shows up here.
          </p>
        ) : (
          <div className="divide-y divide-gray-100 dark:divide-gray-800 max-h-96 overflow-y-auto">
            {calls.map((c) => (
              <div key={c.id} className="py-2.5 flex items-start gap-3 text-sm">
                {c.ok ? (
                  <CheckCircle size={15} className="text-success-500 mt-0.5 shrink-0" />
                ) : (
                  <XCircle size={15} className="text-danger-500 mt-0.5 shrink-0" />
                )}
                <div className="min-w-0 flex-1">
                  <p className="font-medium flex items-center gap-2 flex-wrap">
                    <code className="text-xs">{c.tool}</code>
                    {c.token_name && <span className="text-xs text-gray-400">via {c.token_name}</span>}
                    {c.duration_ms != null && (
                      <span className="text-xs text-gray-400">{c.duration_ms} ms</span>
                    )}
                  </p>
                  {c.request && <p className="text-xs text-gray-500 truncate">{c.request}</p>}
                  {c.error && <p className="text-xs text-danger-500 truncate">{c.error}</p>}
                </div>
                <span className="text-xs text-gray-400 whitespace-nowrap">
                  {c.created_at ? new Date(c.created_at).toLocaleString() : ""}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
