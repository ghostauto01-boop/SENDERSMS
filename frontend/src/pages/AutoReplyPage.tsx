import { useEffect, useState } from "react";
import api from "../api/client";
import { MessageSquareReply, Plus, Trash2, Pencil, FlaskConical } from "lucide-react";

interface AutoReplyRule {
  id: number;
  name: string;
  keywords: string | null;
  match_type: string;
  reply_body: string;
  is_enabled: boolean;
  priority: number;
  cooldown_minutes: number;
  stop_on_match: boolean;
  times_triggered: number;
  last_triggered_at: string | null;
}

const MATCH_LABELS: Record<string, string> = {
  contains: "Message contains",
  exact: "Message is exactly",
  starts: "Message starts with",
  any: "Any message (catch-all)",
};

const EMPTY = {
  name: "",
  keywords: "",
  match_type: "contains",
  reply_body: "",
  is_enabled: true,
  priority: 100,
  cooldown_minutes: 240,
  stop_on_match: true,
};

function RuleModal({
  rule,
  onClose,
  onSaved,
}: {
  rule: AutoReplyRule | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [form, setForm] = useState(
    rule
      ? {
          name: rule.name,
          keywords: rule.keywords || "",
          match_type: rule.match_type,
          reply_body: rule.reply_body,
          is_enabled: rule.is_enabled,
          priority: rule.priority,
          cooldown_minutes: rule.cooldown_minutes,
          stop_on_match: rule.stop_on_match,
        }
      : EMPTY,
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const needsKeywords = form.match_type !== "any";

  const save = async () => {
    if (!form.name.trim()) return setError("Give the rule a name.");
    if (!form.reply_body.trim()) return setError("Write the reply message.");
    if (needsKeywords && !form.keywords.trim())
      return setError("Add at least one keyword, or choose the catch-all match type.");

    setSaving(true);
    setError("");
    try {
      const payload = { ...form, keywords: needsKeywords ? form.keywords : null };
      if (rule) await api.put(`/autoreply/${rule.id}`, payload);
      else await api.post("/autoreply/", payload);
      onSaved();
      onClose();
    } catch (e: any) {
      setError(e.response?.data?.detail || "Could not save the rule.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/50 p-0 sm:p-4">
      <div className="card w-full sm:max-w-lg max-h-[92vh] overflow-y-auto rounded-t-2xl sm:rounded-xl p-4 sm:p-6">
        <h2 className="text-lg font-semibold mb-4">
          {rule ? "Edit Rule" : "New Auto-Reply Rule"}
        </h2>

        <div className="space-y-3">
          <div>
            <label className="label">Rule name</label>
            <input
              className="input text-base sm:text-sm"
              placeholder="e.g. Pricing enquiry"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </div>

          <div>
            <label className="label">When an incoming message...</label>
            <select
              className="input text-base sm:text-sm"
              value={form.match_type}
              onChange={(e) => setForm({ ...form, match_type: e.target.value })}
            >
              {Object.entries(MATCH_LABELS).map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          </div>

          {needsKeywords && (
            <div>
              <label className="label">Keywords</label>
              <input
                className="input text-base sm:text-sm"
                placeholder="price, pricing, how much"
                value={form.keywords}
                onChange={(e) => setForm({ ...form, keywords: e.target.value })}
              />
              <p className="text-xs text-gray-500 mt-1">
                Separate with commas. Not case-sensitive.
              </p>
            </div>
          )}

          <div>
            <label className="label">Send this reply</label>
            <textarea
              className="input text-base sm:text-sm min-h-[100px]"
              placeholder="Hi {{first_name}}, thanks for reaching out! Our packages start at..."
              value={form.reply_body}
              onChange={(e) => setForm({ ...form, reply_body: e.target.value })}
            />
            <p className="text-xs text-gray-500 mt-1">
              You can use {"{{first_name}}"}, {"{{last_name}}"}, {"{{business_name}}"}.
            </p>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="label">Wait before replying again</label>
              <select
                className="input text-base sm:text-sm"
                value={form.cooldown_minutes}
                onChange={(e) =>
                  setForm({ ...form, cooldown_minutes: Number(e.target.value) })
                }
              >
                <option value={0}>Reply every time</option>
                <option value={60}>1 hour</option>
                <option value={240}>4 hours</option>
                <option value={720}>12 hours</option>
                <option value={1440}>24 hours</option>
                <option value={10080}>1 week</option>
              </select>
              <p className="text-xs text-gray-500 mt-1">
                Stops one person getting the same reply over and over.
              </p>
            </div>
            <div>
              <label className="label">Priority</label>
              <input
                type="number"
                className="input text-base sm:text-sm"
                value={form.priority}
                onChange={(e) => setForm({ ...form, priority: Number(e.target.value) })}
              />
              <p className="text-xs text-gray-500 mt-1">
                Lower runs first when several rules match.
              </p>
            </div>
          </div>

          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={form.is_enabled}
              onChange={(e) => setForm({ ...form, is_enabled: e.target.checked })}
            />
            Rule is active
          </label>

          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={form.stop_on_match}
              onChange={(e) => setForm({ ...form, stop_on_match: e.target.checked })}
            />
            Stop checking other rules once this one matches
          </label>

          {error && <p className="text-sm text-red-600">{error}</p>}
        </div>

        <div className="flex gap-2 mt-5">
          <button
            onClick={save}
            disabled={saving}
            className="btn-primary flex-1 min-h-[44px] sm:min-h-0"
          >
            {saving ? "Saving..." : rule ? "Save Changes" : "Create Rule"}
          </button>
          <button onClick={onClose} className="btn-ghost min-h-[44px] sm:min-h-0">
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

function TestModal({ onClose }: { onClose: () => void }) {
  const [body, setBody] = useState("");
  const [result, setResult] = useState<any>(null);
  const [busy, setBusy] = useState(false);

  const run = async () => {
    setBusy(true);
    try {
      const { data } = await api.post("/autoreply/test", { body });
      setResult(data);
    } catch {
      setResult({ error: true });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/50 p-0 sm:p-4">
      <div className="card w-full sm:max-w-md max-h-[92vh] overflow-y-auto rounded-t-2xl sm:rounded-xl p-4 sm:p-6">
        <h2 className="text-lg font-semibold mb-1">Test your rules</h2>
        <p className="text-xs text-gray-500 mb-4">
          Nothing is sent. This just shows which rule would answer.
        </p>
        <textarea
          className="input text-base sm:text-sm min-h-[80px]"
          placeholder="Type a message as if a customer sent it..."
          value={body}
          onChange={(e) => setBody(e.target.value)}
        />
        <button
          onClick={run}
          disabled={busy || !body.trim()}
          className="btn-primary w-full mt-3 min-h-[44px] sm:min-h-0"
        >
          {busy ? "Checking..." : "Check"}
        </button>

        {result && (
          <div className="mt-4 text-sm">
            {result.error ? (
              <p className="text-red-600">Could not run the test.</p>
            ) : result.matched ? (
              <div className="rounded-lg bg-green-50 dark:bg-green-900/20 p-3">
                <p className="font-medium text-green-800 dark:text-green-300">
                  Matches "{result.rule_name}"
                </p>
                <p className="mt-2 text-gray-700 dark:text-gray-300">{result.reply_body}</p>
              </div>
            ) : (
              <div className="rounded-lg bg-gray-100 dark:bg-gray-800 p-3 text-gray-600 dark:text-gray-400">
                No rule matches. Nothing would be sent.
              </div>
            )}
          </div>
        )}

        <button onClick={onClose} className="btn-ghost w-full mt-3 min-h-[44px] sm:min-h-0">
          Close
        </button>
      </div>
    </div>
  );
}

export default function AutoReplyPage() {
  const [rules, setRules] = useState<AutoReplyRule[]>([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [editing, setEditing] = useState<AutoReplyRule | null>(null);
  const [testing, setTesting] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/autoreply/");
      setRules(data.items || []);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const toggle = async (rule: AutoReplyRule) => {
    await api.put(`/autoreply/${rule.id}`, { is_enabled: !rule.is_enabled });
    load();
  };

  const remove = async (rule: AutoReplyRule) => {
    if (!confirm(`Delete the rule "${rule.name}"?`)) return;
    await api.delete(`/autoreply/${rule.id}`);
    load();
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold">Auto-Reply</h1>
          <p className="text-sm text-gray-500">
            Answer incoming messages automatically, using your own rules.
          </p>
        </div>
        <div className="flex gap-2">
          <button onClick={() => setTesting(true)} className="btn-ghost btn-sm">
            <FlaskConical size={14} className="mr-1" /> Test
          </button>
          <button
            onClick={() => {
              setEditing(null);
              setShowModal(true);
            }}
            className="btn-primary btn-sm"
          >
            <Plus size={14} className="mr-1" /> New Rule
          </button>
        </div>
      </div>

      {loading ? (
        <div className="space-y-3">
          {[1, 2].map((i) => (
            <div key={i} className="card p-4">
              <div className="skeleton h-5 w-40 mb-2" />
              <div className="skeleton h-12 w-full" />
            </div>
          ))}
        </div>
      ) : rules.length === 0 ? (
        <div className="card text-center py-12 text-gray-500">
          <MessageSquareReply size={48} className="mx-auto mb-3 opacity-30" />
          <p className="font-medium">No auto-reply rules yet</p>
          <p className="text-sm mt-1">
            Incoming messages are not answered automatically until you add a rule.
          </p>
          <button
            onClick={() => {
              setEditing(null);
              setShowModal(true);
            }}
            className="btn-primary btn-sm mt-4"
          >
            <Plus size={14} className="mr-1" /> Create your first rule
          </button>
        </div>
      ) : (
        <div className="space-y-3">
          {rules.map((rule) => (
            <div key={rule.id} className="card p-4">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <h3 className="font-semibold truncate">{rule.name}</h3>
                    <span
                      className={`badge text-xs ${
                        rule.is_enabled ? "badge-green" : "badge-gray"
                      }`}
                    >
                      {rule.is_enabled ? "Active" : "Paused"}
                    </span>
                  </div>
                  <p className="text-xs text-gray-500 mt-1">
                    {MATCH_LABELS[rule.match_type] || rule.match_type}
                    {rule.match_type !== "any" && rule.keywords ? `: ${rule.keywords}` : ""}
                  </p>
                </div>
                <div className="flex gap-1 shrink-0">
                  <button
                    onClick={() => toggle(rule)}
                    className="btn-ghost btn-sm"
                    title={rule.is_enabled ? "Pause" : "Activate"}
                  >
                    {rule.is_enabled ? "Pause" : "Activate"}
                  </button>
                  <button
                    onClick={() => {
                      setEditing(rule);
                      setShowModal(true);
                    }}
                    className="btn-ghost btn-sm"
                    title="Edit"
                  >
                    <Pencil size={14} />
                  </button>
                  <button
                    onClick={() => remove(rule)}
                    className="btn-ghost btn-sm text-red-600"
                    title="Delete"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>

              <p className="text-sm text-gray-600 dark:text-gray-400 mt-3 whitespace-pre-wrap">
                {rule.reply_body}
              </p>

              <div className="flex gap-3 mt-3 text-xs text-gray-500 flex-wrap">
                <span>Sent {rule.times_triggered}x</span>
                <span>
                  {rule.cooldown_minutes === 0
                    ? "Replies every time"
                    : `Waits ${
                        rule.cooldown_minutes >= 1440
                          ? `${Math.round(rule.cooldown_minutes / 1440)}d`
                          : `${Math.round(rule.cooldown_minutes / 60)}h`
                      } per contact`}
                </span>
                <span>Priority {rule.priority}</span>
              </div>
            </div>
          ))}
        </div>
      )}

      {showModal && (
        <RuleModal
          rule={editing}
          onClose={() => setShowModal(false)}
          onSaved={load}
        />
      )}
      {testing && <TestModal onClose={() => setTesting(false)} />}
    </div>
  );
}
