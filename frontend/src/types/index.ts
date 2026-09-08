export interface User {
  id: number;
  username: string;
  display_name: string | null;
  role: string;
  is_active: boolean;
}

export interface LoginResponse {
  success: boolean;
  user_id: number;
  username: string;
  role: string;
  message: string;
}

export interface Contact {
  id: number;
  first_name: string | null;
  last_name: string | null;
  business_name: string | null;
  phone_number: string;
  email: string | null;
  city: string | null;
  state: string | null;
  country: string;
  website: string | null;
  industry: string | null;
  source: string | null;
  lead_status: string;
  consent_status: string;
  has_consented: boolean;
  is_opted_out: boolean;
  notes: string | null;
  custom_fields: string | null;
  tags: string[];
  messages_sent: number;
  messages_received: number;
  last_contacted_at: string | null;
  last_reply_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ContactList {
  total: number;
  items: Contact[];
}

export interface Campaign {
  id: number;
  name: string;
  description: string | null;
  status: string;
  list_id: number | null;
  template_id: number | null;
  message_body: string | null;
  sequence_id: number | null;
  gateway_setting_id: number | null;
  total_contacts: number;
  messages_sent: number;
  messages_delivered: number;
  messages_failed: number;
  replies: number;
  interested: number;
  // Future time the campaign launches by itself. null = manual start.
  scheduled_start_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface Template {
  id: number;
  name: string;
  category: string | null;
  body: string;
  char_count: number;
  segment_count: number;
  is_active: boolean;
  use_count: number;
  created_at: string;
  updated_at: string;
}

export interface SequenceStep {
  id: number;
  step_order: number;
  step_type: string;
  config: string | null;
  wait_duration_hours: number | null;
  template_id: number | null;
  condition_type: string | null;
  condition_value: string | null;
  true_branch_step_order: number | null;
  false_branch_step_order: number | null;
}

export interface SequenceData {
  id: number;
  name: string;
  description: string | null;
  current_version: number;
  is_active: boolean;
  steps: SequenceStep[];
  created_at: string;
  updated_at: string;
}

export interface Conversation {
  id: number;
  contact_id: number;
  contact_name: string;
  contact_phone: string;
  contact_lead_status: string;
  campaign_id: number | null;
  status: string;
  message_count: number;
  unread_count: number;
  last_message_preview: string | null;
  last_message_at: string | null;
  created_at: string;
}

export interface Message {
  id: number;
  direction: string;
  body: string;
  status: string;
  segment_count: number;
  char_count: number;
  sent_at: string | null;
  delivered_at: string | null;
  created_at: string;
  provider_message_id: string | null;
  ai_sentiment?: string | null;
  ai_intent?: string | null;
  ai_confidence?: number | null;
}

export interface AutomationCondition {
  field: string;
  op: string;
  value: string;
}

export interface AutomationAction {
  type: string;
  value?: string;
  body?: string;
  delay_minutes?: number;
}

export interface Automation {
  id: number;
  name: string;
  description: string | null;
  is_enabled: boolean;
  priority: number;
  trigger_type: string;
  conditions: AutomationCondition[];
  match_all: boolean;
  actions: AutomationAction[];
  times_triggered: number;
  last_triggered_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ConversationDetail {
  id: number;
  contact: {
    id: number;
    first_name: string;
    last_name: string;
    business_name: string;
    phone_number: string;
    email: string;
    lead_status: string;
    city: string;
    state: string;
    website: string;
    industry: string;
    notes: string;
    is_opted_out: boolean;
  } | null;
  campaign_id: number | null;
  status: string;
  sequence_paused: boolean;
  messages: Message[];
}

export interface DashboardStats {
  total_contacts: number;
  contacts_contacted: number;
  messages_sent: number;
  messages_delivered: number;
  messages_failed: number;
  replies: number;
  reply_rate: number;
  delivery_rate: number;
  interested_leads: number;
  active_campaigns: number;
  followups_due_today: number;
  overdue_followups: number;
  completed_campaigns: number;
  gateway_status: string;
  recent_conversations: Array<{
    id: number;
    contact_id: number;
    contact_name: string;
    status: string;
    last_message_preview: string | null;
    last_message_at: string | null;
    unread_count: number;
  }>;
  recent_campaigns: Array<{
    id: number;
    name: string;
    status: string;
    messages_sent: number;
    replies: number;
    updated_at: string;
  }>;
  lead_distribution: Record<string, number>;
}

export interface FollowUp {
  id: number;
  contact_id: number;
  contact_name: string;
  contact_phone: string | null;
  campaign_id: number | null;
  sequence_id: number | null;
  sequence_step_order: number | null;
  status: string;
  scheduled_at: string | null;
  executed_at: string | null;
  message_text: string | null;
  attempt_count: number;
  max_attempts: number;
  notify_on_due: boolean;
  last_error: string | null;
}

export interface GatewaySettings {
  configured: boolean;
  is_enabled: boolean;
  provider: string;
  base_url: string | null;
  username: string | null;
  password: string;
  webhook_url: string | null;
  webhook_secret: string;
  timeout: number;
  retry_count: number;
  polling_interval: number;
  connection_status: string;
  last_successful_connection: string | null;
  last_error: string | null;
  sim_number: number;
}

export interface NotificationProviderSettings {
  id: number;
  provider: string;
  is_enabled: boolean;
  notify_new_reply: boolean;
  notify_campaign_completed: boolean;
  notify_campaign_failed: boolean;
  notify_gateway_offline: boolean;
  notify_followup_due: boolean;
  notify_system_error: boolean;
}

export interface AnalyticsOverview {
  total_sms: number;
  sent: number;
  delivered: number;
  failed: number;
  delivery_rate: number;
  replies: number;
  reply_rate: number;
  opt_outs: number;
  followups: number;
  interested_leads: number;
  period_days: number;
}

export interface PaginatedResponse<T> {
  total: number;
  items: T[];
}

export interface ContactVariable {
  id: number;
  field_key: string;
  label: string;
  shortcode: string;
  fallback_text: string | null;
  description: string | null;
  source: string;
  is_active: boolean;
  contact_count: number;
  sample_value: string | null;
  created_at: string;
  updated_at: string;
}

export interface MessageAnalysis {
  preview: string;
  resolved: Array<{ shortcode: string; field_key: string; value: string }>;
  empty: Array<{ shortcode: string; field_key: string }>;
  unknown: string[];
  will_remove: string[];
}

export interface ContactProfileField {
  field_key: string;
  label: string;
  shortcode: string;
  value: string;
  source: string;
  is_active: boolean;
}

export interface ContactProfile {
  contact_id: number;
  display_name: string;
  phone_number: string;
  lead_status: string;
  is_opted_out: boolean;
  notes: string | null;
  tags: string[];
  messages_sent: number;
  messages_received: number;
  last_contacted_at: string | null;
  last_reply_at: string | null;
  created_at: string | null;
  fields: ContactProfileField[];
}

export interface CampaignFollowUp {
  id: number;
  campaign_id: number;
  step_order: number;
  name: string | null;
  message_text: string | null;
  template_id: number | null;
  delay_minutes: number;
  stop_on_reply: boolean;
  stop_on_opt_out: boolean;
  stop_on_lead_status: string | null;
  send_start_hour: number | null;
  send_end_hour: number | null;
  is_active: boolean;
  sent_count: number;
  stopped_count: number;
  created_at: string;
  updated_at: string;
  stats?: Record<string, number>;
}

export interface CampaignWithFollowUps {
  id: number;
  name: string;
  status: string;
  total_contacts: number;
  messages_sent: number;
  replies: number;
  followup_count: number;
  followups_sent: number;
}

export interface CampaignFollowUpPreviewRow {
  contact_id: number;
  name: string;
  reason: string;
}

export interface CampaignFollowUpPreview {
  followup_id: number;
  campaign_id: number;
  counts: { send: number; stop: number; wait: number; done: number };
  will_send: CampaignFollowUpPreviewRow[];
  will_stop: CampaignFollowUpPreviewRow[];
  waiting: CampaignFollowUpPreviewRow[];
  already_processed: Array<{ contact_id: number; name: string; status: string; reason: string | null }>;
}

export interface MeetingAttendee {
  id: number;
  contact_id: number;
  status: string;
  notified: boolean;
  name: string;
  phone: string;
}

export interface Meeting {
  id: number;
  title: string;
  description: string | null;
  event_type: string;
  status: string;
  starts_at: string;
  ends_at: string;
  all_day: boolean;
  location: string | null;
  meeting_link: string | null;
  contact_id: number | null;
  contact_name: string | null;
  conversation_id: number | null;
  campaign_id: number | null;
  tags: string[];
  send_invite_sms: boolean;
  invite_template_id: number | null;
  invite_body: string | null;
  invite_sent: boolean;
  send_sms_reminder: boolean;
  reminder_minutes: number[];
  reminder_template_id: number | null;
  reminder_body: string | null;
  reminders_sent: Record<string, string>;
  outcome_notes: string | null;
  attendees: MeetingAttendee[];
  created_at: string;
  updated_at: string;
}

export interface TagCount {
  name: string;
  count: number;
}

export interface ContactListSummary {
  id: number;
  name: string;
  description: string | null;
  contact_count: number;
}
