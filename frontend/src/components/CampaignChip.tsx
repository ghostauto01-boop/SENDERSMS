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
  { bg: "bg-primary-100 dark:bg-primary-900/50", text: "text-primary-700 dark:text-primary-300", dot: "bg-primary-500" },
  { bg: "bg-primary-100 dark:bg-primary-900/50", text: "text-primary-700 dark:text-primary-300", dot: "bg-primary-500" },
  { bg: "bg-success-100 dark:bg-success-900/50", text: "text-success-700 dark:text-success-300", dot: "bg-success-500" },
  { bg: "bg-warning-100 dark:bg-warning-900/50", text: "text-warning-700 dark:text-warning-300", dot: "bg-warning-500" },
  { bg: "bg-primary-100 dark:bg-primary-900/50", text: "text-primary-700 dark:text-primary-300", dot: "bg-primary-500" },
  { bg: "bg-primary-100 dark:bg-primary-900/50", text: "text-primary-700 dark:text-primary-300", dot: "bg-primary-500" },
  { bg: "bg-warning-100 dark:bg-warning-900/50", text: "text-warning-700 dark:text-warning-300", dot: "bg-warning-500" },
  { bg: "bg-primary-100 dark:bg-primary-900/50", text: "text-primary-700 dark:text-primary-300", dot: "bg-primary-500" },
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
