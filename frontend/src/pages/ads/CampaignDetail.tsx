import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import toast from "react-hot-toast";
import {
  ArrowLeft,
  BarChart3,
  Bot,
  CheckCircle2,
  Copy,
  Download,
  FileText,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Rocket,
  Trash2,
  TrendingUp,
  Trophy,
  Users,
  Wand2,
  X,
  Zap,
} from "lucide-react";
import api from "../../api/client";
import adsApi, { AdsCreative, AdsSet, CampaignDetail as Detail } from "../../api/ads";
import ShortcodePicker from "../../components/ShortcodePicker";
import TemplatePicker from "../../components/TemplatePicker";
import ContactPicker from "../../components/ContactPicker";
import RichEmailEditor from "../../components/RichEmailEditor";
import { smsCount } from "../../utils/sms";
import { Badge, Bar, Empty, Field, Metric, Modal, Stat, Tabs, Toggle, WinnerBadge, fmtDate } from "./ui";
import CampaignBuilder from "./CampaignBuilder";

const BASE_TABS = [
  "Overview",
  "Sets",
  "Creatives",
  "Audience",
  "Sending",
  "Automation",
  "Optimization",
  "Analytics",
  "Activity",
  "Settings",
];

export default function CampaignDetail({
  campaignId,
  reference,
  onClose,
  onChanged,
  channel = "sms",
  backLabel,
}: {
  campaignId: number;
  reference: any;
  onClose: () => void;
  onChanged: () => void;
  /** "sms" (default) or "email" — the campaign's own channel wins once loaded. */
  channel?: string;
  backLabel?: string;
}) {
  const [tab, setTab] = useState("Overview");
  const [detail, setDetail] = useState<Detail | null>(null);
  const [analytics, setAnalytics] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(false);
  const [launching, setLaunching] = useState<any>(null);

  const load = useCallback(async () => {
    try {
      const [d, a] = await Promise.all([adsApi.getCampaign(campaignId), adsApi.analytics(campaignId)]);
      setDetail(d);
      setAnalytics(a);
    } catch {
      toast.error("Could not load campaign");
    } finally {
      setLoading(false);
    }
  }, [campaignId]);

  useEffect(() => {
    load();
  }, [load]);

  const act = async (fn: () => Promise<any>, message: string) => {
    try {
      await fn();
      toast.success(message);
      await load();
      onChanged();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Action failed");
    }
  };

  if (loading || !detail) {
    return (
      <div className="fixed inset-0 z-40 bg-gray-50 dark:bg-gray-900 flex items-center justify-center">
        <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-primary-600" />
      </div>
    );
  }

  const stats = analytics?.campaign;
  const isEmail = (detail.channel || channel) === "email";
  const TABS = BASE_TABS.map((t) => (t === "Sets" ? (isEmail ? "Email Sets" : "SMS Sets") : t));
  const activeTab = tab === "Sets" ? (isEmail ? "Email Sets" : "SMS Sets") : tab;
  const emailAccounts = reference?.email_accounts || [];
  const defaultEmailAccountId = reference?.default_email_account_id ?? null;

  return (
    <div className="fixed inset-0 z-40 bg-gray-50 dark:bg-gray-900 overflow-y-auto">
      <div className="max-w-6xl mx-auto p-4 sm:p-6 space-y-5">
        <div className="flex flex-wrap gap-3 items-start justify-between">
          <div className="min-w-0">
            <button className="text-sm text-primary-600 mb-2 flex items-center gap-1" onClick={onClose}>
              <ArrowLeft size={15} /> {backLabel || (isEmail ? "Email Manager" : "SMS Ads Manager")}
            </button>
            <h1 className="text-2xl font-bold break-words">{detail.name}</h1>
            <div className="flex flex-wrap items-center gap-2 mt-2">
              <Badge value={detail.status} />
              {detail.state && detail.state !== detail.status && <Badge value={detail.state} />}
              <span className={isEmail ? "badge-blue" : "badge-gray"}>{isEmail ? "Email" : "SMS"}</span>
              {detail.test_mode && <span className="badge-yellow">Test mode</span>}
              {detail.auto_optimize && (
                <span className="badge-green inline-flex items-center gap-1">
                  <Bot size={12} /> Andromeda ON
                </span>
              )}
              <span className="text-xs text-gray-500">{detail.objective}</span>
            </div>
            {isEmail && detail.subject && (
              <p className="text-sm text-gray-500 mt-1 truncate">Subject: {detail.subject}</p>
            )}
            {detail.status === "paused" && detail.paused_reason && (
              <p className="text-sm text-warning-600 dark:text-warning-500 mt-1">{detail.paused_reason}</p>
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            {detail.status === "draft" && (
              <button
                className="btn-primary btn-sm"
                onClick={async () => {
                  try {
                    const check = await adsApi.validate(campaignId);
                    // Keep the review flow usable even when an older backend
                    // returns an incomplete validation payload.
                    setLaunching({
                      ok: Boolean(check?.ok),
                      errors: Array.isArray(check?.errors) ? check.errors : ["The campaign could not be validated. Try again."],
                      warnings: Array.isArray(check?.warnings) ? check.warnings : [],
                      summary: check?.summary || {
                        audience: 0, eligible: 0, already_queued: 0, followup_steps: 0,
                        daily_limit: detail.daily_limit, estimated_days: 0,
                        drip: { mode: detail.drip_mode, batch: detail.drip_batch_size, interval_minutes: detail.drip_interval_minutes },
                        test_mode: detail.test_mode, skipped: {}, per_set: [],
                      },
                    });
                  } catch (err: any) {
                    toast.error(err.response?.data?.detail || "Could not validate campaign");
                  }
                }}
              >
                <Rocket size={15} className="mr-1" /> Review &amp; publish
              </button>
            )}
            {detail.status === "active" && (
              <button className="btn-secondary btn-sm" onClick={() => act(() => adsApi.pause(campaignId), "Paused")}>
                <Pause size={15} className="mr-1" /> Pause
              </button>
            )}
            {detail.status === "paused" && (
              <button
                className="btn-primary btn-sm"
                onClick={() => {
                  // A pause by the bounce circuit breaker is not undone by a reflex click:
                  // read why it paused, then resume deliberately.
                  const tripped = (detail.paused_reason || "").startsWith("Circuit breaker");
                  if (tripped && !confirm(`${detail.paused_reason}\n\nResume anyway? Fix the list first (verify the addresses).`)) return;
                  act(() => adsApi.resume(campaignId, tripped), "Resumed");
                }}
              >
                <Play size={15} className="mr-1" /> Resume
              </button>
            )}
            {(detail.status === "draft" || detail.status === "paused" || detail.status === "active") && (
              <button
                className="btn-danger btn-sm"
                onClick={async () => {
                  if (!confirm(`Delete “${detail.name}” permanently? Its contacts and message history will remain.`)) return;
                  try {
                    await adsApi.deleteCampaign(campaignId);
                    toast.success("Campaign deleted");
                    onChanged();
                    onClose();
                  } catch (err: any) {
                    toast.error(err.response?.data?.detail || "Could not delete campaign");
                  }
                }}
              >
                <Trash2 size={15} className="mr-1" /> Delete
              </button>
            )}
            {detail.status === "active" && (
              <button
                className="btn-secondary btn-sm"
                onClick={() =>
                  act(async () => {
                    const r = await adsApi.dispatch(campaignId);
                    toast.success(`Sent ${r.sent}`);
                  }, "Dispatched")
                }
              >
                <Zap size={15} className="mr-1" /> Send now
              </button>
            )}
            <button className="btn-secondary btn-sm" onClick={() => setEditing(true)}>
              Edit
            </button>
            <button
              className="btn-secondary btn-sm"
              onClick={() => {
                if (confirm(`Duplicate “${detail.name}” with all ${isEmail ? "email" : "SMS"} sets, creatives and audience targeting?`)) {
                  act(() => adsApi.duplicate(campaignId, true), "Campaign duplicated");
                }
              }}
            >
              <Copy size={15} />
            </button>
          </div>
        </div>

        {analytics?.alerts?.length > 0 && (
          <div className="space-y-2">
            {analytics.alerts.map((a: any, i: number) => (
              <div
                key={i}
                className={`p-3 rounded-lg text-sm ${
                  a.level === "warning"
                    ? "bg-warning-50 dark:bg-warning-900/20 text-warning-800 dark:text-warning-200"
                    : "bg-primary-50 dark:bg-primary-900/20 text-primary-800 dark:text-primary-200"
                }`}
              >
                {a.level === "warning" ? "⚠ " : "ℹ "}
                {a.message}
              </div>
            ))}
          </div>
        )}

        <Tabs tabs={TABS} active={activeTab} onChange={setTab} />

        {activeTab === "Overview" && <OverviewTab detail={detail} stats={stats} isEmail={isEmail} />}
        {(activeTab === "SMS Sets" || activeTab === "Email Sets") && (
          <SetsTab detail={detail} reference={reference} reload={load} onChanged={onChanged} isEmail={isEmail} />
        )}
        {activeTab === "Creatives" && (
          <CreativesTab
            detail={detail}
            analytics={analytics}
            reload={load}
            isEmail={isEmail}
            emailAccounts={emailAccounts}
          />
        )}
        {activeTab === "Audience" && (
          <AudienceTab detail={detail} reference={reference} reload={load} isEmail={isEmail} />
        )}
        {activeTab === "Sending" && <SendingTab detail={detail} stats={stats} isEmail={isEmail} />}
        {activeTab === "Automation" && <AutomationTab detail={detail} reload={load} isEmail={isEmail} />}
        {activeTab === "Optimization" && (
          <OptimizationTab detail={detail} reference={reference} reload={load} onChanged={onChanged} />
        )}
        {activeTab === "Analytics" && (
          <AnalyticsTab analytics={analytics} campaignId={campaignId} isEmail={isEmail} />
        )}
        {activeTab === "Activity" && <ActivityTab campaignId={campaignId} />}
        {activeTab === "Settings" && (
          <SettingsTab detail={detail} reload={load} onClose={onClose} onChanged={onChanged} />
        )}
      </div>

      {editing && (
        <CampaignBuilder
          campaign={detail}
          objectives={reference?.objectives || []}
          channel={detail.channel || channel}
          emailAccounts={emailAccounts}
          defaultEmailAccountId={defaultEmailAccountId}
          close={() => setEditing(false)}
          saved={() => {
            setEditing(false);
            load();
            onChanged();
          }}
        />
      )}
      {launching && (
        <LaunchModal
          check={launching}
          campaignId={campaignId}
          close={() => setLaunching(null)}
          launched={() => {
            setLaunching(null);
            load();
            onChanged();
          }}
        />
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ tabs */

function OverviewTab({ detail, stats, isEmail }: { detail: Detail; stats: any; isEmail: boolean }) {
  if (!stats) return null;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Stat label="Contacts assigned" value={stats.assigned} />
        <Stat label={isEmail ? "Emails sent" : "SMS sent"} value={stats.sent} />
        <Stat label="Delivery rate" value={`${stats.delivery_rate}%`} />
        <Stat label="Reply rate" value={`${stats.reply_rate}%`} />
        <Stat
          label="Open rate"
          value={`${stats.open_rate ?? 0}%`}
          hint={isEmail ? "Measured by Brevo's tracking pixel" : "Replied or tapped the link"}
        />
        <Stat
          label="Click rate"
          value={`${stats.click_rate ?? 0}%`}
          hint={isEmail ? "Measured by Brevo link tracking" : undefined}
        />
        <Stat label="Positive replies" value={stats.positive_replies} />
        <Stat label="Follow-ups due" value={stats.followups_due} />
        <Stat label="Meetings" value={stats.meetings} />
        <Stat label={isEmail ? "Unsubscribes" : "Opt-outs"} value={stats.opt_outs} />
      </div>
      <div className="card p-5 grid grid-cols-2 sm:grid-cols-4 gap-5">
        <Metric label="Pending" value={stats.pending} />
        <Metric label="Skipped" value={stats.skipped} />
        <Metric label="Failed" value={stats.failed} />
        <Metric label={isEmail ? "Emails used" : "Credits used"} value={stats.credits_used} />
        <Metric label="Daily limit" value={detail.daily_limit ?? "None"} />
        <Metric label="Total limit" value={detail.total_limit ?? "None"} />
        <Metric label="Drip" value={detail.drip_mode} />
        <Metric label="Performance score" value={stats.score} />
      </div>
    </div>
  );
}

/* --------------------------------------------------------------- SMS sets */

const csvList = (raw?: string | null) => (raw || "").split(",").map((x) => x.trim()).filter(Boolean);

function SetsTab({
  detail,
  reference,
  reload,
  onChanged,
  isEmail,
}: {
  detail: Detail;
  reference: any;
  reload: () => void;
  onChanged: () => void;
  isEmail: boolean;
}) {
  const setNoun = isEmail ? "Email set" : "SMS set";
  const setNounPlural = isEmail ? "Email sets" : "SMS sets";
  const [editing, setEditing] = useState<AdsSet | null>(null);
  const [creating, setCreating] = useState(false);
  const [previews, setPreviews] = useState<Record<number, any>>({});
  const [audiences, setAudiences] = useState<any[]>([]);

  const setIdsKey = detail.sets.map((s) => s.id).join(",");
  useEffect(() => {
    detail.sets.forEach(async (s) => {
      try {
        const preview = await adsApi.previewSet(s.id);
        setPreviews((p) => ({ ...p, [s.id]: preview }));
      } catch {
        /* preview is best-effort */
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [setIdsKey]);
  useEffect(() => {
    adsApi.listAudiences().then((r) => setAudiences(r.items)).catch(() => {});
  }, []);

  const listName = (id: string) =>
    reference?.lists?.find((l: any) => String(l.id) === String(id))?.name || `List ${id}`;
  const audienceName = (id?: number | null) =>
    audiences.find((a) => a.id === id)?.name || (id ? `Audience ${id}` : null);

  const removeList = async (s: AdsSet, listId: string) => {
    const next = csvList(s.list_ids).filter((x) => x !== listId).join(",") || null;
    try {
      await adsApi.updateSet(s.id, { list_ids: next });
      toast.success(`“${listName(listId)}” removed from “${s.name}”`);
      reload();
      onChanged();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not remove list");
    }
  };

  const toggleStatus = async (s: AdsSet) => {
    const next = s.status === "active" ? "paused" : "active";
    try {
      await adsApi.updateSet(s.id, { status: next });
      toast.success(next === "active" ? `“${s.name}” resumed` : `“${s.name}” paused`);
      reload();
      onChanged();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not update set");
    }
  };

  const toggleAuto = async (s: AdsSet, next: boolean) => {
    try {
      await adsApi.updateSet(s.id, { auto_optimize: next });
      toast.success(next ? `Andromeda placement ON for “${s.name}”` : `Andromeda placement OFF for “${s.name}”`);
      reload();
      onChanged();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not update set");
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex justify-between items-center">
        <h2 className="font-semibold">{setNounPlural}</h2>
        <button className="btn-primary btn-sm" onClick={() => setCreating(true)}>
          <Plus size={15} className="mr-1" /> New set
        </button>
      </div>
      {detail.sets.length === 0 ? (
        <Empty
          icon={<Users size={40} />}
          title={`No ${setNounPlural.toLowerCase()} yet`}
          body={`An ${setNoun.toLowerCase()} is an audience plus its delivery rules. Create one per segment you want to compare.`}
          action={
            <button className="btn-primary" onClick={() => setCreating(true)}>
              <Plus size={16} className="mr-1" /> Create {setNoun.toLowerCase()}
            </button>
          }
        />
      ) : (
        detail.sets.map((s) => {
          const preview = previews[s.id];
          const creatives = detail.creatives.filter((c) => c.set_id === s.id);
          const lists = csvList(s.list_ids);
          const explicit = csvList(s.contact_ids);
          const audName = audienceName(s.audience_id);
          const filters: string[] = [];
          if (s.include_tags) filters.push(`tags: ${s.include_tags}`);
          if (s.city) filters.push(s.city);
          if (s.state) filters.push(s.state);
          if (s.industry) filters.push(s.industry);
          if (s.include_statuses) filters.push(`status: ${s.include_statuses}`);
          if (s.activity_filter && s.activity_filter !== "any")
            filters.push(s.activity_filter.replace(/_/g, " "));
          return (
            <div className="card p-4" key={s.id}>
              <div className="flex flex-wrap gap-3 items-start justify-between">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <h3 className="font-semibold">{s.name}</h3>
                    <Badge value={s.status} />
                    {s.auto_optimize === false && <span className="badge-gray">Andromeda off</span>}
                  </div>
                  <p className="text-sm text-gray-500 mt-1">
                    {preview
                      ? `${preview.matched} matched · ${preview.eligible} eligible · ${creatives.length} creative(s)`
                      : `${creatives.length} creative(s)`}
                  </p>
                  {/* Audience sources with inline remove */}
                  <div className="flex flex-wrap gap-1.5 mt-2">
                    {lists.map((lid) => (
                      <span
                        key={lid}
                        className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs bg-primary-50 dark:bg-primary-900/30 text-primary-700 dark:text-primary-200"
                      >
                        {listName(lid)}
                        <button
                          type="button"
                          title={`Remove ${listName(lid)} from this set`}
                          className="hover:text-danger-600"
                          onClick={() => removeList(s, lid)}
                        >
                          <X size={12} />
                        </button>
                      </span>
                    ))}
                    {audName && (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs bg-primary-100 dark:bg-primary-900/40 text-primary-700 dark:text-primary-200">
                        ⦿ {audName}
                      </span>
                    )}
                    {explicit.length > 0 && (
                      <span className="px-2 py-0.5 rounded-full text-xs bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300">
                        {explicit.length} picked contact{explicit.length === 1 ? "" : "s"}
                      </span>
                    )}
                    {filters.map((f) => (
                      <span
                        key={f}
                        className="px-2 py-0.5 rounded-full text-xs bg-gray-100 dark:bg-gray-700 text-gray-500"
                      >
                        {f}
                      </span>
                    ))}
                    {lists.length === 0 && !audName && explicit.length === 0 && filters.length === 0 && (
                      <span className="px-2 py-0.5 rounded-full text-xs bg-warning-100 dark:bg-warning-900/40 text-warning-700 dark:text-warning-200">
                        No audience yet — edit targeting to add lists or contacts
                      </span>
                    )}
                  </div>
                </div>
                <div className="flex flex-wrap gap-2 items-center">
                  <button
                    className="btn-secondary btn-sm"
                    title={s.status === "active" ? "Pause this set" : "Resume this set"}
                    onClick={() => toggleStatus(s)}
                  >
                    {s.status === "active" ? <Pause size={14} /> : <Play size={14} />}
                  </button>
                  <button className="btn-secondary btn-sm" onClick={() => setEditing(s)}>
                    Edit targeting
                  </button>
                  <button
                    className="btn-secondary btn-sm"
                    title="Duplicate this set (targeting + creatives, fresh audience)"
                    onClick={async () => {
                      try {
                        await adsApi.duplicateSet(s.id);
                        toast.success(`“${s.name}” duplicated`);
                        reload();
                        onChanged();
                      } catch (err: any) {
                        toast.error(err.response?.data?.detail || "Could not duplicate set");
                      }
                    }}
                  >
                    <Copy size={14} />
                  </button>
                  <button
                    className="btn-ghost btn-sm text-danger-600"
                    onClick={async () => {
                      if (!confirm(`Remove set “${s.name}”?`)) return;
                      await adsApi.deleteSet(s.id);
                      toast.success("Set removed");
                      reload();
                      onChanged();
                    }}
                  >
                    <Trash2 size={15} />
                  </button>
                </div>
              </div>
              {/* Per-set Andromeda placement toggle */}
              <div className="mt-3 pt-3 border-t border-gray-100 dark:border-gray-700">
                <Toggle
                  checked={s.auto_optimize !== false}
                  onChange={(next) => toggleAuto(s, next)}
                  label="Andromeda placement"
                  hint={
                    detail.auto_optimize
                      ? "This set takes part in automatic winner traffic shifts."
                      : "Only takes effect when the campaign master switch is ON (Optimization tab)."
                  }
                />
              </div>
              {preview && Object.keys(preview.skipped || {}).length > 0 && (
                <div className="mt-2 flex flex-wrap gap-3 text-xs text-gray-500">
                  {Object.entries(preview.skipped).map(([k, v]: any) => (
                    <span key={k}>
                      {k.replace(/_/g, " ")}: <b>{v}</b>
                    </span>
                  ))}
                </div>
              )}
            </div>
          );
        })
      )}
      {(creating || editing) && (
        <SetEditor
          campaignId={detail.id}
          adsSet={editing}
          reference={reference}
          isEmail={isEmail}
          close={() => {
            setCreating(false);
            setEditing(null);
          }}
          saved={() => {
            setCreating(false);
            setEditing(null);
            reload();
            onChanged();
          }}
        />
      )}
    </div>
  );
}

function SetEditor({
  campaignId,
  adsSet,
  reference,
  isEmail,
  close,
  saved,
}: {
  campaignId: number;
  adsSet: AdsSet | null;
  reference: any;
  isEmail: boolean;
  close: () => void;
  saved: () => void;
}) {
  const setNoun = isEmail ? "Email set" : "SMS set";
  const [form, setForm] = useState<any>({
    name: adsSet?.name || "",
    status: adsSet?.status || "active",
    list_ids: adsSet?.list_ids || "",
    contact_ids: adsSet?.contact_ids || "",
    audience_id: adsSet?.audience_id ?? "",
    include_tags: adsSet?.include_tags || "",
    exclude_tags: adsSet?.exclude_tags || "",
    include_statuses: adsSet?.include_statuses || "",
    exclude_statuses: adsSet?.exclude_statuses || "",
    city: adsSet?.city || "",
    state: adsSet?.state || "",
    industry: adsSet?.industry || "",
    activity_filter: adsSet?.activity_filter || "any",
    daily_limit: adsSet?.daily_limit ?? "",
    split_mode: adsSet?.split_mode || "equal",
    auto_optimize: adsSet?.auto_optimize !== false,
  });
  const [audiences, setAudiences] = useState<any[]>([]);
  const [estimate, setEstimate] = useState<any>(null);
  const set = (k: string, v: any) => setForm((f: any) => ({ ...f, [k]: v }));
  const selectedLists = csvList(form.list_ids);
  const selectedContacts: number[] = useMemo(
    () => csvList(form.contact_ids).map(Number).filter((n) => Number.isFinite(n) && n > 0),
    [form.contact_ids]
  );

  useEffect(() => {
    adsApi.listAudiences().then((r) => setAudiences(r.items)).catch(() => {});
  }, []);

  // Live size estimate as targeting changes.
  const estimateKey = JSON.stringify({
    l: form.list_ids,
    c: form.contact_ids,
    a: form.audience_id,
    t: form.include_tags,
    e: form.exclude_tags,
    s: form.include_statuses,
    x: form.exclude_statuses,
    city: form.city,
    st: form.state,
    ind: form.industry,
    act: form.activity_filter,
  });
  useEffect(() => {
    const t = setTimeout(async () => {
      try {
        const r = await adsApi.previewTargeting({
          list_ids: form.list_ids || null,
          contact_ids: form.contact_ids || null,
          audience_id: form.audience_id ? Number(form.audience_id) : null,
          include_tags: form.include_tags || null,
          exclude_tags: form.exclude_tags || null,
          include_statuses: form.include_statuses || null,
          exclude_statuses: form.exclude_statuses || null,
          city: form.city || null,
          state: form.state || null,
          industry: form.industry || null,
          activity_filter: form.activity_filter || null,
        });
        setEstimate(r);
      } catch {
        setEstimate(null);
      }
    }, 400);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [estimateKey]);

  const loadAudience = async (id: number) => {
    try {
      const a = await adsApi.getAudience(id);
      set("list_ids", a.list_ids || "");
      set("contact_ids", a.contact_ids || "");
      set("include_tags", a.include_tags || "");
      set("exclude_tags", a.exclude_tags || "");
      set("include_statuses", a.include_statuses || "");
      set("exclude_statuses", a.exclude_statuses || "");
      set("city", a.city || "");
      set("state", a.state || "");
      set("industry", a.industry || "");
      set("activity_filter", a.activity_filter || "any");
      set("audience_id", a.id);
      toast.success(`Loaded audience “${a.name}”`);
    } catch {
      toast.error("Could not load audience");
    }
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.name.trim()) return toast.error(`Name the ${setNoun.toLowerCase()}`);
    const payload = {
      ...form,
      name: form.name.trim(),
      daily_limit: form.daily_limit === "" ? null : Number(form.daily_limit),
      audience_id: form.audience_id ? Number(form.audience_id) : null,
      list_ids: form.list_ids || null,
      contact_ids: form.contact_ids || null,
      include_tags: form.include_tags || null,
      exclude_tags: form.exclude_tags || null,
      include_statuses: form.include_statuses || null,
      exclude_statuses: form.exclude_statuses || null,
      city: form.city || null,
      state: form.state || null,
      industry: form.industry || null,
      activity_filter: form.activity_filter || null,
    };
    try {
      if (adsSet) await adsApi.updateSet(adsSet.id, payload);
      else await adsApi.createSet(campaignId, payload);
      toast.success(`${setNoun} saved`);
      saved();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not save");
    }
  };

  return (
    <Modal title={adsSet ? `Edit ${setNoun.toLowerCase()}` : `New ${setNoun.toLowerCase()}`} close={close} wide>
      <form onSubmit={submit} className="space-y-4">
        <Field label="Set name">
          <input className="input" value={form.name} onChange={(e) => set("name", e.target.value)} autoFocus />
        </Field>

        {estimate && (
          <div className="p-3 rounded-lg bg-primary-50 dark:bg-primary-900/20 text-sm">
            <b>{estimate.eligible}</b> eligible contacts
            <span className="text-gray-500"> ({estimate.matched} matched before screening)</span>
            {estimate.matched === 0 && (
              <span className="block text-xs mt-1 text-warning-700 dark:text-warning-300">
                Nothing selected yet — add at least one list, contact, audience or filter.
              </span>
            )}
          </div>
        )}

        <Field label="Start from a saved audience" hint="Copies that audience's targeting into this set.">
          <div className="flex gap-2">
            <select
              className="input flex-1"
              value={form.audience_id}
              onChange={(e) => set("audience_id", e.target.value)}
            >
              <option value="">No saved audience</option>
              {audiences.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name} ({a.match_count ?? "?"} contacts)
                </option>
              ))}
            </select>
            {form.audience_id && (
              <button type="button" className="btn-secondary" onClick={() => loadAudience(Number(form.audience_id))}>
                Load
              </button>
            )}
          </div>
        </Field>

        <div>
          <span className="label">Contact lists</span>
          <div className="flex flex-wrap gap-2">
            {(reference?.lists || []).map((l: any) => {
              const on = selectedLists.includes(String(l.id));
              return (
                <button
                  type="button"
                  key={l.id}
                  onClick={() =>
                    set(
                      "list_ids",
                      (on
                        ? selectedLists.filter((x: string) => x !== String(l.id))
                        : [...selectedLists, String(l.id)]
                      ).join(",")
                    )
                  }
                  className={`px-3 py-1.5 rounded-lg text-sm border ${
                    on
                      ? "bg-primary-600 text-white border-primary-600"
                      : "border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300"
                  }`}
                >
                  {l.name} ({l.count})
                </button>
              );
            })}
            {(reference?.lists || []).length === 0 && (
              <p className="text-sm text-gray-500">No lists yet — create one under Lists first.</p>
            )}
          </div>
          <p className="text-xs text-gray-500 mt-1">
            Nothing selected matches <b>0 contacts</b> — a set stays empty until you add at least one
            list, contact, audience or filter.
          </p>
        </div>

        <div>
          <span className="label">Picked contacts</span>
          <ContactPicker
            value={selectedContacts}
            onChange={(ids) => set("contact_ids", ids.join(","))}
            placeholder="Search and add individual contacts…"
          />
          <p className="text-xs text-gray-500 mt-1">Added on top of the lists above, then filtered.</p>
        </div>

        <div className="grid sm:grid-cols-2 gap-3">
          <Field label="Include tags" hint="Comma separated.">
            <input className="input" value={form.include_tags} onChange={(e) => set("include_tags", e.target.value)} />
          </Field>
          <Field label="Exclude tags">
            <input className="input" value={form.exclude_tags} onChange={(e) => set("exclude_tags", e.target.value)} />
          </Field>
          <Field label="Include statuses">
            <input
              className="input"
              value={form.include_statuses}
              onChange={(e) => set("include_statuses", e.target.value)}
              placeholder="prospect,new"
            />
          </Field>
          <Field label="Exclude statuses">
            <input
              className="input"
              value={form.exclude_statuses}
              onChange={(e) => set("exclude_statuses", e.target.value)}
            />
          </Field>
          <Field label="City">
            <input className="input" value={form.city} onChange={(e) => set("city", e.target.value)} />
          </Field>
          <Field label="State">
            <input className="input" value={form.state} onChange={(e) => set("state", e.target.value)} />
          </Field>
          <Field label="Industry">
            <input className="input" value={form.industry} onChange={(e) => set("industry", e.target.value)} />
          </Field>
          <Field label="Activity">
            <select
              className="input"
              value={form.activity_filter}
              onChange={(e) => set("activity_filter", e.target.value)}
            >
              <option value="any">Any</option>
              <option value="never_contacted">Never contacted</option>
              <option value="contacted">Previously contacted</option>
              <option value="replied">Replied before</option>
              <option value="not_replied">Never replied</option>
            </select>
          </Field>
          <Field label="Split mode" hint="How contacts are divided between creatives.">
            <select className="input" value={form.split_mode} onChange={(e) => set("split_mode", e.target.value)}>
              <option value="equal">Equal split</option>
              <option value="percentage">Percentage split</option>
              <option value="weighted">Weighted</option>
              <option value="quota">{isEmail ? "Exact email count per creative" : "Exact SMS count per creative"}</option>
              <option value="random">Randomised</option>
            </select>
          </Field>
          <Field label="Daily limit for this set">
            <input
              type="number"
              className="input"
              value={form.daily_limit}
              onChange={(e) => set("daily_limit", e.target.value)}
            />
          </Field>
          <Field label="Status">
            <select className="input" value={form.status} onChange={(e) => set("status", e.target.value)}>
              <option value="active">Active</option>
              <option value="paused">Paused</option>
              <option value="archived">Archived</option>
            </select>
          </Field>
        </div>

        <div className="p-3 rounded-lg bg-gray-50 dark:bg-gray-700/50">
          <Toggle
            checked={!!form.auto_optimize}
            onChange={(v) => set("auto_optimize", v)}
            label="Andromeda placement for this set"
            hint="When the campaign master switch is ON, this set's traffic shifts to its winning creative automatically."
          />
        </div>

        <div className="flex gap-2">
          <button type="button" className="btn-secondary flex-1" onClick={close}>
            Cancel
          </button>
          <button className="btn-primary flex-1">Save set</button>
        </div>
      </form>
    </Modal>
  );
}

/* --------------------------------------------------------------- creatives */

/** Current template bodies, fetched once when any creative is template-bound.
 *  Lets the creatives list show "synced" vs "template changed" without an
 *  extra request per creative. */
function useTemplateLibrary(creatives: AdsCreative[], channel?: string) {
  const bound = creatives.some((c) => c.template_id);
  // Refetch when the set of bound creatives changes (add/sync/edit), not just
  // on first mount — a body change in the editor must re-derive the chips.
  const signature = creatives
    .filter((c) => c.template_id)
    .map((c) => `${c.id}:${c.template_id}:${c.body.length}`)
    .join("|");
  const [library, setLibrary] = useState<Record<
    number,
    { name: string; body: string; subject?: string | null }
  > | null>(null);
  useEffect(() => {
    if (!bound) return;
    let cancelled = false;
    (async () => {
      try {
        const all: any[] = [];
        let page = 1;
        let total = 0;
        do {
          const { data } = await api.get("/templates/", {
            params: { page, per_page: 100, ...(channel ? { channel } : {}) },
          });
          all.push(...(data.items || []));
          total = data.total ?? all.length;
          page += 1;
        } while (all.length < total);
        if (!cancelled) {
          const map: Record<number, { name: string; body: string; subject?: string | null }> = {};
          all.forEach((t) => (map[t.id] = { name: t.name, body: t.body, subject: t.subject }));
          setLibrary(map);
        }
      } catch {
        if (!cancelled) setLibrary({});
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [bound, signature, channel]);
  return library;
}

function TemplateSyncChip({
  creative,
  library,
  onClick,
}: {
  creative: AdsCreative;
  library: Record<number, { name: string; body: string; subject?: string | null }> | null;
  onClick: () => void;
}) {
  if (!creative.template_id) return null;
  const tpl = library?.[creative.template_id];
  if (!tpl) {
    return (
      <span className="badge-gray inline-flex items-center gap-1" title="The saved template this creative started from was deleted. Open the creative to unbind or keep the text as-is.">
        template deleted
      </span>
    );
  }
  const subjectSynced = !tpl.subject || !creative.subject || tpl.subject === creative.subject;
  const synced = tpl.body === creative.body && subjectSynced;
  return (
    <button
      type="button"
      onClick={onClick}
      title={
        synced
          ? `Uses template “${tpl.name}” — its text is current.`
          : `Template “${tpl.name}” has been edited since this creative was saved. Open it and press “Sync to template” to re-apply the new text as a new version.`
      }
      className={
        synced
          ? "badge-green inline-flex items-center gap-1"
          : "badge-yellow inline-flex items-center gap-1 hover:ring-1 hover:ring-warning-400"
      }
    >
      <FileText size={11} />
      {synced ? `✓ ${tpl.name}` : `${tpl.name} · update available`}
    </button>
  );
}

function CreativesTab({
  detail,
  analytics,
  reload,
  isEmail,
  emailAccounts,
}: {
  detail: Detail;
  analytics: any;
  reload: () => void;
  isEmail: boolean;
  emailAccounts: any[];
}) {
  const [editing, setEditing] = useState<AdsCreative | null>(null);
  const [creatingFor, setCreatingFor] = useState<number | null>(null);
  const [versionsFor, setVersionsFor] = useState<AdsCreative | null>(null);
  const [analyticsFor, setAnalyticsFor] = useState<AdsCreative | null>(null);
  const perf = useMemo(() => {
    const map: Record<number, any> = {};
    (analytics?.creatives || []).forEach((c: any) => (map[c.id] = c));
    return map;
  }, [analytics]);
  const maxScore = Math.max(1, ...(analytics?.creatives || []).map((c: any) => c.score || 0));
  // Template bodies for the "synced / update available" chips on bound
  // creatives — fetched once per tab visit, shared by every card.
  const templateLibrary = useTemplateLibrary(detail.creatives, isEmail ? "email" : "sms");

  if (detail.sets.length === 0)
    return (
      <Empty
        title={isEmail ? "Create an email set first" : "Create an SMS set first"}
        body={isEmail ? "Creatives live inside an email set." : "Creatives live inside an SMS set."}
      />
    );

  const pauseLosers = async (setId: number, setName: string) => {
    if (!confirm(`Pause every creative in “${setName}” except the winner? Pending contacts move to the winner.`))
      return;
    try {
      const r = await adsApi.pauseLosers(setId);
      toast.success(`Winner kept: ${r.winner_name} — ${r.moved} contact(s) moved`);
      reload();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not pause losers");
    }
  };

  return (
    <div className="space-y-5">
      {detail.sets.map((s) => {
        const creatives = detail.creatives.filter((c) => c.set_id === s.id);
        const activeCount = creatives.filter((c) => c.status === "active").length;
        return (
          <div key={s.id} className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="font-semibold">{s.name}</h3>
              <div className="flex gap-2">
                {activeCount >= 2 && (
                  <button className="btn-secondary btn-sm" onClick={() => pauseLosers(s.id, s.name)}>
                    <Trophy size={15} className="mr-1" /> Pause losers
                  </button>
                )}
                <button className="btn-secondary btn-sm" onClick={() => setCreatingFor(s.id)}>
                  <Plus size={15} className="mr-1" /> Add creative
                </button>
              </div>
            </div>
            {creatives.length === 0 ? (
              <Empty
                title="No creatives in this set"
                body="Add two or three message variations and the audience is split between them automatically."
                action={
                  <button className="btn-primary" onClick={() => setCreatingFor(s.id)}>
                    <Plus size={16} className="mr-1" /> Add creative
                  </button>
                }
              />
            ) : (
              creatives.map((c) => {
                const p = perf[c.id] || {};
                return (
                  <div
                    className={`card p-4 ${p.is_winner ? "ring-2 ring-success-500" : ""}`}
                    key={c.id}
                  >
                    <div className="flex flex-wrap gap-3 items-start justify-between">
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2 flex-wrap">
                          <h4 className="font-semibold">{c.name}</h4>
                          <Badge value={c.status} />
                          {p.is_winner && <WinnerBadge />}
                          {p.needs_more_data && (p.sent ?? 0) > 0 && (
                            <span className="badge-gray">Gathering data</span>
                          )}
                          <button
                            className="text-xs text-primary-600"
                            onClick={() => setVersionsFor(c)}
                          >
                            v{c.current_version}
                          </button>
                          {(s.split_mode === "percentage" || s.split_mode === "weighted") && (
                            <span className="text-xs text-gray-500">
                              {s.split_mode === "percentage" ? `${c.allocation}%` : `weight ${c.allocation}`}
                            </span>
                          )}
                          {s.split_mode === "quota" && (
                            <span className="text-xs font-medium text-primary-700 dark:text-primary-300 bg-primary-50 dark:bg-primary-900/30 px-2 py-0.5 rounded-full">
                              {c.send_quota
                                ? `${c.send_quota} ${isEmail ? "emails" : "SMS"}`
                                : "no cap (takes the rest)"}
                            </span>
                          )}
                          {isEmail && c.email_account_id != null && (
                            <span className="text-xs text-gray-500">
                              via {emailAccounts.find((a: any) => a.id === c.email_account_id)?.name || "sender"}
                            </span>
                          )}
                        </div>
                        {c.template_id && templateLibrary !== null && (
                          <div className="mt-1.5">
                            <TemplateSyncChip
                              creative={c}
                              library={templateLibrary}
                              onClick={() => setEditing(c)}
                            />
                          </div>
                        )}
                        {isEmail && (
                          <p className="text-sm font-medium mt-2 truncate">
                            {c.subject || detail.subject || (
                              <span className="text-warning-600">No subject — set one or add a campaign subject</span>
                            )}
                          </p>
                        )}
                        <p className="text-sm text-gray-600 dark:text-gray-300 mt-1 whitespace-pre-wrap break-words">
                          {c.body || <span className="text-gray-400">No message yet</span>}
                        </p>
                        {isEmail && c.html_body && (
                          <span className="badge-blue mt-1 inline-block">rich HTML</span>
                        )}
                      </div>
                      <div className="flex flex-wrap gap-2">
                        <button
                          className="btn-secondary btn-sm"
                          title="Full analytics for this creative"
                          onClick={() => setAnalyticsFor(c)}
                        >
                          <BarChart3 size={14} />
                        </button>
                        <button className="btn-secondary btn-sm" onClick={() => setEditing(c)}>
                          Edit
                        </button>
                        <button
                          className="btn-secondary btn-sm"
                          title={c.status === "active" ? "Turn this creative off" : "Turn this creative on"}
                          onClick={async () => {
                            await adsApi.updateCreative(c.id, {
                              status: c.status === "active" ? "paused" : "active",
                            });
                            toast.success(c.status === "active" ? "Creative paused" : "Creative active");
                            reload();
                          }}
                        >
                          {c.status === "active" ? <Pause size={14} /> : <Play size={14} />}
                        </button>
                        <button
                          className="btn-secondary btn-sm"
                          title="Promote as winner"
                          onClick={async () => {
                            await adsApi.promoteCreative(c.id);
                            toast.success("Winner promoted — remaining traffic moved here");
                            reload();
                          }}
                        >
                          <Trophy size={14} />
                        </button>
                        <button
                          className="btn-secondary btn-sm"
                          onClick={async () => {
                            await adsApi.duplicateCreative(c.id);
                            toast.success("Creative duplicated");
                            reload();
                          }}
                        >
                          <Copy size={14} />
                        </button>
                        <button
                          className="btn-ghost btn-sm text-danger-600"
                          onClick={async () => {
                            if (!confirm("Delete this creative? History is preserved.")) return;
                            await adsApi.deleteCreative(c.id);
                            toast.success("Creative deleted");
                            reload();
                          }}
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </div>
                    <div className="grid grid-cols-3 sm:grid-cols-6 gap-3 mt-4 pt-3 border-t border-gray-100 dark:border-gray-700 text-sm">
                      <Metric label="Sent" value={p.sent ?? 0} />
                      <Metric label="Delivery" value={`${p.delivery_rate ?? 0}%`} />
                      <Metric label="Reply rate" value={`${p.reply_rate ?? 0}%`} />
                      <Metric label="Open rate" value={`${p.open_rate ?? 0}%`} />
                      <Metric label="Click rate" value={`${p.click_rate ?? 0}%`} />
                      <Metric label="Score" value={p.score ?? 0} />
                      <Metric label="Assigned" value={p.assigned ?? 0} />
                      <Metric label="Replies" value={p.replies ?? 0} />
                      <Metric label="Positive" value={p.positive_replies ?? 0} />
                      <Metric label="Opt-outs" value={`${p.opt_outs ?? 0} (${p.opt_out_rate ?? 0}%)`} />
                      <Metric label="Failed" value={`${p.failed ?? 0} (${p.failure_rate ?? 0}%)`} />
                      <Metric label="Pending" value={p.pending ?? 0} />
                    </div>
                    <div className="mt-2">
                      <Bar value={p.score || 0} max={maxScore} tone={p.is_winner ? "success" : "primary"} />
                    </div>
                  </div>
                );
              })
            )}
          </div>
        );
      })}
      {(editing || creatingFor !== null) && (
        <CreativeEditor
          creative={editing}
          setId={creatingFor ?? editing!.set_id}
          isEmail={isEmail}
          campaignSubject={detail.subject}
          emailAccounts={emailAccounts}
          close={() => {
            setEditing(null);
            setCreatingFor(null);
          }}
          saved={() => {
            setEditing(null);
            setCreatingFor(null);
            reload();
          }}
        />
      )}
      {versionsFor && <VersionsModal creative={versionsFor} close={() => setVersionsFor(null)} />}
      {analyticsFor && (
        <CreativeAnalyticsModal creative={analyticsFor} close={() => setAnalyticsFor(null)} isEmail={isEmail} />
      )}
    </div>
  );
}

function CreativeAnalyticsModal({
  creative,
  close,
  isEmail,
}: {
  creative: AdsCreative;
  close: () => void;
  isEmail: boolean;
}) {
  const [data, setData] = useState<any>(null);
  useEffect(() => {
    adsApi.creativeAnalytics(creative.id).then(setData).catch(() => {});
  }, [creative.id]);
  const maxScore = Math.max(1, ...(data?.peers || []).map((p: any) => p.score || 0));
  return (
    <Modal title={`${creative.name} — analytics`} close={close} wide>
      {!data ? (
        <p className="text-sm text-gray-500">Loading…</p>
      ) : (
        <div className="space-y-4">
          <div className="flex items-center gap-2 flex-wrap">
            <Badge value={data.status} />
            {data.is_winner && <WinnerBadge />}
            {data.needs_more_data && <span className="badge-gray">Needs more sends for a verdict</span>}
          </div>
          {isEmail && data.subject && <p className="text-sm font-medium">{data.subject}</p>}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <Stat label="Delivery rate" value={`${data.stats.delivery_rate}%`} />
            <Stat label="Reply rate" value={`${data.stats.reply_rate}%`} />
            <Stat
              label="Open rate"
              value={`${data.stats.open_rate}%`}
              hint={isEmail ? "Measured by Brevo" : "Replied or tapped the link"}
            />
            <Stat label="Click rate" value={`${data.stats.click_rate}%`} />
            <Stat label="Positive reply rate" value={`${data.stats.positive_reply_rate}%`} />
            <Stat label={isEmail ? "Unsubscribe rate" : "Opt-out rate"} value={`${data.stats.opt_out_rate}%`} />
            <Stat label="Failure rate" value={`${data.stats.failure_rate}%`} />
            <Stat label="Score" value={data.stats.score} icon={<TrendingUp size={17} />} />
          </div>
          <div className="card p-4 !shadow-none border border-gray-200 dark:border-gray-700">
            <h4 className="font-semibold text-sm mb-3">Against the other creatives in this set</h4>
            <div className="space-y-2">
              {data.peers.map((p: any) => (
                <div key={p.id}>
                  <div className="flex justify-between text-sm mb-1">
                    <span className="flex items-center gap-2">
                      {p.name} {p.is_winner && <WinnerBadge />}
                    </span>
                    <span className="text-gray-500">
                      {p.sent} sent · {p.reply_rate}% replies · score {p.score}
                    </span>
                  </div>
                  <Bar value={p.score} max={maxScore} tone={p.is_winner ? "success" : "primary"} />
                </div>
              ))}
            </div>
          </div>
          <div className="grid grid-cols-3 gap-3 text-sm">
            <Metric label="Assigned" value={data.stats.assigned} />
            <Metric label="Pending" value={data.stats.pending} />
            <Metric label="Clicks" value={data.stats.clicks} />
          </div>
        </div>
      )}
    </Modal>
  );
}

function CreativeEditor({
  creative,
  setId,
  isEmail,
  campaignSubject,
  emailAccounts,
  close,
  saved,
}: {
  creative: AdsCreative | null;
  setId: number;
  isEmail: boolean;
  campaignSubject?: string | null;
  emailAccounts: any[];
  close: () => void;
  saved: () => void;
}) {
  const [form, setForm] = useState<any>({
    name: creative?.name || "",
    body: creative?.body || "",
    subject: creative?.subject || "",
    html_body: creative?.html_body || "",
    email_account_id: creative?.email_account_id ?? "",
    cta: creative?.cta || "",
    tracking_link: creative?.tracking_link || "",
    allocation: creative?.allocation ?? 0,
    send_quota: creative?.send_quota ?? "",
    status: creative?.status || "active",
  });
  // Which saved template this creative is written against ("" = none). The
  // message box below is the editor; the template is the "sync" source that
  // can re-fill it in one click when the template changes.
  const [templateId, setTemplateId] = useState(
    creative?.template_id ? String(creative.template_id) : ""
  );
  // Current template metadata once fetched; drives the synced/update banner.
  const [templateMeta, setTemplateMeta] = useState<{
    id: string;
    name: string;
    body: string;
    subject?: string | null;
    html_body?: string | null;
    deleted: boolean;
  } | null>(null);
  const [templateBusy, setTemplateBusy] = useState(false);
  const bodyRef = useRef<HTMLTextAreaElement>(null);
  const set = (k: string, v: any) => setForm((f: any) => ({ ...f, [k]: v }));
  const count = smsCount(form.body);

  // Existing binding: fetch the template's CURRENT text and compare it with
  // the body this creative was last saved with, so edits made on the
  // Templates page surface here as "sync available".
  useEffect(() => {
    if (!creative?.template_id) return;
    let cancelled = false;
    setTemplateBusy(true);
    api
      .get(`/templates/${creative.template_id}`)
      .then(({ data }) => {
        if (cancelled) return;
        setTemplateId(String(data.id));
        setTemplateMeta({
          id: String(data.id),
          name: data.name,
          body: data.body,
          subject: data.subject,
          html_body: data.html_body,
          deleted: false,
        });
      })
      .catch(() => {
        // Template was deleted: the pointer is stale. The save below unbinds
        // so the creative never gets stuck on a template that does not exist.
        if (!cancelled)
          setTemplateMeta({
            id: creative.template_id ? String(creative.template_id) : "",
            name: "",
            body: "",
            deleted: true,
          });
      })
      .finally(() => {
        if (!cancelled) setTemplateBusy(false);
      });
    return () => {
      cancelled = true;
    };
    // Loaded once when the editor opens; creative is fixed for the modal's
    // lifetime (the modal itself is keyed by creative).
  }, []);

  const chooseTemplate = async (id: string) => {
    if (!id) {
      setTemplateId("");
      setTemplateMeta(null);
      return;
    }
    if (id === templateId && templateMeta) return; // reselected — nothing to do
    setTemplateBusy(true);
    try {
      const { data } = await api.get(`/templates/${id}`);
      const existingText = (form.body || "").trim().length > 0;
      // Only ask when applying the template would overwrite text the user
      // typed (a fresh message or a re-pick of the same template is silent).
      if (existingText && form.body !== data.body) {
        if (!window.confirm("Replace the current message text with this template?")) {
          return;
        }
      }
      setForm((f: any) => ({
        ...f,
        body: data.body,
        // Email templates bring their subject + HTML with them; the name is
        // auto-filled but never clobbers a name the user already typed.
        ...(isEmail
          ? {
              subject: f.subject?.trim() ? f.subject : data.subject || "",
              html_body: f.html_body?.trim() ? f.html_body : data.html_body || "",
            }
          : {}),
        name: (f.name || "").trim() ? f.name : data.name,
      }));
      setTemplateMeta({
        id: String(data.id),
        name: data.name,
        body: data.body,
        subject: data.subject,
        html_body: data.html_body,
        deleted: false,
      });
      setTemplateId(String(data.id));
      toast.success(`Template “${data.name}” loaded — edit freely or sync later`);
    } catch {
      toast.error("Could not load that template");
    } finally {
      setTemplateBusy(false);
    }
  };

  // One-click re-sync after the template changed on the Templates page.
  const syncFromTemplate = () => {
    if (!templateMeta || templateMeta.deleted) return;
    setForm((f: any) => ({
      ...f,
      body: templateMeta.body,
      ...(isEmail
        ? {
            subject: templateMeta.subject || f.subject,
            html_body: templateMeta.html_body || f.html_body,
          }
        : {}),
    }));
    toast.success(`Synced to “${templateMeta.name}” — save to create a new version`);
  };

  // Banner logic: the template's live text vs. the text in the composer.
  const originalBody = creative?.body ?? null;
  const originalSubject = creative?.subject ?? null;
  const editedSinceSave =
    originalBody !== null &&
    templateMeta !== null &&
    !templateMeta.deleted &&
    (form.body !== originalBody || (isEmail && form.subject !== (originalSubject || "")));
  const templateChanged =
    originalBody !== null &&
    templateMeta !== null &&
    !templateMeta.deleted &&
    (originalBody !== templateMeta.body ||
      (isEmail && !!templateMeta.subject && (originalSubject || "") !== templateMeta.subject));

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.name.trim()) return toast.error("Add a name");
    if (!(form.body || "").trim() && !(isEmail && (form.html_body || "").trim()))
      return toast.error("Add a message");
    if (isEmail && !(form.subject || "").trim() && !(campaignSubject || "").trim())
      return toast.error("Email creatives need a subject (here or on the campaign)");
    const payload: any = {
      ...form,
      name: form.name.trim(),
      allocation: Number(form.allocation) || 0,
      send_quota: form.send_quota === "" || form.send_quota === null ? null : Math.max(0, Number(form.send_quota) || 0),
      cta: form.cta || null,
      tracking_link: form.tracking_link || null,
      subject: isEmail ? (form.subject?.trim() || null) : form.subject?.trim() || null,
      html_body: isEmail ? (form.html_body?.trim() || null) : null,
      email_account_id:
        isEmail && form.email_account_id !== "" && form.email_account_id != null
          ? Number(form.email_account_id)
          : null,
      // The template the composer is bound to. A binding whose template was
      // deleted (or a cleared picker) saves as null so nothing dangles.
      template_id:
        templateMeta && !templateMeta.deleted && templateId ? Number(templateId) : null,
    };
    try {
      if (creative) await adsApi.updateCreative(creative.id, payload);
      else await adsApi.createCreative(setId, payload);
      toast.success(creative ? "Creative saved as a new version" : "Creative added");
      saved();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not save creative");
    }
  };

  return (
    <Modal title={creative ? "Edit creative" : "New creative"} close={close} wide>
      <form onSubmit={submit} className="space-y-4">
        <Field label="Creative name">
          <input className="input" value={form.name} onChange={(e) => set("name", e.target.value)} autoFocus />
        </Field>

        {/* Template + variables live with the message: pick a saved template
            to start from, insert variables at the caret, count segments. */}
        <div>
          <div className="flex items-center justify-between mb-1.5 flex-wrap gap-1">
            <label className="label !mb-0">Start from a saved template</label>
            <span className="text-[11px] text-gray-400">
              optional — the message below stays fully editable
            </span>
          </div>
          <TemplatePicker
            value={templateId}
            onChange={chooseTemplate}
            channel={isEmail ? "email" : "sms"}
            allowNone
            noneLabel="No template — write from scratch"
            placeholder={isEmail ? "Choose an email template…" : "Choose a template…"}
            showPreview={false}
          />
          {templateBusy && (
            <p className="text-xs text-gray-500 mt-1.5 flex items-center gap-1.5">
              <RefreshCw size={12} className="animate-spin" /> Loading template…
            </p>
          )}

          {templateMeta && templateMeta.deleted && (
            <p className="mt-2 text-xs rounded-lg px-3 py-2 bg-danger-50 dark:bg-danger-900/20 text-danger-700 dark:text-danger-300 flex items-start gap-2">
              <Trash2 size={13} className="mt-0.5 flex-shrink-0" />
              The template this creative started from was deleted. Saving keeps your current text and
              removes the binding.
            </p>
          )}
          {templateMeta && !templateMeta.deleted && templateChanged && !editedSinceSave && (
            <div className="mt-2 flex flex-wrap items-center gap-2 rounded-lg px-3 py-2 bg-warning-50 dark:bg-warning-900/20 text-warning-800 dark:text-warning-200 text-xs">
              <span className="flex-1 min-w-[180px]">
                Template “{templateMeta.name}” was updated after this creative was saved.
              </span>
              <button
                type="button"
                onClick={syncFromTemplate}
                className="px-2.5 py-1 rounded-full bg-warning-500 text-white font-semibold hover:bg-warning-600 flex items-center gap-1"
              >
                <RefreshCw size={12} /> Sync to template
              </button>
            </div>
          )}
          {templateMeta && !templateMeta.deleted && !templateChanged && !editedSinceSave && originalBody !== null && (
            <p className="mt-1.5 text-xs text-gray-500 flex items-center gap-1.5">
              <CheckCircle2 size={13} className="text-success-600" />
              Synced to template “{templateMeta.name}” — the card on the Creatives tab shows the same.
            </p>
          )}
          {editedSinceSave && (
            <p className="mt-1.5 text-xs text-gray-500">
              ✎ Customized copy — your edits only change this creative (a new version on save), never
              the template itself.
            </p>
          )}
        </div>

        {isEmail && (
          <Field
            label="Subject"
            hint={
              campaignSubject
                ? `Blank = the campaign subject (“${campaignSubject}”). Supports {{variables}}.`
                : "Required unless the campaign sets a subject. Supports {{variables}}."
            }
          >
            <input
              className="input"
              value={form.subject}
              onChange={(e) => set("subject", e.target.value)}
              placeholder={campaignSubject || "Hi {{first_name}}, a quick question…"}
            />
          </Field>
        )}

        {isEmail ? (
          <div>
            <label className="label">Message</label>
            <RichEmailEditor
              body={form.body}
              onBody={(v) => set("body", v)}
              html={form.html_body}
              onHtml={(v) => set("html_body", v)}
            />
          </div>
        ) : (
          <div>
            <div className="flex items-center justify-between mb-1.5 flex-wrap gap-1">
              <label className="label !mb-0">Message</label>
              <span className="text-xs text-gray-500">
                {count.chars} chars · {count.segments} SMS{count.segments === 1 ? "" : "s"}
                {count.unicode && " · unicode (70/SMS)"}
                {count.segments > 3 && " · long messages cost more"}
              </span>
            </div>
            <textarea
              ref={bodyRef}
              className="input font-mono text-sm"
              rows={6}
              value={form.body}
              onChange={(e) => set("body", e.target.value)}
              placeholder="Hi {{first_name}}, I came across {{business_name}} and had a quick question…"
            />
            <div className="flex flex-wrap items-center gap-1.5 mt-2">
              <ShortcodePicker
                targetRef={bodyRef}
                value={form.body}
                onChange={(v) => set("body", v)}
                label="Insert variable"
              />
              {[
                "{{first_name}}",
                "{{last_name}}",
                "{{business_name}}",
                "{{phone_number}}",
                "{{city}}",
                "{{state}}",
                "{{website}}",
                "{{industry}}",
              ].map((v) => (
                <button
                  key={v}
                  type="button"
                  onClick={() => set("body", (form.body ? form.body + " " : "") + v)}
                  className="text-xs px-2 py-0.5 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 rounded font-mono text-gray-600 dark:text-gray-300"
                  title={`Insert ${v} at the end`}
                >
                  {v}
                </button>
              ))}
            </div>
          </div>
        )}

        <div className="grid sm:grid-cols-2 gap-3">
          {isEmail && emailAccounts.length > 0 && (
            <Field
              label="Send this creative through"
              hint="Override the campaign sender — lets an A/B test compare two domains."
            >
              <select
                className="input"
                value={form.email_account_id}
                onChange={(e) => set("email_account_id", e.target.value)}
              >
                <option value="">Campaign sender</option>
                {emailAccounts.map((a: any) => (
                  <option key={a.id} value={a.id}>
                    {a.name} — {a.from_email}
                  </option>
                ))}
              </select>
            </Field>
          )}
          <Field label="Call to action">
            <input className="input" value={form.cta} onChange={(e) => set("cta", e.target.value)} />
          </Field>
          <Field
            label="Tracking link"
            hint={
              isEmail
                ? "Optional note for this creative — opens and clicks are measured by Brevo."
                : "Taps are counted into click rate and open rate."
            }
          >
            <input
              className="input"
              value={form.tracking_link}
              onChange={(e) => set("tracking_link", e.target.value)}
              placeholder="https://…"
            />
          </Field>
          <Field label="Allocation % / weight" hint="Used with percentage / weighted split.">
            <input
              type="number"
              className="input"
              value={form.allocation}
              onChange={(e) => set("allocation", e.target.value)}
            />
          </Field>
          <Field
            label={isEmail ? "Exact email count" : "Exact SMS count"}
            hint={`Used with '${isEmail ? "Exact email count" : "Exact SMS count"}' split. Blank = no cap (shares the remainder).`}
          >
            <input
              type="number"
              min={0}
              className="input"
              placeholder="e.g. 500"
              value={form.send_quota}
              onChange={(e) => set("send_quota", e.target.value)}
            />
          </Field>
          <Field label="Status">
            <select className="input" value={form.status} onChange={(e) => set("status", e.target.value)}>
              <option value="active">Active</option>
              <option value="draft">Draft</option>
              <option value="paused">Paused</option>
              <option value="archived">Archived</option>
            </select>
          </Field>
        </div>
        {creative && (
          <p className="text-xs text-gray-500">
            Editing the {isEmail ? "subject or message" : "text"} creates version{" "}
            {creative.current_version + 1}. Messages already sent keep their version, so historical
            analytics do not change.
          </p>
        )}
        <div className="flex gap-2">
          <button type="button" className="btn-secondary flex-1" onClick={close}>
            Cancel
          </button>
          <button className="btn-primary flex-1" disabled={templateBusy}>
            {creative ? "Save creative" : "Add creative"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function VersionsModal({ creative, close }: { creative: AdsCreative; close: () => void }) {
  const [items, setItems] = useState<any[]>([]);
  useEffect(() => {
    adsApi.creativeVersions(creative.id).then((r) => setItems(r.items));
  }, [creative.id]);
  return (
    <Modal title={`${creative.name} — versions`} close={close}>
      <div className="space-y-3">
        {items.map((v) => (
          <div key={v.id} className="p-3 rounded-lg bg-gray-50 dark:bg-gray-700/50">
            <div className="flex justify-between text-sm">
              <b>Version {v.version}</b>
              <span className="text-gray-500">{v.sent} sent</span>
            </div>
            {v.subject && <p className="text-sm font-medium mt-2">{v.subject}</p>}
            <p className="text-sm text-gray-600 dark:text-gray-300 mt-1 whitespace-pre-wrap">{v.body}</p>
            {v.html_body && <span className="badge-blue mt-1 inline-block">rich HTML</span>}
            <p className="text-xs text-gray-400 mt-1">{fmtDate(v.created_at)}</p>
          </div>
        ))}
      </div>
    </Modal>
  );
}

/* --------------------------------------------------------------- audience */

const REMOVABLE = new Set(["pending", "skipped", "cancelled", "blocked"]);

function AudienceTab({
  detail,
  reference,
  reload,
  isEmail,
}: {
  detail: Detail;
  reference: any;
  reload: () => void;
  isEmail: boolean;
}) {
  const [rows, setRows] = useState<any>({ items: [], total: 0, removable: 0 });
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("");
  const [adding, setAdding] = useState(false);
  const [selected, setSelected] = useState<number[]>([]);
  // "Select all unsent" mode: every removable row of the current view
  // (all pages, not just this page) is part of the delete.
  const [allRemovable, setAllRemovable] = useState(false);

  const load = useCallback(async () => {
    const data = await adsApi.audience(detail.id, { page, per_page: 50, status: status || undefined });
    setRows(data);
    setSelected([]);
    setAllRemovable(false);
  }, [detail.id, page, status]);

  useEffect(() => {
    load();
  }, [load]);

  const removableOnPage = rows.items.filter((r: any) => REMOVABLE.has(r.send_status));
  const pageRemovableAll =
    removableOnPage.length > 0 && removableOnPage.every((r: any) => selected.includes(r.id));
  const removableTotal: number = rows.removable ?? 0;
  const bulkCount = allRemovable ? removableTotal : selected.length;

  // Tapping a row while in "all unsent" mode drops out of that mode and keeps
  // the removable rows on this page (minus the tapped one) as the selection.
  const toggle = (id: number) => {
    if (allRemovable) {
      const pageIds: number[] = removableOnPage.map((r: any) => r.id);
      setAllRemovable(false);
      setSelected(pageIds.includes(id) ? pageIds.filter((x) => x !== id) : pageIds);
      return;
    }
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
  };

  const removeOne = async (row: any) => {
    if (!REMOVABLE.has(row.send_status)) {
      toast.error("Already sent — this row is analytics history. Suppress the contact to block future sends.");
      return;
    }
    if (!confirm(`Remove ${row.name || row.phone_number} from this campaign's audience?`)) return;
    try {
      await adsApi.removeAudienceContact(detail.id, row.id);
      toast.success("Contact removed from audience");
      load();
      reload();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not remove contact");
    }
  };

  const bulkRemove = async () => {
    if (bulkCount === 0) return;
    if (!confirm(`Remove ${bulkCount} unsent contact(s) from this campaign's audience?`)) return;
    try {
      // allRemovable = server-side scope ("all" + the current status filter),
      // so it covers every page without enumerating ids.
      const r = await adsApi.bulkRemoveAudience(detail.id, selected, allRemovable, status || undefined);
      toast.success(
        `Removed ${r.removed}${r.skipped_sent ? `, skipped ${r.skipped_sent} already sent` : ""}`
      );
      load();
      reload();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not remove contacts");
    }
  };

  const clearSelection = () => {
    setSelected([]);
    setAllRemovable(false);
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2 justify-between items-center">
        <div className="flex gap-2 items-center flex-wrap">
          <select className="input !py-2 !w-auto" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statuses</option>
            {["pending", "sent", "delivered", "failed", "skipped", "cancelled"].map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <span className="text-sm text-gray-500">
            {rows.total} contacts{removableTotal > 0 ? ` · ${removableTotal} unsent` : ""}
          </span>
          {removableTotal > 0 && !allRemovable && selected.length === 0 && (
            <button
              className="text-xs font-semibold text-primary-600 hover:underline"
              title="Ticks every contact that has not been sent to yet, across all pages of this view"
              onClick={() => setAllRemovable(true)}
            >
              Select all {removableTotal} unsent
            </button>
          )}
        </div>
        <div className="flex gap-2">
          <a className="btn-secondary btn-sm" href={adsApi.exportUrl("audience", detail.id)}>
            <Download size={15} className="mr-1" /> Export
          </a>
          <button
            className="btn-secondary btn-sm"
            onClick={async () => {
              const r = await adsApi.rebuild(detail.id);
              toast.success(`${r.added} contacts added`);
              load();
              reload();
            }}
          >
            Refresh audience
          </button>
          <button className="btn-primary btn-sm" onClick={() => setAdding(true)}>
            <Plus size={15} className="mr-1" /> Add contacts
          </button>
        </div>
      </div>

      {/* Bulk-remove bar: ticked contacts, or every unsent contact (all pages) */}
      {(selected.length > 0 || allRemovable) && (
        <div className="flex flex-wrap items-center gap-2 justify-between rounded-lg border border-danger-200 dark:border-danger-900/50 bg-danger-50 dark:bg-danger-950/30 px-3 py-2">
          <span className="text-sm font-semibold text-danger-700 dark:text-danger-300 flex items-center gap-2 flex-wrap">
            <button onClick={clearSelection} className="w-6 h-6 rounded-full bg-danger-100 dark:bg-danger-900/40 flex items-center justify-center">
              <X size={13} />
            </button>
            {allRemovable
              ? `${bulkCount} unsent contact${bulkCount === 1 ? "" : "s"} selected (all pages)`
              : `${selected.length} selected`}
          </span>
          <div className="flex items-center gap-2 flex-wrap">
            {!allRemovable && selected.length > 0 && removableTotal > selected.length && (
              <button
                className="btn-secondary btn-sm"
                onClick={() => setAllRemovable(true)}
              >
                Select all {removableTotal} unsent
              </button>
            )}
            <button className="btn-danger btn-sm" onClick={bulkRemove}>
              <Trash2 size={14} className="mr-1" /> Remove {bulkCount}
            </button>
          </div>
        </div>
      )}

      {rows.items.length === 0 ? (
        <Empty
          title="No contacts in this campaign yet"
          body={
            isEmail
              ? "Add a list to an email set (Email Sets tab), attach a saved audience, or add contacts directly."
              : "Add a list to an SMS set (SMS Sets tab), attach a saved audience, or add contacts directly."
          }
        />
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-gray-500 border-b border-gray-100 dark:border-gray-700">
              <tr>
                <th className="p-3">
                  <input
                    type="checkbox"
                    title={
                      allRemovable
                        ? "Clear selection"
                        : pageRemovableAll && removableTotal > removableOnPage.length
                          ? `Select all ${removableTotal} unsent contacts (every page)`
                          : "Select the unsent contacts on this page"
                    }
                    checked={allRemovable || pageRemovableAll}
                    onChange={() => {
                      if (allRemovable) {
                        clearSelection();
                        return;
                      }
                      if (pageRemovableAll) {
                        // Second click on "select all" escalates to every
                        // unsent contact in this view, on every page.
                        if (removableTotal > removableOnPage.length) setAllRemovable(true);
                        else setSelected([]);
                      } else {
                        setSelected(removableOnPage.map((r: any) => r.id));
                      }
                    }}
                  />
                </th>
                <th className="p-3">Contact</th>
                {isEmail ? <th className="p-3">Email</th> : <th className="p-3">Phone</th>}
                <th className="p-3">Creative</th>
                <th className="p-3">Status</th>
                <th className="p-3">Reply</th>
                <th className="p-3">Sent</th>
                <th className="p-3" />
              </tr>
            </thead>
            <tbody>
              {rows.items.map((r: any) => {
                const canRemove = REMOVABLE.has(r.send_status);
                return (
                <tr key={r.id} className="border-b border-gray-50 dark:border-gray-700/50">
                  <td className="p-3">
                    <input
                      type="checkbox"
                      disabled={!canRemove}
                      title={canRemove ? "Select to remove from this campaign" : "Already sent — locked history"}
                      checked={allRemovable || selected.includes(r.id)}
                      onChange={() => toggle(r.id)}
                    />
                  </td>
                  <td className="p-3">{r.name || "—"}</td>
                  <td className="p-3 whitespace-nowrap">{isEmail ? r.email_address || "—" : r.phone_number}</td>
                  <td className="p-3">{r.creative || "—"}</td>
                  <td className="p-3">
                    <Badge value={r.send_status} />
                    {r.skip_reason && (
                      <span className="block text-xs text-gray-400 mt-1">{r.skip_reason.replace(/_/g, " ")}</span>
                    )}
                  </td>
                  <td className="p-3">{r.reply_status || "—"}</td>
                  <td className="p-3 whitespace-nowrap">{r.sent_at ? fmtDate(r.sent_at) : "—"}</td>
                  <td className="p-3">
                    {REMOVABLE.has(r.send_status) ? (
                      <button
                        className="btn-ghost btn-sm text-danger-600"
                        title="Remove from audience (not sent yet)"
                        onClick={() => removeOne(r)}
                      >
                        <Trash2 size={14} />
                      </button>
                    ) : (
                      <span className="text-xs text-gray-400" title="Already sent — protected history">
                        locked
                      </span>
                    )}
                  </td>
                </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {rows.total > 50 && (
        <div className="flex justify-center gap-2">
          <button className="btn-secondary btn-sm" disabled={page === 1} onClick={() => setPage((p) => p - 1)}>
            Previous
          </button>
          <span className="text-sm text-gray-500 self-center">Page {page}</span>
          <button
            className="btn-secondary btn-sm"
            disabled={page * 50 >= rows.total}
            onClick={() => setPage((p) => p + 1)}
          >
            Next
          </button>
        </div>
      )}
      {adding && (
        <AddContactsModal
          detail={detail}
          reference={reference}
          isEmail={isEmail}
          close={() => setAdding(false)}
          done={() => {
            setAdding(false);
            load();
            reload();
          }}
        />
      )}
    </div>
  );
}

function AddContactsModal({
  detail,
  reference,
  isEmail,
  close,
  done,
}: {
  detail: Detail;
  reference: any;
  isEmail: boolean;
  close: () => void;
  done: () => void;
}) {
  const [setId, setSetId] = useState<number | "">(detail.sets[0]?.id ?? "");
  const [lists, setLists] = useState<number[]>([]);
  const [contactIds, setContactIds] = useState<number[]>([]);
  const [tags, setTags] = useState("");
  const [result, setResult] = useState<any>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (lists.length === 0 && contactIds.length === 0 && !tags.trim()) {
      toast.error("Pick at least one list, contact or tag — nothing is added automatically.");
      return;
    }
    setBusy(true);
    try {
      const r = await adsApi.addContacts(detail.id, {
        set_id: setId || null,
        list_ids: lists,
        tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
        contact_ids: contactIds,
      });
      setResult(r);
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not add contacts");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title="Add contacts" close={close}>
      {result ? (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <Metric label="Selected" value={result.selected} />
            <Metric label="Added" value={result.added} />
          </div>
          <div className="p-3 rounded-lg bg-gray-50 dark:bg-gray-700/50 text-sm space-y-1">
            <p className="font-medium mb-1">Skipped</p>
            {Object.keys(result.skipped || {}).length === 0 ? (
              <p className="text-gray-500">Nothing skipped.</p>
            ) : (
              Object.entries(result.skipped).map(([k, v]: any) => (
                <p key={k} className="text-gray-600 dark:text-gray-300">
                  {k.replace(/_/g, " ")}: <b>{v}</b>
                </p>
              ))
            )}
          </div>
          <button className="btn-primary w-full" onClick={done}>
            Done
          </button>
        </div>
      ) : (
        <div className="space-y-4">
          <Field label={isEmail ? "Add to email set" : "Add to SMS set"}>
            <select className="input" value={setId} onChange={(e) => setSetId(Number(e.target.value))}>
              {detail.sets.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </Field>
          <div>
            <span className="label">Lists — only what you pick is added</span>
            <div className="flex flex-wrap gap-2">
              {(reference?.lists || []).map((l: any) => {
                const on = lists.includes(l.id);
                return (
                  <button
                    key={l.id}
                    type="button"
                    onClick={() => setLists(on ? lists.filter((x) => x !== l.id) : [...lists, l.id])}
                    className={`px-3 py-1.5 rounded-lg text-sm border ${
                      on
                        ? "bg-primary-600 text-white border-primary-600"
                        : "border-gray-300 dark:border-gray-600"
                    }`}
                  >
                    {l.name} ({l.count})
                  </button>
                );
              })}
              {(reference?.lists || []).length === 0 && (
                <p className="text-sm text-gray-500">No lists yet.</p>
              )}
            </div>
          </div>
          <div>
            <span className="label">Individual contacts</span>
            <ContactPicker value={contactIds} onChange={setContactIds} placeholder="Search contacts to add…" />
          </div>
          <Field label="Tags" hint="Comma separated. Contacts with any of these tags are considered.">
            <input className="input" value={tags} onChange={(e) => setTags(e.target.value)} />
          </Field>
          <p className="text-xs text-gray-500">
            Only the lists, contacts and tags you pick are evaluated. Duplicates, suppressed contacts,
            opt-outs and anyone already in this campaign are filtered out automatically — you will see
            exactly why on the next screen.
          </p>
          <div className="flex gap-2">
            <button className="btn-secondary flex-1" onClick={close}>
              Cancel
            </button>
            <button className="btn-primary flex-1" onClick={submit} disabled={busy}>
              {busy ? "Evaluating…" : "Add contacts"}
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}

function SendingTab({ detail, stats, isEmail }: { detail: Detail; stats: any; isEmail: boolean }) {
  return (
    <div className="space-y-3">
      <div className="card p-5 grid sm:grid-cols-2 lg:grid-cols-3 gap-5">
        <Metric label="Status" value={<Badge value={detail.state || detail.status} />} />
        <Metric label={isEmail ? "Daily email limit" : "Daily SMS limit"} value={detail.daily_limit ?? "No limit"} />
        <Metric label={isEmail ? "Total email limit" : "Total SMS limit"} value={detail.total_limit ?? "No limit"} />
        <Metric
          label="Sending window"
          value={
            detail.send_start_hour != null && detail.send_end_hour != null
              ? `${detail.send_start_hour}:00 – ${detail.send_end_hour}:00`
              : "Any time"
          }
        />
        <Metric label="Days" value={detail.send_days || "Every day"} />
        <Metric label="Timezone" value={detail.timezone_name || "Deployment default"} />
        <Metric
          label="Drip"
          value={
            detail.drip_mode === "off"
              ? "Off"
              : detail.drip_mode === "interval"
              ? `1 every ${detail.drip_interval_minutes} min`
              : detail.drip_mode === "batch"
              ? `${detail.drip_batch_size} every ${detail.drip_interval_minutes} min`
              : detail.drip_mode
          }
        />
        <Metric label="Continuous" value={detail.continuous ? "Yes" : "No"} />
        <Metric label="Always on" value={detail.always_on ? "Yes" : "No"} />
        <Metric label="Priority" value={detail.priority} />
        <Metric label="Pending in queue" value={stats?.pending ?? 0} />
        <Metric label="Sent so far" value={stats?.sent ?? 0} />
      </div>
      <div className="card p-5 text-sm text-gray-500 space-y-2">
        <p>
          {isEmail ? (
            <>
              Every email is re-validated immediately before it is sent: unsubscribed, suppression list,
              hard bounce, duplicate protection, frequency caps, campaign and creative status, sending
              window and sender health.
            </>
          ) : (
            <>
              Every message is re-validated immediately before it is sent: opt-out, suppression list,
              duplicate protection, frequency caps, campaign and creative status, sending window and SMS
              balance.
            </>
          )}
        </p>
        <p>
          Sending runs in the background — you can close this page. Global sending limits and pacing from
          Settings still apply.
        </p>
      </div>
    </div>
  );
}

function AutomationTab({ detail, reload, isEmail }: { detail: Detail; reload: () => void; isEmail: boolean }) {
  const [editing, setEditing] = useState<any>(null);
  const [creating, setCreating] = useState(false);

  return (
    <div className="space-y-3">
      <div className="flex justify-between items-center">
        <h2 className="font-semibold">Follow-up workflow</h2>
        <button className="btn-primary btn-sm" onClick={() => setCreating(true)}>
          <Plus size={15} className="mr-1" /> Add step
        </button>
      </div>
      {detail.followup_steps.length === 0 ? (
        <Empty
          icon={<Wand2 size={40} />}
          title="No follow-ups yet"
          body={
            isEmail
              ? "Chain automatic follow-ups: wait, check a condition, then send. They stop automatically when a contact replies, unsubscribes, bounces or converts."
              : "Chain automatic follow-ups: wait, check a condition, then send. They stop automatically when a contact replies, opts out or converts."
          }
          action={
            <button className="btn-primary" onClick={() => setCreating(true)}>
              <Plus size={16} className="mr-1" /> Add first step
            </button>
          }
        />
      ) : (
        detail.followup_steps.map((s) => (
          <div className="card p-4" key={s.id}>
            <div className="flex flex-wrap gap-3 justify-between items-start">
              <div>
                <div className="flex items-center gap-2">
                  <span className="w-6 h-6 rounded-full bg-primary-100 dark:bg-primary-900 text-primary-700 dark:text-primary-200 text-xs font-bold flex items-center justify-center">
                    {s.step_order}
                  </span>
                  <h4 className="font-semibold">{s.name || `Follow-up ${s.step_order}`}</h4>
                  {!s.is_active && <span className="badge-gray">Disabled</span>}
                </div>
                <p className="text-sm text-gray-500 mt-2">
                  Wait <b>{s.wait_hours}h</b> → if <b>{s.condition.replace(/_/g, " ")}</b> →{" "}
                  <b>{(isEmail && s.action === "send_sms" ? "send_email" : s.action).replace(/_/g, " ")}</b>
                  {s.action_value ? ` (${s.action_value})` : ""}
                </p>
                {isEmail && s.subject && (
                  <p className="text-sm font-medium mt-2">{s.subject}</p>
                )}
                {s.body && (
                  <p className="text-sm text-gray-600 dark:text-gray-300 mt-1 whitespace-pre-wrap">{s.body}</p>
                )}
              </div>
              <div className="flex gap-2">
                <button className="btn-secondary btn-sm" onClick={() => setEditing(s)}>
                  Edit
                </button>
                <button
                  className="btn-ghost btn-sm text-danger-600"
                  onClick={async () => {
                    if (!confirm("Remove this follow-up step?")) return;
                    await adsApi.deleteStep(s.id);
                    toast.success("Step removed");
                    reload();
                  }}
                >
                  <Trash2 size={15} />
                </button>
              </div>
            </div>
          </div>
        ))
      )}
      {(creating || editing) && (
        <StepEditor
          campaignId={detail.id}
          step={editing}
          nextOrder={detail.followup_steps.length + 1}
          isEmail={isEmail}
          campaignSubject={detail.subject}
          close={() => {
            setCreating(false);
            setEditing(null);
          }}
          saved={() => {
            setCreating(false);
            setEditing(null);
            reload();
          }}
        />
      )}
    </div>
  );
}

function StepEditor({
  campaignId,
  step,
  nextOrder,
  isEmail,
  campaignSubject,
  close,
  saved,
}: {
  campaignId: number;
  step: any;
  nextOrder: number;
  isEmail: boolean;
  campaignSubject?: string | null;
  close: () => void;
  saved: () => void;
}) {
  const [form, setForm] = useState<any>({
    step_order: step?.step_order ?? nextOrder,
    name: step?.name || "",
    wait_hours: step?.wait_hours ?? 48,
    condition: step?.condition || "no_reply",
    action: step?.action || (isEmail ? "send_email" : "send_sms"),
    body: step?.body || "",
    subject: step?.subject || "",
    action_value: step?.action_value || "",
    is_active: step?.is_active ?? true,
  });
  const set = (k: string, v: any) => setForm((f: any) => ({ ...f, [k]: v }));
  const isSend = form.action === "send_sms" || form.action === "send_email";

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (isSend && !form.body.trim()) return toast.error("Write the follow-up message");
    const payload = {
      ...form,
      step_order: Number(form.step_order),
      wait_hours: Number(form.wait_hours),
      name: form.name || null,
      body: form.body || null,
      subject: isEmail ? form.subject?.trim() || null : step?.subject ?? null,
      action_value: form.action_value || null,
    };
    try {
      if (step) await adsApi.updateStep(step.id, payload);
      else await adsApi.createStep(campaignId, payload);
      toast.success("Follow-up saved");
      saved();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not save step");
    }
  };

  return (
    <Modal title={step ? "Edit follow-up" : "New follow-up step"} close={close}>
      <form onSubmit={submit} className="space-y-4">
        <div className="grid sm:grid-cols-2 gap-3">
          <Field label="Step name">
            <input className="input" value={form.name} onChange={(e) => set("name", e.target.value)} />
          </Field>
          <Field label="Order">
            <input
              type="number"
              min={1}
              className="input"
              value={form.step_order}
              onChange={(e) => set("step_order", e.target.value)}
            />
          </Field>
          <Field label="Wait (hours after the previous message)">
            <input
              type="number"
              min={0}
              className="input"
              value={form.wait_hours}
              onChange={(e) => set("wait_hours", e.target.value)}
            />
          </Field>
          <Field label="Condition">
            <select className="input" value={form.condition} onChange={(e) => set("condition", e.target.value)}>
              {[
                "no_reply",
                "replied",
                "positive",
                "negative",
                "interested",
                "not_interested",
                ...(isEmail ? ["opened", "not_opened", "link_clicked", "link_not_clicked"] : ["link_clicked", "link_not_clicked"]),
                "meeting_scheduled",
                "converted",
                "always",
              ].map((c) => (
                <option key={c} value={c}>
                  {c.replace(/_/g, " ")}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Action">
            <select className="input" value={form.action} onChange={(e) => set("action", e.target.value)}>
              {(isEmail
                ? ["send_email", "add_tag", "remove_tag", "change_status", "suppress", "stop"]
                : ["send_sms", "add_tag", "remove_tag", "change_status", "suppress", "stop"]
              ).map((a) => (
                <option key={a} value={a}>
                  {a.replace(/_/g, " ")}
                </option>
              ))}
            </select>
          </Field>
          {["add_tag", "remove_tag", "change_status"].includes(form.action) && (
            <Field label="Value">
              <input
                className="input"
                value={form.action_value}
                onChange={(e) => set("action_value", e.target.value)}
              />
            </Field>
          )}
        </div>
        {isSend && isEmail && (
          <Field
            label="Subject"
            hint={campaignSubject ? `Blank = the campaign subject (“${campaignSubject}”).` : undefined}
          >
            <input
              className="input"
              value={form.subject}
              onChange={(e) => set("subject", e.target.value)}
              placeholder={campaignSubject || "Re: …"}
            />
          </Field>
        )}
        {isSend && (
          <Field label="Message">
            <textarea className="input" rows={4} value={form.body} onChange={(e) => set("body", e.target.value)} />
          </Field>
        )}
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={form.is_active} onChange={(e) => set("is_active", e.target.checked)} />
          Active
        </label>
        <div className="flex gap-2">
          <button type="button" className="btn-secondary flex-1" onClick={close}>
            Cancel
          </button>
          <button className="btn-primary flex-1">Save step</button>
        </div>
      </form>
    </Modal>
  );
}

/* ------------------------------------------------------------ optimization */

const METRIC_LABELS: Record<string, string> = {
  score: "Performance score",
  reply_rate: "Reply rate",
  positive_reply_rate: "Positive reply rate",
  conversion_rate: "Conversion rate",
};

function OptimizationTab({
  detail,
  reference,
  reload,
  onChanged,
}: {
  detail: Detail;
  reference: any;
  reload: () => void;
  onChanged: () => void;
}) {
  const [plan, setPlan] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState<any>({
    optimize_metric: detail.optimize_metric || "score",
    optimize_min_sends: detail.optimize_min_sends ?? 30,
    optimize_min_gap_pct: detail.optimize_min_gap_pct ?? 25,
    optimize_action: detail.optimize_action || "shift",
  });

  const loadPlan = useCallback(async () => {
    try {
      setPlan(await adsApi.optimizationStatus(detail.id));
    } catch {
      /* best-effort */
    }
  }, [detail.id]);

  useEffect(() => {
    loadPlan();
  }, [loadPlan]);

  const saveStrategy = async () => {
    setBusy(true);
    try {
      await adsApi.updateCampaign(detail.id, {
        optimize_metric: form.optimize_metric,
        optimize_min_sends: Number(form.optimize_min_sends) || 30,
        optimize_min_gap_pct: Number(form.optimize_min_gap_pct) || 0,
        optimize_action: form.optimize_action,
      });
      toast.success("Optimization strategy saved");
      reload();
      onChanged();
      loadPlan();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not save strategy");
    } finally {
      setBusy(false);
    }
  };

  const toggleMaster = async (next: boolean) => {
    try {
      await adsApi.updateCampaign(detail.id, { auto_optimize: next });
      toast.success(next ? "Andromeda auto-optimization is ON" : "Andromeda auto-optimization is OFF");
      reload();
      onChanged();
      loadPlan();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not update");
    }
  };

  const runNow = async (dryRun: boolean) => {
    setBusy(true);
    try {
      const r = await adsApi.runOptimization(detail.id, dryRun);
      if (dryRun) {
        setPlan(r.plan);
        toast.success(
          r.plan.total_moveable > 0
            ? `Preview: ${r.plan.total_moveable} contact(s) would move to winner(s)`
            : "Preview: nothing would move right now"
        );
      } else {
        toast.success(`Optimized: ${r.moved} moved, ${r.paused.length} paused`);
        reload();
        onChanged();
        loadPlan();
      }
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Optimization failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="card p-5 space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 className="font-semibold flex items-center gap-2">
              <Bot size={18} className="text-primary-600" /> Andromeda auto-optimization
            </h3>
            <p className="text-sm text-gray-500 mt-1 max-w-2xl">
              When ON, the winning creative in each participating set automatically receives the remaining
              contacts (spend) while losers are left behind — like Meta's Andromeda. Nothing automatic ever
              runs while this switch is OFF.
            </p>
          </div>
          <Toggle
            checked={!!detail.auto_optimize}
            onChange={toggleMaster}
            label={detail.auto_optimize ? "ON" : "OFF"}
          />
        </div>

        <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-3">
          <Field label="Winning metric">
            <select
              className="input"
              value={form.optimize_metric}
              onChange={(e) => setForm({ ...form, optimize_metric: e.target.value })}
            >
              {Object.entries(METRIC_LABELS).map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Min sends per creative" hint="No decisions on noise.">
            <input
              type="number"
              min={1}
              className="input"
              value={form.optimize_min_sends}
              onChange={(e) => setForm({ ...form, optimize_min_sends: e.target.value })}
            />
          </Field>
          <Field label="Min winning gap %" hint="Winner must beat losers by this much.">
            <input
              type="number"
              min={0}
              className="input"
              value={form.optimize_min_gap_pct}
              onChange={(e) => setForm({ ...form, optimize_min_gap_pct: e.target.value })}
            />
          </Field>
          <Field label="Action on losers">
            <select
              className="input"
              value={form.optimize_action}
              onChange={(e) => setForm({ ...form, optimize_action: e.target.value })}
            >
              <option value="shift">Shift spend, keep learning</option>
              <option value="shift_and_pause">Shift spend + pause losers</option>
            </select>
          </Field>
        </div>

        <div className="flex flex-wrap gap-2">
          <button className="btn-secondary btn-sm" disabled={busy} onClick={saveStrategy}>
            Save strategy
          </button>
          <button className="btn-secondary btn-sm" disabled={busy} onClick={() => runNow(true)}>
            Preview what would happen
          </button>
          <button
            className="btn-primary btn-sm"
            disabled={busy || !detail.auto_optimize}
            title={detail.auto_optimize ? "Run one optimization pass now" : "Turn the master switch ON first"}
            onClick={() => runNow(false)}
          >
            <Zap size={15} className="mr-1" /> Optimize now
          </button>
        </div>
        {plan?.last_run_at && (
          <p className="text-xs text-gray-500">Last automatic run: {fmtDate(plan.last_run_at)}</p>
        )}
      </div>

      <div className="card p-5">
        <h3 className="font-semibold mb-2">The strategy</h3>
        <ol className="text-sm text-gray-600 dark:text-gray-300 space-y-1 list-decimal list-inside">
          <li>Every active creative is scored on <b>{METRIC_LABELS[plan?.metric || "score"]}</b>.</li>
          <li>A creative can only win or lose after <b>{plan?.min_sends ?? 30} sends</b> — no verdicts on noise.</li>
          <li>The winner must beat each loser by at least <b>{plan?.min_gap_pct ?? 25}%</b>.</li>
          <li>Unsent contacts on losing creatives move to the winner — sent history is never touched.</li>
          <li>
            {plan?.action === "shift_and_pause"
              ? "Losers are paused and future splits go 100% to the winner."
              : "Nobody is paused: future splits rebalance toward the winner while losers keep a learning trickle."}
          </li>
          <li>Sets with their placement toggle OFF are skipped entirely.</li>
        </ol>
      </div>

      {plan && (
        <div className="space-y-3">
          <h3 className="font-semibold">
            Right now {plan.total_moveable > 0 ? `— ${plan.total_moveable} contact(s) would move` : "— nothing to move"}
          </h3>
          {plan.per_set.map((ps: any) => (
            <div className="card p-4" key={ps.set_id}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <h4 className="font-semibold">{ps.set_name}</h4>
                  {!ps.set_auto_optimize && <span className="badge-gray">Placement off</span>}
                </div>
                {ps.winner_id ? (
                  <WinnerBadge label={`${ps.winner_name} leads`} />
                ) : (
                  <span className="text-xs text-gray-500">{ps.reason}</span>
                )}
              </div>
              {ps.eligible && ps.losers.length > 0 && (
                <div className="mt-3 space-y-1.5 text-sm">
                  {ps.losers.map((l: any) => (
                    <div
                      key={l.creative_id}
                      className="flex flex-wrap justify-between gap-2 p-2 rounded-lg bg-gray-50 dark:bg-gray-700/50"
                    >
                      <span>
                        {l.name} —{" "}
                        <span className={l.action === "shift" ? "text-primary-600 font-medium" : "text-gray-500"}>
                          {l.action === "shift" ? `would move ${l.pending} contact(s)` : "hold"}
                        </span>
                      </span>
                      <span className="text-xs text-gray-500">{l.reason}</span>
                    </div>
                  ))}
                </div>
              )}
              {ps.eligible && ps.set_auto_optimize && (
                <div className="mt-3">
                  <Toggle
                    checked={true}
                    onChange={async () => {
                      const s = detail.sets.find((x) => x.id === ps.set_id);
                      if (!s) return;
                      await adsApi.updateSet(s.id, { auto_optimize: false });
                      toast.success(`Andromeda placement OFF for “${s.name}”`);
                      reload();
                      loadPlan();
                    }}
                    label="Placement for this set"
                    hint="Turn off to exclude this set from automatic shifts."
                  />
                </div>
              )}
              {ps.eligible === false && ps.set_auto_optimize && ps.reason?.includes("turned off") === false && ps.set_status === "active" && null}
              {!ps.set_auto_optimize && (
                <div className="mt-3">
                  <button
                    className="btn-secondary btn-sm"
                    onClick={async () => {
                      const s = detail.sets.find((x) => x.id === ps.set_id);
                      if (!s) return;
                      await adsApi.updateSet(s.id, { auto_optimize: true });
                      toast.success(`Andromeda placement ON for “${s.name}”`);
                      reload();
                      loadPlan();
                    }}
                  >
                    Turn placement on for this set
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* --------------------------------------------------------------- analytics */

function AnalyticsTab({
  analytics,
  campaignId,
  isEmail,
}: {
  analytics: any;
  campaignId: number;
  isEmail: boolean;
}) {
  if (!analytics) return null;
  const c = analytics.campaign;
  const maxSetScore = Math.max(1, ...analytics.sets.map((s: any) => s.score || 0));
  return (
    <div className="space-y-4">
      <div className="flex justify-end gap-2">
        <a className="btn-secondary btn-sm" href={adsApi.exportUrl("creatives", campaignId)}>
          <Download size={15} className="mr-1" /> Export creatives
        </a>
      </div>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Stat label="Delivery rate" value={`${c.delivery_rate}%`} />
        <Stat label="Reply rate" value={`${c.reply_rate}%`} />
        <Stat
          label="Open rate"
          value={`${c.open_rate ?? 0}%`}
          hint={isEmail ? "Measured by Brevo" : "Replied or tapped the link"}
        />
        <Stat label="Click rate" value={`${c.click_rate ?? 0}%`} />
        <Stat label="Positive reply rate" value={`${c.positive_reply_rate}%`} />
        <Stat label="Conversion rate" value={`${c.conversion_rate}%`} />
        <Stat label="Meeting rate" value={`${c.meeting_rate}%`} />
        <Stat label={isEmail ? "Unsubscribe rate" : "Opt-out rate"} value={`${c.opt_out_rate}%`} />
        <Stat label="Failure rate" value={`${c.failure_rate}%`} />
        <Stat label="Score" value={c.score} icon={<TrendingUp size={17} />} />
      </div>

      <div className="card p-5">
        <h3 className="font-semibold mb-3">{isEmail ? "Email set performance" : "SMS set performance"}</h3>
        <div className="space-y-3">
          {analytics.sets.map((s: any) => (
            <div key={s.id}>
              <div className="flex justify-between text-sm mb-1">
                <span className="flex items-center gap-2">
                  {s.name}
                  {s.winner_id && <WinnerBadge label="has winner" />}
                </span>
                <span className="text-gray-500">
                  {s.sent} sent · {s.reply_rate}% replies · {s.open_rate ?? 0}% opened
                </span>
              </div>
              <Bar value={s.score} max={maxSetScore} />
            </div>
          ))}
        </div>
      </div>

      <div className="card overflow-x-auto">
        <h3 className="font-semibold p-4 pb-0">Creative comparison</h3>
        <table className="w-full text-sm mt-2">
          <thead className="text-left text-gray-500 border-b border-gray-100 dark:border-gray-700">
            <tr>
              <th className="p-3">Creative</th>
              <th className="p-3">Sent</th>
              <th className="p-3">Delivery</th>
              <th className="p-3">Replies</th>
              <th className="p-3">Open</th>
              <th className="p-3">Click</th>
              <th className="p-3">Positive</th>
              <th className="p-3">Opt-outs</th>
              <th className="p-3">Score</th>
            </tr>
          </thead>
          <tbody>
            {analytics.creatives.map((c2: any) => (
              <tr
                key={c2.id}
                className={`border-b border-gray-50 dark:border-gray-700/50 ${
                  c2.is_winner ? "bg-success-50/60 dark:bg-success-900/10" : ""
                }`}
              >
                <td className="p-3">
                  <span className="flex items-center gap-2">
                    {c2.name} {c2.is_winner && <WinnerBadge />}
                  </span>
                </td>
                <td className="p-3">{c2.sent}</td>
                <td className="p-3">{c2.delivery_rate}%</td>
                <td className="p-3">
                  {c2.replies} ({c2.reply_rate}%)
                </td>
                <td className="p-3">{c2.open_rate ?? 0}%</td>
                <td className="p-3">{c2.click_rate ?? 0}%</td>
                <td className="p-3">{c2.positive_replies}</td>
                <td className="p-3">{c2.opt_outs}</td>
                <td className="p-3 font-semibold">{c2.score}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ActivityTab({ campaignId }: { campaignId: number }) {
  const [items, setItems] = useState<any[]>([]);
  useEffect(() => {
    adsApi.campaignActivity(campaignId).then((r) => setItems(r.items));
  }, [campaignId]);
  if (items.length === 0) return <Empty title="No activity yet" />;
  return (
    <div className="card divide-y divide-gray-100 dark:divide-gray-700">
      {items.map((i) => (
        <div key={i.id} className="p-3 flex flex-wrap gap-2 justify-between text-sm">
          <div>
            <b>{i.action.replace(/_/g, " ")}</b>
            {i.detail && <span className="text-gray-500"> — {i.detail}</span>}
          </div>
          <span className="text-xs text-gray-400">
            {i.actor} · {fmtDate(i.created_at)}
          </span>
        </div>
      ))}
    </div>
  );
}

function SettingsTab({
  detail,
  reload,
  onClose,
  onChanged,
}: {
  detail: Detail;
  reload: () => void;
  onClose: () => void;
  onChanged: () => void;
}) {
  return (
    <div className="space-y-3">
      <div className="card p-5 space-y-3">
        <h3 className="font-semibold">Campaign controls</h3>
        <div className="flex flex-wrap gap-2">
          <button
            className="btn-secondary btn-sm"
            onClick={async () => {
              await adsApi.updateCampaign(detail.id, { test_mode: !detail.test_mode });
              toast.success(detail.test_mode ? "Test mode off" : "Test mode on");
              reload();
            }}
          >
            {detail.test_mode ? "Turn off test mode" : "Turn on test mode"}
          </button>
          <button
            className="btn-secondary btn-sm"
            onClick={async () => {
              await adsApi.complete(detail.id);
              toast.success("Campaign completed");
              reload();
              onChanged();
            }}
          >
            Mark completed
          </button>
          <button
            className="btn-secondary btn-sm"
            onClick={async () => {
              await adsApi.archive(detail.id);
              toast.success("Campaign archived");
              reload();
              onChanged();
            }}
          >
            Archive
          </button>
        </div>
      </div>
      <div className="card p-5 space-y-3">
        <h3 className="font-semibold text-danger-600">Danger zone</h3>
        <p className="text-sm text-gray-500">
          Deleting removes the campaign, its sets, creatives and assignments. Contacts, lists and message
          history are untouched.
        </p>
        <button
          className="btn-danger btn-sm"
          onClick={async () => {
            if (!confirm(`Delete "${detail.name}" permanently?`)) return;
            await adsApi.deleteCampaign(detail.id);
            toast.success("Campaign deleted");
            onChanged();
            onClose();
          }}
        >
          <Trash2 size={15} className="mr-1" /> Delete campaign
        </button>
      </div>
    </div>
  );
}

function LaunchModal({
  check,
  campaignId,
  close,
  launched,
}: {
  check: any;
  campaignId: number;
  close: () => void;
  launched: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const s = check.summary || {
    audience: 0, eligible: 0, already_queued: 0, followup_steps: 0,
    daily_limit: null, estimated_days: 0,
    drip: { mode: "off", batch: 1, interval_minutes: 0 },
    test_mode: false, skipped: {}, per_set: [],
  };
  const errors = Array.isArray(check.errors) ? check.errors : [];
  const warnings = Array.isArray(check.warnings) ? check.warnings : [];
  return (
    <Modal title="Pre-launch check" close={close} wide>
      <div className="space-y-4">
        {errors.length > 0 && (
          <div className="p-3 rounded-lg bg-danger-50 dark:bg-danger-900/20 text-sm text-danger-800 dark:text-danger-200 space-y-1">
            {errors.map((e: string, i: number) => (
              <p key={i}>✕ {e}</p>
            ))}
          </div>
        )}
        {warnings.length > 0 && (
          <div className="p-3 rounded-lg bg-warning-50 dark:bg-warning-900/20 text-sm text-warning-800 dark:text-warning-200 space-y-1">
            {warnings.map((w: string, i: number) => (
              <p key={i}>⚠ {w}</p>
            ))}
          </div>
        )}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <Metric label="Audience" value={s.audience} />
          <Metric label="Eligible" value={s.eligible} />
          <Metric label="Already queued" value={s.already_queued} />
          <Metric label="Follow-up steps" value={s.followup_steps} />
          <Metric label="Daily limit" value={s.daily_limit ?? "None"} />
          <Metric label="Estimated duration" value={`${s.estimated_days} day(s)`} />
          <Metric
            label="Drip"
            value={s.drip.mode === "off" ? "Off" : `${s.drip.batch} / ${s.drip.interval_minutes}min`}
          />
          <Metric label="Test mode" value={s.test_mode ? "On" : "Off"} />
        </div>
        {Object.keys(s.skipped || {}).length > 0 && (
          <div className="p-3 rounded-lg bg-gray-50 dark:bg-gray-700/50 text-sm">
            <p className="font-medium mb-1">Filtered out</p>
            {Object.entries(s.skipped).map(([k, v]: any) => (
              <p key={k} className="text-gray-600 dark:text-gray-300">
                {k.replace(/_/g, " ")}: <b>{v}</b>
              </p>
            ))}
          </div>
        )}
        {s.per_set.map((ps: any) => (
          <div key={ps.set_id} className="p-3 rounded-lg bg-gray-50 dark:bg-gray-700/50 text-sm">
            <p className="font-medium">{ps.set_name}</p>
            <p className="text-gray-500 mb-2">
              {ps.matched} matched · {ps.eligible} eligible
            </p>
            {ps.split.map((sp: any) => (
              <p key={sp.creative_id} className="text-gray-600 dark:text-gray-300">
                {sp.name}: <b>{sp.contacts}</b> contacts
              </p>
            ))}
          </div>
        ))}
        <div className="flex gap-2">
          <button className="btn-secondary flex-1" onClick={close}>
            Close
          </button>
          <button
            className="btn-primary flex-1"
            disabled={!check.ok || busy}
            onClick={async () => {
              setBusy(true);
              try {
                await adsApi.launch(campaignId);
                toast.success("Campaign launched");
                launched();
              } catch (err: any) {
                toast.error(err.response?.data?.detail || "Launch failed");
              } finally {
                setBusy(false);
              }
            }}
          >
            <Rocket size={15} className="mr-1" /> {busy ? "Launching…" : "Launch campaign"}
          </button>
        </div>
      </div>
    </Modal>
  );
}
