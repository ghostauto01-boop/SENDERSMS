/** Phone — browse all contacts and call them from the app (via your phone). */
import { useVisiblePolling } from "../hooks/useVisiblePolling";
import { useState, useEffect, useCallback } from "react";
import api from "../api/client";
import { Contact, PaginatedResponse } from "../types";
import toast from "react-hot-toast";
import {
  Phone, Search, PhoneCall, PhoneIncoming, PhoneOutgoing, PhoneMissed,
  ChevronLeft, ChevronRight, Clock, XCircle, CheckCircle2, Loader2, Wifi, WifiOff,
} from "lucide-react";
import ContactActions from "../components/ContactActions";
import { displayName, startCall, openWhatsappForCall } from "../utils/call";
import { contactInitials } from "../utils/contact";

const avatarColor = (name: string) => {
  const colors = ["bg-primary-600", "bg-primary-700", "bg-primary-800", "bg-[#34B7F1]", "bg-warning-400", "bg-accent-500", "bg-primary-400", "bg-warning-400"];
  let h = 0; for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % colors.length;
  return colors[h];
};
const initials = (c: Contact) => contactInitials(c);

const statusMeta: Record<string, { label: string; cls: string; Icon: any }> = {
  ended: { label: "Connected", cls: "text-primary-600", Icon: CheckCircle2 },
  started: { label: "On call", cls: "text-primary-600", Icon: PhoneCall },
  ringing: { label: "Ringing", cls: "text-warning-500", Icon: PhoneCall },
  initiated: { label: "Dialling", cls: "text-warning-500", Icon: PhoneOutgoing },
  direct_dial: { label: "Direct dial", cls: "text-gray-500", Icon: PhoneOutgoing },
  failed: { label: "Failed", cls: "text-danger-700", Icon: XCircle },
};

function fmtDur(s?: number | null) {
  if (s === null || s === undefined) return "";
  const m = Math.floor(s / 60), sec = s % 60;
  return m > 0 ? `${m}m ${sec}s` : `${sec}s`;
}
function fmtTime(iso?: string | null) {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    const now = new Date();
    const sameDay = d.toDateString() === now.toDateString();
    const y = new Date(now); y.setDate(now.getDate() - 1);
    if (sameDay) return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    if (d.toDateString() === y.toDateString()) return "Yesterday";
    return d.toLocaleDateString([], { day: "2-digit", month: "short" });
  } catch { return ""; }
}

