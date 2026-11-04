import { useEffect, useRef, useState } from "react";
import {
  Bot, Check, ChevronDown, Copy, RefreshCw, Sparkles, ThumbsUp, User as UserIcon,
} from "lucide-react";
import Markdown from "../Markdown";
import type { ConfirmationCard } from "../../api/aiAgent";
import {
  ConfirmationPrompt, ErrorNote, Notice, StepProgress, ToolStep,
} from "./AgentTurn";

/**
 * One conversation, top to bottom.
 *
 * The transcript is built from two sources that have to look like one thing:
 * the messages the server stored, and the live events of the turn in progress.
 * Stored messages carry their tool calls as JSON; live events arrive one at a
 * time. Both are normalised into the same `Turn` shape here so a reloaded chat
 * is indistinguishable from a streamed one.
 */

export type TurnBlock =
  | { kind: "text"; text: string }
  | { kind: "notice"; noticeKind: string; text: string }
  | { kind: "tool"; tool: string; label: string; status: string; durationMs?: number; data?: any }
  | { kind: "confirmation"; card: ConfirmationCard }
  | { kind: "error"; message: string; hint?: string; code?: string; retryable?: boolean };

export type Turn = {
  id: string;
  role: "user" | "assistant";
  blocks: TurnBlock[];
  createdAt?: string;
  streaming?: boolean;
  step?: { current: number; max: number } | null;
};

export function useAutoScroll(dependency: unknown) {
  const ref = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);

  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const onScroll = () => {
      // Only follow new content when the operator is already at the bottom —
      // scrolling up to read must never be yanked back down by a stream.
      pinned.current = node.scrollHeight - node.scrollTop - node.clientHeight < 120;
    };
    node.addEventListener("scroll", onScroll, { passive: true });
    return () => node.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    const node = ref.current;
    if (node && pinned.current) node.scrollTop = node.scrollHeight;
  }, [dependency]);

  return ref;
}

const timeOf = (iso?: string) => {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
};

function AssistantBubble({
  turn,
  onApprove,
  onCancel,
  busyCard,
}: {
  turn: Turn;
  onApprove?: (actionId: number) => void;
  onCancel?: (actionId: number) => void;
  busyCard?: { id: number; state: "approve" | "cancel" } | null;
}) {
  const [copied, setCopied] = useState(false);
  const plain = turn.blocks.filter((b) => b.kind === "text").map((b: any) => b.text).join("\n\n");

  return (
    <div className="group flex gap-2.5 sm:gap-3">
      <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary-900 text-white dark:bg-primary-800">
        <Sparkles size={13} />
      </div>
      <div className="min-w-0 flex-1 space-y-1.5">
        {turn.blocks.map((block, index) => {
          if (block.kind === "text") {
            return (
              <div key={index} className="text-gray-800 dark:text-gray-100">
                <Markdown text={block.text} streaming={turn.streaming && index === turn.blocks.length - 1} />
              </div>
            );
          }
          if (block.kind === "notice") {
            return <Notice key={index} kind={block.noticeKind} text={block.text} />;
          }
          if (block.kind === "tool") {
            return (
              <ToolStep
                key={index}
                tool={block.tool}
                label={block.label}
                status={block.status}
                durationMs={block.durationMs}
                data={block.data}
              />
            );
          }
          if (block.kind === "confirmation") {
            return (
              <ConfirmationPrompt
                key={index}
                card={block.card}
                onApprove={() => onApprove?.(block.card.action_id)}
                onCancel={() => onCancel?.(block.card.action_id)}
                busy={busyCard?.id === block.card.action_id ? busyCard.state : null}
              />
            );
          }
          return (
            <ErrorNote
              key={index}
              message={block.message}
              hint={block.hint}
              code={block.code}
              retryable={block.retryable}
            />
          );
        })}

        {turn.streaming && turn.step && (
          <StepProgress step={turn.step.current} max={turn.step.max} />
        )}

        {!turn.streaming && plain && (
          <div className="flex items-center gap-2 pt-0.5 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
            <button
              type="button"
              onClick={async () => {
                try {
                  await navigator.clipboard.writeText(plain);
                  setCopied(true);
                  setTimeout(() => setCopied(false), 1200);
                } catch {
                  /* clipboard blocked */
                }
              }}
              className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10.5px] font-medium text-gray-400 hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-gray-800"
            >
              {copied ? <Check size={10} /> : <Copy size={10} />} {copied ? "Copied" : "Copy"}
            </button>
            <span className="text-[10.5px] text-gray-300 dark:text-gray-600">{timeOf(turn.createdAt)}</span>
          </div>
        )}
      </div>
    </div>
  );
}

function UserBubble({ turn }: { turn: Turn }) {
  const text = turn.blocks.map((b: any) => b.text || "").join("\n");
  return (
    <div className="flex justify-end gap-2.5 sm:gap-3">
      <div className="min-w-0 max-w-[85%] sm:max-w-[75%]">
        <div className="whitespace-pre-wrap break-anywhere rounded-2xl rounded-br-md bg-primary-900 px-3.5 py-2.5 text-[14px] leading-relaxed text-white shadow-sm dark:bg-primary-800">
          {text}
        </div>
        <p className="mt-0.5 text-right text-[10.5px] text-gray-300 dark:text-gray-600">
          {timeOf(turn.createdAt)}
        </p>
      </div>
      <div className="mt-0.5 hidden h-7 w-7 shrink-0 items-center justify-center rounded-full bg-gray-200 text-gray-600 sm:flex dark:bg-gray-700 dark:text-gray-200">
        <UserIcon size={13} />
      </div>
    </div>
  );
}

