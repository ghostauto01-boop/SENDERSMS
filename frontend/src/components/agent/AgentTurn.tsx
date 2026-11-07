import { useState } from "react";
import {
  AlertTriangle, Check, CheckCircle2, ChevronDown, ChevronRight,
  Loader2, Play, Wrench, X, XCircle,
} from "lucide-react";
import type { ConfirmationCard } from "../../api/aiAgent";

/**
 * The pieces of one agent turn.
 *
 * The operator has to be able to tell, at a glance, four different things
 * apart while the answer streams in:
 *
 *   • what the agent *said*        — markdown, plain
 *   • what it *did*                — collapsible tool steps with timings
 *   • what it *wants to do*        — an approval card, never a done deal
 *   • what *went wrong*            — the provider's own words plus a hint
 *
 * Everything here is presentational; the page owns the state.
 */

const STATUS_STYLES: Record<string, { icon: any; tone: string; label: string }> = {
  running: { icon: Loader2, tone: "text-primary-600 dark:text-primary-300", label: "Working" },
  done: { icon: CheckCircle2, tone: "text-success-600 dark:text-success-400", label: "Done" },
  failed: { icon: XCircle, tone: "text-danger-600 dark:text-danger-400", label: "Failed" },
  cancelled: { icon: X, tone: "text-gray-400", label: "Cancelled" },
  pending: { icon: AlertTriangle, tone: "text-warning-600 dark:text-warning-400", label: "Waiting" },
};

export function ToolStep({
  tool,
  label,
  status,
  durationMs,
  data,
}: {
  tool: string;
  label: string;
  status: string;
  durationMs?: number;
  data?: any;
}) {
  const [open, setOpen] = useState(false);
  const style = STATUS_STYLES[status] || STATUS_STYLES.running;
  const Icon = style.icon;
  const hasDetail = data !== undefined && data !== null;

  return (
    <div className="rounded-xl border border-gray-200 bg-gray-50/70 px-2.5 py-1.5 text-[12.5px] dark:border-gray-700 dark:bg-gray-800/40">
      <button
        type="button"
        onClick={() => hasDetail && setOpen((v) => !v)}
        className="flex w-full items-center gap-2 text-left"
        aria-expanded={open}
      >
        <Icon size={13} className={`shrink-0 ${style.tone} ${status === "running" ? "animate-spin" : ""}`} />
        <span className="min-w-0 flex-1 truncate text-gray-700 dark:text-gray-200">{label}</span>
        <span className="shrink-0 font-mono text-[10.5px] text-gray-400">
          {typeof durationMs === "number" ? `${Math.max(durationMs, 1)}ms` : ""}
        </span>
        {hasDetail && (open ? <ChevronDown size={13} /> : <ChevronRight size={13} />)}
      </button>
      <p className="mt-0.5 flex items-center gap-1 pl-5 font-mono text-[10.5px] text-gray-400">
        <Wrench size={9} /> {tool}
      </p>
      {open && hasDetail && (
        <pre className="mt-1.5 max-h-56 overflow-auto rounded-lg bg-white p-2 font-mono text-[11px] leading-relaxed text-gray-600 dark:bg-gray-900 dark:text-gray-300">
          {typeof data === "string" ? data : JSON.stringify(data, null, 2)}
        </pre>
      )}
    </div>
  );
}

