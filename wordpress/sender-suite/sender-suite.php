<?php
/**
 * Plugin Name: Sender Suite — development rebuild
 * Description: Incomplete WordPress port. Schema, local utilities and initial app authentication restored; not a production messaging application.
 * Version: 0.2.0-dev
 * Requires at least: 6.4
 * Requires PHP: 7.4
 * Text Domain: sender-suite
 */
if (!defined('ABSPATH')) { exit; }
foreach (array('schema','database','vault','compat','phone','csv-reader','csv-preview','config','auth','auth-routes','installer','plugin') as $sender_suite_file) { require_once __DIR__.'/includes/class-'.$sender_suite_file.'.php'; }
unset($sender_suite_file);
\SenderSuite\Plugin::boot(__FILE__);
