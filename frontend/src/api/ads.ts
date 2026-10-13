import api from "./client";

/** Typed client for the SMS Ads Manager API (/api/v1/ads). */

export type AdsStats = {
  assigned: number;
  sent: number;
  delivered: number;
  failed: number;
  pending: number;
  skipped: number;
  replies: number;
  positive_replies: number;
  negative_replies: number;
  conversions: number;
  opt_outs: number;
  delivery_rate: number;
  reply_rate: number;
  positive_reply_rate: number;
  negative_reply_rate: number;
  conversion_rate: number;
  opt_out_rate: number;
  failure_rate: number;
  clicks?: number;
  click_rate?: number;
  opens?: number;
  open_rate?: number;
  engagement_rate?: number;
  credits_used?: number;
  score?: number;
  followups_due?: number;
  meetings?: number;
  meeting_rate?: number;
  state?: string;
  is_winner?: boolean;
  needs_more_data?: boolean;
  winner_id?: number | null;
};

export type AdsCampaign = {
  id: number;
  name: string;
  description?: string | null;
  objective: string;
  status: string;
  // Why it is paused, when an operator paused it.
  paused_reason?: string | null;
  paused_at?: string | null;
  channel?: string;
  email_account_id?: number | null;
  fallback_email_account_id?: number | null;
  email_account_ids?: number[] | null;
  sender_distribution?: string;
  email_sender_weights?: Record<string, number> | null;
  subject?: string | null;
  track_opens?: boolean;
  track_clicks?: boolean;
  daily_limit?: number | null;
  total_limit?: number | null;
  start_date?: string | null;
  end_date?: string | null;
  send_start_hour?: number | null;
  send_end_hour?: number | null;
  send_days?: string | null;
  timezone_name?: string | null;
  drip_mode: string;
  drip_batch_size: number;
  drip_interval_minutes: number;
  pacing: string;
  continuous: boolean;
  always_on: boolean;
  max_per_contact_per_day?: number | null;
  max_per_contact_per_week?: number | null;
  optimization_mode: string;
  queued_edit_policy: string;
  priority: string;
  test_mode: boolean;
  auto_optimize?: boolean;
  optimize_metric?: string;
  optimize_min_sends?: number;
  optimize_min_gap_pct?: number;
  optimize_action?: string;
  optimize_last_run_at?: string | null;
  sent_count: number;
  last_state?: string | null;
  last_activity_at?: string | null;
  last_error?: string | null;
  state_changed_at?: string | null;
  created_at: string;
  updated_at: string;
  stats?: AdsStats;
  state?: string;
  score?: number;
};

export type AdsSet = {
  id: number;
  campaign_id: number;
  name: string;
  status: string;
  list_ids?: string | null;
  contact_ids?: string | null;
  audience_id?: number | null;
  include_tags?: string | null;
  exclude_tags?: string | null;
  include_statuses?: string | null;
  exclude_statuses?: string | null;
  city?: string | null;
  state?: string | null;
  industry?: string | null;
  activity_filter?: string | null;
  exclude_campaign_ids?: string | null;
  daily_limit?: number | null;
  split_mode: string;
  auto_optimize?: boolean;
  stats?: AdsStats;
};

export type AdsAudience = {
  id: number;
  name: string;
  description?: string | null;
  list_ids?: string | null;
  contact_ids?: string | null;
  include_tags?: string | null;
  exclude_tags?: string | null;
  include_statuses?: string | null;
  exclude_statuses?: string | null;
  city?: string | null;
  state?: string | null;
  industry?: string | null;
  activity_filter?: string | null;
  exclude_campaign_ids?: string | null;
  created_at: string;
  updated_at: string;
  match_count?: number;
  list_count?: number;
  explicit_contacts?: number;
};

export type AdsCreative = {
  id: number;
  set_id: number;
  campaign_id: number;
  name: string;
  status: string;
  body: string;
  subject?: string | null;
  html_body?: string | null;
  email_account_id?: number | null;
  cta?: string | null;
  tracking_link?: string | null;
  allocation: number;
  /** Exact SMS count for quota split mode (null/0 = no cap). */
  send_quota?: number | null;
  current_version: number;
  is_deleted: boolean;
  /** Saved template this creative was started from (sync anchor). */
  template_id?: number | null;
  stats?: AdsStats;
};

