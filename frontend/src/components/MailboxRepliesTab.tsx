import { useCallback, useEffect, useState } from "react";
import toast from "react-hot-toast";
import {
  AlertTriangle,
  CheckCircle2,
  Inbox,
  KeyRound,
  Mail,
  Plus,
  RefreshCw,
  Send,
  ShieldCheck,
  Trash2,
  XCircle,
} from "lucide-react";
import mailboxApi, {
  Mailbox,
  MailboxSyncSummary,
  ReplyRoutingReport,
  RoutingFinding,
} from "../api/mailbox";
import { Empty, Field, Modal, Stat, Toggle, fmtDate } from "../pages/ads/ui";

/**
 * REPLIES — connected mailboxes, and why replies were going missing.
 *
 * This tab answers one complaint with two halves:
 *
 * 1. **"The reply landed in my spam folder."** Campaigns leave through Brevo.
 *    When the From/Reply-To is a freemail address (`@gmail.com`), Brevo cannot
 *    authenticate it — it does not control that domain's SPF or DKIM — so DMARC
 *    fails and Gmail treats the whole thread as spoofed. The prospect still sees
 *    the campaign, but *their reply* is the thing that gets filed in Spam. The
 *    routing report below says this in plain words, and the "Never send it to
 *    Spam" filters are Gmail's own remedy (`removeLabelIds: ["SPAM"]`, which is
 *    exactly what its filter editor writes for that checkbox).
 *
 * 2. **"The reply never showed up in the app."** Brevo's inbound parsing only
 *    sees mail addressed to a domain whose MX points at Brevo. A reply to a
 *    Gmail address never reaches it. So the app reads the mailbox itself — over
 *    the Gmail API when Google OAuth is configured, or over IMAP with an app
 *    password, which needs no Google Cloud project at all. It reads INBOX *and*
 *    Spam, threads each reply onto the conversation it answers, rescues the ones
 *    Gmail filed away, and can send your answer back out from the same mailbox so
 *    the thread stays authenticated.
 */

const SEVERITY_STYLE: Record<RoutingFinding["severity"], { chip: string; icon: any }> = {
  high: { chip: "badge-red", icon: XCircle },
  medium: { chip: "badge-yellow", icon: AlertTriangle },
  low: { chip: "badge-gray", icon: AlertTriangle },
};

