/** Phone — browse all contacts and call them from the app (via your phone). */
import { useState, useEffect, useCallback } from "react";
import api from "../api/client";
import { Contact, PaginatedResponse } from "../types";
import toast from "react-hot-toast";
import {
  Phone, Search, PhoneCall, PhoneIncoming, PhoneOutgoing, PhoneMissed,
  ChevronLeft, ChevronRight, Clock, XCircle, CheckCircle2, Loader2, Wifi, WifiOff,
} from "lucide-react";
import ContactActions from "../components/ContactActions";
import { displayName, startCall } from "../utils/call";

const avatarColor = (name: string) => {
  const colors = ["bg-[#00a884]", "bg-[#128C7E]", "bg-[#075E54]", "bg-[#34B7F1]", "bg-[#FF8A65]", "bg-[#BA68C8]", "bg-[#4DB6AC]", "bg-[#FFB74D]"];
  let h = 0; for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % colors.length;
  return colors[h];
};
const initials = (c: Contact) => {
  if (c.first_name || c.last_name) return `${(c.first_name?.[0] || "").toUpperCase()}${(c.last_name?.[0] || "").toUpperCase()}` || c.phone_number.slice(-2);
  if (c.business_name) return c.business_name.slice(0, 2).toUpperCase();
  return c.phone_number.slice(-2);
};

const statusMeta: Record<string, { label: string; cls: string; Icon: any }> = {
  ended: { label: "Connected", cls: "text-[#00a884]", Icon: CheckCircle2 },
  started: { label: "On call", cls: "text-[#0066cc]", Icon: PhoneCall },
  ringing: { label: "Ringing", cls: "text-[#ff9f0a]", Icon: PhoneCall },
  initiated: { label: "Dialling", cls: "text-[#ff9f0a]", Icon: PhoneOutgoing },
  direct_dial: { label: "Direct dial", cls: "text-[#667781]", Icon: PhoneOutgoing },
  failed: { label: "Failed", cls: "text-[#c5221f]", Icon: XCircle },
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
        if (data.items.length === 0 && page > 1) { setPage((p) => Math.max(1, p - 1)); return; }
        setContacts(data.items); setTotal(data.total);
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
  useEffect(() => {
    if (!active) return;
    const iv = setInterval(loadActive, 5000);
    return () => clearInterval(iv);
  }, [active, loadActive]);

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
          <Phone size={22} className="text-[#00a884]" /> Phone
        </h1>
        {gw && (
          <span className={`flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-full ${gw.configured ? "bg-[#d9fdd3] text-[#075e54]" : "bg-[#fff8c4] text-[#9a6f00]"}`}>
            {gw.configured ? <><Wifi size={12} /> CallGate connected</> : <><WifiOff size={12} /> Direct dial mode</>}
          </span>
        )}
      </div>

      {gw && !gw.configured && (
        <div className="card p-3 text-sm bg-[#fff8c4]/50 border border-[#ffecb3]">
          <p className="font-medium">CallGate isn't set up yet.</p>
          <p className="text-gray-600 text-[13px] mt-0.5">
            Calls will dial directly from this device. To place calls on your shop phone from anywhere,
            install CallGate on it and connect it in <a href="/settings" className="text-[#00a884] font-semibold">Settings → Calls</a>.
          </p>
        </div>
      )}

      {active && (
        <div className="card p-3 flex items-center gap-3 bg-[#d9fdd3]/60 border border-[#00a884]/30">
          <span className="relative flex h-3 w-3">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-[#00a884] opacity-60" />
            <span className="relative inline-flex rounded-full h-3 w-3 bg-[#00a884]" />
          </span>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-semibold truncate">{active.contact_name} • {active.phone_number}</p>
            <p className="text-xs text-gray-600">{statusMeta[active.status]?.label || active.status}</p>
          </div>
          <button onClick={doEnd} disabled={ending} className="bg-[#c5221f] text-white rounded-full px-4 py-2 text-sm font-semibold disabled:opacity-50">
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
            className={`flex-1 py-2 rounded-full text-sm font-semibold ${tab === t ? "bg-[#00a884] text-white" : "bg-white dark:bg-[#202c33] text-gray-600 dark:text-gray-300 border"}`}
          >
            {t === "contacts" ? `Contacts (${total})` : `Recent (${logsTotal})`}
          </button>
        ))}
      </div>

      {tab === "contacts" ? (
        <>
          <div className="relative">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#667781]" />
            <input
              className="w-full pl-9 pr-3 py-2.5 bg-white dark:bg-[#202c33] rounded-full text-[15px] border shadow-sm focus:outline-none focus:ring-2 focus:ring-[#00a884]/20"
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
                    className="w-11 h-11 rounded-full bg-[#00a884] hover:bg-[#06cf9c] text-white flex items-center justify-center flex-shrink-0 disabled:opacity-60"
                    title={`Call ${name}`}
                  >
                    {callingId === c.id ? <Loader2 size={18} className="animate-spin" /> : <Phone size={18} />}
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
                {l.direction === "incoming" ? <PhoneIncoming size={18} className="text-[#0066cc] flex-shrink-0" /> : <PhoneOutgoing size={18} className="text-[#00a884] flex-shrink-0" />}
                <div className="flex-1 min-w-0">
                  <p className="font-semibold text-[15px] truncate">{l.contact_name}</p>
                  <p className="text-[12px] text-gray-500 flex items-center gap-1.5">
                    <meta.Icon size={12} className={meta.cls} />
                    <span className={meta.cls}>{meta.label}</span>
                    {l.duration_seconds != null && l.status === "ended" && <span>• {fmtDur(l.duration_seconds)}</span>}
                    <span>• {fmtTime(l.created_at)}</span>
                  </p>
                  {l.last_error && <p className="text-[11px] text-red-500 truncate">{l.last_error}</p>}
                </div>
                <button
                  onClick={() => startCall(l.contact_id ? { contact_id: l.contact_id } : { phone_number: l.phone_number }).then(() => loadActive())}
                  className="w-10 h-10 rounded-full bg-[#00a884]/10 hover:bg-[#00a884] hover:text-white text-[#00a884] flex items-center justify-center flex-shrink-0"
                  title={`Call back ${l.phone_number}`}
                >
                  <Phone size={16} />
                </button>
                {l.status === "failed" && <PhoneMissed size={16} className="text-red-400 flex-shrink-0" />}
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