export type FollowUpStep = {
  id: number;
  campaign_id?: number;
  step_order: number;
  name?: string | null;
  /** Hours to wait before this step (from the previous message). */
  wait_hours: number;
  /** The same wait under the documented API name. */
  delay_hours?: number;
  condition: string;
  action: string;
  body?: string | null;
  subject?: string | null;
  template_id?: number | null;
  action_value?: string | null;
  is_active: boolean;
  created_at?: string | null;
  updated_at?: string | null;
};

export type FollowUpTemplate = {
  id: number;
  name: string;
  description?: string | null;
  channel: string;
  subject?: string | null;
  body: string;
  condition: string;
  action: string;
  delay_hours: number;
  is_active: boolean;
  usage_count: number;
};

/** Every condition the follow-up engine understands (mirrors the backend). */
export const FOLLOWUP_CONDITIONS: { value: string; label: string; emailOnly?: boolean }[] = [
  { value: "no_reply", label: "No reply" },
  { value: "opened_no_click", label: "Opened but no click", emailOnly: true },
  { value: "clicked_no_reply", label: "Clicked but no reply" },
  { value: "negative_reply", label: "Replied negatively" },
  { value: "positive_reply", label: "Replied positively" },
  { value: "replied", label: "Replied (any)" },
  { value: "positive", label: "Reply sentiment: positive" },
  { value: "negative", label: "Reply sentiment: negative" },
  { value: "interested", label: "Lead status: interested" },
  { value: "not_interested", label: "Lead status: not interested" },
  { value: "converted", label: "Lead status: converted" },
  { value: "opened", label: "Opened (email)", emailOnly: true },
  { value: "not_opened", label: "Never opened", emailOnly: true },
  { value: "link_clicked", label: "Link clicked" },
  { value: "link_not_clicked", label: "Link not clicked" },
  { value: "meeting_scheduled", label: "Meeting booked" },
  { value: "always", label: "Always (no condition)" },
];

/** Every action a follow-up step may carry (mirrors the backend). */
export const FOLLOWUP_ACTIONS: { value: string; label: string }[] = [
  { value: "send_email", label: "Send email" },
  { value: "send_sms", label: "Send SMS" },
  { value: "add_tag", label: "Add tag" },
  { value: "remove_tag", label: "Remove tag" },
  { value: "change_status", label: "Change lead status" },
  { value: "suppress", label: "Suppress contact" },
  { value: "stop", label: "Stop the workflow" },
];

export type CampaignDetail = AdsCampaign & {
  sets: AdsSet[];
  creatives: AdsCreative[];
  followup_steps: FollowUpStep[];
};

const unwrap = <T,>(p: Promise<{ data: T }>) => p.then((r) => r.data);

