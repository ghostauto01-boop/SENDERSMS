<?php
/**
 * Plugin Name: Sender Suite
 * Description: WordPress-native SMS, Email, Ads Manager, Invoices (Paystack), CallGate, AI Agent (OpenRouter), and MCP/OAuth 2.1 platform with full 73-table schema and native React SPA.
 * Version: 1.0.0-rc1
 * Requires at least: 6.4
 * Requires PHP: 7.4
 * Text Domain: sender-suite
 */
if (!defined('ABSPATH')) { exit; }
foreach (array('schema','database','vault','compat','phone','number-filter','csv-reader','csv-preview','config','auth','auth-routes','records','access-settings','runtime-settings','contacts','contact-lists','contact-actions','contact-export','templating','suggestions','attachments','variables','contact-profile','variable-routes','templates','template-routes','reply-classifier','reply-rules','reply-routes','notification-center','csv-import','csv-url','import-jobs','import-routes','analytics','dashboard','campaign-reports','campaign-controls','campaign-followups','overview','system-guide','validator','sequences','calendar','invoices','inbox','followups','sms-receipts','sms-gate','provider-http','gateway-phones','calls-settings','email-manager','email-transport','ads-manager','campaign-worker','invoice-settlement','ai-agent','mcp-settings','mcp-server','inbox-reply','send-history','scheduled-sms','sms-outbox','data-routes','installer','app-shell','plugin') as $sender_suite_file) { require_once __DIR__.'/includes/class-'.$sender_suite_file.'.php'; }
unset($sender_suite_file);
\SenderSuite\Plugin::boot(__FILE__);
