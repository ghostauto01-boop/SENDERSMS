import { useState } from "react";
import toast from "react-hot-toast";
import { Send } from "lucide-react";
import emailApi, { EmailAccount } from "../api/email";
import { Field, fmtDate } from "../pages/ads/ui";
import RichEmailEditor, { AttachmentPayload } from "./RichEmailEditor";

/**
 * The email composer, shared by the Email Manager's "Send" tab and the Send
 * page when the Email channel is selected — one form, so both places always
 * offer exactly the same capabilities (template, sender, scheduling).
 */
export default function EmailSendPanel({
  reference,
  accounts,
  onSent,
}: {
  reference: any;
  accounts: EmailAccount[];
  onSent?: () => void;
}) {
  const [form, setForm] = useState<any>({
    mode: "email" as "email" | "list",
    email: "",
    list_id: "",
    subject: "",
    body: "",
    html_body: "",
    template_id: "",
    email_account_id: "",
    schedule_at: "",
  });
  const [attachments, setAttachments] = useState<AttachmentPayload[]>([]);
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<any>(null);
  const set = (k: string, v: any) => setForm((f: any) => ({ ...f, [k]: v }));

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.subject.trim()) return toast.error("Add a subject line");
    if (!(form.body.trim() || form.html_body.trim())) return toast.error("Write the email body");
    if (form.mode === "email" && !form.email.includes("@")) return toast.error("Enter a valid address");
    if (form.mode === "list" && !form.list_id) return toast.error("Choose a list");

    setSending(true);
    setResult(null);
    try {
      const payload: any = {
        subject: form.subject,
        body: form.body,
        html_body: form.html_body || null,
        // Ignored when a template supplies its own attachments.
        attachments: attachments.length ? attachments : null,
        email_account_id: form.email_account_id ? Number(form.email_account_id) : null,
        template_id: form.template_id ? Number(form.template_id) : null,
      };
      if (form.mode === "email") payload.email = form.email.trim();
      else payload.list_id = Number(form.list_id);
      if (form.schedule_at) payload.schedule_at = new Date(form.schedule_at).toISOString();
      const response = await emailApi.send(payload);
      setResult(response);
      if (response.sent) setAttachments([]);
      onSent?.();
      toast.success(
        response.scheduled
          ? "Email scheduled"
          : `Sent ${response.sent} · skipped ${response.skipped} · failed ${response.failed}`
      );
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not send this email");
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="grid lg:grid-cols-3 gap-4">
      <form onSubmit={submit} className="card space-y-4 lg:col-span-2">
        <h3 className="font-semibold">Compose an email</h3>

        <div className="flex gap-2">
          {(["email", "list"] as const).map((mode) => (
            <button
              type="button"
              key={mode}
              className={form.mode === mode ? "btn-primary text-sm" : "btn-secondary text-sm"}
              onClick={() => set("mode", mode)}
            >
              {mode === "email" ? "One address" : "A whole list"}
            </button>
          ))}
        </div>

        {form.mode === "email" ? (
          <Field label="To">
            <input
              className="input"
              type="email"
              value={form.email}
              onChange={(e) => set("email", e.target.value)}
              placeholder="lead@company.com"
            />
          </Field>
        ) : (
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
        )}

        <Field label="Template (optional)">
          <select
            className="input"
            value={form.template_id}
            onChange={(e) => {
              const id = e.target.value;
              set("template_id", id);
              const tpl = (reference?.templates || []).find((t: any) => String(t.id) === id);
              if (tpl?.subject) set("subject", tpl.subject);
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

        <Field label="Subject">
          <input
            className="input"
            value={form.subject}
            onChange={(e) => set("subject", e.target.value)}
            placeholder="Quick question about {{company}}"
          />
        </Field>

        <Field label="Message">
          <RichEmailEditor
            body={form.body}
            onBody={(value) => set("body", value)}
            html={form.html_body}
            onHtml={(value) => set("html_body", value)}
            attachments={attachments}
            onAttachments={setAttachments}
          />
        </Field>

        <div className="grid sm:grid-cols-2 gap-3">
          <Field label="Send through">
            <select
              className="input"
              value={form.email_account_id}
              onChange={(e) => set("email_account_id", e.target.value)}
            >
              <option value="">
                Default sender
                {reference?.default_account_id
                  ? ` (${accounts.find((a: any) => a.id === reference.default_account_id)?.name || ""})`
                  : ""}
              </option>
              {accounts.map((a: EmailAccount) => (
                <option key={a.id} value={a.id} disabled={!a.is_active}>
                  {a.name} — {a.from_email}
                  {a.is_active ? "" : " (off)"}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Schedule for (optional)">
            <input
              className="input"
              type="datetime-local"
              value={form.schedule_at}
              onChange={(e) => set("schedule_at", e.target.value)}
            />
          </Field>
        </div>

        <div className="flex justify-end">
          <button className="btn-primary" type="submit" disabled={sending}>
            <Send size={16} className="mr-1" />{" "}
            {sending ? "Sending…" : form.schedule_at ? "Schedule" : "Send now"}
          </button>
        </div>
      </form>

      <div className="space-y-4">
        <div className="card">
          <h3 className="font-semibold mb-2">Who this reaches</h3>
          <p className="text-sm text-gray-500">
            {reference?.emailable_contacts ?? 0} contacts are emailable right now. Opted-out, bounced
            and placeholder addresses are skipped automatically — you are never billed for them.
          </p>
        </div>
        {result && (
          <div className="card text-sm space-y-1">
            <h3 className="font-semibold">Last send</h3>
            {result.scheduled ? (
              <p>Scheduled for {fmtDate(result.schedule_at)}</p>
            ) : (
              <>
                <p>
                  Sent: <strong>{result.sent}</strong>
                </p>
                <p>Skipped: {result.skipped}</p>
                <p>Failed: {result.failed}</p>
                <p className="text-gray-500">Total targets: {result.total}</p>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
