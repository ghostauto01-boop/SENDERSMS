import { useEffect, useMemo, useRef, useState } from "react";
import toast from "react-hot-toast";
import {
  Ban, BellRing, CheckCircle2, Clock3, Copy, CopyPlus, CreditCard, ExternalLink,
  FileText, History, Link2, Loader2, Mail, MoreHorizontal, Pencil, Printer,
  ReceiptText, RotateCw, Send, Trash2, X,
} from "lucide-react";
import invoicesApi, { Invoice, InvoiceEventRow, InvoicePaymentRow } from "../../api/invoices";
import { formatDate, formatDateTime, formatMoney, minorToInput, parseMoneyToMinor } from "../../utils/money";
import StatusPill from "./StatusPill";

/**
 * One invoice, completely: the document, the money, and the audit trail.
 *
 * "Everything editable" is the rule. Editing line items after sending does not
 * create a new invoice; the server recomputes the total and the history records
 * that it changed. A payment is the only thing that can mark an invoice paid —
 * status is never a free-text field.
 */

type Tab = "document" | "payments" | "history";

const PROVIDERS = [
  { value: "manual", label: "Manual / other" },
  { value: "bank_transfer", label: "Bank transfer" },
  { value: "cash", label: "Cash" },
  { value: "paystack", label: "Paystack (recorded by hand)" },
];

function eventIcon(type: string) {
  if (type === "paid") return <CheckCircle2 size={14} />;
  if (type === "partial") return <CreditCard size={14} />;
  if (type === "sent" || type === "email_sent") return <Send size={14} />;
  if (type === "viewed") return <ExternalLink size={14} />;
  if (type === "reminder") return <BellRing size={14} />;
  if (type === "voided") return <Ban size={14} />;
  if (type === "payment_started" || type === "payment_recorded") return <CreditCard size={14} />;
  return <FileText size={14} />;
}

