<?php
namespace SenderSuite;

/**
 * Native WordPress Campaign, Ads Manager, Follow-up, Inbound SMS Auto-Reply,
 * CallGate/SMS-Gate/Push Provider Operations, Click Tracking & Validator Jobs.
 */
final class CampaignWorker {
    private static $runtimeEnabled=null;
    private $db;private $config;private $records;
    public function __construct(Database $db,Config $config=null,callable $clock=null){
        $this->db=$db;$this->config=$config;$this->records=new Records($db,$clock);
    }
    public static function enabled(){
        if(self::$runtimeEnabled!==null){return (bool)self::$runtimeEnabled;}
        return (defined('SENDER_SUITE_ENABLE_WORKERS')&&SENDER_SUITE_ENABLE_WORKERS===true)||SmsOutbox::enabled();
    }
    private function table($n){return Schema::identifier($this->db->schema()->table($n));}
    private function activeGatewayCreds($preferredId=null){
        if($this->config===null){return null;}
        $gp=new GatewayPhones($this->db,$this->config);
        if($preferredId!==null){
            try{return array('id'=>(int)$preferredId,'creds'=>$gp->credentials((int)$preferredId));}catch(\Throwable $e){return null;}
        }
        $rows=$this->db->rows('SELECT id FROM '.$this->table('gateway_settings').' WHERE COALESCE(is_enabled,0)=1 ORDER BY is_default DESC,id ASC LIMIT 10');
        foreach($rows as $r){
            try{return array('id'=>(int)$r['id'],'creds'=>$gp->credentials((int)$r['id']));}catch(\Throwable $e){}
        }
        return null;
    }
    private function sendSmsMessage(array $contact,$text,$campaignId=null,$preferredPhoneId=null){
        $phone=Phone::clean($contact['phone_number']??'');
        if($phone===null||!empty($contact['is_opted_out'])){return array('ok'=>false,'reason'=>'opted_out_or_invalid');}
        $gw=$this->activeGatewayCreds($preferredPhoneId);
        $now=$this->records->now();
        $conv=$this->db->one('conversations',array('contact_id'=>$contact['id'],'channel'=>'sms'));
        if(!$conv){$conv=$this->records->create('conversations',array('contact_id'=>$contact['id'],'channel'=>'sms','last_message_at'=>$now,'last_message_preview'=>substr($text,0,200)));}
        else{$this->db->update('conversations',array('last_message_at'=>$now,'last_message_preview'=>substr($text,0,200)),array('id'=>$conv['id']));}
        if($gw!==null){
            $sim=in_array((int)($gw['creds']['sim_number']??1),array(1,2),true)?(int)$gw['creds']['sim_number']:1;
            $dev=!empty($gw['creds']['device_id'])?(string)$gw['creds']['device_id']:null;
            $res=SmsGate::send($gw['creds'],$phone,$text,$sim,$dev);
            $ok=$res['outcome']==='accepted';
            $msg=$this->records->create('messages',array('conversation_id'=>(int)$conv['id'],'contact_id'=>(int)$contact['id'],'campaign_id'=>$campaignId,'direction'=>'outgoing','channel'=>'sms','body'=>$text,'to_address'=>$phone,'status'=>$ok?($res['status']?:'sent'):'failed','provider'=>'smsgate','provider_message_id'=>$res['provider_message_id']??null,'idempotency_key'=>'sms_'.bin2hex(random_bytes(12)),'sent_at'=>$ok?$now:null,'failed_at'=>$ok?null:$now,'last_error'=>$ok?null:'Gateway rejected or unreachable'));
            return array('ok'=>$ok,'message_id'=>(int)$msg['id'],'provider_message_id'=>$res['provider_message_id']??null);
        }
        $msg=$this->records->create('messages',array('conversation_id'=>(int)$conv['id'],'contact_id'=>(int)$contact['id'],'campaign_id'=>$campaignId,'direction'=>'outgoing','channel'=>'sms','body'=>$text,'to_address'=>$phone,'status'=>'queued','provider'=>'smsgate','idempotency_key'=>'smsq_'.bin2hex(random_bytes(12))));
        return array('ok'=>true,'queued'=>true,'message_id'=>(int)$msg['id']);
    }

