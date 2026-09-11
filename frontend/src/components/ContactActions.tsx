/** Reusable per-contact action buttons: Call, SMS, WhatsApp, Website. */
import { useState } from "react";
import { Phone, MessageSquare, MessageCircle, Globe, Loader2 } from "lucide-react";
import { startCall, openWhatsappChat, openWhatsappForCall, openWebsite, websiteUrl } from "../utils/call";

interface Props {
  contactId?: number;
  phone: string;
  name?: string;
  website?: string | null;
  /** show SMS button (needs an onSms handler from the parent) */
  onSms?: () => void;
  size?: "sm" | "md";
  layout?: "row" | "bar";
}

export default function ContactActions({ contactId, phone, name, website, onSms, size = "sm", layout = "row" }: Props) {
  const [calling, setCalling] = useState(false);
  const hasSite = !!websiteUrl(website);
  const btn = size === "sm" ? "w-8 h-8" : "w-10 h-10";
  const icon = size === "sm" ? 14 : 17;

  const doCall = async () => {
    setCalling(true);
    try {
      await startCall(contactId ? { contact_id: contactId } : { phone_number: phone });
    } finally {
      setCalling(false);
    }
  };

  if (layout === "bar") {
    return (
      <div className="flex gap-2">
        <button
          onClick={doCall}
          disabled={calling}
          className="flex-1 bg-[#00a884] hover:bg-[#06cf9c] text-white rounded-full py-2.5 text-[13px] font-semibold flex items-center justify-center gap-1.5 active:scale-[0.98] transition-transform disabled:opacity-60"
        >
          {calling ? <Loader2 size={14} className="animate-spin" /> : <Phone size={14} />} Call
        </button>
        {onSms && (
          <button
            onClick={onSms}
            className="flex-1 bg-[#008069] hover:bg-[#00a884] text-white rounded-full py-2.5 text-[13px] font-semibold flex items-center justify-center gap-1.5 active:scale-[0.98] transition-transform"
          >
            <MessageSquare size={14} /> SMS
          </button>
        )}
        <button
          onClick={() => openWhatsappChat(phone)}
          title="Open WhatsApp chat"
          className="w-[52px] bg-[#25D366]/15 hover:bg-[#25D366] hover:text-white text-[#128C7E] rounded-full py-2.5 flex items-center justify-center active:scale-[0.98] transition-transform"
        >
          <MessageCircle size={16} />
        </button>
        <button
          onClick={() => openWhatsappForCall(phone, name)}
          title="WhatsApp call (opens WhatsApp)"
          className="w-[52px] bg-[#25D366]/15 hover:bg-[#25D366] hover:text-white text-[#128C7E] rounded-full py-2.5 flex items-center justify-center active:scale-[0.98] transition-transform"
        >
          <Phone size={16} />
        </button>
        {hasSite && (
          <button
            onClick={() => openWebsite(website)}
            title="Open website"
            className="w-[52px] bg-[#e7f3ff] hover:bg-[#0066cc] hover:text-white text-[#0066cc] rounded-full py-2.5 flex items-center justify-center active:scale-[0.98] transition-transform"
          >
            <Globe size={16} />
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="flex gap-1">
      <button
        onClick={doCall}
        disabled={calling}
        className={`${btn} rounded-full bg-[#00a884]/10 hover:bg-[#00a884] hover:text-white text-[#00a884] flex items-center justify-center disabled:opacity-50`}
        title={calling ? "Calling…" : `Call ${phone} (via your phone)`}
      >
        {calling ? <Loader2 size={icon} className="animate-spin" /> : <Phone size={icon} />}
      </button>
      {onSms && (
        <button
          onClick={onSms}
          className={`${btn} rounded-full bg-[#008069]/10 hover:bg-[#008069] hover:text-white text-[#008069] flex items-center justify-center`}
          title="Send SMS"
        >
          <MessageSquare size={icon} />
        </button>
      )}
      <button
        onClick={() => openWhatsappChat(phone)}
        className={`${btn} rounded-full bg-[#25D366]/10 hover:bg-[#25D366] hover:text-white text-[#128C7E] flex items-center justify-center`}
        title="WhatsApp chat"
      >
        <MessageCircle size={icon} />
      </button>
      <button
        onClick={() => openWhatsappForCall(phone, name)}
        className={`${btn} rounded-full bg-[#25D366]/10 hover:bg-[#25D366] hover:text-white text-[#128C7E] flex items-center justify-center`}
        title="WhatsApp call (opens WhatsApp)"
      >
        <Phone size={icon} />
      </button>
      {hasSite && (
        <button
          onClick={() => openWebsite(website)}
          className={`${btn} rounded-full bg-[#e7f3ff] hover:bg-[#0066cc] hover:text-white text-[#0066cc] flex items-center justify-center`}
          title="Open website"
        >
          <Globe size={icon} />
        </button>
      )}
    </div>
  );
}