export default function InvoiceDetail({
  invoiceId,
  initial,
  onClose,
  onEdit,
  onChanged,
  onDeleted,
}: {
  invoiceId: number;
  initial?: Invoice | null;
  onClose: () => void;
  onEdit: (invoice: Invoice) => void;
  onChanged: (invoice?: Invoice) => void;
  onDeleted: () => void;
}) {
  const [invoice, setInvoice] = useState<Invoice | null>(initial || null);
  const [loading, setLoading] = useState(!initial);
  const [busy, setBusy] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("document");
  const [menuOpen, setMenuOpen] = useState(false);
  const [payOpen, setPayOpen] = useState(false);
  const [mailHint, setMailHint] = useState<{ mailto: string | null; message: string } | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const load = async () => {
    try {
      const data = await invoicesApi.get(invoiceId);
      setInvoice(data);
      return data;
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || "Could not load this invoice");
      return null;
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [invoiceId]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        if (payOpen) setPayOpen(false);
        else onClose();
      }
    };
    const onClick = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) setMenuOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClick);
    };
  }, [onClose, payOpen]);

  const apply = (data: Invoice) => {
    setInvoice(data);
    onChanged(data);
  };

  const copyLink = async () => {
    if (!invoice) return;
    try {
      await navigator.clipboard.writeText(invoice.public_url);
      toast.success("Payment link copied");
    } catch {
      window.prompt("Copy the payment link", invoice.public_url);
    }
  };

  const send = async () => {
    if (!invoice) return;
    setBusy("send");
    try {
      const result = await invoicesApi.send(invoice.id, true);
      apply(result.invoice);
      setMailHint({ mailto: result.mailto_url || null, message: result.message });
      toast.success(result.message);
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || "Could not send the invoice");
    } finally {
      setBusy(null);
    }
  };

  const remind = async () => {
    if (!invoice) return;
    setBusy("remind");
    try {
      const result = await invoicesApi.remind(invoice.id);
      apply(result.invoice);
      setMailHint({ mailto: result.mailto_url || null, message: result.message });
      toast.success(result.email_sent ? "Reminder emailed" : "Reminder prepared — share the link");
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || "Could not send a reminder");
    } finally {
      setBusy(null);
    }
  };

  const collect = async () => {
    if (!invoice) return;
    // Claim the tab inside the click itself. Opening it after `await` is a
    // post-gesture popup, which Safari and Chrome block — and a blocked tab is
    // indistinguishable from "the button did nothing".
    const pending = window.open("", "_blank");
    setBusy("pay");
    try {
      const result = await invoicesApi.pay(invoice.id, invoice.customer.email || undefined);
      setInvoice(result.invoice);
      onChanged(result.invoice);
      if (pending && !pending.closed) {
        pending.location.href = result.authorization_url;
        toast.success("Paystack checkout opened — this invoice updates the moment payment lands.");
      } else {
        toast(
          <span>
            Your browser blocked the new tab.{" "}
            <a
              href={result.authorization_url}
              target="_blank"
              rel="noreferrer"
              className="underline font-semibold"
            >
              Open Paystack
            </a>
          </span>,
          { duration: 9000 }
        );
      }
    } catch (err: any) {
      pending?.close();
      toast.error(err?.response?.data?.detail || "Could not start a Paystack payment");
    } finally {
      setBusy(null);
    }
  };

  const duplicate = async () => {
    if (!invoice) return;
    setBusy("duplicate");
    try {
      const result = await invoicesApi.duplicate(invoice.id);
      toast.success(`Copied to ${result.invoice.number} (draft)`);
      onChanged(result.invoice);
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || "Could not duplicate");
    } finally {
      setBusy(null);
      setMenuOpen(false);
    }
  };

  const voidInvoice = async () => {
    if (!invoice) return;
    if (!confirm(`Void ${invoice.number}? The payment link stops collecting money.`)) return;
    setBusy("void");
    try {
      const result = await invoicesApi.void(invoice.id);
      apply(result.invoice);
      toast.success("Invoice voided");
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || "Could not void");
    } finally {
      setBusy(null);
      setMenuOpen(false);
    }
  };

  const rotate = async () => {
    if (!invoice) return;
    if (!confirm("Create a new payment link? Every previously shared link stops working.")) return;
    setBusy("rotate");
    try {
      const result = await invoicesApi.rotateLink(invoice.id);
      apply(result.invoice);
      toast.success("A new payment link is ready");
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || "Could not rotate the link");
    } finally {
      setBusy(null);
      setMenuOpen(false);
    }
  };

  const remove = async () => {
    if (!invoice) return;
    if (!confirm(`Delete ${invoice.number} permanently? Its payment history is deleted too.`)) return;
    setBusy("delete");
    try {
      await invoicesApi.remove(invoice.id);
      toast.success("Invoice deleted");
      onDeleted();
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || "Could not delete");
    } finally {
      setBusy(null);
      setMenuOpen(false);
    }
  };

  const print = () => {
    if (!invoice) return;
    window.open(`${invoice.public_url}?print=1`, "_blank", "noopener,noreferrer");
  };

  const payments = useMemo(() => invoice?.payments || [], [invoice]);
  const events = useMemo(() => invoice?.events || [], [invoice]);
  const paid = invoice ? invoice.amount_paid_minor : 0;

  return (
    <div className="fixed inset-0 z-[65] flex items-end sm:items-center justify-center bg-primary-950/40 backdrop-blur-sm sm:p-5">
      <div className="bg-white dark:bg-gray-800 w-full sm:max-w-3xl h-[94dvh] sm:h-auto sm:max-h-[92vh] rounded-t-[28px] sm:rounded-[28px] shadow-pop flex flex-col animate-sheet-in overflow-hidden">
        {/* Header */}
        <div className="shrink-0 px-4 sm:px-6 pt-5 pb-4 border-b border-gray-100 dark:border-gray-700/60">
          <div className="flex items-start gap-3">
            <div className="w-11 h-11 rounded-2xl bg-primary-50 dark:bg-primary-950/50 flex items-center justify-center shrink-0">
              <ReceiptText size={20} className="text-primary-800 dark:text-primary-300" />
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 flex-wrap">
                <h2 className="text-lg sm:text-xl font-semibold tracking-tight truncate">
                  {invoice?.number || "Invoice"}
                </h2>
                {invoice && <StatusPill status={invoice.status} />}
              </div>
              <p className="text-xs text-gray-500 mt-0.5 truncate">
                {invoice ? `${invoice.customer.name}${invoice.customer.email ? ` · ${invoice.customer.email}` : ""}` : "Loading…"}
              </p>
            </div>
            <div className="flex items-center gap-1.5 shrink-0">
              <button
                onClick={() => invoice && onEdit(invoice)}
                disabled={!invoice}
                className="icon-btn hidden sm:flex"
                title="Edit invoice"
                aria-label="Edit invoice"
              >
                <Pencil size={15} />
              </button>
              <div className="relative" ref={menuRef}>
                <button
                  onClick={() => setMenuOpen((open) => !open)}
                  className="icon-btn"
                  aria-label="More invoice actions"
                  aria-expanded={menuOpen}
                >
                  <MoreHorizontal size={17} />
                </button>
                {menuOpen && (
                  <div className="absolute right-0 mt-2 w-52 rounded-2xl bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 shadow-pop p-1.5 z-30 animate-pop-in">
                    <MenuItem icon={<CopyPlus size={14} />} onClick={duplicate}>Duplicate</MenuItem>
                    <MenuItem icon={<Link2 size={14} />} onClick={rotate}>New payment link</MenuItem>
                    <MenuItem icon={<Printer size={14} />} onClick={print}>Print / save as PDF</MenuItem>
                    <MenuItem icon={<Ban size={14} />} onClick={voidInvoice} disabled={invoice?.status === "paid" || invoice?.status === "void"}>
                      Void invoice
                    </MenuItem>
                    <div className="my-1 border-t border-gray-100 dark:border-gray-700" />
                    <MenuItem icon={<Trash2 size={14} />} onClick={remove} danger>Delete</MenuItem>
                  </div>
                )}
              </div>
              <button onClick={onClose} className="icon-btn" aria-label="Close">
                <X size={17} />
              </button>
            </div>
          </div>

          {/* Actions */}
          {invoice && (
            <div className="flex items-center gap-2 mt-3.5 overflow-x-auto scrollbar-none pb-0.5">
              <button onClick={send} disabled={!!busy} className="btn-primary btn-sm rounded-full shrink-0">
                {busy === "send" ? <Loader2 size={13} className="animate-spin" /> : <Send size={13} />}
                Send
              </button>
              <button onClick={copyLink} className="btn-secondary btn-sm rounded-full shrink-0">
                <Copy size={13} /> Copy link
              </button>
              {invoice.status !== "paid" && invoice.status !== "void" && (
                <button onClick={collect} disabled={!!busy} className="btn-secondary btn-sm rounded-full shrink-0">
                  {busy === "pay" ? <Loader2 size={13} className="animate-spin" /> : <CreditCard size={13} />}
                  Collect with Paystack
                </button>
              )}
              {invoice.status !== "paid" && invoice.status !== "void" && (
                <button onClick={() => setPayOpen(true)} className="btn-secondary btn-sm rounded-full shrink-0">
                  <CheckCircle2 size={13} /> Record payment
                </button>
              )}
              {invoice.status !== "paid" && invoice.status !== "void" && (
                <button onClick={remind} disabled={!!busy} className="btn-ghost btn-sm rounded-full shrink-0">
                  {busy === "remind" ? <Loader2 size={13} className="animate-spin" /> : <BellRing size={13} />}
                  Remind{invoice.reminders_sent ? ` (${invoice.reminders_sent})` : ""}
                </button>
              )}
              <a
                href={invoice.public_url}
                target="_blank"
                rel="noreferrer"
                className="btn-ghost btn-sm rounded-full shrink-0"
              >
                <ExternalLink size={13} /> Preview
              </a>
            </div>
          )}

          {mailHint && (
            <div className="mt-3 rounded-xl bg-primary-50 dark:bg-primary-950/40 px-3 py-2 text-[11px] text-primary-900 dark:text-primary-200 flex items-center gap-2 animate-fade-up">
              <Mail size={13} className="shrink-0" />
              <span className="flex-1">{mailHint.message}</span>
              {mailHint.mailto && (
                <a href={mailHint.mailto} className="font-semibold underline shrink-0">Open mail app</a>
              )}
              <button onClick={() => setMailHint(null)} aria-label="Dismiss" className="shrink-0 opacity-50 hover:opacity-100">
                <X size={12} />
              </button>
            </div>
          )}
        </div>

        {/* Tabs */}
        <div className="shrink-0 px-4 sm:px-6 pt-3">
          <div className="segment w-full sm:w-auto">
            {([
              ["document", "Invoice", <FileText key="i" size={13} />],
              ["payments", `Payments${payments.length ? ` (${payments.length})` : ""}`, <CreditCard key="i" size={13} />],
              ["history", "History", <History key="i" size={13} />],
            ] as [Tab, string, JSX.Element][]).map(([key, label, icon]) => (
              <button
                key={key}
                onClick={() => setTab(key)}
                className={`segment-item flex-1 sm:flex-none inline-flex items-center justify-center gap-1.5 ${
                  tab === key ? "segment-item-active" : ""
                }`}
              >
                {icon} {label}
              </button>
            ))}
          </div>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-4 sm:px-6 py-4 wa-scrollbar">
          {loading || !invoice ? (
            <div className="space-y-3 animate-fade-in">
              <div className="skeleton h-24 w-full rounded-2xl" />
              <div className="skeleton h-40 w-full rounded-2xl" />
              <div className="skeleton h-20 w-full rounded-2xl" />
            </div>
          ) : tab === "document" ? (
            <div className="space-y-4 animate-fade-up">
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <Info label="Total" value={formatMoney(invoice.total_minor, invoice.currency)} strong />
                <Info label="Paid" value={formatMoney(paid, invoice.currency)} />
                <Info label="Balance" value={formatMoney(invoice.balance_minor, invoice.currency)} accent={invoice.balance_minor > 0} />
                <Info label="Due" value={formatDate(invoice.due_date)} />
              </div>

              <div className="rounded-2xl border border-gray-200/80 dark:border-gray-700/70 overflow-hidden">
                <div className="px-4 py-3 bg-gray-50/70 dark:bg-gray-900/25 flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <p className="text-[10px] uppercase tracking-[0.14em] text-gray-400">Bill to</p>
                    <p className="text-sm font-semibold mt-0.5">{invoice.customer.name}</p>
                    <p className="text-[11px] text-gray-500 break-anywhere">
                      {[invoice.customer.email, invoice.customer.phone].filter(Boolean).join(" · ") || "No contact details"}
                    </p>
                    {invoice.customer.address && (
                      <p className="text-[11px] text-gray-400 mt-1 whitespace-pre-line">{invoice.customer.address}</p>
                    )}
                  </div>
                  <div className="text-right">
                    <p className="text-[10px] uppercase tracking-[0.14em] text-gray-400">Issued</p>
                    <p className="text-xs font-medium mt-0.5">{formatDate(invoice.issue_date)}</p>
                    <p className="text-[11px] text-gray-400 mt-0.5">Ref. {invoice.number}</p>
                  </div>
                </div>

                <div className="divide-y divide-gray-100 dark:divide-gray-700/60">
                  {invoice.items.map((item, index) => (
                    <div key={item.id || index} className="px-4 py-3 flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="text-sm font-medium break-anywhere">{item.description}</p>
                        <p className="text-[11px] text-gray-400 mt-0.5">
                          {item.quantity} × {formatMoney(item.unit_price_minor, invoice.currency)}
                        </p>
                      </div>
                      <span className="text-sm tabular-nums whitespace-nowrap">
                        {formatMoney(item.amount_minor ?? 0, invoice.currency)}
                      </span>
                    </div>
                  ))}
                </div>

                <div className="px-4 py-3 bg-gray-50/60 dark:bg-gray-900/20 space-y-1.5 text-sm">
                  <Row label="Subtotal" value={formatMoney(invoice.subtotal_minor, invoice.currency)} />
                  {invoice.discount_minor > 0 && (
                    <Row label="Discount" value={`− ${formatMoney(invoice.discount_minor, invoice.currency)}`} />
                  )}
                  {invoice.tax_minor > 0 && (
                    <Row label={`VAT (${invoice.tax_rate}%)`} value={formatMoney(invoice.tax_minor, invoice.currency)} />
                  )}
                  <div className="border-t border-gray-200 dark:border-gray-700 pt-2 flex items-center justify-between">
                    <span className="font-semibold">Total</span>
                    <span className="font-semibold text-lg tracking-tight tabular-nums">
                      {formatMoney(invoice.total_minor, invoice.currency)}
                    </span>
                  </div>
                </div>
              </div>

              {(invoice.notes || invoice.terms) && (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  {invoice.notes && (
                    <div className="rounded-2xl bg-gray-50 dark:bg-gray-900/25 p-3.5">
                      <p className="text-[10px] uppercase tracking-[0.14em] text-gray-400 mb-1">Notes</p>
                      <p className="text-xs text-gray-600 dark:text-gray-300 whitespace-pre-line">{invoice.notes}</p>
                    </div>
                  )}
                  {invoice.terms && (
                    <div className="rounded-2xl bg-gray-50 dark:bg-gray-900/25 p-3.5">
                      <p className="text-[10px] uppercase tracking-[0.14em] text-gray-400 mb-1">Terms</p>
                      <p className="text-xs text-gray-600 dark:text-gray-300 whitespace-pre-line">{invoice.terms}</p>
                    </div>
                  )}
                </div>
              )}

              <div className="rounded-2xl border border-dashed border-gray-200 dark:border-gray-700 p-3.5 flex flex-wrap items-center gap-2">
                <Link2 size={14} className="text-gray-400" />
                <span className="text-[11px] text-gray-400 flex-1 min-w-0 truncate">{invoice.public_url}</span>
                <button onClick={copyLink} className="text-[11px] font-semibold text-primary-800 dark:text-primary-300 shrink-0">
                  Copy link
                </button>
              </div>

              <div className="flex items-center justify-between gap-3 pt-1">
                <div className="text-[11px] text-gray-400">
                  {invoice.viewed_at ? `Opened by the customer ${formatDateTime(invoice.viewed_at)}` : "Not opened yet"}
                  {invoice.reminders_sent > 0 && ` · ${invoice.reminders_sent} reminder${invoice.reminders_sent === 1 ? "" : "s"}`}
                </div>
                <button onClick={() => onEdit(invoice)} className="btn-secondary btn-sm">
                  <Pencil size={13} /> Edit
                </button>
              </div>
            </div>
          ) : tab === "payments" ? (
            <div className="animate-fade-up space-y-3">
              <div className="rounded-2xl bg-primary-950 text-white px-4 py-3.5 flex items-center justify-between">
                <div>
                  <p className="text-[10px] uppercase tracking-[0.14em] text-white/50">Collected</p>
                  <p className="text-xl font-semibold tracking-tight mt-0.5">{formatMoney(paid, invoice.currency)}</p>
                </div>
                <div className="text-right">
                  <p className="text-[10px] uppercase tracking-[0.14em] text-white/50">Balance</p>
                  <p className="text-xl font-semibold tracking-tight mt-0.5">{formatMoney(invoice.balance_minor, invoice.currency)}</p>
                </div>
              </div>

              {payments.length === 0 ? (
                <Empty
                  icon={<CreditCard size={22} />}
                  title="No payments yet"
                  body="Start a Paystack checkout, or record cash and bank transfers here."
                  action={invoice.status !== "void" && invoice.status !== "paid" ? (
                    <button onClick={() => setPayOpen(true)} className="btn-primary btn-sm">Record a payment</button>
                  ) : undefined}
                />
              ) : (
                <div className="rounded-2xl border border-gray-200/80 dark:border-gray-700/70 overflow-hidden">
                  {payments.map((payment) => (
                    <PaymentRow key={payment.id} payment={payment} currency={invoice.currency} />
                  ))}
                </div>
              )}

              {invoice.status !== "void" && invoice.status !== "paid" && payments.length > 0 && (
                <button onClick={() => setPayOpen(true)} className="btn-secondary w-full">
                  <CreditCard size={14} /> Record another payment
                </button>
              )}
            </div>
          ) : (
            <div className="animate-fade-up">
              {events.length === 0 ? (
                <Empty icon={<History size={22} />} title="No history yet" body="Every send, view, payment and edit will appear here." />
              ) : (
                <div className="relative pl-7">
                  <span className="absolute left-2.5 top-2 bottom-2 w-px bg-gray-200 dark:bg-gray-700" />
                  {events.map((event: InvoiceEventRow) => (
                    <div key={event.id} className="relative pb-4 last:pb-0">
                      <span className="absolute -left-[22px] top-0.5 w-6 h-6 rounded-full bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 flex items-center justify-center text-primary-800 dark:text-primary-300">
                        {eventIcon(event.event_type)}
                      </span>
                      <p className="text-sm font-medium">{event.title}</p>
                      {event.body && <p className="text-[11px] text-gray-500 mt-0.5">{event.body}</p>}
                      <p className="text-[10px] text-gray-400 mt-0.5 flex items-center gap-1">
                        <Clock3 size={10} /> {formatDateTime(event.created_at)}
                      </p>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {payOpen && invoice && (
        <RecordPaymentModal
          invoice={invoice}
          onClose={() => setPayOpen(false)}
          onRecorded={(updated) => {
            apply(updated);
            setPayOpen(false);
            setTab("payments");
          }}
        />
      )}
    </div>
  );
}

function MenuItem({
  children, icon, onClick, danger, disabled,
}: {
  children: React.ReactNode;
  icon: React.ReactNode;
  onClick: () => void;
  danger?: boolean;
  disabled?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`w-full flex items-center gap-2.5 px-2.5 py-2 rounded-xl text-xs font-medium transition-colors text-left
        ${danger
          ? "text-danger-600 hover:bg-danger-50 dark:hover:bg-danger-950/40"
          : "text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700/60"}
        disabled:opacity-40 disabled:pointer-events-none`}
    >
      {icon}
      {children}
    </button>
  );
}

function Info({ label, value, strong, accent }: { label: string; value: string; strong?: boolean; accent?: boolean }) {
  return (
    <div className="rounded-2xl bg-gray-50 dark:bg-gray-900/25 px-3 py-2.5">
      <p className="text-[10px] uppercase tracking-[0.12em] text-gray-400">{label}</p>
      <p className={`mt-1 tabular-nums tracking-tight ${strong ? "text-base font-semibold" : "text-sm font-medium"} ${
        accent ? "text-danger-600 dark:text-danger-400" : ""
      }`}>
        {value}
      </p>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between text-xs text-gray-500 dark:text-gray-400">
      <span>{label}</span>
      <span className="tabular-nums">{value}</span>
    </div>
  );
}

function PaymentRow({ payment, currency }: { payment: InvoicePaymentRow; currency: string }) {
  const statusClass =
    payment.status === "success"
      ? "badge-green"
      : payment.status === "pending"
        ? "badge-yellow"
        : "badge-gray";
  return (
    <div className="px-4 py-3 flex items-center justify-between gap-3 border-b border-gray-100 dark:border-gray-700/60 last:border-0">
      <div className="min-w-0">
        <p className="text-sm font-medium flex items-center gap-2 flex-wrap">
          {formatMoney(payment.amount_minor, payment.currency || currency)}
          <span className={`badge ${statusClass}`}>{payment.status}</span>
        </p>
        <p className="text-[11px] text-gray-400 mt-0.5 truncate">
          {payment.provider.replace(/_/g, " ")}
          {payment.channel ? ` · ${payment.channel}` : ""}
          {payment.reference ? ` · ${payment.reference}` : ""}
        </p>
        {payment.note && <p className="text-[11px] text-gray-500 mt-0.5">{payment.note}</p>}
      </div>
      <span className="text-[10px] text-gray-400 whitespace-nowrap">{formatDateTime(payment.paid_at || payment.created_at)}</span>
    </div>
  );
}

function Empty({
  icon, title, body, action,
}: {
  icon: React.ReactNode;
  title: string;
  body: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="rounded-2xl border border-dashed border-gray-200 dark:border-gray-700 py-10 px-4 text-center">
      <span className="w-11 h-11 rounded-full bg-primary-50 dark:bg-primary-950/50 text-primary-800 dark:text-primary-300 flex items-center justify-center mx-auto mb-3">
        {icon}
      </span>
      <p className="text-sm font-semibold">{title}</p>
      <p className="text-xs text-gray-500 mt-1 max-w-xs mx-auto">{body}</p>
      {action && <div className="mt-4 flex justify-center">{action}</div>}
    </div>
  );
}

function RecordPaymentModal({
  invoice, onClose, onRecorded,
}: {
  invoice: Invoice;
  onClose: () => void;
  onRecorded: (invoice: Invoice) => void;
}) {
  const [amount, setAmount] = useState(minorToInput(invoice.balance_minor));
  const [provider, setProvider] = useState("bank_transfer");
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setError(null);
    const minor = parseMoneyToMinor(amount);
    if (minor <= 0) return setError("Enter the amount that was paid.");
    if (minor > invoice.balance_minor) {
      return setError(`The balance is ${formatMoney(invoice.balance_minor, invoice.currency)}.`);
    }
    setSaving(true);
    try {
      const result = await invoicesApi.recordPayment(invoice.id, {
        amount_minor: minor,
        provider,
        note: note.trim() || null,
      });
      toast.success(
        result.invoice.status === "paid"
          ? "Invoice paid in full"
          : `Payment recorded — ${formatMoney(result.invoice.balance_minor, invoice.currency)} remaining`
      );
      onRecorded(result.invoice);
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Could not record that payment.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[80] flex items-end sm:items-center justify-center bg-primary-950/50 backdrop-blur-sm sm:p-4">
      <div className="bg-white dark:bg-gray-800 w-full sm:max-w-md rounded-t-[26px] sm:rounded-[26px] shadow-pop p-5 sm:p-6 animate-sheet-in safe-bottom">
        <div className="flex items-start justify-between gap-3 mb-4">
          <div>
            <h3 className="text-lg font-semibold">Record a payment</h3>
            <p className="text-xs text-gray-500 mt-0.5">
              {invoice.number} · balance {formatMoney(invoice.balance_minor, invoice.currency)}
            </p>
          </div>
          <button onClick={onClose} className="icon-btn" aria-label="Close">
            <X size={16} />
          </button>
        </div>
        {error && <p className="mb-3 rounded-xl bg-danger-50 dark:bg-danger-950/40 text-danger-700 dark:text-danger-300 px-3 py-2 text-xs">{error}</p>}
        <div className="space-y-3">
          <div>
            <label className="label">Amount received</label>
            <div className="relative">
              <span className="absolute left-3.5 top-1/2 -translate-y-1/2 text-sm text-gray-400">
                {invoice.currency_symbol}
              </span>
              <input
                className="input pl-9 text-lg font-semibold tabular-nums"
                inputMode="decimal"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                autoFocus
              />
            </div>
          </div>
          <div>
            <label className="label">How was it paid?</label>
            <select className="input" value={provider} onChange={(e) => setProvider(e.target.value)}>
              {PROVIDERS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="label">Note (optional)</label>
            <input
              className="input"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="e.g. GTBank transfer ref 0042"
            />
          </div>
        </div>
        <div className="flex gap-2 mt-5">
          <button onClick={onClose} className="btn-ghost flex-1">Cancel</button>
          <button onClick={submit} disabled={saving} className="btn-primary flex-1">
            {saving ? <Loader2 size={14} className="animate-spin" /> : <CheckCircle2 size={14} />}
            Record payment
          </button>
        </div>
      </div>
    </div>
  );
}
