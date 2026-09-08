/**
 * ContactPicker — searchable contact selector (single or multi), shared by the
 * calendar, campaigns and anywhere else contacts are picked. Selected contacts
 * appear as chips; the dropdown searches the server as you type.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Check, Search, User, X } from "lucide-react";
import api from "../api/client";
import type { Contact } from "../types";

interface Props {
  value: number[];
  onChange: (contactIds: number[]) => void;
  multiple?: boolean;
  placeholder?: string;
  excludeIds?: number[];
  className?: string;
}

export const contactName = (c: Partial<Contact> & { phone_number?: string }) =>
  `${c.first_name || ""} ${c.last_name || ""}`.trim() ||
  c.business_name ||
  c.phone_number ||
  "Unnamed";

export const avatarColor = (name: string) => {
  const colors = [
    "bg-[#00a884]", "bg-[#128C7E]", "bg-[#075E54]", "bg-[#34B7F1]", "bg-[#FF8A65]",
    "bg-[#BA68C8]", "bg-[#4DB6AC]", "bg-[#FFB74D]", "bg-[#F06292]", "bg-[#7986CB]",
  ];
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % colors.length;
  return colors[h];
};

export const contactInitials = (c: Partial<Contact> & { phone_number?: string }) => {
  if (c.first_name || c.last_name)
    return `${(c.first_name?.[0] || "").toUpperCase()}${(c.last_name?.[0] || "").toUpperCase()}`;
  if (c.business_name) return c.business_name.slice(0, 2).toUpperCase();
  return (c.phone_number || "??").slice(-2);
};

export default function ContactPicker({
  value,
  onChange,
  multiple = true,
  placeholder = "Search contacts…",
  excludeIds = [],
  className = "",
}: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Contact[]>([]);
  const [loading, setLoading] = useState(false);
  // Full records for chips (dropdown rows only carry what the search returns).
  const [known, setKnown] = useState<Record<number, Contact>>({});
  const rootRef = useRef<HTMLDivElement>(null);
  const excluded = useMemo(() => new Set(excludeIds), [excludeIds]);

  // Resolve chip labels for ids we have not seen yet (edit forms).
  useEffect(() => {
    const missing = value.filter((id) => !known[id]);
    if (missing.length === 0) return;
    let cancelled = false;
    Promise.all(missing.slice(0, 25).map((id) => api.get(`/contacts/${id}`).then((r) => r.data as Contact).catch(() => null)))
      .then((contacts) => {
        if (cancelled) return;
        setKnown((prev) => {
          const next = { ...prev };
          contacts.forEach((c) => {
            if (c) next[c.id] = c;
          });
          return next;
        });
      });
    return () => {
      cancelled = true;
    };
     
  }, [value.join(",")]);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    const t = setTimeout(async () => {
      try {
        const { data } = await api.get("/contacts/", {
          params: { per_page: 25, search: query.trim() || undefined },
        });
        const items: Contact[] = data.items ?? [];
        setResults(items);
        setKnown((prev) => {
          const next = { ...prev };
          items.forEach((c) => {
            next[c.id] = c;
          });
          return next;
        });
      } catch {
        setResults([]);
      } finally {
        setLoading(false);
      }
    }, query ? 250 : 0);
    return () => clearTimeout(t);
  }, [open, query]);

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

  const toggle = (id: number) => {
    if (!multiple) {
      onChange(value[0] === id ? [] : [id]);
      setOpen(false);
      setQuery("");
      return;
    }
    onChange(value.includes(id) ? value.filter((v) => v !== id) : [...value, id]);
  };

  const visible = results.filter((c) => !excluded.has(c.id));

  return (
    <div ref={rootRef} className={`relative ${className}`}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="w-full flex items-center gap-2 px-2.5 py-2 bg-[#f0f2f5] dark:bg-[#111b21] rounded-xl text-sm text-left min-h-[44px] focus:outline-none focus:ring-2 focus-within:ring-[#00a884]/20 flex-wrap"
      >
        {value.length === 0 ? (
          <span className="flex items-center gap-2 text-[#667781] px-1">
            <User size={15} /> {placeholder}
          </span>
        ) : (
          value.slice(0, 8).map((id) => {
            const c = known[id];
            const label = c ? contactName(c) : `#${id}`;
            return (
              <span
                key={id}
                className="inline-flex items-center gap-1.5 text-xs pl-1 pr-1.5 py-1 rounded-full bg-white dark:bg-[#2a3942] text-[#111b21] dark:text-white font-medium shadow-sm"
              >
                <span className={`w-5 h-5 rounded-full flex items-center justify-center text-white text-[9px] font-semibold ${avatarColor(label)}`}>
                  {c ? contactInitials(c) : "?"}
                </span>
                <span className="max-w-[120px] truncate">{label}</span>
                <span
                  role="button"
                  tabIndex={0}
                  aria-label={`Remove ${label}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    onChange(value.filter((v) => v !== id));
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.stopPropagation();
                      onChange(value.filter((v) => v !== id));
                    }
                  }}
                  className="hover:text-red-500"
                >
                  <X size={11} />
                </span>
              </span>
            );
          })
        )}
        {value.length > 8 && (
          <span className="text-xs text-[#667781]">+{value.length - 8} more</span>
        )}
      </button>

      {open && (
        <div
          role="listbox"
          aria-label="Contacts"
          className="absolute z-50 mt-1 left-0 right-0 rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-xl flex flex-col overflow-hidden"
        >
          <div className="flex items-center gap-1.5 px-2.5 py-2 border-b border-gray-100 dark:border-gray-700">
            <Search size={14} className="text-gray-400 flex-shrink-0" />
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Type a name, business or phone…"
              aria-label="Search contacts"
              className="w-full bg-transparent text-sm outline-none text-gray-800 dark:text-gray-100"
            />
          </div>
          <div className="overflow-y-auto max-h-64 py-1">
            {loading && <p className="px-3 py-3 text-xs text-gray-500">Searching…</p>}
            {!loading && visible.length === 0 && (
              <p className="px-3 py-3 text-xs text-gray-500">No contacts found.</p>
            )}
            {!loading &&
              visible.map((c) => {
                const name = contactName(c);
                const active = value.includes(c.id);
                return (
                  <button
                    key={c.id}
                    type="button"
                    role="option"
                    aria-selected={active}
                    onClick={() => toggle(c.id)}
                    className="w-full text-left px-3 py-2 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2.5"
                  >
                    <span className={`w-8 h-8 rounded-full flex items-center justify-center text-white text-xs font-semibold flex-shrink-0 ${avatarColor(name)}`}>
                      {contactInitials(c)}
                    </span>
                    <span className="flex-1 min-w-0">
                      <span className="block text-sm text-gray-800 dark:text-gray-100 truncate">{name}</span>
                      <span className="block text-[11px] text-[#667781] font-mono">{c.phone_number}</span>
                    </span>
                    {active && <Check size={15} className="text-[#00a884] flex-shrink-0" />}
                  </button>
                );
              })}
          </div>
        </div>
      )}
    </div>
  );
}