    // === ADS MANAGER LIVE WORKFLOWS & CLICK REDIRECT ===
    public static function dispatchClickRedirect($request){
        $reqId=bin2hex(random_bytes(16));
        try{
            $c=AuthRoutes::context();
            if(!$c['config']->ready()){throw new AuthFailure(503,'Application is not configured.');}
            $route=$request->get_route();
            if(!preg_match('~/ads/r/(-?[0-9]+)$~D',$route,$m)){throw new AuthFailure(404,'Not found');}
            $aid=DataRoutes::integer($m[1],'assignment_id',1);
            $asg=$c['db']->one('ads_assignments',array('id'=>$aid));
            if(!$asg){throw new AuthFailure(404,'Tracking link not found.');}
            $cr=$asg['creative_id']?$c['db']->one('ads_creatives',array('id'=>$asg['creative_id'])):null;
            $target=$cr&&!empty($cr['tracking_link'])?(string)$cr['tracking_link']:(function_exists('home_url')?(string)home_url('/'):'https://example.com/');
            $parts=parse_url($target);
            if(!$parts||!in_array(strtolower($parts['scheme']??''),array('http','https'),true)||empty($parts['host'])){$target='https://example.com/';}
            $svc=new self($c['db'],$c['config']);
            $c['db']->transaction(function()use($c,$svc,$asg,$cr){
                $now=$svc->records->now();
                $svc->records->create('ads_events',array('campaign_id'=>(int)$asg['campaign_id'],'set_id'=>$asg['set_id']?(int)$asg['set_id']:null,'creative_id'=>$asg['creative_id']?(int)$asg['creative_id']:null,'contact_id'=>(int)$asg['contact_id'],'assignment_id'=>(int)$asg['id'],'event_type'=>'click','detail'=>'Public shortlink redirect'));
                $c['db']->update('ads_assignments',array('last_action_at'=>$now),array('id'=>$asg['id']));
                if($asg['message_id']){
                    $c['db']->execute('UPDATE '.Schema::identifier($c['db']->schema()->table('messages')).' SET click_count=COALESCE(click_count,0)+1,clicked_at=COALESCE(clicked_at,%s) WHERE id=%d',array($now,(int)$asg['message_id']));
                }
            });
            $resp=AuthRoutes::response(array('ok'=>true,'redirect_to'=>$target),302,$reqId);
            $resp->header('Location',$target);
            return $resp;
        }catch(AuthFailure $e){
            return AuthRoutes::response(AuthRoutes::error($e->status,$e->getMessage(),$reqId),$e->status,$reqId);
        }catch(\Throwable $e){
            return AuthRoutes::response(AuthRoutes::error(503,'Click redirect unavailable.',$reqId),503,$reqId);
        }
    }
    private function populateAdsAudience($cid){
        $camp=$this->db->one('ads_campaigns',array('id'=>$cid));
        if(!$camp){throw new AuthFailure(404,'Ads campaign not found');}
        $sets=$this->db->rows('SELECT * FROM '.$this->table('ads_sets')." WHERE campaign_id=%d AND status='active' ORDER BY id ASC",array($cid));
        $creatives=$this->db->rows('SELECT * FROM '.$this->table('ads_creatives')." WHERE campaign_id=%d AND COALESCE(is_deleted,0)=0 AND status='active' ORDER BY id ASC",array($cid));
        if(!$sets||!$creatives){throw new AuthFailure(400,'Campaign requires at least one active ad set and active creative.');}
        $added=0;$idx=0;
        foreach($sets as $sr){
            $set=$this->db->decode('ads_sets',$sr);
            $listIds=json_decode((string)($set['list_ids']??'[]'),true)?:array();
            $contactIds=json_decode((string)($set['contact_ids']??'[]'),true)?:array();
            if(!empty($set['audience_id'])){
                $aud=$this->db->one('ads_audiences',array('id'=>$set['audience_id']));
                if($aud){
                    $listIds=array_merge($listIds,json_decode((string)($aud['list_ids']??'[]'),true)?:array());
                    $contactIds=array_merge($contactIds,json_decode((string)($aud['contact_ids']??'[]'),true)?:array());
                }
            }
            foreach($listIds as $lid){
                foreach($this->db->rows('SELECT contact_id FROM '.$this->table('contact_list_members').' WHERE list_id=%d LIMIT 2000',array((int)$lid)) as $mr){$contactIds[]=(int)$mr['contact_id'];}
            }
            foreach(array_unique(array_map('intval',$contactIds)) as $ctId){
                if($ctId<=0||$this->db->one('ads_assignments',array('campaign_id'=>$cid,'contact_id'=>$ctId))){continue;}
                $contact=$this->db->one('contacts',array('id'=>$ctId));
                if(!$contact){continue;}
                $isEmail=($camp['channel']??'sms')==='email';
                if(($isEmail&&(!empty($contact['is_email_opted_out'])||empty($contact['email'])))||(!$isEmail&&(!empty($contact['is_opted_out'])||empty($contact['phone_number'])))){continue;}
                $setCrs=array_values(array_filter($creatives,function($x)use($set){return (int)$x['set_id']===(int)$set['id'];}))?:$creatives;
                $cr=$this->db->decode('ads_creatives',$setCrs[$idx%count($setCrs)]);$idx++;
                $this->records->create('ads_assignments',array('campaign_id'=>$cid,'set_id'=>(int)$set['id'],'creative_id'=>(int)$cr['id'],'contact_id'=>$ctId,'phone_number'=>$contact['phone_number'],'assignment_method'=>'auto','send_status'=>'queued','delivery_status'=>'pending','idempotency_key'=>'ads_'.$cid.'_'.$ctId));
                $added++;
            }
        }
        return $added;
    }
    public function handleAdsRoute($path,$method,$request,array $user,&$status){
        $now=$this->records->now();
        if($path==='ads/track/click'&&$method==='POST'){
            $b=DataRoutes::body($request);$aid=DataRoutes::integer($b->assignment_id??0,'assignment_id',1);
            $asg=$this->db->one('ads_assignments',array('id'=>$aid));if(!$asg){throw new AuthFailure(404,'Assignment not found');}
            $this->db->transaction(function()use($asg,$now){
                $this->records->create('ads_events',array('campaign_id'=>(int)$asg['campaign_id'],'set_id'=>$asg['set_id']?(int)$asg['set_id']:null,'creative_id'=>$asg['creative_id']?(int)$asg['creative_id']:null,'contact_id'=>(int)$asg['contact_id'],'assignment_id'=>(int)$asg['id'],'event_type'=>'click','detail'=>'API click event'));
                $this->db->update('ads_assignments',array('last_action_at'=>$now),array('id'=>$asg['id']));
            });
            return array('ok'=>true,'assignment_id'=>$aid);
        }
        if($path==='ads/followups/process'&&$method==='POST'){
            $processed=0;
            foreach($this->db->rows('SELECT * FROM '.$this->table('ads_followup_tasks')." WHERE status='pending' AND due_at<=%s ORDER BY id ASC LIMIT 50",array($now)) as $tr){
                $t=$this->db->decode('ads_followup_tasks',$tr);
                $contact=$this->db->one('contacts',array('id'=>$t['contact_id']));
                $body=Compat::strip((string)($t['body']??$t['note']??''));
                $this->db->transaction(function()use($t,$contact,$body,$now,&$processed){
                    if($contact&&$body!==''&&empty($contact['is_opted_out'])){
                        $this->sendSmsMessage($contact,$body,$t['campaign_id']?(int)$t['campaign_id']:null);
                    }
                    $this->db->update('ads_followup_tasks',array('status'=>'completed','completed_at'=>$now),array('id'=>$t['id']));
                    $processed++;
                });
            }
            return array('ok'=>true,'processed'=>$processed);
        }
        if(preg_match('~^ads/sets/(-?[0-9]+)/pause-losers$~D',$path,$m)&&$method==='POST'){
            $sid=DataRoutes::integer($m[1],'set_id');
            if(!$this->db->one('ads_sets',array('id'=>$sid))){throw new AuthFailure(404,'Ad set not found');}
            return array('ok'=>true,'set_id'=>$sid,'paused_creatives'=>0);
        }
        if(preg_match('~^ads/(campaigns|creatives|contacts)/bulk$~D',$path,$m)&&$method==='POST'){
            $b=DataRoutes::body($request);$ids=isset($b->ids)&&is_array($b->ids)?array_map('intval',$b->ids):array();
            $act=strtolower(Compat::strip((string)($b->action??'pause')));
            $updated=0;
            $this->db->transaction(function()use($m,$ids,$act,$now,&$updated){
                foreach($ids as $id){
                    if($id<=0){continue;}
                    if($m[1]==='campaigns'&&in_array($act,array('pause','archive','complete'),true)){
                        $st=$act==='pause'?'paused':($act==='archive'?'archived':'completed');
                        $updated+=$this->db->update('ads_campaigns',array('status'=>$st,'updated_at'=>$now),array('id'=>$id));
                    }elseif($m[1]==='creatives'&&in_array($act,array('pause','activate'),true)){
                        $updated+=$this->db->update('ads_creatives',array('status'=>$act==='pause'?'paused':'active','updated_at'=>$now),array('id'=>$id));
                    }elseif($m[1]==='contacts'&&$act==='suppress'){
                        $c=$this->db->one('contacts',array('id'=>$id));
                        if($c){$this->db->update('contacts',array('is_opted_out'=>true,'updated_at'=>$now),array('id'=>$id));$updated++;}
                    }
                }
            });
            return array('ok'=>true,'updated'=>$updated);
        }
        if(preg_match('~^ads/campaigns/(-?[0-9]+)/(launch|resume|dispatch|contacts|rebuild-audience|optimize|jobs/retry|audience/bulk-remove)$~D',$path,$m)&&$method==='POST'){
            $cid=DataRoutes::integer($m[1],'campaign_id');$act=$m[2];
            $camp=$this->db->one('ads_campaigns',array('id'=>$cid));
            if(!$camp){throw new AuthFailure(404,'Ads campaign not found');}
            if($act==='rebuild-audience'){
                $added=$this->db->transaction(function()use($cid){return $this->populateAdsAudience($cid);});
                return array('ok'=>true,'added'=>$added);
            }
            if($act==='contacts'){
                $b=DataRoutes::body($request);$ids=isset($b->contact_ids)&&is_array($b->contact_ids)?array_map('intval',$b->contact_ids):array();
                $crs=$this->db->rows('SELECT id,set_id FROM '.$this->table('ads_creatives').' WHERE campaign_id=%d AND COALESCE(is_deleted,0)=0 LIMIT 1',array($cid));
                $added=0;
                $this->db->transaction(function()use($cid,$ids,$crs,&$added){
                    foreach($ids as $ctId){
                        $ct=$this->db->one('contacts',array('id'=>$ctId));
                        if(!$ct||$this->db->one('ads_assignments',array('campaign_id'=>$cid,'contact_id'=>$ctId))){continue;}
                        $this->records->create('ads_assignments',array('campaign_id'=>$cid,'set_id'=>$crs?(int)$crs[0]['set_id']:null,'creative_id'=>$crs?(int)$crs[0]['id']:null,'contact_id'=>$ctId,'phone_number'=>$ct['phone_number'],'assignment_method'=>'manual','send_status'=>'queued','delivery_status'=>'pending','idempotency_key'=>'ads_'.$cid.'_'.$ctId));
                        $added++;
                    }
                });
                return array('ok'=>true,'added'=>$added);
            }
            if($act==='audience/bulk-remove'){
                $b=DataRoutes::body($request);$ids=isset($b->assignment_ids)&&is_array($b->assignment_ids)?array_map('intval',$b->assignment_ids):array();
                $rem=0;$this->db->transaction(function()use($cid,$ids,&$rem){foreach($ids as $aid){$rem+=$this->db->delete('ads_assignments',array('id'=>$aid,'campaign_id'=>$cid));}});
                return array('ok'=>true,'removed'=>$rem);
            }
            if($act==='optimize'){
                $this->db->transaction(function()use($cid,$now){$this->db->update('ads_campaigns',array('optimize_last_run_at'=>$now,'updated_at'=>$now),array('id'=>$cid));});
                return array('ok'=>true,'campaign_id'=>$cid,'optimized_at'=>Records::iso($now));
            }
            if($act==='jobs/retry'){
                $this->db->transaction(function()use($cid,$now){$this->db->execute('UPDATE '.$this->table('ads_campaign_jobs')." SET status='queued',error=NULL,updated_at=%s WHERE campaign_id=%d AND status='failed'",array($now,$cid));});
                return array('ok'=>true,'campaign_id'=>$cid);
            }
            if($act==='launch'||$act==='resume'){
                $jobId='job_'.bin2hex(random_bytes(8));
                $queued=$this->db->transaction(function()use($cid,$camp,$act,$jobId,$now,$user){
                    $added=$act==='launch'?$this->populateAdsAudience($cid):0;
                    $total=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('ads_assignments')." WHERE campaign_id=%d AND send_status='queued'",array($cid))[0]['n'];
                    $this->db->update('ads_campaigns',array('status'=>'active','last_state'=>$camp['status'],'state_changed_at'=>$now,'last_activity_at'=>$now,'updated_at'=>$now),array('id'=>$cid));
                    $this->records->create('ads_campaign_state_transitions',array('campaign_id'=>$cid,'from_state'=>$camp['status'],'to_state'=>'active','reason'=>$act,'actor'=>$user['username']??'operator'));
                    $this->records->create('ads_campaign_jobs',array('id'=>$jobId,'campaign_id'=>$cid,'job_type'=>'dispatch','status'=>'queued','progress_completed'=>0,'progress_total'=>$total,'attempts'=>0,'max_retries'=>3));
                    return array('added'=>$added,'queued'=>$total);
                });
                return array('ok'=>true,'status'=>'active','job_id'=>$jobId,'queued_assignments'=>$queued['queued'],'campaign'=>(new AdsManager($this->db))->getCampaign($cid));
            }
            if($act==='dispatch'){
                $sent=0;$failed=0;
                $rows=$this->db->rows('SELECT * FROM '.$this->table('ads_assignments')." WHERE campaign_id=%d AND send_status='queued' ORDER BY id ASC LIMIT 50",array($cid));
                foreach($rows as $ar){
                    $asg=$this->db->decode('ads_assignments',$ar);
                    $contact=$this->db->one('contacts',array('id'=>$asg['contact_id']));
                    $cr=$asg['creative_id']?$this->db->one('ads_creatives',array('id'=>$asg['creative_id'])):null;
                    if(!$contact||!$cr){continue;}
                    $txt=str_replace(array('{{first_name}}','{first_name}'),(string)($contact['first_name']??''),(string)($cr['body']??''));
                    if(!empty($cr['tracking_link'])){
                        $short=(function_exists('rest_url')?rtrim((string)rest_url('sender-suite/v1/ads/r/'.$asg['id']),'/'):('/wp-json/sender-suite/v1/ads/r/'.$asg['id']));
                        $txt=rtrim($txt).' '.$short;
                    }
                    $this->db->transaction(function()use($cid,$camp,$asg,$contact,$cr,$txt,$now,&$sent,&$failed){
                        if(($camp['channel']??'sms')==='email'){
                            try{
                                $et=new EmailTransport($this->db,$this->config);
                                $r=$et->sendEmail(array('account_id'=>$cr['email_account_id']?:$camp['email_account_id'],'contact'=>$contact,'to'=>(string)$contact['email'],'subject'=>(string)($cr['subject']?:$camp['subject']?:$camp['name']),'body'=>$txt,'html_body'=>$cr['html_body']??null));
                                $this->db->update('ads_assignments',array('send_status'=>'sent','delivery_status'=>'sent','message_id'=>$r['message_id'],'sent_at'=>$now,'last_action_at'=>$now),array('id'=>$asg['id']));
                                $sent++;
                            }catch(\Throwable $e){
                                $this->db->update('ads_assignments',array('send_status'=>'failed','last_error'=>$e->getMessage(),'last_action_at'=>$now),array('id'=>$asg['id']));
                                $failed++;
                            }
                        }else{
                            $r=$this->sendSmsMessage($contact,$txt,$cid);
                            if(!empty($r['ok'])){
                                $this->db->update('ads_assignments',array('send_status'=>'sent','delivery_status'=>!empty($r['queued'])?'queued':'sent','message_id'=>$r['message_id'],'sent_at'=>$now,'last_action_at'=>$now),array('id'=>$asg['id']));
                                $sent++;
                            }else{
                                $this->db->update('ads_assignments',array('send_status'=>'failed','skip_reason'=>$r['reason']??'failed','last_action_at'=>$now),array('id'=>$asg['id']));
                                $failed++;
                            }
                        }
                        $this->records->create('ads_events',array('campaign_id'=>$cid,'set_id'=>$asg['set_id']?(int)$asg['set_id']:null,'creative_id'=>$asg['creative_id']?(int)$asg['creative_id']:null,'contact_id'=>(int)$contact['id'],'assignment_id'=>(int)$asg['id'],'event_type'=>'send','detail'=>'Dispatched'));
                    });
                }
                $this->db->transaction(function()use($cid,$sent,$now){
                    $this->db->execute('UPDATE '.$this->table('ads_campaigns').' SET sent_count=COALESCE(sent_count,0)+%d,last_activity_at=%s,updated_at=%s WHERE id=%d',array($sent,$now,$now,$cid));
                    $this->db->execute('UPDATE '.$this->table('ads_campaign_jobs')." SET status='completed',progress_completed=progress_completed+%d,finished_at=%s,updated_at=%s WHERE campaign_id=%d AND status='queued'",array($sent,$now,$now,$cid));
                });
                return array('ok'=>true,'dispatched'=>$sent,'failed'=>$failed);
            }
        }
        throw new AuthFailure(404,'Not found');
    }

    // === INBOUND SMS WEBHOOK + STOP OPT-OUT + AUTO-REPLY / AUTOMATION EXECUTION ===
    public static function dispatchInboundSms($request){
        $reqId=bin2hex(random_bytes(16));
        try{
            $raw=$request->get_body();
            if($raw===''||strlen($raw)>65536){throw new AuthFailure(400,'Invalid inbound SMS payload.');}
            $c=AuthRoutes::context();
            if(!$c['config']->ready()){throw new AuthFailure(503,'Application is not configured.');}
            $secRow=$c['db']->one('system_settings',array('key'=>SmsReceipts::KEY));
            if(!$secRow||empty($secRow['value'])){throw new AuthFailure(503,'Webhook signing is not configured.');}
            $secret=$c['config']->openRuntimeSecret($c['db']->schema()->table('system_settings').'/'.$secRow['id'].'/'.SmsReceipts::KEY,$secRow['value']);
            $ts=trim((string)$request->get_header('x-timestamp'));
            $sig=strtolower(trim((string)$request->get_header('x-signature')));
            $expect=hash_hmac('sha256',$raw.$ts,$secret);
            if($sig===''||!hash_equals($expect,$sig)){throw new AuthFailure(401,'Invalid webhook signature.');}
            $data=json_decode($raw,true);
            if(!is_array($data)){throw new ValidationFailure('Malformed JSON');}
            $pl=$data['payload']??$data;
            $fromRaw=(string)($pl['phoneNumber']??$pl['from']??'');
            $text=Compat::strip((string)($pl['message']??$pl['body']??$pl['text']??''));
            $phone=Phone::clean($fromRaw);
            if($phone===null||$text===''){throw new ValidationFailure('Missing sender phone or message text');}
            $extId=(string)($pl['messageId']??$data['id']??('inb_'.substr(hash('sha256',$raw.$ts),0,24)));
            $svc=new self($c['db'],$c['config']);
            $out=$c['db']->transaction(function()use($c,$svc,$phone,$text,$extId,$raw){
                if($c['db']->one('webhook_events',array('idempotency_key'=>'inb:'.$extId))){return array('ok'=>true,'duplicate'=>true);}
                $now=$svc->records->now();
                $svc->records->create('webhook_events',array('event_type'=>'sms:received','provider'=>'smsgate','provider_event_id'=>$extId,'idempotency_key'=>'inb:'.$extId,'payload'=>$raw,'status'=>'processed','processed_at'=>$now));
                $contact=$c['db']->one('contacts',array('phone_number'=>$phone));
                if(!$contact){$contact=$svc->records->create('contacts',array('phone_number'=>$phone,'first_name'=>$phone,'lead_status'=>'replied'));}
                $upper=strtoupper(trim($text));
                $optedOut=in_array($upper,array('STOP','STOPALL','UNSUBSCRIBE','CANCEL','END','QUIT'),true);
                if($optedOut){
                    $c['db']->update('contacts',array('is_opted_out'=>true,'lead_status'=>'not_interested','updated_at'=>$now),array('id'=>$contact['id']));
                }
                $cls=ReplyClassifier::classify($text);
                $conv=$c['db']->one('conversations',array('contact_id'=>$contact['id'],'channel'=>'sms'));
                if(!$conv){$conv=$svc->records->create('conversations',array('contact_id'=>$contact['id'],'channel'=>'sms','unread_count'=>1,'last_message_at'=>$now,'last_message_preview'=>substr($text,0,200)));}
                else{$c['db']->update('conversations',array('unread_count'=>((int)($conv['unread_count']??0))+1,'last_message_at'=>$now,'last_message_preview'=>substr($text,0,200)),array('id'=>$conv['id']));}
                $msg=$svc->records->create('messages',array('conversation_id'=>(int)$conv['id'],'contact_id'=>(int)$contact['id'],'direction'=>'incoming','channel'=>'sms','body'=>$text,'from_address'=>$phone,'status'=>'received','provider'=>'smsgate','provider_message_id'=>$extId,'idempotency_key'=>'inb_'.substr(hash('sha256',$extId),0,28),'ai_sentiment'=>$cls['sentiment']??null,'ai_intent'=>$cls['intent']??null));
                $c['db']->execute('UPDATE '.Schema::identifier($c['db']->schema()->table('followups'))." SET status='skipped',updated_at=%s WHERE contact_id=%d AND status='pending'",array($now,(int)$contact['id']));
                $autoReplied=false;
                if(!$optedOut){
                    foreach($c['db']->rows('SELECT * FROM '.Schema::identifier($c['db']->schema()->table('auto_reply_rules'))." WHERE COALESCE(is_enabled,0)=1 AND COALESCE(channel,'sms')='sms' ORDER BY priority DESC,id ASC LIMIT 50") as $rr){
                        $rule=$c['db']->decode('auto_reply_rules',$rr);
                        $kws=array_filter(array_map('trim',explode(',',(string)($rule['keywords']??''))));
                        $matched=false;
                        foreach($kws as $kw){
                            if($kw==='') continue;
                            if(($rule['match_type']??'contains')==='exact'&& strcasecmp($text,$kw)===0){$matched=true;break;}
                            if(($rule['match_type']??'contains')!=='exact'&& stripos($text,$kw)!==false){$matched=true;break;}
                        }
                        if($matched&&!empty($rule['reply_body'])){
                            $rtext=str_replace('{{first_name}}',(string)($contact['first_name']??''),(string)$rule['reply_body']);
                            $svc->sendSmsMessage($contact,$rtext);
                            $c['db']->update('auto_reply_rules',array('times_triggered'=>((int)($rule['times_triggered']??0))+1,'last_triggered_at'=>$now,'updated_at'=>$now),array('id'=>$rule['id']));
                            $autoReplied=true;
                            if(!empty($rule['stop_on_match'])){break;}
                        }
                    }
                }
                return array('ok'=>true,'message_id'=>(int)$msg['id'],'contact_id'=>(int)$contact['id'],'opted_out'=>$optedOut,'intent'=>$cls['intent']??null,'auto_replied'=>$autoReplied);
            });
            return AuthRoutes::response($out,200,$reqId);
        }catch(ValidationFailure $e){
            return AuthRoutes::response(AuthRoutes::error(422,$e->getMessage(),$reqId),422,$reqId);
        }catch(AuthFailure $e){
            return AuthRoutes::response(AuthRoutes::error($e->status,$e->getMessage(),$reqId),$e->status,$reqId);
        }catch(\Throwable $e){
            return AuthRoutes::response(AuthRoutes::error(503,'Inbound SMS processing failed.',$reqId),503,$reqId);
        }
    }

    // === CALLS / GATEWAY / NOTIFICATIONS / RETRIES ===
    public function handleCallsAndGateway($path,$method,$request,array $user){
        $now=$this->records->now();
        if(($path==='calls/test'||$path==='settings/callgate/test'||$path==='settings/gateway/test')&&$method==='POST'){
            $gw=$this->activeGatewayCreds();
            if($gw===null){return array('ok'=>false,'online'=>false,'message'=>'No enabled gateway phone configured.');}
            $probe=SmsGate::probe($gw['creds']);
            return array('ok'=>(bool)$probe['online'],'online'=>(bool)$probe['online'],'http'=>$probe['http']);
        }
        if($path==='calls/end'&&$method==='POST'){
            $this->db->transaction(function()use($now){$this->db->execute('UPDATE '.$this->table('call_logs')." SET status='ended',ended_at=%s WHERE status IN ('ringing','started','dialing')",array($now));});
            return array('ok'=>true,'ended'=>true);
        }
        if(preg_match('~^(?:calls|settings/callgate|settings/gateway)/webhooks$~D',$path)&&$method==='GET'){
            return array('items'=>array(),'webhooks'=>array());
        }
        if(preg_match('~^(?:calls/register-webhook|settings/callgate/register-webhook|settings/gateway/register-webhook|settings/gateway/phones/(?:env|-?[0-9]+)/register-webhook|inbox/register-webhook)$~D',$path)&&$method==='POST'){
            $url=function_exists('rest_url')?(string)rest_url('sender-suite/v1/webhooks/smsgateway'):'https://localhost/wp-json/sender-suite/v1/webhooks/smsgateway';
            $gw=$this->activeGatewayCreds();
            if($gw!==null){
                $root=SmsGate::base($gw['creds']['base_url']??null);
                $res=ProviderHttp::request('POST',$root.'/webhooks',array('Authorization'=>'Basic '.base64_encode($gw['creds']['username'].':'.$gw['creds']['password'])),array('url'=>$url,'event'=>'sms:delivered'));
                return array('ok'=>!empty($res['ok']),'webhook_url'=>$url,'http'=>(int)($res['http']??0));
            }
            return array('ok'=>true,'webhook_url'=>$url,'registered_locally'=>true);
        }
        if(preg_match('~^(?:calls|settings/callgate|settings/gateway)/webhooks/([A-Za-z0-9._-]+)$~D',$path,$m)&&$method==='DELETE'){
            return array('ok'=>true,'deleted'=>$m[1]);
        }
        if(preg_match('~^settings/notifications/([A-Za-z0-9_-]+)/test$~D',$path,$m)&&$method==='POST'){
            $prov=$m[1];
            $row=$this->db->one('notification_providers',array('provider'=>$prov));
            if(!$row){throw new AuthFailure(404,'Notification provider is not configured.');}
            if($prov==='browser'){
                $ev=$this->db->transaction(function(){return $this->records->create('notification_events',array('provider'=>'browser','event_type'=>'test','title'=>'Test Notification','body'=>'WordPress Sender Suite notification test succeeded.','is_read'=>false));});
                return array('ok'=>true,'provider'=>'browser','event_id'=>(int)$ev['id']);
            }
            $creds=(new RuntimeSettings($this->db,$this->config))->notificationCredentials($prov);
            if($prov==='pushover'&&!empty($creds['app_token'])&&!empty($creds['user_key'])){
                $r=ProviderHttp::request('POST','https://api.pushover.net/1/messages.json',array(),array('token'=>$creds['app_token'],'user'=>$creds['user_key'],'message'=>'Sender Suite test notification'));
                return array('ok'=>!empty($r['ok']),'provider'=>$prov,'http'=>(int)($r['http']??0));
            }
            return array('ok'=>true,'provider'=>$prov);
        }
        if($path==='notifications/push/test'&&$method==='POST'){
            $subs=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('push_subscriptions'))[0]['n'];
            $this->db->transaction(function(){$this->records->create('notification_events',array('provider'=>'browser','event_type'=>'push_test','title'=>'Push Test','body'=>'Test push recorded.','is_read'=>false));});
            return array('ok'=>true,'subscriptions'=>$subs);
        }
        if(in_array($path,array('inbox/poll-now','inbox/poll-debug','inbox/sync-full'),true)&&$method==='POST'){
            $polled=0;$gw=$this->activeGatewayCreds();
            if($gw!==null){
                foreach($this->db->rows('SELECT id,provider_message_id FROM '.$this->table('messages')." WHERE channel='sms' AND direction='outgoing' AND status IN ('queued','sending','sent') AND provider_message_id IS NOT NULL ORDER BY id DESC LIMIT 20") as $mr){
                    $obs=SmsGate::poll($gw['creds'],(string)$mr['provider_message_id']);
                    if($obs['outcome']==='observed'&&!empty($obs['status'])){
                        $this->db->transaction(function()use($mr,$obs,$now){$this->db->update('messages',array('status'=>$obs['status'],'delivered_at'=>$obs['status']==='delivered'?$now:null),array('id'=>$mr['id']));});
                        $polled++;
                    }
                }
            }
            return array('ok'=>true,'polled'=>$polled);
        }
        if(preg_match('~^send/(scheduled|messages)/(-?[0-9]+)/retry$~D',$path,$m)&&$method==='POST'){
            $kind=$m[1];$id=DataRoutes::integer($m[2],'id',1);
            if($kind==='scheduled'){
                $row=$this->db->one('scheduled_messages',array('id'=>$id));if(!$row){throw new AuthFailure(404,'Scheduled message not found');}
                $this->db->transaction(function()use($id,$now){$this->db->update('scheduled_messages',array('status'=>'pending','error_message'=>null,'updated_at'=>$now),array('id'=>$id));});
                return array('ok'=>true,'id'=>$id,'status'=>'pending');
            }
            $msg=$this->db->one('messages',array('id'=>$id));if(!$msg){throw new AuthFailure(404,'Message not found');}
            $this->db->transaction(function()use($id,$now){$this->db->update('messages',array('status'=>'queued','last_error'=>null,'retry_at'=>$now),array('id'=>$id));});
            return array('ok'=>true,'id'=>$id,'status'=>'queued');
        }
        throw new AuthFailure(404,'Not found');
    }

    // === VALIDATOR JOBS & ENRICHMENT + CAMPAIGN FOLLOWUPS + OVERVIEW ADS DETAIL ===
    public function createValidatorJob($body,array $user){
        if(!is_object($body)){throw new ValidationFailure('Supply a JSON object');}
        $v=new Validator($this->db);
        $recheck=!empty($body->recheck);$deep=!empty($body->deep);
        $ids=array();
        if(isset($body->list_id)&&$body->list_id!==null){
            $lid=DataRoutes::integer($body->list_id,'list_id',1);
            if(!$this->db->one('contact_lists',array('id'=>$lid))){throw new AuthFailure(404,'List not found');}
            foreach($this->db->rows('SELECT contact_id FROM '.$this->table('contact_list_members').' WHERE list_id=%d LIMIT 2000',array($lid)) as $mr){$ids[]=(int)$mr['contact_id'];}
        }elseif(isset($body->contact_ids)&&is_array($body->contact_ids)){
            foreach($body->contact_ids as $cid){$ids[]=(int)$cid;}
        }else{
            foreach($this->db->rows('SELECT id FROM '.$this->table('contacts')." WHERE COALESCE(email,'') LIKE '%@%' LIMIT 1000") as $cr){$ids[]=(int)$cr['id'];}
        }
        $ids=array_values(array_unique(array_filter($ids)));
        $jid='evj_'.bin2hex(random_bytes(8));
        $now=$this->records->now();
        $valid=0;$invalid=0;$risky=0;$skipped=0;
        $this->db->transaction(function()use($jid,$ids,$deep,$recheck,$v,$now,&$valid,&$invalid,&$risky,&$skipped){
            foreach($ids as $cid){
                $c=$this->db->one('contacts',array('id'=>$cid));
                if(!$c||empty($c['email'])){$skipped++;continue;}
                if(!$recheck&&!empty($c['email_verified'])){$valid++;continue;}
                $res=$v->checkEmail($c['email'],$deep);
                $st=$res['verdict']??'unknown';
                if($deep&&$st==='valid'){
                    $dom=substr(strrchr($c['email'],'@'),1);
                    $mx=ProviderHttp::dnsRecords($dom,'MX');
                    if(is_array($mx)&&count($mx)===0&&ProviderHttp::dnsRecords($dom,'TXT')===array()){/* offline neutral */}
                }
                if($st==='valid'){$valid++;$this->db->update('contacts',array('email_verified'=>true,'is_email_undeliverable'=>false,'updated_at'=>$now),array('id'=>$cid));}
                elseif($st==='invalid'){$invalid++;$this->db->update('contacts',array('email_verified'=>false,'is_email_undeliverable'=>true,'updated_at'=>$now),array('id'=>$cid));}
                else{$risky++;}
            }
            $this->records->create('email_verification_jobs',array('id'=>$jid,'status'=>'completed','deep'=>$deep,'recheck'=>$recheck,'contact_ids_json'=>json_encode($ids),'total'=>count($ids),'processed'=>count($ids),'valid'=>$valid,'invalid'=>$invalid,'risky'=>$risky,'unknown'=>0,'skipped'=>$skipped,'engine'=>'native_dns_syntax','smtp_enabled'=>false,'started_at'=>$now,'completed_at'=>$now));
        });
        return $v->job($jid);
    }
    public function enrichContacts($body){
        $enriched=0;$now=$this->records->now();
        $rows=$this->db->rows('SELECT * FROM '.$this->table('contacts')." WHERE (COALESCE(business_name,'')='' OR COALESCE(country,'' )='') LIMIT 200");
        $this->db->transaction(function()use($rows,$now,&$enriched){
            foreach($rows as $r){
                $c=$this->db->decode('contacts',$r);$patch=array();
                if(empty($c['business_name'])&&!empty($c['email'])&&strpos($c['email'],'@')!==false){
                    $dom=strtolower(substr(strrchr($c['email'],'@'),1));
                    if(!in_array($dom,EmailManager::FREEMAIL,true)){
                        $label=ucfirst(explode('.',$dom)[0]);
                        $patch['business_name']=$label;
                    }
                }
                if(empty($c['country'])&&!empty($c['phone_number'])&&strpos($c['phone_number'],'+234')===0){
                    $patch['country']='NG';
                }
                if($patch){$patch['updated_at']=$now;$this->db->update('contacts',$patch,array('id'=>$c['id']));$enriched++;}
            }
        });
        return array('ok'=>true,'enriched'=>$enriched);
    }
    public function runCampaignFollowupRule($fid){
        $rule=$this->db->one('campaign_followups',array('id'=>$fid));
        if(!$rule){throw new AuthFailure(404,'Campaign follow-up rule not found');}
        $sent=0;$stopped=0;$now=$this->records->now();
        $ccs=$this->db->rows('SELECT * FROM '.$this->table('campaign_contacts').' WHERE campaign_id=%d LIMIT 200',array((int)$rule['campaign_id']));
        $this->db->transaction(function()use($rule,$ccs,$now,&$sent,&$stopped){
            foreach($ccs as $ccr){
                $cc=$this->db->decode('campaign_contacts',$ccr);
                $c=$this->db->one('contacts',array('id'=>$cc['contact_id']));
                if(!$c){continue;}
                if((!empty($rule['stop_on_opt_out'])&&!empty($c['is_opted_out']))||(!empty($rule['stop_on_reply'])&&!empty($cc['last_reply_at']))){
                    $stopped++;
                    $this->records->create('campaign_followup_logs',array('followup_id'=>(int)$rule['id'],'campaign_id'=>(int)$rule['campaign_id'],'contact_id'=>(int)$c['id'],'status'=>'stopped','reason'=>'condition_stopped'));
                    continue;
                }
                $txt=str_replace('{{first_name}}',(string)($c['first_name']??''),(string)($rule['message_text']??''));
                if($txt===''){continue;}
                $r=$this->sendSmsMessage($c,$txt,(int)$rule['campaign_id']);
                $this->records->create('campaign_followup_logs',array('followup_id'=>(int)$rule['id'],'campaign_id'=>(int)$rule['campaign_id'],'contact_id'=>(int)$c['id'],'status'=>!empty($r['ok'])?'sent':'failed','message_id'=>$r['message_id']??null,'body_preview'=>substr($txt,0,160)));
                if(!empty($r['ok'])){$sent++;}
            }
            $this->db->update('campaign_followups',array('sent_count'=>((int)($rule['sent_count']??0))+$sent,'stopped_count'=>((int)($rule['stopped_count']??0))+$stopped,'updated_at'=>$now),array('id'=>$rule['id']));
        });
        return array('ok'=>true,'followup_id'=>(int)$fid,'sent'=>$sent,'stopped'=>$stopped);
    }
    public function processCampaignFollowups($cid){
        if(!$this->db->one('campaigns',array('id'=>$cid))){throw new AuthFailure(404,'Campaign not found');}
        $totalSent=0;
        foreach($this->db->rows('SELECT id FROM '.$this->table('campaign_followups').' WHERE campaign_id=%d AND COALESCE(is_active,0)=1 ORDER BY step_order ASC',array($cid)) as $r){
            $res=$this->runCampaignFollowupRule((int)$r['id']);
            $totalSent+=(int)$res['sent'];
        }
        return array('ok'=>true,'campaign_id'=>(int)$cid,'sent'=>$totalSent);
    }
    public function launchClassicCampaign($cid,$action,array $user,$scheduleAt=null){
        $camp=$this->db->one('campaigns',array('id'=>$cid));
        if(!$camp){throw new AuthFailure(404,'Campaign not found');}
        $now=$this->records->now();
        if($action==='schedule'&&$scheduleAt!==null){
            $this->db->transaction(function()use($cid,$scheduleAt,$now){$this->db->update('campaigns',array('status'=>'scheduled','scheduled_start_at'=>$scheduleAt,'scheduled_at'=>$scheduleAt,'updated_at'=>$now),array('id'=>$cid));});
            return (new CampaignReports($this->db))->read($cid);
        }
        $sent=0;$listId=(int)($camp['list_id']??0);
        $tpl=$camp['template_id']?$this->db->one('templates',array('id'=>$camp['template_id'])):null;
        $rawBody=(string)($camp['message_body']?:($tpl?$tpl['body']:''));
        $members=$listId>0?$this->db->rows('SELECT contact_id FROM '.$this->table('contact_list_members').' WHERE list_id=%d LIMIT 500',array($listId)):array();
        $this->db->transaction(function()use($cid,$camp,$members,$rawBody,$now,&$sent){
            foreach($members as $mr){
                $ct=$this->db->one('contacts',array('id'=>(int)$mr['contact_id']));
                if(!$ct||!empty($ct['is_opted_out'])){continue;}
                $txt=str_replace('{{first_name}}',(string)($ct['first_name']??''),$rawBody);
                if($txt===''){continue;}
                $r=$this->sendSmsMessage($ct,$txt,$cid,$camp['gateway_setting_id']?(int)$camp['gateway_setting_id']:null);
                if(!$this->db->one('campaign_contacts',array('campaign_id'=>$cid,'contact_id'=>$ct['id']))){
                    $this->records->create('campaign_contacts',array('campaign_id'=>$cid,'contact_id'=>(int)$ct['id'],'status'=>!empty($r['ok'])?'sent':'failed','messages_sent'=>!empty($r['ok'])?1:0,'last_message_id'=>$r['message_id']??null,'last_message_at'=>$now));
                }
                if(!empty($r['ok'])){$sent++;}
            }
            $this->db->update('campaigns',array('status'=>'running','started_at'=>$camp['started_at']?:$now,'messages_sent'=>((int)($camp['messages_sent']??0))+$sent,'updated_at'=>$now),array('id'=>$cid));
        });
        return (new CampaignReports($this->db))->read($cid);
    }
    public function sendCalendarNotice($meetingId,$kind){
        $cal=new Calendar($this->db);
        $m=$cal->read($meetingId);
        $prev=$cal->preview($meetingId,array('kind'=>$kind==='send-reminder'?'reminder':'invite'));
        $sent=0;
        foreach(($m['attendees']??array()) as $att){
            $ct=$this->db->one('contacts',array('id'=>(int)$att['contact_id']));
            if($ct&&empty($ct['is_opted_out'])){
                $r=$this->db->transaction(function()use($ct,$prev){return $this->sendSmsMessage($ct,(string)$prev['body']);});
                if(!empty($r['ok'])){$sent++;}
            }
        }
        return array('ok'=>true,'meeting_id'=>(int)$meetingId,'sent'=>$sent);
    }
    public function sendFollowupNow($fid){
        $f=$this->db->one('followups',array('id'=>$fid));
        if(!$f){throw new AuthFailure(404,'Follow-up not found');}
        if($f['status']!=='pending'){throw new AuthFailure(409,'Only pending follow-ups can be sent now.');}
        $ct=$this->db->one('contacts',array('id'=>$f['contact_id']));
        if(!$ct){throw new AuthFailure(404,'Contact not found');}
        $now=$this->records->now();
        $txt=(string)($f['message_text']??'');
        if(($f['channel']??'sms')==='email'){
            $et=new EmailTransport($this->db,$this->config);
            $r=$et->sendEmail(array('account_id'=>$f['email_account_id']?(int)$f['email_account_id']:null,'contact'=>$ct,'to'=>(string)$ct['email'],'subject'=>(string)($f['subject']??'Follow-up'),'body'=>$txt));
            $this->db->transaction(function()use($fid,$r,$now){$this->db->update('followups',array('status'=>'sent','executed_at'=>$now,'message_id'=>$r['message_id'],'updated_at'=>$now),array('id'=>$fid));});
            return array('ok'=>true,'status'=>'sent','message_id'=>$r['message_id']);
        }
        $r=$this->db->transaction(function()use($fid,$ct,$txt,$f,$now){
            $res=$this->sendSmsMessage($ct,$txt,$f['campaign_id']?(int)$f['campaign_id']:null);
            $this->db->update('followups',array('status'=>!empty($res['ok'])?'sent':'failed','executed_at'=>$now,'message_id'=>$res['message_id']??null,'updated_at'=>$now),array('id'=>$fid));
            return $res;
        });
        return array('ok'=>!empty($r['ok']),'status'=>!empty($r['ok'])?'sent':'failed','message_id'=>$r['message_id']??null);
    }
}
