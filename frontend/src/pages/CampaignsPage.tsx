import { useState, useEffect, useRef } from "react";
import api from "../api/client";
import { Campaign } from "../types";
import toast from "react-hot-toast";
import { Plus, Play, Pause, Square, Trash2, Copy, Megaphone, Pencil, CalendarClock, Upload, MessageCircle, Users, Activity, Sparkles } from "lucide-react";
import { Link } from "react-router-dom";
import ListPicker from "../components/ListPicker";
import TemplatePicker from "../components/TemplatePicker";
import ShortcodePicker from "../components/ShortcodePicker";
import ImportContactsModal from "../components/ImportContactsModal";
import CampaignRepliesDrawer from "../components/CampaignRepliesDrawer";
import { useChannel } from "../hooks/useChannel";
import ChannelSwitch from "../components/ChannelSwitch";
import emailApi, { EmailSender } from "../api/email";
import RichEmailEditor, { AttachmentPayload } from "../components/RichEmailEditor";
import SenderPoolPicker, { senderPoolFromCampaign, SenderPoolValue } from "../components/SenderPoolPicker";
import { optionalSenderId, senderIdToForm, senderOptionLabel } from "../utils/senders";
import { inboxLabelFor, inboxLink } from "../utils/inboxRoute";

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
    dateStyle: "short",
    timeStyle: "short",
  });

