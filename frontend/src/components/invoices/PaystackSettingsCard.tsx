import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import {
  Building2, CheckCircle2, Copy, CreditCard, Eye, EyeOff, KeyRound, Loader2,
  ShieldCheck, Webhook, XCircle,
} from "lucide-react";
import invoicesApi, { InvoiceSettings } from "../../api/invoices";
import { CURRENCY_CODES, CURRENCY_META } from "../../utils/money";

/**
 * Everything the invoice generator needs in one card: the business identity
 * that prints on the invoice, the numbering/tax defaults, and the Paystack
 * keys used to collect money.
 *
 * The secret key is write-only from the browser's point of view. The API sends
 * a masked value; leaving the field untouched keeps the saved secret, typing a
 * new one replaces it, and "Remove key" explicitly clears it.
 */
export default function PaystackSettingsCard({
  onSaved,
  compact = false,
}: {
  onSaved?: (settings: InvoiceSettings) => void;
  compact?: boolean;
}) {
  const [settings, setSettings] = useState<InvoiceSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [showSecret, setShowSecret] = useState(false);
  const [secret, setSecret] = useState("");
  const [clearSecret, setClearSecret] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);

  const [form, setForm] = useState({
    business_name: "",
    business_email: "",
    business_phone: "",
    business_website: "",
    business_address: "",
    prefix: "INV",
    default_currency: "NGN",
    default_tax_rate: "0",
    default_terms: "",
    footer: "",
    paystack_public_key: "",
    paystack_channels: [] as string[],
  });

  useEffect(() => {
    invoicesApi
      .settings()
      .then((data) => {
        setSettings(data);
        setForm({
          business_name: data.business_name || "",
          business_email: data.business_email || "",
          business_phone: data.business_phone || "",
          business_website: data.business_website || "",
          business_address: data.business_address || "",
          prefix: data.prefix || "INV",
          default_currency: data.default_currency || "NGN",
          default_tax_rate: String(data.default_tax_rate ?? 0),
          default_terms: data.default_terms || "",
          footer: data.footer || "",
          paystack_public_key: data.paystack.public_key || "",
          paystack_channels: data.paystack.channels || [],
        });
      })
      .catch(() => toast.error("Could not load invoice settings"))
      .finally(() => setLoading(false));
  }, []);

  const update = (key: keyof typeof form, value: any) =>
    setForm((current) => ({ ...current, [key]: value }));

  const save = async () => {
    setSaving(true);
    try {
      const payload: Record<string, any> = {
        business_name: form.business_name,
        business_email: form.business_email,
        business_phone: form.business_phone,
        business_website: form.business_website,
        business_address: form.business_address,
        prefix: form.prefix,
        default_currency: form.default_currency,
        default_tax_rate: Number(form.default_tax_rate) || 0,
        default_terms: form.default_terms,
        footer: form.footer,
        paystack_public_key: form.paystack_public_key,
        paystack_channels: form.paystack_channels,
      };
      if (clearSecret) payload.paystack_secret_key = "";
      else if (secret.trim()) payload.paystack_secret_key = secret.trim();
      const saved = await invoicesApi.saveSettings(payload);
      setSettings(saved);
      setSecret("");
      setClearSecret(false);
      setTestResult(null);
      toast.success("Invoice & payment settings saved");
      onSaved?.(saved);
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || "Could not save settings");
    } finally {
      setSaving(false);
    }
  };

  const test = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const result = await invoicesApi.testPaystack();
      setTestResult({
        success: !!result.success,
        message: result.success
          ? result.balances?.length
            ? `Connected — balance ${result.balances
                .map((b) => `${b.currency} ${(b.balance_minor / 100).toLocaleString()}`)
                .join(", ")}`
            : "Connected to Paystack."
          : result.error || "Paystack rejected the key",
      });
      if (result.success) toast.success("Paystack connection is working");
      else toast.error(result.error || "Paystack test failed");
    } catch (err: any) {
      setTestResult({ success: false, message: err?.response?.data?.detail || "Could not reach Paystack" });
      toast.error(err?.response?.data?.detail || "Could not reach Paystack");
    } finally {
      setTesting(false);
    }
  };

  const copyWebhook = async () => {
    const url = settings?.paystack.webhook_url;
    if (!url) return;
    try {
      await navigator.clipboard.writeText(url);
      toast.success("Webhook URL copied");
    } catch {
      window.prompt("Copy the webhook URL", url);
    }
  };

  const toggleChannel = (channel: string) => {
    const current = form.paystack_channels;
    update(
      "paystack_channels",
      current.includes(channel) ? current.filter((c) => c !== channel) : [...current, channel]
    );
  };

  if (loading) {
    return (
      <div className="card p-6">
        <div className="skeleton h-6 w-40 mb-4" />
        <div className="skeleton h-10 w-full mb-2" />
        <div className="skeleton h-10 w-full" />
      </div>
    );
  }

  const paystack = settings?.paystack;
  const configured = paystack?.configured;

  return (
    <div className={`card p-4 sm:p-6 ${compact ? "" : "max-w-3xl"}`}>
      <div className="flex items-start justify-between gap-3 mb-5">
        <div>
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <CreditCard size={18} /> Payments & invoicing
          </h2>
          <p className="text-xs text-gray-500 mt-1">
            Your business details print on every invoice. Paystack collects card, transfer and USSD
            payments in Naira or dollars.
          </p>
        </div>
        <span className={`badge ${configured ? "badge-green" : "badge-yellow"} shrink-0`}>
          {configured ? "Paystack connected" : "Paystack not connected"}
        </span>
      </div>

      {/* Business identity */}
      <section className="mb-5">
        <p className="eyebrow flex items-center gap-1.5 mb-3">
          <Building2 size={12} /> Invoice identity
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div className="sm:col-span-2">
            <label className="label">Business name</label>
            <input
              className="input"
              value={form.business_name}
              onChange={(e) => update("business_name", e.target.value)}
              placeholder="Pikbites Studio"
            />
          </div>
          <div>
            <label className="label">Billing email</label>
            <input
              className="input"
              type="email"
              value={form.business_email}
              onChange={(e) => update("business_email", e.target.value)}
              placeholder="billing@yourbusiness.com"
            />
          </div>
          <div>
            <label className="label">Phone</label>
            <input
              className="input"
              value={form.business_phone}
              onChange={(e) => update("business_phone", e.target.value)}
              placeholder="+234 …"
            />
          </div>
          <div>
            <label className="label">Website</label>
            <input
              className="input"
              value={form.business_website}
              onChange={(e) => update("business_website", e.target.value)}
              placeholder="yourbusiness.com"
            />
          </div>
          <div>
            <label className="label">Invoice prefix</label>
            <input
              className="input"
              value={form.prefix}
              onChange={(e) => update("prefix", e.target.value.toUpperCase())}
              placeholder="INV"
              maxLength={12}
            />
          </div>
          <div className="sm:col-span-2">
            <label className="label">Business address</label>
            <textarea
              className="input resize-none"
              rows={2}
              value={form.business_address}
              onChange={(e) => update("business_address", e.target.value)}
              placeholder="Street, city, state"
            />
          </div>
          <div>
            <label className="label">Default currency</label>
            <select
              className="input"
              value={form.default_currency}
              onChange={(e) => update("default_currency", e.target.value)}
            >
              {CURRENCY_CODES.map((code) => (
                <option key={code} value={code}>
                  {CURRENCY_META[code]?.symbol?.trim()} {code} — {CURRENCY_META[code]?.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="label">Default VAT / tax (%)</label>
            <input
              className="input"
              inputMode="decimal"
              value={form.default_tax_rate}
              onChange={(e) => update("default_tax_rate", e.target.value)}
              placeholder="0"
            />
          </div>
          <div className="sm:col-span-2">
            <label className="label">Default payment terms</label>
            <textarea
              className="input resize-none"
              rows={2}
              value={form.default_terms}
              onChange={(e) => update("default_terms", e.target.value)}
              placeholder="Payment is due within 14 days."
            />
          </div>
          <div className="sm:col-span-2">
            <label className="label">Footer</label>
            <input
              className="input"
              value={form.footer}
              onChange={(e) => update("footer", e.target.value)}
              placeholder="e.g. VAT ID, thank-you line"
            />
          </div>
        </div>
      </section>

      {/* Paystack */}
      <section className="mb-5 rounded-2xl border border-gray-200/80 dark:border-gray-700/70 p-4">
        <div className="flex items-center justify-between gap-3 mb-3">
          <p className="eyebrow flex items-center gap-1.5">
            <KeyRound size={12} /> Paystack API
          </p>
          {paystack && (
            <span className={`badge ${paystack.mode === "live" ? "badge-green" : paystack.mode === "test" ? "badge-yellow" : "badge-gray"}`}>
              {paystack.mode === "live" ? "Live keys" : paystack.mode === "test" ? "Test keys" : "No keys"}
            </span>
          )}
        </div>
        <div className="space-y-3">
          <div>
            <label className="label">Public key</label>
            <input
              className="input font-mono text-xs"
              value={form.paystack_public_key}
              onChange={(e) => update("paystack_public_key", e.target.value)}
              placeholder="pk_live_… or pk_test_…"
              autoComplete="off"
            />
          </div>
          <div>
            <label className="label flex items-center justify-between">
              <span>Secret key</span>
              {paystack?.secret_key_masked && (
                <span className="text-[10px] font-normal text-gray-400">
                  saved {paystack.secret_key_masked}
                </span>
              )}
            </label>
            <div className="relative">
              <input
                className="input font-mono text-xs pr-20"
                type={showSecret ? "text" : "password"}
                value={secret}
                onChange={(e) => {
                  setSecret(e.target.value);
                  setClearSecret(false);
                }}
                placeholder={paystack?.secret_key_masked ? "Leave blank to keep saved key" : "sk_live_… or sk_test_…"}
                autoComplete="new-password"
              />
              <div className="absolute right-2 top-1/2 -translate-y-1/2 flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => setShowSecret((v) => !v)}
                  className="p-1.5 text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
                  aria-label={showSecret ? "Hide secret key" : "Show secret key"}
                >
                  {showSecret ? <EyeOff size={14} /> : <Eye size={14} />}
                </button>
                {paystack?.configured && (
                  <button
                    type="button"
                    onClick={() => {
                      setClearSecret(true);
                      setSecret("");
                      toast("The key will be removed when you save.", { icon: "🔑" });
                    }}
                    className="text-[10px] font-semibold text-danger-600 px-1"
                  >
                    Remove
                  </button>
                )}
              </div>
            </div>
            <p className="text-[11px] text-gray-400 mt-1">
              Stored encrypted. It is never sent back to this browser. Get both keys from
              Paystack Dashboard → Settings → API Keys &amp; Webhooks.
            </p>
          </div>

          <div>
            <label className="label">Payment methods</label>
            <div className="flex flex-wrap gap-2">
              {[
                ["card", "Card"],
                ["bank", "Bank"],
                ["ussd", "USSD"],
                ["transfer", "Transfer"],
                ["mobile_money", "Mobile money"],
              ].map(([value, label]) => {
                const on = form.paystack_channels.includes(value);
                return (
                  <button
                    key={value}
                    type="button"
                    onClick={() => toggleChannel(value)}
                    className={`chip ${on ? "chip-active" : ""}`}
                  >
                    {on && <CheckCircle2 size={11} />} {label}
                  </button>
                );
              })}
            </div>
            <p className="text-[11px] text-gray-400 mt-1">
              Leave all off for Paystack&apos;s own defaults.
            </p>
          </div>

          {settings?.paystack.webhook_url && (
            <div className="rounded-xl bg-gray-50 dark:bg-gray-900/30 p-3">
              <p className="text-[10px] uppercase tracking-[0.12em] text-gray-400 mb-1 flex items-center gap-1">
                <Webhook size={11} /> Paystack webhook URL
              </p>
              <div className="flex items-center gap-2">
                <code className="text-[11px] break-all flex-1">{settings.paystack.webhook_url}</code>
                <button onClick={copyWebhook} className="icon-btn w-8 h-8 shrink-0" aria-label="Copy webhook URL">
                  <Copy size={13} />
                </button>
              </div>
              <p className="text-[10px] text-gray-400 mt-1">
                Paste it in Paystack → Settings → API Keys &amp; Webhooks, so payments confirm even
                if the customer closes the page.
              </p>
            </div>
          )}

          {testResult && (
            <div
              className={`rounded-xl px-3 py-2 text-xs flex items-start gap-2 ${
                testResult.success
                  ? "bg-success-50 dark:bg-success-950/40 text-success-700 dark:text-success-300"
                  : "bg-danger-50 dark:bg-danger-950/40 text-danger-700 dark:text-danger-300"
              }`}
            >
              {testResult.success ? <CheckCircle2 size={14} className="mt-0.5 shrink-0" /> : <XCircle size={14} className="mt-0.5 shrink-0" />}
              <span>{testResult.message}</span>
            </div>
          )}
        </div>
      </section>

      <div className="flex flex-col sm:flex-row gap-2 sm:justify-between">
        <button onClick={test} disabled={testing || !configured} className="btn-secondary">
          {testing ? <Loader2 size={14} className="animate-spin" /> : <ShieldCheck size={14} />}
          Test Paystack connection
        </button>
        <button onClick={save} disabled={saving} className="btn-primary">
          {saving ? <Loader2 size={14} className="animate-spin" /> : <CheckCircle2 size={14} />}
          Save settings
        </button>
      </div>
    </div>
  );
}
