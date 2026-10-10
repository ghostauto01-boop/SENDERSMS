import { describe, expect, it } from "vitest";
import { detectValidationMapping, parseValidationCsv, validationEntries, validationReportCsv } from "./validationCsv";
import type { ValidationRow } from "../api/validator";

const mapping = { name: "0", email: "1", phone: "2" };

describe("Validator CSVs", () => {
  it("handles BOM, quoted commas, leading zeros and invalid emails without dropping rows", () => {
    const csv = parseValidationCsv('\uFEFFName,Email,Phone\r\n"Ada, Obi",bad,08034567891\r\nEmpty,,\r\n');
    expect(detectValidationMapping(csv.headers)).toEqual(mapping);
    expect(validationEntries(csv, mapping)).toEqual([
      { name: "Ada, Obi", email: "bad", phone_number: "08034567891", row: 1 },
      { name: "Empty", email: "", phone_number: "", row: 2 },
    ]);
  });
  it("supports headerless single-column files and does not lose the first row", () => {
    const csv = parseValidationCsv("08034567891\n123", false);
    expect(validationEntries(csv, { email: "", phone: "0", name: "" })).toHaveLength(2);
    expect(csv.rows[0][0]).toBe("08034567891");
  });
  it("supports semicolon-separated CSV", () => {
    const csv = parseValidationCsv("Name;Email;Phone\nAda;bad;08034567891\nObi;good@domain.com;09034567891");
    expect(validationEntries(csv, mapping)[0].phone_number).toBe("08034567891");
  });
  it("does not map number-of-guests as phone", () => {
    expect(detectValidationMapping(["Phone Number", "Number of Guests", "Email"])).toEqual({ name: "", phone: "0", email: "2" });
  });
  it("requires at least one contact column and prevents accidental mapping collisions", () => {
    const csv = parseValidationCsv("Name,Email,Phone\nAda,bad,123");
    expect(() => validationEntries(csv, { name: "0", email: "", phone: "" })).toThrow(/Map an email or phone/);
    expect(() => validationEntries(csv, { name: "0", email: "1", phone: "1" })).toThrow(/different CSV column/);
  });
  it("rejects empty/header-only/malformed files rather than showing false success", () => {
    expect(() => parseValidationCsv("")).toThrow(/empty/);
    expect(() => parseValidationCsv("Email,Phone\n")).toThrow(/no contact rows/);
    expect(() => parseValidationCsv('Email,Phone\n"unterminated')).toThrow(/could not be read/);
    expect(() => parseValidationCsv("Email,Phone\nbad,123,extra")).toThrow(/more columns/);
  });
  it("exports every outcome, reasons and original inputs; neutralizes spreadsheet formulas", () => {
    const rows = ["good", "bad", "risky", "unknown", "missing"].map((status, i) => ({
      row: i + 1, contact_id: i, name: '=HYPERLINK("https://attacker.invalid")', input_email: "bad", input_phone: "+2348034567891", email: { verdict: status, problems: ["explanation"], provider: "builtin", suggested_email: "fixed@domain.com" }, phone: null, status, saved: false, blocked: ["Email opted out"], checked_at: "2026-10-10T00:00:00Z",
    } as ValidationRow));
    const report = validationReportCsv(rows);
    expect(report).toContain("email_reasons");
    expect(report).toContain("explanation");
    expect(report).toContain("fixed@domain.com");
    expect(report).toContain("'=HYPERLINK");
    expect(report.split("\r\n")).toHaveLength(6);
    for (const status of ["good", "bad", "risky", "unknown", "missing"]) expect(report).toContain(status);
  });
});
