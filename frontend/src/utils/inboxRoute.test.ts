import { describe, expect, it } from "vitest";
import {
  channelOf,
  contactLineFor,
  hasCallablePhone,
  inboxLabelFor,
  inboxLink,
  inboxPathFor,
  isEmailChannel,
} from "./inboxRoute";

/**
 * The rule behind "clicking an email campaign must not open the SMS inbox".
 *
 * These are the only functions that decide where an inbox link goes, so they are
 * pinned here rather than in every page that renders a link.
 */

describe("inboxRoute — which inbox a channel belongs to", () => {
  it("sends email to the email inbox and everything else to the SMS one", () => {
    expect(inboxPathFor("email")).toBe("/email-inbox");
    expect(inboxPathFor("EMAIL")).toBe("/email-inbox");
    expect(inboxPathFor(" email ")).toBe("/email-inbox");
    expect(inboxPathFor("sms")).toBe("/inbox");
    // Rows that predate the email channel carry no value at all.
    expect(inboxPathFor(null)).toBe("/inbox");
    expect(inboxPathFor(undefined)).toBe("/inbox");
  });

  it("names the destination so a button says where it goes", () => {
    expect(inboxLabelFor("email")).toBe("Email Inbox");
    expect(inboxLabelFor("sms")).toBe("SMS Inbox");
    expect(isEmailChannel("email")).toBe(true);
    expect(isEmailChannel("sms")).toBe(false);
    expect(channelOf("email")).toBe("email");
    expect(channelOf("nonsense")).toBe("sms");
  });

  it("builds a deep link and drops the empty parts of it", () => {
    expect(inboxLink("email", { campaign_id: 7, contact_id: 501 }))
      .toBe("/email-inbox?campaign_id=7&contact_id=501");
    expect(inboxLink("sms", { campaign_id: 7, replied: 1 }))
      .toBe("/inbox?campaign_id=7&replied=1");
    // undefined / null / "" / false never become `?replied=false`.
    expect(inboxLink("email", { campaign_id: 7, view: undefined, contact_id: null, x: false }))
      .toBe("/email-inbox?campaign_id=7");
    expect(inboxLink("email")).toBe("/email-inbox");
  });
});

describe("inboxRoute — what a row shows about a person", () => {
  it("shows the address on an email thread and the number on a text thread", () => {
    const contact = { email: "ada@acme.example", phone: "+2348012345678" };
    expect(contactLineFor("email", contact)).toBe("ada@acme.example");
    expect(contactLineFor("sms", contact)).toBe("+2348012345678");
    // Falling back beats showing nothing when one of the two is missing.
    expect(contactLineFor("email", { email: "", phone: "+2348012345678" })).toBe("+2348012345678");
    expect(contactLineFor("sms", { email: "ada@acme.example", phone: "" })).toBe("ada@acme.example");
  });

  it("refuses to call a placeholder number", () => {
    expect(hasCallablePhone("+2348012345678")).toBe(true);
    // Contacts made from an email-only import used to carry this as a phone.
    expect(hasCallablePhone("email:ada@acme.example")).toBe(false);
    expect(hasCallablePhone("")).toBe(false);
    expect(hasCallablePhone(null)).toBe(false);
  });
});
