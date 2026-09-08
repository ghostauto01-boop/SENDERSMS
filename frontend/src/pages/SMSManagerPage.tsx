import { useCallback, useEffect, useMemo, useState } from "react";
import toast from "react-hot-toast";
import {
  Activity,
  BarChart3,
  CalendarDays,
  CheckCircle2,
  Clock3,
  Copy,
  Download,
  LayoutGrid,
  MessageCircle,
  Megaphone,
  Pause,
  Play,
  Plus,
  Search,
  Send,
  ShieldOff,
  Sparkles,
  Users,
  Wand2,
} from "lucide-react";
import adsApi, { AdsCampaign } from "../api/ads";
import CampaignBuilder from "./ads/CampaignBuilder";
import CampaignDetail from "./ads/CampaignDetail";
import { Badge, Empty, Field, Metric, Modal, Stat, fmtDate, fmtDay, fromLocalInput } from "./ads/ui";

/**
 * SMS ADS MANAGER
 *
 * The advanced workspace: campaigns -> SMS sets -> creatives -> audiences ->
 * A/B testing -> SMS budgets -> drip -> follow-ups -> CRM -> calendar ->
 * analytics. The simple "Send SMS" / Campaigns pages are unchanged and remain
 * the quick path.
 */

const SECTIONS = [
  { key: "Overview", icon: LayoutGrid },
  { key: "Campaigns", icon: Megaphone },
  { key: "Audience", icon: Users },
  { key: "Automation", icon: Wand2 },
  { key: "Follow-Ups", icon: Clock3 },
  { key: "Calendar", icon: CalendarDays },
  { key: "Analytics", icon: BarChart3 },
  { key: "Suppression", icon: ShieldOff },
  { key: "Activity Log", icon: Activity },
];

