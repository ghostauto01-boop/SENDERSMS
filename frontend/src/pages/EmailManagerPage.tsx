import { useCallback, useEffect, useMemo, useState } from "react";
import toast from "react-hot-toast";
import { useNavigate } from "react-router-dom";
import {
  Activity,
  BarChart3,
  CalendarDays,
  CheckCircle2,
  Clock3,
  Copy,
  Download,
  Eye,
  FileText,
  Inbox,
  KeyRound,
  LayoutGrid,
  Mail,
  Megaphone,
  MousePointerClick,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Search,
  Send,
  ShieldCheck,
  ShieldOff,
  Sparkles,
  Trash2,
  UserRound,
  Users,
  Wand2,
  XCircle,
} from "lucide-react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import api from "../api/client";
import emailApi, { EmailAccount, EmailMessage } from "../api/email";
import adsApi, { AdsCampaign } from "../api/ads";
import CampaignBuilder from "./ads/CampaignBuilder";
import CampaignDetail from "./ads/CampaignDetail";
import EmailSendPanel from "../components/EmailSendPanel";
import MailboxRepliesTab from "../components/MailboxRepliesTab";
import RichEmailEditor from "../components/RichEmailEditor";
import {
  Badge,
  Empty,
  Field,
  Metric,
  Modal,
  Stat,
  fmtDate,
  fmtDay,
  fromLocalInput,
} from "./ads/ui";

/**
 * EMAIL MANAGER
 *
 * The email twin of the SMS Ads Manager: email campaigns -> email sets ->
 * creatives -> audiences -> A/B testing -> budgets -> drip -> follow-ups ->
 * Andromeda auto-optimization -> calendar -> analytics. Sending lives on
 * Brevo, and any number of Brevo keys can be saved at once — the Senders tab
 * is where they are added, tested and picked as the default, and every
 * campaign can override which one it sends through.
 *
 * The second half of the tabs (Senders, Quick Send, Templates,
 * Deliverability, Suppression, Activity) are the email channel extras the SMS
 * side does not need: Brevo senders, one-off sends, the template library,
 * domain authentication and the Brevo event stream.
 */

const SECTIONS = [
  { key: "Overview", icon: LayoutGrid },
  { key: "Campaigns", icon: Megaphone },
  { key: "Audience", icon: Users },
  { key: "Automation", icon: Wand2 },
  { key: "Follow-Ups", icon: Clock3 },
  { key: "Calendar", icon: CalendarDays },
  { key: "Analytics", icon: BarChart3 },
  { key: "Senders", icon: KeyRound },
  { key: "Quick Send", icon: Send },
  { key: "Templates", icon: FileText },
  { key: "Replies", icon: Inbox },
  { key: "Deliverability", icon: ShieldCheck },
  { key: "Suppression", icon: ShieldOff },
  { key: "Activity", icon: Activity },
] as const;

type Section = (typeof SECTIONS)[number]["key"];

const emptyAccount = {
  name: "",
  from_name: "",
  from_email: "",
  api_key: "",
  reply_to: "",
  daily_limit: "" as string | number,
  is_active: true,
  is_default: false,
  track_opens: true,
  track_clicks: true,
};

/**
 * Which tab to open, and what to say about it, when the page is reached with a
 * query string. The Gmail OAuth callback lands here with `?section=Replies`
 * after Google sends the browser back, so the operator arrives on the panel that
 * just changed instead of on the Overview wondering whether it worked.
 */
function sectionFromQuery(): Section {
  const wanted = new URLSearchParams(window.location.search).get("section");
  return SECTIONS.some((s) => s.key === wanted) ? (wanted as Section) : "Overview";
}

function replyBannerFromQuery(): string | null {
  const params = new URLSearchParams(window.location.search);
  if (params.get("mailbox") === "error") {
    return "That mailbox connection did not complete. Nothing was saved — try again, or connect with an app password instead.";
  }
  const address = params.get("address");
  return address ? `${address} is connected. Replies to it now arrive in the Email Inbox.` : null;
}

