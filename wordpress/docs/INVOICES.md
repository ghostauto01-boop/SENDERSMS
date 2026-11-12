# Native draft invoices — unreleased

WordPress-native private invoice storage now covers drafts, line items, server-calculated totals, numbering, audit events, duplication, draft voiding/deletion, listing/filtering and read-only existing payment history. This is **not** a completed invoicing/payment application or qualified accounting system. Original application files are unchanged; no plugin archive was produced.

## Active contracts

Under `/wp-json/sender-suite/v1/`, own-application authentication, origin checks, no-store responses and the common outer transaction apply:

| Method | Route | Behaviour |
|---|---|---|
| GET | `invoices` | Paginated filtered records; single-currency summary when requested |
| POST | `invoices` | Create an invoice draft with persisted items and creation event |
| GET | `invoices/{invoice_id}` | Private detail, stored/derived statuses, items and bounded history |
| PATCH | `invoices/{invoice_id}` | Edit an unpaid draft and recompute changed monetary fields |
| DELETE | `invoices/{invoice_id}` | Delete an unpaid draft and its items/history transactionally |
| POST | `invoices/{invoice_id}/duplicate` | New draft/number/token; copies stored line amounts/totals, not payments |
| POST | `invoices/{invoice_id}/void` | Void an unpaid draft and append an audit event |
| GET | `invoices/{invoice_id}/payments` | Read existing payment metadata, excluding raw provider JSON |
| GET | `invoices/{invoice_id}/events` | Latest-first history; explicit limit 1–500 |

Nine source method/path contracts use six route patterns. These remain **partial**, not native-certified. Like the source calendar and other business APIs, these require an active own-app session; they do not silently inherit the settings module's separate admin-write policy. A WordPress administrator cookie is not a bypass. Broader account/role management remains outstanding.

## Creating a draft

```json
{
  "customer_name": "Example customer",
  "currency": "NGN",
  "items": [
    {"description": "Consulting", "quantity": 1.5, "unit_price_minor": 10000}
  ],
  "discount_minor": 0,
  "tax_rate": 7.5,
  "send": false
}
```

The server derives subtotal/tax/total; client-supplied totals, paid flags and extra fields do not create money. `send` defaults to false. `send_email` is only a future delivery preference and causes no delivery when `send` is false.

`send:true`, creating a `kind:link`, and changing a draft to `sent` fail 503. A `paid` status edit fails 400: status cannot create payment. Public invoice routes, payment-link creation/rotation, checkout/verification/webhooks, payment writes, email/reminders, PDF generation, currency/provider settings and dedicated analytics routes remain unimplemented. The original frontend is untouched and still needs its native adapter/UI.

Private responses deliberately return **null `public_url` and `payment_link`** rather than advertise a non-working `/i/` page or checkout. Tokens remain private metadata, generated with 128 random bits and renewed on duplication. No public token route is installed. Existing payment provider references/metadata are reads, not verification of funds.

## Monetary and persistence rules

