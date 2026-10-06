/**
 * CalendarPage — the full calendar: month / week / day / agenda views, date
 * selection, contact filtering, tags, and SMS reminders.
 *
 * Every meeting is booked through MeetingModal, so invites, templates,
 * shortcodes and reminders behave exactly the same here as from the inbox.
 */
import { useEffect, useMemo, useState } from "react";
import api from "../api/client";
import toast from "react-hot-toast";
import {
  Bell, Calendar as CalendarIcon, ChevronLeft, ChevronRight, Clock,
  MapPin, Plus, Search, Users, Video,
} from "lucide-react";
import MeetingModal from "../components/MeetingModal";
import type { Meeting, TagCount } from "../types";

type View = "month" | "week" | "day" | "agenda";

const pad = (n: number) => String(n).padStart(2, "0");
const dayKey = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const TYPE_STYLE: Record<string, { chip: string; dot: string; label: string }> = {
  meeting: { chip: "bg-primary-100 text-primary-800", dot: "bg-primary-600", label: "Meeting" },
  call: { chip: "bg-primary-50 text-primary-600", dot: "bg-[#34B7F1]", label: "Call" },
  follow_up: { chip: "bg-warning-200 text-warning-700", dot: "bg-warning-500", label: "Follow-up" },
  reminder: { chip: "bg-warning-100 text-warning-700", dot: "bg-warning-400", label: "Reminder" },
  task: { chip: "bg-accent-100 text-accent-600", dot: "bg-accent-600", label: "Task" },
  other: { chip: "bg-gray-100 text-gray-600", dot: "bg-gray-400", label: "Other" },
};

const STATUS_STYLE: Record<string, string> = {
  scheduled: "bg-primary-50 text-primary-600",
  confirmed: "bg-primary-100 text-primary-700",
  completed: "bg-gray-100 text-gray-600",
  cancelled: "bg-danger-100 text-danger-700",
  no_show: "bg-warning-200 text-warning-700",
};

const fmtTime = (iso: string) =>
  new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });

const fmtDay = (key: string) => {
  const [y, m, d] = key.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, {
    weekday: "long", day: "numeric", month: "long",
  });
};

/** Monday of the week containing `d`. */
const weekStart = (d: Date) => {
  const copy = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const shift = (copy.getDay() + 6) % 7;
  copy.setDate(copy.getDate() - shift);
  return copy;
};

