import { useState, useEffect, useRef, useMemo, useCallback } from "react";
import { Link, useNavigate } from "react-router-dom";
import api from "../api/client";
import toast from "react-hot-toast";
import { useAuth } from "../hooks/useAuth";
import {
  Send, Star, ThumbsDown, Archive, ChevronLeft, CheckCheck, MessageCircle,
  Bug, Search as SearchIcon, MoreVertical, Phone, Video, Paperclip, Smile,
  Mic, LogOut, Settings as SettingsIcon, Users, Megaphone, Home,
} from "lucide-react";

/* ------------------------------------------------------------------ */
/* Helpers                                                             */
/* ------------------------------------------------------------------ */

const avatarColor = (name: string) => {
  const colors = [
    "bg-[#25D366]", "bg-[#128C7E]", "bg-[#075E54]", "bg-[#34B7F1]",
    "bg-[#FF8A65]", "bg-[#BA68C8]", "bg-[#4DB6AC]", "bg-[#FFB74D]",
  ];
  let h = 0; for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % colors.length;
  return colors[h];
};

const initials = (name: string, phone: string) => {
  if (name && name.trim()) {
    const parts = name.trim().split(/\s+/);
    if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
    return name.slice(0, 2).toUpperCase();
  }
  return (phone || "??").slice(-2);
};

const formatTime = (iso?: string) => {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch { return ""; }
};

const formatListTime = (iso?: string) => {
  if (!iso) return "";
  const d = new Date(iso);
  const now = new Date();
  const diff = (now.getTime() - d.getTime()) / 1000 / 3600;
  if (diff < 24) return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (diff < 24 * 7) return d.toLocaleDateString([], { weekday: "short" });
  return d.toLocaleDateString([], { day: "2-digit", month: "short" });
};

const dayLabel = (iso?: string) => {
  if (!iso) return "";
  const d = new Date(iso);
  const now = new Date();
  const startOf = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((startOf(now) - startOf(d)) / 86400000);
  if (days === 0) return "Today";
  if (days === 1) return "Yesterday";
  return d.toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" });
};

const isSameDay = (a?: string, b?: string) => {
  if (!a || !b) return false;
  const da = new Date(a), db = new Date(b);
  return da.getFullYear() === db.getFullYear() && da.getMonth() === db.getMonth() && da.getDate() === db.getDate();
};

/* WhatsApp's classic chat wallpaper (tiled doodles) as a data URI. */
const CHAT_BG = `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='120' height='120' viewBox='0 0 120 120'%3E%3Cg fill='%23d1d7db' fill-opacity='0.28'%3E%3Ccircle cx='22' cy='22' r='7'/%3E%3Cpath d='M70 12h8v8h-8zM92 40h10v10H92zM14 78h12v12H14zM60 60h10v10H60zM96 92h8v8h-8zM30 96h14v10H30zM8 44h14v8H8z'/%3E%3Cpath d='M84 66l4-8 4 8zm-46 26l4-8 4 8zM44 18l4-8 4 8z'/%3E%3Cpath d='M104 16l3 7 7 3-7 3-3 7-3-7-7-3 7-3z'/%3E%3Cpath d='M16 110l2 5 5 2-5 2-2 5-2-5-5-2 5-2z'/%3E%3Cpath d='M112 66l2 4 4 2-4 2-2 4-2-4-4-2 4-2z'/%3E%3Cpath d='M40 42l3 7 7 3-7 3-3 7-3-7-7-3 7-3z'/%3E%3C/g%3E%3C/svg%3E")`;

/* Group consecutive messages from the same sender (within 8 min) so bubbles
   get the real WhatsApp treatment: tail + timestamp only on the last of a
   run, tighter spacing inside a run. */
const GROUP_GAP_MS = 8 * 60 * 1000;
const sameGroup = (a: any, b: any) => {
  if (!a || !b) return false;
  if (a.direction !== b.direction) return false;
  const ta = a.created_at ? new Date(a.created_at).getTime() : 0;
  const tb = b.created_at ? new Date(b.created_at).getTime() : 0;
  return tb >= ta && tb - ta < GROUP_GAP_MS;
};

/* ------------------------------------------------------------------ */
/* Component                                                           */
/* ------------------------------------------------------------------ */

