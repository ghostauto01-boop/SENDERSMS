import { useEffect, useMemo, useState } from "react";
import api from "../api/client";
import { AnalyticsOverview } from "../types";
import {
  TrendingUp, TrendingDown, CheckCircle2, XCircle, MessageSquare,
  Users, Clock, AlertTriangle, PhoneCall, ShieldAlert, Filter, BarChart3,
  Minus, Sparkles, ArrowUpRight, Percent, Send,
} from "lucide-react";
import {
  ResponsiveContainer, ComposedChart, Bar, Area, Line, XAxis, YAxis,
  CartesianGrid, Tooltip, PieChart, Pie, Cell, BarChart,
} from "recharts";

/**
 * ANALYTICS — the story of what happened, not a wall of numbers.
 *
 * The screen is built the way an operator reads it, top to bottom:
 *
 *   1. nine indicators, each with a trend arrow and a sparkline, so "is this
 *      better than last week?" is answered without a single calculation;
 *   2. the volume chart — sent, delivered, replies — as a soft gradient area
 *      with bars behind it;
 *   3. the delivery mix donut with the rate in its centre;
 *   4. the funnel, where every stage shows its conversion to the next one;
 *   5. campaign comparison and the reasons behind failures and quarantines.
 *
 * Colours come from one palette (declared below) and every chart uses a
 * gradient of the same hue, so the page feels like one instrument rather than
 * four unrelated widgets. Everything is readable on a 360px phone first.
 */

//: One palette, deliberately small. Every series in every chart comes from it.
const PALETTE = {
  forest: "#123c2b",
  moss: "#2a684e",
  mint: "#5fa982",
  lime: "#88ab56",
  amber: "#dbb440",
  rose: "#bf5167",
  sand: "#d6d8d3",
  sky: "#4f8fa8",
  plum: "#7b5ea7",
};

const SERIES_COLORS = [PALETTE.mint, PALETTE.moss, PALETTE.forest, PALETTE.sky, PALETTE.amber, PALETTE.plum, PALETTE.rose];

const fmtDate = (iso: string) => {
  try {
    const d = new Date(iso + (iso.includes("T") ? "" : "T00:00:00"));
    return d.toLocaleDateString([], { day: "numeric", month: "short" });
  } catch {
    return iso;
  }
};

const fmtNumber = (value: number) =>
  new Intl.NumberFormat(undefined, { notation: value >= 10000 ? "compact" : "standard" }).format(value);

/** Percentage change between the first and second half of a series. */
function trendOf(values: number[]): number | null {
  if (values.length < 2) return null;
  const mid = Math.floor(values.length / 2);
  const before = values.slice(0, mid).reduce((a, b) => a + b, 0);
  const after = values.slice(mid).reduce((a, b) => a + b, 0);
  if (before === 0 && after === 0) return null;
  if (before === 0) return 100;
  return Math.round(((after - before) / before) * 100);
}

