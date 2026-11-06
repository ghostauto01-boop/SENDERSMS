import { useCallback, useEffect, useMemo, useState } from "react";
import toast from "react-hot-toast";
import {
  AlertTriangle, Boxes, Check, ChevronDown, Eye, EyeOff, Gauge, KeyRound, Loader2,
  RefreshCw, ShieldCheck, Sparkles, Wand2, XCircle,
} from "lucide-react";
import {
  agentApi,
  type AgentHealth,
  type AgentSettings,
  type ModelOption,
  type PresetList,
  type Provider,
  type VendorOption,
} from "../../api/aiAgent";

/**
 * MODEL & KEY SETUP — one key, one provider, the whole catalogue.
 *
 * The agent used to ask for a Groq key *and* a Gemini key, each with its own
 * model list and its own way of failing. It now runs on **OpenRouter**, which
 * routes to every major lab behind a single key. Three things this screen has
 * to get right, and does:
 *
 *   1. *Which models exist?* Asked of OpenRouter, live. Nothing is typed by
 *      hand, so a retired model can never crash a chat again.
 *   2. *Which provider is behind it?* Every OpenRouter id is `vendor/model`, so
 *      the live list is grouped by vendor and the operator can pin routing to
 *      one lab. The vendor names come from OpenRouter's catalogue — a lab added
 *      next month appears here with no code change.
 *   3. *Which preset?* A preset created at openrouter.ai/settings/presets
 *      carries the system prompt and temperature the operator already tuned.
 *      Naming it here attaches it to every request; the name is checked against
 *      OpenRouter before it is saved, so a typo is caught here and not mid-chat.
 *
 * The key itself never comes back to the browser: only `sk-or-v***wxyz`-style
 * hints, which is why the input is always empty and says "paste to replace".
 */

const PROVIDER = "openrouter" as Provider;

const PROVIDER_META = {
  label: "OpenRouter",
  blurb: "One key for every major model. Pick the lab, or let OpenRouter choose.",
  console: "openrouter.ai/settings/keys",
  presetConsole: "openrouter.ai/settings/presets",
  placeholder: "sk-or-v1-…",
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
      <span className="text-gray-700 dark:text-gray-200">{PROVIDER_META.label}</span>
      <span className={tone.text}>{tone.label}</span>
    </span>
  );
}

/** Models grouped by vendor, in the order OpenRouter's own list reads. */
function groupByVendor(models: ModelOption[]): { vendor: string; models: ModelOption[] }[] {
  const buckets = new Map<string, ModelOption[]>();
  for (const model of models) {
    const vendor = model.vendor || model.id.split("/")[0] || "Other";
    const list = buckets.get(vendor) || [];
    list.push(model);
    buckets.set(vendor, list);
  }
  return [...buckets.entries()]
    .sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]))
    .map(([vendor, list]) => ({ vendor, models: list }));
}

