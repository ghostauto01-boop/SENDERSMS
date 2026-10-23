import { useVisiblePolling } from "../hooks/useVisiblePolling";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import api from "../api/client";
import {
  Activity, AlertTriangle, CheckCircle2, Inbox as InboxIcon,
  MessageCircle, Megaphone, RefreshCw, Send, Sparkles, Star, ThumbsDown, TrendingUp,
  Users, XCircle, Clock,
} from "lucide-react";
import {
  Area, AreaChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import type { OverviewCampaign, UnifiedMetrics } from "../types";
import { campaignColor } from "../components/CampaignChip";
import ChannelSwitch from "../components/ChannelSwitch";
import { ThemeToggle } from "../components/ThemeToggle";
import { useChannel } from "../hooks/useChannel";
import { inboxLink, inboxPathFor } from "../utils/inboxRoute";

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
 * The selected SMS or Email channel scopes every headline, chart and campaign
 * row; the app-wide channel switch lets the operator compare the two views.
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
  delta,
}: {
  label: string; value: React.ReactNode; sub?: string;
  icon: any; tone?: string; to?: string;
  /** Percentage change vs. the first half of the window — an indicator, not a promise. */
  delta?: number | null;
}) {
  const body = (
    <div className="card p-4 h-full hover:shadow-md transition-shadow">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-xs text-gray-500 dark:text-gray-400 truncate">{label}</p>
          <div className="flex items-baseline gap-1.5 mt-1">
            <p className="text-2xl font-bold tabular-nums tracking-tight">{value}</p>
            {typeof delta === "number" && delta !== 0 && (
              <span
                className={`inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[10px] font-semibold ${
                  delta > 0
                    ? "bg-success-50 text-success-700 dark:bg-success-950/50 dark:text-success-300"
                    : "bg-danger-50 text-danger-700 dark:bg-danger-950/50 dark:text-danger-300"
                }`}
              >
                {delta > 0 ? <TrendingUp size={9} /> : <ThumbsDown size={9} />}
                {Math.abs(delta)}%
              </span>
            )}
          </div>
          {sub && <p className="text-[11px] text-gray-400 mt-1 truncate">{sub}</p>}
        </div>
        <div className={`p-2 rounded-lg flex-shrink-0 ${tone}`}><Icon size={18} /></div>
      </div>
    </div>
  );
  return to ? <Link to={to} className="block">{body}</Link> : body;
}

