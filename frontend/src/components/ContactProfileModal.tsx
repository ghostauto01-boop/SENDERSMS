import { useEffect, useState } from "react";
import api from "../api/client";
import toast from "react-hot-toast";
import { Braces, Copy, Loader2, Phone, X } from "lucide-react";
import type { ContactProfile } from "../types";

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

          {error && <p className="text-center text-red-600 py-6">{error}</p>}

          {profile && (
            <>
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
