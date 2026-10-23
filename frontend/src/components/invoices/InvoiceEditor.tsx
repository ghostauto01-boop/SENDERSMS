import { useEffect, useMemo, useRef, useState } from "react";
import toast from "react-hot-toast";
import {
  CalendarDays, Check, ChevronDown, FileText, Link2, Loader2, Plus, Search,
  Sparkles, Trash2, UserRound, X,
} from "lucide-react";
import api from "../../api/client";
import invoicesApi, { Invoice, InvoiceSettings } from "../../api/invoices";
import {
  CURRENCY_CODES, CURRENCY_META, dateInputToIso, minorToInput,
  parseMoneyToMinor, toDateInput,
} from "../../utils/money";

/**
 * Create or edit an invoice — every field stays editable, including a campaign
 * that is already running (the server recomputes totals on every save).
 *
 * The form deliberately mirrors a paper invoice: who it is for, what it covers,
 * what it costs, then the terms. A phone gets the same fields stacked; a
 * desktop gets a two-column sheet with a live total that never lags behind.
 */

type Row = {
  key: string;
  description: string;
  quantity: string;
  price: string;
};

type ContactLite = {
  id: number;
  first_name?: string | null;
  last_name?: string | null;
  business_name?: string | null;
  phone_number?: string | null;
  email?: string | null;
  address?: string | null;
  city?: string | null;
  state?: string | null;
};

const newKey = () =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `row-${Math.random().toString(36).slice(2)}`;

const emptyRow = (): Row => ({ key: newKey(), description: "", quantity: "1", price: "" });

function contactName(contact: ContactLite) {
  return (
    [contact.first_name, contact.last_name].filter(Boolean).join(" ") ||
    contact.business_name ||
    contact.email ||
    contact.phone_number ||
    "Contact"
  );
}

