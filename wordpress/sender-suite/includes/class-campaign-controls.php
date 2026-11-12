<?php
namespace SenderSuite;

/** Native non-launching campaign mutations and validation. No job registration, sender transport or execution. */
final class CampaignControls {
    const FENCE='native_campaign_controls_fence';
    private $db;private $records;private $reports;private $config;
    public function __construct(Database $db,callable $clock=null,Config $config=null){$this->db=$db;$this->records=new Records($db,$clock);$this->reports=new CampaignReports($db,$clock);$this->config=$config;}
    public static function definitions(){return array('/campaigns/?'=>array('GET','POST'),'/campaigns/(?P<campaign_id>-?[0-9]+)'=>array('GET','PUT','PATCH','DELETE'),'/campaigns/(?P<campaign_id>-?[0-9]+)/(?P<action>duplicate|pause|stop|start|resume|schedule|validate)'=>array('POST'));}
    private function table($name){return Schema::identifier($this->db->schema()->table($name));}
    private function stamp($old){$now=$this->records->now();return $now<=$old?(new \DateTimeImmutable($old,new \DateTimeZone('UTC')))->modify('+1 microsecond')->format('Y-m-d H:i:s.u'):$now;}
    private function fence(){
        $this->db->requireTransaction();$r=$this->db->one('system_settings',array('key'=>self::FENCE));
        if(!$r){$this->records->create('system_settings',array('key'=>self::FENCE,'value'=>'1','category'=>'native_campaign'));return;}
        $stamp=$this->stamp($r['updated_at']);if($this->db->update('system_settings',array('value'=>$stamp,'updated_at'=>$stamp),array('id'=>$r['id'],'value'=>$r['value'],'updated_at'=>$r['updated_at']))!==1){throw new AuthFailure(409,'Campaign configuration changed. Reload and retry.');}
    }
    private function row($id){$this->reports->read($id);return $this->db->one('campaigns',array('id'=>$id));}
    private function change(array $row,array $patch){
        foreach($patch as $key=>$value){if($value===$row[$key]){unset($patch[$key]);}}if(!$patch){return $row;}
        $patch['updated_at']=$this->stamp($row['updated_at']);
        if($this->db->update('campaigns',$patch,array('id'=>$row['id'],'status'=>$row['status'],'updated_at'=>$row['updated_at'],'messages_sent'=>$row['messages_sent']))!==1){throw new AuthFailure(409,'Campaign changed. Reload before modifying it.');}
        return array_replace($row,$patch);
    }
    private static function cut($text,$n){return implode('',array_map(array(Compat::class,'chr'),array_slice(Compat::points($text),0,$n)));}
    public function input($body,$update=false,$allowPast=false){
        if(!is_object($body)){throw new ValidationFailure('Supply a campaign JSON object');}
        $defaults=array('name'=>null,'description'=>null,'channel'=>'sms','list_id'=>null,'template_id'=>null,'message_body'=>null,'sequence_id'=>null,'gateway_setting_id'=>null,'scheduled_start_at'=>null,'daily_limit'=>null,'hourly_limit'=>null,'per_minute_limit'=>null,'min_delay'=>null,'max_delay'=>null,'send_start_hour'=>null,'send_end_hour'=>null,'allow_weekends'=>true,'timezone_name'=>null,'allowed_weekdays'=>null,'email_account_id'=>null,'fallback_email_account_id'=>null,'email_account_ids'=>null,'sender_distribution'=>'round_robin','email_sender_weights'=>null,'sender_daily_limits'=>null,'subject'=>null,'html_body'=>null,'attachments'=>null,'track_opens'=>true,'track_clicks'=>true,'plain_personal_email'=>false);
        $out=$update?array():$defaults;
        foreach($body as $key=>$value){
            if($key==='idempotency_key'&&!$update){continue;}
            if(!array_key_exists($key,$defaults)){throw new ValidationFailure('Unknown campaign field',$key);}
            if($value===null){if(!$update&&in_array($key,array('name','channel','allow_weekends','sender_distribution','track_opens','track_clicks','plain_personal_email'),true)){throw new ValidationFailure('Input must not be null',$key);}if($update&&in_array($key,array('name','channel','allow_weekends','sender_distribution','track_opens','track_clicks','plain_personal_email'),true)){throw new ValidationFailure('This stored field cannot be null',$key);}$out[$key]=null;continue;}
            if(in_array($key,array('list_id','template_id','sequence_id','gateway_setting_id','email_account_id','fallback_email_account_id'),true)){$value=DataRoutes::integer($value,$key);}
            elseif(in_array($key,array('daily_limit','hourly_limit','per_minute_limit','min_delay','max_delay','send_start_hour','send_end_hour'),true)){$value=DataRoutes::integer($value,$key,in_array($key,array('daily_limit','hourly_limit','per_minute_limit'),true)?1:0,in_array($key,array('send_start_hour','send_end_hour'),true)?23:2147483647);}
            elseif(in_array($key,array('allow_weekends','track_opens','track_clicks','plain_personal_email'),true)){$value=DataRoutes::boolean($value,$key);}
            elseif($key==='scheduled_start_at'){$value=Calendar::date($value,$key);if(!$allowPast&&$value<(new \DateTimeImmutable($this->records->now(),new \DateTimeZone('UTC')))->modify('-1 minute')->format('Y-m-d H:i:s.u')){throw new ValidationFailure('scheduled_start_at must be in the future',$key);}}
            elseif(in_array($key,array('email_account_ids','attachments'),true)){if(!is_array($value)||count($value)>100){throw new ValidationFailure('Supply a bounded list',$key);}}
            elseif(in_array($key,array('email_sender_weights','sender_daily_limits'),true)){if(!is_object($value)||count(get_object_vars($value))>100){throw new ValidationFailure('Supply a bounded object',$key);}}
            else{if(!is_string($value)||strpos($value,"\0")!==false||preg_match('//u',$value)!==1){throw new ValidationFailure('Supply valid text without NUL',$key);}$limit=array('name'=>255,'channel'=>10,'subject'=>500,'timezone_name'=>120,'allowed_weekdays'=>30,'sender_distribution'=>20)[$key]??262144;if(Compat::length($value)>$limit){throw new ValidationFailure('Campaign text exceeds the field limit',$key);}}
            if($key==='channel'&&!in_array($value,array('sms','email'),true)){throw new ValidationFailure("channel must be 'sms' or 'email'",$key);}
            if($key==='sender_distribution'){$value=Compat::lower(Compat::strip($value));if(!in_array($value,array('round_robin','weighted','random'),true)){throw new ValidationFailure('Unsupported sender distribution',$key);}}
            $out[$key]=$value;
        }
        if(!$update&&!property_exists($body,'name')){throw new ValidationFailure('Field required','name');}
        return $out;
    }
    private function smsDefinition(array $d){
        if(($d['channel']??'sms')!=='sms'){throw new AuthFailure(503,'Native email campaign sender resolution is not implemented; no SMS fallback is permitted.');}
        foreach(array('email_account_id','fallback_email_account_id','email_account_ids','email_sender_weights','sender_daily_limits','subject','html_body','attachments') as $key){$v=$d[$key??'']??null;if($v!==null&&$v!==''&&$v!==array()&&!(is_object($v)&&!count(get_object_vars($v)))){throw new AuthFailure(503,'Email campaign configuration is not enabled on this SMS draft path.');}}
        foreach(array('list_id'=>'contact_lists','template_id'=>'templates','sequence_id'=>'sequences','gateway_setting_id'=>'gateway_settings') as $key=>$table){if(($d[$key]??null)!==null&&!$this->db->rows('SELECT id FROM '.$this->table($table).' WHERE id=%d',array($d[$key]))){throw new AuthFailure(404,'Selected campaign reference not found: '.$key);}}
    }
    private function resolveBody(array $row,&$tpl=null){
        $tpl=null;
        if($row['template_id']){$trows=$this->db->rows('SELECT id,body,subject,html_body FROM '.$this->table('templates').' WHERE id=%d',array($row['template_id']));$tpl=$trows?$this->db->decode('templates',$trows[0]):null;}
        if($row['message_body']!==null&&Compat::strip($row['message_body'])!==''){return $row['message_body'];}
        if($tpl&&Compat::strip($tpl['body']??'')!==''){return $tpl['body'];}
        return null;
    }
    private function usableSmsSender(){
        $rows=$this->db->rows('SELECT id,username,password_encrypted FROM '.$this->table('gateway_settings').' WHERE is_enabled=1 LIMIT 100');
        foreach($rows as $raw){
            $r=$this->db->decode('gateway_settings',$raw);
            if(Compat::strip($r['username']??'')===''||Compat::strip($r['password_encrypted']??'')===''){continue;}
            if($this->config!==null){try{if($this->config->openRuntimeSecret($this->db->schema()->table('gateway_settings').'/'.$r['id'].'/password',$r['password_encrypted'])===''){continue;}}catch(\Throwable $e){continue;}}
            return true;
        }
        return false;
    }
    private function checkGateway(array $row){
        $channel=Compat::lower(Compat::strip($row['channel']??'sms'))?:'sms';
        if($channel==='email'){return 'Native email campaign sender resolution is not implemented yet.';}
        if($row['gateway_setting_id']){
            $grows=$this->db->rows('SELECT id,is_enabled FROM '.$this->table('gateway_settings').' WHERE id=%d',array($row['gateway_setting_id']));
            if(!$grows){return 'Selected SMS gateway no longer exists';}
            $g=$this->db->decode('gateway_settings',$grows[0]);
            if(!$g['is_enabled']){return 'Selected SMS gateway is disabled';}
            return null;
        }
        if(!$this->usableSmsSender()){return 'No SMS gateway configured. Set SMSGATE_BASE_URL, SMSGATE_USERNAME and SMSGATE_PASSWORD, or connect a phone in Settings -> SMS Gateway.';}
        return null;
    }
    private static function blockedReason(array $c){
        if($c['is_opted_out']){return 'opted_out';}
        if($c['is_undeliverable']){return ($c['undeliverable_reason']!==null&&$c['undeliverable_reason']!=='')?$c['undeliverable_reason']:'undeliverable';}
        $chk=NumberFilter::classify($c['phone_number']??'');
        if($chk['sendable']){return null;}
        $reason=$chk['reason']?:'invalid_format';
        return $reason==='not_mobile'?'not_nigerian_mobile':$reason;
    }
    public function validate($id){
        $row=$this->row($id);$channel=Compat::lower(Compat::strip($row['channel']??'sms'))?:'sms';
        $errors=array();$warnings=array();$audience=new \stdClass();$projection=new \stdClass();$members=0;
        if(!$row['list_id']){$errors[]='No contact list selected';}
        else{$members=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('contact_list_members').' WHERE list_id=%d',array($row['list_id']))[0]['n'];$audience->list_members=$members;if($members===0){$errors[]='Contact list is empty';}}
        $tpl=null;$body=$this->resolveBody($row,$tpl);
        if(!$row['sequence_id']&&$body===null){$errors[]='No message to send. Write a message or choose a template.';}
        $subject=$channel==='email'?($row['subject']?:($tpl['subject']??null)):null;
        $html=$channel==='email'?($row['html_body']?:($tpl['html_body']??null)):null;
        $vars=new Variables($this->db);list($unknown,$known)=$vars->unknownIn(array($body,$subject,$html));
        if($unknown){$errors[]=Variables::unknownError($unknown,$known)['message'];}
        if($row['sequence_id']){
            $srows=$this->db->rows('SELECT id,name,channel,is_active,current_version FROM '.$this->table('sequences').' WHERE id=%d',array($row['sequence_id']));
            $seq=$srows?$this->db->decode('sequences',$srows[0]):null;
            if(!$seq){$errors[]='Selected sequence no longer exists';}
            elseif(!$seq['is_active']){$errors[]='Selected sequence is inactive';}
            else{
                $seqCh=Compat::lower(Compat::strip($seq['channel']??'sms'))?:'sms';
                if($seqCh!==$channel){$errors[]="Sequence '".$seq['name']."' is an ".strtoupper($seqCh).' sequence, but this is an '.strtoupper($channel).' campaign. Pick a '.strtoupper($channel).' sequence.';}
                else{
                    $rawSteps=$this->db->rows('SELECT * FROM '.$this->table('sequence_steps').' WHERE sequence_id=%d AND version=%d ORDER BY step_order ASC',array($seq['id'],$seq['current_version']));
                    $steps=array();foreach($rawSteps as $rs){$steps[]=$this->db->decode('sequence_steps',$rs);}
                    try{(new Sequences($this->db))->validate($steps,$seqCh,$body!==null);}catch(AuthFailure $e){$errors[]='Sequence is invalid: '.$e->getMessage();}
                }
            }
        }
        $gwErr=$this->checkGateway($row);if($gwErr!==null){$errors[]=$gwErr;}
        if($row['list_id']&&$members>0){
            if($members>50000){throw new AuthFailure(413,'Campaign contact list exceeds the synchronous validation limit.');}
            $crows=$this->db->rows('SELECT c.id,c.phone_number,c.is_opted_out,c.is_undeliverable,c.undeliverable_reason FROM '.$this->table('contacts').' c JOIN '.$this->table('contact_list_members').' m ON m.contact_id=c.id WHERE m.list_id=%d ORDER BY c.id ASC',array($row['list_id']));
            $sendable=0;$skipped=array();
            if($channel==='sms'){
                foreach($crows as $cr){$c=$this->db->decode('contacts',$cr);$reason=self::blockedReason($c);if($reason!==null){$skipped[$reason]=($skipped[$reason]??0)+1;}else{$sendable++;}}
            }
            ksort($skipped);$skippedTotal=array_sum($skipped);
            $audience=(object)array('list_members'=>count($crows),'sendable'=>$sendable,'skipped'=>$skipped?(object)$skipped:new \stdClass(),'unverified'=>0,'risky'=>0);
            if($skippedTotal>0){$parts=array();foreach($skipped as $r=>$n){$parts[]=$n.' '.$r;}$warnings[]=$skippedTotal.' of '.count($crows).' contacts will be skipped ('.implode(', ',$parts).').';}
            $daily=$row['daily_limit'];$activeDays=($row['allow_weekends']??true)?7:5;$estDays=0;
            if($daily!==null&&(int)$daily>0){$needed=(int)floor((max($sendable,0)+(int)$daily-1)/(int)$daily);$estDays=(int)floor(($needed*7+$activeDays-1)/$activeDays);}
            $projection=(object)array('campaign_only'=>true,'channel'=>$channel,'sendable_contacts'=>max($sendable,0),'daily_limit'=>$daily,'estimated_calendar_days'=>$estDays,'warnings'=>array());
        }
        $status=$row['status']??'draft';
        if(!in_array($status,array('draft','scheduled'),true)){$warnings[]='This campaign is '.$status.'; the checks below describe its definition only and nothing can be launched from here.';}
        $valid=count($errors)===0;
        return array('valid'=>$valid,'ok'=>$valid,'status'=>$status,'changed'=>false,'errors'=>$errors,'warnings'=>$warnings,'audience'=>$audience,'projection'=>$projection,'message'=>$valid?'Campaign is valid. Nothing was changed: use Start to send now, or Schedule to set a launch time.':count($errors).' problem(s) found. Nothing was changed: fix them and validate again.');
    }
    public function schedule($id,$body,array $user,$headerKey=null,$expectedHeader=null){
        if(($user['role']??'')!=='admin'){throw new AuthFailure(403,'Application administrator role required.');}
        if(!is_object($body)){throw new ValidationFailure('Supply a JSON object');}
        foreach($body as $k=>$v){if(!in_array($k,array('scheduled_start_at','idempotency_key','expected_status'),true)){throw new ValidationFailure('Unknown schedule field',$k);}}
        $when=$body->scheduled_start_at??null;
        if($when!==null){
            $parsed=Calendar::date($when,'scheduled_start_at');
            if($parsed<(new \DateTimeImmutable($this->records->now(),new \DateTimeZone('UTC')))->modify('-1 minute')->format('Y-m-d H:i:s.u')){throw new ValidationFailure('scheduled_start_at must be in the future','scheduled_start_at');}
            if(class_exists(CampaignWorker::class)&&CampaignWorker::enabled()){return (new CampaignWorker($this->db,$this->config))->launchClassicCampaign($id,'schedule',$user,$parsed);}
            throw new AuthFailure(503,'Native campaign launch, resumption and scheduling require a qualified execution worker; no jobs were created.');
        }
        $key=$body->idempotency_key??$headerKey;$ledger=null;$digest=hash('sha256',json_encode(array('id'=>$id,'scheduled_start_at'=>null),JSON_THROW_ON_ERROR));
        if($key!==null&&$key!==''){if(!is_string($key)||!preg_match('/^[A-Za-z0-9._:-]{8,200}$/D',$key)){throw new ValidationFailure('Supply an 8–200 character campaign idempotency key','idempotency_key');}$ledger='native.campaign.schedule.'.$id.'.'.hash('sha256',$user['id'].':'.$key);}
        $exp=$body->expected_status??$expectedHeader;if($exp!==null&&(!is_string($exp)||Compat::length($exp)>40)){throw new ValidationFailure('Invalid expected_status','expected_status');}
        $this->fence();
        if($ledger!==null){
            $rows=$this->db->rows('SELECT value FROM '.$this->table('system_settings').' WHERE `key`=%s AND OCTET_LENGTH(value)<=1024',array($ledger));
            if($rows){try{$saved=json_decode($rows[0]['value'],true,8,JSON_THROW_ON_ERROR);}catch(\Throwable $e){throw new AuthFailure(503,'Campaign schedule ledger needs repair.');}if(!is_array($saved)||!is_string($saved['digest']??null)||!is_array($saved['response']??null)){throw new AuthFailure(503,'Campaign schedule ledger needs repair.');}if(!hash_equals($saved['digest'],$digest)){throw new AuthFailure(409,'Campaign schedule idempotency key was already used for different content.');}$replay=$saved['response'];$replay['replayed']=true;return $replay;}
            if($this->db->rows('SELECT id FROM '.$this->table('system_settings').' WHERE `key`=%s',array($ledger))){throw new AuthFailure(503,'Campaign schedule ledger exceeds the repair limit.');}
        }
        $row=$this->row($id);
        if($exp!==null&&$exp!==''&&$row['status']!==$exp){throw new AuthFailure(409,"State conflict: campaign {$id} is '{$row['status']}', expected '{$exp}'. Reconcile remote state first.");}
        if(!in_array($row['status'],array('draft','scheduled'),true)){throw new AuthFailure(409,'Cannot schedule a campaign that is '.$row['status'].'.');}
        if($row['status']==='scheduled'){$this->change($row,array('status'=>'draft','scheduled_start_at'=>null,'scheduled_at'=>null));$resp=array('success'=>true,'changed'=>true,'status'=>'draft','scheduled_start_at'=>null,'message'=>'Schedule cancelled. The campaign is a draft again.');}
        elseif($row['scheduled_start_at']!==null){$this->change($row,array('scheduled_start_at'=>null));$resp=array('success'=>true,'changed'=>true,'status'=>'draft','scheduled_start_at'=>null,'message'=>'Launch time removed. The campaign is still a draft.');}
        else{$resp=array('success'=>true,'changed'=>false,'status'=>'draft','scheduled_start_at'=>null,'message'=>'Nothing to cancel: this campaign has no schedule.');}
        if($ledger!==null){$this->records->create('system_settings',array('key'=>$ledger,'value'=>json_encode(array('digest'=>$digest,'response'=>$resp),JSON_THROW_ON_ERROR),'category'=>'native_campaign_idempotency'));}
        return $resp;
    }
    public function create($body,array $user,$headerKey=null){
        if(($user['role']??'')!=='admin'){throw new AuthFailure(403,'Application administrator role required.');}
        $d=$this->input($body,false,true);$key=$body->idempotency_key??$headerKey;$ledger=null;$digest=hash('sha256',json_encode($d,JSON_THROW_ON_ERROR));
        if($key!==null&&$key!==''){if(!is_string($key)||!preg_match('/^[A-Za-z0-9._:-]{8,200}$/D',$key)){throw new ValidationFailure('Supply an 8–200 character campaign idempotency key');}$ledger='native.campaign.create.'.hash('sha256',$user['id'].':'.$key);}
        $this->fence();
        if($ledger!==null){$rows=$this->db->rows('SELECT value FROM '.$this->table('system_settings').' WHERE `key`=%s AND OCTET_LENGTH(value)<=1024',array($ledger));if($rows){try{$saved=json_decode($rows[0]['value'],true,8,JSON_THROW_ON_ERROR);}catch(\Throwable $e){throw new AuthFailure(503,'Campaign idempotency ledger needs repair.');}if(!is_array($saved)||!is_int($saved['id']??null)||!is_string($saved['digest']??null)){throw new AuthFailure(503,'Campaign idempotency ledger needs repair.');}if(!hash_equals($saved['digest'],$digest)){throw new AuthFailure(409,'Campaign idempotency key was already used for different content.');}return $this->reports->read($saved['id']);}if($this->db->rows('SELECT id FROM '.$this->table('system_settings').' WHERE `key`=%s',array($ledger))){throw new AuthFailure(503,'Campaign idempotency ledger exceeds the repair limit.');}}
        if($d['scheduled_start_at']!==null&&$d['scheduled_start_at']<(new \DateTimeImmutable($this->records->now(),new \DateTimeZone('UTC')))->modify('-1 minute')->format('Y-m-d H:i:s.u')){throw new ValidationFailure('scheduled_start_at must be in the future','scheduled_start_at');}
        $this->smsDefinition($d);foreach(array('message_body','subject','timezone_name','allowed_weekdays') as $key){if(is_string($d[$key])){$v=Compat::strip($d[$key]);$d[$key]=$v===''?null:$v;}}
        $row=$this->records->create('campaigns',$d);
        if($ledger!==null){$this->records->create('system_settings',array('key'=>$ledger,'value'=>json_encode(array('id'=>$row['id'],'digest'=>$digest),JSON_THROW_ON_ERROR),'category'=>'native_campaign_idempotency'));}
        return $this->reports->read($row['id']);
    }
    public function update($id,$body){
        $d=$this->input($body,true);$this->fence();$row=$this->row($id);
        if(!in_array($row['status'],array('draft','scheduled'),true)){throw new AuthFailure(409,'Cannot edit a campaign that is '.$row['status'].'. Duplicate it to make changes.');}
        if(($row['channel']??'sms')!=='sms'){throw new AuthFailure(503,'Native email campaign editing is not enabled.');}
        $this->smsDefinition(array_replace($row,$d));
        if(array_key_exists('message_body',$d)){$v=Compat::strip($d['message_body']??'');$d['message_body']=$v===''?null:$v;if($v!==''&&!array_key_exists('template_id',$d)){$d['template_id']=null;}}
        if(($d['template_id']??null)&&!array_key_exists('message_body',$d)){$d['message_body']=null;}
        foreach(array('timezone_name','allowed_weekdays') as $key){if(isset($d[$key])&&$d[$key]!==''){$v=Compat::strip($d[$key]);$d[$key]=$v===''?null:$v;}}
        if(array_key_exists('list_id',$d)){$d['total_contacts']=0;}
        $content=array_diff(array_keys($d),array('scheduled_start_at'));if($row['status']==='scheduled'&&($content||(array_key_exists('scheduled_start_at',$d)&&$d['scheduled_start_at']===null))){$d['status']='draft';$d['scheduled_at']=null;}
        $this->change($row,$d);return $this->reports->read($id);
    }
    public function duplicate($id){
        $this->fence();$row=$this->row($id);$fields=array_keys($this->input((object)array('name'=>'copy')));$d=array_intersect_key($row,array_flip($fields));
        $names=$this->db->rows('SELECT name FROM '.$this->table('campaigns').' LIMIT 10001');if(count($names)>10000){throw new AuthFailure(413,'Too many campaign names for bounded duplication.');}$existing=array();foreach($names as $item){$existing[$item['name']]=true;}
        $base=$row['name'];for($n=1;$n<=10000;$n++){$suffix=$n===1?' (Copy)':' (Copy '.$n.')';$name=self::cut($base,255-Compat::length($suffix)).$suffix;if(!isset($existing[$name])){break;}}if($n>10000){throw new AuthFailure(409,'Campaign copy-name limit reached.');}
        $d['name']=$name;$d['scheduled_start_at']=null;$new=$this->records->create('campaigns',$d);return $this->reports->read($new['id']);
    }
    public function pause($id){
        $this->fence();$row=$this->row($id);if(!in_array($row['status'],array('running','scheduled'),true)){throw new AuthFailure(409,'Only running or scheduled campaigns can be paused (this one is '.$row['status'].')');}
        $origin=$row['status']==='scheduled'?'scheduled':null;$this->change($row,array('status'=>'paused','paused_from'=>$origin,'paused_at'=>$this->records->now(),'paused_reason'=>'Paused manually'));
        return array('success'=>true,'changed'=>true,'status'=>'paused','paused_from'=>$origin,'message'=>$origin==='scheduled'?'Scheduled launch held. Resume puts the schedule back; nothing will send until you start it.':'Campaign paused. Resume continues where it left off.');
    }
    public function stop($id){
        $this->fence();$row=$this->row($id);if(!in_array($row['status'],array('running','paused','scheduled','failed'),true)){throw new AuthFailure(409,'Cannot stop campaign in '.$row['status'].' status');}
        // No running transport can be recalled; preserve sending/accepted/unknown history.
        $this->change($row,array('status'=>'stopped','completed_at'=>$this->records->now(),'paused_from'=>null));
        $this->db->execute('UPDATE '.$this->table('messages')." SET status='cancelled',last_error='Campaign stopped',retry_at=NULL WHERE campaign_id=%d AND direction='outgoing' AND status IN ('queued','retrying') AND provider_message_id IS NULL AND retry_count=0",array($id));
        $this->db->execute('UPDATE '.$this->table('scheduled_messages')." SET status='cancelled' WHERE status='pending' AND message_id IN (SELECT id FROM ".$this->table('messages')." WHERE campaign_id=%d AND direction='outgoing' AND status='cancelled' AND last_error='Campaign stopped' AND provider_message_id IS NULL AND retry_count=0)",array($id));
        $this->db->execute('UPDATE '.$this->table('campaign_contacts')." SET status='cancelled',next_action_at=NULL WHERE campaign_id=%d AND status IN ('pending','queued')",array($id));
        $this->db->execute('UPDATE '.$this->table('followups')." SET status='cancelled',last_error='Campaign stopped',updated_at=%s WHERE campaign_id=%d AND status='pending'",array($this->records->now(),$id));
        return array('success'=>true,'changed'=>true,'status'=>'stopped','message'=>'Campaign stopped. Pending contacts were cancelled.');
    }
    public function delete($id){
        $this->fence();$row=$this->row($id);if(!in_array($row['status'],array('draft','scheduled','failed'),true)&&!($row['status']==='paused'&&$row['paused_from']==='scheduled')){throw new AuthFailure(409,'A '.$row['status'].' campaign cannot be deleted. Stop it instead; only draft, scheduled and failed campaigns can be deleted.');}if($row['messages_sent']>0){throw new AuthFailure(409,'This campaign has already sent '.$row['messages_sent'].' message(s), so it cannot be deleted. Stop it to keep the history.');}
        foreach(array('messages'=>array('campaign_id'),'conversations'=>array('campaign_id','last_campaign_id'),'followups'=>array('campaign_id'),'meetings'=>array('campaign_id'),'campaign_contacts'=>array('campaign_id'),'campaign_followups'=>array('campaign_id')) as $table=>$columns){foreach($columns as $column){if($this->db->rows('SELECT id FROM '.$this->table($table).' WHERE '.Schema::identifier($column).'=%d LIMIT 1',array($id))){throw new AuthFailure(409,'Campaign has related history or work; stop it instead of deleting it.');}}}
        if($this->db->delete('campaigns',array('id'=>$id,'status'=>$row['status'],'updated_at'=>$row['updated_at'],'messages_sent'=>$row['messages_sent']))!==1){throw new AuthFailure(409,'Campaign changed before deletion.');}
    }
    public static function handle($request,array $context,array $user,&$status){
        $path=trim(substr($request->get_route(),strlen('/sender-suite/v1/')),'/');$method=$request->get_method();if($method==='GET'){return CampaignReports::handle($request,$context);}
        if(($user['role']??'')!=='admin'){throw new AuthFailure(403,'Application administrator role required.');}
        if(strlen($request->get_body())>524288){throw new AuthFailure(413,'Campaign request exceeds the text limit.');}
        $s=new self($context['db'],null,$context['config']??null);
        $hdrKey=$request->get_header('x-idempotency-key')?:$request->get_header('idempotency-key');
        if($path==='campaigns'&&$method==='POST'){$status=201;return $s->create(DataRoutes::body($request),$user,$hdrKey);}
        if(preg_match('~^campaigns/(-?[0-9]+)(?:/(duplicate|pause|stop|start|resume|schedule|validate))?$~D',$path,$m)){
            $id=DataRoutes::integer($m[1],'campaign_id',1);$action=$m[2]??'';
            if($action===''&&in_array($method,array('PUT','PATCH'),true)){return $s->update($id,DataRoutes::body($request));}
            if($action===''&&$method==='DELETE'){$s->delete($id);$status=204;return null;}
            if($method==='POST'){
                if($action==='validate'){return $s->validate($id);}
                if($action==='schedule'){$raw=$request->get_body();return $s->schedule($id,$raw===''?(object)array():DataRoutes::body($request),$user,$hdrKey,$request->get_header('x-expected-status'));}
                if(in_array($action,array('start','resume'),true)){
                    if(class_exists(CampaignWorker::class)&&CampaignWorker::enabled()){return (new CampaignWorker($context['db'],$context['config']??null))->launchClassicCampaign($id,$action,$user);}
                    throw new AuthFailure(503,'Native campaign launch, resumption and scheduling require a qualified execution worker; no jobs were created.');
                }
                if(in_array($action,array('duplicate','pause','stop'),true)){return $s->$action($id);}
            }
        }
        throw new AuthFailure(404,'Application endpoint not found.');
    }
}
