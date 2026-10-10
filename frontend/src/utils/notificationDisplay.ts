import {
  Bell,
  CalendarClock,
  CheckCircle2,
  Mail,
  MessageSquare,
  PhoneMissed,
  ShieldAlert,
  XCircle,
  type LucideIcon,
} from "lucide-react";

/**
 * How a notification is presented, in one place.
 *
 * The bell dropdown, the notifications page and the browser push payload all
 * show the same three things — a kind, a sender and a preview — so an SMS
 * reply and an email reply read the same way at a glance, which is the point:
 * on a phone lock screen you should be able to tell *who* wrote and *what they
 * said* without opening anything.
 *
 * The strings are parsed rather than assumed, because rows already stored keep
 * their original titles ("📱 New SMS from Ada Obi") and the API is public to
 * older builds. Anything unrecognised still renders sensibly.
 */
export interface NotificationLike {
  id: number;
  event_type: string;
  title: string;
  body?: string | null;
  is_read: boolean;
  url?: string | null;
  status?: string | null;
  created_at?: string | null;
}

export interface NotificationDisplay {
  Icon: LucideIcon;
  /** Tailwind classes for the round icon chip. */
  iconClass: string;
  /** "Ada Obi", "Campaign finished", … — never prefixed with an emoji. */
  sender: string;
  /** The message itself, or a status line. */
  preview: string;
  /** Where a tap goes; null when the row has no target. */
  href: string | null;
  /** Short label shown next to the time, e.g. "Email" / "SMS". */
  channel: string;
}

const EMAIL_PREFIXES = ["📧 new email from ", "📧 ", "new email from "];
const SMS_PREFIXES = ["📱 new sms from ", "📱 ", "new sms from "];

function stripAny(text: string, prefixes: string[]): string | null {
  const lower = text.toLowerCase();
  for (const prefix of prefixes) {
    if (lower.startsWith(prefix)) return text.slice(prefix.length).trim();
  }
  return null;
}

/** Names and statuses look better without the decorative emoji in the title. */
function stripLeadingEmoji(text: string): string {
  return text.replace(
    /^(?:[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{2190}-\u{21FF}\u{2B00}-\u{2BFF}]\u{FE0F}?)+\s*/u,
    ""
  );
}

export function notificationDisplay(item: NotificationLike): NotificationDisplay {
  const rawTitle = item.title || "";
  const body = item.body || "";

  if (item.event_type === "email_reply") {
    const sender =
      stripAny(rawTitle, EMAIL_PREFIXES) ?? stripLeadingEmoji(rawTitle) ?? "Email";
    return {
      Icon: Mail,
      iconClass: "bg-primary-50 text-primary-600 dark:bg-primary-950/60 dark:text-primary-300",
      sender,
      preview: body || "Open the Email Inbox to read it.",
      href: item.url || "/email-inbox",
      channel: "Email",
    };
  }

  if (item.event_type === "new_reply") {
    const sender = stripAny(rawTitle, SMS_PREFIXES) ?? stripLeadingEmoji(rawTitle) ?? "SMS";
    return {
      Icon: MessageSquare,
      iconClass: "bg-success-50 text-success-600 dark:bg-success-950/60 dark:text-success-300",
      sender,
      preview: body || "Open the inbox to reply.",
      href: item.url || "/inbox",
      channel: "SMS",
    };
  }

  if (item.event_type === "campaign_completed") {
    return {
      Icon: CheckCircle2,
      iconClass: "bg-success-50 text-success-600 dark:bg-success-950/60 dark:text-success-300",
      sender: stripLeadingEmoji(rawTitle) || "Campaign finished",
      preview: body,
      href: item.url || "/campaigns",
      channel: "Campaign",
    };
  }

  if (item.event_type === "campaign_failed") {
    return {
      Icon: XCircle,
      iconClass: "bg-danger-50 text-danger-600 dark:bg-danger-950/60 dark:text-danger-300",
      sender: stripLeadingEmoji(rawTitle) || "Campaign failed",
      preview: body,
      href: item.url || "/campaigns",
      channel: "Campaign",
    };
  }

  if (item.event_type === "missed_call") {
    return {
      Icon: PhoneMissed,
      iconClass: "bg-warning-50 text-warning-600 dark:bg-warning-950/60 dark:text-warning-300",
      sender: stripLeadingEmoji(rawTitle) || "Missed call",
      preview: body,
      href: item.url || "/phone",
      channel: "Call",
    };
  }

  if (item.event_type === "followup_due") {
    return {
      Icon: CalendarClock,
      iconClass: "bg-primary-50 text-primary-600 dark:bg-primary-950/60 dark:text-primary-300",
      sender: stripLeadingEmoji(rawTitle) || "Follow-up due",
      preview: body,
      href: item.url || "/follow-ups",
      channel: "Follow-up",
    };
  }

  if (item.event_type === "system_error") {
    return {
      Icon: ShieldAlert,
      iconClass: "bg-danger-50 text-danger-600 dark:bg-danger-950/60 dark:text-danger-300",
      sender: stripLeadingEmoji(rawTitle) || "Something needs attention",
      preview: body,
      href: item.url || null,
      channel: "System",
    };
  }

  return {
    Icon: Bell,
    iconClass: "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300",
    sender: stripLeadingEmoji(rawTitle) || "Notification",
    preview: body,
    href: item.url || null,
    channel: "",
  };
}

/** "3 min ago" for anything inside a day, a date after that. */
export function relativeTime(value?: string | null): string {
  if (!value) return "";
  const then = new Date(value).getTime();
  if (Number.isNaN(then)) return "";
  const seconds = Math.round((Date.now() - then) / 1000);
  if (seconds < 45) return "just now";
  if (seconds < 90) return "1 min ago";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hr${hours === 1 ? "" : "s"} ago`;
  const days = Math.round(hours / 24);
  if (days === 1) return "yesterday";
  if (days < 7) return `${days} days ago`;
  return new Date(value).toLocaleDateString();
}
