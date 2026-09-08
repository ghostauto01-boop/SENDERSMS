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
  credits_used?: number;
  score?: number;
  followups_due?: number;
  meetings?: number;
  meeting_rate?: number;
  state?: string;
};

export type AdsCampaign = {
  id: number;
  name: string;
  description?: string | null;
  objective: string;
  status: string;
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
  sent_count: number;
  last_state?: string | null;
  last_activity_at?: string | null;
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
  stats?: AdsStats;
};

export type AdsCreative = {
  id: number;
  set_id: number;
  campaign_id: number;
  name: string;
  status: string;
  body: string;
  cta?: string | null;
  tracking_link?: string | null;
  allocation: number;
  current_version: number;
  is_deleted: boolean;
  stats?: AdsStats;
};

export type FollowUpStep = {
  id: number;
  step_order: number;
  name?: string | null;
  wait_hours: number;
  condition: string;
  action: string;
  body?: string | null;
  action_value?: string | null;
  is_active: boolean;
};

export type CampaignDetail = AdsCampaign & {
  sets: AdsSet[];
  creatives: AdsCreative[];
  followup_steps: FollowUpStep[];
};

const unwrap = <T,>(p: Promise<{ data: T }>) => p.then((r) => r.data);

export const adsApi = {
  overview: () => unwrap<any>(api.get("/ads/overview")),
  reference: () => unwrap<any>(api.get("/ads/reference")),
  search: (q: string) => unwrap<any>(api.get("/ads/search", { params: { q } })),
  activity: () => unwrap<any>(api.get("/ads/activity")),

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
  bulkCampaigns: (body: any) => unwrap<any>(api.post("/ads/campaigns/bulk", body)),

  listSets: (campaignId: number) => unwrap<{ items: AdsSet[] }>(api.get(`/ads/campaigns/${campaignId}/sets`)),
  createSet: (campaignId: number, body: any) => unwrap<AdsSet>(api.post(`/ads/campaigns/${campaignId}/sets`, body)),
  updateSet: (id: number, body: any) => unwrap<AdsSet>(api.patch(`/ads/sets/${id}`, body)),
  deleteSet: (id: number) => unwrap<void>(api.delete(`/ads/sets/${id}`)),
  previewSet: (id: number) => unwrap<any>(api.get(`/ads/sets/${id}/preview`)),

  listCreatives: (setId: number) => unwrap<{ items: AdsCreative[] }>(api.get(`/ads/sets/${setId}/creatives`)),
  createCreative: (setId: number, body: any) => unwrap<AdsCreative>(api.post(`/ads/sets/${setId}/creatives`, body)),
  updateCreative: (id: number, body: any) => unwrap<AdsCreative>(api.patch(`/ads/creatives/${id}`, body)),
  deleteCreative: (id: number) => unwrap<void>(api.delete(`/ads/creatives/${id}`)),
  duplicateCreative: (id: number) => unwrap<AdsCreative>(api.post(`/ads/creatives/${id}/duplicate`)),
  promoteCreative: (id: number) => unwrap<any>(api.post(`/ads/creatives/${id}/promote`)),
  creativeVersions: (id: number) => unwrap<any>(api.get(`/ads/creatives/${id}/versions`)),

  createStep: (campaignId: number, body: any) => unwrap<any>(api.post(`/ads/campaigns/${campaignId}/followup-steps`, body)),
  updateStep: (id: number, body: any) => unwrap<any>(api.patch(`/ads/followup-steps/${id}`, body)),
  deleteStep: (id: number) => unwrap<void>(api.delete(`/ads/followup-steps/${id}`)),

  followups: (bucket: string) => unwrap<any>(api.get("/ads/followups", { params: { bucket } })),
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

  exportUrl: (kind: string, campaignId?: number) =>
    `/api/v1/ads/export/${kind}${campaignId ? `?campaign_id=${campaignId}` : ""}`,
};

export default adsApi;
