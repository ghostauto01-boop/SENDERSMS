<?php
namespace SenderSuite;

/** Honest WordPress-native health, error log reads and read-only setup guide. Never fabricates Redis/Celery or mutates on GET. */
final class SystemGuide {
    const WORKER_STALE_SECONDS=300;
    const FOLLOWUP_OVERDUE_SECONDS=900;
    private $db;private $config;private $records;
    public function __construct(Database $db,Config $config=null,callable $clock=null){$this->db=$db;$this->config=$config;$this->records=new Records($db,$clock);}
    private function table($n){return Schema::identifier($this->db->schema()->table($n));}
    public static function definitions(){return array('/system/health'=>array('GET'),'/system/errors'=>array('GET'),'/guide/?'=>array('GET'),'/guide/summary'=>array('GET'));}
    public static function dbProbe(Database $db){
        try{
            $db->rows('SELECT 1 AS ok');
            $db->rows('SELECT COUNT(*) AS n FROM '.Schema::identifier($db->schema()->table('system_settings')));
            return array('status'=>200,'body'=>array('ok'=>true,'kind'=>null,'message'=>null,'hint'=>null,'last_ok_at'=>time(),'last_error_at'=>null,'consecutive_failures'=>0,'last_query_error'=>null,'last_query_error_at'=>null));
        }catch(\Throwable $e){
            return array('status'=>503,'body'=>array('ok'=>false,'kind'=>'unavailable','message'=>'Database is unavailable.','hint'=>'Verify WordPress database connectivity and Sender Suite table installation.','last_ok_at'=>null,'last_error_at'=>time(),'consecutive_failures'=>1,'last_query_error'=>null,'last_query_error_at'=>null));
        }
    }
    private static function scrub($text){
        if(!is_string($text)||$text===''){return '';}
        $patterns=array('/(password|secret|token|api[_-]?key|authorization)\s*[:=]\s*[^\s,;]+/i'=>'$1=[redacted]','/Bearer\s+[A-Za-z0-9._~+\/-]+=*/i'=>'Bearer [redacted]','/[A-Za-z0-9+_-]+:\/\/[^\s@]+@/i'=>'[dsn-redacted]@');
        return preg_replace(array_keys($patterns),array_values($patterns),$text);
    }
    private function serializeError(array $raw,$includeTrace=false){
        $r=$this->db->decode('system_error_records',$raw);
        $out=array('request_id'=>$r['request_id'],'code'=>$r['code'],'exception_type'=>$r['exception_type'],'message'=>self::scrub($r['message']),'method'=>$r['method'],'path'=>$r['path'],'created_at'=>Records::iso($r['created_at']));
        if($includeTrace){$out['traceback']=self::scrub($r['traceback']);}
        return $out;
    }
    public function errors(array $q,array $user){
        if(($user['role']??'')!=='admin'){throw new AuthFailure(403,'Administrator access required');}
        $reqId=DataRoutes::text($q,'request_id',null,1,64);
        $limit=DataRoutes::integer($q['limit']??50,'limit',1,200);
        if($reqId!==null){
            if(strpos($reqId,"\0")!==false||preg_match('//u',$reqId)!==1){throw new ValidationFailure('Invalid request_id','request_id');}
            $rows=$this->db->rows('SELECT id,request_id,code,exception_type,CASE WHEN OCTET_LENGTH(message)<=65536 THEN message ELSE SUBSTR(message,1,65536) END AS message,CASE WHEN OCTET_LENGTH(traceback)<=262144 THEN traceback ELSE SUBSTR(traceback,1,262144) END AS traceback,method,path,created_at FROM '.$this->table('system_error_records').' WHERE request_id=%s LIMIT 1',array($reqId));
            if(!$rows){throw new AuthFailure(404,'No stored error for this request_id');}
            return $this->serializeError($rows[0],true);
        }
        $rows=$this->db->rows('SELECT id,request_id,code,exception_type,CASE WHEN OCTET_LENGTH(message)<=65536 THEN message ELSE SUBSTR(message,1,65536) END AS message,\'\' AS traceback,method,path,created_at FROM '.$this->table('system_error_records').' ORDER BY created_at DESC,id DESC LIMIT %d',array($limit));
        $items=array();foreach($rows as $r){$items[]=$this->serializeError($r,false);}
        return array('items'=>$items);
    }
    public function health(){
        $nowStr=$this->records->now();$nowDt=new \DateTimeImmutable($nowStr,new \DateTimeZone('UTC'));
        $nowTs=(int)$nowDt->format('U');
        $overdueCut=$nowDt->modify('-'.self::FOLLOWUP_OVERDUE_SECONDS.' seconds')->format('Y-m-d H:i:s.u');
        $staleJobCut=$nowDt->modify('-10 minutes')->format('Y-m-d H:i:s.u');
        $components=array();
        $this->db->rows('SELECT 1 AS ok');
        $components['database']=array('ok'=>true,'message'=>'WordPress database is responding.');
        $components['redis']=array('ok'=>false,'configured'=>false,'message'=>'WordPress native runtime does not use Redis.');
        $components['celery_workers']=array('ok'=>false,'workers'=>array(),'message'=>'WordPress native runtime does not use Celery workers.');
        $components['inline_poller']=array('ok'=>false,'enabled'=>false,'message'=>'WordPress native runtime uses explicit WP-Cron hooks instead of the Python inline poller.');
        $hbRow=$this->db->one('system_settings',array('key'=>'worker.heartbeat_at'));
        $hbSrc=$this->db->one('system_settings',array('key'=>'worker.heartbeat_source'));
        $hbAt=$hbRow!==null&&is_numeric($hbRow['value'])&&((float)$hbRow['value'])>0?(float)$hbRow['value']:null;
        $hbSource=$hbSrc?$hbSrc['value']:null;
        $hbAge=$hbAt!==null?max(0,(int)floor($nowTs-$hbAt)):null;
        $overdue=(int)$this->db->rows("SELECT COUNT(*) AS n FROM ".$this->table('followups')." WHERE status='pending' AND scheduled_at<=%s",array($overdueCut))[0]['n']
            +(int)$this->db->rows("SELECT COUNT(*) AS n FROM ".$this->table('ads_followup_tasks')." WHERE status='pending' AND due_at<=%s",array($overdueCut))[0]['n'];
        $due=(int)$this->db->rows("SELECT COUNT(*) AS n FROM ".$this->table('followups')." WHERE status='pending' AND scheduled_at<=%s",array($nowStr))[0]['n']
            +(int)$this->db->rows("SELECT COUNT(*) AS n FROM ".$this->table('ads_followup_tasks')." WHERE status='pending' AND due_at<=%s",array($nowStr))[0]['n']
            +(int)$this->db->rows("SELECT COUNT(*) AS n FROM ".$this->table('messages')." WHERE direction='outgoing' AND status IN ('queued','retrying') AND (retry_at IS NULL OR retry_at<=%s)",array($nowStr))[0]['n'];
        $hbStale=$hbAge===null||$hbAge>self::WORKER_STALE_SECONDS;
        $workDue=$due>0;$outboxOn=SmsOutbox::enabled();
        $components['background_worker']=array('ok'=>false,'sms_outbox_enabled'=>$outboxOn,'campaign_worker_enabled'=>false,'last_heartbeat_at'=>$hbAt!==null?gmdate('Y-m-d\TH:i:s+00:00',(int)$hbAt):null,'heartbeat_source'=>$hbSource,'age_seconds'=>$hbAge,'stale'=>$hbStale,'due_followups'=>$due,'overdue_followups'=>$overdue,'message'=>$workDue?"{$due} queued/follow-up item(s) are due. Native bulk campaign and follow-up execution workers are disabled pending host qualification.":($outboxOn?'Single-contact SMS outbox cron is enabled; bulk campaign and follow-up workers remain disabled.':'Background sending workers are disabled; CSV import cron is active.'));
        $jobCounts=new \stdClass();$failedJobs=0;$hasJobCounts=false;
        foreach($this->db->rows('SELECT status,COUNT(*) AS n FROM '.$this->table('ads_campaign_jobs').' GROUP BY status LIMIT 100') as $jr){$st=(string)($jr['status']??'');$n=(int)$jr['n'];$jobCounts->$st=$n;$hasJobCounts=true;if($st==='failed'){$failedJobs=$n;}}
        $dueJobs=(int)$this->db->rows("SELECT COUNT(*) AS n FROM ".$this->table('ads_campaign_jobs')." WHERE status='queued' AND scheduled_for IS NOT NULL AND scheduled_for<=%s",array($nowStr))[0]['n'];
        $staleJobs=(int)$this->db->rows("SELECT COUNT(*) AS n FROM ".$this->table('ads_campaign_jobs')." WHERE status='running' AND updated_at<%s",array($staleJobCut))[0]['n'];
        $jobsOk=$dueJobs===0&&$staleJobs===0&&$failedJobs===0;
        $components['campaign_jobs']=array('ok'=>$jobsOk,'by_status'=>$hasJobCounts?$jobCounts:new \stdClass(),'due'=>$dueJobs,'stale_running'=>$staleJobs,'failed'=>$failedJobs,'message'=>$jobsOk?'No stuck or failed stored campaign jobs.':"{$dueJobs} due job(s), {$staleJobs} stale worker claim(s), and {$failedJobs} failed job(s) are recorded in storage.");
        return array('ok'=>false,'degraded'=>true,'due_followups'=>$due,'overdue_followups'=>$overdue,'checked_at'=>Records::iso($nowStr),'components'=>$components,'message'=>'WordPress database is healthy; native bulk campaign and follow-up workers remain disabled pending host qualification.');
    }
    private static function step($id,$group,$title,$status,$why,$detail='',array $steps=array(),$route=null,$routeLabel='Open',array $copy=array(),$verify=''){
        return array('id'=>$id,'group'=>$group,'title'=>$title,'status'=>$status,'why'=>$why,'detail'=>$detail,'steps'=>$steps,'route'=>$route,'route_label'=>$route?$routeLabel:null,'copy'=>$copy,'docs'=>array(),'verify'=>$verify);
    }
    private function usableGatewayCount(){
        $n=0;
        foreach($this->db->rows('SELECT id,username,password_encrypted FROM '.$this->table('gateway_settings').' WHERE is_enabled=1 LIMIT 100') as $raw){
            $r=$this->db->decode('gateway_settings',$raw);
            if(Compat::strip($r['username']??'')===''||Compat::strip($r['password_encrypted']??'')===''){continue;}
            if($this->config!==null){try{if($this->config->openRuntimeSecret($this->db->schema()->table('gateway_settings').'/'.$r['id'].'/password',$r['password_encrypted'])===''){continue;}}catch(\Throwable $e){continue;}}
            $n++;
        }
        return $n;
    }
    private function webhookSecretReady(){
        $row=$this->db->one('system_settings',array('key'=>SmsReceipts::KEY));
        if(!$row||Compat::strip($row['value']??'')===''){return false;}
        if($this->config!==null){try{return $this->config->openRuntimeSecret($this->db->schema()->table('system_settings').'/'.$row['id'].'/'.SmsReceipts::KEY,$row['value'])!=='';}catch(\Throwable $e){return false;}}
        return true;
    }
    public function guide(){
        $siteUrl=function_exists('home_url')?rtrim((string)home_url('/'),'/'):null;
        $webhookEndpoint=function_exists('rest_url')?(string)rest_url('sender-suite/v1/webhooks/smsgateway'):'/wp-json/sender-suite/v1/webhooks/smsgateway';
        $readySecrets=$this->config!==null?$this->config->ready():true;
        $isHttps=$siteUrl!==null&&stripos($siteUrl,'https://')===0;
        $outboxOn=SmsOutbox::enabled();
        $phones=$this->usableGatewayCount();
        $webhookReady=$this->webhookSecretReady();
        $contacts=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('contacts'))[0]['n'];
        $lists=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('contact_lists'))[0]['n'];
        $notifOn=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('notification_providers').' WHERE is_enabled=1')[0]['n'];
        $steps=array(
            self::step('secrets','foundation','Application operator credentials & vault',$readySecrets?'done':'todo','Sessions and encrypted provider secrets depend on your WordPress salts and dedicated operator login.',$readySecrets?'Operator credentials and encrypted runtime vault are active.':'Complete operator setup in WordPress Settings → Sender Suite.',$readySecrets?array():array('Open WordPress Admin → Settings → Sender Suite.','Set a dedicated operator username and strong password.'),null,'Open',array(),'Sign in at the native Sender Suite login screen.'),
            self::step('public-base-url','foundation','WordPress HTTPS site address',$isHttps?'done':'attention','External SMS-Gate delivery receipts require an HTTPS WordPress URL reachable from the internet.',$siteUrl?"WordPress home URL is {$siteUrl}.":'WordPress home URL is not HTTPS yet.',$isHttps?array():array('Serve WordPress over HTTPS so webhook callbacks are encrypted in transit.'),null,'Open',$siteUrl?array(array('label'=>'Site URL','value'=>$siteUrl)):array(),'Open your WordPress site over HTTPS from an external network.'),
            self::step('database','foundation','WordPress application tables','done','Contacts, lists, templates, campaigns, inbox threads and encrypted settings live in native WordPress tables.','Connected to WordPress application schema.',array(),null,'Open',array(),'GET /wp-json/sender-suite/v1/health/db returns 200.'),
            self::step('worker','foundation','Native background workers','attention','CSV import jobs run on WP-Cron; single-contact SMS outbox is opt-in; bulk campaign and follow-up workers remain gated pending native host qualification.',$outboxOn?'Experimental single-contact SMS outbox cron is enabled; bulk campaign/follow-up launch remains disabled.':'Single-contact SMS outbox is disabled by default (SENDER_SUITE_ENABLE_SMS_OUTBOX=false); bulk campaign/follow-up workers are disabled.',array('CSV background imports run automatically via WP-Cron.','Keep bulk campaign workers disabled until staging host/concurrency qualification completes.'),'/settings','Open Settings'),
            self::step('sms-gateway','sms',$phones>0?'SMS-Gate phone connected':'Connect an SMS-Gate phone',$phones>0?'done':'todo','Outbound SMS requires at least one enabled cloud SMS-Gate phone with encrypted credentials.',$phones>0?"{$phones} enabled SMS-Gate phone(s) configured.":"No enabled SMS-Gate phone is configured yet.",$phones>0?array():array('Open Settings → SMS Gateway in Sender Suite.','Add your cloud SMS-Gate username and password and run Test connection.'),'/settings','Open Settings → SMS Gateway',array(),'Settings → SMS Gateway → Test connection.'),
            self::step('sms-webhook','sms',$webhookReady?'SMS-Gate receipt signing key configured':'Configure SMS-Gate webhook signing key',$webhookReady?'done':'todo','Delivery status webhooks are rejected unless signed with your HMAC secret.',$webhookReady?'Encrypted webhook signing key is stored.':'Set your SMS-Gate webhook signing secret so delivery receipts can be verified.',array('Open Settings → SMS Gateway and save your Webhook Signing Key.','Configure the webhook URL in SMS-Gate for delivery status events.'),'/settings','Open Settings → SMS Gateway',array(array('label'=>'Status Webhook URL','value'=>$webhookEndpoint)),'Signed delivery receipts reconcile message status without plaintext secret exposure.'),
            self::step('email-migration','email','Native email sending transport','optional','Email templates, variables, contacts and suppression flags are stored natively; Brevo/Gmail outbound sending is not enabled in this WordPress build yet.','SMS workflows and stored email records are available; email campaign launch is disabled.',array(),'/templates','Open Templates'),
            self::step('reply-migration','replies','Inbound mailbox & SMS reply sync','optional','Stored inbox conversations, labels, previews and queued final-text SMS replies are available; IMAP/Gmail polling and inbound SMS webhook creation remain gated.','Use Inbox to review threads and queue explicit-sender SMS replies when the experimental outbox is enabled.',array(),'/inbox','Open Inbox'),
            self::step('ai-migration','ai','AI Agent & MCP connectors','optional','Built-in deterministic reply classification is active; external LLM agent sessions and MCP OAuth endpoints are not enabled in this WordPress build.','Rule-based reply intent/sentiment classification runs locally without external API keys.',array(),'/autoreply','Open Auto-Reply'),
            self::step('contacts-audience','optional','Import contacts & build a list',$contacts>0&&$lists>0?'done':'optional','Campaigns and recipient validation operate on your opted-in contacts and lists.',"{$contacts} contact(s) and {$lists} list(s) stored.",array('Import a CSV or create contacts on the Contacts page.','Group recipients on the Lists page.'),'/contacts','Open Contacts'),
            self::step('notifications','optional','Notification providers',$notifOn>0?'done':'optional','Encrypted Pushover/Telegram/ntfy credentials are stored natively; outbound alert dispatch remains gated.',$notifOn>0?"{$notifOn} notification provider(s) enabled in storage.":'Optional alert credentials can be stored in Settings → Notifications.',array(),'/settings','Open Settings')
        );
        $groupDefs=array(
            array('foundation','1. Foundation','WordPress credentials, HTTPS site address, database schema and worker gates.'),
            array('sms','2. SMS (Nigeria)','Cloud SMS-Gate sender phones and signed delivery-status webhooks.'),
            array('email','3. Email sending','Templates and suppression storage vs unmigrated outbound email transports.'),
            array('replies','4. Replies & Inbox','Conversation triage, classification and explicit-sender SMS reply queueing.'),
            array('ai','5. AI & Automation','Local deterministic reply classification and automation rule builders.'),
            array('optional','6. Audience & Alerts','Contacts, lists and encrypted notification provider configuration.')
        );
        $groups=array();
        foreach($groupDefs as $gd){
            list($gid,$glabel,$ghint)=$gd;$members=array();$gdone=0;$gatt=0;
            foreach($steps as $s){if($s['group']===$gid){$members[]=$s;if($s['status']==='done'){$gdone++;}if(in_array($s['status'],array('todo','attention'),true)){$gatt++;}}}
            $groups[]=array('id'=>$gid,'label'=>$glabel,'hint'=>$ghint,'steps'=>$members,'done'=>$gdone,'total'=>count($members),'needs_attention'=>$gatt);
        }
        $doneCount=0;$blocking=array();$optionalCount=0;
        foreach($steps as $s){if($s['status']==='done'){$doneCount++;}elseif($s['status']==='optional'){$optionalCount++;}else{$blocking[]=$s;}}
        $total=count($steps);$pct=$total>0?(int)round(100*($doneCount+$optionalCount)/$total):100;
        $readyToSend=$readySecrets&&$outboxOn&&$phones>0;
        $next=array();foreach(array_slice($blocking,0,5) as $b){$next[]=$b['id'];}
        return array('groups'=>$groups,'steps'=>$steps,'progress'=>array('done'=>$doneCount,'blocking'=>count($blocking),'optional'=>$optionalCount,'total'=>$total,'percent'=>$pct,'ready_to_send'=>$readyToSend),'next'=>$next,'environment'=>array('app_env'=>$this->config!==null?$this->config->get('APP_ENV'):'production','public_base_url'=>$siteUrl,'inline_poller'=>false,'google_oauth_ready'=>false,'google_oauth_source'=>'none','gmail_send_replies'=>false,'gmail_rescue_from_spam'=>false,'mcp_oauth_enabled'=>false));
    }
    public function summary(){
        $g=$this->guide();$first=null;
        foreach($g['steps'] as $s){if(in_array($s['status'],array('todo','attention'),true)){$first=$s;break;}}
        return array_merge($g['progress'],array('next_step'=>$first?$first['title']:null,'next_route'=>$first?$first['route']:null));
    }
    public static function handle($request,array $c,array $user){
        if($request->get_method()!=='GET'){throw new AuthFailure(404,'Not found');}
        $path=trim(substr($request->get_route(),strlen('/sender-suite/v1/')),'/');
        $s=new self($c['db'],$c['config']??null);
        if($path==='system/health'){return $s->health();}
        if($path==='system/errors'){return $s->errors($request->get_query_params(),$user);}
        if($path==='guide'){return $s->guide();}
        if($path==='guide/summary'){return $s->summary();}
        throw new AuthFailure(404,'Not found');
    }
}
