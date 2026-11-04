import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import toast from "react-hot-toast";
import {
  Activity, AlertTriangle, Brain, ChevronDown, ChevronRight, Cpu, History,
  Menu, MessageSquarePlus, MoreHorizontal, Paperclip, Pencil, Plug, Settings2,
  Sparkles, Trash2, WifiOff, X,
} from "lucide-react";
import {
  agentApi,
  streamChat,
  type AgentFile,
  type AgentHealth,
  type AgentSettings,
  type ConfirmationCard,
  type Provider,
  type StreamEvent,
  type AgentSession,
  type Skill,
} from "../api/aiAgent";
import AgentThread, { ActivityList, type Turn, type TurnBlock } from "../components/agent/AgentThread";
import AgentComposer from "../components/agent/AgentComposer";
import AgentFilesPanel from "../components/agent/AgentFilesPanel";
import AgentModelSettings from "../components/agent/AgentModelSettings";
import { ProviderStatusPill } from "../components/agent/AgentModelSettings";

/**
 * THE AGENT
 *
 * One screen where the workspace can be operated in sentences. Layout follows
 * what the operator is doing, not what the code finds convenient:
 *
 *   left   — sessions (drawer on a phone, a rail on a laptop)
 *   centre — the conversation, with files and approvals inline
 *   right  — what the agent did, and its memory, on demand
 *
 * Three rules hold this page together:
 *
 *   1. Nothing destructive happens without a card. Approvals appear inside the
 *      conversation, where the operator is already reading.
 *   2. Every failure is visible and explained. A missing key, a dead model or a
 *      provider outage becomes a sentence with a fix — never a spinner that
 *      stops, and never a toast that vanishes.
 *   3. The transcript survives a reload. Messages, pending approvals and files
 *      all come back from the server.
 */

const SUGGESTIONS = [
  "How many contacts do I have, and how many opted out?",
  "Import a CSV — I'll upload it next",
  "Write an SMS for restaurants in Abuja",
  "Which campaign got the most replies this month?",
  "Reply to everyone who asked about pricing",
  "Draft an invoice for a ₦120,000 monthly retainer",
];

const starterBlocks = (text: string): TurnBlock[] => [{ kind: "text", text }];

let placeholderId = 0;
const nextId = (prefix: string) => `${prefix}-${Date.now()}-${(placeholderId += 1)}`;

