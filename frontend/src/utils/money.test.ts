import { describe, it, expect } from "vitest";
import {
  CURRENCY_CODES, CURRENCY_META, dateInputToIso, formatDate, formatMoney,
  formatMoneyCompact, minorToInput, parseMoneyToMinor, toDateInput,
} from "./money";

/**
 * Money handling is the one place a rounding bug becomes a wrong charge, so
 * these tests spell out the conversions rather than trusting the formatter.
 */

describe("money", () => {
  it("formats minor units in the invoice currency", () => {
    expect(formatMoney(268_750_00, "NGN")).toBe("₦268,750.00");
    expect(formatMoney(1_000, "USD")).toBe("$10.00");
    expect(formatMoney(0, "NGN")).toBe("₦0.00");
    // Two decimal places everywhere, even when the amount is whole.
    expect(formatMoney(5_00, "USD")).toBe("$5.00");
  });

  it("compacts large amounts without losing the unit", () => {
    expect(formatMoneyCompact(268_750_00, "NGN")).toMatch(/^₦269k$/);
    expect(formatMoneyCompact(1_250_000_00, "NGN")).toMatch(/^₦1\.3m$/);
    expect(formatMoneyCompact(45_00, "USD")).toBe("$45");
  });

  it("parses what people type into minor units", () => {
    expect(parseMoneyToMinor("1200")).toBe(120_000);
    expect(parseMoneyToMinor("1,200.50")).toBe(120_050);
    expect(parseMoneyToMinor("0.05")).toBe(5);
    expect(parseMoneyToMinor("")).toBe(0);
    expect(parseMoneyToMinor("abc")).toBe(0);
    // Never produce NaN, never overflow into fractions of a minor unit.
    expect(Number.isInteger(parseMoneyToMinor("19.999"))).toBe(true);
  });

  it("round-trips an input field value", () => {
    expect(minorToInput(120_050)).toBe("1200.50");
    expect(minorToInput(0)).toBe("");
    expect(minorToInput(null)).toBe("");
  });

  it("converts dates for date inputs without shifting the day", () => {
    const iso = dateInputToIso("2026-10-23");
    expect(iso).toMatch(/^2026-10-23T/);
    expect(toDateInput(iso!)).toBe("2026-10-23");
    expect(dateInputToIso("")).toBeNull();
  });

  it("formats a date a person can read", () => {
    expect(formatDate("2026-10-23T09:00:00Z")).toMatch(/Oct/);
    expect(formatDate(null)).toBe("—");
  });

  it("knows every currency the API can send", () => {
    expect(CURRENCY_CODES).toEqual(["NGN", "USD", "GHS", "ZAR", "KES", "XOF", "EGP"]);
    for (const code of CURRENCY_CODES) {
      expect(CURRENCY_META[code as keyof typeof CURRENCY_META].symbol.length).toBeGreaterThan(0);
    }
  });
});
