import { useCallback, useEffect, useState } from "react";
import toast from "react-hot-toast";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Copy,
  ExternalLink,
  Mail,
  Plus,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import mailboxApi, { GoogleOAuthConfig } from "../api/mailbox";
import { Field } from "../pages/ads/ui";

/**
 * GMAIL REPLIES — GOOGLE SIGN-IN, SET UP FROM INSIDE THE APP.
 *
 * This card replaces "go find the two environment variables" with the thing the
 * operator actually needs: the exact values Google asks for, in the order
 * Google asks for them, next to the fields they get pasted into.
 *
 * Three jobs:
 *
 * 1. **Show the callback URL.** `redirect_uri_mismatch` is the single most
 *    common failure, and it is always a copy/paste problem — so the URI is
 *    rendered once, in full, with a copy button, and it is derived from this
 *    deployment's own public address rather than typed by hand.
 * 2. **Save the OAuth client here.** The client ID and secret are stored
 *    encrypted in the database (`system_settings`), so a deployment whose owner
 *    cannot edit server variables — Render dashboard aside — can still finish
 *    the setup. `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` keep working and are
 *    used when nothing is saved here; the card says which one is in force.
 * 3. **Make a second, third, tenth Gmail inbox the same click as the first.**
 *    The consent URL asks Google for `prompt=consent select_account`, so the
 *    account chooser always appears and each inbox gets its own refresh token.
 *    Connected inboxes are listed here with their last sync, and each one is
 *    polled on its own interval.
 *
 * The secret is write-only: it is never returned by the API, so the field shows
 * "saved" rather than the value, and re-saving the client ID alone cannot wipe
 * it.
 */

const CONSOLE = "https://console.cloud.google.com";

/** One numbered step, with the exact values to type where they matter. */
function Step({
  n,
  title,
  children,
}: {
  n: number;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <li className="flex gap-3">
      <span className="w-6 h-6 shrink-0 rounded-full bg-primary-100 dark:bg-primary-950/60 text-primary-700 dark:text-primary-300 text-xs font-bold flex items-center justify-center mt-0.5">
        {n}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold text-gray-900 dark:text-white">{title}</p>
        <div className="text-sm text-gray-600 dark:text-gray-300 mt-1 space-y-1.5 leading-relaxed">
          {children}
        </div>
      </div>
    </li>
  );
}

/** A value the operator has to copy into Google's console, with a copy button. */
function CopyValue({
  label,
  value,
  note,
}: {
  label: string;
  value: string | null;
  note?: string;
}) {
  const copy = async () => {
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
      toast.success(`${label} copied`);
    } catch {
      // Clipboard permission denied (or an insecure context): select it instead.
      toast.error("Could not copy — select the text and copy it manually");
    }
  };

  return (
    <div className="rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/40 p-2.5">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
        {label}
      </p>
      <div className="flex items-start gap-2 mt-1">
        <code className="flex-1 text-xs break-all text-gray-800 dark:text-gray-100">
          {value || "not available yet — see the warning above"}
        </code>
        <button
          type="button"
          onClick={copy}
          disabled={!value}
          className="btn-secondary btn-sm shrink-0 disabled:opacity-40"
          aria-label={`Copy ${label}`}
          title={`Copy ${label}`}
        >
          <Copy size={13} />
        </button>
      </div>
      {note && <p className="text-[11px] text-gray-500 mt-1">{note}</p>}
    </div>
  );
}

