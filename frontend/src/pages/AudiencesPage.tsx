import { useCallback, useEffect, useMemo, useState } from "react";
import toast from "react-hot-toast";
import { Copy, Eye, Megaphone, Pencil, Plus, Search, Trash2, Users } from "lucide-react";
import adsApi, { AdsAudience } from "../api/ads";
import ContactPicker from "../components/ContactPicker";
import { Empty, Field, Metric, Modal, Stat, fmtDay } from "./ads/ui";

const csvList = (raw?: string | null) => (raw || "").split(",").map((x) => x.trim()).filter(Boolean);

/**
 * AUDIENCES — Meta-style saved audiences.
 *
 * Build a reusable audience once (lists + individual contacts + filters),
 * see its live size, then attach it to any campaign's SMS set with one click.
 */
export default function AudiencesPage() {
  const [audiences, setAudiences] = useState<AdsAudience[]>([]);
  const [reference, setReference] = useState<any>(null);
  const [campaigns, setCampaigns] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState<AdsAudience | null>(null);
  const [creating, setCreating] = useState(false);
  const [previewing, setPreviewing] = useState<AdsAudience | null>(null);
  const [attaching, setAttaching] = useState<AdsAudience | null>(null);

  const load = useCallback(async () => {
    try {
      const [a, r, c] = await Promise.all([
        adsApi.listAudiences(),
        adsApi.reference(),
        adsApi.listCampaigns({ per_page: 100 }),
      ]);
      setAudiences(a.items);
      setReference(r);
      setCampaigns(c.items);
    } catch {
      toast.error("Could not load audiences");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const filtered = useMemo(
    () =>
      audiences.filter(
        (a) =>
          !query ||
          a.name.toLowerCase().includes(query.toLowerCase()) ||
          (a.description || "").toLowerCase().includes(query.toLowerCase())
      ),
    [audiences, query]
  );

  const totalReach = audiences.reduce((n, a) => n + (a.match_count || 0), 0);

  return (
    <div className="space-y-5 max-w-6xl mx-auto">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl sm:text-3xl font-bold">Audiences</h1>
          <p className="text-gray-500 mt-1">
            Build reusable audiences from your lists and contacts, then attach them to any campaign.
          </p>
        </div>
        <button className="btn-primary shrink-0" onClick={() => setCreating(true)}>
          <Plus size={17} className="mr-1" /> Create audience
        </button>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Stat label="Saved audiences" value={audiences.length} />
        <Stat label="Combined reach" value={totalReach} hint="Sum of matched contacts" />
        <Stat label="Contact lists" value={reference?.lists?.length ?? 0} />
        <Stat label="Contacts in CRM" value={reference?.total_contacts ?? 0} />
      </div>

      <div className="relative max-w-md">
        <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
        <input
          className="input !pl-9"
          placeholder="Search audiences…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      {loading ? (
        <div className="card p-10 text-center text-gray-500">Loading…</div>
      ) : filtered.length === 0 ? (
        <Empty
          icon={<Users size={40} />}
          title={audiences.length === 0 ? "No audiences yet" : "No matches"}
          body="Combine different lists and contacts into one named audience — like a Meta saved audience — then use it in any campaign."
          action={
            <button className="btn-primary" onClick={() => setCreating(true)}>
              <Plus size={16} className="mr-1" /> Create audience
            </button>
          }
        />
      ) : (
        <div className="grid gap-3">
          {filtered.map((a) => (
            <AudienceCard
              key={a.id}
              audience={a}
              reference={reference}
              onPreview={() => setPreviewing(a)}
              onEdit={() => setEditing(a)}
              onAttach={() => setAttaching(a)}
              onDuplicate={async () => {
                try {
                  await adsApi.duplicateAudience(a.id);
                  toast.success("Audience duplicated");
                  load();
                } catch (err: any) {
                  toast.error(err.response?.data?.detail || "Could not duplicate");
                }
              }}
              onDelete={async () => {
                if (!confirm(`Delete audience “${a.name}”? Campaigns already using it keep their copy.`))
                  return;
                try {
                  await adsApi.deleteAudience(a.id);
                  toast.success("Audience deleted");
                  load();
                } catch (err: any) {
                  toast.error(err.response?.data?.detail || "Could not delete");
                }
              }}
            />
          ))}
        </div>
      )}

      {(creating || editing) && (
        <AudienceEditor
          audience={editing}
          reference={reference}
          close={() => {
            setCreating(false);
            setEditing(null);
          }}
          saved={() => {
            setCreating(false);
            setEditing(null);
            load();
          }}
        />
      )}
      {previewing && <AudiencePreviewModal audience={previewing} close={() => setPreviewing(null)} />}
      {attaching && (
        <AttachModal
          audience={attaching}
          campaigns={campaigns}
          close={() => setAttaching(null)}
          done={() => {
            setAttaching(null);
            load();
          }}
        />
      )}
    </div>
  );
}

function AudienceCard({
  audience: a,
  reference,
  onPreview,
  onEdit,
  onAttach,
  onDuplicate,
  onDelete,
}: {
  audience: AdsAudience;
  reference: any;
  onPreview: () => void;
  onEdit: () => void;
  onAttach: () => void;
  onDuplicate: () => void;
  onDelete: () => void;
}) {
  const listNames = csvList(a.list_ids).map(
    (id) => reference?.lists?.find((l: any) => String(l.id) === String(id))?.name || `List ${id}`
  );
  const chips: string[] = [];
  if (a.include_tags) chips.push(`tags: ${a.include_tags}`);
  if (a.city) chips.push(a.city);
  if (a.state) chips.push(a.state);
  if (a.industry) chips.push(a.industry);
  if (a.include_statuses) chips.push(`status: ${a.include_statuses}`);
  if (a.activity_filter && a.activity_filter !== "any") chips.push(a.activity_filter.replace(/_/g, " "));

  return (
    <div className="card p-4 sm:p-5">
      <div className="flex flex-wrap gap-3 items-start justify-between">
        <div className="min-w-0">
          <h3 className="font-semibold text-lg">{a.name}</h3>
          {a.description && <p className="text-sm text-gray-500 mt-0.5">{a.description}</p>}
          <div className="flex flex-wrap gap-1.5 mt-2">
            {listNames.map((n) => (
              <span
                key={n}
                className="px-2 py-0.5 rounded-full text-xs bg-primary-50 dark:bg-primary-900/30 text-primary-700 dark:text-primary-200"
              >
                {n}
              </span>
            ))}
            {(a.explicit_contacts || 0) > 0 && (
              <span className="px-2 py-0.5 rounded-full text-xs bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300">
                {a.explicit_contacts} picked contact{a.explicit_contacts === 1 ? "" : "s"}
              </span>
            )}
            {chips.map((c) => (
              <span key={c} className="px-2 py-0.5 rounded-full text-xs bg-gray-100 dark:bg-gray-700 text-gray-500">
                {c}
              </span>
            ))}
            {listNames.length === 0 && !a.explicit_contacts && chips.length === 0 && (
              <span className="px-2 py-0.5 rounded-full text-xs bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-200">
                Empty — edit to add lists or contacts
              </span>
            )}
          </div>
        </div>
        <div className="text-right shrink-0">
          <div className="text-2xl font-bold">{a.match_count ?? "—"}</div>
          <div className="text-xs text-gray-500">contacts</div>
        </div>
      </div>
      <div className="flex flex-wrap gap-2 mt-4 pt-3 border-t border-gray-100 dark:border-gray-700">
        <button className="btn-primary btn-sm" onClick={onAttach}>
          <Megaphone size={14} className="mr-1" /> Use in campaign
        </button>
        <button className="btn-secondary btn-sm" onClick={onPreview}>
          <Eye size={14} className="mr-1" /> Preview
        </button>
        <button className="btn-secondary btn-sm" onClick={onEdit}>
          <Pencil size={14} className="mr-1" /> Edit
        </button>
        <button className="btn-secondary btn-sm" onClick={onDuplicate}>
          <Copy size={14} />
        </button>
        <button className="btn-ghost btn-sm text-red-600" onClick={onDelete}>
          <Trash2 size={14} />
        </button>
        <span className="ml-auto text-xs text-gray-400 self-center">Updated {fmtDay(a.updated_at)}</span>
      </div>
    </div>
  );
}

function AudienceEditor({
  audience,
  reference,
  close,
  saved,
}: {
  audience: AdsAudience | null;
  reference: any;
  close: () => void;
  saved: () => void;
}) {
  const [form, setForm] = useState<any>({
    name: audience?.name || "",
    description: audience?.description || "",
    list_ids: audience?.list_ids || "",
    contact_ids: audience?.contact_ids || "",
    include_tags: audience?.include_tags || "",
    exclude_tags: audience?.exclude_tags || "",
    include_statuses: audience?.include_statuses || "",
    exclude_statuses: audience?.exclude_statuses || "",
    city: audience?.city || "",
    state: audience?.state || "",
    industry: audience?.industry || "",
    activity_filter: audience?.activity_filter || "any",
  });
  const [estimate, setEstimate] = useState<any>(null);
  const set = (k: string, v: any) => setForm((f: any) => ({ ...f, [k]: v }));
  const selectedLists = csvList(form.list_ids);
  const selectedContacts: number[] = useMemo(
    () => csvList(form.contact_ids).map(Number).filter((n) => Number.isFinite(n) && n > 0),
    [form.contact_ids]
  );

  const estimateKey = JSON.stringify(form);
  useEffect(() => {
    const t = setTimeout(async () => {
      try {
        const r = await adsApi.previewTargeting({
          list_ids: form.list_ids || null,
          contact_ids: form.contact_ids || null,
          include_tags: form.include_tags || null,
          exclude_tags: form.exclude_tags || null,
          include_statuses: form.include_statuses || null,
          exclude_statuses: form.exclude_statuses || null,
          city: form.city || null,
          state: form.state || null,
          industry: form.industry || null,
          activity_filter: form.activity_filter || null,
        });
        setEstimate(r);
      } catch {
        setEstimate(null);
      }
    }, 400);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [estimateKey]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.name.trim()) return toast.error("Name the audience");
    const payload = {
      ...form,
      name: form.name.trim(),
      description: form.description || null,
      list_ids: form.list_ids || null,
      contact_ids: form.contact_ids || null,
      include_tags: form.include_tags || null,
      exclude_tags: form.exclude_tags || null,
      include_statuses: form.include_statuses || null,
      exclude_statuses: form.exclude_statuses || null,
      city: form.city || null,
      state: form.state || null,
      industry: form.industry || null,
      activity_filter: form.activity_filter || null,
    };
    try {
      if (audience) await adsApi.updateAudience(audience.id, payload);
      else await adsApi.createAudience(payload);
      toast.success(audience ? "Audience updated" : "Audience created");
      saved();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not save audience");
    }
  };

  return (
    <Modal title={audience ? "Edit audience" : "New audience"} close={close} wide>
      <form onSubmit={submit} className="space-y-4">
        <div className="grid sm:grid-cols-2 gap-3">
          <Field label="Audience name">
            <input
              className="input"
              value={form.name}
              onChange={(e) => set("name", e.target.value)}
              placeholder="Lagos VIP buyers"
              autoFocus
            />
          </Field>
          <Field label="Description">
            <input
              className="input"
              value={form.description}
              onChange={(e) => set("description", e.target.value)}
              placeholder="Who is this for?"
            />
          </Field>
        </div>

        {estimate && (
          <div className="p-3 rounded-lg bg-primary-50 dark:bg-primary-900/20 text-sm">
            <b>{estimate.eligible}</b> eligible contacts
            <span className="text-gray-500"> ({estimate.matched} matched before screening)</span>
            {estimate.matched === 0 && (
              <span className="block text-xs mt-1 text-amber-700 dark:text-amber-300">
                Nothing selected yet — add at least one list, contact or filter.
              </span>
            )}
          </div>
        )}

        <div>
          <span className="label">Lists — mix as many as you like</span>
          <div className="flex flex-wrap gap-2">
            {(reference?.lists || []).map((l: any) => {
              const on = selectedLists.includes(String(l.id));
              return (
                <button
                  type="button"
                  key={l.id}
                  onClick={() =>
                    set(
                      "list_ids",
                      (on
                        ? selectedLists.filter((x: string) => x !== String(l.id))
                        : [...selectedLists, String(l.id)]
                      ).join(",")
                    )
                  }
                  className={`px-3 py-1.5 rounded-lg text-sm border ${
                    on
                      ? "bg-primary-600 text-white border-primary-600"
                      : "border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300"
                  }`}
                >
                  {l.name} ({l.count})
                </button>
              );
            })}
            {(reference?.lists || []).length === 0 && (
              <p className="text-sm text-gray-500">No lists yet — create one under Lists first.</p>
            )}
          </div>
        </div>

        <div>
          <span className="label">Individual contacts</span>
          <ContactPicker
            value={selectedContacts}
            onChange={(ids) => set("contact_ids", ids.join(","))}
            placeholder="Search and add individual contacts…"
          />
        </div>

        <div className="grid sm:grid-cols-2 gap-3">
          <Field label="Include tags" hint="Comma separated.">
            <input className="input" value={form.include_tags} onChange={(e) => set("include_tags", e.target.value)} />
          </Field>
          <Field label="Exclude tags">
            <input className="input" value={form.exclude_tags} onChange={(e) => set("exclude_tags", e.target.value)} />
          </Field>
          <Field label="Include statuses">
            <input
              className="input"
              value={form.include_statuses}
              onChange={(e) => set("include_statuses", e.target.value)}
              placeholder="prospect,new"
            />
          </Field>
          <Field label="Exclude statuses">
            <input
              className="input"
              value={form.exclude_statuses}
              onChange={(e) => set("exclude_statuses", e.target.value)}
            />
          </Field>
          <Field label="City">
            <input className="input" value={form.city} onChange={(e) => set("city", e.target.value)} />
          </Field>
          <Field label="State">
            <input className="input" value={form.state} onChange={(e) => set("state", e.target.value)} />
          </Field>
          <Field label="Industry">
            <input className="input" value={form.industry} onChange={(e) => set("industry", e.target.value)} />
          </Field>
          <Field label="Activity">
            <select
              className="input"
              value={form.activity_filter}
              onChange={(e) => set("activity_filter", e.target.value)}
            >
              <option value="any">Any</option>
              <option value="never_contacted">Never contacted</option>
              <option value="contacted">Previously contacted</option>
              <option value="replied">Replied before</option>
              <option value="not_replied">Never replied</option>
            </select>
          </Field>
        </div>

        <div className="flex gap-2">
          <button type="button" className="btn-secondary flex-1" onClick={close}>
            Cancel
          </button>
          <button className="btn-primary flex-1">Save audience</button>
        </div>
      </form>
    </Modal>
  );
}

function AudiencePreviewModal({ audience, close }: { audience: AdsAudience; close: () => void }) {
  const [data, setData] = useState<any>(null);
  useEffect(() => {
    adsApi.previewAudience(audience.id).then(setData).catch(() => {});
  }, [audience.id]);

  return (
    <Modal title={`“${audience.name}” — preview`} close={close} wide>
      {!data ? (
        <p className="text-sm text-gray-500">Loading…</p>
      ) : (
        <div className="space-y-4">
          <div className="grid grid-cols-3 gap-3">
            <Metric label="Matched" value={data.matched} />
            <Metric label="Eligible" value={data.eligible} />
            <Metric label="Picked contacts" value={data.explicit_contacts} />
          </div>
          {data.lists?.length > 0 && (
            <div>
              <h4 className="font-semibold text-sm mb-2">Lists in this audience</h4>
              <div className="space-y-1.5">
                {data.lists.map((l: any) => (
                  <div
                    key={l.list_id}
                    className="flex justify-between text-sm p-2 rounded-lg bg-gray-50 dark:bg-gray-700/50"
                  >
                    <span>{l.name}</span>
                    <b>{l.members}</b>
                  </div>
                ))}
              </div>
            </div>
          )}
          {Object.keys(data.skipped || {}).length > 0 && (
            <div className="p-3 rounded-lg bg-gray-50 dark:bg-gray-700/50 text-sm">
              <p className="font-medium mb-1">Screened out</p>
              {Object.entries(data.skipped).map(([k, v]: any) => (
                <p key={k} className="text-gray-600 dark:text-gray-300">
                  {k.replace(/_/g, " ")}: <b>{v}</b>
                </p>
              ))}
            </div>
          )}
          <div>
            <h4 className="font-semibold text-sm mb-2">Sample contacts</h4>
            {data.sample?.length === 0 ? (
              <p className="text-sm text-gray-500">No eligible contacts in this audience yet.</p>
            ) : (
              <div className="card overflow-x-auto !shadow-none border border-gray-200 dark:border-gray-700">
                <table className="w-full text-sm">
                  <tbody>
                    {data.sample.map((c: any) => (
                      <tr key={c.id} className="border-b border-gray-50 dark:border-gray-700/50">
                        <td className="p-2.5">{c.name || "—"}</td>
                        <td className="p-2.5 whitespace-nowrap">{c.phone_number}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}
    </Modal>
  );
}

function AttachModal({
  audience,
  campaigns,
  close,
  done,
}: {
  audience: AdsAudience;
  campaigns: any[];
  close: () => void;
  done: () => void;
}) {
  const [campaignId, setCampaignId] = useState<number | "">(campaigns[0]?.id ?? "");
  const [sets, setSets] = useState<any[]>([]);
  const [mode, setMode] = useState<"new" | "existing">("new");
  const [setId, setSetId] = useState<number | "">("");
  const [newName, setNewName] = useState(`${audience.name} set`);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<any>(null);

  useEffect(() => {
    if (!campaignId) return;
    adsApi
      .getCampaign(Number(campaignId))
      .then((d) => {
        setSets(d.sets);
        setSetId(d.sets[0]?.id ?? "");
      })
      .catch(() => setSets([]));
  }, [campaignId]);

  const submit = async () => {
    if (!campaignId) return toast.error("Pick a campaign");
    if (mode === "existing" && !setId) return toast.error("Pick an SMS set");
    setBusy(true);
    try {
      const r = await adsApi.attachAudience(audience.id, {
        campaign_id: Number(campaignId),
        set_id: mode === "existing" ? Number(setId) : null,
        new_set_name: mode === "new" ? newName.trim() || undefined : undefined,
      });
      setResult(r);
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not attach audience");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title={`Use “${audience.name}” in a campaign`} close={close}>
      {result ? (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <Metric label="Set" value={result.name} />
            <Metric label="Eligible" value={result.eligible} />
          </div>
          <p className="text-sm text-gray-500">
            {result.matched} contacts matched. They join the campaign the next time it builds its audience
            (launch or “Refresh audience”).
          </p>
          <button className="btn-primary w-full" onClick={done}>
            Done
          </button>
        </div>
      ) : campaigns.length === 0 ? (
        <Empty title="No campaigns yet" body="Create an SMS campaign first, then attach this audience to it." />
      ) : (
        <div className="space-y-4">
          <Field label="Campaign">
            <select className="input" value={campaignId} onChange={(e) => setCampaignId(Number(e.target.value))}>
              {campaigns.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name} ({c.status})
                </option>
              ))}
            </select>
          </Field>
          <div className="flex gap-2">
            <button
              type="button"
              className={`btn-sm flex-1 rounded-lg border px-3 py-2 text-sm ${
                mode === "new" ? "bg-primary-600 text-white border-primary-600" : "btn-secondary"
              }`}
              onClick={() => setMode("new")}
            >
              Create new SMS set
            </button>
            <button
              type="button"
              className={`btn-sm flex-1 rounded-lg border px-3 py-2 text-sm ${
                mode === "existing" ? "bg-primary-600 text-white border-primary-600" : "btn-secondary"
              }`}
              onClick={() => setMode("existing")}
            >
              Use existing set
            </button>
          </div>
          {mode === "new" ? (
            <Field label="New set name">
              <input className="input" value={newName} onChange={(e) => setNewName(e.target.value)} />
            </Field>
          ) : (
            <Field label="SMS set" hint="Its current targeting will be replaced by this audience.">
              <select className="input" value={setId} onChange={(e) => setSetId(Number(e.target.value))}>
                {sets.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </Field>
          )}
          <div className="flex gap-2">
            <button className="btn-secondary flex-1" onClick={close}>
              Cancel
            </button>
            <button className="btn-primary flex-1" onClick={submit} disabled={busy}>
              {busy ? "Attaching…" : "Attach audience"}
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}
