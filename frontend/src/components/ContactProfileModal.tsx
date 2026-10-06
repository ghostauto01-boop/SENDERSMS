import { useEffect, useState } from "react";
import api from "../api/client";
import toast from "react-hot-toast";
import { Braces, Copy, Loader2, Mail, MousePointerClick, Phone, X } from "lucide-react";
import emailApi from "../api/email";
import type { ContactProfile } from "../types";
import ContactActions from "./ContactActions";

/**
 * The full record for one contact: every standard column plus every column the
 * CSV brought in, each labelled with the short code that reads it.
 *
 * The point is that nothing imported is invisible — if a pain point, website
 * or niche came in on the spreadsheet, it is here, and the operator can see
 * exactly which short code will pull it into a message.
 */
export default function ContactProfileModal({
  contactId,
  onClose,
}: {
  contactId: number;
  onClose: () => void;
}) {
  const [profile, setProfile] = useState<ContactProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  //: Email side of this contact — opens, clicks, the links clicked. Loaded in
  //: parallel and shown only when the contact has email history.
  const [engagement, setEngagement] = useState<any>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        setLoading(true);
        const { data } = await api.get(`/variables/contact/${contactId}/profile`);
        if (!cancelled) setProfile(data);
      } catch (err: any) {
        if (!cancelled) setError(err.response?.data?.detail || "Could not load this contact");
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    emailApi
      .engagement(contactId)
      .then((data) => {
        if (!cancelled) setEngagement(data);
      })
      .catch(() => {
        if (!cancelled) setEngagement(null);
      });
    return () => {
      cancelled = true;
    };
  }, [contactId]);

  const copy = (shortcode: string) => {
    navigator.clipboard?.writeText(`{{${shortcode}}}`);
    toast.success(`Copied {{${shortcode}}}`);
  };

  const filled = profile?.fields.filter((field) => field.value) || [];
  const emptyFields = profile?.fields.filter((field) => !field.value) || [];

  return (
    <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4">
      <div className="bg-white dark:bg-gray-800 rounded-2xl w-full max-w-2xl max-h-[85vh] overflow-y-auto">
        <div className="sticky top-0 bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700 px-5 py-4 flex items-start justify-between gap-3">
          <div>
            <h2 className="font-semibold text-lg">
              {loading ? "Loading…" : profile?.display_name || "Contact"}
            </h2>
            {profile && (
              <p className="text-sm text-gray-500 flex items-center gap-1.5 mt-0.5">
                <Phone size={13} /> {profile.phone_number}
                <span className="badge badge-gray ml-1">{profile.lead_status}</span>
                {profile.is_opted_out && <span className="badge badge-red">opted out</span>}
              </p>
            )}
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600" aria-label="Close">
            <X size={20} />
          </button>
        </div>

        <div className="p-5 space-y-4">
          {loading && (
            <div className="py-10 text-center text-gray-500">
              <Loader2 size={28} className="mx-auto animate-spin opacity-50" />
            </div>
          )}

          {error && <p className="text-center text-danger-600 py-6">{error}</p>}

          {profile && (
            <>
              {/* Reach them instantly: SIM call, WhatsApp call, WhatsApp chat, website */}
              <ContactActions
                layout="bar"
                contactId={contactId}
                phone={profile.phone_number}
                name={profile.display_name}
                website={(profile.fields.find((f) => f.shortcode === "website") || {}).value as string | null | undefined}
              />
              {engagement && (engagement.totals?.sent > 0 || engagement.email) && (
                <div className="rounded-lg border border-gray-200 dark:border-gray-700 p-3 space-y-2">
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-sm font-medium flex items-center gap-1.5">
                      <Mail size={14} /> Email
                    </p>
                    <div className="flex items-center gap-1">
                      {engagement.is_email_opted_out && (
                        <span className="badge badge-amber">unsubscribed</span>
                      )}
                      {engagement.is_email_undeliverable && (
                        <span className="badge badge-red">bounced</span>
                      )}
                      {!engagement.is_email_opted_out && !engagement.is_email_undeliverable && engagement.email && (
                        <span className="badge badge-gray">{engagement.email_status || "ok"}</span>
                      )}
                    </div>
                  </div>
                  {engagement.email && (
                    <p className="text-xs text-gray-500 truncate">{engagement.email}</p>
                  )}
                  <div className="grid grid-cols-4 gap-2 text-center text-sm">
                    <div>
                      <p className="font-bold">{engagement.totals.sent}</p>
                      <p className="text-[11px] text-gray-500">sent</p>
                    </div>
                    <div>
                      <p className="font-bold">{engagement.totals.opened}</p>
                      <p className="text-[11px] text-gray-500">opened ({engagement.totals.open_rate}%)</p>
                    </div>
                    <div>
                      <p className="font-bold">{engagement.totals.clicked}</p>
                      <p className="text-[11px] text-gray-500">clicked ({engagement.totals.click_rate}%)</p>
                    </div>
                    <div>
                      <p className="font-bold">{engagement.totals.bounced}</p>
                      <p className="text-[11px] text-gray-500">bounced</p>
                    </div>
                  </div>
                  {(engagement.links || []).length > 0 && (
                    <div className="pt-1 border-t border-gray-100 dark:border-gray-800 space-y-1">
                      <p className="text-[11px] text-gray-500 flex items-center gap-1">
                        <MousePointerClick size={11} /> Links they clicked
                      </p>
                      {engagement.links.slice(0, 5).map((l: any) => (
                        <p key={l.url} className="text-[11px] truncate">
                          <a href={l.url} target="_blank" rel="noreferrer" className="underline">
                            {l.url}
                          </a>{" "}
                          <span className="text-gray-400">×{l.clicks}</span>
                        </p>
                      ))}
                    </div>
                  )}
                  {(engagement.messages || []).length > 0 && (
                    <div className="pt-1 border-t border-gray-100 dark:border-gray-800 space-y-0.5">
                      {engagement.messages.slice(0, 4).map((m: any) => (
                        <p key={m.id} className="text-[11px] text-gray-500 truncate">
                          {m.direction === "incoming" ? "↙" : "↗"} {m.subject || "(no subject)"}
                          {" · "}
                          {m.open_count} open{m.open_count === 1 ? "" : "s"}
                          {" · "}
                          {m.click_count} click{m.click_count === 1 ? "" : "s"}
                        </p>
                      ))}
                    </div>
                  )}
                </div>
              )}

              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-center text-sm">
                <div className="p-2 rounded-lg bg-gray-50 dark:bg-gray-700">
                  <p className="text-lg font-bold">{profile.messages_sent}</p>
                  <p className="text-xs text-gray-500">sent</p>
                </div>
                <div className="p-2 rounded-lg bg-gray-50 dark:bg-gray-700">
                  <p className="text-lg font-bold">{profile.messages_received}</p>
                  <p className="text-xs text-gray-500">received</p>
                </div>
                <div className="p-2 rounded-lg bg-gray-50 dark:bg-gray-700 col-span-2">
                  <p className="text-xs text-gray-500">Last contacted</p>
                  <p className="text-sm">
                    {profile.last_contacted_at
                      ? new Date(profile.last_contacted_at).toLocaleString()
                      : "—"}
                  </p>
                </div>
              </div>

              {profile.tags.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {profile.tags.map((tag) => (
                    <span key={tag} className="badge badge-green">{tag}</span>
                  ))}
                </div>
              )}

              <div>
                <h3 className="text-xs font-medium text-gray-500 uppercase mb-2 flex items-center gap-1.5">
                  <Braces size={13} /> Imported information ({filled.length} fields)
                </h3>
                <div className="divide-y divide-gray-200 dark:divide-gray-700 border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden">
                  {filled.map((field) => (
                    <div
                      key={field.field_key}
                      className="flex items-start justify-between gap-3 px-3 py-2 hover:bg-gray-50 dark:hover:bg-gray-700/50"
                    >
                      <div className="min-w-0">
                        <p className="text-xs text-gray-500">{field.label}</p>
                        <p className="text-sm break-words">{field.value}</p>
                      </div>
                      <button
                        onClick={() => copy(field.shortcode)}
                        className="shrink-0 inline-flex items-center gap-1 px-2 py-1 rounded bg-gray-100 dark:bg-gray-800 font-mono text-[11px] hover:bg-gray-200 dark:hover:bg-gray-700"
                        title="Copy short code"
                      >
                        {`{{${field.shortcode}}}`}
                        <Copy size={10} className="opacity-50" />
                      </button>
                    </div>
                  ))}
                </div>
              </div>

              {profile.notes && (
                <div>
                  <h3 className="text-xs font-medium text-gray-500 uppercase mb-1">Notes</h3>
                  <p className="text-sm whitespace-pre-wrap">{profile.notes}</p>
                </div>
              )}

              {emptyFields.length > 0 && (
                <details className="text-sm">
                  <summary className="cursor-pointer text-gray-500 text-xs">
                    {emptyFields.length} field{emptyFields.length === 1 ? "" : "s"} with no value —
                    short codes for these are removed from messages
                  </summary>
                  <div className="flex flex-wrap gap-1.5 mt-2">
                    {emptyFields.map((field) => (
                      <span
                        key={field.field_key}
                        className="px-2 py-0.5 rounded bg-gray-100 dark:bg-gray-700 font-mono text-[11px] text-gray-500"
                      >
                        {`{{${field.shortcode}}}`}
                      </span>
                    ))}
                  </div>
                </details>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
