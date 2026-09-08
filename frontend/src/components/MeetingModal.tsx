/**
 * MeetingModal — book or edit a meeting from the Calendar page or straight
 * from an inbox chat. Contacts, templates, shortcodes, tags and SMS
 * invites/reminders all live in this one form so booking never leaves context.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import api from "../api/client";
import toast from "react-hot-toast";
import {
  Bell, CalendarPlus, Check, Clock, Link2, MapPin, Send, Trash2, Users, X,
} from "lucide-react";
import ContactPicker from "./ContactPicker";
import TagPicker from "./TagPicker";
import TemplatePicker from "./TemplatePicker";
import ShortcodePicker from "./ShortcodePicker";
import type { Contact, Meeting } from "../types";

export interface MeetingInitial {
  contactIds?: number[];
  conversationId?: number;
  date?: string; // YYYY-MM-DD
  title?: string;
}

interface Props {
  meetingId?: number | null;
  initial?: MeetingInitial;
  onClose: () => void;
  onSaved: (meeting: Meeting) => void;
}

const EVENT_TYPES = [
  { v: "meeting", l: "Meeting" },
  { v: "call", l: "Call" },
  { v: "follow_up", l: "Follow-up" },
  { v: "reminder", l: "Reminder" },
  { v: "task", l: "Task" },
  { v: "other", l: "Other" },
];

const DURATIONS = [15, 30, 45, 60, 90, 120];

const REMINDER_PRESETS = [
  { v: 1440, l: "1 day before" },
  { v: 60, l: "1 hour before" },
  { v: 30, l: "30 min before" },
  { v: 15, l: "15 min before" },
];

const pad = (n: number) => String(n).padStart(2, "0");
const toDateInput = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const toTimeInput = (d: Date) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;

/** Naive client-side render for the instant preview (server re-renders at send). */
const renderPreview = (
  body: string,
  meeting: { title: string; date: string; time: string; location: string; link: string },
  contact: Contact | null
) => {
  const dt = new Date(`${meeting.date}T${meeting.time || "09:00"}:00`);
  const valid = !isNaN(dt.getTime());
  const values: Record<string, string> = {
    meeting_title: meeting.title || "Meeting",
    meeting_date: valid
      ? dt.toLocaleDateString(undefined, { weekday: "short", day: "2-digit", month: "short", year: "numeric" })
      : meeting.date,
    meeting_time: valid
      ? dt.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })
      : meeting.time,
    meeting_datetime: valid ? dt.toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }) : "",
    meeting_location: meeting.location ? `at ${meeting.location}.` : "",
    meeting_link: meeting.link || "",
    meeting_when: "soon",
    first_name: contact?.first_name || contact?.business_name || "there",
    last_name: contact?.last_name || "",
    full_name:
      `${contact?.first_name || ""} ${contact?.last_name || ""}`.trim() ||
      contact?.business_name ||
      "there",
    business_name: contact?.business_name || "",
    phone_number: contact?.phone_number || "",
    city: contact?.city || "",
    state: contact?.state || "",
  };
  return body
    .replace(/\{\{\s*([A-Za-z0-9 _.\-]+?)\s*(?:\|([^}]*))?\}\}/g, (_m, raw: string, fb: string | undefined) => {
      const key = raw.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
      const hit = values[key];
      if (hit) return hit;
      if (fb !== undefined) return fb.trim();
      return "";
    })
    .replace(/[ \t]+([,.!?;:])/g, "$1")
    .replace(/[ \t]{2,}/g, " ")
    .trim();
};

