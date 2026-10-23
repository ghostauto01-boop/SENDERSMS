import type { InvoiceStatus } from "../../api/invoices";

/**
 * One status, one colour — and the colour means the same thing on the list,
 * the detail drawer, the public page and analytics. Never use these colours
 * for decoration elsewhere.
 */
const STYLES: Record<InvoiceStatus, string> = {
  draft: "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300",
  sent: "bg-primary-100 text-primary-800 dark:bg-primary-950/60 dark:text-primary-300",
  viewed: "bg-accent-100 text-accent-800 dark:bg-accent-950/60 dark:text-accent-300",
  partial: "bg-warning-100 text-warning-700 dark:bg-warning-950/60 dark:text-warning-300",
  paid: "bg-success-100 text-success-700 dark:bg-success-950/60 dark:text-success-300",
  overdue: "bg-danger-100 text-danger-700 dark:bg-danger-950/60 dark:text-danger-300",
  void: "bg-gray-100 text-gray-400 line-through dark:bg-gray-800 dark:text-gray-500",
};

const LABELS: Record<InvoiceStatus, string> = {
  draft: "Draft",
  sent: "Sent",
  viewed: "Viewed",
  partial: "Part paid",
  paid: "Paid",
  overdue: "Overdue",
  void: "Void",
};

export default function StatusPill({
  status,
  label,
  dot = true,
  className = "",
}: {
  status: InvoiceStatus | string;
  label?: string;
  dot?: boolean;
  className?: string;
}) {
  const key = (status || "draft") as InvoiceStatus;
  const dotColour: Record<string, string> = {
    draft: "bg-gray-400",
    sent: "bg-primary-500",
    viewed: "bg-accent-500",
    partial: "bg-warning-500",
    paid: "bg-success-600",
    overdue: "bg-danger-500",
    void: "bg-gray-300",
  };
  return (
    <span className={`badge ${STYLES[key] || STYLES.draft} ${className}`}>
      {dot && <span className={`w-1.5 h-1.5 rounded-full ${dotColour[key] || "bg-gray-400"}`} />}
      {label || LABELS[key] || key}
    </span>
  );
}
