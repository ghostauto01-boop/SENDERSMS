import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight, Bot, CheckCircle2, KeyRound, Loader2, Send, Sparkles,
  TriangleAlert,
} from "lucide-react";
import { agentApi, type AgentHealth, type AgentSettings } from "../../api/aiAgent";

/**
 * THE AGENT, ON THE FRONT PAGE
 *
 * The overview is where an operator starts their day, so the agent starts there
 * too. This card is a one-line command bar: type what you want, press Enter, and
 * the agent page opens with the sentence already sent.
 *
 * It also carries the honest status the old app never showed — is a key set, is
 * the model reachable, how many tools are live — because a green dot that lies
 * is worse than no dot at all.
 */

const EXAMPLES = [
  "Which campaign is doing best this week?",
  "Add 200 restaurants from a CSV I'll upload",
  "Reply to everyone who asked about pricing",
  "Write a follow-up for people who didn't answer",
];

export default function AgentQuickAsk() {
  const [settings, setSettings] = useState<AgentSettings | null>(null);
  const [health, setHealth] = useState<AgentHealth | null>(null);
  const [loading, setLoading] = useState(true);
  const [text, setText] = useState("");
  const [example, setExample] = useState(0);

  const load = useCallback(async () => {
    try {
      const [s, h] = await Promise.all([
        agentApi.settings().catch(() => null),
        agentApi.health().catch(() => null),
      ]);
      setSettings(s);
      setHealth(h);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const timer = window.setInterval(() => setExample((i) => (i + 1) % EXAMPLES.length), 4200);
    return () => window.clearInterval(timer);
  }, []);

  const configured = Boolean(settings?.openrouter_configured);
  const ready = health?.state === "ok";

  const go = (prompt: string) => {
    const value = prompt.trim();
    // The agent page reads ?ask= once and sends it, so the command runs without
    // the operator having to type it a second time.
    window.location.assign(`/ai-agent${value ? `?ask=${encodeURIComponent(value)}` : ""}`);
  };

  if (loading) {
    return <div className="card h-[104px] p-4"><div className="skeleton h-full w-full rounded-2xl" /></div>;
  }

  return (
    <div className="relative overflow-hidden rounded-3xl border border-primary-900/10 bg-gradient-to-br from-primary-900 via-primary-900 to-primary-950 p-4 text-white shadow-card sm:p-5">
      {/* soft light, no imagery — keeps the card calm beside the charts */}
      <div
        aria-hidden
        className="pointer-events-none absolute -right-16 -top-20 h-56 w-56 rounded-full bg-primary-500/20 blur-3xl"
      />
      <div className="relative">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-white/15 backdrop-blur">
              <Sparkles size={15} />
            </span>
            <div>
              <p className="text-[13.5px] font-semibold leading-none">Ask the agent</p>
              <p className="mt-0.5 text-[11.5px] text-white/65">
                {ready
                  ? `${health?.tools ?? 0} tools · ${health?.active_model || "ready"}`
                  : configured
                    ? "Key saved — provider unreachable right now"
                    : "Add a key to switch it on"}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-1.5">
            {configured ? (
              <span
                className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10.5px] font-semibold ${
                  ready ? "bg-success-400/20 text-success-100" : "bg-warning-400/20 text-warning-100"
                }`}
              >
                {ready ? <CheckCircle2 size={10} /> : <TriangleAlert size={10} />}
                {ready ? "Live" : "Check key"}
              </span>
            ) : (
              <Link
                to="/ai-agent"
                className="inline-flex items-center gap-1 rounded-full bg-white/15 px-2 py-0.5 text-[10.5px] font-semibold text-white backdrop-blur hover:bg-white/25"
              >
                <KeyRound size={10} /> Add key
              </Link>
            )}
            <Link
              to="/ai-agent"
              className="inline-flex items-center gap-1 rounded-full bg-white/10 px-2 py-0.5 text-[10.5px] font-medium text-white/85 hover:bg-white/20"
            >
              Open <ArrowRight size={10} />
            </Link>
          </div>
        </div>

        <form
          className="mt-3 flex items-center gap-1.5 rounded-2xl bg-white/10 p-1.5 backdrop-blur focus-within:bg-white/15"
          onSubmit={(e) => {
            e.preventDefault();
            if (!text.trim()) return;
            go(text);
          }}
        >
          <Bot size={15} className="ml-1 shrink-0 text-white/60" />
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={EXAMPLES[example]}
            aria-label="Ask the agent"
            className="min-w-0 flex-1 bg-transparent px-1 py-1.5 text-[13.5px] text-white outline-none placeholder:text-white/55"
          />
          <button
            type="submit"
            disabled={!text.trim()}
            className="flex shrink-0 items-center gap-1 rounded-xl bg-white px-2.5 py-1.5 text-[12px] font-semibold text-primary-950 transition-opacity disabled:opacity-50"
          >
            <Send size={12} /> Ask
          </button>
        </form>

        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {EXAMPLES.slice(0, 2).map((prompt) => (
            <button
              key={prompt}
              type="button"
              onClick={() => go(prompt)}
              className="max-w-full truncate rounded-full bg-white/10 px-2 py-1 text-[10.5px] text-white/80 hover:bg-white/20"
            >
              {prompt}
            </button>
          ))}
          {!configured && (
            <span className="ml-auto flex items-center gap-1 text-[10.5px] text-white/60">
              <Loader2 size={9} className="hidden" /> Nothing sends without your approval.
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