export default function OverviewPage() {
  const { channel, isEmail } = useChannel();
  const channelLabel = isEmail ? "Email" : "SMS";
  const messageLabel = isEmail ? "Emails" : "SMS";
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
        api.get("/overview/metrics", { params: { days, channel } }),
        api.get("/overview/campaigns", { params: { channel } }),
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
  }, [days, channel]);

  useEffect(() => { load(); }, [load]);

  // Keep the screen live without a hard reload — campaigns send continuously.
  useVisiblePolling(() => load(true), 30000);

  const visible = useMemo(
    () => (scope === "live" ? campaigns.filter((c) => c.is_live) : campaigns),
    [campaigns, scope]
  );

  const totals = metrics?.campaigns;
  const msg = metrics?.messaging;
  const inbox = metrics?.inbox;

  if (loading || (metrics !== null && metrics.channel !== channel)) {
    return (
      <div className="space-y-6">
        <h1 className="page-title">{channelLabel} Overview</h1>
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
        <AlertTriangle size={44} className="mx-auto text-danger-500 mb-3" />
        <h2 className="text-lg font-semibold mb-1">Couldn't load the overview</h2>
        <p className="text-gray-500 mb-4 max-w-md mx-auto text-sm">{error}</p>
        <button onClick={() => load()} className="btn-primary">Retry</button>
      </div>
    );
  }

  // Trend indicators come from the same series the chart draws.
  const trend = (pick: (point: any) => number) => {
    const points = (metrics?.series || []).map(pick);
    if (points.length < 2) return null;
    const mid = Math.floor(points.length / 2);
    const before = points.slice(0, mid).reduce((a, b) => a + b, 0);
    const after = points.slice(mid).reduce((a, b) => a + b, 0);
    if (before === 0) return after > 0 ? 100 : null;
    return Math.round(((after - before) / before) * 100);
  };
  const sentDelta = trend((p) => Number(p.sent ?? p.count ?? 0));
  const repliesDelta = trend((p) => Number(p.replies ?? 0));

  const sentiment = inbox?.sentiment || { positive: 0, negative: 0, neutral: 0 };
  const sentimentTotal = sentiment.positive + sentiment.negative + sentiment.neutral;

  return (
    <div className="space-y-6">
      {/* ---- Header ---- */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm font-medium text-primary-600 mb-1 flex items-center gap-1">
            <Activity size={15} /> {channelLabel.toUpperCase()} OVERVIEW
          </p>
          <h1 className="page-title">{channelLabel} Overview</h1>
          <p className="text-gray-500 mt-1 text-sm max-w-2xl">
            {isEmail
              ? "Email campaigns, delivery and inbox activity only — SMS activity has its own overview."
              : "SMS campaigns, delivery and inbox activity only — email activity has its own overview."}
            {" "}Numbers use the same messages your inbox shows.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {/* Appearance lives at the top of the overview: the one screen an
              operator opens first, so light/dark is always one tap away. */}
          <ThemeToggle />
          <ChannelSwitch size="sm" />
          <select
            className="input py-1.5 !w-auto text-sm"
            aria-label="Date range"
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
          icon={Megaphone} tone="text-success-600 bg-success-50 dark:bg-success-900/40"
        />
        <StatCard
          label={`${messageLabel} sent`} value={msg?.sent ?? 0}
          sub={`${msg?.queued ?? 0} still queued`}
          icon={Send} tone="text-primary-600 bg-primary-50 dark:bg-primary-900/40"
          delta={sentDelta}
        />
        <StatCard
          label={`${messageLabel} delivered`} value={`${msg?.delivery_rate ?? 0}%`}
          sub={`${msg?.delivered ?? 0} confirmed`}
          icon={CheckCircle2} tone="text-success-600 bg-success-50 dark:bg-success-900/40"
        />
        <StatCard
          label={`${channelLabel} replies`} value={msg?.replies ?? 0}
          sub={`${msg?.reply_rate ?? 0}% reply rate · ${channelLabel.toLowerCase()} only`}
          icon={MessageCircle} tone="text-primary-600 bg-primary-50 dark:bg-primary-900/40"
          to={inboxPathFor(channel)}
          delta={repliesDelta}
        />
        <StatCard
          label="Unread in inbox" value={inbox?.unread ?? 0}
          sub="waiting for you"
          icon={InboxIcon} tone="text-warning-600 bg-warning-50 dark:bg-warning-900/40"
          to={inboxPathFor(channel)}
        />
        <StatCard
          label={`Interested ${channelLabel.toLowerCase()} leads`} value={inbox?.interested ?? 0}
          sub="marked in the inbox"
          icon={Star} tone="text-warning-600 bg-warning-50 dark:bg-warning-900/40"
          to={inboxLink(channel, { status: "interested" })}
        />
        <StatCard
          label={`${messageLabel} failed`} value={msg?.failed ?? 0}
          sub={`${msg?.failure_rate ?? 0}% of attempts`}
          icon={XCircle} tone="text-danger-600 bg-danger-50 dark:bg-danger-900/40"
        />
        <StatCard
          label={`${channelLabel} contacts`} value={metrics?.audience.contacts ?? 0}
          sub={`${metrics?.audience.opted_out ?? 0} ${isEmail ? "email" : "SMS"} opted out`}
          icon={Users} tone="text-primary-600 bg-primary-50 dark:bg-primary-900/40"
          to="/contacts"
        />
      </div>

      {/* ---- Activity chart + reply sentiment ---- */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="card p-4 lg:col-span-2">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold">{channelLabel} sending &amp; replies</h3>
            <span className="text-xs text-gray-400">last {days} days</span>
          </div>
          <div className="h-[220px] sm:h-[260px] -ml-1">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={metrics?.series || []} margin={{ top: 6, right: 6, left: -18, bottom: 0 }}>
                <defs>
                  <linearGradient id="ovSent" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#2a684e" stopOpacity={0.5} />
                    <stop offset="95%" stopColor="#5fa982" stopOpacity={0.02} />
                  </linearGradient>
                  <linearGradient id="ovReplies" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#dbb440" stopOpacity={0.45} />
                    <stop offset="95%" stopColor="#dbb440" stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="4 6" opacity={0.35} vertical={false} />
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 10.5, fill: "#828b83" }}
                  axisLine={false}
                  tickLine={false}
                  tickFormatter={(d) => String(d).slice(5)}
                  minTickGap={18}
                />
                <YAxis
                  tick={{ fontSize: 10.5, fill: "#828b83" }}
                  axisLine={false}
                  tickLine={false}
                  allowDecimals={false}
                  width={44}
                />
                <Tooltip
                  contentStyle={{
                    fontSize: 12,
                    borderRadius: 12,
                    border: "1px solid rgba(214,216,211,0.7)",
                    boxShadow: "0 12px 30px -18px rgba(8,37,26,0.35)",
                  }}
                  labelFormatter={(d) => new Date(String(d)).toDateString()}
                />
                <Legend
                  iconType="circle"
                  iconSize={7}
                  wrapperStyle={{ fontSize: 11, paddingTop: 6 }}
                />
                <Area
                  type="monotone"
                  dataKey="sent"
                  name={`${messageLabel} sent`}
                  stroke="#2a684e"
                  fill="url(#ovSent)"
                  strokeWidth={2.2}
                  activeDot={{ r: 3.5, strokeWidth: 2, stroke: "#fff" }}
                />
                <Area
                  type="monotone"
                  dataKey="replies"
                  name={`${channelLabel} replies`}
                  stroke="#c99a2a"
                  fill="url(#ovReplies)"
                  strokeWidth={2.2}
                  activeDot={{ r: 3.5, strokeWidth: 2, stroke: "#fff" }}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
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
                { key: "positive", label: "Positive", value: sentiment.positive, colour: "bg-success-500", icon: TrendingUp },
                { key: "neutral", label: "Neutral", value: sentiment.neutral, colour: "bg-gray-400", icon: MessageCircle },
                { key: "negative", label: "Negative", value: sentiment.negative, colour: "bg-danger-500", icon: ThumbsDown },
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
              <Link to={inboxPathFor(channel)} className="btn-secondary btn-sm w-full mt-4 justify-center">
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
            <Megaphone size={18} className="text-primary-600" /> {channelLabel} campaigns
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
            <Link to="/sms-manager" className="btn-secondary btn-sm">Campaigns</Link>
            <Link to={isEmail ? "/email-manager" : "/sms-manager"} className="btn-secondary btn-sm hidden sm:inline-flex">
              <Sparkles size={13} className="mr-1" /> {channelLabel} Manager
            </Link>
          </div>
        </div>

        {visible.length === 0 ? (
          <div className="card p-10 text-center">
            <Megaphone size={40} className="mx-auto mb-3 text-gray-300" />
            <h3 className="font-semibold">
              {scope === "live" ? `Nothing is sending by ${channelLabel.toLowerCase()} right now` : `No ${channelLabel.toLowerCase()} campaigns yet`}
            </h3>
            <p className="text-sm text-gray-500 mt-1 mb-4">
              {scope === "live"
                ? "Switch to \"All\" to see drafts and finished campaigns."
                : `Create your first ${channelLabel.toLowerCase()} campaign to start reaching contacts.`}
            </p>
            <Link to="/sms-manager" className="btn-primary btn-sm">Go to Campaigns</Link>
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
            <p className="text-xs text-primary-600 dark:text-primary-400 mt-1 flex items-center gap-1">
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
              className={`h-full ${c.is_live ? "bg-success-500" : "bg-primary-600"}`}
              style={{ width: `${progress}%` }}
            />
          </div>
        </div>
      )}

      <div className="grid grid-cols-3 sm:grid-cols-6 gap-3 mt-3 pt-3 border-t border-gray-100 dark:border-gray-700 text-sm">
        <Metric label="Sent" value={c.sent} />
        <Metric label="Delivered" value={`${c.delivery_rate}%`} />
        <Metric label="Failed" value={c.failed} tone={c.failed > 0 ? "text-danger-600" : undefined} />
        <Metric label="Replies" value={c.replied} />
        <Metric label="Reply rate" value={`${c.reply_rate}%`} />
        <Metric label="Interested" value={c.interested} tone={c.interested > 0 ? "text-success-600" : undefined} />
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
