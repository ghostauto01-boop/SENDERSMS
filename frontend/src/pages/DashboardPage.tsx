import { useState, useEffect } from "react";
import api from "../api/client";
import {
  Users, MessageSquare, CheckCircle2, XCircle, TrendingUp,
  BarChart3, Activity, Clock, AlertTriangle,
} from "lucide-react";
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, BarChart, Bar,
} from "recharts";

export default function DashboardPage() {
  const [stats, setStats] = useState<any>(null);
  const [charts, setCharts] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { loadData(); }, []);

  const loadData = async () => {
    try {
      setLoading(true); setError(null);
      const [sr, cr] = await Promise.allSettled([
        api.get("/dashboard/stats"),
        api.get("/dashboard/charts", { params: { days: 30 } }),
      ]);
      if (sr.status === "fulfilled") setStats(sr.value.data);
      if (cr.status === "fulfilled") setCharts(cr.value.data);
    } catch {
      setError("Failed to load dashboard");
    } finally {
      setLoading(false);
    }
  };

  if (loading) return (
    <div className="space-y-6">
      <h1 className="text-xl sm:text-2xl font-bold">Dashboard</h1>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {[...Array(8)].map((_, i) => (
          <div key={i} className="card p-4">
            <div className="skeleton h-4 w-20 mb-2" />
            <div className="skeleton h-8 w-12" />
          </div>
        ))}
      </div>
    </div>
  );

  if (error && !stats) return (
    <div className="text-center py-12">
      <AlertTriangle size={48} className="mx-auto text-red-500 mb-4" />
      <h2 className="text-xl font-semibold mb-2">Error Loading Dashboard</h2>
      <p className="text-gray-500 mb-4">{error}</p>
      <button onClick={loadData} className="btn-primary">Retry</button>
    </div>
  );

  const s = stats || {};
  const deliveries = s.messages_delivered || 0;
  const sent = s.messages_sent || 0;

  const statCards = [
    { label: "Total Contacts", value: s.total_contacts || 0, icon: Users, color: "text-blue-600 bg-blue-100 dark:bg-blue-900/50" },
    { label: "Messages Sent", value: sent, icon: MessageSquare, color: "text-green-600 bg-green-100 dark:bg-green-900/50" },
    { label: "Delivered", value: deliveries, icon: CheckCircle2, color: "text-emerald-600 bg-emerald-100 dark:bg-emerald-900/50" },
    { label: "Failed", value: s.messages_failed || 0, icon: XCircle, color: "text-red-600 bg-red-100 dark:bg-red-900/50" },
    { label: "Replies", value: s.replies || 0, icon: TrendingUp, color: "text-purple-600 bg-purple-100 dark:bg-purple-900/50" },
    { label: "Delivery Rate", value: sent > 0 ? `${Math.round(deliveries / sent * 100)}%` : "0%", icon: BarChart3, color: "text-indigo-600 bg-indigo-100 dark:bg-indigo-900/50" },
    { label: "Active Campaigns", value: s.active_campaigns || 0, icon: Activity, color: "text-orange-600 bg-orange-100 dark:bg-orange-900/50" },
    { label: "Follow-ups Due", value: s.followups_due_today || 0, icon: Clock, color: "text-yellow-600 bg-yellow-100 dark:bg-yellow-900/50" },
  ];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <h1 className="text-xl sm:text-2xl font-bold text-gray-900 dark:text-white">Dashboard</h1>
        <span className={`badge ${(s.gateway_status || "") === "healthy" ? "badge-green" : "badge-gray"}`}>
          Gateway: {s.gateway_status || "unknown"}
        </span>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {statCards.map((stat, i) => (
          <div key={i} className="card p-4">
            <div className="flex items-start justify-between">
              <div>
                <p className="text-sm text-gray-500 dark:text-gray-400">{stat.label}</p>
                <p className="text-2xl font-bold mt-1">{stat.value}</p>
              </div>
              <div className={`p-2 rounded-lg ${stat.color}`}><stat.icon size={20} /></div>
            </div>
          </div>
        ))}
      </div>

      {charts && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div className="card p-4">
            <h3 className="font-semibold mb-4">Messages Per Day</h3>
            <ResponsiveContainer width="100%" height={250}>
              <LineChart data={charts.messages_per_day || []}>
                <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                <XAxis dataKey="day" tick={{ fontSize: 12 }} />
                <YAxis tick={{ fontSize: 12 }} />
                <Tooltip />
                <Line type="monotone" dataKey="count" stroke="#3b82f6" strokeWidth={2} />
              </LineChart>
            </ResponsiveContainer>
          </div>
          <div className="card p-4">
            <h3 className="font-semibold mb-4">Replies Per Day</h3>
            <ResponsiveContainer width="100%" height={250}>
              <LineChart data={charts.replies_per_day || []}>
                <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                <XAxis dataKey="day" tick={{ fontSize: 12 }} />
                <YAxis tick={{ fontSize: 12 }} />
                <Tooltip />
                <Line type="monotone" dataKey="count" stroke="#8b5cf6" strokeWidth={2} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {(s.lead_distribution && Object.keys(s.lead_distribution).length > 0) && (
        <div className="card p-4">
          <h3 className="font-semibold mb-4">Lead Status Distribution</h3>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={Object.entries(s.lead_distribution).map(([name, count]) => ({ name, count }))}>
              <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
              <XAxis dataKey="name" tick={{ fontSize: 11 }} />
              <YAxis tick={{ fontSize: 12 }} />
              <Tooltip />
              <Bar dataKey="count" fill="#3b82f6" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="card p-4">
          <h3 className="font-semibold mb-3">Recent Conversations</h3>
          {(s.recent_conversations || []).length === 0 ? (
            <p className="text-gray-500 text-sm py-4 text-center">No conversations yet</p>
          ) : (
            <div className="space-y-2">
              {(s.recent_conversations || []).slice(0, 10).map((conv: any) => (
                <div key={conv.id} className="flex items-center justify-between p-2 hover:bg-gray-50 dark:hover:bg-gray-700 rounded-lg">
                  <div>
                    <p className="font-medium text-sm">{conv.contact_name}</p>
                    <p className="text-xs text-gray-500 truncate max-w-[200px]">{conv.last_message_preview || "No messages"}</p>
                  </div>
                  {conv.unread_count > 0 && <span className="badge badge-blue">{conv.unread_count}</span>}
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="card p-4">
          <h3 className="font-semibold mb-3">Recent Campaigns</h3>
          {(s.recent_campaigns || []).length === 0 ? (
            <p className="text-gray-500 text-sm py-4 text-center">No campaigns yet</p>
          ) : (
            <div className="space-y-2">
              {(s.recent_campaigns || []).map((camp: any) => (
                <div key={camp.id} className="flex items-center justify-between p-2 hover:bg-gray-50 dark:hover:bg-gray-700 rounded-lg">
                  <div>
                    <p className="font-medium text-sm">{camp.name}</p>
                    <p className="text-xs text-gray-500">{camp.messages_sent} sent · {camp.replies} replies</p>
                  </div>
                  <span className={`badge ${
                    camp.status === "running" ? "badge-green" : camp.status === "completed" ? "badge-blue"
                    : camp.status === "failed" ? "badge-red" : "badge-gray"}`}>{camp.status}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
