<?php
namespace SenderSuite;
/** Status-only SMS-Gate callback. Never sends, ingests message content, or registers a provider. */
final class SmsReceipts {
    const EVENTS=array('sms:sent'=>'sent','sms:delivered'=>'delivered','sms:failed'=>'failed','sms:cancelled'=>'cancelled');
    const KEY='gateway.webhook_signing_secret';
    private $db;
    private $config;
    private $records;
    public function __construct(Database $db,Config $config,callable $clock=null){
        $this->db=$db;
        $this->config=$config;
        $this->records=new Records($db,$clock);
    }
    private function table($n){
        return Schema::identifier($this->db->schema()->table($n));
    }
    public static function definitions(){
        return array('/settings/gateway/webhook-signing'=>array('GET','PUT'));
    }
    public static function register(){
        register_rest_route('sender-suite/v1','/webhooks/smsgateway',array(array('methods'=>'GET','permission_callback'=>'__return_true','callback'=>array(self::class,'probe')),array('methods'=>'POST','permission_callback'=>'__return_true','callback'=>array(self::class,'dispatch'))));
        register_rest_route('sender-suite/v1','/webhooks/smsgateway/phone/(?P<phone_id>-?[0-9]+)',array('methods'=>'POST','permission_callback'=>'__return_true','callback'=>array(self::class,'dispatchPhone')));
        register_rest_route('sender-suite/v1','/webhooks/smsgate/inbound',array('methods'=>'POST','permission_callback'=>'__return_true','callback'=>array(CampaignWorker::class,'dispatchInboundSms')));
    }
    public static function probe(){return AuthRoutes::response(array('ok'=>true),200,bin2hex(random_bytes(16)));}
    private function scope($id){
        return $this->db->schema()->table('system_settings').'/'.$id.'/'.self::KEY;
    }
    public function metadata(){
        $r=$this->db->one('system_settings',array('key'=>self::KEY));
        return array('configured'=>$r!==null&&$r['value']!==null&&$r['value']!=='','status_events_only'=>true);
    }
    public function saveSecret($body){
        if(!is_object($body)||!property_exists($body,'secret')||count(get_object_vars($body))!==1||!is_string($body->secret)){
            throw new ValidationFailure('Supply only a secret string in JSON','secret');
        }
        $v=$body->secret;
        if($v!==''&&(strlen($v)<32||strlen($v)>512||preg_match('/[\p{Z}\p{C}\s]/u',$v)||preg_match('//u',$v)!==1)){
            throw new ValidationFailure('Signing secret must be 32–512 bytes without whitespace/control characters','secret');
        }
        $r=$this->db->one('system_settings',array('key'=>self::KEY));
        if(!$r){
            $r=$this->records->create('system_settings',array('key'=>self::KEY,'category'=>'gateway'));
        }
        $sealed=$v===''?null:$this->config->sealRuntimeSecret($this->scope($r['id']),$v);
        if($r['value']!==$sealed&&$this->db->update('system_settings',array('value'=>$sealed,'updated_at'=>$this->records->now()),array('id'=>$r['id'],'value'=>$r['value']))!==1){
            throw new AuthFailure(409,'Signing configuration changed. Retry.');
        }
        return array('success'=>true,'configured'=>$v!=='','status_events_only'=>true);
    }
    private function secret(){
        $r=$this->db->one('system_settings',array('key'=>self::KEY));
        if(!$r||!$r['value']){
            throw new AuthFailure(503,'Webhook signing is not configured.');
        }
        try{
            $v=$this->config->openRuntimeSecret($this->scope($r['id']),$r['value']);
            if(strlen($v)<32||strlen($v)>512){
                throw new \RuntimeException();
            }
            return $v;
        }
        catch(\Throwable $e){
            throw new AuthFailure(503,'Webhook signing configuration is unreadable. Replace it explicitly.');
        }
    }
    public static function verify($raw,$signature,$timestamp,$secret,$now){
        if(!is_string($raw)||!is_string($secret)||$secret===''||!is_string($signature)||!is_string($timestamp)||strlen($timestamp)>32||strlen($signature)>128){
            return false;
        }
        $ts=trim($timestamp);
        if(!preg_match('/^[0-9]{1,12}$/D',$ts)||abs($now-(int)$ts)>300){
            return false;
        }
        $sig=strtolower(trim($signature));
        return preg_match('/^[a-f0-9]{64}$/D',$sig)===1&&hash_equals(hash_hmac('sha256',$raw.$ts,$secret),$sig);
    }
    private static function text($v,$field,$max){
        if(!is_string($v)||$v===''||strlen($v)>$max||preg_match('/[\x00-\x1f\x7f]/',$v)||preg_match('//u',$v)!==1){
            throw new ValidationFailure('Invalid callback identifier',$field);
        }
        return $v;
    }
    public function receive($raw,$signature,$timestamp,$secretOverride=null){
        if(!is_string($raw)||strlen($raw)>65536){
            throw new AuthFailure(413,'Callback is too large.');
        }
        $now=(int)(new \DateTimeImmutable($this->records->now(),new \DateTimeZone('UTC')))->format('U');
        $secret=$secretOverride!==null?$secretOverride:$this->secret();
        if(!self::verify($raw,$signature,$timestamp,$secret,$now)){
            throw new AuthFailure(401,'Invalid webhook signature');
        }
        try{
            $body=json_decode($raw,false,32,JSON_THROW_ON_ERROR);
        }
        catch(\Throwable $e){
            throw new ValidationFailure('Invalid callback JSON');
        }
        if(!is_object($body)){
            throw new ValidationFailure('Callback must be a JSON object');
        }
        $event=$body->event??null;
        if(!is_string($event)||!isset(self::EVENTS[$event])){
            throw new AuthFailure(503,'Only SMS delivery status events are implemented. No inbound event was accepted.');
        }
        $payload=$body->payload??$body;
        if(!is_object($payload)){
            throw new ValidationFailure('Payload must be an object');
        }
        $pid=self::text($payload->messageId??null,'messageId',255);
        $eventId=isset($body->id)?self::text($body->id,'id',255):null;
        // Namespaced hashes avoid source truncation/collision and event-type loss when no delivery ID exists.
        $key='native-smsgate:'.hash('sha256',($eventId!==null?'id:'.$eventId:'body:'.$raw));
        $old=$this->db->one('webhook_events',array('idempotency_key'=>$key));
        $digest=hash('sha256',$raw);
        if($old){
            $saved=json_decode($old['payload'],true);
            if(($saved['body_sha256']??null)!==$digest){
                throw new AuthFailure(409,'Delivery ID was reused with different content.');
            }
            if($old['status']!=='processed'){
                throw new AuthFailure(503,'Previous receipt is not settled.');
            }
            return array('ok'=>true,'duplicate'=>true);
        }
        $when=null;
        foreach(array('deliveredAt','sentAt','failedAt','cancelledAt') as $field){
            if(isset($payload->$field)&&$payload->$field!==''){
                try{
                    $when=Calendar::date($payload->$field,$field);
                }
                catch(ValidationFailure $e){
                    throw new ValidationFailure('Invalid receipt timestamp',$field);
                }
                break;
            }
        }
        $matched=$this->reconcile($pid,self::EVENTS[$event],$when);
        if(!$matched){
            throw new AuthFailure(503,'No matching SMS message is committed yet. Retry this receipt.');
        }
        $this->records->create('webhook_events',array('event_type'=>$event,'provider'=>'smsgate','provider_event_id'=>$eventId??$pid,'idempotency_key'=>$key,'payload'=>json_encode(array('body_sha256'=>$digest,'message_id'=>$matched['id'],'content_redacted'=>true),JSON_THROW_ON_ERROR),'status'=>'processed','processed_at'=>$this->records->now()));
        return array('ok'=>true,'matched'=>true);
    }
    /** Caller must hold the common outer transaction. Exactly one outgoing smsgate/SMS match required. */
    public function reconcile($pid,$status,$when=null){
        if(!in_array($status,array_values(self::EVENTS),true)){
            throw new ValidationFailure('Unsupported receipt state');
        }
        $rows=$this->db->rows('SELECT * FROM '.$this->table('messages')." WHERE provider_message_id=%s AND direction='outgoing' AND provider='smsgate' AND COALESCE(channel,'sms')='sms' LIMIT 2",array($pid));
        if(!$rows){
            return null;
        }
        if(count($rows)!==1){
            throw new AuthFailure(409,'Provider message identity is ambiguous.');
        }
        $m=$this->db->decode('messages',$rows[0]);
        // SQL PAD SPACE/collation behavior must not broaden provider identity.
        if($m['provider_message_id']!==$pid||$m['provider']!=='smsgate'||$m['direction']!=='outgoing'||($m['channel']!==null&&$m['channel']!=='sms')){
            throw new AuthFailure(409,'Provider message identity does not match exactly.');
        }
        $terminal=array('delivered','failed','cancelled');
        $rank=array('queued'=>0,'sending'=>1,'sent'=>2,'delivered'=>3,'failed'=>3,'cancelled'=>3);
        $same=$m['status']===$status;
        if(!$same&&(in_array($m['status'],$terminal,true)||($rank[$status]<($rank[$m['status']]??0)))){
            return $m;
        }
        $patch=$same?array():array('status'=>$status);
        $field=$status==='delivered'?'delivered_at':($status==='sent'?'sent_at':'failed_at');
        if($m[$field]===null){
            $patch[$field]=$when??$this->records->now();
        }
        if(!$same&&in_array($status,array('failed','cancelled'),true)){
            $patch['last_error']='Gateway reported '.$status;
        }
        $where=array_intersect_key($m,array_flip(array('id','status','direction','contact_id','campaign_id','provider','provider_message_id','channel','sent_at','delivered_at','failed_at')));
        if($patch&&$this->db->update('messages',$patch,$where)!==1){
            throw new AuthFailure(409,'Message changed. Retry the receipt.');
        }
        if(strpos($m['idempotency_key'],'native-sms:')===0){ScheduledSms::sync($this->db,$this->records,$m,array_replace($patch,array('status'=>$status)));}
        if(!$same&&in_array($status,array('failed','cancelled'),true)){
            $contact=$this->db->one('contacts',array('id'=>$m['contact_id']));
            if($contact){
                if($contact['delivery_fail_count']>=2147483647){
                    throw new AuthFailure(503,'Contact failure counter is exhausted.');
                }
                if($this->db->update('contacts',array('is_undeliverable'=>true,'undeliverable_reason'=>$status,'delivery_fail_count'=>$contact['delivery_fail_count']+1,'updated_at'=>$this->records->now()),array('id'=>$contact['id'],'delivery_fail_count'=>$contact['delivery_fail_count']))!==1){
                    throw new AuthFailure(409,'Contact changed. Retry the receipt.');
                }
            }
        }
        if($m['campaign_id']&&in_array($status,$terminal,true)){
            if(!$same){
                $column=$status==='delivered'?'messages_delivered':'messages_failed';
                $changed=$this->db->execute('UPDATE '.$this->table('campaigns').' SET '.$column.'=COALESCE('.$column.',0)+1,updated_at=%s WHERE id=%d AND COALESCE('.$column.',0)<2147483647',array($this->records->now(),$m['campaign_id']));
                if($changed!==1){
                    throw new AuthFailure(503,'Campaign counter could not be reconciled.');
                }
            }
            $ccs=$this->db->select('campaign_contacts',array('campaign_id'=>$m['campaign_id'],'contact_id'=>$m['contact_id']),2);
            if(count($ccs)>1){
                throw new AuthFailure(409,'Campaign membership is ambiguous.');
            }
            if($ccs&&in_array($ccs[0]['status'],array('queued','sent'),true)){
                $cc=$ccs[0];
                if($this->db->update('campaign_contacts',array('status'=>$status==='delivered'?'delivered':'failed','updated_at'=>$this->records->now()),array('id'=>$cc['id'],'status'=>$cc['status']))!==1){
                    throw new AuthFailure(409,'Campaign member changed. Retry.');
                }
            }
        }
        return array_replace($m,$patch);
    }
    public static function settings($request,array $c,array $user){
        $s=new self($c['db'],$c['config']);
        if($request->get_method()==='GET'){
            return $s->metadata();
        }
        if($user['role']!=='admin'){
            throw new AuthFailure(403,'Application administrator role required.');
        }
        $q=$request->get_query_params();
        unset($q['rest_route']);
        if($q){
            throw new ValidationFailure('Signing configuration accepts JSON only');
        }
        if(strlen($request->get_body())>2048){
            throw new AuthFailure(413,'Signing configuration is too large.');
        }
        return $s->saveSecret(DataRoutes::body($request));
    }
    public static function dispatch($request){
        $id=bin2hex(random_bytes(16));
        try{
            $c=AuthRoutes::context();
            if(!$c['config']->ready()){
                throw new AuthFailure(503,'Application is not configured.');
            }
            $s=new self($c['db'],$c['config']);
            $out=$c['db']->transaction(function()use($s,$request){
                return $s->receive($request->get_body(),$request->get_header('x-signature'),$request->get_header('x-timestamp'));
            }
            );
            return AuthRoutes::response($out,200,$id);
        }
        catch(AuthFailure $e){
            return AuthRoutes::response(AuthRoutes::error($e->status,$e->getMessage(),$id),$e->status,$id);
        }
        catch(ValidationFailure $e){
            return AuthRoutes::response(AuthRoutes::error(422,$e->getMessage(),$id),422,$id);
        }
        catch(\Throwable $e){
            return AuthRoutes::response(AuthRoutes::error(503,'Receipt processing failed. Retry the delivery.',$id),503,$id);
        }
    }
    public function receivePhone($phoneId,$raw,$signature,$timestamp){
        $pid=DataRoutes::integer($phoneId,'phone_id',1);
        $rows=$this->db->rows('SELECT id,webhook_secret_encrypted FROM '.$this->table('gateway_settings').' WHERE id=%d',array($pid));
        if(!$rows){throw new AuthFailure(404,'Sender phone not found');}
        $r=$this->db->decode('gateway_settings',$rows[0]);
        $sec=null;
        if($r['webhook_secret_encrypted']!==null&&$r['webhook_secret_encrypted']!==''){
            try{$v=$this->config->openRuntimeSecret($this->db->schema()->table('gateway_settings').'/'.$pid.'/webhook_secret',$r['webhook_secret_encrypted']);if(strlen($v)<16||strlen($v)>512){throw new \RuntimeException();}$sec=$v;}
            catch(\Throwable $e){throw new AuthFailure(503,'Phone webhook signing configuration is unreadable.');}
        }
        return $this->receive($raw,$signature,$timestamp,$sec);
    }
    public static function dispatchPhone($request){
        $id=bin2hex(random_bytes(16));
        try{
            $c=AuthRoutes::context();if(!$c['config']->ready()){throw new AuthFailure(503,'Application is not configured.');}
            $s=new self($c['db'],$c['config']);
            $path=trim(substr($request->get_route(),strlen('/sender-suite/v1/')),'/');
            if(!preg_match('~^webhooks/smsgateway/phone/(-?[0-9]+)$~D',$path,$m)){throw new AuthFailure(404,'Not found');}
            $out=$c['db']->transaction(function()use($s,$m,$request){return $s->receivePhone($m[1],$request->get_body(),$request->get_header('x-signature'),$request->get_header('x-timestamp'));});
            return AuthRoutes::response($out,200,$id);
        }catch(AuthFailure $e){return AuthRoutes::response(AuthRoutes::error($e->status,$e->getMessage(),$id),$e->status,$id);}
        catch(ValidationFailure $e){return AuthRoutes::response(AuthRoutes::error(422,$e->getMessage(),$id),422,$id);}
        catch(\Throwable $e){return AuthRoutes::response(AuthRoutes::error(503,'Receipt processing failed. Retry the delivery.',$id),503,$id);}
    }
}
