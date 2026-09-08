import { useCallback, useEffect, useMemo, useState } from "react";
import toast from "react-hot-toast";
import {
  ArrowLeft,
  Copy,
  Download,
  Pause,
  Play,
  Plus,
  Rocket,
  Trash2,
  TrendingUp,
  Trophy,
  Users,
  Wand2,
  Zap,
} from "lucide-react";
import adsApi, { AdsCreative, AdsSet, CampaignDetail as Detail } from "../../api/ads";
import { Badge, Bar, Empty, Field, Metric, Modal, Stat, Tabs, fmtDate } from "./ui";
import CampaignBuilder from "./CampaignBuilder";

const TABS = [
  "Overview",
  "SMS Sets",
  "Creatives",
  "Audience",
  "Sending",
  "Automation",
  "Analytics",
  "Activity",
  "Settings",
];

export default function CampaignDetail({
  campaignId,
  reference,
  onClose,
  onChanged,
}: {
  campaignId: number;
  reference: any;
  onClose: () => void;
  onChanged: () => void;
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

  return (
    <div className="fixed inset-0 z-40 bg-gray-50 dark:bg-gray-900 overflow-y-auto">
      <div className="max-w-6xl mx-auto p-4 sm:p-6 space-y-5">
        <div className="flex flex-wrap gap-3 items-start justify-between">
          <div className="min-w-0">
            <button className="text-sm text-primary-600 mb-2 flex items-center gap-1" onClick={onClose}>
              <ArrowLeft size={15} /> SMS Ads Manager
            </button>
            <h1 className="text-2xl font-bold break-words">{detail.name}</h1>
            <div className="flex flex-wrap items-center gap-2 mt-2">
              <Badge value={detail.status} />
              {detail.state && detail.state !== detail.status && <Badge value={detail.state} />}
              {detail.test_mode && <span className="badge-yellow">Test mode</span>}
              <span className="text-xs text-gray-500">{detail.objective}</span>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            {detail.status === "draft" && (
              <button
                className="btn-primary btn-sm"
                onClick={async () => {
                  const check = await adsApi.validate(campaignId);
                  setLaunching(check);
                }}
              >
                <Rocket size={15} className="mr-1" /> Review &amp; launch
              </button>
            )}
            {detail.status === "active" && (
              <button className="btn-secondary btn-sm" onClick={() => act(() => adsApi.pause(campaignId), "Paused")}>
                <Pause size={15} className="mr-1" /> Pause
              </button>
            )}
            {detail.status === "paused" && (
              <button className="btn-primary btn-sm" onClick={() => act(() => adsApi.resume(campaignId), "Resumed")}>
                <Play size={15} className="mr-1" /> Resume
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
              onClick={() => act(() => adsApi.duplicate(campaignId), "Campaign duplicated")}
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
                    ? "bg-amber-50 dark:bg-amber-900/20 text-amber-800 dark:text-amber-200"
                    : "bg-blue-50 dark:bg-blue-900/20 text-blue-800 dark:text-blue-200"
                }`}
              >
                {a.level === "warning" ? "⚠ " : "ℹ "}
                {a.message}
              </div>
            ))}
          </div>
        )}

        <Tabs tabs={TABS} active={tab} onChange={setTab} />

        {tab === "Overview" && <OverviewTab detail={detail} stats={stats} />}
        {tab === "SMS Sets" && <SetsTab detail={detail} reference={reference} reload={load} />}
        {tab === "Creatives" && <CreativesTab detail={detail} analytics={analytics} reload={load} />}
        {tab === "Audience" && <AudienceTab detail={detail} reference={reference} reload={load} />}
        {tab === "Sending" && <SendingTab detail={detail} stats={stats} />}
        {tab === "Automation" && <AutomationTab detail={detail} reload={load} />}
        {tab === "Analytics" && <AnalyticsTab analytics={analytics} campaignId={campaignId} />}
        {tab === "Activity" && <ActivityTab campaignId={campaignId} />}
        {tab === "Settings" && (
          <SettingsTab detail={detail} reload={load} onClose={onClose} onChanged={onChanged} />
        )}
      </div>

      {editing && (
        <CampaignBuilder
          campaign={detail}
          objectives={reference?.objectives || []}
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

function OverviewTab({ detail, stats }: { detail: Detail; stats: any }) {
  if (!stats) return null;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Stat label="Contacts assigned" value={stats.assigned} />
        <Stat label="SMS sent" value={stats.sent} />
        <Stat label="Delivery rate" value={`${stats.delivery_rate}%`} />
        <Stat label="Reply rate" value={`${stats.reply_rate}%`} />
        <Stat label="Positive replies" value={stats.positive_replies} />
        <Stat label="Follow-ups due" value={stats.followups_due} />
        <Stat label="Meetings" value={stats.meetings} />
        <Stat label="Opt-outs" value={stats.opt_outs} />
      </div>
      <div className="card p-5 grid grid-cols-2 sm:grid-cols-4 gap-5">
        <Metric label="Pending" value={stats.pending} />
        <Metric label="Skipped" value={stats.skipped} />
        <Metric label="Failed" value={stats.failed} />
        <Metric label="Credits used" value={stats.credits_used} />
        <Metric label="Daily limit" value={detail.daily_limit ?? "None"} />
        <Metric label="Total limit" value={detail.total_limit ?? "None"} />
        <Metric label="Drip" value={detail.drip_mode} />
        <Metric label="Performance score" value={stats.score} />
      </div>
    </div>
  );
}

function SetsTab({ detail, reference, reload }: { detail: Detail; reference: any; reload: () => void }) {
  const [editing, setEditing] = useState<AdsSet | null>(null);
  const [creating, setCreating] = useState(false);
  const [previews, setPreviews] = useState<Record<number, any>>({});

  useEffect(() => {
    detail.sets.forEach(async (s) => {
      try {
        const preview = await adsApi.previewSet(s.id);
        setPreviews((p) => ({ ...p, [s.id]: preview }));
      } catch {
        /* preview is best-effort */
      }
    });
  }, [detail.sets.map((s) => s.id).join(",")]);

  return (
    <div className="space-y-3">
      <div className="flex justify-between items-center">
        <h2 className="font-semibold">SMS sets</h2>
        <button className="btn-primary btn-sm" onClick={() => setCreating(true)}>
          <Plus size={15} className="mr-1" /> New set
        </button>
      </div>
      {detail.sets.length === 0 ? (
        <Empty
          icon={<Users size={40} />}
          title="No SMS sets yet"
          body="An SMS set is an audience plus its delivery rules. Create one per segment you want to compare."
          action={
            <button className="btn-primary" onClick={() => setCreating(true)}>
              <Plus size={16} className="mr-1" /> Create SMS set
            </button>
          }
        />
      ) : (
        detail.sets.map((s) => {
          const preview = previews[s.id];
          const creatives = detail.creatives.filter((c) => c.set_id === s.id);
          return (
            <div className="card p-4" key={s.id}>
              <div className="flex flex-wrap gap-3 items-start justify-between">
                <div>
                  <div className="flex items-center gap-2">
                    <h3 className="font-semibold">{s.name}</h3>
                    <Badge value={s.status} />
                  </div>
                  <p className="text-sm text-gray-500 mt-1">
                    {preview
                      ? `${preview.matched} matched · ${preview.eligible} eligible · ${creatives.length} creative(s)`
                      : `${creatives.length} creative(s)`}
                  </p>
                </div>
                <div className="flex gap-2">
                  <button className="btn-secondary btn-sm" onClick={() => setEditing(s)}>
                    Edit targeting
                  </button>
                  <button
                    className="btn-ghost btn-sm text-red-600"
                    onClick={async () => {
                      if (!confirm(`Remove set "${s.name}"?`)) return;
                      await adsApi.deleteSet(s.id);
                      toast.success("Set removed");
                      reload();
                    }}
                  >
                    <Trash2 size={15} />
                  </button>
                </div>
              </div>
              {preview && Object.keys(preview.skipped || {}).length > 0 && (
                <div className="mt-3 pt-3 border-t border-gray-100 dark:border-gray-700 flex flex-wrap gap-3 text-xs text-gray-500">
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

function SetEditor({
  campaignId,
  adsSet,
  reference,
  close,
  saved,
}: {
  campaignId: number;
  adsSet: AdsSet | null;
  reference: any;
  close: () => void;
  saved: () => void;
}) {
  const [form, setForm] = useState<any>({
    name: adsSet?.name || "",
    status: adsSet?.status || "active",
    list_ids: adsSet?.list_ids || "",
    include_tags: adsSet?.include_tags || "",
    exclude_tags: adsSet?.exclude_tags || "",
    include_statuses: adsSet?.include_statuses || "",
    exclude_statuses: adsSet?.exclude_statuses || "opted_out",
    city: adsSet?.city || "",
    state: adsSet?.state || "",
    industry: adsSet?.industry || "",
    activity_filter: adsSet?.activity_filter || "any",
    daily_limit: adsSet?.daily_limit ?? "",
    split_mode: adsSet?.split_mode || "equal",
  });
  const set = (k: string, v: any) => setForm((f: any) => ({ ...f, [k]: v }));
  const selectedLists = (form.list_ids || "").split(",").filter(Boolean);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.name.trim()) return toast.error("Name the SMS set");
    const payload = {
      ...form,
      name: form.name.trim(),
      daily_limit: form.daily_limit === "" ? null : Number(form.daily_limit),
      list_ids: form.list_ids || null,
      include_tags: form.include_tags || null,
      exclude_tags: form.exclude_tags || null,
      include_statuses: form.include_statuses || null,
      exclude_statuses: form.exclude_statuses || null,
      city: form.city || null,
      state: form.state || null,
      industry: form.industry || null,
    };
    try {
      if (adsSet) await adsApi.updateSet(adsSet.id, payload);
      else await adsApi.createSet(campaignId, payload);
      toast.success("SMS set saved");
      saved();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not save");
    }
  };

  return (
    <Modal title={adsSet ? "Edit SMS set" : "New SMS set"} close={close} wide>
      <form onSubmit={submit} className="space-y-4">
        <Field label="Set name">
          <input className="input" value={form.name} onChange={(e) => set("name", e.target.value)} autoFocus />
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
          </div>
          <p className="text-xs text-gray-500 mt-1">
            Nothing selected targets all {reference?.total_contacts ?? 0} contacts.
          </p>
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

function CreativesTab({ detail, analytics, reload }: { detail: Detail; analytics: any; reload: () => void }) {
  const [editing, setEditing] = useState<AdsCreative | null>(null);
  const [creatingFor, setCreatingFor] = useState<number | null>(null);
  const [versionsFor, setVersionsFor] = useState<AdsCreative | null>(null);
  const perf = useMemo(() => {
    const map: Record<number, any> = {};
    (analytics?.creatives || []).forEach((c: any) => (map[c.id] = c));
    return map;
  }, [analytics]);
  const maxScore = Math.max(1, ...(analytics?.creatives || []).map((c: any) => c.score || 0));

  if (detail.sets.length === 0)
    return <Empty title="Create an SMS set first" body="Creatives live inside an SMS set." />;

  return (
    <div className="space-y-5">
      {detail.sets.map((s) => {
        const creatives = detail.creatives.filter((c) => c.set_id === s.id);
        return (
          <div key={s.id} className="space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="font-semibold">{s.name}</h3>
              <button className="btn-secondary btn-sm" onClick={() => setCreatingFor(s.id)}>
                <Plus size={15} className="mr-1" /> Add creative
              </button>
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
                  <div className="card p-4" key={c.id}>
                    <div className="flex flex-wrap gap-3 items-start justify-between">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <h4 className="font-semibold">{c.name}</h4>
                          <Badge value={c.status} />
                          <button
                            className="text-xs text-primary-600"
                            onClick={() => setVersionsFor(c)}
                          >
                            v{c.current_version}
                          </button>
                          {s.split_mode === "percentage" && (
                            <span className="text-xs text-gray-500">{c.allocation}%</span>
                          )}
                        </div>
                        <p className="text-sm text-gray-600 dark:text-gray-300 mt-2 whitespace-pre-wrap break-words">
                          {c.body || <span className="text-gray-400">No message yet</span>}
                        </p>
                      </div>
                      <div className="flex flex-wrap gap-2">
                        <button className="btn-secondary btn-sm" onClick={() => setEditing(c)}>
                          Edit
                        </button>
                        <button
                          className="btn-secondary btn-sm"
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
                          className="btn-ghost btn-sm text-red-600"
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
                      <Metric label="Assigned" value={p.assigned ?? 0} />
                      <Metric label="Sent" value={p.sent ?? 0} />
                      <Metric label="Replies" value={p.replies ?? 0} />
                      <Metric label="Positive" value={p.positive_replies ?? 0} />
                      <Metric label="Opt-outs" value={p.opt_outs ?? 0} />
                      <Metric label="Score" value={p.score ?? 0} />
                    </div>
                    <div className="mt-2">
                      <Bar value={p.score || 0} max={maxScore} />
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
    </div>
  );
}

function CreativeEditor({
  creative,
  setId,
  close,
  saved,
}: {
  creative: AdsCreative | null;
  setId: number;
  close: () => void;
  saved: () => void;
}) {
  const [form, setForm] = useState<any>({
    name: creative?.name || "",
    body: creative?.body || "",
    cta: creative?.cta || "",
    tracking_link: creative?.tracking_link || "",
    allocation: creative?.allocation ?? 0,
    status: creative?.status || "active",
  });
  const set = (k: string, v: any) => setForm((f: any) => ({ ...f, [k]: v }));
  const chars = form.body.length;
  const segments = chars === 0 ? 0 : Math.ceil(chars / 160);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.name.trim() || !form.body.trim()) return toast.error("Add a name and message");
    const payload = {
      ...form,
      name: form.name.trim(),
      allocation: Number(form.allocation) || 0,
      cta: form.cta || null,
      tracking_link: form.tracking_link || null,
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
    <Modal title={creative ? "Edit creative" : "New creative"} close={close}>
      <form onSubmit={submit} className="space-y-4">
        <Field label="Creative name">
          <input className="input" value={form.name} onChange={(e) => set("name", e.target.value)} autoFocus />
        </Field>
        <Field
          label="Message"
          hint={`${chars} characters · ${segments} SMS segment(s). Use {{first_name}}, {{business_name}} and any other variable.`}
        >
          <textarea
            className="input"
            rows={5}
            value={form.body}
            onChange={(e) => set("body", e.target.value)}
            placeholder="Hi {{first_name}}, I came across {{business_name}} and had a quick question…"
          />
        </Field>
        <div className="grid sm:grid-cols-2 gap-3">
          <Field label="Call to action">
            <input className="input" value={form.cta} onChange={(e) => set("cta", e.target.value)} />
          </Field>
          <Field label="Tracking link">
            <input
              className="input"
              value={form.tracking_link}
              onChange={(e) => set("tracking_link", e.target.value)}
            />
          </Field>
          <Field label="Allocation %" hint="Used with percentage / weighted split.">
            <input
              type="number"
              className="input"
              value={form.allocation}
              onChange={(e) => set("allocation", e.target.value)}
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
            Editing the text creates version {creative.current_version + 1}. Messages already sent keep their
            version, so historical analytics do not change.
          </p>
        )}
        <div className="flex gap-2">
          <button type="button" className="btn-secondary flex-1" onClick={close}>
            Cancel
          </button>
          <button className="btn-primary flex-1">Save creative</button>
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
            <p className="text-sm text-gray-600 dark:text-gray-300 mt-2 whitespace-pre-wrap">{v.body}</p>
            <p className="text-xs text-gray-400 mt-1">{fmtDate(v.created_at)}</p>
          </div>
        ))}
      </div>
    </Modal>
  );
}

function AudienceTab({ detail, reference, reload }: { detail: Detail; reference: any; reload: () => void }) {
  const [rows, setRows] = useState<any>({ items: [], total: 0 });
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("");
  const [adding, setAdding] = useState(false);

  const load = useCallback(async () => {
    setRows(await adsApi.audience(detail.id, { page, per_page: 50, status: status || undefined }));
  }, [detail.id, page, status]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2 justify-between items-center">
        <div className="flex gap-2 items-center">
          <select className="input !py-2 !w-auto" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statuses</option>
            {["pending", "sent", "delivered", "failed", "skipped", "cancelled"].map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <span className="text-sm text-gray-500">{rows.total} contacts</span>
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

      {rows.items.length === 0 ? (
        <Empty title="No contacts in this campaign yet" body="Add contacts or refresh the audience." />
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-gray-500 border-b border-gray-100 dark:border-gray-700">
              <tr>
                <th className="p-3">Contact</th>
                <th className="p-3">Phone</th>
                <th className="p-3">Creative</th>
                <th className="p-3">Status</th>
                <th className="p-3">Reply</th>
                <th className="p-3">Sent</th>
              </tr>
            </thead>
            <tbody>
              {rows.items.map((r: any) => (
                <tr key={r.id} className="border-b border-gray-50 dark:border-gray-700/50">
                  <td className="p-3">{r.name || "—"}</td>
                  <td className="p-3 whitespace-nowrap">{r.phone_number}</td>
                  <td className="p-3">{r.creative || "—"}</td>
                  <td className="p-3">
                    <Badge value={r.send_status} />
                    {r.skip_reason && (
                      <span className="block text-xs text-gray-400 mt-1">{r.skip_reason.replace(/_/g, " ")}</span>
                    )}
                  </td>
                  <td className="p-3">{r.reply_status || "—"}</td>
                  <td className="p-3 whitespace-nowrap">{r.sent_at ? fmtDate(r.sent_at) : "—"}</td>
                </tr>
              ))}
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
  close,
  done,
}: {
  detail: Detail;
  reference: any;
  close: () => void;
  done: () => void;
}) {
  const [setId, setSetId] = useState<number | "">(detail.sets[0]?.id ?? "");
  const [lists, setLists] = useState<number[]>([]);
  const [tags, setTags] = useState("");
  const [result, setResult] = useState<any>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    try {
      const r = await adsApi.addContacts(detail.id, {
        set_id: setId || null,
        list_ids: lists,
        tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
        contact_ids: [],
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
          <Field label="Add to SMS set">
            <select className="input" value={setId} onChange={(e) => setSetId(Number(e.target.value))}>
              {detail.sets.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </Field>
          <div>
            <span className="label">Lists</span>
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
            </div>
          </div>
          <Field label="Tags" hint="Comma separated. Contacts with any of these tags are considered.">
            <input className="input" value={tags} onChange={(e) => setTags(e.target.value)} />
          </Field>
          <p className="text-xs text-gray-500">
            Duplicates, suppressed contacts, opt-outs and anyone already in this campaign are filtered out
            automatically — you will see exactly why on the next screen.
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

function SendingTab({ detail, stats }: { detail: Detail; stats: any }) {
  return (
    <div className="space-y-3">
      <div className="card p-5 grid sm:grid-cols-2 lg:grid-cols-3 gap-5">
        <Metric label="Status" value={<Badge value={detail.state || detail.status} />} />
        <Metric label="Daily SMS limit" value={detail.daily_limit ?? "No limit"} />
        <Metric label="Total SMS limit" value={detail.total_limit ?? "No limit"} />
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
          Every message is re-validated immediately before it is sent: opt-out, suppression list, duplicate
          protection, frequency caps, campaign and creative status, sending window and SMS balance.
        </p>
        <p>
          Sending runs in the background — you can close this page. Global sending limits and pacing from
          Settings still apply.
        </p>
      </div>
    </div>
  );
}

function AutomationTab({ detail, reload }: { detail: Detail; reload: () => void }) {
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
          body="Chain automatic follow-ups: wait, check a condition, then send. They stop automatically when a contact replies, opts out or converts."
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
                  <b>{s.action.replace(/_/g, " ")}</b>
                  {s.action_value ? ` (${s.action_value})` : ""}
                </p>
                {s.body && (
                  <p className="text-sm text-gray-600 dark:text-gray-300 mt-2 whitespace-pre-wrap">{s.body}</p>
                )}
              </div>
              <div className="flex gap-2">
                <button className="btn-secondary btn-sm" onClick={() => setEditing(s)}>
                  Edit
                </button>
                <button
                  className="btn-ghost btn-sm text-red-600"
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
  close,
  saved,
}: {
  campaignId: number;
  step: any;
  nextOrder: number;
  close: () => void;
  saved: () => void;
}) {
  const [form, setForm] = useState<any>({
    step_order: step?.step_order ?? nextOrder,
    name: step?.name || "",
    wait_hours: step?.wait_hours ?? 48,
    condition: step?.condition || "no_reply",
    action: step?.action || "send_sms",
    body: step?.body || "",
    action_value: step?.action_value || "",
    is_active: step?.is_active ?? true,
  });
  const set = (k: string, v: any) => setForm((f: any) => ({ ...f, [k]: v }));

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (form.action === "send_sms" && !form.body.trim()) return toast.error("Write the follow-up message");
    const payload = {
      ...form,
      step_order: Number(form.step_order),
      wait_hours: Number(form.wait_hours),
      name: form.name || null,
      body: form.body || null,
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
              {["send_sms", "add_tag", "remove_tag", "change_status", "suppress", "stop"].map((a) => (
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
        {form.action === "send_sms" && (
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

function AnalyticsTab({ analytics, campaignId }: { analytics: any; campaignId: number }) {
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
        <Stat label="Positive reply rate" value={`${c.positive_reply_rate}%`} />
        <Stat label="Conversion rate" value={`${c.conversion_rate}%`} />
        <Stat label="Meeting rate" value={`${c.meeting_rate}%`} />
        <Stat label="Opt-out rate" value={`${c.opt_out_rate}%`} />
        <Stat label="Failure rate" value={`${c.failure_rate}%`} />
        <Stat label="Score" value={c.score} icon={<TrendingUp size={17} />} />
      </div>

      <div className="card p-5">
        <h3 className="font-semibold mb-3">SMS set performance</h3>
        <div className="space-y-3">
          {analytics.sets.map((s: any) => (
            <div key={s.id}>
              <div className="flex justify-between text-sm mb-1">
                <span>{s.name}</span>
                <span className="text-gray-500">
                  {s.sent} sent · {s.reply_rate}% replies
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
              <th className="p-3">Assigned</th>
              <th className="p-3">Sent</th>
              <th className="p-3">Replies</th>
              <th className="p-3">Positive</th>
              <th className="p-3">Opt-outs</th>
              <th className="p-3">Score</th>
            </tr>
          </thead>
          <tbody>
            {analytics.creatives.map((c2: any) => (
              <tr key={c2.id} className="border-b border-gray-50 dark:border-gray-700/50">
                <td className="p-3">{c2.name}</td>
                <td className="p-3">{c2.assigned}</td>
                <td className="p-3">{c2.sent}</td>
                <td className="p-3">{c2.replies}</td>
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
        <h3 className="font-semibold text-red-600">Danger zone</h3>
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
  const s = check.summary;
  return (
    <Modal title="Pre-launch check" close={close} wide>
      <div className="space-y-4">
        {check.errors.length > 0 && (
          <div className="p-3 rounded-lg bg-red-50 dark:bg-red-900/20 text-sm text-red-800 dark:text-red-200 space-y-1">
            {check.errors.map((e: string, i: number) => (
              <p key={i}>✕ {e}</p>
            ))}
          </div>
        )}
        {check.warnings.length > 0 && (
          <div className="p-3 rounded-lg bg-amber-50 dark:bg-amber-900/20 text-sm text-amber-800 dark:text-amber-200 space-y-1">
            {check.warnings.map((w: string, i: number) => (
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
