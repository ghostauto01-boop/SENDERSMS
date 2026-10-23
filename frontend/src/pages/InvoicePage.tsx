import { useCallback, useEffect, useMemo, useState } from "react";
import { useLocation } from "react-router-dom";
import toast from "react-hot-toast";
import {
  AlertCircle, ArrowUpRight, Banknote, CalendarClock, CheckCircle2, ChevronLeft,
  ChevronRight, CircleDollarSign, CreditCard, FileText, Loader2, Plus, ReceiptText,
  RefreshCw, Search, Settings2, TrendingUp, Users, Wallet, X, BarChart3, Clock3,
} from "lucide-react";
import invoicesApi, {
  Invoice, InvoiceAnalytics, InvoiceSettings, InvoiceSummary, PaymentsHistory,
} from "../api/invoices";
import InvoiceEditor from "../components/invoices/InvoiceEditor";
import InvoiceDetail from "../components/invoices/InvoiceDetail";
import PaystackSettingsCard from "../components/invoices/PaystackSettingsCard";
import StatusPill from "../components/invoices/StatusPill";
import { useVisiblePolling } from "../hooks/useVisiblePolling";
import {
  CURRENCY_CODES, formatDate, formatDateTime, formatMoney, formatMoneyCompact,
} from "../utils/money";

/**
 * INVOICE STUDIO
 *
 * One nav destination, four jobs:
 *  1. Invoices — the list, the money at a glance, and a full editor/detail view;
 *  2. Analytics — revenue in vs collected, customers, ageing;
 *  3. Payments — every successful collection, from Paystack or recorded by hand;
 *  4. Settings — the business identity and Paystack keys (also in Settings).
 *
 * The design follows the 2026 workspace: a dark forest hero card for the number
 * that matters most, white cards with generous radii for the rest, and a chart
 * whose bars grow into place.
 */

type Tab = "invoices" | "analytics" | "payments";

const STATUS_FILTERS: { value: string; label: string }[] = [
  { value: "", label: "All" },
  { value: "draft", label: "Draft" },
  { value: "sent", label: "Sent" },
  { value: "partial", label: "Part paid" },
  { value: "overdue", label: "Overdue" },
  { value: "paid", label: "Paid" },
  { value: "void", label: "Void" },
];

function initials(name: string) {
  return (name || "?")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
}

