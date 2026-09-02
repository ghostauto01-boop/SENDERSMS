import { useEffect, useState } from "react";
import api from "../api/client";
import { Automation, AutomationAction, AutomationCondition } from "../types";
import toast from "react-hot-toast";
import {
  Zap, Plus, Trash2, Pencil, FlaskConical, Sparkles, ChevronDown, ChevronUp,
} from "lucide-react";

const CONDITION_FIELDS: { value: string; label: string }[] = [
  { value: "sentiment", label: "Sentiment is" },
  { value: "intent", label: "Intent is" },
  { value: "label", label: "Reply label is" },
  { value: "has_tag", label: "Contact has tag" },
  { value: "keyword", label: "Message contains" },
  { value: "any", label: "Any reply (catch-all)" },
];

const SENTIMENT_OPTIONS = ["positive", "negative", "neutral"];
const INTENT_OPTIONS = [
  "interested", "not_interested", "wrong_number", "opt_out",
  "confirm_business_question", "pricing_question", "hours_question", "general",
];
const LABEL_OPTIONS = ["yes", "no", "positive", "negative", "neutral", "wrong_number", "interested", "not_interested", "business_question"];

const ACTION_TYPES: { value: string; label: string }[] = [
  { value: "send_sms", label: "Send an SMS" },
  { value: "stop_sequence", label: "Stop the campaign sequence (skip next messages)" },
  { value: "opt_out", label: "Opt the contact out (block future sends)" },
  { value: "add_tag", label: "Add a tag" },
  { value: "remove_tag", label: "Remove a tag" },
  { value: "set_status", label: "Set lead status" },
  { value: "delete_contact", label: "Permanently delete the contact" },
];

const EMPTY_CONDITION = (): AutomationCondition => ({ field: "sentiment", op: "is", value: "positive" });
const EMPTY_ACTION = (): AutomationAction => ({ type: "send_sms", body: "" });

function conditionSummary(c: AutomationCondition): string {
  if (c.field === "any") return "Any reply";
  if (c.field === "keyword") return `Message contains "${c.value}"`;
  if (c.field === "has_tag") return `${c.op === "is" ? "Has tag" : "Does not have tag"} "${c.value}"`;
  const label = CONDITION_FIELDS.find((f) => f.value === c.field)?.label || c.field;
  return `${label} ${c.op === "is" ? "=" : "≠"} ${c.value}`;
}

function actionSummary(a: AutomationAction): string {
  switch (a.type) {
    case "send_sms":
      return a.delay_minutes ? `Send SMS in ${a.delay_minutes} min: "${a.body?.slice(0, 40)}…"` : `Send SMS: "${a.body?.slice(0, 40)}…"`;
    case "stop_sequence": return "Stop the campaign sequence";
    case "opt_out": return "Opt the contact out";
    case "add_tag": return `Add tag "${a.value}"`;
    case "remove_tag": return `Remove tag "${a.value}"`;
    case "set_status": return `Set status to "${a.value}"`;
    case "delete_contact": return "Permanently delete the contact";
    default: return a.type;
  }
}

