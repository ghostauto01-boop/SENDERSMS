<?php
namespace SenderSuite;

/** Encrypted CallGate, Google OAuth, gateway summary, push subscription storage, call logs, and signed CallGate webhooks. */
final class CallsSettings {
    const CALL_EVENTS=array('call:ringing','call:started','call:ended');
    private $db;private $config;private $records;
    public function __construct(Database $db,Config $config,callable $clock=null){$this->db=$db;$this->config=$config;$this->records=new Records($db,$clock);}
    private function table($n){return Schema::identifier($this->db->schema()->table($n));}
    private function settingScope($id,$key){return $this->db->schema()->table('system_settings').'/'.$id.'/'.$key;}
    public static function register(){
        register_rest_route('sender-suite/v1','/webhooks/callgate',array(
            array('methods'=>'GET','permission_callback'=>'__return_true','callback'=>array(SmsReceipts::class,'probe')),
            array('methods'=>'POST','permission_callback'=>'__return_true','callback'=>array(self::class,'dispatchCallgateWebhook'))
        ));
        register_rest_route('sender-suite/v1','/webhooks/brevo(?:/(?P<account_id>-?[0-9]+))?',array('methods'=>'POST','permission_callback'=>'__return_true','callback'=>array(EmailTransport::class,'dispatchBrevoWebhook')));
    }
    public static function brevoDisabled($request=null){return $request!==null?EmailTransport::dispatchBrevoWebhook($request):AuthRoutes::response(AuthRoutes::error(401,'Missing Brevo webhook token.',bin2hex(random_bytes(16))),401,bin2hex(random_bytes(16)));}
    public static function definitions(){
        return array(
            '/calls/config'=>array('GET','PUT'),
            '/calls/logs'=>array('GET'),
            '/calls/active'=>array('GET'),
            '/calls/start'=>array('POST'),
            '/calls/log-direct'=>array('POST'),
            '/calls/end'=>array('POST'),
            '/calls/test'=>array('POST'),
            '/calls/webhooks'=>array('GET'),
            '/calls/register-webhook'=>array('POST'),
            '/calls/webhooks/(?P<webhook_id>[A-Za-z0-9._-]+)'=>array('DELETE'),
            '/settings/callgate'=>array('GET','PUT'),
            '/settings/callgate/test'=>array('POST'),
            '/settings/callgate/webhooks'=>array('GET'),
            '/settings/callgate/register-webhook'=>array('POST'),
            '/settings/callgate/webhooks/(?P<webhook_id>[A-Za-z0-9._-]+)'=>array('DELETE'),
            '/settings/gateway'=>array('GET'),
            '/settings/gateway/sim'=>array('PUT'),
            '/settings/gateway/routing'=>array('PUT'),
            '/settings/gateway/test'=>array('POST'),
            '/settings/gateway/register-webhook'=>array('POST'),
            '/settings/gateway/webhooks'=>array('GET'),
            '/settings/gateway/webhooks/(?P<webhook_id>[A-Za-z0-9._-]+)'=>array('DELETE'),
            '/settings/gateway/phones/(?P<phone_id>env|-?[0-9]+)/register-webhook'=>array('POST'),
            '/settings/notifications/(?P<provider>[A-Za-z0-9_-]+)/test'=>array('POST'),
            '/settings/google-oauth'=>array('GET','PUT'),
            '/inbox/device-info'=>array('GET'),
            '/inbox/poll-now'=>array('POST'),
            '/inbox/poll-debug'=>array('POST'),
            '/inbox/sync-full'=>array('POST'),
            '/inbox/register-webhook'=>array('POST'),
            '/notifications/push/vapid-key'=>array('GET'),
            '/notifications/push/subscriptions'=>array('GET'),
            '/notifications/push/subscribe'=>array('POST'),
            '/notifications/push/unsubscribe'=>array('POST'),
            '/notifications/push/test'=>array('POST'),
            '/webhooks/logs'=>array('GET'),
            '/send/scheduled/(?P<sid>-?[0-9]+)/retry'=>array('POST'),
            '/send/messages/(?P<mid>-?[0-9]+)/retry'=>array('POST')
        );
    }
    private function getSetting($key,$default=null){$r=$this->db->one('system_settings',array('key'=>$key));return ($r&&$r['value']!==null)?(string)$r['value']:$default;}
    private function putSetting($key,$value,$category='general',$secret=false){
        $r=$this->db->one('system_settings',array('key'=>$key));
        if(!$r){$r=$this->records->create('system_settings',array('key'=>$key,'category'=>$category,'value'=>null));}
        $stored=$value===null||$value===''?null:($secret?$this->config->sealRuntimeSecret($this->settingScope($r['id'],$key),$value):(string)$value);
        $this->db->update('system_settings',array('value'=>$stored,'updated_at'=>$this->records->now()),array('id'=>$r['id']));
    }
    private function openSecretSetting($key){
        $r=$this->db->one('system_settings',array('key'=>$key));
        if(!$r||$r['value']===null||$r['value']===''){return '';}
        try{return (string)$this->config->openRuntimeSecret($this->settingScope($r['id'],$key),$r['value']);}
        catch(\Throwable $e){return '';}
    }
    private static function mask($val){
        if(!is_string($val)||$val===''){return '';}
        $len=strlen($val);if($len<=4){return str_repeat('*',$len);}
        return substr($val,0,2).str_repeat('*',min(8,$len-4)).substr($val,-2);
    }
    private static function displayContact($c,$phone){
        if(!$c){return $phone;}
        $nm=Compat::strip(Compat::strip($c['first_name']??'').' '.Compat::strip($c['last_name']??''));
        return $nm!==''?$nm:(Compat::strip($c['business_name']??'')?:$phone);
    }
    public function callgateConfig(){
        $base=$this->getSetting('callgate.base_url','');
        $user=$this->getSetting('callgate.username','');
        $pw=$this->openSecretSetting('callgate.password');
        $sec=$this->openSecretSetting('callgate.webhook_secret');
        $mode=$this->getSetting('callgate.dial_mode','direct');
        if(!in_array($mode,array('callgate','direct'),true)){$mode='direct';}
        $configured=$base!==''&&$user!==''&&$pw!=='';
        return array(
            'base_url'=>$base,
            'username'=>$user,
            'password_set'=>$pw!=='',
            'password_hint'=>$pw!==''?self::mask($pw):'',
            'webhook_secret_set'=>$sec!=='',
            'dial_mode'=>$mode,
            'configured'=>$configured,
            'webhook_registered'=>$this->getSetting('callgate.webhook_registered',null)
        );
    }
    public function saveCallgateConfig($input,array $user){
        if(($user['role']??'')!=='admin'){throw new AuthFailure(403,'Application administrator role required.');}
        $this->db->requireTransaction();
        $data=is_object($input)?get_object_vars($input):(is_array($input)?$input:array());
        $allowed=array('base_url','username','password','webhook_secret','dial_mode');
        foreach($data as $k=>$v){if(!in_array($k,$allowed,true)){throw new ValidationFailure('Unknown CallGate field',$k);}}
        if(array_key_exists('base_url',$data)&&$data['base_url']!==null){
            $b=rtrim(Compat::strip(DataRoutes::text($data,'base_url','',null,500)),'/');
            if($b!==''&&stripos($b,'http://')!==0&&stripos($b,'https://')!==0&&strpos($b,'.')===false&&strpos($b,':')===false){
                throw new AuthFailure(400,'Base URL must be a phone address like 192.168.1.5:8084 or http://…');
            }
            $this->putSetting('callgate.base_url',$b,'callgate',false);
            $this->putSetting('callgate.webhook_registered',null,'callgate',false);
        }
        if(array_key_exists('username',$data)&&$data['username']!==null){
            $this->putSetting('callgate.username',Compat::strip(DataRoutes::text($data,'username','',null,255)),'callgate',false);
        }
        if(array_key_exists('password',$data)&&$data['password']!==null){
            $pw=DataRoutes::text($data,'password','',null,256);
            if($pw!==''){$this->putSetting('callgate.password',$pw,'callgate',true);}
        }
        if(array_key_exists('webhook_secret',$data)&&$data['webhook_secret']!==null){
            $ws=DataRoutes::text($data,'webhook_secret','',null,512);
            if($ws!==''&&(strlen($ws)<16||preg_match('/[\p{C}\s]/u',$ws))){throw new ValidationFailure('webhook_secret must be at least 16 non-whitespace characters','webhook_secret');}
            $this->putSetting('callgate.webhook_secret',$ws,'callgate',true);
        }
        if(array_key_exists('dial_mode',$data)&&$data['dial_mode']!==null){
            $dm=DataRoutes::text($data,'dial_mode','',null,20);
            if(!in_array($dm,array('callgate','direct'),true)){throw new AuthFailure(400,"dial_mode must be 'callgate' or 'direct'");}
            $this->putSetting('callgate.dial_mode',$dm,'callgate',false);
        }
        $cfg=$this->callgateConfig();
        return array('success'=>true,'configured'=>$cfg['configured'],'dial_mode'=>$cfg['dial_mode']);
    }
    public function startCall($body){
        if(!is_object($body)){throw new ValidationFailure('Supply a JSON object');}
        foreach($body as $k=>$v){if(!in_array($k,array('contact_id','phone_number','allow_direct_fallback'),true)){throw new ValidationFailure('Unknown call field',$k);}}
        $this->db->requireTransaction();
        $contact=null;$phone=isset($body->phone_number)&&$body->phone_number!==null?Compat::strip(DataRoutes::text(array('phone_number'=>$body->phone_number),'phone_number','',null,64)):'';
        if(isset($body->contact_id)&&$body->contact_id!==null){
            $cid=DataRoutes::integer($body->contact_id,'contact_id',1);
            $contact=$this->db->one('contacts',array('id'=>$cid));
            if(!$contact){throw new AuthFailure(404,'Contact not found');}
            $phone=Compat::strip($contact['phone_number']??'');
        }
        if($phone===''){throw new AuthFailure(400,'Provide contact_id or phone_number');}
        $cfg=$this->callgateConfig();
        $norm=Phone::clean($phone)?:$phone;
        if($cfg['dial_mode']==='callgate'&&$cfg['configured']){
            $base=rtrim($this->getSetting('callgate.base_url','https://api.sms-gate.app/3rdparty/v1'),'/');
            $u=$this->getSetting('callgate.username','');$p=$this->openSecretSetting('callgate.password');
            if($u!==''&&$p!==''){
                $res=ProviderHttp::request('POST',$base.'/calls',array('Authorization'=>'Basic '.base64_encode($u.':'.$p)),array('phoneNumber'=>$norm,'simNumber'=>(int)$cfg['sim_number']));
                $row=$this->records->create('call_logs',array('contact_id'=>$contact?$contact['id']:null,'phone_number'=>$norm,'direction'=>'outgoing','status'=>!empty($res['ok'])?'ringing':'failed','started_at'=>$this->records->now()));
                return array('success'=>!empty($res['ok']),'mode'=>'callgate','call_id'=>(int)$row['id'],'phone'=>$norm,'contact'=>self::displayContact($contact,$norm));
            }
        }
        $row=$this->records->create('call_logs',array('contact_id'=>$contact?$contact['id']:null,'phone_number'=>$norm,'direction'=>'outgoing','status'=>'direct_dial','started_at'=>$this->records->now()));
        return array('success'=>true,'mode'=>'direct','tel_url'=>'tel:'.$norm,'call_id'=>(int)$row['id'],'phone'=>$norm,'contact'=>self::displayContact($contact,$norm),'note'=>!$cfg['configured']?'CallGate not configured — dialling directly from this device.':'Direct-dial mode — calling from this device.');
    }
    public function logDirect(array $q,$body){
        $this->db->requireTransaction();
        $cid=isset($q['contact_id'])&&$q['contact_id']!==''?DataRoutes::integer($q['contact_id'],'contact_id',1):(is_object($body)&&isset($body->contact_id)?DataRoutes::integer($body->contact_id,'contact_id',1):null);
        $phone=isset($q['phone_number'])?Compat::strip(DataRoutes::text($q,'phone_number','',null,64)):(is_object($body)&&isset($body->phone_number)?Compat::strip(DataRoutes::text(array('phone_number'=>$body->phone_number),'phone_number','',null,64)):'');
        $contact=null;
        if($cid!==null){$contact=$this->db->one('contacts',array('id'=>$cid));if(!$contact){throw new AuthFailure(404,'Contact not found');}$phone=Compat::strip($contact['phone_number']??'');}
        if($phone===''){throw new AuthFailure(400,'Provide contact_id or phone_number');}
        $norm=Phone::clean($phone)?:$phone;
        $row=$this->records->create('call_logs',array('contact_id'=>$contact?$contact['id']:null,'phone_number'=>$norm,'direction'=>'outgoing','status'=>'direct_dial','started_at'=>$this->records->now()));
        return array('success'=>true,'call_id'=>(int)$row['id']);
    }
    public function callLogs(array $q){
        $page=DataRoutes::integer($q['page']??1,'page',1,1000000);$per=DataRoutes::integer($q['per_page']??25,'per_page',1,100);
        $search=DataRoutes::text($q,'search',null,null,255);
        $where='1=1';$args=array();
        if($search!==null&&Compat::strip($search)!==''){
            $term='%'.Compat::strip($search).'%';
            $where='(LOWER(l.phone_number) LIKE LOWER(%s) OR LOWER(COALESCE(c.first_name,\'\')) LIKE LOWER(%s) OR LOWER(COALESCE(c.last_name,\'\')) LIKE LOWER(%s) OR LOWER(COALESCE(c.business_name,\'\')) LIKE LOWER(%s))';
            $args=array($term,$term,$term,$term);
        }
        $base=' FROM '.$this->table('call_logs').' l LEFT JOIN '.$this->table('contacts').' c ON c.id=l.contact_id WHERE '.$where;
        $total=(int)$this->db->rows('SELECT COUNT(*) AS n'.$base,$args)[0]['n'];
        $rows=$this->db->rows('SELECT l.id,l.contact_id,l.phone_number,l.direction,l.status,l.started_at,l.ended_at,l.duration_seconds,CASE WHEN l.last_error IS NULL OR l.last_error=\'\' THEN NULL ELSE \'Call reported an error.\' END AS last_error,l.created_at,c.first_name,c.last_name,c.business_name'.$base.' ORDER BY l.id DESC LIMIT %d OFFSET %d',array_merge($args,array($per,($page-1)*$per)));
        $items=array();
        foreach($rows as $r){
            $c=$r['contact_id']!==null?array('first_name'=>$r['first_name'],'last_name'=>$r['last_name'],'business_name'=>$r['business_name']):null;
            $items[]=array('id'=>(int)$r['id'],'contact_id'=>$r['contact_id']!==null?(int)$r['contact_id']:null,'contact_name'=>self::displayContact($c,$r['phone_number']),'phone_number'=>$r['phone_number'],'direction'=>$r['direction'],'status'=>$r['status'],'started_at'=>Records::iso($r['started_at']),'ended_at'=>Records::iso($r['ended_at']),'duration_seconds'=>$r['duration_seconds']!==null?(int)$r['duration_seconds']:null,'last_error'=>$r['last_error'],'created_at'=>Records::iso($r['created_at']));
        }
        return array('total'=>$total,'page'=>$page,'per_page'=>$per,'items'=>$items);
    }
    public function activeCall(){
        $rows=$this->db->rows("SELECT l.id,l.contact_id,l.phone_number,l.status,c.first_name,c.last_name,c.business_name FROM ".$this->table('call_logs')." l LEFT JOIN ".$this->table('contacts')." c ON c.id=l.contact_id WHERE l.status IN ('initiated','ringing','started') ORDER BY l.id DESC LIMIT 1");
        if(!$rows){return array('active'=>false);}
        $r=$rows[0];$c=$r['contact_id']!==null?array('first_name'=>$r['first_name'],'last_name'=>$r['last_name'],'business_name'=>$r['business_name']):null;
        return array('active'=>true,'id'=>(int)$r['id'],'phone_number'=>$r['phone_number'],'contact_id'=>$r['contact_id']!==null?(int)$r['contact_id']:null,'status'=>$r['status'],'contact_name'=>self::displayContact($c,$r['phone_number']));
    }
    public function gatewaySummary(){
        $phones=(new GatewayPhones($this->db,$this->config))->phones();
        $active=false;foreach($phones as $p){if(!empty($p['is_enabled'])&&!empty($p['configured'])){$active=true;break;}}
        $sim=(int)$this->getSetting('gateway.sim_number','1');if($sim<1||$sim>2){$sim=1;}
        return array('configured'=>$active,'render_sender_active'=>false,'is_enabled'=>true,'username'=>'','password'=>'','base_url'=>'','sim_number'=>$sim,'connection_status'=>'unknown','last_error'=>null,'phones'=>$phones,'routing_mode'=>'default_phone','sim_failover'=>false);
    }
    public function setGatewaySim(array $q,$body,array $user){
        if(($user['role']??'')!=='admin'){throw new AuthFailure(403,'Application administrator role required.');}
        $this->db->requireTransaction();
        $raw=$q['sim']??(is_object($body)&&isset($body->sim)?$body->sim:1);
        $sim=DataRoutes::integer($raw,'sim',1,2);
        $this->putSetting('gateway.sim_number',(string)$sim,'gateway',false);
        return array('success'=>true,'sim_number'=>$sim);
    }
    public function setGatewayRouting($body,array $user){
        if(($user['role']??'')!=='admin'){throw new AuthFailure(403,'Application administrator role required.');}
        if(!is_object($body)){throw new ValidationFailure('Supply a JSON object');}
        foreach($body as $k=>$v){if(!in_array($k,array('routing_mode','sim_failover'),true)){throw new ValidationFailure('Unknown routing field',$k);}}
        $mode=isset($body->routing_mode)&&$body->routing_mode!==null?DataRoutes::text(array('routing_mode'=>$body->routing_mode),'routing_mode','default_phone',null,40):'default_phone';
        if(!in_array($mode,array('default_phone','round_robin','least_used','sticky_contact'),true)){throw new ValidationFailure('Unsupported routing_mode','routing_mode');}
        $failover=isset($body->sim_failover)?DataRoutes::boolean($body->sim_failover,'sim_failover'):false;
        $this->db->transaction(function()use($mode,$failover){
            $this->putSetting('gateway.routing_mode',$mode,'gateway');
            $this->putSetting('gateway.sim_failover',$failover?'1':'0','gateway');
        });
        return array('success'=>true,'routing_mode'=>$mode,'sim_failover'=>$failover);
    }
    public function googleOauth(){
        $cid=$this->getSetting('google_oauth.client_id','');
        $sec=$this->openSecretSetting('google_oauth.client_secret');
        $hasSec=$sec!=='';$configured=$cid!==''&&$hasSec;
        $base=function_exists('home_url')?rtrim((string)home_url('/'),'/'):null;
        $cb='/wp-json/sender-suite/v1/mailbox/google/callback';
        $mbs=array();
        foreach($this->db->rows("SELECT id,name,provider,email_address,is_active,send_replies,rescue_from_spam,last_sync_at,last_sync_status,total_synced,total_replies,created_at FROM ".$this->table('email_mailboxes')." WHERE provider='gmail_api' ORDER BY id ASC LIMIT 100") as $r){
            $m=$this->db->decode('email_mailboxes',$r);
            $mbs[]=array('id'=>(int)$m['id'],'name'=>$m['name'],'provider'=>$m['provider'],'email_address'=>$m['email_address'],'is_active'=>(bool)$m['is_active'],'send_replies'=>(bool)$m['send_replies'],'rescue_from_spam'=>(bool)$m['rescue_from_spam'],'last_sync_at'=>Records::iso($m['last_sync_at']),'last_sync_status'=>$m['last_sync_status'],'total_synced'=>(int)$m['total_synced'],'total_replies'=>(int)$m['total_replies'],'created_at'=>Records::iso($m['created_at']));
        }
        return array('client_id'=>$cid,'client_id_masked'=>self::mask($cid),'has_secret'=>$hasSec,'configured'=>$configured,'source'=>$configured?'db':'none','redirect_uri'=>$base?($base.$cb):null,'callback_path'=>$cb,'javascript_origin'=>$base,'scope'=>'https://www.googleapis.com/auth/gmail.modify https://www.googleapis.com/auth/gmail.send','public_base_url'=>$base,'public_base_source'=>$base?'wordpress':'none','gmail_mailboxes'=>$mbs,'mailbox_count'=>count($mbs));
    }
    public function saveGoogleOauth($body,array $user){
        if(($user['role']??'')!=='admin'){throw new AuthFailure(403,'Application administrator role required.');}
        if(!is_object($body)){throw new ValidationFailure('Supply a JSON object');}
        foreach($body as $k=>$v){if(!in_array($k,array('client_id','client_secret'),true)){throw new ValidationFailure('Unknown Google OAuth field',$k);}}
        $this->db->requireTransaction();
        $cid=property_exists($body,'client_id')&&$body->client_id!==null?Compat::strip(DataRoutes::text(array('client_id'=>$body->client_id),'client_id','',null,500)):null;
        $sec=property_exists($body,'client_secret')&&$body->client_secret!==null?Compat::strip(DataRoutes::text(array('client_secret'=>$body->client_secret),'client_secret','',null,500)):null;
        $warnings=array();
        if($cid!==null&&$cid!==''){
            if(strpos($cid,' ')!==false){throw new AuthFailure(422,'The client ID cannot contain spaces — copy it again from Google Cloud Console.');}
            if(substr($cid,-27)!=='.apps.googleusercontent.com'){$warnings[]='That does not end in .apps.googleusercontent.com — Google client IDs normally do. Check nothing was cut off when you pasted it.';}
        }
        if($sec!==null&&$sec!==''&&strpos($sec,' ')!==false){throw new AuthFailure(422,'The client secret cannot contain spaces.');}
        $curHas=$this->openSecretSetting('google_oauth.client_secret')!=='';
        if($cid!==null&&$cid!==''&&($sec===null||$sec==='')&&!$curHas){
            throw new AuthFailure(422,'Save the client secret too — Google needs both to exchange the authorization code. It is on the same console page as the client ID.');
        }
        if($cid!==null){$this->putSetting('google_oauth.client_id',$cid,'google_oauth',false);}
        if($sec!==null){$this->putSetting('google_oauth.client_secret',$sec,'google_oauth',true);}
        $st=$this->googleOauth();
        return array('success'=>true,'warnings'=>$warnings,'client_id_masked'=>$st['client_id_masked'],'has_secret'=>$st['has_secret'],'configured'=>$st['configured'],'source'=>$st['source']);
    }
    public function deviceInfo(){
        $phones=(new GatewayPhones($this->db,$this->config))->phones();
        $devices=array();
        foreach($phones as $p){if(!empty($p['is_enabled'])){$devices[]=array('id'=>$p['device_id']!==''?self::mask($p['device_id']):(string)$p['id'],'name'=>$p['name'],'phone_number'=>$p['phone_number'],'status'=>$p['connection_status']);}}
        $target=function_exists('rest_url')?(string)rest_url('sender-suite/v1/webhooks/smsgateway'):null;
        return array('success'=>true,'devices'=>$devices,'device_count'=>count($devices),'cached_device_id'=>$devices?($devices[0]['id']):'','webhook_url'=>$target,'registered_webhooks'=>array(),'webhook_ok'=>(new SmsReceipts($this->db,$this->config))->metadata()['configured']);
    }
    public function vapidKey(){
        $pub=$this->getSetting('push.vapid_public_key','');
        return array('public_key'=>$pub,'configured'=>$pub!=='');
    }
    public function pushSubscriptions(){
        $rows=$this->db->rows('SELECT id,endpoint,user_agent,created_at,last_used_at FROM '.$this->table('push_subscriptions').' ORDER BY id DESC LIMIT 200');
        $items=array();
        foreach($rows as $r){
            $ep=(string)($r['endpoint']??'');$parts=strpos($ep,'://')!==false?explode('/',$ep):array();
            $host=substr((string)($parts[2]??'?'),0,80);
            $items[]=array('id'=>(int)$r['id'],'endpoint_host'=>$host,'user_agent'=>substr((string)($r['user_agent']??''),0,120),'created_at'=>Records::iso($r['created_at']),'last_used_at'=>Records::iso($r['last_used_at']));
        }
        return array('total'=>count($items),'items'=>$items);
    }
    public function pushSubscribe($body){
        if(!is_object($body)||!isset($body->keys)||!is_object($body->keys)){throw new ValidationFailure('Supply endpoint and keys object');}
        $this->db->requireTransaction();
        $ep=Compat::strip(DataRoutes::text(array('endpoint'=>$body->endpoint??''),'endpoint','',null,2000));
        $p256=Compat::strip(DataRoutes::text(array('p256dh'=>$body->keys->p256dh??''),'p256dh','',null,500));
        $auth=Compat::strip(DataRoutes::text(array('auth'=>$body->keys->auth??''),'auth','',null,200));
        $ua=isset($body->user_agent)&&$body->user_agent!==null?substr(DataRoutes::text(array('user_agent'=>$body->user_agent),'user_agent','',null,500),0,500):'';
        if(stripos($ep,'https://')!==0||$p256===''||$auth===''){throw new AuthFailure(400,'Invalid push subscription');}
        $existing=$this->db->one('push_subscriptions',array('endpoint'=>$ep));
        if(!$existing){$this->records->create('push_subscriptions',array('endpoint'=>$ep,'p256dh'=>$p256,'auth'=>$auth,'user_agent'=>$ua));}
        else{$patch=array('p256dh'=>$p256,'auth'=>$auth,'fail_count'=>0);if($ua!==''){$patch['user_agent']=$ua;}$this->db->update('push_subscriptions',$patch,array('id'=>$existing['id']));}
        $total=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('push_subscriptions'))[0]['n'];
        return array('success'=>true,'devices'=>$total);
    }
    public function pushUnsubscribe($body){
        if(!is_object($body)){throw new ValidationFailure('Supply a JSON object');}
        $this->db->requireTransaction();
        $ep=Compat::strip(DataRoutes::text(array('endpoint'=>$body->endpoint??''),'endpoint','',null,2000));
        if($ep!==''){$this->db->delete('push_subscriptions',array('endpoint'=>$ep));}
        return array('success'=>true);
    }
    public function webhookLogs(array $q,array $user){
        if(($user['role']??'')!=='admin'){throw new AuthFailure(403,'Application administrator role required.');}
        $page=DataRoutes::integer($q['page']??1,'page',1,1000000);$per=DataRoutes::integer($q['per_page']??25,'per_page',1,200);
        $etype=DataRoutes::text($q,'event_type',null,null,100);
        $where='1=1';$args=array();
        if($etype!==null&&$etype!==''){$where='event_type=%s';$args=array($etype);}
        $total=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('webhook_events').' WHERE '.$where,$args)[0]['n'];
        $rows=$this->db->rows('SELECT id,event_type,provider,provider_event_id,status,CASE WHEN error IS NULL OR error=\'\' THEN NULL ELSE \'Webhook processing reported an error.\' END AS error,created_at FROM '.$this->table('webhook_events').' WHERE '.$where.' ORDER BY created_at DESC,id DESC LIMIT %d OFFSET %d',array_merge($args,array($per,($page-1)*$per)));
        $items=array();
        foreach($rows as $r){$items[]=array('id'=>(int)$r['id'],'event_type'=>$r['event_type'],'provider'=>$r['provider'],'provider_event_id'=>$r['provider_event_id'],'status'=>$r['status'],'error'=>$r['error'],'created_at'=>Records::iso($r['created_at']));}
        return array('total'=>$total,'items'=>$items);
    }
    public function receiveCallgateWebhook($raw,$signature,$timestamp){
        if(!is_string($raw)||strlen($raw)>65536){throw new AuthFailure(413,'Callback is too large.');}
        $secret=$this->openSecretSetting('callgate.webhook_secret');
        if($secret===''||strlen($secret)<16){throw new AuthFailure(503,'CallGate webhook signing is not configured.');}
        $nowStr=$this->records->now();$nowTs=(int)(new \DateTimeImmutable($nowStr,new \DateTimeZone('UTC')))->format('U');
        if(!SmsReceipts::verify($raw,$signature,$timestamp,$secret,$nowTs)){throw new AuthFailure(401,'Invalid webhook signature');}
        try{$body=json_decode($raw,false,32,JSON_THROW_ON_ERROR);}catch(\Throwable $e){throw new ValidationFailure('Invalid callback JSON');}
        if(!is_object($body)){throw new ValidationFailure('Callback must be a JSON object');}
        $event=is_string($body->event??null)?$body->event:'';
        $payload=isset($body->payload)&&is_object($body->payload)?$body->payload:$body;
        $eventId=is_string($body->id??null)?substr($body->id,0,255):'';
        $phone=Compat::strip((string)($payload->phoneNumber??($payload->phone??'')));
        $idem='native-callgate:'.hash('sha256',$eventId!==''?'id:'.$eventId:'body:'.$raw);
        $digest=hash('sha256',$raw);
        $old=$this->db->one('webhook_events',array('idempotency_key'=>$idem));
        if($old){
            $saved=json_decode($old['payload']??'{}',true);
            if(($saved['body_sha256']??null)!==$digest){throw new AuthFailure(409,'Call event ID was reused with different content.');}
            return array('ok'=>true,'duplicate'=>true);
        }
        $evt=$this->records->create('webhook_events',array('event_type'=>$event!==''?$event:'unknown','provider'=>'callgate','provider_event_id'=>$eventId!==''?$eventId:null,'idempotency_key'=>$idem,'payload'=>json_encode(array('body_sha256'=>$digest),JSON_THROW_ON_ERROR),'status'=>'received'));
        if(in_array($event,self::CALL_EVENTS,true)&&$phone!==''){
            $norm=Phone::clean($phone)?:$phone;
            $contact=$this->db->one('contacts',array('phone_number'=>$norm));
            if(!$contact){
                $digits=substr(preg_replace('/\D+/','',$norm),-9);
                if(strlen($digits)===9){$m=$this->db->rows('SELECT * FROM '.$this->table('contacts').' WHERE phone_number LIKE %s ORDER BY id ASC LIMIT 1',array('%'.$digits));if($m){$contact=$this->db->decode('contacts',$m[0]);}}
            }
            $devId=is_string($body->deviceId??null)?substr($body->deviceId,0,120):null;
            if($event==='call:ringing'){
                $open=$this->db->rows("SELECT * FROM ".$this->table('call_logs')." WHERE direction='outgoing' AND status IN ('initiated','ringing','started') ORDER BY id DESC LIMIT 1");
                if($open){$r=$this->db->decode('call_logs',$open[0]);$this->db->update('call_logs',array('status'=>'ringing','provider_event_id'=>$eventId?:$r['provider_event_id'],'device_id'=>$devId?:$r['device_id']),array('id'=>$r['id']));}
                else{$this->records->create('call_logs',array('contact_id'=>$contact?$contact['id']:null,'phone_number'=>$norm,'direction'=>'incoming','status'=>'ringing','provider_event_id'=>$eventId?:null,'device_id'=>$devId));}
            }elseif($event==='call:started'){
                $open=$this->db->rows("SELECT * FROM ".$this->table('call_logs')." WHERE status IN ('initiated','ringing') ORDER BY id DESC LIMIT 1");
                if($open){$r=$this->db->decode('call_logs',$open[0]);$this->db->update('call_logs',array('status'=>'started','started_at'=>$nowStr,'provider_event_id'=>$eventId?:$r['provider_event_id'],'contact_id'=>$r['contact_id']?:($contact?$contact['id']:null)),array('id'=>$r['id']));}
                else{$this->records->create('call_logs',array('contact_id'=>$contact?$contact['id']:null,'phone_number'=>$norm,'direction'=>'incoming','status'=>'started','provider_event_id'=>$eventId?:null,'device_id'=>$devId,'started_at'=>$nowStr));}
            }elseif($event==='call:ended'){
                $open=$this->db->rows("SELECT * FROM ".$this->table('call_logs')." WHERE status IN ('initiated','ringing','started') ORDER BY id DESC LIMIT 1");
                if($open){
                    $r=$this->db->decode('call_logs',$open[0]);$dur=null;
                    if($r['started_at']!==null){$sTs=(int)(new \DateTimeImmutable($r['started_at'],new \DateTimeZone('UTC')))->format('U');$dur=max(0,$nowTs-$sTs);}
                    $this->db->update('call_logs',array('status'=>'ended','ended_at'=>$nowStr,'duration_seconds'=>$dur,'contact_id'=>$r['contact_id']?:($contact?$contact['id']:null)),array('id'=>$r['id']));
                }else{
                    $this->records->create('call_logs',array('contact_id'=>$contact?$contact['id']:null,'phone_number'=>$norm,'direction'=>'incoming','status'=>'ended','provider_event_id'=>$eventId?:null,'device_id'=>$devId,'ended_at'=>$nowStr));
                }
            }
        }
        $this->db->update('webhook_events',array('status'=>'processed','processed_at'=>$nowStr),array('id'=>$evt['id']));
        return array('ok'=>true);
    }
    public static function dispatchCallgateWebhook($request){
        $id=bin2hex(random_bytes(16));
        try{
            $c=AuthRoutes::context();if(!$c['config']->ready()){throw new AuthFailure(503,'Application is not configured.');}
            $s=new self($c['db'],$c['config']);
            $out=$c['db']->transaction(function()use($s,$request){return $s->receiveCallgateWebhook($request->get_body(),$request->get_header('x-signature'),$request->get_header('x-timestamp'));});
            return AuthRoutes::response($out,200,$id);
        }catch(AuthFailure $e){return AuthRoutes::response(AuthRoutes::error($e->status,$e->getMessage(),$id),$e->status,$id);}
        catch(ValidationFailure $e){return AuthRoutes::response(AuthRoutes::error(422,$e->getMessage(),$id),422,$id);}
        catch(\Throwable $e){return AuthRoutes::response(AuthRoutes::error(503,'CallGate webhook processing failed.',$id),503,$id);}
    }
    public static function matches($path){
        return strpos($path,'calls/')===0||$path==='webhooks/logs'||$path==='settings/google-oauth'||$path==='settings/callgate'||strpos($path,'settings/callgate/')===0||$path==='settings/gateway'||in_array($path,array('settings/gateway/sim','settings/gateway/routing','settings/gateway/test','settings/gateway/register-webhook','settings/gateway/webhooks','inbox/device-info','inbox/poll-now','inbox/poll-debug','inbox/sync-full','inbox/register-webhook','notifications/push/vapid-key','notifications/push/subscriptions','notifications/push/subscribe','notifications/push/unsubscribe','notifications/push/test'),true)||preg_match('~^(?:settings/gateway/webhooks/[A-Za-z0-9._-]+|settings/gateway/phones/(?:env|-?[0-9]+)/register-webhook|settings/notifications/[A-Za-z0-9_-]+/test|send/(?:scheduled|messages)/-?[0-9]+/retry)$~D',$path)===1;
    }
    public static function handle($request,array $c,array $user){
        $path=trim(substr($request->get_route(),strlen('/sender-suite/v1/')),'/');$method=$request->get_method();
        if(strlen($request->get_body())>65536){throw new AuthFailure(413,'Request body exceeds the size limit.');}
        $s=new self($c['db'],$c['config']);$q=$request->get_query_params();
        if(($path==='calls/config'||$path==='settings/callgate')&&$method==='GET'){return $s->callgateConfig();}
        if(($path==='calls/config'||$path==='settings/callgate')&&$method==='PUT'){
            $raw=$request->get_body();$in=$raw!==''?DataRoutes::body($request):(object)$q;
            return $c['db']->transaction(function()use($s,$in,$user){return $s->saveCallgateConfig($in,$user);});
        }
        if($path==='calls/logs'&&$method==='GET'){return $s->callLogs($q);}
        if($path==='calls/active'&&$method==='GET'){return $s->activeCall();}
        if($path==='calls/start'&&$method==='POST'){if(($user['role']??'')!=='admin'){throw new AuthFailure(403,'Application administrator role required.');}$raw=$request->get_body();$b=$raw!==''?DataRoutes::body($request):(object)array();return $c['db']->transaction(function()use($s,$b){return $s->startCall($b);});}
        if($path==='calls/log-direct'&&$method==='POST'){if(($user['role']??'')!=='admin'){throw new AuthFailure(403,'Application administrator role required.');}$raw=$request->get_body();$b=$raw!==''?DataRoutes::body($request):null;return $c['db']->transaction(function()use($s,$q,$b){return $s->logDirect($q,$b);});}
        if($path==='settings/gateway'&&$method==='GET'){return $s->gatewaySummary();}
        if($path==='settings/gateway/sim'&&$method==='PUT'){$raw=$request->get_body();$b=$raw!==''?DataRoutes::body($request):null;return $c['db']->transaction(function()use($s,$q,$b,$user){return $s->setGatewaySim($q,$b,$user);});}
        if($path==='settings/gateway/routing'&&$method==='PUT'){return $s->setGatewayRouting(DataRoutes::body($request),$user);}
        if($path==='settings/google-oauth'&&$method==='GET'){if(($user['role']??'')!=='admin'){throw new AuthFailure(403,'Application administrator role required.');}return $s->googleOauth();}
        if($path==='settings/google-oauth'&&$method==='PUT'){return $c['db']->transaction(function()use($s,$request,$user){return $s->saveGoogleOauth(DataRoutes::body($request),$user);});}
        if($path==='inbox/device-info'&&$method==='GET'){return $s->deviceInfo();}
        if($path==='notifications/push/vapid-key'&&$method==='GET'){return $s->vapidKey();}
        if($path==='notifications/push/subscriptions'&&$method==='GET'){return $s->pushSubscriptions();}
        if($path==='notifications/push/subscribe'&&$method==='POST'){return $c['db']->transaction(function()use($s,$request){return $s->pushSubscribe(DataRoutes::body($request));});}
        if($path==='notifications/push/unsubscribe'&&$method==='POST'){return $c['db']->transaction(function()use($s,$request){return $s->pushUnsubscribe(DataRoutes::body($request));});}
        if($path==='webhooks/logs'&&$method==='GET'){return $s->webhookLogs($q,$user);}
        if(preg_match('~^(?:calls/(?:end|test|webhooks(?:/[A-Za-z0-9._-]+)?|register-webhook)|settings/callgate/(?:test|webhooks(?:/[A-Za-z0-9._-]+)?|register-webhook)|settings/gateway/(?:test|register-webhook|webhooks(?:/[A-Za-z0-9._-]+)?)|settings/gateway/phones/(?:env|-?[0-9]+)/register-webhook|settings/notifications/[A-Za-z0-9_-]+/test|inbox/(?:poll-now|poll-debug|sync-full|register-webhook)|notifications/push/test|send/(?:scheduled|messages)/-?[0-9]+/retry)$~D',$path)){
            if(($user['role']??'')!=='admin'&&$method!=='GET'){throw new AuthFailure(403,'Application administrator role required.');}
            return (new CampaignWorker($c['db'],$c['config']))->handleCallsAndGateway($path,$method,$request,$user);
        }
        throw new AuthFailure(404,'Not found');
    }
}
