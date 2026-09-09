import { Megaphone, Sparkles } from "lucide-react";
import type { CampaignRef } from "../types";

/**
 * "Which campaign is this lead from?" — one chip, used everywhere.
 *
 * The colour is derived from the campaign name, so the same campaign is
 * always the same colour across the inbox list, the chat header and the
 * campaign pages. That consistency is the whole point: you learn to
 * recognise a campaign at a glance instead of reading every label.
 *
 * The icon distinguishes the two campaign systems (classic Campaigns vs the
 * SMS Ads Manager) without spending horizontal space on the word.
 */

const PALETTE = [
  { bg: "bg-blue-100 dark:bg-blue-900/50", text: "text-blue-700 dark:text-blue-300", dot: "bg-blue-500" },
  { bg: "bg-purple-100 dark:bg-purple-900/50", text: "text-purple-700 dark:text-purple-300", dot: "bg-purple-500" },
  { bg: "bg-emerald-100 dark:bg-emerald-900/50", text: "text-emerald-700 dark:text-emerald-300", dot: "bg-emerald-500" },
  { bg: "bg-amber-100 dark:bg-amber-900/50", text: "text-amber-700 dark:text-amber-300", dot: "bg-amber-500" },
  { bg: "bg-pink-100 dark:bg-pink-900/50", text: "text-pink-700 dark:text-pink-300", dot: "bg-pink-500" },
  { bg: "bg-cyan-100 dark:bg-cyan-900/50", text: "text-cyan-700 dark:text-cyan-300", dot: "bg-cyan-500" },
  { bg: "bg-orange-100 dark:bg-orange-900/50", text: "text-orange-700 dark:text-orange-300", dot: "bg-orange-500" },
  { bg: "bg-indigo-100 dark:bg-indigo-900/50", text: "text-indigo-700 dark:text-indigo-300", dot: "bg-indigo-500" },
];

/** Stable colour for a campaign: same name + kind always yields the same slot. */
export function campaignColor(ref: Pick<CampaignRef, "id" | "kind" | "name">) {
  const seed = `${ref.kind}:${ref.id}:${ref.name}`;
  let hash = 0;
  for (let i = 0; i < seed.length; i++) hash = (hash * 31 + seed.charCodeAt(i)) % PALETTE.length;
  return PALETTE[hash];
}

export default function CampaignChip({
  campaign,
  size = "sm",
  onClick,
  title,
  className = "",
}: {
  campaign: CampaignRef | null | undefined;
  size?: "xs" | "sm";
  onClick?: () => void;
  title?: string;
  className?: string;
}) {
  if (!campaign) return null;
  const colour = campaignColor(campaign);
  const Icon = campaign.kind === "ads" ? Sparkles : Megaphone;
  const pad = size === "xs" ? "px-1.5 py-[1px] text-[10px]" : "px-2 py-0.5 text-[11px]";
  const iconSize = size === "xs" ? 9 : 11;

  const content = (
    <>
      <Icon size={iconSize} className="flex-shrink-0" />
      <span className="truncate max-w-[120px]">{campaign.name}</span>
    </>
  );

  const base = `inline-flex items-center gap-1 rounded-full font-medium ${pad} ${colour.bg} ${colour.text} ${className}`;

  if (onClick) {
    return (
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          onClick();
        }}
        title={title || `From campaign: ${campaign.name} — open it`}
        className={`${base} hover:opacity-80 transition-opacity cursor-pointer`}
      >
        {content}
      </button>
    );
  }

  return (
    <span title={title || `From campaign: ${campaign.name}`} className={base}>
      {content}
    </span>
  );
}
