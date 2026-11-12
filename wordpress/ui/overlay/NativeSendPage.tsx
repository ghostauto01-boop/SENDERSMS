import { FormEvent,useEffect,useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import api from '../api/client';
import { nativeRuntime } from '../native/runtime';

type Pending={key:string;messageId?:number;path:string};
type RequestState={pending:Pending;payload:Record<string,unknown>};
const STORAGE='sender-suite.pending-sms.v1';
function remembered():Pending|null{try{const p=JSON.parse(sessionStorage.getItem(STORAGE)||'null');if(p&&typeof p.key==='string'&&/^wp-ui-[a-f0-9]{32}$/.test(p.key)&&typeof p.path==='string')return {key:p.key,path:p.path,...(Number.isSafeInteger(p.messageId)&&p.messageId>0?{messageId:p.messageId}:{})};}catch{}return null;}
function key(){const bytes=crypto.getRandomValues(new Uint8Array(16));return 'wp-ui-'+Array.from(bytes,x=>x.toString(16).padStart(2,'0')).join('');}
function message(error:any){return typeof error?.response?.data?.detail==='string'?error.response.data.detail:(error?.message||'Request failed.');}
export default function NativeSendPage(){
 const [params]=useSearchParams();const conversation=params.get('conversation_id');
 const [contact,setContact]=useState(params.get('contact_id')||'');const [search,setSearch]=useState('');const [contacts,setContacts]=useState<any[]>([]);
 const [boundConversation,setBoundConversation]=useState(false);
 const [gateway,setGateway]=useState('');const [phones,setPhones]=useState<any[]>([]);const [body,setBody]=useState('');const [schedule,setSchedule]=useState('');
 const [pending,setPending]=useState<Pending|null>(remembered);const [request,setRequest]=useState<RequestState|null>(null);const [result,setResult]=useState<any>(null);const [busy,setBusy]=useState(false);const [error,setError]=useState('');
 const [history,setHistory]=useState<any[]>([]);const [historyError,setHistoryError]=useState('');const [status,setStatus]=useState('all');const [page,setPage]=useState(1);const [total,setTotal]=useState(0);
 const enabled=nativeRuntime().smsOutboxEnabled;
 const remember=(p:Pending)=>{try{sessionStorage.setItem(STORAGE,JSON.stringify(p));}catch{throw new Error('Session storage is unavailable. Cannot safely preserve the submission key; nothing was queued.');}setPending(p);};
 const loadHistory=async()=>{try{const {data}=await api.get('/send/history',{params:{status,page,per_page:20}});setHistory(data.items);setTotal(data.total);setHistoryError('');}catch(e){setHistoryError(message(e));}};
 useEffect(()=>{void loadHistory();},[status,page]);
 useEffect(()=>{if(!conversation)return;let active=true;setBoundConversation(false);api.get(`/inbox/conversations/${conversation}`).then(({data})=>{if(active&&Number.isSafeInteger(data.contact?.id)){setContact(String(data.contact.id));setBoundConversation(true);}}).catch(e=>{if(active)setError(message(e));});return()=>{active=false;};},[conversation]);
 useEffect(()=>{api.get('/settings/gateway/phones').then(({data})=>setPhones(data.phones||[])).catch(e=>setError(message(e)));},[]);
 useEffect(()=>{let active=true;const timer=setTimeout(()=>{api.get('/contacts',{params:{search,per_page:20}}).then(({data})=>{if(active)setContacts(data.items||[]);}).catch(e=>{if(active)setError(message(e));});},250);return()=>{active=false;clearTimeout(timer);};},[search]);
 const send=async(e:FormEvent)=>{
  e.preventDefault();if(!enabled||busy)return;setError('');setBusy(true);
  try{
   if(pending&&!request)throw new Error('A previous submission is recorded in this tab. Review its status/history before starting another request.');
   let work=request;
   if(!work){
    if(!/^[1-9][0-9]*$/.test(contact)||!/^[1-9][0-9]*$/.test(gateway)||!Number.isSafeInteger(Number(contact))||!Number.isSafeInteger(Number(gateway)))throw new Error('Choose a contact and an explicit sender.');
    if(conversation&&(!/^[1-9][0-9]*$/.test(conversation)||!Number.isSafeInteger(Number(conversation))||!boundConversation))throw new Error('Invalid conversation link.');
    const payload:Record<string,unknown>={gateway_setting_id:Number(gateway),body};
    const path=conversation?`/inbox/conversations/${conversation}/reply`:'/send/';
    if(!conversation){payload.contact_id=Number(contact);if(schedule){const instant=new Date(schedule);if(!Number.isFinite(instant.getTime()))throw new Error('Choose a valid schedule.');payload.schedule_at=instant.toISOString();}}
    work={pending:{key:key(),path},payload};remember(work.pending);setRequest(work);
   }
   const {data}=await api.post(work.pending.path,work.payload,{headers:{'Idempotency-Key':work.pending.key}});
   // Save the actual server ID before allowing further actions; never interpret 202 as sent.
   if(!Number.isSafeInteger(data.message_id)||data.message_id<1)throw new Error('No valid server message identity was acknowledged.');
   remember({...work.pending,messageId:data.message_id});setResult(data);void loadHistory();
  }catch(e){setError(message(e)+' The request key is retained. Do not create a different request to retry an uncertain submission.');}finally{setBusy(false);}
 };
 const refresh=async()=>{if(!pending?.messageId)return;setBusy(true);try{const {data}=await api.get(`/send/messages/${pending.messageId}`);setResult(data);void loadHistory();setError('');}catch(e){setError(message(e));}finally{setBusy(false);}};
 const cancel=async()=>{if(!pending?.messageId||!window.confirm('Cancel this queued message before worker claim? An already claimed message cannot be cancelled.'))return;setBusy(true);try{const {data}=await api.post(`/send/messages/${pending.messageId}/cancel`);setResult(data);void loadHistory();setError('');}catch(e){setError(message(e));}finally{setBusy(false);}};
 const clear=()=>{if(!window.confirm('Start a separate intentional request? This does NOT cancel the previous message. Check history first; never use a new key to retry an unknown submission.'))return;try{sessionStorage.removeItem(STORAGE);}catch{}setPending(null);setRequest(null);setResult(null);setBody('');setError('');};
 return <main className="space-y-6" aria-label="Native SMS queue">
  <header><h1 className="text-2xl font-semibold">SMS queue & history</h1><p className="text-sm text-gray-600">One consenting contact, explicit sender, final text. Queue acceptance is not delivery. No automatic resend.</p></header>
  {!enabled&&<p role="status" className="rounded-xl bg-warning-100 p-4">Sending is disabled on this WordPress host. Stored history remains available. Native worker qualification is still required.</p>}
  {conversation&&<p className="rounded-xl bg-gray-100 p-3">Reply to SMS conversation #{conversation}. Confirm the contact and re-enter the final text; no message text was placed in the URL.</p>}
  <form onSubmit={send} className="card p-5 space-y-4">
   <fieldset disabled={!!pending||busy||!enabled} className="space-y-4">
    <label className="block">Find contact<input className="input mt-1" value={search} onChange={e=>setSearch(e.target.value)} placeholder="Search name or phone"/></label>
    <label className="block">Contact ID<input className="input mt-1" required inputMode="numeric" disabled={!!conversation} value={contact} onChange={e=>setContact(e.target.value)}/></label>
    <div className="flex flex-wrap gap-2">{contacts.map(c=><button type="button" key={c.id} className="btn-secondary btn-sm" disabled={!!conversation} onClick={()=>setContact(String(c.id))}>{c.first_name||c.business_name||c.phone_number||'Contact'} #{c.id}</button>)}</div>
    <label className="block">Sender<select className="input mt-1" required value={gateway} onChange={e=>setGateway(e.target.value)}><option value="">Choose an explicit sender</option>{phones.filter(p=>p.is_enabled&&p.configured).map(p=><option key={p.db_id} value={p.db_id}>{p.name||'Sender'} — SIM {p.sim_number}</option>)}</select></label>
    <label className="block">Final SMS text<textarea className="input mt-1" required maxLength={1600} value={body} onChange={e=>setBody(e.target.value)} placeholder="No template placeholders; five-segment server limit applies."/></label>
    {!conversation&&<label className="block">Optional schedule (your local time)<input className="input mt-1" type="datetime-local" value={schedule} onChange={e=>setSchedule(e.target.value)}/></label>}
   </fieldset>
   <button className="btn-primary" disabled={!enabled||busy||!!pending&&!request||!!pending?.messageId} type="submit">{busy?'Working…':request?'Retry exact request with same key':'Queue SMS'}</button>
  </form>
  {error&&<p role="alert" className="rounded-xl bg-danger-100 p-4">{error}</p>}
  {pending&&<section className="card p-5 space-y-3"><h2 className="font-semibold">Submission record</h2><p className="break-all text-sm">Key: {pending.key}</p><p>Message: {pending.messageId||'No acknowledgement recorded; review history before another request.'}</p>{result&&<><p role="status">Actual state: <strong>{result.status}</strong></p><p>Provider ID: {result.provider_message_id||'None acknowledged'}</p>{result.needs_review&&<p role="alert">Unknown outcome — manual review required. Do not resend.</p>}</>}<div className="flex gap-2 flex-wrap"><button type="button" className="btn-secondary" disabled={busy||!pending.messageId} onClick={refresh}>Refresh status</button><button type="button" className="btn-secondary" disabled={busy||result?.status!=='queued'} onClick={cancel}>Cancel before claim</button><button type="button" className="btn-secondary" disabled={busy} onClick={clear}>Start separate request…</button></div></section>}
  <section className="card p-5 space-y-3"><h2 className="text-xl font-semibold">Outgoing history</h2><label>Status<select className="input" value={status} onChange={e=>{setStatus(e.target.value);setPage(1);}}>{['all','queued','sending','sent','delivered','failed','unknown','cancelled'].map(s=><option key={s}>{s}</option>)}</select></label>{historyError&&<p role="alert">{historyError}</p>}<p>{total} stored messages</p><ul className="divide-y">{history.map(m=><li key={m.id} className="py-3"><strong>#{m.id} · {m.status}</strong><p>{m.contact_name||m.phone||'Contact unavailable'}</p><p className="whitespace-pre-wrap break-words">{m.body_preview}</p>{m.error&&<p>{m.error}</p>}<small>{m.created_at} {m.provider_message_id&&` · ${m.provider_message_id}`}</small></li>)}</ul><div className="flex gap-2"><button className="btn-secondary" disabled={page===1} onClick={()=>setPage(page-1)}>Previous</button><span>Page {page}</span><button className="btn-secondary" disabled={page*20>=total} onClick={()=>setPage(page+1)}>Next</button><button className="btn-secondary" onClick={()=>void loadHistory()}>Refresh history</button></div></section>
 </main>;
}
