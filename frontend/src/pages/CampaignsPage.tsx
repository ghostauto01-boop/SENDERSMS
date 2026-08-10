import { useState, useEffect } from "react";
import api from "../api/client";
import { Campaign } from "../types";
import toast from "react-hot-toast";
import { Plus, Play, Pause, Square, Trash2, Copy, Megaphone } from "lucide-react";

const statusBadge = (status: string) => {
  const map: Record<string, string> = {
    draft: "badge-gray", scheduled: "badge-blue", running: "badge-green",
    paused: "badge-yellow", completed: "badge-blue", stopped: "badge-red", failed: "badge-red",
  };
  return map[status] || "badge-gray";
};

export default function CampaignsPage() {
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);

  useEffect(() => { loadCampaigns(); }, []);

  const loadCampaigns = async () => {
    try {
      setLoading(true);
      const { data } = await api.get("/campaigns/");
      setCampaigns(data.items);
    } catch (err: any) {
      setError(err.response?.data?.detail || "Failed to load campaigns");
    } finally {
      setLoading(false);
    }
  };

  const handleAction = async (id: number, action: string) => {
    try {
      await api.post(`/campaigns/${id}/${action}`);
      toast.success(`Campaign ${action}ed`);
      loadCampaigns();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || `Failed to ${action} campaign`);
    }
  };

  const handleDuplicate = async (id: number) => {
    try {
      await api.post(`/campaigns/${id}/duplicate`);
      toast.success("Campaign duplicated");
      loadCampaigns();
    } catch {
      toast.error("Failed to duplicate");
    }
  };

  const handleDelete = async (id: number) => {
    if (!window.confirm("Delete this campaign?")) return;
    try {
      await api.delete(`/campaigns/${id}`);
      toast.success("Campaign deleted");
      loadCampaigns();
    } catch {
      toast.error("Failed to delete");
    }
  };

  if (error) {
    return (
      <div className="text-center py-12">
        <h2 className="text-xl font-semibold mb-2">Error</h2>
        <p className="text-gray-500 mb-4">{error}</p>
        <button onClick={loadCampaigns} className="btn-primary">Retry</button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Campaigns</h1>
        <button onClick={() => setShowCreate(true)} className="btn-primary btn-sm">
          <Plus size={14} className="mr-1" /> New Campaign
        </button>
      </div>

      <div className="space-y-3">
        {loading ? (
          [...Array(4)].map((_, i) => (
            <div key={i} className="card p-4">
              <div className="skeleton h-5 w-48 mb-2" />
              <div className="skeleton h-4 w-32" />
            </div>
          ))
        ) : campaigns.length === 0 ? (
          <div className="card p-12 text-center text-gray-500">
            <Megaphone size={48} className="mx-auto mb-2 opacity-30" />
            <p>No campaigns yet. Create your first SMS campaign.</p>
          </div>
        ) : (
          campaigns.map((camp) => (
            <div key={camp.id} className="card p-4">
              <div className="flex items-start justify-between flex-wrap gap-3">
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <h3 className="font-semibold text-lg">{camp.name}</h3>
                    <span className={statusBadge(camp.status)}>{camp.status}</span>
                  </div>
                  {camp.description && (
                    <p className="text-sm text-gray-500 mt-1">{camp.description}</p>
                  )}
                  <div className="flex gap-4 mt-2 text-sm text-gray-500">
                    <span>Sent: {camp.messages_sent}</span>
                    <span>Delivered: {camp.messages_delivered}</span>
                    <span>Failed: {camp.messages_failed}</span>
                    <span>Replies: {camp.replies}</span>
                    <span>Interested: {camp.interested}</span>
                  </div>
                </div>
                <div className="flex gap-1 flex-wrap">
                  {camp.status === "draft" && (
                    <>
                      <button onClick={() => handleAction(camp.id, "validate")} className="btn-primary btn-sm">Validate</button>
                      <button onClick={() => handleDelete(camp.id)} className="btn-ghost btn-sm text-red-600"><Trash2 size={14} /></button>
                    </>
                  )}
                  {camp.status === "scheduled" && (
                    <button onClick={() => handleAction(camp.id, "start")} className="btn-primary btn-sm"><Play size={14} className="mr-1" /> Start</button>
                  )}
                  {camp.status === "running" && (
                    <>
                      <button onClick={() => handleAction(camp.id, "pause")} className="btn-secondary btn-sm"><Pause size={14} className="mr-1" /> Pause</button>
                      <button onClick={() => handleAction(camp.id, "stop")} className="btn-danger btn-sm"><Square size={14} className="mr-1" /> Stop</button>
                    </>
                  )}
                  {camp.status === "paused" && (
                    <>
                      <button onClick={() => handleAction(camp.id, "resume")} className="btn-primary btn-sm"><Play size={14} className="mr-1" /> Resume</button>
                      <button onClick={() => handleAction(camp.id, "stop")} className="btn-danger btn-sm"><Square size={14} className="mr-1" /> Stop</button>
                    </>
                  )}
                  <button onClick={() => handleDuplicate(camp.id)} className="btn-ghost btn-sm"><Copy size={14} /></button>
                </div>
              </div>
            </div>
          ))
        )}
      </div>

      {showCreate && <CreateCampaignModal onClose={() => { setShowCreate(false); loadCampaigns(); }} />}
    </div>
  );
}
function CreateCampaignModal({ onClose }: { onClose: () => void }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [listId, setListId] = useState("");
  const [templateId, setTemplateId] = useState("");
  const [sequenceId, setSequenceId] = useState("");
  const [lists, setLists] = useState<any[]>([]);
  const [templates, setTemplates] = useState<any[]>([]);
  const [sequences, setSequences] = useState<any[]>([]);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    Promise.all([
      api.get("/lists/"),
      api.get("/templates/"),
      api.get("/sequences/"),
    ]).then(([l, t, s]) => {
      setLists(l.data.items);
      setTemplates(t.data.items);
      setSequences(s.data.items);
    });
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) { toast.error("Campaign name is required"); return; }
    setSubmitting(true);
    try {
      await api.post("/campaigns/", {
        name,
        description: description || null,
        list_id: listId ? parseInt(listId) : null,
        template_id: templateId ? parseInt(templateId) : null,
        sequence_id: sequenceId ? parseInt(sequenceId) : null,
      });
      toast.success("Campaign created (draft)");
      onClose();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Failed to create campaign");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="card p-6 w-full max-w-md">
        <h2 className="text-lg font-semibold mb-4">Create Campaign</h2>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="label">Campaign Name *</label>
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} required autoFocus />
          </div>
          <div>
            <label className="label">Description</label>
            <textarea className="input" rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />
          </div>
          <div>
            <label className="label">Contact List</label>
            <select className="input" value={listId} onChange={(e) => setListId(e.target.value)}>
              <option value="">Select list...</option>
              {lists.map((l: any) => <option key={l.id} value={l.id}>{l.name} ({l.contact_count})</option>)}
            </select>
          </div>
          <div>
            <label className="label">Template</label>
            <select className="input" value={templateId} onChange={(e) => setTemplateId(e.target.value)}>
              <option value="">Select template...</option>
              {templates.map((t: any) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          </div>
          <div>
            <label className="label">Sequence (optional)</label>
            <select className="input" value={sequenceId} onChange={(e) => setSequenceId(e.target.value)}>
              <option value="">No sequence</option>
              {sequences.map((s: any) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </div>
          <div className="flex gap-2 pt-2">
            <button type="button" onClick={onClose} className="btn-secondary flex-1">Cancel</button>
            <button type="submit" disabled={submitting} className="btn-primary flex-1">
              {submitting ? "Creating..." : "Create Draft"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