function AutomationModal({
  automation,
  onClose,
  onSaved,
}: {
  automation: Automation | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(automation?.name || "");
  const [description, setDescription] = useState(automation?.description || "");
  const [matchAll, setMatchAll] = useState(automation ? automation.match_all : true);
  const [conditions, setConditions] = useState<AutomationCondition[]>(
    automation?.conditions?.length ? automation.conditions : [EMPTY_CONDITION()],
  );
  const [actions, setActions] = useState<AutomationAction[]>(
    automation?.actions?.length ? automation.actions : [EMPTY_ACTION()],
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const save = async () => {
    if (!name.trim()) return setError("Give the automation a name.");
    if (!actions.length) return setError("Add at least one action.");
    setSaving(true);
    setError("");
    try {
      const payload = { name: name.trim(), description, match_all: matchAll, conditions, actions };
      if (automation) await api.put(`/automations/${automation.id}`, payload);
      else await api.post("/automations/", payload);
      onSaved();
      onClose();
    } catch (e: any) {
      setError(e.response?.data?.detail || "Could not save the automation.");
    } finally {
      setSaving(false);
    }
  };

  const updateCondition = (i: number, patch: Partial<AutomationCondition>) => {
    setConditions((cs) => cs.map((c, idx) => (idx === i ? { ...c, ...patch } : c)));
  };
  const updateAction = (i: number, patch: Partial<AutomationAction>) => {
    setActions((as) => as.map((a, idx) => (idx === i ? { ...a, ...patch } : a)));
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/50 p-0 sm:p-4">
      <div className="bg-white dark:bg-gray-800 w-full sm:max-w-2xl max-h-[92vh] overflow-y-auto rounded-t-2xl sm:rounded-xl p-4 sm:p-6">
        <h2 className="text-lg font-semibold mb-1">{automation ? "Edit Automation" : "New Automation"}</h2>
        <p className="text-xs text-gray-500 mb-4">When a contact replies, run actions — like GoHighLevel workflows.</p>

        <div className="space-y-3">
          <div>
            <label className="label">Name</label>
            <input className="input text-base sm:text-sm" placeholder="e.g. Wrong number → remove them" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div>
            <label className="label">Description (optional)</label>
            <input className="input text-base sm:text-sm" placeholder="If they say they are NOT the business, stop messaging them." value={description} onChange={(e) => setDescription(e.target.value)} />
          </div>

          <div className="rounded-xl border border-gray-200 dark:border-gray-700 p-3 space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium">Trigger</span>
              <span className="badge badge-green">When a contact replies</span>
            </div>
            <div className="flex items-center gap-2 text-sm">
              <span>Match</span>
              <select className="input text-sm py-1" value={matchAll ? "all" : "any"} onChange={(e) => setMatchAll(e.target.value === "all")}>
                <option value="all">ALL of these conditions</option>
                <option value="any">ANY of these conditions</option>
              </select>
            </div>
            {conditions.map((c, i) => (
              <div key={i} className="grid grid-cols-1 sm:grid-cols-[1fr_auto_1fr_auto] gap-2 items-center">
                <select className="input text-sm" value={c.field} onChange={(e) => updateCondition(i, { field: e.target.value, value: "" })}>
                  {CONDITION_FIELDS.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
                </select>
                {c.field !== "any" && c.field !== "keyword" && (
                  <select className="input text-sm" value={c.op} onChange={(e) => updateCondition(i, { op: e.target.value })}>
                    <option value="is">is</option>
                    <option value="is_not">is not</option>
                  </select>
                )}
                {c.field === "sentiment" && (
                  <select className="input text-sm" value={c.value} onChange={(e) => updateCondition(i, { value: e.target.value })}>
                    {SENTIMENT_OPTIONS.map((o) => <option key={o} value={o}>{o}</option>)}
                  </select>
                )}
                {c.field === "intent" && (
                  <select className="input text-sm" value={c.value} onChange={(e) => updateCondition(i, { value: e.target.value })}>
                    {INTENT_OPTIONS.map((o) => <option key={o} value={o}>{o}</option>)}
                  </select>
                )}
                {c.field === "label" && (
                  <select className="input text-sm" value={c.value} onChange={(e) => updateCondition(i, { value: e.target.value })}>
                    {LABEL_OPTIONS.map((o) => <option key={o} value={o}>{o}</option>)}
                  </select>
                )}
                {(c.field === "has_tag" || c.field === "keyword") && (
                  <input className="input text-sm" placeholder={c.field === "has_tag" ? "restaurants" : "refund, broken"} value={c.value} onChange={(e) => updateCondition(i, { value: e.target.value })} />
                )}
                <button onClick={() => setConditions((cs) => cs.filter((_, idx) => idx !== i))} className="btn-ghost btn-sm text-red-600"><Trash2 size={14} /></button>
              </div>
            ))}
            <button onClick={() => setConditions((cs) => [...cs, EMPTY_CONDITION()])} className="btn-secondary btn-sm"><Plus size={14} className="mr-1" />Add condition</button>
          </div>

          <div className="rounded-xl border border-gray-200 dark:border-gray-700 p-3 space-y-2">
            <span className="text-sm font-medium">Then do this</span>
            {actions.map((a, i) => (
              <div key={i} className="space-y-1.5">
                <div className="flex gap-2 items-center">
                  <select className="input text-sm flex-1" value={a.type} onChange={(e) => updateAction(i, { type: e.target.value })}>
                    {ACTION_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                  </select>
                  <button onClick={() => setActions((as) => as.filter((_, idx) => idx !== i))} className="btn-ghost btn-sm text-red-600"><Trash2 size={14} /></button>
                </div>
                {a.type === "send_sms" && (
                  <div className="space-y-1.5">
                    <textarea className="input text-sm min-h-[60px]" placeholder="Great! We'll send you our menu shortly, {{first_name}}." value={a.body || ""} onChange={(e) => updateAction(i, { body: e.target.value })} />
                    <div className="flex items-center gap-2 text-xs text-gray-500">
                      <span>Delay</span>
                      <select className="input text-sm py-1" value={String(a.delay_minutes || 0)} onChange={(e) => updateAction(i, { delay_minutes: Number(e.target.value) })}>
                        <option value="0">Send immediately</option>
                        <option value="5">In 5 minutes</option>
                        <option value="30">In 30 minutes</option>
                        <option value="60">In 1 hour</option>
                        <option value="1440">In 24 hours</option>
                      </select>
                    </div>
                  </div>
                )}
                {(a.type === "add_tag" || a.type === "remove_tag" || a.type === "set_status") && (
                  <input className="input text-sm" placeholder={a.type === "set_status" ? "interested" : "tag name"} value={a.value || ""} onChange={(e) => updateAction(i, { value: e.target.value })} />
                )}
              </div>
            ))}
            <button onClick={() => setActions((as) => [...as, EMPTY_ACTION()])} className="btn-secondary btn-sm"><Plus size={14} className="mr-1" />Add action</button>
          </div>

          {error && <p className="text-sm text-red-600">{error}</p>}
        </div>

        <div className="flex gap-2 mt-5">
          <button onClick={save} disabled={saving} className="btn-primary flex-1 min-h-[44px] sm:min-h-0">{saving ? "Saving..." : automation ? "Save Changes" : "Create Automation"}</button>
          <button onClick={onClose} className="btn-ghost min-h-[44px] sm:min-h-0">Cancel</button>
        </div>
      </div>
    </div>
  );
}

function AiTester() {
  const [text, setText] = useState("");
  const [result, setResult] = useState<any>(null);
  const [busy, setBusy] = useState(false);

  const run = async () => {
    setBusy(true);
    try {
      const { data } = await api.post("/automations/test", { text });
      setResult(data);
    } catch (e: any) {
      toast.error(e.response?.data?.detail || "Could not run the test");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card p-4 sm:p-5 space-y-3">
      <div className="flex items-center gap-2">
        <Sparkles size={16} className="text-primary-600" />
        <h2 className="font-semibold">Test the AI + automations</h2>
      </div>
      <p className="text-xs text-gray-500">Paste a reply a customer might send. The built-in AI (no API key needed) classifies it and shows which automations would fire.</p>
      <textarea className="input text-base sm:text-sm min-h-[70px]" placeholder='e.g. "No, wrong number"' value={text} onChange={(e) => setText(e.target.value)} />
      <button onClick={run} disabled={busy || !text.trim()} className="btn-primary w-full min-h-[44px] sm:min-h-0">{busy ? "Analysing…" : "Analyse"}</button>
      {result && (
        <div className="text-sm space-y-2">
          <div className="rounded-lg bg-gray-50 dark:bg-gray-700/50 p-3 flex flex-wrap gap-2 items-center">
            <span className={`badge ${result.classification?.sentiment === "positive" ? "badge-green" : result.classification?.sentiment === "negative" ? "badge-red" : "badge-gray"}`}>
              {result.classification?.sentiment || "?"}
            </span>
            <span className="badge badge-blue">{result.classification?.intent || "general"}</span>
            <span className="text-xs text-gray-500">confidence {Math.round((result.classification?.confidence || 0) * 100)}%</span>
          </div>
          {result.matched?.length ? (
            <div className="rounded-lg bg-green-50 dark:bg-green-900/20 p-3">
              <p className="font-medium text-green-800 dark:text-green-300">Would trigger:</p>
              {result.matched.map((m: any) => <p key={m.id} className="text-green-700 dark:text-green-200">• {m.name}</p>)}
            </div>
          ) : (
            <p className="text-xs text-gray-500">No automation matches this reply.</p>
          )}
        </div>
      )}
    </div>
  );
}

export default function AutomationsPage() {
  const [automations, setAutomations] = useState<Automation[]>([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [editing, setEditing] = useState<Automation | null>(null);
  const [open, setOpen] = useState<number | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/automations/");
      setAutomations(data.items || []);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const toggle = async (a: Automation) => {
    await api.put(`/automations/${a.id}`, { is_enabled: !a.is_enabled });
    load();
  };

  const remove = async (a: Automation) => {
    if (!confirm(`Delete the automation "${a.name}"?`)) return;
    await api.delete(`/automations/${a.id}`);
    load();
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold">Automations</h1>
          <p className="text-sm text-gray-500">Reply to your contacts automatically — the GoHighLevel way.</p>
        </div>
        <button onClick={() => { setEditing(null); setShowModal(true); }} className="btn-primary btn-sm"><Plus size={14} className="mr-1" />New Automation</button>
      </div>

      <AiTester />

      {loading ? (
        <div className="space-y-3">{[1, 2].map((i) => <div key={i} className="card p-4"><div className="skeleton h-5 w-40 mb-2" /><div className="skeleton h-12 w-full" /></div>)}</div>
      ) : automations.length === 0 ? (
        <div className="card text-center py-12 text-gray-500">
          <Zap size={48} className="mx-auto mb-3 opacity-30" />
          <p className="font-medium">No automations yet</p>
          <p className="text-sm mt-1">Create one to react to replies — e.g. remove wrong numbers before the 2nd message.</p>
          <button onClick={() => { setEditing(null); setShowModal(true); }} className="btn-primary btn-sm mt-4"><Plus size={14} className="mr-1" />Create your first automation</button>
        </div>
      ) : (
        <div className="space-y-3">
          {automations.map((a) => (
            <div key={a.id} className="card p-4">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <h3 className="font-semibold truncate">{a.name}</h3>
                    <span className={`badge text-xs ${a.is_enabled ? "badge-green" : "badge-gray"}`}>{a.is_enabled ? "Active" : "Paused"}</span>
                  </div>
                  {a.description && <p className="text-xs text-gray-500 mt-0.5">{a.description}</p>}
                </div>
                <div className="flex gap-1 shrink-0">
                  <button onClick={() => toggle(a)} className="btn-ghost btn-sm" title={a.is_enabled ? "Pause" : "Activate"}>{a.is_enabled ? "Pause" : "Activate"}</button>
                  <button onClick={() => { setEditing(a); setShowModal(true); }} className="btn-ghost btn-sm" title="Edit"><Pencil size={14} /></button>
                  <button onClick={() => remove(a)} className="btn-ghost btn-sm text-red-600" title="Delete"><Trash2 size={14} /></button>
                </div>
              </div>

              <button onClick={() => setOpen(open === a.id ? null : a.id)} className="flex items-center gap-1 text-xs text-primary-600 mt-2">
                {open === a.id ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                {open === a.id ? "Hide details" : "Show details"}
              </button>

              {open === a.id && (
                <div className="mt-2 grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
                  <div className="rounded-lg bg-gray-50 dark:bg-gray-700/40 p-3">
                    <p className="font-medium mb-1">When {a.match_all ? "ALL" : "ANY"}:</p>
                    {a.conditions?.length ? a.conditions.map((c, i) => <p key={i} className="text-gray-600 dark:text-gray-300">• {conditionSummary(c)}</p>) : <p className="text-gray-400">No conditions</p>}
                  </div>
                  <div className="rounded-lg bg-gray-50 dark:bg-gray-700/40 p-3">
                    <p className="font-medium mb-1">Then:</p>
                    {a.actions?.length ? a.actions.map((act, i) => <p key={i} className="text-gray-600 dark:text-gray-300">• {actionSummary(act)}</p>) : <p className="text-gray-400">No actions</p>}
                  </div>
                </div>
              )}

              <div className="flex gap-3 mt-3 text-xs text-gray-500">
                <span>Triggered {a.times_triggered}x</span>
                {a.last_triggered_at && <span>Last: {new Date(a.last_triggered_at).toLocaleString()}</span>}
              </div>
            </div>
          ))}
        </div>
      )}

      {showModal && <AutomationModal automation={editing} onClose={() => setShowModal(false)} onSaved={load} />}
    </div>
  );
}