export default function InvoiceEditor({
  invoice,
  settings,
  onClose,
  onSaved,
}: {
  invoice?: Invoice | null;
  settings: InvoiceSettings | null;
  onClose: () => void;
  onSaved: (invoice: Invoice, message?: string) => void;
}) {
  const editing = !!invoice;
  const currencyOptions = settings?.currencies?.length ? settings.currencies : CURRENCY_CODES;

  const [customerName, setCustomerName] = useState(invoice?.customer.name || "");
  const [customerEmail, setCustomerEmail] = useState(invoice?.customer.email || "");
  const [customerPhone, setCustomerPhone] = useState(invoice?.customer.phone || "");
  const [customerBusiness, setCustomerBusiness] = useState(invoice?.customer.business || "");
  const [customerAddress, setCustomerAddress] = useState(invoice?.customer.address || "");
  const [contactId, setContactId] = useState<number | null>(invoice?.customer.contact_id ?? null);

  const [currency, setCurrency] = useState(invoice?.currency || settings?.default_currency || "NGN");
  const [issueDate, setIssueDate] = useState(
    toDateInput(invoice?.issue_date, new Date())
  );
  const [dueDate, setDueDate] = useState(
    toDateInput(invoice?.due_date, new Date(Date.now() + 14 * 86400000))
  );

  const [rows, setRows] = useState<Row[]>(
    invoice?.items?.length
      ? invoice.items.map((item) => ({
          key: newKey(),
          description: item.description,
          quantity: String(item.quantity ?? 1),
          price: minorToInput(item.unit_price_minor),
        }))
      : [emptyRow()]
  );
  const [discount, setDiscount] = useState(minorToInput(invoice?.discount_minor) || "");
  const [taxRate, setTaxRate] = useState(
    String(invoice?.tax_rate ?? settings?.default_tax_rate ?? 0)
  );
  const [notes, setNotes] = useState(invoice?.notes ?? settings?.default_notes ?? "");
  const [terms, setTerms] = useState(invoice?.terms ?? settings?.default_terms ?? "");

  const [lookup, setLookup] = useState("");
  const [suggestions, setSuggestions] = useState<ContactLite[]>([]);
  const [lookupOpen, setLookupOpen] = useState(false);
  const [saving, setSaving] = useState<null | "draft" | "send">(null);
  const [error, setError] = useState<string | null>(null);
  const lookupTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !saving) onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose, saving]);

  // Find an existing contact so an invoice is never a dead end: choosing one
  // links the invoice to the CRM record and fills the billing block.
  useEffect(() => {
    if (lookupTimer.current) clearTimeout(lookupTimer.current);
    if (lookup.trim().length < 2) {
      setSuggestions([]);
      return;
    }
    lookupTimer.current = setTimeout(async () => {
      try {
        const { data } = await api.get("/contacts/", {
          params: { search: lookup.trim(), per_page: 5, page: 1 },
        });
        setSuggestions((data.items || []) as ContactLite[]);
        setLookupOpen(true);
      } catch {
        setSuggestions([]);
      }
    }, 280);
    return () => {
      if (lookupTimer.current) clearTimeout(lookupTimer.current);
    };
  }, [lookup]);

  const pickContact = (contact: ContactLite) => {
    setContactId(contact.id);
    setCustomerName(contactName(contact));
    setCustomerEmail(contact.email || "");
    setCustomerPhone(contact.phone_number || "");
    setCustomerBusiness(contact.business_name || "");
    setCustomerAddress(
      [contact.address, contact.city, contact.state].filter(Boolean).join(", ")
    );
    setLookup("");
    setLookupOpen(false);
    setSuggestions([]);
  };

  const subtotalMinor = useMemo(
    () =>
      rows.reduce((sum, row) => {
        const qty = Number(row.quantity) || 0;
        return sum + Math.round(qty * parseMoneyToMinor(row.price));
      }, 0),
    [rows]
  );
  const discountMinor = Math.min(parseMoneyToMinor(discount), subtotalMinor);
  const taxPercent = Math.max(0, Math.min(100, Number(taxRate) || 0));
  const taxMinor = Math.round(((subtotalMinor - discountMinor) * taxPercent) / 100);
  const totalMinor = subtotalMinor - discountMinor + taxMinor;
  const symbol = CURRENCY_META[String(currency).toUpperCase()]?.symbol || `${currency} `;

  const updateRow = (key: string, patch: Partial<Row>) =>
    setRows((current) => current.map((row) => (row.key === key ? { ...row, ...patch } : row)));

  const removeRow = (key: string) =>
    setRows((current) => (current.length === 1 ? current : current.filter((row) => row.key !== key)));

  const submit = async (mode: "draft" | "send") => {
    setError(null);
    // A customer name is a convenience, not a requirement: without one the
    // server falls back to the first line item, and the link still works.
    if (customerEmail.trim() && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(customerEmail.trim())) {
      setError("That customer email address does not look right.");
      return;
    }
    const cleanRows = rows.filter((row) => row.description.trim());
    if (!cleanRows.length) {
      setError("Add at least one line item with a description.");
      return;
    }
    if (totalMinor <= 0) {
      setError("The invoice total must be greater than zero.");
      return;
    }
    if (issueDate && dueDate && dueDate < issueDate) {
      setError("The due date cannot be before the issue date.");
      return;
    }
    const issueIso = dateInputToIso(issueDate);
    const dueIso = dateInputToIso(dueDate);

    const payload = {
      customer_name: customerName.trim() || null,
      customer_email: customerEmail.trim() || null,
      customer_phone: customerPhone.trim() || null,
      customer_address: customerAddress.trim() || null,
      customer_business: customerBusiness.trim() || null,
      contact_id: contactId,
      currency,
      issue_date: issueIso,
      due_date: dueIso,
      items: cleanRows.map((row) => ({
        description: row.description.trim(),
        quantity: Math.max(0.001, Number(row.quantity) || 1),
        unit_price_minor: parseMoneyToMinor(row.price),
      })),
      discount_minor: discountMinor,
      tax_rate: taxPercent,
      notes: notes.trim() || null,
      terms: terms.trim() || null,
    };

    setSaving(mode);
    try {
      if (editing && invoice) {
        let saved = await invoicesApi.update(invoice.id, payload);
        if (mode === "send") {
          const sent = await invoicesApi.send(invoice.id, true);
          saved = sent.invoice;
          onSaved(saved, sent.message);
        } else {
          onSaved(saved, "Invoice saved.");
        }
      } else {
        const created = await invoicesApi.create({
          ...payload,
          send: mode === "send",
          // Only ask the server to email when there is actually an address.
          send_email: !!customerEmail.trim(),
        });
        onSaved(created.invoice, created.message);
      }
    } catch (err: any) {
      const message = err?.response?.data?.detail || "Could not save this invoice.";
      setError(message);
      toast.error(message);
    } finally {
      setSaving(null);
    }
  };

  return (
    <div className="fixed inset-0 z-[70] flex items-end sm:items-center justify-center bg-primary-950/40 backdrop-blur-sm sm:p-5">
      <div className="bg-white dark:bg-gray-800 w-full sm:max-w-4xl h-[95dvh] sm:h-auto sm:max-h-[92vh] rounded-t-[28px] sm:rounded-[28px] shadow-pop flex flex-col animate-sheet-in overflow-hidden">
        {/* Header */}
        <div className="shrink-0 flex items-start justify-between gap-3 px-4 sm:px-6 pt-5 pb-4 border-b border-gray-100 dark:border-gray-700/60">
          <div>
            <p className="eyebrow flex items-center gap-1.5">
              <Sparkles size={12} /> Invoice studio
            </p>
            <h2 className="text-xl sm:text-2xl font-semibold tracking-tight mt-1.5">
              {editing ? `Edit ${invoice?.number}` : "New invoice"}
            </h2>
            <p className="text-xs text-gray-500 mt-1">
              {editing
                ? "Every field stays editable — totals are recalculated when you save."
                : "Need something quick? Use “New link” instead — no customer details at all."}
            </p>
          </div>
          <button
            onClick={onClose}
            disabled={!!saving}
            className="icon-btn shrink-0"
            aria-label="Close invoice editor"
          >
            <X size={17} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-4 sm:px-6 py-5 space-y-5 wa-scrollbar">
          {error && (
            <div className="rounded-2xl bg-danger-50 dark:bg-danger-950/40 text-danger-700 dark:text-danger-300 px-4 py-3 text-sm animate-fade-up">
              {error}
            </div>
          )}

          {/* Customer */}
          <section className="rounded-3xl border border-gray-200/80 dark:border-gray-700/70 p-4 sm:p-5">
            <div className="flex items-center gap-2 mb-3">
              <span className="w-7 h-7 rounded-full bg-primary-50 dark:bg-primary-950/50 flex items-center justify-center">
                <UserRound size={14} className="text-primary-800 dark:text-primary-300" />
              </span>
              <h3 className="text-sm font-semibold">Bill to</h3>
              <span className="text-[10.5px] text-gray-400 ml-1">optional</span>
              {contactId && (
                <span className="badge-green ml-auto gap-1">
                  <Check size={11} /> Linked contact
                </span>
              )}
            </div>

            <div className="relative mb-3">
              <div className="search-pill bg-gray-50 dark:bg-gray-900/40 py-2">
                <Search size={14} className="text-gray-400" />
                <input
                  value={lookup}
                  onChange={(e) => setLookup(e.target.value)}
                  onFocus={() => suggestions.length && setLookupOpen(true)}
                  placeholder="Find a saved contact (optional)"
                  aria-label="Find a saved contact"
                />
              </div>
              {lookupOpen && suggestions.length > 0 && (
                <div className="absolute z-20 left-0 right-0 mt-1.5 rounded-2xl bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 shadow-pop overflow-hidden animate-pop-in">
                  {suggestions.map((contact) => (
                    <button
                      key={contact.id}
                      type="button"
                      onClick={() => pickContact(contact)}
                      className="w-full text-left px-4 py-2.5 hover:bg-primary-50 dark:hover:bg-primary-950/40 transition-colors"
                    >
                      <span className="block text-sm font-medium">{contactName(contact)}</span>
                      <span className="block text-[11px] text-gray-400">
                        {[contact.email, contact.phone_number].filter(Boolean).join(" · ")}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="sm:col-span-2">
                <label className="label">Customer / business name</label>
                <input
                  className="input"
                  value={customerName}
                  onChange={(e) => {
                    setCustomerName(e.target.value);
                    setContactId(null);
                  }}
                  placeholder="Optional — e.g. Adaeze Okafor"
                  autoFocus={!editing}
                />
              </div>
              <div>
                <label className="label">Email <span className="text-gray-400 font-normal">(optional)</span></label>
                <input
                  type="email"
                  className="input"
                  value={customerEmail}
                  onChange={(e) => setCustomerEmail(e.target.value)}
                  placeholder="billing@acme.com"
                />
              </div>
              <div>
                <label className="label">Phone <span className="text-gray-400 font-normal">(optional)</span></label>
                <input
                  className="input"
                  value={customerPhone}
                  onChange={(e) => setCustomerPhone(e.target.value)}
                  placeholder="+234 801 234 5678"
                />
              </div>
              <div className="sm:col-span-2">
                <label className="label">Billing address <span className="text-gray-400 font-normal">(optional)</span></label>
                <textarea
                  className="input resize-none"
                  rows={2}
                  value={customerAddress}
                  onChange={(e) => setCustomerAddress(e.target.value)}
                  placeholder="Street, city, state"
                />
              </div>
            </div>
          </section>

          {/* Dates & currency */}
          <section className="rounded-3xl border border-gray-200/80 dark:border-gray-700/70 p-4 sm:p-5">
            <div className="flex items-center gap-2 mb-3">
              <span className="w-7 h-7 rounded-full bg-primary-50 dark:bg-primary-950/50 flex items-center justify-center">
                <CalendarDays size={14} className="text-primary-800 dark:text-primary-300" />
              </span>
              <h3 className="text-sm font-semibold">Invoice details</h3>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <div>
                <label className="label">Currency</label>
                <div className="relative">
                  <select
                    className="input appearance-none pr-8"
                    value={currency}
                    onChange={(e) => setCurrency(e.target.value)}
                  >
                    {currencyOptions.map((code) => (
                      <option key={code} value={code}>
                        {CURRENCY_META[code]?.symbol?.trim()} {code}
                      </option>
                    ))}
                  </select>
                  <ChevronDown size={14} className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 pointer-events-none" />
                </div>
              </div>
              <div>
                <label className="label">Issue date</label>
                <input
                  type="date"
                  className="input"
                  value={issueDate}
                  onChange={(e) => setIssueDate(e.target.value)}
                />
              </div>
              <div>
                <label className="label">Due date</label>
                <input
                  type="date"
                  className="input"
                  value={dueDate}
                  onChange={(e) => setDueDate(e.target.value)}
                />
              </div>
              <div>
                <label className="label">Number</label>
                <div className="input bg-gray-50 dark:bg-gray-900/30 text-gray-500 flex items-center">
                  {invoice?.number || "Generated on save"}
                </div>
              </div>
            </div>
          </section>

          {/* Items */}
          <section className="rounded-3xl border border-gray-200/80 dark:border-gray-700/70 p-4 sm:p-5">
            <div className="flex items-center gap-2 mb-3">
              <span className="w-7 h-7 rounded-full bg-primary-50 dark:bg-primary-950/50 flex items-center justify-center">
                <FileText size={14} className="text-primary-800 dark:text-primary-300" />
              </span>
              <h3 className="text-sm font-semibold">Line items</h3>
              <span className="text-[11px] text-gray-400 ml-auto hidden sm:inline">
                {rows.length} line{rows.length === 1 ? "" : "s"}
              </span>
            </div>

            <div className="hidden sm:grid grid-cols-[1fr_84px_140px_110px_36px] gap-2 px-1 pb-2 text-[10px] uppercase tracking-[0.12em] text-gray-400">
              <span>Description</span>
              <span>Qty</span>
              <span>Rate</span>
              <span className="text-right">Amount</span>
              <span />
            </div>

            <div className="space-y-2.5">
              {rows.map((row) => {
                const amount = Math.round((Number(row.quantity) || 0) * parseMoneyToMinor(row.price));
                return (
                  <div
                    key={row.key}
                    className="grid grid-cols-2 sm:grid-cols-[1fr_84px_140px_110px_36px] gap-2 items-start
                               rounded-2xl bg-gray-50/70 dark:bg-gray-900/25 p-2.5 sm:bg-transparent sm:p-0"
                  >
                    <input
                      className="input col-span-2 sm:col-span-1"
                      value={row.description}
                      onChange={(e) => updateRow(row.key, { description: e.target.value })}
                      placeholder="What are you charging for?"
                      aria-label="Line item description"
                    />
                    <input
                      className="input"
                      inputMode="decimal"
                      value={row.quantity}
                      onChange={(e) => updateRow(row.key, { quantity: e.target.value })}
                      placeholder="1"
                      aria-label="Quantity"
                    />
                    <div className="relative">
                      <span className="absolute left-3 top-1/2 -translate-y-1/2 text-[11px] text-gray-400">
                        {symbol.trim()}
                      </span>
                      <input
                        className="input pl-7"
                        inputMode="decimal"
                        value={row.price}
                        onChange={(e) => updateRow(row.key, { price: e.target.value })}
                        placeholder="0.00"
                        aria-label="Unit price"
                      />
                    </div>
                    <div className="flex items-center justify-end gap-1 h-11 sm:h-auto sm:pt-2.5">
                      <span className="text-sm font-medium tabular-nums whitespace-nowrap">
                        {amount
                          ? `${symbol}${(amount / 100).toLocaleString(undefined, {
                              minimumFractionDigits: 2, maximumFractionDigits: 2,
                            })}`
                          : "—"}
                      </span>
                      <button
                        type="button"
                        onClick={() => removeRow(row.key)}
                        disabled={rows.length === 1}
                        className="sm:hidden p-1.5 text-gray-400 hover:text-danger-600 disabled:opacity-30"
                        aria-label="Remove line"
                      >
                        <Trash2 size={15} />
                      </button>
                    </div>
                    <button
                      type="button"
                      onClick={() => removeRow(row.key)}
                      disabled={rows.length === 1}
                      className="hidden sm:flex mt-1.5 w-8 h-8 items-center justify-center rounded-lg text-gray-400
                                 hover:text-danger-600 hover:bg-danger-50 dark:hover:bg-danger-950/40 disabled:opacity-30 transition-colors"
                      aria-label="Remove line"
                    >
                      <Trash2 size={15} />
                    </button>
                  </div>
                );
              })}
            </div>

            <button
              type="button"
              onClick={() => setRows((current) => [...current, emptyRow()])}
              className="mt-3 inline-flex items-center gap-1.5 text-xs font-semibold text-primary-800 dark:text-primary-300
                         hover:text-primary-950 dark:hover:text-primary-200 transition-colors"
            >
              <Plus size={14} /> Add line item
            </button>
          </section>

          {/* Totals + notes */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
            <section className="rounded-3xl border border-gray-200/80 dark:border-gray-700/70 p-4 sm:p-5 space-y-3">
              <h3 className="text-sm font-semibold">Notes & terms</h3>
              <div>
                <label className="label">Note to the customer</label>
                <textarea
                  className="input resize-none"
                  rows={3}
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  placeholder="A short thank-you or delivery detail…"
                />
              </div>
              <div>
                <label className="label">Payment terms</label>
                <textarea
                  className="input resize-none"
                  rows={3}
                  value={terms}
                  onChange={(e) => setTerms(e.target.value)}
                  placeholder="e.g. Payment due within 14 days by bank transfer."
                />
              </div>
            </section>

            <section className="rounded-3xl bg-primary-950 dark:bg-primary-950/70 text-white p-4 sm:p-5 relative overflow-hidden">
              <div className="absolute -top-16 -right-10 w-44 h-44 rounded-full bg-accent-400/10 blur-2xl pointer-events-none" />
              <h3 className="text-sm font-semibold mb-4 relative">Summary</h3>
              <div className="space-y-2.5 text-sm relative">
                <div className="flex justify-between text-white/65">
                  <span>Subtotal</span>
                  <span className="tabular-nums">{symbol}{(subtotalMinor / 100).toLocaleString(undefined, { minimumFractionDigits: 2 })}</span>
                </div>
                <div className="flex justify-between items-center gap-3 text-white/65">
                  <span>Discount</span>
                  <div className="relative w-28">
                    <span className="absolute left-2.5 top-1/2 -translate-y-1/2 text-[11px] text-white/40">{symbol.trim()}</span>
                    <input
                      className="w-full bg-white/10 border border-white/15 rounded-lg pl-6 pr-2 py-1.5 text-right text-xs text-white
                                 outline-none focus:border-accent-300/60 tabular-nums"
                      inputMode="decimal"
                      value={discount}
                      onChange={(e) => setDiscount(e.target.value)}
                      placeholder="0.00"
                      aria-label="Discount"
                    />
                  </div>
                </div>
                <div className="flex justify-between items-center gap-3 text-white/65">
                  <span>Tax / VAT</span>
                  <div className="relative w-28">
                    <input
                      className="w-full bg-white/10 border border-white/15 rounded-lg pl-2 pr-6 py-1.5 text-right text-xs text-white
                                 outline-none focus:border-accent-300/60 tabular-nums"
                      inputMode="decimal"
                      value={taxRate}
                      onChange={(e) => setTaxRate(e.target.value)}
                      aria-label="Tax rate"
                    />
                    <span className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[11px] text-white/40">%</span>
                  </div>
                </div>
                {taxMinor > 0 && (
                  <div className="flex justify-between text-white/40 text-xs">
                    <span>Tax amount</span>
                    <span className="tabular-nums">{symbol}{(taxMinor / 100).toLocaleString(undefined, { minimumFractionDigits: 2 })}</span>
                  </div>
                )}
                <div className="border-t border-white/15 pt-3 flex justify-between items-end">
                  <span className="text-white/70 text-xs">Total due</span>
                  <span className="text-2xl font-semibold tracking-tight tabular-nums">
                    {symbol}
                    {(totalMinor / 100).toLocaleString(undefined, { minimumFractionDigits: 2 })}
                  </span>
                </div>
              </div>
            </section>
          </div>
        </div>

        {/* Footer */}
        <div className="shrink-0 border-t border-gray-100 dark:border-gray-700/60 bg-white/95 dark:bg-gray-800/95 backdrop-blur px-4 sm:px-6 py-3.5 safe-bottom">
          <div className="flex flex-col-reverse sm:flex-row sm:items-center gap-2 sm:justify-between">
            <button onClick={onClose} disabled={!!saving} className="btn-ghost">
              Cancel
            </button>
            <div className="flex flex-col sm:flex-row gap-2 sm:items-center">
              <button
                onClick={() => submit("draft")}
                disabled={!!saving}
                className="btn-secondary"
              >
                {saving === "draft" ? <Loader2 size={15} className="animate-spin" /> : <FileText size={15} />}
                {editing ? "Save changes" : "Save draft"}
              </button>
              <button
                onClick={() => submit("send")}
                disabled={!!saving}
                className="btn-primary"
              >
                {saving === "send" ? <Loader2 size={15} className="animate-spin" /> : <Link2 size={15} />}
                Save &amp; share link
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
