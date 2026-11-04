import { useCallback, useEffect, useMemo, useState } from "react";
import toast from "react-hot-toast";
import {
  AlertTriangle, CheckCircle2, ChevronDown, Eye, EyeOff, Gauge,
  KeyRound, Loader2, RefreshCw, ShieldCheck, Sparkles, XCircle, Zap,
} from "lucide-react";
import { agentApi, type AgentHealth, type AgentSettings, type ModelOption, type Provider } from "../../api/aiAgent";

/**
 * MODEL & KEY SETUP
 *
 * The screen that makes the agent's brain work. Three things were broken before
 * and are the whole point of this component:
 *
 *   1. *Which models exist?* Asked of the provider, live, every time. Nothing is
 *      typed by hand, so a retired model can never crash a chat again.
 *   2. *Is the key actually working?* Test and Save both run a real request and
 *      report the provider's own error text. The saved (masked) key can be
 *      tested without re-typing it.
 *   3. *Is it reachable right now?* A live ping, not a "configured: true" flag
 *      read from the database — the old dot was green while every chat 500'd.
 *
 * The key itself never comes back to the browser: only `gsk_***ZJGp`-style
 * hints, which is why the input is always empty and says "paste to replace".
 */

const PROVIDER_META: Record<Provider, { label: string; blurb: string; console: string; placeholder: string }> = {
  groq: {
    label: "Groq",
    blurb: "Fastest replies. Also transcribes voice notes.",
    console: "console.groq.com → API keys",
    placeholder: "gsk_…",
  },
  gemini: {
    label: "Gemini",
    blurb: "Best for long files and careful writing.",
    console: "aistudio.google.com → Get API key",
    placeholder: "AIza…",
  },
};

const STATE_TONE: Record<string, { label: string; dot: string; text: string }> = {
  ok: { label: "Working", dot: "bg-success-500", text: "text-success-700 dark:text-success-400" },
  key_missing: { label: "No key", dot: "bg-gray-300 dark:bg-gray-600", text: "text-gray-500" },
  key_invalid: { label: "Key rejected", dot: "bg-danger-500", text: "text-danger-700 dark:text-danger-400" },
  model_unavailable: { label: "Model retired", dot: "bg-warning-500", text: "text-warning-700 dark:text-warning-400" },
  rate_limited: { label: "Rate limited", dot: "bg-warning-500", text: "text-warning-700 dark:text-warning-400" },
  timeout: { label: "Timed out", dot: "bg-warning-500", text: "text-warning-700 dark:text-warning-400" },
  provider_down: { label: "Unreachable", dot: "bg-danger-500", text: "text-danger-700 dark:text-danger-400" },
  upstream_error: { label: "Provider error", dot: "bg-danger-500", text: "text-danger-700 dark:text-danger-400" },
  safety_blocked: { label: "Blocked", dot: "bg-warning-500", text: "text-warning-700 dark:text-warning-400" },
  bad_request: { label: "Bad request", dot: "bg-danger-500", text: "text-danger-700 dark:text-danger-400" },
};

export function ProviderStatusPill({ provider, check }: { provider: Provider; check?: AgentHealth["providers"][number] }) {
  const state = check?.state || "key_missing";
  const tone = STATE_TONE[state] || STATE_TONE.key_missing;
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-gray-200 bg-white px-2.5 py-1 text-[11px] font-medium dark:border-gray-700 dark:bg-gray-800">
      <span className={`h-1.5 w-1.5 rounded-full ${tone.dot} ${state === "ok" ? "animate-pulse" : ""}`} />
      <span className="text-gray-700 dark:text-gray-200">{PROVIDER_META[provider].label}</span>
      <span className={tone.text}>{tone.label}</span>
    </span>
  );
}

