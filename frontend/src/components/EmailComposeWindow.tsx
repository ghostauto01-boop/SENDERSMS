import { useEffect, useRef, useState } from "react";
import toast from "react-hot-toast";
import { ChevronDown, Mail, Maximize2, Minimize2, Send, X } from "lucide-react";
import emailApi, { EmailSender } from "../api/email";
import { optionalSenderId, senderOptionLabel } from "../utils/senders";
import RichEmailEditor, { AttachmentPayload } from "./RichEmailEditor";

/**
 * The Gmail-style composer.
 *
 * A mail app without a Compose button is a reader, not a client. This is the
 * small floating window people already know: it opens in the bottom-right on a
 * desktop, takes the whole screen on a phone, and can be maximised to a full
 * letter-size sheet while you write a long one.
 *
 * It is deliberately *not* the Email Manager's send panel: that one is a page
 * for a broadcast (lists, scheduling, templates). This one is one person, one
 * subject, one letter — which is what you want after reading a reply.
 */
export default function EmailComposeWindow({
  senders,
  onClose,
  onSent,
  defaultTo = "",
  defaultSubject = "",
  defaultSenderId = "",
}: {
  senders: EmailSender[];
  onClose: () => void;
  onSent?: () => void;
  defaultTo?: string;
  defaultSubject?: string;
  defaultSenderId?: string;
}) {
  const [to, setTo] = useState(defaultTo);
  const [cc, setCc] = useState("");
  const [subject, setSubject] = useState(defaultSubject);
  const [body, setBody] = useState("");
  const [html, setHtml] = useState("");
  const [attachments, setAttachments] = useState<AttachmentPayload[]>([]);
  const [senderId, setSenderId] = useState(defaultSenderId);
  const [showCc, setShowCc] = useState(false);
  const [maximised, setMaximised] = useState(false);
  const [sending, setSending] = useState(false);
  const toRef = useRef<HTMLInputElement>(null);

  // Gmail lands the cursor in the To field; so does this.
  useEffect(() => {
    const timer = window.setTimeout(() => toRef.current?.focus(), 120);
    return () => window.clearTimeout(timer);
  }, []);

  const send = async () => {
    const address = to.trim();
    if (!address.includes("@")) {
      toast.error("Add the address you are writing to");
      return;
    }
    if (!subject.trim()) {
      toast.error("Add a subject line");
      return;
    }
    if (!body.trim() && !html.trim()) {
      toast.error("Write the email body");
      return;
    }
    setSending(true);
    try {
      const response = await emailApi.send({
        email: address,
        subject: subject.trim(),
        body,
        html_body: html.trim() || null,
        attachments: attachments.length ? attachments : null,
        email_account_id: optionalSenderId(senderId),
        cc: cc.trim() ? cc.split(/[,;\s]+/).filter(Boolean) : null,
      });
      toast.success(response?.scheduled ? "Email scheduled" : "Email sent");
      onSent?.();
      onClose();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not send this email");
    } finally {
      setSending(false);
    }
  };

  return (
    <div
      className={`fixed z-[80] flex flex-col bg-white dark:bg-gray-800 shadow-pop border border-gray-200/80 dark:border-gray-700
        ${
          maximised
            ? "inset-2 sm:inset-6 lg:inset-10 rounded-2xl"
            : "inset-0 sm:inset-auto sm:bottom-4 sm:right-4 sm:w-[560px] sm:h-[540px] sm:rounded-2xl"
        }`}
      role="dialog"
      aria-label="Compose email"
    >
      <div className="shrink-0 flex items-center gap-2 px-4 h-12 rounded-t-2xl bg-primary-950 dark:bg-primary-900 text-white">
        <Mail size={15} className="opacity-80" />
        <span className="text-sm font-medium flex-1 truncate">New message</span>
        <button
          type="button"
          onClick={() => setMaximised((v) => !v)}
          className="p-1.5 rounded-lg hover:bg-white/10 transition-colors"
          title={maximised ? "Restore" : "Maximise"}
          aria-label={maximised ? "Restore composer" : "Maximise composer"}
        >
          {maximised ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
        </button>
        <button
          type="button"
          onClick={onClose}
          className="p-1.5 rounded-lg hover:bg-white/10 transition-colors"
          title="Close"
          aria-label="Close composer"
        >
          <X size={15} />
        </button>
      </div>

      <div className="flex-1 min-h-0 flex flex-col p-3 sm:p-4 gap-2 overflow-y-auto">
        <div className="flex items-center gap-2 border-b border-gray-100 dark:border-gray-700 pb-1.5">
          <span className="text-xs text-gray-400 w-10 shrink-0">To</span>
          <input
            ref={toRef}
            className="flex-1 min-w-0 bg-transparent outline-none text-sm placeholder:text-gray-400"
            placeholder="name@example.com"
            value={to}
            onChange={(e) => setTo(e.target.value)}
            aria-label="To"
          />
          <button
            type="button"
            className="text-xs text-gray-500 hover:text-primary-600 shrink-0"
            onClick={() => setShowCc((v) => !v)}
          >
            Cc
          </button>
        </div>

        {showCc && (
          <div className="flex items-center gap-2 border-b border-gray-100 dark:border-gray-700 pb-1.5 animate-fade-in">
            <span className="text-xs text-gray-400 w-10 shrink-0">Cc</span>
            <input
              className="flex-1 min-w-0 bg-transparent outline-none text-sm placeholder:text-gray-400"
              placeholder="separate addresses with commas"
              value={cc}
              onChange={(e) => setCc(e.target.value)}
              aria-label="Cc"
            />
          </div>
        )}

        <div className="flex items-center gap-2 border-b border-gray-100 dark:border-gray-700 pb-1.5">
          <span className="text-xs text-gray-400 w-10 shrink-0">From</span>
          <div className="relative flex-1 min-w-0">
            <select
              className="w-full appearance-none bg-transparent outline-none text-sm pr-6 truncate"
              value={senderId}
              onChange={(e) => setSenderId(e.target.value)}
              aria-label="Send from"
            >
              <option value="">Default sender</option>
              {senders.map((s) => (
                <option key={s.id} value={s.id}>
                  {senderOptionLabel(s)}
                </option>
              ))}
            </select>
            <ChevronDown size={13} className="absolute right-0 top-1/2 -translate-y-1/2 text-gray-400 pointer-events-none" />
          </div>
        </div>

        <input
          className="w-full bg-transparent outline-none text-[15px] font-medium placeholder:text-gray-400 py-1"
          placeholder="Subject"
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
          aria-label="Subject"
        />

        <div className="flex-1 min-h-[160px]">
          <RichEmailEditor
            body={body}
            onBody={setBody}
            html={html}
            onHtml={setHtml}
            attachments={attachments}
            onAttachments={setAttachments}
          />
        </div>
      </div>

      <div className="shrink-0 flex items-center justify-between gap-2 px-3 sm:px-4 py-2.5 border-t border-gray-100 dark:border-gray-700 safe-bottom">
        <button type="button" className="btn-secondary btn-sm" onClick={onClose}>
          Discard
        </button>
        <button type="button" className="btn-primary btn-sm" onClick={send} disabled={sending}>
          {sending ? "Sending…" : (<><Send size={14} /> Send</>)}
        </button>
      </div>
    </div>
  );
}
