import { useEffect, useMemo, useState } from "react";
import toast from "react-hot-toast";
import {
  Check, ChevronDown, Copy, ExternalLink, Link2, Loader2, MessageCircle, Send,
  Sparkles, X,
} from "lucide-react";
import invoicesApi, { Invoice, InvoiceSettings } from "../../api/invoices";
import { CURRENCY_META, parseMoneyToMinor } from "../../utils/money";

/**
 * THE PAYMENT LINK COMPOSER
 *
 * One box, one amount, one button — the shortest path from "I sold something"
 * to "here is a link the client can pay". There is no customer record, email,
 * phone number, address or tax field in this flow, because none of them are
 * needed to take a payment. (A name and an email are tucked into "Add a
 * customer" for anyone who wants a receipt.)
 *
 * Once the link exists the same sheet becomes the share sheet: copy, WhatsApp,
 * SMS, email, or the device's own share menu — because that is the step that
 * actually gets the money in.
 */

export default function PaymentLinkSheet({
  settings,
  onClose,
  onCreated,
}: {
  settings: InvoiceSettings | null;
  onClose: () => void;
  onCreated: (invoice: Invoice, message?: string) => void;
}) {
  const currencies = settings?.currencies?.length ? settings.currencies : ["NGN", "USD"];
  const [title, setTitle] = useState("");
  const [amount, setAmount] = useState("");
  const [currency, setCurrency] = useState(settings?.default_currency || "NGN");
  const [note, setNote] = useState("");
  const [dueInDays, setDueInDays] = useState("14");

  const [customerOpen, setCustomerOpen] = useState(false);
  const [customerName, setCustomerName] = useState("");
  const [customerEmail, setCustomerEmail] = useState("");

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<Invoice | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !saving) onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose, saving]);

  const symbol = CURRENCY_META[String(currency).toUpperCase()]?.symbol || `${currency} `;
  const amountMinor = useMemo(() => parseMoneyToMinor(amount), [amount]);
  const canCreate = title.trim().length > 1 && amountMinor > 0;

  const create = async () => {
    setError(null);
    if (title.trim().length < 2) {
      setError("What are you charging for? A short title is enough.");
      return;
    }
    if (amountMinor <= 0) {
      setError("Enter the amount the client should pay.");
      return;
    }
    setSaving(true);
    try {
      const result = await invoicesApi.createLink({
        title: title.trim(),
        amount_minor: amountMinor,
        currency,
        note: note.trim() || null,
        customer_name: customerName.trim() || null,
        customer_email: customerEmail.trim() || null,
        due_in_days: Math.max(0, Math.min(365, Number(dueInDays) || 14)),
      });
      setCreated(result.invoice);
      onCreated(result.invoice, result.message);
      // Let the tester / operator know the one thing that matters now.
      toast.success("Payment link ready");
    } catch (err: any) {
      const message = err?.response?.data?.detail || "Could not create the link.";
      setError(message);
      toast.error(message);
    } finally {
      setSaving(false);
    }
  };

  const link = created?.public_url || "";
  const shareText = created
    ? `${created.link_title || created.title} — ${created.currency_symbol || symbol}${(
        created.total_minor / 100
      ).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}\nPay securely here: ${link}`
    : "";

  const copy = async () => {
    if (!link) return;
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
      toast.success("Link copied");
      setTimeout(() => setCopied(false), 2200);
    } catch {
      window.prompt("Copy the payment link", link);
    }
  };

  const nativeShare = async () => {
    if (!link) return;
    if (navigator.share) {
      try {
        await navigator.share({ title: "Payment link", text: shareText, url: link });
        return;
      } catch {
        /* the user cancelled — fall through to copy */
      }
    }
    copy();
  };

  return (
    <div className="fixed inset-0 z-[80] flex items-end sm:items-center justify-center bg-primary-950/45 backdrop-blur-sm sm:p-5">
      <div className="bg-white dark:bg-gray-800 w-full sm:max-w-lg rounded-t-[28px] sm:rounded-[28px] shadow-pop flex flex-col max-h-[94dvh] animate-sheet-in overflow-hidden">
        {/* Header */}
        <div className="shrink-0 flex items-start justify-between gap-3 px-5 pt-5 pb-4 border-b border-gray-100 dark:border-gray-700/60">
          <div className="flex items-start gap-3 min-w-0">
            <span className="w-10 h-10 rounded-2xl bg-primary-950 dark:bg-primary-900 text-white flex items-center justify-center shrink-0">
              {created ? <Check size={18} /> : <Link2 size={18} />}
            </span>
            <div className="min-w-0">
              <p className="eyebrow flex items-center gap-1.5">
                <Sparkles size={11} /> {created ? "Ready to share" : "New payment link"}
              </p>
              <h2 className="text-lg font-semibold tracking-tight mt-1 truncate">
                {created ? created.link_title || created.title : "Get paid with one link"}
              </h2>
              <p className="text-[11.5px] text-gray-500 mt-0.5">
                {created
                  ? "Send it however your client talks to you — they just open and pay."
                  : "No customer details needed. Just what you sold and how much."}
              </p>
            </div>
          </div>
          <button onClick={onClose} className="icon-btn shrink-0" aria-label="Close">
            <X size={17} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-5 wa-scrollbar">
          {created ? (
            /* ------------------------------------------------ share view */
            <div className="space-y-4">
              <div className="rounded-3xl bg-primary-950 dark:bg-primary-900/70 text-white p-5 relative overflow-hidden">
                <div className="absolute -top-12 -right-8 w-40 h-40 rounded-full bg-accent-400/15 blur-2xl pointer-events-none" />
                <p className="relative text-[10px] uppercase tracking-[0.18em] text-white/50">
                  Amount to pay
                </p>
                <p className="relative text-[30px] leading-none font-semibold tracking-tight mt-2 tabular-nums">
                  {created.currency_symbol}
                  {(created.total_minor / 100).toLocaleString(undefined, {
                    minimumFractionDigits: 2,
                    maximumFractionDigits: 2,
                  })}
                </p>
                <p className="relative text-[11.5px] text-white/60 mt-2">
                  {created.link_title || created.title}
                </p>
              </div>

              <div>
                <label className="label">Your payment link</label>
                <div className="flex items-center gap-2">
                  <input readOnly value={link} className="input font-mono text-[12px] truncate" aria-label="Payment link" />
                  <button onClick={copy} className="btn-secondary shrink-0 px-3" aria-label="Copy link">
                    {copied ? <Check size={15} className="text-success-600" /> : <Copy size={15} />}
                  </button>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-2">
                <a
                  href={`https://wa.me/?text=${encodeURIComponent(shareText)}`}
                  target="_blank"
                  rel="noreferrer"
                  className="btn-secondary"
                >
                  <MessageCircle size={15} className="text-success-600" /> WhatsApp
                </a>
                <a href={`sms:?&body=${encodeURIComponent(shareText)}`} className="btn-secondary">
                  <Send size={15} className="text-primary-700 dark:text-primary-300" /> SMS
                </a>
                <a
                  href={`mailto:?subject=${encodeURIComponent(
                    created.link_title || "Payment link"
                  )}&body=${encodeURIComponent(shareText)}`}
                  className="btn-secondary"
                >
                  ✉️ Email
                </a>
                <button onClick={nativeShare} className="btn-secondary">
                  <ExternalLink size={15} /> Share…
                </button>
              </div>

              <button onClick={() => setCreated(null)} className="btn-ghost w-full text-xs">
                Create another link
              </button>
            </div>
          ) : (
            /* ------------------------------------------------- create view */
            <div className="space-y-4">
              {error && (
                <div className="rounded-2xl bg-danger-50 dark:bg-danger-950/40 text-danger-700 dark:text-danger-300 px-4 py-3 text-sm animate-fade-up">
                  {error}
                </div>
              )}

              <div>
                <label className="label">What is this for?</label>
                <input
                  className="input"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder="e.g. Logo design, Catering deposit, Consultation"
                  autoFocus
                />
              </div>

              <div>
                <label className="label">Amount</label>
                <div className="flex gap-2">
                  <div className="relative w-[104px] shrink-0">
                    <select
                      className="input appearance-none pr-7"
                      value={currency}
                      onChange={(e) => setCurrency(e.target.value)}
                      aria-label="Currency"
                    >
                      {currencies.map((code) => (
                        <option key={code} value={code}>
                          {code}
                        </option>
                      ))}
                    </select>
                    <ChevronDown
                      size={13}
                      className="absolute right-2.5 top-1/2 -translate-y-1/2 text-gray-400 pointer-events-none"
                    />
                  </div>
                  <div className="relative flex-1">
                    <span className="absolute left-3.5 top-1/2 -translate-y-1/2 text-sm text-gray-400">
                      {symbol.trim()}
                    </span>
                    <input
                      className="input pl-8 text-lg font-semibold tabular-nums"
                      inputMode="decimal"
                      value={amount}
                      onChange={(e) => setAmount(e.target.value)}
                      placeholder="0.00"
                      aria-label="Amount"
                    />
                  </div>
                </div>
                <div className="flex gap-1.5 mt-2 flex-wrap">
                  {[1000, 5000, 10000, 25000, 50000].map((quick) => (
                    <button
                      key={quick}
                      type="button"
                      onClick={() => setAmount(String(quick))}
                      className="chip"
                    >
                      {symbol.trim()}
                      {quick.toLocaleString()}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label className="label">Note for the client (optional)</label>
                <textarea
                  className="input resize-none"
                  rows={2}
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  placeholder="e.g. 50% deposit to start the design"
                />
              </div>

              <div className="rounded-2xl border border-gray-200/80 dark:border-gray-700/70">
                <button
                  type="button"
                  onClick={() => setCustomerOpen((open) => !open)}
                  className="w-full flex items-center justify-between px-4 py-3 text-left"
                >
                  <span>
                    <span className="block text-[12.5px] font-semibold">Add a customer</span>
                    <span className="block text-[11px] text-gray-500">
                      Optional — only used for the receipt and your records.
                    </span>
                  </span>
                  <ChevronDown
                    size={15}
                    className={`text-gray-400 transition-transform ${customerOpen ? "rotate-180" : ""}`}
                  />
                </button>
                {customerOpen && (
                  <div className="px-4 pb-4 space-y-3 animate-fade-up">
                    <input
                      className="input"
                      value={customerName}
                      onChange={(e) => setCustomerName(e.target.value)}
                      placeholder="Name (optional)"
                      aria-label="Customer name"
                    />
                    <input
                      className="input"
                      type="email"
                      value={customerEmail}
                      onChange={(e) => setCustomerEmail(e.target.value)}
                      placeholder="Email for a receipt (optional)"
                      aria-label="Customer email"
                    />
                  </div>
                )}
              </div>

              <label className="flex items-center justify-between gap-3 text-xs text-gray-500">
                <span>Link expires in</span>
                <select
                  className="input py-1.5 w-auto text-sm"
                  value={dueInDays}
                  onChange={(e) => setDueInDays(e.target.value)}
                  aria-label="Link expiry"
                >
                  <option value="7">7 days</option>
                  <option value="14">14 days</option>
                  <option value="30">30 days</option>
                  <option value="0">No expiry</option>
                </select>
              </label>
            </div>
          )}
        </div>

        <div className="shrink-0 border-t border-gray-100 dark:border-gray-700/60 bg-white/95 dark:bg-gray-800/95 backdrop-blur px-5 py-3.5 safe-bottom">
          {created ? (
            <button onClick={onClose} className="btn-primary w-full">
              <Check size={16} /> Done
            </button>
          ) : (
            <button onClick={create} disabled={saving || !canCreate} className="btn-primary w-full">
              {saving ? <Loader2 size={16} className="animate-spin" /> : <Link2 size={16} />}
              {saving ? "Creating link…" : "Create payment link"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
