/**
 * OPTIONAL multi-sender pool picker for email campaigns.
 *
 * Off by default: a campaign with no pool behaves exactly as before (one
 * sender, or the account default). Turning it on distributes contacts
 * across the checked senders — each contact is pinned to one sender, like
 * a creative assignment — so a single From address does not take the whole
 * campaign's volume and reputation risk. Replies keep threading on the
 * sender that mailed the contact, and the Email Inbox can be filtered per
 * sender.
 */
import { senderOptionLabel } from "../utils/senders";

export type SenderLike = {
  id: number;
  name: string;
  from_email: string;
  sends_as?: string | null;
  is_active?: boolean;
  sender_type?: string;
  provider?: string;
};

export type SenderPoolValue = {
  /** Unified sender ids (positive Brevo, negative Gmail mailbox). */
  ids: number[];
  distribution: string; // round_robin | weighted | random
  /** {sender_id: weight} — only used by the weighted distribution. */
  weights: Record<string, number>;
};

export const emptySenderPool: SenderPoolValue = {
  ids: [],
  distribution: "round_robin",
  weights: {},
};

export function senderPoolFromCampaign(campaign: any): SenderPoolValue {
  if (!campaign) return emptySenderPool;
  return {
    ids: Array.isArray(campaign.email_account_ids) ? campaign.email_account_ids : [],
    distribution: campaign.sender_distribution || "round_robin",
    weights: campaign.email_sender_weights || {},
  };
}

/** Approximate split of `total` contacts, for the live preview line. */
export function senderSplitPreview(
  pool: SenderPoolValue,
  accounts: SenderLike[],
  total = 100
): { name: string; count: number }[] {
  const chosen = accounts.filter((a) => pool.ids.includes(a.id));
  if (!chosen.length || total <= 0) return [];
  if (pool.distribution === "weighted") {
    const weights = chosen.map((a) => Math.max(0, Number(pool.weights[String(a.id)] ?? 1)));
    const sum = weights.reduce((s, w) => s + w, 0) || chosen.length;
    const raw = weights.map((w) => (total * (sum ? w / sum : 1 / chosen.length)));
    const counts = raw.map((v) => Math.floor(v));
    let left = total - counts.reduce((s, v) => s + v, 0);
    raw
      .map((v, i) => ({ i, frac: v - Math.floor(v) }))
      .sort((a, b) => b.frac - a.frac)
      .forEach(({ i }) => {
        if (left-- > 0) counts[i] += 1;
      });
    return chosen.map((a, i) => ({ name: a.name, count: counts[i] }));
  }
  const base = Math.floor(total / chosen.length);
  let rest = total - base * chosen.length;
  return chosen.map((a) => ({ name: a.name, count: base + (rest-- > 0 ? 1 : 0) }));
}

export default function SenderPoolPicker({
  accounts,
  value,
  onChange,
  primaryId,
}: {
  accounts: SenderLike[];
  value: SenderPoolValue;
  onChange: (next: SenderPoolValue) => void;
  /** The campaign's single "Send through" sender, folded into the pool server-side. */
  primaryId?: string | number | null;
}) {
  const enabled = value.ids.length > 0;
  const active = accounts.filter((a) => a.is_active !== false);
  const preview = senderSplitPreview(value, accounts);

  const toggle = (id: number, checked: boolean) => {
    const ids = checked ? [...value.ids, id] : value.ids.filter((x) => x !== id);
    onChange({ ...value, ids });
  };

  return (
    <div className="rounded-xl border border-gray-200 dark:border-gray-700 p-3 space-y-3">
      <label className="flex items-start gap-2 text-sm cursor-pointer">
        <input
          type="checkbox"
          className="mt-0.5"
          checked={enabled}
          onChange={(e) => {
            if (!e.target.checked) {
              onChange({ ...value, ids: [] });
            } else if (active.length) {
              // Start from the primary sender (if any) plus the first other
              // active sender, so turning this on always does something.
              const seed = [
                ...(primaryId ? [Number(primaryId)] : []),
                ...active.map((a) => a.id),
              ].filter((v, i, arr) => arr.indexOf(v) === i);
              onChange({ ...value, ids: seed.slice(0, Math.max(2, seed.length > 1 ? 2 : 1)) });
            }
          }}
        />
        <span>
          <span className="font-medium text-gray-800 dark:text-gray-100">
            Distribute across multiple senders (optional)
          </span>
          <span className="block text-xs text-gray-500 dark:text-gray-400 mt-0.5">
            Spread this campaign over several From addresses so one sender isn&apos;t burned.
            Each contact is pinned to one sender — like a creative assignment — and replies
            stay on that sender. Leave off to send from the single sender above.
          </span>
        </span>
      </label>

      {enabled && (
        <>
          <div>
            <label className="label">Distribution</label>
            <select
              className="input"
              value={value.distribution}
              onChange={(e) => onChange({ ...value, distribution: e.target.value })}
            >
              <option value="round_robin">Round robin — split evenly, in order</option>
              <option value="weighted">Weighted — split by the shares below</option>
              <option value="random">Random — stable random sender per contact</option>
            </select>
          </div>
          <ul className="space-y-1.5">
            {accounts.map((a) => {
              const checked = value.ids.includes(a.id);
              return (
                <li key={a.id} className="flex items-center gap-2 text-sm">
                  <label className="flex items-center gap-2 flex-1 min-w-0 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={!a.is_active}
                      onChange={(e) => toggle(a.id, e.target.checked)}
                    />
                    <span className="truncate text-gray-700 dark:text-gray-200">
                      {senderOptionLabel(a)}
                      {primaryId != null && String(a.id) === String(primaryId) ? " · primary" : ""}
                      {a.is_active ? "" : " (off)"}
                    </span>
                  </label>
                  {value.distribution === "weighted" && checked && (
                    <span className="flex items-center gap-1 shrink-0">
                      <input
                        type="number"
                        min={0}
                        className="input !w-20 py-1"
                        aria-label={`Share for ${a.name}`}
                        value={value.weights[String(a.id)] ?? ""}
                        placeholder="1"
                        onChange={(e) =>
                          onChange({
                            ...value,
                            weights: {
                              ...value.weights,
                              [String(a.id)]: e.target.value === "" ? 0 : Number(e.target.value),
                            },
                          })
                        }
                      />
                      <span className="text-xs text-gray-400">share</span>
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
          {preview.length > 1 && (
            <p className="text-xs text-gray-500 dark:text-gray-400">
              Of the next 100 contacts:{" "}
              {preview.map((p) => `${p.name} ≈ ${p.count}`).join(" · ")}
            </p>
          )}
          {value.ids.length === 1 && (
            <p className="text-xs text-warning-600 dark:text-warning-400">
              Only one sender selected — tick at least one more to spread the load, or untick
              the box above to use the single sender.
            </p>
          )}
        </>
      )}
    </div>
  );
}
