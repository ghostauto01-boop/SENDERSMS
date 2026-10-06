import { useCallback, useEffect, useMemo, useState } from "react";
import toast from "react-hot-toast";
import { useNavigate } from "react-router-dom";
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  ChevronDown,
  CircleDashed,
  Copy,
  ExternalLink,
  GraduationCap,
  ListChecks,
  RefreshCw,
  Rocket,
} from "lucide-react";
import guideApi, { GuideStep, SetupGuide, StepStatus } from "../api/guide";

/**
 * SETUP GUIDE — the in-app tutorial for setting everything up.
 *
 * Written for the failure mode this app actually has: every missing setting
 * presents as a symptom somewhere else. "My replies never arrive" is a mailbox
 * that was never connected. "The AI connector is not working" is a missing
 * PUBLIC_BASE_URL. "Inbound SMS stopped" is a webhook registered against an old
 * address. So each step here shows what the server can *see right now*, what
 * that costs you, the values to paste into the other system, and the button that
 * opens the screen where it is fixed.
 */

const STATUS_META: Record<
  StepStatus,
  { label: string; chip: string; icon: any; colour: string }
> = {
  done: { label: "Done", chip: "badge-green", icon: CheckCircle2, colour: "text-success-500" },
  todo: { label: "To do", chip: "badge-red", icon: AlertTriangle, colour: "text-danger-500" },
  attention: {
    label: "Needs attention",
    chip: "badge-yellow",
    icon: AlertTriangle,
    colour: "text-warning-500",
  },
  optional: { label: "Optional", chip: "badge-gray", icon: CircleDashed, colour: "text-gray-400" },
};

function CopyRow({ label, value }: { label: string; value: string }) {
  const [done, setDone] = useState(false);
  if (!value) return null;
  return (
    <div className="flex items-start gap-2">
      <div className="min-w-0 flex-1">
        <p className="text-xs text-gray-500 mb-1">{label}</p>
        <code className="block text-xs bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded p-2 overflow-x-auto whitespace-pre-wrap break-all">
          {value}
        </code>
      </div>
      <button
        className="btn-secondary btn-sm mt-4 shrink-0"
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(value);
            setDone(true);
            setTimeout(() => setDone(false), 1500);
          } catch {
            toast.error("Copy failed — select the text and copy it manually");
          }
        }}
      >
        {done ? <CheckCircle2 size={13} /> : <Copy size={13} />}
      </button>
    </div>
  );
}

