import { useEffect, useMemo, useState } from "react";
import api from "../api/client";
import { Contact, FollowUp, PaginatedResponse } from "../types";
import toast from "react-hot-toast";
import {
  AlertTriangle,
  Calendar,
  ChevronLeft,
  ChevronRight,
  Clock,
  Plus,
  Search,
  Send,
  SkipForward,
  X,
} from "lucide-react";

const defaultFollowupTime = () => {
  const date = new Date(Date.now() + 60 * 60 * 1000);
  date.setSeconds(0, 0);
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
};

const contactName = (contact: Contact) =>
  [contact.first_name, contact.last_name].filter(Boolean).join(" ") ||
  contact.business_name ||
  contact.phone_number;

async function fetchAllContacts(): Promise<Contact[]> {
  const first = await api.get<PaginatedResponse<Contact>>("/contacts/", {
    params: { page: 1, per_page: 100 },
  });
  const pages = Math.ceil(first.data.total / 100);
  if (pages <= 1) return first.data.items;
  const rest = await Promise.all(
    Array.from({ length: pages - 1 }, (_, index) =>
      api.get<PaginatedResponse<Contact>>("/contacts/", {
        params: { page: index + 2, per_page: 100 },
      })
    )
  );
  return [first.data.items, ...rest.map((response) => response.data.items)].flat();
}

