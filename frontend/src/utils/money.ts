/**
 * Money formatting and input parsing.
 *
 * The API speaks only in minor units (kobo/cents) as integers. These helpers
 * are the only place the UI divides by 100, so a rounding decision can never
 * disagree between the list, the editor and the public invoice page.
 */

export const CURRENCY_META: Record<string, { symbol: string; label: string; locale: string }> = {
  NGN: { symbol: "₦", label: "Nigerian Naira", locale: "en-NG" },
  USD: { symbol: "$", label: "US Dollar", locale: "en-US" },
  GHS: { symbol: "GH₵", label: "Ghanaian Cedi", locale: "en-GH" },
  ZAR: { symbol: "R", label: "South African Rand", locale: "en-ZA" },
  KES: { symbol: "KSh", label: "Kenyan Shilling", locale: "en-KE" },
  XOF: { symbol: "CFA ", label: "West African CFA franc", locale: "fr-SN" },
  EGP: { symbol: "E£", label: "Egyptian Pound", locale: "en-EG" },
};

export const CURRENCY_CODES = ["NGN", "USD", "GHS", "ZAR", "KES", "XOF", "EGP"];

export function currencySymbol(code: string | null | undefined): string {
  return CURRENCY_META[(code || "").toUpperCase()]?.symbol ?? `${(code || "").toUpperCase()} `;
}

/** 1234567 minor units NGN -> "₦12,345.67" */
export function formatMoney(minor: number | null | undefined, code: string | null | undefined): string {
  const amount = (Number(minor) || 0) / 100;
  const upper = (code || "NGN").toUpperCase();
  const meta = CURRENCY_META[upper];
  const formatted = amount.toLocaleString(meta?.locale || "en-NG", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  return `${meta?.symbol ?? `${upper} `}${formatted}`;
}

/** Compact display for chart axes / big totals: ₦1.2m, ₦840k. */
export function formatMoneyCompact(minor: number | null | undefined, code: string | null | undefined): string {
  const amount = (Number(minor) || 0) / 100;
  const symbol = currencySymbol(code);
  if (Math.abs(amount) >= 1_000_000) return `${symbol}${(amount / 1_000_000).toFixed(1)}m`;
  if (Math.abs(amount) >= 1_000) return `${symbol}${Math.round(amount / 1_000)}k`;
  return `${symbol}${amount.toFixed(0)}`;
}

/** "12,500.50" (major units, human input) -> 1250050 minor units. */
export function parseMoneyToMinor(value: string | number | null | undefined): number {
  if (value === null || value === undefined || value === "") return 0;
  const raw = String(value).replace(/[^0-9.-]/g, "");
  const amount = Number(raw);
  if (!Number.isFinite(amount)) return 0;
  return Math.round(amount * 100);
}

/** 1250050 -> "12500.50" for a controlled number input. */
export function minorToInput(minor: number | null | undefined): string {
  const amount = (Number(minor) || 0) / 100;
  return amount ? amount.toFixed(2) : "";
}

/** A Date/ISO string as the yyyy-mm-dd value a date input expects. */
export function toDateInput(value: string | Date | null | undefined, fallback?: Date): string {
  const date = value ? new Date(value) : fallback;
  if (!date || Number.isNaN(date.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** yyyy-mm-dd (local) -> an ISO instant at local noon (safe in every timezone). */
export function dateInputToIso(value: string): string | null {
  if (!value) return null;
  const [y, m, d] = value.split("-").map(Number);
  if (!y || !m || !d) return null;
  return new Date(y, m - 1, d, 12, 0, 0).toISOString();
}

/** "23 Oct 2026" in the user's locale. */
export function formatDate(value: string | Date | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

export function formatDateTime(value: string | Date | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString(undefined, {
    day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
  });
}
