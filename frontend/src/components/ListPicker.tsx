/**
 * ListPicker — one list selector used everywhere a list is needed.
 *
 * Searchable + scrollable dropdown, and "＋ Create new list" inline at the
 * bottom so an import or campaign never has to stop halfway to go and create
 * a list first. Creating selects the new list immediately.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronDown, List as ListIcon, Plus, Search } from "lucide-react";
import api from "../api/client";
import type { ContactListSummary } from "../types";

interface Props {
  value: string;
  onChange: (listId: string) => void;
  placeholder?: string;
  allowNone?: boolean;
  noneLabel?: string;
  disabled?: boolean;
  className?: string;
}

export default function ListPicker({
  value,
  onChange,
  placeholder = "Select a list…",
  allowNone = true,
  noneLabel = "No list",
  disabled = false,
  className = "",
}: Props) {
  const [open, setOpen] = useState(false);
  const [lists, setLists] = useState<ContactListSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState("");
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [saving, setSaving] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/lists/", { params: { per_page: 100 } });
      setLists(data.items ?? []);
    } catch {
      setLists([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
        setCreating(false);
      }
    };
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        setCreating(false);
      }
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
    if (!q) return lists;
    return lists.filter((l) => l.name.toLowerCase().includes(q));
  }, [lists, query]);

  const selected = lists.find((l) => String(l.id) === value);

  const createList = async () => {
    const name = newName.trim();
    if (!name || saving) return;
    setSaving(true);
    try {
      const { data } = await api.post("/lists/", null, { params: { name } });
      setLists((prev) => [
        { id: data.id, name: data.name, description: data.description ?? null, contact_count: 0 },
        ...prev,
      ]);
      onChange(String(data.id));
      setNewName("");
      setCreating(false);
      setOpen(false);
      setQuery("");
    } catch {
      // Leave the popover open so the typed name is not lost.
    } finally {
      setSaving(false);
    }
  };

  return (
    <div ref={rootRef} className={`relative ${className}`}>
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen(!open)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="w-full flex items-center gap-2 px-3 py-2.5 bg-[#f0f2f5] dark:bg-[#111b21] rounded-xl text-sm text-left focus:outline-none focus:ring-2 focus:ring-[#00a884]/20 disabled:opacity-60"
      >
        <ListIcon size={15} className="text-[#00a884] flex-shrink-0" />
        <span className={`flex-1 truncate ${selected ? "text-[#111b21] dark:text-white font-medium" : "text-[#667781]"}`}>
          {selected ? `${selected.name} (${selected.contact_count})` : placeholder}
        </span>
        <ChevronDown size={15} className={`text-[#667781] flex-shrink-0 transition-transform ${open ? "rotate-180" : ""}`} />
      </button>

      {open && (
        <div
          role="listbox"
          aria-label="Contact lists"
          className="absolute z-50 mt-1 left-0 right-0 rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-xl flex flex-col overflow-hidden"
        >
          <div className="flex items-center gap-1.5 px-2.5 py-2 border-b border-gray-100 dark:border-gray-700">
            <Search size={14} className="text-gray-400 flex-shrink-0" />
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search lists…"
              aria-label="Search lists"
              className="w-full bg-transparent text-sm outline-none text-gray-800 dark:text-gray-100"
            />
          </div>

          <div className="overflow-y-auto max-h-56 py-1">
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
              filtered.map((l) => (
                <button
                  key={l.id}
                  type="button"
                  role="option"
                  aria-selected={String(l.id) === value}
                  onClick={() => {
                    onChange(String(l.id));
                    setOpen(false);
                    setQuery("");
                  }}
                  className="w-full text-left px-3 py-2 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center justify-between gap-2"
                >
                  <span className="text-sm text-gray-800 dark:text-gray-100 truncate">{l.name}</span>
                  <span className="flex items-center gap-2 flex-shrink-0">
                    <span className="text-[11px] text-[#667781]">{l.contact_count}</span>
                    {String(l.id) === value && <Check size={14} className="text-[#00a884]" />}
                  </span>
                </button>
              ))}
            {!loading && filtered.length === 0 && (
              <p className="px-3 py-3 text-xs text-gray-500">
                No list matches “{query.trim()}”.
              </p>
            )}
          </div>

          <div className="border-t border-gray-100 dark:border-gray-700 p-2 bg-gray-50 dark:bg-gray-900/40">
            {creating ? (
              <div className="flex gap-1.5">
                <input
                  autoFocus
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      createList();
                    }
                  }}
                  placeholder="New list name…"
                  aria-label="New list name"
                  className="flex-1 min-w-0 px-2.5 py-2 bg-white dark:bg-[#111b21] rounded-lg text-sm outline-none border border-[#00a884]/40 text-gray-800 dark:text-gray-100"
                />
                <button
                  type="button"
                  onClick={createList}
                  disabled={!newName.trim() || saving}
                  className="px-3 py-2 rounded-lg bg-[#00a884] text-white text-sm font-medium disabled:opacity-50 flex-shrink-0"
                >
                  {saving ? "…" : "Create"}
                </button>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => {
                  setCreating(true);
                  setNewName(query.trim());
                }}
                className="w-full flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium text-[#00a884] hover:bg-[#00a884]/10"
              >
                <Plus size={15} /> Create new list
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
