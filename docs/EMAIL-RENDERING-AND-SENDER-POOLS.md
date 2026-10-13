# Email rendering, unsubscribe footer, inbox contrast & sender pools

## The blank-email bug (fixed)

`apply_unsubscribe()` in `backend/app/services/email_service.py` returned the
unsubscribe line *instead of* the rendered body whenever a body existed, and
plain-text creatives produced no HTML part at all — so Brevo was handed an
email whose only content was the unsubscribe link. It now always returns
`body + footer`, is idempotent, and generates a real HTML part (body + footer
button) for plain-text copy. Three further guards keep it from regressing
silently:

- Ads dispatch and the classic campaign task refuse to queue a message whose
  rendered body *and* HTML are both empty (it would otherwise go out as
  footer-only mail).
- The Brevo provider refuses to send when both `textContent` and
  `htmlContent` are blank instead of substituting a single space.
- Render/provider logging records body *lengths* only (never the copy), so
  "was a body included?" is answerable from logs.

The unsubscribe token is now signed for the **recipient**. It previously used
the sender's reply-to address, so clicking it would have unsubscribed the
sender and disagreed with the `List-Unsubscribe` header.

## Unsubscribe placement & compliance

The visible unsubscribe lives **only in the footer**: a `.email-footer` div
separated from the copy by a hairline, with a small (12px) grey button. The
plain-text part gets one `Unsubscribe: <url>` line after the body. Bulk mail
also still carries the machine-readable `List-Unsubscribe` /
`List-Unsubscribe-Post` headers (Gmail/Yahoo bulk-sender rules), and replying
with an unsubscribe keyword still works. One-to-one mail carries no visible
unsubscribe, as before.

Legal note (unchanged responsibility): CAN-SPAM (US), GDPR (EU) and CASL (CA)
differ on when an unsubscribe is required and whether cold B2B outreach is
allowed at all. Keeping the small footer + one-click header is the safe
default; confirm your target jurisdictions with legal.

## Inbox dark-mode contrast

`frontend/src/index.css` defines `--email-*` tokens (light + `.dark`, which
the app sets from `prefers-color-scheme`, plus a direct
`prefers-color-scheme` fallback). Dark mode uses #e0e0e0 text, #b0b0b0
secondary text and #66b3ff links on #141b28/#1c2637 surfaces (≥ 4.5:1, WCAG
AA) instead of brand-blue text on dark blue. The Email Inbox list and the
rendered message body (`email-list-item`, `email-subject`,
`email-body-preview`, `email-body-content`) use these tokens; sender HTML
inherits the readable foreground in dark mode and its links take the light
blue token.

## Multi-sender pools (optional)

Both campaign systems (Campaigns page and Ads Manager) accept:

| Field | Meaning |
|---|---|
| `email_account_ids` | Unified sender ids (positive = Brevo, negative = Gmail mailbox). Empty/absent = legacy single sender. |
| `sender_distribution` | `round_robin` (default, even), `weighted` (interleaved by share), or `random` (stable per contact). |
| `email_sender_weights` | `{sender_id: share}` for the weighted strategy, mirroring creative allocations. |

Semantics:

- The campaign's primary sender is folded into the pool; a per-creative
  sender (Ads) still overrides the pool for its contacts.
- Each contact is **pinned** to one sender at assignment time
  (`ads_assignments.email_account_id` / `campaign_contacts.email_account_id`),
  exactly like their creative, so the initial send, retries and follow-ups
  all come from the same From address.
- At dispatch, an unusable pool member (disabled, burned key) is skipped in
  rotation order for the next usable member instead of stalling the campaign.
- Every message/conversation records its sender, replies are sent from the
  thread's sender, and the Email Inbox has an **All senders / per-sender**
  filter (the multi-inbox view, backed by `GET /email/inbox/conversations?account_id=`).

Everything is optional: campaigns that never touch the pool fields behave
exactly as they did before. New columns are added automatically on boot by
`schema_repair` (and are mirrored in `scripts/migrate_existing_db.sql`).
