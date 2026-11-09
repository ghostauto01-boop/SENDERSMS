<?php
namespace SenderSuite;

final class Plugin {
    public static function boot($file) {
        register_activation_hook($file,function(){ Installer::activate($GLOBALS['wpdb']); });
        AppShell::boot();
        SmsOutbox::boot($file); // Inert unless a hosting operator explicitly enables the experimental outbox.
        // By default only non-sending contact import cron is enabled. Deactivation retains application data.
        register_deactivation_hook($file,array(ImportJobs::class,'deactivate'));
        add_filter('cron_schedules',array(ImportJobs::class,'schedules'));
        add_filter('rest_pre_serve_request',array(ImportJobs::class,'serve'),10,4);
        add_filter('rest_pre_serve_request',array(ContactExport::class,'serve'),11,4);
        add_action('init',array(ImportJobs::class,'ensureSchedule'));
        add_action(ImportJobs::HOOK,array(ImportJobs::class,'tick'));
        add_action('rest_api_init',function(){ AuthRoutes::register();DataRoutes::register();SmsReceipts::register();CallsSettings::register();Invoices::register();EmailManager::register();AdsManager::register();McpServer::register();register_rest_route('sender-suite/v1','/health',array('methods'=>'GET','permission_callback'=>'__return_true','callback'=>array(self::class,'health')));register_rest_route('sender-suite/v1','/health/db',array('methods'=>'GET','permission_callback'=>'__return_true','callback'=>array(self::class,'healthDb'))); });
        add_action('admin_menu',function(){ add_options_page('Sender Suite','Sender Suite','manage_options','sender-suite',array(self::class,'admin')); });
        add_action('parse_request',array(self::class,'sourceQuery'),0);
        add_filter('rest_pre_dispatch',array(self::class,'sourceRequest'),10,3);
        add_action('admin_post_sender_suite_setup',array(self::class,'setup'));
        add_filter('rest_jsonp_enabled',function($enabled){$route=$GLOBALS['wp']->query_vars['rest_route']??($_GET['rest_route']??'');if(is_string($route)&&stripos('/'.ltrim(rawurldecode($route),'/'),'/sender-suite/v1/')===0){return false;}$uri=$_SERVER['REQUEST_URI']??'';return is_string($uri)&&stripos(rawurldecode($uri),'/sender-suite/v1/')!==false?false:$enabled;});
        add_action('admin_post_sender_suite_preview',array(self::class,'preview'));
    }
    private static function reserved(){return array('_jsonp','_envelope','_fields','_embed','_pretty','_method','_locale','_wpnonce');}
    public static function sourceQuery($wp){$route='/'.ltrim((string)($wp->query_vars['rest_route']??''),'/');if(stripos($route,'/sender-suite/v1/')!==0){return;}foreach(self::reserved() as $name){unset($_GET[$name],$_REQUEST[$name]);}unset($_SERVER['HTTP_X_HTTP_METHOD_OVERRIDE'],$_SERVER['HTTP_X_WP_NONCE']);}
    public static function sourceRequest($result,$server,$request){if(stripos('/'.ltrim($request->get_route(),'/'),'/sender-suite/v1/')===0){$q=$request->get_query_params();foreach(self::reserved() as $name){unset($q[$name]);}$request->set_query_params($q);}return $result;}
    public static function health() { $r=new \WP_REST_Response(array('status'=>'ok','app'=>'Sendify','version'=>'1.0.0'),200);$r->header('Cache-Control','no-store');return $r; }
    public static function healthDb($request=null) { if($request!==null&&!AuthRoutes::origin($request)){return AuthRoutes::response(AuthRoutes::error(403,'Cross-origin request rejected',bin2hex(random_bytes(16))),403,bin2hex(random_bytes(16)));}try{$wpdb=$GLOBALS['wpdb']??null;if(!is_object($wpdb)||!isset($wpdb->prefix)){throw new \RuntimeException('wpdb unavailable');}$probe=SystemGuide::dbProbe(new Database($wpdb,new Schema($wpdb->prefix)));}catch(\Throwable $e){$probe=array('status'=>503,'body'=>array('ok'=>false,'kind'=>'unavailable','message'=>'Database is unavailable.','hint'=>'Verify WordPress database connectivity and Sender Suite table installation.','last_ok_at'=>null,'last_error_at'=>time(),'consecutive_failures'=>1,'last_query_error'=>null,'last_query_error_at'=>null));}$r=new \WP_REST_Response($probe['body'],$probe['status']);$r->header('Cache-Control','no-store');return $r; }
    public static function admin() {
        if (!current_user_can('manage_options')) { wp_die('Forbidden',403); }
        $liveOn=SmsOutbox::enabled();
        $apiBase=rtrim((string)rest_url('sender-suite/v1/'),'/');
        echo '<div class="wrap"><h1>Sender Suite</h1>';
        echo '<p><a class="button button-primary button-hero" href="'.esc_url(AppShell::url()).'" target="_blank" rel="noopener">Open Sender Suite Workspace</a></p>';
        echo '<p>Full WordPress-native multi-channel workspace: Contacts, Lists, Templates, Sequences, SMS-Gate Cloud, Brevo Email, Gmail Sync, Ads Manager, Invoices &amp; Paystack Checkout, CallGate, OpenRouter AI Agent, and MCP / OAuth 2.1.</p>';
        echo '<h2>Application Operator Setup &amp; Live Workers</h2>';
        echo '<p>Configure or rotate your Sender Suite operator login (separate from WordPress admin accounts) and enable live background SMS/campaign dispatch.</p>';
        echo '<form action="'.esc_url(admin_url('admin-post.php')).'" method="post"><input type="hidden" name="action" value="sender_suite_setup">';
        wp_nonce_field('sender_suite_setup');
        echo '<table class="form-table"><tr><th><label for="ss_user">Operator username</label></th><td><input id="ss_user" class="regular-text" name="operator_username" required autocomplete="username" maxlength="150"></td></tr>';
        echo '<tr><th><label for="ss_pass">New operator password</label></th><td><input id="ss_pass" class="regular-text" type="password" name="operator_password" required autocomplete="new-password"></td></tr>';
        echo '<tr><th>Live Workers &amp; SMS Outbox</th><td><label><input type="checkbox" name="enable_live_workers" value="1" '.($liveOn||get_option('sender_suite_live_workers',null)===null?'checked':'').'> Enable live SMS Outbox, Campaign Launch, Calendar Invites &amp; Follow-up Dispatch</label></td></tr></table>';
        submit_button('Save Operator & Worker Settings');
        echo '</form>';
        echo '<h2>Webhook &amp; MCP Endpoints</h2><ul>';
        echo '<li><strong>SMS-Gate Status Webhook:</strong> <code>'.esc_html($apiBase.'/webhooks/smsgateway').'</code></li>';
        echo '<li><strong>SMS-Gate Inbound Webhook:</strong> <code>'.esc_html($apiBase.'/webhooks/smsgate/inbound').'</code></li>';
        echo '<li><strong>Brevo Email Webhook:</strong> <code>'.esc_html($apiBase.'/webhooks/brevo').'</code></li>';
        echo '<li><strong>Paystack Webhook:</strong> <code>'.esc_html($apiBase.'/invoices/paystack/webhook').'</code></li>';
        echo '<li><strong>CallGate Webhook:</strong> <code>'.esc_html($apiBase.'/webhooks/callgate').'</code></li>';
        echo '<li><strong>MCP Streamable HTTP Server:</strong> <code>'.esc_html($apiBase.'/mcp-wire/mcp').'</code></li></ul>';
        echo '<h2>Local CSV Preview Diagnostic</h2><form action="'.esc_url(admin_url('admin-post.php')).'" method="post" enctype="multipart/form-data"><input type="hidden" name="action" value="sender_suite_preview">';
        wp_nonce_field('sender_suite_preview');echo '<input type="file" name="file" required><p>';submit_button('Preview CSV','secondary','submit',false);echo '</p></form></div>';
    }
    public static function setup() {
        if(!current_user_can('manage_options')){wp_die('Forbidden',403);}check_admin_referer('sender_suite_setup');
        try{$username=isset($_POST['operator_username'])?wp_unslash($_POST['operator_username']):null;$password=isset($_POST['operator_password'])?wp_unslash($_POST['operator_password']):null;if(!is_string($username)||!is_string($password)){throw new \InvalidArgumentException('Invalid configuration.');}$c=AuthRoutes::context();$c['config']->setup($username,$password);if(function_exists('update_option')){update_option('sender_suite_live_workers',!empty($_POST['enable_live_workers'])?'1':'0',false);if(!empty($_POST['enable_live_workers'])){SmsOutbox::schedule();}}wp_safe_redirect(admin_url('options-general.php?page=sender-suite'));exit;}catch(\Throwable $e){wp_die('Unable to save operator configuration. Check native installation and enter a username and a nonblank password.',400);}
    }
    public static function preview() {
        if (!current_user_can('manage_options')) { wp_die('Forbidden',403); }check_admin_referer('sender_suite_preview');$file=$_FILES['file']??null;
        if (!is_array($file) || ($file['error']??UPLOAD_ERR_NO_FILE)!==UPLOAD_ERR_OK || !is_string($file['tmp_name']??null) || !is_uploaded_file($file['tmp_name'])) { wp_send_json_error(array('message'=>'A valid uploaded CSV file is required.'),400);return; }
        $bytes=file_get_contents($file['tmp_name'],false,null,0,20*1024*1024+1);if ($bytes===false) { wp_send_json_error(array('message'=>'Unable to read uploaded CSV.'),400);return; }if (strlen($bytes)>20*1024*1024) { wp_send_json_error(array('message'=>'CSV file is larger than 20 MB'),413);return; }
        try { wp_send_json(CsvPreview::preview($bytes),200); }catch (CsvFailure $e) { wp_send_json_error(array('message'=>'Unable to parse this CSV file.'),422); }catch (\Throwable $e) { wp_send_json_error(array('message'=>'CSV preview is unavailable.'),503); }
    }
}