export default function AgentThread({
  turns,
  onApprove,
  onCancel,
  busyCard,
  error,
  onRetry,
  suggestions,
  onSuggestion,
}: {
  turns: Turn[];
  onApprove?: (actionId: number) => void;
  onCancel?: (actionId: number) => void;
  busyCard?: { id: number; state: "approve" | "cancel" } | null;
  error?: { message: string; hint?: string; retryable?: boolean } | null;
  onRetry?: () => void;
  suggestions?: string[];
  onSuggestion?: (text: string) => void;
}) {
  const scrollRef = useAutoScroll(turns);

  return (
    <div ref={scrollRef} className="chat-scroll flex-1 overflow-y-auto px-3 py-4 sm:px-5">
      <div className="mx-auto max-w-3xl space-y-5">
        {turns.length === 0 && (
          <div className="py-6 text-center">
            <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-primary-900 text-white shadow-lg shadow-primary-950/20 dark:bg-primary-800">
              <Sparkles size={24} />
            </div>
            <h2 className="mt-3 text-lg font-semibold">What should I do?</h2>
            <p className="mx-auto mt-1 max-w-md text-[13px] leading-relaxed text-gray-500 dark:text-gray-400">
              I can create campaigns, import a contact list, reply in the inbox, write
              templates, send invoices and read your numbers. Anything that sends or
              deletes asks you first.
            </p>
            {suggestions && suggestions.length > 0 && (
              <div className="mx-auto mt-4 grid max-w-lg gap-1.5 sm:grid-cols-2">
                {suggestions.map((suggestion) => (
                  <button
                    key={suggestion}
                    type="button"
                    onClick={() => onSuggestion?.(suggestion)}
                    className="rounded-xl border border-gray-200 bg-white px-3 py-2 text-left text-[12.5px] text-gray-700 transition-colors hover:border-primary-300 hover:bg-primary-50/50 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200 dark:hover:border-primary-800"
                  >
                    {suggestion}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {turns.map((turn) =>
          turn.role === "user" ? (
            <UserBubble key={turn.id} turn={turn} />
          ) : (
            <AssistantBubble
              key={turn.id}
              turn={turn}
              onApprove={onApprove}
              onCancel={onCancel}
              busyCard={busyCard}
            />
          ),
        )}

        {error && (
          <div className="flex gap-2.5 sm:gap-3">
            <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-danger-100 text-danger-700 dark:bg-danger-950 dark:text-danger-300">
              <Bot size={13} />
            </div>
            <div className="min-w-0 flex-1">
              <ErrorNote
                message={error.message}
                hint={error.hint}
                retryable={error.retryable}
                onRetry={onRetry}
              />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/** A compact activity list — what the agent did, in reverse order. */
export function ActivityList({
  actions,
  onUndo,
  limit = 6,
}: {
  actions: { id: number; tool: string; summary?: string; status: string; undoable?: boolean; created_at?: string }[];
  onUndo?: (id: number) => void;
  limit?: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const shown = expanded ? actions : actions.slice(0, limit);
  if (!actions.length) {
    return (
      <p className="px-1 py-2 text-[12px] text-gray-400">
        Nothing yet. Every action the agent takes is recorded here — including the
        ones you cancelled.
      </p>
    );
  }
  return (
    <div className="space-y-1">
      {shown.map((action) => (
        <div
          key={action.id}
          className="flex items-start gap-2 rounded-lg border border-gray-100 px-2.5 py-1.5 text-[12px] dark:border-gray-800"
        >
          <span
            className={`mt-1 h-1.5 w-1.5 shrink-0 rounded-full ${
              action.status === "done"
                ? "bg-success-500"
                : action.status === "failed"
                  ? "bg-danger-500"
                  : action.status === "pending"
                    ? "bg-warning-500"
                    : "bg-gray-300"
            }`}
          />
          <div className="min-w-0 flex-1">
            <p className="truncate text-gray-700 dark:text-gray-200">{action.summary || action.tool}</p>
            <p className="font-mono text-[10px] text-gray-400">
              {action.tool} · {action.status}
              {action.created_at ? ` · ${timeOf(action.created_at)}` : ""}
            </p>
          </div>
          {action.undoable && onUndo && (
            <button
              type="button"
              onClick={() => onUndo(action.id)}
              className="shrink-0 rounded-md px-1.5 py-0.5 text-[10.5px] font-semibold text-primary-700 hover:bg-primary-50 dark:text-primary-300 dark:hover:bg-primary-950/40"
            >
              Undo
            </button>
          )}
        </div>
      ))}
      {actions.length > limit && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="flex w-full items-center justify-center gap-1 rounded-lg py-1 text-[11px] font-medium text-gray-500 hover:bg-gray-50 dark:hover:bg-gray-800"
        >
          <ChevronDown size={11} className={expanded ? "rotate-180" : ""} />
          {expanded ? "Show less" : `${actions.length - limit} more`}
        </button>
      )}
    </div>
  );
}

export function TypingDots() {
  return (
    <span className="inline-flex items-center gap-1">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="h-1.5 w-1.5 animate-bounce rounded-full bg-gray-400"
          style={{ animationDelay: `${i * 120}ms` }}
        />
      ))}
    </span>
  );
}
