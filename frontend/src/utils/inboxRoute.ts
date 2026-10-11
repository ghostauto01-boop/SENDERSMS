/**
 * WHICH INBOX DOES THIS BELONG TO?
 *
 * The app has two inboxes — `/inbox` for SMS and `/email-inbox` for email — and
 * for a long time every link said `/inbox`. Clicking a reply on an *email*
 * campaign therefore landed in the SMS inbox, which has no thread for that
 * person: an empty chat list and no way to answer, for a conversation that was
 * happening by email.
 *
 * Everything that links into an inbox now goes through here, so the destination
 * is decided by the channel of the thing being clicked (the campaign, the
 * conversation, the message, the notification) instead of by which page the link
 * happened to be written on.
 */

export type InboxChannel = "sms" | "email";

/** Normalise whatever a row carries (`"email"`, `null`, `"EMAIL"`, undefined). */
export function channelOf(value?: string | null): InboxChannel {
  return (value || "").trim().toLowerCase() === "email" ? "email" : "sms";
}

export function isEmailChannel(value?: string | null): boolean {
  return channelOf(value) === "email";
}

/** The inbox path for a channel. */
export function inboxPathFor(channel?: string | null): string {
  return isEmailChannel(channel) ? "/email-inbox" : "/inbox";
}

/** What to call it on a button, so the click is never a surprise. */
export function inboxLabelFor(channel?: string | null): string {
  return isEmailChannel(channel) ? "Email Inbox" : "SMS Inbox";
}

export type LinkParams = Record<string, string | number | boolean | null | undefined>;

/**
 * A deep link into the right inbox.
 *
 * Empty, null and false values are dropped, so callers can pass a whole bag of
 * context (`{campaign_id, contact_id, replied: tab === "replies"}`) without
 * building the query string by hand or ending up with `?replied=false`.
 */
export function inboxLink(channel?: string | null, params: LinkParams = {}): string {
  const query = Object.entries(params)
    .filter(([, value]) => value !== undefined && value !== null && value !== "" && value !== false)
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`)
    .join("&");
  const path = inboxPathFor(channel);
  return query ? `${path}?${query}` : path;
}

/**
 * The identifier to show for a person in a list: their email on an email thread,
 * their number on a text thread.
 *
 * An email contact often has no phone number at all, and a row that read
 * "—" under the name looked like missing data rather than "we only have their
 * address".
 */
export function contactLineFor(
  channel?: string | null,
  contact?: { email?: string | null; phone?: string | null } | null
): string {
  const email = (contact?.email || "").trim();
  const phone = (contact?.phone || "").trim();
  if (isEmailChannel(channel)) return email || phone;
  return phone || email;
}

/** A phone number this app can actually call or open in WhatsApp. */
export function hasCallablePhone(phone?: string | null): boolean {
  const value = (phone || "").trim();
  // Contacts created from an email-only import used to carry "email:<address>"
  // as a placeholder number; that is not a phone and must not get a call button.
  return Boolean(value) && !value.toLowerCase().startsWith("email:") && /\d/.test(value);
}