export default function PhonePage() {
  const [tab, setTab] = useState<"contacts" | "recent">("contacts");
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [debounced, setDebounced] = useState("");
  const [loading, setLoading] = useState(true);
  const [logs, setLogs] = useState<any[]>([]);
  const [logsTotal, setLogsTotal] = useState(0);
  const [logsPage, setLogsPage] = useState(1);
  const [logsLoading, setLogsLoading] = useState(false);
  const [gw, setGw] = useState<any>(null);
  const [callingId, setCallingId] = useState<number | null>(null);
  const [dialNumber, setDialNumber] = useState("");
  const [active, setActive] = useState<any>(null);
  const [ending, setEnding] = useState(false);

  // Debounce search so typing doesn't hammer the API.
  useEffect(() => {
    const t = setTimeout(() => { setDebounced(search); setPage(1); }, 350);
    return () => clearTimeout(t);
  }, [search]);

  const loadGw = useCallback(async () => {
    try {
      const { data } = await api.get("/calls/config");
      setGw(data);
    } catch { /* offline */ }
  }, []);
  const loadActive = useCallback(async () => {
    try {
      const { data } = await api.get("/calls/active");
      setActive(data.active ? data : null);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { loadGw(); loadActive(); }, [loadGw, loadActive]);

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        setLoading(true);
        const { data } = await api.get<PaginatedResponse<Contact>>("/contacts/", {
          params: { page, per_page: 25, search: debounced || undefined },
        });
        if (!live) return;
        // The dialer only makes sense for numbers. Email-only contacts are
        // perfectly valid CRM records, they just have nothing to ring here, so
        // they are dropped from this list rather than shown as un-callable rows.
        const dialable = data.items.filter((c) => !!c.phone_number);
        if (dialable.length === 0 && page > 1) { setPage((p) => Math.max(1, p - 1)); return; }
        setContacts(dialable); setTotal(data.total);
      } catch { if (live) toast.error("Failed to load contacts"); }
      finally { if (live) setLoading(false); }
    })();
    return () => { live = false; };
  }, [page, debounced]);

  const loadLogs = useCallback(async (p: number) => {
    try {
      setLogsLoading(true);
      const { data } = await api.get("/calls/logs", { params: { page: p, per_page: 25 } });
      setLogs(data.items); setLogsTotal(data.total); setLogsPage(data.page);
    } catch { toast.error("Failed to load call history"); }
    finally { setLogsLoading(false); }
  }, []);

  useEffect(() => { if (tab === "recent") loadLogs(1); }, [tab, loadLogs]);

  // Refresh the active-call banner while it exists.
  useVisiblePolling(loadActive, active ? 5000 : null);

  const doCallContact = async (c: Contact) => {
    setCallingId(c.id);
    try {
      await startCall({ contact_id: c.id });
      loadActive();
      if (tab === "recent") loadLogs(1);
    } finally { setCallingId(null); }
  };

  const doDial = async () => {
    if (!dialNumber.trim()) { toast.error("Enter a number first"); return; }
    setCallingId(-1);
    try {
      await startCall({ phone_number: dialNumber.trim() });
      loadActive();
    } finally { setCallingId(null); }
  };

  const doEnd = async () => {
    setEnding(true);
    try {
      await api.post("/calls/end");
      toast.success("Call ended");
      setActive(null);
      if (tab === "recent") loadLogs(1);
    } catch (e: any) {
      toast.error(e.response?.data?.detail || "Could not end the call");
    } finally { setEnding(false); }
  };

  const totalPages = Math.max(1, Math.ceil(total / 25));
  const logPages = Math.max(1, Math.ceil(logsTotal / 25));

  return (
    <div className="space-y-4 max-w-3xl mx-auto">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <h1 className="text-xl sm:text-2xl font-bold flex items-center gap-2">
          <Phone size={22} className="text-primary-600" /> Phone
        </h1>
        {gw && (
          <span className={`flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-full ${gw.configured ? "bg-primary-100 text-primary-800" : "bg-warning-100 text-warning-700"}`}>
            {gw.configured ? <><Wifi size={12} /> CallGate connected</> : <><WifiOff size={12} /> Direct dial mode</>}
          </span>
        )}
      </div>

      {gw && !gw.configured && (
        <div className="card p-3 text-sm bg-warning-100/50 border border-warning-200">
          <p className="font-medium">CallGate isn't set up yet.</p>
          <p className="text-gray-600 text-[13px] mt-0.5">
            Calls will dial directly from this device. To place calls on your shop phone from anywhere,
            install CallGate on it and connect it in <a href="/settings" className="text-primary-600 font-semibold">Settings → Calls</a>.
          </p>
        </div>
      )}

      {active && (
        <div className="card p-3 flex items-center gap-3 bg-primary-100/60 border border-primary-600/30">
          <span className="relative flex h-3 w-3">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-primary-600 opacity-60" />
            <span className="relative inline-flex rounded-full h-3 w-3 bg-primary-600" />
          </span>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-semibold truncate">{active.contact_name} • {active.phone_number}</p>
            <p className="text-xs text-gray-600">{statusMeta[active.status]?.label || active.status}</p>
          </div>
          <button onClick={doEnd} disabled={ending} className="bg-danger-700 text-white rounded-full px-4 py-2 text-sm font-semibold disabled:opacity-50">
            {ending ? "Ending…" : "End call"}
          </button>
        </div>
      )}

      {/* Dial pad */}
      <div className="card p-3 flex gap-2">
        <input
          className="input flex-1"
          placeholder="Dial a number… e.g. 08031234567"
          value={dialNumber}
          onChange={(e) => setDialNumber(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") doDial(); }}
          inputMode="tel"
        />
        <button onClick={doDial} disabled={callingId === -1} className="btn-primary whitespace-nowrap disabled:opacity-60">
          {callingId === -1 ? <Loader2 size={16} className="animate-spin" /> : <PhoneCall size={16} />}
          <span className="ml-1 hidden sm:inline">Call</span>
        </button>
      </div>

      {/* Tabs */}
      <div className="flex gap-2">
        {(["contacts", "recent"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`flex-1 py-2 rounded-full text-sm font-semibold ${tab === t ? "bg-primary-600 text-white" : "bg-white dark:bg-gray-800 text-gray-600 dark:text-gray-300 border"}`}
          >
            {t === "contacts" ? `Contacts (${total})` : `Recent (${logsTotal})`}
          </button>
        ))}
      </div>

      {tab === "contacts" ? (
        <>
          <div className="relative">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
            <input
              className="w-full pl-9 pr-3 py-2.5 bg-white dark:bg-gray-800 rounded-full text-[15px] border shadow-sm focus:outline-none focus:ring-2 focus:ring-primary-600/20"
              placeholder="Search name, business or phone…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>

          <div className="space-y-2">
            {loading ? [...Array(5)].map((_, i) => (
              <div key={i} className="card p-3 flex gap-3 animate-pulse">
                <div className="w-11 h-11 rounded-full bg-gray-200 dark:bg-gray-700" />
                <div className="flex-1 space-y-2"><div className="h-3 bg-gray-200 dark:bg-gray-700 rounded w-1/2" /><div className="h-2 bg-gray-100 dark:bg-gray-800 rounded w-3/4" /></div>
              </div>
            )) : contacts.length === 0 ? (
              <div className="card p-8 text-center text-gray-500">No contacts found.</div>
            ) : contacts.map((c) => {
              const name = displayName(c);
              return (
                <div key={c.id} className="card p-3 flex items-center gap-3">
                  <div className={`w-11 h-11 rounded-full flex items-center justify-center text-white font-semibold flex-shrink-0 ${avatarColor(name)}`}>
                    {initials(c)}
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="font-semibold text-[15px] truncate">{name}</p>
                    <p className="text-[13px] text-gray-500 truncate">{c.phone_number}{c.business_name && name !== c.business_name ? ` • ${c.business_name}` : ""}</p>
                  </div>
                  <button
                    onClick={() => doCallContact(c)}
                    disabled={callingId === c.id}
                    className="w-11 h-11 rounded-full bg-primary-600 hover:bg-primary-500 text-white flex items-center justify-center flex-shrink-0 disabled:opacity-60"
                    title={`Call ${name} via your phone (SIM)`}
                  >
                    {callingId === c.id ? <Loader2 size={18} className="animate-spin" /> : <Phone size={18} />}
                  </button>
                  <button
                    onClick={() => openWhatsappForCall(c.phone_number as string, name)}
                    className="w-11 h-11 rounded-full bg-primary-600 hover:bg-primary-700 text-white flex items-center justify-center flex-shrink-0"
                    title={`Call ${name} on WhatsApp`}
                  >
                    <PhoneCall size={18} />
                  </button>
                  <div className="hidden sm:block">
                    <ContactActions contactId={c.id} phone={c.phone_number} name={name} website={c.website} />
                  </div>
                </div>
              );
            })}
          </div>

          {/* Mobile per-row actions are inside an expandable second row */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between card px-3 py-2">
              <span className="text-[12px] text-gray-500">{total} contacts • page {page}/{totalPages}</span>
              <div className="flex gap-2">
                <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page === 1} className="w-9 h-9 rounded-full bg-gray-100 dark:bg-gray-700 flex items-center justify-center disabled:opacity-40"><ChevronLeft size={16} /></button>
                <button onClick={() => setPage((p) => Math.min(totalPages, p + 1))} disabled={page === totalPages} className="w-9 h-9 rounded-full bg-gray-100 dark:bg-gray-700 flex items-center justify-center disabled:opacity-40"><ChevronRight size={16} /></button>
              </div>
            </div>
          )}
        </>
      ) : (
        <div className="space-y-2">
          {logsLoading ? [...Array(5)].map((_, i) => (
            <div key={i} className="card p-3 flex gap-3 animate-pulse"><div className="w-10 h-10 rounded-full bg-gray-200" /><div className="flex-1 h-4 bg-gray-100 rounded" /></div>
          )) : logs.length === 0 ? (
            <div className="card p-8 text-center text-gray-500">
              <Clock size={28} className="mx-auto mb-2 text-gray-300" />
              No calls yet. Calls you place from here, Contacts or Inbox appear in this history.
            </div>
          ) : logs.map((l) => {
            const meta = statusMeta[l.status] || { label: l.status, cls: "text-gray-500", Icon: Phone };
            return (
              <div key={l.id} className="card p-3 flex items-center gap-3">
                {l.direction === "incoming" ? <PhoneIncoming size={18} className="text-primary-600 flex-shrink-0" /> : <PhoneOutgoing size={18} className="text-primary-600 flex-shrink-0" />}
                <div className="flex-1 min-w-0">
                  <p className="font-semibold text-[15px] truncate">{l.contact_name}</p>
                  <p className="text-[12px] text-gray-500 flex items-center gap-1.5">
                    <meta.Icon size={12} className={meta.cls} />
                    <span className={meta.cls}>{meta.label}</span>
                    {l.duration_seconds != null && l.status === "ended" && <span>• {fmtDur(l.duration_seconds)}</span>}
                    <span>• {fmtTime(l.created_at)}</span>
                  </p>
                  {l.last_error && <p className="text-[11px] text-danger-500 truncate">{l.last_error}</p>}
                </div>
                <button
                  onClick={() => startCall(l.contact_id ? { contact_id: l.contact_id } : { phone_number: l.phone_number }).then(() => loadActive())}
                  className="w-10 h-10 rounded-full bg-primary-600/10 hover:bg-primary-600 hover:text-white text-primary-600 flex items-center justify-center flex-shrink-0"
                  title={`Call back ${l.phone_number}`}
                >
                  <Phone size={16} />
                </button>
                {l.status === "failed" && <PhoneMissed size={16} className="text-danger-400 flex-shrink-0" />}
              </div>
            );
          })}
          {logPages > 1 && (
            <div className="flex items-center justify-between card px-3 py-2">
              <span className="text-[12px] text-gray-500">page {logsPage}/{logPages}</span>
              <div className="flex gap-2">
                <button onClick={() => loadLogs(Math.max(1, logsPage - 1))} disabled={logsPage === 1} className="w-9 h-9 rounded-full bg-gray-100 flex items-center justify-center disabled:opacity-40"><ChevronLeft size={16} /></button>
                <button onClick={() => loadLogs(Math.min(logPages, logsPage + 1))} disabled={logsPage === logPages} className="w-9 h-9 rounded-full bg-gray-100 flex items-center justify-center disabled:opacity-40"><ChevronRight size={16} /></button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