export default function InboxPage() {
  const [convs, setConvs] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<any>(null);
  const [messages, setMessages] = useState<any[]>([]);
  const [replyText, setReplyText] = useState("");
  const [sending, setSending] = useState(false);
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [showInfo, setShowInfo] = useState(false);
  const [polling, setPolling] = useState(false);
  const [pollDebug, setPollDebug] = useState<any>(null);
  const [debugData, setDebugData] = useState<any>(null);
  const [debugLoading, setDebugLoading] = useState(false);
  const [showActions, setShowActions] = useState(false);
  const [showMenu, setShowMenu] = useState(false);

  const chatEndRef = useRef<HTMLDivElement>(null);
  const pollRef = useRef<any>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  // The inbox renders outside the dashboard shell, so own the dark-mode class.
  useEffect(() => {
    const dark =
      localStorage.getItem("theme") === "dark" ||
      (!localStorage.getItem("theme") && window.matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.classList.toggle("dark", dark);
  }, []);

  const filterRef = useRef(filter);
  const searchRef = useRef(search);
  useEffect(() => { filterRef.current = filter; }, [filter]);
  useEffect(() => { searchRef.current = search; }, [search]);

  useEffect(() => { loadConvs(); }, [filter]);
  useEffect(() => { const t = setTimeout(loadConvs, 350); return () => clearTimeout(t); }, [search]);
  // Key the effects on the stable conversation id, NOT the `selected` object:
  // loadMessages() refreshes the object (to pick up status changes), and an
  // object-keyed effect would re-fire forever in a tight async loop.
  useEffect(() => { if (selected) loadMessages(selected.id); }, [selected?.id]);
  useEffect(() => {
    if (selected && nearBottomRef.current) chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, selected?.id]);

  useEffect(() => {
    pollRef.current = setInterval(() => { loadConvs(); if (selected) loadMessages(selected.id); }, 8000);
    return () => clearInterval(pollRef.current);
  }, [selected?.id]);

  // Auto-grow the composer textarea (WhatsApp style, capped height).
  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 120) + "px";
  }, [replyText]);

  // Keep the view pinned to the newest message unless the user scrolled up.
  const nearBottomRef = useRef(true);
  const onChatScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    nearBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
  };

  const loadConvs = useCallback(async () => {
    try {
      const params: any = { per_page: 500 };
      if (filterRef.current && filterRef.current !== "all") params.status = filterRef.current;
      if (searchRef.current.trim()) params.search = searchRef.current.trim();
      const { data } = await api.get("/inbox/conversations", { params });
      setConvs(data.items || []);
    } catch {} finally { setLoading(false); }
  }, []);

  const loadMessages = useCallback(async (convId: number) => {
    try {
      const { data } = await api.get(`/inbox/conversations/${convId}`);
      setMessages(data.messages || []);
      // Only replace the object when something actually changed, so effects
      // keyed on `selected?.id` stay calm.
      setSelected((s: any) => s && s.id === convId && s.status !== data.status ? { ...s, status: data.status } : s);
    } catch {}
  }, []);

  const openChat = (c: any) => {
    setSelected(c);
    setShowInfo(false);
    setPollDebug(null);
    setDebugData(null);
    nearBottomRef.current = true;
  };

  const sendReply = async () => {
    if (!replyText.trim() || !selected) return;
    setSending(true);
    try {
      const { data } = await api.post(`/inbox/conversations/${selected.id}/reply`, null, { params: { body: replyText } });
      if (data?.success === false) {
        toast.error(data.error || "Gateway rejected the message.");
      } else {
        setReplyText("");
        toast.success("Sent");
      }
      loadMessages(selected.id); loadConvs();
    }
    catch (err: any) { toast.error(err.response?.data?.detail || "Failed"); }
    finally { setSending(false); }
  };

  const markAs = async (status: string) => {
    if (!selected) return;
    try {
      const { data } = await api.post(`/inbox/conversations/${selected.id}/mark-${status}`);
      setSelected((s: any) => s ? { ...s, status: data.status ?? s.status } : s);
      toast.success(status === "interested" ? "Marked interested" : status === "not-interested" ? "Marked not interested" : status === "close" ? "Closed" : "Updated");
      loadConvs();
    } catch { toast.error("Failed"); }
  };

  const doPoll = async () => {
    setPolling(true); setPollDebug(null); setDebugData(null);
    try {
      const { data } = await api.post("/inbox/poll-now");
      setPollDebug(data);
      loadConvs();
      if (data.problems?.length) toast.error(data.problems[0], { duration: 6000 });
      else if (data.outgoing_updated > 0) toast.success(`${data.outgoing_updated} delivery status${data.outgoing_updated > 1 ? "es" : ""} updated`);
      else if (data.export_triggered) toast.success("Phone is replaying recent messages — they'll appear shortly");
      else toast("Inbox synced", { icon: "✅" });
      [3000, 9000].forEach((ms) => setTimeout(() => { loadConvs(); if (selected) loadMessages(selected.id); }, ms));
    } catch (err: any) { toast.error(err.response?.data?.detail || "Sync failed"); setPollDebug({ error: err.message }); }
    finally { setPolling(false); }
  };

  const doPollDebug = async () => {
    setDebugLoading(true); setDebugData(null);
    try {
      const { data } = await api.post("/inbox/poll-debug");
      setDebugData(data);
      if (data.issues?.length) toast.error(`${data.issues.length} problem(s) found`);
      else toast.success("Receive path healthy");
    } catch (err: any) { toast.error("Debug failed"); setDebugData({ error: err.message }); }
    finally { setDebugLoading(false); }
  };

  const handleLogout = async () => {
    setShowMenu(false);
    await logout();
    navigate("/login");
  };

  const filters = [
    { v: "all", l: "All" },
    { v: "unread", l: "Unread" },
    { v: "interested", l: "Interested" },
    { v: "closed", l: "Closed" },
    { v: "failed", l: "Failed" },
  ];

  const unreadCount = convs.filter(c => (c.unread_count > 0) || c.status === "unread").length;

  /* Per-message group boundaries (for tails / timestamps / spacing). */
  const groupInfo = useMemo(() => {
    const info: { isFirst: boolean; isLast: boolean }[] = messages.map(() => ({ isFirst: false, isLast: false }));
    let start = 0;
    for (let i = 1; i <= messages.length; i++) {
      if (i === messages.length || !sameGroup(messages[i - 1], messages[i])) {
        info[start].isFirst = true;
        info[i - 1].isLast = true;
        start = i;
      }
    }
    return info;
  }, [messages]);

  const menuItems = [
    { to: "/dashboard", icon: Home, label: "Dashboard" },
    { to: "/contacts", icon: Users, label: "Contacts" },
    { to: "/campaigns", icon: Megaphone, label: "Campaigns" },
    { to: "/settings", icon: SettingsIcon, label: "Settings" },
  ];

  const displayName = user?.display_name || user?.username || "SMS SENDER";

  /* ================================================================ */
  return (
    <div className="app-shell w-full bg-[#111b21] flex flex-col">
      {/* WhatsApp Web container */}
      <div className="flex flex-1 overflow-hidden bg-white dark:bg-[#111b21]">

        {/* ============ LEFT — Conversation list ============ */}
        <div className={`${selected ? "hidden md:flex" : "flex"} w-full md:w-[420px] lg:w-[420px] flex-shrink-0 flex-col border-r border-[#e9edef] dark:border-[#222d34] bg-white dark:bg-[#111b21]`}>
          {/* List header (59px, like WhatsApp) */}
          <div className="h-[59px] bg-[#f0f2f5] dark:bg-[#202c33] flex items-center justify-between px-3 md:px-4 flex-shrink-0 relative">
            <div className="flex items-center gap-2.5 min-w-0">
              <div className="w-10 h-10 rounded-full bg-[#00a884] flex items-center justify-center text-white font-semibold text-[15px] flex-shrink-0">
                {initials(displayName, "")}
              </div>
              <span className="font-semibold text-[15px] text-[#111b21] dark:text-[#e9edef] truncate hidden sm:block">
                {displayName}
              </span>
            </div>
            <div className="flex items-center gap-0.5">
              <button onClick={doPoll} disabled={polling} title="Sync from phone"
                className="w-10 h-10 flex items-center justify-center rounded-full hover:bg-black/5 dark:hover:bg-white/10 text-[#54656f] dark:text-[#aebac1]">
                <span className={`text-[20px] leading-none ${polling ? "animate-spin inline-block" : ""}`}>↻</span>
              </button>
              <button onClick={doPollDebug} disabled={debugLoading} title="Receive-path diagnostic"
                className="w-10 h-10 flex items-center justify-center rounded-full hover:bg-black/5 dark:hover:bg-white/10 text-[#54656f] dark:text-[#aebac1]">
                <Bug size={19} />
              </button>
              <div className="relative">
                <button onClick={() => setShowMenu(!showMenu)} title="Menu"
                  className="w-10 h-10 flex items-center justify-center rounded-full hover:bg-black/5 dark:hover:bg-white/10 text-[#54656f] dark:text-[#aebac1]">
                  <MoreVertical size={19} />
                </button>
                {showMenu && (
                  <>
                    <div className="fixed inset-0 z-40" onClick={() => setShowMenu(false)} />
                    <div className="absolute right-0 top-11 bg-white dark:bg-[#233138] rounded-lg shadow-xl border border-gray-200 dark:border-[#222d34] py-1.5 w-52 z-50">
                      {menuItems.map(it => (
                        <Link key={it.to} to={it.to} onClick={() => setShowMenu(false)}
                          className="w-full flex items-center gap-3 px-3 py-2.5 text-[14px] text-[#111b21] dark:text-[#e9edef] hover:bg-[#f0f2f5] dark:hover:bg-[#182533]">
                          <it.icon size={16} className="text-[#54656f] dark:text-[#aebac1]" /> {it.label}
                        </Link>
                      ))}
                      <div className="border-t border-gray-100 dark:border-[#222d34] my-1" />
                      <button onClick={handleLogout}
                        className="w-full flex items-center gap-3 px-3 py-2.5 text-[14px] text-red-600 dark:text-red-400 hover:bg-[#f0f2f5] dark:hover:bg-[#182533]">
                        <LogOut size={16} /> Log out
                      </button>
                    </div>
                  </>
                )}
              </div>
            </div>
          </div>

          {/* Search (WhatsApp: grey pill, 8px inset) */}
          <div className="px-2 md:px-3 py-2 bg-white dark:bg-[#111b21] border-b border-[#e9edef] dark:border-[#222d34] flex-shrink-0">
            <div className="relative">
              <SearchIcon size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#54656f] dark:text-[#8696a0]" />
              <input
                className="w-full pl-9 pr-3 py-[7px] bg-[#f0f2f5] dark:bg-[#202c33] rounded-lg text-[14px] placeholder:text-[#667781] dark:placeholder:text-[#8696a0] focus:outline-none text-[#111b21] dark:text-[#e9edef]"
                placeholder="Search or start new chat"
                value={search}
                onChange={e => setSearch(e.target.value)}
              />
            </div>
          </div>

          {/* Filter chips (WhatsApp-style pills) */}
          <div className="px-2 md:px-3 py-2 bg-white dark:bg-[#111b21] flex gap-2 overflow-x-auto scrollbar-none flex-shrink-0">
            {filters.map(f => {
              const active = filter === f.v;
              return (
                <button
                  key={f.v}
                  onClick={() => { setFilter(f.v); setSelected(null); }}
                  className={`whitespace-nowrap px-3 py-1.5 rounded-full text-[13px] font-medium transition-colors flex-shrink-0
                    ${active ? "bg-[#00a884] text-white shadow-sm" : "bg-[#f0f2f5] dark:bg-[#182533] text-[#54656f] dark:text-[#8696a0] hover:bg-[#e9edef] dark:hover:bg-[#202c33]"}`}
                >
                  {f.l} {f.v === "unread" && unreadCount > 0 ? ` · ${unreadCount}` : ""}
                </button>
              );
            })}
          </div>

          {/* Archive-style row */}
          <div className="px-3 md:px-4 py-2.5 bg-white dark:bg-[#111b21] flex items-center justify-between border-b border-[#f0f2f5] dark:border-[#222d34] flex-shrink-0">
            <button onClick={doPoll} className="text-[13px] text-[#00a884] font-medium hover:underline flex items-center gap-1.5">
              <span className="text-[14px]">📥</span> Sync from phone
            </button>
            <span className="text-[11px] text-[#667781] dark:text-[#8696a0]">{convs.length} chats</span>
          </div>

          {/* Conversation list */}
          <div className="flex-1 overflow-y-auto wa-scrollbar bg-white dark:bg-[#111b21]">
            {loading ? (
              [...Array(8)].map((_, i) => (
                <div key={i} className="flex gap-3 px-3 py-3 animate-pulse">
                  <div className="w-[49px] h-[49px] rounded-full bg-gray-200 dark:bg-gray-700 flex-shrink-0" />
                  <div className="flex-1 space-y-2 py-1">
                    <div className="h-3.5 bg-gray-200 dark:bg-gray-700 rounded w-1/2" />
                    <div className="h-3 bg-gray-100 dark:bg-gray-800 rounded w-3/4" />
                  </div>
                </div>
              ))
            ) : convs.length === 0 ? (
              <div className="text-center py-16 px-6">
                <MessageCircle size={48} className="mx-auto mb-3 text-[#00a884] opacity-40" />
                <p className="text-[14px] font-medium text-[#111b21] dark:text-[#e9edef]">No conversations yet</p>
                <p className="text-[13px] text-[#667781] dark:text-[#8696a0] mt-1">
                  Tap <b className="text-[#00a884]">↻ Sync</b> to pull messages from your phone.
                </p>
              </div>
            ) : convs.map(c => {
              const isUnread = (c.unread_count > 0) || c.status === "unread";
              const isActive = selected?.id === c.id;
              const name = c.contact_name || c.contact_phone || "Unknown";
              const preview = c.last_message_preview || "No messages yet";
              return (
                <button
                  key={c.id}
                  onClick={() => openChat(c)}
                  className={`w-full flex gap-3 px-3 py-3 text-left hover:bg-[#f5f6f6] dark:hover:bg-[#202c33] transition-colors relative
                    ${isActive ? "bg-[#f0f2f5] dark:bg-[#2a3942]" : "bg-white dark:bg-[#111b21]"}`}
                >
                  <div className={`w-[49px] h-[49px] rounded-full flex items-center justify-center text-white font-medium text-[15px] flex-shrink-0 ${avatarColor(name)}`}>
                    {initials(name, c.contact_phone)}
                  </div>
                  <div className="flex-1 min-w-0 flex flex-col justify-center border-b border-[#f0f2f5] dark:border-[#222d34] -mb-3 pb-3">
                    <div className="flex items-baseline justify-between gap-2">
                      <span className={`truncate text-[16px] leading-5 ${isUnread ? "font-semibold text-[#111b21] dark:text-[#e9edef]" : "font-normal text-[#111b21] dark:text-[#e9edef]"}`}>
                        {name}
                      </span>
                      <span className={`text-[11px] flex-shrink-0 ml-2 ${isUnread ? "text-[#00a884] font-medium" : "text-[#667781] dark:text-[#8696a0]"}`}>
                        {formatListTime(c.last_message_at)}
                      </span>
                    </div>
                    <div className="flex items-center justify-between gap-2 mt-0.5">
                      <p className={`truncate text-[13px] leading-4 flex items-center gap-1 min-w-0 flex-1 ${isUnread ? "text-[#111b21] dark:text-[#e9edef] font-medium" : "text-[#667781] dark:text-[#8696a0]"}`}>
                        {c.status === "interested" && <Star size={12} className="text-[#f7c948] flex-shrink-0" />}
                        {!isUnread && c.status !== "interested" && <span className="text-[#54656f] dark:text-[#8696a0] flex-shrink-0">✓✓</span>}
                        <span className="truncate">{preview}</span>
                      </p>
                      <div className="flex items-center gap-1.5 flex-shrink-0">
                        {isUnread ? (
                          <span className="bg-[#00a884] text-white text-[11px] font-medium min-w-[20px] h-[20px] px-1.5 flex items-center justify-center rounded-full">
                            {c.unread_count || 1}
                          </span>
                        ) : c.status === "closed" ? (
                          <Archive size={13} className="text-[#667781] dark:text-[#8696a0]" />
                        ) : null}
                      </div>
                    </div>
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        {/* ============ RIGHT — Chat pane ============ */}
        <div className="flex-1 flex flex-col bg-[#efeae2] dark:bg-[#0b141a] relative overflow-hidden">

          {/* DEBUG VIEW */}
          {debugData ? (
            <div className="flex-1 p-4 overflow-y-auto wa-scrollbar bg-white dark:bg-[#111b21]">
              <div className="flex items-center justify-between mb-3">
                <h2 className="font-bold text-[16px] text-[#111b21] dark:text-[#e9edef]">🔍 Receive path diagnostic</h2>
                <button onClick={() => setDebugData(null)} className="w-8 h-8 rounded-full bg-[#f0f2f5] dark:bg-[#202c33] flex items-center justify-center text-[#54656f]">✕</button>
              </div>
              {debugData.error ? (
                <p className="text-red-600 text-sm">Error: {debugData.error}</p>
              ) : (
                <div className="space-y-3 text-[13px]">
                  {debugData.issues?.length > 0 ? (
                    <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg p-3">
                      <p className="font-medium mb-1 text-red-700 dark:text-red-400">Problems blocking incoming SMS</p>
                      <ul className="list-disc ml-4 space-y-1 text-red-700 dark:text-red-300">
                        {debugData.issues.map((i: string, n: number) => (<li key={n}>{i}</li>))}
                      </ul>
                    </div>
                  ) : (
                    <div className="bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 rounded-lg p-3">
                      <p className="font-medium text-green-700 dark:text-green-400">{debugData.note}</p>
                    </div>
                  )}
                  <div className="bg-[#f0f2f5] dark:bg-[#202c33] rounded-lg p-3 space-y-1 text-[#111b21] dark:text-[#e9edef]">
                    <p className="font-semibold mb-1">Configuration</p>
                    <p>Public URL set: <strong>{String(debugData.config?.public_base_url_set)}</strong></p>
                    <p>Gateway credentials: <strong>{String(debugData.config?.credentials_set)}</strong></p>
                    <p>Signing secret: <strong>{String(debugData.config?.signing_secret_set)}</strong>{debugData.config?.allow_unsigned && " (unsigned allowed)"}</p>
                    {debugData.webhook_url && <p className="text-[#667781] dark:text-[#8696a0] break-all text-[11px]">Delivering to: <code className="bg-white dark:bg-[#111b21] px-1 rounded">{debugData.webhook_url}</code></p>}
                  </div>
                  <div className="bg-[#f0f2f5] dark:bg-[#202c33] rounded-lg p-3 space-y-1 text-[#111b21] dark:text-[#e9edef]">
                    <p className="font-semibold mb-1">Device & webhooks</p>
                    <p>Devices online: <strong>{debugData.devices?.count ?? 0}</strong></p>
                    <p>Events registered: <strong>{debugData.matching_events?.length ?? 0}</strong> {debugData.matching_events?.join(", ")}</p>
                    <p>Inbound messages stored: <strong>{debugData.stored_inbound_messages ?? 0}</strong></p>
                  </div>
                  {debugData.recent_webhook_events?.length > 0 ? (
                    <div>
                      <p className="font-semibold mb-1">Last webhooks received</p>
                      {debugData.recent_webhook_events.map((e: any, i: number) => (
                        <div key={i} className={`mb-1 p-2 rounded text-[12px] ${e.status === "error" ? "bg-red-50 dark:bg-red-900/20" : "bg-green-50 dark:bg-green-900/20"}`}>
                          <p><code>{e.event_type}</code> — {e.status} @ {e.at?.slice(11, 19)}</p>
                          {e.error && <p className="text-red-600">{e.error}</p>}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-orange-600">No webhook has ever reached this server.</p>
                  )}
                </div>
              )}
            </div>
          ) : pollDebug && !selected ? (
            <div className="flex-1 p-4 overflow-y-auto wa-scrollbar bg-white dark:bg-[#111b21]">
              <div className="bg-[#f0f2f5] dark:bg-[#202c33] rounded-lg p-3 text-[13px]">
                <p className="font-semibold mb-2">📊 Sync result</p>
                <div className="space-y-1">
                  <p>Device online: <strong className={pollDebug.device_online ? "text-[#00a884]" : "text-red-600"}>{String(!!pollDebug.device_online)}</strong></p>
                  <p>Events registered: <strong className={pollDebug.registered_events?.length ? "text-[#00a884]" : "text-red-600"}>{pollDebug.registered_events?.length || 0}</strong> {pollDebug.registered_events?.join(", ")}</p>
                  <p>History replay triggered: <strong>{String(!!pollDebug.export_triggered)}</strong></p>
                  <p>Delivery statuses updated: <strong>{pollDebug.outgoing_updated || 0}</strong></p>
                  <p>Outgoing checked: {pollDebug.total_api_messages || 0}</p>
                  {pollDebug.webhook_url && <p className="mt-1 text-[#667781] break-all text-[11px]">Webhook: <code className="bg-white dark:bg-[#111b21] px-1 rounded">{pollDebug.webhook_url}</code></p>}
                  {pollDebug.error && <p className="text-red-600 mt-1">Error: {pollDebug.error}</p>}
                  {pollDebug.problems?.length > 0 && (
                    <div className="mt-2 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded p-2">
                      <p className="font-medium text-red-700 dark:text-red-400 mb-1">Needs attention</p>
                      <ul className="list-disc ml-4 space-y-1">{pollDebug.problems.map((p: string, i: number) => (<li key={i}>{p}</li>))}</ul>
                    </div>
                  )}
                  {pollDebug.note && <p className="text-[#00a884] mt-1">{pollDebug.note}</p>}
                </div>
              </div>
              <div className="mt-4 text-center">
                <button onClick={doPollDebug} className="text-[13px] text-[#00a884] hover:underline">🔍 Full diagnostic</button>
                <p className="text-[13px] text-[#667781] mt-2">Select a chat on the left</p>
              </div>
            </div>
          ) : selected ? (
            <>
              {/* Chat header (59px) */}
              <div className="h-[59px] bg-[#f0f2f5] dark:bg-[#202c33] flex items-center justify-between px-2 md:px-4 border-l border-[#e9edef] dark:border-[#222d34] flex-shrink-0 relative z-20">
                <div className="flex items-center gap-2 min-w-0 flex-1">
                  <button className="md:hidden w-9 h-9 -ml-1 flex items-center justify-center rounded-full hover:bg-black/5 dark:hover:bg-white/10 text-[#54656f] dark:text-[#aebac1]" onClick={() => { setSelected(null); setShowActions(false); }}>
                    <ChevronLeft size={22} />
                  </button>
                  <div className={`w-10 h-10 rounded-full flex items-center justify-center text-white font-medium text-[15px] flex-shrink-0 ${avatarColor(selected.contact_name || selected.contact_phone)}`}>
                    {initials(selected.contact_name || "", selected.contact_phone || "")}
                  </div>
                  <div className="min-w-0 flex-1">
                    <h2 className="font-semibold text-[16px] leading-5 text-[#111b21] dark:text-[#e9edef] truncate">
                      {selected.contact_name || selected.contact_phone}
                    </h2>
                    <p className="text-[12px] text-[#667781] dark:text-[#8696a0] truncate flex items-center gap-1">
                      {selected.contact_phone}
                      {selected.unread_count > 0 && <span className="bg-[#00a884] text-white text-[10px] px-1.5 py-px rounded-full ml-1">{selected.unread_count} new</span>}
                      {selected.status === "interested" && <span className="text-[#f7c948]">★ interested</span>}
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-0.5">
                  <button className="hidden sm:flex w-10 h-10 items-center justify-center rounded-full hover:bg-black/5 dark:hover:bg-white/10 text-[#54656f] dark:text-[#aebac1]"><Video size={19} /></button>
                  <button className="hidden sm:flex w-10 h-10 items-center justify-center rounded-full hover:bg-black/5 dark:hover:bg-white/10 text-[#54656f] dark:text-[#aebac1]"><Phone size={19} /></button>
                  <div className="relative">
                    <button onClick={() => setShowActions(!showActions)} className="w-10 h-10 flex items-center justify-center rounded-full hover:bg-black/5 dark:hover:bg-white/10 text-[#54656f] dark:text-[#aebac1]">
                      <MoreVertical size={19} />
                    </button>
                    {showActions && (
                      <>
                        <div className="fixed inset-0 z-40" onClick={() => setShowActions(false)} />
                        <div className="absolute right-0 top-11 bg-white dark:bg-[#233138] rounded-lg shadow-xl border border-gray-200 dark:border-[#222d34] py-1.5 w-52 z-50">
                          <button onClick={() => { markAs("interested"); setShowActions(false); }} className="w-full text-left px-3 py-2.5 text-[14px] text-[#111b21] dark:text-[#e9edef] hover:bg-[#f0f2f5] dark:hover:bg-[#182533] flex items-center gap-2.5"><Star size={16} className="text-[#f7c948]" /> Mark interested</button>
                          <button onClick={() => { markAs("not-interested"); setShowActions(false); }} className="w-full text-left px-3 py-2.5 text-[14px] text-[#111b21] dark:text-[#e9edef] hover:bg-[#f0f2f5] dark:hover:bg-[#182533] flex items-center gap-2.5"><ThumbsDown size={16} className="text-[#667781]" /> Not interested</button>
                          <button onClick={() => { markAs("close"); setShowActions(false); }} className="w-full text-left px-3 py-2.5 text-[14px] text-[#111b21] dark:text-[#e9edef] hover:bg-[#f0f2f5] dark:hover:bg-[#182533] flex items-center gap-2.5"><Archive size={16} className="text-[#667781]" /> Close chat</button>
                          <div className="border-t border-gray-100 dark:border-[#222d34] my-1" />
                          <button onClick={() => { setShowInfo(!showInfo); setShowActions(false); }} className="w-full text-left px-3 py-2.5 text-[14px] text-[#111b21] dark:text-[#e9edef] hover:bg-[#f0f2f5] dark:hover:bg-[#182533]">Contact info</button>
                        </div>
                      </>
                    )}
                  </div>
                </div>
              </div>

              {showInfo && selected?.contact && (
                <div className="px-4 py-3 bg-[#fff8e1] dark:bg-[#182533] border-b border-[#ffe082] dark:border-[#2a3942] text-[13px] space-y-1 z-10">
                  {selected.contact.business_name && <p><strong>Business:</strong> {selected.contact.business_name}</p>}
                  {selected.contact.city && <p><strong>Location:</strong> {selected.contact.city}{selected.contact.state ? `, ${selected.contact.state}` : ""}</p>}
                  <p><strong>Phone:</strong> {selected.contact.phone_number}</p>
                  {selected.contact.email && <p><strong>Email:</strong> {selected.contact.email}</p>}
                </div>
              )}

              {/* Messages area — WhatsApp wallpaper */}
              <div
                className="flex-1 overflow-y-auto wa-scrollbar px-2 md:px-[6%] lg:px-[9%] py-3 relative"
                onScroll={onChatScroll}
                style={{ backgroundColor: "#efeae2", backgroundImage: CHAT_BG }}
              >
                <div className="absolute inset-0 bg-[#0b141a] opacity-0 dark:opacity-100 pointer-events-none" />
                <div className="relative z-10 flex flex-col">
                  {messages.length === 0 ? (
                    <div className="flex justify-center my-10">
                      <div className="bg-white dark:bg-[#182533] text-[#54656f] dark:text-[#8696a0] text-[12.5px] px-3 py-2 rounded-lg shadow-sm max-w-[85%] text-center">
                        🔒 Messages are secured. This chat is with <strong>{selected.contact_name || selected.contact_phone}</strong>. Be respectful and avoid spam.
                      </div>
                    </div>
                  ) : messages.map((m, i) => {
                    const isOut = m.direction === "outgoing";
                    const isFailed = isOut && (m.status === "failed" || m.status === "cancelled");
                    const isDelivered = m.status === "delivered";
                    const isSent = m.status === "sent";
                    const g = groupInfo[i];
                    const showDate = i === 0 || !isSameDay(messages[i - 1]?.created_at, m.created_at);
                    const bubble =
                      isOut
                        ? "bg-[#d9fdd3] dark:bg-[#005c4b] rounded-tr-none text-[#111b21] dark:text-[#e9edef]"
                        : "bg-white dark:bg-[#202c33] rounded-tl-none text-[#111b21] dark:text-[#e9edef]";
                    return (
                      <div key={m.id}>
                        {showDate && (
                          <div className="flex justify-center my-2.5">
                            <span className="bg-white dark:bg-[#182533] text-[#54656f] dark:text-[#8696a0] text-[12.5px] px-3 py-1 rounded-lg shadow-sm">
                              {dayLabel(m.created_at)}
                            </span>
                          </div>
                        )}
                        <div className={`flex ${isOut ? "justify-end" : "justify-start"} ${g.isFirst ? "mt-2" : "mt-[2px]"} ${i === 0 ? "mt-0" : ""}`}>
                          <div className={`relative max-w-[82%] sm:max-w-[65%] px-2 pt-1.5 pb-1 rounded-lg shadow-sm text-[14.2px] leading-[19px] break-words ${bubble}`}>
                            {g.isLast && (
                              <span
                                className={`absolute top-0 w-3 h-3 ${isOut ? "right-[-6px] bg-[#d9fdd3] dark:bg-[#005c4b]" : "left-[-6px] bg-white dark:bg-[#202c33]"}`}
                                style={{ clipPath: isOut ? "polygon(0 0, 100% 0, 0 100%)" : "polygon(100% 0, 0 0, 100% 100%)" }}
                              />
                            )}
                            <p className="whitespace-pre-wrap break-words pr-6">{m.body}</p>
                            {isFailed && m.last_error && (
                              <p className="text-[11px] mt-1 text-red-600 dark:text-red-300 bg-red-50 dark:bg-red-900/20 px-1.5 py-0.5 rounded">⚠ {m.last_error}</p>
                            )}
                            {g.isLast && (
                              <div className={`flex items-center gap-1 justify-end mt-0.5 text-[11px] select-none ${isOut ? "text-[#667781] dark:text-[#8696a0]" : "text-[#667781] dark:text-[#8696a0]"}`}>
                                <span>{formatTime(m.created_at)}</span>
                                {isOut && (
                                  <span className="flex items-center">
                                    {isFailed ? <span className="text-red-500">⚠ failed</span>
                                      : isDelivered ? <CheckCheck size={14} className="text-[#53bdeb]" />
                                      : isSent ? <CheckCheck size={14} className="text-[#8696a0]" />
                                      : <span className="text-[11px]">✓</span>}
                                  </span>
                                )}
                              </div>
                            )}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                  <div ref={chatEndRef} className="h-2" />
                </div>
              </div>

              {/* Composer — pinned above the mobile keyboard via 100dvh shell */}
              <div className="bg-[#f0f2f5] dark:bg-[#202c33] px-2 md:px-4 py-2 flex items-end gap-1.5 flex-shrink-0 safe-bottom">
                <button className="hidden sm:flex w-10 h-10 items-center justify-center rounded-full text-[#54656f] dark:text-[#8696a0] hover:bg-black/5 dark:hover:bg-white/10 flex-shrink-0"><Smile size={22} /></button>
                <button className="hidden sm:flex w-10 h-10 items-center justify-center rounded-full text-[#54656f] dark:text-[#8696a0] hover:bg-black/5 dark:hover:bg-white/10 flex-shrink-0"><Paperclip size={20} /></button>
                <div className="flex-1 bg-white dark:bg-[#2a3942] rounded-[8px] flex items-end gap-2 px-2.5 py-1 shadow-sm min-h-[42px]">
                  <textarea
                    ref={taRef}
                    className="flex-1 bg-transparent border-none outline-none resize-none py-[9px] text-[15px] leading-5 placeholder:text-[#667781] dark:placeholder:text-[#8696a0] text-[#111b21] dark:text-[#e9edef] max-h-[120px]"
                    placeholder="Type a message"
                    rows={1}
                    value={replyText}
                    onChange={e => setReplyText(e.target.value)}
                    onKeyDown={e => {
                      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendReply(); }
                    }}
                    enterKeyHint="send"
                    inputMode="text"
                    autoCapitalize="sentences"
                    autoComplete="off"
                  />
                </div>
                <button
                  onClick={sendReply}
                  disabled={sending || !replyText.trim()}
                  className={`w-11 h-11 rounded-full flex items-center justify-center flex-shrink-0 shadow-sm transition-colors ${replyText.trim() ? "bg-[#00a884] hover:bg-[#06cf9c] text-white" : "bg-[#00a884] text-white opacity-70"}`}
                >
                  {sending
                    ? <span className="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                    : replyText.trim() ? <Send size={18} className="ml-0.5" /> : <Mic size={19} />}
                </button>
              </div>
            </>
          ) : (
            /* Empty state — WhatsApp Web's iconic landing screen */
            <div className="flex-1 flex flex-col items-center justify-center bg-[#f0f2f5] dark:bg-[#222e35] p-6">
              <div className="w-[200px] h-[200px] lg:w-[303px] lg:h-[303px] mx-auto mb-6 rounded-full bg-[#f0f2f5] dark:bg-[#182533] flex items-center justify-center border border-[#e9edef] dark:border-[#222d34] shadow-sm">
                <MessageCircle size={110} className="text-[#41525d] dark:text-[#8696a0] opacity-20" />
              </div>
              <h3 className="text-[28px] font-light text-[#41525d] dark:text-[#e9edef] tracking-tight">SMS SENDER</h3>
              <p className="text-[13px] text-[#667781] dark:text-[#8696a0] mt-2 leading-5 text-center max-w-sm">
                Select a chat to start messaging. Your messages are synced from your phone via SMS-Gate.
              </p>
              <div className="mt-6 flex items-center gap-2">
                <button onClick={doPoll} disabled={polling}
                  className="flex items-center gap-2 px-5 py-2.5 rounded-full bg-[#00a884] text-white text-[14px] font-medium hover:bg-[#06cf9c] transition-colors disabled:opacity-70">
                  <span className={`text-[16px] ${polling ? "animate-spin inline-block" : ""}`}>↻</span> Sync from phone
                </button>
              </div>
              <p className="text-[12px] text-[#667781] dark:text-[#8696a0] mt-6 flex items-center justify-center gap-1">
                🔒 End-to-end synced with your device
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
