/** Shared CSV helpers: header detection + custom-field keys for imports. */

export const customKey = (header: string): string => {
  let key = header
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
  if (!key) key = "custom_field";
  if (/^\d/.test(key)) key = `field_${key}`;
  return key;
};

/**
 * Auto-detect a CSV header -> Contact field mapping shown in the "map columns"
 * step and sent to the server. Unknown columns become template-ready custom
 * fields instead of being dropped.
 *
 * The phone-column rule is deliberately precise: a loose `/number/` substring
 * match treated any column containing "number" — e.g. "Number of Guests",
 * "Table Number", "Order Number", "Invoice Number" — as the phone column.
 * Because the full mapping is applied in header order, a later non-phone
 * "… number" column then overwrote the real phone number and every row was
 * rejected as "Invalid phone number" (0 contacts imported). Only columns that
 * really look like a phone are mapped to `phone_number`.
 */
export const detectColumns = (headers: string[]): Record<string, string> => {
  const map: Record<string, string> = {};
  headers.forEach((h) => {
    const l = h
      .toLowerCase()
      .replace(/[^a-z0-9 ]/g, " ")
      .replace(/\s+/g, " ")
      .trim();
    const isPhone =
      (/\b(phone|mobile|telephone|tel(?:ephone)?|cell(?:ular)?|sms|whats ?app|handphone|gsm)\b/.test(
        l
      ) ||
        /^(number|no\.?|contact)$/.test(l) ||
        /\bcontact\s*(no\.?|number|#)/.test(l)) &&
      !/(name|person|email|fax)/.test(l);
    if (isPhone) map[h] = "phone_number";
    else if (/(first name|firstname|given name)/.test(l)) map[h] = "first_name";
    else if (/(last name|lastname|surname|family name)/.test(l)) map[h] = "last_name";
    else if (/(business|company|brand|organization|organisation|restaurant|shop|store)/.test(l))
      map[h] = "business_name";
    else if (/(email|e mail|mail)/.test(l)) map[h] = "email";
    else if (/(city|town)/.test(l)) map[h] = "city";
    else if (/(state|region|province)/.test(l)) map[h] = "state";
    else if (/country|nation/.test(l)) map[h] = "country";
    else if (/(website|web|url|site)/.test(l)) map[h] = "website";
    else if (/(industry|sector|category)/.test(l)) map[h] = "industry";
    else if (/(source|channel|origin)/.test(l)) map[h] = "source";
    else if (/(lead status|pipeline status|^status$)/.test(l)) map[h] = "lead_status";
    else if (/(notes|note|comments|comment)/.test(l)) map[h] = "notes";
    else if (/^(tags?|labels?|groups?)$/.test(l)) map[h] = "tags";
    else if (/(name|person|contact)/.test(l)) map[h] = "first_name";
    else map[h] = `custom:${customKey(h)}`;
  });
  return map;
};
