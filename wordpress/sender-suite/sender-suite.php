<?php
/**
 * Plugin Name: Sender Suite — development rebuild
 * Description: Incomplete WordPress port. Authentication, contacts/CSV import, lists, templates, reply-rule and notification data APIs; not a production messaging application.
 * Version: 0.11.0-dev
 * Requires at least: 6.4
 * Requires PHP: 7.4
 * Text Domain: sender-suite
 */
if (!defined('ABSPATH')) { exit; }
foreach (array('schema','database','vault','compat','phone','number-filter','csv-reader','csv-preview','config','auth','auth-routes','records','contacts','contact-lists','contact-actions','contact-export','templating','suggestions','attachments','variables','variable-routes','templates','template-routes','reply-classifier','reply-rules','reply-routes','notification-center','csv-import','csv-url','import-jobs','import-routes','analytics','sequences','data-routes','installer','plugin') as $sender_suite_file) { require_once __DIR__.'/includes/class-'.$sender_suite_file.'.php'; }
unset($sender_suite_file);
\SenderSuite\Plugin::boot(__FILE__);
