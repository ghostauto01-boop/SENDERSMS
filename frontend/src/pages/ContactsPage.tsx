import { useState, useEffect } from "react";
import api from "../api/client";
import { Contact, PaginatedResponse } from "../types";
import toast from "react-hot-toast";
import { Plus, Search, Trash2, Upload, Download, ChevronLeft, ChevronRight, X, ListPlus, MessageSquare, Phone, MapPin, Building2, User, CheckSquare, Square, IdCard } from "lucide-react";
import ContactProfileModal from "../components/ContactProfileModal";
import ContactActions from "../components/ContactActions";
import ImportContactsModal from "../components/ImportContactsModal";
import ListPicker from "../components/ListPicker";

const LEAD_STATUSES = ["new","contacted","replied","interested","follow-up","meeting","customer","not_interested","closed"];
const statusBadge = (s:string) => { const m:Record<string,string>={new:"bg-[#e7f3ff] text-[#008069]",contacted:"bg-[#fff8c4] text-[#9a6f00]",replied:"bg-[#d9fdd3] text-[#008069]",interested:"bg-[#d9fdd3] text-[#075e54]", "follow-up":"bg-[#ffecb3] text-[#8d5100]",meeting:"bg-[#e7f3ff] text-[#0066cc]",customer:"bg-[#d9fdd3] text-[#008069]",not_interested:"bg-[#fce8e6] text-[#c5221f]",closed:"bg-[#f0f2f5] text-[#54656f]"}; return m[s]||"bg-[#f0f2f5] text-[#54656f]"; };

const avatarColor = (name:string) => {
  const colors = ["bg-[#00a884]","bg-[#128C7E]","bg-[#075E54]","bg-[#34B7F1]","bg-[#FF8A65]","bg-[#BA68C8]","bg-[#4DB6AC]","bg-[#FFB74D]","bg-[#F06292]","bg-[#7986CB]"];
  let h=0; for(let i=0;i<name.length;i++) h=(h*31+name.charCodeAt(i))%colors.length;
  return colors[h];
};
const initials = (c:Contact) => {
  if (c.first_name||c.last_name) return `${(c.first_name?.[0]||"").toUpperCase()}${(c.last_name?.[0]||"").toUpperCase()}` || (c.phone_number.slice(-2));
  if (c.business_name) return c.business_name.slice(0,2).toUpperCase();
  return c.phone_number.slice(-2);
};

export { customKey, detectColumns } from "../utils/csv";

