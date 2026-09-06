import { useEffect, useMemo, useState } from "react";
import api from "../api/client";
import toast from "react-hot-toast";
import {
  AlertTriangle,
  Braces,
  Check,
  Copy,
  Eye,
  Loader2,
  Pencil,
  RefreshCw,
  Search,
  Trash2,
  X,
} from "lucide-react";
import type { ContactVariable, MessageAnalysis } from "../types";

const SAMPLE_BODY =
  "Hi {{first_name}}, I noticed {{business_name}} still struggles with {{pain_point}}. Worth a chat?";

export default function VariablesPage() {
  const [variables, setVariables] = useState<ContactVariable[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [sourceFilter, setSourceFilter] = useState<"all" | "standard" | "imported">("all");
  const [syncing, setSyncing] = useState(false);

  const [editing, setEditing] = useState<ContactVariable | null>(null);
  const [draft, setDraft] = useState({ label: "", shortcode: "", fallback_text: "", description: "" });
  const [saving, setSaving] = useState(false);

  const [testBody, setTestBody] = useState(SAMPLE_BODY);
  const [analysis, setAnalysis] = useState<MessageAnalysis | null>(null);
  const [analyzing, setAnalyzing] = useState(false);

  useEffect(() => {
    loadVariables();
  }, []);

  const loadVariables = async (sync = true) => {
    try {
      setLoading(true);
      setError(null);
      const { data } = await api.get("/variables/", { params: { sync } });
      setVariables(data.items);
    } catch (err: any) {
      setError(err.response?.data?.detail || "Failed to load variables");
    } finally {
      setLoading(false);
    }
  };

  const handleSync = async () => {
    try {
      setSyncing(true);
      const { data } = await api.post("/variables/sync");
      toast.success(
        data.discovered > 0
          ? `Found ${data.discovered} new variable${data.discovered === 1 ? "" : "s"}`
          : "No new variables — everything is already listed"
      );
      await loadVariables(false);
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Sync failed");
    } finally {
      setSyncing(false);
    }
  };

  const openEdit = (variable: ContactVariable) => {
    setEditing(variable);
    setDraft({
      label: variable.label,
      shortcode: variable.shortcode,
      fallback_text: variable.fallback_text || "",
      description: variable.description || "",
    });
  };

  const saveEdit = async () => {
    if (!editing) return;
    if (!draft.shortcode.trim()) {
      toast.error("A short code is required");
      return;
    }
    try {
      setSaving(true);
      await api.put(`/variables/${editing.id}`, {
        label: draft.label.trim() || editing.label,
        shortcode: draft.shortcode.trim(),
        fallback_text: draft.fallback_text.trim() || null,
        description: draft.description.trim() || null,
      });
      toast.success("Variable saved");
      setEditing(null);
      await loadVariables(false);
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not save this variable");
    } finally {
      setSaving(false);
    }
  };

  const toggleActive = async (variable: ContactVariable) => {
    try {
      await api.put(`/variables/${variable.id}`, { is_active: !variable.is_active });
      await loadVariables(false);
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not update");
    }
  };

  const handleDelete = async (variable: ContactVariable) => {
    if (!window.confirm(`Remove "${variable.label}" from the variable list? Contact data is not deleted.`))
      return;
    try {
      await api.delete(`/variables/${variable.id}`);
      toast.success("Variable removed");
      await loadVariables(false);
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not remove");
    }
  };

  const copyShortcode = (shortcode: string) => {
    const text = `{{${shortcode}}}`;
    navigator.clipboard?.writeText(text);
    toast.success(`Copied ${text}`);
  };

  const runAnalysis = async () => {
    if (!testBody.trim()) return;
    try {
      setAnalyzing(true);
      const { data } = await api.post("/variables/preview", null, {
        params: { body: testBody },
      });
      setAnalysis(data);
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not preview");
    } finally {
      setAnalyzing(false);
    }
  };

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return variables.filter((variable) => {
      if (sourceFilter !== "all" && variable.source !== sourceFilter) return false;
      if (!needle) return true;
      return (
        variable.label.toLowerCase().includes(needle) ||
        variable.shortcode.toLowerCase().includes(needle) ||
        variable.field_key.toLowerCase().includes(needle)
      );
    });
  }, [variables, search, sourceFilter]);

  const importedCount = variables.filter((v) => v.source === "imported").length;

  if (error) {
    return (
      <div className="text-center py-12">
        <h2 className="text-xl font-semibold mb-2">Error</h2>
        <p className="text-gray-500 mb-4">{error}</p>
        <button onClick={() => loadVariables()} className="btn-primary">Retry</button>
      </div>
    );
  }

  return (
    <div className="space-y-4 pb-20 lg:pb-0">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold flex items-center gap-2">
            <Braces size={22} className="text-primary-600" />
            Message Variables
          </h1>
          <p className="text-sm text-gray-500 mt-0.5">
            Every field your CSV imports have introduced. Set the short code you want to type, and it
            is replaced with that contact&apos;s information when you send.
          </p>
        </div>
        <button onClick={handleSync} disabled={syncing} className="btn-secondary whitespace-nowrap">
          {syncing ? <Loader2 size={16} className="mr-1.5 animate-spin" /> : <RefreshCw size={16} className="mr-1.5" />}
          Scan for new variables
        </button>
      </div>

      {/* How it works — short codes are only useful if the rules are visible. */}
      <div className="card p-4 bg-primary-50/60 dark:bg-primary-900/10 border-primary-100 dark:border-primary-900/40">
        <div className="flex gap-3">
          <AlertTriangle size={18} className="text-primary-600 shrink-0 mt-0.5" />
          <div className="text-sm text-gray-700 dark:text-gray-300 space-y-1">
            <p>
              Type a short code in any message as <code className="px-1 rounded bg-white dark:bg-gray-800">{"{{short_code}}"}</code>.
              Spaces and capitals are ignored, so <code className="px-1 rounded bg-white dark:bg-gray-800">{"{{Pain Point}}"}</code> and{" "}
              <code className="px-1 rounded bg-white dark:bg-gray-800">{"{{pain_point}}"}</code> both work.
            </p>
            <p>
              If a contact has <strong>no value</strong> for a short code — or the short code does not
              exist at all — it is <strong>removed from the message</strong>. Your customer never sees
              raw <code className="px-1 rounded bg-white dark:bg-gray-800">{"{{Business_name}}"}</code> text.
            </p>
            <p>Set a <strong>fallback</strong> below if you would rather substitute a default word instead of leaving a gap.</p>
          </div>
        </div>
      </div>

      {/* Tester */}
      <div className="card p-4 space-y-3">
        <div className="flex items-center gap-2">
          <Eye size={16} className="text-gray-500" />
          <h2 className="font-semibold text-sm">Test a message</h2>
        </div>
        <textarea
          value={testBody}
          onChange={(e) => setTestBody(e.target.value)}
          rows={3}
          className="input text-sm font-mono"
          placeholder="Hi {{first_name}}, about {{pain_point}}…"
        />
        <div className="flex items-center gap-2">
          <button onClick={runAnalysis} disabled={analyzing} className="btn-primary btn-sm">
            {analyzing ? <Loader2 size={14} className="mr-1 animate-spin" /> : <Eye size={14} className="mr-1" />}
            Check short codes
          </button>
          {analysis && (
            <button onClick={() => setAnalysis(null)} className="btn-secondary btn-sm">Clear</button>
          )}
        </div>
        {analysis && (
          <div className="space-y-2 text-sm">
            <div className="p-3 rounded-lg bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700">
              <p className="text-xs text-gray-500 mb-1">Sent as (with no contact selected):</p>
              <p className="whitespace-pre-wrap">{analysis.preview || <em className="text-gray-400">empty</em>}</p>
            </div>
            {analysis.unknown.length > 0 && (
              <p className="text-red-600 dark:text-red-400 flex items-start gap-1.5">
                <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                <span>
                  Not a known short code and will be removed:{" "}
                  {analysis.unknown.map((code) => `{{${code}}}`).join(", ")}
                </span>
              </p>
            )}
            {analysis.empty.length > 0 && (
              <p className="text-amber-600 dark:text-amber-400 flex items-start gap-1.5">
                <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                <span>
                  Known, but empty for this contact (removed):{" "}
                  {analysis.empty.map((item) => `{{${item.shortcode}}}`).join(", ")}
                </span>
              </p>
            )}
            {analysis.unknown.length === 0 && analysis.empty.length === 0 && (
              <p className="text-green-600 dark:text-green-400 flex items-center gap-1.5">
                <Check size={14} /> Every short code in this message resolves.
              </p>
            )}
          </div>
        )}
      </div>

      {/* Filters */}
      <div className="flex gap-2 flex-wrap items-center">
        <div className="relative flex-1 min-w-[200px]">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search variables…"
            className="input pl-9 text-sm"
          />
        </div>
        {(["all", "standard", "imported"] as const).map((value) => (
          <button
            key={value}
            onClick={() => setSourceFilter(value)}
            className={`px-3 py-1.5 rounded-lg text-sm font-medium capitalize ${
              sourceFilter === value
                ? "bg-primary-100 dark:bg-primary-900/30 text-primary-700 dark:text-primary-300"
                : "text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700"
            }`}
          >
            {value === "imported" ? `Imported (${importedCount})` : value}
          </button>
        ))}
      </div>

      {/* Table */}
      <div className="card overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[780px]">
            <thead className="bg-gray-50 dark:bg-gray-700/50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Variable</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Short code</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Example value</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Contacts</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Fallback</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
              {loading ? (
                [...Array(6)].map((_, index) => (
                  <tr key={index}>
                    {[...Array(6)].map((__, cell) => (
                      <td key={cell} className="px-4 py-3"><div className="skeleton h-4 w-full" /></td>
                    ))}
                  </tr>
                ))
              ) : filtered.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-4 py-12 text-center text-gray-500">
                    <Braces size={36} className="mx-auto mb-2 opacity-30" />
                    <p>No variables match this filter</p>
                    <p className="text-xs mt-1">Import a CSV and its columns appear here automatically.</p>
                  </td>
                </tr>
              ) : (
                filtered.map((variable) => (
                  <tr
                    key={variable.id}
                    className={`hover:bg-gray-50 dark:hover:bg-gray-700/50 ${
                      variable.is_active ? "" : "opacity-50"
                    }`}
                  >
                    <td className="px-4 py-3">
                      <p className="font-medium text-sm">{variable.label}</p>
                      <p className="text-xs text-gray-400 mt-0.5">
                        {variable.field_key}
                        <span className={`ml-2 badge ${variable.source === "standard" ? "badge-gray" : "badge-green"}`}>
                          {variable.source}
                        </span>
                      </p>
                    </td>
                    <td className="px-4 py-3">
                      <button
                        onClick={() => copyShortcode(variable.shortcode)}
                        className="inline-flex items-center gap-1.5 px-2 py-1 rounded bg-gray-100 dark:bg-gray-800 font-mono text-xs hover:bg-gray-200 dark:hover:bg-gray-700"
                        title="Copy short code"
                      >
                        {`{{${variable.shortcode}}}`}
                        <Copy size={12} className="opacity-50" />
                      </button>
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-600 dark:text-gray-300 max-w-[220px]">
                      <p className="truncate">{variable.sample_value || <span className="text-gray-400">—</span>}</p>
                    </td>
                    <td className="px-4 py-3 text-sm whitespace-nowrap">
                      {variable.contact_count > 0 ? (
                        <span>{variable.contact_count}</span>
                      ) : (
                        <span className="text-amber-600 text-xs">no data yet</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-600 dark:text-gray-300 max-w-[160px]">
                      <p className="truncate">
                        {variable.fallback_text || <span className="text-gray-400">removed if empty</span>}
                      </p>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex gap-1">
                        <button onClick={() => openEdit(variable)} className="btn-secondary btn-sm" title="Edit">
                          <Pencil size={14} />
                        </button>
                        <button
                          onClick={() => toggleActive(variable)}
                          className="btn-secondary btn-sm"
                          title={variable.is_active ? "Disable" : "Enable"}
                        >
                          {variable.is_active ? <X size={14} /> : <Check size={14} />}
                        </button>
                        {variable.source !== "standard" && (
                          <button
                            onClick={() => handleDelete(variable)}
                            className="btn-secondary btn-sm text-red-600"
                            title="Remove"
                          >
                            <Trash2 size={14} />
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Edit modal */}
      {editing && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4">
          <div className="bg-white dark:bg-gray-800 rounded-2xl w-full max-w-md p-5 space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="font-semibold">Edit “{editing.label}”</h2>
              <button onClick={() => setEditing(null)} className="text-gray-400 hover:text-gray-600">
                <X size={18} />
              </button>
            </div>

            <div className="space-y-3 text-sm">
              <div>
                <label className="text-xs font-medium text-gray-500">Display name</label>
                <input
                  value={draft.label}
                  onChange={(e) => setDraft({ ...draft, label: e.target.value })}
                  className="input mt-1"
                />
              </div>
              <div>
                <label className="text-xs font-medium text-gray-500">Short code</label>
                <input
                  value={draft.shortcode}
                  onChange={(e) => setDraft({ ...draft, shortcode: e.target.value })}
                  className="input mt-1 font-mono"
                />
                <p className="text-xs text-gray-400 mt-1">
                  You will type <code>{`{{${draft.shortcode || "…"}}}`}</code> in your messages.
                </p>
              </div>
              <div>
                <label className="text-xs font-medium text-gray-500">Fallback text (optional)</label>
                <input
                  value={draft.fallback_text}
                  onChange={(e) => setDraft({ ...draft, fallback_text: e.target.value })}
                  className="input mt-1"
                  placeholder="Leave empty to remove the short code"
                />
                <p className="text-xs text-gray-400 mt-1">
                  Used when a contact has no value. Empty means the short code is removed from the message.
                </p>
              </div>
              <div>
                <label className="text-xs font-medium text-gray-500">Note (optional)</label>
                <input
                  value={draft.description}
                  onChange={(e) => setDraft({ ...draft, description: e.target.value })}
                  className="input mt-1"
                />
              </div>
            </div>

            <div className="flex gap-2 justify-end">
              <button onClick={() => setEditing(null)} className="btn-secondary">Cancel</button>
              <button onClick={saveEdit} disabled={saving} className="btn-primary">
                {saving ? <Loader2 size={16} className="mr-1.5 animate-spin" /> : null}
                Save
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
