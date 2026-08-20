import { describe, it, expect } from "vitest";
import { detectColumns } from "./ContactsPage";

/**
 * Regression guard for the CSV import "refusing to import contacts".
 *
 * The old phone detection matched any header containing the word "number", so
 * a full restaurant CSV with a non-phone "… number" column (Number of Guests,
 * Table Number, Order Number, Invoice Number, …) silently treated that column
 * as the phone number. Because the full mapping is applied in header order,
 * the later non-phone column overwrote the real phone number and every row was
 * rejected as "Invalid phone number" -> 0 contacts imported.
 */
describe("detectColumns phone detection", () => {
  it("keeps the real phone column and does not hijack non-phone '… number' columns", () => {
    const map = detectColumns([
      "First Name", "Phone Number", "Number of Guests",
      "Table Number", "Order Number", "Invoice Number",
    ]);
    expect(map["Phone Number"]).toBe("phone_number");
    // Non-phone "… number" columns must NOT be treated as the phone column.
    expect(map["Number of Guests"]).not.toBe("phone_number");
    expect(map["Table Number"]).not.toBe("phone_number");
    expect(map["Order Number"]).not.toBe("phone_number");
    expect(map["Invoice Number"]).not.toBe("phone_number");
    // They should still be imported as custom fields (never silently dropped).
    expect(map["Number of Guests"]).toBe("custom:number_of_guests");
  });

  it("maps a full restaurant CSV with all the fields to the right columns", () => {
    const map = detectColumns([
      "First Name", "Last Name", "Phone Number", "Business Name", "Email",
      "City", "State", "Country", "Website", "Industry", "Source",
      "Lead Status", "Notes", "Pain Point", "2026 Score",
    ]);
    expect(map["Phone Number"]).toBe("phone_number");
    expect(map["First Name"]).toBe("first_name");
    expect(map["Last Name"]).toBe("last_name");
    expect(map["Business Name"]).toBe("business_name");
    expect(map["Email"]).toBe("email");
    expect(map["City"]).toBe("city");
    expect(map["State"]).toBe("state");
    expect(map["Country"]).toBe("country");
    expect(map["Website"]).toBe("website");
    expect(map["Industry"]).toBe("industry");
    expect(map["Source"]).toBe("source");
    expect(map["Lead Status"]).toBe("lead_status");
    expect(map["Notes"]).toBe("notes");
    expect(map["Pain Point"]).toBe("custom:pain_point");
    expect(map["2026 Score"]).toBe("custom:field_2026_score");
  });

  it("still detects the common phone header spellings", () => {
    for (const header of [
      "Phone", "Phone Number", "Phone No", "Phone #", "Mobile", "Mobile Number",
      "Mobile No", "Tel", "Telephone", "Cell", "Cell Phone", "SMS Number",
      "Contact Number", "Contact", "Number", "Whatsapp",
    ]) {
      expect(detectColumns([header])[header]).toBe(`phone_number`);
    }
  });
});