function StepCard({
  step,
  defaultOpen,
}: {
  step: GuideStep;
  defaultOpen: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const navigate = useNavigate();
  const meta = STATUS_META[step.status] || STATUS_META.optional;
  const Icon = meta.icon;
  const hasBody =
    step.steps.length > 0 || step.copy.length > 0 || step.docs.length > 0 || !!step.verify;

  return (
    <div className="card p-4">
      <button
        className="w-full flex items-start gap-3 text-left"
        onClick={() => hasBody && setOpen((v) => !v)}
        aria-expanded={open}
      >
        <Icon size={18} className={`mt-0.5 shrink-0 ${meta.colour}`} />
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2 flex-wrap">
            <span className="font-medium text-sm">{step.title}</span>
            <span className={meta.chip}>{meta.label}</span>
          </span>
          <span className="block text-xs text-gray-500 mt-1">{step.why}</span>
        </span>
        {hasBody && (
          <ChevronDown
            size={16}
            className={`mt-1 shrink-0 text-gray-400 transition-transform ${open ? "rotate-180" : ""}`}
          />
        )}
      </button>

      {step.detail && (
        <p
          className={`text-xs mt-2 rounded-lg p-2.5 leading-relaxed ${
            step.status === "done"
              ? "bg-success-50 dark:bg-success-950/30 text-gray-700 dark:text-gray-300"
              : step.status === "optional"
                ? "bg-gray-50 dark:bg-gray-800/40 text-gray-600 dark:text-gray-300"
                : "bg-warning-50 dark:bg-warning-950/30 text-gray-700 dark:text-gray-200"
          }`}
        >
          {step.detail}
        </p>
      )}

      {open && hasBody && (
        <div className="mt-3 space-y-3 pl-7">
          {step.steps.length > 0 && (
            <ol className="text-sm text-gray-600 dark:text-gray-300 space-y-1.5 list-decimal pl-5">
              {step.steps.map((s, i) => (
                <li key={i}>{s}</li>
              ))}
            </ol>
          )}

          {step.copy.length > 0 && (
            <div className="space-y-2">
              {step.copy.map((c) => (
                <CopyRow key={c.label} label={c.label} value={c.value} />
              ))}
            </div>
          )}

          {step.verify && (
            <p className="text-xs text-gray-500">
              <strong>How to check:</strong> {step.verify}
            </p>
          )}

          <div className="flex flex-wrap items-center gap-2">
            {step.route && (
              <button
                className="btn-primary btn-sm"
                onClick={() => navigate(step.route as string)}
              >
                {step.route_label || "Open"} <ArrowRight size={13} className="ml-1" />
              </button>
            )}
            {step.docs.map((d) => (
              <a
                key={d.url}
                className="btn-secondary btn-sm inline-flex items-center gap-1"
                href={d.url}
                target={d.url.startsWith("http") ? "_blank" : undefined}
                rel="noreferrer"
              >
                {d.label} <ExternalLink size={12} />
              </a>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

type Filter = "all" | "attention" | "done";

export default function SetupPage() {
  const [guide, setGuide] = useState<SetupGuide | null>(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<Filter>("all");

  const load = useCallback(async () => {
    try {
      setGuide(await guideApi.get());
      // The sidebar badge listens for this, so it drops the moment a step is fixed.
      window.dispatchEvent(new Event("setup-guide-updated"));
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not load the setup guide");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const visible = useMemo(() => {
    if (!guide) return [];
    return guide.groups
      .map((group) => ({
        ...group,
        steps: group.steps.filter((s) =>
          filter === "all"
            ? true
            : filter === "done"
              ? s.status === "done"
              : s.status === "todo" || s.status === "attention",
        ),
      }))
      .filter((group) => group.steps.length > 0);
  }, [guide, filter]);

  if (loading) {
    return (
      <div className="space-y-4 max-w-4xl mx-auto">
        <div className="skeleton h-10 w-64" />
        <div className="skeleton h-24 w-full" />
        <div className="skeleton h-40 w-full" />
        <div className="skeleton h-40 w-full" />
      </div>
    );
  }

  if (!guide) return null;

  const { progress, environment } = guide;
  const blocking = progress.blocking;

  return (
    <div className="space-y-5 max-w-4xl mx-auto">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm font-medium text-primary-600 mb-1 flex items-center gap-1">
            <GraduationCap size={15} /> TUTORIAL · LIVE STATUS
          </p>
          <h1 className="text-2xl sm:text-3xl font-bold">Set up everything</h1>
          <p className="text-gray-500 mt-1 max-w-2xl">
            Six sections, in the order that matters. Each step says whether it is done, what the
            server can see right now, and exactly what to paste where.
          </p>
        </div>
        <button className="btn-secondary" onClick={load}>
          <RefreshCw size={15} className="mr-1" /> Re-check
        </button>
      </div>

      <div className="card p-5 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="font-semibold flex items-center gap-2">
            {progress.ready_to_send ? (
              <>
                <Rocket size={17} className="text-success-600" /> You can send
              </>
            ) : (
              <>
                <ListChecks size={17} className="text-primary-600" /> {progress.done} of{" "}
                {progress.total} steps done
              </>
            )}
          </p>
          <p className="text-sm text-gray-500">
            {blocking > 0
              ? `${blocking} step${blocking === 1 ? "" : "s"} need${blocking === 1 ? "s" : ""} attention`
              : "Nothing is blocking you"}
            {progress.optional > 0 && ` · ${progress.optional} optional`}
          </p>
        </div>

        <div className="h-2.5 rounded-full bg-gray-200 dark:bg-gray-700 overflow-hidden">
          <div
            className={`h-full rounded-full transition-all ${
              blocking > 0 ? "bg-warning-400" : "bg-success-500"
            }`}
            style={{ width: `${progress.percent}%` }}
          />
        </div>

        <div className="flex flex-wrap gap-2 pt-1">
          {(
            [
              ["all", "Everything"],
              ["attention", `Needs attention${blocking ? ` (${blocking})` : ""}`],
              ["done", `Done (${progress.done})`],
            ] as [Filter, string][]
          ).map(([key, label]) => (
            <button
              key={key}
              onClick={() => setFilter(key)}
              className={`px-3 py-1.5 rounded-lg text-sm font-medium ${
                filter === key
                  ? "bg-primary-100 dark:bg-primary-900/30 text-primary-700 dark:text-primary-300"
                  : "text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700"
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        <p className="text-xs text-gray-500 pt-1">
          Running in <code>{environment.app_env}</code>
          {environment.public_base_url ? (
            <>
              {" "}
              · public address <code>{environment.public_base_url}</code>
            </>
          ) : (
            <> · no public address set</>
          )}
          {" · "}
          {environment.inline_poller
            ? "background work runs in the web process"
            : "background work needs a Celery worker"}
          {environment.google_oauth_ready
            ? " · Google OAuth ready"
            : " · Gmail connects with an app password"}
        </p>
      </div>

      {visible.length === 0 && (
        <div className="card p-10 text-center">
          <CheckCircle2 size={28} className="mx-auto text-success-500 mb-3" />
          <h3 className="font-semibold">Nothing left in this view</h3>
          <p className="text-sm text-gray-500 mt-1">
            Switch the filter to <em>Everything</em> to see the optional steps.
          </p>
        </div>
      )}

      {visible.map((group) => (
        <section key={group.id} className="space-y-3">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="text-lg font-bold">{group.label}</h2>
            <span className="text-xs text-gray-500">
              {group.done}/{group.total} done
            </span>
          </div>
          <p className="text-sm text-gray-500 -mt-2">{group.hint}</p>
          <div className="space-y-3">
            {group.steps.map((step) => (
              <StepCard
                key={step.id}
                step={step}
                defaultOpen={step.status === "todo" || step.status === "attention"}
              />
            ))}
          </div>
        </section>
      ))}

      <div className="card p-5 text-sm text-gray-600 dark:text-gray-300 space-y-2">
        <h3 className="font-semibold text-gray-900 dark:text-white">Where the written guides live</h3>
        <ul className="space-y-1 list-disc pl-5 text-xs">
          <li>
            <code>README.md</code> — what the app does, the email channel, and the AI connectors.
          </li>
          <li>
            <code>DEPLOY.md</code> — local, Docker and Render deployment, every environment
            variable, and the schema-repair behaviour on an existing database.
          </li>
          <li>
            <code>START_HERE.md</code>, <code>SMS_GATE.md</code>, <code>CALL_GATE.md</code>,{" "}
            <code>NOTIFICATIONS.md</code>, <code>SCHEDULING-AND-AUTOREPLY.md</code> — per-feature
            notes.
          </li>
          <li>
            <code>tools/arena-connector/AGENTS.md</code> — what to hand an Arena agent so it can
            drive this app.
          </li>
        </ul>
      </div>
    </div>
  );
}
