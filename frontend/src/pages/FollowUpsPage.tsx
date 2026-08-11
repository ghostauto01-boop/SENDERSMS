import { useState, useEffect } from "react";
import api from "../api/client";
import { FollowUp } from "../types";
import toast from "react-hot-toast";
import { Clock, Send, SkipForward, Calendar, AlertTriangle } from "lucide-react";

export default function FollowUpsPage() {
  const [followups, setFollowups] = useState<FollowUp[]>([]);
  const [total, setTotal] = useState(0);
  const [view, setView] = useState("due_today");
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { loadFollowups(); }, [view, page]);

  const loadFollowups = async () => {
    try {
      setLoading(true);
      const { data } = await api.get("/followups/", {
        params: { view, page, per_page: 25 },
      });
      setFollowups(data.items);
      setTotal(data.total);
    } catch (err: any) {
      setError(err.response?.data?.detail || "Failed to load follow-ups");
    } finally {
      setLoading(false);
    }
  };

  const handleSendNow = async (id: number) => {
    try {
      await api.post(`/followups/${id}/send-now`);
      toast.success("Follow-up queued");
      loadFollowups();
    } catch {
      toast.error("Failed");
    }
  };

  const handleSkip = async (id: number) => {
    try {
      await api.post(`/followups/${id}/skip`);
      toast.success("Skipped");
      loadFollowups();
    } catch {
      toast.error("Failed");
    }
  };

  const views = [
    { value: "due_today", label: "Due Today", icon: Clock },
    { value: "overdue", label: "Overdue", icon: AlertTriangle },
    { value: "upcoming", label: "Upcoming", icon: Calendar },
    { value: "completed", label: "Completed", icon: Send },
    { value: "skipped", label: "Skipped", icon: SkipForward },
  ];

  if (error) {
    return (
      <div className="text-center py-12">
        <h2 className="text-xl font-semibold mb-2">Error</h2>
        <p className="text-gray-500 mb-4">{error}</p>
        <button onClick={loadFollowups} className="btn-primary">Retry</button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <h1 className="text-xl sm:text-2xl font-bold">Follow-ups</h1>

      <div className="flex gap-2 flex-wrap">
        {views.map((v) => (
          <button
            key={v.value}
            onClick={() => { setView(v.value); setPage(1); }}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
              view === v.value
                ? "bg-primary-100 dark:bg-primary-900/30 text-primary-700 dark:text-primary-300"
                : "text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700"
            }`}
          >
            <v.icon size={14} />
            {v.label}
          </button>
        ))}
      </div>

      <div className="card">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-gray-50 dark:bg-gray-700/50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Contact</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Scheduled</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Attempts</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
              {loading ? (
                [...Array(5)].map((_, i) => (
                  <tr key={i}>
                    {[...Array(5)].map((_, j) => (
                      <td key={j} className="px-4 py-3"><div className="skeleton h-4 w-full" /></td>
                    ))}
                  </tr>
                ))
              ) : followups.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-4 py-12 text-center text-gray-500">
                    No follow-ups in this view
                  </td>
                </tr>
              ) : (
                followups.map((f) => (
                  <tr key={f.id} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
                    <td className="px-4 py-3 font-medium text-sm">{f.contact_name}</td>
                    <td className="px-4 py-3 text-sm">
                      {f.scheduled_at ? new Date(f.scheduled_at).toLocaleString() : "—"}
                    </td>
                    <td className="px-4 py-3">
                      <span className={`badge ${
                        f.status === "sent" || f.status === "delivered" ? "badge-green" :
                        f.status === "failed" ? "badge-red" :
                        f.status === "skipped" || f.status === "cancelled" ? "badge-gray" :
                        "badge-yellow"
                      }`}>{f.status}</span>
                    </td>
                    <td className="px-4 py-3 text-sm">{f.attempt_count}</td>
                    <td className="px-4 py-3">
                      <div className="flex gap-1">
                        {(view === "due_today" || view === "overdue" || view === "upcoming") && (
                          <>
                            <button onClick={() => handleSendNow(f.id)} className="btn-primary btn-sm" title="Send Now">
                              <Send size={14} />
                            </button>
                            <button onClick={() => handleSkip(f.id)} className="btn-secondary btn-sm" title="Skip">
                              <SkipForward size={14} />
                            </button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