function ConnectModal({ close, connected }: { close: () => void; connected: () => void }) {
  const [form, setForm] = useState({
    email_address: "",
    app_password: "",
    rescue_from_spam: true,
    send_replies: true,
    import_all: false,
    poll_interval: 120,
  });
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<MailboxSyncSummary | null>(null);

  const submit = async () => {
    setBusy(true);
    try {
      const made = await mailboxApi.connect(form);
      setResult(made.first_sync);
      toast.success(`${made.mailbox.email_address} connected`);
      connected();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not connect that mailbox");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title="Connect a mailbox with an app password" close={close}>
      <div className="space-y-3">
        <div className="card bg-blue-50 dark:bg-blue-950/30 border-blue-200 dark:border-blue-900 p-3 text-sm">
          <p className="font-medium flex items-center gap-2">
            <KeyRound size={15} /> Your Gmail password will not work here
          </p>
          <p className="text-gray-600 dark:text-gray-300 mt-1 text-xs leading-relaxed">
            Google blocks IMAP logins with the account password. Turn on 2-Step Verification, then
            create an <strong>app password</strong> at{" "}
            <a
              className="underline"
              href="https://myaccount.google.com/apppasswords"
              target="_blank"
              rel="noreferrer"
            >
              myaccount.google.com/apppasswords
            </a>{" "}
            and paste the 16 characters here. It is encrypted at rest, never shown again, and you
            can revoke it at any time. Nothing is stored if the login fails.
          </p>
        </div>

        <Field label="Mailbox address">
          <input
            className="input"
            type="email"
            autoComplete="off"
            placeholder="you@gmail.com"
            value={form.email_address}
            onChange={(e) => setForm({ ...form, email_address: e.target.value })}
          />
        </Field>

        <Field label="App password" hint="Spaces are optional — they are stripped before use.">
          <input
            className="input"
            type="password"
            autoComplete="off"
            placeholder="abcd efgh ijkl mnop"
            value={form.app_password}
            onChange={(e) => setForm({ ...form, app_password: e.target.value })}
          />
        </Field>

        <div className="space-y-3 pt-1">
          <Toggle
            checked={form.rescue_from_spam}
            onChange={(v) => setForm({ ...form, rescue_from_spam: v })}
            label="Move recognised replies out of Spam"
            hint="Only for mail that answers something this app sent — never for real spam."
          />
          <Toggle
            checked={form.send_replies}
            onChange={(v) => setForm({ ...form, send_replies: v })}
            label="Send one-to-one replies from this mailbox"
            hint="Keeps the thread authenticated. Campaigns still go out through Brevo."
          />
          <Toggle
            checked={form.import_all}
            onChange={(v) => setForm({ ...form, import_all: v })}
            label="Import everything, not just replies"
            hint="Off is right for almost everyone: a CRM inbox is not a mail client."
          />
        </div>

        <Field label="Check every (seconds)">
          <input
            className="input"
            type="number"
            min={30}
            max={3600}
            value={form.poll_interval}
            onChange={(e) => setForm({ ...form, poll_interval: Number(e.target.value) || 120 })}
          />
        </Field>

        {result && (
          <div className="card bg-green-50 dark:bg-green-950/30 border-green-200 dark:border-green-900 p-3 text-sm">
            <p className="font-medium">
              Connected — saw {result.seen}, imported {result.stored}, rescued {result.rescued}
            </p>
            {(result.errors || []).length > 0 && (
              <ul className="text-xs text-amber-700 mt-1 list-disc pl-4">
                {result.errors.map((e, i) => (
                  <li key={i}>{e}</li>
                ))}
              </ul>
            )}
          </div>
        )}

        <div className="flex justify-end gap-2 pt-2">
          <button className="btn-secondary" onClick={close}>
            {result ? "Done" : "Cancel"}
          </button>
          {!result && (
            <button
              className="btn-primary"
              disabled={busy || !form.email_address || form.app_password.length < 6}
              onClick={submit}
            >
              {busy ? "Connecting…" : "Connect and import now"}
            </button>
          )}
        </div>
      </div>
    </Modal>
  );
}

function MailboxRow({
  mailbox,
  reload,
  onDisconnect,
}: {
  mailbox: Mailbox;
  reload: () => void;
  onDisconnect: (m: Mailbox) => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [summary, setSummary] = useState<MailboxSyncSummary | null>(null);
  const [testTo, setTestTo] = useState("");
  const isGoogle = mailbox.provider === "gmail_api";

  const run = async (what: string, fn: () => Promise<any>, ok?: (r: any) => void) => {
    setBusy(what);
    try {
      const result = await fn();
      ok?.(result);
      reload();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || `${what} failed`);
    } finally {
      setBusy(null);
    }
  };

  const patch = (next: Partial<Mailbox>) =>
    run("Saving", async () => {
      await mailboxApi.patch(mailbox.id, next as any);
      toast.success("Saved");
    });

  return (
    <div className="card space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h4 className="font-semibold flex items-center gap-2">
            <Mail size={16} className="text-primary-600" />
            {mailbox.email_address}
            <span className={isGoogle ? "badge-green" : "badge-gray"}>
              {isGoogle ? "Gmail API" : "IMAP"}
            </span>
          </h4>
          <p className="text-xs text-gray-500 mt-0.5">
            {mailbox.name} · checks every {mailbox.poll_interval}s · folders{" "}
            {mailbox.folders.join(", ")}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            className="btn-secondary text-xs"
            disabled={busy === "Syncing"}
            onClick={() =>
              run("Syncing", () => mailboxApi.sync(mailbox.id), (r) => {
                setSummary(r);
                toast.success(
                  r.stored
                    ? `${r.stored} repl${r.stored === 1 ? "y" : "ies"} imported`
                    : "Nothing new",
                );
              })
            }
          >
            <RefreshCw size={13} className="mr-1" />
            {busy === "Syncing" ? "Importing…" : "Import replies now"}
          </button>
          <button
            className="btn-secondary text-xs"
            disabled={busy === "Checking"}
            onClick={() =>
              run("Checking", () => mailboxApi.check(mailbox.id), (r) => {
                r.success
                  ? toast.success("Credential works")
                  : toast.error(r.error || "Credential failed");
              })
            }
          >
            Check
          </button>
          <button
            className="btn-secondary text-xs text-red-600"
            disabled={busy === "Removing"}
            aria-label={`Disconnect ${mailbox.email_address}`}
            title="Disconnect this mailbox"
            onClick={() => onDisconnect(mailbox)}
          >
            <Trash2 size={13} />
          </button>
        </div>
      </div>

      {mailbox.last_sync_status === "error" && mailbox.last_error && (
        <p className="text-xs text-red-600 flex items-start gap-1.5">
          <XCircle size={14} className="mt-0.5 shrink-0" />
          Last sync failed: {mailbox.last_error}
        </p>
      )}
      {mailbox.last_sync_at && (
        <p className="text-xs text-gray-500">
          Last check {fmtDate(mailbox.last_sync_at)} · {mailbox.total_replies} replies imported ·{" "}
          {mailbox.total_rescued} rescued from Spam · {mailbox.total_sent} sent from this mailbox
        </p>
      )}

      {summary && (summary.seen || summary.errors.length) && (
        <div className="card bg-gray-50 dark:bg-gray-800/40 p-3 text-xs space-y-1">
          <p>
            Saw {summary.seen} message{summary.seen === 1 ? "" : "s"} · {summary.replies} recognised
            as replies · {summary.skipped} skipped · {summary.stored} stored · {summary.rescued}{" "}
            rescued
          </p>
          {summary.errors.map((e, i) => (
            <p key={i} className="text-amber-700">
              {e}
            </p>
          ))}
        </div>
      )}

      <div className="grid sm:grid-cols-2 gap-3 pt-1">
        <Toggle
          checked={mailbox.rescue_from_spam}
          onChange={(v) => patch({ rescue_from_spam: v } as any)}
          label="Rescue replies from Spam"
          hint="Moves a recognised reply back to the inbox, then imports it."
        />
        <Toggle
          checked={mailbox.send_replies}
          onChange={(v) => patch({ send_replies: v } as any)}
          label="Send one-to-one replies from here"
          hint="Campaigns stay on Brevo; personal replies leave from this mailbox."
        />
        <Toggle
          checked={mailbox.import_all}
          onChange={(v) => patch({ import_all: v } as any)}
          label="Import every message"
          hint="Off imports only replies to mail this app sent."
        />
        <div className="space-y-1.5">
          <Toggle
            checked={mailbox.never_spam_filter}
            disabled={!isGoogle || busy === "Filters"}
            onChange={(v) =>
              run("Filters", () => mailboxApi.filters(mailbox.id, v), (r) => {
                if (r.success === false) toast.error(r.error || "Could not change the filters");
                else toast.success(v ? "Filters installed" : "Filters removed");
              })
            }
            label="Never send it to Spam (Gmail filters)"
            hint={
              isGoogle
                ? `One filter per person who replied — Gmail's own "never spam" action. ${mailbox.filters_installed} installed.`
                : "Needs the Google (OAuth) connection: IMAP cannot create Gmail filters. Spam rescue still works without it."
            }
          />
        </div>
      </div>

      <div className="flex flex-wrap items-end gap-2 pt-1 border-t border-gray-100 dark:border-gray-800">
        <div className="flex-1 min-w-[200px]">
          <Field label="Prove sending works — send a test to">
            <input
              className="input"
              type="email"
              placeholder={mailbox.email_address}
              value={testTo}
              onChange={(e) => setTestTo(e.target.value)}
            />
          </Field>
        </div>
        <button
          className="btn-secondary text-xs mb-1"
          disabled={busy === "Test" || !testTo}
          onClick={() =>
            run("Test", () => mailboxApi.testSend(mailbox.id, testTo), () =>
              toast.success(`Test sent from ${mailbox.email_address}`),
            )
          }
        >
          <Send size={13} className="mr-1" /> Send test
        </button>
      </div>
    </div>
  );
}

export default function MailboxRepliesTab({ banner }: { banner?: string | null }) {
  const [report, setReport] = useState<ReplyRoutingReport | null>(null);
  const [mailboxes, setMailboxes] = useState<Mailbox[]>([]);
  const [oauthReady, setOauthReady] = useState(false);
  const [loading, setLoading] = useState(true);
  const [connecting, setConnecting] = useState(false);
  const [googleBusy, setGoogleBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [rep, list] = await Promise.all([mailboxApi.report(), mailboxApi.list(true)]);
      setReport(rep);
      setMailboxes(list.items);
      setOauthReady(list.google_oauth_ready);
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not read the reply settings");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const connectGoogle = async () => {
    setGoogleBusy(true);
    try {
      const { url } = await mailboxApi.googleStart();
      window.open(url, "_blank", "noopener,noreferrer,width=640,height=760");
      toast("Finish the Google sign-in in the window that opened", { icon: "🔑" });
      // The popup ends on a page that redirects here; poll for the new mailbox so
      // the tab fills in without a manual refresh.
      let tries = 0;
      const timer = setInterval(async () => {
        tries += 1;
        await load();
        if (tries > 20) clearInterval(timer);
      }, 3000);
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not start the Google sign-in");
    } finally {
      setGoogleBusy(false);
    }
  };

  if (loading) {
    return (
      <div className="card p-6">
        <div className="skeleton h-8 w-64 mb-4" />
        <div className="space-y-3">
          {[...Array(3)].map((_, i) => (
            <div key={i} className="skeleton h-16 w-full" />
          ))}
        </div>
      </div>
    );
  }

  const findings = report?.findings || [];

  return (
    <div className="space-y-4">
      {banner && (
        <div className="card border-l-4 border-green-500 bg-green-50 dark:bg-green-950/30">
          <p className="text-sm font-medium flex items-center gap-2">
            <CheckCircle2 size={16} className="text-green-600" /> {banner}
          </p>
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-bold flex items-center gap-2">
            <Inbox size={18} className="text-primary-600" /> Replies
          </h2>
          <p className="text-sm text-gray-500 max-w-2xl">
            Where a prospect's reply can actually reach you, and why it may be landing in Spam
            instead.
          </p>
        </div>
        <div className="flex gap-2">
          <button className="btn-secondary" onClick={load}>
            <RefreshCw size={15} className="mr-1" /> Refresh
          </button>
          <button className="btn-primary" onClick={connectGoogle} disabled={googleBusy}>
            <Plus size={15} className="mr-1" />
            {googleBusy ? "Opening Google…" : "Connect with Google"}
          </button>
          <button className="btn-secondary" onClick={() => setConnecting(true)}>
            <KeyRound size={15} className="mr-1" /> App password
          </button>
        </div>
      </div>

      {!oauthReady && (
        <div className="card border-l-4 border-amber-400 p-3 text-sm">
          <p className="font-medium">“Connect with Google” needs two environment variables</p>
          <p className="text-gray-600 dark:text-gray-300 text-xs mt-1 leading-relaxed">
            Set <code>GOOGLE_CLIENT_ID</code> and <code>GOOGLE_CLIENT_SECRET</code> from an OAuth
            client (Desktop app) at console.cloud.google.com with the Gmail API enabled, and add{" "}
            <code>{window.location.origin}/api/v1/mailbox/google/callback</code> as an authorized
            redirect URI. Until then, use <strong>App password</strong> — it needs no Google project
            and does everything except create Gmail filters.
          </p>
        </div>
      )}

      {report && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <Stat
            label="Mailboxes connected"
            value={report.connected}
            icon={<Inbox size={18} />}
            hint={report.connected ? "Replies are read automatically" : "Nothing is reading your inbox yet"}
          />
          <Stat
            label="Replies imported"
            value={report.replies_imported}
            icon={<Mail size={18} />}
            hint="Stored on the conversation they answer"
          />
          <Stat
            label="Rescued from Spam"
            value={report.rescued_from_spam}
            icon={<ShieldCheck size={18} />}
            hint="Moved back to the inbox, then imported"
          />
          <Stat
            label="Last check"
            value={report.last_sync_at ? fmtDate(report.last_sync_at) : "never"}
            icon={<RefreshCw size={18} />}
            hint="Every mailbox has its own interval"
          />
        </div>
      )}

      {findings.length > 0 && (
        <div className="card space-y-3">
          <h3 className="font-semibold">Why replies go missing</h3>
          {findings.map((f, i) => {
            const style = SEVERITY_STYLE[f.severity] || SEVERITY_STYLE.low;
            const Icon = style.icon;
            return (
              <div key={i} className="flex items-start gap-3 text-sm">
                <Icon
                  size={17}
                  className={`mt-0.5 shrink-0 ${
                    f.severity === "high"
                      ? "text-red-500"
                      : f.severity === "medium"
                        ? "text-amber-500"
                        : "text-gray-400"
                  }`}
                />
                <div className="min-w-0">
                  <p className="font-medium">
                    {f.where} <span className={style.chip}>{f.value}</span>
                  </p>
                  <p className="text-gray-600 dark:text-gray-300 text-xs mt-0.5 leading-relaxed">
                    {f.problem}
                  </p>
                  <p className="text-xs mt-1 text-primary-700 dark:text-primary-300">
                    <strong>Fix:</strong> {f.fix}
                  </p>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {report && report.healthy && findings.length === 0 && (
        <div className="card border-l-4 border-green-500 p-3 text-sm flex items-center gap-2">
          <CheckCircle2 size={16} className="text-green-600" /> Nothing looks wrong with reply
          routing right now.
        </div>
      )}

      {report && (
        <div className="card space-y-2">
          <h3 className="font-semibold">The three doors a reply can come through</h3>
          {report.paths.map((p) => {
            const ready = p.status !== "not connected" && p.status !== "not configured";
            return (
              <div key={p.id} className="flex items-start gap-3 text-sm">
                {ready ? (
                  <CheckCircle2 size={16} className="text-green-500 mt-0.5 shrink-0" />
                ) : (
                  <XCircle size={16} className="text-gray-300 mt-0.5 shrink-0" />
                )}
                <div>
                  <p className="font-medium">
                    {p.label} <span className={ready ? "badge-green" : "badge-gray"}>{p.status}</span>
                  </p>
                  <p className="text-xs text-gray-500">{p.detail}</p>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {mailboxes.length === 0 ? (
        <Empty
          icon={<Inbox size={28} />}
          title="No mailbox connected"
          body="Connect the mailbox your prospects reply to. The app then reads INBOX and Spam, threads every reply onto the conversation it answers, moves replies out of Spam, and can send your answer back from the same address."
          action={
            <div className="flex gap-2 justify-center">
              <button className="btn-primary" onClick={connectGoogle}>
                Connect with Google
              </button>
              <button className="btn-secondary" onClick={() => setConnecting(true)}>
                Use an app password
              </button>
            </div>
          }
        />
      ) : (
        mailboxes.map((m) => (
          <MailboxRow
            key={m.id}
            mailbox={m}
            reload={load}
            onDisconnect={(mailbox) => {
              if (
                !window.confirm(
                  `Disconnect ${mailbox.email_address}? Imported replies stay in the inbox; the stored credential is deleted and any Gmail filters this app created are removed.`,
                )
              )
                return;
              mailboxApi
                .disconnect(mailbox.id)
                .then((r) => {
                  toast.success(`${r.email_address} disconnected`);
                  load();
                })
                .catch((err: any) =>
                  toast.error(err.response?.data?.detail || "Could not disconnect"),
                );
            }}
          />
        ))
      )}

      {connecting && <ConnectModal close={() => setConnecting(false)} connected={load} />}
    </div>
  );
}
