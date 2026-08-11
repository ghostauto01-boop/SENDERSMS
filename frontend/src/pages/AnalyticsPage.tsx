import { useState, useEffect } from "react";
import api from "../api/client";
import { AnalyticsOverview } from "../types";
import {
  TrendingUp, CheckCircle2, XCircle, MessageSquare,
  Users, Clock, AlertTriangle,
} from "lucide-react";

export default function AnalyticsPage() {
  const [analytics, setAnalytics] = useState<AnalyticsOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [days, setDays] = useState(30);

  useEffect(() => { loadAnalytics(); }, [days]);

  const loadAnalytics = async () => {
    try {
      setLoading(true);
      const { data } = await api.get("/analytics/overview", { params: { days } });
      setAnalytics(data);
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
    { label: "Messages Sent", value: analytics.sent, icon: MessageSquare, color: "text-blue-600" },
    { label: "Delivered", value: analytics.delivered, icon: CheckCircle2, color: "text-green-600" },
    { label: "Failed", value: analytics.failed, icon: XCircle, color: "text-red-600" },
    { label: "Delivery Rate", value: `${analytics.delivery_rate}%`, icon: TrendingUp, color: "text-emerald-600" },
    { label: "Replies", value: analytics.replies, icon: MessageSquare, color: "text-purple-600" },
    { label: "Reply Rate", value: `${analytics.reply_rate}%`, icon: TrendingUp, color: "text-indigo-600" },
    { label: "Opt-outs", value: analytics.opt_outs, icon: AlertTriangle, color: "text-orange-600" },
    { label: "Interested Leads", value: analytics.interested_leads, icon: Users, color: "text-pink-600" },
    { label: "Follow-ups", value: analytics.followups, icon: Clock, color: "text-yellow-600" },
  ] : [];

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
        <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
          {metrics.map((m, i) => (
            <div key={i} className="card p-4">
              <div className="flex items-center gap-2 text-sm text-gray-500 mb-1">
                <m.icon size={16} className={m.color} />
                {m.label}
              </div>
              <p className="text-2xl font-bold">{m.value}</p>
            </div>
          ))}
        </div>
      )}

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
    </div>
  );
}
