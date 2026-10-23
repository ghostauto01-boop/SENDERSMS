import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useChannel } from "../hooks/useChannel";
import { useAuth } from "../hooks/useAuth";
import ChannelSwitch from "../components/ChannelSwitch";
import StatusPill from "../components/invoices/StatusPill";
import api from "../api/client";
import invoicesApi, { Invoice, InvoiceAnalytics } from "../api/invoices";
import { formatMoney, formatMoneyCompact } from "../utils/money";
import {
  Activity, AlertTriangle, ArrowRight, ArrowUpRight, CalendarClock, CheckCircle2,
  Clock3, MessageSquare, Megaphone, Plus, ReceiptText, Send, Sparkles, TrendingUp,
  Users, Wallet,
} from "lucide-react";

/**
 * The dashboard, in the 2026 language:
 *
 *  * a warm parchment surface with one white "device" card at the top;
 *  * one dark forest hero stat, calm white cards beside it;
 *  * a hand-built bar chart whose bars grow from the baseline;
 *  * a "team collaboration"-style activity card, here showing campaign pulse;
 *  * a revenue snapshot tied to the new invoice studio.
 *
 * Everything is real data. When the API is unavailable each block degrades on
 * its own instead of taking the page down.
 */

function AnimatedNumber({
  value, format, className = "",
}: {
  value: number;
  format?: (value: number) => string;
  className?: string;
}) {
  const [display, setDisplay] = useState(value);
  const fromRef = useRef(value);
  const startedRef = useRef(false);

  useEffect(() => {
    const reduce =
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (reduce) {
      setDisplay(value);
      fromRef.current = value;
      return;
    }
    const from = startedRef.current ? fromRef.current : 0;
    startedRef.current = true;
    const start = performance.now();
    const duration = 650;
    let frame = 0;
    const tick = (now: number) => {
      const progress = Math.min(1, (now - start) / duration);
      // easeOutExpo — fast start, soft landing
      const eased = progress === 1 ? 1 : 1 - Math.pow(2, -10 * progress);
      const next = from + (value - from) * eased;
      setDisplay(next);
      if (progress < 1) frame = requestAnimationFrame(tick);
      else fromRef.current = value;
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [value]);

  return <span className={className}>{format ? format(display) : Math.round(display).toLocaleString()}</span>;
}

function todayKey(offsetDays = 0) {
  const date = new Date(Date.now() + offsetDays * 86400000);
  return date.toISOString().slice(0, 10);
}

export default function DashboardPage() {
  const [stats, setStats] = useState<any>(null);
  const [charts, setCharts] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [invoiceStats, setInvoiceStats] = useState<InvoiceAnalytics | null>(null);
  const [recentInvoices, setRecentInvoices] = useState<Invoice[]>([]);
  const [invoiceCurrency, setInvoiceCurrency] = useState("NGN");
  const { channel } = useChannel();
  const { user } = useAuth();

  useEffect(() => {
    loadData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [channel]);

  useEffect(() => {
    let cancelled = false;
    invoicesApi
      .settings()
      .then((settings) => {
        if (cancelled) return;
        const code = settings.default_currency || "NGN";
        setInvoiceCurrency(code);
        return Promise.all([
          invoicesApi.analytics(code).then((data) => !cancelled && setInvoiceStats(data)),
          invoicesApi
            .list({ currency: code, per_page: 4 })
            .then((data) => !cancelled && setRecentInvoices(data.items)),
        ]).then(() => undefined);
      })
      .catch(() => {
        /* an invoice API hiccup must not blank the dashboard */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const loadData = async () => {
    try {
      setLoading(true);
      setError(null);
      const [sr, cr] = await Promise.allSettled([
        api.get("/dashboard/stats", { params: { channel } }),
        api.get("/dashboard/charts", { params: { days: 30, channel } }),
      ]);
      if (sr.status === "fulfilled") setStats(sr.value.data);
      if (cr.status === "fulfilled") setCharts(cr.value.data);
      if (sr.status === "rejected" && cr.status === "rejected") {
        setError("Could not reach the dashboard API.");
      }
    } finally {
      setLoading(false);
    }
  };

  const s = stats || {};
  const invoiceSummary = invoiceStats?.summary;

  const weekly = useMemo(() => {
    const rows = (charts?.messages_per_day || []) as { day: string; count: number }[];
    const byDay = new Map(rows.map((row) => [String(row.day).slice(0, 10), Number(row.count) || 0]));
    return Array.from({ length: 7 }, (_, index) => {
      const key = todayKey(index - 6);
      const date = new Date(`${key}T12:00:00Z`);
      return {
        key,
        label: date.toLocaleDateString(undefined, { weekday: "short" }),
        count: byDay.get(key) || 0,
      };
    });
  }, [charts]);

  const weeklyMax = Math.max(...weekly.map((day) => day.count), 1);
  const weeklyTotal = weekly.reduce((sum, day) => sum + day.count, 0);
  const weeklyChange = useMemo(() => {
    if (!weekly.length) return 0;
    const firstHalf = weekly.slice(0, 3).reduce((sum, day) => sum + day.count, 0);
    const lastHalf = weekly.slice(4).reduce((sum, day) => sum + day.count, 0);
    if (!firstHalf) return lastHalf ? 100 : 0;
    return Math.round(((lastHalf - firstHalf) / firstHalf) * 100);
  }, [weekly]);

  const deliveryRate = Number(s.delivery_rate || 0);
  const displayName = user?.display_name || user?.username || "Administrator";
  const initials = displayName.slice(0, 1).toUpperCase();
  const today = new Date().toLocaleDateString(undefined, {
    weekday: "long", day: "numeric", month: "long",
  });

  if (loading && !stats) {
    return (
      <div className="space-y-5">
        <div className="card p-3"><div className="skeleton h-10 w-56 rounded-full" /></div>
        <div className="skeleton h-10 w-64" />
        <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
          {[...Array(4)].map((_, i) => <div key={i} className="skeleton h-40 rounded-3xl" />)}
        </div>
        <div className="grid xl:grid-cols-3 gap-4">
          <div className="skeleton h-80 rounded-3xl xl:col-span-2" />
          <div className="skeleton h-80 rounded-3xl" />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-5 pb-4 max-w-[1500px]">
      {/* The account card, exactly like the reference's floating profile pill */}
      <div className="card px-3.5 py-2.5 flex items-center gap-3 animate-fade-up">
        <span className="avatar w-10 h-10 text-sm brand-gradient text-white ring-white/70">{initials}</span>
        <div className="min-w-0">
          <p className="text-[13.5px] font-semibold truncate">{displayName}</p>
          <p className="text-[10.5px] text-gray-400 truncate">
            Operator account · {today}
          </p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <span className={`badge ${s.gateway_status === "healthy" ? "badge-green" : "badge-gray"} hidden sm:inline-flex`}>
            <span className={`w-1.5 h-1.5 rounded-full ${s.gateway_status === "healthy" ? "bg-success-600" : "bg-gray-400"}`} />
            Gateway {s.gateway_status || "unknown"}
          </span>
          <Link to="/settings" className="icon-btn w-9 h-9" aria-label="Open settings">
            <ArrowUpRight size={15} />
          </Link>
        </div>
      </div>

      {/* Heading */}
      <div className="flex flex-col sm:flex-row sm:items-end sm:justify-between gap-3">
        <div>
          <h1 className="page-title">Dashboard</h1>
          <p className="page-subtitle">Plan, prioritise, and grow every conversation with ease.</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="hidden md:block"><ChannelSwitch size="sm" /></div>
          <Link to="/invoices" className="btn-primary">
            <ReceiptText size={15} />
            <span className="hidden xs:inline">New invoice</span>
          </Link>
        </div>
      </div>

      {error && (
        <div className="rounded-2xl bg-danger-50 dark:bg-danger-950/40 px-4 py-3 text-sm text-danger-700 dark:text-danger-300 flex items-center gap-2">
          <AlertTriangle size={16} /> {error}
          <button onClick={loadData} className="ml-auto font-semibold underline">Retry</button>
        </div>
      )}

      {/* Hero stats */}
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-3 sm:gap-4 stagger">
        <Link
          to="/contacts"
          className="stat-hero col-span-2 xl:col-span-1 p-5 group animate-fade-up min-h-[164px] flex flex-col"
        >
          <div className="relative z-10 flex flex-col h-full">
            <div className="flex items-start justify-between">
              <div>
                <p className="text-[12px] text-white/65 font-medium">Total contacts</p>
                <p className="text-[10px] text-white/40 mt-0.5">Your whole audience</p>
              </div>
              <span className="glass-circle"><ArrowUpRight size={15} /></span>
            </div>
            <p className="stat-value mt-auto pt-6">
              <AnimatedNumber value={Number(s.total_contacts || 0)} />
            </p>
            <p className="text-[10.5px] text-white/55 mt-2 flex items-center gap-1.5">
              <TrendingUp size={11} /> {Number(s.contacts_contacted || 0).toLocaleString()} contacted so far
            </p>
          </div>
        </Link>

        <div className="card p-5 min-h-[164px] flex flex-col animate-fade-up">
          <div className="flex items-start justify-between">
            <div>
              <p className="text-[12px] text-gray-500 font-medium">Messages sent</p>
              <p className="text-[10px] text-gray-400 mt-0.5">Across SMS & email</p>
            </div>
            <span className="w-9 h-9 rounded-full bg-primary-50 dark:bg-primary-950/50 text-primary-800 dark:text-primary-300 flex items-center justify-center">
              <Send size={15} />
            </span>
          </div>
          <p className="stat-value text-gray-900 dark:text-white mt-auto pt-6">
            <AnimatedNumber value={Number(s.messages_sent || 0)} />
          </p>
          <p className="text-[10.5px] text-gray-400 mt-2 flex items-center gap-1.5">
            <CheckCircle2 size={11} className="text-success-600" /> {deliveryRate}% delivered
          </p>
        </div>

        <Link to="/sms-manager" className="card card-hover p-5 min-h-[164px] flex flex-col animate-fade-up">
          <div className="flex items-start justify-between">
            <div>
              <p className="text-[12px] text-gray-500 font-medium">Active campaigns</p>
              <p className="text-[10px] text-gray-400 mt-0.5">Sending right now</p>
            </div>
            <span className="w-9 h-9 rounded-full bg-accent-50 dark:bg-accent-950/50 text-accent-800 dark:text-accent-300 flex items-center justify-center">
              <Megaphone size={15} />
            </span>
          </div>
          <p className="stat-value text-gray-900 dark:text-white mt-auto pt-6">
            <AnimatedNumber value={Number(s.active_campaigns || 0)} />
          </p>
          <p className="text-[10.5px] text-gray-400 mt-2 flex items-center gap-1.5">
            <Activity size={11} className="text-accent-600" /> {Number(s.completed_campaigns || 0)} completed
          </p>
        </Link>

        <Link to="/invoices" className="card card-hover p-5 min-h-[164px] flex flex-col animate-fade-up">
          <div className="flex items-start justify-between">
            <div>
              <p className="text-[12px] text-gray-500 font-medium">Revenue collected</p>
              <p className="text-[10px] text-gray-400 mt-0.5">Paid invoices</p>
            </div>
            <span className="w-9 h-9 rounded-full bg-success-50 dark:bg-success-950/50 text-success-700 dark:text-success-300 flex items-center justify-center">
              <Wallet size={15} />
            </span>
          </div>
          <p className="stat-value text-gray-900 dark:text-white mt-auto pt-6">
            <AnimatedNumber
              value={Number(invoiceSummary?.paid_minor || 0)}
              format={(value) => formatMoneyCompact(value, invoiceCurrency)}
            />
          </p>
          <p className="text-[10.5px] text-gray-400 mt-2 flex items-center gap-1.5">
            <Clock3 size={11} /> {formatMoneyCompact(invoiceSummary?.outstanding_minor || 0, invoiceCurrency)} outstanding
          </p>
        </Link>
      </div>

      {/* Chart + campaign pulse */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <div className="card p-4 sm:p-5 xl:col-span-2 animate-fade-up">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold">Message analytics</h2>
              <p className="text-[11px] text-gray-400 mt-0.5">
                {weeklyTotal.toLocaleString()} messages this week
                {weeklyChange !== 0 && (
                  <span className={weeklyChange > 0 ? "text-success-600" : "text-danger-500"}>
                    {" "}· {weeklyChange > 0 ? "+" : ""}{weeklyChange}%
                  </span>
                )}
              </p>
            </div>
            <span className="rounded-full bg-success-100 dark:bg-success-950/60 text-success-700 dark:text-success-300 text-[10px] font-semibold px-2.5 py-1">
              {deliveryRate}% delivered
            </span>
          </div>

          <div className="mt-6 flex items-end gap-2 sm:gap-4 h-52">
            {weekly.map((day, index) => {
              const height = day.count ? Math.max(8, (day.count / weeklyMax) * 100) : 0;
              const isToday = index === weekly.length - 1;
              return (
                <div key={day.key} className="flex-1 min-w-0 flex flex-col items-center gap-2" title={`${day.label}: ${day.count} message${day.count === 1 ? "" : "s"}`}>
                  <div className="flex items-end justify-center h-40 w-full">
                    {height ? (
                      <div
                        className={`bar-grow animate-bar-grow w-[70%] max-w-[44px] rounded-full relative ${
                          isToday
                            ? "bg-gradient-to-t from-primary-950 via-primary-800 to-primary-600 shadow-lg shadow-primary-900/20"
                            : "bg-gradient-to-t from-primary-900 to-primary-700/90"
                        }`}
                        style={{ height: `${height}%`, animationDelay: `${index * 60}ms` }}
                      >
                        {isToday && (
                          <span className="absolute -top-5 left-1/2 -translate-x-1/2 text-[9px] font-semibold text-primary-800 dark:text-primary-300 whitespace-nowrap">
                            today
                          </span>
                        )}
                      </div>
                    ) : (
                      <div className="hatched-bar w-[70%] max-w-[44px] h-3" />
                    )}
                  </div>
                  <span className={`text-[10px] ${isToday ? "font-semibold text-gray-700 dark:text-gray-200" : "text-gray-400"}`}>
                    {day.label}
                  </span>
                </div>
              );
            })}
          </div>
        </div>

        {/* The reference's "Team Collaboration" card → campaign pulse */}
        <div className="card p-4 sm:p-5 animate-fade-up animate-delay-75">
          <div className="flex items-center justify-between gap-2 mb-4">
            <div>
              <h2 className="text-sm font-semibold">Campaign pulse</h2>
              <p className="text-[11px] text-gray-400 mt-0.5">Your most recent outreach</p>
            </div>
            <Link to="/sms-manager" className="btn-secondary btn-sm rounded-full">
              <Plus size={12} /> New
            </Link>
          </div>

          {(s.recent_campaigns || []).length === 0 ? (
            <div className="py-10 text-center">
              <span className="w-11 h-11 rounded-full bg-primary-50 dark:bg-primary-950/50 text-primary-800 dark:text-primary-300 flex items-center justify-center mx-auto mb-3">
                <Sparkles size={19} />
              </span>
              <p className="text-xs font-medium">No campaigns yet</p>
              <p className="text-[10.5px] text-gray-400 mt-1">
                Start one in SMS Manager or Email Manager.
              </p>
              <Link to="/sms-manager" className="btn-primary btn-sm mt-3">Create a campaign</Link>
            </div>
          ) : (
            <div className="space-y-2.5">
              {(s.recent_campaigns || []).slice(0, 4).map((campaign: any, index: number) => (
                <Link
                  to="/sms-manager"
                  key={campaign.id ?? index}
                  className="flex items-center gap-3 rounded-2xl px-2 py-2 -mx-2 hover:bg-gray-50 dark:hover:bg-gray-900/30 transition-colors"
                >
                  <span
                    className={`w-9 h-9 rounded-full flex items-center justify-center text-[10px] font-semibold shrink-0 ${
                      ["bg-accent-100 text-accent-800", "bg-primary-100 text-primary-900", "bg-warning-100 text-warning-700", "bg-danger-100 text-danger-700"][index % 4]
                    }`}
                  >
                    {String(campaign.name || "C").slice(0, 2).toUpperCase()}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-[12px] font-semibold truncate">{campaign.name}</p>
                    <p className="text-[10px] text-gray-400 truncate">
                      {campaign.messages_sent ?? 0} sent · {campaign.replies ?? 0} replies
                    </p>
                  </div>
                  <span className={`badge shrink-0 ${
                    campaign.status === "running" || campaign.status === "active"
                      ? "badge-green"
                      : campaign.status === "completed"
                        ? "badge-blue"
                        : campaign.status === "paused"
                          ? "badge-yellow"
                          : "badge-gray"
                  }`}>
                    {campaign.status}
                  </span>
                </Link>
              ))}
              <Link to="/sms-manager" className="flex items-center justify-center gap-1 text-[11px] font-semibold text-primary-800 dark:text-primary-300 pt-2">
                View all campaigns <ArrowRight size={12} />
              </Link>
            </div>
          )}
        </div>
      </div>

      {/* Revenue + conversations + pipeline */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="card p-4 sm:p-5 animate-fade-up">
          <div className="flex items-center justify-between gap-2 mb-3">
            <h2 className="text-sm font-semibold flex items-center gap-2">
              <ReceiptText size={15} /> Recent invoices
            </h2>
            <Link to="/invoices" className="text-[11px] font-semibold text-primary-800 dark:text-primary-300">All</Link>
          </div>
          {recentInvoices.length === 0 ? (
            <div className="py-8 text-center">
              <p className="text-xs text-gray-400">No invoices yet.</p>
              <Link to="/invoices" className="btn-secondary btn-sm mt-3">Create your first</Link>
            </div>
          ) : (
            <div className="space-y-2">
              {recentInvoices.map((invoice) => (
                <Link
                  key={invoice.id}
                  to={`/invoices?invoice=${invoice.id}`}
                  className="flex items-center justify-between gap-2 rounded-xl px-2 py-2 -mx-2 hover:bg-gray-50 dark:hover:bg-gray-900/30 transition-colors"
                >
                  <div className="min-w-0">
                    <p className="text-[12px] font-medium truncate">{invoice.customer.name}</p>
                    <p className="text-[10px] text-gray-400">{invoice.number}</p>
                  </div>
                  <div className="text-right shrink-0">
                    <p className="text-[12px] font-semibold tabular-nums">{formatMoney(invoice.total_minor, invoice.currency)}</p>
                    <StatusPill status={invoice.status} dot={false} className="mt-0.5" />
                  </div>
                </Link>
              ))}
            </div>
          )}

          <div className="mt-4 rounded-2xl bg-primary-950 dark:bg-primary-900/50 text-white p-3.5">
            <div className="flex items-center justify-between text-[10px] text-white/55">
              <span>Collected</span>
              <span>Outstanding</span>
            </div>
            <div className="flex items-center justify-between mt-1">
              <span className="text-sm font-semibold">{formatMoneyCompact(invoiceSummary?.paid_minor || 0, invoiceCurrency)}</span>
              <span className="text-sm font-semibold text-warning-200">{formatMoneyCompact(invoiceSummary?.outstanding_minor || 0, invoiceCurrency)}</span>
            </div>
          </div>
        </div>

        <div className="card p-4 sm:p-5 animate-fade-up animate-delay-75">
          <div className="flex items-center justify-between gap-2 mb-3">
            <h2 className="text-sm font-semibold flex items-center gap-2">
              <MessageSquare size={15} /> Recent conversations
            </h2>
            <Link to="/sms-inbox" className="text-[11px] font-semibold text-primary-800 dark:text-primary-300">Inbox</Link>
          </div>
          {(s.recent_conversations || []).length === 0 ? (
            <div className="py-8 text-center">
              <p className="text-xs text-gray-400">No conversations yet. Replies will land here.</p>
            </div>
          ) : (
            <div className="space-y-1.5">
              {(s.recent_conversations || []).slice(0, 4).map((conversation: any) => (
                <Link
                  key={conversation.id}
                  to={`/sms-inbox?conversation_id=${conversation.id}`}
                  className="flex items-center gap-2.5 rounded-xl px-2 py-2 -mx-2 hover:bg-gray-50 dark:hover:bg-gray-900/30 transition-colors"
                >
                  <span className="w-8 h-8 rounded-full bg-primary-50 dark:bg-primary-950/50 text-primary-900 dark:text-primary-200 text-[10px] font-semibold flex items-center justify-center shrink-0">
                    {String(conversation.contact_name || "?").slice(0, 1).toUpperCase()}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-[12px] font-medium truncate">{conversation.contact_name || "Unknown"}</p>
                    <p className="text-[10px] text-gray-400 truncate">{conversation.last_message_preview || "No messages"}</p>
                  </div>
                  {conversation.unread_count > 0 && (
                    <span className="nav-badge bg-primary-900">{conversation.unread_count}</span>
                  )}
                </Link>
              ))}
            </div>
          )}
        </div>

        <div className="card p-4 sm:p-5 animate-fade-up animate-delay-150">
          <div className="flex items-center justify-between gap-2 mb-3">
            <h2 className="text-sm font-semibold flex items-center gap-2">
              <Users size={15} /> Pipeline
            </h2>
            <Link to="/contacts" className="text-[11px] font-semibold text-primary-800 dark:text-primary-300">Contacts</Link>
          </div>
          {!s.lead_distribution || Object.keys(s.lead_distribution).length === 0 ? (
            <div className="py-8 text-center">
              <p className="text-xs text-gray-400">Import contacts to see your pipeline.</p>
            </div>
          ) : (
            <div className="space-y-2.5">
              {Object.entries(s.lead_distribution as Record<string, number>)
                .sort(([, a], [, b]) => Number(b) - Number(a))
                .slice(0, 5)
                .map(([label, count], index) => {
                  const total = Object.values(s.lead_distribution as Record<string, number>).reduce(
                    (sum, value) => sum + Number(value), 0
                  ) || 1;
                  return (
                    <div key={label}>
                      <div className="flex items-center justify-between text-[11px] mb-1">
                        <span className="capitalize text-gray-600 dark:text-gray-300">{label.replace(/_/g, " ")}</span>
                        <span className="font-semibold tabular-nums">{count}</span>
                      </div>
                      <div className="h-1.5 rounded-full bg-gray-100 dark:bg-gray-700/60 overflow-hidden">
                        <div
                          className={`h-full rounded-full ${
                            ["bg-primary-800", "bg-accent-400", "bg-success-500", "bg-warning-400", "bg-danger-400"][index % 5]
                          } transition-all duration-700 ease-gentle`}
                          style={{ width: `${Math.max(4, (Number(count) / total) * 100)}%` }}
                        />
                      </div>
                    </div>
                  );
                })}
            </div>
          )}
          <div className="mt-4 flex items-center gap-2 text-[10.5px] text-gray-400">
            <CalendarClock size={12} />
            {Number(s.followups_due_today || 0)} follow-up{Number(s.followups_due_today || 0) === 1 ? "" : "s"} due today
            {Number(s.overdue_followups || 0) > 0 && (
              <span className="text-danger-500 font-medium">· {s.overdue_followups} overdue</span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
