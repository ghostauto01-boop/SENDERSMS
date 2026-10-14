/** Shared sender-id and label helpers for Brevo and connected Gmail pickers. */

export function isBrevoRelay(address: string | null | undefined): boolean {
  const domain = (address || "").trim().toLowerCase().split("@").pop() || "";
  return domain === "brevosend.com" || domain.endsWith(".brevosend.com");
}

/** "" and null mean "use the default". 0 is not a sender. Negative Gmail ids are kept. */
export function optionalSenderId(value: string | number | null | undefined): number | null {
  if (value == null || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed !== 0 ? parsed : null;
}

export function senderIdToForm(value: string | number | null | undefined): string {
  const parsed = optionalSenderId(value);
  return parsed == null ? "" : String(parsed);
}

export function senderOptionLabel(sender: {
  name: string;
  from_email: string;
  sends_as?: string | null;
  sender_type?: string;
  provider?: string;
}): string {
  const gmail =
    sender.sender_type === "gmail" ||
    sender.provider === "gmail" ||
    sender.provider === "gmail_api" ||
    sender.provider === "imap";
  const kind = gmail ? "Gmail" : "Brevo";
  const sendsAs = (sender.sends_as || "").trim();
  const via =
    sendsAs && sendsAs.toLowerCase() !== sender.from_email.toLowerCase()
      ? `${sender.from_email} · sends as ${sendsAs}`
      : sender.from_email;
  return `${sender.name} (${kind}) — ${via}`;
}
