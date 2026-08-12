import { useState, useEffect } from "react";
import api from "../api/client";
import toast from "react-hot-toast";
import { Send, UserPlus, Search, X, Users, Phone, List, Clock, AlertCircle, CheckCircle2, Hourglass, RotateCcw, Trash2, Eye, Calendar, MessageSquare } from "lucide-react";

export default function SendPage() {
  const [mode, setMode] = useState<"contact" | "number" | "list">("contact");
  const [sendType, setSendType] = useState<"now" | "scheduled">("now");
  const [contacts, setContacts] = useState<any[]>([]);
  const [lists, setLists] = useState<any[]>([]);
  const [search, setSearch] = useState("");
  const [selectedContacts, setSelectedContacts] = useState<any[]>([]);
  const [phoneNumber, setPhoneNumber] = useState("");
  const [selectedListId, setSelectedListId] = useState("");
  const [message, setMessage] = useState("");
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [scheduleDate, setScheduleDate] = useState("");
  const [scheduleTime, setScheduleTime] = useState("09:00");
  const [activeTab, setActiveTab] = useState<"compose"|"scheduled"|"sent"|"failed">("compose");
  const [scheduled, setScheduled] = useState<any[]>([]);
  const [scheduledTotal, setScheduledTotal] = useState(0);
  const [history, setHistory] = useState<any[]>([]);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [failed, setFailed] = useState<any[]>([]);
  const [failedTotal, setFailedTotal] = useState(0);
  const [loadingSched, setLoadingSched] = useState(false);
  const [loadingHist, setLoadingHist] = useState(false);

  const charCount = message.length;
  const segmentCount = charCount <= 160 ? 1 : Math.ceil(charCount / 153);

  useEffect(() => {
    api.get("/contacts/", { params: { per_page: 100 } }).then(r => setContacts(r.data.items)).catch(()=>{});
    api.get("/lists/").then(r => setLists(r.data.items)).catch(()=>{});
    const now = new Date(); now.setMinutes(now.getMinutes() + 10);
    setScheduleDate(now.toISOString().split("T")[0]);
    setScheduleTime(now.toTimeString().split(" ")[0].substring(0, 5));
    loadScheduled();
    loadHistory();
    loadFailed();
    const iv = setInterval(()=>{ loadScheduled(); loadHistory(); loadFailed(); }, 15000);
    return ()=>clearInterval(iv);
  }, []);

  // reload when switching tabs
  useEffect(()=>{ if(activeTab==="scheduled") loadScheduled(); if(activeTab==="sent") loadHistory(); if(activeTab==="failed") loadFailed(); }, [activeTab]);

  const loadScheduled = async () => {
    setLoadingSched(true);
    try {
      const { data } = await api.get("/send/scheduled", { params: { status: "pending", per_page: 50 } });
      setScheduled(data.items||[]);
      setScheduledTotal(data.total||0);
    } catch {} finally { setLoadingSched(false); }
  };
  const loadHistory = async () => {
    setLoadingHist(true);
    try {
      const { data } = await api.get("/send/history", { params: { status: "sent", per_page: 50 } });
      // include delivered as sent for display
      // Actually fetch sent + delivered together via two calls? Simpler: fetch all and filter client for sent/delivered
      // We'll fetch sent, then if needed also delivered counts viaAPI - but API only returns one status, so we fetch all and filter
      // Workaround: fetch without status and filter
      if (data.items.length===0) {
        const all = await api.get("/send/history", { params: { per_page: 50 } });
        const sentOnly = (all.data.items||[]).filter((m:any)=>["sent","delivered"].includes(m.status));
        setHistory(sentOnly.slice(0,50));
        setHistoryTotal(sentOnly.length);
      } else {
        setHistory(data.items||[]);
        setHistoryTotal(data.total||0);
        // also try to get delivered
        try {
          const d = await api.get("/send/history", { params: { status: "delivered", per_page: 50 } });
          if (d.data.items?.length) {
            // merge
            const merged = [...data.items, ...d.data.items].sort((a:any,b:any)=> new Date(b.created_at).getTime() - new Date(a.created_at).getTime()).slice(0,50);
            setHistory(merged);
          }
        } catch {}
      }
    } catch {} finally { setLoadingHist(false); }
  };
  const loadFailed = async () => {
    try {
      const { data } = await api.get("/send/history", { params: { status: "failed", per_page: 50 } });
      setFailed(data.items||[]);
      setFailedTotal(data.total||0);
      // also include failed scheduled
      try {
        const s = await api.get("/send/scheduled", { params: { status: "failed", per_page: 50 } });
        if (s.data.items?.length) {
          // merge failed scheduled into failed list as combined view? keep separate but we show both in same tab via second section
          // We'll append scheduled failed as well with flag
          const schedFailed = s.data.items.map((x:any)=>({ ...x, _isScheduled: true }));
          // we will handle in render via separate section, but for count combine
        }
      } catch {}
    } catch {
      // fallback to scheduled failed
      try { const s = await api.get("/send/scheduled", { params: { status: "failed", per_page: 50 } }); setFailed(s.data.items||[]); setFailedTotal(s.data.total||0);} catch {}
    }
  };

  const filtered = contacts.filter(c => !search || (c.first_name||"").toLowerCase().includes(search.toLowerCase()) || (c.last_name||"").toLowerCase().includes(search.toLowerCase()) || (c.business_name||"").toLowerCase().includes(search.toLowerCase()) || c.phone_number.includes(search));
  const toggleContact = (c: any) => setSelectedContacts(prev => prev.find(x=>x.id===c.id) ? prev.filter(x=>x.id!==c.id) : [...prev,c]);

  const handleSend = async () => {
    if (!message.trim()) { toast.error("Type a message"); return; }
    setSending(true); setResult(null);
    try {
      if (sendType === "scheduled") {
        // Convert local datetime to UTC ISO - crucial: don't hardcode +01:00
        const local = new Date(`${scheduleDate}T${scheduleTime}:00`);
        const iso = local.toISOString(); // UTC
        const params: any = { body: message, schedule_at: iso };

        if (mode === "contact" && selectedContacts.length > 0) {
          let ok = 0;
          for (const c of selectedContacts) {
            const { data } = await api.post("/send/", null, { params: { ...params, contact_id: c.id } });
            if (data.scheduled) ok += data.count;
          }
          setResult({ scheduled: true, count: ok, schedule_at: iso });
          toast.success(`${ok} message(s) scheduled for ${local.toLocaleString()}`);
        } else if (mode === "number" && phoneNumber) {
          const { data } = await api.post("/send/", null, { params: { ...params, phone_number: phoneNumber } });
          if (data.scheduled) { setResult(data); toast.success(`Scheduled for ${local.toLocaleString()}`); }
        } else if (mode === "list" && selectedListId) {
          const { data } = await api.post("/send/", null, { params: { ...params, list_id: parseInt(selectedListId) } });
          if (data.scheduled) { setResult(data); toast.success(`${data.count} messages scheduled`); }
        } else { toast.error("Select recipients"); }
        loadScheduled();
        setActiveTab("scheduled");
        return;
      }

      // Send now
      if (mode === "contact" && selectedContacts.length > 0) {
        const allResults: any[] = [];
        for (const c of selectedContacts) {
          const { data } = await api.post("/send/", null, { params: { contact_id: c.id, body: message } });
          allResults.push(...data.results);
        }
        setResult({ sent: allResults.filter(r=>r.status!=="failed").length, failed: allResults.filter(r=>r.status==="failed").length, total: allResults.length, results: allResults, gateway_url: allResults[0]?.gateway_url });
        toast.success(`Sent to ${allResults.filter(r=>r.status!=="failed").length}/${allResults.length} contacts`);
        if (allResults.some(r=>r.status==="failed")) setActiveTab("failed");
        else setActiveTab("sent");
      } else if (mode === "number" && phoneNumber) {
        const { data } = await api.post("/send/", null, { params: { phone_number: phoneNumber, body: message } });
        setResult(data); toast.success(data.success ? "Message sent!" : "Send failed");
        setActiveTab(data.failed? "failed" : "sent");
      } else if (mode === "list" && selectedListId) {
        const { data } = await api.post("/send/", null, { params: { list_id: parseInt(selectedListId), body: message } });
        setResult(data); toast.success(`Sent ${data.sent} / ${data.total} messages`);
        setActiveTab(data.failed? "failed" : "sent");
      } else { toast.error("Select a contact, enter a number, or pick a list"); }
      loadHistory(); loadFailed();
    } catch (err: any) { toast.error(err.response?.data?.detail || "Send failed"); }
    finally { setSending(false); }
  };

  const cancelScheduled = async (id:number) => {
    if(!confirm("Cancel this scheduled message?")) return;
    try { await api.post(`/send/scheduled/${id}/cancel`); toast.success("Cancelled"); loadScheduled(); } catch { toast.error("Failed"); }
  };
  const retryFailed = async (id:number) => {
    try { await api.post(`/send/scheduled/${id}/retry`); toast.success("Retrying..."); loadScheduled(); loadFailed(); } catch(e:any){ toast.error(e.response?.data?.detail||"Failed"); }
  };
  const retryMessage = async (id:number) => {
    try { await api.post(`/send/messages/${id}/retry`); toast.success("Retrying..."); loadFailed(); loadHistory(); } catch(e:any){ toast.error(e.response?.data?.detail||"Failed"); }
  };

  return (
    <div className="space-y-3 pb-20 lg:pb-0">
      <div className="flex items-center justify-between">
        <h1 className="text-[22px] font-bold text-[#111b21] dark:text-white flex items-center gap-2">
          <span className="w-8 h-8 rounded-full bg-[#00a884] flex items-center justify-center text-white"><Send size={16}/></span>
          Send SMS
        </h1>
        <div className="flex items-center gap-1 text-xs">
          <span className="hidden sm:inline text-[#667781]">Gateway active</span>
          <span className="w-2 h-2 bg-[#00a884] rounded-full animate-pulse"/>
        </div>
      </div>

      {/* Top tabs like WhatsApp - Compose / Scheduled / Sent / Failed */}
      <div className="bg-white dark:bg-[#202c33] rounded-xl p-1.5 flex gap-1 shadow-sm border border-gray-100 dark:border-[#2a3942] overflow-x-auto">
        {[
          {id:"compose", label:"Compose", icon: Send, count: null},
          {id:"scheduled", label:"Scheduled", icon: Clock, count: scheduledTotal},
          {id:"sent", label:"Sent", icon: CheckCircle2, count: historyTotal},
          {id:"failed", label:"Failed", icon: AlertCircle, count: failedTotal},
        ].map(t=>(
          <button key={t.id} onClick={()=>setActiveTab(t.id as any)} className={`flex-1 min-w-[80px] flex items-center justify-center gap-1.5 py-2.5 rounded-full text-sm font-medium transition-colors ${activeTab===t.id?"bg-[#00a884] text-white shadow-sm":"text-[#54656f] dark:text-[#8696a0] hover:bg-[#f0f2f5] dark:hover:bg-[#111b21]"}`}>
            <t.icon size={14}/> {t.label} {t.count!==null && t.count>0 && <span className={`ml-1 px-1.5 py-0.5 rounded-full text-xs ${activeTab===t.id?"bg-white/20 text-white":"bg-[#fce8e6] text-[#c5221f] dark:bg-[#2a3942] dark:text-[#f15c6d]"}`}>{t.count}</span>}
          </button>
        ))}
      </div>

      {activeTab==="compose" && (
        <>
          <div className="flex gap-1.5 flex-wrap">
            {[{id:"contact" as const,label:"Pick Contact",icon:UserPlus},{id:"number" as const,label:"Enter Number",icon:Phone},{id:"list" as const,label:"Send to List",icon:List}].map(m=>(
              <button key={m.id} onClick={()=>{setMode(m.id);setResult(null)}} className={`flex items-center gap-1.5 px-3 py-2 rounded-full text-sm font-medium border ${mode===m.id?"bg-[#00a884] text-white border-[#00a884]":"bg-white dark:bg-[#202c33] text-[#54656f] dark:text-[#8696a0] border-gray-200 dark:border-[#2a3942]"}`}><m.icon size={14}/>{m.label}</button>
            ))}
            <span className="hidden sm:block w-px bg-gray-200 dark:bg-[#2a3942] mx-1 self-stretch my-1"/>
            <button onClick={()=>{setSendType("now");setResult(null)}} className={`flex items-center gap-1.5 px-3 py-2 rounded-full text-sm font-medium border ${sendType==="now"?"bg-[#008069] text-white border-[#008069]":"bg-white dark:bg-[#202c33] text-[#54656f] dark:text-[#8696a0] border-gray-200 dark:border-[#2a3942]"}`}><Send size={14}/>Now</button>
            <button onClick={()=>{setSendType("scheduled");setResult(null)}} className={`flex items-center gap-1.5 px-3 py-2 rounded-full text-sm font-medium border ${sendType==="scheduled"?"bg-[#ffad1f] text-white border-[#ffad1f]":"bg-white dark:bg-[#202c33] text-[#54656f] dark:text-[#8696a0] border-gray-200 dark:border-[#2a3942]"}`}><Clock size={14}/>Schedule</button>
          </div>

          {sendType === "scheduled" && (
            <div className="bg-[#fff8c4] dark:bg-[#2a3942] border border-[#ffecb3] dark:border-[#2a3942] rounded-xl p-3 flex flex-wrap items-end gap-3">
              <div><label className="text-xs font-medium text-[#54656f] dark:text-[#8696a0]">Date</label><input type="date" className="mt-1 px-3 py-2.5 bg-white dark:bg-[#111b21] rounded-xl text-sm w-full border-0 focus:ring-2 focus:ring-[#00a884]/20 outline-none" value={scheduleDate} onChange={e=>setScheduleDate(e.target.value)}/></div>
              <div><label className="text-xs font-medium text-[#54656f] dark:text-[#8696a0]">Time</label><input type="time" className="mt-1 px-3 py-2.5 bg-white dark:bg-[#111b21] rounded-xl text-sm w-full border-0 focus:ring-2 focus:ring-[#00a884]/20 outline-none" value={scheduleTime} onChange={e=>setScheduleTime(e.target.value)}/></div>
              <p className="text-xs text-[#667781] dark:text-[#8696a0] flex-1 min-w-[200px]">Will send automatically in your local timezone. You can cancel in <b>Scheduled</b> tab before it fires. Status moves to <b>Sent</b> or <b>Failed</b> afterwards.</p>
            </div>
          )}

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            <div className="bg-white dark:bg-[#202c33] rounded-xl p-4 space-y-3 shadow-sm border border-gray-100 dark:border-[#2a3942]">
              <h2 className="font-semibold text-[#111b21] dark:text-white flex items-center gap-2"><Users size={16} className="text-[#00a884]"/> Recipients</h2>
              {mode==="contact"&&(<><div className="relative"><Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#667781]"/><input className="w-full pl-9 pr-3 py-2.5 bg-[#f0f2f5] dark:bg-[#111b21] rounded-full text-sm placeholder:text-[#667781] focus:outline-none focus:ring-2 focus:ring-[#00a884]/20" placeholder="Search contacts… e.g. Chicken Republic" value={search} onChange={e=>setSearch(e.target.value)}/></div><div className="max-h-64 overflow-y-auto space-y-1 border border-gray-100 dark:border-[#2a3942] rounded-xl p-1.5 bg-[#f0f2f5]/50 dark:bg-[#111b21]/50">{filtered.slice(0,50).map(c=>(<label key={c.id} className={`flex items-center gap-2.5 p-2.5 rounded-xl cursor-pointer ${selectedContacts.find(x=>x.id===c.id)?"bg-[#d9fdd3] dark:bg-[#0a332c] border border-[#00a884]/20":"hover:bg-white dark:hover:bg-[#202c33] border border-transparent"}`}><input type="checkbox" checked={!!selectedContacts.find(x=>x.id===c.id)} onChange={()=>toggleContact(c)} className="rounded accent-[#00a884] w-4 h-4"/><span className="text-sm font-medium text-[#111b21] dark:text-white truncate">{c.first_name} {c.last_name} {c.business_name?`• ${c.business_name}`:""}</span><span className="text-xs text-[#667781] ml-auto font-mono">{c.phone_number}</span></label>))}{filtered.length===0&&<p className="text-xs text-center py-4 text-[#667781]">No contacts found</p>}</div>{selectedContacts.length>0&&(<div className="flex flex-wrap gap-1.5">{selectedContacts.map(c=>(<span key={c.id} className="bg-[#e7f3ff] dark:bg-[#182533] text-[#008069] dark:text-[#53bdeb] text-xs px-2.5 py-1 rounded-full flex items-center gap-1.5 font-medium">{c.first_name||c.phone_number}<button onClick={()=>toggleContact(c)} className="hover:text-red-500"><X size={12}/></button></span>))}</div>)}</>)}
              {mode==="number"&&(<div><label className="text-xs font-medium text-[#54656f]">Phone Number</label><input className="mt-1 w-full px-3 py-3 bg-[#f0f2f5] dark:bg-[#111b21] rounded-xl text-sm font-mono focus:outline-none focus:ring-2 focus:ring-[#00a884]/20" placeholder="08012345678" value={phoneNumber} onChange={e=>setPhoneNumber(e.target.value)}/><p className="text-xs text-[#667781] mt-1">International format auto-normalized to +234...</p></div>)}
              {mode==="list"&&(<div><label className="text-xs font-medium text-[#54656f]">Contact List</label><select className="mt-1 w-full px-3 py-3 bg-[#f0f2f5] dark:bg-[#111b21] rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-[#00a884]/20" value={selectedListId} onChange={e=>setSelectedListId(e.target.value)}><option value="">Select a list…</option>{lists.map((l:any)=>(<option key={l.id} value={l.id}>{l.name} ({l.contact_count} contacts)</option>))}</select><p className="text-xs text-[#667781] mt-1">All contacts in list get the same personalized message.</p></div>)}
            </div>
            <div className="bg-white dark:bg-[#202c33] rounded-xl p-4 space-y-3 shadow-sm border border-gray-100 dark:border-[#2a3942]">
              <h2 className="font-semibold text-[#111b21] dark:text-white flex items-center gap-2"><MessageSquare size={16} className="text-[#00a884]"/> Message</h2>
              <div className="relative">
                <textarea className="w-full px-3 py-3 bg-[#f0f2f5] dark:bg-[#111b21] rounded-xl text-[15px] placeholder:text-[#667781] focus:outline-none focus:ring-2 focus:ring-[#00a884]/20 resize-none" rows={6} placeholder="Hi {{first_name}}, craving something tasty? 🍗 At Chicken Republic… Reply STOP to opt out" value={message} onChange={e=>setMessage(e.target.value)} maxLength={1600}/>
                <span className="absolute bottom-2 right-2 text-[11px] bg-white dark:bg-[#2a3942] px-2 py-0.5 rounded-full text-[#667781] shadow-sm">{charCount} chars</span>
              </div>
              <div className="flex justify-between text-xs text-[#667781]">
                <span>{charCount} chars • {segmentCount} SMS • ~{segmentCount*4} NGN {segmentCount>1&& segmentCount>3?"• long message costs more":""}</span>
                <span className={charCount>140?"text-orange-500":""}>{charCount>160?"Multi-part":"Single"}</span>
              </div>
              <div className="flex flex-wrap gap-1.5">{["{{first_name}}","{{business_name}}","{{city}}","{{state}}"].map(v=>(<button key={v} type="button" onClick={()=>setMessage(message+" "+v)} className="text-xs px-2.5 py-1 bg-[#f0f2f5] dark:bg-[#111b21] hover:bg-[#e9edef] dark:hover:bg-[#2a3942] rounded-full font-mono text-[#54656f] dark:text-[#8696a0]">{v}</button>))}</div>
              <button onClick={handleSend} disabled={sending||!message.trim()} className={`w-full py-3 rounded-full font-semibold flex items-center justify-center gap-2 shadow-sm active:scale-[0.98] transition-transform ${sendType==="scheduled"?"bg-[#ffad1f] hover:bg-[#ff9f00] text-white":"bg-[#00a884] hover:bg-[#06cf9c] text-white"} disabled:opacity-50`}>
                {sending? <span className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"/> : sendType==="scheduled" ? <Calendar size={16}/> : <Send size={16}/>}
                {sending?"Sending...":sendType==="scheduled"?`Schedule SMS${mode==="list"?" to List":" • "+selectedContacts.length||""}`:`Send Now${mode==="list"?" to List":""}`}
              </button>
              {result&&(<div className="space-y-2 pt-1">
                {result.scheduled ? (
                  <div className="p-3 rounded-xl text-sm bg-[#fff8c4] border border-[#ffecb3] text-[#8d5100] flex gap-2"><Hourglass size={16} className="flex-shrink-0 mt-0.5"/><div><strong>{result.count} scheduled</strong> for {new Date(result.schedule_at).toLocaleString()} • Watch <b>Scheduled</b> tab for status (pending → sent/failed).</div></div>
                ) : (
                  <><div className={`p-3 rounded-xl text-sm flex gap-2 ${result.failed>0?"bg-[#fce8e6] border border-[#f5c6cb] text-[#8a1c1c]":"bg-[#d9fdd3] border border-[#a3e9a4] text-[#0a332c]"}`}>{result.failed>0?<AlertCircle size={16} className="flex-shrink-0 mt-0.5"/>:<CheckCircle2 size={16} className="flex-shrink-0 mt-0.5"/>}<div><strong>{result.sent} sent</strong>{result.failed>0&&<>, {result.failed} failed</>}{result.total>0&&<> • {result.total} total</>}{result.gateway_url&&<div className="text-xs mt-1 opacity-70">Gateway SIM {result.sim_used}</div>}</div></div>
                  {result.results&&result.results.slice(0,5).map((r:any,i:number)=>(<details key={i} className={`text-xs rounded-xl p-2.5 border ${r.status==="sent"?"bg-[#d9fdd3]/50 border-[#a3e9a4]":"bg-[#fce8e6] border-[#f5c6cb]"}`}><summary className="cursor-pointer font-medium flex items-center gap-2">{r.phone}: {r.status==="sent"?<span className="text-[#00a884]">✅ Sent</span>:<span className="text-[#c5221f]">❌ Failed</span>}{r.error&&<span className="text-[#c5221f] truncate">— {r.error}</span>}</summary><pre className="mt-2 whitespace-pre-wrap text-[10px] opacity-70 bg-white/50 p-2 rounded">{JSON.stringify(r.api_response,null,2)}</pre></details>))}</>
                )}
              </div>)}
            </div>
          </div>
        </>
      )}

      {activeTab==="scheduled" && (
        <div className="bg-white dark:bg-[#202c33] rounded-xl shadow-sm border border-gray-100 dark:border-[#2a3942] overflow-hidden">
          <div className="px-4 py-3 flex items-center justify-between border-b border-gray-100 dark:border-[#2a3942] bg-[#f0f2f5] dark:bg-[#111b21]">
            <h3 className="font-semibold text-[#111b21] dark:text-white flex items-center gap-2"><Clock size={16} className="text-[#ffad1f]"/> Scheduled Messages</h3>
            <button onClick={loadScheduled} className="text-xs bg-white dark:bg-[#2a3942] px-3 py-1.5 rounded-full border">↻ Refresh</button>
          </div>
          {loadingSched ? <div className="p-8 text-center"><div className="w-8 h-8 border-2 border-[#00a884] border-t-transparent rounded-full animate-spin mx-auto"/><p className="text-xs text-[#667781] mt-2">Loading…</p></div>
            : scheduled.length===0 ? (
              <div className="p-10 text-center">
                <Clock size={36} className="mx-auto text-[#ffad1f] opacity-40 mb-2"/>
                <p className="font-medium text-[#111b21] dark:text-white text-sm">No pending scheduled messages</p>
                <p className="text-xs text-[#667781] mt-1">Schedule from <b>Compose</b> tab — they appear here until sent. After firing they move to <b>Sent</b> or <b>Failed</b>.</p>
              </div>
            ) : (
              <div className="divide-y divide-gray-100 dark:divide-[#2a3942]">
                {scheduled.map((s:any)=>(
                  <div key={s.id} className="p-3 lg:p-4 flex gap-3">
                    <div className="w-10 h-10 rounded-full bg-[#ffecb3] flex items-center justify-center flex-shrink-0"><Clock size={16} className="text-[#8d5100]"/></div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-start justify-between gap-2">
                        <p className="font-medium text-[13px] text-[#111b21] dark:text-white truncate">{s.contact_name || s.phone || "Unknown"} • <span className="font-mono text-[#00a884]">{s.phone}</span></p>
                        <span className="text-[11px] bg-[#fff8c4] text-[#8d5100] px-2 py-0.5 rounded-full font-medium whitespace-nowrap flex items-center gap-1"><Hourglass size={10}/> pending</span>
                      </div>
                      <p className="text-[13px] text-[#111b21] dark:text-[#e9edef] mt-1 line-clamp-2 bg-[#f0f2f5] dark:bg-[#111b21] rounded-lg px-2.5 py-1.5">{s.body}</p>
                      <div className="flex items-center gap-2 mt-2 text-xs text-[#667781] flex-wrap">
                        <span className="flex items-center gap-1"><Calendar size={12}/> {new Date(s.schedule_at).toLocaleString()}</span>
                        <span>• SIM {s.sim_number}</span>
                        {s.list_id && <span>• list #{s.list_id}</span>}
                      </div>
                    </div>
                    <button onClick={()=>cancelScheduled(s.id)} className="self-center w-8 h-8 rounded-full bg-[#fce8e6] hover:bg-red-500 hover:text-white text-[#c5221f] flex items-center justify-center flex-shrink-0" title="Cancel"><Trash2 size={14}/></button>
                  </div>
                ))}
              </div>
            )}
          {/* also show recently sent/failed scheduled for context */}
          <div className="px-4 py-2 bg-[#f0f2f5] dark:bg-[#111b21] text-xs text-[#667781]">Total pending: {scheduledTotal} • Auto-refresh every 15s • Scheduled messages become Message rows on send so they appear in Inbox chat</div>
        </div>
      )}

      {activeTab==="sent" && (
        <div className="bg-white dark:bg-[#202c33] rounded-xl shadow-sm border border-gray-100 dark:border-[#2a3942] overflow-hidden">
          <div className="px-4 py-3 flex items-center justify-between border-b border-gray-100 dark:border-[#2a3942] bg-[#f0f2f5] dark:bg-[#111b21]">
            <h3 className="font-semibold text-[#111b21] dark:text-white flex items-center gap-2"><CheckCircle2 size={16} className="text-[#00a884]"/> Sent History</h3>
            <button onClick={loadHistory} className="text-xs bg-white dark:bg-[#2a3942] px-3 py-1.5 rounded-full border">↻ Refresh</button>
          </div>
          {loadingHist ? <div className="p-8 text-center"><div className="w-8 h-8 border-2 border-[#00a884] border-t-transparent rounded-full animate-spin mx-auto"/></div>
            : history.length===0 ? (
              <div className="p-10 text-center">
                <CheckCircle2 size={36} className="mx-auto text-[#00a884] opacity-40 mb-2"/>
                <p className="font-medium text-sm">No sent messages yet</p>
                <p className="text-xs text-[#667781] mt-1">Delivered messages appear here with double-blue checks in Inbox.</p>
              </div>
            ) : (
              <div className="divide-y divide-gray-100 dark:divide-[#2a3942]">
                {history.map((m:any)=>(
                  <div key={m.id} className="p-3 lg:p-4 flex gap-3">
                    <div className="w-10 h-10 rounded-full bg-[#d9fdd3] flex items-center justify-center flex-shrink-0"><CheckCircle2 size={16} className="text-[#00a884]"/></div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-start justify-between gap-2">
                        <p className="font-medium text-[13px] truncate">{m.contact_name || m.phone || "Unknown"} <span className="font-mono text-[#00a884]">{m.phone}</span></p>
                        <span className={`text-[11px] px-2 py-0.5 rounded-full font-medium ${m.status==="delivered"?"bg-[#d9fdd3] text-[#008069]":"bg-[#e7f3ff] text-[#0066cc]"}`}>{m.status}</span>
                      </div>
                      <p className="text-[13px] bg-[#f0f2f5] dark:bg-[#111b21] rounded-lg px-2.5 py-1.5 mt-1 line-clamp-2">{m.body}</p>
                      <p className="text-xs text-[#667781] mt-1.5">{new Date(m.created_at).toLocaleString()} {m.sent_at && `• sent ${new Date(m.sent_at).toLocaleTimeString()}`}</p>
                    </div>
                  </div>
                ))}
              </div>
            )}
          <div className="px-4 py-2 bg-[#f0f2f5] dark:bg-[#111b21] text-xs text-[#667781]">{historyTotal} sent • polling delivery receipts every 30s</div>
        </div>
      )}

      {activeTab==="failed" && (
        <div className="bg-white dark:bg-[#202c33] rounded-xl shadow-sm border border-gray-100 dark:border-[#2a3942] overflow-hidden">
          <div className="px-4 py-3 flex items-center justify-between border-b border-gray-100 dark:border-[#2a3942] bg-[#f0f2f5] dark:bg-[#111b21]">
            <h3 className="font-semibold text-[#c5221f] flex items-center gap-2"><AlertCircle size={16}/> Failed Messages</h3>
            <button onClick={loadFailed} className="text-xs bg-white dark:bg-[#2a3942] px-3 py-1.5 rounded-full border">↻ Refresh</button>
          </div>
          {failed.length===0 ? (
            <div className="p-10 text-center">
              <div className="w-12 h-12 rounded-full bg-[#d9fdd3] flex items-center justify-center mx-auto mb-2"><CheckCircle2 size={24} className="text-[#00a884]"/></div>
              <p className="font-medium text-sm text-[#00a884]">No failures — great!</p>
              <p className="text-xs text-[#667781] mt-1">Failed SMS (gateway rejected, device offline, bad number) appear here with error & retry. They also stay as red bubbles in Inbox.</p>
            </div>
          ) : (
            <div className="divide-y divide-gray-100 dark:divide-[#2a3942]">
              {failed.map((m:any)=>(
                <div key={m.id} className="p-3 lg:p-4 flex gap-3">
                  <div className="w-10 h-10 rounded-full bg-[#fce8e6] flex items-center justify-center flex-shrink-0"><AlertCircle size={16} className="text-[#c5221f]"/></div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-start justify-between gap-2">
                      <p className="font-medium text-[13px] truncate">{m.contact_name || m.phone || "Unknown"} <span className="font-mono text-[#c5221f]">{m.phone}</span></p>
                      <span className="text-[11px] bg-[#fce8e6] text-[#c5221f] px-2 py-0.5 rounded-full font-medium">failed</span>
                    </div>
                    <p className="text-[13px] bg-[#fce8e6]/50 dark:bg-[#2a3942] rounded-lg px-2.5 py-1.5 mt-1 line-clamp-2 border border-[#f5c6cb]/50">{m.body || m.body_preview}</p>
                    {m.error && <p className="text-xs text-[#c5221f] mt-1.5 bg-[#fce8e6] dark:bg-red-900/20 px-2 py-1 rounded">⚠ {m.error}</p>}
                    <p className="text-xs text-[#667781] mt-1">{m.created_at ? new Date(m.created_at).toLocaleString() : m.schedule_at ? new Date(m.schedule_at).toLocaleString():""}</p>
                  </div>
                  <div className="flex flex-col gap-1 self-center">
                    <button onClick={()=> m._isScheduled ? retryFailed(m.id) : retryMessage(m.id)} className="w-8 h-8 rounded-full bg-[#00a884] hover:bg-[#06cf9c] text-white flex items-center justify-center" title="Retry now"><RotateCcw size={14}/></button>
                    {m.conversation_id && <a href={`/inbox`} onClick={(e)=>{/* could deep link to conversation */}} className="w-8 h-8 rounded-full bg-[#f0f2f5] dark:bg-[#111b21] flex items-center justify-center text-[#54656f]" title="View in Inbox"><Eye size={14}/></a>}
                  </div>
                </div>
              ))}
            </div>
          )}
          {/* Scheduled failed section if any */}
          <FailedScheduledSection />
          <div className="px-4 py-2 bg-[#f0f2f5] dark:bg-[#111b21] text-xs text-[#667781]">{failedTotal} failed • tap ↻ to retry immediately. Failed scheduled also shown in “Scheduled → Failed” list.</div>
        </div>
      )}
    </div>
  );
}

function FailedScheduledSection(){
  const [items,setItems]=useState<any[]>([]);
  useEffect(()=>{ api.get("/send/scheduled",{params:{status:"failed",per_page:20}}).then(r=>setItems(r.data.items||[])).catch(()=>{}); },[]);
  if(items.length===0) return null;
  return (
    <div className="border-t border-gray-100 dark:border-[#2a3942]">
      <div className="px-4 py-2 bg-[#fff8c4] text-xs font-semibold text-[#8d5100]">Scheduled that failed to send</div>
      <div className="divide-y divide-gray-100 dark:divide-[#2a3942]">
        {items.map((s:any)=>(
          <div key={s.id} className="p-3 flex gap-3">
            <div className="w-8 h-8 rounded-full bg-[#fff8c4] flex items-center justify-center"><Clock size={14}/></div>
            <div className="flex-1 min-w-0">
              <p className="text-[13px] font-medium">{s.phone} <span className="text-[#667781]">• scheduled {new Date(s.schedule_at).toLocaleString()}</span></p>
              <p className="text-[12px] bg-[#f0f2f5] rounded px-2 py-1 mt-1 line-clamp-1">{s.body}</p>
              {s.error&&<p className="text-xs text-[#c5221f] mt-1">⚠ {s.error}</p>}
            </div>
            <button onClick={async()=>{ try{await api.post(`/send/scheduled/${s.id}/retry`); toast.success("Queued"); }catch{ toast.error("Failed"); } }} className="w-8 h-8 rounded-full bg-[#00a884] text-white flex items-center justify-center"><RotateCcw size={12}/></button>
          </div>
        ))}
      </div>
    </div>
  );
}
