<?php
namespace SenderSuite;

final class Plugin {
    public static function boot($file) {
        register_activation_hook($file,function(){ Installer::activate($GLOBALS['wpdb']); });
        // Only non-sending contact import cron is enabled. Deactivation retains application data.
        register_deactivation_hook($file,array(ImportJobs::class,'deactivate'));
        add_filter('cron_schedules',array(ImportJobs::class,'schedules'));
        add_filter('rest_pre_serve_request',array(ImportJobs::class,'serve'),10,4);
        add_action('init',array(ImportJobs::class,'ensureSchedule'));
        add_action(ImportJobs::HOOK,array(ImportJobs::class,'tick'));
        add_action('rest_api_init',function(){ AuthRoutes::register();DataRoutes::register();register_rest_route('sender-suite/v1','/health',array('methods'=>'GET','permission_callback'=>'__return_true','callback'=>array(self::class,'health'))); });
        add_action('admin_menu',function(){ add_options_page('Sender Suite build status','Sender Suite','manage_options','sender-suite',array(self::class,'admin')); });
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
    public static function admin() {
        if (!current_user_can('manage_options')) { wp_die('Forbidden',403); }
        echo '<div class="wrap"><h1>Sender Suite — development rebuild</h1><p><strong>Incomplete. Do not use this build for production messaging.</strong></p><p>Source-derived schema, encrypted-value utility, Unicode/phone parsing, read-only CSV preview and unchanged frontend build are restored. Initial application authentication, encrypted operator setup, contact create/edit/filter/tag APIs and non-destructive list/membership APIs, a variable registry and text-personalization preview plus SMS/email template-library workflows are implemented. Auto-reply/automation rule management and non-sending tests, the built-in reply classifier and in-app notification read state are implemented. Inbound automation execution, cooldown enforcement and notification delivery remain missing. Authenticated CSV upload preview and synchronous import APIs now persist contacts, additive merges, tags, list membership and custom variables. Durable background CSV jobs, progress polling and private CSV error reports are implemented using a non-sending WordPress cron worker. Public URL imports now feed the same durable queue using a bounded pinned-address PHP cURL fetcher. Sending, campaign workers and full interface integration remain missing. URL transport and host-policy validation remain unverified on a real host. Native cron/MySQL validation is still outstanding. No providers or sending jobs are enabled.</p><h2>Local CSV preview diagnostic</h2><p>This administrator-only diagnostic is not the application API. Files are not saved to the media library or imported into contacts. Limit: 20 MiB.</p><form action="'.esc_url(admin_url('admin-post.php')).'" method="post" enctype="multipart/form-data"><input type="hidden" name="action" value="sender_suite_preview">';
        wp_nonce_field('sender_suite_preview');echo '<input type="file" name="file" required><p>';submit_button('Preview CSV','secondary','submit',false);echo '</p></form><h2>Application operator setup</h2><p>Requires a completed native database installation. No shared default password is installed. This form rotates the operator password; it is separate from WordPress administrator login.</p><form action="'.esc_url(admin_url('admin-post.php')).'" method="post"><input type="hidden" name="action" value="sender_suite_setup">';wp_nonce_field('sender_suite_setup');echo '<label>Operator username <input name="operator_username" required autocomplete="username" maxlength="150"></label><p><label>New operator password <input type="password" name="operator_password" required autocomplete="new-password"></label></p>';submit_button('Save operator credentials');echo '</form></div>';
    }
    public static function setup() {
        if(!current_user_can('manage_options')){wp_die('Forbidden',403);}check_admin_referer('sender_suite_setup');
        try{$username=isset($_POST['operator_username'])?wp_unslash($_POST['operator_username']):null;$password=isset($_POST['operator_password'])?wp_unslash($_POST['operator_password']):null;if(!is_string($username)||!is_string($password)){throw new \InvalidArgumentException('Invalid configuration.');}$c=AuthRoutes::context();$c['config']->setup($username,$password);wp_safe_redirect(admin_url('options-general.php?page=sender-suite'));exit;}catch(\Throwable $e){wp_die('Unable to save operator configuration. Check native installation and enter a username and a nonblank password.',400);}
    }
    public static function preview() {
        if (!current_user_can('manage_options')) { wp_die('Forbidden',403); }check_admin_referer('sender_suite_preview');$file=$_FILES['file']??null;
        if (!is_array($file) || ($file['error']??UPLOAD_ERR_NO_FILE)!==UPLOAD_ERR_OK || !is_string($file['tmp_name']??null) || !is_uploaded_file($file['tmp_name'])) { wp_send_json_error(array('message'=>'A valid uploaded CSV file is required.'),400);return; }
        $bytes=file_get_contents($file['tmp_name'],false,null,0,20*1024*1024+1);if ($bytes===false) { wp_send_json_error(array('message'=>'Unable to read uploaded CSV.'),400);return; }if (strlen($bytes)>20*1024*1024) { wp_send_json_error(array('message'=>'CSV file is larger than 20 MB'),413);return; }
        try { wp_send_json(CsvPreview::preview($bytes),200); }catch (CsvFailure $e) { wp_send_json_error(array('message'=>'Unable to parse this CSV file.'),422); }catch (\Throwable $e) { wp_send_json_error(array('message'=>'CSV preview is unavailable.'),503); }
    }
}
