import { X } from "lucide-react";
import React from "react";

/** Shared primitives for the SMS Ads Manager. Mobile-first by default. */

export const STATUS_BADGE: Record<string, string> = {
  active: "badge-green",
  sending: "badge-green",
  running: "badge-green",
  paused: "badge-yellow",
  daily_limit_reached: "badge-yellow",
  outside_sending_window: "badge-yellow",
  waiting_for_schedule: "badge-blue",
  scheduled: "badge-blue",
  completed: "badge-blue",
  draft: "badge-gray",
  archived: "badge-gray",
  no_eligible_contacts: "badge-gray",
  error: "badge-red",
  needs_attention: "badge-red",
  insufficient_balance: "badge-red",
};

export const STATE_LABEL: Record<string, string> = {
  active: "Active",
  sending: "Sending",
  paused: "Paused",
  draft: "Draft",
  scheduled: "Scheduled",
  completed: "Completed",
  archived: "Archived",
  error: "Error",
  daily_limit_reached: "Daily limit reached",
  outside_sending_window: "Outside sending window",
  waiting_for_schedule: "Waiting for schedule",
  no_eligible_contacts: "No eligible contacts",
  insufficient_balance: "Insufficient SMS balance",
  preparing_audience: "Preparing audience",
  needs_attention: "Needs attention",
};

export function Badge({ value }: { value?: string | null }) {
  if (!value) return null;
  return <span className={STATUS_BADGE[value] || "badge-gray"}>{STATE_LABEL[value] || value}</span>;
}

export function Stat({
  label,
  value,
  hint,
  icon,
}: {
  label: string;
  value: React.ReactNode;
  hint?: string;
  icon?: React.ReactNode;
}) {
  return (
    <div className="card p-4">
      {icon && <div className="text-primary-600 mb-2">{icon}</div>}
      <div className="text-xl font-bold break-words">{value}</div>
      <div className="text-xs text-gray-500 mt-1">{label}</div>
      {hint && <div className="text-[11px] text-gray-400 mt-1">{hint}</div>}
    </div>
  );
}

export function Metric({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <div className="text-xs text-gray-500">{label}</div>
      <div className="font-semibold mt-1">{value}</div>
    </div>
  );
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      {children}
      {hint && <span className="block text-xs text-gray-500 mt-1">{hint}</span>}
    </label>
  );
}

export function Modal({
  title,
  close,
  children,
  wide,
}: {
  title: string;
  close: () => void;
  children: React.ReactNode;
  wide?: boolean;
}) {
  return (
    <div className="fixed inset-0 z-50 bg-black/50 flex items-end sm:items-center justify-center sm:p-4">
      <div
        className={`card w-full ${
          wide ? "sm:max-w-3xl" : "sm:max-w-xl"
        } max-h-[94vh] overflow-y-auto rounded-t-2xl sm:rounded-xl p-5`}
      >
        <div className="flex justify-between items-center mb-4 sticky top-0 bg-white dark:bg-gray-800 z-10">
          <h2 className="text-lg sm:text-xl font-semibold">{title}</h2>
          <button className="btn-ghost btn-sm" onClick={close} aria-label="Close">
            <X size={18} />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function Empty({
  title,
  body,
  action,
  icon,
}: {
  title: string;
  body?: string;
  action?: React.ReactNode;
  icon?: React.ReactNode;
}) {
  return (
    <div className="card p-10 text-center">
      {icon && <div className="flex justify-center text-gray-300 mb-3">{icon}</div>}
      <h3 className="font-semibold">{title}</h3>
      {body && <p className="text-sm text-gray-500 mt-1 mb-4 max-w-md mx-auto">{body}</p>}
      {action}
    </div>
  );
}

export function Tabs({
  tabs,
  active,
  onChange,
}: {
  tabs: string[];
  active: string;
  onChange: (t: string) => void;
}) {
  return (
    <div className="flex gap-1 overflow-x-auto scrollbar-none border-b border-gray-200 dark:border-gray-700">
      {tabs.map((t) => (
        <button
          key={t}
          onClick={() => onChange(t)}
          className={`px-3 py-3 text-sm whitespace-nowrap border-b-2 ${
            active === t
              ? "border-primary-600 text-primary-600 font-medium"
              : "border-transparent text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
          }`}
        >
          {t}
        </button>
      ))}
    </div>
  );
}

/** Horizontal bar used for split / performance comparisons. */
export function Bar({ value, max, tone = "primary" }: { value: number; max: number; tone?: string }) {
  const pct = max > 0 ? Math.min((value / max) * 100, 100) : 0;
  const colour =
    tone === "danger" ? "bg-red-500" : tone === "success" ? "bg-green-500" : "bg-primary-600";
  return (
    <div className="h-2 rounded-full bg-gray-100 dark:bg-gray-700 overflow-hidden">
      <div className={`h-full ${colour}`} style={{ width: `${pct}%` }} />
    </div>
  );
}

export function fmtDate(value?: string | null) {
  if (!value) return "—";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
}

export function fmtDay(value?: string | null) {
  if (!value) return "—";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleDateString();
}

export function toLocalInput(value?: string | null) {
  if (!value) return "";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(
    d.getMinutes()
  )}`;
}

export function fromLocalInput(value: string) {
  return value ? new Date(value).toISOString() : null;
}
