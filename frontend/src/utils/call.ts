/** Phone / WhatsApp / website helpers shared by Contacts, Inbox and Phone. */
import api from "../api/client";
import toast from "react-hot-toast";

/** Digits only, for wa.me links (no +, spaces or dashes). */
export function waDigits(phone: string): string {
  const d = (phone || "").replace(/\D/g, "");
  // wa.me needs the full international number without "+".
  if (d.startsWith("0")) return "234" + d.slice(1);
  return d;
}

/** Direct WhatsApp chat deep link. `text` pre-fills the message box. */
export function whatsappChatUrl(phone: string, text?: string): string {
  const base = `https://wa.me/${waDigits(phone)}`;
  return text ? `${base}?text=${encodeURIComponent(text)}` : base;
}

/**
 * Open WhatsApp and land the user one tap from a live call.
 *
 * WhatsApp exposes no public voice-call URL scheme — the chat deep link is the
 * official, reliable entry point on Android, iOS and desktop. Opening wa.me
 * drops the user straight into the contact's chat where the call button sits
 * in the header bar, so the whole flow is open → tap call.
 */
export function openWhatsappForCall(phone: string, name?: string) {
  window.open(whatsappChatUrl(phone), "_blank", "noopener");
  toast.success(
    name
      ? `WhatsApp opened for ${name} — tap the call icon (top right) to start the call`
      : "WhatsApp opened — tap the call icon (top right) to start the call",
    { duration: 4000 }
  );
}

export function openWhatsappChat(phone: string, text?: string) {
  window.open(whatsappChatUrl(phone, text), "_blank", "noopener");
}

/** Normalise a stored website value into an openable https URL. */
export function websiteUrl(raw?: string | null): string | null {
  const v = (raw || "").trim();
  if (!v) return null;
  if (/^https?:\/\//i.test(v)) return v;
  return `https://${v}`;
}

export function openWebsite(raw?: string | null) {
  const url = websiteUrl(raw);
  if (url) window.open(url, "_blank", "noopener");
}

export function telUrl(phone: string): string {
  return `tel:${(phone || "").replace(/[^+\d]/g, "")}`;
}

export interface StartCallResult {
  success: boolean;
  mode?: string;
  tel_url?: string;
  phone?: string;
  contact?: string;
  error?: string;
  note?: string;
}

/**
 * Place a call through the backend (CallGate on your phone). Falls back to a
 * direct tel: link when CallGate is unreachable, so one tap always dials.
 */
export async function startCall(opts: { contact_id?: number; phone_number?: string }): Promise<StartCallResult> {
  try {
    const { data } = await api.post("/calls/start", {
      contact_id: opts.contact_id,
      phone_number: opts.phone_number,
      allow_direct_fallback: true,
    });
    if (data.success) {
      if (data.mode === "direct" && data.tel_url) {
        window.location.href = data.tel_url;
        try {
          await api.post("/calls/log-direct", null, {
            params: { contact_id: opts.contact_id, phone_number: opts.phone_number },
          });
        } catch { /* history logging is best-effort */ }
      } else {
        toast.success(data.note || `Calling ${data.contact || data.phone} via your phone…`);
      }
    } else {
      toast.error(data.error || "Call failed");
      if (data.tel_url) {
        // Offer the direct-dial escape hatch.
        const go = window.confirm(
          `${data.error || "CallGate is unreachable."}\n\nDial ${data.phone} directly from this device instead?`
        );
        if (go) {
          window.location.href = data.tel_url;
          try {
            await api.post("/calls/log-direct", null, {
              params: { contact_id: opts.contact_id, phone_number: opts.phone_number },
            });
          } catch { /* best-effort */ }
        }
      }
    }
    return data;
  } catch (e: any) {
    const msg = e.response?.data?.detail || "Call failed";
    toast.error(msg);
    return { success: false, error: msg };
  }
}

export function displayName(c: { first_name?: string | null; last_name?: string | null; business_name?: string | null; phone_number: string }): string {
  const n = `${c.first_name || ""} ${c.last_name || ""}`.trim();
  return n || c.business_name || c.phone_number;
}
