import { useState, useEffect, useRef } from "react";
import api from "../api/client";
import toast from "react-hot-toast";
import { Send, Star, ThumbsDown, Archive, ChevronLeft, CheckCheck, MessageCircle, Bug, Search as SearchIcon, MoreVertical, Phone, Video, Paperclip, Smile, Mic } from "lucide-react";

// Helpers
const avatarColor = (name: string) => {
  const colors = [
    "bg-[#25D366]", "bg-[#128C7E]", "bg-[#075E54]", "bg-[#34B7F1]", "bg-[#FF8A65]", "bg-[#BA68C8]", "bg-[#4DB6AC]", "bg-[#FFB74D]",
  ];
  let h = 0; for (let i=0;i<name.length;i++) h = (h*31 + name.charCodeAt(i)) % colors.length;
  return colors[h];
};
const initials = (name: string, phone: string) => {
  if (name && name.trim()) {
    const parts = name.trim().split(/\s+/);
    if (parts.length >=2) return (parts[0][0]+parts[1][0]).toUpperCase();
    return name.slice(0,2).toUpperCase();
  }
  return (phone||"??").slice(-2);
};
const formatTime = (iso?: string) => {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString([], { hour:"2-digit", minute:"2-digit" });
  } catch { return ""; }
};
const formatListTime = (iso?: string) => {
  if (!iso) return "";
  const d = new Date(iso);
  const now = new Date();
  const diff = (now.getTime() - d.getTime())/1000/3600;
  if (diff < 24) return d.toLocaleTimeString([], { hour:"2-digit", minute:"2-digit" });
  if (diff < 24*7) return d.toLocaleDateString([], { weekday:"short" });
  return d.toLocaleDateString([], { day:"2-digit", month:"short" });
};

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
  const chatEndRef = useRef<HTMLDivElement>(null);
  const pollRef = useRef<any>(null);

  useEffect(() => { loadConvs(); }, [filter]);
  useEffect(() => { const t = setTimeout(loadConvs, 350); return () => clearTimeout(t); }, [search]);
  useEffect(() => { if (selected) loadMessages(selected.id); }, [selected]);
  useEffect(() => { chatEndRef.current?.scrollIntoView({behavior:"smooth"}); }, [messages]);
  useEffect(() => { pollRef.current = setInterval(() => { loadConvs(); if(selected) loadMessages(selected.id); }, 8000); return () => clearInterval(pollRef.current); }, [selected]);

  const filterRef = useRef(filter);
  const searchRef = useRef(search);
  useEffect(() => { filterRef.current = filter; }, [filter]);
  useEffect(() => { searchRef.current = search; }, [search]);

  const loadConvs = async () => {
    try {
      const params: any = { per_page: 500 };
      if (filterRef.current && filterRef.current !== "all") params.status = filterRef.current;
      if (searchRef.current.trim()) params.search = searchRef.current.trim();
      const { data } = await api.get("/inbox/conversations", { params });
      setConvs(data.items || []);
    } catch {} finally { setLoading(false); }
  };

  const loadMessages = async (convId: number) => {
    try { const { data } = await api.get(`/inbox/conversations/${convId}`); setMessages(data.messages || []); setSelected((s:any)=> s && s.id===convId ? { ...s, status: data.status } : s); } catch {}
  };

  const sendReply = async () => {
    if (!replyText.trim() || !selected) return; setSending(true);
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
      setSelected((s:any) => s ? { ...s, status: data.status ?? s.status } : s);
      toast.success(status==="interested"? "Marked interested" : status==="not-interested"? "Marked not interested" : status==="close"? "Closed" : "Updated");
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
      else if (data.outgoing_updated > 0) toast.success(`${data.outgoing_updated} delivery status${data.outgoing_updated>1?"es":""} updated`);
      else if (data.export_triggered) toast.success("Phone is replaying recent messages — they'll appear shortly");
      else toast("Inbox synced", { icon: "✅" });
      [3000, 9000].forEach((ms) => setTimeout(() => { loadConvs(); if (selected) loadMessages(selected.id); }, ms));
    } catch(err:any) { toast.error(err.response?.data?.detail || "Sync failed"); setPollDebug({error: err.message}); }
    finally { setPolling(false); }
  };

  const doPollDebug = async () => {
    setDebugLoading(true); setDebugData(null);
    try {
      const { data } = await api.post("/inbox/poll-debug");
      setDebugData(data);
      if (data.issues?.length) toast.error(`${data.issues.length} problem(s) found`);
      else toast.success("Receive path healthy");
    } catch(err:any) { toast.error("Debug failed"); setDebugData({error:err.message}); }
    finally { setDebugLoading(false); }
  };

  const filters = [
    {v:"all",l:"All"},
    {v:"unread",l:"Unread"},
    {v:"interested",l:"Interested"},
    {v:"closed",l:"Closed"},
    {v:"failed",l:"Failed"},
  ];

  const unreadCount = convs.filter(c=> (c.unread_count>0) || c.status==="unread").length;

  return (
    <div className="flex flex-col h-[calc(100vh-4rem)] lg:h-[calc(100vh-5rem)] -m-4 lg:-m-6 bg-[#111b21] lg:bg-transparent overflow-hidden">
      {/* WhatsApp Web container - desktop card */}
      <div className="flex flex-1 overflow-hidden bg-white dark:bg-[#111b21] lg:rounded-lg lg:shadow-lg lg:m-2 lg:border lg:border-gray-200 lg:dark:border-gray-700">
        {/* LEFT - Conversation List */}
        <div className={`${selected ? "hidden md:flex" : "flex"} w-full md:w-[380px] lg:w-[420px] flex-shrink-0 flex-col border-r border-gray-200 dark:border-gray-700 bg-white dark:bg-[#111b21]`}>
          {/* Header */}
          <div className="h-[59px] bg-[#f0f2f5] dark:bg-[#202c33] flex items-center justify-between px-3 lg:px-4 flex-shrink-0">
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 rounded-full bg-[#00a884] flex items-center justify-center text-white font-bold text-sm">WA</div>
              <span className="font-semibold text-[14px] text-[#111b21] dark:text-[#e9edef] hidden sm:inline">Messaging</span>
            </div>
            <div className="flex items-center gap-1">
              <button onClick={doPoll} disabled={polling} className="w-9 h-9 flex items-center justify-center rounded-full hover:bg-black/5 dark:hover:bg-white/10 text-[#54656f] dark:text-[#aebac1]">
                <span className="text-[18px]">{polling ? "⏳" : "↻"}</span>
              </button>
              <button onClick={doPollDebug} disabled={debugLoading} className="w-9 h-9 flex items-center justify-center rounded-full hover:bg-black/5 dark:hover:bg-white/10 text-[#54656f] dark:text-[#aebac1]">
                <Bug size={18}/>
              </button>
              <button className="w-9 h-9 flex items-center justify-center rounded-full hover:bg-black/5 dark:hover:bg-white/10 text-[#54656f] dark:text-[#aebac1] hidden md:flex">
                <MoreVertical size={18}/>
              </button>
            </div>
          </div>

          {/* Search */}
          <div className="p-2 lg:p-3 bg-white dark:bg-[#111b21] border-b border-gray-100 dark:border-[#222d34]">
            <div className="relative">
              <SearchIcon size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#54656f] dark:text-[#8696a0]"/>
              <input
                className="w-full pl-9 pr-3 py-[8px] bg-[#f0f2f5] dark:bg-[#202c33] rounded-lg text-[14px] placeholder:text-[#667781] dark:placeholder:text-[#8696a0] focus:outline-none focus:bg-white dark:focus:bg-[#202c33] border border-transparent focus:border-transparent text-[#111b21] dark:text-[#e9edef]"
                placeholder="Search or start new chat"
                value={search}
                onChange={e=>setSearch(e.target.value)}
              />
            </div>
          </div>

          {/* Filter chips - WhatsApp style horizontally scrollable */}
          <div className="px-2 lg:px-3 py-2 bg-white dark:bg-[#111b21] flex gap-2 overflow-x-auto scrollbar-none border-b border-gray-100 dark:border-[#222d34] flex-shrink-0">
            {filters.map(f=>{
              const active = filter===f.v;
              return (
                <button
                  key={f.v}
                  onClick={()=>{setFilter(f.v);setSelected(null);}}
                  className={`whitespace-nowrap px-3 py-1.5 rounded-full text-[13px] font-medium transition-colors flex-shrink-0
                    ${active ? "bg-[#00a884] text-white shadow-sm" : "bg-[#f0f2f5] dark:bg-[#182533] text-[#54656f] dark:text-[#8696a0] hover:bg-[#e9edef] dark:hover:bg-[#202c33]"}`}
                >
                  {f.l} {f.v==="unread" && unreadCount>0 ? `· ${unreadCount}` : ""}
                </button>
              );
            })}
          </div>

          {/* Archive/banner muted */}
          <div className="px-3 py-1.5 bg-white dark:bg-[#111b21] flex items-center justify-between border-b border-gray-100 dark:border-[#222d34]">
            <span className="text-[12px] text-[#00a884] font-medium">📥 Sync from phone</span>
            <span className="text-[11px] text-[#667781] dark:text-[#8696a0]">{convs.length} chats</span>
          </div>

          {/* Conversation list */}
          <div className="flex-1 overflow-y-auto bg-white dark:bg-[#111b21] scrollbar-thin scrollbar-thumb-gray-300 dark:scrollbar-thumb-gray-600">
            {loading ? (
              [...Array(8)].map((_,i)=>(
                <div key={i} className="flex gap-3 p-3 border-b border-gray-50 dark:border-[#222d34] animate-pulse">
                  <div className="w-12 h-12 rounded-full bg-gray-200 dark:bg-gray-700 flex-shrink-0"/>
                  <div className="flex-1 space-y-2">
                    <div className="h-3 bg-gray-200 dark:bg-gray-700 rounded w-1/2"/>
                    <div className="h-2 bg-gray-100 dark:bg-gray-800 rounded w-3/4"/>
                  </div>
                </div>
              ))
            ) : convs.length===0 ? (
              <div className="text-center py-16 px-6">
                <MessageCircle size={48} className="mx-auto mb-3 text-[#00a884] opacity-40"/>
                <p className="text-[14px] font-medium text-[#111b21] dark:text-[#e9edef]">No conversations yet</p>
                <p className="text-[13px] text-[#667781] dark:text-[#8696a0] mt-1">Tap <b>↻ Sync</b> to pull messages from your phone. New SMS arrive automatically when webhooks are active.</p>
                <code className="mt-3 inline-block text-[10px] bg-[#f0f2f5] dark:bg-[#202c33] px-2 py-1 rounded text-[#54656f] dark:text-[#8696a0]">/api/v1/webhooks/smsgateway</code>
              </div>
            ) : convs.map(c => {
              const isUnread = (c.unread_count>0) || c.status==="unread";
              const isActive = selected?.id===c.id;
              const name = c.contact_name || c.contact_phone || "Unknown";
              const preview = c.last_message_preview || "No messages yet";
              return (
                <button
                  key={c.id}
                  onClick={()=>{setSelected(c);setShowInfo(false);setPollDebug(null);}}
                  className={`w-full flex gap-3 p-3 text-left hover:bg-[#f5f6f6] dark:hover:bg-[#202c33] border-b border-[#f0f2f5] dark:border-[#222d34] transition-colors relative ${isActive?"bg-[#f0f2f5] dark:bg-[#2a3942]":"bg-white dark:bg-[#111b21]"} ${isUnread?"":""}`}
                >
                  {/* Avatar */}
                  <div className={`w-[49px] h-[49px] rounded-full flex items-center justify-center text-white font-medium text-[15px] flex-shrink-0 ${avatarColor(name)}`}>
                    {initials(name, c.contact_phone)}
                  </div>
                  {/* Content */}
                  <div className="flex-1 min-w-0 flex flex-col justify-center">
                    <div className="flex items-baseline justify-between gap-2">
                      <span className={`truncate text-[16px] leading-5 ${isUnread?"font-semibold text-[#111b21] dark:text-[#e9edef]":"font-normal text-[#111b21] dark:text-[#e9edef]"}`}>{name}</span>
                      <span className={`text-[11px] flex-shrink-0 ml-2 ${isUnread?"text-[#00a884] font-medium":"text-[#667781] dark:text-[#8696a0]"}`}>{formatListTime(c.last_message_at)}</span>
                    </div>
                    <div className="flex items-center justify-between gap-2 mt-1">
                      <p className={`truncate text-[13px] leading-4 flex items-center gap-1 min-w-0 flex-1 ${isUnread?"text-[#111b21] dark:text-[#e9edef] font-medium":"text-[#667781] dark:text-[#8696a0]"}`}>
                        {/* status icon for last outgoing */}
                        {c.status==="read" && !isUnread && <CheckCheck size={14} className="text-[#53bdeb] flex-shrink-0"/>}
                        {c.status==="interested" && <Star size={12} className="text-[#f7c948] flex-shrink-0"/>}
                        <span className="truncate">{preview}</span>
                      </p>
                      <div className="flex items-center gap-1.5 flex-shrink-0">
                        {c.status==="interested" && !isUnread && <Star size={12} className="text-[#f7c948]"/>}
                        {isUnread ? (
                          <span className="bg-[#00a884] text-white text-[11px] font-medium min-w-[20px] h-[20px] px-1 flex items-center justify-center rounded-full">{c.unread_count || 1}</span>
                        ) : c.status==="closed" ? (
                          <Archive size={12} className="text-[#667781]"/>
                        ) : null}
                        {/* dropdown arrow on hover */}
                        <span className="hidden group-hover:block text-[#667781]">›</span>
                      </div>
                    </div>
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        {/* RIGHT - Chat */}
        <div className="flex-1 flex flex-col bg-[#efeae2] dark:bg-[#0b141a] relative overflow-hidden">
          {/* DEBUG VIEW */}
          {debugData ? (
            <div className="flex-1 p-4 overflow-y-auto bg-white dark:bg-[#111b21]">
              <div className="flex items-center justify-between mb-3">
                <h2 className="font-bold text-[16px] text-[#111b21] dark:text-[#e9edef]">🔍 Receive path diagnostic</h2>
                <button onClick={()=>setDebugData(null)} className="w-8 h-8 rounded-full bg-[#f0f2f5] dark:bg-[#202c33] flex items-center justify-center text-[#54656f]">✕</button>
              </div>
              {debugData.error ? (
                <p className="text-red-600 text-sm">Error: {debugData.error}</p>
              ) : (
                <div className="space-y-3 text-[13px]">
                  {debugData.issues?.length > 0 ? (
                    <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg p-3">
                      <p className="font-medium mb-1 text-red-700 dark:text-red-400">Problems blocking incoming SMS</p>
                      <ul className="list-disc ml-4 space-y-1 text-red-700 dark:text-red-300">
                        {debugData.issues.map((i:string,n:number)=>(<li key={n}>{i}</li>))}
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
                      {debugData.recent_webhook_events.map((e:any,i:number)=>(
                        <div key={i} className={`mb-1 p-2 rounded text-[12px] ${e.status==="error" ? "bg-red-50 dark:bg-red-900/20" : "bg-green-50 dark:bg-green-900/20"}`}>
                          <p><code>{e.event_type}</code> — {e.status} @ {e.at?.slice(11,19)}</p>
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
            <div className="flex-1 p-4 overflow-y-auto bg-white dark:bg-[#111b21]">
              <div className="bg-[#f0f2f5] dark:bg-[#202c33] rounded-lg p-3 text-[13px]">
                <p className="font-semibold mb-2">📊 Sync result</p>
                <div className="space-y-1">
                  <p>Device online: <strong className={pollDebug.device_online?"text-[#00a884]":"text-red-600"}>{String(!!pollDebug.device_online)}</strong></p>
                  <p>Events registered: <strong className={pollDebug.registered_events?.length?"text-[#00a884]":"text-red-600"}>{pollDebug.registered_events?.length || 0}</strong> {pollDebug.registered_events?.join(", ")}</p>
                  <p>History replay triggered: <strong>{String(!!pollDebug.export_triggered)}</strong></p>
                  <p>Delivery statuses updated: <strong>{pollDebug.outgoing_updated || 0}</strong></p>
                  <p>Outgoing checked: {pollDebug.total_api_messages || 0}</p>
                  {pollDebug.webhook_url && <p className="mt-1 text-[#667781] break-all text-[11px]">Webhook: <code className="bg-white dark:bg-[#111b21] px-1 rounded">{pollDebug.webhook_url}</code></p>}
                  {pollDebug.error && <p className="text-red-600 mt-1">Error: {pollDebug.error}</p>}
                  {pollDebug.problems?.length > 0 && (
                    <div className="mt-2 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded p-2">
                      <p className="font-medium text-red-700 dark:text-red-400 mb-1">Needs attention</p>
                      <ul className="list-disc ml-4 space-y-1">{pollDebug.problems.map((p:string,i:number)=>(<li key={i}>{p}</li>))}</ul>
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
              {/* Chat Header - WhatsApp style */}
              <div className="h-[59px] bg-[#f0f2f5] dark:bg-[#202c33] flex items-center justify-between px-3 lg:px-4 border-l border-gray-200 dark:border-[#222d34] flex-shrink-0">
                <div className="flex items-center gap-3 min-w-0 flex-1">
                  <button className="md:hidden w-8 h-8 flex items-center justify-center rounded-full hover:bg-black/5 dark:hover:bg-white/10 text-[#54656f] dark:text-[#aebac1]" onClick={()=>setSelected(null)}><ChevronLeft size={22}/></button>
                  <div className={`w-9 h-9 rounded-full flex items-center justify-center text-white font-medium text-sm flex-shrink-0 ${avatarColor(selected.contact_name||selected.contact_phone)}`}>
                    {initials(selected.contact_name||"", selected.contact_phone||"")}
                  </div>
                  <div className="min-w-0 flex-1">
                    <h2 className="font-semibold text-[15px] leading-none text-[#111b21] dark:text-[#e9edef] truncate">{selected.contact_name||selected.contact_phone}</h2>
                    <p className="text-[12px] text-[#667781] dark:text-[#8696a0] truncate flex items-center gap-1">
                      {selected.contact_phone}
                      {selected.unread_count>0 && <span className="bg-[#00a884] text-white text-[10px] px-1 rounded-full ml-1">{selected.unread_count} new</span>}
                      {selected.status==="interested" && <span className="text-[#f7c948]">★ interested</span>}
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-1">
                  <button className="hidden sm:flex w-9 h-9 items-center justify-center rounded-full hover:bg-black/5 dark:hover:bg-white/10 text-[#54656f] dark:text-[#aebac1]"><Video size={18}/></button>
                  <button className="hidden sm:flex w-9 h-9 items-center justify-center rounded-full hover:bg-black/5 dark:hover:bg-white/10 text-[#54656f] dark:text-[#aebac1]"><Phone size={18}/></button>
                  <div className="relative">
                    <button onClick={()=>setShowActions(!showActions)} className="w-9 h-9 flex items-center justify-center rounded-full hover:bg-black/5 dark:hover:bg-white/10 text-[#54656f] dark:text-[#aebac1]"><MoreVertical size={18}/></button>
                    {showActions && (
                      <div className="absolute right-0 top-10 bg-white dark:bg-[#233138] rounded-lg shadow-xl border border-gray-200 dark:border-[#222d34] py-2 w-48 z-50">
                        <button onClick={()=>{markAs("interested");setShowActions(false)}} className="w-full text-left px-3 py-2 text-[14px] hover:bg-[#f0f2f5] dark:hover:bg-[#182533] flex items-center gap-2"><Star size={14}/> Mark interested</button>
                        <button onClick={()=>{markAs("not-interested");setShowActions(false)}} className="w-full text-left px-3 py-2 text-[14px] hover:bg-[#f0f2f5] dark:hover:bg-[#182533] flex items-center gap-2"><ThumbsDown size={14}/> Not interested</button>
                        <button onClick={()=>{markAs("close");setShowActions(false)}} className="w-full text-left px-3 py-2 text-[14px] hover:bg-[#f0f2f5] dark:hover:bg-[#182533] flex items-center gap-2"><Archive size={14}/> Close chat</button>
                        <div className="border-t border-gray-100 dark:border-[#222d34] my-1"/>
                        <button onClick={()=>{setShowInfo(!showInfo);setShowActions(false)}} className="w-full text-left px-3 py-2 text-[14px] hover:bg-[#f0f2f5] dark:hover:bg-[#182533]">Contact info</button>
                      </div>
                    )}
                  </div>
                </div>
              </div>

              {showInfo && selected?.contact && (
                <div className="px-4 py-3 bg-[#fff8e1] dark:bg-[#182533] border-b border-[#ffe082] dark:border-[#2a3942] text-[13px] space-y-1">
                  {selected.contact.business_name&&<p><strong>Business:</strong> {selected.contact.business_name}</p>}
                  {selected.contact.city&&<p><strong>Location:</strong> {selected.contact.city}{selected.contact.state?`, ${selected.contact.state}`:""}</p>}
                  <p><strong>Phone:</strong> {selected.contact.phone_number}</p>
                  {selected.contact.email&&<p><strong>Email:</strong> {selected.contact.email}</p>}
                </div>
              )}

              {/* Messages area - WhatsApp bg */}
              <div className="flex-1 overflow-y-auto px-2 lg:px-[5%] py-4 space-y-1 scrollbar-thin scrollbar-thumb-gray-300 dark:scrollbar-thumb-gray-600 relative"
                   style={{
                     backgroundColor: "#efeae2",
                     backgroundImage: `url("data:image/svg+xml,%3Csvg width='100' height='100' viewBox='0 0 100 100' xmlns='http://www.w3.org/2000/svg'%3E%3Cg fill='%23d1d7db' fill-opacity='0.2'%3E%3Cpath d='M20 20h10v10H20zM50 50h10v10H50zM80 10h10v10H80zM10 80h10v10H10z'/%3E%3C/g%3E%3C/svg%3E")`,
                   }}>
                {/* dark mode overlay */}
                <div className="absolute inset-0 bg-[#0b141a] opacity-0 dark:opacity-100 pointer-events-none"/>
                <div className="relative z-10 space-y-1">
                  {/* Date separator */}
                  <div className="flex justify-center my-3">
                    <span className="bg-white dark:bg-[#182533] text-[#54656f] dark:text-[#8696a0] text-[12px] px-3 py-1 rounded-lg shadow-sm">
                      {new Date().toLocaleDateString([], { weekday:"long", day:"numeric", month:"long" })}
                    </span>
                  </div>
                  {messages.length===0 ? (
                    <div className="flex justify-center my-8">
                      <div className="bg-[#fff8c4] dark:bg-[#182533] text-[#54656f] dark:text-[#8696a0] text-[12px] px-3 py-2 rounded-lg shadow-sm max-w-[85%] text-center">
                        🔒 Messages are secured. This chat is with <strong>{selected.contact_name||selected.contact_phone}</strong>. Be respectful and avoid spam.
                      </div>
                    </div>
                  ) : messages.map(m => {
                    const isOut = m.direction==="outgoing";
                    const isFailed = isOut && (m.status==="failed"||m.status==="cancelled");
                    const isDelivered = m.status==="delivered";
                    const isSent = m.status==="sent";
                    return (
                      <div key={m.id} className={`flex ${isOut?"justify-end":"justify-start"}`}>
                        <div className={`relative max-w-[78%] lg:max-w-[65%] px-2.5 pt-1.5 pb-1 rounded-lg shadow-sm text-[14.2px] leading-[18px] break-words
                          ${isOut ? "bg-[#d9fdd3] dark:bg-[#005c4b] rounded-tr-none text-[#111b21] dark:text-[#e9edef]" : "bg-white dark:bg-[#202c33] rounded-tl-none text-[#111b21] dark:text-[#e9edef]"}`}>
                          {/* tail */}
                          <span className={`absolute top-0 w-3 h-3 ${isOut ? "right-[-6px] bg-[#d9fdd3] dark:bg-[#005c4b]" : "left-[-6px] bg-white dark:bg-[#202c33]"}`} style={{clipPath: isOut ? "polygon(0 0, 100% 0, 0 100%)" : "polygon(100% 0, 0 0, 100% 100%)"}}/>
                          <p className="whitespace-pre-wrap break-words pr-6">{m.body}</p>
                          {isFailed && m.last_error && <p className="text-[11px] mt-1 text-red-600 dark:text-red-300 bg-red-50 dark:bg-red-900/20 px-1.5 py-0.5 rounded">⚠ {m.last_error}</p>}
                          <div className={`flex items-center gap-1 justify-end mt-1 text-[11px] select-none ${isOut ? "text-[#667781] dark:text-[#8696a0]" : "text-[#667781] dark:text-[#8696a0]"}`}>
                            <span>{formatTime(m.created_at)}</span>
                            {isOut && (
                              <span className="flex items-center">
                                {isFailed ? <span className="text-red-500">⚠ failed</span> : isDelivered ? <CheckCheck size={13} className="text-[#53bdeb]"/> : isSent ? <CheckCheck size={13} className="text-[#667781]"/> : <span className="text-[10px]">✓</span>}
                              </span>
                            )}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                  <div ref={chatEndRef}/>
                </div>
              </div>

              {/* Input bar - WhatsApp style */}
              <div className="bg-[#f0f2f5] dark:bg-[#202c33] px-2 lg:px-3 py-[5px] flex items-end gap-2 flex-shrink-0">
                <button className="hidden sm:flex w-10 h-10 items-center justify-center rounded-full text-[#54656f] dark:text-[#8696a0] hover:bg-black/5 dark:hover:bg-white/10 flex-shrink-0"><Smile size={22}/></button>
                <button className="hidden sm:flex w-10 h-10 items-center justify-center rounded-full text-[#54656f] dark:text-[#8696a0] hover:bg-black/5 dark:hover:bg-white/10 flex-shrink-0"><Paperclip size={20}/></button>
                <div className="flex-1 bg-white dark:bg-[#2a3942] rounded-[8px] flex items-end gap-2 px-3 py-1.5 shadow-sm min-h-[42px]">
                  <textarea
                    className="flex-1 bg-transparent border-none outline-none resize-none py-2 text-[15px] leading-5 placeholder:text-[#667781] dark:placeholder:text-[#8696a0] text-[#111b21] dark:text-[#e9edef] max-h-24"
                    placeholder="Type a message"
                    rows={1}
                    value={replyText}
                    onChange={e=>setReplyText(e.target.value)}
                    onKeyDown={e=>{
                      if(e.key==="Enter" && !e.shiftKey){ e.preventDefault(); sendReply(); }
                    }}
                    style={{height: "auto"}}
                  />
                </div>
                <button
                  onClick={sendReply}
                  disabled={sending||!replyText.trim()}
                  className={`w-11 h-11 rounded-full flex items-center justify-center flex-shrink-0 shadow-sm transition-colors ${replyText.trim() ? "bg-[#00a884] hover:bg-[#06cf9c] text-white" : "bg-[#00a884] text-white opacity-60"}`}
                >
                  {sending ? <span className="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin"/> : replyText.trim() ? <Send size={18} className="ml-0.5"/> : <Mic size={18}/>}
                </button>
              </div>
            </>
          ) : (
            <div className="flex-1 flex items-center justify-center bg-[#f0f2f5] dark:bg-[#222e35] p-6">
              <div className="text-center max-w-sm">
                <div className="w-[303px] h-[303px] mx-auto mb-6 rounded-full bg-[#f0f2f5] dark:bg-[#182533] flex items-center justify-center border border-[#e9edef] dark:border-[#222d34] hidden lg:flex">
                  <MessageCircle size={120} className="text-[#41525d] dark:text-[#8696a0] opacity-20"/>
                </div>
                <h3 className="text-[22px] font-light text-[#41525d] dark:text-[#e9edef]">WhatsApp Web</h3>
                <p className="text-[13px] text-[#667781] dark:text-[#8696a0] mt-2 leading-5">
                  Select a chat to start messaging. Your messages are synced from your phone via SMS-Gate. <br/>Click <b>↻ Sync</b> to pull latest SMS.
                </p>
                <p className="text-[12px] text-[#667781] dark:text-[#8696a0] mt-6 flex items-center justify-center gap-1">
                  🔒 End-to-end synced with your device
                </p>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