export default function FollowUpsPage() {
  const [followups, setFollowups] = useState<FollowUp[]>([]);
  const [total, setTotal] = useState(0);
  const [view, setView] = useState("all");
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [contactsLoading, setContactsLoading] = useState(false);
  const [contactSearch, setContactSearch] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [form, setForm] = useState({
    contact_id: "",
    scheduled_at: defaultFollowupTime(),
    message_text: "",
  });

  useEffect(() => {
    loadFollowups();
  }, [view, page]);

  const loadFollowups = async () => {
    try {
      setLoading(true);
      setError(null);
      const { data } = await api.get("/followups/", {
        params: { view, page, per_page: 25 },
      });
      setFollowups(data.items);
      setTotal(data.total);
    } catch (err: any) {
      setError(err.response?.data?.detail || "Failed to load follow-ups");
    } finally {
      setLoading(false);
    }
  };

  const openCreate = async () => {
    setShowCreate(true);
    if (contacts.length > 0) return;
    try {
      setContactsLoading(true);
      setContacts(await fetchAllContacts());
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Failed to load contacts");
    } finally {
      setContactsLoading(false);
    }
  };

  const closeCreate = () => {
    if (submitting) return;
    setShowCreate(false);
    setContactSearch("");
  };

  const handleCreate = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!form.contact_id) {
      toast.error("Select a contact");
      return;
    }
    if (!form.message_text.trim()) {
      toast.error("Type a follow-up message");
      return;
    }

    const localTime = new Date(form.scheduled_at);
    if (Number.isNaN(localTime.getTime()) || localTime.getTime() <= Date.now()) {
      toast.error("Choose a future date and time");
      return;
    }

    try {
      setSubmitting(true);
      await api.post("/followups/", {
        contact_id: Number(form.contact_id),
        scheduled_at: localTime.toISOString(),
        message_text: form.message_text.trim(),
      });
      toast.success("Follow-up created");
      setShowCreate(false);
      setContactSearch("");
      setForm({
        contact_id: "",
        scheduled_at: defaultFollowupTime(),
        message_text: "",
      });
      if (view !== "all") {
        setView("all");
        setPage(1);
      } else {
        await loadFollowups();
      }
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Failed to create follow-up");
    } finally {
      setSubmitting(false);
    }
  };

  const handleSendNow = async (id: number) => {
    try {
      await api.post(`/followups/${id}/send-now`);
      toast.success("Follow-up queued");
      loadFollowups();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Failed to send follow-up");
    }
  };

  const handleSkip = async (id: number) => {
    if (!window.confirm("Skip this follow-up?")) return;
    try {
      await api.post(`/followups/${id}/skip`);
      toast.success("Follow-up skipped");
      loadFollowups();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Failed to skip follow-up");
    }
  };

  const views = [
    { value: "all", label: "All", icon: Clock },
    { value: "due_today", label: "Due Today", icon: Clock },
    { value: "overdue", label: "Overdue", icon: AlertTriangle },
    { value: "upcoming", label: "Upcoming", icon: Calendar },
    { value: "completed", label: "Completed", icon: Send },
    { value: "skipped", label: "Skipped", icon: SkipForward },
  ];

  const filteredContacts = useMemo(() => {
    const needle = contactSearch.trim().toLowerCase();
    if (!needle) return contacts;
    return contacts.filter((contact) =>
      [
        contact.first_name,
        contact.last_name,
        contact.business_name,
        contact.phone_number,
      ]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(needle))
    );
  }, [contacts, contactSearch]);

  const totalPages = Math.max(1, Math.ceil(total / 25));

  if (error) {
    return (
      <div className="text-center py-12">
        <h2 className="text-xl font-semibold mb-2">Error</h2>
        <p className="text-gray-500 mb-4">{error}</p>
        <button onClick={loadFollowups} className="btn-primary">Retry</button>
      </div>
    );
  }

  return (
    <div className="space-y-4 pb-20 lg:pb-0">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold">Follow-ups</h1>
          <p className="text-sm text-gray-500 mt-0.5">Schedule a personal SMS and track what is due.</p>
        </div>
        <button onClick={openCreate} className="btn-primary whitespace-nowrap">
          <Plus size={16} className="mr-1.5" /> Create Follow-up
        </button>
      </div>

      <div className="flex gap-2 flex-wrap">
        {views.map((item) => (
          <button
            key={item.value}
            onClick={() => { setView(item.value); setPage(1); }}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
              view === item.value
                ? "bg-primary-100 dark:bg-primary-900/30 text-primary-700 dark:text-primary-300"
                : "text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700"
            }`}
          >
            <item.icon size={14} />
            {item.label}
          </button>
        ))}
      </div>

      <div className="card overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[820px]">
            <thead className="bg-gray-50 dark:bg-gray-700/50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Contact</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Message</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Scheduled</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
              {loading ? (
                [...Array(5)].map((_, index) => (
                  <tr key={index}>
                    {[...Array(5)].map((__, cell) => (
                      <td key={cell} className="px-4 py-3"><div className="skeleton h-4 w-full" /></td>
                    ))}
                  </tr>
                ))
              ) : followups.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-4 py-12 text-center text-gray-500">
                    <Clock size={36} className="mx-auto mb-2 opacity-30" />
                    <p>No follow-ups in this view</p>
                    {view === "all" && (
                      <button onClick={openCreate} className="btn-primary btn-sm mt-3">
                        <Plus size={14} className="mr-1" /> Create your first follow-up
                      </button>
                    )}
                  </td>
                </tr>
              ) : (
                followups.map((followup) => (
                  <tr key={followup.id} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
                    <td className="px-4 py-3">
                      <p className="font-medium text-sm">{followup.contact_name}</p>
                      {followup.contact_phone && <p className="text-xs text-gray-400 mt-0.5">{followup.contact_phone}</p>}
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-600 dark:text-gray-300 max-w-xs">
                      <p className="line-clamp-2">{followup.message_text || "Sequence follow-up"}</p>
                      {followup.last_error && <p className="text-xs text-red-600 mt-1">{followup.last_error}</p>}
                    </td>
                    <td className="px-4 py-3 text-sm whitespace-nowrap">
                      {followup.scheduled_at ? new Date(followup.scheduled_at).toLocaleString() : "—"}
                    </td>
                    <td className="px-4 py-3">
                      <span className={`badge ${
                        followup.status === "sent" || followup.status === "delivered" ? "badge-green" :
                        followup.status === "failed" ? "badge-red" :
                        followup.status === "skipped" || followup.status === "cancelled" ? "badge-gray" :
                        "badge-yellow"
                      }`}>{followup.status}</span>
                    </td>
                    <td className="px-4 py-3">
                      {followup.status === "pending" && (
                        <div className="flex gap-1">
                          <button onClick={() => handleSendNow(followup.id)} className="btn-primary btn-sm" title="Send now">
                            <Send size={14} />
                          </button>
                          <button onClick={() => handleSkip(followup.id)} className="btn-secondary btn-sm" title="Skip">
                            <SkipForward size={14} />
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
        {total > 25 && (
          <div className="px-4 py-3 border-t dark:border-gray-700 flex items-center justify-between text-sm">
            <span className="text-gray-500">Page {page} of {totalPages} · {total} follow-ups</span>
            <div className="flex gap-1">
              <button className="btn-secondary btn-sm" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}><ChevronLeft size={15} /></button>
              <button className="btn-secondary btn-sm" disabled={page >= totalPages} onClick={() => setPage((value) => value + 1)}><ChevronRight size={15} /></button>
            </div>
          </div>
        )}
      </div>

      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/50 p-0 sm:p-4">
          <div className="bg-white dark:bg-[#202c33] w-full sm:max-w-lg max-h-[92vh] overflow-y-auto rounded-t-2xl sm:rounded-2xl">
            <div className="sticky top-0 bg-[#008069] dark:bg-[#202c33] px-4 py-3 flex items-center justify-between">
              <div>
                <h2 className="text-white font-semibold">Create Follow-up</h2>
                <p className="text-white/70 text-xs">The SMS sends automatically at the selected time.</p>
              </div>
              <button onClick={closeCreate} className="w-8 h-8 rounded-full bg-white/10 flex items-center justify-center text-white"><X size={16} /></button>
            </div>

            <form onSubmit={handleCreate} className="p-4 sm:p-6 space-y-4">
              <div>
                <label className="text-xs font-medium text-[#54656f]">Contact</label>
                <div className="relative mt-1">
                  <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#667781]" />
                  <input
                    className="w-full pl-9 pr-3 py-2.5 bg-[#f0f2f5] dark:bg-[#111b21] rounded-xl text-sm"
                    placeholder="Filter by name, business or phone"
                    value={contactSearch}
                    onChange={(event) => setContactSearch(event.target.value)}
                  />
                </div>
                <select
                  className="w-full mt-2 px-3 py-2.5 bg-[#f0f2f5] dark:bg-[#111b21] rounded-xl text-sm"
                  value={form.contact_id}
                  onChange={(event) => setForm({ ...form, contact_id: event.target.value })}
                  disabled={contactsLoading}
                >
                  <option value="">{contactsLoading ? "Loading contacts…" : "Select contact…"}</option>
                  {filteredContacts.map((contact) => (
                    <option key={contact.id} value={contact.id}>
                      {contactName(contact)} — {contact.phone_number}
                    </option>
                  ))}
                </select>
                {!contactsLoading && contacts.length === 0 && (
                  <p className="text-xs text-amber-600 mt-1">Create a contact first, then return here.</p>
                )}
                {!contactsLoading && contactSearch && filteredContacts.length === 0 && contacts.length > 0 && (
                  <p className="text-xs text-gray-500 mt-1">No contacts match this filter.</p>
                )}
              </div>

              <div>
                <label className="text-xs font-medium text-[#54656f]">Send date and time</label>
                <input
                  type="datetime-local"
                  min={defaultFollowupTime()}
                  className="w-full mt-1 px-3 py-2.5 bg-[#f0f2f5] dark:bg-[#111b21] rounded-xl text-sm"
                  value={form.scheduled_at}
                  onChange={(event) => setForm({ ...form, scheduled_at: event.target.value })}
                  required
                />
                <p className="text-[11px] text-[#667781] mt-1">Uses your device's local time.</p>
              </div>

              <div>
                <div className="flex items-center justify-between">
                  <label className="text-xs font-medium text-[#54656f]">Message</label>
                  <span className="text-[11px] text-[#667781]">{form.message_text.length} characters</span>
                </div>
                <textarea
                  className="w-full mt-1 px-3 py-2.5 bg-[#f0f2f5] dark:bg-[#111b21] rounded-xl text-sm min-h-28 resize-y"
                  placeholder="Hi {{first_name}}, just following up…"
                  value={form.message_text}
                  onChange={(event) => setForm({ ...form, message_text: event.target.value })}
                  maxLength={5000}
                  required
                />
                <p className="text-[11px] text-[#667781] mt-1">Contact placeholders such as {"{{first_name}}"} are filled when sent.</p>
              </div>

              <div className="flex gap-2 pt-1">
                <button type="button" onClick={closeCreate} className="flex-1 py-3 rounded-full bg-[#f0f2f5] dark:bg-[#111b21] text-[#54656f] dark:text-white font-medium">Cancel</button>
                <button type="submit" disabled={submitting || contactsLoading || contacts.length === 0} className="flex-1 py-3 rounded-full bg-[#00a884] text-white font-semibold disabled:opacity-50">
                  {submitting ? "Creating…" : "Create Follow-up"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
