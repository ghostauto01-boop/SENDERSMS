import { useState, useEffect } from "react";
import api from "../api/client";
import toast from "react-hot-toast";
import { Send, UserPlus, Search, X, Users, Phone, List, Clock } from "lucide-react";

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

  const charCount = message.length;
  const segmentCount = charCount <= 160 ? 1 : Math.ceil(charCount / 153);

  useEffect(() => {
    api.get("/contacts/", { params: { per_page: 100 } }).then(r => setContacts(r.data.items));
    api.get("/lists/").then(r => setLists(r.data.items));
    const now = new Date(); now.setMinutes(now.getMinutes() + 5);
    setScheduleDate(now.toISOString().split("T")[0]);
    setScheduleTime(now.toTimeString().split(" ")[0].substring(0, 5));
  }, []);

  const filtered = contacts.filter(c => !search || (c.first_name||"").toLowerCase().includes(search.toLowerCase()) || (c.last_name||"").toLowerCase().includes(search.toLowerCase()) || (c.business_name||"").toLowerCase().includes(search.toLowerCase()) || c.phone_number.includes(search));
  const toggleContact = (c: any) => setSelectedContacts(prev => prev.find(x=>x.id===c.id) ? prev.filter(x=>x.id!==c.id) : [...prev,c]);

  const handleSend = async () => {
    if (!message.trim()) { toast.error("Type a message"); return; }
    setSending(true); setResult(null);
    try {
      const params: any = { body: message };
      let sendMode: "contact" | "number" | "list" = mode;

      if (sendType === "scheduled") {
        const dt = `${scheduleDate}T${scheduleTime}:00+01:00`;
        params.schedule_at = dt;

        if (mode === "contact" && selectedContacts.length > 0) {
          for (const c of selectedContacts) {
            const { data } = await api.post("/send/", null, { params: { ...params, contact_id: c.id } });
            if (data.scheduled) { setResult(data); toast.success(`${data.count} message(s) scheduled for ${new Date(scheduleDate+'T'+scheduleTime).toLocaleString()}`); }
          }
        } else if (mode === "number" && phoneNumber) {
          const { data } = await api.post("/send/", null, { params: { ...params, phone_number: phoneNumber } });
          if (data.scheduled) { setResult(data); toast.success(`Scheduled for ${new Date(scheduleDate+'T'+scheduleTime).toLocaleString()}`); }
        } else if (mode === "list" && selectedListId) {
          const { data } = await api.post("/send/", null, { params: { ...params, list_id: parseInt(selectedListId) } });
          if (data.scheduled) { setResult(data); toast.success(`${data.count} messages scheduled`); }
        } else { toast.error("Select recipients"); }
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
        toast.success(`Sent to ${allResults.filter(r=>r.status!=="failed").length} contacts`);
      } else if (mode === "number" && phoneNumber) {
        const { data } = await api.post("/send/", null, { params: { phone_number: phoneNumber, body: message } });
        setResult(data); toast.success(data.success ? "Message sent!" : "Send failed");
      } else if (mode === "list" && selectedListId) {
        const { data } = await api.post("/send/", null, { params: { list_id: parseInt(selectedListId), body: message } });
        setResult(data); toast.success(`Sent ${data.sent} / ${data.total} messages`);
      } else { toast.error("Select a contact, enter a number, or pick a list"); }
    } catch (err: any) { toast.error(err.response?.data?.detail || "Send failed"); }
    finally { setSending(false); }
  };

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">Send SMS</h1>
      <div className="flex gap-2 flex-wrap">
        {[{id:"contact"as const,label:"Pick Contact",icon:UserPlus},{id:"number"as const,label:"Enter Number",icon:Phone},{id:"list"as const,label:"Send to List",icon:List}].map(m=>(
          <button key={m.id} onClick={()=>{setMode(m.id);setResult(null)}} className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium ${mode===m.id?"bg-primary-100 dark:bg-primary-900/30 text-primary-700 dark:text-primary-300":"text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700"}`}><m.icon size={14}/>{m.label}</button>
        ))}
        <span className="text-gray-300 dark:text-gray-600 mx-1 self-center">|</span>
        <button onClick={()=>{setSendType("now");setResult(null)}} className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium ${sendType==="now"?"bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-300":"text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700"}`}><Send size={14}/>Now</button>
        <button onClick={()=>{setSendType("scheduled");setResult(null)}} className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium ${sendType==="scheduled"?"bg-orange-100 dark:bg-orange-900/30 text-orange-700 dark:text-orange-300":"text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700"}`}><Clock size={14}/>Schedule</button>
      </div>

      {sendType === "scheduled" && (
        <div className="card p-3 flex flex-wrap items-end gap-3">
          <div><label className="label text-xs">Date</label><input type="date" className="input py-1.5 text-sm" value={scheduleDate} onChange={e=>setScheduleDate(e.target.value)}/></div>
          <div><label className="label text-xs">Time</label><input type="time" className="input py-1.5 text-sm" value={scheduleTime} onChange={e=>setScheduleTime(e.target.value)}/></div>
          <p className="text-xs text-gray-500 self-center">Message will be sent automatically at this time</p>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="card p-4 space-y-3">
          <h2 className="font-semibold">Recipients</h2>
          {mode==="contact"&&(<><div className="relative"><Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400"/><input className="input pl-9" placeholder="Search contacts..." value={search} onChange={e=>setSearch(e.target.value)}/></div><div className="max-h-64 overflow-y-auto space-y-1 border rounded-lg p-2">{filtered.slice(0,50).map(c=>(<label key={c.id} className={`flex items-center gap-2 p-2 rounded cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700 ${selectedContacts.find(x=>x.id===c.id)?"bg-primary-50 dark:bg-primary-900/20":""}`}><input type="checkbox" checked={!!selectedContacts.find(x=>x.id===c.id)} onChange={()=>toggleContact(c)} className="rounded"/><span className="text-sm font-medium">{c.first_name} {c.last_name}</span><span className="text-xs text-gray-400 ml-auto">{c.phone_number}</span></label>))}</div>{selectedContacts.length>0&&(<div className="flex flex-wrap gap-1">{selectedContacts.map(c=>(<span key={c.id} className="badge badge-blue text-xs flex items-center gap-1">{c.first_name||c.phone_number}<button onClick={()=>toggleContact(c)}><X size={10}/></button></span>))}</div>)}</>)}
          {mode==="number"&&(<div><label className="label">Phone Number</label><input className="input" placeholder="08012345678" value={phoneNumber} onChange={e=>setPhoneNumber(e.target.value)}/></div>)}
          {mode==="list"&&(<div><label className="label">Contact List</label><select className="input" value={selectedListId} onChange={e=>setSelectedListId(e.target.value)}><option value="">Select a list...</option>{lists.map((l:any)=>(<option key={l.id} value={l.id}>{l.name} ({l.contact_count} contacts)</option>))}</select></div>)}
        </div>
        <div className="card p-4 space-y-3">
          <h2 className="font-semibold">Message</h2>
          <textarea className="input" rows={6} placeholder="Type your SMS message here..." value={message} onChange={e=>setMessage(e.target.value)} maxLength={1600}/>
          <div className="flex justify-between text-xs text-gray-500"><span>{charCount} chars · {segmentCount} segment{segmentCount>1?"s":""}</span><span>~{segmentCount*4} NGN</span></div>
          <div className="flex flex-wrap gap-1">{["{{first_name}}","{{business_name}}","{{city}}","{{state}}"].map(v=>(<button key={v} type="button" onClick={()=>setMessage(message+" "+v)} className="text-xs px-2 py-0.5 bg-gray-100 dark:bg-gray-700 rounded hover:bg-gray-200 dark:hover:bg-gray-600">{v}</button>))}</div>
          <button onClick={handleSend} disabled={sending||!message.trim()} className={`btn-primary w-full ${sendType==="scheduled"?"bg-orange-600 hover:bg-orange-700":""}`}>
            {sendType==="scheduled" ? <Clock size={14} className="mr-1"/> : <Send size={14} className="mr-1"/>}
            {sending?"Sending...":sendType==="scheduled"?`Schedule SMS${mode==="list"?" to List":""}`:`Send SMS${mode==="list"?" to List":""}`}
          </button>
          {result&&(<div className="space-y-2">
            {result.scheduled ? (
              <div className="p-3 rounded-lg text-sm bg-orange-50 dark:bg-orange-900/30 text-orange-700 dark:text-orange-300"><strong>{result.count} scheduled</strong> for {new Date(result.schedule_at).toLocaleString()}</div>
            ) : (
              <><div className={`p-3 rounded-lg text-sm ${result.failed>0?"bg-yellow-50 dark:bg-yellow-900/30 text-yellow-800 dark:text-yellow-200":"bg-green-50 dark:bg-green-900/30 text-green-800 dark:text-green-200"}`}><strong>{result.sent} sent</strong>{result.failed>0&&<>, {result.failed} failed</>}{result.total>0&&<> · {result.total} total</>}{result.gateway_url&&<div className="text-xs mt-1">Gateway: {result.gateway_url} (SIM {result.sim_used})</div>}</div>
              {result.results&&result.results.map((r:any,i:number)=>(<details key={i} className={`text-xs rounded p-2 ${r.status==="sent"?"bg-green-50 dark:bg-green-900/20":"bg-red-50 dark:bg-red-900/20"}`}><summary className="cursor-pointer font-medium">{r.phone}: {r.status==="sent"?"✅ Sent":"❌ Failed"}{r.error&&<span className="text-red-500 ml-1">— {r.error}</span>}</summary><pre className="mt-1 whitespace-pre-wrap text-[10px] opacity-70">{JSON.stringify(r.api_response,null,2)}</pre></details>))}</>
            )}
          </div>)}
        </div>
      </div>
    </div>
  );
}
