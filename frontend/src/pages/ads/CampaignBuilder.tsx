import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import adsApi from "../../api/ads";
import { Field, Modal, fromLocalInput, toLocalInput } from "./ui";

/**
 * Create / edit a campaign. Every field here maps to a real backend setting --
 * objective, SMS budget, schedule, drip pacing, frequency caps, optimization
 * and test mode.
 */
export default function CampaignBuilder({
  campaign,
  objectives,
  close,
  saved,
}: {
  campaign?: any;
  objectives: { value: string; label: string }[];
  close: () => void;
  saved: (id: number) => void;
}) {
  const [form, setForm] = useState<any>({
    name: "",
    description: "",
    objective: "replies",
    daily_limit: 200,
    total_limit: "",
    start_date: "",
    end_date: "",
    send_start_hour: 9,
    send_end_hour: 18,
    send_days: "0,1,2,3,4",
    drip_mode: "off",
    drip_batch_size: 50,
    drip_interval_minutes: 60,
    pacing: "even",
    continuous: true,
    always_on: false,
    max_per_contact_per_day: "",
    max_per_contact_per_week: "",
    optimization_mode: "manual",
    queued_edit_policy: "keep",
    priority: "normal",
    test_mode: false,
    ...(campaign || {}),
  });
  const [saving, setSaving] = useState(false);
  const set = (k: string, v: any) => setForm((f: any) => ({ ...f, [k]: v }));

  useEffect(() => {
    if (campaign) {
      setForm({
        ...campaign,
        start_date: toLocalInput(campaign.start_date),
        end_date: toLocalInput(campaign.end_date),
        total_limit: campaign.total_limit ?? "",
        max_per_contact_per_day: campaign.max_per_contact_per_day ?? "",
        max_per_contact_per_week: campaign.max_per_contact_per_week ?? "",
      });
    }
  }, [campaign?.id]);

  const num = (v: any) => (v === "" || v === null || v === undefined ? null : Number(v));

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.name.trim()) return toast.error("Give the campaign a name");
    const payload = {
      name: form.name.trim(),
      description: form.description || null,
      objective: form.objective,
      daily_limit: num(form.daily_limit),
      total_limit: num(form.total_limit),
      start_date: fromLocalInput(form.start_date),
      end_date: fromLocalInput(form.end_date),
      send_start_hour: num(form.send_start_hour),
      send_end_hour: num(form.send_end_hour),
      send_days: form.send_days || null,
      drip_mode: form.drip_mode,
      drip_batch_size: Number(form.drip_batch_size) || 1,
      drip_interval_minutes: Number(form.drip_interval_minutes) || 0,
      pacing: form.pacing,
      continuous: !!form.continuous,
      always_on: !!form.always_on,
      max_per_contact_per_day: num(form.max_per_contact_per_day),
      max_per_contact_per_week: num(form.max_per_contact_per_week),
      optimization_mode: form.optimization_mode,
      queued_edit_policy: form.queued_edit_policy,
      priority: form.priority,
      test_mode: !!form.test_mode,
    };
    setSaving(true);
    try {
      const result = campaign
        ? await adsApi.updateCampaign(campaign.id, payload)
        : await adsApi.createCampaign(payload);
      toast.success(campaign ? "Campaign updated" : "Campaign created");
      saved(result.id);
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not save campaign");
    } finally {
      setSaving(false);
    }
  };

  const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const selectedDays = (form.send_days || "").split(",").filter(Boolean);
  const toggleDay = (i: number) => {
    const key = String(i);
    const next = selectedDays.includes(key)
      ? selectedDays.filter((d: string) => d !== key)
      : [...selectedDays, key].sort();
    set("send_days", next.join(","));
  };

  return (
    <Modal title={campaign ? "Edit campaign" : "New campaign"} close={close} wide>
      <form onSubmit={submit} className="space-y-5">
        <section className="space-y-3">
          <h3 className="font-semibold text-sm text-gray-500 uppercase tracking-wide">Campaign</h3>
          <Field label="Name">
            <input
              className="input"
              value={form.name}
              onChange={(e) => set("name", e.target.value)}
              placeholder="Lagos e-commerce outreach"
              autoFocus
            />
          </Field>
          <Field label="Description">
            <textarea
              className="input"
              rows={2}
              value={form.description || ""}
              onChange={(e) => set("description", e.target.value)}
            />
          </Field>
          <Field label="Objective" hint="Decides which metrics are highlighted and how creatives are scored.">
            <select className="input" value={form.objective} onChange={(e) => set("objective", e.target.value)}>
              {objectives.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </Field>
        </section>

        <section className="space-y-3">
          <h3 className="font-semibold text-sm text-gray-500 uppercase tracking-wide">SMS budget</h3>
          <div className="grid sm:grid-cols-2 gap-3">
            <Field label="Daily SMS limit" hint="Leave blank to send with no daily cap.">
              <input
                type="number"
                min={1}
                className="input"
                value={form.daily_limit ?? ""}
                onChange={(e) => set("daily_limit", e.target.value)}
              />
            </Field>
            <Field label="Total SMS limit" hint="Campaign completes once this many are sent.">
              <input
                type="number"
                min={1}
                className="input"
                value={form.total_limit}
                onChange={(e) => set("total_limit", e.target.value)}
              />
            </Field>
          </div>
        </section>

        <section className="space-y-3">
          <h3 className="font-semibold text-sm text-gray-500 uppercase tracking-wide">Schedule</h3>
          <div className="grid sm:grid-cols-2 gap-3">
            <Field label="Start">
              <input
                type="datetime-local"
                className="input"
                value={form.start_date}
                onChange={(e) => set("start_date", e.target.value)}
              />
            </Field>
            <Field label="End">
              <input
                type="datetime-local"
                className="input"
                value={form.end_date}
                onChange={(e) => set("end_date", e.target.value)}
              />
            </Field>
            <Field label="Sending window starts">
              <input
                type="number"
                min={0}
                max={23}
                className="input"
                value={form.send_start_hour ?? ""}
                onChange={(e) => set("send_start_hour", e.target.value)}
              />
            </Field>
            <Field label="Sending window ends">
              <input
                type="number"
                min={0}
                max={23}
                className="input"
                value={form.send_end_hour ?? ""}
                onChange={(e) => set("send_end_hour", e.target.value)}
              />
            </Field>
          </div>
          <div>
            <span className="label">Sending days</span>
            <div className="flex flex-wrap gap-2">
              {days.map((d, i) => (
                <button
                  type="button"
                  key={d}
                  onClick={() => toggleDay(i)}
                  className={`px-3 py-1.5 rounded-lg text-sm border ${
                    selectedDays.includes(String(i))
                      ? "bg-primary-600 text-white border-primary-600"
                      : "border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300"
                  }`}
                >
                  {d}
                </button>
              ))}
            </div>
          </div>
        </section>

        <section className="space-y-3">
          <h3 className="font-semibold text-sm text-gray-500 uppercase tracking-wide">Drip &amp; pacing</h3>
          <Field label="Drip mode">
            <select className="input" value={form.drip_mode} onChange={(e) => set("drip_mode", e.target.value)}>
              <option value="off">Off — send as fast as limits allow</option>
              <option value="interval">One message every N minutes</option>
              <option value="batch">A batch every N minutes</option>
              <option value="daily">Daily limit only</option>
              <option value="smart">Smart pacing across the window</option>
            </select>
          </Field>
          {(form.drip_mode === "interval" || form.drip_mode === "batch" || form.drip_mode === "smart") && (
            <div className="grid sm:grid-cols-2 gap-3">
              {form.drip_mode !== "interval" && (
                <Field label="Batch size">
                  <input
                    type="number"
                    min={1}
                    className="input"
                    value={form.drip_batch_size}
                    onChange={(e) => set("drip_batch_size", e.target.value)}
                  />
                </Field>
              )}
              <Field label="Interval (minutes)">
                <input
                  type="number"
                  min={0}
                  className="input"
                  value={form.drip_interval_minutes}
                  onChange={(e) => set("drip_interval_minutes", e.target.value)}
                />
              </Field>
            </div>
          )}
          {form.drip_mode === "smart" && (
            <Field label="Distribution">
              <select className="input" value={form.pacing} onChange={(e) => set("pacing", e.target.value)}>
                <option value="even">Even</option>
                <option value="front">Front loaded</option>
                <option value="back">Back loaded</option>
                <option value="random">Randomised</option>
              </select>
            </Field>
          )}
          <div className="grid sm:grid-cols-2 gap-3">
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={!!form.continuous}
                onChange={(e) => set("continuous", e.target.checked)}
              />
              Continuous — keep sending day after day until the audience is finished
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={!!form.always_on}
                onChange={(e) => set("always_on", e.target.checked)}
              />
              Always on — automatically pull in new matching contacts
            </label>
          </div>
        </section>

        <section className="space-y-3">
          <h3 className="font-semibold text-sm text-gray-500 uppercase tracking-wide">Safety</h3>
          <div className="grid sm:grid-cols-2 gap-3">
            <Field label="Max messages per contact / day">
              <input
                type="number"
                min={1}
                className="input"
                value={form.max_per_contact_per_day}
                onChange={(e) => set("max_per_contact_per_day", e.target.value)}
              />
            </Field>
            <Field label="Max messages per contact / week">
              <input
                type="number"
                min={1}
                className="input"
                value={form.max_per_contact_per_week}
                onChange={(e) => set("max_per_contact_per_week", e.target.value)}
              />
            </Field>
            <Field label="Optimization">
              <select
                className="input"
                value={form.optimization_mode}
                onChange={(e) => set("optimization_mode", e.target.value)}
              >
                <option value="manual">Manual</option>
                <option value="recommend">Recommendations only</option>
                <option value="auto">Automatic</option>
              </select>
            </Field>
            <Field
              label="If a creative is edited mid-flight"
              hint="Default keeps the version each queued contact was assigned."
            >
              <select
                className="input"
                value={form.queued_edit_policy}
                onChange={(e) => set("queued_edit_policy", e.target.value)}
              >
                <option value="keep">Keep the assigned version</option>
                <option value="update">Update queued messages</option>
              </select>
            </Field>
            <Field label="Sending priority">
              <select className="input" value={form.priority} onChange={(e) => set("priority", e.target.value)}>
                <option value="high">High</option>
                <option value="normal">Normal</option>
                <option value="low">Low</option>
              </select>
            </Field>
          </div>
          <label className="flex items-center gap-2 text-sm p-3 rounded-lg bg-amber-50 dark:bg-amber-900/20">
            <input type="checkbox" checked={!!form.test_mode} onChange={(e) => set("test_mode", e.target.checked)} />
            <span>
              <b>Test mode</b> — simulate everything (assignment, split, queue, limits, follow-ups) without
              sending real SMS or using credits.
            </span>
          </label>
        </section>

        <div className="flex gap-2 pt-1">
          <button type="button" className="btn-secondary flex-1" onClick={close}>
            Cancel
          </button>
          <button className="btn-primary flex-1" disabled={saving}>
            {saving ? "Saving…" : campaign ? "Save changes" : "Create campaign"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
