import { useState, useEffect } from "react";
import api from "../api/client";
import { Contact, PaginatedResponse } from "../types";
import toast from "react-hot-toast";
import { Plus, Search, Trash2, Upload, Download, ChevronLeft, ChevronRight, X, ListPlus, MessageSquare, Phone, Mail, MapPin, Building2, User, CheckSquare, Square, IdCard, Sparkles } from "lucide-react";
import ContactProfileModal from "../components/ContactProfileModal";
import ContactActions from "../components/ContactActions";
import ImportContactsModal from "../components/ImportContactsModal";
import ListPicker from "../components/ListPicker";
import {
  contactChannel,
  contactInitials,
  contactLabel,
  emailTrust,
  emailTrustClass,
  emailTrustLabel,
} from "../utils/contact";

const LEAD_STATUSES = ["new","contacted","replied","interested","follow-up","meeting","customer","not_interested","closed"];
const statusBadge = (s:string) => { const m:Record<string,string>={new:"bg-primary-50 text-primary-700",contacted:"bg-warning-100 text-warning-700",replied:"bg-primary-100 text-primary-700",interested:"bg-primary-100 text-primary-800", "follow-up":"bg-warning-200 text-warning-700",meeting:"bg-primary-50 text-primary-600",customer:"bg-primary-100 text-primary-700",not_interested:"bg-danger-100 text-danger-700",closed:"bg-gray-100 text-gray-600"}; return m[s]||"bg-gray-100 text-gray-600"; };

const avatarColor = (name:string) => {
  const colors = ["bg-primary-600","bg-primary-700","bg-primary-800","bg-[#34B7F1]","bg-warning-400","bg-accent-500","bg-primary-400","bg-warning-400","bg-primary-500","bg-primary-400"];
  let h=0; for(let i=0;i<name.length;i++) h=(h*31+name.charCodeAt(i))%colors.length;
  return colors[h];
};
// A contact with no phone is normal now (they came in through an email
// import), so initials and avatars must never assume one exists.
const initials = (c:Contact) => contactInitials(c);

export { customKey, detectColumns } from "../utils/csv";

