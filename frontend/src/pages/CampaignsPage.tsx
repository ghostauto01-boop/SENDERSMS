import { useState, useEffect } from "react";
import api from "../api/client";
import { Campaign } from "../types";
import toast from "react-hot-toast";
import { Plus, Play, Pause, Square, Trash2, Copy, Megaphone, Pencil, CalendarClock } from "lucide-react";

const statusBadge = (status: string) => {
  const map: Record<string, string> = {
    draft: "badge-gray", scheduled: "badge-blue", running: "badge-green",
    paused: "badge-yellow", completed: "badge-blue", stopped: "badge-red", failed: "badge-red",
  };
  return map[status] || "badge-gray";
};

/** Format a stored UTC timestamp in the user's own timezone. */
const formatWhen = (iso: string) =>
  new Date(iso).toLocaleString(undefined, {
    weekday: "short", day: "numeric", month: "short",
    hour: "2-digit", minute: "2-digit",
  });

/** A UTC ISO string as the value a datetime-local input expects (local time). */
const toLocalInput = (iso: string | null) => {
  const d = iso ? new Date(iso) : new Date(Date.now() + 60 * 60 * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
};

function ScheduleModal({
  campaign, onClose, onSaved,
}: {
  campaign: Campaign;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [when, setWhen] = useState(toLocalInput(campaign.scheduled_start_at));
  const [saving, setSaving] = useState(false);

  const submit = async (clear: boolean) => {
    setSaving(true);
    try {
      // new Date(local string).toISOString() converts to UTC, so a campaign
      // set for 9am is sent at 9am where the user is, not 9am UTC.
      const payload = clear
        ? { scheduled_start_at: null }
        : { scheduled_start_at: new Date(when).toISOString() };
      const { data } = await api.post(`/campaigns/${campaign.id}/schedule`, payload);
      toast.success(data.message || "Schedule updated");
      onSaved();
      onClose();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || "Could not schedule the campaign");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/50 p-0 sm:p-4">
      <div className="card w-full sm:max-w-md max-h-[92vh] overflow-y-auto rounded-t-2xl sm:rounded-xl p-4 sm:p-6">
        <h2 className="text-lg font-semibold mb-1">Schedule Campaign</h2>
        <p className="text-xs text-gray-500 mb-4">
          "{campaign.name}" will send by itself at the time you pick. Times are in
          your local timezone.
        </p>

        <label className="label">Send at</label>
        <input
          type="datetime-local"
          className="input text-base sm:text-sm"
          value={when}
          min={toLocalInput(null)}
          onChange={(e) => setWhen(e.target.value)}
        />

        {campaign.scheduled_start_at && (
          <p className="text-xs text-blue-600 dark:text-blue-400 mt-2">
            Currently scheduled for {formatWhen(campaign.scheduled_start_at)}.
          </p>
        )}

        <div className="flex gap-2 mt-5">
          <button
            onClick={() => submit(false)}
            disabled={saving || !when}
            className="btn-primary flex-1 min-h-[44px] sm:min-h-0"
          >
            {saving ? "Saving..." : "Schedule"}
          </button>
          {campaign.scheduled_start_at && (
            <button
              onClick={() => submit(true)}
              disabled={saving}
              className="btn-secondary min-h-[44px] sm:min-h-0"
            >
              Cancel schedule
            </button>
          )}
          <button onClick={onClose} className="btn-ghost min-h-[44px] sm:min-h-0">
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

export default function CampaignsPage() {
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState<Campaign | null>(null);
  const [scheduling, setScheduling] = useState<Campaign | null>(null);

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
      const { data } = await api.post(`/campaigns/${id}/duplicate`);
      toast.success(`Created "${data.name}"`);
      await loadCampaigns();
      // Open the copy straight away: duplicating is nearly always the first
      // half of "duplicate and change something".
      setEditing(data);
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Failed to duplicate");
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
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <h1 className="text-xl sm:text-2xl font-bold">Campaigns</h1>
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
                  {camp.scheduled_start_at && camp.status === "scheduled" && (
                    <p className="text-sm text-blue-600 dark:text-blue-400 mt-1 flex items-center gap-1">
                      <CalendarClock size={14} />
                      Sends automatically on {formatWhen(camp.scheduled_start_at)}
                    </p>
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
                    <button onClick={() => handleAction(camp.id, "start")} className="btn-primary btn-sm"><Play size={14} className="mr-1" /> Start now</button>
                  )}
                  {(camp.status === "draft" || camp.status === "scheduled") && (
                    <button
                      onClick={() => setScheduling(camp)}
                      className="btn-secondary btn-sm"
                      title="Send automatically at a chosen time"
                    >
                      <CalendarClock size={14} className="mr-1" />
                      {camp.scheduled_start_at ? "Reschedule" : "Schedule"}
                    </button>
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
                  {(camp.status === "draft" || camp.status === "scheduled") && (
                    <button onClick={() => setEditing(camp)} className="btn-ghost btn-sm" title="Edit campaign">
                      <Pencil size={14} />
                    </button>
                  )}
                  <button onClick={() => handleDuplicate(camp.id)} className="btn-ghost btn-sm" title="Duplicate campaign">
                    <Copy size={14} />
                  </button>
                </div>
              </div>
            </div>
          ))
        )}
      </div>

      {showCreate && <CampaignModal onClose={() => { setShowCreate(false); loadCampaigns(); }} />}
      {editing && (
        <CampaignModal
          key={editing.id}
          campaign={editing}
          onClose={() => { setEditing(null); loadCampaigns(); }}
        />
      )}
      {scheduling && (
        <ScheduleModal
          key={`sched-${scheduling.id}`}
          campaign={scheduling}
          onClose={() => setScheduling(null)}
          onSaved={loadCampaigns}
        />
      )}
    </div>
  );
}
function CampaignModal({ campaign, onClose }: { campaign?: Campaign | null; onClose: () => void }) {
  // One modal for both create and edit: the fields, the validation and the
  // segment counter are identical, and keeping two copies in sync is how they
  // drift apart.
  const editing = !!campaign;
  const [name, setName] = useState(campaign?.name ?? "");
  const [description, setDescription] = useState(campaign?.description ?? "");
  const [listId, setListId] = useState(campaign?.list_id ? String(campaign.list_id) : "");
  // "write" = compose the message here; "template" = reuse a saved one.
  const [mode, setMode] = useState<"write" | "template">(
    campaign?.template_id && !campaign?.message_body ? "template" : "write"
  );
  const [messageBody, setMessageBody] = useState(campaign?.message_body ?? "");
  const [templateId, setTemplateId] = useState(campaign?.template_id ? String(campaign.template_id) : "");
  const [sequenceId, setSequenceId] = useState(campaign?.sequence_id ? String(campaign.sequence_id) : "");
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
    }).catch(() => toast.error("Could not load lists and templates"));
  }, []);

  // GSM-7 vs UCS-2: one emoji or curly quote drops the limit from 160 to 70,
  // so count the way the carrier will rather than assuming 160.
  const unicode = /[^\x00-\x7F]/.test(messageBody);
  const per = unicode ? 70 : 160;
  const perMulti = unicode ? 67 : 153;
  const len = messageBody.length;
  const segments = len === 0 ? 0 : len <= per ? 1 : Math.ceil(len / perMulti);

  const insertVar = (v: string) => setMessageBody((b) => b + "{{" + v + "}}");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) { toast.error("Campaign name is required"); return; }
    // Guard here as well as on the server: a campaign with no message used to
    // be creatable and only failed later, mid-send.
    if (!sequenceId) {
      if (mode === "write" && !messageBody.trim()) {
        toast.error("Write a message, or switch to Use template"); return;
      }
      if (mode === "template" && !templateId) {
        toast.error("Choose a template, or switch to Write message"); return;
      }
    }
    setSubmitting(true);
    // Send only the one the user actually chose, so precedence is never
    // ambiguous on the server.
    const payload = {
      name,
      description: description || null,
      list_id: listId ? parseInt(listId) : null,
      template_id: mode === "template" && templateId ? parseInt(templateId) : null,
      message_body: mode === "write" && messageBody.trim() ? messageBody : null,
      sequence_id: sequenceId ? parseInt(sequenceId) : null,
    };
    try {
      if (editing) {
        await api.put(`/campaigns/${campaign!.id}`, payload);
        toast.success("Campaign updated");
      } else {
        await api.post("/campaigns/", payload);
        toast.success("Campaign created (draft)");
      }
      onClose();
    } catch (err: any) {
      toast.error(
        err.response?.data?.detail ||
          (editing ? "Failed to update campaign" : "Failed to create campaign")
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/50 p-0 sm:p-4">
      <div className="card w-full sm:max-w-lg max-h-[92vh] overflow-y-auto rounded-t-2xl sm:rounded-xl p-4 sm:p-6">
        <h2 className="text-lg font-semibold mb-4">
          {editing ? "Edit Campaign" : "Create Campaign"}
        </h2>
        {editing && campaign!.status === "scheduled" && (
          <p className="text-xs text-amber-600 dark:text-amber-500 -mt-2 mb-3">
            Saving changes returns this campaign to draft, so it must be validated again.
          </p>
        )}
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

          {/* Message: write from scratch, or reuse a saved template. */}
          <div>
            <label className="label">Message</label>
            <div className="flex gap-1 p-1 mb-2 rounded-lg bg-gray-100 dark:bg-gray-700">
              <button type="button" onClick={() => setMode("write")}
                className={`flex-1 px-3 py-2 text-sm rounded-md font-medium transition-colors ${mode === "write" ? "bg-white dark:bg-gray-800 shadow-sm text-primary-600" : "text-gray-600 dark:text-gray-300"}`}>
                Write message
              </button>
              <button type="button" onClick={() => setMode("template")}
                className={`flex-1 px-3 py-2 text-sm rounded-md font-medium transition-colors ${mode === "template" ? "bg-white dark:bg-gray-800 shadow-sm text-primary-600" : "text-gray-600 dark:text-gray-300"}`}>
                Use template
              </button>
            </div>

            {mode === "write" ? (
              <>
                <textarea
                  className="input font-mono text-sm"
                  rows={5}
                  value={messageBody}
                  onChange={(e) => setMessageBody(e.target.value)}
                  placeholder={"Hi {{first_name}}, ..."}
                />
                <div className="flex flex-wrap items-center gap-1.5 mt-2">
                  <span className="text-xs text-gray-500 mr-1">Insert:</span>
                  {["first_name", "last_name", "business_name", "city"].map((v) => (
                    <button key={v} type="button" onClick={() => insertVar(v)}
                      className="px-2 py-1 text-xs rounded-md bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 font-mono">
                      {"{{" + v + "}}"}
                    </button>
                  ))}
                </div>
                <p className="text-xs text-gray-400 mt-2">
                  {len} chars · {segments} SMS{segments === 1 ? "" : "s"}
                  {unicode && " · unicode (70/SMS)"}
                  {segments > 3 && " · long messages cost more"}
                </p>
              </>
            ) : (
              <>
                <select className="input" value={templateId} onChange={(e) => setTemplateId(e.target.value)}>
                  <option value="">Select template...</option>
                  {templates.map((t: any) => <option key={t.id} value={t.id}>{t.name}</option>)}
                </select>
                {templates.length === 0 && (
                  <p className="text-xs text-gray-400 mt-1">
                    No saved templates yet — switch to "Write message".
                  </p>
                )}
                {templateId && (
                  <p className="text-xs text-gray-500 mt-2 whitespace-pre-wrap break-words">
                    {templates.find((t: any) => String(t.id) === templateId)?.body}
                  </p>
                )}
              </>
            )}
          </div>

          <div>
            <label className="label">Sequence (optional)</label>
            <select className="input" value={sequenceId} onChange={(e) => setSequenceId(e.target.value)}>
              <option value="">No sequence</option>
              {sequences.map((s: any) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
            {sequenceId && (
              <p className="text-xs text-gray-400 mt-1">
                The sequence controls the messages; the text above is ignored.
              </p>
            )}
          </div>

          <div className="flex flex-col-reverse sm:flex-row gap-2 pt-2">
            <button type="button" onClick={onClose} className="btn-secondary flex-1">Cancel</button>
            <button type="submit" disabled={submitting} className="btn-primary flex-1">
              {submitting
                ? editing ? "Saving..." : "Creating..."
                : editing ? "Save Changes" : "Create Draft"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
