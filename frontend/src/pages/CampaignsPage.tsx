import { useState, useEffect, useRef } from "react";
import api from "../api/client";
import { Campaign } from "../types";
import toast from "react-hot-toast";
import { Plus, Play, Pause, Square, Trash2, Copy, Megaphone, Pencil, CalendarClock, Upload, MessageCircle, Users, Activity } from "lucide-react";
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
          <p className="text-xs text-primary-600 dark:text-primary-400 mt-2">
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

/**
 * Classic campaigns (list, schedule, pause, duplicate, edit, replies, leads).
 *
 * This is no longer a destination of its own: the Campaigns tab is gone, and
 * the same, fully editable list is rendered inside SMS Manager and Email
 * Manager, scoped to that manager's channel. Existing deep links such as
 * ``/campaigns?campaign=12`` still work because the page is mounted there.
 */
export default function CampaignsPage({
  embedded = false,
  fixedChannel,
  unifiedDrafts = false,
}: {
  /** Rendered as a panel inside a manager: no page heading, no channel switch. */
  embedded?: boolean;
  /** Pin the list to one channel instead of following the app-wide switch. */
  fixedChannel?: "sms" | "email";
  /** One review page for SMS and email campaign drafts. */
  unifiedDrafts?: boolean;
} = {}) {
  const { channel: activeChannel } = useChannel();
  const [draftChannel, setDraftChannel] = useState<"all" | "sms" | "email">("all");
  const [createChannel, setCreateChannel] = useState<"sms" | "email">("sms");
  const channel = unifiedDrafts ? draftChannel : fixedChannel ?? activeChannel;
  const modalChannel: "sms" | "email" = unifiedDrafts
    ? createChannel
    : channel === "email" ? "email" : "sms";
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState<Campaign | null>(null);
  const [scheduling, setScheduling] = useState<Campaign | null>(null);
  // Which campaign's leads/replies drawer is open, and which tab it opened on.
  const [viewing, setViewing] = useState<{ campaign: Campaign; tab: "replies" | "leads" } | null>(null);
  // Live, inbox-derived counts per campaign id. The counters on the campaign
  // row are webhook-maintained and can drift; these come from the same tables
  // the inbox renders, so the "N replies" button never lies.
  const [live, setLive] = useState<Record<number, any>>({});

  useEffect(() => { loadCampaigns(); }, [channel]);

  const loadCampaigns = async () => {
    try {
      setLoading(true);
      setError(null);
      const { data } = await api.get("/campaigns/", {
        params: { channel, ...(unifiedDrafts ? { status: "draft" } : {}) },
      });
      setCampaigns(data.items);
      // Best-effort: the page is fully usable without it.
      api.get("/overview/campaigns")
        .then(({ data: ov }) => {
          const byId: Record<number, any> = {};
          (ov.items || [])
            .filter((i: any) => i.kind === "campaign")
            .forEach((i: any) => { byId[i.id] = i; });
          setLive(byId);
        })
        .catch(() => {});
    } catch (err: any) {
      const status = err.response?.status;
      const detail = err.response?.data?.detail;
      if (!err.response) {
        setError("Can't reach the server. Check your connection and try again.");
      } else if (status >= 500) {
        setError(
          "The server hit an error loading campaigns. If this just started " +
            "after an update, the database may be missing a recent change — " +
            "restarting the service applies it automatically.",
        );
      } else {
        setError(detail || "Failed to load campaigns");
      }
    } finally {
      setLoading(false);
    }
  };

  const handleAction = async (id: number, action: string) => {
    if (unifiedDrafts && action === "start") {
      const campaign = campaigns.find((item) => item.id === id);
      const channelLabel = campaign?.channel === "email" ? "email" : "SMS";
      if (!window.confirm(`Send “${campaign?.name || "this campaign"}” to its selected audience now by ${channelLabel}?`)) return;
    }
    try {
      const { data } = await api.post(`/campaigns/${id}/${action}`);
      // The server says what actually happened (and when nothing did).
      toast.success(data?.message || `Campaign ${action} done`);
      loadCampaigns();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || `Failed to ${action} campaign`);
    }
  };

  /** Resume. Nothing stands in the way: a high bounce rate is reported, never enforced. */
  const handleResume = async (camp: Campaign) => {
    try {
      const { data } = await api.post(`/campaigns/${camp.id}/resume`);
      toast.success(data?.message || "Campaign resumed");
      loadCampaigns();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Failed to resume campaign");
    }
  };

  /** Validate is a report. It never changes the campaign, however often it is clicked. */
  const handleValidate = async (id: number) => {
    try {
      const { data } = await api.post(`/campaigns/${id}/validate`);
      if (data.valid) {
        toast.success(data.message || "Campaign is valid. Nothing was changed.");
      } else {
        toast.error((data.errors || []).join("\n") || data.message || "Validation found problems", {
          duration: 9000,
        });
      }
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not validate the campaign");
    }
  };

  /** Cancel a schedule: the campaign goes back to draft and will not launch by itself. */
  const handleUnschedule = async (id: number) => {
    try {
      const { data } = await api.post(`/campaigns/${id}/schedule`, { scheduled_start_at: null });
      toast.success(data?.message || "Schedule cancelled");
      loadCampaigns();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not cancel the schedule");
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
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Failed to delete");
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
      {unifiedDrafts ? (
        <div className="space-y-3">
          <div className="flex items-start justify-between gap-3 flex-wrap">
            <div>
              <h1 className="page-title">Drafts</h1>
              <p className="page-subtitle">Review and edit SMS or email campaign drafts before you validate, schedule, or send them.</p>
              <p className="text-xs text-gray-500 mt-1">MCP-created campaigns saved as drafts appear here too.</p>
            </div>
            <div className="flex gap-2">
              <button onClick={() => { setCreateChannel("sms"); setShowCreate(true); }} className="btn-secondary btn-sm">
                <Plus size={14} className="mr-1" /> SMS draft
              </button>
              <button onClick={() => { setCreateChannel("email"); setShowCreate(true); }} className="btn-primary btn-sm">
                <Plus size={14} className="mr-1" /> Email draft
              </button>
            </div>
          </div>
          <div className="flex gap-2" role="tablist" aria-label="Draft channel">
            {(["all", "sms", "email"] as const).map((filter) => (
              <button key={filter} role="tab" aria-selected={draftChannel === filter}
                onClick={() => setDraftChannel(filter)} className={`chip ${draftChannel === filter ? "chip-active" : ""}`}>
                {filter === "all" ? "All drafts" : filter === "sms" ? "SMS" : "Email"}
              </button>
            ))}
          </div>
        </div>
      ) : embedded ? (
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <div>
            <h2 className="text-base font-semibold">Bulk campaigns</h2>
            <p className="text-[11px] text-gray-400 mt-0.5">
              {channel === "email" ? "Email" : "SMS"} blasts to a list or segment. Every campaign stays editable — schedule, pause, duplicate or stop it.
            </p>
          </div>
          <div className="flex gap-2">
            <button onClick={() => setShowCreate(true)} className="btn-primary btn-sm">
              <Plus size={14} className="mr-1" /> New campaign
            </button>
          </div>
        </div>
      ) : (
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <h1 className="page-title flex items-center gap-2.5">
            Campaigns
            <ChannelSwitch size="sm" />
          </h1>
          <div className="flex gap-2">
            <Link to="/overview" className="btn-secondary btn-sm" title="All campaigns and inbox activity in one view">
              <Activity size={14} className="mr-1" /> Overview
            </Link>
            <button onClick={() => setShowCreate(true)} className="btn-primary btn-sm">
              <Plus size={14} className="mr-1" /> New Campaign
            </button>
          </div>
        </div>
      )}

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
            <p>
              {unifiedDrafts
                ? `No ${channel === "all" ? "" : channel === "email" ? "email " : "SMS "}drafts yet. Create a draft or ask your MCP assistant to prepare one.`
                : `No ${channel === "email" ? "email" : "SMS"} campaigns yet. Create your first one.`}
            </p>
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
                    <p className="text-sm text-primary-600 dark:text-primary-400 mt-1 flex items-center gap-1">
                      <CalendarClock size={14} />
                      Sends automatically on {formatWhen(camp.scheduled_start_at)}
                    </p>
                  )}
                  {camp.scheduled_start_at && camp.status === "draft" && (
                    <p className="text-sm text-gray-500 mt-1 flex items-center gap-1">
                      <CalendarClock size={14} />
                      Launch time {formatWhen(camp.scheduled_start_at)} is saved but the campaign is
                      not scheduled yet. Click Schedule to arm it.
                    </p>
                  )}
                  {camp.status === "paused" && (
                    <p className="text-sm text-warning-600 dark:text-warning-500 mt-1">
                      {camp.paused_from === "scheduled" ? "Scheduled launch on hold. " : ""}
                      {camp.paused_reason || ""}
                    </p>
                  )}
                  {(() => {
                    // Prefer the inbox-derived numbers when we have them.
                    const l = live[camp.id];
                    const sent = l?.sent ?? camp.messages_sent;
                    const delivered = l?.delivered ?? camp.messages_delivered;
                    const failed = l?.failed ?? camp.messages_failed;
                    const replies = l?.replied ?? camp.replies;
                    const interested = l?.interested ?? camp.interested;
                    return (
                      <>
                        <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2 text-sm text-gray-500">
                          <span>Sent: {sent}</span>
                          <span>Delivered: {delivered}</span>
                          <span className={failed > 0 ? "text-danger-600" : ""}>Failed: {failed}</span>
                          <span>Replies: {replies}</span>
                          <span>Interested: {interested}</span>
                          {l?.reply_rate > 0 && <span>Reply rate: {l.reply_rate}%</span>}
                        </div>

                        {/* Drill-down: leads this campaign produced, and the
                            replies. Each row inside opens that exact chat. */}
                        <div className="flex flex-wrap gap-2 mt-3">
                          <button
                            onClick={() => setViewing({ campaign: camp, tab: "replies" })}
                            disabled={!replies}
                            className="btn-secondary btn-sm disabled:opacity-40 disabled:cursor-not-allowed"
                            title={replies ? "Read the replies and jump into any chat" : "No replies yet"}
                          >
                            <MessageCircle size={13} className="mr-1" />
                            {replies} {replies === 1 ? "reply" : "replies"}
                          </button>
                          <button
                            onClick={() => setViewing({ campaign: camp, tab: "leads" })}
                            disabled={!(l?.leads ?? 0)}
                            className="btn-ghost btn-sm disabled:opacity-40 disabled:cursor-not-allowed"
                            title="Every lead this campaign produced"
                          >
                            <Users size={13} className="mr-1" /> {l?.leads ?? 0} leads
                          </button>
                          {(l?.unread ?? 0) > 0 && (
                            <Link
                              // The campaign's own channel decides which inbox
                              // this opens: an email campaign has no SMS thread.
                              to={inboxLink(camp.channel ?? l?.channel ?? channel, {
                                campaign_id: camp.id,
                              })}
                              className="btn-ghost btn-sm text-warning-600"
                              title={`Unread replies from this campaign — opens the ${inboxLabelFor(
                                camp.channel ?? l?.channel ?? channel
                              )}`}
                            >
                              {l.unread} unread
                            </Link>
                          )}
                        </div>
                      </>
                    );
                  })()}
                </div>
                <div className="flex gap-1 flex-wrap">
                  {camp.status === "draft" && (
                    <>
                      <button
                        onClick={() => handleValidate(camp.id)}
                        className="btn-secondary btn-sm"
                        title="Check the campaign. Changes nothing."
                      >
                        Validate
                      </button>
                      <button onClick={() => handleAction(camp.id, "start")} className="btn-primary btn-sm" title="Validates, then sends now">
                        <Play size={14} className="mr-1" /> Start now
                      </button>
                      <button onClick={() => handleDelete(camp.id)} className="btn-ghost btn-sm text-danger-600" title="Delete draft"><Trash2 size={14} /></button>
                    </>
                  )}
                  {camp.status === "scheduled" && (
                    <>
                      <button onClick={() => handleAction(camp.id, "start")} className="btn-primary btn-sm"><Play size={14} className="mr-1" /> Start now</button>
                      <button onClick={() => handleAction(camp.id, "pause")} className="btn-secondary btn-sm" title="Hold the scheduled launch"><Pause size={14} className="mr-1" /> Pause</button>
                      <button onClick={() => handleUnschedule(camp.id)} className="btn-ghost btn-sm" title="Cancel the schedule and go back to draft">Cancel schedule</button>
                      {(camp.messages_sent || 0) === 0 && (
                        <button onClick={() => handleDelete(camp.id)} className="btn-ghost btn-sm text-danger-600" title="Delete campaign"><Trash2 size={14} /></button>
                      )}
                    </>
                  )}
                  {(camp.status === "draft" || camp.status === "scheduled") && (
                    <button
                      onClick={() => setScheduling(camp)}
                      className="btn-secondary btn-sm"
                      title="Send automatically at a chosen time"
                    >
                      <CalendarClock size={14} className="mr-1" />
                      {camp.status === "scheduled" ? "Reschedule" : "Schedule"}
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
                      <button onClick={() => handleResume(camp)} className="btn-primary btn-sm"><Play size={14} className="mr-1" /> Resume</button>
                      <button onClick={() => handleAction(camp.id, "stop")} className="btn-danger btn-sm"><Square size={14} className="mr-1" /> Stop</button>
                      {camp.paused_from === "scheduled" && (camp.messages_sent || 0) === 0 && (
                        <button onClick={() => handleDelete(camp.id)} className="btn-ghost btn-sm text-danger-600" title="Delete campaign"><Trash2 size={14} /></button>
                      )}
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

      {showCreate && (
        <CampaignModal
          channel={modalChannel}
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
          key={`replies-${viewing.campaign.id}`}
          campaignId={viewing.campaign.id}
          campaignName={viewing.campaign.name}
          channel={viewing.campaign.channel ?? channel}
          initialTab={viewing.tab}
          onClose={() => setViewing(null)}
        />
      )}
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
      <div className="card w-full sm:max-w-lg max-h-[92vh] overflow-y-auto rounded-t-2xl sm:rounded-xl p-4 sm:p-6">
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
                className="text-xs font-medium text-primary-600 hover:underline flex items-center gap-1"
              >
                <Upload size={12} /> Import contacts
              </button>
            </div>
            <ListPicker
              value={listId}
              onChange={setListId}
              placeholder="Select list... (or create one)"
              noneLabel="No list yet"
            />
            <p className="text-[11px] text-gray-400 mt-1">
              Missing a list? Search it, or create it inline — no detour needed.
            </p>
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
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mb-2">
                  <span className="text-xs text-gray-500">
                    Start from a saved template — it fills the box below and stays editable:
                  </span>
                  <TemplatePicker
                    value={fillTemplateId}
                    onChange={fillFromTemplate}
                    allowNone
                    noneLabel="None — typing from scratch"
                    placeholder="Choose a template to fill…"
                    showPreview={false}
                    className="flex-1 min-w-[220px]"
                  />
                </div>
                {isEmail ? (
                  <RichEmailEditor
                    body={messageBody}
                    onBody={setMessageBody}
                    html={htmlBody}
                    onHtml={setHtmlBody}
                    attachments={attachments}
                    onAttachments={setAttachments}
                  />
                ) : (
                <textarea
                  ref={messageRef}
                  className="input font-mono text-sm"
                  rows={5}
                  value={messageBody}
                  onChange={(e) => setMessageBody(e.target.value)}
                  placeholder={"Hi {{first_name}}, ..."}
                />
                )}
                <div className="flex flex-wrap items-center gap-1.5 mt-2">
                  <span className="text-xs text-gray-500 mr-1">Insert:</span>
                  <ShortcodePicker
                    targetRef={messageRef}
                    value={messageBody}
                    onChange={setMessageBody}
                    label="Shortcode"
                  />
                  {["{{first_name}}", "{{business_name}}", "{{city}}", "{{state}}"].map((v) => (
                    <button
                      key={v}
                      type="button"
                      onClick={() => setMessageBody((prev) => (prev ? prev + " " + v : v))}
                      className="text-xs px-2 py-0.5 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 rounded font-mono text-gray-600 dark:text-gray-300"
                    >
                      {v}
                    </button>
                  ))}
                </div>
                <p className={`text-xs text-gray-400 mt-2 ${isEmail ? "hidden" : ""}`}>
                  {len} chars · {segments} SMS{segments === 1 ? "" : "s"}
                  {unicode && " · unicode (70/SMS)"}
                  {segments > 3 && " · long messages cost more"}
                  {fillTemplateId && " · template text was copied in — this campaign keeps its own copy"}
                </p>
              </>
            ) : (
              <>
                <TemplatePicker
                  value={templateId}
                  onChange={setTemplateId}
                  allowNone={false}
                  placeholder="Select template..."
                />
                <p className="text-xs text-gray-400 mt-2">
                  This campaign references the saved template live — edits made on the Templates page
                  later apply to what it sends. Choose “Write message” to paste the text in and keep
                  your own copy instead.
                </p>
              </>
            )}
          </div>

          {isEmail && (
            <>
              <div>
                <label className="label">Subject *</label>
                <input
                  className="input"
                  value={subject}
                  onChange={(e) => setSubject(e.target.value)}
                  placeholder="Quick question about {{company}}"
                />
              </div>
              <div className="grid sm:grid-cols-2 gap-3">
                <div>
                  <label className="label">Send through</label>
                  <select
                    className="input"
                    value={emailAccountId}
                    onChange={(e) => setEmailAccountId(e.target.value)}
                  >
                    <option value="">Default sender</option>
                    {emailAccounts.map((a) => (
                      <option key={a.id} value={a.id} disabled={!a.is_active}>
                        {senderOptionLabel(a)}
                        {a.is_active ? "" : " (off)"}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="label">Fallback sender</label>
                  <select
                    className="input"
                    value={fallbackAccountId}
                    onChange={(e) => setFallbackAccountId(e.target.value)}
                  >
                    <option value="">None</option>
                    {emailAccounts.map((a) => (
                      <option
                        key={a.id}
                        value={a.id}
                        disabled={String(a.id) === emailAccountId || !a.is_active}
                      >
                        {senderOptionLabel(a)}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
              <SenderPoolPicker
                accounts={emailAccounts}
                value={senderPool}
                onChange={setSenderPool}
                primaryId={emailAccountId || null}
              />
            </>
          )}

          <section className="rounded-lg border border-gray-200 dark:border-gray-700 p-3 space-y-3">
            <div>
              <h3 className="font-semibold text-sm">Campaign sending controls</h3>
              <p className="text-xs text-gray-500 mt-1">
                These optional controls affect only this campaign. Leave caps and delays blank for
                unrestricted pacing; blank sending hours mean 24/7. Direct sends are not restricted
                by campaign controls. Hours use the app timezone.
              </p>
            </div>
            <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-3">
              <div>
                <label className="label">Daily cap</label>
                <input className="input" type="number" min={1} value={dailyLimit}
                  onChange={(e) => setDailyLimit(e.target.value)} placeholder="Unlimited" />
              </div>
              <div>
                <label className="label">Hourly cap</label>
                <input className="input" type="number" min={1} value={hourlyLimit}
                  onChange={(e) => setHourlyLimit(e.target.value)} placeholder="Unlimited" />
              </div>
              <div>
                <label className="label">Per-minute cap</label>
                <input className="input" type="number" min={1} value={perMinuteLimit}
                  onChange={(e) => setPerMinuteLimit(e.target.value)} placeholder="Unlimited" />
              </div>
              <div>
                <label className="label">Minimum delay (sec)</label>
                <input className="input" type="number" min={0} value={minDelay}
                  onChange={(e) => setMinDelay(e.target.value)} placeholder="None" />
              </div>
              <div>
                <label className="label">Maximum delay (sec)</label>
                <input className="input" type="number" min={0} value={maxDelay}
                  onChange={(e) => setMaxDelay(e.target.value)} placeholder="None" />
              </div>
              <div>
                <label className="label">Send from hour (0–23)</label>
                <input className="input" type="number" min={0} max={23} value={sendStartHour}
                  onChange={(e) => setSendStartHour(e.target.value)} placeholder="24/7" />
              </div>
              <div>
                <label className="label">Send until hour (0–23)</label>
                <input className="input" type="number" min={0} max={23} value={sendEndHour}
                  onChange={(e) => setSendEndHour(e.target.value)} placeholder="24/7" />
              </div>
              <label className="flex items-center gap-2 text-sm pt-2">
                <input type="checkbox" checked={allowWeekends}
                  onChange={(e) => setAllowWeekends(e.target.checked)} />
                Allow weekend sending
              </label>
            </div>
          </section>

          {!isEmail && (
          <div>
            <label className="label">Sequence (optional)</label>
            <select className="input" value={sequenceId} onChange={(e) => setSequenceId(e.target.value)}>
              <option value="">No sequence</option>
              {sequences.map((s: any) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
            {sequenceId && (
              <p className="text-xs text-gray-400 mt-1">
                This sequence controls every message and wait. Its wait steps appear in Follow-ups.
                Campaign-level text above will not be saved.
              </p>
            )}
          </div>
          )}

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