export default function GoogleOAuthSetupCard({
  onChanged,
  startOpen,
  returnTo,
}: {
  /** Called after the client is saved or an inbox is connected. */
  onChanged?: () => void;
  /** Open the step-by-step immediately (used when nothing is configured). */
  startOpen?: boolean;
  /** Where Google should send the browser back after the consent screen. */
  returnTo?: string;
}) {
  const [config, setConfig] = useState<GoogleOAuthConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [saving, setSaving] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [showSteps, setShowSteps] = useState(Boolean(startOpen));
  const [refreshing, setRefreshing] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await mailboxApi.googleConfig();
      setConfig(data);
      setClientId(data.client_id || "");
      // Open the guide by default only while OAuth cannot work yet: once it is
      // configured the steps are reference material, not the next action.
      setShowSteps((open) => open || !data.configured);
    } catch {
      toast.error("Could not read the Gmail OAuth settings");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const save = async () => {
    setSaving(true);
    try {
      const result = await mailboxApi.saveGoogleConfig({
        client_id: clientId.trim(),
        // Only send the secret when one was typed, so saving the ID again never
        // clears the stored secret.
        ...(clientSecret.trim() ? { client_secret: clientSecret.trim() } : {}),
      });
      (result.warnings || []).forEach((w) => toast(w, { icon: "⚠️" }));
      toast.success(
        result.configured
          ? "Google OAuth saved — you can connect a Gmail inbox now"
          : "Saved. Add the client secret too and Google sign-in will work.",
      );
      setClientSecret("");
      await load();
      onChanged?.();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not save the Google OAuth client");
    } finally {
      setSaving(false);
    }
  };

  /** One consent round-trip = one Gmail inbox. Repeat it to add another. */
  const connectGmail = async () => {
    setConnecting(true);
    try {
      const { url } = await mailboxApi.googleStart({
        returnTo: returnTo || "/settings?tab=gmail",
      });
      // Same tab, not a popup: Google blocks popups for some accounts, and the
      // callback closes the loop by sending the browser back here.
      window.location.href = url;
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not start the Google sign-in");
      setConnecting(false);
    }
  };

  const syncNow = async (id: number, address: string) => {
    setRefreshing(id);
    try {
      const summary = await mailboxApi.sync(id);
      toast.success(
        summary.stored
          ? `${summary.stored} repl${summary.stored === 1 ? "y" : "ies"} imported from ${address}`
          : `Nothing new in ${address}`,
      );
      await load();
      onChanged?.();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || `Could not import from ${address}`);
    } finally {
      setRefreshing(null);
    }
  };

  if (loading) {
    return (
      <div className="card p-4 space-y-3">
        <div className="skeleton h-5 w-64" />
        <div className="skeleton h-20 w-full" />
      </div>
    );
  }

  const ready = Boolean(config?.configured);
  const mailboxes = config?.gmail_mailboxes || [];

  return (
    <div className="card p-4 sm:p-5 space-y-4">
      {/* ---------------- Header ---------------- */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <Mail size={18} className="text-primary-600" /> Gmail replies — Google sign-in
            {ready ? (
              <span className="badge-green inline-flex items-center gap-1">
                <CheckCircle2 size={11} /> ready
              </span>
            ) : (
              <span className="badge-yellow inline-flex items-center gap-1">
                <AlertTriangle size={11} /> not set up yet
              </span>
            )}
          </h2>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-1 max-w-2xl">
            Sign in with Google instead of an app password. Connect as many Gmail inboxes as you
            want — each one is read on its own schedule, and replies can be sent back out from the
            same address they arrived on.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            className="btn-primary btn-sm"
            onClick={connectGmail}
            disabled={connecting || !ready}
            title={
              ready
                ? "Sign in with Google — press it again to add another Gmail inbox"
                : "Save your Google OAuth client under Settings → Gmail replies first"
            }
          >
            <Plus size={14} className="mr-1" />
            {connecting ? "Opening Google…" : "Connect a Gmail inbox"}
          </button>
          <button
            className="btn-secondary btn-sm"
            onClick={() => setShowSteps((v) => !v)}
            aria-expanded={showSteps}
          >
            {showSteps ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            <span className="ml-1">{showSteps ? "Hide the setup steps" : "Show the setup steps"}</span>
          </button>
        </div>
      </div>

      {!ready && (
        <div className="rounded-xl border border-warning-300 dark:border-warning-800 bg-warning-50 dark:bg-warning-950/30 p-3 text-sm">
          <p className="font-medium text-warning-800 dark:text-warning-200">
            “Connect a Gmail inbox” stays switched off until an OAuth client is saved
          </p>
          <p className="text-xs text-warning-700 dark:text-warning-300 mt-1 leading-relaxed">
            Follow the steps below once — about five minutes in the Google Cloud Console — then paste
            the client ID and secret here. An IMAP app password still works without any of this, but
            it cannot create Gmail's “never send it to Spam” filters and Google is retiring it for
            most accounts.
          </p>
        </div>
      )}

      {config && !config.public_base_url && (
        <div className="rounded-xl border border-danger-300 dark:border-danger-800 bg-danger-50 dark:bg-danger-950/30 p-3 text-sm">
          <p className="font-medium text-danger-800 dark:text-danger-200">
            This deployment has no public address yet, so Google has nowhere to send you back
          </p>
          <p className="text-xs text-danger-700 dark:text-danger-300 mt-1 leading-relaxed">
            Set <code>PUBLIC_BASE_URL</code> to the HTTPS address you open this app on (for example{" "}
            <code>https://crm.example.com</code>) and restart. The redirect URI below appears as soon
            as it is set.
          </p>
        </div>
      )}

      {/* ---------------- The values Google asks for ---------------- */}
      <div className="grid sm:grid-cols-2 gap-2.5">
        <CopyValue
          label="Authorized redirect URI (callback URL)"
          value={config?.redirect_uri || null}
          note="Paste this into the OAuth client exactly as shown — https, no trailing slash."
        />
        <CopyValue
          label="Authorized JavaScript origin"
          value={config?.javascript_origin || null}
          note="Optional on Google's form, harmless to add: the origin only, no path."
        />
        <CopyValue
          label="Scope to request"
          value={config?.scope || null}
          note="Full mailbox access: reads Inbox and Spam, sends replies, creates the Spam filter."
        />
        <CopyValue
          label="OAuth client in use"
          value={
            config?.configured
              ? `${config.client_id_masked || config.client_id} · from ${
                  config.source === "env" ? "the server environment" : "this app"
                }`
              : null
          }
          note={
            config?.configured
              ? "Saved values win over the environment. Re-save here to switch clients."
              : "Nothing saved yet."
          }
        />
      </div>

      {/* ---------------- Step by step ---------------- */}
      {showSteps && (
        <div className="rounded-2xl border border-gray-200 dark:border-gray-700 p-4 space-y-4 animate-fade-in">
          <div>
            <h3 className="font-semibold">Setting it up in the Google Cloud Console</h3>
            <p className="text-xs text-gray-500 mt-0.5">
              Once per deployment, not once per inbox. After this, adding another Gmail address is
              one click.
            </p>
          </div>

          <ol className="space-y-4">
            <Step n={1} title="Create (or open) a Google Cloud project">
              <p>
                Go to{" "}
                <a className="text-primary-600 hover:underline inline-flex items-center gap-1" href={CONSOLE} target="_blank" rel="noreferrer">
                  console.cloud.google.com <ExternalLink size={11} />
                </a>{" "}
                and sign in with <strong>any</strong> Google account — it owns the OAuth client, not
                the mailboxes you connect. Project picker → <strong>New Project</strong> → name it
                something you recognise (e.g. “SENDERSMS replies”) → Create. No billing account is
                needed: the Gmail API quota used here is free.
              </p>
            </Step>

            <Step n={2} title="Enable the Gmail API">
              <p>
                Menu → <strong>APIs &amp; Services → Library</strong> → search{" "}
                <strong>Gmail API</strong> → <strong>Enable</strong>. (Direct link:{" "}
                <a className="text-primary-600 hover:underline" href={`${CONSOLE}/apis/library/gmail.googleapis.com`} target="_blank" rel="noreferrer">
                  Gmail API in the library
                </a>
                .) Without this the consent screen fails with <code>access_denied</code>.
              </p>
            </Step>

            <Step n={3} title="Configure the OAuth consent screen">
              <p>
                <strong>APIs &amp; Services → OAuth consent screen</strong>:
              </p>
              <ul className="list-disc pl-5 space-y-1">
                <li>
                  User type: <strong>External</strong> (Internal is only for Google Workspace
                  accounts in your own domain).
                </li>
                <li>
                  App name, your support email and your developer email — these are what the consent
                  screen shows, so use something you recognise.
                </li>
                <li>
                  Scopes: <strong>Add or remove scopes</strong> → paste{" "}
                  <code>{config?.scope || "https://mail.google.com/"}</code> → Update. This one scope
                  covers reading Inbox and Spam, sending a reply and creating the Spam filter.
                </li>
                <li>
                  <strong>Audience → Test users:</strong> while the app is in <em>Testing</em> only
                  listed accounts can sign in. Add <strong>every Gmail address you intend to
                  connect</strong> (up to 100). This is the step people miss when the second inbox
                  refuses with <code>access_blocked</code>.
                </li>
                <li>Save.</li>
              </ul>
              <p className="text-xs text-gray-500">
                Publishing the app (“In production”) removes the test-user limit but puts it through
                Google's verification for sensitive scopes; for your own inboxes, Testing + test
                users is the right answer. A Testing consent screen expires refresh tokens after 7
                days <em>unless</em> the account is a test user — another reason to add each one.
              </p>
            </Step>

            <Step n={4} title="Create the OAuth client ID">
              <p>
                <strong>APIs &amp; Services → Credentials → Create Credentials → OAuth client ID</strong>:
              </p>
              <ul className="list-disc pl-5 space-y-1">
                <li>
                  Application type: <strong>Web application</strong> — not “Desktop app”. This app
                  runs the callback on its own server, so it needs a redirect URI.
                </li>
                <li>
                  Authorized JavaScript origins: <code>{config?.javascript_origin || "https://your-app"}</code>
                </li>
                <li>
                  Authorized redirect URIs: <strong>add the callback URL copied above</strong>{" "}
                  (<code>{config?.redirect_uri || "https://your-app/api/v1/mailbox/google/callback"}</code>
                  ). One character of difference — a trailing slash, http instead of https, a missing{" "}
                  <code>/api/v1</code> — is what causes <code>redirect_uri_mismatch</code>.
                </li>
                <li>Create.</li>
              </ul>
            </Step>

            <Step n={5} title="Paste the client ID and secret below, then save">
              <p>
                Google shows the <strong>Client ID</strong> and <strong>Client secret</strong> once —
                copy both into the form under this card. The secret is encrypted before it is stored
                and is never shown again; you can always reset it in the console and paste the new
                one here.
              </p>
            </Step>

            <Step n={6} title="Connect your first Gmail inbox">
              <p>
                Press <strong>Connect a Gmail inbox</strong>. Google asks which account to use, then
                shows a consent screen. Because the app is not verified by Google you will see{" "}
                <em>“Google hasn't verified this app”</em> — press <strong>Advanced</strong> →{" "}
                <strong>Go to {`<your app name>`} (unsafe)</strong> → <strong>Allow</strong>. That
                warning is about the app not having paid for a Google audit; it is not a problem with
                your setup, and it disappears if you publish and verify the app.
              </p>
            </Step>

            <Step n={7} title="Add every other Gmail inbox the same way">
              <p>
                Press <strong>Connect a Gmail inbox</strong> again and pick the next account. The
                account chooser is forced on purpose (<code>prompt=consent select_account</code>), so
                Google never silently reuses the account already signed in to your browser — which is
                how a second inbox ends up connected. Each account must be a test user (step 3), and
                each one is stored separately with its own refresh token, poll interval and counters.
              </p>
            </Step>

            <Step n={8} title="Turn on “Never send it to Spam”, then check the inbox">
              <p>
                On each connected mailbox, enable the never-spam filter (Email Manager → Replies →
                the mailbox card). It creates the same filter Gmail's own editor writes, so a
                prospect's reply is not filed away before this app can read it. Then reply to one of
                your own campaigns from another address: it should appear in the{" "}
                <strong>Email Inbox → Conversations</strong> page within a minute.
              </p>
            </Step>
          </ol>

          <div className="rounded-xl bg-gray-50 dark:bg-gray-900/40 p-3 text-xs text-gray-600 dark:text-gray-300 space-y-1.5">
            <p className="font-semibold text-gray-800 dark:text-gray-100">When it does not work</p>
            <p>
              <code>redirect_uri_mismatch</code> — the URI in the console is not byte-identical to the
              one above. Re-copy it.
            </p>
            <p>
              <code>access_blocked:unverified</code> / “app is not verified” with no Advanced button —
              that Gmail address is not a test user and the consent screen is still in Testing.
            </p>
            <p>
              <code>Google did not return a refresh_token</code> — the account already granted access
              once. Revoke it at myaccount.google.com/permissions and connect again (this app always
              asks with <code>prompt=consent</code>, so a re-consent fixes it).
            </p>
            <p>
              <code>invalid_client</code> — the client ID and secret are not from the same OAuth
              client, or the secret was reset in the console and not re-saved here.
            </p>
            <p>
              Replies arrive but nothing is imported — the mailbox's folders are read as{" "}
              <code>INBOX</code> and <code>[Gmail]/Spam</code>; press <strong>Import replies now</strong>{" "}
              on the mailbox card and read the error it reports.
            </p>
          </div>
        </div>
      )}

      {/* ---------------- Credentials ---------------- */}
      <div className="rounded-2xl border border-gray-200 dark:border-gray-700 p-4 space-y-3">
        <div className="flex items-start justify-between gap-2">
          <div>
            <h3 className="font-semibold flex items-center gap-2">
              <ShieldCheck size={16} className="text-primary-600" /> Your OAuth client
            </h3>
            <p className="text-xs text-gray-500 mt-0.5">
              Stored encrypted in this app's database. Leave it empty to keep using{" "}
              <code>GOOGLE_CLIENT_ID</code> / <code>GOOGLE_CLIENT_SECRET</code> from the environment.
            </p>
          </div>
          {config?.source === "env" && <span className="badge-gray shrink-0">from environment</span>}
        </div>

        <Field label="Client ID" hint="Ends in .apps.googleusercontent.com. Not secret — it appears in every consent URL.">
          <input
            className="input font-mono text-xs"
            autoComplete="off"
            spellCheck={false}
            placeholder="1234567890-abcdefghijklmnop.apps.googleusercontent.com"
            value={clientId}
            onChange={(e) => setClientId(e.target.value)}
          />
        </Field>

        <Field
          label="Client secret"
          hint={
            config?.has_secret
              ? "A secret is already saved — type a new one only to replace it."
              : "Shown once by Google when the client is created; ‘Reset secret’ there gets you a new one."
          }
        >
          <input
            className="input font-mono text-xs"
            type="password"
            autoComplete="off"
            spellCheck={false}
            placeholder={config?.has_secret ? "•••••••••••• saved" : "GOCSPX-…"}
            value={clientSecret}
            onChange={(e) => setClientSecret(e.target.value)}
          />
        </Field>

        <div className="flex flex-wrap gap-2">
          <button
            className="btn-primary btn-sm"
            onClick={save}
            disabled={saving || !clientId.trim()}
          >
            {saving ? "Saving…" : "Save OAuth client"}
          </button>
          <button className="btn-secondary btn-sm" onClick={load} disabled={loading}>
            <RefreshCw size={13} className="mr-1" /> Re-check
          </button>
        </div>
      </div>

      {/* ---------------- Connected Gmail inboxes ---------------- */}
      <div className="space-y-2">
        <div className="flex items-center justify-between gap-2">
          <h3 className="font-semibold">
            Gmail inboxes connected{" "}
            <span className="badge-gray ml-1">{mailboxes.length}</span>
          </h3>
          {ready && (
            <button className="btn-secondary btn-sm" onClick={connectGmail} disabled={connecting}>
              <Plus size={13} className="mr-1" /> Add another
            </button>
          )}
        </div>

        {mailboxes.length === 0 ? (
          <p className="text-sm text-gray-500 dark:text-gray-400 rounded-xl border border-dashed border-gray-300 dark:border-gray-600 p-3">
            None yet. Once one is connected, its replies are imported automatically and you can send
            answers back out from the same address.
          </p>
        ) : (
          <ul className="space-y-2">
            {mailboxes.map((m) => (
              <li
                key={m.id}
                className="rounded-xl border border-gray-200 dark:border-gray-700 p-3 flex flex-wrap items-center gap-2"
              >
                <span className="w-8 h-8 rounded-full bg-primary-100 dark:bg-primary-950/60 text-primary-700 dark:text-primary-300 flex items-center justify-center shrink-0">
                  <Mail size={15} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-medium truncate">{m.email_address}</span>
                  <span className="block text-[11px] text-gray-500">
                    {m.is_active ? "active" : "paused"} · checks every {m.poll_interval}s ·{" "}
                    {m.total_replies} replies imported
                    {m.last_sync_at ? ` · last ${new Date(m.last_sync_at).toLocaleString()}` : " · never synced"}
                  </span>
                  {m.last_error && (
                    <span className="block text-[11px] text-danger-600 mt-0.5 break-words">
                      {m.last_error}
                    </span>
                  )}
                </span>
                <button
                  className="btn-secondary btn-sm"
                  onClick={() => syncNow(m.id, m.email_address)}
                  disabled={refreshing === m.id}
                  title="Import this inbox's replies right now"
                >
                  <RefreshCw size={13} className={refreshing === m.id ? "mr-1 animate-spin" : "mr-1"} />
                  {refreshing === m.id ? "Importing…" : "Import now"}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
