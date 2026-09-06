/**
 * ShortcodePicker — insert a variable shortcode at the caret.
 *
 * Writing a personalized message meant remembering the exact spelling of every
 * shortcode and typing it by hand; a typo like {{Business_nam}} is silently
 * stripped at send time, so the mistake only shows up in the delivered SMS.
 * This drops a button next to any body/reply field that lists the variables
 * that actually exist (straight from the registry, so freshly imported CSV
 * columns appear on their own) and splices the chosen one in at the cursor,
 * leaving the caret after the insertion so typing simply continues.
 */
import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { Braces, Search } from "lucide-react";
import api from "../api/client";
import type { ContactVariable } from "../types";

interface Props {
  /** The field to insert into. */
  targetRef: RefObject<HTMLTextAreaElement | HTMLInputElement | null>;
  /** Current text of that field. */
  value: string;
  /** Receives the text with the shortcode spliced in. */
  onChange: (next: string) => void;
  /** Optional label next to the icon. */
  label?: string;
  className?: string;
}

/** Module-level cache: the registry is small and shared by every composer. */
let cache: ContactVariable[] | null = null;

export default function ShortcodePicker({
  targetRef,
  value,
  onChange,
  label = "Shortcode",
  className = "",
}: Props) {
  const [open, setOpen] = useState(false);
  const [vars, setVars] = useState<ContactVariable[]>(cache ?? []);
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);
  /** Caret position captured before the field loses focus to the button. */
  const caretRef = useRef<number | null>(null);

  // Remember where the caret was: clicking the button blurs the textarea, and
  // on blur selectionStart collapses to the end, which would otherwise append
  // every shortcode to the end of the message instead of at the cursor.
  useEffect(() => {
    const el = targetRef.current;
    if (!el) return;
    const remember = () => { caretRef.current = el.selectionStart; };
    el.addEventListener("keyup", remember);
    el.addEventListener("mouseup", remember);
    el.addEventListener("blur", remember);
    return () => {
      el.removeEventListener("keyup", remember);
      el.removeEventListener("mouseup", remember);
      el.removeEventListener("blur", remember);
    };
  }, [targetRef]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onEsc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onEsc);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onEsc);
    };
  }, [open]);

  const load = async () => {
    if (cache) { setVars(cache); return; }
    setLoading(true);
    try {
      const { data } = await api.get("/variables/", { params: { active_only: true } });
      const items: ContactVariable[] = data.items ?? data ?? [];
      cache = items;
      setVars(items);
    } catch {
      setVars([]);
    } finally {
      setLoading(false);
    }
  };

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next) { setQuery(""); load(); }
  };

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return vars;
    return vars.filter(
      v => v.label.toLowerCase().includes(q) || v.shortcode.toLowerCase().includes(q),
    );
  }, [vars, query]);

  const insert = (shortcode: string) => {
    // The registry stores the code bare ("pain_point"); the braces are added
    // at display time everywhere else in the app. Insert the wrapped form --
    // inserting the bare key would send the literal word to the contact.
    const token = `{{${shortcode}}}`;
    const el = targetRef.current;
    // Fall back to the end of the text when the field was never focused.
    const pos = caretRef.current ?? el?.selectionStart ?? value.length;
    const at = Math.max(0, Math.min(pos, value.length));
    // Keep words from running together, without doubling existing spaces.
    const before = value.slice(0, at);
    const after = value.slice(at);
    const lead = before && !/\s$/.test(before) ? " " : "";
    const snippet = lead + token;
    onChange(before + snippet + after);
    setOpen(false);

    // Restore focus and drop the caret right after what we inserted so the
    // user can keep typing mid-sentence.
    const caret = at + snippet.length;
    caretRef.current = caret;
    requestAnimationFrame(() => {
      const node = targetRef.current;
      if (!node) return;
      node.focus();
      try { node.setSelectionRange(caret, caret); } catch { /* input type may not support it */ }
    });
  };

  return (
    <div ref={rootRef} className={`relative inline-block ${className}`}>
      <button
        type="button"
        onClick={toggle}
        aria-haspopup="listbox"
        aria-expanded={open}
        title="Insert a variable shortcode at the cursor"
        className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-200"
      >
        <Braces size={13} />
        {label}
      </button>

      {open && (
        <div
          role="listbox"
          aria-label="Variable shortcodes"
          className="absolute z-50 bottom-full mb-1 left-0 w-64 max-h-72 overflow-hidden rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-lg flex flex-col"
        >
          <div className="flex items-center gap-1.5 px-2 py-1.5 border-b border-gray-100 dark:border-gray-700">
            <Search size={13} className="text-gray-400 flex-shrink-0" />
            <input
              autoFocus
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="Search variables…"
              aria-label="Search variables"
              className="w-full bg-transparent text-xs outline-none text-gray-800 dark:text-gray-100"
            />
          </div>
          <div className="overflow-y-auto">
            {loading && <p className="px-3 py-3 text-xs text-gray-500">Loading…</p>}
            {!loading && filtered.length === 0 && (
              <p className="px-3 py-3 text-xs text-gray-500">
                {vars.length === 0
                  ? "No variables yet — import a CSV to create them."
                  : "No match."}
              </p>
            )}
            {!loading && filtered.map(v => (
              <button
                key={v.id}
                type="button"
                role="option"
                aria-selected={false}
                onClick={() => insert(v.shortcode)}
                className="w-full text-left px-3 py-1.5 hover:bg-gray-100 dark:hover:bg-gray-700"
              >
                <span className="block text-xs font-medium text-gray-800 dark:text-gray-100">
                  {v.label}
                </span>
                <span className="block font-mono text-[11px] text-primary-600 dark:text-primary-400">
                  {`{{${v.shortcode}}}`}
                </span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/** Let a fresh CSV import show up without a page reload. */
export function clearShortcodeCache() { cache = null; }