export function ConfirmationPrompt({
  card,
  onApprove,
  onCancel,
  busy,
}: {
  card: ConfirmationCard;
  onApprove: () => void;
  onCancel: () => void;
  busy?: "approve" | "cancel" | null;
}) {
  const { details = {} } = card;
  const preview = details.message_preview as string | undefined;
  const channel = (details.channel as string | undefined) || "";
  const audience = details.audience as { sendable?: number; skipped?: Record<string, number> } | undefined;

  return (
    <div className="overflow-hidden rounded-2xl border border-warning-300 bg-warning-50/70 dark:border-warning-800 dark:bg-warning-950/30">
      <div className="flex items-start gap-2.5 px-3.5 py-3">
        <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-warning-100 text-warning-700 dark:bg-warning-900/60 dark:text-warning-300">
          <AlertTriangle size={15} />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-[13.5px] font-semibold text-warning-950 dark:text-warning-100">{card.title}</p>
          <p className="mt-0.5 text-[13px] leading-relaxed text-warning-900/90 dark:text-warning-100/80">
            {card.summary}
          </p>

          {(preview || audience || channel) && (
            <dl className="mt-2.5 space-y-1.5 rounded-xl bg-white/70 px-3 py-2 text-[12.5px] dark:bg-gray-900/40">
              {channel && (
                <div className="flex gap-2">
                  <dt className="w-20 shrink-0 text-gray-500 dark:text-gray-400">Channel</dt>
                  <dd className="font-medium uppercase tracking-wide">{channel}</dd>
                </div>
              )}
              {audience && typeof audience.sendable === "number" && (
                <div className="flex gap-2">
                  <dt className="w-20 shrink-0 text-gray-500 dark:text-gray-400">Audience</dt>
                  <dd className="min-w-0 font-medium">
                    {audience.sendable.toLocaleString()} contact{audience.sendable === 1 ? "" : "s"}
                    {audience.skipped && Object.keys(audience.skipped).length > 0 && (
                      <span className="ml-1 font-normal text-gray-500 dark:text-gray-400">
                        ({Object.entries(audience.skipped)
                          .map(([why, n]) => `${n} ${why.replace(/_/g, " ")}`)
                          .join(", ")} skipped)
                      </span>
                    )}
                  </dd>
                </div>
              )}
              {preview && (
                <div className="flex gap-2">
                  <dt className="w-20 shrink-0 text-gray-500 dark:text-gray-400">Message</dt>
                  <dd className="min-w-0">
                    <p className="whitespace-pre-wrap break-anywhere text-gray-800 dark:text-gray-100">
                      {preview}
                    </p>
                    {preview.length >= 280 && (
                      <p className="mt-0.5 text-[11px] text-gray-500">Truncated for review.</p>
                    )}
                  </dd>
                </div>
              )}
            </dl>
          )}

          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={onApprove}
              disabled={busy !== null && busy !== undefined}
              className="btn-primary btn-sm"
            >
              {busy === "approve" ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
              {busy === "approve" ? "Running…" : "Approve and run"}
            </button>
            <button
              type="button"
              onClick={onCancel}
              disabled={busy !== null && busy !== undefined}
              className="btn-secondary btn-sm"
            >
              {busy === "cancel" ? <Loader2 size={13} className="animate-spin" /> : <X size={13} />}
              Cancel
            </button>
            <span className="text-[11px] text-warning-800/70 dark:text-warning-200/60">
              Nothing has been sent or deleted yet.
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

export function Notice({ kind, text }: { kind: string; text: string }) {
  const tone =
    kind === "recovery"
      ? "border-primary-200 bg-primary-50 text-primary-900 dark:border-primary-800 dark:bg-primary-950/40 dark:text-primary-100"
      : kind === "fallback"
        ? "border-warning-200 bg-warning-50 text-warning-900 dark:border-warning-800 dark:bg-warning-950/30 dark:text-warning-100"
        : "border-gray-200 bg-gray-50 text-gray-700 dark:border-gray-700 dark:bg-gray-800/60 dark:text-gray-200";
  return (
    <div className={`flex items-start gap-2 rounded-xl border px-3 py-2 text-[12.5px] ${tone}`}>
      <Check size={13} className="mt-0.5 shrink-0" />
      <span className="min-w-0">{text}</span>
    </div>
  );
}

export function ErrorNote({
  message,
  hint,
  code,
  retryable,
  onRetry,
}: {
  message: string;
  hint?: string;
  code?: string;
  retryable?: boolean;
  onRetry?: () => void;
}) {
  return (
    <div className="rounded-2xl border border-danger-200 bg-danger-50 px-3.5 py-3 text-[13px] dark:border-danger-900 dark:bg-danger-950/40">
      <p className="flex items-start gap-2 font-medium text-danger-800 dark:text-danger-200">
        <XCircle size={14} className="mt-0.5 shrink-0" />
        <span className="min-w-0">{message}</span>
      </p>
      {hint && <p className="mt-1 pl-6 text-danger-700/90 dark:text-danger-200/80">{hint}</p>}
      <div className="mt-2 flex items-center gap-3 pl-6">
        {code && <span className="font-mono text-[10.5px] uppercase text-danger-500">{code}</span>}
        {retryable && onRetry && (
          <button type="button" onClick={onRetry} className="text-[12px] font-semibold text-danger-700 underline dark:text-danger-300">
            Try again
          </button>
        )}
      </div>
    </div>
  );
}

export function ThinkingBlock({ text, streaming }: { text: string; streaming?: boolean }) {
  const [open, setOpen] = useState(Boolean(streaming));
  return (
    <div className="rounded-xl border border-gray-200/80 bg-gray-50/70 px-2.5 py-1.5 text-[12px] dark:border-gray-700/80 dark:bg-gray-800/40">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1.5 text-left font-medium text-gray-600 dark:text-gray-300"
      >
        {streaming ? <Loader2 size={12} className="animate-spin text-primary-600" /> : <Check size={12} className="text-success-600" />}
        <span>{streaming ? "Reasoning live…" : "Reasoning trace"}</span>
        <span className="ml-auto text-gray-400">
          {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        </span>
      </button>
      {open && (
        <p className="mt-1.5 whitespace-pre-wrap border-l-2 border-primary-300 pl-2.5 text-[11.5px] leading-relaxed text-gray-500 dark:border-primary-700 dark:text-gray-400">
          {text}
        </p>
      )}
    </div>
  );
}

export function StepProgress({
  step,
  max,
  statusText,
  routeLabel,
}: {
  step: number;
  max: number;
  statusText?: string;
  routeLabel?: string;
}) {
  return (
    <div className="inline-flex max-w-full items-center gap-2 rounded-full border border-primary-200/70 bg-primary-50/80 px-3 py-1 text-[11.5px] font-medium text-primary-800 dark:border-primary-800/60 dark:bg-primary-950/50 dark:text-primary-200">
      <Loader2 size={11} className="shrink-0 animate-spin text-primary-600 dark:text-primary-400" />
      <span className="truncate">
        {statusText || (step > 0 ? `Working · Step ${step} of ${max}` : "Connecting to OpenRouter…")}
      </span>
      {routeLabel ? (
        <span className="hidden shrink-0 rounded bg-white/80 px-1.5 py-0.5 font-mono text-[10px] text-gray-600 sm:inline dark:bg-gray-900/70 dark:text-gray-300">
          {routeLabel}
        </span>
      ) : null}
    </div>
  );
}