export default function AIAgentPage() {
  const navigate = useNavigate();
  const [sessions, setSessions] = useState<AgentSession[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [files, setFiles] = useState<AgentFile[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [activity, setActivity] = useState<any[]>([]);
  const [memory, setMemory] = useState("");
  const [editingMemory, setEditingMemory] = useState(false);
  const [settings, setSettings] = useState<AgentSettings | null>(null);
  const [health, setHealth] = useState<AgentHealth | null>(null);
  const [loadingSession, setLoadingSession] = useState(true);
  const [loadingList, setLoadingList] = useState(true);
  const [sending, setSending] = useState(false);
  const [streamError, setStreamError] = useState<{ message: string; hint?: string; retryable?: boolean } | null>(null);
  const [busyCard, setBusyCard] = useState<{ id: number; state: "approve" | "cancel" } | null>(null);
  const [attached, setAttached] = useState<AgentFile | null>(null);

  const [drawer, setDrawer] = useState(false);
  const [panel, setPanel] = useState<"files" | "settings" | "activity" | "memory" | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [draftName, setDraftName] = useState("");
  const [lastMessage, setLastMessage] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  // A prompt handed over from the Overview ("Ask the agent") — sent once.
  const askRef = useRef<string | null>(
    typeof window !== "undefined"
      ? new URLSearchParams(window.location.search).get("ask")
      : null,
  );
  const streamBuffer = useRef<{ text: string; blocks: TurnBlock[] }>({ text: "", blocks: [] });

  const active = sessions.find((s) => s.id === activeId) || null;
  const configured = Boolean(settings?.groq_configured || settings?.gemini_configured);

  // ---------------------------------------------------------------------
  // load
  // ---------------------------------------------------------------------
  const loadShell = useCallback(async () => {
    setLoadingList(true);
    try {
      const [list, s, h, sk] = await Promise.all([
        agentApi.sessions(),
        agentApi.settings().catch(() => null),
        agentApi.health().catch(() => null),
        agentApi.skills().catch(() => [] as Skill[]),
      ]);
      setSessions(list);
      setSettings(s);
      setHealth(h);
      setSkills(sk);
      if (list.length && !activeId) setActiveId(list[0].id);
      else if (!list.length && !activeId) {
        try {
          const session = await agentApi.createSession({ name: "New chat" });
          setSessions([session]);
          setActiveId(session.id);
        } catch {
          setLoadingSession(false);
          toast.error("Could not start a chat. Use New to try again.");
        }
      }
    } catch {
      setLoadingSession(false);
      toast.error("Could not load conversations. Refresh the page to retry.");
    } finally {
      setLoadingList(false);
    }
  }, [activeId]);

  useEffect(() => {
    void loadShell();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadSession = useCallback(async (id: string) => {
    setLoadingSession(true);
    setStreamError(null);
    try {
      const data = await agentApi.session(id);
      setTurns(messagesToTurns(data.messages, data.pending_actions));
      setFiles(data.files);
      agentApi.memory("global").then(setMemory).catch(() => setMemory(""));
      setAttached(null);
      agentApi.activity(id).then(setActivity).catch(() => setActivity([]));
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || "Could not open that conversation.");
      setTurns([]);
    } finally {
      setLoadingSession(false);
    }
  }, []);

  useEffect(() => {
    if (activeId) void loadSession(activeId);
    else if (!loadingList) {
      setTurns([]);
      setFiles([]);
      setAttached(null);
      setLoadingSession(false);
    }
  }, [activeId, loadSession]);

  // Send the handoff prompt exactly once, after the conversation it belongs to
  // has actually been loaded — otherwise its first turn would render into an
  // empty transcript and then be overwritten by the load.
  useEffect(() => {
    if (loadingSession) return;
    const pending = askRef.current;
    if (!pending || loadingList) return;
    if (!activeId) {
      // No conversation yet: make one, which re-enters this effect.
      askRef.current = pending;
      void createSession();
      return;
    }
    askRef.current = null;
    if (window.history.replaceState) {
      window.history.replaceState({}, "", "/ai-agent");
    }
    if (configured) void send(pending);
    else setStreamError({
      message: "Add a key first — then this runs.",
      hint: "Groq and Gemini are the only providers; either key is enough.",
      retryable: false,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadingSession, activeId, configured, loadingList]);

  // ---------------------------------------------------------------------
  // sessions
  // ---------------------------------------------------------------------
  const createSession = async () => {
    try {
      const session = await agentApi.createSession({ name: "New chat" });
      setSessions((list) => [session, ...list]);
      setActiveId(session.id);
      setTurns([]);
      setFiles([]);
      setMemory("");
      setActivity([]);
      setDrawer(false);
    } catch {
      toast.error("Could not start a new chat.");
    }
  };

  const deleteSession = async (id: string) => {
    if (!window.confirm("Delete this conversation and its messages?")) return;
    try {
      await agentApi.deleteSession(id);
      const rest = sessions.filter((s) => s.id !== id);
      setSessions(rest);
      if (activeId === id) setActiveId(rest[0]?.id || null);
      toast.success("Conversation deleted");
    } catch {
      toast.error("Could not delete that conversation.");
    }
  };

  const renameSession = async () => {
    if (!activeId) return;
    const name = draftName.trim();
    if (!name) return setRenaming(false);
    try {
      await agentApi.renameSession(activeId, name);
      setSessions((list) => list.map((s) => (s.id === activeId ? { ...s, name } : s)));
    } catch {
      toast.error("Could not rename that conversation.");
    } finally {
      setRenaming(false);
    }
  };

  // ---------------------------------------------------------------------
  // sending
  // ---------------------------------------------------------------------
  const send = async (text: string) => {
    if (!activeId || sending) return;
    setSending(true);
    setStreamError(null);
    setLastMessage(text);

    const userTurn: Turn = { id: nextId("u"), role: "user", blocks: [{ kind: "text", text }] };
    const assistantId = nextId("a");
    const assistantTurn: Turn = {
      id: assistantId,
      role: "assistant",
      blocks: [],
      streaming: true,
      step: null,
    };
    streamBuffer.current = { text: "", blocks: [] };
    setTurns((current) => [...current, userTurn, assistantTurn]);

    const patchAssistant = (update: (turn: Turn) => Turn) =>
      setTurns((current) => current.map((turn) => (turn.id === assistantId ? update(turn) : turn)));

    const appendBlock = (block: TurnBlock) => {
      streamBuffer.current.blocks = [...streamBuffer.current.blocks, block];
      patchAssistant((turn) => ({ ...turn, blocks: [...turn.blocks, block] }));
    };

    const controller = new AbortController();
    abortRef.current = controller;
    let sawText = false;
    let failed = false;

    try {
      await streamChat(activeId, text, {
        file_ids: attached ? [attached.id] : [],
        signal: controller.signal,
        onEvent: (event: StreamEvent) => {
          switch (event.type) {
            case "turn_start":
              patchAssistant((turn) => ({ ...turn, step: { current: 0, max: 15 } }));
              break;
            case "step":
              patchAssistant((turn) => ({ ...turn, step: { current: event.step, max: event.max } }));
              break;
            case "text": {
              sawText = true;
              streamBuffer.current.text += event.text;
              const buffered = streamBuffer.current.text;
              patchAssistant((turn) => {
                const blocks = [...turn.blocks];
                const last = blocks[blocks.length - 1];
                if (last && last.kind === "text") blocks[blocks.length - 1] = { kind: "text", text: buffered };
                else blocks.push({ kind: "text", text: buffered });
                return { ...turn, blocks };
              });
              break;
            }
            case "notice":
              streamBuffer.current.text = "";
              appendBlock({ kind: "notice", noticeKind: event.kind, text: event.text });
              break;
            case "tool_start":
              appendBlock({
                kind: "tool",
                tool: event.tool,
                label: event.label,
                status: "running",
              });
              streamBuffer.current.text = "";
              break;
            case "tool_end":
              setTurns((current) =>
                current.map((turn) => {
                  if (turn.id !== assistantId) return turn;
                  const blocks = [...turn.blocks];
                  for (let i = blocks.length - 1; i >= 0; i -= 1) {
                    const block = blocks[i];
                    if (block.kind === "tool" && block.tool === event.tool && block.status === "running") {
                      blocks[i] = {
                        ...block,
                        status: event.status,
                        label: event.label || block.label,
                        durationMs: event.duration_ms,
                        data: event.data,
                      };
                      return { ...turn, blocks };
                    }
                  }
                  return turn;
                }),
              );
              streamBuffer.current.text = "";
              break;
            case "confirmation":
              appendBlock({
                kind: "confirmation",
                card: {
                  kind: "confirmation",
                  action_id: event.action_id,
                  tool: event.tool,
                  title: event.title,
                  summary: event.summary,
                  details: event.details,
                },
              });
              break;
            case "error":
              failed = true;
              appendBlock({
                kind: "error",
                message: event.error.message,
                hint: event.error.hint,
                code: event.error.code,
                retryable: event.error.retryable,
              });
              if (!event.error.retryable) {
                setStreamError({ message: event.error.message, hint: event.error.hint, retryable: false });
              }
              break;
            case "done":
              patchAssistant((turn) => ({ ...turn, streaming: false, step: null }));
              break;
            default:
              break;
          }
        },
      });
    } catch (error: any) {
      if (error?.name !== "AbortError") {
        failed = true;
        const message =
          error?.message === "Failed to fetch"
            ? "Could not reach the server. It may be waking up — try again in a moment."
            : error?.message || "Something went wrong while the agent was working.";
        setStreamError({ message, retryable: true });
      }
    } finally {
      abortRef.current = null;
      setSending(false);
      patchAssistant((turn) => ({
        ...turn,
        streaming: false,
        step: null,
        blocks: turn.blocks.length
          ? turn.blocks
          : [{ kind: "text", text: failed ? "" : "…" } as TurnBlock].filter(() => false),
      }));

      // Refresh the peripheral state once the turn is settled.
      if (!controller.signal.aborted) {
        agentApi.sessions().then(setSessions).catch(() => {});
        agentApi.activity(activeId).then(setActivity).catch(() => {});
        agentApi.memory("global").then(setMemory).catch(() => {});
        agentApi.health().then(setHealth).catch(() => {});
        if (!sawText && !failed) {
          // The turn ended with nothing said (e.g. only an approval card): show
          // the card and leave the bubble alone rather than an empty shell.
        }
        if (!failed) setAttached(null);
      }
    }
  };

  const stop = () => {
    abortRef.current?.abort();
    toast("Stopped.", { icon: "⏹" });
  };

  // ---------------------------------------------------------------------
  // approvals
  // ---------------------------------------------------------------------
  const approve = async (actionId: number) => {
    setBusyCard({ id: actionId, state: "approve" });
    try {
      const result = await agentApi.approve(actionId);
      const ok = result?.status === "done";
      if (ok) toast.success(result.summary || "Done");
      else {
        const reason = result?.result?.errors?.[0] || result?.error || result?.result?.error || "It did not run.";
        toast.error(String(reason), { duration: 8000 });
      }
      if (activeId) {
        const data = await agentApi.session(activeId);
        setTurns(messagesToTurns(data.messages, data.pending_actions));
        agentApi.activity(activeId).then(setActivity).catch(() => {});
      }
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || "Could not run that action.");
    } finally {
      setBusyCard(null);
    }
  };

  const cancel = async (actionId: number) => {
    setBusyCard({ id: actionId, state: "cancel" });
    try {
      await agentApi.cancel(actionId);
      setTurns((current) =>
        current.map((turn) => ({
          ...turn,
          blocks: turn.blocks.filter(
            (block) => !(block.kind === "confirmation" && block.card.action_id === actionId),
          ),
        })),
      );
      toast.success("Cancelled — nothing was done.");
    } catch {
      toast.error("Could not cancel that action.");
    } finally {
      setBusyCard(null);
    }
  };

  const undo = async (actionId: number) => {
    try {
      const result = await agentApi.undo(actionId);
      toast.success(`Undone — ${result.restored} restored`);
      if (activeId) agentApi.activity(activeId).then(setActivity).catch(() => {});
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || "That cannot be undone.");
    }
  };

  // ---------------------------------------------------------------------
  // files + memory
  // ---------------------------------------------------------------------
  const onUploaded = (file: AgentFile) => {
    setFiles((current) => [file, ...current]);
    setAttached(file);
  };

  const useFile = (file: AgentFile) => {
    setAttached(file);
    setPanel(null);
    toast.success(`${file.filename} will go with your next message`);
  };

  const saveMemory = async () => {
    try {
      await agentApi.saveMemory("global", memory);
      setEditingMemory(false);
      toast.success("Saved — the agent will remember this in every chat.");
    } catch {
      toast.error("Could not save that note.");
    }
  };

  const toolsByArea = useMemo(() => {
    const map: Record<string, string[]> = {};
    for (const area of health?.tool_areas || []) map[area] = [];
    return map;
  }, [health?.tool_areas]);

  // ---------------------------------------------------------------------
  // render
  // ---------------------------------------------------------------------
  const keyMissing = settings !== null && !configured;

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden bg-white dark:bg-gray-900 lg:border-t lg:border-gray-200 dark:lg:border-gray-800">
      {/* ---------- header ------------------------------------------- */}
      <header className="flex shrink-0 items-center gap-2 border-b border-gray-200 bg-white/95 px-2.5 py-2 backdrop-blur dark:border-gray-800 dark:bg-gray-900/95 sm:px-4">
        <button
          type="button"
          onClick={() => setDrawer((v) => !v)}
          className="rounded-xl p-2 hover:bg-gray-100 dark:hover:bg-gray-800 lg:hidden"
          aria-label="Conversations"
        >
          <Menu size={18} />
        </button>
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-primary-900 text-white dark:bg-primary-800">
          <Sparkles size={16} />
        </div>
        <div className="min-w-0 flex-1">
          {renaming ? (
            <input
              autoFocus
              value={draftName}
              onChange={(e) => setDraftName(e.target.value)}
              onBlur={renameSession}
              onKeyDown={(e) => {
                if (e.key === "Enter") void renameSession();
                if (e.key === "Escape") setRenaming(false);
              }}
              className="w-full rounded-lg border border-gray-200 px-1.5 py-0.5 text-[14px] font-semibold dark:border-gray-700 dark:bg-gray-800"
            />
          ) : (
            <button
              type="button"
              onClick={() => {
                if (!active) return;
                setDraftName(active.name);
                setRenaming(true);
              }}
              className="group flex max-w-full items-center gap-1 text-left"
            >
              <h1 className="truncate text-[14.5px] font-semibold leading-tight">
                {active?.name || "AI Agent"}
              </h1>
              {active && (
                <Pencil size={10} className="shrink-0 text-gray-300 opacity-0 transition-opacity group-hover:opacity-100" />
              )}
            </button>
          )}
          <div className="mt-0.5 flex items-center gap-1.5">
            {health ? (
              <>
                {(["groq", "gemini"] as Provider[]).map((provider) => (
                  <ProviderStatusPill
                    key={provider}
                    provider={provider}
                    check={health.providers.find((c) => c.provider === provider)}
                  />
                ))}
              </>
            ) : (
              <span className="flex items-center gap-1 text-[11px] text-gray-400">
                <WifiOff size={10} /> checking…
              </span>
            )}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            onClick={createSession}
            disabled={sending}
            className="btn-primary btn-sm"
            title="Start a new conversation"
          >
            <MessageSquarePlus size={13} />
            <span className="hidden sm:inline">New</span>
          </button>
          <button
            type="button"
            onClick={() => setPanel(panel === "files" ? null : "files")}
            className={`rounded-xl p-2 transition-colors ${
              panel === "files"
                ? "bg-primary-50 text-primary-800 dark:bg-primary-950/50 dark:text-primary-300"
                : "text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800"
            }`}
            aria-label="Files"
            aria-pressed={panel === "files"}
          >
            <Paperclip size={17} />
          </button>
          <button
            type="button"
            onClick={() => setPanel(panel === "activity" ? null : "activity")}
            className={`rounded-xl p-2 transition-colors ${
              panel === "activity"
                ? "bg-primary-50 text-primary-800 dark:bg-primary-950/50 dark:text-primary-300"
                : "text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800"
            }`}
            aria-label="Activity"
            aria-pressed={panel === "activity"}
          >
            <History size={17} />
          </button>
          <button
            type="button"
            onClick={() => setPanel(panel === "settings" ? null : "settings")}
            className={`rounded-xl p-2 transition-colors ${
              panel === "settings"
                ? "bg-primary-50 text-primary-800 dark:bg-primary-950/50 dark:text-primary-300"
                : "text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800"
            }`}
            aria-label="Agent settings"
            aria-pressed={panel === "settings"}
          >
            <Settings2 size={17} />
          </button>
        </div>
      </header>

      {keyMissing && (
        <div className="flex shrink-0 items-start gap-2 border-b border-warning-200 bg-warning-50 px-3 py-2 text-[12.5px] dark:border-warning-900 dark:bg-warning-950/30">
          <AlertTriangle size={14} className="mt-0.5 shrink-0 text-warning-600" />
          <p className="min-w-0 flex-1 text-warning-900 dark:text-warning-100">
            <b>No key yet.</b> Add a Groq or Gemini key and the agent can run your
            workspace.{" "}
            <button
              type="button"
              onClick={() => setPanel("settings")}
              className="font-semibold underline"
            >
              Add a key
            </button>
          </p>
        </div>
      )}

      {/* ---------- body --------------------------------------------- */}
      <div className="relative flex min-h-0 flex-1">
        {/* sessions rail */}
        {drawer && (
          <button
            type="button"
            aria-label="Close conversations"
            onClick={() => setDrawer(false)}
            className="absolute inset-0 z-20 bg-black/30 lg:hidden"
          />
        )}
        <aside
          className={`absolute inset-y-0 left-0 z-30 flex w-[80%] max-w-[280px] flex-col border-r border-gray-200 bg-white transition-transform duration-200 dark:border-gray-800 dark:bg-gray-900 lg:static lg:z-0 lg:w-[248px] lg:translate-x-0 ${
            drawer ? "translate-x-0 shadow-xl" : "-translate-x-full"
          }`}
        >
          <div className="flex items-center justify-between px-3 py-2">
            <p className="text-[11px] font-semibold uppercase tracking-wider text-gray-400">
              Conversations
            </p>
            <button
              type="button"
              onClick={() => setDrawer(false)}
              className="rounded-lg p-1 text-gray-400 hover:bg-gray-100 lg:hidden dark:hover:bg-gray-800"
              aria-label="Close"
            >
              <X size={14} />
            </button>
          </div>
          <div className="flex-1 space-y-0.5 overflow-y-auto px-1.5 pb-3">
            {loadingList &&
              [0, 1, 2].map((i) => <div key={i} className="skeleton mx-1 h-12 rounded-xl" />)}
            {!loadingList && sessions.length === 0 && (
              <div className="px-2 py-6 text-center">
                <Brain size={22} className="mx-auto mb-1.5 text-gray-300 dark:text-gray-600" />
                <p className="text-[12px] text-gray-500">No conversations yet.</p>
              </div>
            )}
            {sessions.map((session) => {
              const isActive = session.id === activeId;
              const Icon = session.model === "gemini" ? Brain : Cpu;
              return (
                <div
                  key={session.id}
                  className={`group flex items-start gap-2 rounded-xl px-2 py-2 transition-colors ${
                    isActive
                      ? "bg-primary-50 dark:bg-primary-950/40"
                      : "hover:bg-gray-50 dark:hover:bg-gray-800/60"
                  }`}
                >
                  <button
                    type="button"
                    onClick={() => {
                      if (sending) return;
                      setActiveId(session.id);
                      setDrawer(false);
                    }}
                    className="flex min-w-0 flex-1 items-start gap-2 text-left"
                  >
                    <Icon
                      size={13}
                      className={`mt-0.5 shrink-0 ${isActive ? "text-primary-700 dark:text-primary-300" : "text-gray-400"}`}
                    />
                    <span className="min-w-0">
                      <span
                        className={`block truncate text-[12.5px] ${
                          isActive ? "font-semibold text-primary-900 dark:text-primary-100" : "font-medium"
                        }`}
                      >
                        {session.name}
                      </span>
                      <span className="block truncate text-[10.5px] text-gray-400">
                        {session.last_message || `${session.message_count || 0} messages`}
                      </span>
                    </span>
                  </button>
                  <button
                    type="button"
                    disabled={sending}
                    onClick={() => deleteSession(session.id)}
                    className="mt-0.5 rounded p-1 text-gray-300 opacity-0 transition-opacity hover:text-danger-600 group-hover:opacity-100"
                    aria-label={`Delete ${session.name}`}
                  >
                    <Trash2 size={11} />
                  </button>
                </div>
              );
            })}
          </div>
          {skills.length > 0 && (
            <div className="border-t border-gray-100 px-3 py-2 dark:border-gray-800">
              <p className="text-[10px] font-semibold uppercase tracking-wider text-gray-400">
                Skills
              </p>
              <div className="mt-1 flex flex-wrap gap-1">
                {skills.slice(0, 6).map((skill) => (
                  <span
                    key={skill.id}
                    className="rounded-full bg-gray-100 px-1.5 py-0.5 text-[10px] font-medium text-gray-600 dark:bg-gray-800 dark:text-gray-300"
                    title={skill.description || skill.name}
                  >
                    {skill.name}
                  </span>
                ))}
              </div>
            </div>
          )}
        </aside>

        {/* conversation */}
        <main className="flex min-w-0 flex-1 flex-col">
          {loadingSession ? (
            <div className="flex-1 space-y-4 p-4">
              <div className="skeleton h-16 w-2/3 rounded-2xl" />
              <div className="ml-auto skeleton h-10 w-1/2 rounded-2xl" />
              <div className="skeleton h-24 w-3/4 rounded-2xl" />
            </div>
          ) : (
            <AgentThread
              turns={turns}
              onApprove={approve}
              onCancel={cancel}
              busyCard={busyCard}
              error={streamError}
              onRetry={() => lastMessage && send(lastMessage)}
              suggestions={SUGGESTIONS}
              onSuggestion={(text) => {
                if (text.includes("upload it next")) setPanel("files");
                else void send(text);
              }}
            />
          )}

          <AgentComposer
            onSend={send}
            busy={sending}
            onStop={stop}
            attached={attached}
            onDetachAttachment={() => setAttached(null)}
            onOpenFiles={() => setPanel(panel === "files" ? null : "files")}
            disabled={!activeId || keyMissing}
          />
        </main>

        {/* right panel */}
        {panel && (
          <aside className="absolute inset-y-0 right-0 z-30 flex w-full max-w-[340px] flex-col border-l border-gray-200 bg-white shadow-xl dark:border-gray-800 dark:bg-gray-900 lg:static lg:z-0 lg:w-[330px] lg:shadow-none">
            {panel === "files" && activeId && (
              <AgentFilesPanel
                sessionId={activeId}
                files={files}
                onUploaded={onUploaded}
                onDeleted={(id) => { setFiles((current) => current.filter((f) => f.id !== id)); setAttached((current) => current?.id === id ? null : current); }}
                onUse={useFile}
                onClose={() => setPanel(null)}
              />
            )}

            {panel === "settings" && (
              <div className="flex h-full flex-col">
                <div className="flex items-center justify-between border-b border-gray-100 px-3 py-2.5 dark:border-gray-800">
                  <h3 className="flex items-center gap-1.5 text-[13px] font-semibold">
                    <Settings2 size={13} /> Agent settings
                  </h3>
                  <button
                    type="button"
                    onClick={() => setPanel(null)}
                    className="rounded-lg p-1 text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"
                    aria-label="Close settings"
                  >
                    <X size={14} />
                  </button>
                </div>
                <div className="flex-1 overflow-y-auto p-2.5">
                  <AgentModelSettings
                    compact
                    onConfigured={() => {
                      agentApi.settings().then(setSettings).catch(() => {});
                      agentApi.health().then(setHealth).catch(() => {});
                    }}
                  />
                  <button
                    type="button"
                    onClick={() => navigate("/settings?tab=ai")}
                    className="btn-secondary btn-sm mt-3 w-full"
                  >
                    <Plug size={12} /> All settings, skills and MCP
                  </button>
                  {health && (
                    <div className="mt-3 rounded-xl border border-gray-200 p-2.5 dark:border-gray-700">
                      <p className="text-[11px] font-semibold uppercase tracking-wide text-gray-400">
                        Can do · {health.tools} tools
                      </p>
                      <div className="mt-1 flex flex-wrap gap-1">
                        {health.tool_areas.map((area) => (
                          <span
                            key={area}
                            className="rounded-full bg-gray-100 px-1.5 py-0.5 text-[10px] font-medium capitalize text-gray-600 dark:bg-gray-800 dark:text-gray-300"
                          >
                            {area}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            )}

            {panel === "activity" && (
              <div className="flex h-full flex-col">
                <div className="flex items-center justify-between border-b border-gray-100 px-3 py-2.5 dark:border-gray-800">
                  <h3 className="flex items-center gap-1.5 text-[13px] font-semibold">
                    <Activity size={13} /> What it did
                  </h3>
                  <button
                    type="button"
                    onClick={() => setPanel(null)}
                    className="rounded-lg p-1 text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"
                    aria-label="Close activity"
                  >
                    <X size={14} />
                  </button>
                </div>
                <div className="flex-1 space-y-3 overflow-y-auto p-2.5">
                  <ActivityList actions={activity} onUndo={undo} limit={40} />

                  <div className="rounded-xl border border-gray-200 p-2.5 dark:border-gray-700">
                    <div className="flex items-center justify-between">
                      <p className="text-[11px] font-semibold uppercase tracking-wide text-gray-400">
                        Memory
                      </p>
                      {editingMemory ? (
                        <button
                          type="button"
                          onClick={saveMemory}
                          className="text-[10.5px] font-semibold text-primary-700 dark:text-primary-300"
                        >
                          Save
                        </button>
                      ) : (
                        <button
                          type="button"
                          onClick={() => setEditingMemory(true)}
                          className="text-[10.5px] font-semibold text-primary-700 dark:text-primary-300"
                        >
                          Edit
                        </button>
                      )}
                    </div>
                    <p className="mt-0.5 text-[10.5px] text-gray-400">
                      Standing notes the agent reads in every conversation.
                    </p>
                    {editingMemory ? (
                      <textarea
                        value={memory}
                        onChange={(e) => setMemory(e.target.value)}
                        rows={5}
                        className="input mt-1.5 !text-[12px]"
                        placeholder="e.g. We sell to restaurants in Abuja. Prices in Naira. Never promise a discount."
                      />
                    ) : (
                      <p className="mt-1.5 whitespace-pre-wrap text-[12px] text-gray-600 dark:text-gray-300">
                        {memory || <span className="text-gray-400">Nothing saved yet.</span>}
                      </p>
                    )}
                  </div>
                </div>
              </div>
            )}
          </aside>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// stored messages → turns
// ---------------------------------------------------------------------------
function messagesToTurns(
  messages: { id?: number; role: string; content: string; created_at?: string; error?: any }[],
  pending: ConfirmationCard[],
): Turn[] {
  const turns: Turn[] = [];
  let current: Turn | null = null;

  for (const message of messages) {
    if (message.role === "user") {
      current = null;
      turns.push({
        id: `u-${message.id ?? Math.random()}`,
        role: "user",
        blocks: [{ kind: "text", text: message.content }],
        createdAt: message.created_at,
      });
      continue;
    }
    if (message.role === "tool") {
      // Approved/cancelled actions are appended to the transcript server-side.
      const blocks: TurnBlock[] = [];
      try {
        const payload = JSON.parse(message.content);
        if (payload?.approved) {
          const failed = payload?.result?.error || payload?.result?.errors;
          blocks.push({
            kind: "tool",
            tool: payload.tool,
            label: failed
              ? `Approved · ${typeof failed === "string" ? failed : failed[0]}`
              : `Approved · ${payload.tool.replace(/_/g, " ")}`,
            status: failed ? "failed" : "done",
            data: payload.result,
          });
        }
      } catch {
        blocks.push({ kind: "text", text: message.content.slice(0, 400) });
      }
      if (!blocks.length) continue;
      if (!current) {
        current = { id: `a-${message.id ?? Math.random()}`, role: "assistant", blocks: [], createdAt: message.created_at };
        turns.push(current);
      }
      current.blocks = [...current.blocks, ...blocks];
      continue;
    }
    if (message.role === "assistant") {
      if (message.error) {
        const block: TurnBlock = {
          kind: "error",
          message: message.error.message || "The agent could not answer.",
          hint: message.error.hint,
          code: message.error.code,
          retryable: message.error.retryable,
        };
        if (!current) {
          current = { id: `a-${message.id}`, role: "assistant", blocks: [], createdAt: message.created_at };
          turns.push(current);
        }
        current.blocks = [...current.blocks, block];
        continue;
      }
      const text = (message.content || "").trim();
      if (!text) continue;
      if (current && !current.blocks.some((b) => b.kind === "error")) {
        current.blocks = [...current.blocks, { kind: "text", text }];
      } else {
        current = { id: `a-${message.id ?? Math.random()}`, role: "assistant", blocks: [{ kind: "text", text }], createdAt: message.created_at };
        turns.push(current);
      }
    }
  }

  // Whatever is still awaiting the operator goes at the end of the thread.
  for (const card of pending) {
    if (!current || current.role !== "assistant") {
      current = {
        id: `pending-${card.action_id}`,
        role: "assistant",
        blocks: [],
      };
      turns.push(current);
    }
    current.blocks = [
      ...current.blocks,
      { kind: "notice", noticeKind: "info", text: "Waiting for your approval — nothing has run yet." },
      { kind: "confirmation", card },
    ];
  }

  return turns;
}
