import api from "./client";

/**
 * Typed client for the in-app setup tutorial (/api/v1/guide).
 *
 * The guide is not documentation: every step's status is read live from the
 * database and the environment, so it says "your gateway still has the old
 * webhook URL" rather than "register the webhook URL". That is the difference
 * between a tutorial someone reads once and one that tells them what is actually
 * wrong with their deployment.
 */

export type StepStatus = "done" | "todo" | "attention" | "optional";

export type GuideCopy = { label: string; value: string };
export type GuideDoc = { label: string; url: string };

export type GuideStep = {
  id: string;
  group: string;
  title: string;
  status: StepStatus;
  /** One sentence on why this matters — the symptom it prevents. */
  why: string;
  /** What the app can currently see: live values, counts, the failure. */
  detail: string;
  steps: string[];
  route?: string | null;
  route_label?: string | null;
  copy: GuideCopy[];
  docs: GuideDoc[];
  verify: string;
};

export type GuideGroup = {
  id: string;
  label: string;
  hint: string;
  steps: GuideStep[];
  done: number;
  total: number;
  needs_attention: number;
};

export type GuideProgress = {
  done: number;
  blocking: number;
  optional: number;
  total: number;
  percent: number;
  /** True once nothing required for sending is missing. */
  ready_to_send: boolean;
};

export type SetupGuide = {
  groups: GuideGroup[];
  steps: GuideStep[];
  progress: GuideProgress;
  next: string[];
  environment: {
    app_env: string;
    public_base_url?: string | null;
    inline_poller: boolean;
    google_oauth_ready: boolean;
    gmail_send_replies: boolean;
    gmail_rescue_from_spam: boolean;
    mcp_oauth_enabled: boolean;
  };
};

export type GuideSummary = GuideProgress & {
  next_step?: string | null;
  next_route?: string | null;
};

export const guideApi = {
  get: () => api.get<SetupGuide>("/guide").then((r) => r.data),
  summary: () => api.get<GuideSummary>("/guide/summary").then((r) => r.data),
};

export default guideApi;
