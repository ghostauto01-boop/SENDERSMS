import { useEffect, useState } from "react";
import { useLocation, useParams } from "react-router-dom";
import toast from "react-hot-toast";
import {
  CheckCircle2, CreditCard, Loader2, Lock, Mail, ShieldCheck, XCircle,
} from "lucide-react";
import invoicesApi, { Invoice } from "../api/invoices";
import BrandMark from "../components/BrandMark";
import StatusPill from "../components/invoices/StatusPill";
import { formatDate, formatDateTime, formatMoney } from "../utils/money";

/**
 * The customer-facing invoice page (``/i/<token>``).
 *
 * No app shell, no login: a customer receives this link by SMS or email and
 * sees one document and one action. Payment is a server-initialised Paystack
 * checkout — the page never touches a secret key. When Paystack sends the
 * browser back with ``?reference=…`` the payment is verified immediately; the
 * webhook remains the authoritative confirmation.
 */
export default function PublicInvoicePage() {
  const { token = "" } = useParams();
  const [invoice, setInvoice] = useState<Invoice | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [paying, setPaying] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [paidJustNow, setPaidJustNow] = useState(false);
  const [email, setEmail] = useState("");

  // Read the query through the router, not window: the same code then works in
  // a test renderer and behind any base path the deployment happens to use.
  const location = useLocation();
  const params = new URLSearchParams(location.search);
  const reference = params.get("reference");
  const printMode = params.get("print") === "1";

  useEffect(() => {
    let cancelled = false;
    invoicesApi
      .publicGet(token)
      .then((data) => {
        if (cancelled) return;
        setInvoice(data);
        setEmail(data.customer.email || "");
        document.title = `${data.number} · ${data.business?.name || "Invoice"}`;
      })
      .catch((err: any) => {
        if (!cancelled) {
          setError(
            err?.response?.status === 404
              ? "This invoice link is not valid. Ask the sender for a new link."
              : err?.response?.data?.detail || "Could not load this invoice."
          );
        }
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [token]);

  // Coming back from Paystack: confirm before claiming anything happened.
  useEffect(() => {
    if (!reference || !token) return;
    let cancelled = false;
    setVerifying(true);
    invoicesApi
      .publicVerify(token, reference)
      .then((result) => {
        if (cancelled) return;
        setInvoice(result.invoice);
        if (result.success) {
          setPaidJustNow(true);
          toast.success("Payment received — thank you!");
        } else {
          toast.error("Paystack has not confirmed this payment yet. This page will update when it does.");
        }
      })
      .catch((err: any) => {
        if (!cancelled) toast.error(err?.response?.data?.detail || "Could not confirm the payment");
      })
      .finally(() => !cancelled && setVerifying(false));
    return () => {
      cancelled = true;
    };
  }, [reference, token]);

  useEffect(() => {
    if (printMode && invoice) {
      const timer = setTimeout(() => window.print(), 450);
      return () => clearTimeout(timer);
    }
  }, [printMode, invoice]);

  const pay = async () => {
    if (!invoice) return;
    // Paying must never be blocked by a form field. A receipt address is a
    // nice-to-have; the server falls back to a placeholder when it is absent.
    const typed = (email || invoice.customer.email || "").trim();
    const address = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(typed) ? typed : undefined;
    setPaying(true);
    try {
      const result = await invoicesApi.publicPay(token, address);
      // Same-tab redirect: Paystack returns to this page with the reference.
      window.location.href = result.authorization_url;
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || "Could not start the payment");
      setPaying(false);
    }
  };

  if (loading) {
    return (
      <div className="min-h-[100dvh] bg-canvas flex items-center justify-center p-4">
        <div className="card p-8 w-full max-w-md text-center">
          <div className="animate-spin rounded-full h-9 w-9 border-2 border-primary-200 border-t-primary-800 mx-auto" />
          <p className="text-sm text-gray-500 mt-4">Opening your invoice…</p>
        </div>
      </div>
    );
  }

  if (error || !invoice) {
    return (
      <div className="min-h-[100dvh] bg-canvas flex items-center justify-center p-4">
        <div className="card p-8 w-full max-w-md text-center animate-fade-up">
          <span className="w-14 h-14 rounded-full bg-danger-50 dark:bg-danger-950/40 text-danger-600 flex items-center justify-center mx-auto mb-4">
            <XCircle size={24} />
          </span>
          <h1 className="text-lg font-semibold">Invoice unavailable</h1>
          <p className="text-sm text-gray-500 mt-2">{error}</p>
        </div>
      </div>
    );
  }

  const paid = invoice.status === "paid" || invoice.balance_minor === 0;
  const voided = invoice.status === "void";
  const business = invoice.business;

  return (
    <div className="min-h-[100dvh] bg-canvas dark:bg-canvas-dark py-5 sm:py-10 px-3 sm:px-4">
      <div className="max-w-2xl mx-auto">
        {/* Brand bar */}
        <div className="flex items-center justify-between gap-3 px-1 mb-4 print:hidden">
          <div className="flex items-center gap-2">
            <BrandMark className="w-8 h-8 rounded-xl" />
            <span className="text-sm font-semibold text-primary-950 dark:text-white lowercase tracking-tight">
              sendify
            </span>
          </div>
          <span className="text-[10px] text-gray-400 flex items-center gap-1">
            <Lock size={11} /> Secure page
          </span>
        </div>

        <div className="bg-white dark:bg-gray-800 rounded-[26px] sm:rounded-[32px] shadow-shell overflow-hidden animate-fade-up">
          {/* Header */}
          <div className="relative overflow-hidden px-5 sm:px-8 pt-6 sm:pt-8 pb-6 bg-white dark:bg-gray-800">
            <div className="absolute -top-24 -right-16 w-64 h-64 rounded-full bg-primary-50 dark:bg-primary-950/30 blur-2xl pointer-events-none" />
            <div className="relative flex items-start justify-between gap-4">
              <div className="min-w-0">
                <p className="eyebrow">{invoice.kind === "link" ? "Payment link" : "Invoice"}</p>
                <h1 className="text-2xl sm:text-3xl font-semibold tracking-[-0.03em] mt-1 break-words">
                  {invoice.kind === "link" ? invoice.link_title || invoice.title : invoice.number}
                </h1>
                <p className="text-xs text-gray-400 mt-1.5">
                  {invoice.kind === "link"
                    ? `${business?.name || "Pay securely"} · Ref. ${invoice.number}`
                    : `Issued ${formatDate(invoice.issue_date)} · Due ${formatDate(invoice.due_date)}`}
                </p>
              </div>
              {business?.logo_url ? (
                <img
                  src={business.logo_url}
                  alt={business.name}
                  className="w-12 h-12 rounded-2xl object-cover shrink-0"
                />
              ) : (
                <span className="w-12 h-12 rounded-2xl bg-primary-50 dark:bg-primary-950/50 text-primary-900 dark:text-primary-200 flex items-center justify-center text-sm font-semibold shrink-0">
                  {(business?.name || "IN").slice(0, 2).toUpperCase()}
                </span>
              )}
            </div>

            <div className="relative mt-6 flex flex-wrap items-end justify-between gap-4">
              <div className="min-w-0">
                <p className="text-[10px] uppercase tracking-[0.16em] text-gray-400">From</p>
                <p className="text-sm font-semibold mt-1 truncate">{business?.name || "Your supplier"}</p>
                <p className="text-[11px] text-gray-500 whitespace-pre-line max-w-[260px]">
                  {[business?.address, business?.email, business?.phone].filter(Boolean).join("\n")}
                </p>
              </div>
              {(invoice.kind !== "link" || invoice.customer.name !== "Customer" || invoice.customer.email || invoice.customer.phone) && (
              <div className="min-w-0">
                <p className="text-[10px] uppercase tracking-[0.16em] text-gray-400">Bill to</p>
                <p className="text-sm font-semibold mt-1 truncate">{invoice.customer.name}</p>
                <p className="text-[11px] text-gray-500 break-anywhere max-w-[260px]">
                  {[invoice.customer.email, invoice.customer.phone].filter(Boolean).join(" · ")}
                </p>
                {invoice.customer.address && (
                  <p className="text-[11px] text-gray-400 mt-0.5 whitespace-pre-line">{invoice.customer.address}</p>
                )}
              </div>
              )}
              <div className="shrink-0">
                <StatusPill status={invoice.status} />
              </div>
            </div>
          </div>

          {/* Amount due */}
          <div className="mx-4 sm:mx-6 rounded-2xl bg-primary-950 dark:bg-primary-900/60 text-white px-5 py-4 flex items-center justify-between gap-3">
            <div>
              <p className="text-[10px] uppercase tracking-[0.16em] text-white/50">
                {paid ? "Total paid" : "Amount due"}
              </p>
              <p className="text-2xl sm:text-[28px] font-semibold tracking-tight mt-1 tabular-nums">
                {formatMoney(paid ? invoice.total_minor : invoice.balance_minor, invoice.currency)}
              </p>
            </div>
            {paid ? (
              <span className="w-10 h-10 rounded-full bg-success-400/20 text-success-200 flex items-center justify-center">
                <CheckCircle2 size={20} />
              </span>
            ) : (
              <span className="w-10 h-10 rounded-full bg-white/10 text-white/80 flex items-center justify-center">
                <CreditCard size={19} />
              </span>
            )}
          </div>

          {/* Items */}
          <div className="px-4 sm:px-6 py-5">
            {invoice.kind !== "link" && (
              <div className="hidden sm:grid grid-cols-[1fr_60px_120px] gap-3 pb-2 text-[10px] uppercase tracking-[0.14em] text-gray-400">
                <span>Description</span>
                <span className="text-right">Qty</span>
                <span className="text-right">Amount</span>
              </div>
            )}
            <div className="divide-y divide-gray-100 dark:divide-gray-700/60">
              {invoice.items.map((item, index) => (
                <div key={item.id || index} className="py-3 grid grid-cols-[1fr_auto] sm:grid-cols-[1fr_60px_120px] gap-2 sm:gap-3">
                  <div className="min-w-0">
                    <p className="text-sm font-medium break-anywhere">{item.description}</p>
                    <p className="text-[11px] text-gray-400 mt-0.5 sm:hidden">
                      {item.quantity} × {formatMoney(item.unit_price_minor, invoice.currency)}
                    </p>
                  </div>
                  <p className="hidden sm:block text-right text-xs text-gray-500 tabular-nums">{item.quantity}</p>
                  <p className="text-right text-sm tabular-nums">
                    {formatMoney(item.amount_minor ?? 0, invoice.currency)}
                  </p>
                </div>
              ))}
            </div>

            <div className="mt-4 ml-auto max-w-[260px] space-y-1.5 text-xs">
              <div className="flex justify-between text-gray-500">
                <span>Subtotal</span>
                <span className="tabular-nums">{formatMoney(invoice.subtotal_minor, invoice.currency)}</span>
              </div>
              {invoice.discount_minor > 0 && (
                <div className="flex justify-between text-gray-500">
                  <span>Discount</span>
                  <span className="tabular-nums">− {formatMoney(invoice.discount_minor, invoice.currency)}</span>
                </div>
              )}
              {invoice.tax_minor > 0 && (
                <div className="flex justify-between text-gray-500">
                  <span>VAT ({invoice.tax_rate}%)</span>
                  <span className="tabular-nums">{formatMoney(invoice.tax_minor, invoice.currency)}</span>
                </div>
              )}
              <div className="flex justify-between pt-2 border-t border-gray-200 dark:border-gray-700 text-sm font-semibold">
                <span>Total</span>
                <span className="tabular-nums">{formatMoney(invoice.total_minor, invoice.currency)}</span>
              </div>
              {invoice.amount_paid_minor > 0 && invoice.balance_minor > 0 && (
                <div className="flex justify-between text-success-700 dark:text-success-300">
                  <span>Already paid</span>
                  <span className="tabular-nums">− {formatMoney(invoice.amount_paid_minor, invoice.currency)}</span>
                </div>
              )}
            </div>
          </div>

          {/* Notes */}
          {(invoice.notes || invoice.terms || invoice.footer) && (
            <div className="mx-4 sm:mx-6 mb-5 rounded-2xl bg-gray-50 dark:bg-gray-900/30 p-4 space-y-2">
              {invoice.notes && (
                <p className="text-xs text-gray-600 dark:text-gray-300 whitespace-pre-line">{invoice.notes}</p>
              )}
              {invoice.terms && (
                <p className="text-[11px] text-gray-400 whitespace-pre-line">{invoice.terms}</p>
              )}
              {invoice.footer && (
                <p className="text-[10px] text-gray-400">{invoice.footer}</p>
              )}
            </div>
          )}

          {/* Pay area */}
          <div className="px-4 sm:px-6 pb-6 pt-1 print:hidden">
            {verifying ? (
              <div className="rounded-2xl bg-primary-50 dark:bg-primary-950/40 px-4 py-4 flex items-center justify-center gap-2 text-sm text-primary-900 dark:text-primary-200">
                <Loader2 size={16} className="animate-spin" /> Confirming your payment…
              </div>
            ) : paidJustNow || paid ? (
              <div className="rounded-2xl bg-success-50 dark:bg-success-950/40 px-4 py-4 text-center">
                <CheckCircle2 size={22} className="mx-auto text-success-600 mb-1.5" />
                <p className="text-sm font-semibold text-success-800 dark:text-success-300">Paid in full</p>
                {invoice.paid_at && (
                  <p className="text-[11px] text-success-700/70 dark:text-success-400/80 mt-0.5">
                    Received {formatDateTime(invoice.paid_at)}
                  </p>
                )}
                <p className="text-[11px] text-gray-500 mt-1">A receipt email is on its way from Paystack.</p>
              </div>
            ) : voided ? (
              <div className="rounded-2xl bg-gray-100 dark:bg-gray-900/40 px-4 py-4 text-center text-sm text-gray-500">
                This invoice has been voided. No payment is due.
              </div>
            ) : (
              <div className="space-y-3">
                {!invoice.customer.email ? (
                  <div>
                    <label className="label">
                      Email for a receipt <span className="text-gray-400 font-normal">(optional)</span>
                    </label>
                    <div className="relative">
                      <Mail size={15} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-gray-400" />
                      <input
                        className="input pl-9"
                        type="email"
                        value={email}
                        onChange={(e) => setEmail(e.target.value)}
                        placeholder="you@example.com"
                      />
                    </div>
                  </div>
                ) : null}
                <button
                  onClick={pay}
                  disabled={paying}
                  className="btn-primary w-full min-h-[52px] rounded-2xl text-[15px]"
                >
                  {paying ? (
                    <><Loader2 size={17} className="animate-spin" /> Opening Paystack…</>
                  ) : (
                    <><CreditCard size={17} /> Pay {formatMoney(invoice.balance_minor, invoice.currency)}</>
                  )}
                </button>
                <p className="text-[10.5px] text-gray-400 text-center flex items-center justify-center gap-1.5">
                  <ShieldCheck size={12} /> Card, bank transfer and USSD · secured by Paystack
                </p>
              </div>
            )}
          </div>
        </div>

        <p className="text-center text-[10px] text-gray-400 mt-4 px-3">
          {business?.name || "This business"} · invoice {invoice.number}
          {business?.website ? ` · ${business.website}` : ""}
          <br />
          <span className="inline-flex items-center gap-1 mt-1">
            <Lock size={9} /> A private link, unique to this invoice.
          </span>
        </p>
      </div>
    </div>
  );
}
