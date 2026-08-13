import { useState, useEffect } from "react";
import api from "../api/client";
import { NotificationProviderSettings } from "../types";
import toast from "react-hot-toast";
import { Wifi, Bell, Shield, Clock, TestTube, Eye, EyeOff, Activity, CheckCircle, XCircle, Webhook, RefreshCw, Trash2 } from "lucide-react";

type Tab = "gateway" | "notifications" | "compliance" | "sending";

export default function SettingsPage() {
  const [tab, setTab] = useState<Tab>("gateway");
  const [notifs, setNotifs] = useState<NotificationProviderSettings[]>([]);
  const [comp, setComp] = useState<Record<string, string>>({});
  const [rules, setRules] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);

  useEffect(() => { load(); }, []);
  const load = async () => {
    setLoading(true);
    try {
      const [n, c, r] = await Promise.all([
        api.get("/settings/notifications"), api.get("/settings/compliance"), api.get("/settings/sending-rules"),
      ]);
      setNotifs(n.data.providers); setComp(c.data); setRules(r.data);
    } catch {} finally { setLoading(false); }
  };

  const tabs = [
    { id: "gateway" as Tab, label: "SMS Gateway", icon: Wifi },
    { id: "notifications" as Tab, label: "Pushover", icon: Bell },
    { id: "compliance" as Tab, label: "Compliance", icon: Shield },
    { id: "sending" as Tab, label: "Sending Rules", icon: Clock },
  ];

  return (
    <div className="space-y-4"><h1 className="text-xl sm:text-2xl font-bold">Settings</h1>
      <div className="flex gap-2 flex-wrap">{tabs.map(t => (<button key={t.id} onClick={() => setTab(t.id)}
        className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium ${tab===t.id?"bg-primary-100 dark:bg-primary-900/30 text-primary-700 dark:text-primary-300":"text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700"}`}><t.icon size={14}/>{t.label}</button>))}</div>
      {loading ? (<div className="card p-6"><div className="skeleton h-8 w-48 mb-4"/><div className="space-y-3">{[...Array(3)].map((_,i)=>(<div key={i} className="skeleton h-10 w-full"/>))}</div></div>)
      : (<>{tab==="gateway"&&<GatewayTab />}{tab==="notifications"&&<NotifsTab provs={notifs} onUpdate={load}/>}{tab==="compliance"&&<CompTab s={comp} onUpdate={load}/>}{tab==="sending"&&<RulesTab s={rules} onUpdate={load}/>}</>)}
    </div>);
}

function GatewayTab() {
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [sim, setSim] = useState(1);
  const [savingSim, setSavingSim] = useState(false);
  const [gw, setGw] = useState<any>(null);

  useEffect(() => {
    api.get("/settings/gateway").then(({data}:any) => { setGw(data); if(data?.sim_number) setSim(data.sim_number); }).catch(()=>{});
  }, []);

  const saveSim = async (n: number) => {
    setSim(n); setSavingSim(true);
    try { await api.put("/settings/gateway/sim", null, { params: { sim: n } }); toast.success("SIM " + n + " selected"); }
    catch { toast.error("Failed to set SIM"); }
    finally { setSavingSim(false); }
  };

  const test = async () => {
    setTesting(true); setResult(null);
    try {
      const { data } = await api.post("/settings/gateway/test", null, { params: { sim } });
      setResult(data);
      toast.success(data.success ? "Connected!" : "Failed: " + (data.message || ""));
    } catch (e: any) { setResult({ success: false, message: e.message }); toast.error("Test failed"); }
    finally { setTesting(false); }
  };

  return (
    <div className="space-y-4">
    <div className="card p-6 space-y-4 max-w-lg">
      <h2 className="text-lg font-semibold">SMS Gateway</h2>
      <p className="text-xs text-gray-500">Connected to <code className="text-xs bg-gray-100 dark:bg-gray-700 px-1 rounded">{gw?.base_url || "api.sms-gate.app/3rdparty/v1"}</code>{gw?.username ? <> Username: <strong>{gw.username}</strong>.</> : null}</p>
      {gw && !gw.configured && (
        <div className="p-3 rounded-lg text-sm bg-red-50 dark:bg-red-900/30 text-red-700">
          Gateway credentials are not configured. Set SMSGATE_USERNAME and SMSGATE_PASSWORD.
        </div>
      )}

      <div>
        <label className="label">SIM Slot for Sending</label>
        <div className="flex gap-2">
          <button onClick={() => saveSim(1)} className={`btn flex-1 ${sim===1 ? "btn-primary" : "btn-secondary"}`} disabled={savingSim}>
            📱 SIM 1
          </button>
          <button onClick={() => saveSim(2)} className={`btn flex-1 ${sim===2 ? "btn-primary" : "btn-secondary"}`} disabled={savingSim}>
            📱 SIM 2
          </button>
        </div>
        <p className="text-xs text-gray-400 mt-1">Select which SIM card to use for sending SMS</p>
      </div>

      {result && (
        <div className={`p-3 rounded-lg text-sm flex items-center gap-2 ${result.success ? "bg-green-50 dark:bg-green-900/30 text-green-700" : "bg-red-50 dark:bg-red-900/30 text-red-700"}`}>
          {result.success ? <CheckCircle size={16} /> : <XCircle size={16} />}
          <span>{result.message || (result.success ? "Connected" : "Failed")}</span>
        </div>
      )}

      <button onClick={test} disabled={testing} className="btn-primary w-full">
        <Activity size={14} className="mr-1" />{testing ? "Testing..." : "Test Connection"}
      </button>

      {result && (
        <details className="text-xs bg-gray-50 dark:bg-gray-700 rounded p-2">
          <summary className="cursor-pointer font-medium">Details</summary>
          <pre className="mt-1 whitespace-pre-wrap overflow-x-auto">{JSON.stringify(result, null, 2)}</pre>
        </details>
      )}
    </div>
    <WebhooksCard />
    </div>
  );
}

/** Manage the webhook registrations that make inbound SMS reach the Inbox. */
function WebhooksCard() {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [customUrl, setCustomUrl] = useState("");
  const [showCustom, setShowCustom] = useState(false);

  const load = async () => {
    setLoading(true);
    try { const { data } = await api.get("/settings/gateway/webhooks"); setData(data); }
    catch (e: any) { toast.error(e.response?.data?.detail || "Could not read webhooks"); }
    finally { setLoading(false); }
  };
  useEffect(() => { load(); }, []);

  const register = async () => {
    setBusy(true);
    try {
      const params = showCustom && customUrl.trim() ? { url: customUrl.trim() } : undefined;
      const { data } = await api.post("/settings/gateway/register-webhook", null, { params });
      const n = (data.created?.length ?? 0);
      toast.success(n ? `Registered ${n} event${n===1?"":"s"}` : "Already up to date");
      if (data.errors?.length) toast.error(data.errors[0]);
      await load();
    } catch (e: any) { toast.error(e.response?.data?.detail || "Registration failed"); }
    finally { setBusy(false); }
  };

  const remove = async (id: string) => {
    setBusy(true);
    try { await api.delete(`/settings/gateway/webhooks/${id}`); toast.success("Webhook removed"); await load(); }
    catch (e: any) { toast.error(e.response?.data?.detail || "Delete failed"); }
    finally { setBusy(false); }
  };

  return (
    <div className="card p-6 space-y-4 max-w-lg">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold flex items-center gap-2"><Webhook size={18}/>Webhooks</h2>
        <button onClick={load} disabled={loading||busy} className="btn-secondary text-xs">
          <RefreshCw size={12} className="mr-1"/>Refresh
        </button>
      </div>
      <p className="text-xs text-gray-500">
        Incoming SMS only reach the Inbox if the gateway is told where to deliver them.
        Register once here and replies arrive automatically.
      </p>

      {loading ? <div className="skeleton h-20 w-full"/> : !data ? null : (<>
        {!data.configured && (
          <div className="p-3 rounded-lg text-sm bg-red-50 dark:bg-red-900/30 text-red-700">
            Gateway credentials are missing, so webhooks cannot be registered.
          </div>
        )}
        {data.configured && !data.public_base_url_set && (
          <div className="p-3 rounded-lg text-sm bg-amber-50 dark:bg-amber-900/30 text-amber-700">
            PUBLIC_BASE_URL is not set, so we do not know this server&apos;s public address.
            Set it, or enter a URL manually below.
          </div>
        )}
        {data.configured && data.public_base_url_set && !data.signing_secret_set && (
          <div className="p-3 rounded-lg text-sm bg-amber-50 dark:bg-amber-900/30 text-amber-700">
            No signing secret set. Copy the key from the phone app (Settings &rarr; Webhooks &rarr;
            Signing Key) into SMSGATE_WEBHOOK_SECRET so deliveries can be verified.
          </div>
        )}

        {data.webhook_url && (
          <div className="text-xs">
            <span className="text-gray-500">Delivering to</span>
            <code className="block mt-0.5 break-all bg-gray-100 dark:bg-gray-700 px-2 py-1 rounded">{data.webhook_url}</code>
          </div>
        )}

        <div className={`p-3 rounded-lg text-sm flex items-start gap-2 ${data.healthy ? "bg-green-50 dark:bg-green-900/30 text-green-700" : "bg-amber-50 dark:bg-amber-900/30 text-amber-700"}`}>
          {data.healthy ? <CheckCircle size={16} className="mt-0.5 shrink-0"/> : <XCircle size={16} className="mt-0.5 shrink-0"/>}
          <span>
            {data.healthy
              ? `All ${data.registered_events.length} events registered — inbound SMS will reach the Inbox.`
              : `${data.missing_events?.length ?? 0} of ${data.required_events?.length ?? 0} events are not registered. Inbound SMS will NOT appear until you register them.`}
          </span>
        </div>

        {data.required_events?.length > 0 && (
          <div className="space-y-1">
            {data.required_events.map((ev: string) => {
              const on = data.registered_events?.includes(ev);
              return (
                <div key={ev} className="flex items-center justify-between text-xs py-1 border-b border-gray-100 dark:border-gray-700 last:border-0">
                  <code>{ev}</code>
                  <span className={on ? "text-green-600 font-medium" : "text-gray-400"}>
                    {on ? "registered" : "missing"}
                  </span>
                </div>
              );
            })}
          </div>
        )}

        {showCustom && (
          <div>
            <label className="label">Webhook URL</label>
            <input className="input w-full" placeholder="https://your-tunnel.ngrok.io" value={customUrl}
                   onChange={e=>setCustomUrl(e.target.value)} />
            <p className="text-xs text-gray-400 mt-1">
              Must be HTTPS. The path is appended automatically.
            </p>
          </div>
        )}

        <div className="flex gap-2">
          <button onClick={register} disabled={busy || !data.configured} className="btn-primary flex-1">
            {busy ? "Working..." : data.healthy ? "Re-register" : "Register webhooks"}
          </button>
          <button onClick={()=>setShowCustom(v=>!v)} className="btn-secondary text-xs">
            {showCustom ? "Use default" : "Custom URL"}
          </button>
        </div>

        {data.webhooks?.length > 0 && (
          <details className="text-xs">
            <summary className="cursor-pointer font-medium">
              All registrations on this gateway ({data.webhooks.length})
            </summary>
            <div className="mt-2 space-y-1">
              {data.webhooks.map((w: any) => (
                <div key={w.id} className="flex items-center justify-between gap-2 py-1 border-b border-gray-100 dark:border-gray-700 last:border-0">
                  <div className="min-w-0">
                    <code className="block truncate">{w.event}</code>
                    <span className={`block truncate text-[10px] ${w.is_ours ? "text-green-600" : "text-gray-400"}`}>
                      {w.is_ours ? "this server" : w.url}
                    </span>
                  </div>
                  <button onClick={()=>remove(w.id)} disabled={busy}
                          className="text-red-600 hover:text-red-700 shrink-0" title="Remove">
                    <Trash2 size={14}/>
                  </button>
                </div>
              ))}
            </div>
          </details>
        )}

        {data.error && <p className="text-xs text-red-600 break-all">{data.error}</p>}
      </>)}
    </div>
  );
}

function NotifsTab({ provs, onUpdate }: { provs: NotificationProviderSettings[]; onUpdate: () => void }) {
  const po = provs.find(p => p.provider === "pushover");
  return <PushoverCard s={po} onUpdate={onUpdate} />;
}

function PushoverCard({ s, onUpdate }: { s?: NotificationProviderSettings; onUpdate: () => void }) {
  const [on,setOn]=useState(s?.is_enabled||false); const [uk,setUk]=useState(""); const [at,setAt]=useState(""); const [sv,setSv]=useState(false); const [ts,setTs]=useState(false); const [sa,setSa]=useState(false);
  const [muted,setMuted]=useState(""); const [mutedLoaded,setMutedLoaded]=useState(false); const [msv,setMsv]=useState(false);
  useEffect(()=>{ if(!mutedLoaded){ api.get("/settings/notifications/muted-senders").then(({data}:any)=>{ setMuted((data.senders||[]).join(", ")); setMutedLoaded(true); }).catch(()=>setMutedLoaded(true)); } },[mutedLoaded]);
  const save=async()=>{setSv(true);try{await api.put("/settings/notifications/pushover",null,{params:{is_enabled:on,user_key:uk||undefined,app_token:at||undefined}});toast.success("Saved");onUpdate();}catch{toast.error("Failed")}finally{setSv(false)}};
  const saveMuted=async()=>{setMsv(true);try{const{data}=await api.put("/settings/notifications/muted-senders",null,{params:{senders:muted}});setMuted((data.senders||[]).join(", "));toast.success("Muted senders saved");}catch{toast.error("Failed")}finally{setMsv(false)}};
  const test=async()=>{if(!uk||!at){toast.error("Enter both keys");return;}setTs(true);try{await save();const{data}=await api.post("/settings/notifications/pushover/test");data.success?toast.success("Test sent!"):toast.error(data.error||"Failed");}catch{toast.error("Failed")}finally{setTs(false)}};
  return(<div className="card p-4"><div className="flex items-center justify-between mb-3"><div className="flex items-center gap-3"><input type="checkbox" checked={on} onChange={e=>setOn(e.target.checked)}/><h3 className="font-semibold">Pushover</h3></div><button onClick={save} disabled={sv} className="btn-primary btn-sm">{sv?"Saving...":"Save"}</button></div>
  {on&&(<div className="space-y-3"><div><label className="label">User Key</label><input className="input" value={uk} onChange={e=>setUk(e.target.value)} placeholder="Pushover User Key"/></div>
  <div><label className="label">App Token</label><div className="relative"><input type={sa?"text":"password"} className="input pr-10" value={at} onChange={e=>setAt(e.target.value)} placeholder="From pushover.net/apps/build"/><button onClick={()=>setSa(!sa)} className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400">{sa?<EyeOff size={16}/>:<Eye size={16}/>}</button></div></div>
  <div className="border-t border-gray-100 dark:border-gray-700 pt-3">
    <label className="label">Muted senders — no alert (comma-separated)</label>
    <div className="flex gap-2">
      <input className="input flex-1" value={muted} onChange={e=>setMuted(e.target.value)} placeholder="MTN, AIRTEL"/>
      <button onClick={saveMuted} disabled={msv||!mutedLoaded} className="btn-secondary btn-sm">{msv?"Saving...":"Save"}</button>
    </div>
    <p className="text-xs text-gray-400 mt-1">Carrier alerts (MTN, Airtel, banks, 2FA short codes) never ping your phone. Their messages still appear in the Inbox.</p>
  </div>
  <button onClick={test} disabled={ts} className="btn-secondary btn-sm"><TestTube size={14} className="mr-1"/>{ts?"Sending...":"Send Test"}</button></div>)}</div>);}

function CompTab({ s, onUpdate }: { s: Record<string, string>; onUpdate: () => void }) {
  const [f,setF]=useState({ec:s.enable_consent_requirement==="true",ea:s.enable_auto_opt_out==="true",es:s.enable_suppression_list==="true",ek:s.enable_campaign_suppression==="true"});
  const [sv,setSv]=useState(false);
  const save=async()=>{setSv(true);try{await api.put("/settings/compliance",null,{params:{enable_consent_requirement:f.ec,enable_auto_opt_out:f.ea,enable_suppression_list:f.es,enable_campaign_suppression:f.ek}});toast.success("Saved");onUpdate();}catch{toast.error("Failed")}finally{setSv(false)}};
  return(<div className="card p-6 space-y-4"><h2 className="text-lg font-semibold">Compliance</h2><div className="space-y-3">{["Require consent","Auto-detect opt-out","Suppression list","Campaign suppression"].map((l,i)=>{const k=["ec","ea","es","ek"][i];return<label key={k} className="flex items-center gap-3 text-sm"><input type="checkbox" checked={(f as any)[k]} onChange={e=>setF({...f,[k]:e.target.checked})}/>{l}</label>})}</div><button onClick={save} disabled={sv} className="btn-primary">{sv?"Saving...":"Save"}</button></div>);}

function RulesTab({ s, onUpdate }: { s: Record<string, string>; onUpdate: () => void }) {
  const [f,setF]=useState({dl:s.enable_daily_limit==="true",dm:parseInt(s.daily_maximum||"1000"),hl:s.enable_hourly_limit==="true",hm:parseInt(s.hourly_maximum||"100"),ml:s.enable_per_minute_limit==="true",mm:parseInt(s.messages_per_minute||"10"),ss:s.sending_start_time||"08:00",se:s.sending_end_time||"20:00",aw:s.allow_weekends||"true",ah:s.allow_holidays||"true"});
  const [sv,setSv]=useState(false);
  const save=async()=>{setSv(true);try{await api.put("/settings/sending-rules",null,{params:{enable_daily_limit:f.dl,daily_maximum:f.dm,enable_hourly_limit:f.hl,hourly_maximum:f.hm,enable_per_minute_limit:f.ml,messages_per_minute:f.mm,sending_start_time:f.ss,sending_end_time:f.se,allow_weekends:f.aw==="true",allow_holidays:f.ah==="true"}});toast.success("Saved");onUpdate();}catch{toast.error("Failed")}finally{setSv(false)}};
  return(<div className="card p-6 space-y-4"><h2 className="text-lg font-semibold">Sending Rules</h2>
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
      <div className="space-y-2"><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.dl} onChange={e=>setF({...f,dl:e.target.checked})}/>Daily limit</label>{f.dl&&<input type="number" className="input" value={f.dm} onChange={e=>setF({...f,dm:parseInt(e.target.value)||0})}/>}</div>
      <div className="space-y-2"><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.hl} onChange={e=>setF({...f,hl:e.target.checked})}/>Hourly limit</label>{f.hl&&<input type="number" className="input" value={f.hm} onChange={e=>setF({...f,hm:parseInt(e.target.value)||0})}/>}</div>
      <div className="space-y-2"><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.ml} onChange={e=>setF({...f,ml:e.target.checked})}/>Per-minute limit</label>{f.ml&&<input type="number" className="input" value={f.mm} onChange={e=>setF({...f,mm:parseInt(e.target.value)||0})}/>}</div></div>
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4"><div><label className="label">Start</label><input type="time" className="input" value={f.ss} onChange={e=>setF({...f,ss:e.target.value})}/></div><div><label className="label">End</label><input type="time" className="input" value={f.se} onChange={e=>setF({...f,se:e.target.value})}/></div></div>
    <div className="flex gap-4"><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.aw==="true"} onChange={e=>setF({...f,aw:String(e.target.checked)})}/>Weekends</label><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.ah==="true"} onChange={e=>setF({...f,ah:String(e.target.checked)})}/>Holidays</label></div>
    <button onClick={save} disabled={sv} className="btn-primary">{sv?"Saving...":"Save"}</button></div>);}
