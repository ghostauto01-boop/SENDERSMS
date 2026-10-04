import { useCallback, useEffect, useMemo, useState } from "react";
import toast from "react-hot-toast";
import { useNavigate } from "react-router-dom";
import {
  Activity,
  BarChart3,
  CheckCircle2,
  Eye,
  FileText,
  Inbox,
  KeyRound,
  Mail,
  Megaphone,
  MousePointerClick,
  Plus,
  RefreshCw,
  Send,
  Paperclip,
  ShieldCheck,
  ShieldOff,
  Trash2,
  UserRound,
  XCircle,
} from "lucide-react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import api from "../api/client";
import emailApi, { EmailAccount, EmailMessage } from "../api/email";
import EmailSendPanel from "../components/EmailSendPanel";
import RichEmailEditor from "../components/RichEmailEditor";
import { Badge, Empty, Field, Metric, Modal, Stat, fmtDate } from "./ads/ui";

/**
 * EMAIL MANAGER
 *
 * The email twin of the SMS Manager. Sending lives on Brevo, and any number of
 * Brevo keys can be saved at once — the Senders tab is where they are added,
 * tested and picked as the default, and every campaign can override which one
 * it sends through.
 */

const SECTIONS = [
  { key: "Overview", icon: BarChart3 },
  { key: "Senders", icon: KeyRound },
  { key: "Send", icon: Send },
  { key: "Campaigns", icon: Megaphone },
  { key: "Templates", icon: FileText },
  { key: "Deliverability", icon: ShieldCheck },
  { key: "Suppression", icon: ShieldOff },
  { key: "Activity", icon: Activity },
] as const;

type Section = (typeof SECTIONS)[number]["key"];

const emptyAccount = {
  name: "",
  from_name: "",
  from_email: "",
  api_key: "",
  reply_to: "",
  daily_limit: "" as string | number,
  is_active: true,
  is_default: false,
  track_opens: true,
  track_clicks: true,
};

export default function EmailManagerPage() {
  const [section, setSection] = useState<Section>("Overview");
  const navigate = useNavigate();

  const [overview, setOverview] = useState<any>(null);
  const [accounts, setAccounts] = useState<EmailAccount[]>([]);
  const [reference, setReference] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [accountModal, setAccountModal] = useState<null | { editing?: EmailAccount }>(null);

  const load = useCallback(async () => {
    try {
      const [o, a] = await Promise.all([
        emailApi.overview({ days: 30 }),
        emailApi.listAccounts(),
      ]);
      setOverview(o);
      setAccounts(a.items);
    } catch {
      toast.error("Could not load the Email Manager");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    emailApi.reference().then(setReference).catch(() => {});
  }, [load]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-primary-600" />
      </div>
    );
  }

  const totals = overview?.totals || {};

  return (
    <div className="space-y-5 max-w-6xl mx-auto">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm font-medium text-primary-600 mb-1 flex items-center gap-1">
            <Mail size={15} /> EMAIL CHANNEL · BREVO
          </p>
          <h1 className="text-2xl sm:text-3xl font-bold">Email Manager</h1>
          <p className="text-gray-500 mt-1">
            Senders, email campaigns, templates, inbox, suppression and delivery analytics.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button className="btn-secondary" onClick={() => navigate("/email-inbox")}>
            <Inbox size={17} className="mr-1" /> Email inbox
            {overview?.unread_conversations ? (
              <span className="ml-2 badge-red">{overview.unread_conversations}</span>
            ) : null}
          </button>
          <button className="btn-primary" onClick={() => setAccountModal({})}>
            <Plus size={17} className="mr-1" /> Add sender
          </button>
        </div>
      </div>

      {!accounts.length && (
        <div className="card border-l-4 border-amber-400">
          <h3 className="font-semibold flex items-center gap-2">
            <KeyRound size={17} /> Connect Brevo to start sending email
          </h3>
          <p className="text-sm text-gray-500 mt-1">
            Create an API key at brevo.com (SMTP &amp; API → API keys), then paste it here with the
            From name and From address. You can add as many keys as you like — if one gets burned
            or rate-limited, add another and keep sending.
          </p>
          <button className="btn-primary mt-3" onClick={() => setAccountModal({})}>
            <Plus size={17} className="mr-1" /> Add my first sender
          </button>
        </div>
      )}

      <div className="flex gap-1 overflow-x-auto scrollbar-none border-b border-gray-200 dark:border-gray-700">
        {SECTIONS.map(({ key, icon: Icon }) => (
          <button
            key={key}
            onClick={() => setSection(key)}
            className={`px-3 py-3 text-sm whitespace-nowrap border-b-2 flex items-center gap-1.5 ${
              section === key
                ? "border-primary-600 text-primary-600 font-medium"
                : "border-transparent text-gray-500"
            }`}
          >
            <Icon size={15} /> {key}
          </button>
        ))}
      </div>

      {section === "Overview" && (
        <OverviewTab overview={overview} totals={totals} accounts={accounts} navigate={navigate} />
      )}
      {section === "Senders" && (
        <SendersTab
          accounts={accounts}
          reload={load}
          onEdit={(account: EmailAccount) => setAccountModal({ editing: account })}
          onAdd={() => setAccountModal({})}
        />
      )}
      {section === "Send" && <SendTab reference={reference} accounts={accounts} />}
      {section === "Campaigns" && <CampaignsTab reference={reference} accounts={accounts} />}
      {section === "Templates" && <TemplatesTab reference={reference} />}
      {section === "Deliverability" && <DeliverabilityTab accounts={accounts} />}
      {section === "Suppression" && <SuppressionTab />}
      {section === "Activity" && <ActivityTab accounts={accounts} />}

      {accountModal && (
        <AccountModal
          account={accountModal.editing}
          close={() => setAccountModal(null)}
          saved={() => {
            setAccountModal(null);
            load();
          }}
        />
      )}
    </div>
  );
}

/* ========================================================================== */
/* Overview                                                                    */
/* ========================================================================== */

