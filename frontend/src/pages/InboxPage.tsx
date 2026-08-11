import { useState, useEffect, useRef } from "react";
import api from "../api/client";
import toast from "react-hot-toast";
import { Send, Star, ThumbsDown, Archive, ChevronLeft, CheckCheck, MessageCircle, Bug } from "lucide-react";

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
  const chatEndRef = useRef<HTMLDivElement>(null);
  const pollRef = useRef<any>(null);

  useEffect(() => { loadConvs(); }, [filter]);
  useEffect(() => { const t = setTimeout(loadConvs, 300); return () => clearTimeout(t); }, [search]);
  useEffect(() => { if (selected) loadMessages(selected.id); }, [selected]);
  useEffect(() => { chatEndRef.current?.scrollIntoView({behavior:"smooth"}); }, [messages]);
  useEffect(() => { pollRef.current = setInterval(() => { loadConvs(); if(selected) loadMessages(selected.id); }, 10000); return () => clearInterval(pollRef.current); }, [selected]);

  // Kept in refs so the 10s poller always sends the CURRENT filter/search
  // without having to tear down and recreate the interval.
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
    try { const { data } = await api.get(`/inbox/conversations/${convId}`); setMessages(data.messages || []); } catch {}
  };

  const sendReply = async () => {
    if (!replyText.trim() || !selected) return; setSending(true);
    try {
      const { data } = await api.post(`/inbox/conversations/${selected.id}/reply`, null, { params: { body: replyText } });
      if (data?.success === false) {
        // The row is saved as `failed`; surface the gateway's own reason.
        toast.error(data.error || "The SMS gateway rejected the message.");
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
      toast.success("Updated");
      loadConvs();
    } catch { toast.error("Failed"); }
  };

  const doPoll = async () => {
    setPolling(true); setPollDebug(null); setDebugData(null);
    try {
      const { data } = await api.post("/inbox/poll-now");
      setPollDebug(data);
      loadConvs();

      if (data.problems?.length) {
        toast.error(data.problems[0], { duration: 6000 });
      } else if (data.outgoing_updated > 0) {
        toast.success(`${data.outgoing_updated} delivery status${data.outgoing_updated>1?"es":""} updated`);
      } else if (data.export_triggered) {
        toast.success("Phone is replaying recent messages — they'll appear shortly");
      } else {
        toast("Webhooks registered — new SMS arrive automatically", { icon: "\u2705" });
      }

      // Inbound SMS come back asynchronously as webhooks, not in this response.
      // Refresh a couple of times so the replay lands without another click.
      [4000, 10000].forEach((ms) => setTimeout(() => {
        loadConvs(); if (selected) loadMessages(selected.id);
      }, ms));
    } catch(err:any) {
      toast.error(err.response?.data?.detail || "Sync failed");
      setPollDebug({error: err.message});
    }
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

  const filters = [{v:"all",l:"All"},{v:"unread",l:"Unread"},{v:"interested",l:"Interested"},{v:"closed",l:"Closed"}];

  return (
    <div className="flex h-[calc(100vh-8rem)] gap-4">
      {/* LEFT */}
      <div className={`${selected ? "hidden md:flex" : "flex"} w-full md:w-80 flex-shrink-0 flex-col`}>
        <div className="flex items-center justify-between mb-2">
          <h1 className="text-xl sm:text-2xl font-bold">Inbox</h1>
          <div className="flex gap-1">
            <button onClick={doPoll} disabled={polling} className="btn-secondary btn-sm">{polling ? "Syncing..." : "📥 Sync"}</button>
            <button onClick={doPollDebug} disabled={debugLoading} className="btn-ghost btn-sm" title="Debug: show raw API response"><Bug size={14}/></button>
          </div>
        </div>
        <div className="bg-blue-50 dark:bg-blue-900/20 rounded-lg p-2 mb-2 text-xs text-blue-700 dark:text-blue-300">
          <p>Syncs ALL SMS from your phone. Webhook: <code className="text-xs bg-blue-100 dark:bg-blue-800 px-1 rounded">/api/v1/webhooks/smsgateway</code></p>
        </div>
        <input className="input w-full mb-2 text-sm" placeholder="Search name, phone or message..."
               value={search} onChange={e=>setSearch(e.target.value)} />
        <div className="flex flex-wrap gap-1 mb-2">{filters.map(f=>(<button key={f.v} onClick={()=>{setFilter(f.v);setSelected(null)}} className={`text-xs px-2 py-1 rounded-full ${filter===f.v?"bg-primary-100 dark:bg-primary-900/30 text-primary-700 dark:text-primary-300":"bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-400"}`}>{f.l}</button>))}</div>
        <div className="flex-1 overflow-y-auto space-y-1">
          {loading ? [...Array(5)].map((_,i)=>(<div key={i} className="card p-3"><div className="skeleton h-4 w-32 mb-1"/><div className="skeleton h-3 w-48"/></div>))
          : convs.length===0 ? <div className="text-center py-8 text-gray-500"><MessageCircle size={40} className="mx-auto mb-2 opacity-30"/><p className="text-sm">No conversations yet — click Sync Inbox</p></div>
          : convs.map(c => (<button key={c.id} onClick={()=>{setSelected(c);setShowInfo(false)}} className={`w-full text-left card p-3 hover:bg-gray-50 dark:hover:bg-gray-700/50 ${selected?.id===c.id?"ring-2 ring-primary-500":""}`}><div className="flex items-center justify-between"><span className="font-medium text-sm truncate">{c.contact_name||c.contact_phone}</span><div className="flex items-center gap-1">{c.unread_count>0?<span className="badge badge-blue text-xs">{c.unread_count}</span>:(c.status==="read"&&<CheckCheck size={12} className="text-gray-400"/>)}{c.status==="interested"&&<Star size={12} className="text-yellow-500"/>}</div></div><p className="text-xs text-gray-500 truncate mt-0.5">{c.last_message_preview||"No messages"}</p></button>))}
        </div>
      </div>

      {/* RIGHT */}
      <div className="flex-1 flex flex-col card overflow-hidden">
      {/* DEBUG VIEW */}
      {debugData ? (
        <div className="flex-1 p-4 overflow-y-auto">
          <div className="flex items-center justify-between mb-2">
            <h2 className="font-bold text-lg">🔍 Receive path diagnostic</h2>
            <button onClick={()=>setDebugData(null)} className="btn-ghost btn-sm">Close</button>
          </div>
          {debugData.error ? (
            <p className="text-red-600">Error: {debugData.error}</p>
          ) : (
            <div className="space-y-3 text-xs">
              {debugData.issues?.length > 0 ? (
                <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg p-3">
                  <p className="font-medium mb-1 text-red-700 dark:text-red-400">Problems blocking incoming SMS</p>
                  <ul className="list-disc ml-4 space-y-1">
                    {debugData.issues.map((i:string,n:number)=>(<li key={n}>{i}</li>))}
                  </ul>
                </div>
              ) : (
                <div className="bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 rounded-lg p-3">
                  <p className="font-medium text-green-700 dark:text-green-400">{debugData.note}</p>
                </div>
              )}

              <div className="bg-gray-50 dark:bg-gray-800 rounded-lg p-3 space-y-1">
                <p className="font-medium mb-1">Configuration</p>
                <p>Public URL set: <strong>{String(debugData.config?.public_base_url_set)}</strong></p>
                <p>Gateway credentials: <strong>{String(debugData.config?.credentials_set)}</strong></p>
                <p>Signing secret: <strong>{String(debugData.config?.signing_secret_set)}</strong>{debugData.config?.allow_unsigned && " (unsigned allowed)"}</p>
                {debugData.webhook_url && <p className="text-gray-500 break-all">Delivering to: <code className="text-[10px] bg-gray-200 dark:bg-gray-700 px-1 rounded">{debugData.webhook_url}</code></p>}
              </div>

              <div className="bg-gray-50 dark:bg-gray-800 rounded-lg p-3 space-y-1">
                <p className="font-medium mb-1">Device &amp; webhooks</p>
                <p>Devices online: <strong>{debugData.devices?.count ?? 0}</strong></p>
                <p>Events registered: <strong>{debugData.matching_events?.length ?? 0}</strong> {debugData.matching_events?.join(", ")}</p>
                <p>Inbound messages stored: <strong>{debugData.stored_inbound_messages ?? 0}</strong></p>
              </div>

              {debugData.recent_webhook_events?.length > 0 ? (
                <div>
                  <p className="font-medium mb-1">Last webhooks received</p>
                  {debugData.recent_webhook_events.map((e:any,i:number)=>(
                    <div key={i} className={`mb-1 p-2 rounded ${e.status==="error" ? "bg-red-50 dark:bg-red-900/20" : "bg-green-50 dark:bg-green-900/20"}`}>
                      <p><code>{e.event_type}</code> — {e.status} @ {e.at?.slice(11,19)}</p>
                      {e.error && <p className="text-red-600">{e.error}</p>}
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-orange-600">No webhook has ever reached this server.</p>
              )}

              <details><summary className="cursor-pointer font-medium text-gray-500">Raw response</summary>
                <pre className="text-[10px] mt-1 bg-gray-100 dark:bg-gray-900 p-2 rounded max-h-60 overflow-y-auto whitespace-pre-wrap">{JSON.stringify(debugData, null, 2)}</pre>
              </details>
            </div>
          )}
        </div>
      ) : pollDebug && !selected ? (
        <div className="flex-1 p-4 overflow-y-auto">
          <div className="text-xs bg-gray-50 dark:bg-gray-800 rounded-lg p-3">
            <p className="font-medium mb-2">📊 Sync result</p>
            <div className="space-y-1">
              <p>Device online: <strong className={pollDebug.device_online?"text-green-600":"text-red-600"}>{String(!!pollDebug.device_online)}</strong></p>
              <p>Events registered: <strong className={pollDebug.registered_events?.length?"text-green-600":"text-red-600"}>{pollDebug.registered_events?.length || 0}</strong> {pollDebug.registered_events?.join(", ")}</p>
              <p>History replay triggered: <strong>{String(!!pollDebug.export_triggered)}</strong></p>
              <p>Delivery statuses updated: <strong>{pollDebug.outgoing_updated || 0}</strong></p>
              <p>Outgoing messages checked: {pollDebug.total_api_messages || 0}</p>
              {pollDebug.webhook_url && <p className="mt-1 text-gray-500 break-all">Webhook: <code className="text-[10px] bg-gray-200 dark:bg-gray-700 px-1 rounded">{pollDebug.webhook_url}</code></p>}
              {pollDebug.error && <p className="text-red-600 mt-1">Error: {pollDebug.error}</p>}
              {pollDebug.problems?.length > 0 && (
                <div className="mt-2 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded p-2">
                  <p className="font-medium text-red-700 dark:text-red-400 mb-1">Needs attention</p>
                  <ul className="list-disc ml-4 space-y-1">{pollDebug.problems.map((p:string,i:number)=>(<li key={i}>{p}</li>))}</ul>
                </div>
              )}
              {pollDebug.note && <p className="text-blue-600 mt-1">{pollDebug.note}</p>}
              {pollDebug.details?.length > 0 && (
                <details className="mt-2"><summary className="cursor-pointer font-medium">{pollDebug.details.length} activity entries</summary>
                  <div className="max-h-32 overflow-y-auto mt-1">
                    {pollDebug.details.slice(0,20).map((d:any,i:number)=>(<div key={i} className="text-[10px] py-0.5 border-b border-gray-200 dark:border-gray-700">{d.type}: {d.id} {d.state}</div>))}
                  </div>
                </details>
              )}
            </div>
          </div>
          <div className="mt-4 text-center text-gray-400">
            <button onClick={doPollDebug} className="text-xs text-blue-500 hover:underline">🔍 Full receive-path diagnostic</button>
            <p className="text-sm mt-2">Select a conversation on the left to view</p>
          </div>
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
            : messages.map(m => (<div key={m.id} className={`flex ${m.direction==="outgoing"?"justify-end":"justify-start"}`}><div className={`max-w-[80%] px-3 py-2 rounded-xl text-sm ${m.direction==="outgoing"?"bg-primary-600 text-white rounded-br-sm":"bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 rounded-bl-sm shadow-sm"}`}><p className="whitespace-pre-wrap">{m.body}</p>{m.direction==="outgoing"&&(m.status==="failed"||m.status==="cancelled")&&m.last_error&&<p className="text-[10px] mt-1 text-red-200">{m.last_error}</p>}<div className={`flex items-center gap-1 justify-end mt-1 text-[10px] ${m.direction==="outgoing"?"text-primary-200":"text-gray-400"}`}>{new Date(m.created_at).toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"})}{m.direction==="outgoing"&&<span title={m.last_error||m.status}>· {m.status==="delivered"?"✓✓":m.status==="sent"?"✓":m.status==="failed"?"⚠ failed":m.status==="cancelled"?"⚠ cancelled":"·"}</span>}</div></div></div>))}
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
