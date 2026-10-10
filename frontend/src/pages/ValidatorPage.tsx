import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { AlertCircle, CheckCircle2, ChevronLeft, ChevronRight, Download, FileUp, FlaskConical, List, Loader2, Play, Search, ShieldCheck, Square, User, Users } from "lucide-react";
import api from "../api/client";
import { validatorApi, type CheckKind, type ChannelResult, type ResultState, type SelfTest, type ValidationEntry, type ValidationRow, type ValidatorStatus } from "../api/validator";
import { detectValidationMapping, downloadValidationCsv, MAX_CSV_BYTES, parseValidationCsv, validationEntries, type ValidationMapping } from "../utils/validationCsv";

const SOURCES = [
  { key: "contacts", label: "Current contacts", icon: Users },
  { key: "list", label: "Contact list", icon: List },
  { key: "csv", label: "CSV file", icon: FileUp },
  { key: "single", label: "Single contact", icon: User },
] as const;
type Source = typeof SOURCES[number]["key"];
const STATES: { key: ResultState; label: string; badge: string }[] = [
  { key: "good", label: "Good", badge: "badge-green" },
  { key: "bad", label: "Bad", badge: "badge-red" },
  { key: "risky", label: "Risky", badge: "badge-yellow" },
  { key: "unknown", label: "Unknown", badge: "badge-blue" },
  { key: "missing", label: "Missing", badge: "badge-gray" },
];
const LEADS = ["new", "contacted", "replied", "interested", "follow-up", "meeting", "customer", "not_interested", "closed"];
const errorText = (error: unknown) => {
  const detail = (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
  return typeof detail === "string" ? detail : error instanceof Error ? error.message : "Request failed. Please try again.";
};

function Badge({ value }: { value: string }) {
  const key = value === "deliverable" ? "good" : value === "undeliverable" ? "bad" : value;
  const state = STATES.find(s => s.key === key);
  return <span className={`${state?.badge || "badge-gray"} whitespace-nowrap`}>{state?.label || value}</span>;
}

function ChannelDetail({ result, kind }: { result: ChannelResult | null; kind: string }) {
  if (!result) return <p className="text-gray-400">{kind}: not checked</p>;
  const flags: [string, boolean | null | undefined][] = kind === "Email" ? [
    ["Syntax valid", result.is_valid_syntax], ["Mail domain", result.accepts_mail],
    ["SMTP connected", result.smtp_can_connect], ["Catch-all", result.is_catch_all],
    ["Disposable", result.is_disposable], ["Role account", result.is_role_account], ["Free provider", result.is_free],
  ] : [];
  return <div className="min-w-0 space-y-2">
    <div className="flex items-center gap-2"><strong>{kind}</strong><Badge value={result.verdict} />{result.provider && <span className="text-xs text-gray-500">{result.provider}</span>}</div>
    {(result.email || result.normalized) && <p className="break-all font-mono text-xs">{result.email || result.normalized}</p>}
    <ul className="list-disc pl-4 space-y-1 text-gray-600 dark:text-gray-300">{result.problems.map((problem, i) => <li key={i}>{problem}</li>)}</ul>
    {result.suggested_email && <p className="text-warning-700 dark:text-warning-300 break-all">Possible typo: {result.suggested_email} (not changed automatically)</p>}
    {!!flags.length && result.verdict !== "missing" && <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">{flags.map(([name, value]) => <div key={name} className="flex justify-between gap-2"><dt className="text-gray-500 dark:text-gray-400">{name}</dt><dd>{value == null ? "Not confirmed" : value ? "Yes" : "No"}</dd></div>)}</dl>}
  </div>;
}

interface Run {
  ids?: number[];
  entries?: ValidationEntry[];
  check: CheckKind;
  deep: boolean;
  save: boolean;
  next: number;
  total: number;
  label: string;
}

export default function ValidatorPage() {
  const [params] = useSearchParams();
  const [source, setSource] = useState<Source>(params.get("list_id") && params.get("scope") !== "ids" ? "list" : "contacts");
  const [selectedIds, setSelectedIds] = useState<number[]>(() => params.get("scope") === "ids" ? [...new Set((params.get("contact_ids") || "").split(",").map(Number).filter(n => Number.isSafeInteger(n) && n > 0))] : []);
  const [search, setSearch] = useState(params.get("search") || "");
  const [lead, setLead] = useState(params.get("lead_status") || "");
  const [channel, setChannel] = useState(params.get("channel") || "");
  const [emailState, setEmailState] = useState(params.get("email_state") || "");
  const [listId, setListId] = useState(params.get("list_id") || "");
  const [lists, setLists] = useState<{ id: number; name: string; contact_count: number }[]>([]);
  const [listError, setListError] = useState("");
  const [status, setStatus] = useState<ValidatorStatus | null>(null);
  const [statusError, setStatusError] = useState("");
  const [selfTest, setSelfTest] = useState<SelfTest | null>(null);
  const [testing, setTesting] = useState(false);
  const [check, setCheck] = useState<CheckKind>("both");
  const [deep, setDeep] = useState(true);
  const [save, setSave] = useState(false);
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [name, setName] = useState("");
  const [fileText, setFileText] = useState("");
  const [fileName, setFileName] = useState("");
  const [fileLoading, setFileLoading] = useState(false);
  const [hasHeader, setHasHeader] = useState(true);
  const [mapping, setMapping] = useState<ValidationMapping>({ email: "", phone: "", name: "" });
  const [rows, setRows] = useState<ValidationRow[]>([]);
  const [runState, setRunState] = useState<"idle" | "running" | "paused" | "complete" | "error">("idle");
  const [total, setTotal] = useState(0);
  const [runLabel, setRunLabel] = useState("");
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<ResultState | "all">("all");
  const [resultSearch, setResultSearch] = useState("");
  const [page, setPage] = useState(1);
  const [stopping, setStopping] = useState(false);
  const stop = useRef(false);
  const mounted = useRef(true);
  const inFlight = useRef(false);
  const activeRun = useRef<Run | null>(null);
  const running = runState === "running";
  const savedSource = source === "contacts" || source === "list";

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; stop.current = true; };
  }, []);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    if (running) window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [running]);

  const loadStatus = () => {
    setStatusError("");
    validatorApi.status().then(setStatus).catch(() => setStatusError("Could not load engine status. Retry to check configuration."));
  };
  useEffect(() => {
    loadStatus();
    // Fetch every list, not just the first page of the picker.
    const load = async () => {
      const all: typeof lists = [];
      let page = 1;
      try {
        while (true) {
          const { data } = await api.get("/lists/", { params: { page, per_page: 100 } });
          all.push(...(data.items || []));
          if (!data.items?.length || all.length >= data.total || data.items.length < 100) break;
          page++;
        }
        if (mounted.current) setLists(all);
      } catch { if (mounted.current) setListError("Lists could not be loaded. Refresh this page to retry."); }
    };
    void load();
  }, []);

  const csv = useMemo(() => {
    if (!fileText) return { data: null, error: "" };
    try { return { data: parseValidationCsv(fileText, hasHeader), error: "" }; }
    catch (e) { return { data: null, error: errorText(e) }; }
  }, [fileText, hasHeader]);
  useEffect(() => { if (csv.data) setMapping(detectValidationMapping(csv.data.headers)); }, [csv.data]);

  const loadFile = async (file?: File) => {
    setFileText(""); setFileName(""); setError("");
    if (!file) return;
    if (file.size > MAX_CSV_BYTES) { setError("CSV files must be 10 MB or smaller. Split the file into parts and validate each part."); return; }
    setFileLoading(true);
    try {
      const text = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result || ""));
        reader.onerror = () => reject(new Error("Could not read the file."));
        reader.readAsText(file);
      });
      if (!text.trim()) throw new Error("The CSV file is empty.");
      setFileName(file.name); setFileText(text);
    } catch (e) { setError(errorText(e)); }
    finally { setFileLoading(false); }
  };

  const testEngine = async () => {
    setTesting(true); setError("");
    try { setSelfTest(await validatorApi.selfTest()); }
    catch (e) { setError(errorText(e)); }
    finally { setTesting(false); }
  };

  const execute = async (run: Run) => {
    try {
      const size = Math.max(1, Math.min(status?.batch_size || 5, 5));
      while (run.next < run.total && !stop.current) {
        const result = await validatorApi.batch({
          ...(run.ids ? { contact_ids: run.ids.slice(run.next, run.next + size) } : { items: run.entries!.slice(run.next, run.next + size) }),
          check: run.check, deep: run.deep, save: run.save,
        });
        const expected = Math.min(size, run.total - run.next);
        if (result.items.length !== expected) throw new Error("Incomplete batch response. Resume to retry this batch; no rows have been skipped.");
        run.next += expected;
        if (!mounted.current) return;
        setRows(previous => [...previous, ...result.items]);
      }
      if (mounted.current) setRunState(run.next === run.total ? "complete" : "paused");
    } catch (e) {
      if (mounted.current) { setError(errorText(e)); setRunState("error"); }
    } finally {
      inFlight.current = false;
      if (mounted.current) setStopping(false);
    }
  };

  const start = async () => {
    if (inFlight.current) return;
    setError("");
    let entries: ValidationEntry[] | undefined;
    try {
      if (source === "single") {
        if (!email.trim() && !phone.trim()) throw new Error("Enter an email address or phone number.");
        if (check === "email" && !email.trim()) throw new Error("Enter an email address, or choose Phone checks.");
        if (check === "phone" && !phone.trim()) throw new Error("Enter a phone number, or choose Email checks.");
        entries = [{ email, phone_number: phone, name }];
      }
      if (source === "csv") {
        if (!csv.data) throw new Error(csv.error || "Choose a CSV file first.");
        entries = validationEntries(csv.data, mapping);
        if (check === "email" && mapping.email === "") throw new Error("Map an email column, or choose Phone checks.");
        if (check === "phone" && mapping.phone === "") throw new Error("Map a phone column, or choose Email checks.");
      }
      if (source === "list" && !listId) throw new Error("Choose a contact list first.");
      if (savedSource && save && !window.confirm("Save validation results to these existing contacts? Confirmed-bad addresses/numbers will be quarantined; confirmed-good emails will be verified. Nothing is deleted, and opt-outs/suppressions are never removed.")) return;
    } catch (e) { setError(errorText(e)); return; }
    inFlight.current = true; stop.current = false; setStopping(false);
    setRunState("running"); setRows([]); setTotal(0); setPage(1); setFilter("all"); setResultSearch(""); activeRun.current = null;
    try {
      let ids: number[] | undefined;
      if (savedSource) {
        const selection = await validatorApi.selection({
          scope: source === "contacts" && selectedIds.length ? "ids" : source === "list" ? "list" : "all",
          ...(source === "contacts" && selectedIds.length ? { contact_ids: selectedIds } : {
            list_id: source === "list" ? Number(listId) : undefined,
            search: search || undefined, lead_status: lead || undefined,
            channel: channel === "sms" || channel === "email" ? channel : undefined,
            email_state: emailState || undefined,
          }),
        });
        ids = selection.contact_ids;
      }
      const label = source === "csv" ? fileName : source === "single" ? "Single contact" : source === "list" ? lists.find(l => l.id === Number(listId))?.name || "Selected list" : selectedIds.length ? "Selected contacts" : "Current contacts (matching filters)";
      const run: Run = { ids, entries, check, deep, save: savedSource && save, next: 0, total: ids?.length ?? entries?.length ?? 0, label };
      activeRun.current = run;
      if (!mounted.current) { inFlight.current = false; return; }
      setTotal(run.total); setRunLabel(label);
      await execute(run);
    } catch (e) {
      inFlight.current = false;
      if (mounted.current) { setError(errorText(e)); setRunState("error"); }
    }
  };

  const resume = () => {
    if (!activeRun.current || inFlight.current) return;
    inFlight.current = true; stop.current = false; setStopping(false); setError(""); setRunState("running");
    void execute(activeRun.current);
  };
  const counts = useMemo(() => rows.reduce((acc, row) => { acc[row.status]++; return acc; }, { good: 0, bad: 0, risky: 0, unknown: 0, missing: 0 }), [rows]);
  const annotated = useMemo(() => {
    const seen = new Map<string, number>();
    return rows.map((row, i) => {
      const key = [row.email?.email || row.input_email?.trim().toLowerCase() || "", row.phone?.normalized || row.input_phone?.replace(/[\s()-]/g, "") || ""].join("|");
      if (key === "|") return row;
      const previous = seen.get(key);
      if (previous !== undefined) return { ...row, duplicate_of: previous };
      seen.set(key, row.row || i + 1);
      return row;
    });
  }, [rows]);
  const filtered = useMemo(() => annotated.filter(row =>
    (filter === "all" || row.status === filter) && [row.name, row.input_email, row.input_phone, row.contact_id, row.note, ...row.blocked, ...(row.email?.problems || []), ...(row.phone?.problems || [])].join(" ").toLowerCase().includes(resultSearch.toLowerCase())
  ), [annotated, filter, resultSearch]);
  const pages = Math.max(1, Math.ceil(filtered.length / 25));
  const currentPage = Math.min(page, pages);
  const displayed = filtered.slice((currentPage - 1) * 25, currentPage * 25);
  const progress = total ? Math.round(rows.length / total * 100) : 0;

  return <div className="max-w-7xl mx-auto space-y-5 min-w-0">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div><h1 className="text-2xl font-bold flex items-center gap-2"><ShieldCheck className="text-primary-600" />Validator</h1><p className="text-sm text-gray-500 dark:text-gray-400 mt-1">Check your contact data. See what’s good, bad, and still unconfirmed.</p></div>
      <Link to="/contacts" className="btn-secondary btn-sm"><Users size={16} />Contacts</Link>
    </div>

    <section className="card p-4 sm:p-5 space-y-3" aria-label="Validator engine">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2 flex-wrap"><span className="badge-green">No API key required for built-in checks</span><span className="text-sm font-medium">{status ? status.engine === "reacher" ? "Reacher configured · built-in fallback" : "Built-in engine" : "Checking configuration…"}</span></div>
        <button className="btn-secondary btn-sm" onClick={testEngine} disabled={testing || running}>{testing ? <Loader2 size={15} className="animate-spin" /> : <FlaskConical size={15} />}Run engine self-test</button>
      </div>
      <p className="text-sm text-gray-600 dark:text-gray-400">Email checks use syntax, mail-domain DNS, and optional mailbox probing. Deep checks may use a configured Reacher service. No emails or SMS are sent. Valid phone format does not prove an active SIM.</p>
      {status && <p className="text-xs text-gray-500 dark:text-gray-400">DNS library: {status.dns_available ? "available" : "unavailable — domain checks will be Unknown"} · Built-in SMTP: {status.smtp_enabled ? "enabled (network access not guaranteed)" : "disabled"}{status.reacher_configured ? ` · Reacher key: ${status.reacher_key_configured ? "configured" : "not set (only needed if your service requires it)"}` : " · Reacher: optional, not configured"}</p>}
      {statusError && <p role="alert" className="text-sm text-danger-600">{statusError} <button className="underline" onClick={loadStatus}>Retry status</button></p>}
      {selfTest && <div role="status" className="rounded-xl bg-gray-50 dark:bg-gray-900 p-3 text-sm space-y-2"><p className="font-semibold">{selfTest.passed ? "Local engine self-test passed" : "Engine self-test failed"} · {new Date(selfTest.checked_at).toLocaleTimeString()}</p><ul className="grid sm:grid-cols-2 gap-1">{selfTest.checks.map(test => <li key={test.name} className="flex items-center gap-2">{test.passed ? <CheckCircle2 size={14} className="text-success-600" /> : <AlertCircle size={14} className="text-danger-600" />}{test.name}</li>)}</ul><p className="text-xs text-gray-500 dark:text-gray-400">{selfTest.notice}</p></div>}
    </section>

    <section className="card overflow-hidden" aria-label="Validation input">
      <div className="grid grid-cols-2 sm:grid-cols-4 border-b border-gray-200 dark:border-gray-700 p-2 gap-1" role="tablist" aria-label="Contact source">
        {SOURCES.map(tab => <button key={tab.key} role="tab" id={`source-${tab.key}`} aria-controls="validator-input" aria-selected={source === tab.key} disabled={running} onClick={() => { setSource(tab.key); setError(""); }} className={`flex items-center justify-center gap-2 rounded-xl px-2 py-3 text-sm font-medium transition-colors ${source === tab.key ? "bg-primary-50 dark:bg-primary-950/50 text-primary-700 dark:text-primary-300" : "text-gray-500 hover:bg-gray-50 dark:hover:bg-gray-700"}`}><tab.icon size={17} className="shrink-0" />{tab.label}</button>)}
      </div>
      <fieldset disabled={running} id="validator-input" role="tabpanel" aria-labelledby={`source-${source}`} className="p-4 sm:p-5 space-y-5 min-w-0">
        {savedSource && <div className="space-y-3">
          {source === "contacts" && selectedIds.length > 0 ? <div className="rounded-xl bg-primary-50 dark:bg-primary-950/30 p-3 flex flex-wrap justify-between gap-2 text-sm"><span>{selectedIds.length} selected contacts from the Contacts page.</span><button onClick={() => setSelectedIds([])} className="text-primary-700 dark:text-primary-300 underline">Use all contacts instead</button></div> : <>
            {source === "list" && <div><label className="label" htmlFor="validation-list">Contact list</label><select id="validation-list" className="input" value={listId} onChange={e => setListId(e.target.value)}><option value="">Choose a list…</option>{lists.map(list => <option key={list.id} value={list.id}>{list.name} ({list.contact_count} contacts)</option>)}</select>{listError && <p role="alert" className="text-sm text-danger-600 mt-1">{listError}</p>}</div>}
            <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-3">
              <div className="sm:col-span-2"><label htmlFor="validation-search" className="label">Search contacts</label><input id="validation-search" className="input" value={search} onChange={e => setSearch(e.target.value)} placeholder="Name, business, email or phone" maxLength={255} /></div>
              <div><label htmlFor="validation-lead" className="label">Lead status</label><select id="validation-lead" className="input" value={lead} onChange={e => setLead(e.target.value)}><option value="">All statuses</option>{LEADS.map(l => <option key={l}>{l}</option>)}</select></div>
              <div><label htmlFor="validation-channel" className="label">Contact channel</label><select id="validation-channel" className="input" value={channel} onChange={e => setChannel(e.target.value)}><option value="">All contacts</option><option value="sms">Has phone</option><option value="email">Has email</option></select></div>
            </div>
            {emailState && <p className="text-sm text-gray-500">Email filter from Contacts: <strong>{emailState}</strong> <button onClick={() => setEmailState("")} className="underline ml-2">Clear filter</button></p>}
          </>}
          <p className="text-xs text-gray-500 dark:text-gray-400">Every matching contact across every page is included. The selection is fixed when you start; new contacts added during the run are not included.</p>
        </div>}

        {source === "single" && <div className="grid sm:grid-cols-2 gap-3">
          <div className="sm:col-span-2"><label className="label" htmlFor="single-name">Name (optional)</label><input id="single-name" className="input" value={name} onChange={e => setName(e.target.value)} maxLength={500} placeholder="Contact name" /></div>
          <div><label className="label" htmlFor="single-email">Email address</label><input id="single-email" className="input" type="text" inputMode="email" value={email} onChange={e => setEmail(e.target.value)} maxLength={1000} placeholder="Enter an address to check" /></div>
          <div><label className="label" htmlFor="single-phone">Phone number</label><input id="single-phone" className="input" type="tel" value={phone} onChange={e => setPhone(e.target.value)} maxLength={1000} placeholder="080… or +234…" /></div>
          <p className="sm:col-span-2 text-xs text-gray-500">Enter either field or both. Invalid values are allowed here so you can see why they fail. This does not create a contact.</p>
        </div>}

        {source === "csv" && <div className="space-y-3">
          <div className="border-2 border-dashed border-gray-200 dark:border-gray-600 rounded-xl p-4 sm:p-6 bg-gray-50/60 dark:bg-gray-900/30">
            <label htmlFor="validation-file" className="label flex items-center gap-2"><FileUp size={19} />Upload a CSV to validate</label>
            <input id="validation-file" type="file" accept=".csv,text/csv,text/plain" className="block w-full min-w-0 text-sm mt-3 file:mr-3 file:rounded-lg file:border-0 file:px-3 file:py-2 file:bg-primary-100 file:text-primary-700" onChange={e => void loadFile(e.target.files?.[0])} />
            <p className="text-xs text-gray-500 dark:text-gray-400 mt-3">Up to 10 MB / 50,000 rows. CSV data is checked in batches, not imported into Contacts. Leading zeros in phone numbers are preserved.</p>
          </div>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={hasHeader} onChange={e => setHasHeader(e.target.checked)} className="accent-primary-600" />First row contains headers</label>
          {fileLoading && <p className="text-sm" role="status">Reading CSV…</p>}
          {csv.error && <p role="alert" className="text-danger-600 text-sm">{csv.error}</p>}
          {csv.data && <>
            <p className="text-sm font-medium break-all">{fileName} · {csv.data.rows.length.toLocaleString()} rows ready to map</p>
            <div className="grid sm:grid-cols-3 gap-3">{([ ["email", "Email column"], ["phone", "Phone column"], ["name", "Name column (optional)"] ] as const).map(([field, label]) => <div key={field} className="min-w-0"><label className="label" htmlFor={`map-${field}`}>{label}</label><select id={`map-${field}`} className="input" value={mapping[field]} onChange={e => setMapping({ ...mapping, [field]: e.target.value })}><option value="">Not included</option>{csv.data!.headers.map((header, i) => <option key={i} value={i}>{header} (column {i + 1})</option>)}</select>{mapping[field] !== "" && <p className="text-xs text-gray-500 mt-1 break-all">Example: {csv.data!.rows[0]?.[Number(mapping[field])] || "(empty)"}</p>}</div>)}</div>
          </>}
        </div>}

        <div className="border-t border-gray-100 dark:border-gray-700 pt-4 space-y-3">
          <div className="grid sm:grid-cols-2 gap-4">
            <div><label className="label" htmlFor="check-kind">What to check</label><select id="check-kind" className="input" value={check} onChange={e => setCheck(e.target.value as CheckKind)}><option value="both">Email + phone</option><option value="email">Email only</option><option value="phone">Phone only (Nigerian mobiles)</option></select></div>
            {check !== "phone" && <div><label className="label" htmlFor="check-depth">Email check depth</label><select id="check-depth" className="input" value={deep ? "deep" : "quick"} onChange={e => setDeep(e.target.value === "deep")}><option value="deep">Deep — include mailbox check</option><option value="quick">Quick — syntax, DNS and risk flags</option></select></div>}
          </div>
          {check !== "phone" && <p className="text-xs text-gray-500 dark:text-gray-400">{deep ? "Deep checks can take a minute per batch. Blocked SMTP, timeouts and inconclusive responses stay Unknown, never falsely Good or Bad." : "Quick checks cannot confirm a mailbox exists. Well-formed addresses with mail records remain Unknown, unless risk flags are found."}</p>}
          {savedSource && <div className="rounded-xl bg-gray-50 dark:bg-gray-900/50 p-3"><label className="flex items-start gap-2 text-sm font-medium"><input type="checkbox" checked={save} onChange={e => setSave(e.target.checked)} className="mt-1 accent-primary-600" />Save results to existing contacts</label><p className="text-xs text-gray-500 dark:text-gray-400 ml-5 mt-1">Off by default: review only. When enabled, confirmed-bad channels are quarantined and confirmed-good emails are verified. No contacts are deleted or removed from lists. Opt-outs, suppressions and existing delivery blocks stay protected.</p></div>}
        </div>
      </fieldset>
      <div className="px-4 sm:px-5 pb-5 flex flex-wrap items-center gap-3">
        <button className="btn-primary" disabled={running || fileLoading} onClick={() => void start()}>{running ? <Loader2 size={16} className="animate-spin" /> : <Play size={16} />} {running ? "Validating…" : "Start validation"}</button>
        {running && <button className="btn-secondary" disabled={stopping} onClick={() => { stop.current = true; setStopping(true); }}><Square size={14} />{stopping ? "Stopping after this batch…" : "Stop after batch"}</button>}
        {!running && activeRun.current && activeRun.current.next < activeRun.current.total && <button className="btn-secondary" onClick={resume}>Resume remaining</button>}
        <span className="text-xs text-gray-500">{savedSource && save ? "Save mode — confirmation required" : "Preview only · no contact changes"}</span>
      </div>
    </section>

    {error && <div role="alert" className="flex gap-2 rounded-xl bg-danger-50 dark:bg-danger-950/30 border border-danger-200 dark:border-danger-800 p-4 text-sm text-danger-700 dark:text-danger-300"><AlertCircle size={18} className="shrink-0" /><span className="break-words min-w-0">{error}{rows.length > 0 && " Completed results are kept below. Resume to retry the unfinished batch."}</span></div>}

    {runState !== "idle" && <section className="space-y-4" aria-label="Validation results">
      <div className="card p-4 sm:p-5 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2"><div className="min-w-0"><h2 className="text-lg font-semibold">Validation results</h2><p className="text-sm text-gray-500 break-words">{runLabel}</p></div><span role="status" className="text-sm font-medium">{running ? "Checking" : runState === "paused" ? "Paused" : runState === "error" ? "Incomplete" : "Complete"} · {rows.length.toLocaleString()} / {total.toLocaleString()}</span></div>
        <div role="progressbar" aria-label="Validation progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress} className="h-2 rounded-full bg-gray-100 dark:bg-gray-700 overflow-hidden"><div className="h-full bg-primary-600 transition-all rounded-full" style={{ width: `${progress}%` }} /></div>
        <p className="text-xs text-gray-500 dark:text-gray-400">Keep this page open while checking. You can stop and resume here. Export your report before leaving; results are not stored as a separate history. {activeRun.current?.save ? "This run saves confirmed validation results to existing contacts." : "This run is read-only."}</p>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-6 gap-2" aria-label="Result filters">
        {[{ key: "all", label: "All results", badge: "badge-gray" }, ...STATES].map(item => <button key={item.key} aria-pressed={filter === item.key} onClick={() => { setFilter(item.key as typeof filter); setPage(1); }} className={`card p-3 text-left transition-colors ${filter === item.key ? "ring-2 ring-primary-500" : "hover:bg-gray-50 dark:hover:bg-gray-700"}`}><span className={item.badge}>{item.label}</span><span className="block text-2xl font-semibold mt-2 tabular-nums">{(item.key === "all" ? rows.length : counts[item.key as ResultState]).toLocaleString()}</span></button>)}
      </div>
      <p className="text-xs text-gray-500 dark:text-gray-400">Bad = at least one checked field is invalid. Risky = review first. Unknown = not proven either way. Missing = no data for the chosen checks. Good phone results mean valid format only. A validation result is not consent to send.</p>
      <div className="card overflow-hidden">
        <div className="p-3 sm:p-4 border-b border-gray-100 dark:border-gray-700 flex flex-col sm:flex-row sm:items-center gap-3">
          <div className="relative flex-1 min-w-0"><Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" /><input className="input pl-9" aria-label="Search validation results" placeholder="Search results or reasons…" value={resultSearch} onChange={e => { setResultSearch(e.target.value); setPage(1); }} /></div>
          <div className="flex flex-wrap gap-2"><button className="btn-secondary btn-sm" disabled={!filtered.length} onClick={() => downloadValidationCsv(filtered, `validator-${filter}.csv`)}><Download size={15} />Export shown ({filtered.length})</button><button className="btn-secondary btn-sm" disabled={!rows.length} onClick={() => downloadValidationCsv(annotated)}><Download size={15} />Export all</button></div>
        </div>
        {!displayed.length ? <div className="p-8 text-center text-sm text-gray-500">{running ? "Checking the first batch. Deep mailbox checks may take a minute…" : total === 0 && runState === "complete" ? "No matching contacts. Adjust your filters and try again." : rows.length ? "No results match this filter." : "No results yet. Check the message above and try again."}</div> : <div className="divide-y divide-gray-100 dark:divide-gray-700">{displayed.map((row, index) => <details key={`${row.contact_id ?? row.row ?? "single"}-${(currentPage - 1) * 25 + index}`} className="group">
          <summary className="cursor-pointer p-3 sm:p-4 list-none hover:bg-gray-50 dark:hover:bg-gray-900/40 flex items-start gap-3">
            <span className="text-xs text-gray-400 pt-1 w-7 shrink-0">{row.row || (currentPage - 1) * 25 + index + 1}</span>
            <div className="min-w-0 flex-1 space-y-1"><p className="font-medium text-sm break-words">{row.name || row.input_email || row.input_phone || "Empty contact"}</p><div className="text-xs text-gray-500 dark:text-gray-400 flex flex-col sm:flex-row sm:flex-wrap gap-x-4 gap-y-1"><span className="break-all">{row.input_email || "No email"}</span><span className="break-all">{row.input_phone || "No phone"}</span></div><div className="flex flex-wrap gap-2 text-[11px] text-gray-500">{row.email && <span>Email: {row.email.verdict}</span>}{row.phone && <span>Phone: {row.phone.verdict}</span>}{row.blocked.length > 0 && <span className="text-danger-600 dark:text-danger-400">Sending blocked</span>}{row.duplicate_of && <span className="text-warning-700 dark:text-warning-300">Duplicate of row {row.duplicate_of}</span>}{row.saved && <span>Saved to contact</span>}</div></div>
            <div className="flex items-center gap-2 shrink-0"><Badge value={row.status} /><ChevronRight size={15} className="text-gray-400 group-open:rotate-90 transition-transform" /></div>
          </summary>
          <div className="px-4 pb-4 sm:pl-14 text-sm space-y-3"><div className="grid md:grid-cols-2 gap-4 rounded-xl bg-gray-50 dark:bg-gray-900/60 p-3"><ChannelDetail kind="Email" result={row.email} /><ChannelDetail kind="Phone" result={row.phone} /></div>{row.blocked.length > 0 && <p className="text-danger-700 dark:text-danger-300">Sending restrictions: {row.blocked.join(" · ")}. Validation does not remove these restrictions.</p>}{row.note && <p className="text-warning-700 dark:text-warning-300">{row.note}</p>}<p className="text-xs text-gray-500">Checked {new Date(row.checked_at).toLocaleString()}{row.contact_id ? ` · Contact #${row.contact_id}` : " · Not imported"}{row.saved ? " · Saved" : " · Not saved"}</p></div>
        </details>)}</div>}
        <div className="p-3 sm:p-4 border-t border-gray-100 dark:border-gray-700 flex flex-wrap justify-between items-center gap-2 text-sm"><span className="text-gray-500">{filtered.length.toLocaleString()} results · page {currentPage} of {pages}</span><div className="flex gap-2"><button className="btn-secondary btn-sm" aria-label="Previous result page" disabled={currentPage === 1} onClick={() => setPage(currentPage - 1)}><ChevronLeft size={16} /></button><button className="btn-secondary btn-sm" aria-label="Next result page" disabled={currentPage === pages} onClick={() => setPage(currentPage + 1)}><ChevronRight size={16} /></button></div></div>
      </div>
    </section>}
  </div>;
}