function OverviewTab({ overview, totals, accounts, navigate }: any) {
  const series = overview?.series || [];
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
        <Stat label="Sent (30d)" value={totals.sent ?? 0} />
        <Stat label="Delivered" value={totals.delivered ?? 0} hint={`${totals.delivery_rate ?? 0}%`} />
        <Stat label="Opened" value={totals.opened ?? 0} hint={`${totals.open_rate ?? 0}%`} />
        <Stat label="Clicked" value={totals.clicked ?? 0} hint={`${totals.click_rate ?? 0}%`} />
        <Stat label="Replies" value={totals.replies ?? 0} hint={`${totals.reply_rate ?? 0}%`} />
        <Stat label="Failed / bounced" value={totals.failed ?? 0} hint={`${totals.failure_rate ?? 0}%`} />
        <Stat label="In queue" value={totals.queued ?? 0} />
        <Stat label="Unsubscribed" value={overview?.suppressed_total ?? 0} />
      </div>

      <div className="card">
        <h3 className="font-semibold mb-3">Email activity (30 days)</h3>
        {series.length ? (
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={series}>
                <CartesianGrid strokeDasharray="3 3" strokeOpacity={0.15} />
                <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
                <Tooltip />
                <Area type="monotone" dataKey="sent" name="Sent" stroke="#4f46e5" fill="#4f46e580" />
                <Area type="monotone" dataKey="opened" name="Opens" stroke="#0ea5e9" fill="#0ea5e940" />
                <Area type="monotone" dataKey="clicks" name="Clicks" stroke="#16a34a" fill="#16a34a40" />
                <Area type="monotone" dataKey="replies" name="Replies" stroke="#f59e0b" fill="#f59e0b40" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <Empty title="No email activity yet" body="Add a sender and send your first campaign." />
        )}
      </div>

      <div className="grid lg:grid-cols-2 gap-4">
        <div className="card">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold">Sender health</h3>
            <button className="text-sm text-primary-600" onClick={() => navigate("/email-manager")}>
              Manage
            </button>
          </div>
          {accounts.length ? (
            <div className="space-y-3">
              {accounts.map((a: EmailAccount) => (
                <div key={a.id} className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <p className="font-medium truncate flex items-center gap-2">
                      {a.name}
                      {a.is_default && <span className="badge-blue">default</span>}
                    </p>
                    <p className="text-xs text-gray-500 truncate">
                      {a.from_name} &lt;{a.from_email}&gt;
                      {a.daily_limit ? ` · ${a.sent_today}/${a.daily_limit} today` : ` · ${a.sent_today} today`}
                    </p>
                  </div>
                  {a.connection_status === "error" ? (
                    <span className="badge-red">key error</span>
                  ) : a.is_active ? (
                    <span className="badge-green">active</span>
                  ) : (
                    <span className="badge-gray">off</span>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <Empty title="No senders yet" body="Add a Brevo API key to start sending." />
          )}
        </div>

        <div className="card">
          <h3 className="font-semibold mb-3">Recent email campaigns</h3>
          {overview?.recent_campaigns?.length ? (
            <div className="space-y-3">
              {overview.recent_campaigns.map((c: any) => (
                <div key={c.id} className="text-sm">
                  <div className="flex items-center justify-between gap-2">
                    <p className="font-medium truncate">{c.name}</p>
                    <Badge value={c.status} />
                  </div>
                  <p className="text-xs text-gray-500 truncate">{c.subject || "—"}</p>
                  <p className="text-xs text-gray-500">
                    {c.messages_sent} sent · {c.messages_delivered} delivered · {c.replies} replies
                  </p>
                </div>
              ))}
            </div>
          ) : (
            <Empty title="No email campaigns yet" body="Create one from the Campaigns tab." />
          )}
        </div>
      </div>

      <div className="card">
        <h3 className="font-semibold mb-3">Latest Brevo events</h3>
        {overview?.recent_events?.length ? (
          <div className="divide-y divide-gray-100 dark:divide-gray-800">
            {overview.recent_events.map((e: any) => (
              <div key={e.id} className="py-2 flex items-center justify-between gap-3 text-sm">
                <div className="min-w-0">
                  <span className="font-medium">{e.event_type}</span>{" "}
                  <span className="text-gray-500 truncate">{e.email_address}</span>
                </div>
                <span className="text-xs text-gray-400 shrink-0">{fmtDate(e.created_at)}</span>
              </div>
            ))}
          </div>
        ) : (
          <Empty
            title="No webhook events yet"
            body="Paste each sender's webhook URL into Brevo (Transactional webhook + inbound parsing) to receive opens, clicks and replies."
          />
        )}
      </div>
    </div>
  );
}

/* ========================================================================== */
/* Senders                                                                     */
/* ========================================================================== */

function SendersTab({ accounts, reload, onEdit, onAdd }: any) {
  const [testing, setTesting] = useState<number | null>(null);
  const [testSendFor, setTestSendFor] = useState<EmailAccount | null>(null);

  const test = async (account: EmailAccount) => {
    setTesting(account.id);
    try {
      const result = await emailApi.testAccount(account.id);
      if (result.success) toast.success(`"${account.name}" is connected to Brevo`);
      else toast.error(result.error || "Brevo rejected this API key");
      reload();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not reach Brevo");
    } finally {
      setTesting(null);
    }
  };

  const makeDefault = async (account: EmailAccount) => {
    try {
      await emailApi.makeDefault(account.id);
      toast.success(`"${account.name}" is now the default sender`);
      reload();
    } catch {
      toast.error("Could not change the default sender");
    }
  };

  const toggleActive = async (account: EmailAccount) => {
    try {
      await emailApi.updateAccount(account.id, { is_active: !account.is_active });
      reload();
    } catch {
      toast.error("Could not update this sender");
    }
  };

  const remove = async (account: EmailAccount) => {
    if (!window.confirm(`Delete the sender "${account.name}"? Sends using it will fail until another is picked.`))
      return;
    try {
      await emailApi.deleteAccount(account.id);
      toast.success("Sender deleted");
      reload();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not delete this sender");
    }
  };

  const copyWebhook = async (account: EmailAccount) => {
    if (!account.webhook_url) {
      toast.error("Set PUBLIC_BASE_URL on the server to get a webhook URL");
      return;
    }
    try {
      await navigator.clipboard.writeText(account.webhook_url);
      toast.success("Webhook URL copied — paste it into Brevo");
    } catch {
      window.prompt("Copy this webhook URL into Brevo:", account.webhook_url);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-gray-500">
          Each sender is its own Brevo API key. A campaign can pick any of them, and the default is
          used when it does not.
        </p>
        <button className="btn-primary" onClick={onAdd}>
          <Plus size={16} className="mr-1" /> Add sender
        </button>
      </div>

      {accounts.length === 0 && (
        <Empty title="No Brevo senders yet" body="Add an API key + From address to send email." />
      )}

      <div className="grid md:grid-cols-2 gap-4">
        {accounts.map((account: EmailAccount) => (
          <div key={account.id} className="card space-y-3">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <h3 className="font-semibold truncate flex items-center gap-2">
                  {account.name}
                  {account.is_default && <span className="badge-blue">default</span>}
                  {!account.is_active && <span className="badge-gray">off</span>}
                </h3>
                <p className="text-sm text-gray-500 truncate">
                  {account.from_name} &lt;{account.from_email}&gt;
                </p>
                <p className="text-xs text-gray-400">
                  key {account.api_key_masked || "—"} ·{" "}
                  {account.daily_limit
                    ? `${account.sent_today}/${account.daily_limit} today`
                    : `${account.sent_today} sent today`}
                </p>
              </div>
              {account.connection_status === "error" ? (
                <span className="badge-red shrink-0 flex items-center gap-1">
                  <XCircle size={12} /> error
                </span>
              ) : account.connection_status === "connected" ? (
                <span className="badge-green shrink-0 flex items-center gap-1">
                  <CheckCircle2 size={12} /> connected
                </span>
              ) : (
                <span className="badge-gray shrink-0 flex items-center gap-1">
                  <RefreshCw size={12} /> untested
                </span>
              )}
            </div>

            {account.last_error && (
              <p className="text-xs text-red-500 line-clamp-2" title={account.last_error}>
                {account.last_error}
              </p>
            )}

            <div className="grid grid-cols-3 gap-2 text-center">
              <Metric label="Sent" value={account.sent_total ?? account.total_sent ?? 0} />
              <Metric label="Failed" value={account.failed_total ?? 0} />
              <Metric label="Campaigns" value={account.campaigns_using ?? 0} />
            </div>

            <div className="flex flex-wrap gap-2">
              <button className="btn-secondary text-sm" onClick={() => test(account)} disabled={testing === account.id}>
                {testing === account.id ? "Testing…" : "Test connection"}
              </button>
              <button className="btn-secondary text-sm" onClick={() => setTestSendFor(account)}>
                Send test email
              </button>
              {!account.is_default && (
                <button className="btn-secondary text-sm" onClick={() => makeDefault(account)}>
                  Make default
                </button>
              )}
              <button className="btn-secondary text-sm" onClick={() => toggleActive(account)}>
                {account.is_active ? "Switch off" : "Switch on"}
              </button>
              <button className="btn-secondary text-sm" onClick={() => copyWebhook(account)}>
                Copy webhook URL
              </button>
              <button className="text-gray-500 hover:text-primary-600 text-sm px-2" onClick={() => onEdit(account)}>
                Edit
              </button>
              <button className="text-gray-400 hover:text-red-500 text-sm px-2" onClick={() => remove(account)}>
                <Trash2 size={15} />
              </button>
            </div>
          </div>
        ))}
      </div>

      {testSendFor && (
        <TestSendModal account={testSendFor} close={() => setTestSendFor(null)} />
      )}
    </div>
  );
}

function TestSendModal({ account, close }: { account: EmailAccount; close: () => void }) {
  const [to, setTo] = useState("");
  const [busy, setBusy] = useState(false);
  const send = async () => {
    setBusy(true);
    try {
      const result = await emailApi.testSend(account.id, to);
      if (result.success) toast.success("Test email sent — check the inbox (and spam)");
      else toast.error(result.error || "Brevo rejected the send");
      if (result.success) close();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not send the test email");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title={`Send a test email via ${account.name}`} close={close}>
      <div className="space-y-4">
        <Field label="Send to">
          <input
            className="input"
            placeholder="you@yourdomain.com"
            value={to}
            onChange={(e) => setTo(e.target.value)}
            autoFocus
          />
        </Field>
        <div className="flex justify-end gap-2">
          <button className="btn-secondary" onClick={close}>
            Cancel
          </button>
          <button className="btn-primary" onClick={send} disabled={busy || !to.includes("@")}>
            {busy ? "Sending…" : "Send test"}
          </button>
        </div>
      </div>
    </Modal>
  );
}

function AccountModal({
  account,
  close,
  saved,
}: {
  account?: EmailAccount;
  close: () => void;
  saved: () => void;
}) {
  const [form, setForm] = useState<any>({
    ...emptyAccount,
    ...(account
      ? {
          ...account,
          api_key: "",
          reply_to: account.reply_to || "",
          daily_limit: account.daily_limit ?? "",
        }
      : {}),
  });
  const [saving, setSaving] = useState(false);
  const [senders, setSenders] = useState<any[]>([]);
  const [domains, setDomains] = useState<any[]>([]);
  const [loadingSenders, setLoadingSenders] = useState(false);
  const [probeNote, setProbeNote] = useState<string | null>(null);
  const set = (k: string, v: any) => setForm((f: any) => ({ ...f, [k]: v }));

  /** Ask Brevo about the key — before saving it when it is brand new.
   *
   * Using an address Brevo has already verified (ideally on an authenticated
   * domain) is what keeps mail out of spam, so the picker is deliberately part
   * of the add-sender flow rather than a step you have to remember later.
   */
  const loadSenders = async () => {
    const typed = form.api_key.trim();
    setLoadingSenders(true);
    setProbeNote(null);
    try {
      const result = typed
        ? await emailApi.sendersPreview(typed)
        : account
        ? await (async () => {
            const list = await emailApi.verifiedSenders(account.id);
            return {
              success: true,
              error: null,
              senders: (list.items || []).map((s: any) => ({
                email: s?.email || s?.Email || s?.emailAddress,
                name: s?.name || s?.Name || "",
                active: s?.active ?? true,
              })),
              domains: [] as any[],
            };
          })()
        : { success: false, error: "Paste the API key first", senders: [], domains: [] };

      setSenders((result.senders || []).filter((s: any) => s.email));
      setDomains(result.domains || []);
      if (result.error) setProbeNote(result.error);
      else if (!(result.senders || []).length) {
        setProbeNote("Brevo has no verified sender under this key yet.");
      }
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not read the senders from Brevo");
    } finally {
      setLoadingSenders(false);
    }
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.name.trim()) return toast.error("Give this sender a name");
    if (!form.from_email.includes("@")) return toast.error("Enter the From email address");
    if (!account && !form.api_key.trim()) return toast.error("Paste the Brevo API key");
    if (form.from_name && !form.from_email.includes("@")) return toast.error("Check the From address");

    const payload: any = {
      name: form.name.trim(),
      from_name: (form.from_name || form.name).trim(),
      from_email: form.from_email.trim(),
      reply_to: form.reply_to?.trim() || null,
      daily_limit: form.daily_limit === "" ? null : Number(form.daily_limit),
      is_active: !!form.is_active,
      is_default: !!form.is_default,
      track_opens: !!form.track_opens,
      track_clicks: !!form.track_clicks,
    };
    if (form.api_key.trim()) payload.api_key = form.api_key.trim();

    setSaving(true);
    try {
      if (account) await emailApi.updateAccount(account.id, payload);
      else await emailApi.createAccount(payload);
      toast.success(account ? "Sender updated" : "Sender added — test it to be sure");
      saved();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not save this sender");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title={account ? `Edit ${account.name}` : "Add a Brevo sender"} close={close} wide>
      <form onSubmit={submit} className="space-y-4">
        <div className="rounded-lg bg-gray-50 dark:bg-gray-800 p-3 text-sm text-gray-600 dark:text-gray-300">
          In Brevo: <strong>SMTP &amp; API → API keys → Create a new API key</strong>. Paste it below
          with the From name and From address (both must be verified in Brevo).
        </div>

        <Field label="Sender name (how you recognise it here)">
          <input
            className="input"
            value={form.name}
            onChange={(e) => set("name", e.target.value)}
            placeholder="Main Brevo"
            autoFocus
          />
        </Field>

        <div className="grid sm:grid-cols-2 gap-3">
          <Field label="From name">
            <input
              className="input"
              value={form.from_name}
              onChange={(e) => set("from_name", e.target.value)}
              placeholder="Your Company"
            />
          </Field>
          <Field label="From email">
            <input
              className="input"
              type="email"
              value={form.from_email}
              onChange={(e) => set("from_email", e.target.value)}
              placeholder="hello@yourdomain.com"
            />
          </Field>
        </div>

        <Field label={account ? "Brevo API key (leave blank to keep the saved key)" : "Brevo API key"}>
          <input
            className="input font-mono"
            value={form.api_key}
            onChange={(e) => set("api_key", e.target.value)}
            placeholder="xkeysib-…"
            autoComplete="off"
          />
        </Field>
        <button
          type="button"
          className="text-sm text-primary-600 -mt-2"
          onClick={loadSenders}
          disabled={loadingSenders}
        >
          {loadingSenders ? "Reading Brevo…" : "Check Brevo's verified senders & domains"}
        </button>

        {probeNote && (
          <p className="text-xs text-amber-600 -mt-2">{probeNote}</p>
        )}

        {senders.length > 0 && (
          <div className="rounded-lg border border-gray-200 dark:border-gray-700 p-3 space-y-2">
            <p className="text-xs font-medium text-gray-600 dark:text-gray-300">
              Addresses Brevo has already verified — pick one and mail leaves from a warm sender:
            </p>
            {senders.map((s: any) => {
              const chosen = form.from_email.trim().toLowerCase() === (s.email || "").toLowerCase();
              return (
                <button
                  type="button"
                  key={s.email}
                  onClick={() => {
                    set("from_email", s.email);
                    if (s.name && !form.from_name.trim()) set("from_name", s.name);
                  }}
                  className={`w-full flex items-center justify-between gap-2 text-left text-sm px-2 py-1.5 rounded ${
                    chosen ? "bg-primary-50 dark:bg-primary-900/20" : "hover:bg-gray-50 dark:hover:bg-gray-800"
                  }`}
                >
                  <span className="truncate">
                    <span className="font-medium">{s.email}</span>
                    {s.name ? <span className="text-gray-400"> · {s.name}</span> : null}
                  </span>
                  <span className="flex items-center gap-1 shrink-0">
                    {s.recommended ? (
                      <span className="badge-green">warm</span>
                    ) : s.domain_verified ? (
                      <span className="badge-yellow">verified</span>
                    ) : (
                      <span className="badge-yellow">domain not authenticated</span>
                    )}
                  </span>
                </button>
              );
            })}
            {form.from_email.includes("@") &&
              !senders.some(
                (s: any) =>
                  (s.email || "").toLowerCase() === form.from_email.trim().toLowerCase()
              ) && (
                <p className="text-xs text-amber-600">
                  {form.from_email} is not in this key's verified list — Brevo will reject it or
                  it will land in spam. Verify it in Brevo → Senders &amp; IP first.
                </p>
              )}
            {domains.length > 0 && (
              <div className="text-xs text-gray-500 pt-1 border-t border-gray-100 dark:border-gray-800">
                Domain authentication:{" "}
                {domains.map((d: any) => (
                  <span key={d.domain} className="mr-2">
                    <span className="font-mono">{d.domain}</span>{" "}
                    {d.dkim ? (
                      <span className="text-green-600">SPF/DKIM ✓</span>
                    ) : (
                      <span className="text-amber-600">not authenticated</span>
                    )}
                  </span>
                ))}
              </div>
            )}
          </div>
        )}

        <div className="grid sm:grid-cols-2 gap-3">
          <Field label="Reply-to (optional)">
            <input
              className="input"
              value={form.reply_to}
              onChange={(e) => set("reply_to", e.target.value)}
              placeholder="replies@yourdomain.com"
            />
          </Field>
          <Field label="Daily limit (optional)">
            <input
              className="input"
              type="number"
              min={0}
              value={form.daily_limit}
              onChange={(e) => set("daily_limit", e.target.value)}
              placeholder="Leave blank for no app-side cap"
            />
          </Field>
        </div>

        <div className="flex flex-wrap gap-4 text-sm">
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={!!form.is_active} onChange={(e) => set("is_active", e.target.checked)} />
            Active
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={!!form.is_default} onChange={(e) => set("is_default", e.target.checked)} />
            Default sender
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={!!form.track_opens} onChange={(e) => set("track_opens", e.target.checked)} />
            Track opens
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={!!form.track_clicks} onChange={(e) => set("track_clicks", e.target.checked)} />
            Track clicks
          </label>
        </div>

        <div className="flex justify-end gap-2">
          <button type="button" className="btn-secondary" onClick={close}>
            Cancel
          </button>
          <button className="btn-primary" type="submit" disabled={saving}>
            {saving ? "Saving…" : account ? "Save changes" : "Add sender"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

/* ========================================================================== */
/* Send                                                                       */
/* ========================================================================== */

function SendTab({ reference, accounts }: any) {
  return <EmailSendPanel reference={reference} accounts={accounts} />;
}

/* ========================================================================== */
/* Campaigns                                                                  */
/* ========================================================================== */

const emptyCampaign = {
  name: "",
  list_id: "",
  template_id: "",
  subject: "",
  message_body: "",
  html_body: "",
  email_account_id: "",
  fallback_email_account_id: "",
  scheduled_start_at: "",
  track_opens: true,
  track_clicks: true,
};

function CampaignsTab({ reference, accounts }: any) {
  const [campaigns, setCampaigns] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get("/campaigns", { params: { channel: "email", per_page: 100 } });
      setCampaigns(data.items || data || []);
    } catch {
      toast.error("Could not load email campaigns");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const act = async (campaign: any, action: string) => {
    setBusyId(campaign.id);
    try {
      if (action === "delete") {
        if (!window.confirm(`Delete the campaign "${campaign.name}"?`)) return;
        await api.delete(`/campaigns/${campaign.id}`);
      } else if (action === "schedule") {
        await api.post(`/campaigns/${campaign.id}/validate`);
      } else {
        await api.post(`/campaigns/${campaign.id}/${action}`);
      }
      toast.success(`Campaign ${action === "delete" ? "deleted" : action + "ed"}`);
      load();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || `Could not ${action} this campaign`);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-gray-500">
          List-based email campaigns with a subject, an optional HTML body and a chosen Brevo sender.
        </p>
        <button className="btn-primary" onClick={() => setCreating(true)}>
          <Plus size={16} className="mr-1" /> New email campaign
        </button>
      </div>

      {loading ? (
        <div className="py-10 text-center text-gray-500">Loading…</div>
      ) : campaigns.length === 0 ? (
        <Empty title="No email campaigns yet" body="Create one to send a list through Brevo." />
      ) : (
        <div className="space-y-3">
          {campaigns.map((c) => (
            <div key={c.id} className="card">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <h3 className="font-semibold flex items-center gap-2">
                    {c.name} <Badge value={c.status} />
                  </h3>
                  <p className="text-sm text-gray-500 truncate">{c.subject || "No subject yet"}</p>
                  <p className="text-xs text-gray-400">
                    {c.messages_sent ?? 0} sent · {c.messages_delivered ?? 0} delivered ·{" "}
                    {c.messages_failed ?? 0} failed · {c.replies ?? 0} replies
                    {c.scheduled_start_at ? ` · starts ${fmtDate(c.scheduled_start_at)}` : ""}
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  {(c.status === "draft" || c.status === "failed") && (
                    <button className="btn-secondary text-sm" onClick={() => act(c, "validate")} disabled={busyId === c.id}>
                      Schedule
                    </button>
                  )}
                  {(c.status === "scheduled" || c.status === "paused") && (
                    <button className="btn-primary text-sm" onClick={() => act(c, "start")} disabled={busyId === c.id}>
                      Start
                    </button>
                  )}
                  {c.status === "running" && (
                    <button className="btn-secondary text-sm" onClick={() => act(c, "pause")} disabled={busyId === c.id}>
                      Pause
                    </button>
                  )}
                  {["running", "paused"].includes(c.status) && (
                    <button className="btn-secondary text-sm" onClick={() => act(c, "stop")} disabled={busyId === c.id}>
                      Stop
                    </button>
                  )}
                  <button className="text-gray-400 hover:text-red-500 text-sm px-2" onClick={() => act(c, "delete")}>
                    <Trash2 size={15} />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {creating && (
        <CampaignModal
          reference={reference}
          accounts={accounts}
          close={() => setCreating(false)}
          saved={() => {
            setCreating(false);
            load();
          }}
        />
      )}
    </div>
  );
}

function CampaignModal({ reference, accounts, close, saved }: any) {
  const [form, setForm] = useState<any>({ ...emptyCampaign });
  const [saving, setSaving] = useState(false);
  const set = (k: string, v: any) => setForm((f: any) => ({ ...f, [k]: v }));

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.name.trim()) return toast.error("Give the campaign a name");
    if (!form.subject.trim()) return toast.error("An email campaign needs a subject");
    if (!(form.message_body.trim() || form.html_body.trim() || form.template_id))
      return toast.error("Write the email body or choose a template");

    const payload: any = {
      name: form.name.trim(),
      channel: "email",
      list_id: form.list_id ? Number(form.list_id) : null,
      template_id: form.template_id ? Number(form.template_id) : null,
      message_body: form.message_body || null,
      subject: form.subject,
      html_body: form.html_body || null,
      email_account_id: form.email_account_id ? Number(form.email_account_id) : null,
      fallback_email_account_id: form.fallback_email_account_id
        ? Number(form.fallback_email_account_id)
        : null,
      track_opens: !!form.track_opens,
      track_clicks: !!form.track_clicks,
      scheduled_start_at: form.scheduled_start_at
        ? new Date(form.scheduled_start_at).toISOString()
        : null,
    };
    setSaving(true);
    try {
      await api.post("/campaigns", payload);
      toast.success("Email campaign created — schedule it when the copy is ready");
      saved();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not create this campaign");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title="New email campaign" close={close} wide>
      <form onSubmit={submit} className="space-y-4">
        <Field label="Campaign name">
          <input className="input" value={form.name} onChange={(e) => set("name", e.target.value)} autoFocus />
        </Field>

        <div className="grid sm:grid-cols-2 gap-3">
          <Field label="List">
            <select className="input" value={form.list_id} onChange={(e) => set("list_id", e.target.value)}>
              <option value="">Choose a list…</option>
              {(reference?.lists || []).map((l: any) => (
                <option key={l.id} value={l.id}>
                  {l.name} ({l.contact_count ?? 0})
                </option>
              ))}
            </select>
          </Field>
          <Field label="Template (optional)">
            <select
              className="input"
              value={form.template_id}
              onChange={(e) => {
                set("template_id", e.target.value);
                const tpl = (reference?.templates || []).find((t: any) => String(t.id) === e.target.value);
                if (tpl?.subject && !form.subject) set("subject", tpl.subject);
              }}
            >
              <option value="">Write it here</option>
              {(reference?.templates || []).map((t: any) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </Field>
        </div>

        <Field label="Subject">
          <input className="input" value={form.subject} onChange={(e) => set("subject", e.target.value)} />
        </Field>

        <Field label="Message">
          <textarea
            className="input min-h-[150px]"
            value={form.message_body}
            onChange={(e) => set("message_body", e.target.value)}
            placeholder={"Hi {{first_name}},\n\n…"}
          />
        </Field>

        <details className="text-sm">
          <summary className="cursor-pointer text-gray-500">HTML body (optional)</summary>
          <textarea
            className="input min-h-[120px] mt-2 font-mono text-xs"
            value={form.html_body}
            onChange={(e) => set("html_body", e.target.value)}
          />
        </details>

        <div className="grid sm:grid-cols-2 gap-3">
          <Field label="Send through">
            <select
              className="input"
              value={form.email_account_id}
              onChange={(e) => set("email_account_id", e.target.value)}
            >
              <option value="">Default sender</option>
              {accounts.map((a: EmailAccount) => (
                <option key={a.id} value={a.id}>
                  {a.name} — {a.from_email}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Fallback sender (optional)">
            <select
              className="input"
              value={form.fallback_email_account_id}
              onChange={(e) => set("fallback_email_account_id", e.target.value)}
            >
              <option value="">None</option>
              {accounts.map((a: EmailAccount) => (
                <option key={a.id} value={a.id} disabled={String(a.id) === String(form.email_account_id)}>
                  {a.name} — {a.from_email}
                </option>
              ))}
            </select>
          </Field>
        </div>

        <div className="grid sm:grid-cols-2 gap-3">
          <Field label="Start at (optional)">
            <input
              className="input"
              type="datetime-local"
              value={form.scheduled_start_at}
              onChange={(e) => set("scheduled_start_at", e.target.value)}
            />
          </Field>
          <div className="flex items-end gap-4 text-sm pb-2">
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={!!form.track_opens} onChange={(e) => set("track_opens", e.target.checked)} />
              Track opens
            </label>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={!!form.track_clicks} onChange={(e) => set("track_clicks", e.target.checked)} />
              Track clicks
            </label>
          </div>
        </div>

        <div className="flex justify-end gap-2">
          <button type="button" className="btn-secondary" onClick={close}>
            Cancel
          </button>
          <button className="btn-primary" type="submit" disabled={saving}>
            {saving ? "Creating…" : "Create campaign"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

/* ========================================================================== */
/* Templates                                                                  */
/* ========================================================================== */

function TemplatesTab({ reference }: any) {
  const [templates, setTemplates] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<any | null>(null);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get("/templates", { params: { channel: "email", per_page: 200 } });
      setTemplates(data.items || []);
    } catch {
      toast.error("Could not load email templates");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const remove = async (template: any) => {
    if (!window.confirm(`Delete the template "${template.name}"?`)) return;
    try {
      await api.delete(`/templates/${template.id}`);
      toast.success("Template deleted");
      load();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not delete this template");
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-gray-500">
          Email templates carry a subject, a plain-text body and optional HTML. They are the same rows
          the SMS templates live in, filtered to this channel.
        </p>
        <button className="btn-primary" onClick={() => setEditing({})}>
          <Plus size={16} className="mr-1" /> New template
        </button>
      </div>

      {loading ? (
        <div className="py-10 text-center text-gray-500">Loading…</div>
      ) : templates.length === 0 ? (
        <Empty title="No email templates yet" body="Save your subject + body once and reuse it everywhere." />
      ) : (
        <div className="grid md:grid-cols-2 gap-3">
          {templates.map((t) => (
            <div key={t.id} className="card">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <h3 className="font-semibold truncate">{t.name}</h3>
                  <p className="text-sm text-gray-500 truncate">{t.subject || "(no subject)"}</p>
                  {t.category && <span className="badge-gray mt-1 inline-block">{t.category}</span>}
                </div>
                <div className="flex gap-1 shrink-0">
                  <button className="text-sm text-primary-600 px-2" onClick={() => setEditing(t)}>
                    Edit
                  </button>
                  <button className="text-gray-400 hover:text-red-500 px-2" onClick={() => remove(t)}>
                    <Trash2 size={15} />
                  </button>
                </div>
              </div>
              <p className="text-xs text-gray-400 mt-2 line-clamp-3 whitespace-pre-wrap">{t.body}</p>
            </div>
          ))}
        </div>
      )}

      {editing && (
        <TemplateModal
          template={editing.id ? editing : null}
          close={() => setEditing(null)}
          saved={() => {
            setEditing(null);
            load();
          }}
        />
      )}
    </div>
  );
}

function TemplateModal({ template, close, saved }: any) {
  const [templateAttachments, setTemplateAttachments] = useState<any[]>([]);
  const [form, setForm] = useState<any>({
    name: template?.name || "",
    category: template?.category || "",
    subject: template?.subject || "",
    body: template?.body || "",
    html_body: template?.html_body || "",
    preheader: template?.preheader || "",
  });
  const [saving, setSaving] = useState(false);
  const set = (k: string, v: any) => setForm((f: any) => ({ ...f, [k]: v }));

  // Load the stored attachments (metadata only) so "no new upload" keeps them.
  useEffect(() => {
    if (!template) return;
    api
      .get(`/templates/${template.id}`)
      .then(({ data }) => {
        if (Array.isArray(data.attachments) && data.attachments.length) {
          setTemplateAttachments(
            data.attachments.map((a: any) => ({
              name: a.name,
              content_type: a.content_type,
              size: a.size,
              // Re-opened templates keep their files: mark them "already stored"
              // so the payload only sends genuinely new uploads.
              content_base64: "",
              stored: true,
            }))
          );
        }
      })
      .catch(() => {});
  }, [template?.id]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.name.trim()) return toast.error("Give the template a name");
    if (!form.subject.trim()) return toast.error("Email templates need a subject");
    if (!form.body.trim() && !form.html_body.trim()) return toast.error("Write the email body");

    const newUploads = templateAttachments.filter((a) => !a.stored && a.content_base64);
    const payload: any = {
      name: form.name.trim(),
      category: form.category || null,
      channel: "email",
      subject: form.subject,
      body: form.body,
      html_body: form.html_body || null,
      preheader: form.preheader || null,
      is_active: true,
    };
    // Only touch attachments when the user actually changed them, so editing a
    // template never silently drops the files it already carries.
    if (newUploads.length) payload.attachments = newUploads;
    setSaving(true);
    try {
      if (template) await api.put(`/templates/${template.id}`, payload);
      else await api.post("/templates", payload);
      toast.success(template ? "Template updated" : "Template created");
      saved();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not save this template");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title={template ? `Edit ${template.name}` : "New email template"} close={close} wide>
      <form onSubmit={submit} className="space-y-4">
        <div className="grid sm:grid-cols-2 gap-3">
          <Field label="Name">
            <input className="input" value={form.name} onChange={(e) => set("name", e.target.value)} autoFocus />
          </Field>
          <Field label="Category (optional)">
            <input className="input" value={form.category} onChange={(e) => set("category", e.target.value)} />
          </Field>
        </div>
        <Field label="Subject">
          <input className="input" value={form.subject} onChange={(e) => set("subject", e.target.value)} />
        </Field>
        <Field label="Preheader (optional)">
          <input
            className="input"
            value={form.preheader}
            onChange={(e) => set("preheader", e.target.value)}
            placeholder="The line inboxes show under the subject"
          />
        </Field>
        <Field label="Body">
          <RichEmailEditor
            body={form.body}
            onBody={(value) => set("body", value)}
            html={form.html_body}
            onHtml={(value) => set("html_body", value)}
            attachments={templateAttachments}
            onAttachments={setTemplateAttachments}
          />
        </Field>
        <div className="flex justify-end gap-2">
          <button type="button" className="btn-secondary" onClick={close}>
            Cancel
          </button>
          <button className="btn-primary" type="submit" disabled={saving}>
            {saving ? "Saving…" : "Save template"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

/* ========================================================================== */
/* Deliverability                                                             */
/* ========================================================================== */

function DeliverabilityTab({ accounts }: { accounts: EmailAccount[] }) {
  const [accountId, setAccountId] = useState<string>("");
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!accountId && accounts.length) {
      const preferred = accounts.find((a) => a.is_default) || accounts[0];
      setAccountId(String(preferred.id));
    }
  }, [accounts, accountId]);

  useEffect(() => {
    if (!accountId) return;
    setLoading(true);
    emailApi
      .deliverability(Number(accountId), 30)
      .then(setData)
      .catch((err: any) => toast.error(err.response?.data?.detail || "Could not read deliverability"))
      .finally(() => setLoading(false));
  }, [accountId]);

  if (!accounts.length) {
    return (
      <Empty
        title="Add a Brevo sender first"
        body="Deliverability checks need an API key so the app can read your domain authentication from Brevo."
      />
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <select className="input max-w-xs" value={accountId} onChange={(e) => setAccountId(e.target.value)}>
          {accounts.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name} — {a.from_email}
            </option>
          ))}
        </select>
        {data && (
          <span
            className={`badge ${
              data.score >= 80 ? "badge-green" : data.score >= 50 ? "badge-yellow" : "badge-red"
            }`}
          >
            Deliverability score {data.score}/100
          </span>
        )}
      </div>

      {loading ? (
        <div className="card p-6 text-center text-gray-500">Checking with Brevo…</div>
      ) : data ? (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
            <Stat label="Sent (30d)" value={data.metrics.sent} />
            <Stat label="Bounce rate" value={`${data.metrics.bounce_rate}%`} />
            <Stat label="Open rate" value={`${data.metrics.open_rate}%`} />
            <Stat label="Click rate" value={`${data.metrics.click_rate}%`} />
            <Stat label="Unsubscribed" value={data.metrics.unsubscribed} />
          </div>

          <div className="card space-y-3">
            <h3 className="font-semibold">Checks</h3>
            {data.checks.map((check: any) => (
              <div key={check.key} className="flex items-start gap-3 text-sm">
                {check.ok ? (
                  <CheckCircle2 size={17} className="text-green-500 mt-0.5 shrink-0" />
                ) : (
                  <XCircle size={17} className="text-amber-500 mt-0.5 shrink-0" />
                )}
                <div>
                  <p className="font-medium">{check.label}</p>
                  <p className="text-gray-500 text-xs">{check.detail}</p>
                </div>
              </div>
            ))}
          </div>

          <div className="card space-y-2">
            <h3 className="font-semibold">Authenticated domains in Brevo</h3>
            {data.domains_error && (
              <p className="text-sm text-amber-600">{data.domains_error}</p>
            )}
            {data.domains.length === 0 ? (
              <p className="text-sm text-gray-500">
                Brevo reports no authenticated sending domain for this key. Authenticate your
                domain in Brevo (<strong>Senders &amp; IP → Domains</strong>) — SPF/DKIM are what
                keep you out of spam.
              </p>
            ) : (
              <div className="divide-y divide-gray-100 dark:divide-gray-800">
                {data.domains.map((d: any, i: number) => (
                  <div key={i} className="py-2 flex items-center justify-between text-sm">
                    <span className="font-medium">{d.domain}</span>
                    {d.authenticated || d.verified ? (
                      <span className="badge-green">authenticated</span>
                    ) : (
                      <span className="badge-yellow">not verified</span>
                    )}
                  </div>
                ))}
              </div>
            )}
            <p className="text-xs text-gray-500">
              The app uses a real, threaded conversation per contact: replies quote the message they
              answer (In-Reply-To/References), bulk mail carries List-Unsubscribe, and every send has
              a text alternative — the three things filters look at first.
            </p>
          </div>
        </>
      ) : null}
    </div>
  );
}

/* ========================================================================== */
/* Suppression                                                                */
/* ========================================================================== */

function SuppressionTab() {
  const [items, setItems] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [address, setAddress] = useState("");
  const [reason, setReason] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await emailApi.suppression({ search: search || undefined, per_page: 100 });
      setItems(data.items);
      setTotal(data.total);
    } catch {
      toast.error("Could not load the suppression list");
    } finally {
      setLoading(false);
    }
  }, [search]);

  useEffect(() => {
    load();
  }, [load]);

  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!address.includes("@")) return toast.error("Enter a valid email address");
    try {
      await emailApi.addSuppression({ email_address: address, reason: reason || "Manual" });
      toast.success("Address suppressed");
      setAddress("");
      setReason("");
      load();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not suppress this address");
    }
  };

  const remove = async (entry: any) => {
    try {
      await emailApi.removeSuppression(entry.id);
      toast.success("Address can be emailed again");
      load();
    } catch {
      toast.error("Could not remove this entry");
    }
  };

  return (
    <div className="space-y-4">
      <form onSubmit={add} className="card flex flex-wrap items-end gap-3">
        <Field label="Email address">
          <input
            className="input"
            value={address}
            onChange={(e) => setAddress(e.target.value)}
            placeholder="do-not-contact@company.com"
          />
        </Field>
        <Field label="Reason (optional)">
          <input className="input" value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
        <button className="btn-primary" type="submit">
          <ShieldOff size={16} className="mr-1" /> Suppress
        </button>
      </form>

      <div className="flex items-center justify-between gap-3">
        <input
          className="input max-w-xs"
          placeholder="Search suppressed addresses…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <span className="text-sm text-gray-500">{total} suppressed</span>
      </div>

      {loading ? (
        <div className="py-10 text-center text-gray-500">Loading…</div>
      ) : items.length === 0 ? (
        <Empty
          title="Nothing suppressed"
          body="Unsubscribes, hard bounces and manual blocks all land here — and none of them are ever emailed again."
        />
      ) : (
        <div className="card divide-y divide-gray-100 dark:divide-gray-800">
          {items.map((entry) => (
            <div key={entry.id} className="py-2 flex items-center justify-between gap-3 text-sm">
              <div className="min-w-0">
                <p className="font-medium truncate flex items-center gap-2">
                  <Mail size={13} className="text-gray-400" /> {entry.email_address}
                  {entry.hard_bounce && <span className="badge-red">hard bounce</span>}
                </p>
                <p className="text-xs text-gray-500 truncate">
                  {entry.reason || "—"} · {entry.source || "manual"} · {fmtDate(entry.created_at)}
                </p>
              </div>
              <button className="text-gray-400 hover:text-red-500 px-2" onClick={() => remove(entry)}>
                <Trash2 size={15} />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ========================================================================== */
/* Activity                                                                   */
/* ========================================================================== */

function ActivityTab({ accounts }: any) {
  const [tab, setTab] = useState<"events" | "history">("events");
  const [events, setEvents] = useState<any[]>([]);
  const [history, setHistory] = useState<EmailMessage[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [e, h] = await Promise.all([
        emailApi.events({ per_page: 100 }),
        emailApi.history({ per_page: 100 }),
      ]);
      setEvents(e.items);
      setHistory(h.items);
    } catch {
      toast.error("Could not load email activity");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const retry = async (message: EmailMessage) => {
    try {
      await emailApi.retry(message.id);
      toast.success("Retry queued");
      load();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not retry this send");
    }
  };

  const accountName = (id?: number | null) => accounts.find((a: EmailAccount) => a.id === id)?.name || "—";

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <div className="flex gap-2">
          <button
            className={tab === "events" ? "btn-primary text-sm" : "btn-secondary text-sm"}
            onClick={() => setTab("events")}
          >
            <Activity size={15} className="mr-1" /> Events
          </button>
          <button
            className={tab === "history" ? "btn-primary text-sm" : "btn-secondary text-sm"}
            onClick={() => setTab("history")}
          >
            <Mail size={15} className="mr-1" /> Messages
          </button>
        </div>
        <button className="btn-secondary text-sm" onClick={load}>
          <RefreshCw size={15} className="mr-1" /> Refresh
        </button>
      </div>

      {loading ? (
        <div className="py-10 text-center text-gray-500">Loading…</div>
      ) : tab === "events" ? (
        events.length === 0 ? (
          <Empty
            title="No Brevo events yet"
            body="Delivered / opened / clicked / bounced events appear here once the webhook URL is configured in Brevo."
          />
        ) : (
          <div className="card divide-y divide-gray-100 dark:divide-gray-800">
            {events.map((e) => (
              <div key={e.id} className="py-2 flex items-start justify-between gap-3 text-sm">
                <div className="min-w-0">
                  <p className="font-medium flex items-center gap-2">
                    <EventIcon type={e.event_type} /> {e.event_type}
                    {e.link && <span className="text-xs text-gray-400 truncate">{e.link}</span>}
                  </p>
                  <p className="text-xs text-gray-500 truncate">
                    {e.email_address} · {e.subject || "—"} · {accountName(e.account_id)}
                  </p>
                </div>
                <span className="text-xs text-gray-400 shrink-0">{fmtDate(e.created_at)}</span>
              </div>
            ))}
          </div>
        )
      ) : history.length === 0 ? (
        <Empty title="No email messages yet" body="Sends and replies both show up here." />
      ) : (
        <div className="card divide-y divide-gray-100 dark:divide-gray-800">
          {history.map((m) => (
            <div key={m.id} className="py-2 flex items-start justify-between gap-3 text-sm">
              <div className="min-w-0">
                <p className="font-medium truncate flex items-center gap-2">
                  {m.direction === "incoming" ? (
                    <Inbox size={14} className="text-amber-500" />
                  ) : (
                    <Send size={14} className="text-primary-500" />
                  )}
                  {m.subject || "(no subject)"}
                  <Badge value={m.status} />
                  {m.open_count > 0 && <span className="badge-blue">{m.open_count}x opened</span>}
                  {m.click_count > 0 && <span className="badge-green">{m.click_count}x clicked</span>}
                </p>
                <p className="text-xs text-gray-500 truncate">
                  {m.direction === "incoming" ? m.from_address : m.to_address} ·{" "}
                  {m.email_account_name || accountName(m.email_account_id)}
                  {m.last_error ? ` · ${m.last_error}` : ""}
                  {(m.attachments?.length || 0) > 0
                    ? ` · ${m.attachments!.length} attachment(s)`
                    : ""}
                </p>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <span className="text-xs text-gray-400">{fmtDate(m.created_at)}</span>
                {m.status === "failed" && (
                  <button className="text-primary-600 text-xs" onClick={() => retry(m)}>
                    Retry
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function EventIcon({ type }: { type: string }) {
  if (type === "opened") return <Eye size={14} className="text-sky-500" />;
  if (type === "clicked") return <MousePointerClick size={14} className="text-green-600" />;
  if (type === "delivered" || type === "sent") return <CheckCircle2 size={14} className="text-green-500" />;
  if (type === "bounce" || type === "blocked" || type === "error") return <XCircle size={14} className="text-red-500" />;
  if (type === "unsubscribed") return <UserRound size={14} className="text-amber-500" />;
  return <Activity size={14} className="text-gray-400" />;
}