- Supported source currencies: NGN, USD, GHS, ZAR, KES, XOF, EGP. Stored amounts are integer minor units. Currency-specific financial/formatting qualification is still outstanding.
- Quantities and rates follow the selected source binary64 calculation, including nearest/even rounding. Decimal conversion uses locale-independent `sprintf(...F)` instead of PHP `round`'s tolerance around ties. Source examples such as `2.675` and adjacent binary values are in the oracle corpus.
- Quantity is rounded to three decimals; as in the source, a quantity rounding to zero becomes one. Tax is calculated from the input rate before storing the rate rounded to two decimals. Consequently a later explicit monetary recalculation uses that stored rate; duplication instead preserves stored line amounts/totals. These source quirks require financial/domain review before release, not claims of universal decimal accounting correctness.
- Price, line amount, subtotal, discount, tax and total must fit signed SQL `INT` (maximum 2,147,483,647 minor units), matching the source-derived schema's actual storage capacity. Nonfinite numbers and overflowing calculations fail before persistence. Totals must remain positive, including discount-only edits; this intentionally closes the source's zero-total edit loophole.
- The number uses a cleaned prefix and UTC-year counter. A missing counter scans existing prefix/year numbers in cursor batches, and allocation conditionally compares the prior counter value. Unique number/token constraints are required. Conflicts roll back allocation and all draft side effects. Invalid/out-of-range counter state fails closed; stale existing-counter repair and native concurrency/recovery still need qualification.
- Only plain `invoice` drafts with zero recorded paid amount **and no payment attempts of any status** may be edited, voided or deleted. Paid, sent/viewed, void, legacy payment-link and attempted-payment records are not silently rewritten/deleted. This is deliberately more restrictive than the source until settlement/reversal/refund workflows exist.
- Mutations claim the invoice row with prior timestamp/status/paid amount/token and advance a monotonic microsecond timestamp. Item replacement and audit insertion share the transaction. Failure of an event write also rolls back numbering, items and parent changes; deleting a draft removes its own history as in the source, not a retained settlement audit.
- Nonempty draft patches append update events; duplicates append a duplication event without copying prior audit/payments. History is consistently latest-first, including mutation responses; the source's in-memory append order can temporarily differ until a fresh read.
- Customer email rejects control/whitespace injection. Free text is valid UTF-8/NUL-free with source column bounds or a 100,000-character guard. Changed contact references are checked, with nonexistent IDs becoming null like source draft creation. No Paystack secrets/environment fallbacks are read for these draft operations; only allowlisted presentation defaults and the number prefix/counter are used.

## Read/resource boundaries

- Listing supports page/per-page, status, currency, kind, search and contact filters, with stable creation-time/ID order. The source's SQL wildcard semantics are retained; collation equivalence is not native-certified.
- Currency summary intentionally covers that whole currency, not just the other list filters, matching the source. Mixed currencies are not added together. Summary scans read only needed columns in 100-row cursor batches; aggregates outside safe JSON integer bounds fail rather than lose precision.
- Up to 100 line items; requests capped at 1 MiB. Detail admits 500 events and 1,000 payment records; oversized history fails 413 rather than silently truncate. The dedicated event route explicitly returns the requested latest slice. History pagination/export beyond this remains open.
- Application UTF-8 JSON estimates admit at most 2 MiB per detail and 8 MiB per page. These are not native WordPress wire-size, peak-memory, SQL-packet or latency benchmarks. Oversized output rolls back a synchronous mutation as well.

## Executed evidence

The source oracle extracts selected **actual original** DTOs, functions, models and constants, without importing original application/config/provider modules. It uses synthetic SQLite, fixed UTC clock, synthetic configuration/UUIDs, omitted foreign keys, three reconstructed relationships and an explicit UTC result adapter for SQLite's timezone loss.

- **32 source operations**, response and selected persistent-state comparisons.
- **400 actual source total-calculation cases** within admitted storage bounds.
- **333 actual Python rounding cases**, including half ties, nextafter neighbours, fractional quantities and tax rates.
- **34 source DTO acceptance cases**.
- **866 invoice assertions per PHP runtime**, including item/audit/delete failure rollback, number/invoice conflicts, first-counter failures, imported-number recovery, distinct tokens, no advertised checkout, unsupported-send refusal, overflow/nonfinite/NUL guards, protected attempted-payment records and private provider JSON.
- **614 shared dispatcher assertions per runtime**, including invoice authentication/origin/path boundaries, monetary recomputation, audit persistence, rollback, duplication, voiding and guarded deletion.

Known comparison boundaries are explicit: nondeterministic tokens, disabled checkout URLs and monotonic `updated_at` differ intentionally; event collections normalize to latest-first. No actual provider requests or payments occurred. Seeded payment history in the safety test is synthetic, not a settlement test.

Still required: real WordPress/MySQL/MariaDB installation, constraints/locks/duplicate-number races/deadlocks/reconnect/recovery tests, legacy data/settings/secret migration, complete role/account policy, qualified payments/reversals/refunds/idempotency, public privacy/access lifecycle, signatures/replays/webhooks, email/PDF delivery and complete native browser workflows. Zero native-certified invoice contracts; no ready-plugin claim.
