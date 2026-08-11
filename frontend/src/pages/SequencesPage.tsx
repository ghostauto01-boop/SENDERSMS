import { useState, useEffect } from "react";
import api from "../api/client";
import { SequenceData, SequenceStep } from "../types";
import toast from "react-hot-toast";
import { Plus, Trash2, GitBranch, Clock, Send, HelpCircle, StopCircle } from "lucide-react";

export default function SequencesPage() {
  const [sequences, setSequences] = useState<SequenceData[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);

  useEffect(() => { loadSequences(); }, []);

  const loadSequences = async () => {
    try {
      setLoading(true);
      const { data } = await api.get("/sequences/");
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
    } catch {
      toast.error("Failed to delete");
    }
  };

  const stepIcon = (type: string) => {
    switch (type) {
      case "send_sms": return <Send size={14} className="text-blue-500" />;
      case "wait": return <Clock size={14} className="text-yellow-500" />;
      case "condition": return <HelpCircle size={14} className="text-purple-500" />;
      case "stop": return <StopCircle size={14} className="text-red-500" />;
      default: return null;
    }
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
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <h1 className="text-xl sm:text-2xl font-bold">Sequences</h1>
        <button onClick={() => setShowCreate(true)} className="btn-primary btn-sm">
          <Plus size={14} className="mr-1" /> New Sequence
        </button>
      </div>

      <div className="space-y-4">
        {loading ? (
          [...Array(3)].map((_, i) => (
            <div key={i} className="card p-4">
              <div className="skeleton h-5 w-48 mb-2" />
              <div className="skeleton h-4 w-64" />
            </div>
          ))
        ) : sequences.length === 0 ? (
          <div className="card p-12 text-center text-gray-500">
            <GitBranch size={48} className="mx-auto mb-2 opacity-30" />
            <p>No sequences yet. Build an automated SMS sequence.</p>
          </div>
        ) : (
          sequences.map((seq) => (
            <div key={seq.id} className="card p-4">
              <div className="flex items-start justify-between">
                <div className="flex-1">
                  <h3 className="font-semibold text-lg">{seq.name}</h3>
                  {seq.description && (
                    <p className="text-sm text-gray-500 mt-1">{seq.description}</p>
                  )}
                  <p className="text-xs text-gray-400 mt-1">
                    Version {seq.current_version} · {seq.steps?.length || 0} steps
                  </p>

                  {/* Visual sequence */}
                  {seq.steps && seq.steps.length > 0 && (
                    <div className="flex flex-wrap items-center gap-2 mt-3">
                      {seq.steps.map((step, i) => (
                        <div key={step.id || i} className="flex items-center gap-2">
                          <div className="flex items-center gap-1 px-2 py-1 rounded bg-gray-50 dark:bg-gray-700 text-xs">
                            {stepIcon(step.step_type)}
                            <span>{step.step_type.replace("_", " ")}</span>
                            {step.wait_duration_hours && (
                              <span className="text-gray-400">({step.wait_duration_hours}h)</span>
                            )}
                          </div>
                          {i < seq.steps.length - 1 && (
                            <span className="text-gray-300 dark:text-gray-600">→</span>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
                <button onClick={() => handleDelete(seq.id)} className="btn-ghost btn-sm text-red-600">
                  <Trash2 size={14} />
                </button>
              </div>
            </div>
          ))
        )}
      </div>

      {showCreate && <CreateSequenceModal onClose={() => { setShowCreate(false); loadSequences(); }} />}
    </div>
  );
}

function CreateSequenceModal({ onClose }: { onClose: () => void }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [steps, setSteps] = useState<Partial<SequenceStep>[]>([
    { step_order: 0, step_type: "send_sms", wait_duration_hours: 0 },
    { step_order: 1, step_type: "wait", wait_duration_hours: 48 },
    { step_order: 2, step_type: "condition", condition_type: "contact_replied" },
    { step_order: 3, step_type: "stop" },
  ]);
  const [submitting, setSubmitting] = useState(false);

  const addStep = () => {
    setSteps([...steps, { step_order: steps.length, step_type: "send_sms" }]);
  };

  const removeStep = (idx: number) => {
    setSteps(steps.filter((_, i) => i !== idx).map((s, i) => ({ ...s, step_order: i })));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) { toast.error("Name required"); return; }
    setSubmitting(true);
    try {
      await api.post("/sequences/", {
        name: name.trim(),
        description: description || null,
        steps: steps.map((s, i) => ({
          step_order: i,
          step_type: s.step_type || "send_sms",
          wait_duration_hours: s.wait_duration_hours || null,
          condition_type: s.condition_type || null,
          condition_value: s.condition_value || null,
          true_branch_step_order: s.true_branch_step_order || null,
          false_branch_step_order: s.false_branch_step_order || null,
        })),
      });
      toast.success("Sequence created");
      onClose();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Failed to create sequence");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/50 p-0 sm:p-4">
      <div className="card w-full sm:max-w-lg max-h-[92vh] overflow-y-auto rounded-t-2xl sm:rounded-xl p-4 sm:p-6">
        <h2 className="text-lg font-semibold mb-4">Create Sequence</h2>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="label">Name *</label>
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} required />
          </div>
          <div>
            <label className="label">Description</label>
            <textarea className="input" rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />
          </div>

          <div>
            <label className="label">Steps</label>
            <div className="space-y-2 border border-gray-200 dark:border-gray-700 rounded-lg p-3">
              {steps.map((step, idx) => (
                <div key={idx} className="flex items-center gap-2 p-2 bg-gray-50 dark:bg-gray-700 rounded">
                  <span className="text-xs text-gray-400">#{idx + 1}</span>
                  <select
                    className="input py-1 text-sm w-32"
                    value={step.step_type}
                    onChange={(e) => {
                      const ns = [...steps];
                      ns[idx] = { ...ns[idx], step_type: e.target.value };
                      setSteps(ns);
                    }}
                  >
                    <option value="send_sms">Send SMS</option>
                    <option value="wait">Wait</option>
                    <option value="condition">Condition</option>
                    <option value="stop">Stop</option>
                  </select>
                  {step.step_type === "wait" && (
                    <input
                      type="number"
                      className="input py-1 text-sm w-20"
                      placeholder="Hours"
                      value={step.wait_duration_hours || ""}
                      onChange={(e) => {
                        const ns = [...steps];
                        ns[idx] = { ...ns[idx], wait_duration_hours: parseInt(e.target.value) || 0 };
                        setSteps(ns);
                      }}
                    />
                  )}
                  {step.step_type === "condition" && (
                    <select
                      className="input py-1 text-sm w-40"
                      value={step.condition_type || "contact_replied"}
                      onChange={(e) => {
                        const ns = [...steps];
                        ns[idx] = { ...ns[idx], condition_type: e.target.value };
                        setSteps(ns);
                      }}
                    >
                      <option value="contact_replied">Contact replied</option>
                      <option value="contact_did_not_reply">No reply</option>
                      <option value="message_delivered">Delivered</option>
                      <option value="message_failed">Failed</option>
                    </select>
                  )}
                  <button type="button" onClick={() => removeStep(idx)} className="btn-ghost btn-sm text-red-600 ml-auto">
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
              <button type="button" onClick={addStep} className="btn-secondary btn-sm w-full text-center">
                + Add Step
              </button>
            </div>
          </div>

          <div className="flex gap-2 pt-2">
            <button type="button" onClick={onClose} className="btn-secondary flex-1">Cancel</button>
            <button type="submit" disabled={submitting} className="btn-primary flex-1">
              {submitting ? "Creating..." : "Create"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
