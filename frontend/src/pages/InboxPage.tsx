import { useVisiblePolling } from "../hooks/useVisiblePolling";
import { useState, useEffect, useRef, useMemo, useCallback } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import api from "../api/client";
import toast from "react-hot-toast";
import { useAuth } from "../hooks/useAuth";
import {
  Send, Star, ThumbsDown, ThumbsUp, Archive, ChevronLeft, CheckCheck, MessageCircle,
  Bug, Search as SearchIcon, MoreVertical, Phone, Video, Paperclip, Smile,
  Mic, LogOut, Settings as SettingsIcon, Users, Megaphone, Home, FileText, CalendarPlus,
  Filter, X as XIcon, BarChart3, Globe, Mail,
} from "lucide-react";
import emailApi from "../api/email";
import { startCall, openWhatsappChat, openWhatsappForCall, openWebsite, websiteUrl } from "../utils/call";
import type { Meeting, Template } from "../types";
import ShortcodePicker from "../components/ShortcodePicker";
import MeetingModal from "../components/MeetingModal";
import CampaignChip from "../components/CampaignChip";

/* ------------------------------------------------------------------ */
/* Helpers                                                             */
/* ------------------------------------------------------------------ */

const avatarColor = (name: string) => {
  const colors = [
    "bg-primary-600", "bg-primary-700", "bg-primary-800", "bg-[#34B7F1]",
    "bg-warning-400", "bg-accent-500", "bg-primary-400", "bg-warning-400",
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
  // Unread email threads, so the Email button carries the same kind of badge
  // the SMS side shows for chats.
  const [emailUnread, setEmailUnread] = useState(0);
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
  const [templates, setTemplates] = useState<Template[]>([]);
  const [showBooking, setShowBooking] = useState(false);
  const [editingMeetingId, setEditingMeetingId] = useState<number | null>(null);
  const [contactMeetings, setContactMeetings] = useState<Meeting[]>([]);
  const [selectedTemplateId, setSelectedTemplateId] = useState("");
  const [templateLoading, setTemplateLoading] = useState(false);

  /* ---- Campaign filtering ------------------------------------------
     The Campaigns page deep-links here with ?campaign_id=… (optionally
     &replied=1), which is what "see the replies from this campaign" does.
     The URL is the source of truth so the link is shareable and Back works. */
  const [searchParams, setSearchParams] = useSearchParams();
  const campaignId = searchParams.get("campaign_id");
  const adsCampaignId = searchParams.get("ads_campaign_id");
  const repliedOnly = searchParams.get("replied") === "1";
  const focusContactId = searchParams.get("contact_id");
  const [campaignOptions, setCampaignOptions] = useState<any[]>([]);
  const [showCampaignPicker, setShowCampaignPicker] = useState(false);

  const activeCampaign = useMemo(() => {
    if (!campaignId && !adsCampaignId) return null;
    const kind = adsCampaignId ? "ads" : "campaign";
    const id = Number(adsCampaignId || campaignId);
    return campaignOptions.find((c) => c.kind === kind && c.id === id) || { id, kind, name: `Campaign #${id}`, status: "" };
  }, [campaignId, adsCampaignId, campaignOptions]);

  const chatEndRef = useRef<HTMLDivElement>(null);
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

  // Templates are loaded once and personalized by the backend when selected,
  // using both built-in and CSV-imported custom contact fields.
  useEffect(() => {
    let cancelled = false;
    const loadTemplates = async () => {
      try {
        const all: Template[] = [];
        let page = 1;
        let total = 0;
        do {
          const { data } = await api.get("/templates/", { params: { page, per_page: 100 } });
          all.push(...(data.items || []));
          total = data.total ?? all.length;
          page += 1;
        } while (all.length < total);
        if (!cancelled) setTemplates(all.filter(template => template.is_active));
      } catch {
        if (!cancelled) setTemplates([]);
      }
    };
    loadTemplates();
    return () => { cancelled = true; };
  }, []);

  // Campaigns that actually have leads in the inbox, for the filter menu.
  useEffect(() => {
    let cancelled = false;
    api.get("/inbox/campaign-filters")
      .then(({ data }) => { if (!cancelled) setCampaignOptions(data.items || []); })
      .catch(() => { if (!cancelled) setCampaignOptions([]); });
    return () => { cancelled = true; };
  }, []);

  const filterRef = useRef(filter);
  const searchRef = useRef(search);
  const campaignRef = useRef({ campaignId, adsCampaignId, repliedOnly });
  // Badge for the Email inbox button; refreshed with the SMS poll below.
  useEffect(() => {
    emailApi
      .unreadCount()
      .then((data) => setEmailUnread(data.unread || 0))
      .catch(() => {});
  }, []);

  useEffect(() => { filterRef.current = filter; }, [filter]);
  useEffect(() => { searchRef.current = search; }, [search]);
  useEffect(() => {
    campaignRef.current = { campaignId, adsCampaignId, repliedOnly };
  }, [campaignId, adsCampaignId, repliedOnly]);

  useEffect(() => { loadConvs(); }, [filter, campaignId, adsCampaignId, repliedOnly]);
  useEffect(() => { const t = setTimeout(loadConvs, 350); return () => clearTimeout(t); }, [search]);

  // Deep link into one person's chat: /inbox?contact_id=42 (what a campaign's
  // lead list links to). Runs once per target, after the list has loaded, and
  // then drops the param so the chat is not force-reopened on every poll.
  const openedContactRef = useRef<string | null>(null);
  useEffect(() => {
    if (!focusContactId || loading) return;
    if (openedContactRef.current === focusContactId) return;

    const clearParam = () => {
      const next = new URLSearchParams(searchParams);
      next.delete("contact_id");
      setSearchParams(next, { replace: true });
    };

    const match = convs.find((c) => String(c.contact_id) === focusContactId);
    if (match) {
      openedContactRef.current = focusContactId;
      openChat(match);
      clearParam();
      return;
    }

    // The list finished loading and the person is not in it — a status filter
    // is hiding them, or they fell outside the page. Say so and drop the
    // param, rather than leaving a dead link that silently does nothing.
    openedContactRef.current = focusContactId;
    toast("That chat isn't in the current view — try clearing the filters.", { icon: "🔍" });
    clearParam();
  }, [focusContactId, convs, loading, searchParams, setSearchParams]);
  // Key the effects on the stable conversation id, NOT the `selected` object:
  // loadMessages() refreshes the object (to pick up status changes), and an
  // object-keyed effect would re-fire forever in a tight async loop.
  useEffect(() => { if (selected) loadMessages(selected.id); }, [selected?.id]);
  useEffect(() => {
    if (!selected?.contact_id) { setContactMeetings([]); return; }
    let cancelled = false;
    api.get("/calendar/upcoming", { params: { contact_id: selected.contact_id, limit: 5 } })
      .then(({ data }) => { if (!cancelled) setContactMeetings(data.items || []); })
      .catch(() => { if (!cancelled) setContactMeetings([]); });
    return () => { cancelled = true; };
  }, [selected?.id]);  
  useEffect(() => {
    if (selected && nearBottomRef.current) chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, selected?.id]);

  // Live refresh while the tab is visible; a background tab stops polling so
  // it does not keep the database awake all day. (Coming back refreshes at once.)
  useVisiblePolling(() => { loadConvs(); if (selected) loadMessages(selected.id); }, 8000);

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
      const c = campaignRef.current;
      if (c.campaignId) params.campaign_id = c.campaignId;
      if (c.adsCampaignId) params.ads_campaign_id = c.adsCampaignId;
      if (c.repliedOnly) params.replied_only = true;
      const { data } = await api.get("/inbox/conversations", { params });
      setConvs(data.items || []);
    } catch {} finally { setLoading(false); }
  }, []);

  /** Point the inbox at one campaign (or clear it). Drives the URL. */
  const applyCampaignFilter = useCallback((option: any | null, onlyReplies = false) => {
    const next = new URLSearchParams();
    if (option) {
      next.set(option.kind === "ads" ? "ads_campaign_id" : "campaign_id", String(option.id));
      if (onlyReplies) next.set("replied", "1");
    }
    setSearchParams(next, { replace: false });
    setSelected(null);
    setShowCampaignPicker(false);
  }, [setSearchParams]);

  const loadMessages = useCallback(async (convId: number) => {
    try {
      const { data } = await api.get(`/inbox/conversations/${convId}`);
      setMessages(data.messages || []);
      // Only replace the object when something actually changed, so effects
      // keyed on `selected?.id` stay calm. The campaign badge is picked up
      // here too: the detail endpoint backfills attribution for old threads,
      // so a chat opened from a list row that had no chip still gets one.
      setSelected((s: any) => {
        if (!s || s.id !== convId) return s;
        const statusChanged = s.status !== data.status;
        const campaignChanged = (s.campaign?.id ?? null) !== (data.campaign?.id ?? null);
        if (!statusChanged && !campaignChanged) return s;
        return { ...s, status: data.status, campaign: data.campaign, last_campaign: data.last_campaign };
      });
    } catch {}
  }, []);

  const openChat = (c: any) => {
    setSelected(c);
    setShowInfo(false);
    setPollDebug(null);
    setDebugData(null);
    setReplyText("");
    setSelectedTemplateId("");
    nearBottomRef.current = true;
  };

  const chooseTemplate = async (value: string) => {
    setSelectedTemplateId(value);
    if (!value || !selected) return;
    setTemplateLoading(true);
    try {
      const { data } = await api.get(
        `/inbox/conversations/${selected.id}/templates/${value}/preview`
      );
      setReplyText(data.body || "");
      requestAnimationFrame(() => taRef.current?.focus());
    } catch (err: any) {
      setSelectedTemplateId("");
      toast.error(err.response?.data?.detail || "Could not personalize this template");
    } finally {
      setTemplateLoading(false);
    }
  };

  const sendReply = async () => {
    if (!replyText.trim() || !selected) return;
    setSending(true);
    try {
      const params: Record<string, string> = { body: replyText.trim() };
      if (selectedTemplateId) params.template_id = selectedTemplateId;
      const { data } = await api.post(`/inbox/conversations/${selected.id}/reply`, null, { params });
      if (data?.success === false) {
        toast.error(data.error || "Gateway rejected the message.");
      } else {
        setReplyText("");
        setSelectedTemplateId("");
        toast.success("Sent");
      }
      loadMessages(selected.id); loadConvs();
    }
    catch (err: any) { toast.error(err.response?.data?.detail || "Failed"); }
    finally { setSending(false); }
  };

  const rateReply = async (messageId: number, verdict: "good" | "bad") => {
    try {
      const { data } = await api.post(`/inbox/messages/${messageId}/feedback`, { verdict });
      setMessages((ms) => ms.map((m) => m.id === messageId
        ? { ...m, ai_sentiment: data.ai_sentiment, ai_intent: data.ai_intent, ai_confidence: 1 } : m));
      setSelected((s: any) => s ? { ...s, status: data.status ?? s.status } : s);
      toast.success(verdict === "good" ? "Marked as good reply" : "Marked as bad reply — lead opted out");
      loadConvs();
    } catch (err: any) { toast.error(err.response?.data?.detail || "Failed"); }
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
    <div className="app-shell w-full bg-gray-900 flex flex-col">
      {/* WhatsApp Web container */}
      <div className="flex flex-1 overflow-hidden bg-white dark:bg-gray-900">

        {/* ============ LEFT — Conversation list ============ */}
        <div className={`${selected ? "hidden md:flex" : "flex"} w-full md:w-[420px] lg:w-[420px] flex-shrink-0 flex-col border-r border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900`}>
          {/* List header (59px, like WhatsApp) */}
          <div className="h-[59px] bg-gray-100 dark:bg-gray-800 flex items-center justify-between px-3 md:px-4 flex-shrink-0 relative">
            <div className="flex items-center gap-2.5 min-w-0">
              <div className="w-10 h-10 rounded-full bg-primary-600 flex items-center justify-center text-white font-semibold text-[15px] flex-shrink-0">
                {initials(displayName, "")}
              </div>
              <span className="font-semibold text-[15px] text-gray-900 dark:text-gray-200 truncate hidden sm:block">
                {displayName}
              </span>
            </div>
            <div className="flex items-center gap-0.5">
              {/* The email inbox is its own screen; this jumps straight to it. */}
              <Link to="/email-inbox" title="Open the Email inbox"
                className="h-10 px-2.5 flex items-center gap-1.5 rounded-full hover:bg-black/5 dark:hover:bg-white/10 text-gray-600 dark:text-gray-400 text-[13px]">
                <Mail size={18} />
                {emailUnread > 0 && (
                  <span className="min-w-[18px] h-[18px] px-1 rounded-full bg-primary-600 text-white text-[11px] flex items-center justify-center">
                    {emailUnread}
                  </span>
                )}
              </Link>
              <button onClick={doPoll} disabled={polling} title="Sync from phone"
                className="w-10 h-10 flex items-center justify-center rounded-full hover:bg-black/5 dark:hover:bg-white/10 text-gray-600 dark:text-gray-400">
                <span className={`text-[20px] leading-none ${polling ? "animate-spin inline-block" : ""}`}>↻</span>
              </button>
              <button onClick={doPollDebug} disabled={debugLoading} title="Receive-path diagnostic"
                className="w-10 h-10 flex items-center justify-center rounded-full hover:bg-black/5 dark:hover:bg-white/10 text-gray-600 dark:text-gray-400">
                <Bug size={19} />
              </button>
              <div className="relative">
                <button onClick={() => setShowMenu(!showMenu)} title="Menu"
                  className="w-10 h-10 flex items-center justify-center rounded-full hover:bg-black/5 dark:hover:bg-white/10 text-gray-600 dark:text-gray-400">
                  <MoreVertical size={19} />
                </button>
                {showMenu && (
                  <>
                    <div className="fixed inset-0 z-40" onClick={() => setShowMenu(false)} />
                    <div className="absolute right-0 top-11 bg-white dark:bg-gray-700 rounded-lg shadow-xl border border-gray-200 dark:border-gray-800 py-1.5 w-52 z-50">
                      {menuItems.map(it => (
                        <Link key={it.to} to={it.to} onClick={() => setShowMenu(false)}
                          className="w-full flex items-center gap-3 px-3 py-2.5 text-[14px] text-gray-900 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-800">
                          <it.icon size={16} className="text-gray-600 dark:text-gray-400" /> {it.label}
                        </Link>
                      ))}
                      <div className="border-t border-gray-100 dark:border-gray-800 my-1" />
                      <button onClick={handleLogout}
                        className="w-full flex items-center gap-3 px-3 py-2.5 text-[14px] text-danger-600 dark:text-danger-400 hover:bg-gray-100 dark:hover:bg-gray-800">
                        <LogOut size={16} /> Log out
                      </button>
                    </div>
                  </>
                )}
              </div>
            </div>
          </div>

          {/* Search (WhatsApp: grey pill, 8px inset) */}
          <div className="px-2 md:px-3 py-2 bg-white dark:bg-gray-900 border-b border-gray-200 dark:border-gray-800 flex-shrink-0">
            <div className="relative">
              <SearchIcon size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-600 dark:text-gray-400" />
              <input
                className="w-full pl-9 pr-3 py-[7px] bg-gray-100 dark:bg-gray-800 rounded-lg text-[14px] placeholder:text-gray-500 dark:placeholder:text-gray-400 focus:outline-none text-gray-900 dark:text-gray-200"
                placeholder="Search or start new chat"
                value={search}
                onChange={e => setSearch(e.target.value)}
              />
            </div>
          </div>

          {/* Filter chips (WhatsApp-style pills) */}
          <div className="px-2 md:px-3 py-2 bg-white dark:bg-gray-900 flex gap-2 overflow-x-auto scrollbar-none flex-shrink-0">
            {filters.map(f => {
              const active = filter === f.v;
              return (
                <button
                  key={f.v}
                  onClick={() => { setFilter(f.v); setSelected(null); }}
                  className={`whitespace-nowrap px-3 py-1.5 rounded-full text-[13px] font-medium transition-colors flex-shrink-0
                    ${active ? "bg-primary-600 text-white shadow-sm" : "bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-800"}`}
                >
                  {f.l} {f.v === "unread" && unreadCount > 0 ? ` · ${unreadCount}` : ""}
                </button>
              );
            })}
          </div>

          {/* Campaign filter — "which campaign are these leads from?" */}
          <div className="px-2 md:px-3 pb-2 bg-white dark:bg-gray-900 flex-shrink-0 relative">
            {activeCampaign ? (
              <div className="flex items-center gap-2 px-2.5 py-2 rounded-lg bg-primary-50 dark:bg-gray-800 border border-primary-600/30">
                <CampaignChip campaign={activeCampaign} />
                <span className="text-[11px] text-gray-600 dark:text-gray-400 truncate">
                  {repliedOnly ? "replies only" : "all leads"} · {convs.length}
                </span>
                <div className="ml-auto flex items-center gap-1">
                  <button
                    onClick={() => applyCampaignFilter(activeCampaign, !repliedOnly)}
                    className="text-[11px] font-medium text-primary-600 hover:underline px-1.5"
                    title={repliedOnly ? "Show every lead from this campaign" : "Show only leads who replied"}
                  >
                    {repliedOnly ? "All leads" : "Replies"}
                  </button>
                  <button
                    onClick={() => applyCampaignFilter(null)}
                    className="w-6 h-6 flex items-center justify-center rounded-full hover:bg-black/5 dark:hover:bg-white/10 text-gray-600 dark:text-gray-400"
                    title="Clear campaign filter"
                  >
                    <XIcon size={13} />
                  </button>
                </div>
              </div>
            ) : (
              <button
                onClick={() => setShowCampaignPicker((v) => !v)}
                className="w-full flex items-center gap-2 px-2.5 py-2 rounded-lg bg-gray-100 dark:bg-gray-800 text-[13px] text-gray-600 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-700"
              >
                <Filter size={14} className="text-primary-600" />
                Filter by campaign
                {campaignOptions.length > 0 && (
                  <span className="ml-auto text-[11px] text-gray-500">{campaignOptions.length}</span>
                )}
              </button>
            )}

            {showCampaignPicker && (
              <>
                <div className="fixed inset-0 z-40" onClick={() => setShowCampaignPicker(false)} />
                <div className="absolute left-2 right-2 md:left-3 md:right-3 top-full mt-1 z-50 bg-white dark:bg-gray-700 rounded-lg shadow-xl border border-gray-200 dark:border-gray-800 py-1.5 max-h-[320px] overflow-y-auto wa-scrollbar">
                  {campaignOptions.length === 0 ? (
                    <p className="px-3 py-3 text-[12px] text-gray-500 dark:text-gray-400">
                      No campaign has produced leads yet.
                    </p>
                  ) : (
                    campaignOptions.map((c) => (
                      <button
                        key={`${c.kind}-${c.id}`}
                        onClick={() => applyCampaignFilter(c)}
                        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-gray-100 dark:hover:bg-gray-800"
                      >
                        <CampaignChip campaign={c} />
                        <span className="ml-auto text-[11px] text-gray-500 dark:text-gray-400 flex-shrink-0">
                          {c.leads} lead{c.leads === 1 ? "" : "s"} · {c.replies} replied
                        </span>
                      </button>
                    ))
                  )}
                  <div className="border-t border-gray-100 dark:border-gray-800 my-1" />
                  <Link
                    to="/overview"
                    onClick={() => setShowCampaignPicker(false)}
                    className="w-full flex items-center gap-2 px-3 py-2 text-[13px] text-primary-600 hover:bg-gray-100 dark:hover:bg-gray-800"
                  >
                    <BarChart3 size={14} /> Open campaign overview
                  </Link>
                </div>
              </>
            )}
          </div>

          {/* Archive-style row */}
          <div className="px-3 md:px-4 py-2.5 bg-white dark:bg-gray-900 flex items-center justify-between border-b border-gray-100 dark:border-gray-800 flex-shrink-0">
            <button onClick={doPoll} className="text-[13px] text-primary-600 font-medium hover:underline flex items-center gap-1.5">
              <span className="text-[14px]">📥</span> Sync from phone
            </button>
            <span className="text-[11px] text-gray-500 dark:text-gray-400">{convs.length} chats</span>
          </div>

          {/* Conversation list */}
          <div className="flex-1 overflow-y-auto wa-scrollbar bg-white dark:bg-gray-900">
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
                <MessageCircle size={48} className="mx-auto mb-3 text-primary-600 opacity-40" />
                <p className="text-[14px] font-medium text-gray-900 dark:text-gray-200">No conversations yet</p>
                <p className="text-[13px] text-gray-500 dark:text-gray-400 mt-1">
                  Tap <b className="text-primary-600">↻ Sync</b> to pull messages from your phone.
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
                  className={`w-full flex gap-3 px-3 py-3 text-left hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors relative
                    ${isActive ? "bg-gray-100 dark:bg-gray-700" : "bg-white dark:bg-gray-900"}`}
                >
                  <div className={`w-[49px] h-[49px] rounded-full flex items-center justify-center text-white font-medium text-[15px] flex-shrink-0 ${avatarColor(name)}`}>
                    {initials(name, c.contact_phone)}
                  </div>
                  <div className="flex-1 min-w-0 flex flex-col justify-center border-b border-gray-100 dark:border-gray-800 -mb-3 pb-3">
                    <div className="flex items-baseline justify-between gap-2">
                      <span className={`truncate text-[16px] leading-5 ${isUnread ? "font-semibold text-gray-900 dark:text-gray-200" : "font-normal text-gray-900 dark:text-gray-200"}`}>
                        {name}
                      </span>
                      <span className={`text-[11px] flex-shrink-0 ml-2 ${isUnread ? "text-primary-600 font-medium" : "text-gray-500 dark:text-gray-400"}`}>
                        {formatListTime(c.last_message_at)}
                      </span>
                    </div>
                    <div className="flex items-center justify-between gap-2 mt-0.5">
                      <p className={`truncate text-[13px] leading-4 flex items-center gap-1 min-w-0 flex-1 ${isUnread ? "text-gray-900 dark:text-gray-200 font-medium" : "text-gray-500 dark:text-gray-400"}`}>
                        {c.status === "interested" && <Star size={12} className="text-warning-400 flex-shrink-0" />}
                        {!isUnread && c.status !== "interested" && <span className="text-gray-600 dark:text-gray-400 flex-shrink-0">✓✓</span>}
                        <span className="truncate">{preview}</span>
                      </p>
                      <div className="flex items-center gap-1.5 flex-shrink-0">
                        {isUnread ? (
                          <span className="bg-primary-600 text-white text-[11px] font-medium min-w-[20px] h-[20px] px-1.5 flex items-center justify-center rounded-full">
                            {c.unread_count || 1}
                          </span>
                        ) : c.status === "closed" ? (
                          <Archive size={13} className="text-gray-500 dark:text-gray-400" />
                        ) : null}
                      </div>
                    </div>
                    {/* Campaign source: the indicator that tells you at a
                        glance which campaign this lead came from. Tapping it
                        filters the whole inbox to that campaign. */}
                    {c.campaign && (
                      <div className="mt-1 flex items-center gap-1">
                        <CampaignChip
                          campaign={c.campaign}
                          size="xs"
                          onClick={() => applyCampaignFilter(c.campaign)}
                          title={`Lead from "${c.campaign.name}" — tap to see every lead from it`}
                        />
                        {c.last_campaign && c.last_campaign.id !== c.campaign.id && (
                          <span className="text-[9px] text-gray-500 dark:text-gray-400" title={`Last messaged by "${c.last_campaign.name}"`}>
                            → {c.last_campaign.name}
                          </span>
                        )}
                      </div>
                    )}
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        {/* ============ RIGHT — Chat pane ============ */}
        <div className="flex-1 flex flex-col bg-gray-100 dark:bg-gray-950 relative overflow-hidden">

          {/* DEBUG VIEW */}
          {debugData ? (
            <div className="flex-1 p-4 overflow-y-auto wa-scrollbar bg-white dark:bg-gray-900">
              <div className="flex items-center justify-between mb-3">
                <h2 className="font-bold text-[16px] text-gray-900 dark:text-gray-200">🔍 Receive path diagnostic</h2>
                <button onClick={() => setDebugData(null)} className="w-8 h-8 rounded-full bg-gray-100 dark:bg-gray-800 flex items-center justify-center text-gray-600">✕</button>
              </div>
              {debugData.error ? (
                <p className="text-danger-600 text-sm">Error: {debugData.error}</p>
              ) : (
                <div className="space-y-3 text-[13px]">
                  {debugData.issues?.length > 0 ? (
                    <div className="bg-danger-50 dark:bg-danger-900/20 border border-danger-200 dark:border-danger-800 rounded-lg p-3">
                      <p className="font-medium mb-1 text-danger-700 dark:text-danger-400">Problems blocking incoming SMS</p>
                      <ul className="list-disc ml-4 space-y-1 text-danger-700 dark:text-danger-300">
                        {debugData.issues.map((i: string, n: number) => (<li key={n}>{i}</li>))}
                      </ul>
                    </div>
                  ) : (
                    <div className="bg-success-50 dark:bg-success-900/20 border border-success-200 dark:border-success-800 rounded-lg p-3">
                      <p className="font-medium text-success-700 dark:text-success-400">{debugData.note}</p>
                    </div>
                  )}
                  <div className="bg-gray-100 dark:bg-gray-800 rounded-lg p-3 space-y-1 text-gray-900 dark:text-gray-200">
                    <p className="font-semibold mb-1">Configuration</p>
                    <p>Public URL set: <strong>{String(debugData.config?.public_base_url_set)}</strong></p>
                    <p>Gateway credentials: <strong>{String(debugData.config?.credentials_set)}</strong></p>
                    <p>Signing secret: <strong>{String(debugData.config?.signing_secret_set)}</strong>{debugData.config?.allow_unsigned && " (unsigned allowed)"}</p>
                    {debugData.webhook_url && <p className="text-gray-500 dark:text-gray-400 break-all text-[11px]">Delivering to: <code className="bg-white dark:bg-gray-900 px-1 rounded">{debugData.webhook_url}</code></p>}
                  </div>
                  <div className="bg-gray-100 dark:bg-gray-800 rounded-lg p-3 space-y-1 text-gray-900 dark:text-gray-200">
                    <p className="font-semibold mb-1">Device & webhooks</p>
                    <p>Devices online: <strong>{debugData.devices?.count ?? 0}</strong></p>
                    <p>Events registered: <strong>{debugData.matching_events?.length ?? 0}</strong> {debugData.matching_events?.join(", ")}</p>
                    <p>Inbound messages stored: <strong>{debugData.stored_inbound_messages ?? 0}</strong></p>
                  </div>
                  {debugData.recent_webhook_events?.length > 0 ? (
                    <div>
                      <p className="font-semibold mb-1">Last webhooks received</p>
                      {debugData.recent_webhook_events.map((e: any, i: number) => (
                        <div key={i} className={`mb-1 p-2 rounded text-[12px] ${e.status === "error" ? "bg-danger-50 dark:bg-danger-900/20" : "bg-success-50 dark:bg-success-900/20"}`}>
                          <p><code>{e.event_type}</code> — {e.status} @ {e.at?.slice(11, 19)}</p>
                          {e.error && <p className="text-danger-600">{e.error}</p>}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-warning-600">No webhook has ever reached this server.</p>
                  )}
                </div>
              )}
            </div>
          ) : pollDebug && !selected ? (
            <div className="flex-1 p-4 overflow-y-auto wa-scrollbar bg-white dark:bg-gray-900">
              <div className="bg-gray-100 dark:bg-gray-800 rounded-lg p-3 text-[13px]">
                <p className="font-semibold mb-2">📊 Sync result</p>
                <div className="space-y-1">
                  <p>Device online: <strong className={pollDebug.device_online ? "text-primary-600" : "text-danger-600"}>{String(!!pollDebug.device_online)}</strong></p>
                  <p>Events registered: <strong className={pollDebug.registered_events?.length ? "text-primary-600" : "text-danger-600"}>{pollDebug.registered_events?.length || 0}</strong> {pollDebug.registered_events?.join(", ")}</p>
                  <p>History replay triggered: <strong>{String(!!pollDebug.export_triggered)}</strong></p>
                  <p>Delivery statuses updated: <strong>{pollDebug.outgoing_updated || 0}</strong></p>
                  <p>Outgoing checked: {pollDebug.total_api_messages || 0}</p>
                  {pollDebug.webhook_url && <p className="mt-1 text-gray-500 break-all text-[11px]">Webhook: <code className="bg-white dark:bg-gray-900 px-1 rounded">{pollDebug.webhook_url}</code></p>}
                  {pollDebug.error && <p className="text-danger-600 mt-1">Error: {pollDebug.error}</p>}
                  {pollDebug.problems?.length > 0 && (
                    <div className="mt-2 bg-danger-50 dark:bg-danger-900/20 border border-danger-200 dark:border-danger-800 rounded p-2">
                      <p className="font-medium text-danger-700 dark:text-danger-400 mb-1">Needs attention</p>
                      <ul className="list-disc ml-4 space-y-1">{pollDebug.problems.map((p: string, i: number) => (<li key={i}>{p}</li>))}</ul>
                    </div>
                  )}
                  {pollDebug.note && <p className="text-primary-600 mt-1">{pollDebug.note}</p>}
                </div>
              </div>
              <div className="mt-4 text-center">
                <button onClick={doPollDebug} className="text-[13px] text-primary-600 hover:underline">🔍 Full diagnostic</button>
                <p className="text-[13px] text-gray-500 mt-2">Select a chat on the left</p>
              </div>
            </div>
          ) : selected ? (
            <>
              {/* Chat header (59px) */}
              <div className="h-[59px] bg-gray-100 dark:bg-gray-800 flex items-center justify-between px-2 md:px-4 border-l border-gray-200 dark:border-gray-800 flex-shrink-0 relative z-20">
                <div className="flex items-center gap-2 min-w-0 flex-1">
                  <button className="md:hidden w-9 h-9 -ml-1 flex items-center justify-center rounded-full hover:bg-black/5 dark:hover:bg-white/10 text-gray-600 dark:text-gray-400" onClick={() => { setSelected(null); setShowActions(false); }}>
                    <ChevronLeft size={22} />
                  </button>
                  <div className={`w-10 h-10 rounded-full flex items-center justify-center text-white font-medium text-[15px] flex-shrink-0 ${avatarColor(selected.contact_name || selected.contact_phone)}`}>
                    {initials(selected.contact_name || "", selected.contact_phone || "")}
                  </div>
                  <div className="min-w-0 flex-1">
                    <h2 className="font-semibold text-[16px] leading-5 text-gray-900 dark:text-gray-200 truncate">
                      {selected.contact_name || selected.contact_phone}
                    </h2>
                    <p className="text-[12px] text-gray-500 dark:text-gray-400 truncate flex items-center gap-1.5">
                      {selected.contact_phone}
                      {selected.unread_count > 0 && <span className="bg-primary-600 text-white text-[10px] px-1.5 py-px rounded-full ml-1">{selected.unread_count} new</span>}
                      {selected.status === "interested" && <span className="text-warning-400">★ interested</span>}
                      {/* Where this lead came from, right in the chat header. */}
                      {selected.campaign && (
                        <CampaignChip
                          campaign={selected.campaign}
                          size="xs"
                          onClick={() => applyCampaignFilter(selected.campaign)}
                          title={`This lead came from "${selected.campaign.name}" — tap to see every lead from it`}
                        />
                      )}
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-0.5">
                  {/* GSM call via your phone (CallGate) */}
                  <button
                    onClick={() => {
                      const cid = selected.contact?.id || selected.contact_id;
                      startCall(cid ? { contact_id: cid } : { phone_number: selected.contact_phone });
                    }}
                    title={`Call ${selected.contact_phone} (via your phone)`}
                    className="flex w-10 h-10 items-center justify-center rounded-full hover:bg-black/5 dark:hover:bg-white/10 text-primary-600"
                  ><Phone size={19} /></button>
                  {/* WhatsApp video/voice call entry */}
                  <button
                    onClick={() => openWhatsappForCall(selected.contact_phone, selected.contact_name)}
                    title="WhatsApp call (opens WhatsApp)"
                    className="flex w-10 h-10 items-center justify-center rounded-full hover:bg-black/5 dark:hover:bg-white/10 text-gray-600 dark:text-gray-400"
                  ><Video size={19} /></button>
                  {/* WhatsApp chat */}
                  <button
                    onClick={() => openWhatsappChat(selected.contact_phone)}
                    title="Open WhatsApp chat"
                    className="flex w-10 h-10 items-center justify-center rounded-full hover:bg-black/5 dark:hover:bg-white/10 text-primary-600"
                  ><MessageCircle size={19} /></button>
                  {/* Website, when the contact has one */}
                  {websiteUrl(selected.contact?.website) && (
                    <button
                      onClick={() => openWebsite(selected.contact.website)}
                      title="Open website"
                      className="hidden sm:flex w-10 h-10 items-center justify-center rounded-full hover:bg-black/5 dark:hover:bg-white/10 text-primary-600"
                    ><Globe size={19} /></button>
                  )}
                  <button
                    onClick={() => setShowBooking(true)}
                    title="Book a meeting"
                    className="w-10 h-10 flex items-center justify-center rounded-full hover:bg-black/5 dark:hover:bg-white/10 text-primary-600"
                  >
                    <CalendarPlus size={19} />
                  </button>
                  <div className="relative">
                    <button onClick={() => setShowActions(!showActions)} className="w-10 h-10 flex items-center justify-center rounded-full hover:bg-black/5 dark:hover:bg-white/10 text-gray-600 dark:text-gray-400">
                      <MoreVertical size={19} />
                    </button>
                    {showActions && (
                      <>
                        <div className="fixed inset-0 z-40" onClick={() => setShowActions(false)} />
                        <div className="absolute right-0 top-11 bg-white dark:bg-gray-700 rounded-lg shadow-xl border border-gray-200 dark:border-gray-800 py-1.5 w-52 z-50">
                          <button onClick={() => { setShowBooking(true); setShowActions(false); }} className="w-full text-left px-3 py-2.5 text-[14px] text-gray-900 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-800 flex items-center gap-2.5"><CalendarPlus size={16} className="text-primary-600" /> Book a meeting</button>
                          <button onClick={() => { markAs("interested"); setShowActions(false); }} className="w-full text-left px-3 py-2.5 text-[14px] text-gray-900 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-800 flex items-center gap-2.5"><Star size={16} className="text-warning-400" /> Mark interested</button>
                          <button onClick={() => { markAs("not-interested"); setShowActions(false); }} className="w-full text-left px-3 py-2.5 text-[14px] text-gray-900 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-800 flex items-center gap-2.5"><ThumbsDown size={16} className="text-gray-500" /> Not interested</button>
                          <button onClick={() => { markAs("close"); setShowActions(false); }} className="w-full text-left px-3 py-2.5 text-[14px] text-gray-900 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-800 flex items-center gap-2.5"><Archive size={16} className="text-gray-500" /> Close chat</button>
                          <div className="border-t border-gray-100 dark:border-gray-800 my-1" />
                          <button onClick={() => { setShowInfo(!showInfo); setShowActions(false); }} className="w-full text-left px-3 py-2.5 text-[14px] text-gray-900 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-800">Contact info</button>
                        </div>
                      </>
                    )}
                  </div>
                </div>
              </div>

              {showInfo && selected?.contact && (
                <div className="px-4 py-3 bg-warning-100 dark:bg-gray-800 border-b border-warning-200 dark:border-gray-700 text-[13px] space-y-1 z-10">
                  {selected.contact.business_name && <p><strong>Business:</strong> {selected.contact.business_name}</p>}
                  {selected.contact.city && <p><strong>Location:</strong> {selected.contact.city}{selected.contact.state ? `, ${selected.contact.state}` : ""}</p>}
                  <p><strong>Phone:</strong> {selected.contact.phone_number}</p>
                  {selected.contact.email && <p><strong>Email:</strong> {selected.contact.email}</p>}
                  {Object.entries(selected.contact.custom_fields || {}).map(([key, value]) => (
                    <p key={key}>
                      <strong>{key.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase())}:</strong>{" "}
                      {String(value)}
                    </p>
                  ))}
                  <div className="flex flex-wrap gap-2 pt-2">
                    <button
                      onClick={() => {
                        const cid = selected.contact?.id || selected.contact_id;
                        startCall(cid ? { contact_id: cid } : { phone_number: selected.contact_phone });
                      }}
                      className="flex items-center gap-1.5 bg-primary-600 text-white text-[12px] font-semibold px-3 py-1.5 rounded-full"
                    ><Phone size={13} /> Call</button>
                    <button
                      onClick={() => openWhatsappChat(selected.contact_phone)}
                      className="flex items-center gap-1.5 bg-primary-600 text-white text-[12px] font-semibold px-3 py-1.5 rounded-full"
                    ><MessageCircle size={13} /> WhatsApp</button>
                    <button
                      onClick={() => openWhatsappForCall(selected.contact_phone, selected.contact_name)}
                      className="flex items-center gap-1.5 bg-primary-700 text-white text-[12px] font-semibold px-3 py-1.5 rounded-full"
                    ><Video size={13} /> WhatsApp call</button>
                    {websiteUrl(selected.contact.website) && (
                      <button
                        onClick={() => openWebsite(selected.contact.website)}
                        className="flex items-center gap-1.5 bg-primary-600 text-white text-[12px] font-semibold px-3 py-1.5 rounded-full"
                      ><Globe size={13} /> Website</button>
                    )}
                  </div>
                </div>
              )}
              {showInfo && (
                <div className="px-4 py-3 bg-white dark:bg-gray-900 border-b border-gray-200 dark:border-gray-800 z-10">
                  <div className="flex items-center justify-between mb-1.5">
                    <p className="text-[13px] font-semibold text-gray-900 dark:text-gray-200">Upcoming meetings</p>
                    <button
                      onClick={() => setShowBooking(true)}
                      className="text-[12px] font-medium text-primary-600 hover:underline flex items-center gap-1"
                    >
                      <CalendarPlus size={13} /> Book
                    </button>
                  </div>
                  {contactMeetings.length === 0 ? (
                    <p className="text-[12px] text-gray-500 dark:text-gray-400">None booked yet.</p>
                  ) : (
                    <div className="space-y-1">
                      {contactMeetings.map(m => (
                        <button
                          key={m.id}
                          onClick={() => setEditingMeetingId(m.id)}
                          className="w-full text-left text-[12px] px-2.5 py-1.5 rounded-lg bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 flex items-center justify-between gap-2"
                        >
                          <span className="font-medium text-gray-900 dark:text-gray-200 truncate">{m.title}</span>
                          <span className="text-gray-500 dark:text-gray-400 flex-shrink-0">
                            {new Date(m.starts_at).toLocaleDateString([], { day: "numeric", month: "short" })}{" "}
                            {new Date(m.starts_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                          </span>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* Messages area — WhatsApp wallpaper */}
              <div
                className="flex-1 overflow-y-auto wa-scrollbar px-2 md:px-[6%] lg:px-[9%] py-3 relative"
                onScroll={onChatScroll}
                style={{ backgroundColor: "#eff3f9", backgroundImage: CHAT_BG }}
              >
                <div className="absolute inset-0 bg-gray-950 opacity-0 dark:opacity-100 pointer-events-none" />
                <div className="relative z-10 flex flex-col">
                  {messages.length === 0 ? (
                    <div className="flex justify-center my-10">
                      <div className="bg-white dark:bg-gray-800 text-gray-600 dark:text-gray-400 text-[12.5px] px-3 py-2 rounded-lg shadow-sm max-w-[85%] text-center">
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
                        ? "bg-primary-100 dark:bg-primary-800 rounded-tr-none text-gray-900 dark:text-gray-200"
                        : "bg-white dark:bg-gray-800 rounded-tl-none text-gray-900 dark:text-gray-200";
                    return (
                      <div key={m.id}>
                        {showDate && (
                          <div className="flex justify-center my-2.5">
                            <span className="bg-white dark:bg-gray-800 text-gray-600 dark:text-gray-400 text-[12.5px] px-3 py-1 rounded-lg shadow-sm">
                              {dayLabel(m.created_at)}
                            </span>
                          </div>
                        )}
                        <div className={`flex ${isOut ? "justify-end" : "justify-start"} ${g.isFirst ? "mt-2" : "mt-[2px]"} ${i === 0 ? "mt-0" : ""}`}>
                          <div className={`relative max-w-[82%] sm:max-w-[65%] px-2 pt-1.5 pb-1 rounded-lg shadow-sm text-[14.2px] leading-[19px] break-words ${bubble}`}>
                            {g.isLast && (
                              <span
                                className={`absolute top-0 w-3 h-3 ${isOut ? "right-[-6px] bg-primary-100 dark:bg-primary-800" : "left-[-6px] bg-white dark:bg-gray-800"}`}
                                style={{ clipPath: isOut ? "polygon(0 0, 100% 0, 0 100%)" : "polygon(100% 0, 0 0, 100% 100%)" }}
                              />
                            )}
                            <p className="whitespace-pre-wrap break-words pr-6">{m.body}</p>
                            {isFailed && m.last_error && (
                              <p className="text-[11px] mt-1 text-danger-600 dark:text-danger-300 bg-danger-50 dark:bg-danger-900/20 px-1.5 py-0.5 rounded">⚠ {m.last_error}</p>
                            )}
                            {!isOut && (
                              <div className="text-[10px] mt-1 flex flex-wrap items-center gap-1 text-gray-600 dark:text-gray-400">
                                {m.ai_sentiment && (
                                  <span className={`px-1.5 py-0.5 rounded-full ${m.ai_sentiment === "positive" ? "bg-primary-100 text-primary-700" : m.ai_sentiment === "negative" ? "bg-danger-100 text-danger-700" : "bg-gray-100 text-gray-600"}`}>
                                    AI: {m.ai_sentiment}{m.ai_intent && m.ai_intent !== "general" ? ` · ${m.ai_intent.replace(/_/g, " ")}` : ""}
                                  </span>
                                )}
                                <button
                                  type="button"
                                  onClick={() => rateReply(m.id, "good")}
                                  aria-label="Mark as good reply"
                                  title="Good reply"
                                  className={`inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-full border ${m.ai_sentiment === "positive" ? "bg-primary-700 text-white border-primary-700" : "border-gray-300 hover:bg-primary-100"}`}
                                >
                                  <ThumbsUp size={11} /> Good
                                </button>
                                <button
                                  type="button"
                                  onClick={() => rateReply(m.id, "bad")}
                                  aria-label="Mark as bad reply and opt out"
                                  title="Bad reply — opt out lead"
                                  className={`inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-full border ${m.ai_sentiment === "negative" ? "bg-danger-700 text-white border-danger-700" : "border-gray-300 hover:bg-danger-100"}`}
                                >
                                  <ThumbsDown size={11} /> Bad
                                </button>
                              </div>
                            )}
                            {g.isLast && (
                              <div className={`flex items-center gap-1 justify-end mt-0.5 text-[11px] select-none ${isOut ? "text-gray-500 dark:text-gray-400" : "text-gray-500 dark:text-gray-400"}`}>
                                <span>{formatTime(m.created_at)}</span>
                                {isOut && (
                                  <span className="flex items-center">
                                    {isFailed ? <span className="text-danger-500">⚠ failed</span>
                                      : isDelivered ? <CheckCheck size={14} className="text-primary-400" />
                                      : isSent ? <CheckCheck size={14} className="text-gray-400" />
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

              {/* Template picker: selecting one inserts a contact-personalized,
                  editable preview into the composer. */}
              <div className="bg-gray-100 dark:bg-gray-800 px-2 md:px-4 pt-2 flex items-center gap-2 flex-shrink-0 border-t border-gray-300 dark:border-gray-700">
                <FileText size={16} className="text-primary-600 flex-shrink-0" />
                <label htmlFor="reply-template" className="sr-only">Reply with a template</label>
                <select
                  id="reply-template"
                  aria-label="Reply with a template"
                  value={selectedTemplateId}
                  disabled={templateLoading}
                  onChange={e => chooseTemplate(e.target.value)}
                  className="min-w-0 flex-1 sm:max-w-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-200 rounded-lg px-3 py-2 text-[13px] outline-none border border-transparent focus:border-primary-600 disabled:opacity-60"
                >
                  <option value="">{templateLoading ? "Personalizing template…" : "Choose a reply template…"}</option>
                  {templates.map(template => (
                    <option key={template.id} value={template.id}>
                      {template.name}{template.category ? ` · ${template.category}` : ""}
                    </option>
                  ))}
                </select>
                {/* Insert a shortcode into the reply at the cursor. */}
                <ShortcodePicker
                  targetRef={taRef}
                  value={replyText}
                  onChange={setReplyText}
                  label="Variable"
                  className="flex-shrink-0"
                />
                {selectedTemplateId && !templateLoading && (
                  <button
                    type="button"
                    onClick={() => setSelectedTemplateId("")}
                    className="text-[12px] text-gray-500 hover:text-gray-900 dark:hover:text-white px-2"
                    title="Keep the text but unlink the template"
                  >
                    Unlink
                  </button>
                )}
              </div>

              {/* Composer — pinned above the mobile keyboard via 100dvh shell */}
              <div className="bg-gray-100 dark:bg-gray-800 px-2 md:px-4 py-2 flex items-end gap-1.5 flex-shrink-0 safe-bottom">
                <button className="hidden sm:flex w-10 h-10 items-center justify-center rounded-full text-gray-600 dark:text-gray-400 hover:bg-black/5 dark:hover:bg-white/10 flex-shrink-0"><Smile size={22} /></button>
                <button className="hidden sm:flex w-10 h-10 items-center justify-center rounded-full text-gray-600 dark:text-gray-400 hover:bg-black/5 dark:hover:bg-white/10 flex-shrink-0"><Paperclip size={20} /></button>
                <div className="flex-1 bg-white dark:bg-gray-700 rounded-[8px] flex items-end gap-2 px-2.5 py-1 shadow-sm min-h-[42px]">
                  <textarea
                    ref={taRef}
                    className="flex-1 bg-transparent border-none outline-none resize-none py-[9px] text-[15px] leading-5 placeholder:text-gray-500 dark:placeholder:text-gray-400 text-gray-900 dark:text-gray-200 max-h-[120px]"
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
                  className={`w-11 h-11 rounded-full flex items-center justify-center flex-shrink-0 shadow-sm transition-colors ${replyText.trim() ? "bg-primary-600 hover:bg-primary-500 text-white" : "bg-primary-600 text-white opacity-70"}`}
                >
                  {sending
                    ? <span className="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                    : replyText.trim() ? <Send size={18} className="ml-0.5" /> : <Mic size={19} />}
                </button>
              </div>
            </>
          ) : (
            /* Empty state — WhatsApp Web's iconic landing screen */
            <div className="flex-1 flex flex-col items-center justify-center bg-gray-100 dark:bg-gray-800 p-6">
              <div className="w-[200px] h-[200px] lg:w-[303px] lg:h-[303px] mx-auto mb-6 rounded-full bg-gray-100 dark:bg-gray-800 flex items-center justify-center border border-gray-200 dark:border-gray-800 shadow-sm">
                <MessageCircle size={110} className="text-gray-600 dark:text-gray-400 opacity-20" />
              </div>
              <h3 className="text-[28px] font-light text-gray-600 dark:text-gray-200 tracking-tight">SMS SENDER</h3>
              <p className="text-[13px] text-gray-500 dark:text-gray-400 mt-2 leading-5 text-center max-w-sm">
                Select a chat to start messaging. Your messages are synced from your phone via SMS-Gate.
              </p>
              <div className="mt-6 flex items-center gap-2">
                <button onClick={doPoll} disabled={polling}
                  className="flex items-center gap-2 px-5 py-2.5 rounded-full bg-primary-600 text-white text-[14px] font-medium hover:bg-primary-500 transition-colors disabled:opacity-70">
                  <span className={`text-[16px] ${polling ? "animate-spin inline-block" : ""}`}>↻</span> Sync from phone
                </button>
              </div>
              <p className="text-[12px] text-gray-500 dark:text-gray-400 mt-6 flex items-center justify-center gap-1">
                🔒 End-to-end synced with your device
              </p>
            </div>
          )}
        </div>
      </div>
      {showBooking && selected && (
        <MeetingModal
          initial={{ contactIds: selected.contact_id ? [selected.contact_id] : [], conversationId: selected.id }}
          onClose={() => setShowBooking(false)}
          onSaved={(m) => {
            setShowBooking(false);
            if ((m as Meeting).starts_at) setContactMeetings(prev => [...prev, m as Meeting]);
          }}
        />
      )}
      {editingMeetingId !== null && (
        <MeetingModal
          meetingId={editingMeetingId}
          onClose={() => setEditingMeetingId(null)}
          onSaved={() => {
            setEditingMeetingId(null);
            if (selected?.contact_id) {
              api.get("/calendar/upcoming", { params: { contact_id: selected.contact_id, limit: 5 } })
                .then(({ data }) => setContactMeetings(data.items || []))
                .catch(() => {});
            }
          }}
        />
      )}
    </div>
  );
}