export default function InvoicePage() {
  const [tab, setTab] = useState<Tab>("invoices");
  const [settings, setSettings] = useState<InvoiceSettings | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);

  const [items, setItems] = useState<Invoice[]>([]);
  const [total, setTotal] = useState(0);
  const [summary, setSummary] = useState<InvoiceSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("");
  const [currency, setCurrency] = useState("");
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const perPage = 20;

  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing] = useState<Invoice | null>(null);
  // `?invoice=<id>` opens one invoice straight away — that is how the dashboard,
  // notifications and copied links deep-link into the studio. Read through the
  // router so a search-only navigation (already on this page) still works.
  const location = useLocation();
  const linkedInvoiceId = useMemo(() => {
    const parsed = Number(new URLSearchParams(location.search).get("invoice"));
    return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
  }, [location.search]);
  const [openId, setOpenId] = useState<number | null>(linkedInvoiceId);
  useEffect(() => {
    if (linkedInvoiceId !== null) setOpenId(linkedInvoiceId);
  }, [linkedInvoiceId]);

  const [analytics, setAnalytics] = useState<InvoiceAnalytics | null>(null);
  const [analyticsLoading, setAnalyticsLoading] = useState(false);
  const [payments, setPayments] = useState<PaymentsHistory | null>(null);
  const [paymentsLoading, setPaymentsLoading] = useState(false);
  const [paymentsPage, setPaymentsPage] = useState(1);

  // Settings first: the default currency drives the list and the summary.
  useEffect(() => {
    invoicesApi
      .settings()
      .then((data) => {
        setSettings(data);
        setCurrency((current) => current || data.default_currency || "NGN");
      })
      .catch(() => toast.error("Could not load invoice settings"));
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(query.trim()), 320);
    return () => clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    setPage(1);
  }, [status, currency, debouncedQuery]);

  const loadList = useCallback(async () => {
    setLoading(true);
    setListError(null);
    try {
      const data = await invoicesApi.list({
        page,
        per_page: perPage,
        status: status || undefined,
        currency: currency || undefined,
        q: debouncedQuery || undefined,
      });
      setItems(data.items);
      setTotal(data.total);
      setSummary(data.summary);
    } catch (err: any) {
      setListError(err?.response?.data?.detail || "Could not load invoices");
    } finally {
      setLoading(false);
    }
  }, [page, status, currency, debouncedQuery]);

  useEffect(() => {
    loadList();
  }, [loadList]);

  const loadAnalytics = useCallback(async () => {
    if (!currency) return;
    setAnalyticsLoading(true);
    try {
      const data = await invoicesApi.analytics(currency);
      setAnalytics(data);
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || "Could not load invoice analytics");
    } finally {
      setAnalyticsLoading(false);
    }
  }, [currency]);

  useEffect(() => {
    if (tab === "analytics" && currency) loadAnalytics();
  }, [tab, loadAnalytics, currency]);

  const loadPayments = useCallback(async () => {
    setPaymentsLoading(true);
    try {
      const data = await invoicesApi.allPayments({
        page: paymentsPage,
        per_page: 20,
        currency: currency || undefined,
      });
      setPayments(data);
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || "Could not load payment history");
    } finally {
      setPaymentsLoading(false);
    }
  }, [paymentsPage, currency]);

  useEffect(() => {
    if (tab === "payments") loadPayments();
  }, [tab, loadPayments]);

  useEffect(() => {
    setPaymentsPage(1);
  }, [currency]);

  // A running studio should not need a refresh click. Visible-tab polling keeps
  // Paystack confirmations appearing on their own.
  useVisiblePolling(() => {
    if (tab === "invoices") loadList();
    if (tab === "analytics") loadAnalytics();
    if (tab === "payments") loadPayments();
  }, 60000);

  const refresh = () => {
    loadList();
    if (tab === "analytics") loadAnalytics();
    if (tab === "payments") loadPayments();
  };

  const openNew = () => {
    setEditing(null);
    setEditorOpen(true);
  };

  const handleSaved = (invoice: Invoice, message?: string) => {
    setEditorOpen(false);
    setEditing(null);
    setOpenId(invoice.id);
    loadList();
    if (message) toast.success(message);
  };

  const handleChanged = (invoice?: Invoice) => {
    loadList();
    if (tab === "analytics") loadAnalytics();
    if (tab === "payments") loadPayments();
    if (invoice) {
      setItems((current) => current.map((row) => (row.id === invoice.id ? invoice : row)));
    }
  };

  const pages = Math.max(1, Math.ceil(total / perPage));
  const rangeStart = total ? (page - 1) * perPage + 1 : 0;
  const rangeEnd = Math.min(page * perPage, total);
  const paystackReady = settings?.paystack.configured;

  return (
    <div className="space-y-5 pb-4">
      {/* Heading */}
      <div className="flex flex-col sm:flex-row sm:items-end sm:justify-between gap-3">
        <div>
          <p className="eyebrow flex items-center gap-1.5">
            <ReceiptText size={12} /> Studio · Payments
          </p>
          <h1 className="page-title mt-1.5">Invoices</h1>
          <p className="page-subtitle">
            Write an invoice in Naira or dollars, send it, and get paid through Paystack.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => setSettingsOpen(true)} className="btn-secondary">
            <Settings2 size={15} />
            <span className="hidden xs:inline">Paystack</span>
            <span className={`w-1.5 h-1.5 rounded-full ${paystackReady ? "bg-success-500" : "bg-warning-500"}`} />
          </button>
          <button onClick={openNew} className="btn-primary">
            <Plus size={16} /> New invoice
          </button>
        </div>
      </div>

      {!paystackReady && settings && (
        <div className="rounded-2xl border border-warning-200/70 bg-warning-50/80 dark:bg-warning-950/30 dark:border-warning-900/50 px-4 py-3 flex flex-col sm:flex-row sm:items-center gap-2 animate-fade-up">
          <div className="flex items-start gap-2.5 flex-1">
            <AlertCircle size={16} className="text-warning-600 mt-0.5 shrink-0" />
            <p className="text-xs text-warning-800 dark:text-warning-300">
              <span className="font-semibold">Paystack is not connected.</span> You can still create
              and send invoices; connect a key to collect card, transfer and USSD payments online.
            </p>
          </div>
          <button onClick={() => setSettingsOpen(true)} className="btn-secondary btn-sm shrink-0 self-start sm:self-auto">
            Connect Paystack
          </button>
        </div>
      )}

      {/* Tabs */}
      <div className="segment w-full sm:w-auto">
        {([
          ["invoices", "Invoices", <ReceiptText key="i" size={13} />],
          ["analytics", "Analytics", <BarChart3 key="i" size={13} />],
          ["payments", "Payments", <CreditCard key="i" size={13} />],
        ] as [Tab, string, JSX.Element][]).map(([key, label, icon]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`segment-item flex-1 sm:flex-none inline-flex items-center justify-center gap-1.5 ${
              tab === key ? "segment-item-active" : ""
            }`}
          >
            {icon}
            {label}
          </button>
        ))}
      </div>

      {tab === "invoices" && (
        <>
          {/* Money at a glance */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4 stagger">
            <div className="stat-hero col-span-2 lg:col-span-1 p-5 group animate-fade-up">
              <div className="relative z-10 flex flex-col h-full min-h-[132px]">
                <div className="flex items-start justify-between">
                  <span className="text-[12px] text-white/65 font-medium">Outstanding</span>
                  <span className="glass-circle">
                    <ArrowUpRight size={15} />
                  </span>
                </div>
                <p className="stat-value mt-auto pt-6">
                  {summary ? formatMoney(summary.outstanding_minor, currency || "NGN") : "—"}
                </p>
                <p className="text-[10.5px] text-white/55 mt-2 flex items-center gap-1.5">
                  <TrendingUp size={11} />
                  {summary
                    ? `${summary.invoice_count - summary.paid_count - summary.draft_count - (summary.by_status.void || 0)} open invoice${
                        summary.invoice_count === 1 ? "" : "s"
                      }${summary.overdue_minor ? ` · ${formatMoneyCompact(summary.overdue_minor, currency)} overdue` : ""}`
                    : "Across every open invoice"}
                </p>
              </div>
            </div>

            <MiniStat
              icon={<Wallet size={15} />}
              tone="success"
              label="Collected"
              value={summary ? formatMoney(summary.paid_minor, currency || "NGN") : "—"}
              hint={summary ? `${summary.paid_count} paid invoice${summary.paid_count === 1 ? "" : "s"}` : ""}
            />
            <MiniStat
              icon={<Clock3 size={15} />}
              tone="danger"
              label="Overdue"
              value={summary ? formatMoney(summary.overdue_minor, currency || "NGN") : "—"}
              hint={summary ? `${summary.overdue_count} needing a nudge` : ""}
              onClick={() => setStatus("overdue")}
            />
            <MiniStat
              icon={<FileText size={15} />}
              tone="neutral"
              label="Drafts"
              value={summary ? formatMoney(summary.draft_minor, currency || "NGN") : "—"}
              hint={summary ? `${summary.draft_count} not sent yet` : ""}
              onClick={() => setStatus("draft")}
            />
          </div>

          {/* Filter + list */}
          <div className="card overflow-hidden">
            <div className="p-4 sm:p-5 border-b border-gray-100 dark:border-gray-700/60 space-y-3">
              <div className="flex flex-wrap items-center gap-2 justify-between">
                <h2 className="text-sm font-semibold">All invoices</h2>
                <div className="flex items-center gap-2">
                  <div className="search-pill py-2 w-48 sm:w-64">
                    <Search size={14} className="text-gray-400" />
                    <input
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                      placeholder="Search number or customer"
                      aria-label="Search invoices"
                    />
                  </div>
                  <div className="relative">
                    <select
                      className="input py-2 pr-8 pl-3 text-xs appearance-none w-[92px]"
                      value={currency}
                      onChange={(e) => setCurrency(e.target.value)}
                      aria-label="Currency filter"
                    >
                      <option value="">All</option>
                      {CURRENCY_CODES.map((code) => (
                        <option key={code} value={code}>{code}</option>
                      ))}
                    </select>
                    <ChevronRight size={12} className="absolute right-2.5 top-1/2 -translate-y-1/2 rotate-90 text-gray-400 pointer-events-none" />
                  </div>
                  <button onClick={refresh} className="icon-btn" aria-label="Refresh invoices" title="Refresh">
                    <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
                  </button>
                </div>
              </div>
              <div className="flex gap-1.5 overflow-x-auto scrollbar-none -mx-1 px-1">
                {STATUS_FILTERS.map((filter) => (
                  <button
                    key={filter.value}
                    onClick={() => setStatus(filter.value)}
                    className={`chip shrink-0 ${status === filter.value ? "chip-active" : ""}`}
                  >
                    {filter.label}
                  </button>
                ))}
              </div>
            </div>

            {loading ? (
              <div className="p-4 space-y-2">
                {[...Array(5)].map((_, index) => (
                  <div key={index} className="skeleton h-[68px] w-full rounded-2xl" />
                ))}
              </div>
            ) : listError ? (
              <div className="py-14 text-center">
                <AlertCircle size={30} className="mx-auto text-danger-500 mb-3" />
                <p className="text-sm font-medium">{listError}</p>
                <button onClick={refresh} className="btn-secondary btn-sm mt-3">Try again</button>
              </div>
            ) : items.length === 0 ? (
              <div className="py-16 px-5 text-center">
                <span className="w-14 h-14 rounded-full bg-primary-50 dark:bg-primary-950/50 text-primary-800 dark:text-primary-300 flex items-center justify-center mx-auto mb-4">
                  <ReceiptText size={24} />
                </span>
                <p className="text-base font-semibold">
                  {debouncedQuery || status ? "No invoices match this view" : "Your first invoice starts here"}
                </p>
                <p className="text-xs text-gray-500 mt-1 max-w-sm mx-auto">
                  {debouncedQuery || status
                    ? "Try a different status or clear the search."
                    : "Bill a client, send them a payment link, and watch Paystack confirm the money here."}
                </p>
                {!debouncedQuery && !status && (
                  <button onClick={openNew} className="btn-primary mt-4">
                    <Plus size={15} /> Create an invoice
                  </button>
                )}
              </div>
            ) : (
              <div className="divide-y divide-gray-100 dark:divide-gray-700/60 stagger">
                {items.map((invoice) => (
                  <InvoiceRow
                    key={invoice.id}
                    invoice={invoice}
                    onOpen={() => setOpenId(invoice.id)}
                  />
                ))}
              </div>
            )}

            {total > perPage && (
              <div className="flex items-center justify-between gap-3 px-4 sm:px-5 py-3 border-t border-gray-100 dark:border-gray-700/60">
                <p className="text-[11px] text-gray-400">
                  Showing {rangeStart}–{rangeEnd} of {total}
                </p>
                <div className="flex items-center gap-1">
                  <button
                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                    disabled={page <= 1}
                    className="icon-btn w-8 h-8"
                    aria-label="Previous page"
                  >
                    <ChevronLeft size={14} />
                  </button>
                  <span className="text-[11px] text-gray-500 px-1">{page} / {pages}</span>
                  <button
                    onClick={() => setPage((p) => Math.min(pages, p + 1))}
                    disabled={page >= pages}
                    className="icon-btn w-8 h-8"
                    aria-label="Next page"
                  >
                    <ChevronRight size={14} />
                  </button>
                </div>
              </div>
            )}
          </div>
        </>
      )}

      {tab === "analytics" && (
        <AnalyticsTab
          analytics={analytics}
          loading={analyticsLoading}
          currency={currency}
          onCurrency={setCurrency}
          onOpenSettings={() => setSettingsOpen(true)}
        />
      )}

      {tab === "payments" && (
        <PaymentsTab
          data={payments}
          loading={paymentsLoading}
          currency={currency}
          onCurrency={setCurrency}
          page={paymentsPage}
          onPage={setPaymentsPage}
          onOpen={(id) => setOpenId(id)}
        />
      )}

      {editorOpen && (
        <InvoiceEditor
          invoice={editing}
          settings={settings}
          onClose={() => {
            setEditorOpen(false);
            setEditing(null);
          }}
          onSaved={handleSaved}
        />
      )}

      {openId !== null && (
        <InvoiceDetail
          invoiceId={openId}
          onClose={() => setOpenId(null)}
          onChanged={handleChanged}
          onDeleted={() => {
            setOpenId(null);
            loadList();
          }}
          onEdit={(invoice) => {
            setOpenId(null);
            setEditing(invoice);
            setEditorOpen(true);
          }}
        />
      )}

      {settingsOpen && (
        <div className="fixed inset-0 z-[75] flex items-end sm:items-center justify-center bg-primary-950/40 backdrop-blur-sm sm:p-5">
          <div className="bg-white dark:bg-gray-900 w-full sm:max-w-3xl h-[95dvh] sm:h-auto sm:max-h-[92vh] rounded-t-[28px] sm:rounded-[28px] shadow-pop overflow-y-auto animate-sheet-in">
            <div className="sticky top-0 z-10 flex items-center justify-between px-4 sm:px-6 py-4 bg-white/95 dark:bg-gray-900/95 backdrop-blur border-b border-gray-100 dark:border-gray-700/60">
              <div>
                <p className="eyebrow">Invoices · Settings</p>
                <h2 className="text-lg font-semibold mt-0.5">Payments & business identity</h2>
              </div>
              <button onClick={() => setSettingsOpen(false)} className="icon-btn" aria-label="Close settings">
                <X size={17} />
              </button>
            </div>
            <div className="p-4 sm:p-6">
              <PaystackSettingsCard
                onSaved={(saved) => {
                  setSettings(saved);
                  setSettingsOpen(false);
                }}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ rows */

function InvoiceRow({ invoice, onOpen }: { invoice: Invoice; onOpen: () => void }) {
  const overdue = invoice.status === "overdue";
  return (
    <button
      onClick={onOpen}
      className="w-full text-left px-4 sm:px-5 py-3.5 flex items-center gap-3 sm:gap-4
                 hover:bg-gray-50/80 dark:hover:bg-gray-900/30 transition-colors duration-200 group stagger-item"
    >
      <span
        className={`w-10 h-10 rounded-full flex items-center justify-center text-[11px] font-semibold shrink-0
          ${overdue
            ? "bg-danger-50 text-danger-700 dark:bg-danger-950/50 dark:text-danger-300"
            : "bg-primary-50 text-primary-900 dark:bg-primary-950/60 dark:text-primary-200"}`}
      >
        {initials(invoice.customer.name)}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-sm font-semibold tracking-tight">{invoice.customer.name}</span>
          <StatusPill status={invoice.status} />
        </div>
        <p className="text-[11px] text-gray-400 mt-0.5 truncate">
          {invoice.number} · issued {formatDate(invoice.issue_date)}
          {invoice.customer.email ? ` · ${invoice.customer.email}` : ""}
        </p>
      </div>
      <div className="text-right shrink-0">
        <p className="text-sm font-semibold tabular-nums">{formatMoney(invoice.total_minor, invoice.currency)}</p>
        <p className={`text-[10.5px] mt-0.5 ${invoice.balance_minor > 0 ? "text-gray-400" : "text-success-600"}`}>
          {invoice.balance_minor > 0
            ? `${formatMoney(invoice.balance_minor, invoice.currency)} due ${formatDate(invoice.due_date)}`
            : "Paid in full"}
        </p>
      </div>
      <ChevronRight size={15} className="text-gray-300 dark:text-gray-600 shrink-0 transition-transform duration-200 group-hover:translate-x-0.5" />
    </button>
  );
}

function MiniStat({
  icon, label, value, hint, tone, onClick,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  hint: string;
  tone: "success" | "danger" | "neutral";
  onClick?: () => void;
}) {
  const toneClass = {
    success: "bg-success-50 text-success-700 dark:bg-success-950/40 dark:text-success-300",
    danger: "bg-danger-50 text-danger-700 dark:bg-danger-950/40 dark:text-danger-300",
    neutral: "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300",
  }[tone];
  return (
    <button
      onClick={onClick}
      disabled={!onClick}
      className="card card-hover p-4 sm:p-5 text-left animate-fade-up disabled:hover:translate-y-0 disabled:cursor-default"
    >
      <div className="flex items-center justify-between">
        <span className={`w-8 h-8 rounded-full flex items-center justify-center ${toneClass}`}>{icon}</span>
        {onClick && <ArrowUpRight size={13} className="text-gray-300 group-hover:text-gray-500" />}
      </div>
      <p className="text-[11.5px] text-gray-500 mt-3">{label}</p>
      <p className="text-lg sm:text-xl font-semibold tracking-tight mt-0.5 tabular-nums">{value}</p>
      <p className="text-[10px] text-gray-400 mt-0.5 truncate">{hint || " "}</p>
    </button>
  );
}

/* -------------------------------------------------------------- analytics */

function AnalyticsTab({
  analytics, loading, currency, onCurrency, onOpenSettings,
}: {
  analytics: InvoiceAnalytics | null;
  loading: boolean;
  currency: string;
  onCurrency: (code: string) => void;
  onOpenSettings: () => void;
}) {
  if (loading && !analytics) {
    return (
      <div className="space-y-4">
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          {[...Array(4)].map((_, i) => <div key={i} className="skeleton h-28 rounded-3xl" />)}
        </div>
        <div className="skeleton h-80 rounded-3xl" />
      </div>
    );
  }
  if (!analytics) {
    return (
      <div className="card p-10 text-center">
        <BarChart3 size={30} className="mx-auto text-gray-300 mb-3" />
        <p className="text-sm font-medium">Choose a currency to see revenue analytics</p>
        <button onClick={onOpenSettings} className="btn-secondary btn-sm mt-3">Invoice settings</button>
      </div>
    );
  }

  const summary = analytics.summary;
  const max = Math.max(
    ...analytics.monthly.flatMap((m) => [m.invoiced_minor, m.collected_minor]),
    1
  );
  const last = analytics.monthly[analytics.monthly.length - 1];
  const collectionRate = last && last.invoiced_minor > 0
    ? Math.min(100, Math.round((last.collected_minor / last.invoiced_minor) * 100))
    : null;
  const statuses = Object.entries(summary.by_status).filter(([, count]) => count > 0);
  const statusTotal = statuses.reduce((sum, [, count]) => sum + count, 0) || 1;

  return (
    <div className="space-y-4 sm:space-y-5 animate-fade-up">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-gray-500">
          Revenue for <span className="font-semibold text-gray-800 dark:text-gray-200">{analytics.currency}</span> · last 12 months
        </p>
        <div className="relative">
          <select
            className="input py-2 pr-8 pl-3 text-xs w-32 appearance-none"
            value={currency}
            onChange={(e) => onCurrency(e.target.value)}
            aria-label="Analytics currency"
          >
            {CURRENCY_CODES.map((code) => (
              <option key={code} value={code}>{code}</option>
            ))}
          </select>
          <ChevronRight size={12} className="absolute right-2.5 top-1/2 -translate-y-1/2 rotate-90 text-gray-400 pointer-events-none" />
        </div>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <div className="stat-hero p-4 sm:p-5 col-span-2 lg:col-span-1">
          <p className="text-[11px] text-white/60">Collected</p>
          <p className="text-2xl font-semibold tracking-tight mt-2">{formatMoney(summary.paid_minor, analytics.currency)}</p>
          <p className="text-[10px] text-white/50 mt-2">{summary.paid_count} invoices paid</p>
        </div>
        <MetricCard label="Invoiced" value={formatMoney(summary.invoiced_minor, analytics.currency)} hint={`${summary.invoice_count} invoices`} />
        <MetricCard label="Outstanding" value={formatMoney(summary.outstanding_minor, analytics.currency)} hint={`Due in 14 days: ${formatMoneyCompact(analytics.due_in_14_days_minor, analytics.currency)}`} />
        <MetricCard label="Average invoice" value={formatMoney(summary.average_invoice_minor, analytics.currency)} hint={`${summary.overdue_count} overdue`} />
      </div>

      {/* Bar chart — deliberately hand-built so it matches the reference exactly */}
      <div className="card p-4 sm:p-6">
        <div className="flex items-start justify-between gap-3 mb-5">
          <div>
            <h2 className="text-sm font-semibold">Revenue analytics</h2>
            <p className="text-[11px] text-gray-400 mt-0.5">Invoiced vs actually collected, by month.</p>
          </div>
          <div className="flex items-center gap-3 text-[10px] text-gray-400">
            <span className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-full bg-primary-800" /> Invoiced</span>
            <span className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-full bg-accent-300" /> Collected</span>
          </div>
        </div>
        <div className="relative">
          {collectionRate !== null && (
            <div className="absolute right-0 -top-1 z-10 rounded-full bg-accent-100 text-accent-800 text-[10px] font-semibold px-2 py-1 dark:bg-accent-950/60 dark:text-accent-300">
              {collectionRate}% collected
            </div>
          )}
          <div className="flex items-end gap-1.5 sm:gap-3 h-52 pt-4">
            {analytics.monthly.map((month, index) => {
              const invHeight = month.invoiced_minor ? Math.max(5, (month.invoiced_minor / max) * 100) : 0;
              const colHeight = month.collected_minor ? Math.max(5, (month.collected_minor / max) * 100) : 0;
              return (
                <div key={month.month} className="flex-1 min-w-0 flex flex-col items-center gap-2" title={`${month.month}: invoiced ${formatMoney(month.invoiced_minor, analytics.currency)}, collected ${formatMoney(month.collected_minor, analytics.currency)}`}>
                  <div className="flex items-end justify-center gap-0.5 sm:gap-1.5 h-40 w-full">
                    {invHeight ? (
                      <div
                        className="bar-grow w-[8px] sm:w-[13px] rounded-full bg-gradient-to-t from-primary-950 to-primary-600 animate-bar-grow"
                        style={{ height: `${invHeight}%`, animationDelay: `${index * 35}ms` }}
                      />
                    ) : (
                      <div className="hatched-bar w-[8px] sm:w-[13px] h-2" />
                    )}
                    {colHeight ? (
                      <div
                        className="bar-grow w-[8px] sm:w-[13px] rounded-full bg-accent-300/80 animate-bar-grow"
                        style={{ height: `${colHeight}%`, animationDelay: `${index * 35 + 90}ms` }}
                      />
                    ) : (
                      <div className="hatched-bar w-[8px] sm:w-[13px] h-2" />
                    )}
                  </div>
                  <span className="text-[9px] sm:text-[10px] text-gray-400">{month.label}</span>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="card p-4 sm:p-5">
          <h2 className="text-sm font-semibold mb-3 flex items-center gap-2">
            <CircleDollarSign size={15} /> By status
          </h2>
          {statuses.length === 0 ? (
            <p className="text-xs text-gray-400 py-6 text-center">No invoices yet.</p>
          ) : (
            <div className="space-y-3">
              {statuses.map(([key, count]) => (
                <div key={key}>
                  <div className="flex items-center justify-between gap-2 mb-1">
                    <StatusPill status={key} />
                    <span className="text-xs font-medium tabular-nums">{count}</span>
                  </div>
                  <div className="h-1.5 rounded-full bg-gray-100 dark:bg-gray-700/60 overflow-hidden">
                    <div
                      className="h-full rounded-full bg-primary-800/75 transition-all duration-700 ease-gentle"
                      style={{ width: `${Math.max(3, (count / statusTotal) * 100)}%` }}
                    />
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="card p-4 sm:p-5">
          <h2 className="text-sm font-semibold mb-3 flex items-center gap-2">
            <Users size={15} /> Top customers
          </h2>
          {analytics.top_customers.length === 0 ? (
            <p className="text-xs text-gray-400 py-6 text-center">No paid customers yet.</p>
          ) : (
            <div className="space-y-2.5">
              {analytics.top_customers.map((customer) => (
                <div key={customer.customer_name} className="flex items-center gap-3">
                  <span className="w-8 h-8 rounded-full bg-primary-50 dark:bg-primary-950/50 text-primary-900 dark:text-primary-200 text-[10px] font-semibold flex items-center justify-center shrink-0">
                    {initials(customer.customer_name)}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-xs font-medium truncate">{customer.customer_name}</p>
                    <p className="text-[10px] text-gray-400">{customer.invoices} invoice{customer.invoices === 1 ? "" : "s"}</p>
                  </div>
                  <div className="text-right">
                    <p className="text-xs font-semibold tabular-nums">{formatMoney(customer.paid_minor, analytics.currency)}</p>
                    <p className="text-[10px] text-gray-400">paid</p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="card p-4 sm:p-5">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold flex items-center gap-2">
            <Banknote size={15} /> Recent payments
          </h2>
          <span className="text-[10px] text-gray-400">{summary.paid_count} total</span>
        </div>
        {analytics.recent_payments.length === 0 ? (
          <p className="text-xs text-gray-400 py-6 text-center">Payments will appear here the moment they land.</p>
        ) : (
          <div className="divide-y divide-gray-100 dark:divide-gray-700/60">
            {analytics.recent_payments.map((payment) => (
              <div key={payment.id} className="flex items-center justify-between gap-3 py-2.5">
                <div className="min-w-0">
                  <p className="text-xs font-medium truncate">{payment.customer_name}</p>
                  <p className="text-[10px] text-gray-400 truncate">
                    {payment.invoice_number} · {payment.provider.replace(/_/g, " ")}
                    {payment.channel ? ` · ${payment.channel}` : ""}
                  </p>
                </div>
                <div className="text-right shrink-0">
                  <p className="text-xs font-semibold tabular-nums">{formatMoney(payment.amount_minor, payment.currency)}</p>
                  <p className="text-[10px] text-gray-400">{formatDateTime(payment.paid_at)}</p>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function MetricCard({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <div className="card p-4 sm:p-5">
      <p className="text-[11px] text-gray-400">{label}</p>
      <p className="text-lg sm:text-xl font-semibold tracking-tight mt-1 tabular-nums">{value}</p>
      <p className="text-[10px] text-gray-400 mt-0.5 truncate">{hint}</p>
    </div>
  );
}

/* --------------------------------------------------------------- payments */

function PaymentsTab({
  data, loading, currency, onCurrency, page, onPage, onOpen,
}: {
  data: PaymentsHistory | null;
  loading: boolean;
  currency: string;
  onCurrency: (code: string) => void;
  page: number;
  onPage: (page: number) => void;
  onOpen: (id: number) => void;
}) {
  const pages = data ? Math.max(1, Math.ceil(data.total / data.per_page)) : 1;
  return (
    <div className="space-y-4 animate-fade-up">
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <div className="stat-hero p-4 sm:p-5 sm:col-span-1">
          <p className="text-[11px] text-white/60">Total collected</p>
          <p className="text-2xl font-semibold tracking-tight mt-2">
            {data ? formatMoney(data.total_collected_minor, currency || "NGN") : "—"}
          </p>
          <p className="text-[10px] text-white/50 mt-2">{data?.total || 0} successful payments</p>
        </div>
        <div className="card p-4 sm:p-5 sm:col-span-2">
          <div className="flex items-center justify-between gap-2 mb-3">
            <p className="text-[11px] text-gray-400">Where the money came from</p>
            <div className="relative">
              <select
                className="input py-1.5 pr-7 pl-2.5 text-[11px] w-24 appearance-none"
                value={currency}
                onChange={(e) => onCurrency(e.target.value)}
                aria-label="Payments currency"
              >
                <option value="">All</option>
                {CURRENCY_CODES.map((code) => <option key={code} value={code}>{code}</option>)}
              </select>
              <ChevronRight size={11} className="absolute right-2 top-1/2 -translate-y-1/2 rotate-90 text-gray-400 pointer-events-none" />
            </div>
          </div>
          {data?.by_provider?.length ? (
            <div className="space-y-2">
              {data.by_provider.map((row) => (
                <div key={row.provider} className="flex items-center justify-between text-xs">
                  <span className="capitalize text-gray-600 dark:text-gray-300">{row.provider.replace(/_/g, " ")}</span>
                  <span className="font-semibold tabular-nums">{formatMoney(row.amount_minor, currency || "NGN")}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs text-gray-400 py-3">No payments recorded yet.</p>
          )}
        </div>
      </div>

      <div className="card overflow-hidden">
        <div className="px-4 sm:px-5 py-4 border-b border-gray-100 dark:border-gray-700/60 flex items-center justify-between">
          <h2 className="text-sm font-semibold flex items-center gap-2"><CreditCard size={15} /> Payment history</h2>
          <span className="text-[10px] text-gray-400">{data?.total || 0} records</span>
        </div>
        {loading ? (
          <div className="p-4 space-y-2">
            {[...Array(5)].map((_, i) => <div key={i} className="skeleton h-14 rounded-2xl" />)}
          </div>
        ) : !data?.items.length ? (
          <div className="py-16 text-center px-5">
            <span className="w-14 h-14 rounded-full bg-primary-50 dark:bg-primary-950/50 text-primary-800 dark:text-primary-300 flex items-center justify-center mx-auto mb-4">
              <CreditCard size={24} />
            </span>
            <p className="text-sm font-semibold">No payments yet</p>
            <p className="text-xs text-gray-500 mt-1">
              When a customer pays a Paystack link — or you record cash — it lands here.
            </p>
          </div>
        ) : (
          <div className="divide-y divide-gray-100 dark:divide-gray-700/60">
            {data.items.map((payment) => (
              <button
                key={payment.id}
                onClick={() => onOpen(payment.invoice_id)}
                className="w-full text-left px-4 sm:px-5 py-3 flex items-center gap-3 hover:bg-gray-50/80 dark:hover:bg-gray-900/30 transition-colors"
              >
                <span className="w-9 h-9 rounded-full bg-success-50 text-success-700 dark:bg-success-950/40 dark:text-success-300 flex items-center justify-center shrink-0">
                  <CheckCircle2 size={15} />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-xs font-semibold truncate">{payment.customer_name}</p>
                  <p className="text-[10px] text-gray-400 truncate">
                    {payment.invoice_number} · {payment.provider.replace(/_/g, " ")}
                    {payment.channel ? ` · ${payment.channel}` : ""}
                  </p>
                </div>
                <div className="text-right shrink-0">
                  <p className="text-xs font-semibold tabular-nums">{formatMoney(payment.amount_minor, payment.currency)}</p>
                  <p className="text-[10px] text-gray-400">{formatDateTime(payment.paid_at)}</p>
                </div>
              </button>
            ))}
          </div>
        )}
        {data && data.total > data.per_page && (
          <div className="flex items-center justify-between gap-3 px-4 sm:px-5 py-3 border-t border-gray-100 dark:border-gray-700/60">
            <p className="text-[11px] text-gray-400">Page {page} of {pages}</p>
            <div className="flex gap-1">
              <button onClick={() => onPage(Math.max(1, page - 1))} disabled={page <= 1} className="icon-btn w-8 h-8" aria-label="Previous page">
                <ChevronLeft size={14} />
              </button>
              <button onClick={() => onPage(Math.min(pages, page + 1))} disabled={page >= pages} className="icon-btn w-8 h-8" aria-label="Next page">
                <ChevronRight size={14} />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
