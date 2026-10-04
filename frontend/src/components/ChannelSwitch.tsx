import { Mail, MessageSquare } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useChannel } from "../hooks/useChannel";

/**
 * The SMS | Email segmented switch shown in the header (and reused on the
 * channel-aware pages).
 *
 * Choosing a channel also offers the matching "manager" page when the current
 * page has no email equivalent (e.g. leaving the SMS inbox), so the user is
 * never stranded on a screen that only understands one channel.
 */
export default function ChannelSwitch({
  size = "md",
  onChanged,
  navigateManagers = false,
}: {
  size?: "sm" | "md";
  onChanged?: (channel: "sms" | "email") => void;
  navigateManagers?: boolean;
}) {
  const { channel, setChannel } = useChannel();
  const navigate = useNavigate();

  const pad = size === "sm" ? "px-2.5 py-1 text-xs" : "px-3 py-1.5 text-sm";

  const choose = (next: "sms" | "email") => {
    if (next === channel) return;
    setChannel(next);
    onChanged?.(next);
    if (navigateManagers) {
      navigate(next === "email" ? "/email-manager" : "/sms-manager");
    }
  };

  const base = `flex items-center gap-1.5 rounded-md font-medium transition-colors ${pad}`;

  return (
    <div
      className="inline-flex items-center rounded-lg bg-gray-100 dark:bg-gray-800 p-0.5"
      role="tablist"
      aria-label="Channel"
    >
      <button
        type="button"
        role="tab"
        aria-selected={channel === "sms"}
        onClick={() => choose("sms")}
        className={`${base} ${
          channel === "sms"
            ? "bg-white dark:bg-gray-700 text-primary-600 shadow-sm"
            : "text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
        }`}
      >
        <MessageSquare size={size === "sm" ? 12 : 14} /> SMS
      </button>
      <button
        type="button"
        role="tab"
        aria-selected={channel === "email"}
        onClick={() => choose("email")}
        className={`${base} ${
          channel === "email"
            ? "bg-white dark:bg-gray-700 text-primary-600 shadow-sm"
            : "text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
        }`}
      >
        <Mail size={size === "sm" ? 12 : 14} /> Email
      </button>
    </div>
  );
}