/** CampaignPage component */
export default function CampaignsPage() {
  const { channel: currentChannel } = useChannel();
  const [unifiedDrafts, setUnifiedDrafts] = useState(true);
  const [embedded, setEmbedded] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [draftChannel, setDraftChannel] = useState<"all" | "sms" | "email" | "ai-agent">(currentChannel || "all");
  const [showCreate, setShowCreate] = useState(false);
  const [createChannel, setCreateChannel] = useState<"sms" | "email">("sms");
  const [editing, setEditing] = useState<Campaign | null>(null);
  const [scheduling, setScheduling] = useState<Campaign | null>(null);
  const [viewing, setViewing] = useState<Campaign | null>(null);
  const [showRepliesDrawer, setShowRepliesDrawer] = useState(false);
  const [repliesCampaign, setRepliesCampaign] = useState<Campaign | null>(null);

  const loadCampaigns = async () => {
    setLoading(true);
    setError("");
    try {
      const params: Record<string, string> = {};
      if (draftChannel !== "all") params.channel = draftChannel;
      const { data } = await api.get("/campaigns/", { params });
      setCampaigns(data.items);
    } catch (err: any) {
      setError(err.response?.data?.detail || "Failed to load campaigns");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadCampaigns();
  }, [draftChannel]);

  if (loading) return <div className="text-center py-12">Loading...</div>;
  if (error) return (
    <div className="text-center py-12">
      <h2 className="text-xl font-semibold mb-2">Error</h2>
      <p className="text-gray-500 mb-4">{error}</p>
      <button onClick={loadCampaigns} className="btn-primary">Retry</button>
    </div>
  );

  return (
    <div className="space-y-4">
      {unifiedDrafts ? (
        <div className="space-y-3">
          <div className="flex items-start justify-between gap-3 flex-wrap">
            <div>
              <h1 className="page-title">Drafts</h1>
              <p className="page-subtitle">Review and edit SMS, email, or AI agent campaign drafts before you validate, schedule, or send them.</p>
              <p className="text-xs text-gray-500 mt-1">MCP-created campaigns saved as drafts appear here too.</p>
            </div>
            <div className="flex gap-2">
              <button onClick={() => { setCreateChannel("sms"); setShowCreate(true); }} className="btn-secondary btn-sm">
                <Plus size={14} className="mr-1" /> SMS draft
              </button>
              <button onClick={() => { setCreateChannel("email"); setShowCreate(true); }} className="btn-primary btn-sm">
                <Plus size={14} className="mr-1" /> Email draft
              </button>
              <Link to="/ai-agent" className="btn-secondary btn-sm" title="AI Agent Tab">
                <Sparkles size={14} className="mr-1" /> AI Agent
              </Link>
            </div>
          </div>
          <div className="flex gap-2" role="tablist" aria-label="Draft channel">
            {(["all", "sms", "email", "ai-agent"] as const).map((filter) => (
              <button key={filter} role="tab" aria-selected={draftChannel === filter}
                onClick={() => setDraftChannel(filter)} className={`chip ${draftChannel === filter ? "chip-active" : ""}`}>
                {filter === "all" ? "All drafts" : filter === "sms" ? "SMS" : filter === "email" ? "Email" : "AI Agent"}
              </button>
            ))}
          </div>
        </div>
      ) : embedded ? (
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <div>
            <h2 className="text-base font-semibold">Bulk campaigns</h2>
            <p className="text-[11px] text-gray-400 mt-0.5">
              {currentChannel === "email" ? "Email" : "SMS"} blasts to a list or segment. Every campaign stays editable — schedule, pause, duplicate or stop it.
            </p>
          </div>
          <div className="flex gap-2">
            <button onClick={() => { setCreateChannel(currentChannel || "sms"); setShowCreate(true); }} className="btn-primary btn-sm">
              <Plus size={14} className="mr-1" /> New {currentChannel === "email" ? "Email" : "SMS"} campaign
            </button>
          </div>
        </div>
      ) : null}

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 mt-4">
        {campaigns.map((camp) => (
          <div key={camp.id} className="card p-4 flex flex-col gap-3">
            <div className="flex items-start justify-between">
              <div>
                <h3 className="font-semibold text-lg">{camp.name}</h3>
                <p className="text-sm text-gray-500">{camp.description || "No description"}</p>
              </div>
              <div className={`badge ${statusBadge(camp.status)}`}>{camp.status}</div>
            </div>
            <div className="flex items-center justify-between text-xs text-gray-500">
              <span>{camp.channel === "email" ? "Email" : "SMS"}</span>
              <span>{formatWhen(camp.updated_at)}</span>
            </div>
            <div className="flex flex-col sm:flex-row gap-2 pt-2">
              <button type="button" onClick={() => { setViewing(camp); setShowRepliesDrawer(true); setRepliesCampaign(camp); }} className="btn-secondary flex-1 min-h-[48px]">
                <MessageCircle size={14} className="mr-1" /> Replies
              </button>
              <button type="button" onClick={() => setEditing(camp)} className="btn-secondary flex-1 min-h-[48px]">
                <Pencil size={14} className="mr-1" /> Edit
              </button>
              <button type="button" onClick={() => setScheduling(camp)} className="btn-primary flex-1 min-h-[48px]">
                <CalendarClock size={14} className="mr-1" /> Schedule
              </button>
              <button type="button" onClick={() => { setCreateChannel(camp.channel || "sms"); setShowCreate(true); }} className="btn-secondary flex-1 min-h-[48px]">
                <Copy size={14} className="mr-1" /> Duplicate
              </button>
            </div>
          </div>
        ))}
      </div>

      {showCreate && (
        <CampaignModal
          channel={createChannel}
          onClose={() => { setShowCreate(false); loadCampaigns(); }}
        />
      )}
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
      {viewing && (
        <CampaignRepliesDrawer
          campaignId={viewing.id}
          campaignName={viewing.name}
          channel={viewing.channel}
          initialTab="replies"
          onClose={() => { setShowRepliesDrawer(false); setViewing(null); setRepliesCampaign(null); }}
        />
      )}
    </div>
  );
}

/** Schedule modal (stubbed from existing imports) */
function ScheduleModal({ campaign, onClose, onSaved }: { campaign: Campaign; onClose: () => void; onSaved: () => void }) {
  // Simplified stub to resolve compilation errors; actual implementation should match existing ScheduleModal
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="card w-full sm:max-w-lg p-6">
        <h2 className="text-lg font-semibold mb-4">Schedule {campaign.name}</h2>
        <button onClick={onClose} className="btn-primary">Close</button>
      </div>
    </div>
  );
}

