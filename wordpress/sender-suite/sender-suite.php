<?php
/**
 * Plugin Name: Sender Suite — development rebuild
 * Description: Incomplete WordPress port. Initial authentication, contact and list data APIs; not a production messaging application.
 * Version: 0.3.0-dev
 * Requires at least: 6.4
 * Requires PHP: 7.4
 * Text Domain: sender-suite
 */
if (!defined('ABSPATH')) { exit; }
foreach (array('schema','database','vault','compat','phone','csv-reader','csv-preview','config','auth','auth-routes','records','contacts','contact-lists','data-routes','installer','plugin') as $sender_suite_file) { require_once __DIR__.'/includes/class-'.$sender_suite_file.'.php'; }
unset($sender_suite_file);
\SenderSuite\Plugin::boot(__FILE__);
