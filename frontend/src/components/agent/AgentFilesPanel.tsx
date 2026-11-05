import { useCallback, useRef, useState } from "react";
import toast from "react-hot-toast";
import {
  AlertTriangle, Check, ChevronLeft, ChevronRight, FileArchive, FileSpreadsheet, FileText,
  Image, Loader2, Paperclip, Table2, Trash2, Upload, X,
} from "lucide-react";
import { agentApi, type AgentFile } from "../../api/aiAgent";

/**
 * FILE UPLOAD & MAPPING
 *
 * An operator's contact list arrives as a CSV from a phone, a spreadsheet from a
 * laptop, or a PDF somebody emailed. This panel accepts all three, shows what
 * the server actually parsed (columns, types, the first rows), and proposes a
 * mapping of the file's columns onto the app's contact fields.
 *
 * The upload is finished before the agent is asked anything: the parse happens
 * server-side on the bytes, so a file that cannot be read says so immediately
 * instead of turning into a confident, wrong import three turns later.
 */

const CONTACT_FIELDS = [
  { key: "phone_number", label: "Phone number" },
  { key: "email", label: "Email" },
  { key: "first_name", label: "First name" },
  { key: "last_name", label: "Last name" },
  { key: "business_name", label: "Business name" },
  { key: "city", label: "City" },
  { key: "state", label: "State" },
  { key: "country", label: "Country" },
  { key: "tags", label: "Tags" },
  { key: "", label: "— ignore —" },
];

const ACCEPT = "*/*";
const MAX_MB = 25;

const iconFor = (filename: string) => {
  if (/\.(xlsx|xlsm)$/i.test(filename)) return FileSpreadsheet;
  if (/\.zip$/i.test(filename)) return FileArchive;
  if (/\.(png|jpe?g|webp)$/i.test(filename)) return Image;
  if (/\.(pdf|docx|pptx|md|json|html?|xml|ya?ml|rtf|log)$/i.test(filename)) return FileText;
  if (/\.(csv|tsv)$/i.test(filename)) return Table2;
  return FileText;
};

