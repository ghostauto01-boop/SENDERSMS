import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import api from "../api/client";
import toast from "react-hot-toast";
import {
  Activity, AlertTriangle, ArrowRight, BarChart3, CheckCircle2, Inbox as InboxIcon,
  MessageCircle, Megaphone, RefreshCw, Send, Sparkles, Star, ThumbsDown, TrendingUp,
  Users, XCircle, Clock,
} from "lucide-react";
import {
  Area, AreaChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import type { OverviewCampaign, UnifiedMetrics } from "../types";
import { campaignColor } from "../components/CampaignChip";

/**
 * APPLICATION OVERVIEW
 *
 * One screen that answers the three questions the app previously spread
 * across three pages:
 *
 *   1. What is running right now?      -> both campaign systems, one list
 *   2. How is it performing?           -> metrics derived from the SAME
 *                                         tables the inbox renders, so the
 *                                         numbers here and in the inbox can
 *                                         never disagree
 *   3. Where did these leads come from? -> every campaign row deep-links into
 *                                         the inbox, filtered to its leads
 *
 * The classic Campaigns page and the SMS Ads Manager are unchanged; this
 * joins them for reading.
 */

const RANGES = [
  { days: 7, label: "7 days" },
  { days: 30, label: "30 days" },
  { days: 90, label: "90 days" },
];

const statusTone = (status: string, isLive: boolean) => {
  if (isLive && (status === "running" || status === "active")) return "badge-green";
  if (status === "paused") return "badge-yellow";
  if (status === "scheduled") return "badge-blue";
  if (status === "completed") return "badge-blue";
  if (status === "failed" || status === "error" || status === "stopped") return "badge-red";
  return "badge-gray";
};

function StatCard({
  label, value, sub, icon: Icon, tone = "text-primary-600 bg-primary-50 dark:bg-primary-900/40", to,
}: {
  label: string; value: React.ReactNode; sub?: string;
  icon: any; tone?: string; to?: string;
}) {
  const body = (
    <div className="card p-4 h-full hover:shadow-md transition-shadow">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-xs text-gray-500 dark:text-gray-400 truncate">{label}</p>
          <p className="text-2xl font-bold mt-1">{value}</p>
          {sub && <p className="text-[11px] text-gray-400 mt-1 truncate">{sub}</p>}
        </div>
        <div className={`p-2 rounded-lg flex-shrink-0 ${tone}`}><Icon size={18} /></div>
      </div>
    </div>
  );
  return to ? <Link to={to} className="block">{body}</Link> : body;
}

export default function OverviewPage() {
  const [metrics, setMetrics] = useState<UnifiedMetrics | null>(null);
  const [campaigns, setCampaigns] = useState<OverviewCampaign[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [days, setDays] = useState(30);
  const [scope, setScope] = useState<"live" | "all">("live");
  const navigate = useNavigate();

  const load = useCallback(async (quiet = false) => {
    try {
      if (!quiet) setLoading(true);
      setRefreshing(true);
      setError(null);
      const [m, c] = await Promise.all([
        api.get("/overview/metrics", { params: { days } }),
        api.get("/overview/campaigns"),
      ]);
      setMetrics(m.data);
      setCampaigns(c.data.items || []);
    } catch (err: any) {
      setError(
        err.response?.data?.detail ||
          "Could not load the overview. If the server was just updated, give it a moment and retry."
      );
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [days]);

  useEffect(() => { load(); }, [load]);

  // Keep the screen live without a hard reload — campaigns send continuously.
  useEffect(() => {
    const t = setInterval(() => load(true), 30000);
    return () => clearInterval(t);
  }, [load]);

  const visible = useMemo(
    () => (scope === "live" ? campaigns.filter((c) => c.is_live) : campaigns),
    [campaigns, scope]
  );

  const totals = metrics?.campaigns;
  const msg = metrics?.messaging;
  const inbox = metrics?.inbox;

  if (loading) {
    return (
      <div className="space-y-6">
        <h1 className="text-xl sm:text-2xl font-bold">Overview</h1>
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          {[...Array(8)].map((_, i) => (
            <div key={i} className="card p-4">
              <div className="skeleton h-3 w-20 mb-2" />
              <div className="skeleton h-8 w-14" />
            </div>
          ))}
        </div>
        <div className="card p-4"><div className="skeleton h-64 w-full" /></div>
      </div>
    );
  }

  if (error && !metrics) {
    return (
      <div className="text-center py-12">
        <AlertTriangle size={44} className="mx-auto text-red-500 mb-3" />
        <h2 className="text-lg font-semibold mb-1">Couldn't load the overview</h2>
        <p className="text-gray-500 mb-4 max-w-md mx-auto text-sm">{error}</p>
        <button onClick={() => load()} className="btn-primary">Retry</button>
      </div>
    );
  }

  const sentiment = inbox?.sentiment || { positive: 0, negative: 0, neutral: 0 };
  const sentimentTotal = sentiment.positive + sentiment.negative + sentiment.neutral;

  return (
    <div className="space-y-6">
      {/* ---- Header ---- */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm font-medium text-primary-600 mb-1 flex items-center gap-1">
            <Activity size={15} /> EVERYTHING, IN SYNC
          </p>
          <h1 className="text-2xl sm:text-3xl font-bold">Overview</h1>
          <p className="text-gray-500 mt-1 text-sm max-w-2xl">
            Campaigns, delivery and inbox activity in one place. Every number here is
            computed from the same messages your inbox shows, so they always agree.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            className="input py-1.5 !w-auto text-sm"
            value={days}
            onChange={(e) => setDays(parseInt(e.target.value))}
          >
            {RANGES.map((r) => <option key={r.days} value={r.days}>Last {r.label}</option>)}
          </select>
          <button
            onClick={() => load()}
            disabled={refreshing}
            className="btn-secondary btn-sm"
            title="Refresh now"
          >
            <RefreshCw size={14} className={refreshing ? "animate-spin" : ""} />
          </button>
        </div>
      </div>

      {/* ---- Headline metrics ---- */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 sm:gap-4">
        <StatCard
          label="Live campaigns" value={totals?.live ?? 0}
          sub={`${totals?.campaigns ?? 0} total`}
          icon={Megaphone} tone="text-green-600 bg-green-50 dark:bg-green-900/40"
        />
        <StatCard
          label="Messages sent" value={msg?.sent ?? 0}
          sub={`${msg?.queued ?? 0} still queued`}
          icon={Send} tone="text-blue-600 bg-blue-50 dark:bg-blue-900/40"
        />
        <StatCard
          label="Delivered" value={`${msg?.delivery_rate ?? 0}%`}
          sub={`${msg?.delivered ?? 0} confirmed`}
          icon={CheckCircle2} tone="text-emerald-600 bg-emerald-50 dark:bg-emerald-900/40"
        />
        <StatCard
          label="Replies" value={msg?.replies ?? 0}
          sub={`${msg?.reply_rate ?? 0}% reply rate`}
          icon={MessageCircle} tone="text-purple-600 bg-purple-50 dark:bg-purple-900/40"
          to="/inbox"
        />
        <StatCard
          label="Unread in inbox" value={inbox?.unread ?? 0}
          sub="waiting for you"
          icon={InboxIcon} tone="text-amber-600 bg-amber-50 dark:bg-amber-900/40"
          to="/inbox"
        />
        <StatCard
          label="Interested leads" value={inbox?.interested ?? 0}
          sub="marked in the inbox"
          icon={Star} tone="text-yellow-600 bg-yellow-50 dark:bg-yellow-900/40"
          to="/inbox?status=interested"
        />
        <StatCard
          label="Failed" value={msg?.failed ?? 0}
          sub={`${msg?.failure_rate ?? 0}% of attempts`}
          icon={XCircle} tone="text-red-600 bg-red-50 dark:bg-red-900/40"
        />
        <StatCard
          label="Contacts" value={metrics?.audience.contacts ?? 0}
          sub={`${metrics?.audience.opted_out ?? 0} opted out`}
          icon={Users} tone="text-indigo-600 bg-indigo-50 dark:bg-indigo-900/40"
          to="/contacts"
        />
      </div>

      {/* ---- Activity chart + reply sentiment ---- */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="card p-4 lg:col-span-2">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold">Sending &amp; replies</h3>
            <span className="text-xs text-gray-400">last {days} days</span>
          </div>
          <ResponsiveContainer width="100%" height={260}>
            <AreaChart data={metrics?.series || []}>
              <defs>
                <linearGradient id="ovSent" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.35} />
                  <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
                </linearGradient>
                <linearGradient id="ovReplies" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#8b5cf6" stopOpacity={0.4} />
                  <stop offset="95%" stopColor="#8b5cf6" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
              <XAxis dataKey="date" tick={{ fontSize: 11 }} tickFormatter={(d) => String(d).slice(5)} />
              <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
              <Tooltip
                contentStyle={{ fontSize: 12, borderRadius: 8 }}
                labelFormatter={(d) => new Date(String(d)).toDateString()}
              />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Area type="monotone" dataKey="sent" name="Sent" stroke="#3b82f6" fill="url(#ovSent)" strokeWidth={2} />
              <Area type="monotone" dataKey="replies" name="Replies" stroke="#8b5cf6" fill="url(#ovReplies)" strokeWidth={2} />
            </AreaChart>
          </ResponsiveContainer>
        </div>

        <div className="card p-4">
          <h3 className="font-semibold mb-1">How people are replying</h3>
          <p className="text-xs text-gray-400 mb-4">
            Classified automatically as each reply arrives.
          </p>
          {sentimentTotal === 0 ? (
            <p className="text-sm text-gray-500 py-8 text-center">No replies in this period yet.</p>
          ) : (
            <div className="space-y-3">
              {([
                { key: "positive", label: "Positive", value: sentiment.positive, colour: "bg-green-500", icon: TrendingUp },
                { key: "neutral", label: "Neutral", value: sentiment.neutral, colour: "bg-gray-400", icon: MessageCircle },
                { key: "negative", label: "Negative", value: sentiment.negative, colour: "bg-red-500", icon: ThumbsDown },
              ] as const).map((row) => {
                const pct = Math.round((row.value / sentimentTotal) * 100);
                return (
                  <div key={row.key}>
                    <div className="flex items-center justify-between text-sm mb-1">
                      <span className="flex items-center gap-1.5 text-gray-600 dark:text-gray-300">
                        <row.icon size={13} /> {row.label}
                      </span>
                      <span className="font-semibold">{row.value} <span className="text-xs text-gray-400">({pct}%)</span></span>
                    </div>
                    <div className="h-2 rounded-full bg-gray-100 dark:bg-gray-700 overflow-hidden">
                      <div className={`h-full ${row.colour}`} style={{ width: `${pct}%` }} />
                    </div>
                  </div>
                );
              })}
              <Link to="/inbox" className="btn-secondary btn-sm w-full mt-4 justify-center">
                <InboxIcon size={14} className="mr-1" /> Open inbox
              </Link>
            </div>
          )}
        </div>
      </div>

      {/* ---- Campaigns ---- */}
      <div>
        <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <Megaphone size={18} className="text-primary-600" /> Campaigns
          </h2>
          <div className="flex items-center gap-2">
            <div className="flex gap-1 p-1 rounded-lg bg-gray-100 dark:bg-gray-700">
              {(["live", "all"] as const).map((s) => (
                <button
                  key={s}
                  onClick={() => setScope(s)}
                  className={`px-3 py-1 text-xs rounded-md font-medium transition-colors ${
                    scope === s
                      ? "bg-white dark:bg-gray-800 shadow-sm text-primary-600"
                      : "text-gray-600 dark:text-gray-300"
                  }`}
                >
                  {s === "live" ? `Running (${campaigns.filter((c) => c.is_live).length})` : `All (${campaigns.length})`}
                </button>
              ))}
            </div>
            <Link to="/campaigns" className="btn-secondary btn-sm">Campaigns</Link>
            <Link to="/sms-manager" className="btn-secondary btn-sm hidden sm:inline-flex">
              <Sparkles size={13} className="mr-1" /> Ads Manager
            </Link>
          </div>
        </div>

        {visible.length === 0 ? (
          <div className="card p-10 text-center">
            <Megaphone size={40} className="mx-auto mb-3 text-gray-300" />
            <h3 className="font-semibold">
              {scope === "live" ? "Nothing is sending right now" : "No campaigns yet"}
            </h3>
            <p className="text-sm text-gray-500 mt-1 mb-4">
              {scope === "live"
                ? "Switch to \"All\" to see drafts and finished campaigns."
                : "Create your first campaign to start reaching contacts."}
            </p>
            <Link to="/campaigns" className="btn-primary btn-sm">Go to Campaigns</Link>
          </div>
        ) : (
          <div className="space-y-3">
            {visible.map((c) => (
              <CampaignCard key={`${c.kind}-${c.id}`} campaign={c} navigate={navigate} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function CampaignCard({
  campaign: c,
  navigate,
}: {
  campaign: OverviewCampaign;
  navigate: ReturnType<typeof useNavigate>;
}) {
  const progress = c.audience > 0 ? Math.min(Math.round((c.sent / c.audience) * 100), 100) : 0;
  // Same colour this campaign gets on its inbox chips, so the two screens
  // read as one system. The name is the heading here, so the badge only
  // needs to say which sender it came from.
  const colour = campaignColor({ id: c.id, kind: c.kind, name: c.name });
  const KindIcon = c.kind === "ads" ? Sparkles : Megaphone;

  return (
    <div className="card p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span
              className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${colour.bg} ${colour.text}`}
              title={c.kind === "ads" ? "SMS Ads Manager campaign" : "Campaign"}
            >
              <KindIcon size={11} /> {c.kind === "ads" ? "Ads" : "Campaign"}
            </span>
            <span className={statusTone(c.status, c.is_live)}>{c.status}</span>
            {c.unread > 0 && (
              <span className="badge-yellow inline-flex items-center gap-1">
                {c.unread} unread
              </span>
            )}
          </div>
          <h3 className="font-semibold text-base mt-1.5 break-words">{c.name}</h3>
          {c.description && (
            <p className="text-sm text-gray-500 mt-0.5 line-clamp-2">{c.description}</p>
          )}
          {c.status === "scheduled" && c.scheduled_start_at && (
            <p className="text-xs text-blue-600 dark:text-blue-400 mt-1 flex items-center gap-1">
              <Clock size={12} /> Sends {new Date(c.scheduled_start_at).toLocaleString()}
            </p>
          )}
        </div>

        {/* Primary actions: this is the "click through to the replies" path. */}
        <div className="flex gap-2 flex-wrap">
          <button
            onClick={() => navigate(c.replies_url)}
            disabled={c.replied === 0}
            className="btn-primary btn-sm disabled:opacity-40 disabled:cursor-not-allowed"
            title={c.replied === 0 ? "No replies to this campaign yet" : "Open the replies in your inbox"}
          >
            <MessageCircle size={13} className="mr-1" />
            {c.replied} {c.replied === 1 ? "reply" : "replies"}
          </button>
          <button
            onClick={() => navigate(c.inbox_url)}
            disabled={c.leads === 0}
            className="btn-secondary btn-sm disabled:opacity-40 disabled:cursor-not-allowed"
            title="See every lead this campaign produced, in the inbox"
          >
            <InboxIcon size={13} className="mr-1" /> {c.leads} leads
          </button>
        </div>
      </div>

      {/* Delivery progress */}
      {c.audience > 0 && (
        <div className="mt-3">
          <div className="flex justify-between text-[11px] text-gray-500 mb-1">
            <span>{c.sent} of {c.audience} sent</span>
            <span>{progress}%</span>
          </div>
          <div className="h-1.5 rounded-full bg-gray-100 dark:bg-gray-700 overflow-hidden">
            <div
              className={`h-full ${c.is_live ? "bg-green-500" : "bg-primary-600"}`}
              style={{ width: `${progress}%` }}
            />
          </div>
        </div>
      )}

      <div className="grid grid-cols-3 sm:grid-cols-6 gap-3 mt-3 pt-3 border-t border-gray-100 dark:border-gray-700 text-sm">
        <Metric label="Sent" value={c.sent} />
        <Metric label="Delivered" value={`${c.delivery_rate}%`} />
        <Metric label="Failed" value={c.failed} tone={c.failed > 0 ? "text-red-600" : undefined} />
        <Metric label="Replies" value={c.replied} />
        <Metric label="Reply rate" value={`${c.reply_rate}%`} />
        <Metric label="Interested" value={c.interested} tone={c.interested > 0 ? "text-green-600" : undefined} />
      </div>
    </div>
  );
}

function Metric({ label, value, tone }: { label: string; value: React.ReactNode; tone?: string }) {
  return (
    <div>
      <div className="text-[11px] text-gray-500">{label}</div>
      <div className={`font-semibold mt-0.5 ${tone || ""}`}>{value}</div>
    </div>
  );
}