export default function AgentModelSettings({
  compact = false,
  onConfigured,
}: {
  /** Compact hides the switches and the model card — used inside the chat shell. */
  compact?: boolean;
  onConfigured?: (ready: boolean) => void;
}) {
  const [settings, setSettings] = useState<AgentSettings | null>(null);
  const [health, setHealth] = useState<AgentHealth | null>(null);
  const [models, setModels] = useState<Record<Provider, ModelOption[]>>({ groq: [], gemini: [] });
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<Provider, string>>({ groq: "", gemini: "" });
  const [reveal, setReveal] = useState<Record<Provider, boolean>>({ groq: false, gemini: false });
  const [openModel, setOpenModel] = useState<Provider | null>(null);
  const [results, setResults] = useState<Record<string, any>>({});

  const load = useCallback(async (refreshModels = false) => {
    setLoadError(null);
    try {
      const [s, h] = await Promise.all([agentApi.settings(), agentApi.health().catch(() => null)]);
      setSettings(s);
      setHealth(h);
      onConfigured?.(Boolean(s.groq_configured || s.gemini_configured));

      const providers: Provider[] = ["groq", "gemini"];
      const lists = await Promise.all(
        providers.map((p) =>
          (s[`${p}_configured` as "groq_configured" | "gemini_configured"])
            ? agentApi.models(p, refreshModels).then((r) => r.models).catch(() => [])
            : Promise.resolve<ModelOption[]>([]),
        ),
      );
      setModels({ groq: lists[0], gemini: lists[1] });
    } catch (error: any) {
      setLoadError(error?.response?.data?.detail || "Could not read the agent settings.");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const configured = Boolean(settings?.groq_configured || settings?.gemini_configured);
  const ready = health?.state === "ok";

  const save = async (provider: Provider, key?: string) => {
    setBusy(`save-${provider}`);
    try {
      await agentApi.saveSettings({ provider, api_key: key ?? drafts[provider] });
      setDrafts((d) => ({ ...d, [provider]: "" }));
      toast.success(`${PROVIDER_META[provider].label} key saved`);
      await load(true);
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || "Could not save the key");
    } finally {
      setBusy(null);
    }
  };

  const clear = async (provider: Provider) => {
    if (!window.confirm(`Remove the ${PROVIDER_META[provider].label} key? The agent will stop using it.`)) return;
    setBusy(`clear-${provider}`);
    try {
      await agentApi.saveSettings({ provider, clear_key: true });
      toast.success(`${PROVIDER_META[provider].label} key removed`);
      await load();
    } finally {
      setBusy(null);
    }
  };

  /**
   * Test uses the saved key when the input is empty — the fix for a button that
   * used to demand a key the operator could no longer see.
   */
  const test = async (provider: Provider) => {
    setBusy(`test-${provider}`);
    setResults((r) => ({ ...r, [provider]: null }));
    try {
      const typed = drafts[provider].trim();
      const data = await agentApi.testKey(typed ? { provider, api_key: typed } : { provider });
      setResults((r) => ({ ...r, [provider]: data }));
      if (data.success) toast.success(data.message || "Key works");
      else toast.error(data.message || "Key check failed");
      if (data.success) await load(true);
    } catch (error: any) {
      const message = error?.response?.data?.detail || "Could not reach the server to test the key";
      setResults((r) => ({ ...r, [provider]: { success: false, message } }));
      toast.error(message);
    } finally {
      setBusy(null);
    }
  };

  const chooseModel = async (provider: Provider, model: string) => {
    setBusy(`model-${provider}`);
    try {
      await agentApi.saveSettings({ provider, model });
      setSettings((s) => (s ? { ...s, models: { ...s.models, [provider]: model } } : s));
      toast.success(`Now using ${model}`);
      setOpenModel(null);
      agentApi.health().then(setHealth).catch(() => {});
    } catch {
      toast.error("Could not save the model");
    } finally {
      setBusy(null);
    }
  };

  const setFlag = async (patch: Partial<{ auto_fallback: boolean; auto_approve: boolean; default_provider: Provider }>) => {
    setSettings((s) => (s ? { ...s, ...patch } : s));
    try {
      await agentApi.saveSettings(patch);
    } catch {
      toast.error("Could not save that setting");
      await load();
    }
  };

  const missingKey = !configured;
  const checkedAt = useMemo(() => {
    if (!health?.checked_at) return "";
    try {
      return new Date(health.checked_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    } catch {
      return "";
    }
  }, [health?.checked_at]);

  return (
    <div className="space-y-4">
      {/* ---- headline ------------------------------------------------ */}
      <div className={`card ${compact ? "p-3.5" : "p-4 sm:p-5"}`}>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="flex items-center gap-2 text-[15px] font-semibold">
              <Sparkles size={16} className="text-primary-700 dark:text-primary-300" />
              The agent's brain
            </h2>
            <p className="mt-0.5 text-[12.5px] text-gray-500 dark:text-gray-400">
              {ready
                ? "Connected and answering. Models are read live from the provider."
                : missingKey
                  ? "Add one key and the agent can run your whole workspace."
                  : "A key is saved, but the agent cannot reach the provider right now."}
            </p>
          </div>
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => load(true)}
              className="btn-ghost btn-sm"
              title="Re-check keys, models and reachability"
            >
              <RefreshCw size={13} /> Re-check
            </button>
          </div>
        </div>

        {loadError && (
          <p className="mt-2 flex items-center gap-1.5 text-[12.5px] text-danger-700 dark:text-danger-400">
            <AlertTriangle size={13} /> {loadError}
          </p>
        )}

        {health && (
          <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
            {(["groq", "gemini"] as Provider[]).map((p) => (
              <ProviderStatusPill key={p} provider={p} check={health.providers.find((c) => c.provider === p)} />
            ))}
            <span className="ml-auto flex items-center gap-1 text-[11px] text-gray-400">
              <Gauge size={11} /> {health.tools} tools · {health.tool_areas.length} areas
              {checkedAt ? ` · checked ${checkedAt}` : ""}
            </span>
          </div>
        )}
      </div>

      {/* ---- keys ---------------------------------------------------- */}
      <div className="grid gap-3 sm:grid-cols-2">
        {(["groq", "gemini"] as Provider[]).map((provider) => {
          const meta = PROVIDER_META[provider];
          const isSet = Boolean(settings?.[`${provider}_configured` as "groq_configured" | "gemini_configured"]);
          const hint = settings?.[`${provider}_hint` as "groq_hint" | "gemini_hint"];
          const check = health?.providers.find((c) => c.provider === provider);
          const result = results[provider];
          const isDefault = settings?.default_provider === provider;
          return (
            <div key={provider} className="card p-3.5">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="flex items-center gap-1.5 text-[13.5px] font-semibold">
                    <KeyRound size={13} className="text-gray-400" /> {meta.label}
                    {isDefault && (
                      <span className="badge-blue !px-1.5 !py-0.5 !text-[9.5px]">PRIMARY</span>
                    )}
                  </p>
                  <p className="mt-0.5 text-[11.5px] text-gray-500 dark:text-gray-400">{meta.blurb}</p>
                </div>
                <span
                  className={`shrink-0 rounded-full px-2 py-0.5 text-[10.5px] font-semibold ${
                    isSet
                      ? "bg-success-100 text-success-700 dark:bg-success-950/70 dark:text-success-300"
                      : "bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400"
                  }`}
                >
                  {isSet ? "Saved" : "Not set"}
                </span>
              </div>

              {isSet && hint && (
                <p className="mt-2 flex items-center gap-1.5 font-mono text-[11.5px] text-gray-500 dark:text-gray-400">
                  <ShieldCheck size={11} className="text-success-600" /> {hint}
                  <span className="font-sans text-[10.5px] text-gray-400">— encrypted, never sent to the browser</span>
                </p>
              )}

              <div className="mt-2 flex gap-1.5">
                <div className="relative min-w-0 flex-1">
                  <input
                    type={reveal[provider] ? "text" : "password"}
                    value={drafts[provider]}
                    onChange={(e) => setDrafts((d) => ({ ...d, [provider]: e.target.value }))}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && drafts[provider].trim()) void save(provider);
                    }}
                    placeholder={isSet ? "Paste a new key to replace it" : meta.placeholder}
                    aria-label={`${meta.label} API key`}
                    autoComplete="off"
                    spellCheck={false}
                    className="input !py-2 pr-9 font-mono !text-[13px]"
                  />
                  <button
                    type="button"
                    onClick={() => setReveal((r) => ({ ...r, [provider]: !r[provider] }))}
                    className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded-lg p-1.5 text-gray-400 hover:text-gray-600"
                    aria-label={reveal[provider] ? "Hide key" : "Show key"}
                  >
                    {reveal[provider] ? <EyeOff size={13} /> : <Eye size={13} />}
                  </button>
                </div>
              </div>

              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                <button
                  type="button"
                  onClick={() => save(provider)}
                  disabled={!drafts[provider].trim() || busy !== null}
                  className="btn-primary btn-sm"
                >
                  {busy === `save-${provider}` ? <Loader2 size={12} className="animate-spin" /> : <CheckCircle2 size={12} />}
                  Save
                </button>
                <button
                  type="button"
                  onClick={() => test(provider)}
                  disabled={busy !== null || (!drafts[provider].trim() && !isSet)}
                  className="btn-secondary btn-sm"
                  title={drafts[provider].trim() ? "Test the key you typed" : "Test the saved key"}
                >
                  {busy === `test-${provider}` ? <Loader2 size={12} className="animate-spin" /> : <Zap size={12} />}
                  Test
                </button>
                {isSet && (
                  <button
                    type="button"
                    onClick={() => clear(provider)}
                    disabled={busy !== null}
                    className="btn-ghost btn-sm ml-auto text-danger-600 hover:bg-danger-50 dark:text-danger-400 dark:hover:bg-danger-950/40"
                  >
                    Remove
                  </button>
                )}
              </div>

              {result && (
                <p
                  className={`mt-2 flex items-start gap-1.5 rounded-lg px-2 py-1.5 text-[11.5px] ${
                    result.success
                      ? "bg-success-50 text-success-800 dark:bg-success-950/40 dark:text-success-300"
                      : "bg-danger-50 text-danger-800 dark:bg-danger-950/40 dark:text-danger-300"
                  }`}
                >
                  {result.success ? <CheckCircle2 size={12} className="mt-0.5" /> : <XCircle size={12} className="mt-0.5" />}
                  <span className="min-w-0">
                    {result.message}
                    {result.suggested_model && (
                      <span className="mt-0.5 block font-mono text-[10.5px] opacity-80">
                        model: {result.suggested_model}
                      </span>
                    )}
                    {result.hint && <span className="mt-0.5 block opacity-80">{result.hint}</span>}
                  </span>
                </p>
              )}

              {!result && check && check.state !== "ok" && check.state !== "key_missing" && (
                <p className="mt-2 flex items-start gap-1.5 rounded-lg bg-warning-50 px-2 py-1.5 text-[11.5px] text-warning-800 dark:bg-warning-950/40 dark:text-warning-300">
                  <AlertTriangle size={12} className="mt-0.5" />
                  <span className="min-w-0">
                    {check.message}
                    {check.hint ? <span className="mt-0.5 block opacity-80">{check.hint}</span> : null}
                  </span>
                </p>
              )}

              <p className="mt-2 text-[10.5px] text-gray-400">
                Get one at {meta.console}.
              </p>
            </div>
          );
        })}
      </div>

      {/* ---- models -------------------------------------------------- */}
      {!compact && configured && (
        <div className="card p-3.5 sm:p-4">
          <h3 className="text-[13.5px] font-semibold">Model</h3>
          <p className="mt-0.5 text-[11.5px] text-gray-500 dark:text-gray-400">
            Read from the provider's live list. If one is ever retired, the agent
            picks a working replacement and tells you.
          </p>
          <div className="mt-2.5 grid gap-3 sm:grid-cols-2">
            {(["groq", "gemini"] as Provider[]).map((provider) => {
              const list = models[provider];
              const selected = settings?.models?.[provider];
              if (!settings?.[`${provider}_configured` as "groq_configured" | "gemini_configured"]) return null;
              return (
                <div key={provider}>
                  <button
                    type="button"
                    onClick={() => setOpenModel(openModel === provider ? null : provider)}
                    className="flex w-full items-center justify-between gap-2 rounded-xl border border-gray-200 px-3 py-2 text-left hover:border-primary-300 dark:border-gray-700"
                  >
                    <span className="min-w-0">
                      <span className="block text-[11px] font-medium uppercase tracking-wide text-gray-400">
                        {PROVIDER_META[provider].label}
                      </span>
                      <span className="block truncate font-mono text-[12px]">
                        {selected || (list.length ? "Choose a model" : "No models listed")}
                      </span>
                    </span>
                    <ChevronDown
                      size={14}
                      className={`shrink-0 text-gray-400 transition-transform ${openModel === provider ? "rotate-180" : ""}`}
                    />
                  </button>
                  {openModel === provider && (
                    <div className="mt-1.5 max-h-64 space-y-1 overflow-y-auto rounded-xl border border-gray-200 p-1 dark:border-gray-700">
                      {list.length === 0 && (
                        <p className="px-2 py-1.5 text-[12px] text-gray-500">
                          No models were returned. Use Test to see why.
                        </p>
                      )}
                      {list.map((model) => (
                        <button
                          key={model.id}
                          type="button"
                          onClick={() => chooseModel(provider, model.id)}
                          className={`flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[12px] hover:bg-gray-50 dark:hover:bg-gray-800 ${
                            model.id === selected ? "bg-primary-50 dark:bg-primary-950/40" : ""
                          }`}
                        >
                          <span className="min-w-0 flex-1 truncate font-mono">{model.id}</span>
                          {!model.tools && (
                            <span className="shrink-0 text-[10px] text-gray-400" title="This model cannot call tools">
                              no tools
                            </span>
                          )}
                          {model.recommended && <span className="badge-blue !px-1.5 !py-0.5 !text-[9px]">BEST</span>}
                          {model.id === selected && <CheckCircle2 size={12} className="shrink-0 text-primary-600" />}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* ---- behaviour ---------------------------------------------- */}
      {!compact && configured && settings && (
        <div className="card divide-y divide-gray-100 p-0 dark:divide-gray-800">
          <Toggle
            title="Fall back to the other provider"
            description="If one is rate-limited or down, the agent continues on the other and says so."
            checked={settings.auto_fallback}
            onChange={(v) => setFlag({ auto_fallback: v })}
          />
          <Toggle
            title="Skip approval for risky actions"
            description="Off by default. When on, sending, deleting and launching campaigns happen without a card — the activity log still records everything."
            checked={settings.auto_approve}
            onChange={(v) => setFlag({ auto_approve: v })}
            danger
          />
        </div>
      )}
    </div>
  );
}

function Toggle({
  title,
  description,
  checked,
  onChange,
  danger,
}: {
  title: string;
  description: string;
  checked: boolean;
  onChange: (value: boolean) => void;
  danger?: boolean;
}) {
  return (
    <label className="flex cursor-pointer items-start justify-between gap-3 px-3.5 py-3">
      <span className="min-w-0">
        <span className={`block text-[13px] font-medium ${danger ? "text-danger-800 dark:text-danger-300" : ""}`}>
          {title}
        </span>
        <span className="mt-0.5 block text-[11.5px] text-gray-500 dark:text-gray-400">{description}</span>
      </span>
      <span className="relative mt-0.5 shrink-0">
        <input type="checkbox" className="peer sr-only" checked={checked} onChange={(e) => onChange(e.target.checked)} />
        <span
          className={`block h-6 w-11 rounded-full transition-colors ${
            checked ? (danger ? "bg-danger-600" : "bg-primary-700") : "bg-gray-300 dark:bg-gray-600"
          }`}
        />
        <span
          className={`absolute left-0.5 top-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform ${
            checked ? "translate-x-5" : ""
          }`}
        />
      </span>
    </label>
  );
}