export default function CalendarPage() {
  const todayKey = dayKey(new Date());
  const [view, setView] = useState<View>("month");
  const [cursor, setCursor] = useState(() => new Date());
  const [selected, setSelected] = useState(todayKey);
  const [meetings, setMeetings] = useState<Meeting[]>([]);
  const [loading, setLoading] = useState(true);

  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("active");
  const [tagFilter, setTagFilter] = useState("");
  const [tags, setTags] = useState<TagCount[]>([]);

  const [showNew, setShowNew] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [newDate, setNewDate] = useState(todayKey);

  // Visible range per view; the server filters to it.
  const range = useMemo(() => {
    if (view === "day") {
      const s = new Date(`${selected}T00:00:00`);
      const e = new Date(`${selected}T23:59:59`);
      return { from: s.toISOString(), to: e.toISOString() };
    }
    if (view === "agenda") {
      const s = new Date();
      s.setHours(0, 0, 0, 0);
      const e = new Date(s.getTime() + 30 * 86400000);
      return { from: s.toISOString(), to: e.toISOString() };
    }
    if (view === "week") {
      const s = weekStart(cursor);
      const e = new Date(s.getTime() + 7 * 86400000 - 1000);
      return { from: s.toISOString(), to: e.toISOString() };
    }
    const first = new Date(cursor.getFullYear(), cursor.getMonth(), 1);
    const s = weekStart(first);
    const e = new Date(s.getTime() + 42 * 86400000 - 1000);
    return { from: s.toISOString(), to: e.toISOString() };
  }, [view, cursor, selected]);

  const load = async () => {
    setLoading(true);
    try {
      const params: Record<string, string> = {
        date_from: range.from,
        date_to: range.to,
        per_page: "500",
      };
      if (search.trim()) params.search = search.trim();
      if (typeFilter !== "all") params.event_type = typeFilter;
      if (statusFilter === "active") {
        // Active = not finished. The API has no multi-status filter, so fetch
        // everything and narrow it client-side below.
      } else if (statusFilter !== "all") {
        params.status = statusFilter;
      }
      if (tagFilter) params.tag = tagFilter;
      const { data } = await api.get("/calendar/", { params });
      let items: Meeting[] = data.items ?? [];
      if (statusFilter === "active") {
        items = items.filter((m) => ["scheduled", "confirmed"].includes(m.status));
      }
      setMeetings(items);
    } catch {
      toast.error("Could not load the calendar");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
     
  }, [range.from, range.to, typeFilter, statusFilter, tagFilter]);

  useEffect(() => {
    const t = setTimeout(load, 400);
    return () => clearTimeout(t);
     
  }, [search]);

  useEffect(() => {
    api.get("/calendar/tags").then(({ data }) => setTags(data.items ?? [])).catch(() => {});
  }, []);

  const byDay = useMemo(() => {
    const map: Record<string, Meeting[]> = {};
    meetings.forEach((m) => {
      const key = dayKey(new Date(m.starts_at));
      (map[key] = map[key] || []).push(m);
    });
    Object.values(map).forEach((list) =>
      list.sort((a, b) => +new Date(a.starts_at) - +new Date(b.starts_at))
    );
    return map;
  }, [meetings]);

  const gridDays = useMemo(() => {
    if (view === "week") {
      const s = weekStart(cursor);
      return Array.from({ length: 7 }, (_, i) => new Date(s.getTime() + i * 86400000));
    }
    // Month: 6 fixed rows so the grid never jumps height.
    const first = new Date(cursor.getFullYear(), cursor.getMonth(), 1);
    const s = weekStart(first);
    return Array.from({ length: 42 }, (_, i) => new Date(s.getTime() + i * 86400000));
  }, [view, cursor]);

  const agendaDays = useMemo(() => {
    if (view === "agenda") return Object.keys(byDay).sort();
    return [];
  }, [view, byDay]);

  const move = (dir: 1 | -1) => {
    const c = new Date(cursor);
    if (view === "month") c.setMonth(c.getMonth() + dir);
    else if (view === "week") c.setDate(c.getDate() + dir * 7);
    else if (view === "day") {
      const s = new Date(`${selected}T12:00:00`);
      s.setDate(s.getDate() + dir);
      setSelected(dayKey(s));
      setCursor(s);
      return;
    } else return;
    setCursor(c);
  };

  const goToday = () => {
    const now = new Date();
    setCursor(now);
    setSelected(dayKey(now));
  };

  const openNew = (dateKey?: string) => {
    setNewDate(dateKey || selected || todayKey);
    setShowNew(true);
  };

  const headerLabel = () => {
    if (view === "day") return fmtDay(selected);
    if (view === "agenda") return "Upcoming";
    return cursor.toLocaleDateString(undefined, { month: "long", year: "numeric" });
  };

  const selectedMeetings = byDay[selected] || [];
  const todayCount = (byDay[todayKey] || []).length;

  const chip = (m: Meeting) => {
    const style = TYPE_STYLE[m.event_type] || TYPE_STYLE.other;
    const dead = m.status === "cancelled";
    const done = m.status === "completed";
    return (
      <button
        key={m.id}
        onClick={(e) => {
          e.stopPropagation();
          setEditingId(m.id);
        }}
        title={`${m.title} · ${fmtTime(m.starts_at)} · ${m.status}`}
        className={`w-full text-left px-1.5 py-0.5 rounded-md text-[11px] leading-4 truncate flex items-center gap-1 ${style.chip} ${dead ? "line-through opacity-60" : ""} ${done ? "opacity-60" : ""}`}
      >
        <span className="hidden sm:inline font-medium">{fmtTime(m.starts_at)}</span>
        <span className="truncate">{m.title}</span>
        {m.send_sms_reminder && m.reminder_minutes.length > 0 && (
          <Bell size={9} className="flex-shrink-0 opacity-70" />
        )}
      </button>
    );
  };

  return (
    <div className="space-y-3 pb-20 lg:pb-0">
      {/* Header */}
      <div className="flex flex-col gap-2.5">
        <div className="flex items-center justify-between gap-2">
          <div>
            <h1 className="text-[22px] font-bold text-gray-900 dark:text-white flex items-center gap-2">
              <span className="w-8 h-8 rounded-full bg-primary-600 flex items-center justify-center text-white">
                <CalendarIcon size={16} />
              </span>
              Calendar
            </h1>
            <p className="text-[13px] text-gray-500 dark:text-gray-400">
              {todayCount} today · {meetings.length} in view · tap a date to plan it
            </p>
          </div>
          <button
            onClick={() => openNew()}
            className="h-10 px-4 rounded-full lg:rounded-lg bg-primary-600 hover:bg-primary-500 text-white flex items-center gap-1.5 text-sm font-medium shadow-sm"
          >
            <Plus size={17} /> <span className="hidden sm:inline">New meeting</span>
            <span className="sm:hidden">New</span>
          </button>
        </div>

        {/* View switch + navigation */}
        <div className="bg-white dark:bg-gray-800 rounded-xl p-1.5 flex gap-1 shadow-sm border border-gray-100 dark:border-gray-700 overflow-x-auto">
          {(["month", "week", "day", "agenda"] as View[]).map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              className={`flex-1 min-w-[70px] py-2 rounded-full text-sm font-medium capitalize transition-colors ${
                view === v
                  ? "bg-primary-600 text-white shadow-sm"
                  : "text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-900"
              }`}
            >
              {v}
            </button>
          ))}
        </div>

        <div className="flex items-center justify-between bg-white dark:bg-gray-800 rounded-xl px-2 py-1.5 shadow-sm border border-gray-100 dark:border-gray-700">
          <button
            onClick={() => move(-1)}
            aria-label="Previous"
            className="w-9 h-9 rounded-full hover:bg-gray-100 dark:hover:bg-gray-900 flex items-center justify-center text-gray-600"
          >
            <ChevronLeft size={18} />
          </button>
          <button onClick={goToday} className="text-sm font-semibold text-gray-900 dark:text-white hover:text-primary-600">
            {headerLabel()}
          </button>
          <div className="flex items-center gap-1">
            {view !== "agenda" && view !== "day" && (
              <button
                onClick={goToday}
                className="text-xs px-3 py-1.5 rounded-full bg-gray-100 dark:bg-gray-900 text-gray-600 dark:text-gray-400 font-medium mr-1"
              >
                Today
              </button>
            )}
            <button
              onClick={() => move(1)}
              aria-label="Next"
              className="w-9 h-9 rounded-full hover:bg-gray-100 dark:hover:bg-gray-900 flex items-center justify-center text-gray-600"
            >
              <ChevronRight size={18} />
            </button>
          </div>
        </div>

        {/* Filters */}
        <div className="bg-white dark:bg-gray-800 rounded-xl p-2.5 shadow-sm border border-gray-100 dark:border-gray-700 space-y-2">
          <div className="flex gap-2 flex-col sm:flex-row">
            <div className="relative flex-1">
              <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search title, location…"
                className="w-full pl-9 pr-3 py-2 bg-gray-100 dark:bg-gray-900 rounded-full text-sm placeholder:text-gray-500 focus:outline-none focus:ring-2 focus:ring-primary-600/20 text-gray-900 dark:text-white"
              />
            </div>
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              aria-label="Filter by status"
              className="bg-gray-100 dark:bg-gray-900 rounded-full px-3 py-2 text-sm text-gray-600 dark:text-gray-400 outline-none"
            >
              <option value="active">Active</option>
              <option value="all">All statuses</option>
              <option value="scheduled">Scheduled</option>
              <option value="confirmed">Confirmed</option>
              <option value="completed">Completed</option>
              <option value="cancelled">Cancelled</option>
              <option value="no_show">No-show</option>
            </select>
          </div>
          <div className="flex gap-1.5 overflow-x-auto pb-0.5">
            {[{ v: "all", l: "All types" }, ...Object.entries(TYPE_STYLE).map(([v, s]) => ({ v, l: s.label }))].map(
              (t) => (
                <button
                  key={t.v}
                  onClick={() => setTypeFilter(t.v)}
                  className={`whitespace-nowrap px-2.5 py-1.5 rounded-full text-xs font-medium flex-shrink-0 ${
                    typeFilter === t.v
                      ? "bg-primary-600 text-white"
                      : "bg-gray-100 dark:bg-gray-900 text-gray-600 dark:text-gray-400"
                  }`}
                >
                  {t.l}
                </button>
              )
            )}
          </div>
          {tags.length > 0 && (
            <div className="flex gap-1.5 overflow-x-auto pb-0.5">
              <button
                onClick={() => setTagFilter("")}
                className={`whitespace-nowrap px-2.5 py-1 rounded-full text-xs font-medium flex-shrink-0 ${
                  !tagFilter ? "bg-primary-600 text-white" : "bg-primary-50 text-primary-600"
                }`}
              >
                All tags
              </button>
              {tags.map((t) => (
                <button
                  key={t.name}
                  onClick={() => setTagFilter(tagFilter === t.name ? "" : t.name)}
                  className={`whitespace-nowrap px-2.5 py-1 rounded-full text-xs font-medium flex-shrink-0 ${
                    tagFilter === t.name ? "bg-primary-600 text-white" : "bg-primary-50 text-primary-600"
                  }`}
                >
                  {t.name} · {t.count}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Month / week grid */}
      {(view === "month" || view === "week") && (
        <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-100 dark:border-gray-700 overflow-hidden">
          <div className="grid grid-cols-7 bg-gray-100 dark:bg-gray-900">
            {WEEKDAYS.map((d) => (
              <div key={d} className="py-2 text-center text-[11px] font-semibold text-gray-500 uppercase">
                {d}
              </div>
            ))}
          </div>
          {loading ? (
            <div className="p-10 text-center">
              <div className="w-8 h-8 border-2 border-primary-600 border-t-transparent rounded-full animate-spin mx-auto" />
            </div>
          ) : (
            <div className="grid grid-cols-7">
              {gridDays.map((d, i) => {
                const key = dayKey(d);
                const list = byDay[key] || [];
                const isToday = key === todayKey;
                const isSelected = key === selected;
                const outside = view === "month" && d.getMonth() !== cursor.getMonth();
                return (
                  <div
                    key={i}
                    onClick={() => {
                      setSelected(key);
                      setCursor(new Date(d));
                    }}
                    onDoubleClick={() => openNew(key)}
                    className={`min-h-[64px] sm:min-h-[96px] p-1 border-b border-r border-gray-100 dark:border-gray-700 cursor-pointer transition-colors last:border-r-0 ${
                      outside ? "bg-gray-50/60 dark:bg-gray-900/40" : ""
                    } ${isSelected ? "bg-primary-50 dark:bg-primary-950/40 ring-1 ring-inset ring-primary-600" : "hover:bg-gray-50 dark:hover:bg-gray-900"}`}
                  >
                    <div className="flex items-center justify-between px-0.5">
                      <span
                        className={`w-6 h-6 flex items-center justify-center rounded-full text-xs font-medium ${
                          isToday
                            ? "bg-primary-600 text-white"
                            : outside
                              ? "text-gray-300 dark:text-gray-600"
                              : "text-gray-900 dark:text-white"
                        }`}
                      >
                        {d.getDate()}
                      </span>
                      {list.length > 0 && (
                        <span className="sm:hidden flex gap-0.5">
                          {list.slice(0, 3).map((m) => (
                            <span key={m.id} className={`w-1.5 h-1.5 rounded-full ${(TYPE_STYLE[m.event_type] || TYPE_STYLE.other).dot}`} />
                          ))}
                        </span>
                      )}
                    </div>
                    <div className="hidden sm:flex flex-col gap-0.5 mt-1">
                      {list.slice(0, 3).map(chip)}
                      {list.length > 3 && (
                        <span className="text-[10px] text-gray-500 px-1">+{list.length - 3} more</span>
                      )}
                    </div>
                    <div className="sm:hidden mt-0.5 space-y-0.5">
                      {list.slice(0, 2).map((m) => (
                        <button
                          key={m.id}
                          onClick={(e) => {
                            e.stopPropagation();
                            setEditingId(m.id);
                          }}
                          className="w-full text-left text-[10px] leading-3 truncate text-gray-600 dark:text-gray-400"
                        >
                          {fmtTime(m.starts_at)} {m.title}
                        </button>
                      ))}
                      {list.length > 2 && <span className="text-[10px] text-gray-500">+{list.length - 2}</span>}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* Selected-day agenda (month/week) or full day view */}
      {(view === "month" || view === "week" || view === "day") && (
        <DayAgenda
          dateKey={view === "day" ? selected : selected}
          meetings={view === "day" ? meetings : selectedMeetings}
          loading={loading && view === "day"}
          onNew={() => openNew(selected)}
          onEdit={setEditingId}
          compact={view !== "day"}
        />
      )}

      {/* Agenda view */}
      {view === "agenda" && (
        <div className="space-y-2.5">
          {loading ? (
            <div className="bg-white dark:bg-gray-800 rounded-xl p-10 text-center">
              <div className="w-8 h-8 border-2 border-primary-600 border-t-transparent rounded-full animate-spin mx-auto" />
            </div>
          ) : agendaDays.length === 0 ? (
            <EmptyState onNew={() => openNew()} label="Nothing scheduled in the next 30 days." />
          ) : (
            agendaDays.map((key) => (
              <DayAgenda
                key={key}
                dateKey={key}
                meetings={byDay[key]}
                loading={false}
                onNew={() => openNew(key)}
                onEdit={setEditingId}
                compact
              />
            ))
          )}
        </div>
      )}

      {showNew && (
        <MeetingModal
          initial={{ date: newDate }}
          onClose={() => setShowNew(false)}
          onSaved={() => {
            setShowNew(false);
            load();
            api.get("/calendar/tags").then(({ data }) => setTags(data.items ?? [])).catch(() => {});
          }}
        />
      )}
      {editingId !== null && (
        <MeetingModal
          meetingId={editingId}
          onClose={() => setEditingId(null)}
          onSaved={() => {
            setEditingId(null);
            load();
          }}
        />
      )}
    </div>
  );
}

function EmptyState({ onNew, label }: { onNew: () => void; label: string }) {
  return (
    <div className="bg-white dark:bg-gray-800 rounded-xl p-10 text-center shadow-sm border border-gray-100 dark:border-gray-700">
      <CalendarIcon size={36} className="mx-auto text-primary-600 opacity-40 mb-2" />
      <p className="font-medium text-sm text-gray-900 dark:text-white">{label}</p>
      <button onClick={onNew} className="mt-3 bg-primary-600 text-white px-4 py-2 rounded-full text-sm font-medium">
        Book a meeting
      </button>
    </div>
  );
}

function DayAgenda({
  dateKey,
  meetings,
  loading,
  onNew,
  onEdit,
  compact,
}: {
  dateKey: string;
  meetings: Meeting[];
  loading: boolean;
  onNew: () => void;
  onEdit: (id: number) => void;
  compact: boolean;
}) {
  return (
    <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-100 dark:border-gray-700 overflow-hidden">
      <div className="px-3.5 py-2.5 flex items-center justify-between bg-gray-100 dark:bg-gray-900">
        <h3 className="font-semibold text-sm text-gray-900 dark:text-white">{fmtDay(dateKey)}</h3>
        <button
          onClick={onNew}
          className="text-xs bg-primary-600 text-white px-3 py-1.5 rounded-full font-medium flex items-center gap-1"
        >
          <Plus size={12} /> Book
        </button>
      </div>
      {loading ? (
        <div className="p-6 text-center">
          <div className="w-6 h-6 border-2 border-primary-600 border-t-transparent rounded-full animate-spin mx-auto" />
        </div>
      ) : meetings.length === 0 ? (
        <p className="px-4 py-5 text-center text-[13px] text-gray-500">
          Nothing here yet. <button onClick={onNew} className="text-primary-600 font-medium hover:underline">Book the first one</button>
        </p>
      ) : (
        <div className="divide-y divide-gray-100 dark:divide-gray-700">
          {meetings.map((m) => {
            const style = TYPE_STYLE[m.event_type] || TYPE_STYLE.other;
            return (
              <button
                key={m.id}
                onClick={() => onEdit(m.id)}
                className="w-full text-left p-3 flex gap-3 hover:bg-gray-50 dark:hover:bg-gray-900"
              >
                <div className={`w-1 rounded-full flex-shrink-0 ${style.dot}`} />
                <div className="flex-1 min-w-0">
                  <div className="flex items-start justify-between gap-2">
                    <p className={`font-semibold text-[14px] text-gray-900 dark:text-white ${m.status === "cancelled" ? "line-through opacity-60" : ""}`}>
                      {m.title}
                    </p>
                    <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium whitespace-nowrap flex-shrink-0 ${STATUS_STYLE[m.status] || STATUS_STYLE.scheduled}`}>
                      {m.status.replace("_", " ")}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 mt-1 text-xs text-gray-500 dark:text-gray-400 flex-wrap">
                    <span className="flex items-center gap-1 font-medium text-primary-600">
                      <Clock size={11} />
                      {m.all_day ? "All day" : `${fmtTime(m.starts_at)} – ${fmtTime(m.ends_at)}`}
                    </span>
                    <span className={`px-1.5 py-0.5 rounded-full text-[10px] font-medium ${style.chip}`}>{style.label}</span>
                    {m.send_sms_reminder && m.reminder_minutes.length > 0 && (
                      <span className="flex items-center gap-1 text-[11px]">
                        <Bell size={11} className="text-warning-500" />
                        {m.reminder_minutes.length} reminder{m.reminder_minutes.length > 1 ? "s" : ""}
                      </span>
                    )}
                  </div>
                  {(m.attendees || []).length > 0 && (
                    <p className="flex items-center gap-1.5 mt-1.5 text-xs text-gray-600 dark:text-gray-400">
                      <Users size={12} className="flex-shrink-0" />
                      <span className="truncate">
                        {(m.attendees || []).map((a) => a.name).join(", ")}
                      </span>
                    </p>
                  )}
                  {!compact && (
                    <>
                      {m.location && (
                        <p className="flex items-center gap-1.5 mt-1 text-xs text-gray-600 dark:text-gray-400">
                          <MapPin size={12} className="flex-shrink-0" /> {m.location}
                        </p>
                      )}
                      {m.meeting_link && (
                        <p className="flex items-center gap-1.5 mt-1 text-xs text-primary-600">
                          <Video size={12} className="flex-shrink-0" /> Video link attached
                        </p>
                      )}
                      {m.description && (
                        <p className="mt-1.5 text-xs text-gray-600 dark:text-gray-400 line-clamp-2">{m.description}</p>
                      )}
                      {(m.tags || []).length > 0 && (
                        <span className="flex gap-1 mt-1.5 flex-wrap">
                          {(m.tags || []).map((t) => (
                            <span key={t} className="text-[10px] px-1.5 py-0.5 rounded-full bg-primary-50 text-primary-600">
                              {t}
                            </span>
                          ))}
                        </span>
                      )}
                    </>
                  )}
                </div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
