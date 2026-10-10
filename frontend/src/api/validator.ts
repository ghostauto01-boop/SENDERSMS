import api from "./client";

export type ResultState = "good" | "bad" | "risky" | "unknown" | "missing";
export type CheckKind = "both" | "email" | "phone";
export interface ValidationEntry {
  email?: string | null;
  phone_number?: string | null;
  name?: string;
  row?: number;
}
export interface ChannelResult {
  verdict: string;
  problems: string[];
  email?: string | null;
  normalized?: string | null;
  provider?: string;
  suggested_email?: string | null;
  is_valid_syntax?: boolean;
  accepts_mail?: boolean | null;
  smtp_can_connect?: boolean | null;
  is_catch_all?: boolean | null;
  is_disposable?: boolean;
  is_role_account?: boolean;
  is_free?: boolean;
}
export interface ValidationRow {
  contact_id: number | null;
  row: number | null;
  name: string;
  input_email: string | null;
  input_phone: string | null;
  email: ChannelResult | null;
  phone: ChannelResult | null;
  status: ResultState;
  checked_at: string;
  saved: boolean;
  blocked: string[];
  note?: string;
  duplicate_of?: number;
}
export interface ValidatorStatus {
  engine: "builtin" | "reacher";
  api_key_required: boolean;
  reacher_configured: boolean;
  reacher_key_configured: boolean;
  dns_available: boolean;
  smtp_enabled: boolean;
  batch_size: number;
  phone_region: string;
  notice: string;
}
export interface SelfTest {
  passed: boolean;
  checks: { name: string; passed: boolean }[];
  checked_at: string;
  notice: string;
}
export interface ValidationSelection {
  scope: "all" | "list" | "ids";
  contact_ids?: number[];
  list_id?: number;
  search?: string;
  lead_status?: string;
  channel?: "sms" | "email";
  email_state?: string;
}
export interface ValidationBatch {
  items?: ValidationEntry[];
  contact_ids?: number[];
  check: CheckKind;
  deep: boolean;
  save: boolean;
}
export const validatorApi = {
  status: async () => (await api.get<ValidatorStatus>("/validator/status")).data,
  selfTest: async () => (await api.post<SelfTest>("/validator/self-test")).data,
  selection: async (selection: ValidationSelection) =>
    (await api.post<{ contact_ids: number[]; total: number }>("/validator/selection", selection)).data,
  batch: async (batch: ValidationBatch) =>
    (await api.post<{ items: ValidationRow[]; processed: number }>("/validator/batch", batch, { timeout: 180000 })).data,
};