const humanSize = (bytes: number) => {
  if (!bytes) return "0 KB";
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

export default function AgentFilesPanel({
  sessionId,
  files,
  onUploaded,
  onDeleted,
  onUse,
  onClose,
}: {
  sessionId: string;
  files: AgentFile[];
  onUploaded: (file: AgentFile) => void;
  onDeleted: (fileId: number) => void;
  /** Hand the file to the agent: attaches it to the next message. */
  onUse?: (file: AgentFile) => void;
  onClose?: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [preview, setPreview] = useState<AgentFile | null>(null);
  const [rows, setRows] = useState<Record<string, any>[]>([]);
  const [offset, setOffset] = useState(0);
  const [loadingRows, setLoadingRows] = useState(false);
  const [mapping, setMapping] = useState<Record<string, string>>({});

  const upload = useCallback(
    async (file: File) => {
      if (file.size > MAX_MB * 1024 * 1024) {
        toast.error(`${file.name} is ${humanSize(file.size)} — the limit is ${MAX_MB} MB.`);
        return;
      }
      setProgress(0);
      try {
        const uploaded = await agentApi.upload(sessionId, file, setProgress);
        onUploaded(uploaded);
        toast.success(`${uploaded.filename} uploaded`);
        openPreview(uploaded);
      } catch (error: any) {
        const detail = error?.response?.data?.detail || "That file could not be read.";
        toast.error(String(detail), { duration: 7000 });
      } finally {
        setProgress(null);
        if (inputRef.current) inputRef.current.value = "";
      }
    },
    [sessionId],
  );

  const openPreview = async (file: AgentFile) => {
    setPreview(file);
    setMapping(file.preview?.suggested_mapping || {});
    setOffset(0);
    await loadRows(file.id, 0);
  };

  const loadRows = async (fileId: number, nextOffset: number) => {
    setLoadingRows(true);
    try {
      const data = await agentApi.fileRows(fileId, nextOffset, 25);
      setRows(data.rows || []);
      setOffset(nextOffset);
    } catch {
      setRows([]);
    } finally {
      setLoadingRows(false);
    }
  };

  const columns: { name: string; type?: string }[] = [
    ...(preview?.preview?.columns || []),
    ...rows.flatMap((row) => Object.keys(row)).map((name) => ({ name })),
  ].filter((column, index, all) => all.findIndex((c) => c.name === column.name) === index);

  const total = preview?.preview?.total_rows_estimate || 0;

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between gap-2 border-b border-gray-100 px-3 py-2.5 dark:border-gray-800">
        <h3 className="flex items-center gap-1.5 text-[13px] font-semibold">
          <Paperclip size={13} /> Files
        </h3>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            className="btn-primary btn-sm"
            disabled={progress !== null}
          >
            {progress !== null ? <Loader2 size={12} className="animate-spin" /> : <Upload size={12} />} Upload
          </button>
          {onClose && (
            <button type="button" onClick={onClose} className="btn-ghost btn-sm !px-1.5" aria-label="Close files">
              <X size={14} />
            </button>
          )}
        </div>
      </div>

      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) void upload(file);
        }}
      />

      <div className="flex-1 space-y-2 overflow-y-auto p-2.5">
        {/* drop zone */}
        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            const file = e.dataTransfer.files?.[0];
            if (file) void upload(file);
          }}
          onClick={() => inputRef.current?.click()}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); inputRef.current?.click(); } }}
          className={`flex cursor-pointer flex-col items-center justify-center gap-1 rounded-xl border border-dashed px-3 py-4 text-center transition-colors ${
            dragging
              ? "border-primary-500 bg-primary-50 dark:bg-primary-950/40"
              : "border-gray-300 hover:border-primary-400 dark:border-gray-700"
          }`}
        >
          <Upload size={16} className="text-gray-400" />
          <p className="text-[12px] font-medium text-gray-700 dark:text-gray-200">
            Drop a file, or tap to choose
          </p>
          <p className="text-[10.5px] text-gray-400">Any file type · up to {MAX_MB} MB</p>
          <p className="text-[10.5px] text-gray-500">PDFs, Office documents, text, spreadsheets and ZIP members are extracted where possible; images use a vision-capable model.</p>
          {progress !== null && (
            <div className="mt-1 w-full max-w-[180px]">
              <div className="h-1 overflow-hidden rounded-full bg-gray-200 dark:bg-gray-700">
                <div className="h-full rounded-full bg-primary-600 transition-all" style={{ width: `${progress}%` }} />
              </div>
              <p className="mt-0.5 text-[10px] text-gray-400">{progress}%</p>
            </div>
          )}
        </div>

        {files.length === 0 && progress === null && (
          <p className="px-1 py-2 text-center text-[11.5px] text-gray-400">
            No files yet. Upload a contact list and the agent will show the columns
            it found before importing anything.
          </p>
        )}

        {files.map((file) => {
          const Icon = iconFor(file.filename);
          const active = preview?.id === file.id;
          return (
            <div
              key={file.id}
              className={`rounded-xl border p-2 transition-colors ${
                active
                  ? "border-primary-300 bg-primary-50/60 dark:border-primary-800 dark:bg-primary-950/30"
                  : "border-gray-200 dark:border-gray-700"
              }`}
            >
              <div className="flex items-start gap-2">
                <Icon size={15} className="mt-0.5 shrink-0 text-gray-400" />
                <button type="button" onClick={() => openPreview(file)} className="min-w-0 flex-1 text-left">
                  <p className="truncate text-[12.5px] font-medium">{file.filename}</p>
                  <p className="truncate text-[10.5px] text-gray-400">
                    {humanSize(file.size)}
                    {file.preview?.total_rows_estimate ? ` · ${file.preview.total_rows_estimate} rows` : ""}
                    {file.preview?.columns?.length ? ` · ${file.preview.columns.length} cols` : ""}
                  </p>
                </button>
                <button
                  type="button"
                  onClick={async () => {
                    if (!window.confirm(`Remove ${file.filename}?`)) return;
                    await agentApi.deleteFile(file.id);
                    onDeleted(file.id);
                    if (preview?.id === file.id) setPreview(null);
                    toast.success("File removed");
                  }}
                  className="rounded-lg p-1 text-gray-400 hover:bg-danger-50 hover:text-danger-600 dark:hover:bg-danger-950/40"
                  aria-label={`Remove ${file.filename}`}
                >
                  <Trash2 size={12} />
                </button>
              </div>
              {onUse && (
                <button type="button" onClick={() => onUse(file)} className="btn-secondary btn-sm mt-1.5 w-full !text-[11.5px]">
                  Use in this chat
                </button>
              )}
            </div>
          );
        })}
      </div>

      {/* ---- preview drawer ------------------------------------------ */}
      {preview && (
        <div className="max-h-[62%] shrink-0 overflow-y-auto border-t border-gray-200 bg-gray-50/60 dark:border-gray-700 dark:bg-gray-900/40">
          <div className="flex items-start justify-between gap-2 px-3 py-2">
            <div className="min-w-0">
              <p className="truncate text-[12.5px] font-semibold">{preview.filename}</p>
              <p className="text-[10.5px] text-gray-500 dark:text-gray-400">
                {preview.summary || preview.preview?.summary || "Parsed on the server."}
              </p>
            </div>
            <button
              type="button"
              onClick={() => setPreview(null)}
              className="rounded-lg p-1 text-gray-400 hover:text-gray-600"
              aria-label="Close preview"
            >
              <X size={14} />
            </button>
          </div>

          {/* mapping */}
          {columns.length > 0 && (
            <div className="px-3 pb-2">
              <p className="flex items-center gap-1 text-[10.5px] font-semibold uppercase tracking-wide text-gray-400">
                <Check size={10} /> Column mapping
              </p>
              <div className="mt-1 space-y-1">
                {columns.slice(0, 12).map((column) => (
                  <div key={column.name} className="flex items-center gap-1.5">
                    <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-gray-600 dark:text-gray-300">
                      {column.name}
                    </span>
                    <span className="shrink-0 text-gray-300">→</span>
                    <select
                      value={mapping[column.name] || ""}
                      onChange={(e) => setMapping((m) => ({ ...m, [column.name]: e.target.value }))}
                      className="w-[130px] shrink-0 rounded-lg border border-gray-200 bg-white px-1.5 py-1 text-[11px] dark:border-gray-700 dark:bg-gray-800"
                      aria-label={`Map ${column.name}`}
                    >
                      {!CONTACT_FIELDS.some((f) => f.key === (mapping[column.name] || "")) && (
                        <option value={mapping[column.name] || ""}>{mapping[column.name] || "— ignore —"}</option>
                      )}
                      {CONTACT_FIELDS.map((field) => (
                        <option key={field.key || "ignore"} value={field.key}>
                          {field.label}
                        </option>
                      ))}
                    </select>
                  </div>
                ))}
              </div>
              <p className="mt-1.5 text-[10px] text-gray-400">
                Suggested mapping for reference. Tell the agent about any changes before importing.
              </p>
            </div>
          )}

          {/* rows */}
          <div className="px-3 pb-3">
            {loadingRows ? (
              <div className="space-y-1">
                {[0, 1, 2].map((i) => (
                  <div key={i} className="skeleton h-5 w-full" />
                ))}
              </div>
            ) : rows.length === 0 ? (
              <p className="flex items-center gap-1.5 text-[11.5px] text-gray-500">
                <AlertTriangle size={11} /> No tabular rows to display. See the summary above.
              </p>
            ) : (
              <>
                <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-900">
                  <table className="w-full border-collapse text-left text-[11px]">
                    <thead>
                      <tr className="bg-gray-50 dark:bg-gray-800">
                        {(preview.preview?.columns || []).slice(0, 5).map((column) => (
                          <th key={column.name} className="whitespace-nowrap px-2 py-1 font-semibold text-gray-600 dark:text-gray-300">
                            {column.name}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {rows.slice(0, 8).map((row, index) => (
                        <tr key={index} className="border-t border-gray-100 dark:border-gray-800">
                          {(preview.preview?.columns || []).slice(0, 5).map((column) => (
                            <td key={column.name} className="max-w-[140px] truncate px-2 py-1 text-gray-600 dark:text-gray-400">
                              {String(row[column.name] ?? "")}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="mt-1.5 flex items-center justify-between text-[10.5px] text-gray-400">
                  <span>
                    Showing {offset + 1}–{Math.min(offset + rows.length, total || offset + rows.length)}
                    {total ? ` of ${total}` : ""}
                  </span>
                  <div className="flex gap-1">
                    <button
                      type="button"
                      disabled={offset === 0}
                      onClick={() => loadRows(preview.id, Math.max(0, offset - 25))}
                      className="rounded-md p-1 hover:bg-gray-100 disabled:opacity-40 dark:hover:bg-gray-800"
                      aria-label="Previous rows"
                    >
                      <ChevronLeft size={12} />
                    </button>
                    <button
                      type="button"
                      disabled={total ? offset + rows.length >= total : rows.length < 25}
                      onClick={() => loadRows(preview.id, offset + 25)}
                      className="rounded-md p-1 hover:bg-gray-100 disabled:opacity-40 dark:hover:bg-gray-800"
                      aria-label="Next rows"
                    >
                      <ChevronRight size={12} />
                    </button>
                  </div>
                </div>
              </>
            )}
          </div>

          {onUse && (
            <div className="sticky bottom-0 border-t border-gray-200 bg-white/90 px-3 py-2 backdrop-blur dark:border-gray-700 dark:bg-gray-900/90">
              <button type="button" onClick={() => onUse(preview)} className="btn-primary btn-sm w-full">
                <Check size={12} /> Ask the agent about this file
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
