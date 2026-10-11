import { Mail, MessageSquare } from "lucide-react";

export type InboxChannel = "sms" | "email";

/**
 * SMS | Email segmented picker for the pages that list conversations from
 * both channels. Page-local on purpose: the choice lives in the page's URL,
 * so a link to "SMS, replies only" stays what it says.
 */
export default function ChannelPicker({
  value,
  onChange,
  counts,
}: {
  value: InboxChannel;
  onChange: (next: InboxChannel) => void;
  counts?: Partial<Record<InboxChannel, number>>;
}) {
  const options: { key: InboxChannel; label: string; icon: typeof Mail }[] = [
    { key: "sms", label: "SMS", icon: MessageSquare },
    { key: "email", label: "Email", icon: Mail },
  ];
  return (
    <div
      role="tablist"
      aria-label="Channel"
      className="inline-flex gap-1 p-1 rounded-xl bg-gray-100 dark:bg-gray-700/70"
    >
      {options.map((opt) => {
        const active = value === opt.key;
        const count = counts?.[opt.key];
        return (
          <button
            key={opt.key}
            role="tab"
            aria-selected={active}
            onClick={() => onChange(opt.key)}
            className={`px-3.5 py-2 rounded-lg text-sm font-medium transition-colors flex items-center gap-1.5 ${
              active
                ? "bg-white dark:bg-gray-800 shadow-sm text-primary-600"
                : "text-gray-600 dark:text-gray-300 hover:text-gray-900 dark:hover:text-white"
            }`}
          >
            <opt.icon size={14} />
            {opt.label}
            {count !== undefined && (
              <span
                className={`text-[11px] px-1.5 rounded-full ${
                  active
                    ? "bg-primary-100 text-primary-700 dark:bg-primary-950/70 dark:text-primary-300"
                    : "bg-gray-200 text-gray-600 dark:bg-gray-600 dark:text-gray-200"
                }`}
              >
                {count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