export default function ContactsPage() {
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState(""); const [leadStatus, setLeadStatus] = useState("");
  // Debounced search text — typing no longer fires a request per keystroke.
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [loading, setLoading] = useState(true); const [error, setError] = useState<string|null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  // "Select all N matching" — the selection covers every contact matching the
  // current search/status across ALL pages, not just the 25 on screen.
  const [allMatching, setAllMatching] = useState(false);
  const [showAdd, setShowAdd] = useState(false); const [showImport, setShowImport] = useState(false);
  const [showListModal, setShowListModal] = useState(false);
  const [selectedListId, setSelectedListId] = useState("");
  const [quickMsg, setQuickMsg] = useState(""); const [quickSendId, setQuickSendId] = useState<number|null>(null);
  const [quickSending, setQuickSending] = useState(false);
  const [quickContact, setQuickContact] = useState<Contact|null>(null);
  // Full-record view: every column the CSV imported for this contact.
  const [profileId, setProfileId] = useState<number|null>(null);

  useEffect(()=>{ const t=setTimeout(()=>{ setDebouncedSearch(search); },350); return ()=>clearTimeout(t); },[search]);
  useEffect(()=>{loadContacts();},[page,debouncedSearch,leadStatus]);

  const loadContacts = async () => {
    try { setLoading(true); const {data}=await api.get<PaginatedResponse<Contact>>("/contacts/",{params:{page,per_page:25,search:debouncedSearch||undefined,lead_status:leadStatus||undefined}});
      // Deleting the last row of a page must not leave the user stranded on
      // an empty page — step back one page and let the effect reload.
      if (data.items.length===0 && page>1) { setPage(p=>Math.max(1,p-1)); return; }
      setContacts(data.items); setTotal(data.total); }
    catch (err:any) { setError(err.response?.data?.detail||"Failed"); }
    finally { setLoading(false); }
  };

  const handleExport = async () => {
    try {
      const res = await api.get("/contacts/export/csv", {
        params: { search: search || undefined, lead_status: leadStatus || undefined },
        responseType: "blob",
      });
      const url = window.URL.createObjectURL(new Blob([res.data], { type: "text/csv" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = "contacts.csv";
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
      toast.success("Contacts exported");
    } catch { toast.error("Failed to export"); }
  };

  const [deletingId, setDeletingId] = useState<number | null>(null);
  const handleDelete = async (id:number) => { if(!confirm("Permanently delete this phone number? This cannot be undone."))return; setDeletingId(id); try { await api.delete(`/contacts/${id}`); toast.success("Contact permanently deleted"); loadContacts(); } catch { toast.error("Failed to delete"); } finally { setDeletingId(null); } };
  const clearSelection = () => { setSelected(new Set()); setAllMatching(false); };
  const handleBulkDelete = async () => {
    const count = allMatching ? total : selected.size;
    if (count === 0) return;
    const label = allMatching ? `${count} matching phone numbers` : `${count} phone numbers`;
    if(!confirm(`Permanently delete ${label}? This cannot be undone.`))return;
    try {
      if (allMatching) {
        // Server-side scope: delete everything matching the current filters
        // across every page — no need to enumerate thousands of ids.
        await api.post("/contacts/bulk",{contact_ids:[],action:"delete",scope:"all",search:search||undefined,lead_status:leadStatus||undefined});
      } else {
        await api.post("/contacts/bulk",{contact_ids:[...selected],action:"delete"});
      }
      toast.success(`${count} phone numbers permanently deleted`);
      clearSelection(); loadContacts();
    } catch { toast.error("Failed"); }
  };
  const handleBulkStatus = async (status:string) => {
    if (selected.size===0 || allMatching) return;
    try { await api.post("/contacts/bulk",{contact_ids:[...selected],action:"status",value:status}); toast.success(`Updated ${selected.size} contacts`); clearSelection(); loadContacts(); } catch { toast.error("Failed"); }
  };

  const doQuickSend = async () => { if(!quickMsg.trim()||!quickSendId)return; setQuickSending(true);
    try { await api.post("/send/",null,{params:{contact_id:quickSendId,body:quickMsg}}); toast.success("SMS sent!"); setQuickSendId(null); setQuickMsg(""); setQuickContact(null); }
    catch (err:any) { toast.error(err.response?.data?.detail||"Failed to send"); }
    finally { setQuickSending(false); }
  };

  const pageAll = contacts.length>0 && selected.size===contacts.length && !allMatching;
  const selectedCount = allMatching ? total : selected.size;

  // Tapping a row while in "all matching" mode drops out of that mode and
  // keeps the visible page (minus the tapped contact) as the new selection,
  // so what the checkboxes show always matches what will be deleted.
  const toggleSelect = (id:number) => {
    if (allMatching) {
      const n=new Set(contacts.map(c=>c.id)); n.delete(id);
      setSelected(n); setAllMatching(false);
      return;
    }
    const n=new Set(selected); n.has(id)?n.delete(id):n.add(id); setSelected(n);
  };
  // Three states: select the 25 on this page -> "select all N matching"
  // (every page of the current search/status view) -> clear everything.
  const toggleSelectAll = () => {
    if (allMatching) { clearSelection(); return; }
    if (pageAll) {
      if (total > contacts.length) setAllMatching(true);
      else setSelected(new Set());
    } else setSelected(new Set(contacts.map(c=>c.id)));
  };

  const totalPages = Math.ceil(total/25);
  if(error) return (<div className="text-center py-12"><h2 className="text-xl font-semibold mb-2">Error</h2><p className="text-gray-500 mb-4">{error}</p><button onClick={loadContacts} className="btn-primary">Retry</button></div>);

  return (
    <div className="space-y-3 pb-20 lg:pb-0">
      {/* Header - WhatsApp style */}
      <div className="flex flex-col gap-3">
        <div className="flex items-center justify-between gap-2">
          <div>
            <h1 className="text-[22px] font-bold text-[#111b21] dark:text-white flex items-center gap-2">
              <span className="w-8 h-8 rounded-full bg-[#00a884] flex items-center justify-center text-white text-sm"><User size={16}/></span>
              Contacts
            </h1>
            <p className="text-[13px] text-[#667781] dark:text-[#8696a0]">{total} contacts • Tap card to message</p>
          </div>
          <div className="flex gap-1.5">
            <button onClick={async()=>{
              if(!confirm("Scan all contacts and mark invalid / previously-failed numbers as undeliverable so they are never billed again?")) return;
              try {
                const {data}=await api.post("/contacts/clean");
                toast.success(`Scanned ${data.scanned}. Quarantined ${data.quarantined} bad numbers (${data.invalid_format} invalid, ${data.previous_failures} bounced).`);
                loadContacts();
              } catch { toast.error("Clean failed"); }
            }} className="w-10 h-10 lg:w-auto lg:px-3 lg:py-2 rounded-full lg:rounded-lg bg-[#f0f2f5] dark:bg-[#202c33] text-[#54656f] dark:text-[#aebac1] flex items-center justify-center gap-1.5 text-sm font-medium hover:bg-[#e9edef] dark:hover:bg-[#2a3942]" title="Quarantine numbers that fail delivery">
              <span className="hidden lg:inline">Clean list</span>
              <span className="lg:hidden">✓</span>
            </button>
            <button onClick={()=>setShowImport(true)} className="w-10 h-10 lg:w-auto lg:px-3 lg:py-2 rounded-full lg:rounded-lg bg-[#f0f2f5] dark:bg-[#202c33] text-[#54656f] dark:text-[#aebac1] flex items-center justify-center gap-1.5 text-sm font-medium hover:bg-[#e9edef] dark:hover:bg-[#2a3942]">
              <Upload size={16}/> <span className="hidden lg:inline">Import</span>
            </button>
            <button onClick={handleExport} className="w-10 h-10 lg:w-auto lg:px-3 lg:py-2 rounded-full lg:rounded-lg bg-[#f0f2f5] dark:bg-[#202c33] text-[#54656f] dark:text-[#aebac1] flex items-center justify-center gap-1.5 text-sm font-medium hover:bg-[#e9edef] dark:hover:bg-[#2a3942]" title="Export CSV">
              <Download size={16}/> <span className="hidden lg:inline">Export</span>
            </button>
            <button onClick={()=>setShowAdd(true)} className="w-10 h-10 lg:w-auto lg:px-4 lg:py-2 rounded-full lg:rounded-lg bg-[#00a884] hover:bg-[#06cf9c] text-white flex items-center justify-center gap-1.5 text-sm font-medium shadow-sm">
              <Plus size={18}/> <span className="hidden lg:inline">Add</span>
            </button>
          </div>
        </div>

        {/* Selected bar - sticky like WhatsApp */}
        {selectedCount>0 && (
          <div className="bg-[#00a884] text-white rounded-xl px-3 lg:px-4 py-2.5 shadow-sm">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-sm font-medium flex items-center gap-2 flex-wrap">
                <button onClick={clearSelection} className="w-7 h-7 rounded-full bg-white/20 flex items-center justify-center"><X size={14}/></button>
                {allMatching
                  ? <span>All {total} matching selected</span>
                  : <span>{selected.size} selected</span>}
                {!allMatching && selected.size>0 && total>selected.size && (
                  <button onClick={()=>setAllMatching(true)} className="bg-white/20 hover:bg-white/30 px-2.5 py-1 rounded-full text-xs font-semibold">
                    Select all {total} matching
                  </button>
                )}
              </span>
              <div className="flex items-center gap-1.5 flex-wrap">
                {!allMatching && (
                  <>
                    <select className="bg-white text-[#111b21] px-2 py-1.5 rounded-full text-xs font-medium" onChange={e=>{if(e.target.value)handleBulkStatus(e.target.value); e.target.value=""}} value="">
                      <option value="">Status…</option>{LEAD_STATUSES.map(s=><option key={s} value={s}>{s}</option>)}
                    </select>
                    <button onClick={()=>setShowListModal(true)} className="bg-white text-[#008069] px-3 py-1.5 rounded-full text-xs font-semibold flex items-center gap-1"><ListPlus size={12}/><span className="hidden sm:inline">List</span></button>
                  </>
                )}
                <button onClick={handleBulkDelete} className="bg-white text-[#c5221f] px-3 py-1.5 rounded-full text-xs font-semibold flex items-center gap-1"><Trash2 size={12}/>Delete permanently</button>
              </div>
            </div>
            {allMatching && (
              <p className="text-[11px] text-white/80 mt-1.5 leading-snug">
                Delete permanently will remove all {total} phone numbers matching your current search &amp; status — across every page, not just this one.
              </p>
            )}
          </div>
        )}
      </div>

      {/* Search + filter - card */}
      <div className="bg-white dark:bg-[#202c33] rounded-xl p-2.5 flex gap-2 shadow-sm border border-gray-100 dark:border-[#2a3942]">
        <div className="relative flex-1">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#667781] dark:text-[#8696a0]"/>
          <input type="text" className="w-full pl-9 pr-3 py-2.5 bg-[#f0f2f5] dark:bg-[#111b21] rounded-full text-[15px] placeholder:text-[#667781] focus:outline-none focus:ring-2 focus:ring-[#00a884]/20 text-[#111b21] dark:text-white border border-transparent" placeholder="Search name, business or phone…" value={search} onChange={e=>{setSearch(e.target.value);setPage(1);clearSelection();}}/>
        </div>
        <select className="bg-[#f0f2f5] dark:bg-[#111b21] text-[#54656f] dark:text-[#aebac1] rounded-full px-3 py-2.5 text-sm font-medium border-0 focus:ring-2 focus:ring-[#00a884]/20 outline-none" value={leadStatus} onChange={e=>{setLeadStatus(e.target.value);setPage(1);clearSelection();}}>
          <option value="">All</option>{LEAD_STATUSES.map(s=><option key={s} value={s}>{s}</option>)}
        </select>
      </div>

      {/* MOBILE CARDS - WhatsApp style list */}
      <div className="block lg:hidden space-y-2">
        {!loading && contacts.length>0 && (
          <div className="flex items-center justify-between px-1">
            <span className="text-[12px] text-[#667781] dark:text-[#8696a0]">{total} contacts</span>
            <button
              onClick={toggleSelectAll}
              className="flex items-center gap-1.5 text-[13px] font-semibold text-[#00a884] dark:text-[#00dfa2] py-1 px-1"
              title={allMatching ? "Clear selection" : pageAll && total > contacts.length ? "Select every matching contact, on every page" : "Select all on this page"}
            >
              {allMatching
                ? <><X size={14}/> Clear</>
                : <><CheckSquare size={15}/>{pageAll ? (total > contacts.length ? `Select all ${total} matching` : "Clear") : "Select all"}</>}
            </button>
          </div>
        )}
        {loading ? [...Array(5)].map((_,i)=>(
          <div key={i} className="bg-white dark:bg-[#202c33] rounded-xl p-3 flex gap-3 animate-pulse">
            <div className="w-12 h-12 rounded-full bg-gray-200 dark:bg-gray-700"/>
            <div className="flex-1 space-y-2"><div className="h-3 bg-gray-200 dark:bg-gray-700 rounded w-1/2"/><div className="h-2 bg-gray-100 dark:bg-gray-800 rounded w-3/4"/></div>
          </div>
        ))
        : contacts.length===0 ? (
          <div className="bg-white dark:bg-[#202c33] rounded-xl p-8 text-center">
            <div className="w-16 h-16 rounded-full bg-[#f0f2f5] dark:bg-[#111b21] flex items-center justify-center mx-auto mb-3"><User size={28} className="text-[#667781]"/></div>
            <p className="text-[#111b21] dark:text-white font-medium">{search||leadStatus?"No matches":"No contacts yet"}</p>
            <p className="text-sm text-[#667781] dark:text-[#8696a0] mt-1">{search||leadStatus?"Try another search":"Import a CSV or add your first restaurant contact"}</p>
            <button onClick={()=>setShowAdd(true)} className="mt-3 bg-[#00a884] text-white px-4 py-2 rounded-full text-sm font-medium">Add contact</button>
          </div>
        )
        : contacts.map(c => {
          const isSelected = allMatching || selected.has(c.id);
          const name = `${c.first_name||""} ${c.last_name||""}`.trim() || c.business_name || "Unnamed";
          return (
            <div key={c.id} className={`bg-white dark:bg-[#202c33] rounded-xl overflow-hidden shadow-sm border ${isSelected?"border-[#00a884] ring-1 ring-[#00a884]":"border-gray-100 dark:border-[#2a3942]"} transition-all`}>
              {/* top row */}
              <div className="flex gap-3 p-3">
                <button onClick={()=>toggleSelect(c.id)} className={`w-12 h-12 rounded-full flex items-center justify-center text-white font-semibold flex-shrink-0 relative ${avatarColor(name)}`}>
                  {initials(c)}
                  <span className={`absolute -bottom-1 -right-1 w-5 h-5 rounded-full border-2 border-white dark:border-[#202c33] flex items-center justify-center ${isSelected?"bg-[#00a884]":"bg-white dark:bg-[#111b21]"}`}>
                    {isSelected ? <CheckSquare size={12} className="text-white"/> : <Square size={12} className="text-[#667781]"/>}
                  </span>
                </button>
                <div className="flex-1 min-w-0" onClick={()=>toggleSelect(c.id)}>
                  <div className="flex items-start justify-between gap-2">
                    <h3 className="font-semibold text-[15px] leading-tight text-[#111b21] dark:text-white truncate pr-2">{name}</h3>
                    <span className={`text-[11px] px-2 py-0.5 rounded-full font-medium whitespace-nowrap flex-shrink-0 ${statusBadge(c.lead_status)}`}>{c.lead_status}</span>
                  </div>
                  <div className="flex items-center gap-1.5 mt-1 text-[13px] text-[#111b21] dark:text-[#e9edef]">
                    <Phone size={12} className="text-[#00a884] flex-shrink-0"/>
                    <span className="font-medium tracking-wide">{c.phone_number}</span>
                    <button onClick={(e)=>{e.stopPropagation(); navigator.clipboard.writeText(c.phone_number); toast.success("Copied");}} className="text-[#667781] hover:text-[#00a884] ml-1">⎘</button>
                  </div>
                  {c.business_name && c.business_name !== name && (
                    <div className="flex items-center gap-1.5 mt-1 text-[12px] text-[#667781] dark:text-[#8696a0]">
                      <Building2 size={12}/> <span className="truncate">{c.business_name}</span>
                    </div>
                  )}
                  {(c.city||c.state) && (
                    <div className="flex items-center gap-1.5 mt-0.5 text-[12px] text-[#667781] dark:text-[#8696a0]">
                      <MapPin size={12}/> <span>{[c.city,c.state].filter(Boolean).join(", ")}</span>
                    </div>
                  )}
                  {c.tags && c.tags.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-1.5">
                      {c.tags.map(t=>(<span key={t} className="text-[10px] px-1.5 py-0.5 rounded-full bg-[#e7f3ff] text-[#0066cc]">{t}</span>))}
                    </div>
                  )}
                </div>
              </div>
              {/* action bar */}
              <div className="px-3 pb-2">
                <ContactActions
                  layout="bar"
                  contactId={c.id}
                  phone={c.phone_number}
                  name={`${c.first_name||""} ${c.last_name||""}`.trim()||c.business_name||undefined}
                  website={c.website}
                  onSms={()=>{setQuickSendId(c.id);setQuickContact(c);setQuickMsg(`Hi ${c.first_name||c.business_name||"there"}! 👋 This is a quick message from our restaurant promo team. Reply STOP to opt out.`);}}
                />
              </div>
              <div className="flex gap-2 px-3 pb-3">
                <button
                  onClick={()=>setProfileId(c.id)}
                  className="w-[56px] bg-[#f0f2f5] dark:bg-[#2a3942] hover:bg-[#e9edef] text-[#54656f] dark:text-[#aebac1] rounded-full py-2.5 flex items-center justify-center active:scale-[0.98] transition-transform"
                  title="View everything imported for this contact"
                >
                  <IdCard size={16}/>
                </button>
                <button
                  onClick={()=>handleDelete(c.id)}
                  disabled={deletingId===c.id}
                  className="w-[56px] bg-[#fce8e6] dark:bg-[#2a3942] hover:bg-[#f8d7da] text-[#c5221f] dark:text-[#f15c6d] rounded-full py-2.5 flex items-center justify-center active:scale-[0.98] transition-transform disabled:opacity-50"
                  title="Delete permanently"
                >
                  {deletingId===c.id ? <span className="w-4 h-4 border-2 border-[#c5221f] border-t-transparent rounded-full animate-spin"/> : <Trash2 size={16}/>}
                </button>
              </div>
            </div>
          );
        })}
        {totalPages>1 && (
          <div className="flex items-center justify-between bg-white dark:bg-[#202c33] rounded-xl px-3 py-2">
            <span className="text-[12px] text-[#667781]">{total} contacts • page {page}/{totalPages}</span>
            <div className="flex gap-2">
              <button onClick={()=>setPage(p=>Math.max(1,p-1))} disabled={page===1} className="w-9 h-9 rounded-full bg-[#f0f2f5] dark:bg-[#111b21] flex items-center justify-center disabled:opacity-40"><ChevronLeft size={16}/></button>
              <button onClick={()=>setPage(p=>Math.min(totalPages,p+1))} disabled={page===totalPages} className="w-9 h-9 rounded-full bg-[#f0f2f5] dark:bg-[#111b21] flex items-center justify-center disabled:opacity-40"><ChevronRight size={16}/></button>
            </div>
          </div>
        )}
      </div>

      {/* DESKTOP TABLE */}
      <div className="hidden lg:block bg-white dark:bg-[#202c33] rounded-xl overflow-hidden shadow-sm border border-gray-100 dark:border-[#2a3942]">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-[#f0f2f5] dark:bg-[#111b21] text-left">
              <tr>
                <th className="px-4 py-3 w-10">
                  <input
                    type="checkbox"
                    ref={(el)=>{ if (el) el.indeterminate = !allMatching && selected.size>0 && selected.size<contacts.length; }}
                    onChange={toggleSelectAll}
                    checked={allMatching || pageAll}
                    title={allMatching ? "Clear selection" : pageAll && total > contacts.length ? `Select all ${total} matching contacts (every page)` : "Select all on this page"}
                    className="rounded accent-[#00a884]"
                  />
                </th>
                <th className="px-3 py-3 text-[11px] font-semibold text-[#667781] uppercase tracking-wider">Contact</th>
                <th className="px-3 py-3 text-[11px] font-semibold text-[#667781] uppercase">Phone</th>
                <th className="px-3 py-3 text-[11px] font-semibold text-[#667781] uppercase">Business</th>
                <th className="px-3 py-3 text-[11px] font-semibold text-[#667781] uppercase">Status</th>
                <th className="px-3 py-3 text-[11px] font-semibold text-[#667781] uppercase text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-[#2a3942]">
              {loading ? [...Array(5)].map((_,i)=>(<tr key={i}>{[...Array(6)].map((_,j)=>(<td key={j} className="px-4 py-3"><div className="h-4 bg-gray-100 dark:bg-gray-700 rounded animate-pulse"/></td>))}</tr>))
              : contacts.length===0 ? (<tr><td colSpan={6} className="px-4 py-12 text-center text-[#667781]">{search||leadStatus?"No matches":"No contacts. Import or add one."}</td></tr>)
              : contacts.map(c => (
                <tr key={c.id} className={`hover:bg-[#f5f6f6] dark:hover:bg-[#111b21] ${allMatching||selected.has(c.id)?"bg-[#f0f9f6] dark:bg-[#0a332c]":""}`}>
                  <td className="px-4 py-3"><input type="checkbox" checked={allMatching||selected.has(c.id)} onChange={()=>toggleSelect(c.id)} title={allMatching?"Tap to keep this contact":"Select"} className="rounded accent-[#00a884]"/></td>
                  <td className="px-3 py-3">
                    <div className="flex items-center gap-2.5">
                      <div className={`w-8 h-8 rounded-full flex items-center justify-center text-white text-xs font-semibold ${avatarColor(`${c.first_name||""} ${c.last_name||""}`.trim()||c.business_name||c.phone_number)}`}>{initials(c)}</div>
                      <span className="font-medium text-[13px] text-[#111b21] dark:text-white">{c.first_name} {c.last_name}</span>
                    </div>
                  </td>
                  <td className="px-3 py-3 text-[13px] font-medium text-[#111b21] dark:text-[#e9edef]">{c.phone_number}</td>
                  <td className="px-3 py-3 text-[13px] text-[#667781] dark:text-[#8696a0] max-w-[160px]">
                    <div className="truncate">{c.business_name||"—"}</div>
                    {c.tags && c.tags.length > 0 && (
                      <div className="flex flex-wrap gap-1 mt-1">
                        {c.tags.slice(0,3).map(t=>(<span key={t} className="text-[10px] px-1.5 py-0.5 rounded-full bg-[#e7f3ff] text-[#0066cc]">{t}</span>))}
                        {c.tags.length > 3 && <span className="text-[10px] text-[#667781]">+{c.tags.length-3}</span>}
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-3"><span className={`text-[11px] px-2 py-1 rounded-full font-medium ${statusBadge(c.lead_status)}`}>{c.lead_status}</span></td>
                  <td className="px-3 py-3">
                    <div className="flex justify-end gap-1 items-center">
                      <ContactActions
                        contactId={c.id}
                        phone={c.phone_number}
                        name={`${c.first_name||""} ${c.last_name||""}`.trim()||c.business_name||undefined}
                        website={c.website}
                        onSms={()=>{setQuickSendId(c.id);setQuickContact(c);setQuickMsg("")}}
                      />
                      <button onClick={()=>setProfileId(c.id)} className="w-8 h-8 rounded-full bg-[#f0f2f5] dark:bg-[#2a3942] hover:bg-[#00a884] hover:text-white text-[#54656f] dark:text-[#aebac1] flex items-center justify-center" title="View full profile"><IdCard size={14}/></button>
                      <button onClick={()=>handleDelete(c.id)} disabled={deletingId===c.id} className="w-8 h-8 rounded-full bg-red-50 hover:bg-red-500 hover:text-white text-red-500 flex items-center justify-center disabled:opacity-50" title="Delete permanently">
                        {deletingId===c.id ? <span className="w-3.5 h-3.5 border-2 border-red-500 border-t-transparent rounded-full animate-spin"/> : <Trash2 size={14}/>}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {totalPages>1 && (<div className="flex items-center justify-between px-4 py-3 bg-[#f0f2f5] dark:bg-[#111b21] border-t border-gray-100 dark:border-[#2a3942]"><span className="text-[13px] text-[#667781]">{total} contacts</span><div className="flex gap-2"><button onClick={()=>setPage(p=>Math.max(1,p-1))} disabled={page===1} className="btn-secondary btn-sm"><ChevronLeft size={14}/></button><span className="text-sm self-center px-2">{page}/{totalPages}</span><button onClick={()=>setPage(p=>Math.min(totalPages,p+1))} disabled={page===totalPages} className="btn-secondary btn-sm"><ChevronRight size={14}/></button></div></div>)}
      </div>

      {showAdd && <AddContactModal lists={[]} onClose={()=>{setShowAdd(false);loadContacts()}} />}
      {showImport && <ImportContactsModal onClose={()=>setShowImport(false)} onDone={()=>{setShowImport(false);loadContacts()}}/>}
      {profileId !== null && <ContactProfileModal contactId={profileId} onClose={()=>setProfileId(null)}/>}
      {showListModal && <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/50 p-0 sm:p-4"><div className="bg-white dark:bg-[#202c33] w-full sm:max-w-md rounded-t-2xl sm:rounded-xl p-4 sm:p-6 max-h-[92vh] overflow-y-auto"><div className="flex justify-between mb-4"><h2 className="text-lg font-semibold text-[#111b21] dark:text-white">Add to List</h2><button onClick={()=>setShowListModal(false)} className="w-8 h-8 rounded-full bg-[#f0f2f5] dark:bg-[#111b21] flex items-center justify-center"><X size={16}/></button></div><p className="text-sm text-[#667781] mb-3">{selected.size} contacts</p><ListPicker value={selectedListId} onChange={setSelectedListId} placeholder="Select list... (or create one)" allowNone={false} /><button onClick={async()=>{if(!selectedListId){toast.error("Select a list");return};try{await api.post(`/lists/${selectedListId}/contacts`,[...selected]);toast.success("Added to list");setShowListModal(false);setSelected(new Set());setSelectedListId("");loadContacts();}catch{toast.error("Failed")}}} className="bg-[#00a884] text-white w-full rounded-full py-3 mt-4 font-semibold">Add to List</button></div></div>}

      {/* WhatsApp-style Quick Send - bottom sheet */}
      {quickSendId && (
        <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/50 p-0 sm:p-4" onClick={()=>setQuickSendId(null)}>
          <div className="bg-[#efeae2] dark:bg-[#111b21] w-full sm:max-w-md rounded-t-[20px] sm:rounded-2xl max-h-[92vh] flex flex-col overflow-hidden" onClick={e=>e.stopPropagation()}>
            {/* sheet header like WhatsApp */}
            <div className="bg-[#008069] dark:bg-[#202c33] px-4 py-3 flex items-center gap-3 flex-shrink-0">
              <button onClick={()=>{setQuickSendId(null);setQuickContact(null);}} className="w-8 h-8 rounded-full hover:bg-white/10 flex items-center justify-center text-white"><X size={18}/></button>
              <div className={`w-8 h-8 rounded-full flex items-center justify-center text-white text-sm font-semibold ${quickContact?avatarColor(`${quickContact.first_name||""}`):"bg-[#00a884]"}`}>{quickContact?initials(quickContact):"?"}</div>
              <div className="flex-1 min-w-0">
                <p className="text-white font-semibold text-[15px] truncate">{quickContact? `${quickContact.first_name||""} ${quickContact.last_name||""}`.trim()||quickContact.business_name||quickContact.phone_number : "Send SMS"}</p>
                <p className="text-white/70 text-[12px] truncate">{quickContact?.phone_number||""} • WhatsApp style</p>
              </div>
            </div>

            {/* chat preview bg */}
            <div className="flex-1 overflow-y-auto p-4 space-y-3 min-h-[120px]" style={{backgroundColor:"#efeae2", backgroundImage:`url("data:image/svg+xml,%3Csvg width='100' height='100' viewBox='0 0 100 100' xmlns='http://www.w3.org/2000/svg'%3E%3Cg fill='%23d1d7db' fill-opacity='0.2'%3E%3Cpath d='M20 20h10v10H20zM50 50h10v10H50z'/%3E%3C/g%3E%3C/svg%3E")`}}>
              {quickContact && (
                <div className="flex justify-center">
                  <div className="bg-[#fff8c4] text-[11px] px-3 py-1.5 rounded-lg shadow-sm text-[#54656f] text-center max-w-[90%]">
                    🔒 This will be sent as SMS via your SIM. Message preview below.
                  </div>
                </div>
              )}
              {/* preview bubble */}
              {quickMsg.trim() && (
                <div className="flex justify-end">
                  <div className="bg-[#d9fdd3] rounded-lg rounded-tr-none px-3 py-2 shadow-sm max-w-[85%] relative">
                    <p className="text-[14px] whitespace-pre-wrap break-words text-[#111b21]">{quickMsg}</p>
                    <p className="text-[10px] text-[#667781] text-right mt-1">{new Date().toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"})} ✓✓</p>
                  </div>
                </div>
              )}
            </div>

            {/* composer */}
            <div className="bg-[#f0f2f5] dark:bg-[#202c33] p-2 flex items-end gap-2 flex-shrink-0">
              <div className="flex-1 bg-white dark:bg-[#2a3942] rounded-3xl px-3 py-2 flex items-end gap-2 shadow-sm">
                <textarea
                  className="flex-1 bg-transparent outline-none resize-none py-1.5 text-[15px] placeholder:text-[#667781] text-[#111b21] dark:text-white max-h-28"
                  rows={3}
                  placeholder="Type a message… Use {{first_name}} etc."
                  value={quickMsg}
                  onChange={e=>setQuickMsg(e.target.value)}
                  autoFocus
                />
              </div>
              <button onClick={doQuickSend} disabled={quickSending||!quickMsg.trim()} className="w-11 h-11 rounded-full bg-[#00a884] hover:bg-[#06cf9c] text-white flex items-center justify-center flex-shrink-0 disabled:opacity-50 shadow-sm">
                {quickSending ? <span className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"/> : <MessageSquare size={18}/>}
              </button>
            </div>
            <div className="bg-white dark:bg-[#202c33] px-4 py-2 flex items-center justify-between text-[11px] text-[#667781] dark:text-[#8696a0]">
              <span>{quickMsg.length} chars • {quickMsg.length<=160?1:Math.ceil(quickMsg.length/153)} SMS • ~{quickMsg.length<=160?4:Math.ceil(quickMsg.length/153)*4} NGN</span>
              <span className="hidden sm:inline">Enter to send • Shift+Enter for newline</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function AddContactModal({ onClose }: { lists: any[]; onClose: () => void }) {
  const [f, setF] = useState({ first_name:"",last_name:"",business_name:"",phone_number:"",email:"",city:"",state:"",website:"",industry:"",source:"",lead_status:"new",list_id:"" });
  const [sub, setSub] = useState(false);

  const handle = async (e:React.FormEvent) => { e.preventDefault(); if(!f.phone_number){toast.error("Phone required");return;} setSub(true);
    try { const res = await api.post("/contacts/", f);
      if(f.list_id) { try { await api.post(`/lists/${f.list_id}/contacts`,[(res.data as any).id]); } catch {} }
      toast.success("Contact created"); onClose();
    } catch (err:any) { toast.error(err.response?.data?.detail||"Failed"); }
    finally { setSub(false); }
  };

  return (<div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/50 p-0 sm:p-4"><div className="bg-white dark:bg-[#202c33] w-full sm:max-w-lg max-h-[92vh] overflow-y-auto rounded-t-[20px] sm:rounded-2xl"><div className="sticky top-0 bg-[#008069] dark:bg-[#202c33] px-4 py-3 flex items-center justify-between"><h2 className="text-white font-semibold flex items-center gap-2"><User size={18}/> Add Contact</h2><button onClick={onClose} className="w-8 h-8 rounded-full bg-white/10 flex items-center justify-center text-white"><X size={16}/></button></div>
    <form onSubmit={handle} className="p-4 sm:p-6 space-y-3">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3"><div><label className="text-xs font-medium text-[#54656f] dark:text-[#8696a0]">First Name</label><input className="w-full mt-1 px-3 py-2.5 bg-[#f0f2f5] dark:bg-[#111b21] rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-[#00a884]/20" value={f.first_name} onChange={e=>setF({...f,first_name:e.target.value})}/></div><div><label className="text-xs font-medium text-[#54656f]">Last Name</label><input className="w-full mt-1 px-3 py-2.5 bg-[#f0f2f5] dark:bg-[#111b21] rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-[#00a884]/20" value={f.last_name} onChange={e=>setF({...f,last_name:e.target.value})}/></div></div>
      <div><label className="text-xs font-medium text-[#54656f]">Phone Number *</label><input className="w-full mt-1 px-3 py-2.5 bg-[#f0f2f5] dark:bg-[#111b21] rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-[#00a884]/20" placeholder="08012345678" value={f.phone_number} onChange={e=>setF({...f,phone_number:e.target.value})} required/></div>
      <div><label className="text-xs font-medium text-[#54656f]">Business / Restaurant Name</label><input className="w-full mt-1 px-3 py-2.5 bg-[#f0f2f5] dark:bg-[#111b21] rounded-xl text-sm" placeholder="e.g. Chicken Republic, Mama Gold" value={f.business_name} onChange={e=>setF({...f,business_name:e.target.value})}/></div>
      <div><label className="text-xs font-medium text-[#54656f]">Email</label><input className="w-full mt-1 px-3 py-2.5 bg-[#f0f2f5] dark:bg-[#111b21] rounded-xl text-sm" type="email" value={f.email} onChange={e=>setF({...f,email:e.target.value})}/></div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3"><div><label className="text-xs font-medium text-[#54656f]">City</label><input className="w-full mt-1 px-3 py-2.5 bg-[#f0f2f5] dark:bg-[#111b21] rounded-xl text-sm" value={f.city} onChange={e=>setF({...f,city:e.target.value})}/></div><div><label className="text-xs font-medium text-[#54656f]">State</label><input className="w-full mt-1 px-3 py-2.5 bg-[#f0f2f5] dark:bg-[#111b21] rounded-xl text-sm" value={f.state} onChange={e=>setF({...f,state:e.target.value})}/></div></div>
      <div><label className="text-xs font-medium text-[#54656f]">Industry</label><input className="w-full mt-1 px-3 py-2.5 bg-[#f0f2f5] dark:bg-[#111b21] rounded-xl text-sm" placeholder="Restaurant, Fast Food, etc." value={f.industry} onChange={e=>setF({...f,industry:e.target.value})}/></div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div><label className="text-xs font-medium text-[#54656f]">Lead Status</label><select className="w-full mt-1 px-3 py-2.5 bg-[#f0f2f5] dark:bg-[#111b21] rounded-xl text-sm" value={f.lead_status} onChange={e=>setF({...f,lead_status:e.target.value})}>{LEAD_STATUSES.map(s=><option key={s} value={s}>{s}</option>)}</select></div>
        <div><label className="text-xs font-medium text-[#54656f]">Add to List</label><div className="mt-1"><ListPicker value={f.list_id} onChange={v=>setF({...f,list_id:v})} placeholder="No list — pick or create one" /></div></div>
      </div>
      <div className="flex gap-2 pt-2"><button type="button" onClick={onClose} className="flex-1 py-3 rounded-full bg-[#f0f2f5] dark:bg-[#111b21] text-[#54656f] dark:text-white font-medium">Cancel</button><button type="submit" disabled={sub} className="flex-1 py-3 rounded-full bg-[#00a884] text-white font-semibold disabled:opacity-50">{sub?"Creating...":"Create Contact"}</button></div>
    </form></div></div>);
}
