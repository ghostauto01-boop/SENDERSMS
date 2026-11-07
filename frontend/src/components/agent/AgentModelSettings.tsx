import { useCallback, useEffect, useState } from "react";
import toast from "react-hot-toast";
import {
  AlertTriangle,
  Eye,
  EyeOff,
  KeyRound,
  Loader2,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Wand2,
} from "lucide-react";
import {
  agentApi,
  type AgentHealth,
  type AgentSettings,
  type PresetList,
  type Provider,
} from "../../api/aiAgent";

/**
 * OpenRouter setup. The only routing choice exposed here is an OpenRouter
 * preset: the preset owns its model, provider routing, and fallback policy.
 * The API key is never returned to the browser; only a masked hint is shown.
 */
const PROVIDER = "openrouter" as Provider;

const PROVIDER_META = {
  label: "OpenRouter",
  console: "https://openrouter.ai/settings/keys",
  presetConsole: "https://openrouter.ai/settings/presets",
  placeholder: "sk-or-v1-…",
};

const STATE_TONE: Record<string, { label: string; dot: string; text: string }> = {
  ok: { label: "Working", dot: "bg-success-500", text: "text-success-700 dark:text-success-400" },
  key_missing: { label: "No key", dot: "bg-gray-300 dark:bg-gray-600", text: "text-gray-500" },
  key_invalid: { label: "Key rejected", dot: "bg-danger-500", text: "text-danger-700 dark:text-danger-400" },
  model_unavailable: { label: "Preset unavailable", dot: "bg-warning-500", text: "text-warning-700 dark:text-warning-400" },
  rate_limited: { label: "Rate limited", dot: "bg-warning-500", text: "text-warning-700 dark:text-warning-400" },
  timeout: { label: "Timed out", dot: "bg-warning-500", text: "text-warning-700 dark:text-warning-400" },
  provider_down: { label: "Unreachable", dot: "bg-danger-500", text: "text-danger-700 dark:text-danger-400" },
  upstream_error: { label: "Provider error", dot: "bg-danger-500", text: "text-danger-700 dark:text-danger-400" },
  safety_blocked: { label: "Blocked", dot: "bg-warning-500", text: "text-warning-700 dark:text-warning-400" },
  bad_request: { label: "Bad request", dot: "bg-danger-500", text: "text-danger-700 dark:text-danger-400" },
};

export function ProviderStatusPill({
  provider,
  check,
}: {
  provider: Provider;
  check?: AgentHealth["providers"][number];
}) {
  const state = check?.state || "key_missing";
  const tone = STATE_TONE[state] || STATE_TONE.key_missing;
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-gray-200 bg-white px-2.5 py-1 text-[11px] font-medium dark:border-gray-700 dark:bg-gray-800">
      <span className={`h-1.5 w-1.5 rounded-full ${tone.dot} ${state === "ok" ? "animate-pulse" : ""}`} />
      <span className="text-gray-700 dark:text-gray-200">{provider === PROVIDER ? PROVIDER_META.label : provider}</span>
      <span className={tone.text}>{tone.label}</span>
    </span>
  );
}

