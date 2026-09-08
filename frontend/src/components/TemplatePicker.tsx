/**
 * TemplatePicker — one template selector used everywhere a template is needed
 * (campaigns, inbox replies, calendar invites/reminders, quick send).
 *
 * Searchable + scrollable, shows the message body so the choice is visible
 * without leaving the form, and links out to the Templates page.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronDown, FileText, Search } from "lucide-react";
import { Link } from "react-router-dom";
import api from "../api/client";
import type { Template } from "../types";

interface Props {
  value: string;
  onChange: (templateId: string) => void;
  placeholder?: string;
  allowNone?: boolean;
  noneLabel?: string;
  disabled?: boolean;
  showPreview?: boolean;
  className?: string;
}

export default function TemplatePicker({
  value,
  onChange,
  placeholder = "Choose a template…",
  allowNone = true,
  noneLabel = "No template (write custom)",
  disabled = false,
  showPreview = true,
  className = "",
}: Props) {
  const [open, setOpen] = useState(false);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      try {
        const all: Template[] = [];
        let page = 1;
        let total = 0;
        do {
          const { data } = await api.get("/templates/", { params: { page, per_page: 100 } });
          all.push(...(data.items || []));
          total = data.total ?? all.length;
          page += 1;
        } while (all.length < total);
        if (!cancelled) setTemplates(all.filter((t) => t.is_active));
      } catch {
        if (!cancelled) setTemplates([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onEsc);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onEsc);
    };
  }, [open ]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return templates;
    return templates.filter(
      (t) =>
        t.name.toLowerCase().includes(q) ||
        (t.category || "").toLowerCase().includes(q) ||
        t.body.toLowerCase().includes(q)
    );
  }, [templates, query]);

  const selected = templates.find((t) => String(t.id) === value);

  return (
    <div className={className}>
      <div ref={rootRef} className="relative">
        <button
          type="button"
          disabled={disabled}
          onClick={() => setOpen(!open)}
          aria-haspopup="listbox"
          aria-expanded={open}
          className="w-full flex items-center gap-2 px-3 py-2.5 bg-[#f0f2f5] dark:bg-[#111b21] rounded-xl text-sm text-left focus:outline-none focus:ring-2 focus:ring-[#00a884]/20 disabled:opacity-60"
        >
          <FileText size={15} className="text-[#00a884] flex-shrink-0" />
          <span className={`flex-1 truncate ${selected ? "text-[#111b21] dark:text-white font-medium" : "text-[#667781]"}`}>
            {selected ? `${selected.name}${selected.category ? ` · ${selected.category}` : ""}` : placeholder}
          </span>
          <ChevronDown size={15} className={`text-[#667781] flex-shrink-0 transition-transform ${open ? "rotate-180" : ""}`} />
        </button>

        {open && (
          <div
            role="listbox"
            aria-label="Templates"
            className="absolute z-50 mt-1 left-0 right-0 rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-xl flex flex-col overflow-hidden"
          >
            <div className="flex items-center gap-1.5 px-2.5 py-2 border-b border-gray-100 dark:border-gray-700">
              <Search size={14} className="text-gray-400 flex-shrink-0" />
              <input
                autoFocus
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search templates…"
                aria-label="Search templates"
                className="w-full bg-transparent text-sm outline-none text-gray-800 dark:text-gray-100"
              />
            </div>

            <div className="overflow-y-auto max-h-64 py-1">
              {loading && <p className="px-3 py-3 text-xs text-gray-500">Loading…</p>}
              {!loading && allowNone && (
                <button
                  type="button"
                  role="option"
                  aria-selected={value === ""}
                  onClick={() => {
                    onChange("");
                    setOpen(false);
                    setQuery("");
                  }}
                  className="w-full text-left px-3 py-2 text-sm hover:bg-gray-100 dark:hover:bg-gray-700 text-[#667781] flex items-center justify-between"
                >
                  {noneLabel}
                  {value === "" && <Check size={14} className="text-[#00a884]" />}
                </button>
              )}
              {!loading &&
                filtered.map((t) => (
                  <button
                    key={t.id}
                    type="button"
                    role="option"
                    aria-selected={String(t.id) === value}
                    onClick={() => {
                      onChange(String(t.id));
                      setOpen(false);
                      setQuery("");
                    }}
                    className="w-full text-left px-3 py-2 hover:bg-gray-100 dark:hover:bg-gray-700"
                  >
                    <span className="flex items-center justify-between gap-2">
                      <span className="text-sm font-medium text-gray-800 dark:text-gray-100 truncate">
                        {t.name}
                        {t.category && (
                          <span className="ml-1.5 text-[11px] font-normal text-[#667781]">{t.category}</span>
                        )}
                      </span>
                      {String(t.id) === value && <Check size={14} className="text-[#00a884] flex-shrink-0" />}
                    </span>
                    <span className="block text-xs text-[#667781] dark:text-[#8696a0] truncate mt-0.5">
                      {t.body}
                    </span>
                  </button>
                ))}
              {!loading && filtered.length === 0 && (
                <p className="px-3 py-3 text-xs text-gray-500">No template matches.</p>
              )}
            </div>

            <div className="border-t border-gray-100 dark:border-gray-700 px-3 py-2 bg-gray-50 dark:bg-gray-900/40">
              <Link
                to="/templates"
                className="text-xs text-[#00a884] hover:underline"
                onClick={() => setOpen(false)}
              >
                ＋ Manage templates (new, edit, shortcodes)
              </Link>
            </div>
          </div>
        )}
      </div>

      {showPreview && selected && (
        <p className="mt-1.5 text-xs text-[#667781] dark:text-[#8696a0] whitespace-pre-wrap break-words bg-[#f0f2f5] dark:bg-[#111b21] rounded-lg px-2.5 py-2">
          {selected.body}
        </p>
      )}
    </div>
  );
}
