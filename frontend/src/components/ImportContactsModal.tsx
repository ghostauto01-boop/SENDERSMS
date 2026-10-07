/**
 * ImportContactsModal — the one CSV import flow, used from Contacts AND from
 * inside a campaign. Upload → map columns → pick (or create) a list + tags →
 * done. Creating a list inline selects it immediately and the import result
 * carries the list back so the caller can keep working with it.
 */
import { useState } from "react";
import Papa from "papaparse";
import api from "../api/client";
import toast from "react-hot-toast";
import { Upload, X } from "lucide-react";
import { customKey, detectColumns } from "../utils/csv";
import { clearShortcodeCache } from "./ShortcodePicker";
import { notifyDataChange } from "../utils/syncEvents";
import ListPicker from "./ListPicker";
import TagPicker from "./TagPicker";

export interface ImportResult {
  imported: number;
  /** Rows that matched an existing contact and improved it (not rejected). */
  merged?: number;
  merged_by_name?: number;
  duplicates: number;
  invalid: number;
  skipped: number;
  total_rows: number;
  errors: any[];
  imported_ids: number[];
  new_variables: number;
  // Channel split of what was imported, so "these will never get an SMS" is
  // visible immediately instead of being discovered at campaign time.
  with_phone?: number;
  with_email?: number;
  email_only?: number;
  phone_only?: number;
  both?: number;
  list: { id: number; name: string; contact_count: number } | null;
}

interface Props {
  onClose: () => void;
  onDone: (result: ImportResult) => void;
  defaultListId?: string;
}

const FIELD_OPTIONS: Array<{ value: string; label: string } | { header: string; label: string }> = [];

