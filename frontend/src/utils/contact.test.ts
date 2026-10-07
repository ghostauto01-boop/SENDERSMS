import { describe, it, expect } from "vitest";
import {
  contactChannel,
  contactInitials,
  contactLabel,
  emailTrust,
  emailTrustLabel,
  isEmailable,
  isTextable,
} from "./contact";
import type { Contact } from "../types";

/** Minimal contact factory; every field is overridable per test. */
const make = (over: Partial<Contact> = {}): Contact =>
  ({
    id: 1,
    first_name: null,
    last_name: null,
    business_name: null,
    phone_number: null,
    email: null,
    city: null,
    state: null,
    country: "Nigeria",
    website: null,
    industry: null,
    source: null,
    lead_status: "new",
    consent_status: "unknown",
    has_consented: false,
    is_opted_out: false,
    notes: null,
    custom_fields: null,
    tags: [],
    messages_sent: 0,
    messages_received: 0,
    last_contacted_at: null,
    last_reply_at: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...over,
  }) as Contact;

describe("contactLabel", () => {
  it("prefers the person's name", () => {
    expect(contactLabel(make({ first_name: "Ada", last_name: "Obi" }))).toBe("Ada Obi");
  });

  it("falls back to the business", () => {
    expect(contactLabel(make({ business_name: "Acme Foods" }))).toBe("Acme Foods");
  });

  it("falls back to the email for an email-only contact (never 'null')", () => {
    expect(contactLabel(make({ email: "hello@acme.ng" }))).toBe("hello@acme.ng");
  });

  it("never renders a bare null", () => {
    expect(contactLabel(make({ id: 7 }))).toBe("Contact #7");
  });
});

describe("contactChannel", () => {
  it("reports the phone when there is one", () => {
    expect(contactChannel(make({ phone_number: "+2348031234567" }))).toEqual({
      value: "+2348031234567",
      kind: "phone",
    });
  });

  it("reports the email for an email-only contact", () => {
    expect(contactChannel(make({ email: "hello@acme.ng" }))).toEqual({
      value: "hello@acme.ng",
      kind: "email",
    });
  });

  it("prefers the phone when both exist", () => {
    expect(
      contactChannel(make({ phone_number: "+2348031234567", email: "a@b.co" })).kind,
    ).toBe("phone");
  });

  it("handles a contact with neither", () => {
    expect(contactChannel(make({})).kind).toBe("none");
    expect(contactChannel(null).kind).toBe("none");
  });
});

describe("contactInitials", () => {
  it("uses the initials of the name", () => {
    expect(contactInitials(make({ first_name: "Ada", last_name: "Obi" }))).toBe("AO");
  });

  it("uses the business when there is no name", () => {
    expect(contactInitials(make({ business_name: "Acme Foods" }))).toBe("AC");
  });

  it("does not throw for an email-only contact with no name", () => {
    expect(contactInitials(make({ email: "hello@acme.ng" }))).toBe("NG");
  });

  it("falls back to a placeholder when there is nothing at all", () => {
    expect(contactInitials(make({}))).toBe("?");
  });
});

describe("emailTrust", () => {
  it("is 'none' without an address", () => {
    expect(emailTrust(make({}))).toBe("none");
  });

  it("is 'verified' only when the enrichment flag says so", () => {
    expect(emailTrust(make({ email: "a@b.co", email_verified: true }))).toBe("verified");
    expect(emailTrust(make({ email: "a@b.co" }))).toBe("unverified");
  });

  it("marks a guessed address as 'inferred', not merely unverified", () => {
    expect(emailTrust(make({ email: "ada.obi@acme.ng", email_source: "inferred" }))).toBe(
      "inferred",
    );
  });

  it("prefers a real verification over the guess label", () => {
    expect(
      emailTrust(make({ email: "a@b.co", email_source: "inferred", email_verified: true })),
    ).toBe("verified");
  });

  it("reports an undeliverable address as invalid", () => {
    expect(emailTrust(make({ email: "a@b.co", is_email_undeliverable: true }))).toBe("invalid");
  });
});

describe("emailTrustLabel", () => {
  it("spells out each state so a guess cannot be mistaken for a real address", () => {
    expect(emailTrustLabel("verified")).toBe("Verified");
    expect(emailTrustLabel("unverified")).toBe("Unverified");
    expect(emailTrustLabel("inferred")).toBe("Guessed");
    expect(emailTrustLabel("invalid")).toBe("Invalid");
    expect(emailTrustLabel("none")).toBeNull();
  });
});

describe("reachability", () => {
  it("an email-only contact is emailable but not textable", () => {
    const contact = make({ email: "hello@acme.ng" });
    expect(isEmailable(contact)).toBe(true);
    expect(isTextable(contact)).toBe(false);
  });

  it("a phone-only contact is textable but not emailable", () => {
    const contact = make({ phone_number: "+2348031234567" });
    expect(isEmailable(contact)).toBe(false);
    expect(isTextable(contact)).toBe(true);
  });

  it("honours opt-outs on each channel independently", () => {
    const contact = make({
      phone_number: "+2348031234567",
      email: "a@b.co",
      is_opted_out: true,
    });
    // An SMS STOP must not remove them from email.
    expect(isTextable(contact)).toBe(false);
    expect(isEmailable(contact)).toBe(true);
  });
});