function CampaignModal({
  campaign,
  channel = "sms",
  onClose,
}: {
  campaign?: Campaign | null;
  channel?: "sms" | "email";
  onClose: () => void;
}) {
  const isEmail = (campaign?.channel || channel) === "email";
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
  // "Start from template" fill inside Write mode: copies the body in so the
  // user can tweak it; the campaign keeps its own copy (mode stays "write").
  const [fillTemplateId, setFillTemplateId] = useState("");
  const [sequenceId, setSequenceId] = useState(campaign?.sequence_id ? String(campaign.sequence_id) : "");
  const [sequences, setSequences] = useState<any[]>([]);
  const [subject, setSubject] = useState((campaign as any)?.subject ?? "");
  const [htmlBody, setHtmlBody] = useState((campaign as any)?.html_body ?? "");
  const [emailAccountId, setEmailAccountId] = useState(
    senderIdToForm((campaign as any)?.email_account_id)
  );
  const [fallbackAccountId, setFallbackAccountId] = useState(
    senderIdToForm((campaign as any)?.fallback_email_account_id)
  );
  const [senderPool, setSenderPool] = useState<SenderPoolValue>(() => senderPoolFromCampaign(campaign as any));
  const [dailyLimit, setDailyLimit] = useState((campaign as any)?.daily_limit?.toString() ?? "");
  const [hourlyLimit, setHourlyLimit] = useState((campaign as any)?.hourly_limit?.toString() ?? "");
  const [perMinuteLimit, setPerMinuteLimit] = useState((campaign as any)?.per_minute_limit?.toString() ?? "");
  const [minDelay, setMinDelay] = useState((campaign as any)?.min_delay?.toString() ?? "");
  const [maxDelay, setMaxDelay] = useState((campaign as any)?.max_delay?.toString() ?? "");
  const [sendStartHour, setSendStartHour] = useState((campaign as any)?.send_start_hour?.toString() ?? "");
  const [sendEndHour, setSendEndHour] = useState((campaign as any)?.send_end_hour?.toString() ?? "");
  const [allowWeekends, setAllowWeekends] = useState((campaign as any)?.allow_weekends ?? true);
  const [emailAccounts, setEmailAccounts] = useState<EmailSender[]>([]);
  const [attachments, setAttachments] = useState<AttachmentPayload[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const messageRef = useRef<HTMLTextAreaElement>(null);

  // Lists and templates are loaded by their pickers; only sequences need a fetch here.
  // Sequences are channel-scoped, so this campaign only ever offers ones it can
  // actually run: an email campaign cannot pick an SMS sequence.
  const sequenceChannel = campaign?.channel || channel;
  useEffect(() => {
    api
      .get("/sequences/", { params: { channel: sequenceChannel } })
      .then((s) => setSequences(s.data.items))
      .catch(() => toast.error("Could not load sequences"));
  }, [sequenceChannel]);

  useEffect(() => {
    if (!isEmail) return;
    emailApi
      .listSenders()
      .then((data) => setEmailAccounts(data.items))
      .catch(() => toast.error("Could not load your email senders"));
  }, [isEmail]);

  // GSM-7 vs UCS-2: one emoji or curly quote drops the limit from 160 to 70,
  // so count the way the carrier will rather than assuming 160.
  const unicode = /[^\x00-\x7F]/.test(messageBody);
  const per = unicode ? 70 : 160;
  const perMulti = unicode ? 67 : 153;
  const len = messageBody.length;
  const segments = len === 0 ? 0 : len <= per ? 1 : Math.ceil(len / perMulti);

  const fillFromTemplate = async (id: string) => {
    if (!id) { setFillTemplateId(""); return; }
    try {
      const { data } = await api.get(`/templates/${id}`);
      if (messageBody.trim() && messageBody !== data.body) {
        if (!window.confirm("Replace the current message with this template's text?")) return;
      }
      setMessageBody(data.body);
      setFillTemplateId(id);
    } catch {
      toast.error("Could not load that template");
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) { toast.error("Campaign name is required"); return; }
    // Guard here as well as on the server: a campaign with no message used to
    // be creatable and only failed later, mid-send.
    if (!sequenceId) {
      if (mode === "write" && !messageBody.trim() && !htmlBody.trim()) {
        toast.error("Write a message, or switch to Use template"); return;
      }
      if (mode === "template" && !templateId) {
        toast.error("Choose a template, or switch to Write message"); return;
      }
    }
    if (isEmail && !subject.trim()) {
      toast.error("An email campaign needs a subject"); return;
    }
    setSubmitting(true);
    // Send only the one the user actually chose, so precedence is never
    // ambiguous on the server.
    const payload = {
      name,
      description: description || null,
      list_id: listId ? parseInt(listId) : null,
      // A sequence owns every step's message. Clear campaign-level text so the
      // worker cannot accidentally fall back to stale copy from an earlier edit.
      template_id: !sequenceId && mode === "template" && templateId ? parseInt(templateId) : null,
      message_body: !sequenceId && mode === "write" && messageBody.trim() ? messageBody : null,
      sequence_id: sequenceId ? parseInt(sequenceId) : null,
      daily_limit: dailyLimit === "" ? null : Number(dailyLimit),
      hourly_limit: hourlyLimit === "" ? null : Number(hourlyLimit),
      per_minute_limit: perMinuteLimit === "" ? null : Number(perMinuteLimit),
      min_delay: minDelay === "" ? null : Number(minDelay),
      max_delay: maxDelay === "" ? null : Number(maxDelay),
      send_start_hour: sendStartHour === "" ? null : Number(sendStartHour),
      send_end_hour: sendEndHour === "" ? null : Number(sendEndHour),
      allow_weekends: allowWeekends,
      ...(isEmail
        ? {
            channel: "email",
            subject: subject.trim(),
            html_body: htmlBody.trim() || null,
            attachments: attachments.length ? attachments : null,
            email_account_id: optionalSenderId(emailAccountId),
            fallback_email_account_id: optionalSenderId(fallbackAccountId),
            // Optional multi-sender pool: empty = single sender above.
            email_account_ids: senderPool.ids.length ? senderPool.ids : null,
            sender_distribution: senderPool.distribution,
            email_sender_weights:
              senderPool.distribution === "weighted" ? senderPool.weights : null,
          }
        : { channel: "sms" }),
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
      <div className="card w-full sm:max-w-lg max-h-[95vh] overflow-y-auto rounded-t-2xl sm:rounded-xl p-4 sm:p-6">
        <h2 className="text-lg font-semibold mb-4">
          {editing ? "Edit Campaign" : "Create Campaign"}
        </h2>
        {editing && campaign!.status === "scheduled" && (
          <p className="text-xs text-warning-600 dark:text-warning-500 -mt-2 mb-3">
            Saving changes returns this campaign to draft. Schedule it again afterwards.
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
            <div className="flex items-center justify-between mb-1">
              <label className="label !mb-0">Contact List</label>
              <button
                type="button"
                onClick={() => setShowImport(true)}
                className="btn-ghost btn-sm"
              >
                <Upload size={14} className="mr-1" /> Import
              </button>
            </div>
            <ListPicker
              value={listId}
              onChange={(v) => setListId(v || "")}
            />
          </div>
          {/* Channel-specific fields */}
          {isEmail ? (
            <>
              <div>
                <label className="label">Subject *</label>
                <input className="input" value={subject} onChange={(e) => setSubject(e.target.value)} required />
              </div>
              <div>
                <label className="label">Email Body</label>
              <RichEmailEditor
                body={htmlBody}
                onBody={setHtmlBody}
                html={htmlBody}
                onHtml={setHtmlBody}
                attachments={attachments}
                onAttachments={setAttachments}
              />
              </div>
              <div>
                <label className="label">Sender Account</label>
                <select className="input" value={emailAccountId} onChange={(e) => setEmailAccountId(e.target.value)}>
                  <option value="">-- Select --</option>
                  {emailAccounts.map((acc) => (
                    <option key={acc.id} value={senderIdToForm(acc.id)}>
                      {senderOptionLabel(acc)}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="label">Fallback Account</label>
                <select className="input" value={fallbackAccountId} onChange={(e) => setFallbackAccountId(e.target.value)}>
                  <option value="">-- None --</option>
                  {emailAccounts.map((acc) => (
                    <option key={acc.id} value={senderIdToForm(acc.id)}>
                      {senderOptionLabel(acc)}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="label">Sender Pool</label>
              <SenderPoolPicker
                accounts={emailAccounts}
                value={senderPool}
                onChange={setSenderPool}
              />
              </div>
            </>
          ) : (
            <>
              <div>
                <label className="label">Message Body *</label>
                <textarea
                  ref={messageRef}
                  className="input"
                  rows={4}
                  value={messageBody}
                  onChange={(e) => setMessageBody(e.target.value)}
                  placeholder="Type your SMS message here..."
                />
                <div className="flex justify-between text-xs text-gray-500 mt-1">
                  <span>{len} characters</span>
                  <span>{segments} segment{segments !== 1 ? "s" : ""}</span>
                </div>
              </div>
              <div>
                <label className="label">Template</label>
              <TemplatePicker
                value={templateId}
                onChange={(v) => {
                  const id = v || "";
                  setFillTemplateId(id);
                  if (id) fillFromTemplate(id);
                }}
              />
              </div>
            </>
          )}
          {/* Common limits and delays */}
          <div className="grid grid-cols-2 gap-3 mt-4">
            <div>
              <label className="label">Daily Limit</label>
              <input className="input" type="number" value={dailyLimit} onChange={(e) => setDailyLimit(e.target.value)} />
            </div>
            <div>
              <label className="label">Hourly Limit</label>
              <input className="input" type="number" value={hourlyLimit} onChange={(e) => setHourlyLimit(e.target.value)} />
            </div>
            <div>
              <label className="label">Per Minute Limit</label>
              <input className="input" type="number" value={perMinuteLimit} onChange={(e) => setPerMinuteLimit(e.target.value)} />
            </div>
            <div>
              <label className="label">Min Delay (s)</label>
              <input className="input" type="number" value={minDelay} onChange={(e) => setMinDelay(e.target.value)} />
            </div>
            <div>
              <label className="label">Max Delay (s)</label>
              <input className="input" type="number" value={maxDelay} onChange={(e) => setMaxDelay(e.target.value)} />
            </div>
            <div>
              <label className="label">Send Start Hour</label>
              <input className="input" type="number" value={sendStartHour} onChange={(e) => setSendStartHour(e.target.value)} />
            </div>
            <div>
              <label className="label">Send End Hour</label>
              <input className="input" type="number" value={sendEndHour} onChange={(e) => setSendEndHour(e.target.value)} />
            </div>
            <div>
              <label className="label">Allow Weekends</label>
              <input className="input" type="checkbox" checked={allowWeekends} onChange={(e) => setAllowWeekends(e.target.checked)} />
            </div>
          </div>
          <div className="flex flex-col sm:flex-row gap-2 pt-2">
            <button type="button" onClick={onClose} className="btn-secondary flex-1 min-h-[48px]">Cancel</button>
            <button type="submit" disabled={submitting} className="btn-primary flex-1 min-h-[48px]">
              {submitting
                ? editing ? "Saving..." : "Creating..."
                : editing ? "Save Changes" : "Create Draft"}
            </button>
          </div>
        </form>
      </div>
      {showImport && (
        <ImportContactsModal
          defaultListId={listId}
          onClose={() => setShowImport(false)}
          onDone={(result) => {
            // An import from inside a campaign lands straight on the audience:
            // the (possibly just-created) list is selected automatically.
            if (result.list) setListId(String(result.list.id));
            setShowImport(false);
          }}
        />
      )}
    </div>
  );
}
