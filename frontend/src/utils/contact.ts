/**
 * How a contact is reached, and how sure we are about their email.
 *
 * A contact can have a phone, an email, or both — so anything that renders
 * "the contact's details" has to go through here instead of assuming
 * `phone_number` is a string. Before email-only contacts existed, code could
 * safely do `contact.phone_number.slice(-2)`; that now throws.
 */

import type { Contact } from "../types";

/** The best single line to identify a contact by: name, else business, else channel. */
export function contactLabel(contact: Contact | null | undefined): string {
  if (!contact) return "";
  const name = `${contact.first_name || ""} ${contact.last_name || ""}`.trim();
  if (name) return name;
  if (contact.business_name) return contact.business_name;
  return contact.phone_number || contact.email || `Contact #${contact.id}`;
}

/** The channel to show as the contact's primary way of being reached. */
export function contactChannel(contact: Contact | null | undefined): {
  value: string;
  kind: "phone" | "email" | "none";
} {
  if (!contact) return { value: "", kind: "none" };
  if (contact.phone_number) return { value: contact.phone_number, kind: "phone" };
  if (contact.email) return { value: contact.email, kind: "email" };
  return { value: "—", kind: "none" };
}

/** Avatar initials; falls back to the channel when there is no name. */
export function contactInitials(contact: Contact | null | undefined): string {
  if (!contact) return "?";
  const first = contact.first_name?.[0] || "";
  const last = contact.last_name?.[0] || "";
  const fromName = `${first}${last}`.toUpperCase();
  if (fromName) return fromName;
  if (contact.business_name) return contact.business_name.slice(0, 2).toUpperCase();
  const channel = contact.phone_number || contact.email || "";
  return channel ? channel.replace(/[^A-Za-z0-9]/g, "").slice(-2).toUpperCase() : "?";
}

export type EmailTrust = "verified" | "unverified" | "inferred" | "invalid" | "none";

/**
 * How much an address can be trusted, driven by the enrichment columns.
 *
 * `inferred` is deliberately its own state rather than being lumped in with
 * `unverified`: an address a provider could not confirm is a different risk
 * from one this app made up from a name pattern.
 */
export function emailTrust(contact: Contact | null | undefined): EmailTrust {
  if (!contact?.email) return "none";
  if (contact.is_email_undeliverable) return "invalid";
  if (contact.email_source === "inferred" && !contact.email_verified) return "inferred";
  if (contact.email_verified) return "verified";
  return "unverified";
}

/** Short badge text for an address's trust level, or null when there is none. */
export function emailTrustLabel(trust: EmailTrust): string | null {
  switch (trust) {
    case "verified":
      return "Verified";
    case "unverified":
      return "Unverified";
    case "inferred":
      return "Guessed";
    case "invalid":
      return "Invalid";
    default:
      return null;
  }
}

/** Tailwind classes for the trust badge. */
export function emailTrustClass(trust: EmailTrust): string {
  switch (trust) {
    case "verified":
      return "bg-primary-100 text-primary-700";
    case "inferred":
      return "bg-warning-200 text-warning-700";
    case "invalid":
      return "bg-danger-100 text-danger-700";
    case "unverified":
      return "bg-gray-100 text-gray-600";
    default:
      return "bg-gray-100 text-gray-500";
  }
}

/**
 * Can this contact be emailed? Mirrors the server's `emailable` filter so the
 * UI and the sender never disagree about who is skipped.
 */
export function isEmailable(contact: Contact | null | undefined): boolean {
  if (!contact?.email) return false;
  if (contact.is_email_opted_out || contact.is_email_undeliverable) return false;
  return true;
}

/** Can this contact be texted? */
export function isTextable(contact: Contact | null | undefined): boolean {
  if (!contact?.phone_number) return false;
  if (contact.is_opted_out || contact.is_undeliverable) return false;
  return true;
}