export default function EmailManagerPage() {
  const [section, setSection] = useState<Section>(sectionFromQuery);
  const [replyBanner] = useState<string | null>(replyBannerFromQuery);
  const navigate = useNavigate();

  const [campaigns, setCampaigns] = useState<AdsCampaign[]>([]);
  const [adsOverview, setAdsOverview] = useState<any>(null);
  const [emailOverview, setEmailOverview] = useState<any>(null);
  const [accounts, setAccounts] = useState<EmailAccount[]>([]);
  const [adsReference, setAdsReference] = useState<any>(null);
  const [emailReference, setEmailReference] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [openId, setOpenId] = useState<number | null>(null);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [accountModal, setAccountModal] = useState<null | { editing?: EmailAccount }>(null);

  const load = useCallback(async () => {
    try {
      const [c, ao, eo, a] = await Promise.all([
        adsApi.listCampaigns({ per_page: 100, channel: "email" }),
        adsApi.overview({ channel: "email" }),
        emailApi.overview({ days: 30 }),
        emailApi.listAccounts(),
      ]);
      setCampaigns(c.items);
      setAdsOverview(ao);
      setEmailOverview(eo);
      setAccounts(a.items);
    } catch {
      toast.error("Could not load the Email Manager");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    adsApi.reference().then(setAdsReference).catch(() => {});
    emailApi.reference().then(setEmailReference).catch(() => {});
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

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-primary-600" />
      </div>
    );
  }

  return (
    <div className="space-y-5 max-w-6xl mx-auto">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm font-medium text-primary-600 mb-1 flex items-center gap-1">
            <Mail size={15} /> EMAIL CHANNEL · BREVO · <Sparkles size={13} /> ADVANCED
          </p>
          <h1 className="text-2xl sm:text-3xl font-bold">Email Manager</h1>
          <p className="text-gray-500 mt-1">
            Email campaigns, email sets, creatives, A/B testing, Andromeda, follow-ups and
            analytics — plus senders, templates, replies and deliverability.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button className="btn-secondary" onClick={() => navigate("/email-inbox")}>
            <Inbox size={17} className="mr-1" /> Email inbox
            {emailOverview?.unread_conversations ? (
              <span className="ml-2 badge-red">{emailOverview.unread_conversations}</span>
            ) : null}
          </button>
          <button className="btn-secondary" onClick={() => setAccountModal({})}>
            <Plus size={17} className="mr-1" /> Add sender
          </button>
          <button className="btn-primary" onClick={() => setCreating(true)}>
            <Plus size={17} className="mr-1" /> Create campaign
          </button>
        </div>
      </div>

      {!accounts.length && (
        <div className="card border-l-4 border-warning-400">
          <h3 className="font-semibold flex items-center gap-2">
            <KeyRound size={17} /> Connect Brevo to start sending email
          </h3>
          <p className="text-sm text-gray-500 mt-1">
            Create an API key at brevo.com (SMTP &amp; API → API keys), then paste it here with the
            From name and From address. You can add as many keys as you like — if one gets burned
            or rate-limited, add another and keep sending.
          </p>
          <button className="btn-primary mt-3" onClick={() => setAccountModal({})}>
            <Plus size={17} className="mr-1" /> Add my first sender
          </button>
        </div>
      )}

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

      {section === "Overview" && (
        <ManagerOverview
          ads={adsOverview}
          email={emailOverview}
          accounts={accounts}
          campaigns={campaigns}
          onOpen={setOpenId}
          onCreate={() => setCreating(true)}
          onAddSender={() => setAccountModal({})}
          goSenders={() => setSection("Senders")}
        />
      )}
      {section === "Campaigns" && (
        <ManagerCampaigns
          campaigns={filtered}
          query={query}
          setQuery={setQuery}
          statusFilter={statusFilter}
          setStatusFilter={setStatusFilter}
          accounts={accounts}
          onOpen={setOpenId}
          reload={load}
          create={() => setCreating(true)}
        />
      )}
      {section === "Audience" && (
        <ManagerAudience reference={adsReference} campaigns={campaigns} />
      )}
      {section === "Automation" && (
        <ManagerAutomation campaigns={campaigns} onOpen={setOpenId} />
      )}
      {section === "Follow-Ups" && <ManagerFollowUps />}
      {section === "Calendar" && <ManagerCalendar />}
      {section === "Analytics" && (
        <ManagerAnalytics campaigns={campaigns} onOpen={setOpenId} />
      )}
      {section === "Senders" && (
        <SendersTab
          accounts={accounts}
          reload={load}
          onEdit={(account: EmailAccount) => setAccountModal({ editing: account })}
          onAdd={() => setAccountModal({})}
        />
      )}
      {section === "Quick Send" && <SendTab reference={emailReference} accounts={accounts} />}
      {section === "Templates" && <TemplatesTab reference={emailReference} />}
      {section === "Replies" && <MailboxRepliesTab banner={replyBanner} />}
      {section === "Deliverability" && <DeliverabilityTab accounts={accounts} />}
      {section === "Suppression" && <SuppressionTab />}
      {section === "Activity" && <ActivityTab accounts={accounts} />}

      {creating && (
        <CampaignBuilder
          objectives={adsReference?.objectives || []}
          channel="email"
          emailAccounts={accounts}
          defaultEmailAccountId={emailOverview?.default_account_id ?? null}
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
          reference={adsReference}
          channel="email"
          backLabel="Email Manager"
          onClose={() => setOpenId(null)}
          onChanged={load}
        />
      )}
      {accountModal && (
        <AccountModal
          account={accountModal.editing}
          close={() => setAccountModal(null)}
          saved={() => {
            setAccountModal(null);
            load();
          }}
        />
      )}
    </div>
  );
}

/* ========================================================================== */
/* Overview                                                                    */
/* ========================================================================== */

function ManagerOverview({ ads, email, accounts, campaigns, onOpen, onCreate, onAddSender, goSenders }: any) {
  const totals = email?.totals || {};
  const adsTotals = ads?.totals || {};
  const series = email?.series || [];
  const active = campaigns.filter((c: AdsCampaign) => c.status === "active");
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
        <Stat label="Sent (30d)" value={totals.sent ?? 0} />
        <Stat label="Delivered" value={totals.delivered ?? 0} hint={`${totals.delivery_rate ?? 0}%`} />
        <Stat label="Opened" value={totals.opened ?? 0} hint={`${totals.open_rate ?? 0}% · Brevo`} />
        <Stat label="Clicked" value={totals.clicked ?? 0} hint={`${totals.click_rate ?? 0}% · Brevo`} />
        <Stat label="Replies" value={totals.replies ?? 0} hint={`${totals.reply_rate ?? 0}%`} />
        <Stat label="Failed / bounced" value={totals.failed ?? 0} hint={`${totals.failure_rate ?? 0}%`} />
        <Stat label="Active campaigns" value={ads?.active_campaigns ?? 0} />
        <Stat label="Follow-ups due" value={ads?.followups_due ?? 0} />
        <Stat label="Positive replies" value={adsTotals.positive_replies ?? 0} />
        <Stat label="Meetings" value={ads?.meetings ?? 0} />
        <Stat label="In queue" value={totals.queued ?? 0} />
        <Stat label="Unsubscribed" value={email?.suppressed_total ?? 0} />
      </div>

      <div className="card">
        <h3 className="font-semibold mb-3">Email activity (30 days)</h3>
        {series.length ? (
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={series}>
                <CartesianGrid strokeDasharray="3 3" strokeOpacity={0.15} />
                <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
                <Tooltip />
                <Area type="monotone" dataKey="sent" name="Sent" stroke="#4f46e5" fill="#4f46e5" />
                <Area type="monotone" dataKey="opened" name="Opens" stroke="#4163f6" fill="#0ea5e940" />
                <Area type="monotone" dataKey="clicks" name="Clicks" stroke="#059669" fill="#059669" />
                <Area type="monotone" dataKey="replies" name="Replies" stroke="#f59e0b" fill="#f59e0b" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <Empty title="No email activity yet" body="Add a sender and send your first campaign." />
        )}
      </div>

      <div>
        <div className="flex items-center justify-between mb-2">
          <h3 className="font-semibold">Running now</h3>
          <button className="btn-primary btn-sm" onClick={onCreate}>
            <Plus size={15} className="mr-1" /> Create campaign
          </button>
        </div>
        {active.length === 0 ? (
          <Empty
            title="No active email campaigns"
            body="Create one with email sets and creatives, launch it, and Andromeda can auto-shift spend to the winner."
            action={
              <button className="btn-primary" onClick={onCreate}>
                <Plus size={16} className="mr-1" /> Create campaign
              </button>
            }
          />
        ) : (
          <div className="grid gap-3">
            {active.map((c: AdsCampaign) => (
              <EmailCampaignRow key={c.id} campaign={c} onOpen={onOpen} accounts={accounts} />
            ))}
          </div>
        )}
      </div>

      <div className="grid lg:grid-cols-2 gap-4">
        <div className="card">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold">Sender health</h3>
            <button className="text-sm text-primary-600" onClick={goSenders}>
              Manage
            </button>
          </div>
          {accounts.length ? (
            <div className="space-y-3">
              {accounts.map((a: EmailAccount) => (
                <div key={a.id} className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <p className="font-medium truncate flex items-center gap-2">
                      {a.name}
                      {a.is_default && <span className="badge-blue">default</span>}
                    </p>
                    <p className="text-xs text-gray-500 truncate">
                      {a.from_name} &lt;{a.from_email}&gt;
                      {a.daily_limit ? ` · ${a.sent_today}/${a.daily_limit} today` : ` · ${a.sent_today} today`}
                    </p>
                  </div>
                  {a.connection_status === "error" ? (
                    <span className="badge-red">key error</span>
                  ) : a.is_active ? (
                    <span className="badge-green">active</span>
                  ) : (
                    <span className="badge-gray">off</span>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <Empty
              title="No senders yet"
              body="Add a Brevo API key to start sending."
              action={
                <button className="btn-primary" onClick={onAddSender}>
                  <Plus size={16} className="mr-1" /> Add sender
                </button>
              }
            />
          )}
        </div>

        <div className="card">
          <h3 className="font-semibold mb-3">Latest Brevo events</h3>
          {email?.recent_events?.length ? (
            <div className="divide-y divide-gray-100 dark:divide-gray-800">
              {email.recent_events.map((e: any) => (
                <div key={e.id} className="py-2 flex items-center justify-between gap-3 text-sm">
                  <div className="min-w-0">
                    <span className="font-medium">{e.event_type}</span>{" "}
                    <span className="text-gray-500 truncate">{e.email_address}</span>
                  </div>
                  <span className="text-xs text-gray-400 shrink-0">{fmtDate(e.created_at)}</span>
                </div>
              ))}
            </div>
          ) : (
            <Empty
              title="No webhook events yet"
              body="Paste each sender's webhook URL into Brevo (Transactional webhook + inbound parsing) to receive opens, clicks and replies."
            />
          )}
        </div>
      </div>
    </div>
  );
}

/* ========================================================================== */
/* Campaigns (ads engine: email sets + creatives + Andromeda)                  */
/* ========================================================================== */

function EmailCampaignRow({
  campaign,
  accounts,
  onOpen,
  reload,
}: {
  campaign: AdsCampaign;
  accounts: EmailAccount[];
  onOpen: (id: number) => void;
  reload?: () => void;
}) {
  const s = campaign.stats;
  const sender = accounts.find((a) => a.id === campaign.email_account_id);
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
            {campaign.subject || campaign.description || campaign.objective.replace(/_/g, " ")}
          </p>
          {sender && (
            <p className="text-xs text-gray-400 mt-0.5">
              via {sender.name} · {sender.from_email}
            </p>
          )}
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
        <Metric label="Opens" value={`${s?.opens ?? 0} (${s?.open_rate ?? 0}%)`} />
        <Metric label="Clicks" value={`${s?.clicks ?? 0} (${s?.click_rate ?? 0}%)`} />
        <Metric label="Replies" value={s?.replies ?? 0} />
        <Metric label="Last activity" value={fmtDay(campaign.last_activity_at || campaign.updated_at)} />
      </div>
    </div>
  );
}

function ManagerCampaigns({
  campaigns,
  query,
  setQuery,
  statusFilter,
  setStatusFilter,
  accounts,
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
            placeholder="Search email campaigns…"
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
        <a className="btn-secondary btn-sm" href={adsApi.exportUrl("campaigns", undefined, "email")}>
          <Download size={15} className="mr-1" /> Export
        </a>
      </div>
      {campaigns.length === 0 ? (
        <Empty
          icon={<Megaphone size={40} />}
          title="No email campaigns yet"
          body="A campaign holds email sets, each set holds creatives (subject + body + HTML), and the audience is split between them automatically. Andromeda can shift spend to the winner."
          action={
            <button className="btn-primary" onClick={create}>
              <Plus size={16} className="mr-1" /> Create campaign
            </button>
          }
        />
      ) : (
        <div className="grid gap-3">
          {campaigns.map((c: AdsCampaign) => (
            <EmailCampaignRow key={c.id} campaign={c} accounts={accounts} onOpen={onOpen} reload={reload} />
          ))}
        </div>
      )}
    </div>
  );
}

/* ========================================================================== */
/* Audience                                                                    */
/* ========================================================================== */

function ManagerAudience({ reference, campaigns }: { reference: any; campaigns: AdsCampaign[] }) {
  const totalAssigned = campaigns.reduce((a, c) => a + (c.stats?.assigned || 0), 0);
  const [audiences, setAudiences] = useState<any[]>([]);
  useEffect(() => {
    adsApi.listAudiences().then((r) => setAudiences(r.items)).catch(() => {});
  }, []);
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Stat label="Emailable contacts" value={reference?.emailable_contacts ?? 0} />
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
          Email sets stay empty until you add at least one list, contact, audience or filter — nothing is ever
          pulled in automatically. Contacts without an email address are screened out before sending.
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

/* ========================================================================== */
/* Automation                                                                  */
/* ========================================================================== */

function ManagerAutomation({ campaigns, onOpen }: { campaigns: AdsCampaign[]; onOpen: (id: number) => void }) {
  return (
    <div className="space-y-3">
      <div className="card p-5 text-sm text-gray-500">
        Follow-up workflows live inside each campaign so their conditions (opened, clicked, replied…) can see
        that campaign's replies and creatives. Open a campaign and use the <b>Automation</b> tab.
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

/* ========================================================================== */
/* Follow-Ups                                                                  */
/* ========================================================================== */

const FOLLOWUP_BUCKETS = ["today", "overdue", "upcoming", "waiting", "completed", "cancelled"];

function ManagerFollowUps() {
  const [bucket, setBucket] = useState("today");
  const [items, setItems] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setItems((await adsApi.followups(bucket, "email")).items);
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
          {FOLLOWUP_BUCKETS.map((b) => (
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
          <a className="btn-secondary btn-sm" href={adsApi.exportUrl("followups", undefined, "email")}>
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
        <Empty title={`Nothing ${bucket}`} body="Follow-ups appear here as email campaigns send." />
      ) : (
        <div className="grid gap-2">
          {items.map((i) => (
            <div key={i.id} className="card p-4">
              <div className="flex flex-wrap gap-3 justify-between items-start">
                <div>
                  <p className="font-medium">{i.contact}</p>
                  <p className="text-sm text-gray-500">
                    {i.business ? `${i.business} · ` : ""}
                    {i.email_address || i.phone_number} · {i.campaign || "Manual"}
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
                    <button className="btn-ghost btn-sm text-danger-600" onClick={() => act(i.id, "cancel")}>
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

/* ========================================================================== */
/* Calendar                                                                    */
/* ========================================================================== */

function ManagerCalendar() {
  const [items, setItems] = useState<any[]>([]);
  const [creating, setCreating] = useState(false);
  const [view, setView] = useState<"agenda" | "week" | "month">("agenda");

  const load = useCallback(async () => {
    setItems((await adsApi.calendar({ channel: "email" })).items);
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
                        className="btn-ghost btn-sm text-danger-600"
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
        <ManagerEventModal
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

function ManagerEventModal({ close, saved }: { close: () => void; saved: () => void }) {
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

/* ========================================================================== */
/* Analytics                                                                   */
/* ========================================================================== */

function ManagerAnalytics({ campaigns, onOpen }: { campaigns: AdsCampaign[]; onOpen: (id: number) => void }) {
  const ranked = [...campaigns].sort((a, b) => (b.score || 0) - (a.score || 0));
  if (campaigns.length === 0) return <Empty title="No email campaigns to analyse yet" />;
  return (
    <div className="card overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-left text-gray-500 border-b border-gray-100 dark:border-gray-700">
          <tr>
            <th className="p-3">Campaign</th>
            <th className="p-3">Status</th>
            <th className="p-3">Audience</th>
            <th className="p-3">Sent</th>
            <th className="p-3">Opens</th>
            <th className="p-3">Clicks</th>
            <th className="p-3">Replies</th>
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
              <td className="p-3">{c.stats?.assigned ?? 0}</td>
              <td className="p-3">{c.stats?.sent ?? 0}</td>
              <td className="p-3">{c.stats?.open_rate ?? 0}%</td>
              <td className="p-3">{c.stats?.click_rate ?? 0}%</td>
              <td className="p-3">{c.stats?.replies ?? 0}</td>
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

/* ========================================================================== */
/* Senders                                                                     */
/* ========================================================================== */

function SendersTab({ accounts, reload, onEdit, onAdd }: any) {
  const [testing, setTesting] = useState<number | null>(null);
  const [testSendFor, setTestSendFor] = useState<EmailAccount | null>(null);

  const test = async (account: EmailAccount) => {
    setTesting(account.id);
    try {
      const result = await emailApi.testAccount(account.id);
      if (result.success) toast.success(`"${account.name}" is connected to Brevo`);
      else toast.error(result.error || "Brevo rejected this API key");
      reload();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not reach Brevo");
    } finally {
      setTesting(null);
    }
  };

  const makeDefault = async (account: EmailAccount) => {
    try {
      await emailApi.makeDefault(account.id);
      toast.success(`"${account.name}" is now the default sender`);
      reload();
    } catch {
      toast.error("Could not change the default sender");
    }
  };

  const toggleActive = async (account: EmailAccount) => {
    try {
      await emailApi.updateAccount(account.id, { is_active: !account.is_active });
      reload();
    } catch {
      toast.error("Could not update this sender");
    }
  };

  const remove = async (account: EmailAccount) => {
    if (!window.confirm(`Delete the sender "${account.name}"? Sends using it will fail until another is picked.`))
      return;
    try {
      await emailApi.deleteAccount(account.id);
      toast.success("Sender deleted");
      reload();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not delete this sender");
    }
  };

  // The webhook token is a credential: account reads only carry a masked hint, and the
  // real URL is fetched here, on an explicit click.
  const copyText = async (text: string, success: string, fallbackPrompt: string) => {
    try {
      await navigator.clipboard.writeText(text);
      toast.success(success);
    } catch {
      window.prompt(fallbackPrompt, text);
    }
  };

  const copyWebhook = async (account: EmailAccount) => {
    if (!account.webhook_url) {
      toast.error("Set PUBLIC_BASE_URL on the server to get a webhook URL");
      return;
    }
    try {
      const revealed = await emailApi.revealWebhook(account.id);
      await copyText(
        revealed.webhook_url || revealed.webhook_path,
        "Webhook URL copied — paste it into Brevo",
        "Copy this webhook URL into Brevo:",
      );
    } catch (err: any) {
      toast.error(err.response?.data?.message || "Could not fetch the webhook URL");
    }
  };

  const rotateWebhook = async (account: EmailAccount) => {
    if (!window.confirm(
      `Rotate the webhook token for "${account.name}"? The current URL stops working immediately, ` +
      "so Brevo's deliveries are refused until you paste the new URL into Brevo.",
    )) return;
    try {
      const rotated = await emailApi.rotateWebhook(account.id);
      await copyText(
        rotated.webhook_url || rotated.webhook_path,
        "Token rotated — the new URL is copied. Paste it into Brevo now.",
        "New webhook URL — paste it into Brevo now:",
      );
      reload();
    } catch (err: any) {
      toast.error(err.response?.data?.message || "Could not rotate the webhook token");
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-gray-500">
          Each sender is its own Brevo API key. A campaign can pick any of them, and the default is
          used when it does not.
        </p>
        <button className="btn-primary" onClick={onAdd}>
          <Plus size={16} className="mr-1" /> Add sender
        </button>
      </div>

      {accounts.length === 0 && (
        <Empty title="No Brevo senders yet" body="Add an API key + From address to send email." />
      )}

      <div className="grid md:grid-cols-2 gap-4">
        {accounts.map((account: EmailAccount) => (
          <div key={account.id} className="card space-y-3">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <h3 className="font-semibold truncate flex items-center gap-2">
                  {account.name}
                  {account.is_default && <span className="badge-blue">default</span>}
                  {!account.is_active && <span className="badge-gray">off</span>}
                </h3>
                <p className="text-sm text-gray-500 truncate">
                  {account.from_name} &lt;{account.from_email}&gt;
                </p>
                <p className="text-xs text-gray-400">
                  key {account.api_key_masked || "—"} ·{" "}
                  {account.daily_limit
                    ? `${account.sent_today}/${account.daily_limit} today`
                    : `${account.sent_today} sent today`}
                </p>
              </div>
              {account.connection_status === "error" ? (
                <span className="badge-red shrink-0 flex items-center gap-1">
                  <XCircle size={12} /> error
                </span>
              ) : account.connection_status === "connected" ? (
                <span className="badge-green shrink-0 flex items-center gap-1">
                  <CheckCircle2 size={12} /> connected
                </span>
              ) : (
                <span className="badge-gray shrink-0 flex items-center gap-1">
                  <RefreshCw size={12} /> untested
                </span>
              )}
            </div>

            {account.last_error && (
              <p className="text-xs text-danger-500 line-clamp-2" title={account.last_error}>
                {account.last_error}
              </p>
            )}

            <div className="grid grid-cols-3 gap-2 text-center">
              <Metric label="Sent" value={account.sent_total ?? account.total_sent ?? 0} />
              <Metric label="Failed" value={account.failed_total ?? 0} />
              <Metric label="Campaigns" value={account.campaigns_using ?? 0} />
            </div>

            <div className="flex flex-wrap gap-2">
              <button className="btn-secondary text-sm" onClick={() => test(account)} disabled={testing === account.id}>
                {testing === account.id ? "Testing…" : "Test connection"}
              </button>
              <button className="btn-secondary text-sm" onClick={() => setTestSendFor(account)}>
                Send test email
              </button>
              {!account.is_default && (
                <button className="btn-secondary text-sm" onClick={() => makeDefault(account)}>
                  Make default
                </button>
              )}
              <button className="btn-secondary text-sm" onClick={() => toggleActive(account)}>
                {account.is_active ? "Switch off" : "Switch on"}
              </button>
              <button className="btn-secondary text-sm" onClick={() => copyWebhook(account)}>
                Copy webhook URL
              </button>
              <button
                className="text-gray-500 hover:text-primary-600 text-sm px-2"
                title="Issue a new webhook token; the current one stops working"
                onClick={() => rotateWebhook(account)}
              >
                Rotate token
              </button>
              <button className="text-gray-500 hover:text-primary-600 text-sm px-2" onClick={() => onEdit(account)}>
                Edit
              </button>
              <button className="text-gray-400 hover:text-danger-500 text-sm px-2" onClick={() => remove(account)}>
                <Trash2 size={15} />
              </button>
            </div>
          </div>
        ))}
      </div>

      {testSendFor && (
        <TestSendModal account={testSendFor} close={() => setTestSendFor(null)} />
      )}
    </div>
  );
}

function TestSendModal({ account, close }: { account: EmailAccount; close: () => void }) {
  const [to, setTo] = useState("");
  const [busy, setBusy] = useState(false);
  const send = async () => {
    setBusy(true);
    try {
      const result = await emailApi.testSend(account.id, to);
      if (result.success) toast.success("Test email sent — check the inbox (and spam)");
      else toast.error(result.error || "Brevo rejected the send");
      if (result.success) close();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not send the test email");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title={`Send a test email via ${account.name}`} close={close}>
      <div className="space-y-4">
        <Field label="Send to">
          <input
            className="input"
            placeholder="you@yourdomain.com"
            value={to}
            onChange={(e) => setTo(e.target.value)}
            autoFocus
          />
        </Field>
        <div className="flex justify-end gap-2">
          <button className="btn-secondary" onClick={close}>
            Cancel
          </button>
          <button className="btn-primary" onClick={send} disabled={busy || !to.includes("@")}>
            {busy ? "Sending…" : "Send test"}
          </button>
        </div>
      </div>
    </Modal>
  );
}

function AccountModal({
  account,
  close,
  saved,
}: {
  account?: EmailAccount;
  close: () => void;
  saved: () => void;
}) {
  const [form, setForm] = useState<any>({
    ...emptyAccount,
    ...(account
      ? {
          ...account,
          api_key: "",
          reply_to: account.reply_to_addresses?.join(", ") || account.reply_to || "",
          daily_limit: account.daily_limit ?? "",
        }
      : {}),
  });
  const [saving, setSaving] = useState(false);
  const [senders, setSenders] = useState<any[]>([]);
  const [domains, setDomains] = useState<any[]>([]);
  const [loadingSenders, setLoadingSenders] = useState(false);
  const [probeNote, setProbeNote] = useState<string | null>(null);
  const set = (k: string, v: any) => setForm((f: any) => ({ ...f, [k]: v }));

  /** Ask Brevo about the key — before saving it when it is brand new.
   *
   * Using an address Brevo has already verified (ideally on an authenticated
   * domain) is what keeps mail out of spam, so the picker is deliberately part
   * of the add-sender flow rather than a step you have to remember later.
   */
  const loadSenders = async () => {
    const typed = form.api_key.trim();
    setLoadingSenders(true);
    setProbeNote(null);
    try {
      const result = typed
        ? await emailApi.sendersPreview(typed)
        : account
        ? await (async () => {
            const list = await emailApi.verifiedSenders(account.id);
            return {
              success: true,
              error: null,
              senders: (list.items || []).map((s: any) => ({
                email: s?.email || s?.Email || s?.emailAddress,
                name: s?.name || s?.Name || "",
                active: s?.active ?? true,
              })),
              domains: [] as any[],
            };
          })()
        : { success: false, error: "Paste the API key first", senders: [], domains: [] };

      setSenders((result.senders || []).filter((s: any) => s.email));
      setDomains(result.domains || []);
      if (result.error) setProbeNote(result.error);
      else if (!(result.senders || []).length) {
        setProbeNote("Brevo has no verified sender under this key yet.");
      }
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not read the senders from Brevo");
    } finally {
      setLoadingSenders(false);
    }
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.name.trim()) return toast.error("Give this sender a name");
    if (!form.from_email.includes("@")) return toast.error("Enter the From email address");
    if (!account && !form.api_key.trim()) return toast.error("Paste the Brevo API key");
    if (form.from_name && !form.from_email.includes("@")) return toast.error("Check the From address");

    const payload: any = {
      name: form.name.trim(),
      from_name: (form.from_name || form.name).trim(),
      from_email: form.from_email.trim(),
      reply_to: form.reply_to?.trim() || null,
      daily_limit: form.daily_limit === "" ? null : Number(form.daily_limit),
      is_active: !!form.is_active,
      is_default: !!form.is_default,
      track_opens: !!form.track_opens,
      track_clicks: !!form.track_clicks,
    };
    if (form.api_key.trim()) payload.api_key = form.api_key.trim();

    setSaving(true);
    try {
      if (account) await emailApi.updateAccount(account.id, payload);
      else await emailApi.createAccount(payload);
      toast.success(account ? "Sender updated" : "Sender added — test it to be sure");
      saved();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not save this sender");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title={account ? `Edit ${account.name}` : "Add a Brevo sender"} close={close} wide>
      <form onSubmit={submit} className="space-y-4">
        <div className="rounded-lg bg-gray-50 dark:bg-gray-800 p-3 text-sm text-gray-600 dark:text-gray-300">
          In Brevo: <strong>SMTP &amp; API → API keys → Create a new API key</strong>. Paste it below
          with the From name and From address (both must be verified in Brevo).
        </div>

        <Field label="Sender name (how you recognise it here)">
          <input
            className="input"
            value={form.name}
            onChange={(e) => set("name", e.target.value)}
            placeholder="Main Brevo"
            autoFocus
          />
        </Field>

        <div className="grid sm:grid-cols-2 gap-3">
          <Field label="From name">
            <input
              className="input"
              value={form.from_name}
              onChange={(e) => set("from_name", e.target.value)}
              placeholder="Your Company"
            />
          </Field>
          <Field label="From email">
            <input
              className="input"
              type="email"
              value={form.from_email}
              onChange={(e) => set("from_email", e.target.value)}
              placeholder="hello@yourdomain.com"
            />
          </Field>
        </div>

        <Field label={account ? "Brevo API key (leave blank to keep the saved key)" : "Brevo API key"}>
          <input
            className="input font-mono"
            value={form.api_key}
            onChange={(e) => set("api_key", e.target.value)}
            placeholder="xkeysib-…"
            autoComplete="off"
          />
        </Field>
        <button
          type="button"
          className="text-sm text-primary-600 -mt-2"
          onClick={loadSenders}
          disabled={loadingSenders}
        >
          {loadingSenders ? "Reading Brevo…" : "Check Brevo's verified senders & domains"}
        </button>

        {probeNote && (
          <p className="text-xs text-warning-600 -mt-2">{probeNote}</p>
        )}

        {senders.length > 0 && (
          <div className="rounded-lg border border-gray-200 dark:border-gray-700 p-3 space-y-2">
            <p className="text-xs font-medium text-gray-600 dark:text-gray-300">
              Addresses Brevo has already verified — pick one and mail leaves from a warm sender:
            </p>
            {senders.map((s: any) => {
              const chosen = form.from_email.trim().toLowerCase() === (s.email || "").toLowerCase();
              return (
                <button
                  type="button"
                  key={s.email}
                  onClick={() => {
                    set("from_email", s.email);
                    if (s.name && !form.from_name.trim()) set("from_name", s.name);
                  }}
                  className={`w-full flex items-center justify-between gap-2 text-left text-sm px-2 py-1.5 rounded ${
                    chosen ? "bg-primary-50 dark:bg-primary-900/20" : "hover:bg-gray-50 dark:hover:bg-gray-800"
                  }`}
                >
                  <span className="truncate">
                    <span className="font-medium">{s.email}</span>
                    {s.name ? <span className="text-gray-400"> · {s.name}</span> : null}
                  </span>
                  <span className="flex items-center gap-1 shrink-0">
                    {s.recommended ? (
                      <span className="badge-green">warm</span>
                    ) : s.domain_verified ? (
                      <span className="badge-yellow">verified</span>
                    ) : (
                      <span className="badge-yellow">domain not authenticated</span>
                    )}
                  </span>
                </button>
              );
            })}
            {form.from_email.includes("@") &&
              !senders.some(
                (s: any) =>
                  (s.email || "").toLowerCase() === form.from_email.trim().toLowerCase()
              ) && (
                <p className="text-xs text-warning-600">
                  {form.from_email} is not in this key's verified list — Brevo will reject it or
                  it will land in spam. Verify it in Brevo → Senders &amp; IP first.
                </p>
              )}
            {domains.length > 0 && (
              <div className="text-xs text-gray-500 pt-1 border-t border-gray-100 dark:border-gray-800">
                Domain authentication:{" "}
                {domains.map((d: any) => (
                  <span key={d.domain} className="mr-2">
                    <span className="font-mono">{d.domain}</span>{" "}
                    {d.dkim ? (
                      <span className="text-success-600">SPF/DKIM ✓</span>
                    ) : (
                      <span className="text-warning-600">not authenticated</span>
                    )}
                  </span>
                ))}
              </div>
            )}
          </div>
        )}

        <div className="grid sm:grid-cols-2 gap-3">
          <Field label="Reply-to addresses (optional)">
            <input
              className="input"
              value={form.reply_to}
              onChange={(e) => set("reply_to", e.target.value)}
              placeholder="replies@yourdomain.com, team@yourdomain.com"
            />
            <p className="mt-1 text-xs text-gray-500">Use one address or separate up to 10 addresses with commas. Replies route to the connected mailbox for each address.</p>
          </Field>
          <Field label="Daily limit (optional)">
            <input
              className="input"
              type="number"
              min={0}
              value={form.daily_limit}
              onChange={(e) => set("daily_limit", e.target.value)}
              placeholder="Leave blank for no app-side cap"
            />
          </Field>
        </div>

        <div className="flex flex-wrap gap-4 text-sm">
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={!!form.is_active} onChange={(e) => set("is_active", e.target.checked)} />
            Active
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={!!form.is_default} onChange={(e) => set("is_default", e.target.checked)} />
            Default sender
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={!!form.track_opens} onChange={(e) => set("track_opens", e.target.checked)} />
            Track opens
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={!!form.track_clicks} onChange={(e) => set("track_clicks", e.target.checked)} />
            Track clicks
          </label>
        </div>

        <div className="flex justify-end gap-2">
          <button type="button" className="btn-secondary" onClick={close}>
            Cancel
          </button>
          <button className="btn-primary" type="submit" disabled={saving}>
            {saving ? "Saving…" : account ? "Save changes" : "Add sender"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

/* ========================================================================== */
/* Send                                                                       */
/* ========================================================================== */

function SendTab({ reference, accounts }: any) {
  return <EmailSendPanel reference={reference} accounts={accounts} />;
}

/* ========================================================================== */
/* Templates                                                                  */
/* ========================================================================== */

function TemplatesTab({ reference }: any) {
  const [templates, setTemplates] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<any | null>(null);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get("/templates", { params: { channel: "email", per_page: 200 } });
      setTemplates(data.items || []);
    } catch {
      toast.error("Could not load email templates");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const remove = async (template: any) => {
    if (!window.confirm(`Delete the template "${template.name}"?`)) return;
    try {
      await api.delete(`/templates/${template.id}`);
      toast.success("Template deleted");
      load();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not delete this template");
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-gray-500">
          Email templates carry a subject, a plain-text body and optional HTML. They are the same rows
          the SMS templates live in, filtered to this channel.
        </p>
        <button className="btn-primary" onClick={() => setEditing({})}>
          <Plus size={16} className="mr-1" /> New template
        </button>
      </div>

      {loading ? (
        <div className="py-10 text-center text-gray-500">Loading…</div>
      ) : templates.length === 0 ? (
        <Empty title="No email templates yet" body="Save your subject + body once and reuse it everywhere." />
      ) : (
        <div className="grid md:grid-cols-2 gap-3">
          {templates.map((t) => (
            <div key={t.id} className="card">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <h3 className="font-semibold truncate">{t.name}</h3>
                  <p className="text-sm text-gray-500 truncate">{t.subject || "(no subject)"}</p>
                  {t.category && <span className="badge-gray mt-1 inline-block">{t.category}</span>}
                </div>
                <div className="flex gap-1 shrink-0">
                  <button className="text-sm text-primary-600 px-2" onClick={() => setEditing(t)}>
                    Edit
                  </button>
                  <button className="text-gray-400 hover:text-danger-500 px-2" onClick={() => remove(t)}>
                    <Trash2 size={15} />
                  </button>
                </div>
              </div>
              <p className="text-xs text-gray-400 mt-2 line-clamp-3 whitespace-pre-wrap">{t.body}</p>
            </div>
          ))}
        </div>
      )}

      {editing && (
        <TemplateModal
          template={editing.id ? editing : null}
          close={() => setEditing(null)}
          saved={() => {
            setEditing(null);
            load();
          }}
        />
      )}
    </div>
  );
}

function TemplateModal({ template, close, saved }: any) {
  const [templateAttachments, setTemplateAttachments] = useState<any[]>([]);
  const [form, setForm] = useState<any>({
    name: template?.name || "",
    category: template?.category || "",
    subject: template?.subject || "",
    body: template?.body || "",
    html_body: template?.html_body || "",
    preheader: template?.preheader || "",
  });
  const [saving, setSaving] = useState(false);
  const set = (k: string, v: any) => setForm((f: any) => ({ ...f, [k]: v }));

  // Load the stored attachments (metadata only) so "no new upload" keeps them.
  useEffect(() => {
    if (!template) return;
    api
      .get(`/templates/${template.id}`)
      .then(({ data }) => {
        if (Array.isArray(data.attachments) && data.attachments.length) {
          setTemplateAttachments(
            data.attachments.map((a: any) => ({
              name: a.name,
              content_type: a.content_type,
              size: a.size,
              // Re-opened templates keep their files: mark them "already stored"
              // so the payload only sends genuinely new uploads.
              content_base64: "",
              stored: true,
            }))
          );
        }
      })
      .catch(() => {});
  }, [template?.id]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.name.trim()) return toast.error("Give the template a name");
    if (!form.subject.trim()) return toast.error("Email templates need a subject");
    if (!form.body.trim() && !form.html_body.trim()) return toast.error("Write the email body");

    const newUploads = templateAttachments.filter((a) => !a.stored && a.content_base64);
    const payload: any = {
      name: form.name.trim(),
      category: form.category || null,
      channel: "email",
      subject: form.subject,
      body: form.body,
      html_body: form.html_body || null,
      preheader: form.preheader || null,
      is_active: true,
    };
    // Only touch attachments when the user actually changed them, so editing a
    // template never silently drops the files it already carries.
    if (newUploads.length) payload.attachments = newUploads;
    setSaving(true);
    try {
      if (template) await api.put(`/templates/${template.id}`, payload);
      else await api.post("/templates", payload);
      toast.success(template ? "Template updated" : "Template created");
      saved();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not save this template");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title={template ? `Edit ${template.name}` : "New email template"} close={close} wide>
      <form onSubmit={submit} className="space-y-4">
        <div className="grid sm:grid-cols-2 gap-3">
          <Field label="Name">
            <input className="input" value={form.name} onChange={(e) => set("name", e.target.value)} autoFocus />
          </Field>
          <Field label="Category (optional)">
            <input className="input" value={form.category} onChange={(e) => set("category", e.target.value)} />
          </Field>
        </div>
        <Field label="Subject">
          <input className="input" value={form.subject} onChange={(e) => set("subject", e.target.value)} />
        </Field>
        <Field label="Preheader (optional)">
          <input
            className="input"
            value={form.preheader}
            onChange={(e) => set("preheader", e.target.value)}
            placeholder="The line inboxes show under the subject"
          />
        </Field>
        <Field label="Body">
          <RichEmailEditor
            body={form.body}
            onBody={(value) => set("body", value)}
            html={form.html_body}
            onHtml={(value) => set("html_body", value)}
            attachments={templateAttachments}
            onAttachments={setTemplateAttachments}
          />
        </Field>
        <div className="flex justify-end gap-2">
          <button type="button" className="btn-secondary" onClick={close}>
            Cancel
          </button>
          <button className="btn-primary" type="submit" disabled={saving}>
            {saving ? "Saving…" : "Save template"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

/* ========================================================================== */
/* Deliverability                                                             */
/* ========================================================================== */

function DeliverabilityTab({ accounts }: { accounts: EmailAccount[] }) {
  const [accountId, setAccountId] = useState<string>("");
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!accountId && accounts.length) {
      const preferred = accounts.find((a) => a.is_default) || accounts[0];
      setAccountId(String(preferred.id));
    }
  }, [accounts, accountId]);

  useEffect(() => {
    if (!accountId) return;
    setLoading(true);
    emailApi
      .deliverability(Number(accountId), 30)
      .then(setData)
      .catch((err: any) => toast.error(err.response?.data?.detail || "Could not read deliverability"))
      .finally(() => setLoading(false));
  }, [accountId]);

  if (!accounts.length) {
    return (
      <Empty
        title="Add a Brevo sender first"
        body="Deliverability checks need an API key so the app can read your domain authentication from Brevo."
      />
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <select className="input max-w-xs" value={accountId} onChange={(e) => setAccountId(e.target.value)}>
          {accounts.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name} — {a.from_email}
            </option>
          ))}
        </select>
        {data && (
          <span
            className={`badge ${
              data.score >= 80 ? "badge-green" : data.score >= 50 ? "badge-yellow" : "badge-red"
            }`}
          >
            Deliverability score {data.score}/100
          </span>
        )}
      </div>

      {loading ? (
        <div className="card p-6 text-center text-gray-500">Checking with Brevo…</div>
      ) : data ? (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
            <Stat label="Sent (30d)" value={data.metrics.sent} />
            <Stat label="Bounce rate" value={`${data.metrics.bounce_rate}%`} />
            <Stat label="Open rate" value={`${data.metrics.open_rate}%`} />
            <Stat label="Click rate" value={`${data.metrics.click_rate}%`} />
            <Stat label="Unsubscribed" value={data.metrics.unsubscribed} />
          </div>

          <div className="card space-y-3">
            <h3 className="font-semibold">Checks</h3>
            {data.checks.map((check: any) => (
              <div key={check.key} className="flex items-start gap-3 text-sm">
                {check.ok ? (
                  <CheckCircle2 size={17} className="text-success-500 mt-0.5 shrink-0" />
                ) : (
                  <XCircle size={17} className="text-warning-500 mt-0.5 shrink-0" />
                )}
                <div>
                  <p className="font-medium">{check.label}</p>
                  <p className="text-gray-500 text-xs">{check.detail}</p>
                </div>
              </div>
            ))}
          </div>

          <div className="card space-y-2">
            <h3 className="font-semibold">Authenticated domains in Brevo</h3>
            {data.domains_error && (
              <p className="text-sm text-warning-600">{data.domains_error}</p>
            )}
            {data.domains.length === 0 ? (
              <p className="text-sm text-gray-500">
                Brevo reports no authenticated sending domain for this key. Authenticate your
                domain in Brevo (<strong>Senders &amp; IP → Domains</strong>) — SPF/DKIM are what
                keep you out of spam.
              </p>
            ) : (
              <div className="divide-y divide-gray-100 dark:divide-gray-800">
                {data.domains.map((d: any, i: number) => (
                  <div key={i} className="py-2 flex items-center justify-between text-sm">
                    <span className="font-medium">{d.domain}</span>
                    {d.authenticated || d.verified ? (
                      <span className="badge-green">authenticated</span>
                    ) : (
                      <span className="badge-yellow">not verified</span>
                    )}
                  </div>
                ))}
              </div>
            )}
            <p className="text-xs text-gray-500">
              The app uses a real, threaded conversation per contact: replies quote the message they
              answer (In-Reply-To/References), bulk mail carries List-Unsubscribe, and every send has
              a text alternative — the three things filters look at first.
            </p>
          </div>
        </>
      ) : null}
    </div>
  );
}

/* ========================================================================== */
/* Suppression                                                                */
/* ========================================================================== */

function SuppressionTab() {
  const [items, setItems] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [address, setAddress] = useState("");
  const [reason, setReason] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await emailApi.suppression({ search: search || undefined, per_page: 100 });
      setItems(data.items);
      setTotal(data.total);
    } catch {
      toast.error("Could not load the suppression list");
    } finally {
      setLoading(false);
    }
  }, [search]);

  useEffect(() => {
    load();
  }, [load]);

  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!address.includes("@")) return toast.error("Enter a valid email address");
    try {
      await emailApi.addSuppression({ email_address: address, reason: reason || "Manual" });
      toast.success("Address suppressed");
      setAddress("");
      setReason("");
      load();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not suppress this address");
    }
  };

  const remove = async (entry: any) => {
    try {
      await emailApi.removeSuppression(entry.id);
      toast.success("Address can be emailed again");
      load();
    } catch {
      toast.error("Could not remove this entry");
    }
  };

  return (
    <div className="space-y-4">
      <form onSubmit={add} className="card flex flex-wrap items-end gap-3">
        <Field label="Email address">
          <input
            className="input"
            value={address}
            onChange={(e) => setAddress(e.target.value)}
            placeholder="do-not-contact@company.com"
          />
        </Field>
        <Field label="Reason (optional)">
          <input className="input" value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
        <button className="btn-primary" type="submit">
          <ShieldOff size={16} className="mr-1" /> Suppress
        </button>
      </form>

      <div className="flex items-center justify-between gap-3">
        <input
          className="input max-w-xs"
          placeholder="Search suppressed addresses…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <span className="text-sm text-gray-500">{total} suppressed</span>
      </div>

      {loading ? (
        <div className="py-10 text-center text-gray-500">Loading…</div>
      ) : items.length === 0 ? (
        <Empty
          title="Nothing suppressed"
          body="Unsubscribes, hard bounces and manual blocks all land here — and none of them are ever emailed again."
        />
      ) : (
        <div className="card divide-y divide-gray-100 dark:divide-gray-800">
          {items.map((entry) => (
            <div key={entry.id} className="py-2 flex items-center justify-between gap-3 text-sm">
              <div className="min-w-0">
                <p className="font-medium truncate flex items-center gap-2">
                  <Mail size={13} className="text-gray-400" /> {entry.email_address}
                  {entry.hard_bounce && <span className="badge-red">hard bounce</span>}
                </p>
                <p className="text-xs text-gray-500 truncate">
                  {entry.reason || "—"} · {entry.source || "manual"} · {fmtDate(entry.created_at)}
                </p>
              </div>
              <button className="text-gray-400 hover:text-danger-500 px-2" onClick={() => remove(entry)}>
                <Trash2 size={15} />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ========================================================================== */
/* Activity                                                                   */
/* ========================================================================== */

function ActivityTab({ accounts }: any) {
  const [tab, setTab] = useState<"events" | "history" | "campaigns">("events");
  const [events, setEvents] = useState<any[]>([]);
  const [history, setHistory] = useState<EmailMessage[]>([]);
  const [campaignLog, setCampaignLog] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [e, h, a] = await Promise.all([
        emailApi.events({ per_page: 100 }),
        emailApi.history({ per_page: 100 }),
        adsApi.activity("email"),
      ]);
      setEvents(e.items);
      setHistory(h.items);
      setCampaignLog(a.items);
    } catch {
      toast.error("Could not load email activity");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const retry = async (message: EmailMessage) => {
    try {
      await emailApi.retry(message.id);
      toast.success("Retry queued");
      load();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not retry this send");
    }
  };

  const accountName = (id?: number | null) => accounts.find((a: EmailAccount) => a.id === id)?.name || "—";

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <div className="flex gap-2">
          <button
            className={tab === "events" ? "btn-primary text-sm" : "btn-secondary text-sm"}
            onClick={() => setTab("events")}
          >
            <Activity size={15} className="mr-1" /> Events
          </button>
          <button
            className={tab === "history" ? "btn-primary text-sm" : "btn-secondary text-sm"}
            onClick={() => setTab("history")}
          >
            <Mail size={15} className="mr-1" /> Messages
          </button>
          <button
            className={tab === "campaigns" ? "btn-primary text-sm" : "btn-secondary text-sm"}
            onClick={() => setTab("campaigns")}
          >
            <Megaphone size={15} className="mr-1" /> Campaign log
          </button>
        </div>
        <button className="btn-secondary text-sm" onClick={load}>
          <RefreshCw size={15} className="mr-1" /> Refresh
        </button>
      </div>

      {loading ? (
        <div className="py-10 text-center text-gray-500">Loading…</div>
      ) : tab === "events" ? (
        events.length === 0 ? (
          <Empty
            title="No Brevo events yet"
            body="Delivered / opened / clicked / bounced events appear here once the webhook URL is configured in Brevo."
          />
        ) : (
          <div className="card divide-y divide-gray-100 dark:divide-gray-800">
            {events.map((e) => (
              <div key={e.id} className="py-2 flex items-start justify-between gap-3 text-sm">
                <div className="min-w-0">
                  <p className="font-medium flex items-center gap-2">
                    <EventIcon type={e.event_type} /> {e.event_type}
                    {e.link && <span className="text-xs text-gray-400 truncate">{e.link}</span>}
                  </p>
                  <p className="text-xs text-gray-500 truncate">
                    {e.email_address} · {e.subject || "—"} · {accountName(e.account_id)}
                  </p>
                </div>
                <span className="text-xs text-gray-400 shrink-0">{fmtDate(e.created_at)}</span>
              </div>
            ))}
          </div>
        )
      ) : tab === "campaigns" ? (
        campaignLog.length === 0 ? (
          <Empty title="No campaign activity yet" body="Launches, pauses, Andromeda runs and edits show up here." />
        ) : (
          <div className="card divide-y divide-gray-100 dark:divide-gray-700">
            {campaignLog.map((i) => (
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
        )
      ) : history.length === 0 ? (
        <Empty title="No email messages yet" body="Sends and replies both show up here." />
      ) : (
        <div className="card divide-y divide-gray-100 dark:divide-gray-800">
          {history.map((m) => (
            <div key={m.id} className="py-2 flex items-start justify-between gap-3 text-sm">
              <div className="min-w-0">
                <p className="font-medium truncate flex items-center gap-2">
                  {m.direction === "incoming" ? (
                    <Inbox size={14} className="text-warning-500" />
                  ) : (
                    <Send size={14} className="text-primary-500" />
                  )}
                  {m.subject || "(no subject)"}
                  <Badge value={m.status} />
                  {m.open_count > 0 && <span className="badge-blue">{m.open_count}x opened</span>}
                  {m.click_count > 0 && <span className="badge-green">{m.click_count}x clicked</span>}
                </p>
                <p className="text-xs text-gray-500 truncate">
                  {m.direction === "incoming" ? m.from_address : m.to_address} ·{" "}
                  {m.email_account_name || accountName(m.email_account_id)}
                  {m.last_error ? ` · ${m.last_error}` : ""}
                  {(m.attachments?.length || 0) > 0
                    ? ` · ${m.attachments!.length} attachment(s)`
                    : ""}
                </p>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <span className="text-xs text-gray-400">{fmtDate(m.created_at)}</span>
                {m.status === "failed" && (
                  <button className="text-primary-600 text-xs" onClick={() => retry(m)}>
                    Retry
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function EventIcon({ type }: { type: string }) {
  if (type === "opened") return <Eye size={14} className="text-primary-500" />;
  if (type === "clicked") return <MousePointerClick size={14} className="text-success-600" />;
  if (type === "delivered" || type === "sent") return <CheckCircle2 size={14} className="text-success-500" />;
  if (type === "bounce" || type === "blocked" || type === "error") return <XCircle size={14} className="text-danger-500" />;
  if (type === "unsubscribed") return <UserRound size={14} className="text-warning-500" />;
  return <Activity size={14} className="text-gray-400" />;
}