export default function SMSManagerPage() {
  const [section, setSection] = useState("Overview");
  const [campaigns, setCampaigns] = useState<AdsCampaign[]>([]);
  const [reference, setReference] = useState<any>(null);
  const [overview, setOverview] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [openId, setOpenId] = useState<number | null>(null);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("");

  const load = useCallback(async () => {
    try {
      const [c, o] = await Promise.all([adsApi.listCampaigns({ per_page: 100 }), adsApi.overview()]);
      setCampaigns(c.items);
      setOverview(o);
    } catch {
      toast.error("Could not load the SMS Ads Manager");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    adsApi.reference().then(setReference).catch(() => {});
  }, [load]);

  const filtered = useMemo(
    () =>
      campaigns.filter(
        (c) =>
          (!statusFilter || c.status === statusFilter) &&
          (!query || c.name.toLowerCase().includes(query.toLowerCase()))
      ),
    [campaigns, query, statusFilter]
  );

  return (
    <div className="space-y-5 max-w-6xl mx-auto">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm font-medium text-primary-600 mb-1 flex items-center gap-1">
            <Sparkles size={15} /> ADVANCED
          </p>
          <h1 className="text-2xl sm:text-3xl font-bold">SMS Ads Manager</h1>
          <p className="text-gray-500 mt-1">
            Campaigns, SMS sets, creatives, A/B testing, drip budgets, follow-ups and analytics.
          </p>
        </div>
        <button className="btn-primary shrink-0" onClick={() => setCreating(true)}>
          <Plus size={17} className="mr-1" /> Create campaign
        </button>
      </div>

      <div className="flex gap-1 overflow-x-auto scrollbar-none border-b border-gray-200 dark:border-gray-700">
        {SECTIONS.map(({ key, icon: Icon }) => (
          <button
            key={key}
            onClick={() => setSection(key)}
            className={`px-3 py-3 text-sm whitespace-nowrap border-b-2 flex items-center gap-1.5 ${
              section === key
                ? "border-primary-600 text-primary-600 font-medium"
                : "border-transparent text-gray-500"
            }`}
          >
            <Icon size={15} /> {key}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="card p-10 text-center text-gray-500">Loading…</div>
      ) : (
        <>
          {section === "Overview" && (
            <OverviewSection overview={overview} campaigns={campaigns} onOpen={setOpenId} />
          )}
          {section === "Campaigns" && (
            <CampaignsSection
              campaigns={filtered}
              query={query}
              setQuery={setQuery}
              statusFilter={statusFilter}
              setStatusFilter={setStatusFilter}
              onOpen={setOpenId}
              reload={load}
              create={() => setCreating(true)}
            />
          )}
          {section === "Audience" && <AudienceSection reference={reference} campaigns={campaigns} />}
          {section === "Automation" && <AutomationSection campaigns={campaigns} onOpen={setOpenId} />}
          {section === "Follow-Ups" && <FollowUpsSection />}
          {section === "Calendar" && <CalendarSection />}
          {section === "Analytics" && <AnalyticsSection campaigns={campaigns} onOpen={setOpenId} />}
          {section === "Suppression" && <SuppressionSection />}
          {section === "Activity Log" && <ActivitySection />}
        </>
      )}

      {creating && (
        <CampaignBuilder
          objectives={reference?.objectives || []}
          close={() => setCreating(false)}
          saved={(id) => {
            setCreating(false);
            load();
            setOpenId(id);
          }}
        />
      )}
      {openId !== null && (
        <CampaignDetail
          campaignId={openId}
          reference={reference}
          onClose={() => setOpenId(null)}
          onChanged={load}
        />
      )}
    </div>
  );
}

/* ------------------------------------------------------------- Overview */

function OverviewSection({
  overview,
  campaigns,
  onOpen,
}: {
  overview: any;
  campaigns: AdsCampaign[];
  onOpen: (id: number) => void;
}) {
  if (!overview) return null;
  const t = overview.totals;
  const maxSent = Math.max(1, ...overview.series.map((d: any) => d.sent));
  const active = campaigns.filter((c) => c.status === "active");
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Stat icon={<Play size={17} />} label="Active campaigns" value={overview.active_campaigns} />
        <Stat icon={<Send size={17} />} label="SMS sent" value={t.sent} />
        <Stat icon={<CheckCircle2 size={17} />} label="Delivery rate" value={`${t.delivery_rate}%`} />
        <Stat icon={<MessageCircle size={17} />} label="Replies" value={t.replies} />
        <Stat label="Positive replies" value={t.positive_replies} />
        <Stat label="Follow-ups due" value={overview.followups_due} />
        <Stat label="Meetings" value={overview.meetings} />
        <Stat label="Opt-outs / suppressed" value={overview.suppressed} />
      </div>

      <div className="card p-5">
        <h3 className="font-semibold mb-4">Messages sent over the last 14 days</h3>
        <div className="flex items-end gap-1 h-32">
          {overview.series.map((d: any) => (
            <div key={d.date} className="flex-1 flex flex-col items-center gap-1" title={`${d.date}: ${d.sent} sent`}>
              <div
                className="w-full bg-primary-600 rounded-t"
                style={{ height: `${(d.sent / maxSent) * 100}%`, minHeight: d.sent ? 3 : 0 }}
              />
              <span className="text-[9px] text-gray-400">{d.date.slice(8)}</span>
            </div>
          ))}
        </div>
      </div>

      <div>
        <h3 className="font-semibold mb-2">Running now</h3>
        {active.length === 0 ? (
          <Empty title="No active campaigns" body="Create one and launch it to start sending." />
        ) : (
          <div className="grid gap-3">
            {active.map((c) => (
              <CampaignRow key={c.id} campaign={c} onOpen={onOpen} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function CampaignRow({
  campaign,
  onOpen,
  reload,
}: {
  campaign: AdsCampaign;
  onOpen: (id: number) => void;
  reload?: () => void;
}) {
  const s = campaign.stats;
  const act = async (fn: () => Promise<any>, msg: string) => {
    try {
      await fn();
      toast.success(msg);
      reload?.();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Action failed");
    }
  };
  return (
    <div className="card p-4 sm:p-5">
      <div className="flex flex-wrap gap-3 items-start justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h3 className="font-semibold text-lg break-words">{campaign.name}</h3>
            <Badge value={campaign.status} />
            {campaign.state && campaign.state !== campaign.status && <Badge value={campaign.state} />}
            {campaign.test_mode && <span className="badge-yellow">Test</span>}
          </div>
          <p className="text-sm text-gray-500 mt-1">
            {campaign.description || campaign.objective.replace(/_/g, " ")}
          </p>
        </div>
        <div className="flex gap-2 flex-wrap">
          <button className="btn-secondary btn-sm" onClick={() => onOpen(campaign.id)}>
            Open
          </button>
          {reload && campaign.status === "active" && (
            <button className="btn-secondary btn-sm" onClick={() => act(() => adsApi.pause(campaign.id), "Paused")}>
              <Pause size={14} />
            </button>
          )}
          {reload && campaign.status === "paused" && (
            <button className="btn-primary btn-sm" onClick={() => act(() => adsApi.resume(campaign.id), "Resumed")}>
              <Play size={14} />
            </button>
          )}
          {reload && (
            <button
              className="btn-secondary btn-sm"
              onClick={() => act(() => adsApi.duplicate(campaign.id), "Duplicated")}
            >
              <Copy size={14} />
            </button>
          )}
        </div>
      </div>
      <div className="grid grid-cols-3 sm:grid-cols-6 gap-3 mt-4 pt-3 border-t border-gray-100 dark:border-gray-700 text-sm">
        <Metric label="Audience" value={s?.assigned ?? 0} />
        <Metric label="Sent" value={s?.sent ?? 0} />
        <Metric label="Replies" value={s?.replies ?? 0} />
        <Metric label="Reply rate" value={`${s?.reply_rate ?? 0}%`} />
        <Metric label="Daily limit" value={campaign.daily_limit ?? "—"} />
        <Metric label="Last activity" value={fmtDay(campaign.last_activity_at || campaign.updated_at)} />
      </div>
    </div>
  );
}

/* ------------------------------------------------------------ Campaigns */

function CampaignsSection({
  campaigns,
  query,
  setQuery,
  statusFilter,
  setStatusFilter,
  onOpen,
  reload,
  create,
}: any) {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2 items-center">
        <div className="relative flex-1 min-w-[200px]">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            className="input !pl-9"
            placeholder="Search campaigns…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <select className="input !w-auto" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">All statuses</option>
          {["draft", "scheduled", "active", "paused", "completed", "archived"].map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <a className="btn-secondary btn-sm" href={adsApi.exportUrl("campaigns")}>
          <Download size={15} className="mr-1" /> Export
        </a>
      </div>
      {campaigns.length === 0 ? (
        <Empty
          icon={<Megaphone size={40} />}
          title="No campaigns yet"
          body="A campaign holds SMS sets, each set holds creatives, and the audience is split between them automatically."
          action={
            <button className="btn-primary" onClick={create}>
              <Plus size={16} className="mr-1" /> Create campaign
            </button>
          }
        />
      ) : (
        <div className="grid gap-3">
          {campaigns.map((c: AdsCampaign) => (
            <CampaignRow key={c.id} campaign={c} onOpen={onOpen} reload={reload} />
          ))}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------- Audience */

function AudienceSection({ reference, campaigns }: { reference: any; campaigns: AdsCampaign[] }) {
  const totalAssigned = campaigns.reduce((a, c) => a + (c.stats?.assigned || 0), 0);
  const [audiences, setAudiences] = useState<any[]>([]);
  useEffect(() => {
    adsApi.listAudiences().then((r) => setAudiences(r.items)).catch(() => {});
  }, []);
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Stat label="Contacts in CRM" value={reference?.total_contacts ?? 0} />
        <Stat label="Lists" value={reference?.lists?.length ?? 0} />
        <Stat label="Saved audiences" value={audiences.length} />
        <Stat label="Assigned across campaigns" value={totalAssigned} />
      </div>
      <div className="card p-5">
        <div className="flex flex-wrap justify-between items-center gap-2 mb-3">
          <h3 className="font-semibold">Saved audiences</h3>
          <a className="btn-primary btn-sm" href="/audiences">
            <Plus size={15} className="mr-1" /> Build audience
          </a>
        </div>
        {audiences.length === 0 ? (
          <p className="text-sm text-gray-500">
            No saved audiences yet. Combine lists and contacts into a reusable audience — like a Meta saved
            audience — then attach it to any campaign with one click.
          </p>
        ) : (
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {audiences.map((a) => (
              <a
                key={a.id}
                href="/audiences"
                className="p-3 rounded-lg bg-gray-50 dark:bg-gray-700/50 flex justify-between text-sm hover:ring-1 hover:ring-primary-500"
              >
                <span className="truncate mr-2">{a.name}</span>
                <b className="shrink-0">{a.match_count ?? "—"}</b>
              </a>
            ))}
          </div>
        )}
      </div>
      <div className="card p-5">
        <h3 className="font-semibold mb-3">Lists available for targeting</h3>
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-2">
          {(reference?.lists || []).map((l: any) => (
            <div key={l.id} className="p-3 rounded-lg bg-gray-50 dark:bg-gray-700/50 flex justify-between text-sm">
              <span>{l.name}</span>
              <b>{l.count}</b>
            </div>
          ))}
        </div>
        <p className="text-xs text-gray-500 mt-3">
          SMS sets stay empty until you add at least one list, contact, audience or filter — nothing is ever
          pulled in automatically.
        </p>
      </div>
      <div className="card p-5">
        <h3 className="font-semibold mb-3">Tags</h3>
        <div className="flex flex-wrap gap-2">
          {(reference?.tags || []).map((t: string) => (
            <span key={t} className="badge-gray">
              {t}
            </span>
          ))}
          {(reference?.tags || []).length === 0 && <p className="text-sm text-gray-500">No tags yet.</p>}
        </div>
      </div>
    </div>
  );
}

/* ----------------------------------------------------------- Automation */

function AutomationSection({ campaigns, onOpen }: { campaigns: AdsCampaign[]; onOpen: (id: number) => void }) {
  return (
    <div className="space-y-3">
      <div className="card p-5 text-sm text-gray-500">
        Follow-up workflows live inside each campaign so their conditions can see that campaign's replies and
        creatives. Open a campaign and use the <b>Automation</b> tab.
      </div>
      {campaigns.length === 0 ? (
        <Empty title="No campaigns yet" />
      ) : (
        <div className="grid gap-2">
          {campaigns.map((c) => (
            <button
              key={c.id}
              className="card p-4 text-left hover:border-primary-500 transition"
              onClick={() => onOpen(c.id)}
            >
              <div className="flex justify-between items-center">
                <span className="font-medium">{c.name}</span>
                <Badge value={c.status} />
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/* ----------------------------------------------------------- Follow-Ups */

const BUCKETS = ["today", "overdue", "upcoming", "waiting", "completed", "cancelled"];

function FollowUpsSection() {
  const [bucket, setBucket] = useState("today");
  const [items, setItems] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setItems((await adsApi.followups(bucket)).items);
    } finally {
      setLoading(false);
    }
  }, [bucket]);

  useEffect(() => {
    load();
  }, [load]);

  const act = async (id: number, action: string, params?: any) => {
    try {
      await adsApi.followupAction(id, action, params);
      toast.success("Updated");
      load();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Action failed");
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2 justify-between">
        <div className="flex gap-1 overflow-x-auto scrollbar-none">
          {BUCKETS.map((b) => (
            <button
              key={b}
              onClick={() => setBucket(b)}
              className={`px-3 py-1.5 rounded-lg text-sm capitalize ${
                bucket === b ? "bg-primary-600 text-white" : "bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300"
              }`}
            >
              {b}
            </button>
          ))}
        </div>
        <div className="flex gap-2">
          <a className="btn-secondary btn-sm" href={adsApi.exportUrl("followups")}>
            <Download size={15} className="mr-1" /> Export
          </a>
          <button
            className="btn-secondary btn-sm"
            onClick={async () => {
              const r = await adsApi.processFollowups();
              toast.success(`Processed: ${r.sent} sent, ${r.cancelled} stopped`);
              load();
            }}
          >
            Run due follow-ups
          </button>
        </div>
      </div>
      {loading ? (
        <div className="card p-8 text-center text-gray-500">Loading…</div>
      ) : items.length === 0 ? (
        <Empty title={`Nothing ${bucket}`} body="Follow-ups appear here as campaigns send." />
      ) : (
        <div className="grid gap-2">
          {items.map((i) => (
            <div key={i.id} className="card p-4">
              <div className="flex flex-wrap gap-3 justify-between items-start">
                <div>
                  <p className="font-medium">{i.contact}</p>
                  <p className="text-sm text-gray-500">
                    {i.business ? `${i.business} · ` : ""}
                    {i.phone_number} · {i.campaign || "Manual"}
                  </p>
                  {i.body && <p className="text-sm text-gray-600 dark:text-gray-300 mt-2">{i.body}</p>}
                  <p className="text-xs text-gray-400 mt-1">Due {fmtDate(i.due_at)}</p>
                </div>
                {i.status === "pending" && (
                  <div className="flex flex-wrap gap-2">
                    <button className="btn-primary btn-sm" onClick={() => act(i.id, "send-now")}>
                      Send now
                    </button>
                    <button className="btn-secondary btn-sm" onClick={() => act(i.id, "complete")}>
                      Complete
                    </button>
                    <button
                      className="btn-secondary btn-sm"
                      onClick={() => {
                        const when = prompt("Reschedule to (YYYY-MM-DD HH:MM)");
                        if (when) act(i.id, "reschedule", { due_at: new Date(when).toISOString() });
                      }}
                    >
                      Reschedule
                    </button>
                    <button className="btn-ghost btn-sm text-red-600" onClick={() => act(i.id, "cancel")}>
                      Cancel
                    </button>
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------- Calendar */

function CalendarSection() {
  const [items, setItems] = useState<any[]>([]);
  const [creating, setCreating] = useState(false);
  const [view, setView] = useState<"agenda" | "week" | "month">("agenda");

  const load = useCallback(async () => {
    setItems((await adsApi.calendar()).items);
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  const grouped = useMemo(() => {
    const map: Record<string, any[]> = {};
    items.forEach((i) => {
      const key = new Date(i.starts_at).toDateString();
      (map[key] = map[key] || []).push(i);
    });
    return map;
  }, [items]);

  const now = new Date();
  const windowDays = view === "week" ? 7 : view === "month" ? 31 : 3650;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2 justify-between">
        <div className="flex gap-1">
          {(["agenda", "week", "month"] as const).map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              className={`px-3 py-1.5 rounded-lg text-sm capitalize ${
                view === v ? "bg-primary-600 text-white" : "bg-gray-100 dark:bg-gray-700"
              }`}
            >
              {v}
            </button>
          ))}
        </div>
        <button className="btn-primary btn-sm" onClick={() => setCreating(true)}>
          <Plus size={15} className="mr-1" /> New event
        </button>
      </div>
      {items.length === 0 ? (
        <Empty
          icon={<CalendarDays size={40} />}
          title="No events yet"
          body="Book meetings, calls, tasks and reminders — from here or straight from a contact."
          action={
            <button className="btn-primary" onClick={() => setCreating(true)}>
              <Plus size={16} className="mr-1" /> Create event
            </button>
          }
        />
      ) : (
        <div className="space-y-3">
          {Object.entries(grouped)
            .filter(([day]) => {
              const diff = (new Date(day).getTime() - now.getTime()) / 86400000;
              return diff > -1 && diff < windowDays;
            })
            .map(([day, events]) => (
              <div className="card p-4" key={day}>
                <h3 className="font-semibold mb-2">{day}</h3>
                <div className="space-y-2">
                  {events.map((e) => (
                    <div
                      key={e.id}
                      className="flex flex-wrap gap-2 justify-between items-center p-2 rounded-lg bg-gray-50 dark:bg-gray-700/50"
                    >
                      <div>
                        <p className="font-medium text-sm">{e.title}</p>
                        <p className="text-xs text-gray-500">
                          {new Date(e.starts_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} ·{" "}
                          {e.event_type}
                          {e.contact ? ` · ${e.contact}` : ""}
                        </p>
                      </div>
                      <button
                        className="btn-ghost btn-sm text-red-600"
                        onClick={async () => {
                          await adsApi.deleteEvent(e.id);
                          load();
                        }}
                      >
                        Remove
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            ))}
        </div>
      )}
      {creating && (
        <EventModal
          close={() => setCreating(false)}
          saved={() => {
            setCreating(false);
            load();
          }}
        />
      )}
    </div>
  );
}

function EventModal({ close, saved }: { close: () => void; saved: () => void }) {
  const [form, setForm] = useState<any>({
    title: "",
    event_type: "meeting",
    starts_at: "",
    duration_minutes: 30,
    notes: "",
    priority: "normal",
  });
  const set = (k: string, v: any) => setForm((f: any) => ({ ...f, [k]: v }));
  return (
    <Modal title="New calendar event" close={close}>
      <form
        className="space-y-4"
        onSubmit={async (e) => {
          e.preventDefault();
          if (!form.title.trim() || !form.starts_at) return toast.error("Add a title and date");
          try {
            await adsApi.createEvent({
              ...form,
              starts_at: fromLocalInput(form.starts_at),
              duration_minutes: Number(form.duration_minutes),
            });
            toast.success("Event created");
            saved();
          } catch (err: any) {
            toast.error(err.response?.data?.detail || "Could not create event");
          }
        }}
      >
        <Field label="Title">
          <input className="input" value={form.title} onChange={(e) => set("title", e.target.value)} autoFocus />
        </Field>
        <div className="grid sm:grid-cols-2 gap-3">
          <Field label="Type">
            <select className="input" value={form.event_type} onChange={(e) => set("event_type", e.target.value)}>
              {["meeting", "call", "followup", "task", "reminder"].map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </Field>
          <Field label="When">
            <input
              type="datetime-local"
              className="input"
              value={form.starts_at}
              onChange={(e) => set("starts_at", e.target.value)}
            />
          </Field>
          <Field label="Duration (minutes)">
            <input
              type="number"
              className="input"
              value={form.duration_minutes}
              onChange={(e) => set("duration_minutes", e.target.value)}
            />
          </Field>
          <Field label="Priority">
            <select className="input" value={form.priority} onChange={(e) => set("priority", e.target.value)}>
              <option value="high">High</option>
              <option value="normal">Normal</option>
              <option value="low">Low</option>
            </select>
          </Field>
        </div>
        <Field label="Notes">
          <textarea className="input" rows={3} value={form.notes} onChange={(e) => set("notes", e.target.value)} />
        </Field>
        <div className="flex gap-2">
          <button type="button" className="btn-secondary flex-1" onClick={close}>
            Cancel
          </button>
          <button className="btn-primary flex-1">Create event</button>
        </div>
      </form>
    </Modal>
  );
}

/* ------------------------------------------------------------ Analytics */

function AnalyticsSection({ campaigns, onOpen }: { campaigns: AdsCampaign[]; onOpen: (id: number) => void }) {
  const ranked = [...campaigns].sort((a, b) => (b.score || 0) - (a.score || 0));
  if (campaigns.length === 0) return <Empty title="No campaigns to analyse yet" />;
  return (
    <div className="card overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-left text-gray-500 border-b border-gray-100 dark:border-gray-700">
          <tr>
            <th className="p-3">Campaign</th>
            <th className="p-3">Status</th>
            <th className="p-3">Objective</th>
            <th className="p-3">Audience</th>
            <th className="p-3">Sent</th>
            <th className="p-3">Replies</th>
            <th className="p-3">Reply rate</th>
            <th className="p-3">Score</th>
            <th className="p-3" />
          </tr>
        </thead>
        <tbody>
          {ranked.map((c) => (
            <tr key={c.id} className="border-b border-gray-50 dark:border-gray-700/50">
              <td className="p-3 font-medium">{c.name}</td>
              <td className="p-3">
                <Badge value={c.status} />
              </td>
              <td className="p-3">{c.objective}</td>
              <td className="p-3">{c.stats?.assigned ?? 0}</td>
              <td className="p-3">{c.stats?.sent ?? 0}</td>
              <td className="p-3">{c.stats?.replies ?? 0}</td>
              <td className="p-3">{c.stats?.reply_rate ?? 0}%</td>
              <td className="p-3 font-semibold">{c.score ?? 0}</td>
              <td className="p-3">
                <button className="btn-secondary btn-sm" onClick={() => onOpen(c.id)}>
                  Open
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ---------------------------------------------------------- Suppression */

function SuppressionSection() {
  const [data, setData] = useState<any>({ items: [], total: 0 });
  const [search, setSearch] = useState("");
  const [adding, setAdding] = useState(false);

  const load = useCallback(async () => {
    setData(await adsApi.suppression({ search: search || undefined }));
  }, [search]);
  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2 justify-between">
        <input
          className="input flex-1 min-w-[200px]"
          placeholder="Search phone number…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <div className="flex gap-2">
          <a className="btn-secondary btn-sm" href={adsApi.exportUrl("suppression")}>
            <Download size={15} className="mr-1" /> Export
          </a>
          <button className="btn-primary btn-sm" onClick={() => setAdding(true)}>
            <Plus size={15} className="mr-1" /> Suppress a number
          </button>
        </div>
      </div>
      <p className="text-sm text-gray-500">
        {data.total} suppressed. Suppressed numbers are blocked at send time, not only when a queue is built —
        so a contact who opts out after being queued is still protected.
      </p>
      {data.items.length === 0 ? (
        <Empty title="Suppression list is empty" />
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-gray-500 border-b border-gray-100 dark:border-gray-700">
              <tr>
                <th className="p-3">Phone</th>
                <th className="p-3">Reason</th>
                <th className="p-3">Source</th>
                <th className="p-3">Date</th>
                <th className="p-3" />
              </tr>
            </thead>
            <tbody>
              {data.items.map((r: any) => (
                <tr key={r.id} className="border-b border-gray-50 dark:border-gray-700/50">
                  <td className="p-3 whitespace-nowrap">{r.phone_number}</td>
                  <td className="p-3">{r.reason || "—"}</td>
                  <td className="p-3">{r.source}</td>
                  <td className="p-3 whitespace-nowrap">{fmtDay(r.suppressed_at)}</td>
                  <td className="p-3">
                    <button
                      className="btn-ghost btn-sm text-red-600"
                      onClick={async () => {
                        if (!confirm("Remove from suppression list?")) return;
                        await adsApi.removeSuppression(r.id);
                        toast.success("Removed");
                        load();
                      }}
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {adding && (
        <SuppressModal
          close={() => setAdding(false)}
          saved={() => {
            setAdding(false);
            load();
          }}
        />
      )}
    </div>
  );
}

function SuppressModal({ close, saved }: { close: () => void; saved: () => void }) {
  const [phone, setPhone] = useState("");
  const [reason, setReason] = useState("Opted out");
  return (
    <Modal title="Suppress a number" close={close}>
      <form
        className="space-y-4"
        onSubmit={async (e) => {
          e.preventDefault();
          if (!phone.trim()) return toast.error("Enter a phone number");
          try {
            await adsApi.addSuppression({ phone_number: phone.trim(), reason });
            toast.success("Number suppressed");
            saved();
          } catch (err: any) {
            toast.error(err.response?.data?.detail || "Could not suppress");
          }
        }}
      >
        <Field label="Phone number">
          <input className="input" value={phone} onChange={(e) => setPhone(e.target.value)} autoFocus />
        </Field>
        <Field label="Reason">
          <select className="input" value={reason} onChange={(e) => setReason(e.target.value)}>
            {[
              "Opted out",
              "Not interested",
              "Invalid number",
              "Manual suppression",
              "Compliance",
              "Duplicate",
              "Customer preference",
              "Other",
            ].map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </Field>
        <div className="flex gap-2">
          <button type="button" className="btn-secondary flex-1" onClick={close}>
            Cancel
          </button>
          <button className="btn-primary flex-1">Suppress</button>
        </div>
      </form>
    </Modal>
  );
}

/* --------------------------------------------------------- Activity log */

function ActivitySection() {
  const [items, setItems] = useState<any[]>([]);
  useEffect(() => {
    adsApi.activity().then((r) => setItems(r.items));
  }, []);
  if (items.length === 0) return <Empty title="No activity recorded yet" />;
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
