/**
 * TagPicker — multi-tag input with suggestions, shared by imports, meetings,
 * and everywhere else tags are used. Typing a new name and pressing Enter
 * creates it on the spot.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Tag as TagIcon, X } from "lucide-react";
import api from "../api/client";
import type { TagCount } from "../types";

interface Props {
  value: string[];
  onChange: (tags: string[]) => void;
  placeholder?: string;
  suggestionsUrl?: string;
  className?: string;
}

export default function TagPicker({
  value,
  onChange,
  placeholder = "Add tags…",
  suggestionsUrl = "/contacts/tags/",
  className = "",
}: Props) {
  const [input, setInput] = useState("");
  const [open, setOpen] = useState(false);
  const [allTags, setAllTags] = useState<TagCount[]>([]);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .get(suggestionsUrl)
      .then(({ data }) => {
        if (!cancelled) setAllTags(data.items ?? []);
      })
      .catch(() => {
        if (!cancelled) setAllTags([]);
      });
    return () => {
      cancelled = true;
    };
  }, [suggestionsUrl]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open ]);

  const suggestions = useMemo(() => {
    const q = input.trim().toLowerCase();
    const mine = new Set(value.map((t) => t.toLowerCase()));
    return allTags.filter(
      (t) => !mine.has(t.name.toLowerCase()) && (!q || t.name.toLowerCase().includes(q))
    );
  }, [allTags, input, value]);

  const add = (raw: string) => {
    const name = raw.trim().replace(/[,;|]+/g, "");
    if (!name) return;
    if (value.some((t) => t.toLowerCase() === name.toLowerCase())) {
      setInput("");
      return;
    }
    onChange([...value, name.slice(0, 100)]);
    setInput("");
  };

  const remove = (name: string) => onChange(value.filter((t) => t !== name));

  return (
    <div ref={rootRef} className={`relative ${className}`}>
      <div className="flex flex-wrap items-center gap-1.5 px-2.5 py-2 bg-[#f0f2f5] dark:bg-[#111b21] rounded-xl min-h-[44px] focus-within:ring-2 focus-within:ring-[#00a884]/20">
        <TagIcon size={14} className="text-[#667781] flex-shrink-0" />
        {value.map((t) => (
          <span
            key={t}
            className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-full bg-[#e7f3ff] text-[#0066cc] dark:bg-[#182533] dark:text-[#53bdeb] font-medium"
          >
            {t}
            <button type="button" onClick={() => remove(t)} aria-label={`Remove tag ${t}`} className="hover:text-red-500">
              <X size={11} />
            </button>
          </span>
        ))}
        <input
          value={input}
          onChange={(e) => {
            setInput(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              add(input);
            } else if (e.key === "Backspace" && !input && value.length > 0) {
              remove(value[value.length - 1]);
            } else if (e.key === "Escape") {
              setOpen(false);
            }
          }}
          placeholder={value.length === 0 ? placeholder : ""}
          aria-label="Add a tag"
          className="flex-1 min-w-[90px] bg-transparent text-sm outline-none text-gray-800 dark:text-gray-100 placeholder:text-[#667781]"
        />
      </div>

      {open && (suggestions.length > 0 || input.trim()) && (
        <div className="absolute z-50 mt-1 left-0 right-0 rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-xl overflow-hidden max-h-48 overflow-y-auto py-1">
          {suggestions.slice(0, 20).map((t) => (
            <button
              key={t.name}
              type="button"
              onClick={() => add(t.name)}
              className="w-full text-left px-3 py-1.5 text-sm hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center justify-between gap-2"
            >
              <span className="text-gray-800 dark:text-gray-100 truncate">{t.name}</span>
              <span className="text-[11px] text-[#667781] flex-shrink-0">{t.count}</span>
            </button>
          ))}
          {input.trim() && !suggestions.some((t) => t.name.toLowerCase() === input.trim().toLowerCase()) && (
            <button
              type="button"
              onClick={() => add(input)}
              className="w-full text-left px-3 py-1.5 text-sm text-[#00a884] hover:bg-[#00a884]/10 font-medium"
            >
              ＋ Create “{input.trim()}”
            </button>
          )}
        </div>
      )}
    </div>
  );
}
