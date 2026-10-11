import { Briefcase, Mail, MessageSquare } from "lucide-react";
import { avatarColor, formatListTime, initials } from "../utils/inboxFormat";

/**
 * One conversation in a cross-channel list (Conversations, Opportunity).
 * Unread display matches the SMS Inbox: bold name and preview, the count pill,
 * and the timestamp in the primary colour.
 */
export default function ConversationRow({
  conv,
  onOpen,
  actions,
}: {
  conv: any;
  onOpen: () => void;
  actions?: React.ReactNode;
}) {
  const name = conv.contact_name || conv.contact_phone || conv.contact?.email || "Unknown";
  const isUnread = conv.unread_count > 0 || conv.status === "unread";
  const isEmail = conv.channel === "email";
  const preview = conv.last_message_preview || (isEmail ? conv.subject : "") || "No messages yet";
  return (
    <div className="group flex items-center gap-3 px-4 py-3 hover:bg-gray-50 dark:hover:bg-gray-800/60 transition-colors">
      <button onClick={onOpen} className="flex flex-1 min-w-0 items-center gap-3 text-left">
        <div
          className={`w-11 h-11 rounded-full flex items-center justify-center text-white font-medium text-[14px] flex-shrink-0 ${avatarColor(name)}`}
        >
          {initials(name, conv.contact_phone || "")}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline justify-between gap-2">
            <span
              className={`truncate text-[15px] ${
                isUnread ? "font-semibold text-gray-900 dark:text-gray-100" : "text-gray-800 dark:text-gray-200"
              }`}
            >
              {name}
            </span>
            <span
              className={`text-[11px] flex-shrink-0 ${
                isUnread ? "text-primary-600 font-medium" : "text-gray-500 dark:text-gray-400"
              }`}
            >
              {formatListTime(conv.last_message_at)}
            </span>
          </div>
          <div className="flex items-center gap-2 mt-0.5">
            <span
              className={`inline-flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wide px-1.5 py-px rounded ${
                isEmail
                  ? "bg-accent-100 text-accent-700 dark:bg-accent-950/70 dark:text-accent-300"
                  : "bg-primary-100 text-primary-700 dark:bg-primary-950/70 dark:text-primary-300"
              }`}
            >
              {isEmail ? <Mail size={10} /> : <MessageSquare size={10} />}
              {isEmail ? "Email" : "SMS"}
            </span>
            {conv.status === "opportunity" && (
              <span className="inline-flex items-center gap-1 text-[10px] font-semibold text-warning-600 dark:text-warning-400">
                <Briefcase size={10} /> Opportunity
              </span>
            )}
            <p
              className={`truncate text-[13px] min-w-0 flex-1 ${
                isUnread ? "text-gray-900 dark:text-gray-200 font-medium" : "text-gray-500 dark:text-gray-400"
              }`}
            >
              {preview}
            </p>
            {isUnread && (
              <span className="bg-primary-600 text-white text-[11px] font-medium min-w-[20px] h-[20px] px-1.5 flex items-center justify-center rounded-full flex-shrink-0">
                {conv.unread_count || 1}
              </span>
            )}
          </div>
        </div>
      </button>
      {actions && <div className="flex-shrink-0 flex items-center gap-1">{actions}</div>}
    </div>
  );
}
