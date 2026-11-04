import { useState, useEffect, useRef, useCallback } from "react";
import api from "../api/client";
import toast from "react-hot-toast";
import {
  MessageSquare, Upload, Trash2, Plus, Settings, X, ChevronLeft,
  Send, Sparkles, Menu, FileText, Code, Layers, Cpu, Brain,
  ChevronDown, ChevronUp, Paperclip, Zap, Database, Users, Megaphone, CheckCircle, AlertCircle, Copy
} from "lucide-react";
import { useNavigate } from "react-router-dom";

type ModelType = "groq" | "gemini";
type ChatMessage = { id?: number; role: "user" | "assistant" | "system" | "tool"; content: string; tool_calls?: any; created_at?: string; timestamp?: number };
type Session = { id: string; name: string; model: ModelType; memory?: string; skills?: string[]; updated_at?: string; created_at?: string };
type Skill = { id: number; name: string; description?: string; is_active?: boolean };
type FileItem = { id: number; filename: string; size: number; preview?: any; created_at?: string };

const MODEL_LABEL: Record<ModelType, string> = { groq: "Groq (Madeline)", gemini: "Gemini" };
const MODEL_ICON: Record<ModelType, any> = { groq: Cpu, gemini: Brain };

export default function AIAgentPage() {
  const navigate = useNavigate();
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionMeta, setSessionMeta] = useState<Session | null>(null);
  const [files, setFiles] = useState<FileItem[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [loadingSessions, setLoadingSessions] = useState(true);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [showSkills, setShowSkills] = useState(false);
  const [showFiles, setShowFiles] = useState(false);
  const [showMemory, setShowMemory] = useState(false);
  const [aiStatus, setAiStatus] = useState<{ groq_configured: boolean; gemini_configured: boolean } | null>(null);
  const [modelOverride, setModelOverride] = useState<ModelType | "">("");
  const chatRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const skillInputRef = useRef<HTMLInputElement>(null);

  const active = sessions.find(s => s.id === activeId) || sessionMeta;

  // Load sessions + status + skills
  const loadSessions = useCallback(async () => {
    try {
      const [sRes, statusRes, skillsRes] = await Promise.all([
        api.get("/ai-agent/sessions").catch(() => ({ data: { sessions: [] } })),
        api.get("/ai-agent/settings").catch(() => ({ data: { groq_configured: false, gemini_configured: false } })),
        api.get("/ai-agent/skills").catch(() => ({ data: { skills: [] } })),
      ]);
      const list: Session[] = sRes.data.sessions || [];
      setSessions(list);
      setAiStatus({ groq_configured: statusRes.data.groq_configured, gemini_configured: statusRes.data.gemini_configured });
      setSkills(skillsRes.data.skills || []);
      if (list.length && !activeId) setActiveId(list[0].id);
    } catch {}
    finally { setLoadingSessions(false); }
  }, [activeId]);

  useEffect(() => { loadSessions(); }, []);

  // Load active session details
  const loadSession = useCallback(async (id: string) => {
    try {
      const { data } = await api.get(`/ai-agent/sessions/${id}`);
      setSessionMeta({ id: data.session.id, name: data.session.name, model: data.session.model, memory: data.session.memory, skills: data.session.skills });
      setMessages(data.messages.map((m: any) => ({ ...m, timestamp: new Date(m.created_at).getTime() })));
      setFiles(data.files || []);
      // refresh skills list too
      api.get("/ai-agent/skills").then(r => setSkills(r.data.skills || [])).catch(() => {});
    } catch (e: any) {
      toast.error(e.response?.data?.detail || "Could not load session");
    }
  }, []);

  useEffect(() => { if (activeId) loadSession(activeId); }, [activeId, loadSession]);

  // Auto-scroll to bottom
  useEffect(() => { if (chatRef.current) chatRef.current.scrollTop = chatRef.current.scrollHeight; }, [messages, loading]);

  // Create session
  const createSession = async () => {
    try {
      const { data } = await api.post("/ai-agent/sessions", { name: `Session ${sessions.length + 1}`, model: "groq" });
      const s = data.session;
      const newList = [{ id: s.id, name: s.name, model: s.model, updated_at: s.created_at }, ...sessions];
      setSessions(newList);
      setActiveId(s.id);
      setDrawerOpen(false);
      toast.success("New session created");
    } catch (e: any) { toast.error(e.response?.data?.detail || "Failed to create session"); }
  };

  const deleteSession = async (id: string) => {
    if (!confirm("Delete this session and all its messages?")) return;
    try {
      await api.delete(`/ai-agent/sessions/${id}`);
      const next = sessions.filter(s => s.id !== id);
      setSessions(next);
      if (activeId === id) setActiveId(next[0]?.id || null);
      toast.success("Session deleted");
    } catch { toast.error("Failed to delete"); }
  };

  const sendMessage = async () => {
    if (!input.trim() || !activeId || loading) return;
    const text = input.trim();
    setInput("");
    const userMsg: ChatMessage = { role: "user", content: text, timestamp: Date.now() };
    setMessages(prev => [...prev, userMsg]);
    setLoading(true);
    try {
      const payload: any = { message: text };
      if (modelOverride) payload.model = modelOverride;
      // Include pending file ids if user referenced a file? For now auto-attach recent file if text mentions csv/file
      const { data } = await api.post(`/ai-agent/sessions/${activeId}/chat`, payload);
      setMessages(data.messages.map((m: any) => ({ ...m, timestamp: new Date(m.created_at).getTime() })));
      // refresh sessions list to update updated_at
      const sRes = await api.get("/ai-agent/sessions");
      setSessions(sRes.data.sessions || []);
      // refresh session meta for memory
      const sDetail = await api.get(`/ai-agent/sessions/${activeId}`);
      setSessionMeta({ id: sDetail.data.session.id, name: sDetail.data.session.name, model: sDetail.data.session.model, memory: sDetail.data.session.memory, skills: sDetail.data.session.skills });
    } catch (e: any) {
      const detail = e.response?.data?.detail || e.message || "Agent error";
      toast.error(detail);
      // Show error as assistant message for visibility
      setMessages(prev => [...prev, { role: "assistant", content: `⚠️ ${detail}`, timestamp: Date.now() }]);
    } finally { setLoading(false); }
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const list = e.target.files;
    if (!list || !list.length || !activeId) return;
    const fd = new FormData();
    fd.append("file", list[0]);
    try {
      toast.loading("Uploading file…", { id: "fileup" });
      const { data } = await api.post(`/ai-agent/sessions/${activeId}/files`, fd, { headers: { "Content-Type": "multipart/form-data" } } as any);
      toast.success(`Uploaded ${data.file.filename}`, { id: "fileup" });
      setFiles(prev => [data.file, ...prev]);
      // Pre-fill input with hint
      setInput(prev => prev ? `${prev} Use file #${data.file.id} (${data.file.filename})` : `Import contacts from ${data.file.filename} (file #${data.file.id}) — map phone and email columns`);
      inputRef.current?.focus();
    } catch (err: any) { toast.error(err.response?.data?.detail || "Upload failed", { id: "fileup" }); }
    finally { if (fileInputRef.current) fileInputRef.current.value = ""; }
  };

  const handleSkillUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const list = e.target.files;
    if (!list || !list.length) return;
    const fd = new FormData();
    fd.append("file", list[0]);
    fd.append("name", list[0].name.replace(/\.[^.]+$/, ""));
    try {
      toast.loading("Uploading skill…", { id: "skillup" });
      await api.post("/ai-agent/skills", fd, { headers: { "Content-Type": "multipart/form-data" } } as any);
      toast.success(`Skill ${list[0].name} added`, { id: "skillup" });
      const r = await api.get("/ai-agent/skills");
      setSkills(r.data.skills || []);
    } catch (err: any) { toast.error(err.response?.data?.detail || "Skill upload failed", { id: "skillup" }); }
    finally { if (skillInputRef.current) skillInputRef.current.value = ""; }
  };

  const copyText = async (text: string) => {
    try { await navigator.clipboard.writeText(text); toast.success("Copied"); } catch {}
  };

  const needsKey = aiStatus && !aiStatus.groq_configured && !aiStatus.gemini_configured;
  const activeModel: ModelType = (modelOverride as ModelType) || (active?.model as ModelType) || "groq";
  const ModelIcon = MODEL_ICON[activeModel] || Cpu;

  return (
    <div className="flex flex-col h-[calc(100vh-64px)] lg:h-[calc(100vh-88px)] -m-4 sm:-m-6 lg:mx-0 bg-white dark:bg-gray-900 rounded-none lg:rounded-2xl overflow-hidden border border-gray-200 dark:border-gray-800 shadow-sm">
      {/* Top bar */}
      <div className="shrink-0 h-14 sm:h-16 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between px-3 sm:px-5 bg-white dark:bg-gray-900 gap-2">
        <div className="flex items-center gap-2 sm:gap-3 min-w-0">
          <button className="lg:hidden p-2 -ml-1 rounded-xl hover:bg-gray-100 dark:hover:bg-gray-800" onClick={() => setDrawerOpen(v => !v)} aria-label="Toggle sessions">
            <Menu size={18} />
          </button>
          <div className="w-8 h-8 sm:w-9 sm:h-9 rounded-xl bg-primary-900 dark:bg-primary-800 flex items-center justify-center text-white shrink-0">
            <Sparkles size={16} className="sm:w-[18px] sm:h-[18px]" />
          </div>
          <div className="min-w-0">
            <h1 className="text-[15px] sm:text-[17px] font-semibold leading-none truncate flex items-center gap-1.5">AI Agent <span className="hidden sm:inline text-[11px] font-medium px-1.5 py-0.5 rounded-full bg-primary-50 dark:bg-primary-900/30 text-primary-700 dark:text-primary-300">Full Access</span></h1>
            <p className="text-[11px] sm:text-xs text-gray-500 dark:text-gray-400 truncate hidden sm:block">Groq (Madeline) + Gemini — campaigns, contacts, inbox, everything.</p>
          </div>
        </div>
        <div className="flex items-center gap-1.5 sm:gap-2 shrink-0">
          <button onClick={createSession} className="inline-flex items-center gap-1 sm:gap-1.5 px-2.5 sm:px-3.5 py-2 rounded-full bg-primary-900 dark:bg-primary-800 text-white text-xs sm:text-sm font-medium hover:bg-primary-800 dark:hover:bg-primary-700 transition-colors">
            <Plus size={14} /> <span className="hidden sm:inline">New</span><span className="sm:hidden">New</span>
          </button>
          <button onClick={() => navigate("/settings?tab=ai")} className="hidden sm:inline-flex items-center gap-1.5 px-3 py-2 rounded-full bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-200 text-sm font-medium hover:bg-gray-200 dark:hover:bg-gray-700">
            <Settings size={14} /> Keys
          </button>
          <button onClick={() => navigate("/settings?tab=ai")} className="sm:hidden p-2 rounded-full bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-200">
            <Settings size={16} />
          </button>
        </div>
      </div>

      {needsKey && (
        <div className="shrink-0 px-3 sm:px-4 py-2.5 bg-warning-50 dark:bg-warning-950/30 border-b border-warning-200 dark:border-warning-900 flex items-start gap-2.5 text-xs sm:text-sm">
          <AlertCircle size={16} className="text-warning-600 dark:text-warning-400 shrink-0 mt-0.5" />
          <div className="min-w-0 flex-1">
            <p className="font-medium text-warning-900 dark:text-warning-100">No LLM key set — the agent can&apos;t answer yet.</p>
            <p className="text-warning-800/80 dark:text-warning-200/80 mt-0.5">Add your <b>Groq</b> (Madeline) or <b>Gemini</b> API key in Settings → AI Agent. The agent needs one key to chat. <button onClick={() => navigate("/settings?tab=ai")} className="underline font-medium">Open Settings →</button></p>
          </div>
        </div>
      )}

      <div className="flex flex-1 overflow-hidden relative">
        {/* Sidebar — drawer on mobile, fixed on desktop */}
        <div className={`${drawerOpen ? "translate-x-0" : "-translate-x-full"} lg:translate-x-0 fixed lg:static inset-y-0 left-0 z-20 w-[84%] max-w-[300px] lg:w-[300px] border-r border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 flex flex-col transition-transform duration-200 ease-out lg:shrink-0`}>
          <div className="shrink-0 p-3 sm:p-4 border-b border-gray-100 dark:border-gray-800 flex items-center justify-between">
            <h2 className="font-semibold text-sm">Sessions</h2>
            <div className="flex items-center gap-1">
              <span className="text-[11px] px-2 py-1 rounded-full bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300">{sessions.length}</span>
              <button onClick={() => setDrawerOpen(false)} className="lg:hidden p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800"><X size={14} /></button>
            </div>
          </div>
          <div className="flex-1 overflow-y-auto p-2 space-y-1 scrollbar-thin">
            {loadingSessions ? (
              <div className="p-4 space-y-3">{[0,1,2].map(i=> <div key={i} className="h-16 rounded-xl bg-gray-100 dark:bg-gray-800 animate-pulse" />)}</div>
            ) : sessions.length === 0 ? (
              <div className="p-6 text-center">
                <MessageSquare size={28} className="mx-auto text-gray-300 dark:text-gray-600 mb-2" />
                <p className="text-sm text-gray-500 dark:text-gray-400">No sessions yet</p>
                <button onClick={createSession} className="mt-3 btn-primary btn-sm">Start one →</button>
              </div>
            ) : sessions.map(s => {
              const isActive = s.id === activeId;
              return (
                <button key={s.id} onClick={() => { setActiveId(s.id); setDrawerOpen(false); }} className={`w-full text-left p-3 rounded-xl flex gap-2.5 transition-colors ${isActive ? "bg-primary-50 dark:bg-primary-900/30 text-primary-900 dark:text-primary-100 border border-primary-200 dark:border-primary-800" : "hover:bg-gray-50 dark:hover:bg-gray-800 border border-transparent"}`}>
                  <div className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${isActive ? "bg-primary-900 dark:bg-primary-700 text-white" : "bg-gray-100 dark:bg-gray-800 text-gray-500"}`}>
                    {s.model === "gemini" ? <Brain size={14} /> : <Cpu size={14} />}
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="text-[13px] font-medium truncate leading-none">{s.name}</p>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1 flex items-center gap-1.5"><span className="uppercase tracking-wide">{s.model}</span> <span>•</span> {s.updated_at ? new Date(s.updated_at).toLocaleDateString() : ""}</p>
                  </div>
                </button>
              );
            })}
          </div>
          {/* Sidebar footer: skills + files quick access */}
          <div className="shrink-0 p-3 border-t border-gray-100 dark:border-gray-800 bg-gray-50/50 dark:bg-gray-800/20 space-y-2">
            <button onClick={() => setShowSkills(v=>!v)} className="w-full flex items-center justify-between px-3 py-2 rounded-xl bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 text-xs font-medium hover:bg-gray-50 dark:hover:bg-gray-750">
              <span className="flex items-center gap-1.5"><Layers size={12}/> Skills ({skills.length})</span>{showSkills ? <ChevronUp size={12}/> : <ChevronDown size={12}/>}
            </button>
            {showSkills && (
              <div className="rounded-xl bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 p-2 max-h-32 overflow-y-auto">
                {skills.length===0 ? <p className="text-[11px] text-gray-500 px-2 py-1">No skills. Upload one — a .md/.txt with instructions. Use @name to invoke.</p> : skills.map(sk=>(
                  <div key={sk.id} className="flex items-center justify-between px-2 py-1.5 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700/50">
                    <span className="text-xs font-mono truncate">@{sk.name}</span><button onClick={async()=>{ await api.delete(`/ai-agent/skills/${sk.id}`); setSkills(prev=>prev.filter(x=>x.id!==sk.id)); toast.success("Skill removed");}} className="p-1 text-gray-400 hover:text-danger-600"><Trash2 size={12}/></button>
                  </div>
                ))}
                <label className="mt-2 flex items-center justify-center gap-1.5 px-2 py-1.5 rounded-lg border border-dashed border-gray-300 dark:border-gray-600 text-[11px] font-medium cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700/30"><Upload size={12}/> Upload skill<input ref={skillInputRef} type="file" accept=".md,.txt,.json" className="hidden" onChange={handleSkillUpload} /></label>
              </div>
            )}
            {activeId && <button onClick={() => deleteSession(activeId)} className="w-full flex items-center justify-center gap-1.5 px-3 py-2 rounded-xl text-xs font-medium text-gray-500 hover:text-danger-600 hover:bg-white dark:hover:bg-gray-800 border border-transparent hover:border-gray-200 dark:hover:border-gray-700"><Trash2 size={12}/> Delete session</button>}
          </div>
        </div>
        {/* Backdrop for drawer */}
        {drawerOpen && <div className="fixed inset-0 bg-black/20 dark:bg-black/40 z-10 lg:hidden" onClick={()=>setDrawerOpen(false)} />}

        {/* Main chat */}
        <div className="flex-1 flex flex-col min-w-0 bg-gray-50 dark:bg-gray-950">
          {!activeId ? (
            <div className="flex-1 flex flex-col items-center justify-center p-6 sm:p-10 text-center">
              <div className="w-16 h-16 rounded-2xl bg-primary-900 dark:bg-primary-800 flex items-center justify-center text-white mb-4 shadow-lg"><Sparkles size={28}/></div>
              <h2 className="text-xl font-semibold">Your AI Agent</h2>
              <p className="text-sm text-gray-500 dark:text-gray-400 mt-2 max-w-md leading-relaxed">Talk to your workspace like a teammate. <b>Create campaigns</b>, <b>import CSVs</b>, <b>delete contacts</b>, <b>check inbox</b>, <b>run analytics</b> — everything you can do in the UI, the agent can do via tools. Upload a skill and call it with <code className="px-1 py-0.5 rounded bg-gray-100 dark:bg-gray-800 font-mono text-xs">@skill</code>.</p>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 sm:gap-3 mt-6 w-full max-w-lg">
                {[
                  { t: "Create a campaign", d: "Create SMS campaign for my Lagos contacts", icon: Megaphone },
                  { t: "Clean my list", d: "Find and delete invalid contacts", icon: Users },
                  { t: "Show inbox", d: "Who replied interested today?", icon: MessageSquare },
                  { t: "Analyze", d: "How did my last campaign perform?", icon: Database },
                ].map(card=>(
                  <button key={card.t} onClick={async()=>{
                    const prompt = card.d;
                    if (!activeId) {
                      // create session then send
                      const { data } = await api.post("/ai-agent/sessions", { name: card.t, model: "groq" });
                      setSessions(prev=>[{id:data.session.id, name:data.session.name, model:data.session.model}, ...prev]);
                      setActiveId(data.session.id);
                      setTimeout(()=> { setInput(prompt); }, 300);
                    } else { setInput(prompt); inputRef.current?.focus(); }
                  }} className="text-left p-3 sm:p-4 rounded-2xl bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 hover:border-primary-300 dark:hover:border-primary-700 hover:shadow-sm transition-all">
                    <card.icon size={16} className="text-primary-700 dark:text-primary-300 mb-1.5"/>
                    <p className="text-sm font-medium">{card.t}</p><p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5 line-clamp-1">{card.d}</p>
                  </button>
                ))}
              </div>
              <button onClick={createSession} className="mt-6 inline-flex items-center gap-2 px-5 py-2.5 rounded-full bg-primary-900 dark:bg-primary-800 text-white text-sm font-medium hover:bg-primary-800 dark:hover:bg-primary-700"><Plus size={16}/> New Session</button>
            </div>
          ) : (
            <>
              {/* Chat header */}
              <div className="shrink-0 h-12 sm:h-14 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between px-3 sm:px-4 bg-white dark:bg-gray-900 gap-2">
                <div className="flex items-center gap-2 min-w-0 flex-1">
                  <button onClick={()=> setActiveId(null)} className="sm:hidden p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800"><ChevronLeft size={18}/></button>
                  <div className={`w-7 h-7 sm:w-8 sm:h-8 rounded-lg flex items-center justify-center text-white shrink-0 ${activeModel==="gemini" ? "bg-violet-600" : "bg-primary-800"}`}><ModelIcon size={14} /></div>
                  <div className="min-w-0">
                    <p className="text-sm font-semibold truncate leading-none">{active?.name || sessionMeta?.name || "Session"}</p>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400">Memory • {sessionMeta?.memory ? `${Math.min(6000, sessionMeta.memory.length)} chars` : "empty"} • {files.length} files</p>
                  </div>
                </div>
                <div className="flex items-center gap-1 sm:gap-1.5 shrink-0">
                  <div className="hidden sm:flex items-center gap-1 p-1 rounded-full bg-gray-100 dark:bg-gray-800">
                    {(["groq","gemini"] as ModelType[]).map(m=>{
                      const on = activeModel===m;
                      return <button key={m} onClick={()=> { setModelOverride(m); if (activeId) api.patch(`/ai-agent/sessions/${activeId}`, { model: m }).catch(()=>{}); }} className={`px-2.5 sm:px-3 py-1 rounded-full text-xs font-medium transition-colors ${on ? "bg-white dark:bg-gray-700 shadow-sm text-gray-900 dark:text-white" : "text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"}`}>{MODEL_LABEL[m]}</button>;
                    })}
                  </div>
                  <select value={modelOverride || active?.model || "groq"} onChange={e=> { setModelOverride(e.target.value as ModelType); if (activeId) api.patch(`/ai-agent/sessions/${activeId}`, { model: e.target.value }).catch(()=>{}); }} className="sm:hidden text-xs px-2 py-1.5 rounded-full bg-gray-100 dark:bg-gray-800 border-none">
                    <option value="groq">Groq</option><option value="gemini">Gemini</option>
                  </select>
                  <button onClick={()=> setShowFiles(v=>!v)} className={`p-2 rounded-lg border text-xs font-medium ${showFiles ? "bg-primary-50 dark:bg-primary-900/20 border-primary-200 dark:border-primary-800 text-primary-800 dark:text-primary-200" : "bg-white dark:bg-gray-800 border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300"}`} title="Files"><Paperclip size={14}/></button>
                  <button onClick={()=> setShowMemory(v=>!v)} className={`p-2 rounded-lg border text-xs font-medium ${showMemory ? "bg-primary-50 dark:bg-primary-900/20 border-primary-200 dark:border-primary-800 text-primary-800 dark:text-primary-200" : "bg-white dark:bg-gray-800 border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300"}`} title="Memory"><Brain size={14}/></button>
                </div>
              </div>

              {(showFiles || showMemory) && (
                <div className="shrink-0 border-b border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 p-3 flex flex-col sm:flex-row gap-3 max-h-[40vh] overflow-y-auto">
                  {showFiles && (
                    <div className="flex-1 min-w-0 rounded-xl border border-gray-200 dark:border-gray-700 p-3">
                      <p className="text-xs font-semibold flex items-center gap-1.5"><Paperclip size={12}/> Session files</p>
                      {files.length===0 ? <p className="text-xs text-gray-500 mt-2">No files. Upload a CSV to import contacts, etc.</p> : <ul className="mt-2 space-y-1.5">{files.map(f=>(
                        <li key={f.id} className="flex items-center justify-between gap-2 px-2 py-1.5 rounded-lg bg-gray-50 dark:bg-gray-800 text-xs">
                          <span className="truncate flex items-center gap-1.5"><FileText size={12} className="shrink-0"/>{f.filename} <span className="text-[10px] text-gray-400">#{f.id} • {(f.size/1024).toFixed(1)}KB</span></span>
                          <span className="text-[11px] text-gray-500 hidden sm:inline">{f.preview?.headers?.slice(0,3).join(", ")}</span>
                        </li>
                      ))}</ul>}
                    </div>
                  )}
                  {showMemory && (
                    <div className="flex-1 min-w-0 rounded-xl border border-gray-200 dark:border-gray-700 p-3">
                      <p className="text-xs font-semibold flex items-center gap-1.5"><Brain size={12}/> Memory</p>
                      <p className="text-xs text-gray-600 dark:text-gray-300 mt-2 whitespace-pre-wrap break-words max-h-32 overflow-y-auto">{sessionMeta?.memory || "No memory yet. The agent remembers your preferences and past actions in this session."}</p>
                    </div>
                  )}
                </div>
              )}

              {/* Messages */}
              <div ref={chatRef} className="flex-1 overflow-y-auto p-3 sm:p-5 space-y-4 scrollbar-thin">
                {messages.length===0 && (
                  <div className="text-center py-8">
                    <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-primary-800 to-primary-600 flex items-center justify-center text-white mx-auto"><Zap size={20}/></div>
                    <p className="text-sm font-medium mt-3">How can I help today?</p>
                    <p className="text-xs text-gray-500 dark:text-gray-400 mt-1.5 max-w-sm mx-auto leading-relaxed">Try: <b>“Create an SMS campaign for my 2025 leads”</b> • <b>“Delete contacts without phone numbers”</b> • <b>“What does @my_skill do?”</b> • Upload a CSV and say <b>“import this”</b></p>
                  </div>
                )}
                {messages.filter(m=> m.role!=="system").map((m,i)=> {
                  const isUser = m.role==="user";
                  const isTool = m.role==="tool";
                  const isAssistant = m.role==="assistant";
                  if (isTool) {
                    // Tool results are shown as collapsible technical cards, not chat bubbles
                    let parsed: any = null;
                    try { parsed = JSON.parse(m.content); } catch {}
                    const isError = parsed?.error;
                    return (
                      <div key={i} className="flex justify-start">
                        <div className={`max-w-[90%] sm:max-w-[85%] rounded-xl border px-3 py-2 text-xs ${isError ? "bg-danger-50 dark:bg-danger-950/30 border-danger-200 dark:border-danger-900 text-danger-900 dark:text-danger-100" : "bg-emerald-50 dark:bg-emerald-950/20 border-emerald-200 dark:border-emerald-900 text-emerald-900 dark:text-emerald-100"}`}>
                          <p className="font-medium flex items-center gap-1.5">{isError ? <AlertCircle size={12}/> : <CheckCircle size={12}/>} Tool: { (m as any).tool_call_id ? "" : ""} {m.content.slice(0,120)}</p>
                          <details className="mt-1"><summary className="cursor-pointer text-[11px] underline opacity-70">Details</summary><pre className="mt-1 whitespace-pre-wrap break-words text-[11px] opacity-90 max-h-48 overflow-y-auto">{m.content.slice(0,3000)}</pre></details>
                        </div>
                      </div>
                    );
                  }
                  return (
                    <div key={i} className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
                      <div className={`max-w-[88%] sm:max-w-[78%] rounded-2xl px-3.5 sm:px-4 py-3 shadow-sm text-[13.5px] leading-[1.6] whitespace-pre-wrap break-words ${isUser ? "bg-primary-900 dark:bg-primary-800 text-white rounded-br-md" : isAssistant ? "bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-bl-md" : "bg-gray-100 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 text-xs text-gray-600 dark:text-gray-300"}`}>
                        {m.content}
                        <div className={`flex items-center gap-1.5 mt-2 text-[10px] ${isUser ? "text-white/60 justify-end" : "text-gray-400"}`}>
                          <span>{m.timestamp ? new Date(m.timestamp).toLocaleTimeString([], {hour:"2-digit", minute:"2-digit"}) : m.created_at ? new Date(m.created_at).toLocaleTimeString([], {hour:"2-digit", minute:"2-digit"}) : ""}</span>
                          {!isUser && <button onClick={()=>copyText(m.content)} className="p-1 rounded hover:bg-gray-100 dark:hover:bg-gray-800" title="Copy"><Copy size={10}/></button>}
                        </div>
                      </div>
                    </div>
                  );
                })}
                {loading && (
                  <div className="flex justify-start"><div className="max-w-[85%] sm:max-w-[78%] rounded-2xl rounded-bl-md bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 px-4 py-4 shadow-sm"><div className="flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400"><span className="w-2 h-2 rounded-full bg-primary-600 animate-pulse"/><span className="w-2 h-2 rounded-full bg-primary-600 animate-pulse [animation-delay:150ms]"/><span className="w-2 h-2 rounded-full bg-primary-600 animate-pulse [animation-delay:300ms]"/><span className="ml-1">Thinking & acting…</span></div></div></div>
                )}
              </div>

              {/* Composer */}
              <div className="shrink-0 p-3 sm:p-4 border-t border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900">
                <div className="flex items-end gap-2">
                  <div className="flex items-center gap-1 shrink-0">
                    <label className="p-2 sm:p-2.5 rounded-xl bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-600 dark:text-gray-300 cursor-pointer transition-colors" title="Upload CSV / file">
                      <Paperclip size={16} />
                      <input ref={fileInputRef} type="file" accept=".csv,.txt,.json,.xlsx" className="hidden" onChange={handleFileUpload} />
                    </label>
                    <label className="hidden sm:flex p-2.5 rounded-xl bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-600 dark:text-gray-300 cursor-pointer transition-colors" title="Upload skill (.md, .txt)">
                      <Layers size={16} />
                      <input ref={skillInputRef} type="file" accept=".md,.txt,.json" className="hidden" onChange={handleSkillUpload} />
                    </label>
                  </div>
                  <div className="flex-1 relative min-w-0">
                    <input
                      ref={inputRef}
                      value={input}
                      onChange={e=>setInput(e.target.value)}
                      onKeyDown={e=>{ if(e.key==="Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }}}
                      placeholder="Ask or instruct… e.g. Create campaign, Import CSV, @skill-name do X"
                      className="w-full pl-3 sm:pl-4 pr-10 sm:pr-12 py-3 sm:py-3.5 rounded-full border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 focus:bg-white dark:focus:bg-gray-900 focus:outline-none focus:ring-2 focus:ring-primary-200 dark:focus:ring-primary-800 focus:border-primary-400 text-sm placeholder:text-gray-400"
                    />
                    {input.length>0 && <span className="absolute right-[52px] top-1/2 -translate-y-1/2 hidden sm:block text-[10px] text-gray-400">Enter ↵</span>}
                    <button onClick={sendMessage} disabled={loading || !input.trim()} className="absolute right-1 top-1 bottom-1 w-9 sm:w-10 rounded-full bg-primary-900 dark:bg-primary-800 text-white flex items-center justify-center hover:bg-primary-800 dark:hover:bg-primary-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
                      {loading ? <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin"/> : <Send size={16} className="translate-x-[-1px]" />}
                    </button>
                  </div>
                </div>
                <p className="text-[11px] text-gray-400 dark:text-gray-500 mt-2 text-center hidden sm:block">The agent has full app access — it can create campaigns, import/delete contacts, send messages, and more. Use <code className="px-1 py-0.5 rounded bg-gray-100 dark:bg-gray-800 font-mono">@skill</code> to invoke a skill. Press Enter to send, Shift+Enter for new line.</p>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
