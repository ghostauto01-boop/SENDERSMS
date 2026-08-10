import { useState, useEffect } from "react";
import api from "../api/client";
import { Template } from "../types";
import toast from "react-hot-toast";
import { Plus, Trash2, Copy, FileText, Eye } from "lucide-react";

export default function TemplatesPage() {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [previewData, setPreviewData] = useState<{ preview: string; char_count: number; segment_count: number } | null>(null);

  useEffect(() => { loadTemplates(); }, []);

  const loadTemplates = async () => {
    try {
      setLoading(true);
      const { data } = await api.get("/templates/");
      setTemplates(data.items);
    } catch (err: any) {
      setError(err.response?.data?.detail || "Failed to load templates");
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async (id: number) => {
    if (!window.confirm("Delete this template?")) return;
    try {
      await api.delete(`/templates/${id}`);
      toast.success("Template deleted");
      loadTemplates();
    } catch {
      toast.error("Failed to delete");
    }
  };

  const handleDuplicate = async (id: number) => {
    try {
      await api.post(`/templates/${id}/duplicate`);
      toast.success("Template duplicated");
      loadTemplates();
    } catch {
      toast.error("Failed to duplicate");
    }
  };

  const handlePreview = async (body: string) => {
    try {
      const { data } = await api.post("/templates/preview", null, { params: { body } });
      setPreviewData(data);
    } catch {
      toast.error("Preview failed");
    }
  };

  if (error) {
    return (
      <div className="text-center py-12">
        <h2 className="text-xl font-semibold mb-2">Error</h2>
        <p className="text-gray-500 mb-4">{error}</p>
        <button onClick={loadTemplates} className="btn-primary">Retry</button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Templates</h1>
        <button onClick={() => setShowCreate(true)} className="btn-primary btn-sm">
          <Plus size={14} className="mr-1" /> New Template
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {loading ? (
          [...Array(6)].map((_, i) => (
            <div key={i} className="card p-4">
              <div className="skeleton h-5 w-32 mb-2" />
              <div className="skeleton h-16 w-full" />
            </div>
          ))
        ) : templates.length === 0 ? (
          <div className="col-span-full text-center py-12 text-gray-500">
            <FileText size={48} className="mx-auto mb-2 opacity-30" />
            <p>No templates yet. Create your first SMS template.</p>
          </div>
        ) : (
          templates.map((tpl) => (
            <div key={tpl.id} className="card p-4">
              <div className="flex items-start justify-between">
                <div>
                  <h3 className="font-semibold">{tpl.name}</h3>
                  {tpl.category && <span className="badge badge-blue text-xs mt-1">{tpl.category}</span>}
                </div>
                <div className="flex gap-1">
                  <button onClick={() => handlePreview(tpl.body)} className="btn-ghost btn-sm" title="Preview">
                    <Eye size={14} />
                  </button>
                  <button onClick={() => handleDuplicate(tpl.id)} className="btn-ghost btn-sm" title="Duplicate">
                    <Copy size={14} />
                  </button>
                  <button onClick={() => handleDelete(tpl.id)} className="btn-ghost btn-sm text-red-600" title="Delete">
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
              <p className="text-sm text-gray-600 dark:text-gray-400 mt-2 line-clamp-3">
                {tpl.body}
              </p>
              <div className="flex gap-3 mt-3 text-xs text-gray-500">
                <span>{tpl.char_count} chars</span>
                <span>{tpl.segment_count} segment{tpl.segment_count > 1 ? "s" : ""}</span>
                <span>Used {tpl.use_count}x</span>
              </div>
            </div>
          ))
        )}
      </div>

      {/* Preview Modal */}
      {previewData && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="card p-6 w-full max-w-md">
            <div className="flex justify-between items-center mb-4">
              <h2 className="text-lg font-semibold">Preview</h2>
              <button onClick={() => setPreviewData(null)} className="btn-ghost btn-sm">✕</button>
            </div>
            <div className="bg-gray-100 dark:bg-gray-700 rounded-lg p-4 mb-3">
              <p className="whitespace-pre-wrap text-sm">{previewData.preview}</p>
            </div>
            <div className="flex gap-4 text-sm text-gray-500">
              <span>{previewData.char_count} characters</span>
              <span>{previewData.segment_count} segment{previewData.segment_count > 1 ? "s" : ""}</span>
            </div>
          </div>
        </div>
      )}

      {showCreate && <CreateTemplateModal onClose={() => { setShowCreate(false); loadTemplates(); }} />}
    </div>
  );
}

function CreateTemplateModal({ onClose }: { onClose: () => void }) {
  const [name, setName] = useState("");
  const [category, setCategory] = useState("");
  const [body, setBody] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const charCount = body.length;
  const segmentCount = charCount <= 160 ? 1 : Math.ceil(charCount / 153);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !body.trim()) { toast.error("Name and body required"); return; }
    setSubmitting(true);
    try {
      await api.post("/templates/", { name, category: category || null, body });
      toast.success("Template created");
      onClose();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Failed to create template");
    } finally {
      setSubmitting(false);
    }
  };

  const insertVariable = (variable: string) => {
    setBody(body + variable);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="card p-6 w-full max-w-lg">
        <h2 className="text-lg font-semibold mb-4">Create Template</h2>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="label">Name *</label>
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} required autoFocus />
          </div>
          <div>
            <label className="label">Category</label>
            <input className="input" value={category} onChange={(e) => setCategory(e.target.value)} placeholder="e.g. outreach, follow-up" />
          </div>
          <div>
            <label className="label">Message Body *</label>
            <textarea
              className="input"
              rows={5}
              value={body}
              onChange={(e) => setBody(e.target.value)}
              placeholder="Type your SMS message..."
              required
            />
            <div className="flex justify-between mt-1">
              <div className="flex gap-1 flex-wrap">
                {["{{first_name}}", "{{business_name}}", "{{city}}", "{{state}}", "{{website}}", "{{industry}}"].map((v) => (
                  <button
                    key={v}
                    type="button"
                    onClick={() => insertVariable(v)}
                    className="text-xs px-2 py-0.5 bg-gray-100 dark:bg-gray-700 rounded hover:bg-gray-200 dark:hover:bg-gray-600"
                  >
                    {v}
                  </button>
                ))}
              </div>
              <span className="text-xs text-gray-500">
                {charCount} chars · {segmentCount} segment{segmentCount > 1 ? "s" : ""}
              </span>
            </div>
          </div>
          <div className="flex gap-2 pt-2">
            <button type="button" onClick={onClose} className="btn-secondary flex-1">Cancel</button>
            <button type="submit" disabled={submitting} className="btn-primary flex-1">
              {submitting ? "Creating..." : "Create"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
