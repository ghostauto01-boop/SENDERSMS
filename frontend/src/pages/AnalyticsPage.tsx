import { useState, useEffect } from "react";
import api from "../api/client";
import { AnalyticsOverview } from "../types";
import {
  TrendingUp, CheckCircle2, XCircle, MessageSquare,
  Users, Clock, AlertTriangle, PhoneCall, ShieldAlert, Filter,
} from "lucide-react";
import {
  ResponsiveContainer, ComposedChart, Bar, Line, XAxis, YAxis,
  CartesianGrid, Tooltip, Legend, PieChart, Pie, Cell,
  BarChart, Area, AreaChart,
} from "recharts";

const GREEN = "#00a884";
const BLUE = "#0066cc";
const RED = "#e5484d";
const AMBER = "#f5a623";
const PURPLE = "#8e4ec6";
const TEAL = "#12a594";

const fmtDate = (iso: string) => {
  try {
    const d = new Date(iso + (iso.includes("T") ? "" : "T00:00:00"));
    return d.toLocaleDateString([], { day: "numeric", month: "short" });
  } catch { return iso; }
};

export default function AnalyticsPage() {
  const [analytics, setAnalytics] = useState<AnalyticsOverview | null>(null);
  const [series, setSeries] = useState<any[]>([]);
  const [funnel, setFunnel] = useState<any>(null);
  const [byCampaign, setByCampaign] = useState<any[]>([]);
  const [calls, setCalls] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [days, setDays] = useState(30);

  useEffect(() => { loadAnalytics(); }, [days]);

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

  if (error) {
    return (
      <div className="text-center py-12">
        <h2 className="text-xl font-semibold mb-2">Error</h2>
        <p className="text-gray-500 mb-4">{error}</p>
        <button onClick={loadAnalytics} className="btn-primary">Retry</button>
      </div>
    );
  }

  const metrics = analytics ? [
    { label: "Messages Sent", value: analytics.sent, icon: MessageSquare, color: "text-blue-600", bg: "bg-blue-50 dark:bg-blue-900/20" },
    { label: "Delivered", value: analytics.delivered, icon: CheckCircle2, color: "text-green-600", bg: "bg-green-50 dark:bg-green-900/20" },
    { label: "Failed", value: analytics.failed, icon: XCircle, color: "text-red-600", bg: "bg-red-50 dark:bg-red-900/20" },
    { label: "Delivery Rate", value: `${analytics.delivery_rate}%`, icon: TrendingUp, color: "text-emerald-600", bg: "bg-emerald-50 dark:bg-emerald-900/20" },
    { label: "Replies", value: analytics.replies, icon: MessageSquare, color: "text-purple-600", bg: "bg-purple-50 dark:bg-purple-900/20" },
    { label: "Reply Rate", value: `${analytics.reply_rate}%`, icon: TrendingUp, color: "text-indigo-600", bg: "bg-indigo-50 dark:bg-indigo-900/20" },
    { label: "Opt-outs", value: analytics.opt_outs, icon: AlertTriangle, color: "text-orange-600", bg: "bg-orange-50 dark:bg-orange-900/20" },
    { label: "Interested Leads", value: analytics.interested_leads, icon: Users, color: "text-pink-600", bg: "bg-pink-50 dark:bg-pink-900/20" },
    { label: "Follow-ups", value: analytics.followups, icon: Clock, color: "text-yellow-600", bg: "bg-yellow-50 dark:bg-yellow-900/20" },
  ] : [];

  const donut = analytics ? [
    { name: "Delivered", value: analytics.delivered },
    { name: "Failed", value: analytics.failed },
    { name: "In flight / other", value: Math.max(0, analytics.sent - analytics.delivered - analytics.failed) },
  ].filter((d) => d.value > 0) : [];
  const donutColors = [GREEN, RED, "#cbd5e1"];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <h1 className="text-xl sm:text-2xl font-bold">Analytics</h1>
        <select
          className="input py-1.5 w-auto text-sm"
          value={days}
          onChange={(e) => setDays(parseInt(e.target.value))}
        >
          <option value={7}>Last 7 days</option>
          <option value={30}>Last 30 days</option>
          <option value={90}>Last 90 days</option>
          <option value={365}>Last year</option>
        </select>
      </div>

      {loading ? (
        <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
          {[...Array(9)].map((_, i) => (
            <div key={i} className="card p-4">
              <div className="skeleton h-4 w-20 mb-2" />
              <div className="skeleton h-8 w-12" />
            </div>
          ))}
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
            {metrics.map((m, i) => (
              <div key={i} className="card p-4">
                <div className={`w-9 h-9 rounded-xl ${m.bg} flex items-center justify-center mb-2`}>
                  <m.icon size={18} className={m.color} />
                </div>
                <p className="text-2xl font-bold">{m.value}</p>
                <p className="text-sm text-gray-500">{m.label}</p>
              </div>
            ))}
          </div>

          {/* Delivery over time */}
          <div className="card p-4">
            <h3 className="font-semibold mb-1">Delivery over time</h3>
            <p className="text-xs text-gray-500 mb-3">Sent vs delivered vs replies per day.</p>
            {series.length === 0 ? (
              <p className="text-sm text-gray-400 py-8 text-center">No messages in this period yet.</p>
            ) : (
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart data={series} margin={{ top: 5, right: 5, left: -15, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" opacity={0.4} />
                    <XAxis dataKey="date" tick={{ fontSize: 11 }} interval="preserveStartEnd" />
                    <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
                    <Tooltip />
                    <Legend wrapperStyle={{ fontSize: 12 }} />
                    <Bar dataKey="sent" name="Sent" fill={BLUE} opacity={0.85} radius={[3, 3, 0, 0]} />
                    <Bar dataKey="failed" name="Failed" fill={RED} radius={[3, 3, 0, 0]} />
                    <Line type="monotone" dataKey="delivered" name="Delivered" stroke={GREEN} strokeWidth={2.5} dot={false} />
                    <Line type="monotone" dataKey="replies" name="Replies" stroke={PURPLE} strokeWidth={2} dot={false} />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
            )}
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {/* Delivery donut */}
            <div className="card p-4">
              <h3 className="font-semibold mb-1">Delivery mix</h3>
              <p className="text-xs text-gray-500 mb-3">Share of sent SMS by outcome.</p>
              {donut.length === 0 ? (
                <p className="text-sm text-gray-400 py-8 text-center">Nothing sent yet.</p>
              ) : (
                <div className="h-56">
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie data={donut} dataKey="value" nameKey="name" innerRadius={55} outerRadius={85} paddingAngle={3} label={({ name, value }) => `${name}: ${value}`}>
                        {donut.map((_, i) => (<Cell key={i} fill={donutColors[i % donutColors.length]} />))}
                      </Pie>
                      <Tooltip />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
              )}
            </div>

            {/* Funnel */}
            <div className="card p-4">
              <h3 className="font-semibold mb-1">Lead funnel</h3>
              <p className="text-xs text-gray-500 mb-3">Sent → delivered → replied → interested.</p>
              {(funnel?.stages || []).length === 0 ? (
                <p className="text-sm text-gray-400 py-8 text-center">No data.</p>
              ) : (
                <div className="h-56">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={funnel.stages} layout="vertical" margin={{ left: 10, right: 20 }}>
                      <CartesianGrid strokeDasharray="3 3" opacity={0.4} horizontal={false} />
                      <XAxis type="number" tick={{ fontSize: 11 }} allowDecimals={false} />
                      <YAxis type="category" dataKey="stage" tick={{ fontSize: 12 }} width={80} />
                      <Tooltip />
                      <Bar dataKey="count" name="Contacts" fill={TEAL} radius={[0, 4, 4, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              )}
            </div>
          </div>

          {/* Campaign comparison */}
          <div className="card p-4">
            <h3 className="font-semibold mb-1">Campaign comparison</h3>
            <p className="text-xs text-gray-500 mb-3">Top campaigns by volume in this period.</p>
            {byCampaign.length === 0 ? (
              <p className="text-sm text-gray-400 py-6 text-center">No campaign traffic in this period.</p>
            ) : (
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={byCampaign} margin={{ top: 5, right: 5, left: -10, bottom: 40 }}>
                    <CartesianGrid strokeDasharray="3 3" opacity={0.4} />
                    <XAxis dataKey="campaign_name" tick={{ fontSize: 11 }} interval={0} angle={-18} dy={10} height={50} />
                    <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
                    <Tooltip />
                    <Legend wrapperStyle={{ fontSize: 12 }} />
                    <Bar dataKey="sent" name="Sent" fill={BLUE} radius={[3, 3, 0, 0]} />
                    <Bar dataKey="delivered" name="Delivered" fill={GREEN} radius={[3, 3, 0, 0]} />
                    <Bar dataKey="replies" name="Replies" fill={PURPLE} radius={[3, 3, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}
          </div>

          {/* Failure + quarantine reasons */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div className="card p-4">
              <h3 className="font-semibold mb-1 flex items-center gap-2"><XCircle size={16} className="text-red-500" /> Why sends failed</h3>
              <p className="text-xs text-gray-500 mb-3">Top gateway/carrier failure reasons.</p>
              {(funnel?.failure_reasons || []).length === 0 ? (
                <p className="text-sm text-green-600 flex items-center gap-1.5 py-4"><CheckCircle2 size={15} /> No failures in this period. 🎉</p>
              ) : (
                <div className="space-y-2">
                  {(funnel.failure_reasons || []).map((r: any, i: number) => {
                    const max = Math.max(...(funnel.failure_reasons || []).map((x: any) => x.count), 1);
                    return (
                      <div key={i}>
                        <div className="flex justify-between text-xs mb-0.5"><span className="truncate font-medium">{r.reason}</span><span className="text-gray-500 ml-2">{r.count}</span></div>
                        <div className="h-2 rounded-full bg-gray-100 dark:bg-gray-700"><div className="h-2 rounded-full bg-red-400" style={{ width: `${Math.round((r.count / max) * 100)}%` }} /></div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
            <div className="card p-4">
              <h3 className="font-semibold mb-1 flex items-center gap-2"><Filter size={16} className="text-amber-500" /> Numbers the filter is blocking</h3>
              <p className="text-xs text-gray-500 mb-3">Bad numbers quarantined so they never bill your SIM. Clean them from Lists.</p>
              {(funnel?.quarantine_reasons || []).length === 0 ? (
                <p className="text-sm text-gray-400 py-4">No quarantined numbers — your lists are clean. ✨</p>
              ) : (
                <div className="space-y-2">
                  {(funnel.quarantine_reasons || []).map((r: any, i: number) => {
                    const max = Math.max(...(funnel.quarantine_reasons || []).map((x: any) => x.count), 1);
                    return (
                      <div key={i}>
                        <div className="flex justify-between text-xs mb-0.5"><span className="truncate font-medium flex items-center gap-1"><ShieldAlert size={12} className="text-amber-500" />{r.reason}</span><span className="text-gray-500 ml-2">{r.count}</span></div>
                        <div className="h-2 rounded-full bg-gray-100 dark:bg-gray-700"><div className="h-2 rounded-full bg-amber-400" style={{ width: `${Math.round((r.count / max) * 100)}%` }} /></div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>

          {/* Calls */}
          <div className="card p-4">
            <h3 className="font-semibold mb-1 flex items-center gap-2"><PhoneCall size={16} className="text-[#00a884]" /> Calls</h3>
            <p className="text-xs text-gray-500 mb-3">
              {calls ? `${calls.total} calls placed • ${calls.connected} connected (${calls.connect_rate}%)` : "Phone call activity via CallGate."}
            </p>
            {(calls?.points || []).length === 0 ? (
              <p className="text-sm text-gray-400 py-6 text-center">No calls in this period. Place one from the Phone page. 📞</p>
            ) : (
              <div className="h-48">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={(calls.points || []).map((p: any) => ({ ...p, date: fmtDate(p.date) }))} margin={{ top: 5, right: 5, left: -15, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" opacity={0.4} />
                    <XAxis dataKey="date" tick={{ fontSize: 11 }} interval="preserveStartEnd" />
                    <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
                    <Tooltip />
                    <Legend wrapperStyle={{ fontSize: 12 }} />
                    <Area type="monotone" dataKey="calls" name="Calls" stroke={GREEN} fill={GREEN} fillOpacity={0.2} />
                    <Area type="monotone" dataKey="connected" name="Connected" stroke={BLUE} fill={BLUE} fillOpacity={0.2} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            )}
          </div>

          {analytics && (
            <div className="card p-4">
              <h3 className="font-semibold mb-2">Summary</h3>
              <p className="text-sm text-gray-500">
                Over the last {analytics.period_days} days, you sent {analytics.sent} SMS messages.
                {analytics.delivered} were delivered ({analytics.delivery_rate}% delivery rate).
                You received {analytics.replies} replies ({analytics.reply_rate}% reply rate).
                {analytics.opt_outs} contacts opted out and {analytics.interested_leads} contacts are marked as interested leads.
              </p>
            </div>
          )}
        </>
      )}
    </div>
  );
}
