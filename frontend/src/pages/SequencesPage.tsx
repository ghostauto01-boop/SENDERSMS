import { useEffect, useState } from "react";
import api from "../api/client";
import { SequenceData, SequenceStep } from "../types";
import toast from "react-hot-toast";
import {
  ArrowDown,
  ArrowUp,
  Clock,
  Edit2,
  GitBranch,
  HelpCircle,
  Plus,
  Send,
  StopCircle,
  Trash2,
  X,
} from "lucide-react";

type StepType = "send_sms" | "wait" | "condition" | "stop";
type MessageMode = "write" | "template";

interface TemplateChoice {
  id: number;
  name: string;
  body: string;
  is_active: boolean;
}

interface BuilderStep {
  step_type: StepType;
  wait_duration_hours: number;
  message_mode: MessageMode;
  message_body: string;
  template_id: string;
  condition_type: string;
  true_branch_step_order: string;
  false_branch_step_order: string;
}

const blankStep = (step_type: StepType = "send_sms"): BuilderStep => ({
  step_type,
  wait_duration_hours: 24,
  message_mode: "write",
  message_body: "",
  template_id: "",
  condition_type: "contact_replied",
  true_branch_step_order: "",
  false_branch_step_order: "",
});

const defaultSteps = (): BuilderStep[] => [
  blankStep("send_sms"),
  blankStep("wait"),
  blankStep("send_sms"),
  blankStep("stop"),
];

const readMessageBody = (config: string | null | undefined) => {
  if (!config) return "";
  try {
    const parsed = JSON.parse(config);
    return typeof parsed === "object" && parsed ? String(parsed.message_body || "") : String(parsed || "");
  } catch {
    return config;
  }
};

const fromApiStep = (step: SequenceStep): BuilderStep => ({
  step_type: step.step_type as StepType,
  wait_duration_hours: step.wait_duration_hours || 24,
  message_mode: step.template_id ? "template" : "write",
  message_body: readMessageBody(step.config),
  template_id: step.template_id ? String(step.template_id) : "",
  condition_type: step.condition_type || "contact_replied",
  true_branch_step_order: step.true_branch_step_order == null ? "" : String(step.true_branch_step_order),
  false_branch_step_order: step.false_branch_step_order == null ? "" : String(step.false_branch_step_order),
});

const stepIcon = (type: string) => {
  switch (type) {
    case "send_sms": return <Send size={14} className="text-blue-500" />;
    case "wait": return <Clock size={14} className="text-yellow-500" />;
    case "condition": return <HelpCircle size={14} className="text-purple-500" />;
    case "stop": return <StopCircle size={14} className="text-red-500" />;
    default: return null;
  }
};

const stepSummary = (step: SequenceStep) => {
  if (step.step_type === "send_sms") {
    if (step.template_id) return `Template #${step.template_id}`;
    const body = readMessageBody(step.config);
    return body || "No message";
  }
  if (step.step_type === "wait") return `${step.wait_duration_hours || 24} hours`;
  if (step.step_type === "condition") return (step.condition_type || "condition").replace(/_/g, " ");
  return "End sequence";
};

