import { useEffect, useState } from "react";
import { ArrowLeft, Mail, Reply, Send, Trash2, XCircle } from "lucide-react";
import type { EmailConversationDetail } from "../api/email";
import RichEmailEditor, { type AttachmentPayload } from "../components/RichEmailEditor";

/**
 * A reply is a destination, not a permanent tax on the reading pane.
 *
 * The inbox keeps this page's draft in memory while the operator goes back to
 * read the email. Only Send or an explicit Discard clears it. Keeping the same
 * conversation endpoint also preserves the existing mailbox/Brevo routing and
 * Gmail threading — this is a reading/writing change, not a new sending path.
 */
export default function EmailReplyPage({
  detail,
  loading,
  body,
  onBody,
  html,
  onHtml,
  attachments,
  onAttachments,
  cc,
  onCc,
  showCc,
  onShowCc,
  sending,
  error,
  onSend,
  onBack,
  onDiscard,
  onRetry,
}: {
  detail: EmailConversationDetail | null;
  loading: boolean;
  body: string;
  onBody: (value: string) => void;
  html: string;
  onHtml: (value: string) => void;
  attachments: AttachmentPayload[];
  onAttachments: (value: AttachmentPayload[]) => void;
  cc: string;
  onCc: (value: string) => void;
  showCc: boolean;
  onShowCc: (value: boolean) => void;
  sending: boolean;
  error: string;
  onSend: () => void;
  onBack: () => void;
  onDiscard: () => void;
  onRetry: () => void;
}) {
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const messages = detail?.messages || [];
  const latestIncoming = [...messages].reverse().find((m) => m.direction === "incoming" && m.from_address);
  const recipient = latestIncoming?.from_address || detail?.contact?.email || "No email address";
  const subject = detail?.conversation.subject || "(no subject)";
  const replySubject = /^re:\s*/i.test(subject) ? subject : `Re: ${subject}`;
  const optedOut = Boolean(detail?.contact?.is_email_opted_out);
  const hasDraft = Boolean(body.trim() || html.trim() || cc.trim() || attachments.length);
  const canSend = Boolean(detail && hasDraft && !sending && !optedOut);

  useEffect(() => {
    const handleKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && canSend && !confirmDiscard) {
        event.preventDefault();
        onSend();
      }
      if (event.key === "Escape" && confirmDiscard) setConfirmDiscard(false);
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [canSend, confirmDiscard, onSend]);

  return (
    <main className="fixed inset-0 z-[70] h-[100dvh] flex flex-col bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 safe-top" aria-label="Reply page">
      <header className="shrink-0 flex items-center gap-3 px-3 sm:px-6 h-16 border-b border-gray-200 dark:border-gray-700">
        <button type="button" className="btn-ghost btn-sm" aria-label="Back to email" onClick={onBack} disabled={sending}>
          <ArrowLeft size={17} /> <span className="hidden sm:inline">Back to email</span><span className="sm:hidden">Back</span>
        </button>
        <div className="h-6 w-px bg-gray-200 dark:bg-gray-700" />
        <h1 className="flex items-center gap-2 text-base font-semibold"><Reply size={17} /> Reply</h1>
        <span className="ml-auto hidden sm:flex items-center gap-1.5 text-xs text-gray-400"><Mail size={13} /> Same email conversation</span>
      </header>

      {loading ? (
        <div className="flex-1 p-6 space-y-4" role="status" aria-label="Loading reply">
          <div className="skeleton h-10 w-full" /><div className="skeleton h-10 w-2/3" /><div className="skeleton h-64 w-full" />
        </div>
      ) : !detail ? (
        <div className="flex-1 flex flex-col items-center justify-center gap-3 p-6 text-center">
          <XCircle size={28} className="text-gray-400" />
          <p>This conversation could not be loaded.</p>
          <button type="button" className="btn-primary" onClick={onRetry}>Try again</button>
        </div>
      ) : (
        <>
          <div className="shrink-0 px-4 sm:px-6 py-3 border-b border-gray-100 dark:border-gray-700/70 space-y-2">
            <div className="flex items-center gap-2 text-sm">
              <span className="w-14 shrink-0 text-xs text-gray-500">To</span>
              <span className="min-w-0 flex-1 truncate font-medium" title={recipient}>
                {detail.contact?.name && <span className="mr-1.5">{detail.contact.name}</span>}
                <span className="text-gray-500 dark:text-gray-400">&lt;{recipient}&gt;</span>
              </span>
              <button type="button" className="text-xs text-primary-600 dark:text-primary-400 px-2 py-1" onClick={() => onShowCc(!showCc)} aria-expanded={showCc} disabled={sending}>Cc</button>
            </div>
            {showCc && (
              <div className="flex items-center gap-2 text-sm">
                <label htmlFor="reply-cc" className="w-14 shrink-0 text-xs text-gray-500">Cc</label>
                <input id="reply-cc" type="text" className="input py-2" placeholder="Separate addresses with commas" value={cc} onChange={(e) => onCc(e.target.value)} disabled={sending} autoFocus />
              </div>
            )}
            <div className="flex items-center gap-2 text-sm">
              <span className="w-14 shrink-0 text-xs text-gray-500">Subject</span>
              <span className="min-w-0 flex-1 truncate" aria-label="Reply subject">{replySubject}</span>
            </div>
            <p className="pl-0 sm:pl-16 text-[11px] text-gray-400">
              From {detail.conversation.email_account_name || "your connected reply sender"}. Your reply stays in this thread.
            </p>
          </div>

          {optedOut && (
            <p role="alert" className="shrink-0 px-4 sm:px-6 py-2.5 text-sm bg-warning-50 dark:bg-warning-950/30 text-warning-700 dark:text-warning-300">
              This contact unsubscribed. Opt them back in on the Contacts page before sending a reply.
            </p>
          )}
          {error && (
            <div role="alert" className="shrink-0 mx-4 sm:mx-6 mt-3 rounded-xl border border-danger-200 dark:border-danger-900 bg-danger-50 dark:bg-danger-950/30 px-3 py-2.5 text-sm text-danger-700 dark:text-danger-300">
              {error} Your draft has not been cleared.
            </div>
          )}

          <div className="flex-1 min-h-0 overflow-y-auto px-3 sm:px-6 py-3 wa-scrollbar">
            <fieldset disabled={sending} className="h-full min-h-[280px] border-0 p-0 m-0 min-w-0">
              <RichEmailEditor body={body} onBody={onBody} html={html} onHtml={onHtml} attachments={attachments} onAttachments={onAttachments} fillHeight bodyLabel="Reply message" autoFocus />
            </fieldset>
          </div>

          <footer className="shrink-0 border-t border-gray-200 dark:border-gray-700 px-3 sm:px-6 py-3 safe-bottom bg-white dark:bg-gray-900">
            {confirmDiscard ? (
              <div role="alertdialog" aria-label="Discard this reply?" className="flex flex-wrap items-center justify-between gap-3">
                <p className="text-sm">Discard this reply? Your text and attachments will be removed.</p>
                <div className="flex gap-2">
                  <button type="button" className="btn-secondary btn-sm" onClick={() => setConfirmDiscard(false)} autoFocus>Keep writing</button>
                  <button type="button" className="btn-danger btn-sm" onClick={onDiscard}>Discard reply</button>
                </div>
              </div>
            ) : (
              <div className="flex items-center justify-between gap-3">
                <button type="button" className="btn-ghost btn-sm text-gray-500" onClick={() => hasDraft ? setConfirmDiscard(true) : onDiscard()} disabled={sending}>
                  <Trash2 size={15} /> Discard
                </button>
                <span className="hidden md:block text-xs text-gray-400">{hasDraft ? "Draft kept when you go back to read" : "Ctrl/⌘ + Enter to send"}</span>
                <button type="button" className="btn-primary min-w-[130px]" onClick={onSend} disabled={!canSend}>
                  {sending ? <><span className="h-4 w-4 rounded-full border-2 border-white/40 border-t-white animate-spin" /> Sending…</> : <><Send size={16} /> Send reply</>}
                </button>
              </div>
            )}
          </footer>
        </>
      )}
    </main>
  );
}
