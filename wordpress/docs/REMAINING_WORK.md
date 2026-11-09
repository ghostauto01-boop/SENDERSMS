# Remaining WordPress recreation work

351 missing source handlers,157 partial health/auth/contact/list/variable/template/reply/classification/notification/import/analytics/sequence/settings/calendar/invoice/inbox/status-webhook/follow-up-control/access/profile/dashboard/campaign-reporting/gateway-phone/experimental-single-contact-send handlers,0 fully certified contracts. The administrator CSV diagnostic is not the original source import API.

Every row is outstanding, including all partial handlers until native HTTP certification. See README.md for actual implemented utilities and fresh test boundaries.

| Method | Path | Source | State |
|---|---|---|---|
| DELETE | /api/v1/ads/audiences/{audience_id} | backend/app/api/v1/ads.py:1719 | missing original application handler |
| DELETE | /api/v1/ads/calendar/{event_id} | backend/app/api/v1/ads.py:2545 | missing original application handler |
| DELETE | /api/v1/ads/campaigns/{campaign_id} | backend/app/api/v1/ads.py:514 | missing original application handler |
| DELETE | /api/v1/ads/campaigns/{campaign_id}/audience/{assignment_id} | backend/app/api/v1/ads.py:1053 | missing original application handler |
| DELETE | /api/v1/ads/campaigns/{campaign_id}/followup-steps/{step_id} | backend/app/api/v1/ads.py:2123 | missing original application handler |
| DELETE | /api/v1/ads/creatives/{creative_id} | backend/app/api/v1/ads.py:1499 | missing original application handler |
| DELETE | /api/v1/ads/followup-steps/{step_id} | backend/app/api/v1/ads.py:2140 | missing original application handler |
| DELETE | /api/v1/ads/followup-templates/{template_id} | backend/app/api/v1/ads.py:2284 | missing original application handler |
| DELETE | /api/v1/ads/sets/{set_id} | backend/app/api/v1/ads.py:1285 | missing original application handler |
| DELETE | /api/v1/ads/suppression/{entry_id} | backend/app/api/v1/ads.py:2626 | missing original application handler |
| DELETE | /api/v1/ai-agent/files/{file_id} | backend/app/api/v1/ai_agent.py:835 | missing original application handler |
| DELETE | /api/v1/ai-agent/memory/global | backend/app/api/v1/ai_agent.py:914 | missing original application handler |
| DELETE | /api/v1/ai-agent/memory/{session_id} | backend/app/api/v1/ai_agent.py:944 | missing original application handler |
| DELETE | /api/v1/ai-agent/sessions/{session_id} | backend/app/api/v1/ai_agent.py:507 | missing original application handler |
| DELETE | /api/v1/ai-agent/skills/{skill_id} | backend/app/api/v1/ai_agent.py:1043 | missing original application handler |
| DELETE | /api/v1/automations/{automation_id} | backend/app/api/v1/automations.py:130 | partial handler; native WordPress/HTTP unverified |
| DELETE | /api/v1/autoreply/{rule_id} | backend/app/api/v1/autoreply.py:134 | partial handler; native WordPress/HTTP unverified |
| DELETE | /api/v1/calendar/{meeting_id} | backend/app/api/v1/calendar.py:535 | partial handler; native WordPress/HTTP unverified |
| DELETE | /api/v1/calls/webhooks/{webhook_id} | backend/app/api/v1/calls.py:160 | missing original application handler |
| DELETE | /api/v1/campaign-followups/{followup_id} | backend/app/api/v1/campaign_followups.py:247 | missing original application handler |
| DELETE | /api/v1/campaigns/{campaign_id} | backend/app/api/v1/campaigns.py:596 | missing original application handler |
| DELETE | /api/v1/contacts/{contact_id} | backend/app/api/v1/contacts.py:963 | partial handler; native WordPress/HTTP unverified |
| DELETE | /api/v1/email/accounts/{account_id} | backend/app/api/v1/email.py:295 | missing original application handler |
| DELETE | /api/v1/email/domains/{domain_id} | backend/app/api/v1/email.py:412 | missing original application handler |
| DELETE | /api/v1/email/suppression/{entry_id} | backend/app/api/v1/email.py:1669 | missing original application handler |
| DELETE | /api/v1/followups/{followup_id} | backend/app/api/v1/followups.py:213 | partial handler; native WordPress/HTTP unverified |
| DELETE | /api/v1/invoices/{invoice_id} | backend/app/api/v1/invoices.py:1203 | partial handler; native WordPress/HTTP unverified |
| DELETE | /api/v1/lists/{list_id} | backend/app/api/v1/lists.py:213 | partial handler; native WordPress/HTTP unverified |
| DELETE | /api/v1/mailbox/{mailbox_id} | backend/app/api/v1/mailbox.py:559 | missing original application handler |
| DELETE | /api/v1/mcp/clients/{client_pk} | backend/app/api/v1/mcp.py:1188 | missing original application handler |
| DELETE | /api/v1/mcp/tokens/{token_id} | backend/app/api/v1/mcp.py:1000 | missing original application handler |
| DELETE | /api/v1/sequences/{sequence_id} | backend/app/api/v1/sequences.py:243 | partial handler; native WordPress/HTTP unverified |
| DELETE | /api/v1/settings/callgate/webhooks/{webhook_id} | backend/app/api/v1/settings.py:287 | missing original application handler |
| DELETE | /api/v1/settings/gateway/phones/{phone_id} | backend/app/api/v1/settings.py:501 | partial handler; native WordPress/HTTP unverified |
| DELETE | /api/v1/settings/gateway/webhooks/{webhook_id} | backend/app/api/v1/settings.py:142 | missing original application handler |
| DELETE | /api/v1/templates/{template_id} | backend/app/api/v1/templates.py:306 | partial handler; native WordPress/HTTP unverified |
| DELETE | /api/v1/variables/{variable_id} | backend/app/api/v1/variables.py:163 | partial handler; native WordPress/HTTP unverified |
| DELETE | /connectors/{key}/mcp | backend/app/api/v1/mcp.py:353 | missing original application handler |
| DELETE | /mcp | backend/app/api/v1/mcp.py:313 | missing original application handler |
| DELETE | /mcp/ | backend/app/api/v1/mcp.py:313 | missing original application handler |
| GET | /.well-known/oauth-authorization-server | backend/app/api/v1/mcp.py:403 | missing original application handler |
| GET | /.well-known/oauth-authorization-server/{path:path} | backend/app/api/v1/mcp.py:403 | missing original application handler |
| GET | /.well-known/oauth-protected-resource | backend/app/api/v1/mcp.py:378 | missing original application handler |
| GET | /.well-known/oauth-protected-resource/{path:path} | backend/app/api/v1/mcp.py:378 | missing original application handler |
| GET | /.well-known/openid-configuration | backend/app/api/v1/mcp.py:403 | missing original application handler |
| GET | /.well-known/openid-configuration/{path:path} | backend/app/api/v1/mcp.py:403 | missing original application handler |
| GET | /api/v1/ads/activity | backend/app/api/v1/ads.py:3100 | missing original application handler |
| GET | /api/v1/ads/audiences | backend/app/api/v1/ads.py:1651 | missing original application handler |
| GET | /api/v1/ads/audiences/{audience_id} | backend/app/api/v1/ads.py:1691 | missing original application handler |
| GET | /api/v1/ads/audiences/{audience_id}/preview | backend/app/api/v1/ads.py:1732 | missing original application handler |
| GET | /api/v1/ads/calendar | backend/app/api/v1/ads.py:2451 | missing original application handler |
| GET | /api/v1/ads/campaigns | backend/app/api/v1/ads.py:275 | missing original application handler |
| GET | /api/v1/ads/campaigns/{campaign_id} | backend/app/api/v1/ads.py:352 | missing original application handler |
| GET | /api/v1/ads/campaigns/{campaign_id}/activity | backend/app/api/v1/ads.py:1139 | missing original application handler |
| GET | /api/v1/ads/campaigns/{campaign_id}/analytics | backend/app/api/v1/ads.py:1046 | missing original application handler |
| GET | /api/v1/ads/campaigns/{campaign_id}/audience | backend/app/api/v1/ads.py:812 | missing original application handler |
| GET | /api/v1/ads/campaigns/{campaign_id}/followup-steps | backend/app/api/v1/ads.py:1938 | missing original application handler |
| GET | /api/v1/ads/campaigns/{campaign_id}/jobs | backend/app/api/v1/ads.py:893 | missing original application handler |
| GET | /api/v1/ads/campaigns/{campaign_id}/optimization | backend/app/api/v1/ads.py:1110 | missing original application handler |
| GET | /api/v1/ads/campaigns/{campaign_id}/send-logs | backend/app/api/v1/ads.py:936 | missing original application handler |
| GET | /api/v1/ads/campaigns/{campaign_id}/sets | backend/app/api/v1/ads.py:1177 | missing original application handler |
| GET | /api/v1/ads/contacts/{contact_id}/timeline | backend/app/api/v1/ads.py:2718 | missing original application handler |
| GET | /api/v1/ads/creatives/{creative_id}/analytics | backend/app/api/v1/ads.py:1568 | missing original application handler |
| GET | /api/v1/ads/creatives/{creative_id}/versions | backend/app/api/v1/ads.py:1441 | missing original application handler |
| GET | /api/v1/ads/export/{kind} | backend/app/api/v1/ads.py:2943 | missing original application handler |
| GET | /api/v1/ads/followup-steps/{step_id} | backend/app/api/v1/ads.py:2009 | missing original application handler |
| GET | /api/v1/ads/followup-templates | backend/app/api/v1/ads.py:2213 | missing original application handler |
| GET | /api/v1/ads/followup-templates/{template_id} | backend/app/api/v1/ads.py:2249 | missing original application handler |
| GET | /api/v1/ads/followups | backend/app/api/v1/ads.py:2301 | missing original application handler |
| GET | /api/v1/ads/overview | backend/app/api/v1/ads.py:187 | missing original application handler |
| GET | /api/v1/ads/r/{assignment_id} | backend/app/api/v1/ads.py:1831 | missing original application handler |
| GET | /api/v1/ads/reference | backend/app/api/v1/ads.py:3039 | missing original application handler |
| GET | /api/v1/ads/search | backend/app/api/v1/ads.py:2805 | missing original application handler |
| GET | /api/v1/ads/sets/{set_id}/creatives | backend/app/api/v1/ads.py:1338 | missing original application handler |
| GET | /api/v1/ads/sets/{set_id}/preview | backend/app/api/v1/ads.py:1306 | missing original application handler |
| GET | /api/v1/ads/suppression | backend/app/api/v1/ads.py:2563 | missing original application handler |
| GET | /api/v1/ai-agent/activity | backend/app/api/v1/ai_agent.py:643 | missing original application handler |
| GET | /api/v1/ai-agent/files | backend/app/api/v1/ai_agent.py:802 | missing original application handler |
| GET | /api/v1/ai-agent/files/{file_id} | backend/app/api/v1/ai_agent.py:815 | missing original application handler |
| GET | /api/v1/ai-agent/health | backend/app/api/v1/ai_agent.py:183 | missing original application handler |
| GET | /api/v1/ai-agent/memory/global | backend/app/api/v1/ai_agent.py:898 | missing original application handler |
| GET | /api/v1/ai-agent/memory/{session_id} | backend/app/api/v1/ai_agent.py:921 | missing original application handler |
| GET | /api/v1/ai-agent/models | backend/app/api/v1/ai_agent.py:101 | missing original application handler |
| GET | /api/v1/ai-agent/presets | backend/app/api/v1/ai_agent.py:133 | missing original application handler |
| GET | /api/v1/ai-agent/sessions | backend/app/api/v1/ai_agent.py:366 | missing original application handler |
| GET | /api/v1/ai-agent/sessions/{session_id} | backend/app/api/v1/ai_agent.py:418 | missing original application handler |
| GET | /api/v1/ai-agent/settings | backend/app/api/v1/ai_agent.py:228 | missing original application handler |
| GET | /api/v1/ai-agent/skills | backend/app/api/v1/ai_agent.py:959 | missing original application handler |
| GET | /api/v1/ai-agent/skills/{skill_id} | backend/app/api/v1/ai_agent.py:1030 | missing original application handler |
| GET | /api/v1/ai-agent/tools | backend/app/api/v1/ai_agent.py:1057 | missing original application handler |
| GET | /api/v1/analytics/by-campaign | backend/app/api/v1/analytics.py:205 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/analytics/calls | backend/app/api/v1/analytics.py:251 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/analytics/funnel | backend/app/api/v1/analytics.py:148 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/analytics/overview | backend/app/api/v1/analytics.py:24 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/analytics/timeseries | backend/app/api/v1/analytics.py:101 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/auth/access | backend/app/api/v1/auth.py:243 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/auth/me | backend/app/api/v1/auth.py:276 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/automations | backend/app/api/v1/automations.py:43 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/automations/ | backend/app/api/v1/automations.py:43 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/automations/{automation_id} | backend/app/api/v1/automations.py:88 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/autoreply | backend/app/api/v1/autoreply.py:44 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/autoreply/ | backend/app/api/v1/autoreply.py:44 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/autoreply/{rule_id} | backend/app/api/v1/autoreply.py:87 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/calendar | backend/app/api/v1/calendar.py:164 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/calendar/ | backend/app/api/v1/calendar.py:164 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/calendar/day | backend/app/api/v1/calendar.py:251 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/calendar/tags | backend/app/api/v1/calendar.py:273 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/calendar/tags/all | backend/app/api/v1/calendar.py:634 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/calendar/upcoming | backend/app/api/v1/calendar.py:237 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/calendar/{meeting_id} | backend/app/api/v1/calendar.py:301 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/calendar/{meeting_id}/preview | backend/app/api/v1/calendar.py:315 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/calls/active | backend/app/api/v1/calls.py:328 | missing original application handler |
| GET | /api/v1/calls/config | backend/app/api/v1/calls.py:59 | missing original application handler |
| GET | /api/v1/calls/logs | backend/app/api/v1/calls.py:287 | missing original application handler |
| GET | /api/v1/calls/webhooks | backend/app/api/v1/calls.py:113 | missing original application handler |
| GET | /api/v1/campaign-followups/campaigns | backend/app/api/v1/campaign_followups.py:45 | missing original application handler |
| GET | /api/v1/campaign-followups/campaigns/{campaign_id} | backend/app/api/v1/campaign_followups.py:100 | missing original application handler |
| GET | /api/v1/campaign-followups/{followup_id}/log | backend/app/api/v1/campaign_followups.py:405 | missing original application handler |
| GET | /api/v1/campaign-followups/{followup_id}/preview | backend/app/api/v1/campaign_followups.py:323 | missing original application handler |
| GET | /api/v1/campaigns | backend/app/api/v1/campaigns.py:53 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/campaigns/ | backend/app/api/v1/campaigns.py:53 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/campaigns/reconcile | backend/app/api/v1/campaigns.py:106 | missing original application handler |
| GET | /api/v1/campaigns/{campaign_id} | backend/app/api/v1/campaigns.py:131 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/campaigns/{campaign_id}/analytics | backend/app/api/v1/campaigns.py:610 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/campaigns/{campaign_id}/conversations | backend/app/api/v1/campaigns.py:634 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/campaigns/{campaign_id}/performance | backend/app/api/v1/campaigns.py:763 | missing original application handler |
| GET | /api/v1/contacts | backend/app/api/v1/contacts.py:186 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/contacts/ | backend/app/api/v1/contacts.py:186 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/contacts/enrich/status | backend/app/api/v1/contacts.py:1496 | missing original application handler |
| GET | /api/v1/contacts/export | backend/app/api/v1/contacts.py:349 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/contacts/export/csv | backend/app/api/v1/contacts.py:1663 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/contacts/import/jobs/{job_id} | backend/app/api/v1/contacts.py:1334 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/contacts/import/jobs/{job_id}/errors.csv | backend/app/api/v1/contacts.py:1367 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/contacts/tags/ | backend/app/api/v1/contacts.py:546 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/contacts/{contact_id} | backend/app/api/v1/contacts.py:588 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/contacts/{contact_id}/activity | backend/app/api/v1/contacts.py:1803 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/dashboard/charts | backend/app/api/v1/dashboard.py:205 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/dashboard/stats | backend/app/api/v1/dashboard.py:38 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/email/accounts | backend/app/api/v1/email.py:154 | missing original application handler |
| GET | /api/v1/email/accounts/{account_id}/deliverability | backend/app/api/v1/email.py:1842 | missing original application handler |
| GET | /api/v1/email/accounts/{account_id}/senders | backend/app/api/v1/email.py:669 | missing original application handler |
| GET | /api/v1/email/contacts/{contact_id}/engagement | backend/app/api/v1/email.py:2071 | missing original application handler |
| GET | /api/v1/email/domains | backend/app/api/v1/email.py:361 | missing original application handler |
| GET | /api/v1/email/domains/{domain_id} | backend/app/api/v1/email.py:382 | missing original application handler |
| GET | /api/v1/email/events | backend/app/api/v1/email.py:1689 | missing original application handler |
| GET | /api/v1/email/health | backend/app/api/v1/email.py:182 | missing original application handler |
| GET | /api/v1/email/history | backend/app/api/v1/email.py:1062 | missing original application handler |
| GET | /api/v1/email/inbox/conversations | backend/app/api/v1/email.py:1167 | missing original application handler |
| GET | /api/v1/email/inbox/conversations/{conversation_id} | backend/app/api/v1/email.py:1321 | missing original application handler |
| GET | /api/v1/email/inbox/unread-count | backend/app/api/v1/email.py:1579 | missing original application handler |
| GET | /api/v1/email/messages/{message_id}/events | backend/app/api/v1/email.py:2016 | missing original application handler |
| GET | /api/v1/email/overview | backend/app/api/v1/email.py:694 | missing original application handler |
| GET | /api/v1/email/reference | backend/app/api/v1/email.py:796 | missing original application handler |
| GET | /api/v1/email/senders | backend/app/api/v1/email.py:172 | missing original application handler |
| GET | /api/v1/email/stats | backend/app/api/v1/email.py:786 | missing original application handler |
| GET | /api/v1/email/suppression | backend/app/api/v1/email.py:1607 | missing original application handler |
| GET | /api/v1/email/unsubscribe | backend/app/api/v1/email.py:1769 | missing original application handler |
| GET | /api/v1/followups | backend/app/api/v1/followups.py:79 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/followups/ | backend/app/api/v1/followups.py:79 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/guide | backend/app/api/v1/guide.py:27, backend/app/api/v1/guide.py:27 | missing original application handler |
| GET | /api/v1/guide/ | backend/app/api/v1/guide.py:27 | missing original application handler |
| GET | /api/v1/guide/summary | backend/app/api/v1/guide.py:36 | missing original application handler |
| GET | /api/v1/health | backend/app/main.py:1402 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/health/db | backend/app/main.py:1413 | missing original application handler |
| GET | /api/v1/inbox/campaign-filters | backend/app/api/v1/inbox.py:182 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/inbox/conversations | backend/app/api/v1/inbox.py:34 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/inbox/conversations/{conversation_id} | backend/app/api/v1/inbox.py:237 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/inbox/conversations/{conversation_id}/templates/{template_id}/preview | backend/app/api/v1/inbox.py:262 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/inbox/device-info | backend/app/api/v1/inbox.py:503 | missing original application handler |
| GET | /api/v1/invoices | backend/app/api/v1/invoices.py:779 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/invoices/analytics | backend/app/api/v1/invoices.py:351 | missing original application handler |
| GET | /api/v1/invoices/currencies | backend/app/api/v1/invoices.py:333 | missing original application handler |
| GET | /api/v1/invoices/links | backend/app/api/v1/invoices.py:1015 | missing original application handler |
| GET | /api/v1/invoices/payments | backend/app/api/v1/invoices.py:464 | missing original application handler |
| GET | /api/v1/invoices/public/{token} | backend/app/api/v1/invoices.py:644 | missing original application handler |
| GET | /api/v1/invoices/public/{token}/verify | backend/app/api/v1/invoices.py:725 | missing original application handler |
| GET | /api/v1/invoices/settings | backend/app/api/v1/invoices.py:292 | missing original application handler |
| GET | /api/v1/invoices/{invoice_id} | backend/app/api/v1/invoices.py:1086 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/invoices/{invoice_id}/events | backend/app/api/v1/invoices.py:1470 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/invoices/{invoice_id}/payments | backend/app/api/v1/invoices.py:1460 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/lists | backend/app/api/v1/lists.py:77 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/lists/ | backend/app/api/v1/lists.py:77 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/lists/{list_id}/contacts | backend/app/api/v1/lists.py:263 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/lists/{list_id}/stats | backend/app/api/v1/lists.py:619 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/mail/messages | backend/app/api/v1/mail.py:80 | missing original application handler |
| GET | /api/v1/mail/messages/{mailbox_id}/{provider_message_id} | backend/app/api/v1/mail.py:99 | missing original application handler |
| GET | /api/v1/mail/overview | backend/app/api/v1/mail.py:67 | missing original application handler |
| GET | /api/v1/mailbox | backend/app/api/v1/mailbox.py:282, backend/app/api/v1/mailbox.py:282 | missing original application handler |
| GET | /api/v1/mailbox/ | backend/app/api/v1/mailbox.py:282 | missing original application handler |
| GET | /api/v1/mailbox/google/callback | backend/app/api/v1/mailbox.py:343 | missing original application handler |
| GET | /api/v1/mailbox/google/start | backend/app/api/v1/mailbox.py:304 | missing original application handler |
| GET | /api/v1/mailbox/report | backend/app/api/v1/mailbox.py:271 | missing original application handler |
| GET | /api/v1/mailbox/{mailbox_id} | backend/app/api/v1/mailbox.py:526 | missing original application handler |
| GET | /api/v1/mcp/activity | backend/app/api/v1/mcp.py:1013 | missing original application handler |
| GET | /api/v1/mcp/bridge | backend/app/api/v1/mcp.py:1113 | missing original application handler |
| GET | /api/v1/mcp/clients | backend/app/api/v1/mcp.py:1153 | missing original application handler |
| GET | /api/v1/mcp/connectors | backend/app/api/v1/mcp.py:1041 | missing original application handler |
| GET | /api/v1/mcp/endpoints | backend/app/api/v1/mcp.py:1248 | missing original application handler |
| GET | /api/v1/mcp/execution-health | backend/app/api/v1/mcp.py:1271 | missing original application handler |
| GET | /api/v1/mcp/tokens | backend/app/api/v1/mcp.py:964 | missing original application handler |
| GET | /api/v1/notifications | backend/app/api/v1/notifications.py:33 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/notifications/ | backend/app/api/v1/notifications.py:33 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/notifications/push/subscriptions | backend/app/api/v1/notifications.py:156 | missing original application handler |
| GET | /api/v1/notifications/push/vapid-key | backend/app/api/v1/notifications.py:105 | missing original application handler |
| GET | /api/v1/notifications/unread-count | backend/app/api/v1/notifications.py:55 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/overview/campaigns | backend/app/api/v1/overview.py:358 | missing original application handler |
| GET | /api/v1/overview/campaigns/{campaign_id} | backend/app/api/v1/overview.py:478 | missing original application handler |
| GET | /api/v1/overview/metrics | backend/app/api/v1/overview.py:574 | missing original application handler |
| GET | /api/v1/send/history | backend/app/api/v1/send.py:374 | missing original application handler |
| GET | /api/v1/send/scheduled | backend/app/api/v1/send.py:319 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/sequences | backend/app/api/v1/sequences.py:69 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/sequences/ | backend/app/api/v1/sequences.py:69 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/sequences/{sequence_id} | backend/app/api/v1/sequences.py:104 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/settings/access | backend/app/api/v1/settings.py:22 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/settings/appearance | backend/app/api/v1/settings.py:606 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/settings/callgate | backend/app/api/v1/settings.py:213 | missing original application handler |
| GET | /api/v1/settings/callgate/webhooks | backend/app/api/v1/settings.py:248 | missing original application handler |
| GET | /api/v1/settings/compliance | backend/app/api/v1/settings.py:196 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/settings/gateway | backend/app/api/v1/settings.py:45 | missing original application handler |
| GET | /api/v1/settings/gateway/phones | backend/app/api/v1/settings.py:451 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/settings/gateway/webhooks | backend/app/api/v1/settings.py:111 | missing original application handler |
| GET | /api/v1/settings/google-oauth | backend/app/api/v1/settings.py:314 | missing original application handler |
| GET | /api/v1/settings/notifications | backend/app/api/v1/settings.py:151 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/settings/notifications/muted-senders | backend/app/api/v1/settings.py:158 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/system/errors | backend/app/api/v1/system.py:291 | missing original application handler |
| GET | /api/v1/system/health | backend/app/api/v1/system.py:47 | missing original application handler |
| GET | /api/v1/templates | backend/app/api/v1/templates.py:95 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/templates/ | backend/app/api/v1/templates.py:95 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/templates/categories | backend/app/api/v1/templates.py:126 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/templates/{template_id} | backend/app/api/v1/templates.py:201 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/validator/jobs | backend/app/api/v1/validator.py:186 | missing original application handler |
| GET | /api/v1/validator/jobs/{job_id} | backend/app/api/v1/validator.py:199 | missing original application handler |
| GET | /api/v1/validator/status | backend/app/api/v1/validator.py:83 | missing original application handler |
| GET | /api/v1/variables | backend/app/api/v1/variables.py:33 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/variables/ | backend/app/api/v1/variables.py:33 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/variables/contact/{contact_id}/profile | backend/app/api/v1/variables.py:204 | partial handler; native WordPress/HTTP unverified |
| GET | /api/v1/webhooks/callgate | backend/app/api/v1/webhooks.py:566 | missing original application handler |
| GET | /api/v1/webhooks/logs | backend/app/api/v1/webhooks.py:708 | missing original application handler |
| GET | /api/v1/webhooks/smsgateway | backend/app/api/v1/webhooks.py:404 | missing original application handler |
| GET | /apple-touch-icon.png | backend/app/main.py:1548 | missing original application handler |
| GET | /connectors/{key}/mcp | backend/app/api/v1/mcp.py:348 | missing original application handler |
| GET | /connectors/{key}/oauth/authorize | backend/app/api/v1/mcp.py:660 | missing original application handler |
| GET | /connectors/{key}/oauth/register/{client_id} | backend/app/api/v1/mcp.py:453 | missing original application handler |
| GET | /favicon.svg | backend/app/main.py:1550 | missing original application handler |
| GET | /icon-192.png | backend/app/main.py:1540 | missing original application handler |
| GET | /icon-512.png | backend/app/main.py:1542 | missing original application handler |
| GET | /icon-maskable-192.png | backend/app/main.py:1544 | missing original application handler |
| GET | /icon-maskable-512.png | backend/app/main.py:1546 | missing original application handler |
| GET | /manifest.json | backend/app/main.py:1520 | missing original application handler |
| GET | /mcp | backend/app/api/v1/mcp.py:307 | missing original application handler |
| GET | /mcp/ | backend/app/api/v1/mcp.py:307 | missing original application handler |
| GET | /mcp/health | backend/app/api/v1/mcp.py:319 | missing original application handler |
| GET | /oauth/approved | backend/app/api/v1/mcp.py:673 | missing original application handler |
| GET | /oauth/authorize | backend/app/api/v1/mcp.py:660 | missing original application handler |
| GET | /oauth/register/{client_id} | backend/app/api/v1/mcp.py:453 | missing original application handler |
| GET | /sw.js | backend/app/main.py:1538 | missing original application handler |
| GET | /{fp:path} | backend/app/main.py:1557, backend/app/main.py:1563 | missing original application handler |
| PATCH | /api/v1/ads/audiences/{audience_id} | backend/app/api/v1/ads.py:1700 | missing original application handler |
| PATCH | /api/v1/ads/calendar/{event_id} | backend/app/api/v1/ads.py:2527 | missing original application handler |
| PATCH | /api/v1/ads/campaigns/{campaign_id} | backend/app/api/v1/ads.py:394 | missing original application handler |
| PATCH | /api/v1/ads/campaigns/{campaign_id}/followup-steps/{step_id} | backend/app/api/v1/ads.py:2045 | missing original application handler |
| PATCH | /api/v1/ads/creatives/{creative_id} | backend/app/api/v1/ads.py:1387 | missing original application handler |
| PATCH | /api/v1/ads/followup-steps/{step_id} | backend/app/api/v1/ads.py:2058 | missing original application handler |
| PATCH | /api/v1/ads/followup-templates/{template_id} | backend/app/api/v1/ads.py:2263 | missing original application handler |
| PATCH | /api/v1/ads/sets/{set_id} | backend/app/api/v1/ads.py:1220 | missing original application handler |
| PATCH | /api/v1/ai-agent/sessions/{session_id} | backend/app/api/v1/ai_agent.py:487 | missing original application handler |
| PATCH | /api/v1/campaigns/{campaign_id} | backend/app/api/v1/campaigns.py:221 | missing original application handler |
| PATCH | /api/v1/email/accounts/{account_id} | backend/app/api/v1/email.py:256 | missing original application handler |
| PATCH | /api/v1/invoices/{invoice_id} | backend/app/api/v1/invoices.py:1096 | partial handler; native WordPress/HTTP unverified |
| PATCH | /api/v1/mailbox/{mailbox_id} | backend/app/api/v1/mailbox.py:536 | missing original application handler |
| POST | /api/v1/ads/audiences | backend/app/api/v1/ads.py:1675 | missing original application handler |
| POST | /api/v1/ads/audiences/{audience_id}/attach | backend/app/api/v1/ads.py:1772 | missing original application handler |
| POST | /api/v1/ads/audiences/{audience_id}/duplicate | backend/app/api/v1/ads.py:1739 | missing original application handler |
| POST | /api/v1/ads/calendar | backend/app/api/v1/ads.py:2511 | missing original application handler |
| POST | /api/v1/ads/campaigns | backend/app/api/v1/ads.py:321 | missing original application handler |
| POST | /api/v1/ads/campaigns/bulk | backend/app/api/v1/ads.py:2859 | missing original application handler |
| POST | /api/v1/ads/campaigns/{campaign_id}/archive | backend/app/api/v1/ads.py:681 | missing original application handler |
| POST | /api/v1/ads/campaigns/{campaign_id}/audience/bulk-remove | backend/app/api/v1/ads.py:1083 | missing original application handler |
| POST | /api/v1/ads/campaigns/{campaign_id}/complete | backend/app/api/v1/ads.py:665 | missing original application handler |
| POST | /api/v1/ads/campaigns/{campaign_id}/contacts | backend/app/api/v1/ads.py:733 | missing original application handler |
| POST | /api/v1/ads/campaigns/{campaign_id}/dispatch | backend/app/api/v1/ads.py:711 | missing original application handler |
| POST | /api/v1/ads/campaigns/{campaign_id}/duplicate | backend/app/api/v1/ads.py:697 | missing original application handler |
| POST | /api/v1/ads/campaigns/{campaign_id}/followup-steps | backend/app/api/v1/ads.py:1964 | missing original application handler |
| POST | /api/v1/ads/campaigns/{campaign_id}/jobs/retry | backend/app/api/v1/ads.py:907 | missing original application handler |
| POST | /api/v1/ads/campaigns/{campaign_id}/launch | backend/app/api/v1/ads.py:576 | missing original application handler |
| POST | /api/v1/ads/campaigns/{campaign_id}/optimize | backend/app/api/v1/ads.py:1118 | missing original application handler |
| POST | /api/v1/ads/campaigns/{campaign_id}/pause | backend/app/api/v1/ads.py:610 | missing original application handler |
| POST | /api/v1/ads/campaigns/{campaign_id}/rebuild-audience | backend/app/api/v1/ads.py:802 | missing original application handler |
| POST | /api/v1/ads/campaigns/{campaign_id}/resume | backend/app/api/v1/ads.py:630 | missing original application handler |
| POST | /api/v1/ads/campaigns/{campaign_id}/sets | backend/app/api/v1/ads.py:1197 | missing original application handler |
| POST | /api/v1/ads/campaigns/{campaign_id}/simulate | backend/app/api/v1/ads.py:569 | missing original application handler |
| POST | /api/v1/ads/campaigns/{campaign_id}/validate | backend/app/api/v1/ads.py:523 | missing original application handler |
| POST | /api/v1/ads/contacts/bulk | backend/app/api/v1/ads.py:2919 | missing original application handler |
| POST | /api/v1/ads/contacts/{contact_id}/action | backend/app/api/v1/ads.py:2650 | missing original application handler |
| POST | /api/v1/ads/creatives/bulk | backend/app/api/v1/ads.py:2895 | missing original application handler |
| POST | /api/v1/ads/creatives/{creative_id}/duplicate | backend/app/api/v1/ads.py:1471 | missing original application handler |
| POST | /api/v1/ads/creatives/{creative_id}/promote | backend/app/api/v1/ads.py:1519 | missing original application handler |
| POST | /api/v1/ads/followup-templates | backend/app/api/v1/ads.py:2235 | missing original application handler |
| POST | /api/v1/ads/followups | backend/app/api/v1/ads.py:2382 | missing original application handler |
| POST | /api/v1/ads/followups/process | backend/app/api/v1/ads.py:2439 | missing original application handler |
| POST | /api/v1/ads/followups/{task_id}/{action} | backend/app/api/v1/ads.py:2401 | missing original application handler |
| POST | /api/v1/ads/sets/preview | backend/app/api/v1/ads.py:1274 | missing original application handler |
| POST | /api/v1/ads/sets/{set_id}/creatives | backend/app/api/v1/ads.py:1364 | missing original application handler |
| POST | /api/v1/ads/sets/{set_id}/duplicate | backend/app/api/v1/ads.py:1239 | missing original application handler |
| POST | /api/v1/ads/sets/{set_id}/pause-losers | backend/app/api/v1/ads.py:1252 | missing original application handler |
| POST | /api/v1/ads/suppression | backend/app/api/v1/ads.py:2598 | missing original application handler |
| POST | /api/v1/ads/track/click | backend/app/api/v1/ads.py:1813 | missing original application handler |
| POST | /api/v1/ai-agent/actions/{action_id}/approve | backend/app/api/v1/ai_agent.py:602 | missing original application handler |
| POST | /api/v1/ai-agent/actions/{action_id}/cancel | backend/app/api/v1/ai_agent.py:624 | missing original application handler |
| POST | /api/v1/ai-agent/activity/{action_id}/undo | backend/app/api/v1/ai_agent.py:674 | missing original application handler |
| POST | /api/v1/ai-agent/files | backend/app/api/v1/ai_agent.py:790 | missing original application handler |
| POST | /api/v1/ai-agent/sessions | backend/app/api/v1/ai_agent.py:398 | missing original application handler |
| POST | /api/v1/ai-agent/sessions/{session_id}/chat | backend/app/api/v1/ai_agent.py:531 | missing original application handler |
| POST | /api/v1/ai-agent/sessions/{session_id}/files | backend/app/api/v1/ai_agent.py:777 | missing original application handler |
| POST | /api/v1/ai-agent/settings/test | backend/app/api/v1/ai_agent.py:307 | missing original application handler |
| POST | /api/v1/ai-agent/skills | backend/app/api/v1/ai_agent.py:977 | missing original application handler |
| POST | /api/v1/ai-agent/skills/json | backend/app/api/v1/ai_agent.py:999 | missing original application handler |
| POST | /api/v1/ai-agent/transcribe | backend/app/api/v1/ai_agent.py:853 | missing original application handler |
| POST | /api/v1/ai/classify | backend/app/api/v1/ai.py:24 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/auth/admin | backend/app/api/v1/auth.py:187 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/auth/login | backend/app/api/v1/auth.py:212 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/auth/logout | backend/app/api/v1/auth.py:229 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/automations/ | backend/app/api/v1/automations.py:60 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/automations/test | backend/app/api/v1/automations.py:144 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/autoreply/ | backend/app/api/v1/autoreply.py:67 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/autoreply/test | backend/app/api/v1/autoreply.py:148 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/calendar/ | backend/app/api/v1/calendar.py:361 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/calendar/{meeting_id}/cancel | backend/app/api/v1/calendar.py:587 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/calendar/{meeting_id}/complete | backend/app/api/v1/calendar.py:577 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/calendar/{meeting_id}/confirm | backend/app/api/v1/calendar.py:568 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/calendar/{meeting_id}/no-show | backend/app/api/v1/calendar.py:597 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/calendar/{meeting_id}/send-invite | backend/app/api/v1/calendar.py:606 | missing original application handler |
| POST | /api/v1/calendar/{meeting_id}/send-reminder | backend/app/api/v1/calendar.py:620 | missing original application handler |
| POST | /api/v1/calls/end | backend/app/api/v1/calls.py:240 | missing original application handler |
| POST | /api/v1/calls/log-direct | backend/app/api/v1/calls.py:263 | missing original application handler |
| POST | /api/v1/calls/register-webhook | backend/app/api/v1/calls.py:125 | missing original application handler |
| POST | /api/v1/calls/start | backend/app/api/v1/calls.py:184 | missing original application handler |
| POST | /api/v1/calls/test | backend/app/api/v1/calls.py:102 | missing original application handler |
| POST | /api/v1/campaign-followups/campaigns/{campaign_id} | backend/app/api/v1/campaign_followups.py:144 | missing original application handler |
| POST | /api/v1/campaign-followups/campaigns/{campaign_id}/process-now | backend/app/api/v1/campaign_followups.py:287 | missing original application handler |
| POST | /api/v1/campaign-followups/{followup_id}/run | backend/app/api/v1/campaign_followups.py:264 | missing original application handler |
| POST | /api/v1/campaigns/ | backend/app/api/v1/campaigns.py:160 | missing original application handler |
| POST | /api/v1/campaigns/{campaign_id}/duplicate | backend/app/api/v1/campaigns.py:917 | missing original application handler |
| POST | /api/v1/campaigns/{campaign_id}/pause | backend/app/api/v1/campaigns.py:481 | missing original application handler |
| POST | /api/v1/campaigns/{campaign_id}/resume | backend/app/api/v1/campaigns.py:507 | missing original application handler |
| POST | /api/v1/campaigns/{campaign_id}/schedule | backend/app/api/v1/campaigns.py:340 | missing original application handler |
| POST | /api/v1/campaigns/{campaign_id}/start | backend/app/api/v1/campaigns.py:394 | missing original application handler |
| POST | /api/v1/campaigns/{campaign_id}/stop | backend/app/api/v1/campaigns.py:576 | missing original application handler |
| POST | /api/v1/campaigns/{campaign_id}/validate | backend/app/api/v1/campaigns.py:321 | missing original application handler |
| POST | /api/v1/contacts/ | backend/app/api/v1/contacts.py:602 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/contacts/bulk | backend/app/api/v1/contacts.py:978 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/contacts/clean | backend/app/api/v1/contacts.py:477 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/contacts/enrich | backend/app/api/v1/contacts.py:1558 | missing original application handler |
| POST | /api/v1/contacts/import/csv | backend/app/api/v1/contacts.py:1393 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/contacts/import/jobs | backend/app/api/v1/contacts.py:1241 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/contacts/import/preview | backend/app/api/v1/contacts.py:1227 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/contacts/import/url | backend/app/api/v1/contacts.py:1274 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/contacts/quarantine/generated | backend/app/api/v1/contacts.py:569 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/contacts/validate-emails | backend/app/api/v1/contacts.py:495 | missing original application handler |
| POST | /api/v1/contacts/{contact_id}/email-opt-in | backend/app/api/v1/contacts.py:1749 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/contacts/{contact_id}/email-opt-out | backend/app/api/v1/contacts.py:1725 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/contacts/{contact_id}/sms-opt-in | backend/app/api/v1/contacts.py:1773 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/email/accounts | backend/app/api/v1/email.py:226 | missing original application handler |
| POST | /api/v1/email/accounts/{account_id}/default | backend/app/api/v1/email.py:507 | missing original application handler |
| POST | /api/v1/email/accounts/{account_id}/test | backend/app/api/v1/email.py:522 | missing original application handler |
| POST | /api/v1/email/accounts/{account_id}/test-send | backend/app/api/v1/email.py:541 | missing original application handler |
| POST | /api/v1/email/accounts/{account_id}/webhook/reveal | backend/app/api/v1/email.py:443 | missing original application handler |
| POST | /api/v1/email/accounts/{account_id}/webhook/rotate | backend/app/api/v1/email.py:471 | missing original application handler |
| POST | /api/v1/email/domains | backend/app/api/v1/email.py:339 | missing original application handler |
| POST | /api/v1/email/domains/{domain_id}/verify | backend/app/api/v1/email.py:393 | missing original application handler |
| POST | /api/v1/email/inbox/conversations/{conversation_id}/read | backend/app/api/v1/email.py:1528 | missing original application handler |
| POST | /api/v1/email/inbox/conversations/{conversation_id}/reply | backend/app/api/v1/email.py:1405 | missing original application handler |
| POST | /api/v1/email/inbox/conversations/{conversation_id}/status | backend/app/api/v1/email.py:1551 | missing original application handler |
| POST | /api/v1/email/messages/{message_id}/retry | backend/app/api/v1/email.py:1122 | missing original application handler |
| POST | /api/v1/email/preview | backend/app/api/v1/email.py:839 | missing original application handler |
| POST | /api/v1/email/send | backend/app/api/v1/email.py:895 | missing original application handler |
| POST | /api/v1/email/senders-preview | backend/app/api/v1/email.py:557 | missing original application handler |
| POST | /api/v1/email/suppression | backend/app/api/v1/email.py:1646 | missing original application handler |
| POST | /api/v1/email/test-send | backend/app/api/v1/email.py:623 | missing original application handler |
| POST | /api/v1/email/unsubscribe | backend/app/api/v1/email.py:1796 | missing original application handler |
| POST | /api/v1/followups/ | backend/app/api/v1/followups.py:154 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/followups/{followup_id}/reschedule | backend/app/api/v1/followups.py:305 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/followups/{followup_id}/send-now | backend/app/api/v1/followups.py:248 | missing original application handler |
| POST | /api/v1/followups/{followup_id}/skip | backend/app/api/v1/followups.py:273 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/inbox/conversations/{conversation_id}/mark-close | backend/app/api/v1/inbox.py:401 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/inbox/conversations/{conversation_id}/mark-interested | backend/app/api/v1/inbox.py:393 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/inbox/conversations/{conversation_id}/mark-not-interested | backend/app/api/v1/inbox.py:397 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/inbox/conversations/{conversation_id}/mark-opportunity | backend/app/api/v1/inbox.py:405 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/inbox/conversations/{conversation_id}/mark-unread | backend/app/api/v1/inbox.py:426 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/inbox/conversations/{conversation_id}/reply | backend/app/api/v1/inbox.py:298 | missing original application handler |
| POST | /api/v1/inbox/conversations/{conversation_id}/resume-sequence | backend/app/api/v1/inbox.py:444 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/inbox/conversations/{conversation_id}/status | backend/app/api/v1/inbox.py:414 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/inbox/conversations/{conversation_id}/stop-sequence | backend/app/api/v1/inbox.py:435 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/inbox/conversations/{conversation_id}/{mark} | backend/app/api/v1/inbox.py:851 | missing original application handler |
| POST | /api/v1/inbox/messages/{message_id}/feedback | backend/app/api/v1/inbox.py:786 | missing original application handler |
| POST | /api/v1/inbox/poll-debug | backend/app/api/v1/inbox.py:700 | missing original application handler |
| POST | /api/v1/inbox/poll-now | backend/app/api/v1/inbox.py:768 | missing original application handler |
| POST | /api/v1/inbox/register-webhook | backend/app/api/v1/inbox.py:527 | missing original application handler |
| POST | /api/v1/inbox/sync-full | backend/app/api/v1/inbox.py:544 | missing original application handler |
| POST | /api/v1/invoices | backend/app/api/v1/invoices.py:851 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/invoices/links | backend/app/api/v1/invoices.py:946 | missing original application handler |
| POST | /api/v1/invoices/paystack/webhook | backend/app/api/v1/invoices.py:537 | missing original application handler |
| POST | /api/v1/invoices/public/{token}/pay | backend/app/api/v1/invoices.py:658 | missing original application handler |
| POST | /api/v1/invoices/settings/test | backend/app/api/v1/invoices.py:308 | missing original application handler |
| POST | /api/v1/invoices/{invoice_id}/duplicate | backend/app/api/v1/invoices.py:1216 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/invoices/{invoice_id}/pay | backend/app/api/v1/invoices.py:1338 | missing original application handler |
| POST | /api/v1/invoices/{invoice_id}/payments | backend/app/api/v1/invoices.py:1409 | missing original application handler |
| POST | /api/v1/invoices/{invoice_id}/remind | backend/app/api/v1/invoices.py:1288 | missing original application handler |
| POST | /api/v1/invoices/{invoice_id}/rotate-link | backend/app/api/v1/invoices.py:1489 | missing original application handler |
| POST | /api/v1/invoices/{invoice_id}/send | backend/app/api/v1/invoices.py:1275 | missing original application handler |
| POST | /api/v1/invoices/{invoice_id}/void | backend/app/api/v1/invoices.py:1323 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/lists/ | backend/app/api/v1/lists.py:172 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/lists/{list_id}/clean | backend/app/api/v1/lists.py:521 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/lists/{list_id}/contacts | backend/app/api/v1/lists.py:315 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/lists/{list_id}/contacts/add-all | backend/app/api/v1/lists.py:387 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/lists/{list_id}/contacts/delete | backend/app/api/v1/lists.py:464 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/lists/{list_id}/contacts/remove | backend/app/api/v1/lists.py:417 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/lists/{list_id}/validate-emails | backend/app/api/v1/lists.py:547 | missing original application handler |
| POST | /api/v1/mail/messages/{mailbox_id}/{provider_message_id}/delete | backend/app/api/v1/mail.py:155 | missing original application handler |
| POST | /api/v1/mail/messages/{mailbox_id}/{provider_message_id}/flags | backend/app/api/v1/mail.py:117 | missing original application handler |
| POST | /api/v1/mail/messages/{mailbox_id}/{provider_message_id}/move | backend/app/api/v1/mail.py:137 | missing original application handler |
| POST | /api/v1/mail/messages/{mailbox_id}/{provider_message_id}/reply | backend/app/api/v1/mail.py:170 | missing original application handler |
| POST | /api/v1/mail/refresh | backend/app/api/v1/mail.py:194 | missing original application handler |
| POST | /api/v1/mailbox | backend/app/api/v1/mailbox.py:463 | missing original application handler |
| POST | /api/v1/mailbox/ | backend/app/api/v1/mailbox.py:463 | missing original application handler |
| POST | /api/v1/mailbox/{mailbox_id}/check | backend/app/api/v1/mailbox.py:581 | missing original application handler |
| POST | /api/v1/mailbox/{mailbox_id}/filters | backend/app/api/v1/mailbox.py:607 | missing original application handler |
| POST | /api/v1/mailbox/{mailbox_id}/sync | backend/app/api/v1/mailbox.py:594 | missing original application handler |
| POST | /api/v1/mailbox/{mailbox_id}/test-send | backend/app/api/v1/mailbox.py:641 | missing original application handler |
| POST | /api/v1/mcp/connectors/{key} | backend/app/api/v1/mcp.py:1134 | missing original application handler |
| POST | /api/v1/mcp/connectors/{key}/revoke | backend/app/api/v1/mcp.py:1202 | missing original application handler |
| POST | /api/v1/mcp/connectors/{key}/test | backend/app/api/v1/mcp.py:1223 | missing original application handler |
| POST | /api/v1/mcp/tokens | backend/app/api/v1/mcp.py:971 | missing original application handler |
| POST | /api/v1/notifications/push/subscribe | backend/app/api/v1/notifications.py:116 | missing original application handler |
| POST | /api/v1/notifications/push/test | backend/app/api/v1/notifications.py:168 | missing original application handler |
| POST | /api/v1/notifications/push/unsubscribe | backend/app/api/v1/notifications.py:144 | missing original application handler |
| POST | /api/v1/notifications/read-all | backend/app/api/v1/notifications.py:80 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/notifications/{event_id}/read | backend/app/api/v1/notifications.py:67 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/send/ | backend/app/api/v1/send.py:37 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/send/messages/{mid}/retry | backend/app/api/v1/send.py:468 | missing original application handler |
| POST | /api/v1/send/scheduled/{sid}/cancel | backend/app/api/v1/send.py:440 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/send/scheduled/{sid}/retry | backend/app/api/v1/send.py:452 | missing original application handler |
| POST | /api/v1/send/validate | backend/app/api/v1/send.py:264 | missing original application handler |
| POST | /api/v1/sequences/ | backend/app/api/v1/sequences.py:119 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/settings/callgate/register-webhook | backend/app/api/v1/settings.py:258 | missing original application handler |
| POST | /api/v1/settings/callgate/test | backend/app/api/v1/settings.py:240 | missing original application handler |
| POST | /api/v1/settings/gateway/phones | backend/app/api/v1/settings.py:466 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/settings/gateway/phones/{phone_id}/register-webhook | backend/app/api/v1/settings.py:538 | missing original application handler |
| POST | /api/v1/settings/gateway/phones/{phone_id}/test | backend/app/api/v1/settings.py:518 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/settings/gateway/register-webhook | backend/app/api/v1/settings.py:78 | missing original application handler |
| POST | /api/v1/settings/gateway/test | backend/app/api/v1/settings.py:72 | missing original application handler |
| POST | /api/v1/settings/notifications/{provider}/test | backend/app/api/v1/settings.py:184 | missing original application handler |
| POST | /api/v1/templates/ | backend/app/api/v1/templates.py:215 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/templates/preview | backend/app/api/v1/templates.py:141 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/templates/{template_id}/duplicate | backend/app/api/v1/templates.py:321 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/templates/{template_id}/to-email | backend/app/api/v1/templates.py:353 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/validator/batch | backend/app/api/v1/validator.py:281 | missing original application handler |
| POST | /api/v1/validator/jobs | backend/app/api/v1/validator.py:159 | missing original application handler |
| POST | /api/v1/validator/jobs/{job_id}/cancel | backend/app/api/v1/validator.py:211 | missing original application handler |
| POST | /api/v1/validator/selection | backend/app/api/v1/validator.py:143 | missing original application handler |
| POST | /api/v1/validator/self-test | backend/app/api/v1/validator.py:105 | missing original application handler |
| POST | /api/v1/variables/ | backend/app/api/v1/variables.py:85 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/variables/preview | backend/app/api/v1/variables.py:186 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/variables/sync | backend/app/api/v1/variables.py:76 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/webhooks/brevo | backend/app/api/v1/webhooks.py:628 | missing original application handler |
| POST | /api/v1/webhooks/brevo/{account_id} | backend/app/api/v1/webhooks.py:628 | missing original application handler |
| POST | /api/v1/webhooks/callgate | backend/app/api/v1/webhooks.py:431 | missing original application handler |
| POST | /api/v1/webhooks/smsgate/inbound | backend/app/api/v1/webhooks.py:570 | missing original application handler |
| POST | /api/v1/webhooks/smsgateway | backend/app/api/v1/webhooks.py:228 | partial handler; native WordPress/HTTP unverified |
| POST | /api/v1/webhooks/smsgateway/phone/{phone_id} | backend/app/api/v1/webhooks.py:217 | missing original application handler |
| POST | /connectors/{key}/mcp | backend/app/api/v1/mcp.py:338 | missing original application handler |
| POST | /connectors/{key}/oauth/authorize | backend/app/api/v1/mcp.py:667 | missing original application handler |
| POST | /connectors/{key}/oauth/register | backend/app/api/v1/mcp.py:429 | missing original application handler |
| POST | /connectors/{key}/oauth/revoke | backend/app/api/v1/mcp.py:931 | missing original application handler |
| POST | /connectors/{key}/oauth/token | backend/app/api/v1/mcp.py:921 | missing original application handler |
| POST | /mcp | backend/app/api/v1/mcp.py:300 | missing original application handler |
| POST | /mcp/ | backend/app/api/v1/mcp.py:300 | missing original application handler |
| POST | /oauth/authorize | backend/app/api/v1/mcp.py:667 | missing original application handler |
| POST | /oauth/register | backend/app/api/v1/mcp.py:429 | missing original application handler |
| POST | /oauth/revoke | backend/app/api/v1/mcp.py:931 | missing original application handler |
| POST | /oauth/token | backend/app/api/v1/mcp.py:921 | missing original application handler |
| PUT | /api/v1/ai-agent/memory/global | backend/app/api/v1/ai_agent.py:903 | missing original application handler |
| PUT | /api/v1/ai-agent/memory/{session_id} | backend/app/api/v1/ai_agent.py:931 | missing original application handler |
| PUT | /api/v1/ai-agent/presets | backend/app/api/v1/ai_agent.py:153 | missing original application handler |
| PUT | /api/v1/ai-agent/settings | backend/app/api/v1/ai_agent.py:258 | missing original application handler |
| PUT | /api/v1/ai-agent/settings/voice | backend/app/api/v1/ai_agent.py:284 | missing original application handler |
| PUT | /api/v1/automations/{automation_id} | backend/app/api/v1/automations.py:100 | partial handler; native WordPress/HTTP unverified |
| PUT | /api/v1/autoreply/{rule_id} | backend/app/api/v1/autoreply.py:99 | partial handler; native WordPress/HTTP unverified |
| PUT | /api/v1/calendar/{meeting_id} | backend/app/api/v1/calendar.py:457 | partial handler; native WordPress/HTTP unverified |
| PUT | /api/v1/calls/config | backend/app/api/v1/calls.py:78 | missing original application handler |
| PUT | /api/v1/campaign-followups/{followup_id} | backend/app/api/v1/campaign_followups.py:212 | missing original application handler |
| PUT | /api/v1/campaigns/{campaign_id} | backend/app/api/v1/campaigns.py:221 | missing original application handler |
| PUT | /api/v1/contacts/{contact_id} | backend/app/api/v1/contacts.py:663 | partial handler; native WordPress/HTTP unverified |
| PUT | /api/v1/invoices/settings | backend/app/api/v1/invoices.py:298 | missing original application handler |
| PUT | /api/v1/lists/{list_id} | backend/app/api/v1/lists.py:194 | partial handler; native WordPress/HTTP unverified |
| PUT | /api/v1/sequences/{sequence_id} | backend/app/api/v1/sequences.py:163 | partial handler; native WordPress/HTTP unverified |
| PUT | /api/v1/settings/access | backend/app/api/v1/settings.py:28 | partial handler; native WordPress/HTTP unverified |
| PUT | /api/v1/settings/appearance | backend/app/api/v1/settings.py:633 | partial handler; native WordPress/HTTP unverified |
| PUT | /api/v1/settings/callgate | backend/app/api/v1/settings.py:226 | missing original application handler |
| PUT | /api/v1/settings/compliance | backend/app/api/v1/settings.py:200 | partial handler; native WordPress/HTTP unverified |
| PUT | /api/v1/settings/gateway/phones/{phone_id} | backend/app/api/v1/settings.py:481 | partial handler; native WordPress/HTTP unverified |
| PUT | /api/v1/settings/gateway/routing | backend/app/api/v1/settings.py:558 | missing original application handler |
| PUT | /api/v1/settings/gateway/sim | backend/app/api/v1/settings.py:67 | missing original application handler |
| PUT | /api/v1/settings/google-oauth | backend/app/api/v1/settings.py:357 | missing original application handler |
| PUT | /api/v1/settings/notifications/muted-senders | backend/app/api/v1/settings.py:165 | partial handler; native WordPress/HTTP unverified |
| PUT | /api/v1/settings/notifications/{provider} | backend/app/api/v1/settings.py:172 | partial handler; native WordPress/HTTP unverified |
| PUT | /api/v1/templates/{template_id} | backend/app/api/v1/templates.py:262 | partial handler; native WordPress/HTTP unverified |
| PUT | /api/v1/variables/{variable_id} | backend/app/api/v1/variables.py:123 | partial handler; native WordPress/HTTP unverified |