export default function MeetingModal({ meetingId = null, initial, onClose, onSaved }: Props) {
  const editing = meetingId != null;
  const [loading, setLoading] = useState(editing);
  const [saving, setSaving] = useState(false);
  const [actionBusy, setActionBusy] = useState("");

  const [title, setTitle] = useState(initial?.title || "Meeting");
  const [eventType, setEventType] = useState("meeting");
  const [date, setDate] = useState(initial?.date || toDateInput(new Date(Date.now() + 86400000)));
  const [time, setTime] = useState("09:00");
  const [duration, setDuration] = useState(30);
  const [allDay, setAllDay] = useState(false);
  const [contactIds, setContactIds] = useState<number[]>(initial?.contactIds || []);
  const [location, setLocation] = useState("");
  const [link, setLink] = useState("");
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState<string[]>([]);

  const [sendInvite, setSendInvite] = useState(true);
  const [inviteTemplateId, setInviteTemplateId] = useState("");
  const [inviteBody, setInviteBody] = useState("");
  const inviteRef = useRef<HTMLTextAreaElement>(null);

  const [sendReminder, setSendReminder] = useState(true);
  const [reminderMinutes, setReminderMinutes] = useState<number[]>([60, 15]);
  const [customMinutes, setCustomMinutes] = useState("");
  const [reminderTemplateId, setReminderTemplateId] = useState("");
  const [reminderBody, setReminderBody] = useState("");
  const reminderRef = useRef<HTMLTextAreaElement>(null);

  const [status, setStatus] = useState("scheduled");
  const [previewContact, setPreviewContact] = useState<Contact | null>(null);
  const [inviteTemplates, setInviteTemplates] = useState<Record<string, string>>({});
  const [showAdvanced, setShowAdvanced] = useState(false);

  // Load an existing meeting for editing.
  useEffect(() => {
    if (!meetingId) return;
    let cancelled = false;
    setLoading(true);
    api
      .get(`/calendar/${meetingId}`)
      .then(({ data }: { data: Meeting }) => {
        if (cancelled) return;
        const start = new Date(data.starts_at);
        const end = new Date(data.ends_at);
        setTitle(data.title);
        setEventType(data.event_type);
        setDate(toDateInput(start));
        setTime(toTimeInput(start));
        setDuration(Math.max(5, Math.round((end.getTime() - start.getTime()) / 60000)));
        setAllDay(data.all_day);
        setContactIds((data.attendees || []).map((a) => a.contact_id));
        setLocation(data.location || "");
        setLink(data.meeting_link || "");
        setDescription(data.description || "");
        setTags(data.tags || []);
        setSendInvite(data.send_invite_sms);
        setInviteTemplateId(data.invite_template_id ? String(data.invite_template_id) : "");
        setInviteBody(data.invite_body || "");
        setSendReminder(data.send_sms_reminder);
        setReminderMinutes(data.reminder_minutes || []);
        setReminderTemplateId(data.reminder_template_id ? String(data.reminder_template_id) : "");
        setReminderBody(data.reminder_body || "");
        setStatus(data.status);
        if (data.invite_body || data.reminder_body || data.invite_template_id || data.reminder_template_id) {
          setShowAdvanced(true);
        }
      })
      .catch(() => {
        if (!cancelled) toast.error("Could not load this meeting");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [meetingId]);

  // First attendee powers the "Preview for …" line.
  useEffect(() => {
    if (contactIds.length === 0) {
      setPreviewContact(null);
      return;
    }
    let cancelled = false;
    api
      .get(`/contacts/${contactIds[0]}`)
      .then(({ data }) => {
        if (!cancelled) setPreviewContact(data as Contact);
      })
      .catch(() => {
        if (!cancelled) setPreviewContact(null);
      });
    return () => {
      cancelled = true;
    };
  }, [contactIds.join(",")]);  

  // Template bodies for the instant preview (without another picker fetch).
  useEffect(() => {
    const ids = [inviteTemplateId, reminderTemplateId].filter(Boolean).filter((id) => !inviteTemplates[id]);
    if (ids.length === 0) return;
    let cancelled = false;
    Promise.all(ids.map((id) => api.get(`/templates/${id}`).then((r) => r.data).catch(() => null))).then(
      (tpls) => {
        if (cancelled) return;
        setInviteTemplates((prev) => {
          const next = { ...prev };
          tpls.forEach((t: any) => {
            if (t) next[String(t.id)] = t.body;
          });
          return next;
        });
      }
    );
    return () => {
      cancelled = true;
    };
     
  }, [inviteTemplateId, reminderTemplateId]);

  const toggleReminderMinute = (v: number) =>
    setReminderMinutes((prev) => (prev.includes(v) ? prev.filter((m) => m !== v) : [...prev, v]));

  const addCustomMinutes = () => {
    const v = parseInt(customMinutes, 10);
    if (!v || v < 1 || v > 10080) {
      toast.error("Enter 1 – 10080 minutes");
      return;
    }
    if (!reminderMinutes.includes(v)) setReminderMinutes((prev) => [...prev, v]);
    setCustomMinutes("");
  };

  const meetingForPreview = useMemo(
    () => ({ title, date, time, location, link }),
    [title, date, time, location, link]
  );
  const inviteSource = inviteBody.trim() || (inviteTemplateId && inviteTemplates[inviteTemplateId]) || "";
  const reminderSource =
    reminderBody.trim() || (reminderTemplateId && inviteTemplates[reminderTemplateId]) || "";
  const invitePreview = inviteSource ? renderPreview(inviteSource, meetingForPreview, previewContact) : "";
  const reminderPreview = reminderSource ? renderPreview(reminderSource, meetingForPreview, previewContact) : "";

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim()) {
      toast.error("Give the meeting a title");
      return;
    }
    if (!date) {
      toast.error("Pick a date");
      return;
    }
    if (contactIds.length === 0) {
      toast.error("Select at least one contact");
      return;
    }
    setSaving(true);
    try {
      const startsAt = allDay ? new Date(`${date}T00:00:00`) : new Date(`${date}T${time || "09:00"}:00`);
      if (isNaN(startsAt.getTime())) {
        toast.error("That date/time is not valid");
        setSaving(false);
        return;
      }
      const endsAt = allDay
        ? new Date(`${date}T23:59:00`)
        : new Date(startsAt.getTime() + Math.max(5, duration) * 60000);
      const payload: Record<string, unknown> = {
        title: title.trim(),
        description: description.trim() || null,
        event_type: eventType,
        starts_at: startsAt.toISOString(),
        ends_at: endsAt.toISOString(),
        all_day: allDay,
        location: location.trim() || null,
        meeting_link: link.trim() || null,
        contact_ids: contactIds,
        tags,
        send_invite_sms: sendInvite,
        invite_template_id: inviteTemplateId ? parseInt(inviteTemplateId, 10) : null,
        invite_body: inviteBody.trim() || null,
        send_sms_reminder: sendReminder,
        reminder_minutes: sendReminder ? reminderMinutes : [],
        reminder_template_id: reminderTemplateId ? parseInt(reminderTemplateId, 10) : null,
        reminder_body: reminderBody.trim() || null,
      };
      if (!editing && initial?.conversationId) payload.conversation_id = initial.conversationId;

      const { data } = editing
        ? await api.put(`/calendar/${meetingId}`, payload)
        : await api.post("/calendar/", payload);
      const invite = (data as any).invite;
      if (!editing && invite && sendInvite) {
        if (invite.sent > 0) toast.success(`Booked · invite sent to ${invite.sent} contact${invite.sent > 1 ? "s" : ""}`);
        else if (invite.error) toast.success("Booked · invite failed, resend it from the calendar");
        else toast.success("Booked");
      } else {
        toast.success(editing ? "Meeting updated" : "Meeting booked");
      }
      onSaved(data as Meeting);
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not save the meeting");
    } finally {
      setSaving(false);
    }
  };

  const runAction = async (action: string, label: string) => {
    if (!meetingId) return;
    setActionBusy(action);
    try {
      const { data } = await api.post(`/calendar/${meetingId}/${action}`);
      if (action === "send-invite" || action === "send-reminder") {
        toast.success(`${label}: sent to ${data.sent}, skipped ${data.skipped}`);
      } else {
        toast.success(label);
        setStatus(data.status || status);
      }
      onSaved(data as Meeting);
    } catch (err: any) {
      toast.error(err.response?.data?.detail || `${label} failed`);
    } finally {
      setActionBusy("");
    }
  };

  const remove = async () => {
    if (!meetingId || !window.confirm("Delete this meeting? Attendees are not notified.")) return;
    setActionBusy("delete");
    try {
      await api.delete(`/calendar/${meetingId}`);
      toast.success("Meeting deleted");
      onSaved({ id: meetingId } as Meeting);
      onClose();
    } catch {
      toast.error("Could not delete");
    } finally {
      setActionBusy("");
    }
  };

  const statusPill = (s: string) => {
    const map: Record<string, string> = {
      scheduled: "bg-[#e7f3ff] text-[#0066cc]",
      confirmed: "bg-[#d9fdd3] text-[#008069]",
      completed: "bg-[#f0f2f5] text-[#54656f]",
      cancelled: "bg-[#fce8e6] text-[#c5221f]",
      no_show: "bg-[#ffecb3] text-[#8d5100]",
    };
    return map[s] || "bg-[#f0f2f5] text-[#54656f]";
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/50 p-0 sm:p-4">
      <div className="bg-white dark:bg-[#202c33] w-full sm:max-w-2xl max-h-[94vh] overflow-y-auto rounded-t-[20px] sm:rounded-2xl">
        <div className="sticky top-0 bg-[#008069] dark:bg-[#202c33] px-4 py-3 flex items-center justify-between z-10">
          <h2 className="text-white font-semibold flex items-center gap-2">
            <CalendarPlus size={18} /> {editing ? "Edit meeting" : "Book a meeting"}
            {editing && (
              <span className={`text-[11px] px-2 py-0.5 rounded-full font-medium ${statusPill(status)}`}>
                {status.replace("_", " ")}
              </span>
            )}
          </h2>
          <button onClick={onClose} aria-label="Close" className="w-8 h-8 rounded-full bg-white/10 flex items-center justify-center text-white">
            <X size={16} />
          </button>
        </div>

        {loading ? (
          <div className="p-10 text-center">
            <div className="w-8 h-8 border-2 border-[#00a884] border-t-transparent rounded-full animate-spin mx-auto" />
          </div>
        ) : (
          <form onSubmit={submit} className="p-4 sm:p-6 space-y-4">
            {/* Title + type */}
            <div>
              <label className="text-xs font-medium text-[#54656f]">Title *</label>
              <input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="Site visit, Demo call, …"
                autoFocus={!editing}
                className="mt-1 w-full px-3 py-2.5 bg-[#f0f2f5] dark:bg-[#111b21] rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-[#00a884]/20 text-[#111b21] dark:text-white"
              />
              <div className="flex gap-1.5 mt-2 flex-wrap">
                {EVENT_TYPES.map((t) => (
                  <button
                    key={t.v}
                    type="button"
                    onClick={() => setEventType(t.v)}
                    className={`px-3 py-1.5 rounded-full text-xs font-medium ${
                      eventType === t.v
                        ? "bg-[#00a884] text-white"
                        : "bg-[#f0f2f5] dark:bg-[#111b21] text-[#54656f] dark:text-[#8696a0]"
                    }`}
                  >
                    {t.l}
                  </button>
                ))}
              </div>
            </div>

            {/* Date / time / duration */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5">
              <div className="col-span-2 sm:col-span-1">
                <label className="text-xs font-medium text-[#54656f]">Date *</label>
                <input
                  type="date"
                  value={date}
                  onChange={(e) => setDate(e.target.value)}
                  className="mt-1 w-full px-3 py-2.5 bg-[#f0f2f5] dark:bg-[#111b21] rounded-xl text-sm text-[#111b21] dark:text-white"
                />
              </div>
              <div>
                <label className="text-xs font-medium text-[#54656f]">Starts</label>
                <input
                  type="time"
                  value={time}
                  disabled={allDay}
                  onChange={(e) => setTime(e.target.value)}
                  className="mt-1 w-full px-3 py-2.5 bg-[#f0f2f5] dark:bg-[#111b21] rounded-xl text-sm text-[#111b21] dark:text-white disabled:opacity-50"
                />
              </div>
              <div>
                <label className="text-xs font-medium text-[#54656f]">Length</label>
                <select
                  value={duration}
                  disabled={allDay}
                  onChange={(e) => setDuration(parseInt(e.target.value, 10))}
                  className="mt-1 w-full px-3 py-2.5 bg-[#f0f2f5] dark:bg-[#111b21] rounded-xl text-sm text-[#111b21] dark:text-white disabled:opacity-50"
                >
                  {DURATIONS.map((d) => (
                    <option key={d} value={d}>
                      {d >= 60 ? `${d / 60} hr${d > 60 ? "s" : ""}` : `${d} min`}
                    </option>
                  ))}
                  {!DURATIONS.includes(duration) && <option value={duration}>{duration} min</option>}
                </select>
              </div>
              <div className="flex items-end pb-1">
                <label className="flex items-center gap-2 text-xs text-[#54656f] cursor-pointer select-none">
                  <input type="checkbox" checked={allDay} onChange={(e) => setAllDay(e.target.checked)} className="rounded accent-[#00a884] w-4 h-4" />
                  All day
                </label>
              </div>
            </div>

            {/* Contacts */}
            <div>
              <label className="text-xs font-medium text-[#54656f] flex items-center gap-1">
                <Users size={12} /> Contacts * <span className="font-normal">(first is the primary)</span>
              </label>
              <div className="mt-1">
                <ContactPicker value={contactIds} onChange={setContactIds} placeholder="Search and add contacts…" />
              </div>
            </div>

            {/* Location / link / notes */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
              <div>
                <label className="text-xs font-medium text-[#54656f] flex items-center gap-1">
                  <MapPin size={12} /> Location
                </label>
                <input
                  value={location}
                  onChange={(e) => setLocation(e.target.value)}
                  placeholder="12 Allen Avenue, Ikeja"
                  className="mt-1 w-full px-3 py-2.5 bg-[#f0f2f5] dark:bg-[#111b21] rounded-xl text-sm text-[#111b21] dark:text-white"
                />
              </div>
              <div>
                <label className="text-xs font-medium text-[#54656f] flex items-center gap-1">
                  <Link2 size={12} /> Meeting link
                </label>
                <input
                  value={link}
                  onChange={(e) => setLink(e.target.value)}
                  placeholder="https://meet…"
                  className="mt-1 w-full px-3 py-2.5 bg-[#f0f2f5] dark:bg-[#111b21] rounded-xl text-sm text-[#111b21] dark:text-white"
                />
              </div>
            </div>
            <div>
              <label className="text-xs font-medium text-[#54656f]">Notes</label>
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={2}
                placeholder="Agenda, things to bring, …"
                className="mt-1 w-full px-3 py-2.5 bg-[#f0f2f5] dark:bg-[#111b21] rounded-xl text-sm text-[#111b21] dark:text-white"
              />
            </div>
            <div>
              <label className="text-xs font-medium text-[#54656f]">Tags</label>
              <div className="mt-1">
                <TagPicker value={tags} onChange={setTags} suggestionsUrl="/calendar/tags" placeholder="vip, lagos, …" />
              </div>
            </div>

            {/* SMS invite + reminders */}
            <div className="border-t border-gray-100 dark:border-[#2a3942] pt-3 space-y-3">
              <button
                type="button"
                onClick={() => setShowAdvanced(!showAdvanced)}
                className="w-full flex items-center justify-between text-sm font-medium text-[#111b21] dark:text-white"
              >
                <span className="flex items-center gap-2">
                  <Send size={15} className="text-[#00a884]" /> SMS invite & reminders
                </span>
                <span className="text-xs text-[#667781]">{showAdvanced ? "Hide ▲" : "Show ▼"}</span>
              </button>

              {showAdvanced && (
                <>
                  <label className="flex items-center gap-2 text-sm cursor-pointer select-none">
                    <input type="checkbox" checked={sendInvite} onChange={(e) => setSendInvite(e.target.checked)} className="rounded accent-[#00a884] w-4 h-4" />
                    <span className="text-[#111b21] dark:text-white font-medium">Text the invite on booking</span>
                  </label>
                  {sendInvite && (
                    <div className="space-y-2 pl-1">
                      <TemplatePicker value={inviteTemplateId} onChange={setInviteTemplateId} placeholder="Invite template (optional)…" showPreview={false} />
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-[#667781]">or custom message</span>
                        <ShortcodePicker targetRef={inviteRef} value={inviteBody} onChange={setInviteBody} label="Shortcode" />
                      </div>
                      <textarea
                        ref={inviteRef}
                        value={inviteBody}
                        onChange={(e) => setInviteBody(e.target.value)}
                        rows={2}
                        placeholder="Hi {{first_name}}, you're booked: {{meeting_title}} on {{meeting_date}} at {{meeting_time}} {{meeting_location}}"
                        className="w-full px-3 py-2.5 bg-[#f0f2f5] dark:bg-[#111b21] rounded-xl text-sm font-mono text-[#111b21] dark:text-white"
                      />
                      {invitePreview && (
                        <p className="text-xs bg-[#d9fdd3]/60 dark:bg-[#0a332c] rounded-xl px-3 py-2 text-[#0a332c] dark:text-[#e9edef]">
                          <b>Preview{previewContact ? ` for ${previewContact.first_name || "contact"}` : ""}:</b> {invitePreview}
                        </p>
                      )}
                      {!inviteSource && (
                        <p className="text-[11px] text-[#667781]">Empty = the default invite text (personalized automatically).</p>
                      )}
                    </div>
                  )}

                  <label className="flex items-center gap-2 text-sm cursor-pointer select-none">
                    <input type="checkbox" checked={sendReminder} onChange={(e) => setSendReminder(e.target.checked)} className="rounded accent-[#00a884] w-4 h-4" />
                    <span className="text-[#111b21] dark:text-white font-medium flex items-center gap-1.5">
                      <Bell size={14} className="text-[#ffad1f]" /> Send SMS reminders
                    </span>
                  </label>
                  {sendReminder && (
                    <div className="space-y-2 pl-1">
                      <div className="flex gap-1.5 flex-wrap">
                        {REMINDER_PRESETS.map((p) => (
                          <button
                            key={p.v}
                            type="button"
                            onClick={() => toggleReminderMinute(p.v)}
                            className={`px-2.5 py-1.5 rounded-full text-xs font-medium flex items-center gap-1 ${
                              reminderMinutes.includes(p.v)
                                ? "bg-[#ffad1f] text-white"
                                : "bg-[#f0f2f5] dark:bg-[#111b21] text-[#54656f] dark:text-[#8696a0]"
                            }`}
                          >
                            {reminderMinutes.includes(p.v) && <Check size={11} />}
                            <Clock size={11} /> {p.l}
                          </button>
                        ))}
                      </div>
                      <div className="flex gap-1.5">
                        <input
                          value={customMinutes}
                          onChange={(e) => setCustomMinutes(e.target.value)}
                          placeholder="Custom minutes before…"
                          inputMode="numeric"
                          className="flex-1 px-3 py-2 bg-[#f0f2f5] dark:bg-[#111b21] rounded-xl text-xs text-[#111b21] dark:text-white"
                        />
                        <button type="button" onClick={addCustomMinutes} className="px-3 py-2 rounded-xl bg-[#f0f2f5] dark:bg-[#111b21] text-xs font-medium text-[#54656f]">
                          Add
                        </button>
                      </div>
                      {reminderMinutes.filter((m) => !REMINDER_PRESETS.some((p) => p.v === m)).length > 0 && (
                        <div className="flex gap-1.5 flex-wrap">
                          {reminderMinutes
                            .filter((m) => !REMINDER_PRESETS.some((p) => p.v === m))
                            .map((m) => (
                              <button
                                key={m}
                                type="button"
                                onClick={() => toggleReminderMinute(m)}
                                className="px-2.5 py-1 rounded-full text-xs bg-[#ffad1f] text-white"
                                title="Tap to remove"
                              >
                                {m} min ✕
                              </button>
                            ))}
                        </div>
                      )}
                      <TemplatePicker value={reminderTemplateId} onChange={setReminderTemplateId} placeholder="Reminder template (optional)…" showPreview={false} />
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-[#667781]">or custom message</span>
                        <ShortcodePicker targetRef={reminderRef} value={reminderBody} onChange={setReminderBody} label="Shortcode" />
                      </div>
                      <textarea
                        ref={reminderRef}
                        value={reminderBody}
                        onChange={(e) => setReminderBody(e.target.value)}
                        rows={2}
                        placeholder="Hi {{first_name}}, reminder: {{meeting_title}} {{meeting_when}} ({{meeting_date}} at {{meeting_time}})"
                        className="w-full px-3 py-2.5 bg-[#f0f2f5] dark:bg-[#111b21] rounded-xl text-sm font-mono text-[#111b21] dark:text-white"
                      />
                      {reminderPreview && (
                        <p className="text-xs bg-[#fff8c4] dark:bg-[#2a3942] rounded-xl px-3 py-2 text-[#5c4b00] dark:text-[#e9edef]">
                          <b>Preview{previewContact ? ` for ${previewContact.first_name || "contact"}` : ""}:</b> {reminderPreview}
                        </p>
                      )}
                    </div>
                  )}
                </>
              )}
            </div>

            {/* Edit-mode actions */}
            {editing && (
              <div className="border-t border-gray-100 dark:border-[#2a3942] pt-3">
                <p className="text-xs font-medium text-[#54656f] mb-2">Update this meeting</p>
                <div className="flex gap-1.5 flex-wrap">
                  {status !== "confirmed" && status !== "completed" && (
                    <button type="button" disabled={!!actionBusy} onClick={() => runAction("confirm", "Confirmed")} className="px-3 py-2 rounded-full bg-[#d9fdd3] text-[#008069] text-xs font-semibold disabled:opacity-50">
                      {actionBusy === "confirm" ? "…" : "✓ Confirm"}
                    </button>
                  )}
                  {status !== "completed" && (
                    <button type="button" disabled={!!actionBusy} onClick={() => runAction("complete", "Marked complete")} className="px-3 py-2 rounded-full bg-[#00a884] text-white text-xs font-semibold disabled:opacity-50">
                      {actionBusy === "complete" ? "…" : "✓ Complete"}
                    </button>
                  )}
                  {status !== "cancelled" && status !== "completed" && (
                    <>
                      <button type="button" disabled={!!actionBusy} onClick={() => runAction("no-show", "Marked no-show")} className="px-3 py-2 rounded-full bg-[#ffecb3] text-[#8d5100] text-xs font-semibold disabled:opacity-50">
                        {actionBusy === "no-show" ? "…" : "No-show"}
                      </button>
                      <button type="button" disabled={!!actionBusy} onClick={() => runAction("cancel", "Cancelled")} className="px-3 py-2 rounded-full bg-[#fce8e6] text-[#c5221f] text-xs font-semibold disabled:opacity-50">
                        {actionBusy === "cancel" ? "…" : "Cancel"}
                      </button>
                    </>
                  )}
                </div>
                <div className="flex gap-1.5 flex-wrap mt-2">
                  <button type="button" disabled={!!actionBusy} onClick={() => runAction("send-invite", "Invite sent")} className="px-3 py-2 rounded-full bg-[#e7f3ff] text-[#0066cc] text-xs font-semibold disabled:opacity-50">
                    {actionBusy === "send-invite" ? "Sending…" : "↻ Resend invite"}
                  </button>
                  <button type="button" disabled={!!actionBusy} onClick={() => runAction("send-reminder", "Reminder sent")} className="px-3 py-2 rounded-full bg-[#e7f3ff] text-[#0066cc] text-xs font-semibold disabled:opacity-50">
                    {actionBusy === "send-reminder" ? "Sending…" : "🔔 Remind now"}
                  </button>
                  <button type="button" disabled={!!actionBusy} onClick={remove} className="px-3 py-2 rounded-full bg-transparent border border-red-200 text-[#c5221f] text-xs font-semibold disabled:opacity-50 flex items-center gap-1">
                    <Trash2 size={12} /> Delete
                  </button>
                </div>
              </div>
            )}

            <div className="flex gap-2 pt-1">
              <button type="button" onClick={onClose} className="flex-1 py-3 rounded-full bg-[#f0f2f5] dark:bg-[#111b21] text-[#54656f] dark:text-white font-medium">
                Cancel
              </button>
              <button type="submit" disabled={saving} className="flex-1 py-3 rounded-full bg-[#00a884] text-white font-semibold disabled:opacity-50">
                {saving ? "Saving…" : editing ? "Save changes" : "Book meeting"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