export default function AgentModelSettings({
  compact = false,
  onConfigured,
}: {
  /** Compact hides behaviour switches; live model selection stays available. */
  compact?: boolean;
  onConfigured?: (ready: boolean) => void;
}) {
  const [settings, setSettings] = useState<AgentSettings | null>(null);
  const [health, setHealth] = useState<AgentHealth | null>(null);
  const [models, setModels] = useState<ModelOption[]>([]);
  const [vendors, setVendors] = useState<VendorOption[]>([]);
  const [presets, setPresets] = useState<PresetList | null>(null);
  const [vendorFilter, setVendorFilter] = useState<string>("");
  const [presetDraft, setPresetDraft] = useState<string>("");
  const [loadingModels, setLoadingModels] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [keyDraft, setKeyDraft] = useState("");
  const [reveal, setReveal] = useState(false);
  const [openModel, setOpenModel] = useState(false);
  const [result, setResult] = useState<any>(null);

  const load = useCallback(async (refreshModels = false) => {
    setLoadError(null);
    setLoadingModels(true);
    try {
      // Settings and the live catalogue are the useful UI. The health ping
      // spends provider quota, so compact chat settings reuse the page's
      // existing status instead of firing a second AI request.
      const s = await agentApi.settings();
      setSettings(s);
      setPresetDraft(s.preset || "");
      onConfigured?.(Boolean(s.openrouter_configured));
      if (!compact) {
        agentApi.health().then(setHealth).catch(() => setHealth(null));
      }
      if (s.openrouter_configured) {
        const data = await agentApi.models(PROVIDER, refreshModels).catch(() => null);
        if (data) {
          setModels(data.models || []);
          setVendors(data.vendors || []);
        }
        agentApi.presets().then(setPresets).catch(() => setPresets(null));
      } else {
        setModels([]);
        setVendors([]);
        setPresets(null);
      }
      setLoadingModels(false);
    } catch (error: any) {
      setLoadingModels(false);
      setLoadError(error?.response?.data?.detail || "Could not read the agent settings.");
    }
  }, [compact, onConfigured]);

  useEffect(() => {
    load();
  }, [load]);

  const configured = Boolean(settings?.openrouter_configured);
  const ready = health?.state === "ok";
  const selectedModel = settings?.models?.[PROVIDER] || null;

  const grouped = useMemo(() => {
    const filtered = vendorFilter
      ? models.filter((m) => (m.vendor || "") === vendorFilter)
      : models;
    return groupByVendor(filtered);
  }, [models, vendorFilter]);

  const saveKey = async (key?: string) => {
    setBusy("save");
    try {
      await agentApi.saveSettings({ provider: PROVIDER, api_key: key ?? keyDraft });
      setKeyDraft("");
      toast.success("OpenRouter key saved");
      await load(true);
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
      await agentApi.saveSettings({ provider: PROVIDER, clear_key: true });
      toast.success("OpenRouter key removed");
      await load();
    } finally {
      setBusy(null);
    }
  };

  /** Test uses the saved key when the input is empty. */
  const test = async () => {
    setBusy("test");
    setResult(null);
    try {
      const typed = keyDraft.trim();
      const data = await agentApi.testKey(typed ? { provider: PROVIDER, api_key: typed } : { provider: PROVIDER });
      setResult(data);
      if (data.success) {
        toast.success(data.message || "Key works");
        await load(true);
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

  const chooseModel = async (model: string) => {
    setBusy(`model-${model}`);
    try {
      await agentApi.saveSettings({ provider: PROVIDER, model });
      setSettings((s) => (s ? { ...s, models: { ...s.models, [PROVIDER]: model } } : s));
      toast.success(`Now using ${model}`);
      setOpenModel(false);
      if (!compact) agentApi.health().then(setHealth).catch(() => {});
    } catch {
      toast.error("Could not save the model");
    } finally {
      setBusy(null);
    }
  };

  const chooseVendor = async (vendor: string) => {
    setBusy("vendor");
    try {
      await agentApi.savePresets({ vendor });
      setSettings((s) => (s ? { ...s, vendor } : s));
      toast.success(vendor ? `Routing pinned to ${vendor}` : "OpenRouter will choose the provider");
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || "Could not save the provider");
    } finally {
      setBusy(null);
    }
  };

  const savePreset = async () => {
    setBusy("preset");
    try {
      const data = await agentApi.savePresets({ preset: presetDraft.trim(), check: true });
      if (!data.success) {
        toast.error(data.error || "OpenRouter did not accept that preset name");
        return;
      }
      (data.warnings || []).forEach((w) => toast(w, { icon: "⚠️" }));
      setSettings((s) => (s ? { ...s, preset: presetDraft.trim() } : s));
      toast.success(presetDraft.trim() ? `Preset “${presetDraft.trim()}” attached` : "Preset cleared");
      agentApi.presets().then(setPresets).catch(() => {});
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || "Could not save the preset");
    } finally {
      setBusy(null);
    }
  };

  const setFlag = async (patch: Partial<{ auto_fallback: boolean; auto_approve: boolean }>) => {
    setSettings((s) => (s ? { ...s, ...patch } : s));
    try {
      await agentApi.saveSettings(patch);
    } catch {
      toast.error("Could not save that setting");
      await load();
    }
  };

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
                ? "Connected and answering. Models are read live from OpenRouter."
                : !configured
                  ? "Add one OpenRouter key and the agent can run your whole workspace."
                  : health
                    ? "A key is saved, but the agent cannot reach OpenRouter right now."
                    : "Pick a model below. The agent recovers automatically if a model retires."}
            </p>
          </div>
          <button
            type="button"
            onClick={() => load(true)}
            className="btn-ghost btn-sm"
            title="Re-check the key, the catalogue and reachability"
          >
            <RefreshCw size={13} /> Re-check
          </button>
        </div>

        {loadError && (
          <p className="mt-2 flex items-center gap-1.5 text-[12.5px] text-danger-700 dark:text-danger-400">
            <AlertTriangle size={13} /> {loadError}
          </p>
        )}

        {health && (
          <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
            <ProviderStatusPill provider={PROVIDER} check={health.providers.find((c) => c.provider === PROVIDER)} />
            {settings?.preset ? (
              <span className="inline-flex items-center gap-1 rounded-full bg-accent-100 px-2.5 py-1 text-[11px] font-medium text-accent-700 dark:bg-accent-950/70 dark:text-accent-300">
                <Wand2 size={11} /> preset: {settings.preset}
              </span>
            ) : null}
            {settings?.vendor ? (
              <span className="inline-flex items-center gap-1 rounded-full bg-primary-100 px-2.5 py-1 text-[11px] font-medium text-primary-700 dark:bg-primary-950/70 dark:text-primary-300">
                <Boxes size={11} /> {settings.vendor}
              </span>
            ) : null}
            <span className="ml-auto flex items-center gap-1 text-[11px] text-gray-400">
              <Gauge size={11} /> {health.tools} tools · {health.tool_areas.length} areas
              {checkedAt ? ` · checked ${checkedAt}` : ""}
            </span>
          </div>
        )}
      </div>

      {/* ---- the key ------------------------------------------------- */}
      <div className="card p-3.5">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="flex items-center gap-1.5 text-[13.5px] font-semibold">
              <KeyRound size={13} className="text-gray-400" /> {PROVIDER_META.label}
            </p>
            <p className="mt-0.5 text-[11.5px] text-gray-500 dark:text-gray-400">{PROVIDER_META.blurb}</p>
          </div>
          <span
            className={`shrink-0 rounded-full px-2 py-0.5 text-[10.5px] font-semibold ${
              configured
                ? "bg-success-100 text-success-700 dark:bg-success-950/70 dark:text-success-300"
                : "bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400"
            }`}
          >
            {configured ? "Saved" : "Not set"}
          </span>
        </div>

        {configured && settings?.openrouter_hint && (
          <p className="mt-2 flex items-center gap-1.5 font-mono text-[11.5px] text-gray-500 dark:text-gray-400">
            <ShieldCheck size={11} className="text-success-600" /> {settings.openrouter_hint}
            <span className="font-sans text-[10.5px] text-gray-400">— encrypted, never sent to the browser</span>
          </p>
        )}

        <div className="mt-2 flex gap-1.5">
          <div className="relative min-w-0 flex-1">
            <input
              type={reveal ? "text" : "password"}
              value={keyDraft}
              onChange={(e) => setKeyDraft(e.target.value)}
              placeholder={configured ? "Paste to replace the saved key" : PROVIDER_META.placeholder}
              className="input w-full pr-8 font-mono text-[12px]"
              autoComplete="off"
              spellCheck={false}
            />
            <button
              type="button"
              onClick={() => setReveal((v) => !v)}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
              title={reveal ? "Hide" : "Show"}
            >
              {reveal ? <EyeOff size={13} /> : <Eye size={13} />}
            </button>
          </div>
          <button type="button" className="btn-secondary btn-sm" disabled={busy === "test"} onClick={test}>
            {busy === "test" ? <Loader2 size={13} className="animate-spin" /> : null} Test
          </button>
          <button
            type="button"
            className="btn-primary btn-sm"
            disabled={busy === "save" || !keyDraft.trim()}
            aria-label="Save the OpenRouter key"
            onClick={() => saveKey()}
          >
            {busy === "save" ? <Loader2 size={13} className="animate-spin" /> : null} Save
          </button>
          {configured && (
            <button type="button" className="btn-ghost btn-sm" disabled={busy === "clear"} onClick={clearKey}>
              Remove
            </button>
          )}
        </div>

        <p className="mt-1.5 text-[11px] text-gray-400">
          Create a key at{" "}
          <a
            className="text-primary-600 underline decoration-dotted dark:text-primary-400"
            href={`https://${settings?.console_url?.replace("https://", "") || PROVIDER_META.console}`}
            target="_blank"
            rel="noreferrer"
          >
            {PROVIDER_META.console}
          </a>
          .
        </p>

        {result && (
          <p
            className={`mt-2 flex items-start gap-1.5 rounded-md border p-2 text-[12px] ${
              result.success
                ? "border-success-200 bg-success-50 text-success-800 dark:border-success-900 dark:bg-success-950/40 dark:text-success-200"
                : "border-danger-200 bg-danger-50 text-danger-800 dark:border-danger-900 dark:bg-danger-950/40 dark:text-danger-200"
            }`}
          >
            {result.success ? <Check size={13} className="mt-0.5 shrink-0" /> : <XCircle size={13} className="mt-0.5 shrink-0" />}
            <span>
              {result.message}
              {result.hint ? <span className="block text-[11px] opacity-80">{result.hint}</span> : null}
            </span>
          </p>
        )}
      </div>

      {/* ---- model, grouped by provider ------------------------------ */}
      <div className="card p-3.5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="min-w-0">
            <p className="flex items-center gap-1.5 text-[13.5px] font-semibold">
              <Boxes size={13} className="text-gray-400" /> Model
            </p>
            <p className="mt-0.5 text-[11.5px] text-gray-500 dark:text-gray-400">
              {configured
                ? `${models.length} models on OpenRouter right now — grouped by the lab behind each one.`
                : "Save a key to see the live catalogue."}
            </p>
          </div>
          <div className="flex items-center gap-1.5">
            <label className="text-[11px] text-gray-400" htmlFor="vendor-filter">Provider</label>
            <select
              id="vendor-filter"
              className="input !w-auto !py-1 text-[12px]"
              value={vendorFilter}
              onChange={(e) => setVendorFilter(e.target.value)}
            >
              <option value="">All providers</option>
              {vendors.map((v) => (
                <option key={v.vendor} value={v.vendor}>
                  {v.vendor} ({v.count})
                </option>
              ))}
            </select>
          </div>
        </div>

        <button
          type="button"
          className="mt-2 flex w-full items-center justify-between gap-2 rounded-md border border-gray-200 px-2.5 py-2 text-left text-[12.5px] hover:bg-gray-50 dark:border-gray-700 dark:hover:bg-gray-800"
          onClick={() => setOpenModel((v) => !v)}
          disabled={!configured}
        >
          <span className="min-w-0 truncate font-mono">
            {selectedModel || (loadingModels ? "Loading the catalogue…" : "Choose a model")}
          </span>
          <ChevronDown size={14} className={`shrink-0 transition ${openModel ? "rotate-180" : ""}`} />
        </button>

        {openModel && (
          <div className="mt-2 max-h-80 space-y-3 overflow-y-auto rounded-md border border-gray-200 p-2 dark:border-gray-700">
            {loadingModels ? (
              <p className="flex items-center gap-1.5 p-2 text-[12px] text-gray-500">
                <Loader2 size={12} className="animate-spin" /> Reading the live catalogue…
              </p>
            ) : grouped.length === 0 ? (
              <p className="p-2 text-[12px] text-gray-500">
                {vendorFilter ? `No ${vendorFilter} models in this catalogue.` : "The catalogue is empty."}
              </p>
            ) : (
              grouped.map((group) => (
                <div key={group.vendor}>
                  <p className="sticky top-0 bg-white px-1 py-0.5 text-[10.5px] font-semibold uppercase tracking-wide text-gray-400 dark:bg-gray-900">
                    {group.vendor}
                  </p>
                  <div className="space-y-0.5">
                    {group.models.map((model) => (
                      <button
                        key={model.id}
                        type="button"
                        onClick={() => chooseModel(model.id)}
                        disabled={busy === `model-${model.id}`}
                        className={`flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-[12px] hover:bg-gray-50 disabled:opacity-60 dark:hover:bg-gray-800 ${
                          selectedModel === model.id ? "bg-primary-50 dark:bg-primary-950/40" : ""
                        }`}
                      >
                        <span className="min-w-0 flex-1 truncate font-mono">{model.id}</span>
                        {model.recommended && <span className="badge-blue !px-1.5 !py-0.5 !text-[9.5px]">PICK</span>}
                        {!model.tools && <span className="badge-gray !px-1.5 !py-0.5 !text-[9.5px]">no tools</span>}
                        {(model.input_modalities || []).includes("image") && (
                          <span className="badge-gray !px-1.5 !py-0.5 !text-[9.5px]">vision</span>
                        )}
                        {model.context ? (
                          <span className="shrink-0 text-[10.5px] text-gray-400">
                            {Math.round(model.context / 1000)}k
                          </span>
                        ) : null}
                      </button>
                    ))}
                  </div>
                </div>
              ))
            )}
          </div>
        )}

        {/* ---- provider pin + preset -------------------------------- */}
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <div>
            <label htmlFor="vendor-pin" className="text-[12px] font-semibold text-gray-600 dark:text-gray-300">
              Route through
            </label>
            <p className="mt-0.5 text-[11px] text-gray-400">
              Pin the lab. OpenRouter still runs the model you chose above.
            </p>
            <select
              id="vendor-pin"
              aria-label="Pin the lab that answers"
              className="input mt-1 w-full text-[12px]"
              value={settings?.vendor || ""}
              disabled={busy === "vendor" || !configured}
              onChange={(e) => chooseVendor(e.target.value)}
            >
              <option value="">OpenRouter chooses (default)</option>
              {vendors.map((v) => (
                <option key={v.vendor} value={v.vendor}>
                  {v.vendor}
                </option>
              ))}
            </select>
          </div>

          <div>
            <p className="flex items-center gap-1.5 text-[12px] font-semibold text-gray-600 dark:text-gray-300">
              <Wand2 size={12} /> Preset
            </p>
            <p className="mt-0.5 text-[11px] text-gray-400">
              {presets?.available
                ? "One you created on OpenRouter. It carries your system prompt and temperature."
                : "Type the preset's name — OpenRouter is not listing presets for this key, so it is checked when saved."}
            </p>
            <div className="mt-1 flex gap-1.5">
              {presets?.available && presets.items.length > 0 ? (
                <select
                  className="input min-w-0 flex-1 text-[12px]"
                  value={presetDraft}
                  onChange={(e) => setPresetDraft(e.target.value)}
                  disabled={!configured}
                >
                  <option value="">No preset</option>
                  {presets.items.map((preset) => (
                    <option key={preset.name} value={preset.name}>
                      {preset.name}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  className="input min-w-0 flex-1 text-[12px]"
                  value={presetDraft}
                  onChange={(e) => setPresetDraft(e.target.value)}
                  placeholder="my-sales-preset"
                  disabled={!configured}
                />
              )}
              <button
                type="button"
                className="btn-secondary btn-sm shrink-0"
                disabled={busy === "preset" || !configured}
                aria-label="Save the preset"
                onClick={savePreset}
              >
                {busy === "preset" ? <Loader2 size={13} className="animate-spin" /> : null} Save
              </button>
            </div>
            <a
              className="mt-1 inline-block text-[11px] text-primary-600 underline decoration-dotted dark:text-primary-400"
              href={`https://${settings?.preset_console_url?.replace("https://", "") || PROVIDER_META.presetConsole}`}
              target="_blank"
              rel="noreferrer"
            >
              {PROVIDER_META.presetConsole}
            </a>
          </div>
        </div>
      </div>

      {/* ---- behaviour switches -------------------------------------- */}
      {!compact && (
        <div className="card space-y-2 p-3.5">
          <label className="flex items-start gap-2 text-[12.5px]">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={Boolean(settings?.auto_approve)}
              onChange={(e) => setFlag({ auto_approve: e.target.checked })}
            />
            <span>
              <span className="font-medium">Run high-impact actions without asking</span>
              <span className="block text-[11px] text-gray-400">
                Off by default: sends, deletes and money movements wait for your approval.
              </span>
            </span>
          </label>
          <label className="flex items-start gap-2 text-[12.5px]">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={Boolean(settings?.auto_fallback)}
              onChange={(e) => setFlag({ auto_fallback: e.target.checked })}
            />
            <span>
              <span className="font-medium">Switch model automatically when one fails</span>
              <span className="block text-[11px] text-gray-400">
                A retired or rate-limited model is replaced from the live catalogue, and the chat
                says which one answered.
              </span>
            </span>
          </label>
        </div>
      )}
    </div>
  );
}