export default function ContactsPage() {
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState(""); const [leadStatus, setLeadStatus] = useState("");
  //: Email-only view of the same list: who can be emailed, who cannot, why.
  const [emailState, setEmailState] = useState("");
  //: Channel view: SMS contacts / Email contacts / All
  const [channel, setChannel] = useState<string>("");
  //: Which list to view (when the user opens a list from the sidebar)
  const [listId, setListId] = useState<string>("");
  const [lists, setLists] = useState<{id:number;name:string}[]>([]);
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
  // Enrichment: which provider keys exist (drives whether the button is
  // usable) and the in-flight state.
  const [enriching, setEnriching] = useState(false);
  const [enrichProviders, setEnrichProviders] = useState<string[]>([]);
  const [enrichInfo, setEnrichInfo] = useState<{with_email:number; without_email:number; verified:number; unverified:number}|null>(null);

  useEffect(()=>{
    // Best-effort: a failed status call must never break the Contacts page.
    api.get("/contacts/enrich/status")
      .then(({data})=>{ setEnrichProviders(data.providers || []); setEnrichInfo(data.contacts || null); })
      .catch(()=>{ setEnrichProviders([]); });
    api.get("/lists/",{params:{per_page:500}})
      .then(({data})=>{ setLists(data.items||[]); })
      .catch(()=>{});
  },[]);

  useEffect(()=>{ const t=setTimeout(()=>{ setDebouncedSearch(search); },350); return ()=>clearTimeout(t); },[search]);
  useEffect(()=>{loadContacts();},[page,debouncedSearch,leadStatus,emailState,channel,listId]);

  const loadContacts = async () => {
    try { setLoading(true); const {data}=await api.get<PaginatedResponse<Contact>>("/contacts/",{params:{page,per_page:25,search:debouncedSearch||undefined,lead_status:leadStatus||undefined,email_state:emailState||undefined,channel:channel||undefined,list_id:listId||undefined}});
      // Deleting the last row of a page must not leave the user stranded on
      // an empty page — step back one page and let the effect reload.
      if (data.items.length===0 && page>1) { setPage(p=>Math.max(1,p-1)); return; }
      setContacts(data.items); setTotal(data.total); }
    catch (err:any) { setError(err.response?.data?.detail||"Failed"); }
    finally { setLoading(false); }
  };

  /**
   * Run email enrichment on whatever the user is looking at.
   *
   * Scope follows the selection so the action always means "these ones": a
   * ticked set is enriched by id, otherwise the current filter decides. The
   * server caps a single run (see /contacts/enrich) so one click cannot drain
   * a provider's monthly free quota.
   */
  const handleEnrich = async () => {
    const targetsNoEmail = enrichInfo ? enrichInfo.without_email : 0;
    const useSelection = selected.size > 0 && !allMatching;
    const scope = useSelection ? "ids" : emailState === "no_email" ? "no_email" : "all";
    const label = useSelection
      ? `${selected.size} selected contact${selected.size===1?"":"s"}`
      : `up to 500 contacts matching this view`;
    const extra = !useSelection && targetsNoEmail > 0
      ? ` ${targetsNoEmail} have no address at all and can be looked up.`
      : "";
    if (!confirm(`Enrich emails for ${label}?${extra}`)) return;

    setEnriching(true);
    try {
      const { data } = await api.post("/contacts/enrich", null, {
        params: {
          scope,
          ...(useSelection ? { contact_ids: [...selected] } : {}),
          search: useSelection ? undefined : debouncedSearch || undefined,
          lead_status: useSelection ? undefined : leadStatus || undefined,
        },
        // A 500-row batch with provider calls can take a while.
        timeout: 300000,
      });
      const parts = [
        data.verified ? `${data.verified} verified` : null,
        data.found ? `${data.found} found` : null,
        data.inferred ? `${data.inferred} guessed` : null,
        data.skipped ? `${data.skipped} skipped` : null,
        data.failed ? `${data.failed} unavailable` : null,
      ].filter(Boolean);
      toast.success(
        parts.length ? `Enrichment done — ${parts.join(", ")}` : "Nothing to enrich",
      );
      if (data.failed && !data.verified && !data.found) {
        // Almost always a missing key or an exhausted free tier.
        toast(
          "No provider results — check the API keys and the monthly quota.",
          { icon: "ℹ️" },
        );
      }
      clearSelection();
      loadContacts();
      api.get("/contacts/enrich/status")
        .then(({data})=>setEnrichInfo(data.contacts || null))
        .catch(()=>{});
    } catch (err:any) {
      toast.error(err.response?.data?.detail || "Enrichment failed");
    } finally { setEnriching(false); }
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

  const [addingToList, setAddingToList] = useState(false);
  // Works for BOTH selection modes: ticked contacts are sent as ids, while
  // "all N matching" is resolved on the server from the current search/status
  // filters — so adding 12,000 contacts to a list is one request, not 12,000.
  const confirmAddToList = async () => {
    if(!selectedListId){toast.error("Select a list");return;}
    setAddingToList(true);
    try {
      if (allMatching) {
        const { data } = await api.post(`/lists/${selectedListId}/contacts/add-all`,{
          search: debouncedSearch || undefined,
          lead_status: leadStatus || undefined,
        });
        toast.success(`${data.added} contact${data.added===1?"":"s"} added to list${data.matched>data.added?` (${data.matched-data.added} already in it)`:""}`);
      } else {
        await api.post(`/lists/${selectedListId}/contacts`,[...selected]);
        toast.success(`${selected.size} contact${selected.size===1?"":"s"} added to list`);
      }
      setShowListModal(false); clearSelection(); setSelectedListId(""); loadContacts();
    } catch (err:any) { toast.error(err.response?.data?.detail||"Failed to add to list"); }
    finally { setAddingToList(false); }
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
            <h1 className="text-[22px] font-bold text-gray-900 dark:text-white flex items-center gap-2">
              <span className="w-8 h-8 rounded-full bg-primary-600 flex items-center justify-center text-white text-sm"><User size={16}/></span>
              Contacts
            </h1>
            <p className="text-[13px] text-gray-500 dark:text-gray-400">{total} contacts • Tap card to message</p>
          </div>
          <div className="flex gap-1.5">
            <button onClick={async()=>{
              if(!confirm("Scan all contacts and mark invalid / previously-failed numbers as undeliverable so they are never billed again?")) return;
              try {
                const {data}=await api.post("/contacts/clean");
                toast.success(`Scanned ${data.scanned}. Quarantined ${data.quarantined} bad numbers (${data.invalid_format} invalid, ${data.previous_failures} bounced).`);
                loadContacts();
              } catch { toast.error("Clean failed"); }
            }} className="w-10 h-10 lg:w-auto lg:px-3 lg:py-2 rounded-full lg:rounded-lg bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400 flex items-center justify-center gap-1.5 text-sm font-medium hover:bg-gray-200 dark:hover:bg-gray-700" title="Quarantine numbers that fail delivery">
              <span className="hidden lg:inline">Clean list</span>
              <span className="lg:hidden">✓</span>
            </button>
            <button onClick={async()=>{
              const useSelection = selected.size > 0 && !allMatching;
              const label = useSelection ? `${selected.size} selected contact${selected.size===1?"":"s"}` : `all ${total} contacts matching your view`;
              if(!confirm(`Validate email addresses for ${label}? Confirmed-bad addresses will be quarantined for email sends.`)) return;
              try {
                const params: any = { deep: true };
                if (useSelection) { params.contact_ids = [...selected]; params.scope = "ids"; }
                else { params.scope = "all";
                  if (search) params.search = search;
                  if (leadStatus) params.lead_status = leadStatus;
                  if (channel) params.channel = channel;
                  if (listId) params.list_id = listId;
                  if (emailState) params.email_state = emailState;
                }
                const {data}=await api.post("/contacts/validate-emails",null,{params});
                toast.success(`Validated ${data.scanned}. ${data.deliverable} good, ${data.undeliverable} bad, ${data.risky} risky.`);
                loadContacts();
              } catch (err:any) { toast.error(err.response?.data?.detail||"Validation failed"); }
            }} className="w-10 h-10 lg:w-auto lg:px-3 lg:py-2 rounded-full lg:rounded-lg bg-primary-50 dark:bg-primary-950 text-primary-700 dark:text-primary-300 flex items-center justify-center gap-1.5 text-sm font-medium hover:bg-primary-100 disabled:opacity-50" title="Validate email addresses with Reacher (https://reacher.email)">
              <Sparkles size={16}/> <span className="hidden lg:inline">Validate emails</span>
            </button>
            <button onClick={()=>setShowImport(true)} className="w-10 h-10 lg:w-auto lg:px-3 lg:py-2 rounded-full lg:rounded-lg bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400 flex items-center justify-center gap-1.5 text-sm font-medium hover:bg-gray-200 dark:hover:bg-gray-700">
              <Upload size={16}/> <span className="hidden lg:inline">Import</span>
            </button>
            <button onClick={handleExport} className="w-10 h-10 lg:w-auto lg:px-3 lg:py-2 rounded-full lg:rounded-lg bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400 flex items-center justify-center gap-1.5 text-sm font-medium hover:bg-gray-200 dark:hover:bg-gray-700" title="Export CSV">
              <Download size={16}/> <span className="hidden lg:inline">Export</span>
            </button>
            <button onClick={()=>setShowAdd(true)} className="w-10 h-10 lg:w-auto lg:px-4 lg:py-2 rounded-full lg:rounded-lg bg-primary-600 hover:bg-primary-500 text-white flex items-center justify-center gap-1.5 text-sm font-medium shadow-sm">
              <Plus size={18}/> <span className="hidden lg:inline">Add</span>
            </button>
          </div>
        </div>

        {/* Selected bar - sticky like WhatsApp */}
        {selectedCount>0 && (
          <div className="bg-primary-600 text-white rounded-xl px-3 lg:px-4 py-2.5 shadow-sm">
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
                  <select className="bg-white text-gray-900 px-2 py-1.5 rounded-full text-xs font-medium" onChange={e=>{if(e.target.value)handleBulkStatus(e.target.value); e.target.value=""}} value="">
                    <option value="">Status…</option>{LEAD_STATUSES.map(s=><option key={s} value={s}>{s}</option>)}
                  </select>
                )}
                <button onClick={()=>setShowListModal(true)} className="bg-white text-primary-700 px-3 py-1.5 rounded-full text-xs font-semibold flex items-center gap-1"><ListPlus size={12}/><span className="hidden sm:inline">Add to list</span><span className="sm:hidden">List</span></button>
                <button onClick={handleBulkDelete} className="bg-white text-danger-700 px-3 py-1.5 rounded-full text-xs font-semibold flex items-center gap-1"><Trash2 size={12}/>Delete permanently</button>
              </div>
            </div>
            {allMatching && (
              <p className="text-[11px] text-white/80 mt-1.5 leading-snug">
                Bulk actions cover all {total} contacts matching your current search &amp; status — across every page. “Add to list” adds them all in one go; “Delete permanently” removes them all.
              </p>
            )}
          </div>
        )}
      </div>

      {/* Search + filter - card */}
      <div className="bg-white dark:bg-gray-800 rounded-xl p-2.5 flex gap-2 shadow-sm border border-gray-100 dark:border-gray-700">
        <div className="relative flex-1">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500 dark:text-gray-400"/>
          <input type="text" className="w-full pl-9 pr-3 py-2.5 bg-gray-100 dark:bg-gray-900 rounded-full text-[15px] placeholder:text-gray-500 focus:outline-none focus:ring-2 focus:ring-primary-600/20 text-gray-900 dark:text-white border border-transparent" placeholder="Search name, business or phone…" value={search} onChange={e=>{setSearch(e.target.value);setPage(1);clearSelection();}}/>
        </div>
        <select className="bg-gray-100 dark:bg-gray-900 text-gray-600 dark:text-gray-400 rounded-full px-3 py-2.5 text-sm font-medium border-0 focus:ring-2 focus:ring-primary-600/20 outline-none" value={leadStatus} onChange={e=>{setLeadStatus(e.target.value);setPage(1);clearSelection();}}>
          <option value="">All</option>{LEAD_STATUSES.map(s=><option key={s} value={s}>{s}</option>)}
        </select>
        <select className="bg-gray-100 dark:bg-gray-900 text-gray-600 dark:text-gray-400 rounded-full px-3 py-2.5 text-sm font-medium border-0 focus:ring-2 focus:ring-primary-600/20 outline-none" value={channel} onChange={e=>{setChannel(e.target.value);setPage(1);clearSelection();}} title="Channel view">
          <option value="">All contacts</option>
          <option value="sms">SMS contacts</option>
          <option value="email">Email contacts</option>
        </select>
        <select className="bg-gray-100 dark:bg-gray-900 text-gray-600 dark:text-gray-400 rounded-full px-3 py-2.5 text-sm font-medium border-0 focus:ring-2 focus:ring-primary-600/20 outline-none" value={listId} onChange={e=>{setListId(e.target.value);setPage(1);clearSelection();}} title="Show only this list">
          <option value="">All lists</option>
          {lists.map(l=><option key={l.id} value={l.id}>{l.name}</option>)}
        </select>
        <select className="bg-gray-100 dark:bg-gray-900 text-gray-600 dark:text-gray-400 rounded-full px-3 py-2.5 text-sm font-medium border-0 focus:ring-2 focus:ring-primary-600/20 outline-none" value={emailState} onChange={e=>{setEmailState(e.target.value);setPage(1);clearSelection();}} title="Filter by email eligibility">
          <option value="">Email: all</option>
          <option value="emailable">Emailable</option>
          <option value="no_email">No email</option>
          <option value="verified">Verified</option>
          <option value="unverified">Unverified</option>
          <option value="inferred">Guessed (inferred)</option>
          <option value="unsubscribed">Unsubscribed</option>
          <option value="bounced">Bounced</option>
        </select>
        <button
          onClick={handleEnrich}
          disabled={enriching || !enrichProviders}
          title={
            enrichProviders
              ? "Look up missing addresses and verify the ones on file"
              : "Add a Hunter/ZeroBounce/NeverBounce API key in Settings to enable enrichment"
          }
          className="w-10 h-10 lg:w-auto lg:px-3 lg:py-2.5 rounded-full lg:rounded-full bg-primary-50 dark:bg-primary-950 text-primary-700 dark:text-primary-300 flex items-center justify-center gap-1.5 text-sm font-medium hover:bg-primary-100 disabled:opacity-50"
        >
          {enriching
            ? <span className="w-4 h-4 border-2 border-primary-600 border-t-transparent rounded-full animate-spin"/>
            : <Sparkles size={16}/>}
          <span className="hidden lg:inline">{enriching ? "Enriching…" : "Enrich emails"}</span>
        </button>
      </div>

      {/* MOBILE CARDS - WhatsApp style list */}
      <div className="block lg:hidden space-y-2">
        {!loading && contacts.length>0 && (
          <div className="flex items-center justify-between px-1">
            <span className="text-[12px] text-gray-500 dark:text-gray-400">{total} contacts</span>
            <button
              onClick={toggleSelectAll}
              className="flex items-center gap-1.5 text-[13px] font-semibold text-primary-600 dark:text-primary-500 py-1 px-1"
              title={allMatching ? "Clear selection" : pageAll && total > contacts.length ? "Select every matching contact, on every page" : "Select all on this page"}
            >
              {allMatching
                ? <><X size={14}/> Clear</>
                : <><CheckSquare size={15}/>{pageAll ? (total > contacts.length ? `Select all ${total} matching` : "Clear") : "Select all"}</>}
            </button>
          </div>
        )}
        {loading ? [...Array(5)].map((_,i)=>(
          <div key={i} className="bg-white dark:bg-gray-800 rounded-xl p-3 flex gap-3 animate-pulse">
            <div className="w-12 h-12 rounded-full bg-gray-200 dark:bg-gray-700"/>
            <div className="flex-1 space-y-2"><div className="h-3 bg-gray-200 dark:bg-gray-700 rounded w-1/2"/><div className="h-2 bg-gray-100 dark:bg-gray-800 rounded w-3/4"/></div>
          </div>
        ))
        : contacts.length===0 ? (
          <div className="bg-white dark:bg-gray-800 rounded-xl p-8 text-center">
            <div className="w-16 h-16 rounded-full bg-gray-100 dark:bg-gray-900 flex items-center justify-center mx-auto mb-3"><User size={28} className="text-gray-500"/></div>
            <p className="text-gray-900 dark:text-white font-medium">{search||leadStatus?"No matches":"No contacts yet"}</p>
            <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">{search||leadStatus?"Try another search":"Import a CSV or add your first restaurant contact"}</p>
            <button onClick={()=>setShowAdd(true)} className="mt-3 bg-primary-600 text-white px-4 py-2 rounded-full text-sm font-medium">Add contact</button>
          </div>
        )
        : contacts.map(c => {
          const isSelected = allMatching || selected.has(c.id);
          const name = `${c.first_name||""} ${c.last_name||""}`.trim() || c.business_name || "Unnamed";
          return (
            <div key={c.id} className={`bg-white dark:bg-gray-800 rounded-xl overflow-hidden shadow-sm border ${isSelected?"border-primary-600 ring-1 ring-primary-600":"border-gray-100 dark:border-gray-700"} transition-all`}>
              {/* top row */}
              <div className="flex gap-3 p-3">
                <button onClick={()=>toggleSelect(c.id)} className={`w-12 h-12 rounded-full flex items-center justify-center text-white font-semibold flex-shrink-0 relative ${avatarColor(name)}`}>
                  {initials(c)}
                  <span className={`absolute -bottom-1 -right-1 w-5 h-5 rounded-full border-2 border-white dark:border-gray-800 flex items-center justify-center ${isSelected?"bg-primary-600":"bg-white dark:bg-gray-900"}`}>
                    {isSelected ? <CheckSquare size={12} className="text-white"/> : <Square size={12} className="text-gray-500"/>}
                  </span>
                </button>
                <div className="flex-1 min-w-0" onClick={()=>toggleSelect(c.id)}>
                  <div className="flex items-start justify-between gap-2">
                    <h3 className="font-semibold text-[15px] leading-tight text-gray-900 dark:text-white truncate pr-2">{name}</h3>
                    <span className={`text-[11px] px-2 py-0.5 rounded-full font-medium whitespace-nowrap flex-shrink-0 ${statusBadge(c.lead_status)}`}>{c.lead_status}</span>
                  </div>
                  {/* Phone when there is one, otherwise the address — an
                      email-only contact must still show a way to reach them. */}
                  <div className="flex items-center gap-1.5 mt-1 text-[13px] text-gray-900 dark:text-gray-200">
                    {c.phone_number ? (
                      <>
                        <Phone size={12} className="text-primary-600 flex-shrink-0"/>
                        <span className="font-medium tracking-wide">{c.phone_number}</span>
                      </>
                    ) : (
                      <>
                        <Mail size={12} className="text-[#34B7F1] flex-shrink-0"/>
                        <span className="font-medium truncate">{c.email || "No contact details"}</span>
                      </>
                    )}
                    <button
                      onClick={(e)=>{
                        e.stopPropagation();
                        const value = contactChannel(c).value;
                        if (!value) return;
                        navigator.clipboard.writeText(value);
                        toast.success("Copied");
                      }}
                      className="text-gray-500 hover:text-primary-600 ml-1"
                      title="Copy"
                    >⎘</button>
                  </div>
                  {c.phone_number && c.email && (
                    <div className="flex items-center gap-1.5 mt-0.5 text-[12px] text-gray-500 dark:text-gray-400">
                      <Mail size={12}/> <span className="truncate">{c.email}</span>
                    </div>
                  )}
                  {c.business_name && c.business_name !== name && (
                    <div className="flex items-center gap-1.5 mt-1 text-[12px] text-gray-500 dark:text-gray-400">
                      <Building2 size={12}/> <span className="truncate">{c.business_name}</span>
                    </div>
                  )}
                  {(c.city||c.state) && (
                    <div className="flex items-center gap-1.5 mt-0.5 text-[12px] text-gray-500 dark:text-gray-400">
                      <MapPin size={12}/> <span>{[c.city,c.state].filter(Boolean).join(", ")}</span>
                    </div>
                  )}
                  {c.tags && c.tags.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-1.5">
                      {c.tags.map(t=>(<span key={t} className="text-[10px] px-1.5 py-0.5 rounded-full bg-primary-50 text-primary-600">{t}</span>))}
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
                  className="w-[56px] bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 text-gray-600 dark:text-gray-400 rounded-full py-2.5 flex items-center justify-center active:scale-[0.98] transition-transform"
                  title="View everything imported for this contact"
                >
                  <IdCard size={16}/>
                </button>
                <button
                  onClick={()=>handleDelete(c.id)}
                  disabled={deletingId===c.id}
                  className="w-[56px] bg-danger-100 dark:bg-gray-700 hover:bg-danger-100 text-danger-700 dark:text-danger-500 rounded-full py-2.5 flex items-center justify-center active:scale-[0.98] transition-transform disabled:opacity-50"
                  title="Delete permanently"
                >
                  {deletingId===c.id ? <span className="w-4 h-4 border-2 border-danger-700 border-t-transparent rounded-full animate-spin"/> : <Trash2 size={16}/>}
                </button>
              </div>
            </div>
          );
        })}
        {totalPages>1 && (
          <div className="flex items-center justify-between bg-white dark:bg-gray-800 rounded-xl px-3 py-2">
            <span className="text-[12px] text-gray-500">{total} contacts • page {page}/{totalPages}</span>
            <div className="flex gap-2">
              <button onClick={()=>setPage(p=>Math.max(1,p-1))} disabled={page===1} className="w-9 h-9 rounded-full bg-gray-100 dark:bg-gray-900 flex items-center justify-center disabled:opacity-40"><ChevronLeft size={16}/></button>
              <button onClick={()=>setPage(p=>Math.min(totalPages,p+1))} disabled={page===totalPages} className="w-9 h-9 rounded-full bg-gray-100 dark:bg-gray-900 flex items-center justify-center disabled:opacity-40"><ChevronRight size={16}/></button>
            </div>
          </div>
        )}
      </div>

      {/* DESKTOP TABLE */}
      <div className="hidden lg:block bg-white dark:bg-gray-800 rounded-xl overflow-hidden shadow-sm border border-gray-100 dark:border-gray-700">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-gray-100 dark:bg-gray-900 text-left">
              <tr>
                <th className="px-4 py-3 w-10">
                  <input
                    type="checkbox"
                    ref={(el)=>{ if (el) el.indeterminate = !allMatching && selected.size>0 && selected.size<contacts.length; }}
                    onChange={toggleSelectAll}
                    checked={allMatching || pageAll}
                    title={allMatching ? "Clear selection" : pageAll && total > contacts.length ? `Select all ${total} matching contacts (every page)` : "Select all on this page"}
                    className="rounded accent-primary-600"
                  />
                </th>
                <th className="px-3 py-3 text-[11px] font-semibold text-gray-500 uppercase tracking-wider">Contact</th>
                <th className="px-3 py-3 text-[11px] font-semibold text-gray-500 uppercase">Phone</th>
                <th className="px-3 py-3 text-[11px] font-semibold text-gray-500 uppercase">Business</th>
                <th className="px-3 py-3 text-[11px] font-semibold text-gray-500 uppercase">Email</th>
                <th className="px-3 py-3 text-[11px] font-semibold text-gray-500 uppercase">Status</th>
                <th className="px-3 py-3 text-[11px] font-semibold text-gray-500 uppercase text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
              {loading ? [...Array(5)].map((_,i)=>(<tr key={i}>{[...Array(7)].map((_,j)=>(<td key={j} className="px-4 py-3"><div className="h-4 bg-gray-100 dark:bg-gray-700 rounded animate-pulse"/></td>))}</tr>))
              : contacts.length===0 ? (<tr><td colSpan={7} className="px-4 py-12 text-center text-gray-500">{search||leadStatus||channel||listId||emailState?"No matches":"No contacts. Import or add one."}</td></tr>)
              : contacts.map(c => {
                const contactTrust = emailTrust(c);
                const contactTrustLabel = emailTrustLabel(contactTrust);
                return (
                <tr key={c.id} className={`hover:bg-gray-50 dark:hover:bg-gray-900 ${allMatching||selected.has(c.id)?"bg-primary-50 dark:bg-primary-950":""}`}>
                  <td className="px-4 py-3"><input type="checkbox" checked={allMatching||selected.has(c.id)} onChange={()=>toggleSelect(c.id)} title={allMatching?"Tap to keep this contact":"Select"} className="rounded accent-primary-600"/></td>
                  <td className="px-3 py-3">
                    <div className="flex items-center gap-2.5">
                      <div className={`w-8 h-8 rounded-full flex items-center justify-center text-white text-xs font-semibold ${avatarColor(contactLabel(c))}`}>{initials(c)}</div>
                      <span className="font-medium text-[13px] text-gray-900 dark:text-white">{c.first_name} {c.last_name}</span>
                    </div>
                  </td>
                  <td className="px-3 py-3 text-[13px] font-medium text-gray-900 dark:text-gray-200">
                    {c.phone_number
                      ? c.phone_number
                      : <span className="inline-flex items-center gap-1 text-[#34B7F1]"><Mail size={11}/>email only</span>}
                  </td>
                  <td className="px-3 py-3 text-[13px] text-gray-500 dark:text-gray-400 max-w-[160px]">
                    <div className="truncate">{c.business_name||"—"}</div>
                    {c.tags && c.tags.length > 0 && (
                      <div className="flex flex-wrap gap-1 mt-1">
                        {c.tags.slice(0,3).map(t=>(<span key={t} className="text-[10px] px-1.5 py-0.5 rounded-full bg-primary-50 text-primary-600">{t}</span>))}
                        {c.tags.length > 3 && <span className="text-[10px] text-gray-500">+{c.tags.length-3}</span>}
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-3 max-w-[170px]">
                    {c.email ? (
                      <div className="truncate text-[12px] text-gray-600 dark:text-gray-400" title={c.email}>
                        {c.email}
                        {contactTrustLabel && (
                          /* "Guessed" / "Unverified" / "Verified" — the operator
                             must never mistake an inferred address for a
                             confirmed one. */
                          <span className={`ml-1 text-[10px] px-1.5 py-0.5 rounded-full ${emailTrustClass(contactTrust)}`}>
                            {contactTrustLabel}
                          </span>
                        )}
                        {(c.is_email_opted_out || c.email_status === "unsubscribed") && (
                          <span className="ml-1 text-[10px] px-1.5 py-0.5 rounded-full bg-warning-100 text-warning-700">unsub</span>
                        )}
                        {(c.is_email_undeliverable || c.email_status === "bounced") ? (
                          <span className="ml-1 text-[10px] px-1.5 py-0.5 rounded-full bg-danger-100 text-danger-700">bounced</span>
                        ) : null}
                      </div>
                    ) : (
                      <span className="text-[12px] text-gray-400">—</span>
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
                      <button onClick={()=>setProfileId(c.id)} className="w-8 h-8 rounded-full bg-gray-100 dark:bg-gray-700 hover:bg-primary-600 hover:text-white text-gray-600 dark:text-gray-400 flex items-center justify-center" title="View full profile"><IdCard size={14}/></button>
                      <button onClick={()=>handleDelete(c.id)} disabled={deletingId===c.id} className="w-8 h-8 rounded-full bg-danger-50 hover:bg-danger-500 hover:text-white text-danger-500 flex items-center justify-center disabled:opacity-50" title="Delete permanently">
                        {deletingId===c.id ? <span className="w-3.5 h-3.5 border-2 border-danger-500 border-t-transparent rounded-full animate-spin"/> : <Trash2 size={14}/>}
                      </button>
                    </div>
                  </td>
                </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {totalPages>1 && (<div className="flex items-center justify-between px-4 py-3 bg-gray-100 dark:bg-gray-900 border-t border-gray-100 dark:border-gray-700"><span className="text-[13px] text-gray-500">{total} contacts</span><div className="flex gap-2"><button onClick={()=>setPage(p=>Math.max(1,p-1))} disabled={page===1} className="btn-secondary btn-sm"><ChevronLeft size={14}/></button><span className="text-sm self-center px-2">{page}/{totalPages}</span><button onClick={()=>setPage(p=>Math.min(totalPages,p+1))} disabled={page===totalPages} className="btn-secondary btn-sm"><ChevronRight size={14}/></button></div></div>)}
      </div>

      {showAdd && <AddContactModal lists={[]} onClose={()=>{setShowAdd(false);loadContacts()}} />}
      {showImport && <ImportContactsModal onClose={()=>setShowImport(false)} onDone={()=>{setShowImport(false);loadContacts()}}/>}
      {profileId !== null && <ContactProfileModal contactId={profileId} onClose={()=>setProfileId(null)}/>}
      {showListModal && <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/50 p-0 sm:p-4"><div className="bg-white dark:bg-gray-800 w-full sm:max-w-md rounded-t-2xl sm:rounded-xl p-4 sm:p-6 max-h-[92vh] overflow-y-auto"><div className="flex justify-between mb-4"><h2 className="text-lg font-semibold text-gray-900 dark:text-white">Add to List</h2><button onClick={()=>setShowListModal(false)} className="w-8 h-8 rounded-full bg-gray-100 dark:bg-gray-900 flex items-center justify-center"><X size={16}/></button></div><p className="text-sm text-gray-500 mb-3">{allMatching ? `All ${total} matching contacts${search||leadStatus?" (current search & status)":""}` : `${selected.size} contacts`}</p><ListPicker value={selectedListId} onChange={setSelectedListId} placeholder="Select list... (or create one)" allowNone={false} /><button onClick={confirmAddToList} disabled={addingToList} className="bg-primary-600 text-white w-full rounded-full py-3 mt-4 font-semibold disabled:opacity-60">{addingToList ? "Adding…" : allMatching ? `Add all ${total} to list` : "Add to List"}</button></div></div>}

      {/* WhatsApp-style Quick Send - bottom sheet */}
      {quickSendId && (
        <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/50 p-0 sm:p-4" onClick={()=>setQuickSendId(null)}>
          <div className="bg-gray-100 dark:bg-gray-900 w-full sm:max-w-md rounded-t-[20px] sm:rounded-2xl max-h-[92vh] flex flex-col overflow-hidden" onClick={e=>e.stopPropagation()}>
            {/* sheet header like WhatsApp */}
            <div className="bg-primary-700 dark:bg-gray-800 px-4 py-3 flex items-center gap-3 flex-shrink-0">
              <button onClick={()=>{setQuickSendId(null);setQuickContact(null);}} className="w-8 h-8 rounded-full hover:bg-white/10 flex items-center justify-center text-white"><X size={18}/></button>
              <div className={`w-8 h-8 rounded-full flex items-center justify-center text-white text-sm font-semibold ${quickContact?avatarColor(`${quickContact.first_name||""}`):"bg-primary-600"}`}>{quickContact?initials(quickContact):"?"}</div>
              <div className="flex-1 min-w-0">
                <p className="text-white font-semibold text-[15px] truncate">{quickContact? contactLabel(quickContact) : "Send SMS"}</p>
                <p className="text-white/70 text-[12px] truncate">{quickContact?.phone_number||"No phone on file"} • WhatsApp style</p>
              </div>
            </div>

            {/* chat preview bg */}
            <div className="flex-1 overflow-y-auto p-4 space-y-3 min-h-[120px]" style={{backgroundColor:"#eff3f9", backgroundImage:`url("data:image/svg+xml,%3Csvg width='100' height='100' viewBox='0 0 100 100' xmlns='http://www.w3.org/2000/svg'%3E%3Cg fill='%23d1d7db' fill-opacity='0.2'%3E%3Cpath d='M20 20h10v10H20zM50 50h10v10H50z'/%3E%3C/g%3E%3C/svg%3E")`}}>
              {quickContact && (
                <div className="flex justify-center">
                  <div className="bg-warning-100 text-[11px] px-3 py-1.5 rounded-lg shadow-sm text-gray-600 text-center max-w-[90%]">
                    🔒 This will be sent as SMS via your SIM. Message preview below.
                  </div>
                </div>
              )}
              {/* preview bubble */}
              {quickMsg.trim() && (
                <div className="flex justify-end">
                  <div className="bg-primary-100 rounded-lg rounded-tr-none px-3 py-2 shadow-sm max-w-[85%] relative">
                    <p className="text-[14px] whitespace-pre-wrap break-words text-gray-900">{quickMsg}</p>
                    <p className="text-[10px] text-gray-500 text-right mt-1">{new Date().toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"})} ✓✓</p>
                  </div>
                </div>
              )}
            </div>

            {/* composer */}
            <div className="bg-gray-100 dark:bg-gray-800 p-2 flex items-end gap-2 flex-shrink-0">
              <div className="flex-1 bg-white dark:bg-gray-700 rounded-3xl px-3 py-2 flex items-end gap-2 shadow-sm">
                <textarea
                  className="flex-1 bg-transparent outline-none resize-none py-1.5 text-[15px] placeholder:text-gray-500 text-gray-900 dark:text-white max-h-28"
                  rows={3}
                  placeholder="Type a message… Use {{first_name}} etc."
                  value={quickMsg}
                  onChange={e=>setQuickMsg(e.target.value)}
                  autoFocus
                />
              </div>
              <button onClick={doQuickSend} disabled={quickSending||!quickMsg.trim()} className="w-11 h-11 rounded-full bg-primary-600 hover:bg-primary-500 text-white flex items-center justify-center flex-shrink-0 disabled:opacity-50 shadow-sm">
                {quickSending ? <span className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"/> : <MessageSquare size={18}/>}
              </button>
            </div>
            <div className="bg-white dark:bg-gray-800 px-4 py-2 flex items-center justify-between text-[11px] text-gray-500 dark:text-gray-400">
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

  const handle = async (e:React.FormEvent) => { e.preventDefault();
    // A contact needs a phone number or an email address — one of the two, not
    // necessarily the phone. The server enforces the same rule and answers 400
    // with the reason, so this check only saves a round trip.
    if(!f.phone_number.trim() && !f.email.trim()){toast.error("Add a phone number or an email address");return;}
    setSub(true);
    try { const res = await api.post("/contacts/", f);
      if(f.list_id) { try { await api.post(`/lists/${f.list_id}/contacts`,[(res.data as any).id]); } catch {} }
      toast.success("Contact created"); onClose();
    } catch (err:any) { toast.error(err.response?.data?.detail||"Failed"); }
    finally { setSub(false); }
  };

  return (<div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/50 p-0 sm:p-4"><div className="bg-white dark:bg-gray-800 w-full sm:max-w-lg max-h-[92vh] overflow-y-auto rounded-t-[20px] sm:rounded-2xl"><div className="sticky top-0 bg-primary-700 dark:bg-gray-800 px-4 py-3 flex items-center justify-between"><h2 className="text-white font-semibold flex items-center gap-2"><User size={18}/> Add Contact</h2><button onClick={onClose} className="w-8 h-8 rounded-full bg-white/10 flex items-center justify-center text-white"><X size={16}/></button></div>
    <form onSubmit={handle} className="p-4 sm:p-6 space-y-3">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3"><div><label className="text-xs font-medium text-gray-600 dark:text-gray-400">First Name</label><input className="w-full mt-1 px-3 py-2.5 bg-gray-100 dark:bg-gray-900 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-primary-600/20" value={f.first_name} onChange={e=>setF({...f,first_name:e.target.value})}/></div><div><label className="text-xs font-medium text-gray-600">Last Name</label><input className="w-full mt-1 px-3 py-2.5 bg-gray-100 dark:bg-gray-900 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-primary-600/20" value={f.last_name} onChange={e=>setF({...f,last_name:e.target.value})}/></div></div>
      <div><label className="text-xs font-medium text-gray-600">Phone Number <span className="text-gray-400">(or email below)</span></label><input className="w-full mt-1 px-3 py-2.5 bg-gray-100 dark:bg-gray-900 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-primary-600/20" placeholder="08012345678" value={f.phone_number} onChange={e=>setF({...f,phone_number:e.target.value})}/></div>
      <div><label className="text-xs font-medium text-gray-600">Business / Restaurant Name</label><input className="w-full mt-1 px-3 py-2.5 bg-gray-100 dark:bg-gray-900 rounded-xl text-sm" placeholder="e.g. Chicken Republic, Mama Gold" value={f.business_name} onChange={e=>setF({...f,business_name:e.target.value})}/></div>
      <div><label className="text-xs font-medium text-gray-600">Email <span className="text-gray-400">(or phone number above)</span></label><input className="w-full mt-1 px-3 py-2.5 bg-gray-100 dark:bg-gray-900 rounded-xl text-sm" type="email" placeholder="ada@example.com" value={f.email} onChange={e=>setF({...f,email:e.target.value})}/></div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3"><div><label className="text-xs font-medium text-gray-600">City</label><input className="w-full mt-1 px-3 py-2.5 bg-gray-100 dark:bg-gray-900 rounded-xl text-sm" value={f.city} onChange={e=>setF({...f,city:e.target.value})}/></div><div><label className="text-xs font-medium text-gray-600">State</label><input className="w-full mt-1 px-3 py-2.5 bg-gray-100 dark:bg-gray-900 rounded-xl text-sm" value={f.state} onChange={e=>setF({...f,state:e.target.value})}/></div></div>
      <div><label className="text-xs font-medium text-gray-600">Industry</label><input className="w-full mt-1 px-3 py-2.5 bg-gray-100 dark:bg-gray-900 rounded-xl text-sm" placeholder="Restaurant, Fast Food, etc." value={f.industry} onChange={e=>setF({...f,industry:e.target.value})}/></div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div><label className="text-xs font-medium text-gray-600">Lead Status</label><select className="w-full mt-1 px-3 py-2.5 bg-gray-100 dark:bg-gray-900 rounded-xl text-sm" value={f.lead_status} onChange={e=>setF({...f,lead_status:e.target.value})}>{LEAD_STATUSES.map(s=><option key={s} value={s}>{s}</option>)}</select></div>
        <div><label className="text-xs font-medium text-gray-600">Add to List</label><div className="mt-1"><ListPicker value={f.list_id} onChange={v=>setF({...f,list_id:v})} placeholder="No list — pick or create one" /></div></div>
      </div>
      <div className="flex gap-2 pt-2"><button type="button" onClick={onClose} className="flex-1 py-3 rounded-full bg-gray-100 dark:bg-gray-900 text-gray-600 dark:text-white font-medium">Cancel</button><button type="submit" disabled={sub} className="flex-1 py-3 rounded-full bg-primary-600 text-white font-semibold disabled:opacity-50">{sub?"Creating...":"Create Contact"}</button></div>
    </form></div></div>);
}