export default function SequencesPage() {
  const [sequences, setSequences] = useState<SequenceData[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState<SequenceData | null>(null);

  useEffect(() => { loadSequences(); }, []);

  const loadSequences = async () => {
    try {
      setLoading(true);
      setError(null);
      const { data } = await api.get("/sequences/", { params: { per_page: 100 } });
      setSequences(data.items);
    } catch (err: any) {
      setError(err.response?.data?.detail || "Failed to load sequences");
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async (id: number) => {
    if (!window.confirm("Delete this sequence?")) return;
    try {
      await api.delete(`/sequences/${id}`);
      toast.success("Sequence deleted");
      loadSequences();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Failed to delete sequence");
    }
  };

  const closeBuilder = () => {
    setShowCreate(false);
    setEditing(null);
    loadSequences();
  };

  if (error) {
    return (
      <div className="text-center py-12">
        <h2 className="text-xl font-semibold mb-2">Error</h2>
        <p className="text-gray-500 mb-4">{error}</p>
        <button onClick={loadSequences} className="btn-primary">Retry</button>
      </div>
    );
  }

  return (
    <div className="space-y-4 pb-20 lg:pb-0">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold">Sequences</h1>
          <p className="text-sm text-gray-500 mt-0.5">Automate messages and create visible follow-ups between steps.</p>
        </div>
        <button onClick={() => setShowCreate(true)} className="btn-primary btn-sm">
          <Plus size={14} className="mr-1" /> New Sequence
        </button>
      </div>

      <div className="space-y-4">
        {loading ? (
          [...Array(3)].map((_, index) => (
            <div key={index} className="card p-4">
              <div className="skeleton h-5 w-48 mb-2" />
              <div className="skeleton h-4 w-64" />
            </div>
          ))
        ) : sequences.length === 0 ? (
          <div className="card p-12 text-center text-gray-500">
            <GitBranch size={48} className="mx-auto mb-2 opacity-30" />
            <p>No sequences yet. Build an automated SMS sequence.</p>
            <button onClick={() => setShowCreate(true)} className="btn-primary btn-sm mt-3"><Plus size={14} className="mr-1" /> Build sequence</button>
          </div>
        ) : (
          sequences.map((sequence) => (
            <div key={sequence.id} className="card p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <h3 className="font-semibold text-lg truncate">{sequence.name}</h3>
                  {sequence.description && <p className="text-sm text-gray-500 mt-1">{sequence.description}</p>}
                  <p className="text-xs text-gray-400 mt-1">
                    Version {sequence.current_version} · {sequence.steps?.length || 0} steps
                  </p>
                </div>
                <div className="flex gap-1 flex-shrink-0">
                  <button onClick={() => setEditing(sequence)} className="btn-secondary btn-sm" title="Edit sequence"><Edit2 size={14} /></button>
                  <button onClick={() => handleDelete(sequence.id)} className="btn-ghost btn-sm text-red-600" title="Delete sequence"><Trash2 size={14} /></button>
                </div>
              </div>

              {sequence.steps && sequence.steps.length > 0 && (
                <div className="flex flex-wrap items-center gap-2 mt-4">
                  {sequence.steps.map((step, index) => (
                    <div key={step.id || index} className="flex items-center gap-2 max-w-full">
                      <div className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-gray-50 dark:bg-gray-700 text-xs max-w-[220px]">
                        {stepIcon(step.step_type)}
                        <div className="min-w-0">
                          <p className="font-medium capitalize">{step.step_type.replace("_", " ")}</p>
                          <p className="text-gray-400 truncate">{stepSummary(step)}</p>
                        </div>
                      </div>
                      {index < sequence.steps.length - 1 && <span className="text-gray-300 dark:text-gray-600">→</span>}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))
        )}
      </div>

      {(showCreate || editing) && (
        <SequenceBuilderModal sequence={editing} onClose={closeBuilder} />
      )}
    </div>
  );
}

function SequenceBuilderModal({ sequence, onClose }: { sequence: SequenceData | null; onClose: () => void }) {
  const editing = !!sequence;
  const [name, setName] = useState(sequence?.name || "");
  const [description, setDescription] = useState(sequence?.description || "");
  const [steps, setSteps] = useState<BuilderStep[]>(
    sequence?.steps?.length ? sequence.steps.map(fromApiStep) : defaultSteps()
  );
  const [templates, setTemplates] = useState<TemplateChoice[]>([]);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    api.get("/templates/", { params: { per_page: 100 } })
      .then((response) => setTemplates((response.data.items || []).filter((template: TemplateChoice) => template.is_active)))
      .catch(() => toast.error("Could not load templates"));
  }, []);

  const updateStep = (index: number, patch: Partial<BuilderStep>) => {
    setSteps((current) => current.map((step, position) => position === index ? { ...step, ...patch } : step));
  };

  const addStep = () => setSteps((current) => [...current, blankStep("send_sms")]);

  const removeStep = (index: number) => {
    setSteps((current) =>
      current
        .filter((_, position) => position !== index)
        .map((step) => ({ ...step, true_branch_step_order: "", false_branch_step_order: "" }))
    );
  };

  const moveStep = (index: number, direction: -1 | 1) => {
    const target = index + direction;
    if (target < 0 || target >= steps.length) return;
    const next = [...steps];
    [next[index], next[target]] = [next[target], next[index]];
    // Clear branch positions after reordering; preserving old numeric targets
    // would silently point at a different action.
    setSteps(next.map((step) => ({ ...step, true_branch_step_order: "", false_branch_step_order: "" })));
  };

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!name.trim()) { toast.error("Sequence name is required"); return; }
    if (steps.length === 0) { toast.error("Add at least one step"); return; }
    if (!steps.some((step) => step.step_type === "send_sms")) { toast.error("Add at least one Send SMS step"); return; }

    for (let index = 0; index < steps.length; index += 1) {
      const step = steps[index];
      if (step.step_type === "send_sms") {
        if (step.message_mode === "write" && !step.message_body.trim()) {
          toast.error(`Step ${index + 1} needs a message`); return;
        }
        if (step.message_mode === "template" && !step.template_id) {
          toast.error(`Step ${index + 1} needs a template`); return;
        }
      }
      if (step.step_type === "wait" && step.wait_duration_hours < 1) {
        toast.error(`Step ${index + 1} wait must be at least 1 hour`); return;
      }
    }

    const payload = {
      name: name.trim(),
      description: description.trim() || null,
      steps: steps.map((step, index) => ({
        step_order: index,
        step_type: step.step_type,
        config: step.step_type === "send_sms" && step.message_mode === "write"
          ? JSON.stringify({ message_body: step.message_body.trim() })
          : null,
        wait_duration_hours: step.step_type === "wait" ? step.wait_duration_hours : null,
        template_id: step.step_type === "send_sms" && step.message_mode === "template"
          ? Number(step.template_id)
          : null,
        condition_type: step.step_type === "condition" ? step.condition_type : null,
        condition_value: null,
        true_branch_step_order: step.step_type === "condition" && step.true_branch_step_order !== ""
          ? Number(step.true_branch_step_order)
          : null,
        false_branch_step_order: step.step_type === "condition" && step.false_branch_step_order !== ""
          ? Number(step.false_branch_step_order)
          : null,
      })),
    };

    try {
      setSubmitting(true);
      if (editing) {
        await api.put(`/sequences/${sequence!.id}`, payload);
        toast.success("Sequence updated. Running campaigns keep their saved version.");
      } else {
        await api.post("/sequences/", payload);
        toast.success("Sequence created");
      }
      onClose();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Failed to save sequence");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/50 p-0 sm:p-4">
      <div className="bg-white dark:bg-[#202c33] w-full sm:max-w-3xl max-h-[94vh] overflow-y-auto rounded-t-2xl sm:rounded-2xl">
        <div className="sticky top-0 z-10 bg-[#008069] dark:bg-[#202c33] px-4 py-3 flex items-center justify-between">
          <div>
            <h2 className="text-white font-semibold">{editing ? "Edit Sequence" : "Create Sequence"}</h2>
            <p className="text-white/70 text-xs">Wait steps become follow-ups and resume automatically.</p>
          </div>
          <button type="button" onClick={onClose} className="w-8 h-8 rounded-full bg-white/10 flex items-center justify-center text-white"><X size={16} /></button>
        </div>

        <form onSubmit={handleSubmit} className="p-4 sm:p-6 space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="label">Name *</label>
              <input className="input" value={name} onChange={(event) => setName(event.target.value)} required autoFocus />
            </div>
            <div>
              <label className="label">Description</label>
              <input className="input" value={description} onChange={(event) => setDescription(event.target.value)} />
            </div>
          </div>

          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="font-semibold text-sm">Automation steps</h3>
                <p className="text-xs text-gray-500">Each Send SMS step has its own message or template.</p>
              </div>
              <button type="button" onClick={addStep} className="btn-secondary btn-sm"><Plus size={14} className="mr-1" /> Add step</button>
            </div>

            {steps.map((step, index) => {
              const laterSteps = steps.map((candidate, position) => ({ candidate, position })).filter(({ position }) => position > index);
              return (
                <div key={index} className="border border-gray-200 dark:border-[#2a3942] rounded-xl overflow-hidden">
                  <div className="px-3 py-2 bg-gray-50 dark:bg-[#111b21] flex items-center gap-2">
                    <span className="w-6 h-6 rounded-full bg-[#00a884] text-white text-xs font-semibold flex items-center justify-center">{index + 1}</span>
                    <select
                      className="input py-1.5 text-sm w-40"
                      value={step.step_type}
                      onChange={(event) => updateStep(index, { step_type: event.target.value as StepType })}
                    >
                      <option value="send_sms">Send SMS</option>
                      <option value="wait">Wait / Follow-up</option>
                      <option value="condition">Condition</option>
                      <option value="stop">Stop</option>
                    </select>
                    <div className="ml-auto flex gap-1">
                      <button type="button" onClick={() => moveStep(index, -1)} disabled={index === 0} className="btn-ghost btn-sm" title="Move up"><ArrowUp size={13} /></button>
                      <button type="button" onClick={() => moveStep(index, 1)} disabled={index === steps.length - 1} className="btn-ghost btn-sm" title="Move down"><ArrowDown size={13} /></button>
                      <button type="button" onClick={() => removeStep(index)} disabled={steps.length === 1} className="btn-ghost btn-sm text-red-600" title="Remove step"><Trash2 size={13} /></button>
                    </div>
                  </div>

                  <div className="p-3">
                    {step.step_type === "send_sms" && (
                      <div className="space-y-2">
                        <div className="flex gap-1 p-1 rounded-lg bg-gray-100 dark:bg-gray-700">
                          <button type="button" onClick={() => updateStep(index, { message_mode: "write" })} className={`flex-1 px-3 py-1.5 rounded-md text-xs font-medium ${step.message_mode === "write" ? "bg-white dark:bg-gray-800 shadow-sm text-primary-600" : "text-gray-500"}`}>Write message</button>
                          <button type="button" onClick={() => updateStep(index, { message_mode: "template" })} className={`flex-1 px-3 py-1.5 rounded-md text-xs font-medium ${step.message_mode === "template" ? "bg-white dark:bg-gray-800 shadow-sm text-primary-600" : "text-gray-500"}`}>Use template</button>
                        </div>
                        {step.message_mode === "write" ? (
                          <>
                            <textarea
                              className="input min-h-24 text-sm"
                              placeholder={index === 0 ? "Hi {{first_name}}, ..." : "Hi {{first_name}}, just following up..."}
                              value={step.message_body}
                              onChange={(event) => updateStep(index, { message_body: event.target.value })}
                              maxLength={5000}
                            />
                            <p className="text-[11px] text-gray-500">{step.message_body.length} characters · supports {"{{first_name}}"}</p>
                          </>
                        ) : (
                          <select className="input" value={step.template_id} onChange={(event) => updateStep(index, { template_id: event.target.value })}>
                            <option value="">Select template...</option>
                            {templates.map((template) => <option key={template.id} value={template.id}>{template.name}</option>)}
                          </select>
                        )}
                      </div>
                    )}

                    {step.step_type === "wait" && (
                      <div>
                        <label className="label">Wait before the next step</label>
                        <div className="flex items-center gap-2">
                          <input type="number" min={1} max={8760} className="input w-28" value={step.wait_duration_hours} onChange={(event) => updateStep(index, { wait_duration_hours: Math.max(1, Number(event.target.value) || 1) })} />
                          <span className="text-sm text-gray-500">hours</span>
                        </div>
                        <p className="text-xs text-gray-500 mt-1">A pending item appears in Follow-ups and continues automatically when due.</p>
                      </div>
                    )}

                    {step.step_type === "condition" && (
                      <div className="space-y-3">
                        <div>
                          <label className="label">Check</label>
                          <select className="input" value={step.condition_type} onChange={(event) => updateStep(index, { condition_type: event.target.value })}>
                            <option value="contact_replied">Contact replied</option>
                            <option value="contact_did_not_reply">Contact did not reply</option>
                            <option value="message_delivered">Last message delivered</option>
                            <option value="message_failed">Last message failed</option>
                            <option value="contact_opted_out">Contact opted out</option>
                          </select>
                        </div>
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                          {[{ key: "true_branch_step_order", label: "If true" }, { key: "false_branch_step_order", label: "If false" }].map((branch) => (
                            <div key={branch.key}>
                              <label className="label">{branch.label}</label>
                              <select
                                className="input"
                                value={step[branch.key as "true_branch_step_order" | "false_branch_step_order"]}
                                onChange={(event) => updateStep(index, { [branch.key]: event.target.value })}
                              >
                                <option value="">End sequence</option>
                                {laterSteps.map(({ candidate, position }) => <option key={position} value={position}>Step {position + 1}: {candidate.step_type.replace("_", " ")}</option>)}
                              </select>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {step.step_type === "stop" && <p className="text-sm text-gray-500 flex items-center gap-2"><StopCircle size={16} className="text-red-500" /> End automation for this contact.</p>}
                  </div>
                </div>
              );
            })}
          </div>

          <div className="flex flex-col-reverse sm:flex-row gap-2 pt-2">
            <button type="button" onClick={onClose} className="btn-secondary flex-1">Cancel</button>
            <button type="submit" disabled={submitting} className="btn-primary flex-1">
              {submitting ? "Saving…" : editing ? "Save new version" : "Create sequence"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
