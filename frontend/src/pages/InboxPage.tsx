import { useState, useEffect, useRef } from "react";
import api from "../api/client";
import toast from "react-hot-toast";
import { Send, Star, ThumbsDown, Archive, ChevronLeft, CheckCheck, MessageCircle } from "lucide-react";

export default function InboxPage() {
  const [convs, setConvs] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<any>(null);
  const [messages, setMessages] = useState<any[]>([]);
  const [replyText, setReplyText] = useState("");
  const [sending, setSending] = useState(false);
  const [filter, setFilter] = useState("all");
  const [showInfo, setShowInfo] = useState(false);
  const [polling, setPolling] = useState(false);
  const [pollDebug, setPollDebug] = useState<any>(null);
  const chatEndRef = useRef<HTMLDivElement>(null);
  const pollRef = useRef<any>(null);

  useEffect(() => { loadConvs(); return () => clearInterval(pollRef.current); }, [filter]);
  useEffect(() => { if (selected) loadMessages(selected.id); }, [selected]);
  useEffect(() => { chatEndRef.current?.scrollIntoView({behavior:"smooth"}); }, [messages]);
  useEffect(() => { pollRef.current = setInterval(() => { loadConvs(); if(selected) loadMessages(selected.id); }, 10000); return () => clearInterval(pollRef.current); }, [selected]);

  const loadConvs = async () => {
    try {
      const { data } = await api.get("/inbox/conversations", { params: { per_page: 500 } });
      setConvs(data.items || []);
    } catch {} finally { setLoading(false); }
  };

  const loadMessages = async (convId: number) => {
    try { const { data } = await api.get(`/inbox/conversations/${convId}`); setMessages(data.messages || []); } catch {}
  };

  const sendReply = async () => {
    if (!replyText.trim() || !selected) return; setSending(true);
    try { await api.post(`/inbox/conversations/${selected.id}/reply`, null, { params: { body: replyText } }); setReplyText(""); toast.success("Sent"); loadMessages(selected.id); loadConvs(); }
    catch (err: any) { toast.error(err.response?.data?.detail || "Failed"); }
    finally { setSending(false); }
  };

  const markAs = async (status: string) => {
    if (!selected) return;
    try { await api.post(`/inbox/conversations/${selected.id}/mark-${status}`); toast.success("Updated"); loadConvs(); } catch { toast.error("Failed"); }
  };

  const doPoll = async () => {
    setPolling(true); setPollDebug(null);
    try {
      const { data } = await api.post("/inbox/poll-now");
      setPollDebug(data);
      const total = data.new_inbound || 0;
      if (total > 0) { toast.success(`${total} messages synced to inbox!`); loadConvs(); }
      else if (data.outgoing_updated > 0) { toast.success(`${data.outgoing_updated} statuses updated`); loadConvs(); }
      else { toast(`Checked ${data.total_api_messages || 0} messages — up to date`, {icon: "ℹ️"}); }
    } catch(err:any) { toast.error("Sync failed"); setPollDebug({error:err.message}); }
    finally { setPolling(false); }
  };

  const filters = [{v:"all",l:"All"},{v:"unread",l:"Unread"},{v:"interested",l:"Interested"},{v:"closed",l:"Closed"}];

  return (
    <div className="flex h-[calc(100vh-8rem)] gap-4">
      {/* LEFT */}
      <div className={`${selected ? "hidden md:flex" : "flex"} w-full md:w-80 flex-shrink-0 flex-col`}>
        <div className="flex items-center justify-between mb-2">
          <h1 className="text-2xl font-bold">Inbox</h1>
          <button onClick={doPoll} disabled={polling} className="btn-secondary btn-sm">{polling ? "Syncing..." : "📥 Sync Inbox"}</button>
        </div>
        <div className="bg-blue-50 dark:bg-blue-900/20 rounded-lg p-2 mb-2 text-xs text-blue-700 dark:text-blue-300">
          <p>Syncs ALL SMS from your phone. Webhook: <code className="text-xs bg-blue-100 dark:bg-blue-800 px-1 rounded">/api/v1/webhooks/smsgateway</code></p>
        </div>
        <div className="flex flex-wrap gap-1 mb-2">{filters.map(f=>(<button key={f.v} onClick={()=>{setFilter(f.v);setSelected(null)}} className={`text-xs px-2 py-1 rounded-full ${filter===f.v?"bg-primary-100 dark:bg-primary-900/30 text-primary-700 dark:text-primary-300":"bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-400"}`}>{f.l}</button>))}</div>
        <div className="flex-1 overflow-y-auto space-y-1">
          {loading ? [...Array(5)].map((_,i)=>(<div key={i} className="card p-3"><div className="skeleton h-4 w-32 mb-1"/><div className="skeleton h-3 w-48"/></div>))
          : convs.length===0 ? <div className="text-center py-8 text-gray-500"><MessageCircle size={40} className="mx-auto mb-2 opacity-30"/><p className="text-sm">No conversations yet — click Sync Inbox</p></div>
          : convs.map(c => (<button key={c.id} onClick={()=>{setSelected(c);setShowInfo(false)}} className={`w-full text-left card p-3 hover:bg-gray-50 dark:hover:bg-gray-700/50 ${selected?.id===c.id?"ring-2 ring-primary-500":""}`}><div className="flex items-center justify-between"><span className="font-medium text-sm truncate">{c.contact_name||c.contact_phone}</span><div className="flex items-center gap-1">{c.unread_count>0?<span className="badge badge-blue text-xs">{c.unread_count}</span>:(c.status==="read"&&<CheckCheck size={12} className="text-gray-400"/>)}{c.status==="interested"&&<Star size={12} className="text-yellow-500"/>}</div></div><p className="text-xs text-gray-500 truncate mt-0.5">{c.last_message_preview||"No messages"}</p></button>))}
        </div>
      </div>

      {/* RIGHT */}
      <div className="flex-1 flex flex-col card overflow-hidden">
      {pollDebug && !selected ? (
        <div className="flex-1 p-4 overflow-y-auto">
          <div className="text-xs bg-gray-50 dark:bg-gray-800 rounded-lg p-3">
            <p className="font-medium mb-2">📊 Sync Result</p>
            <div className="space-y-1">
              <p>Total in API: <strong>{pollDebug.total_api_messages || 0}</strong></p>
              <p>New synced: <strong className={pollDebug.new_inbound>0?"text-green-600":""}>{pollDebug.new_inbound || 0}</strong></p>
              <p>Statuses updated: <strong>{pollDebug.outgoing_updated || 0}</strong></p>
              <p>New contacts: {pollDebug.new_contacts || 0} · Conversations: {pollDebug.new_conversations || 0}</p>
              <p>Dupes skipped: {pollDebug.skipped || 0}</p>
              {pollDebug.webhook_url && <p className="mt-1 text-gray-500">Webhook: <code className="text-[10px] bg-gray-200 dark:bg-gray-700 px-1 rounded">{pollDebug.webhook_url}</code></p>}
              {pollDebug.error && <p className="text-red-600 mt-1">Error: {pollDebug.error}</p>}
              {pollDebug.note && <p className="text-blue-600 mt-1">{pollDebug.note}</p>}
              {pollDebug.details && pollDebug.details.length > 0 && (
                <details className="mt-2"><summary className="cursor-pointer font-medium">{pollDebug.details.length} activity entries</summary>
                  <div className="max-h-32 overflow-y-auto mt-1">
                    {pollDebug.details.slice(0,20).map((d:any,i:number)=>(<div key={i} className="text-[10px] py-0.5 border-b border-gray-200 dark:border-gray-700">{d.type}: 📱 {d.from||d.phone} {d.text||d.state} @ {d.time?.slice(11,19)||""}</div>))}
                  </div>
                </details>
              )}
            </div>
          </div>
          <div className="mt-4 text-center text-gray-400"><p className="text-sm">Select a conversation on the left to view</p></div>
        </div>
      ) : selected ? (<>
          <div className="p-3 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <button className="md:hidden btn-ghost btn-sm" onClick={()=>setSelected(null)}><ChevronLeft size={18}/></button>
              <div><h2 className="font-semibold text-sm">{selected.contact_name||selected.contact_phone}</h2><p className="text-xs text-gray-500">{selected.contact_phone}</p></div>
            </div>
            <div className="flex gap-1">
              <button onClick={()=>markAs("interested")} className="btn-ghost btn-sm"><Star size={16} className={selected.status==="interested"?"text-yellow-500":""}/></button>
              <button onClick={()=>markAs("not-interested")} className="btn-ghost btn-sm"><ThumbsDown size={16}/></button>
              <button onClick={()=>markAs("close")} className="btn-ghost btn-sm"><Archive size={16}/></button>
              <button onClick={()=>setShowInfo(!showInfo)} className="btn-ghost btn-sm text-xs">Info</button>
            </div>
          </div>
          {showInfo && selected?.contact && (<div className="px-4 py-2 bg-gray-50 dark:bg-gray-700/50 text-xs space-y-1 border-b">{selected.contact.business_name&&<p><strong>Business:</strong> {selected.contact.business_name}</p>}{selected.contact.city&&<p><strong>Location:</strong> {selected.contact.city}{selected.contact.state?`, ${selected.contact.state}`:""}</p>}<p><strong>Phone:</strong> {selected.contact.phone_number}</p></div>)}
          <div className="flex-1 overflow-y-auto p-3 space-y-2 bg-gray-50 dark:bg-gray-900/50">
            {messages.length===0 ? <p className="text-center text-gray-400 text-sm py-12">No messages</p>
            : messages.map(m => (<div key={m.id} className={`flex ${m.direction==="outgoing"?"justify-end":"justify-start"}`}><div className={`max-w-[80%] px-3 py-2 rounded-xl text-sm ${m.direction==="outgoing"?"bg-primary-600 text-white rounded-br-sm":"bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 rounded-bl-sm shadow-sm"}`}><p className="whitespace-pre-wrap">{m.body}</p><div className={`flex items-center gap-1 justify-end mt-1 text-[10px] ${m.direction==="outgoing"?"text-primary-200":"text-gray-400"}`}>{new Date(m.created_at).toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"})}{m.direction==="outgoing"&&<span>· {m.status==="delivered"?"✓✓":m.status==="sent"?"✓":"·"}</span>}</div></div></div>))}
            <div ref={chatEndRef}/>
          </div>
          <div className="p-3 border-t border-gray-200 dark:border-gray-700 flex gap-2 bg-white dark:bg-gray-800">
            <input className="input flex-1" placeholder="Type reply..." value={replyText} onChange={e=>setReplyText(e.target.value)} onKeyDown={e=>e.key==="Enter"&&sendReply()}/>
            <button onClick={sendReply} disabled={sending||!replyText.trim()} className="btn-primary"><Send size={16}/></button>
          </div>
        </>) : (
        <div className="flex-1 flex items-center justify-center text-gray-400"><div className="text-center"><MessageCircle size={64} className="mx-auto mb-4 opacity-30"/><p>Select a conversation or click Sync Inbox</p></div></div>
      )}
      </div>
    </div>
  );
}