export default function AgentModelSettings({
  compact = false,
  onConfigured,
}: {
  /** Compact hides behaviour switches and avoids a duplicate live health ping. */
  compact?: boolean;
  onConfigured?: (ready: boolean) => void;
}) {
  const [settings, setSettings] = useState<AgentSettings | null>(null);
  const [health, setHealth] = useState<AgentHealth | null>(null);
  const [presets, setPresets] = useState<PresetList | null>(null);
  const [presetDraft, setPresetDraft] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [keyDraft, setKeyDraft] = useState("");
  const [reveal, setReveal] = useState(false);
  const [result, setResult] = useState<any>(null);

  const load = useCallback(async () => {
    setLoadError(null);
    setLoading(true);
    try {
      const current = await agentApi.settings();
      setSettings(current);
      setPresetDraft(current.preset || "");
      onConfigured?.(Boolean(current.openrouter_configured));

      if (current.openrouter_configured) {
        const presetList = await agentApi.presets().catch(() => null);
        setPresets(presetList);
      } else {
        setPresets(null);
      }
      if (!compact) {
        agentApi.health().then(setHealth).catch(() => setHealth(null));
      }
    } catch (error: any) {
      setLoadError(error?.response?.data?.detail || "Could not read the agent settings.");
    } finally {
      setLoading(false);
    }
  }, [compact, onConfigured]);

  useEffect(() => {
    void load();
  }, [load]);

  const configured = Boolean(settings?.openrouter_configured);

  const saveKey = async () => {
    setBusy("save");
    try {
      await agentApi.saveSettings({ api_key: keyDraft });
      setKeyDraft("");
      toast.success("OpenRouter key saved");
      await load();
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || "Could not save the key");
    } finally {
      setBusy(null);
    }
  };

  const clearKey = async () => {
    if (!window.confirm("Remove the OpenRouter key? The agent will stop answering.")) return;
    setBusy("clear");
    try {
      await agentApi.saveSettings({ clear_key: true });
      setSettings((current) => current ? { ...current, openrouter_configured: false, openrouter_hint: null } : current);
      setPresetDraft("");
      setPresets(null);
      toast.success("OpenRouter key removed");
      await load();
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || "Could not remove the key");
    } finally {
      setBusy(null);
    }
  };

  const testKey = async () => {
    setBusy("test");
    setResult(null);
    try {
      const typed = keyDraft.trim();
      const data = await agentApi.testKey(typed ? { api_key: typed } : {});
      setResult(data);
      if (data.success) {
        toast.success(data.message || "OpenRouter key works");
        await load();
      } else {
        toast.error(data.message || "Key check failed");
      }
    } catch (error: any) {
      const message = error?.response?.data?.detail || "Could not reach the server to test the key";
      setResult({ success: false, message });
      toast.error(message);
    } finally {
      setBusy(null);
    }
  };

  const savePreset = async () => {
    setBusy("preset");
    try {
      const value = presetDraft.trim();
      const data = await agentApi.savePresets({ preset: value, check: Boolean(value) });
      if (!data.success) {
        toast.error(data.error || "OpenRouter did not accept that preset");
        return;
      }
      (data.warnings || []).forEach((warning) => toast(warning, { icon: "⚠️" }));
      setSettings((current) => current ? { ...current, preset: value } : current);
      setPresetDraft(value);
      toast.success(value ? `Preset “${value}” selected` : "Preset selection cleared");
      if (!compact) agentApi.health().then(setHealth).catch(() => setHealth(null));
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || "Could not save the preset");
    } finally {
      setBusy(null);
    }
  };

  const setFlag = async (patch: Partial<Pick<AgentSettings, "auto_fallback" | "auto_approve">>) => {
    setSettings((current) => current ? { ...current, ...patch } : current);
    try {
      await agentApi.saveSettings(patch);
    } catch {
      toast.error("Could not save that setting");
      await load();
    }
  };

  if (loading && !settings) {
    return <div className="card h-40 p-4"><div className="skeleton h-full w-full rounded-2xl" /></div>;
  }

  const presetOptions = presets?.items || [];
  const canSelectPreset = Boolean(presets?.available && presetOptions.length);

  return (
    <div className="space-y-3">
      <section className="card space-y-3 p-3.5" aria-labelledby="openrouter-key-heading">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <h3 id="openrouter-key-heading" className="flex items-center gap-1.5 text-[13.5px] font-semibold">
              <KeyRound size={14} /> OpenRouter API key
            </h3>
            <p className="mt-0.5 text-[11.5px] text-gray-500 dark:text-gray-400">
              One key connects the agent to the presets in your OpenRouter account.
            </p>
          </div>
          {!compact && <ProviderStatusPill provider={PROVIDER} check={health?.providers.find((item) => item.provider === PROVIDER)} />}
        </div>

        {loadError && (
          <div role="alert" className="flex items-center gap-2 rounded-lg bg-danger-50 px-3 py-2 text-[12px] text-danger-700 dark:bg-danger-950/40 dark:text-danger-300">
            <AlertTriangle size={14} /> {loadError}
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2">
          <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10.5px] font-semibold ${configured ? "bg-success-50 text-success-700 dark:bg-success-950/40 dark:text-success-300" : "bg-gray-100 text-gray-500 dark:bg-gray-800"}`}>
            {configured ? <ShieldCheck size={11} /> : <AlertTriangle size={11} />}
            {configured ? (settings?.openrouter_hint || "Key saved") : "Not set"}
          </span>
          {configured && (
            <button type="button" className="text-[11px] text-danger-600 hover:underline dark:text-danger-400" onClick={clearKey} disabled={busy === "clear"}>
              Remove key
            </button>
          )}
          <a className="ml-auto text-[11px] text-primary-600 underline decoration-dotted dark:text-primary-400" href={settings?.console_url || PROVIDER_META.console} target="_blank" rel="noreferrer">
            Manage key on OpenRouter
          </a>
        </div>

        <div className="flex flex-wrap gap-1.5">
          <div className="relative min-w-[220px] flex-1">
            <input
              aria-label="OpenRouter API key"
              className="input w-full pr-9 text-[12px]"
              type={reveal ? "text" : "password"}
              autoComplete="new-password"
              placeholder={configured ? "Paste a new key to replace the saved one" : PROVIDER_META.placeholder}
              value={keyDraft}
              onChange={(event) => setKeyDraft(event.target.value)}
            />
            <button type="button" aria-label={reveal ? "Hide key" : "Show key"} className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400" onClick={() => setReveal((value) => !value)}>
              {reveal ? <EyeOff size={14} /> : <Eye size={14} />}
            </button>
          </div>
          <button type="button" className="btn-primary btn-sm" disabled={!keyDraft.trim() || busy === "save"} onClick={saveKey}>
            {busy === "save" ? <Loader2 size={13} className="animate-spin" /> : null} Save key
          </button>
          <button type="button" className="btn-secondary btn-sm" disabled={busy === "test" || (!configured && !keyDraft.trim())} onClick={testKey}>
            {busy === "test" ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />} Test key
          </button>
        </div>
        {result && (
          <p role="status" className={`text-[11.5px] ${result.success ? "text-success-700 dark:text-success-400" : "text-danger-700 dark:text-danger-300"}`}>
            {result.message || result.error || (result.success ? "OpenRouter key works" : "Key check failed")}
          </p>
        )}
      </section>

      <section className="card space-y-3 p-3.5" aria-labelledby="openrouter-preset-heading">
        <div>
          <h3 id="openrouter-preset-heading" className="flex items-center gap-1.5 text-[13.5px] font-semibold">
            <Wand2 size={14} /> OpenRouter preset
          </h3>
          <p className="mt-0.5 text-[11.5px] text-gray-500 dark:text-gray-400">
            Select a preset. It controls the model, provider routing, and fallback behavior; there are no separate model or provider choices here.
          </p>
        </div>

        {canSelectPreset ? (
          <select
            aria-label="OpenRouter preset"
            className="input w-full text-[12px]"
            value={presetDraft}
            onChange={(event) => setPresetDraft(event.target.value)}
            disabled={!configured}
          >
            <option value="">Choose a preset…</option>
            {presetOptions.map((preset) => (
              <option key={preset.name} value={preset.name}>
                {preset.label || preset.name}{preset.label && preset.label !== preset.name ? ` (${preset.name})` : ""}
              </option>
            ))}
          </select>
        ) : (
          <div>
            <label htmlFor="openrouter-preset-slug" className="mb-1 block text-[11px] font-medium text-gray-600 dark:text-gray-300">Preset slug</label>
            <input
              id="openrouter-preset-slug"
              aria-label="OpenRouter preset slug"
              className="input w-full text-[12px]"
              value={presetDraft}
              onChange={(event) => setPresetDraft(event.target.value)}
              placeholder="my-sales-preset"
              disabled={!configured}
            />
            {configured && (
              <p className="mt-1 text-[11px] text-gray-400">
                {presets?.available
                  ? "No presets were returned for this key. Enter a preset slug to validate it."
                  : "If preset listing is unavailable for this key, enter the slug shown in your OpenRouter preset settings."}
              </p>
            )}
          </div>
        )}

        <div className="flex flex-wrap items-center justify-between gap-2">
          <a className="text-[11px] text-primary-600 underline decoration-dotted dark:text-primary-400" href={settings?.preset_console_url || PROVIDER_META.presetConsole} target="_blank" rel="noreferrer">
            Manage presets on OpenRouter
          </a>
          <button type="button" className="btn-primary btn-sm" disabled={!configured || busy === "preset" || (canSelectPreset && !presetDraft.trim() && !settings?.preset)} onClick={savePreset}>
            {busy === "preset" ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
            {presetDraft ? "Select preset" : "Clear preset"}
          </button>
        </div>
        {settings?.preset && (
          <p className="text-[11px] text-gray-500 dark:text-gray-400">Currently selected: <span className="font-mono font-medium">{settings.preset}</span></p>
        )}
      </section>

      {!compact && settings && (
        <section className="card space-y-3 p-3.5" aria-labelledby="agent-behavior-heading">
          <div>
            <h3 id="agent-behavior-heading" className="text-[13.5px] font-semibold">Agent behavior</h3>
            <p className="mt-0.5 text-[11.5px] text-gray-500 dark:text-gray-400">A selected preset keeps its own model and provider routing.</p>
          </div>
          <label className="flex items-start gap-2 text-[12px]">
            <input type="checkbox" checked={settings.auto_fallback} onChange={(event) => void setFlag({ auto_fallback: event.target.checked })} />
            <span><span className="font-medium">Automatic recovery without a preset</span><span className="block text-[11px] text-gray-500">When no preset is selected, retry once with another OpenRouter model after an unavailable model or rate limit.</span></span>
          </label>
          <label className="flex items-start gap-2 text-[12px]">
            <input type="checkbox" checked={settings.auto_approve} onChange={(event) => void setFlag({ auto_approve: event.target.checked })} />
            <span><span className="font-medium">Auto-approve agent actions</span><span className="block text-[11px] text-gray-500">Only enable if you want actions to run without a confirmation card.</span></span>
          </label>
        </section>
      )}
    </div>
  );
}