/** A tiny inline sparkline — cheaper and sharper than a nested chart. */
function Sparkline({ values, color, id }: { values: number[]; color: string; id: string }) {
  const points = values.length >= 2 ? values : [0, 0];
  const max = Math.max(...points, 1);
  const min = Math.min(...points, 0);
  const span = Math.max(max - min, 1);
  const width = 96;
  const height = 30;
  const step = width / Math.max(points.length - 1, 1);
  const coords = points.map((value, index) => {
    const x = index * step;
    const y = height - ((value - min) / span) * (height - 4) - 2;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const line = `M${coords.join(" L")}`;
  const area = `${line} L${width},${height} L0,${height} Z`;

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-24 h-[30px] overflow-visible" aria-hidden>
      <defs>
        <linearGradient id={`spark-${id}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.35" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#spark-${id})`} />
      <path d={line} fill="none" stroke={color} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={coords[coords.length - 1].split(",")[0]} cy={coords[coords.length - 1].split(",")[1]} r="2.2" fill={color} />
    </svg>
  );
}

function TrendBadge({ change, invert = false }: { change: number | null; invert?: boolean }) {
  if (change === null) {
    return <span className="text-[10.5px] text-gray-400 inline-flex items-center gap-1"><Minus size={10} /> no trend yet</span>;
  }
  const good = invert ? change < 0 : change > 0;
  const flat = change === 0;
  const tone = flat
    ? "text-gray-500 bg-gray-100 dark:bg-gray-800 dark:text-gray-400"
    : good
      ? "text-success-700 bg-success-50 dark:bg-success-950/50 dark:text-success-300"
      : "text-danger-700 bg-danger-50 dark:bg-danger-950/50 dark:text-danger-300";
  const Icon = change >= 0 ? TrendingUp : TrendingDown;
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10.5px] font-semibold ${tone}`}>
      <Icon size={10} />
      {change > 0 ? "+" : ""}
      {change}%
    </span>
  );
}

const TONES = {
  primary: { icon: "text-primary-800 dark:text-primary-200", chip: "bg-primary-50 dark:bg-primary-950/60", line: PALETTE.moss },
  success: { icon: "text-success-700 dark:text-success-300", chip: "bg-success-50 dark:bg-success-950/50", line: PALETTE.lime },
  danger: { icon: "text-danger-700 dark:text-danger-300", chip: "bg-danger-50 dark:bg-danger-950/50", line: PALETTE.rose },
  warning: { icon: "text-warning-700 dark:text-warning-300", chip: "bg-warning-50 dark:bg-warning-950/50", line: PALETTE.amber },
  neutral: { icon: "text-gray-600 dark:text-gray-300", chip: "bg-gray-100 dark:bg-gray-800", line: PALETTE.sand },
} as const;

type Tone = keyof typeof TONES;

function KpiCard({
  label, value, hint, icon: Icon, tone, change, spark, invert, index,
}: {
  label: string;
  value: string | number;
  hint?: string;
  icon: typeof Send;
  tone: Tone;
  change: number | null;
  spark: number[];
  invert?: boolean;
  index: number;
}) {
  const t = TONES[tone];
  return (
    <div
      className="card group relative overflow-hidden p-3.5 sm:p-4 animate-fade-up transition-shadow duration-300 hover:shadow-raised"
      style={{ animationDelay: `${index * 35}ms` }}
    >
      <div className="flex items-start justify-between gap-2">
        <span className={`w-8 h-8 rounded-xl flex items-center justify-center ${t.chip}`}>
          <Icon size={15} className={t.icon} />
        </span>
        <TrendBadge change={change} invert={invert} />
      </div>
      <p className="text-[19px] sm:text-2xl font-semibold tracking-[-0.02em] mt-2.5 tabular-nums leading-none">
        {value}
      </p>
      <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1 truncate">{label}</p>
      <div className="flex items-end justify-between gap-2 mt-2">
        <p className="text-[10px] text-gray-400 truncate">{hint || "vs. previous period"}</p>
        <Sparkline values={spark} color={t.line} id={label.replace(/\s+/g, "-").toLowerCase()} />
      </div>
    </div>
  );
}

function ChartTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-xl border border-gray-200/80 dark:border-gray-700/80 bg-white/95 dark:bg-gray-900/95 backdrop-blur px-3 py-2 shadow-pop">
      <p className="text-[10px] uppercase tracking-[0.12em] text-gray-400 mb-1">{label}</p>
      <div className="space-y-0.5">
        {payload.map((entry: any) => (
          <div key={entry.name} className="flex items-center gap-2 text-[11.5px]">
            <span className="w-1.5 h-1.5 rounded-full" style={{ background: entry.color || entry.fill }} />
            <span className="text-gray-500 flex-1">{entry.name}</span>
            <span className="font-semibold tabular-nums">{fmtNumber(Number(entry.value) || 0)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function CardHead({
  title, subtitle, icon: Icon, right,
}: {
  title: string;
  subtitle?: string;
  icon?: typeof BarChart3;
  right?: React.ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-3 mb-3">
      <div className="min-w-0">
        <h3 className="font-semibold text-[13.5px] flex items-center gap-1.5">
          {Icon && <Icon size={14} className="text-primary-700 dark:text-primary-300" />}
          {title}
        </h3>
        {subtitle && <p className="text-[11px] text-gray-500 mt-0.5">{subtitle}</p>}
      </div>
      {right}
    </div>
  );
}

export default function AnalyticsPage() {
  const [analytics, setAnalytics] = useState<AnalyticsOverview | null>(null);
  const [series, setSeries] = useState<any[]>([]);
  const [funnel, setFunnel] = useState<any>(null);
  const [byCampaign, setByCampaign] = useState<any[]>([]);
  const [calls, setCalls] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [days, setDays] = useState(30);

  useEffect(() => {
    loadAnalytics();
  }, [days]);

  const loadAnalytics = async () => {
    try {
      setLoading(true);
      setError(null);
      const [a, t, f, c, k] = await Promise.all([
        api.get("/analytics/overview", { params: { days } }),
        api.get("/analytics/timeseries", { params: { days } }),
        api.get("/analytics/funnel", { params: { days } }),
        api.get("/analytics/by-campaign", { params: { days } }),
        api.get("/analytics/calls", { params: { days } }),
      ]);
      setAnalytics(a.data);
      setSeries((t.data.points || []).map((p: any) => ({ ...p, date: fmtDate(p.date) })));
      setFunnel(f.data);
      setByCampaign(c.data.campaigns || []);
      setCalls(k.data);
    } catch (err: any) {
      setError(err.response?.data?.detail || "Failed to load analytics");
    } finally {
      setLoading(false);
    }
  };

  // Sparklines are derived from the same daily series every chart draws, so a
  // card and the chart below it can never tell different stories.
  const sparks = useMemo(
    () => ({
      sent: series.map((p) => Number(p.sent) || 0),
      delivered: series.map((p) => Number(p.delivered) || 0),
      failed: series.map((p) => Number(p.failed) || 0),
      replies: series.map((p) => Number(p.replies) || 0),
      rate: series.map((p) => Number(p.delivery_rate) || 0),
    }),
    [series]
  );

  if (error) {
    return (
      <div className="card p-8 text-center max-w-md mx-auto mt-10">
        <AlertTriangle size={30} className="mx-auto text-danger-500 mb-3" />
        <h2 className="text-lg font-semibold mb-1">Analytics could not load</h2>
        <p className="text-sm text-gray-500 mb-4">{error}</p>
        <button onClick={loadAnalytics} className="btn-primary">Try again</button>
      </div>
    );
  }

  const metrics = analytics
    ? [
        { label: "Messages sent", value: fmtNumber(analytics.sent), icon: Send, tone: "primary" as Tone, change: trendOf(sparks.sent), spark: sparks.sent, hint: `${series.length} active day${series.length === 1 ? "" : "s"}` },
        { label: "Delivered", value: fmtNumber(analytics.delivered), icon: CheckCircle2, tone: "success" as Tone, change: trendOf(sparks.delivered), spark: sparks.delivered, hint: `${analytics.delivery_rate}% delivery rate` },
        { label: "Failed", value: fmtNumber(analytics.failed), icon: XCircle, tone: "danger" as Tone, change: trendOf(sparks.failed), spark: sparks.failed, invert: true, hint: "lower is better" },
        { label: "Delivery rate", value: `${analytics.delivery_rate}%`, icon: Percent, tone: "success" as Tone, change: trendOf(sparks.rate), spark: sparks.rate, hint: "of everything sent" },
        { label: "Replies", value: fmtNumber(analytics.replies), icon: MessageSquare, tone: "primary" as Tone, change: trendOf(sparks.replies), spark: sparks.replies, hint: `${analytics.reply_rate}% reply rate` },
        { label: "Reply rate", value: `${analytics.reply_rate}%`, icon: ArrowUpRight, tone: "primary" as Tone, change: null, spark: sparks.replies, hint: "replies ÷ sent" },
        { label: "Opt-outs", value: fmtNumber(analytics.opt_outs), icon: AlertTriangle, tone: "warning" as Tone, change: null, spark: sparks.failed, invert: true, hint: "keep this low" },
        { label: "Interested leads", value: fmtNumber(analytics.interested_leads), icon: Users, tone: "primary" as Tone, change: null, spark: sparks.replies, hint: "marked in the inbox" },
        { label: "Follow-ups", value: fmtNumber(analytics.followups), icon: Clock, tone: "warning" as Tone, change: null, spark: sparks.replies, hint: "scheduled or due" },
      ]
    : [];

  const donut = analytics
    ? [
        { name: "Delivered", value: analytics.delivered, color: PALETTE.moss },
        { name: "Failed", value: analytics.failed, color: PALETTE.rose },
        {
          name: "In flight",
          value: Math.max(0, analytics.sent - analytics.delivered - analytics.failed),
          color: PALETTE.sand,
        },
      ].filter((slice) => slice.value > 0)
    : [];

  const stages: { stage: string; count: number }[] = funnel?.stages || [];
  const stageColors = [PALETTE.forest, PALETTE.moss, PALETTE.mint, PALETTE.lime, PALETTE.amber];

  const campaignData = byCampaign.slice(0, 8).map((c: any, i: number) => ({
    ...c,
    colour: SERIES_COLORS[i % SERIES_COLORS.length],
  }));

  return (
    <div className="space-y-4 sm:space-y-5 pb-4">
      {/* Header */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="eyebrow flex items-center gap-1.5">
            <Sparkles size={12} /> Insights
          </p>
          <h1 className="page-title mt-1.5">Analytics</h1>
          <p className="page-subtitle">
            Every message, reply and naira of attention — measured from the same tables your inbox reads.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="segment w-full sm:w-auto">
            {[
              [7, "7d"],
              [30, "30d"],
              [90, "90d"],
              [365, "1y"],
            ].map(([value, label]) => (
              <button
                key={String(value)}
                onClick={() => setDays(Number(value))}
                className={`segment-item flex-1 sm:flex-none ${days === value ? "segment-item-active" : ""}`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {loading ? (
        <div className="grid grid-cols-2 lg:grid-cols-3 xl:grid-cols-3 gap-3">
          {[...Array(9)].map((_, i) => (
            <div key={i} className="card p-4">
              <div className="skeleton h-8 w-8 rounded-xl mb-3" />
              <div className="skeleton h-6 w-16 mb-2" />
              <div className="skeleton h-3 w-24" />
            </div>
          ))}
        </div>
      ) : (
        <>
          {/* ---- Indicators ---- */}
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3 sm:gap-4 stagger">
            {metrics.map((m, i) => (
              <KpiCard key={m.label} {...m} index={i} />
            ))}
          </div>

          {/* ---- Volume over time ---- */}
          <div className="card p-4 sm:p-5 animate-fade-up">
            <CardHead
              title="Delivery over time"
              subtitle={`Sent, delivered, failed and replies · last ${days} days`}
              icon={BarChart3}
              right={
                <div className="hidden sm:flex items-center gap-3 text-[10.5px] text-gray-500">
                  {[
                    ["Sent", PALETTE.mint],
                    ["Delivered", PALETTE.moss],
                    ["Failed", PALETTE.rose],
                  ].map(([label, colour]) => (
                    <span key={label} className="inline-flex items-center gap-1">
                      <span className="w-2 h-2 rounded-full" style={{ background: colour }} /> {label}
                    </span>
                  ))}
                </div>
              }
            />
            {series.length === 0 ? (
              <p className="text-sm text-gray-400 py-12 text-center">No messages in this period yet.</p>
            ) : (
              <div className="h-60 sm:h-72 -ml-2">
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart data={series} margin={{ top: 6, right: 6, left: -18, bottom: 0 }}>
                    <defs>
                      <linearGradient id="grad-sent" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor={PALETTE.mint} stopOpacity={0.9} />
                        <stop offset="100%" stopColor={PALETTE.mint} stopOpacity={0.35} />
                      </linearGradient>
                      <linearGradient id="grad-replies" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor={PALETTE.moss} stopOpacity={0.28} />
                        <stop offset="100%" stopColor={PALETTE.moss} stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="4 6" stroke={PALETTE.sand} vertical={false} opacity={0.5} />
                    <XAxis
                      dataKey="date"
                      tick={{ fontSize: 10.5, fill: "#828b83" }}
                      axisLine={false}
                      tickLine={false}
                      interval="preserveStartEnd"
                      minTickGap={22}
                    />
                    <YAxis
                      tick={{ fontSize: 10.5, fill: "#828b83" }}
                      axisLine={false}
                      tickLine={false}
                      allowDecimals={false}
                      width={44}
                    />
                    <Tooltip content={<ChartTooltip />} cursor={{ fill: "rgba(95,169,130,0.08)" }} />
                    <Bar dataKey="sent" name="Sent" fill="url(#grad-sent)" radius={[4, 4, 0, 0]} maxBarSize={26} />
                    <Bar dataKey="failed" name="Failed" fill={PALETTE.rose} fillOpacity={0.75} radius={[4, 4, 0, 0]} maxBarSize={26} />
                    <Area
                      type="monotone"
                      dataKey="replies"
                      name="Replies"
                      stroke={PALETTE.forest}
                      strokeWidth={2}
                      fill="url(#grad-replies)"
                      dot={false}
                      activeDot={{ r: 3.5, strokeWidth: 2, stroke: "#fff" }}
                    />
                    <Line
                      type="monotone"
                      dataKey="delivered"
                      name="Delivered"
                      stroke={PALETTE.moss}
                      strokeWidth={2.4}
                      dot={false}
                      activeDot={{ r: 3.5, strokeWidth: 2, stroke: "#fff" }}
                    />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
            )}
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-5 gap-3 sm:gap-4">
            {/* ---- Delivery mix ---- */}
            <div className="card p-4 sm:p-5 lg:col-span-2">
              <CardHead title="Delivery mix" subtitle="Share of sent messages by outcome" />
              {donut.length === 0 ? (
                <p className="text-sm text-gray-400 py-12 text-center">Nothing sent yet.</p>
              ) : (
                <>
                  <div className="relative h-40 sm:h-44">
                    <ResponsiveContainer width="100%" height="100%">
                      <PieChart>
                        <Pie
                          data={donut}
                          dataKey="value"
                          nameKey="name"
                          innerRadius="68%"
                          outerRadius="94%"
                          paddingAngle={3}
                          stroke="none"
                          startAngle={90}
                          endAngle={-270}
                        >
                          {donut.map((slice) => (
                            <Cell key={slice.name} fill={slice.color} />
                          ))}
                        </Pie>
                        <Tooltip content={<ChartTooltip />} />
                      </PieChart>
                    </ResponsiveContainer>
                    <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
                      <span className="text-[22px] font-semibold tracking-tight leading-none">
                        {analytics?.delivery_rate}%
                      </span>
                      <span className="text-[10px] text-gray-400 mt-1">delivered</span>
                    </div>
                  </div>
                  <div className="space-y-1.5 mt-3">
                    {donut.map((slice) => (
                      <div key={slice.name} className="flex items-center gap-2 text-[11.5px]">
                        <span className="w-2 h-2 rounded-full shrink-0" style={{ background: slice.color }} />
                        <span className="text-gray-500 flex-1">{slice.name}</span>
                        <span className="font-semibold tabular-nums">{fmtNumber(slice.value)}</span>
                        <span className="text-gray-400 tabular-nums w-10 text-right">
                          {Math.round((slice.value / Math.max(analytics?.sent || 1, 1)) * 100)}%
                        </span>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </div>

            {/* ---- Funnel ---- */}
            <div className="card p-4 sm:p-5 lg:col-span-3">
              <CardHead
                title="Lead funnel"
                subtitle="How many made it to each stage, and the drop between them"
                icon={Filter}
              />
              {stages.length === 0 ? (
                <p className="text-sm text-gray-400 py-12 text-center">No data in this period.</p>
              ) : (
                <div className="space-y-3">
                  {stages.map((stage, index) => {
                    const top = Math.max(stages[0]?.count || 1, 1);
                    const share = Math.round((stage.count / top) * 100);
                    const previous = index === 0 ? null : stages[index - 1].count;
                    const step = previous && previous > 0 ? Math.round((stage.count / previous) * 100) : null;
                    const colour = stageColors[index % stageColors.length];
                    return (
                      <div key={stage.stage}>
                        <div className="flex items-baseline justify-between gap-2 mb-1">
                          <span className="text-[12px] font-medium capitalize truncate">{stage.stage}</span>
                          <span className="text-[11px] text-gray-400 tabular-nums shrink-0">
                            {fmtNumber(stage.count)} · {share}%
                            {step !== null && (
                              <span className={`ml-1.5 font-semibold ${step >= 50 ? "text-success-600" : "text-warning-600"}`}>
                                {step}%
                              </span>
                            )}
                          </span>
                        </div>
                        <div className="h-2.5 rounded-full bg-gray-100 dark:bg-gray-700/50 overflow-hidden">
                          <div
                            className="h-full rounded-full transition-[width] duration-700 ease-gentle"
                            style={{
                              width: `${Math.max(share, stage.count > 0 ? 3 : 0)}%`,
                              background: `linear-gradient(90deg, ${colour}, ${colour}99)`,
                            }}
                          />
                        </div>
                      </div>
                    );
                  })}
                  <p className="text-[10.5px] text-gray-400 pt-1">
                    The small percentage beside each stage is the conversion from the one above it.
                  </p>
                </div>
              )}
            </div>
          </div>

          {/* ---- Campaign comparison ---- */}
          <div className="card p-4 sm:p-5">
            <CardHead
              title="Campaign comparison"
              subtitle="Top campaigns by volume in this period"
              icon={BarChart3}
              right={
                byCampaign.length > 0 ? (
                  <span className="badge-blue shrink-0">{byCampaign.length} campaigns</span>
                ) : null
              }
            />
            {byCampaign.length === 0 ? (
              <p className="text-sm text-gray-400 py-10 text-center">No campaign traffic in this period.</p>
            ) : (
              <div className="h-[260px] sm:h-[280px] -ml-2">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={campaignData} margin={{ top: 4, right: 6, left: -18, bottom: 0 }} barGap={3}>
                    <defs>
                      <linearGradient id="grad-delivered" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor={PALETTE.moss} />
                        <stop offset="100%" stopColor={PALETTE.mint} />
                      </linearGradient>
                      <linearGradient id="grad-reply" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor={PALETTE.amber} />
                        <stop offset="100%" stopColor={PALETTE.lime} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="4 6" stroke={PALETTE.sand} vertical={false} opacity={0.5} />
                    <XAxis
                      dataKey="campaign_name"
                      tick={{ fontSize: 10, fill: "#828b83" }}
                      axisLine={false}
                      tickLine={false}
                      interval={0}
                      height={44}
                      tickFormatter={(value: string) => (value.length > 12 ? `${value.slice(0, 11)}…` : value)}
                    />
                    <YAxis
                      tick={{ fontSize: 10.5, fill: "#828b83" }}
                      axisLine={false}
                      tickLine={false}
                      allowDecimals={false}
                      width={44}
                    />
                    <Tooltip content={<ChartTooltip />} cursor={{ fill: "rgba(95,169,130,0.08)" }} />
                    <Bar dataKey="sent" name="Sent" fill={PALETTE.sand} radius={[5, 5, 0, 0]} maxBarSize={22} />
                    <Bar dataKey="delivered" name="Delivered" fill="url(#grad-delivered)" radius={[5, 5, 0, 0]} maxBarSize={22} />
                    <Bar dataKey="replies" name="Replies" fill="url(#grad-reply)" radius={[5, 5, 0, 0]} maxBarSize={22} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}
            {byCampaign.length > 0 && (
              <div className="mt-3 pt-3 border-t border-gray-100 dark:border-gray-700/60 space-y-2">
                {byCampaign.slice(0, 5).map((c: any, i: number) => (
                  <div key={c.campaign_name || i} className="flex items-center gap-2 text-[11.5px]">
                    <span
                      className="w-2 h-2 rounded-full shrink-0"
                      style={{ background: SERIES_COLORS[i % SERIES_COLORS.length] }}
                    />
                    <span className="flex-1 truncate">{c.campaign_name}</span>
                    <span className="text-gray-400 tabular-nums">{fmtNumber(c.sent || 0)} sent</span>
                    <span className="badge-green tabular-nums">{Math.round(((c.delivered || 0) / Math.max(c.sent || 1, 1)) * 100)}%</span>
                    <span className="badge-blue tabular-nums">{c.replies || 0} replies</span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* ---- Failure + quarantine ---- */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 sm:gap-4">
            <div className="card p-4 sm:p-5">
              <CardHead
                title="Why sends failed"
                subtitle="Top gateway and carrier reasons"
                icon={XCircle}
              />
              {(funnel?.failure_reasons || []).length === 0 ? (
                <p className="text-sm text-success-600 flex items-center gap-1.5 py-6">
                  <CheckCircle2 size={15} /> No failures in this period.
                </p>
              ) : (
                <div className="space-y-2.5">
                  {(funnel.failure_reasons || []).map((r: any, i: number) => {
                    const max = Math.max(...(funnel.failure_reasons || []).map((x: any) => x.count), 1);
                    return (
                      <div key={i}>
                        <div className="flex justify-between text-[11.5px] mb-1">
                          <span className="truncate font-medium">{r.reason || "Unknown"}</span>
                          <span className="text-gray-500 ml-2 tabular-nums">{r.count}</span>
                        </div>
                        <div className="h-2 rounded-full bg-gray-100 dark:bg-gray-700/60 overflow-hidden">
                          <div
                            className="h-full rounded-full transition-[width] duration-700"
                            style={{
                              width: `${Math.round((r.count / max) * 100)}%`,
                              background: `linear-gradient(90deg, ${PALETTE.rose}, ${PALETTE.rose}88)`,
                            }}
                          />
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
            <div className="card p-4 sm:p-5">
              <CardHead
                title="Numbers the filter blocked"
                subtitle="Quarantined before they could cost you a SIM"
                icon={ShieldAlert}
              />
              {(funnel?.quarantine_reasons || []).length === 0 ? (
                <p className="text-sm text-gray-400 py-6">No quarantined numbers — your lists are clean.</p>
              ) : (
                <div className="space-y-2.5">
                  {(funnel.quarantine_reasons || []).map((r: any, i: number) => {
                    const max = Math.max(...(funnel.quarantine_reasons || []).map((x: any) => x.count), 1);
                    return (
                      <div key={i}>
                        <div className="flex justify-between text-[11.5px] mb-1">
                          <span className="truncate font-medium flex items-center gap-1">
                            <ShieldAlert size={11} className="text-warning-500 shrink-0" />
                            {r.reason}
                          </span>
                          <span className="text-gray-500 ml-2 tabular-nums">{r.count}</span>
                        </div>
                        <div className="h-2 rounded-full bg-gray-100 dark:bg-gray-700/60 overflow-hidden">
                          <div
                            className="h-full rounded-full transition-[width] duration-700"
                            style={{
                              width: `${Math.round((r.count / max) * 100)}%`,
                              background: `linear-gradient(90deg, ${PALETTE.amber}, ${PALETTE.lime})`,
                            }}
                          />
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>

          {/* ---- Calls ---- */}
          <div className="card p-4 sm:p-5">
            <CardHead
              title="Calls"
              subtitle="Placed and connected through CallGate"
              icon={PhoneCall}
              right={
                calls ? (
                  <div className="flex items-center gap-1.5 shrink-0">
                    <span className="badge-gray tabular-nums">{calls.total} placed</span>
                    <span className="badge-green tabular-nums">{calls.connect_rate}% connected</span>
                  </div>
                ) : null
              }
            />
            {(calls?.points || []).length === 0 ? (
              <p className="text-sm text-gray-400 py-8 text-center">
                No calls in this period. Place one from the Phone page.
              </p>
            ) : (
              <div className="h-44 sm:h-48 -ml-2">
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart
                    data={(calls.points || []).map((p: any) => ({ ...p, date: fmtDate(p.date) }))}
                    margin={{ top: 4, right: 6, left: -18, bottom: 0 }}
                  >
                    <defs>
                      <linearGradient id="grad-calls" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor={PALETTE.sky} stopOpacity={0.4} />
                        <stop offset="100%" stopColor={PALETTE.sky} stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="4 6" stroke={PALETTE.sand} vertical={false} opacity={0.5} />
                    <XAxis
                      dataKey="date"
                      tick={{ fontSize: 10.5, fill: "#828b83" }}
                      axisLine={false}
                      tickLine={false}
                      interval="preserveStartEnd"
                    />
                    <YAxis
                      tick={{ fontSize: 10.5, fill: "#828b83" }}
                      axisLine={false}
                      tickLine={false}
                      allowDecimals={false}
                      width={44}
                    />
                    <Tooltip content={<ChartTooltip />} />
                    <Area
                      type="monotone"
                      dataKey="calls"
                      name="Calls"
                      stroke={PALETTE.sky}
                      strokeWidth={2.2}
                      fill="url(#grad-calls)"
                      dot={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="connected"
                      name="Connected"
                      stroke={PALETTE.mint}
                      strokeWidth={2}
                      strokeDasharray="4 3"
                      dot={false}
                    />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
            )}
          </div>

          {analytics && (
            <div className="rounded-3xl bg-primary-950 dark:bg-primary-900/70 text-white p-5 relative overflow-hidden">
              <div className="absolute -top-16 -right-10 w-52 h-52 rounded-full bg-accent-400/15 blur-3xl pointer-events-none" />
              <p className="relative text-[10px] uppercase tracking-[0.18em] text-white/50">The short version</p>
              <p className="relative text-sm text-white/80 mt-2 leading-relaxed max-w-3xl">
                Over the last {analytics.period_days} days you sent{" "}
                <span className="font-semibold text-white">{fmtNumber(analytics.sent)}</span> messages.
                {" "}{fmtNumber(analytics.delivered)} were delivered ({analytics.delivery_rate}%) and you
                received <span className="font-semibold text-white">{fmtNumber(analytics.replies)}</span> replies
                ({analytics.reply_rate}%). {analytics.interested_leads} contacts are marked interested and{" "}
                {analytics.opt_outs} opted out.
              </p>
            </div>
          )}
        </>
      )}
    </div>
  );
}
