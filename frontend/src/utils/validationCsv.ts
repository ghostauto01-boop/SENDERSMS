import Papa from "papaparse";
import { detectColumns } from "./csv";
import type { ValidationEntry, ValidationRow } from "../api/validator";

export interface ValidationCsv {
  headers: string[];
  rows: string[][];
}
export interface ValidationMapping { email: string; phone: string; name: string }
export const MAX_CSV_BYTES = 10 * 1024 * 1024;
export const MAX_CSV_ROWS = 50000;

export function parseValidationCsv(text: string, hasHeader = true): ValidationCsv {
  const parsed = Papa.parse<string[]>(text.replace(/^\uFEFF/, ""), { skipEmptyLines: "greedy", dynamicTyping: false });
  const error = parsed.errors.find(e => e.code !== "UndetectableDelimiter");
  if (error) throw new Error(`CSV could not be read: ${error.message}`);
  const rows = parsed.data;
  if (!rows.length) throw new Error("The CSV file is empty.");
  const width = Math.max(...rows.slice(0, 100).map(row => row.length));
  const headers = hasHeader
    ? rows.shift()!.map((h, i) => h.trim() || `Column ${i + 1}`)
    : Array.from({ length: width }, (_, i) => `Column ${i + 1}`);
  if (!rows.length) throw new Error("The CSV has a header but no contact rows.");
  if (rows.length > MAX_CSV_ROWS) throw new Error(`Use files with up to ${MAX_CSV_ROWS.toLocaleString()} rows. Split this file and check each part; no rows have been checked.`);
  if (rows.some(row => row.length > headers.length)) throw new Error("Some rows have more columns than the header. Check the delimiter, quoting, or turn off ‘First row contains headers’. ");
  return { headers, rows };
}

export function detectValidationMapping(headers: string[]): ValidationMapping {
  const detected = detectColumns(headers);
  const find = (field: string) => {
    const i = headers.findIndex(h => detected[h] === field);
    return i < 0 ? "" : String(i);
  };
  return { email: find("email"), phone: find("phone_number"), name: find("first_name") };
}

export function validationEntries(csv: ValidationCsv, map: ValidationMapping): ValidationEntry[] {
  if (map.email === "" && map.phone === "") throw new Error("Map an email or phone column before validating.");
  const mapped = Object.values(map).filter(v => v !== "");
  if (new Set(mapped).size !== mapped.length) throw new Error("Choose a different CSV column for each field.");
  const cell = (row: string[], key: string) => key === "" ? "" : (row[Number(key)] || "").trim();
  return csv.rows.map((row, i) => {
    const entry = { email: cell(row, map.email), phone_number: cell(row, map.phone), name: cell(row, map.name), row: i + 1 };
    if (entry.email.length > 1000 || entry.phone_number.length > 1000 || entry.name.length > 500) {
      throw new Error(`Data row ${i + 1} has a value that is too long. Check the column mapping.`);
    }
    return entry;
  });
}

/** Every row, including unknown/missing/duplicates, can be exported safely. */
export function validationReportCsv(rows: ValidationRow[]): string {
  return Papa.unparse(rows.map((row, i) => ({
    row: row.row || i + 1,
    contact_id: row.contact_id ?? "",
    name: row.name,
    input_email: row.input_email || "",
    input_phone: row.input_phone || "",
    result: row.status,
    email_result: row.email?.verdict || "not_checked",
    normalized_email: row.email?.email || "",
    email_provider: row.email?.provider || "",
    email_syntax: row.email?.is_valid_syntax ?? "",
    email_mx: row.email?.accepts_mail ?? "",
    email_smtp: row.email?.smtp_can_connect ?? "",
    email_catch_all: row.email?.is_catch_all ?? "",
    email_disposable: row.email?.is_disposable ?? "",
    email_role: row.email?.is_role_account ?? "",
    email_free_provider: row.email?.is_free ?? "",
    email_reasons: row.email?.problems.join("; ") || "",
    suggested_email: row.email?.suggested_email || "",
    phone_result: row.phone?.verdict || "not_checked",
    normalized_phone: row.phone?.normalized || "",
    phone_reasons: row.phone?.problems.join("; ") || "",
    sending_blocks: row.blocked.join("; "),
    duplicate_of_row: row.duplicate_of ?? "",
    saved: row.saved,
    note: row.note || "",
    checked_at: row.checked_at,
  })), { escapeFormulae: true });
}

export function downloadValidationCsv(rows: ValidationRow[], name = "validator-results.csv") {
  const url = URL.createObjectURL(new Blob(["\uFEFF" + validationReportCsv(rows)], { type: "text/csv;charset=utf-8;" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Do not revoke synchronously before the browser starts the download.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
