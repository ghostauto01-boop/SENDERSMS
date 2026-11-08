<?php
/** Isolated CI worker entry point only. Not a deployed web endpoint or cron substitute. */
$root=getenv('NATIVE_WP_ROOT');
if (!$root || getenv('NATIVE_DB_PASSWORD')!=='isolated-wordpress-test-only' || !getenv('NATIVE_WP_EVIDENCE') || !is_file($root.'/wp-config.php')) { throw new RuntimeException('Refusing non-isolated worker execution.'); }
require $root.'/wp-load.php';
if (!defined('DISABLE_WP_CRON') || !DISABLE_WP_CRON || !defined('WP_HTTP_BLOCK_EXTERNAL') || !WP_HTTP_BLOCK_EXTERNAL || $GLOBALS['wpdb']->get_var('SELECT DATABASE()')!=='sender_suite_wordpress_test') { throw new RuntimeException('Isolated test database and network sink required.'); }
\SenderSuite\ImportJobs::process(\SenderSuite\AuthRoutes::context()['db']);
echo json_encode(array('ok'=>true,'provider_messages_sent'=>0),JSON_THROW_ON_ERROR)."\n";
