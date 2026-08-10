import { useState, useEffect, useRef } from "react";
import api from "../api/client";
import { Contact, PaginatedResponse } from "../types";
import toast from "react-hot-toast";
import { Plus, Search, Trash2, Upload, Download, ChevronLeft, ChevronRight, X, ListPlus, MessageSquare } from "lucide-react";

const LEAD_STATUSES = ["new","contacted","replied","interested","follow-up","meeting","customer","not_interested","closed"];
const statusBadge = (s:string) => { const m:Record<string,string>={new:"badge-blue",contacted:"badge-yellow",replied:"badge-green",interested:"badge-green","follow-up":"badge-yellow",meeting:"badge-blue",customer:"badge-green",not_interested:"badge-red",closed:"badge-gray"}; return m[s]||"badge-gray"; };

export default function ContactsPage() {
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState(""); const [leadStatus, setLeadStatus] = useState("");
  const [loading, setLoading] = useState(true); const [error, setError] = useState<string|null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [showAdd, setShowAdd] = useState(false); const [showImport, setShowImport] = useState(false);
  const [showListModal, setShowListModal] = useState(false); const [availableLists, setAvailableLists] = useState<any[]>([]);
  const [selectedListId, setSelectedListId] = useState("");
  const [quickMsg, setQuickMsg] = useState(""); const [quickSendId, setQuickSendId] = useState<number|null>(null);
  const [quickSending, setQuickSending] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(()=>{loadContacts();},[page,search,leadStatus]);

  const loadContacts = async () => {
    try { setLoading(true); const {data}=await api.get<PaginatedResponse<Contact>>("/contacts/",{params:{page,per_page:25,search:search||undefined,lead_status:leadStatus||undefined}}); setContacts(data.items); setTotal(data.total); }
    catch (err:any) { setError(err.response?.data?.detail||"Failed"); }
    finally { setLoading(false); }
  };

  const handleDelete = async (id:number) => { if(!confirm("Delete?"))return; try { await api.delete(`/contacts/${id}`); toast.success("Deleted"); loadContacts(); } catch { toast.error("Failed"); } };
  const handleBulkDelete = async () => { if(selected.size===0||!confirm(`Delete ${selected.size}?`))return; try { await api.post("/contacts/bulk",{contact_ids:[...selected],action:"delete"}); toast.success("Deleted"); setSelected(new Set()); loadContacts(); } catch { toast.error("Failed"); } };
  const handleBulkStatus = async (status:string) => { if(selected.size===0)return; try { await api.post("/contacts/bulk",{contact_ids:[...selected],action:"status",value:status}); toast.success("Updated"); setSelected(new Set()); loadContacts(); } catch { toast.error("Failed"); } };

  const handleImport = async (e:React.FormEvent) => {
    e.preventDefault(); const file = fileRef.current?.files?.[0]; if(!file)return;
    const formData = new FormData(); formData.append("file",file);
    try { const {data}=await api.post("/contacts/import/csv",formData,{headers:{"Content-Type":"multipart/form-data"}}); toast.success(`Imported ${data.imported} (${data.duplicates} skipped)`); setShowImport(false); loadContacts(); }
    catch { toast.error("Import failed"); }
  };

  const doQuickSend = async () => { if(!quickMsg.trim()||!quickSendId)return; setQuickSending(true);
    try { await api.post("/send/",null,{params:{contact_id:quickSendId,body:quickMsg}}); toast.success("SMS sent!"); setQuickSendId(null); setQuickMsg(""); }
    catch (err:any) { toast.error(err.response?.data?.detail||"Failed"); }
    finally { setQuickSending(false); }
  };

  const totalPages = Math.ceil(total/25);
  if(error) return (<div className="text-center py-12"><h2 className="text-xl font-semibold mb-2">Error</h2><p className="text-gray-500 mb-4">{error}</p><button onClick={loadContacts} className="btn-primary">Retry</button></div>);

  return (<div className="space-y-4">
    <div className="flex items-center justify-between flex-wrap gap-2">
      <h1 className="text-2xl font-bold">Contacts</h1>
      <div className="flex gap-2 flex-wrap">
        {selected.size>0 && (<><span className="text-sm text-gray-500 self-center">{selected.size} selected</span>
          <button onClick={handleBulkDelete} className="btn-danger btn-sm"><Trash2 size={14} className="mr-1"/>Delete</button>
          <select className="input py-1 text-sm w-auto" onChange={e=>{if(e.target.value)handleBulkStatus(e.target.value)}} value="">{["Set Status...",...LEAD_STATUSES].map(s=><option key={s} value={s==="Set Status..."?"":s}>{s}</option>)}</select>
          <button onClick={async()=>{const{data}=await api.get("/lists/");setAvailableLists(data.items);setShowListModal(true)}} disabled={selected.size===0} className="btn-secondary btn-sm"><ListPlus size={14} className="mr-1"/>Add to List</button>
        </>)}
        <button onClick={()=>setShowImport(true)} className="btn-secondary btn-sm"><Upload size={14} className="mr-1"/>Import</button>
        <button onClick={()=>setShowAdd(true)} className="btn-primary btn-sm"><Plus size={14} className="mr-1"/>Add Contact</button>
      </div>
    </div>

    <div className="card p-3 flex gap-3 flex-wrap">
      <div className="relative flex-1 min-w-[200px]"><Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400"/><input type="text" className="input pl-9 py-1.5 text-sm" placeholder="Search contacts..." value={search} onChange={e=>{setSearch(e.target.value);setPage(1)}}/></div>
      <select className="input py-1.5 text-sm w-auto" value={leadStatus} onChange={e=>{setLeadStatus(e.target.value);setPage(1)}}>{["All Statuses",...LEAD_STATUSES].map(s=><option key={s} value={s==="All Statuses"?"":s}>{s}</option>)}</select>
    </div>

    <div className="card overflow-hidden"><div className="overflow-x-auto"><table className="w-full">
      <thead className="bg-gray-50 dark:bg-gray-700/50"><tr>
        <th className="px-4 py-3 text-left"><input type="checkbox" onChange={e=>{e.target.checked?setSelected(new Set(contacts.map(c=>c.id))):setSelected(new Set())}} checked={selected.size===contacts.length&&contacts.length>0}/></th>
        <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Name</th>
        <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Phone</th>
        <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Business</th>
        <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
        <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Actions</th>
      </tr></thead>
      <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
        {loading ? [...Array(5)].map((_,i)=>(<tr key={i}>{[...Array(6)].map((_,j)=>(<td key={j} className="px-4 py-3"><div className="skeleton h-4 w-full"/></td>))}</tr>))
        : contacts.length===0 ? (<tr><td colSpan={6} className="px-4 py-12 text-center text-gray-500">{search||leadStatus?"No matches":"No contacts. Import or add one."}</td></tr>)
        : contacts.map(c => (
          <tr key={c.id} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
            <td className="px-4 py-3"><input type="checkbox" checked={selected.has(c.id)} onChange={()=>{const n=new Set(selected);n.has(c.id)?n.delete(c.id):n.add(c.id);setSelected(n)}}/></td>
            <td className="px-4 py-3 font-medium">{c.first_name} {c.last_name}</td>
            <td className="px-4 py-3 text-sm">{c.phone_number}</td>
            <td className="px-4 py-3 text-sm">{c.business_name||"—"}</td>
            <td className="px-4 py-3"><span className={statusBadge(c.lead_status)}>{c.lead_status}</span></td>
            <td className="px-4 py-3"><div className="flex gap-1">
              <button onClick={()=>{setQuickSendId(c.id);setQuickMsg("")}} className="btn-ghost btn-sm text-primary-600" title="Send SMS"><MessageSquare size={14}/></button>
              <button onClick={()=>handleDelete(c.id)} className="btn-ghost btn-sm text-red-600" title="Delete"><Trash2 size={14}/></button>
            </div></td>
          </tr>
        ))}
      </tbody></table></div>
      {totalPages>1 && (<div className="flex items-center justify-between px-4 py-3 border-t"><span className="text-sm text-gray-500">{total} contacts</span><div className="flex gap-2"><button onClick={()=>setPage(p=>Math.max(1,p-1))} disabled={page===1} className="btn-secondary btn-sm"><ChevronLeft size={14}/></button><span className="text-sm self-center px-2">{page}/{totalPages}</span><button onClick={()=>setPage(p=>Math.min(totalPages,p+1))} disabled={page===totalPages} className="btn-secondary btn-sm"><ChevronRight size={14}/></button></div></div>)}
    </div>

    {showAdd && <AddContactModal lists={availableLists} onClose={()=>{setShowAdd(false);loadContacts()}} onOpen={()=>{api.get("/lists/").then(r=>setAvailableLists(r.data.items))}}/>}
    {showImport && <ImportModal onClose={()=>setShowImport(false)} onDone={()=>{setShowImport(false);loadContacts()}}/>}
    {showListModal && <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"><div className="card p-6 w-full max-w-md"><div className="flex justify-between mb-4"><h2 className="text-lg font-semibold">Add to List</h2><button onClick={()=>setShowListModal(false)} className="btn-ghost btn-sm"><X size={16}/></button></div><p className="text-sm text-gray-500 mb-3">{selected.size} contacts</p><select className="input mb-3" value={selectedListId} onChange={e=>setSelectedListId(e.target.value)}><option value="">Select list...</option>{availableLists.map((l:any)=><option key={l.id} value={l.id}>{l.name} ({l.contact_count})</option>)}</select><button onClick={async()=>{if(!selectedListId){toast.error("Select a list");return};try{await api.post(`/lists/${selectedListId}/contacts`,[...selected]);toast.success("Added");setShowListModal(false);setSelected(new Set());setSelectedListId("");loadContacts();}catch{toast.error("Failed")}}} className="btn-primary w-full">Add to List</button></div></div>}
    {quickSendId && <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"><div className="card p-6 w-full max-w-md"><div className="flex justify-between mb-4"><h2 className="text-lg font-semibold">Send SMS</h2><button onClick={()=>setQuickSendId(null)} className="btn-ghost btn-sm"><X size={16}/></button></div><textarea className="input" rows={4} placeholder="Type message..." value={quickMsg} onChange={e=>setQuickMsg(e.target.value)} autoFocus/><p className="text-xs text-gray-500 mt-1">{quickMsg.length} chars</p><button onClick={doQuickSend} disabled={quickSending||!quickMsg.trim()} className="btn-primary w-full mt-3">{quickSending?"Sending...":"Send SMS"}</button></div></div>}
  </div>);
}

function AddContactModal({ lists, onClose, onOpen }: { lists: any[]; onClose: () => void; onOpen: () => void }) {
  const [f, setF] = useState({ first_name:"",last_name:"",business_name:"",phone_number:"",email:"",city:"",state:"",website:"",industry:"",source:"",lead_status:"new",list_id:"" });
  const [sub, setSub] = useState(false);
  const [loadedLists, setLoadedLists] = useState<any[]>(lists||[]);
  useEffect(() => { api.get("/lists/").then(r=>setLoadedLists(r.data.items)); }, []);

  const handle = async (e:React.FormEvent) => { e.preventDefault(); if(!f.phone_number){toast.error("Phone required");return;} setSub(true);
    try { const res = await api.post("/contacts/", f);
      if(f.list_id) { try { await api.post(`/lists/${f.list_id}/contacts`,[(res.data as any).id]); } catch {} }
      toast.success("Contact created"); onClose();
    } catch (err:any) { toast.error(err.response?.data?.detail||"Failed"); }
    finally { setSub(false); }
  };

  return (<div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"><div className="card p-6 w-full max-w-lg max-h-[90vh] overflow-y-auto"><div className="flex justify-between mb-4"><h2 className="text-lg font-semibold">Add Contact</h2><button onClick={onClose} className="btn-ghost btn-sm"><X size={16}/></button></div>
    <form onSubmit={handle} className="space-y-3">
      <div className="grid grid-cols-2 gap-3"><div><label className="label">First Name</label><input className="input" value={f.first_name} onChange={e=>setF({...f,first_name:e.target.value})}/></div><div><label className="label">Last Name</label><input className="input" value={f.last_name} onChange={e=>setF({...f,last_name:e.target.value})}/></div></div>
      <div><label className="label">Phone Number *</label><input className="input" placeholder="08012345678" value={f.phone_number} onChange={e=>setF({...f,phone_number:e.target.value})} required/></div>
      <div><label className="label">Business Name</label><input className="input" value={f.business_name} onChange={e=>setF({...f,business_name:e.target.value})}/></div>
      <div><label className="label">Email</label><input className="input" type="email" value={f.email} onChange={e=>setF({...f,email:e.target.value})}/></div>
      <div className="grid grid-cols-2 gap-3"><div><label className="label">City</label><input className="input" value={f.city} onChange={e=>setF({...f,city:e.target.value})}/></div><div><label className="label">State</label><input className="input" value={f.state} onChange={e=>setF({...f,state:e.target.value})}/></div></div>
      <div><label className="label">Industry</label><input className="input" value={f.industry} onChange={e=>setF({...f,industry:e.target.value})}/></div>
      <div><label className="label">Source</label><input className="input" value={f.source} onChange={e=>setF({...f,source:e.target.value})}/></div>
      <div className="grid grid-cols-2 gap-3">
        <div><label className="label">Lead Status</label><select className="input" value={f.lead_status} onChange={e=>setF({...f,lead_status:e.target.value})}>{LEAD_STATUSES.map(s=><option key={s} value={s}>{s}</option>)}</select></div>
        <div><label className="label">Add to List</label><select className="input" value={f.list_id} onChange={e=>setF({...f,list_id:e.target.value})}><option value="">No list</option>{loadedLists.map((l:any)=><option key={l.id} value={l.id}>{l.name}</option>)}</select></div>
      </div>
      <div className="flex gap-2 pt-2"><button type="button" onClick={onClose} className="btn-secondary flex-1">Cancel</button><button type="submit" disabled={sub} className="btn-primary flex-1">{sub?"Creating...":"Create Contact"}</button></div>
    </form></div></div>);
}

function ImportModal({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [file, setFile] = useState<File|null>(null);
  const [preview, setPreview] = useState<any>(null);
  const [step, setStep] = useState<"upload"|"map"|"importing"|"done">("upload");
  const [mapping, setMapping] = useState<Record<string,string>>({});
  const [selectedListId, setSelectedListId] = useState("");
  const [lists, setLists] = useState<any[]>([]);
  const [result, setResult] = useState<any>(null);
  const [impErrors, setImpErrors] = useState<any[]>([]);

  useEffect(() => { api.get("/lists/").then(r=>setLists(r.data.items)); }, []);

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]; if(!f)return; setFile(f);
    const fd = new FormData(); fd.append("file",f);
    try { setStep("map"); } catch {}
    // Read locally for preview
    const text = await f.text();
    const rows = text.split("\n").filter(r=>r.trim());
    const headers = (rows[0]||"").split(",").map(h=>h.trim().replace(/"/g,""));
    const previewRows = rows.slice(1,6).map(r=>r.split(",").map(c=>c.trim().replace(/"/g,"")));
    setPreview({ headers, rows: previewRows });

    // Smart mapping
    const map: Record<string,string> = {};
    headers.forEach(h => {
      const l = h.toLowerCase();
      if(/phone|mobile|tel|number|contact/.test(l)) map[h] = "phone_number";
      else if(/first.*name|firstname/.test(l)) map[h] = "first_name";
      else if(/last.*name|lastname/.test(l)) map[h] = "last_name";
      else if(/business|company|brand|organization/.test(l)) map[h] = "business_name";
      else if(/email|mail/.test(l)) map[h] = "email";
      else if(/city/.test(l)) map[h] = "city";
      else if(/state|region/.test(l)) map[h] = "state";
      else if(/website|url|site/.test(l)) map[h] = "website";
      else if(/industry|sector/.test(l)) map[h] = "industry";
      else if(/source|channel/.test(l)) map[h] = "source";
    });
    setMapping(map);
  };

  const doImport = async () => {
    if(!file)return; setStep("importing");
    const fd = new FormData(); fd.append("file",file);
    fd.append("column_mapping",JSON.stringify(mapping));
    if(selectedListId) fd.append("list_id",selectedListId);
    try {
      const { data } = await api.post("/contacts/import/csv",fd,{headers:{"Content-Type":"multipart/form-data"}});
      setResult(data); setImpErrors(data.errors||[]); setStep("done");
    } catch { toast.error("Import failed"); setStep("upload"); }
  };

  return (<div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"><div className="card p-6 w-full max-w-xl max-h-[90vh] overflow-y-auto">
    <div className="flex justify-between mb-4"><h2 className="text-lg font-semibold">Import CSV</h2><button onClick={onClose} className="btn-ghost btn-sm"><X size={16}/></button></div>

    {step==="upload" && (<div className="space-y-3">
      <div><label className="label">CSV File</label><input type="file" accept=".csv" onChange={handleFileChange} className="input"/></div>
      <p className="text-xs text-gray-500">Select a CSV file — columns will be auto-detected.</p>
    </div>)}

    {step==="map" && preview && (<div className="space-y-3">
      <p className="text-sm font-medium">Column Mapping (auto-detected)</p>
      <div className="max-h-48 overflow-auto text-xs">
        <table className="w-full"><thead><tr className="bg-gray-50 dark:bg-gray-700">{preview.headers.map((h:string)=><th key={h} className="px-2 py-1 text-left">{h}</th>)}</tr></thead>
        <tbody>{preview.rows.map((r:string[],i:number)=><tr key={i} className="border-t">{r.map((c:string,j:number)=><td key={j} className="px-2 py-1 truncate max-w-[100px]">{c}</td>)}</tr>)}</tbody></table>
      </div>
      <div className="grid grid-cols-2 gap-2">
        {preview.headers.map((h:string) => (
          <div key={h}><label className="label text-xs">{h}</label>
            <select className="input py-1 text-xs" value={mapping[h]||""} onChange={e=>setMapping({...mapping,[h]:e.target.value})}>
              <option value="">Ignore</option>
              <option value="phone_number">Phone</option>
              <option value="first_name">First Name</option>
              <option value="last_name">Last Name</option>
              <option value="business_name">Business/Company</option>
              <option value="email">Email</option>
              <option value="city">City</option>
              <option value="state">State</option>
              <option value="website">Website</option>
              <option value="industry">Industry</option>
              <option value="source">Source</option>
            </select></div>
        ))}
      </div>
      <div><label className="label">Import to List</label><select className="input" value={selectedListId} onChange={e=>setSelectedListId(e.target.value)}><option value="">No list</option>{lists.map((l:any)=><option key={l.id} value={l.id}>{l.name}</option>)}</select></div>
      <button onClick={doImport} className="btn-primary w-full">Import Contacts</button>
    </div>)}

    {step==="importing" && <div className="text-center py-8"><div className="animate-spin rounded-full h-10 w-10 border-b-2 border-primary-600 mx-auto"/><p className="text-sm mt-4">Importing...</p></div>}

    {step==="done" && result && (<div className="space-y-3 text-center">
      <p className="text-4xl">✅</p>
      <p className="font-semibold">Import Complete</p>
      <div className="grid grid-cols-3 gap-2 text-sm">
        <div className="bg-green-50 dark:bg-green-900/30 rounded p-2"><p className="text-2xl font-bold text-green-600">{result.imported}</p><p className="text-xs">Imported</p></div>
        <div className="bg-yellow-50 dark:bg-yellow-900/30 rounded p-2"><p className="text-2xl font-bold text-yellow-600">{result.duplicates}</p><p className="text-xs">Duplicates</p></div>
        <div className="bg-red-50 dark:bg-red-900/30 rounded p-2"><p className="text-2xl font-bold text-red-600">{result.invalid}</p><p className="text-xs">Invalid</p></div>
      </div>
      {impErrors.length>0 && <details className="text-xs"><summary className="cursor-pointer">{impErrors.length} errors</summary><pre className="mt-1 text-left max-h-32 overflow-auto">{JSON.stringify(impErrors.slice(0,20),null,2)}</pre></details>}
      <button onClick={()=>{setStep("upload");setResult(null);setFile(null);setPreview(null);onDone()}} className="btn-primary w-full">Done</button>
    </div>)}
  </div></div>);
}