export const adsApi = {
  overview: (params: any = {}) => unwrap<any>(api.get("/ads/overview", { params })),
  reference: () => unwrap<any>(api.get("/ads/reference")),
  search: (q: string) => unwrap<any>(api.get("/ads/search", { params: { q } })),
  activity: (channel?: string) =>
    unwrap<any>(api.get("/ads/activity", { params: channel ? { channel } : {} })),

  listCampaigns: (params: any = {}) => unwrap<{ total: number; items: AdsCampaign[] }>(api.get("/ads/campaigns", { params })),
  createCampaign: (body: any) => unwrap<AdsCampaign>(api.post("/ads/campaigns", body)),
  getCampaign: (id: number) => unwrap<CampaignDetail>(api.get(`/ads/campaigns/${id}`)),
  updateCampaign: (id: number, body: any) => unwrap<AdsCampaign>(api.patch(`/ads/campaigns/${id}`, body)),
  deleteCampaign: (id: number) => unwrap<void>(api.delete(`/ads/campaigns/${id}`)),
  validate: (id: number) => unwrap<any>(api.post(`/ads/campaigns/${id}/validate`)),
  simulate: (id: number) => unwrap<any>(api.post(`/ads/campaigns/${id}/simulate`)),
  launch: (id: number) => unwrap<any>(api.post(`/ads/campaigns/${id}/launch`)),
  pause: (id: number) => unwrap<any>(api.post(`/ads/campaigns/${id}/pause`)),
  resume: (id: number) => unwrap<any>(api.post(`/ads/campaigns/${id}/resume`)),
  complete: (id: number) => unwrap<any>(api.post(`/ads/campaigns/${id}/complete`)),
  archive: (id: number) => unwrap<any>(api.post(`/ads/campaigns/${id}/archive`)),
  duplicate: (id: number, copyAudience = false) =>
    unwrap<AdsCampaign>(api.post(`/ads/campaigns/${id}/duplicate`, null, { params: { copy_audience: copyAudience } })),
  dispatch: (id: number) => unwrap<any>(api.post(`/ads/campaigns/${id}/dispatch`)),
  addContacts: (id: number, body: any) => unwrap<any>(api.post(`/ads/campaigns/${id}/contacts`, body)),
  rebuild: (id: number) => unwrap<any>(api.post(`/ads/campaigns/${id}/rebuild-audience`)),
  audience: (id: number, params: any = {}) => unwrap<any>(api.get(`/ads/campaigns/${id}/audience`, { params })),
  analytics: (id: number) => unwrap<any>(api.get(`/ads/campaigns/${id}/analytics`)),
  campaignActivity: (id: number) => unwrap<any>(api.get(`/ads/campaigns/${id}/activity`)),
  campaignJobs: (id: number) => unwrap<any>(api.get(`/ads/campaigns/${id}/jobs`)),
  retryCampaignJob: (id: number) => unwrap<any>(api.post(`/ads/campaigns/${id}/jobs/retry`)),
  sendLogs: (id: number, params: any = {}) => unwrap<any>(api.get(`/ads/campaigns/${id}/send-logs`, { params })),
  bulkCampaigns: (body: any) => unwrap<any>(api.post("/ads/campaigns/bulk", body)),

  listSets: (campaignId: number) => unwrap<{ items: AdsSet[] }>(api.get(`/ads/campaigns/${campaignId}/sets`)),
  createSet: (campaignId: number, body: any) => unwrap<AdsSet>(api.post(`/ads/campaigns/${campaignId}/sets`, body)),
  updateSet: (id: number, body: any) => unwrap<AdsSet>(api.patch(`/ads/sets/${id}`, body)),
  deleteSet: (id: number) => unwrap<void>(api.delete(`/ads/sets/${id}`)),
  previewSet: (id: number) => unwrap<any>(api.get(`/ads/sets/${id}/preview`)),
  previewTargeting: (body: any) => unwrap<any>(api.post("/ads/sets/preview", body)),
  duplicateSet: (id: number) => unwrap<AdsSet>(api.post(`/ads/sets/${id}/duplicate`)),
  pauseLosers: (setId: number, minSends = 10) =>
    unwrap<any>(api.post(`/ads/sets/${setId}/pause-losers`, null, { params: { min_sends: minSends } })),

  listCreatives: (setId: number) => unwrap<{ items: AdsCreative[] }>(api.get(`/ads/sets/${setId}/creatives`)),
  createCreative: (setId: number, body: any) => unwrap<AdsCreative>(api.post(`/ads/sets/${setId}/creatives`, body)),
  updateCreative: (id: number, body: any) => unwrap<AdsCreative>(api.patch(`/ads/creatives/${id}`, body)),
  deleteCreative: (id: number) => unwrap<void>(api.delete(`/ads/creatives/${id}`)),
  duplicateCreative: (id: number) => unwrap<AdsCreative>(api.post(`/ads/creatives/${id}/duplicate`)),
  promoteCreative: (id: number) => unwrap<any>(api.post(`/ads/creatives/${id}/promote`)),
  creativeVersions: (id: number) => unwrap<any>(api.get(`/ads/creatives/${id}/versions`)),
  creativeAnalytics: (id: number) => unwrap<any>(api.get(`/ads/creatives/${id}/analytics`)),

  removeAudienceContact: (campaignId: number, assignmentId: number) =>
    unwrap<void>(api.delete(`/ads/campaigns/${campaignId}/audience/${assignmentId}`)),
  bulkRemoveAudience: (campaignId: number, ids: number[] = [], all = false, status?: string) =>
    unwrap<any>(api.post(`/ads/campaigns/${campaignId}/audience/bulk-remove`, {
      ids,
      action: "remove",
      scope: all ? "all" : "ids",
      status: status || undefined,
    })),

  optimizationStatus: (campaignId: number) => unwrap<any>(api.get(`/ads/campaigns/${campaignId}/optimization`)),
  runOptimization: (campaignId: number, dryRun = false) =>
    unwrap<any>(api.post(`/ads/campaigns/${campaignId}/optimize`, { dry_run: dryRun })),

  listAudiences: (search = "") =>
    unwrap<{ total: number; items: AdsAudience[] }>(api.get("/ads/audiences", { params: search ? { search } : {} })),
  createAudience: (body: any) => unwrap<AdsAudience>(api.post("/ads/audiences", body)),
  getAudience: (id: number) => unwrap<any>(api.get(`/ads/audiences/${id}`)),
  updateAudience: (id: number, body: any) => unwrap<AdsAudience>(api.patch(`/ads/audiences/${id}`, body)),
  deleteAudience: (id: number) => unwrap<void>(api.delete(`/ads/audiences/${id}`)),
  duplicateAudience: (id: number) => unwrap<AdsAudience>(api.post(`/ads/audiences/${id}/duplicate`)),
  previewAudience: (id: number) => unwrap<any>(api.get(`/ads/audiences/${id}/preview`)),
  attachAudience: (id: number, body: { campaign_id: number; set_id?: number | null; new_set_name?: string }) =>
    unwrap<any>(api.post(`/ads/audiences/${id}/attach`, body)),

  createStep: (campaignId: number, body: any) => unwrap<FollowUpStep>(api.post(`/ads/campaigns/${campaignId}/followup-steps`, body)),
  /** Every step on a campaign, in chain order. */
  listSteps: (campaignId: number, includeInactive = true) =>
    unwrap<{ items: FollowUpStep[]; total: number }>(
      api.get(`/ads/campaigns/${campaignId}/followup-steps`, { params: { include_inactive: includeInactive } }),
    ),
  getStep: (id: number) => unwrap<any>(api.get(`/ads/followup-steps/${id}`)),
  /** Edit any subset of a step after creation. */
  updateStep: (campaignId: number, id: number, body: any) =>
    unwrap<FollowUpStep>(api.patch(`/ads/campaigns/${campaignId}/followup-steps/${id}`, body)),
  /** Soft-delete: the step stops sending, its history survives. */
  deleteStep: (campaignId: number, id: number) =>
    unwrap<{ ok: boolean; step_id: number }>(api.delete(`/ads/campaigns/${campaignId}/followup-steps/${id}`)),

  followupTemplates: (params: any = {}) =>
    unwrap<{ items: FollowUpTemplate[]; total: number }>(api.get("/ads/followup-templates", { params })),
  createFollowupTemplate: (body: any) =>
    unwrap<FollowUpTemplate>(api.post("/ads/followup-templates", body)),
  updateFollowupTemplate: (id: number, body: any) =>
    unwrap<FollowUpTemplate>(api.patch(`/ads/followup-templates/${id}`, body)),
  deleteFollowupTemplate: (id: number) => unwrap<void>(api.delete(`/ads/followup-templates/${id}`)),

  followups: (bucket: string, channel?: string) =>
    unwrap<any>(api.get("/ads/followups", { params: { bucket, ...(channel ? { channel } : {}) } })),
  createFollowup: (body: any) => unwrap<any>(api.post("/ads/followups", body)),
  followupAction: (id: number, action: string, params: any = {}) =>
    unwrap<any>(api.post(`/ads/followups/${id}/${action}`, null, { params })),
  processFollowups: () => unwrap<any>(api.post("/ads/followups/process")),

  calendar: (params: any = {}) => unwrap<any>(api.get("/ads/calendar", { params })),
  createEvent: (body: any) => unwrap<any>(api.post("/ads/calendar", body)),
  deleteEvent: (id: number) => unwrap<void>(api.delete(`/ads/calendar/${id}`)),

  suppression: (params: any = {}) => unwrap<any>(api.get("/ads/suppression", { params })),
  addSuppression: (body: any) => unwrap<any>(api.post("/ads/suppression", body)),
  removeSuppression: (id: number) => unwrap<void>(api.delete(`/ads/suppression/${id}`)),

  contactAction: (id: number, body: any) => unwrap<any>(api.post(`/ads/contacts/${id}/action`, body)),
  contactTimeline: (id: number) => unwrap<any>(api.get(`/ads/contacts/${id}/timeline`)),

  exportUrl: (kind: string, campaignId?: number, channel?: string) => {
    const params = new URLSearchParams();
    if (campaignId) params.set("campaign_id", String(campaignId));
    if (channel) params.set("channel", channel);
    const query = params.toString();
    return `/api/v1/ads/export/${kind}${query ? `?${query}` : ""}`;
  },
};

export default adsApi;