export default function ImportContactsModal({ onClose, onDone, defaultListId = "" }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<{ headers: string[]; rows: string[][] } | null>(null);
  const [step, setStep] = useState<"upload" | "map" | "importing" | "done">("upload");
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [selectedListId, setSelectedListId] = useState(defaultListId);
  const [tags, setTags] = useState<string[]>([]);
  const [result, setResult] = useState<ImportResult | null>(null);

  // Silence unused warnings for the shared-field constant shape.
  void FIELD_OPTIONS;

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (!f) return;
    setFile(f);
    setStep("map");
    Papa.parse<string[]>(f, {
      complete: (res) => {
        const rows = res.data.filter((r: string[]) => r && r.some((c: string) => c && c.trim() !== ""));
        const headers = (rows[0] || []).map((h: string) => (h || "").trim());
        const previewRows = rows.slice(1, 6);
        setPreview({ headers, rows: previewRows });
        setMapping(detectColumns(headers));
      },
    });
  };

  const doImport = async () => {
    if (!file) return;
    setStep("importing");
    const fd = new FormData();
    fd.append("file", file);
    // Send the complete visible mapping. This preserves explicit Ignore
    // choices and gives every otherwise-unknown column a custom field target.
    fd.append("column_mapping", JSON.stringify(mapping));
    if (selectedListId) fd.append("list_id", selectedListId);
    if (tags.length > 0) fd.append("tags", tags.join(", "));
    try {
      const { data } = await api.post("/contacts/import/csv", fd, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      setResult(data as ImportResult);
      setStep("done");
      // Fresh imports register new shortcodes — drop the cached registry so
      // every composer picks them up without a reload.
      clearShortcodeCache();
      notifyDataChange("variables-changed");
      if ((data.imported ?? 0) > 0) toast.success(`${data.imported} contacts imported`);
      if ((data.new_variables ?? 0) > 0)
        toast.success(`${data.new_variables} new shortcode${data.new_variables > 1 ? "s" : ""} discovered`, { icon: "🧩" });
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Import failed");
      setStep("map");
    }
  };

  const phoneMapped = preview?.headers.some((h) => mapping[h] === "phone_number");
  const emailMapped = preview?.headers.some((h) => mapping[h] === "email");
  // A contact needs a phone OR an email. Requiring a phone column (as this
  // modal used to) made it impossible to import the perfectly ordinary file
  // that only carries addresses.
  const identityMapped = phoneMapped || emailMapped;
  // Which rows can be imported, counted from the preview so the operator sees
  // the split before committing rather than in the result afterwards.
  const channelCounts = (() => {
    if (!preview) return null;
    const idx = (field: string) =>
      preview.headers.findIndex((h) => mapping[h] === field);
    const phoneIdx = idx("phone_number");
    const emailIdx = idx("email");
    let withPhone = 0, withEmail = 0, neither = 0;
    preview.rows.forEach((row) => {
      const hasPhone = phoneIdx >= 0 && (row[phoneIdx] || "").trim() !== "";
      const hasEmail = emailIdx >= 0 && (row[emailIdx] || "").trim() !== "";
      if (hasPhone) withPhone += 1;
      if (hasEmail) withEmail += 1;
      if (!hasPhone && !hasEmail) neither += 1;
    });
    return { withPhone, withEmail, neither, sampled: preview.rows.length };
  })();

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/50 p-0 sm:p-4">
      <div className="bg-white dark:bg-gray-800 w-full sm:max-w-xl max-h-[92vh] overflow-y-auto rounded-t-[20px] sm:rounded-2xl">
        <div className="sticky top-0 bg-primary-700 dark:bg-gray-800 px-4 py-3 flex items-center justify-between z-10">
          <h2 className="text-white font-semibold">Import contacts</h2>
          <button
            onClick={onClose}
            aria-label="Close import"
            className="w-8 h-8 rounded-full bg-white/10 flex items-center justify-center text-white"
          >
            <X size={16} />
          </button>
        </div>
        <div className="p-4 sm:p-6">
          {step === "upload" && (
            <div className="space-y-3">
              <div className="border-2 border-dashed border-primary-600/30 bg-primary-50 dark:bg-primary-950/30 rounded-2xl p-6 text-center">
                <Upload size={32} className="mx-auto text-primary-600 mb-2" />
                <p className="font-medium text-gray-900 dark:text-white">Drop your CSV here</p>
                <p className="text-xs text-gray-500 mt-1">
                  Every column is imported — including your own custom fields, which become shortcodes
                </p>
                <input type="file" accept=".csv" onChange={handleFileChange} className="mt-3 block w-full text-sm" />
              </div>
              <p className="text-xs text-gray-500 text-center">
                Columns are auto-detected. Unknown columns become template-ready custom fields.
              </p>
            </div>
          )}

          {step === "map" && preview && (
            <div className="space-y-3">
              <p className="text-sm font-semibold text-gray-900 dark:text-white">
                Map columns <span className="font-normal text-gray-500">(auto-detected)</span>
              </p>
              {!identityMapped && (
                <p className="text-xs bg-danger-100 text-danger-700 rounded-xl px-3 py-2">
                  ⚠ Map a column to <b>Phone</b> or <b>Email</b> — a contact needs at
                  least one of the two to be reachable.
                </p>
              )}
              {identityMapped && !phoneMapped && (
                <p className="text-xs bg-primary-100 text-primary-700 rounded-xl px-3 py-2">
                  📧 Email-only import — every contact will be reachable by email
                  and skipped by SMS campaigns.
                </p>
              )}
              {identityMapped && !emailMapped && (
                <p className="text-xs bg-warning-200 text-warning-700 rounded-xl px-3 py-2">
                  📱 Phone-only import — no addresses in this file, so nobody in it
                  can be emailed. You can add emails later with “Enrich emails”.
                </p>
              )}
              {channelCounts && channelCounts.neither > 0 && (
                <p className="text-xs bg-warning-200 text-warning-700 rounded-xl px-3 py-2">
                  {channelCounts.neither} of the first {channelCounts.sampled} rows have
                  neither a phone nor an email and will be reported as invalid.
                </p>
              )}
              <div className="max-h-40 overflow-auto text-xs border border-gray-200 dark:border-gray-700 rounded-xl">
                <table className="w-full">
                  <thead>
                    <tr className="bg-gray-100 dark:bg-gray-900">
                      {preview.headers.map((h: string) => (
                        <th key={h} className="px-2 py-2 text-left text-gray-600">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {preview.rows.map((r: string[], i: number) => (
                      <tr key={i} className="border-t border-gray-100 dark:border-gray-700">
                        {r.map((c: string, j: number) => (
                          <td key={j} className="px-2 py-1.5 truncate max-w-[100px]">
                            {c}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 max-h-56 overflow-y-auto">
                {preview.headers.map((h: string) => (
                  <div key={h}>
                    <label className="text-xs font-medium text-gray-600">{h}</label>
                    <select
                      className="w-full mt-1 px-2 py-2 bg-gray-100 dark:bg-gray-900 rounded-xl text-xs text-gray-900 dark:text-white"
                      value={mapping[h] || ""}
                      onChange={(e) => setMapping({ ...mapping, [h]: e.target.value })}
                      aria-label={`Map column ${h}`}
                    >
                      <option value="">Ignore</option>
                      <option value={`custom:${customKey(h)}`}>Custom field · {customKey(h)}</option>
                      <option value="phone_number">Phone</option>
                      <option value="first_name">First Name</option>
                      <option value="last_name">Last Name</option>
                      <option value="business_name">Restaurant / Business</option>
                      <option value="email">Email</option>
                      <option value="city">City</option>
                      <option value="state">State</option>
                      <option value="country">Country</option>
                      <option value="website">Website</option>
                      <option value="industry">Industry</option>
                      <option value="source">Source</option>
                      <option value="lead_status">Lead Status</option>
                      <option value="notes">Notes</option>
                      <option value="tags">Tags</option>
                    </select>
                  </div>
                ))}
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label className="text-xs font-medium text-gray-600">Import into list</label>
                  <div className="mt-1">
                    <ListPicker
                      value={selectedListId}
                      onChange={setSelectedListId}
                      placeholder="No list — pick or create one"
                    />
                  </div>
                </div>
                <div>
                  <label className="text-xs font-medium text-gray-600">Tag every imported contact</label>
                  <div className="mt-1">
                    <TagPicker value={tags} onChange={setTags} placeholder="e.g. restaurants" />
                  </div>
                </div>
              </div>
              <p className="text-xs text-gray-500">
                Can&apos;t find the right list? Open the list box and tap{" "}
                <b className="text-primary-600">＋ Create new list</b> — it is created and selected
                in one step, no trip to the Lists page.
              </p>
              <button
                onClick={doImport}
                disabled={!identityMapped}
                className="w-full py-3 rounded-full bg-primary-600 text-white font-semibold disabled:opacity-50"
              >
                Import Contacts
              </button>
            </div>
          )}

          {step === "importing" && (
            <div className="text-center py-8">
              <div className="w-10 h-10 border-4 border-primary-600 border-t-transparent rounded-full animate-spin mx-auto" />
              <p className="text-sm mt-4 text-gray-500">Importing…</p>
            </div>
          )}

          {step === "done" && result && (
            <div className="space-y-3 text-center">
              <p className="text-4xl">✅</p>
              <p className="font-semibold text-gray-900 dark:text-white">Import complete</p>
              {result.list && (
                <p className="text-xs text-gray-500">
                  into list <b className="text-primary-600">{result.list.name}</b>
                </p>
              )}
              <div className="grid grid-cols-4 gap-2 text-sm">
                <div className="bg-primary-100 rounded-xl p-3">
                  <p className="text-2xl font-bold text-primary-700">{result.imported}</p>
                  <p className="text-xs text-gray-600">Imported</p>
                </div>
                <div className="bg-primary-50 rounded-xl p-3">
                  <p className="text-2xl font-bold text-primary-600">{result.merged ?? 0}</p>
                  <p className="text-xs text-gray-600">Merged</p>
                </div>
                <div className="bg-warning-200 rounded-xl p-3">
                  <p className="text-2xl font-bold text-warning-700">{result.duplicates}</p>
                  <p className="text-xs">Duplicates</p>
                </div>
                <div className="bg-danger-100 rounded-xl p-3">
                  <p className="text-2xl font-bold text-danger-700">{result.invalid}</p>
                  <p className="text-xs">Invalid</p>
                </div>
              </div>
              {typeof result.with_phone === "number" && (
                <div className="text-left text-xs bg-gray-100 dark:bg-gray-900 rounded-xl p-3 space-y-0.5">
                  <p className="font-semibold text-gray-700 dark:text-gray-200">
                    How these contacts can be reached
                  </p>
                  <p className="text-gray-600 dark:text-gray-400">
                    📱 Phone only: <b>{result.phone_only ?? 0}</b>
                  </p>
                  <p className="text-gray-600 dark:text-gray-400">
                    📧 Email only: <b>{result.email_only ?? 0}</b>
                  </p>
                  <p className="text-gray-600 dark:text-gray-400">
                    📱📧 Both: <b>{result.both ?? 0}</b>
                  </p>
                  {(result.email_only ?? 0) > 0 && (
                    <p className="text-gray-500 pt-1">
                      Email-only contacts are skipped by SMS campaigns — filter the
                      Contacts page by “No email”/“No phone” to see them.
                    </p>
                  )}
                </div>
              )}
              {(result.merged ?? 0) > 0 && (
                <p className="text-xs text-gray-500">
                  {result.merged} existing contact{result.merged === 1 ? "" : "s"} were
                  updated with details from this file instead of being duplicated.
                  {(result.merged_by_name ?? 0) > 0 && (
                    <> {result.merged_by_name} matched by name.</>
                  )}
                </p>
              )}
              {result.errors.length > 0 && (
                <details className="text-xs text-left">
                  <summary className="cursor-pointer text-gray-500">{result.errors.length} errors</summary>
                  <pre className="mt-1 max-h-32 overflow-auto bg-gray-100 p-2 rounded">
                    {JSON.stringify(result.errors.slice(0, 20), null, 2)}
                  </pre>
                </details>
              )}
              <button
                onClick={() => onDone(result)}
                className="w-full py-3 rounded-full bg-primary-600 text-white font-semibold"
              >
                Done
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
