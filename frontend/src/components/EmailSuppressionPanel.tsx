import { useCallback, useEffect, useState } from "react";
import toast from "react-hot-toast";
import { Mail, ShieldOff, Trash2 } from "lucide-react";
import emailApi, { type EmailSuppression } from "../api/email";
import { Empty, Field, fmtDate } from "../pages/ads/ui";

export default function EmailSuppressionPanel() {
  const [items, setItems] = useState<EmailSuppression[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [address, setAddress] = useState("");
  const [reason, setReason] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await emailApi.suppression({ search: search || undefined, per_page: 100, page });
      setItems(data.items);
      setTotal(data.total);
      if (page > 1 && !data.items.length) setPage(p => p - 1);
    } catch {
      toast.error("Could not load the suppression list");
    } finally {
      setLoading(false);
    }
  }, [search, page]);

  useEffect(() => {
    load();
  }, [load]);

  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!address.includes("@")) return toast.error("Enter a valid email address");
    try {
      await emailApi.addSuppression({ email_address: address, reason: reason || "Manual" });
      toast.success("Address suppressed");
      setAddress("");
      setReason("");
      load();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not suppress this address");
    }
  };

  const remove = async (entry: EmailSuppression) => {
    if (!window.confirm(`Remove suppression for ${entry.email_address}? Other sending restrictions still apply. Only resume sending with permission.`)) return;
    try {
      await emailApi.removeSuppression(entry.id);
      toast.success("Suppression removed. Other sending restrictions still apply.");
      load();
    } catch {
      toast.error("Could not remove this entry");
    }
  };

  return (
    <div className="space-y-4">
      <form onSubmit={add} className="card flex flex-wrap items-end gap-3">
        <Field label="Email address">
          <input
            className="input"
            type="email"
            required
            aria-label="Suppress email address"
            value={address}
            onChange={(e) => setAddress(e.target.value)}
            placeholder="do-not-contact@company.com"
          />
        </Field>
        <Field label="Reason (optional)">
          <input className="input" value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
        <button className="btn-primary" type="submit">
          <ShieldOff size={16} className="mr-1" /> Suppress
        </button>
      </form>

      <div className="flex items-center justify-between gap-3">
        <input
          className="input max-w-xs"
          placeholder="Search suppressed addresses…"
          value={search}
          onChange={(e) => { setSearch(e.target.value); setPage(1); }}
        />
        <span className="text-sm text-gray-500">{total} suppressed</span>
      </div>

      <div className="flex items-center gap-3 text-sm">
        <button className="btn-secondary btn-sm" disabled={loading || page === 1} onClick={() => setPage(p => p - 1)}>Previous suppressions</button>
        <span>Page {page} of {Math.max(1, Math.ceil(total / 100))}</span>
        <button className="btn-secondary btn-sm" disabled={loading || page * 100 >= total} onClick={() => setPage(p => p + 1)}>Next suppressions</button>
      </div>
      {loading ? (
        <div className="py-10 text-center text-gray-500">Loading…</div>
      ) : items.length === 0 ? (
        <Empty
          title="Nothing suppressed"
          body="Unsubscribes, hard bounces and manual blocks all land here — and none of them are ever emailed again."
        />
      ) : (
        <div className="card divide-y divide-gray-100 dark:divide-gray-800">
          {items.map((entry) => (
            <div key={entry.id} className="py-2 flex items-center justify-between gap-3 text-sm">
              <div className="min-w-0">
                <p className="font-medium truncate flex items-center gap-2">
                  <Mail size={13} className="text-gray-400" /> {entry.email_address}
                  {entry.hard_bounce && <span className="badge-red">hard bounce</span>}
                </p>
                <p className="text-xs text-gray-500 truncate">
                  {entry.reason || "—"} · {entry.source || "manual"} · {fmtDate(entry.created_at)}
                </p>
              </div>
              <button aria-label={`Remove suppression for ${entry.email_address}`} className="text-gray-400 hover:text-danger-500 px-2" onClick={() => remove(entry)}>
                <Trash2 size={15} />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
