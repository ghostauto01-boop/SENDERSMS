<?php
namespace SenderSuite;

/** Stored SMS/Email Ads Manager CRUD, analytics, preflight validation and simulation; live dispatch workers remain gated. */
final class AdsManager {
    const CHANNELS=array('sms','email');
    private $db;private $config;private $records;
    public function __construct(Database $db,Config $config=null,callable $clock=null){$this->db=$db;$this->config=$config;$this->records=new Records($db,$clock);}
    private function table($n){return Schema::identifier($this->db->schema()->table($n));}
    public static function register(){
        register_rest_route('sender-suite/v1','/ads/r/(?P<assignment_id>-?[0-9]+)',array('methods'=>'GET','permission_callback'=>'__return_true','callback'=>array(CampaignWorker::class,'dispatchClickRedirect')));
    }
    public static function publicRedirect($request=null){
        return $request!==null?CampaignWorker::dispatchClickRedirect($request):AuthRoutes::response(AuthRoutes::error(400,'Missing tracking assignment.',bin2hex(random_bytes(16))),400,bin2hex(random_bytes(16)));
    }
    public static function definitions(){
        return array(
            '/ads/overview'=>array('GET'),
            '/ads/reference'=>array('GET'),
            '/ads/activity'=>array('GET'),
            '/ads/search'=>array('GET'),
            '/ads/export/(?P<kind>[a-z_]+)'=>array('GET'),
            '/ads/campaigns'=>array('GET','POST'),
            '/ads/campaigns/bulk'=>array('POST'),
            '/ads/campaigns/(?P<campaign_id>-?[0-9]+)'=>array('GET','PATCH','DELETE'),
            '/ads/campaigns/(?P<campaign_id>-?[0-9]+)/(?P<op>validate|simulate|launch|pause|resume|complete|archive|duplicate|dispatch|contacts|rebuild-audience|optimize)'=>array('POST'),
            '/ads/campaigns/(?P<campaign_id>-?[0-9]+)/(?P<subread>audience|jobs|send-logs|analytics|optimization|activity)'=>array('GET'),
            '/ads/campaigns/(?P<campaign_id>-?[0-9]+)/jobs/retry'=>array('POST'),
            '/ads/campaigns/(?P<campaign_id>-?[0-9]+)/audience/bulk-remove'=>array('POST'),
            '/ads/campaigns/(?P<campaign_id>-?[0-9]+)/audience/(?P<assignment_id>-?[0-9]+)'=>array('DELETE'),
            '/ads/campaigns/(?P<campaign_id>-?[0-9]+)/sets'=>array('GET','POST'),
            '/ads/sets/preview'=>array('POST'),
            '/ads/sets/(?P<set_id>-?[0-9]+)'=>array('PATCH','DELETE'),
            '/ads/sets/(?P<set_id>-?[0-9]+)/preview'=>array('GET'),
            '/ads/sets/(?P<set_id>-?[0-9]+)/(?P<setop>duplicate|pause-losers)'=>array('POST'),
            '/ads/sets/(?P<set_id>-?[0-9]+)/creatives'=>array('GET','POST'),
            '/ads/creatives/bulk'=>array('POST'),
            '/ads/creatives/(?P<creative_id>-?[0-9]+)'=>array('PATCH','DELETE'),
            '/ads/creatives/(?P<creative_id>-?[0-9]+)/(?P<cread>versions|analytics)'=>array('GET'),
            '/ads/creatives/(?P<creative_id>-?[0-9]+)/(?P<cop>duplicate|promote)'=>array('POST'),
            '/ads/audiences'=>array('GET','POST'),
            '/ads/audiences/(?P<audience_id>-?[0-9]+)'=>array('GET','PATCH','DELETE'),
            '/ads/audiences/(?P<audience_id>-?[0-9]+)/preview'=>array('GET'),
            '/ads/audiences/(?P<audience_id>-?[0-9]+)/(?P<aop>duplicate|attach)'=>array('POST'),
            '/ads/track/click'=>array('POST'),
            '/ads/campaigns/(?P<campaign_id>-?[0-9]+)/followup-steps'=>array('GET','POST'),
            '/ads/campaigns/(?P<campaign_id>-?[0-9]+)/followup-steps/(?P<step_id>-?[0-9]+)'=>array('PATCH','DELETE'),
            '/ads/followup-steps/(?P<step_id>-?[0-9]+)'=>array('GET','PATCH','DELETE'),
            '/ads/followup-templates'=>array('GET','POST'),
            '/ads/followup-templates/(?P<template_id>-?[0-9]+)'=>array('GET','PATCH','DELETE'),
            '/ads/followups'=>array('GET','POST'),
            '/ads/followups/process'=>array('POST'),
            '/ads/followups/(?P<task_id>-?[0-9]+)/(?P<fop>[a-z_]+)'=>array('POST'),
            '/ads/calendar'=>array('GET','POST'),
            '/ads/calendar/(?P<event_id>-?[0-9]+)'=>array('PATCH','DELETE'),
            '/ads/suppression'=>array('GET','POST'),
            '/ads/suppression/(?P<entry_id>-?[0-9]+)'=>array('DELETE'),
            '/ads/contacts/bulk'=>array('POST'),
            '/ads/contacts/(?P<contact_id>-?[0-9]+)/action'=>array('POST'),
            '/ads/contacts/(?P<contact_id>-?[0-9]+)/timeline'=>array('GET')
        );
    }
    private static function rate($p,$w){return $w>0?round(($p/$w)*100,1):0.0;}
    private static function jlist($v){if(is_array($v)){return array_values($v);}if(is_string($v)&&$v!==''){$d=json_decode($v,true);if(is_array($d)){return array_values($d);}}return array();}
    private static function score($objective,array $st){
        $o=(string)($objective?:'replies');
        if($o==='website_visits'){return round($st['click_rate']*0.7+$st['delivery_rate']*0.3,1);}
        return round($st['reply_rate']*0.7+$st['delivery_rate']*0.3,1);
    }
    private function logActivity($action,$campaignId=null,$entityType=null,$entityId=null,$actor=null,$detail=null){
        $this->records->create('ads_activity_logs',array('campaign_id'=>$campaignId,'entity_type'=>$entityType,'entity_id'=>$entityId,'action'=>$action,'detail'=>$detail,'actor'=>$actor));
    }
    private function countsWhere($sql,array $args,$campaignId=null,$setId=null,$creativeId=null){
        $agg=$this->db->rows("SELECT COUNT(*) AS assigned,SUM(CASE WHEN sent_at IS NOT NULL THEN 1 ELSE 0 END) AS sent,SUM(CASE WHEN COALESCE(delivery_status,'') IN ('delivered','simulated') THEN 1 ELSE 0 END) AS delivered,SUM(CASE WHEN send_status='failed' THEN 1 ELSE 0 END) AS failed,SUM(CASE WHEN send_status='pending' THEN 1 ELSE 0 END) AS pending,SUM(CASE WHEN send_status='skipped' THEN 1 ELSE 0 END) AS skipped,SUM(CASE WHEN COALESCE(reply_status,'')<>'' AND reply_status<>'unsubscribe' THEN 1 ELSE 0 END) AS replies,SUM(CASE WHEN reply_status='positive' THEN 1 ELSE 0 END) AS positive_replies,SUM(CASE WHEN reply_status='negative' THEN 1 ELSE 0 END) AS negative_replies,SUM(CASE WHEN conversion_status='converted' THEN 1 ELSE 0 END) AS conversions,SUM(CASE WHEN skip_reason IN ('opted_out','suppressed') THEN 1 ELSE 0 END) AS opt_outs FROM ".$this->table('ads_assignments').' WHERE '.$sql,$args)[0];
        $assigned=(int)($agg['assigned']??0);$sent=(int)($agg['sent']??0);$del=(int)($agg['delivered']??0);$fail=(int)($agg['failed']??0);$pend=(int)($agg['pending']??0);$skip=(int)($agg['skipped']??0);$rep=(int)($agg['replies']??0);$pos=(int)($agg['positive_replies']??0);$neg=(int)($agg['negative_replies']??0);$conv=(int)($agg['conversions']??0);$opt=(int)($agg['opt_outs']??0);
        $evW=array("event_type='LINK_CLICKED'");$evA=array();
        if($campaignId!==null){$evW[]='campaign_id=%d';$evA[]=$campaignId;}
        if($setId!==null){$evW[]='set_id=%d';$evA[]=$setId;}
        if($creativeId!==null){$evW[]='creative_id=%d';$evA[]=$creativeId;}
        $clicks=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('ads_events').' WHERE '.implode(' AND ',$evW),$evA)[0]['n'];
        $opens=$rep+$clicks>$sent?$sent:max($rep,$clicks);
        $base=$del>0?$del:$sent;
        return array('assigned'=>$assigned,'sent'=>$sent,'delivered'=>$del,'failed'=>$fail,'pending'=>$pend,'skipped'=>$skip,'replies'=>$rep,'positive_replies'=>$pos,'negative_replies'=>$neg,'conversions'=>$conv,'opt_outs'=>$opt,'clicks'=>$clicks,'opens'=>$opens,'delivery_rate'=>self::rate($del,$sent),'reply_rate'=>self::rate($rep,$sent),'positive_reply_rate'=>self::rate($pos,$sent),'negative_reply_rate'=>self::rate($neg,$sent),'conversion_rate'=>self::rate($conv,$sent),'opt_out_rate'=>self::rate($opt,$assigned>0?$assigned:$sent),'click_rate'=>self::rate($clicks,$base),'open_rate'=>self::rate($opens,$base));
    }
    private function campaignOut(array $c){
        return array(
            'id'=>(int)$c['id'],'name'=>$c['name'],'description'=>$c['description'],'objective'=>$c['objective']??'replies',
            'status'=>$c['status']??'draft','channel'=>$c['channel']??'sms','email_account_id'=>$c['email_account_id']!==null?(int)$c['email_account_id']:null,
            'fallback_email_account_id'=>$c['fallback_email_account_id']!==null?(int)$c['fallback_email_account_id']:null,
            'email_account_ids'=>self::jlist($c['email_account_ids']??null),'sender_distribution'=>$c['sender_distribution']??'round_robin',
            'subject'=>$c['subject'],'track_opens'=>(bool)($c['track_opens']??true),'track_clicks'=>(bool)($c['track_clicks']??true),
            'plain_personal_email'=>(bool)($c['plain_personal_email']??false),'daily_limit'=>$c['daily_limit']!==null?(int)$c['daily_limit']:null,
            'total_limit'=>$c['total_limit']!==null?(int)$c['total_limit']:null,'start_date'=>Records::iso($c['start_date']??null),'end_date'=>Records::iso($c['end_date']??null),
            'send_start_hour'=>$c['send_start_hour']!==null?(int)$c['send_start_hour']:9,'send_end_hour'=>$c['send_end_hour']!==null?(int)$c['send_end_hour']:18,
            'send_days'=>$c['send_days']??'0,1,2,3,4,5,6','timezone_name'=>$c['timezone_name']??'UTC',
            'drip_mode'=>$c['drip_mode']??'immediate','drip_batch_size'=>(int)($c['drip_batch_size']??25),'drip_interval_minutes'=>(int)($c['drip_interval_minutes']??5),
            'pacing'=>$c['pacing']??'even','continuous'=>(bool)($c['continuous']??false),'always_on'=>(bool)($c['always_on']??false),
            'max_per_contact_per_day'=>(int)($c['max_per_contact_per_day']??1),'max_per_contact_per_week'=>(int)($c['max_per_contact_per_week']??3),
            'optimization_mode'=>$c['optimization_mode']??'manual','auto_optimize'=>(bool)($c['auto_optimize']??false),
            'optimize_metric'=>$c['optimize_metric']??'reply_rate','optimize_min_sends'=>(int)($c['optimize_min_sends']??30),
            'optimize_min_gap_pct'=>(float)($c['optimize_min_gap_pct']??15.0),'optimize_action'=>$c['optimize_action']??'rebalance',
            'test_mode'=>(bool)($c['test_mode']??false),'priority'=>(int)($c['priority']??5),
            'paused_reason'=>$c['paused_reason'],'paused_at'=>Records::iso($c['paused_at']??null),'sent_count'=>(int)($c['sent_count']??0),
            'last_error'=>$c['last_error'],'created_at'=>Records::iso($c['created_at']??null),'updated_at'=>Records::iso($c['updated_at']??null)
        );
    }
    private function setOut(array $s){
        return array('id'=>(int)$s['id'],'campaign_id'=>(int)$s['campaign_id'],'name'=>$s['name'],'status'=>$s['status']??'active','list_ids'=>self::jlist($s['list_ids']??null),'contact_ids'=>self::jlist($s['contact_ids']??null),'audience_id'=>$s['audience_id']!==null?(int)$s['audience_id']:null,'include_tags'=>self::jlist($s['include_tags']??null),'exclude_tags'=>self::jlist($s['exclude_tags']??null),'include_statuses'=>self::jlist($s['include_statuses']??null),'exclude_statuses'=>self::jlist($s['exclude_statuses']??null),'city'=>$s['city'],'state'=>$s['state'],'industry'=>$s['industry'],'activity_filter'=>$s['activity_filter'],'exclude_campaign_ids'=>self::jlist($s['exclude_campaign_ids']??null),'daily_limit'=>$s['daily_limit']!==null?(int)$s['daily_limit']:null,'split_mode'=>$s['split_mode']??'even','auto_optimize'=>(bool)($s['auto_optimize']??false),'created_at'=>Records::iso($s['created_at']??null),'updated_at'=>Records::iso($s['updated_at']??null));
    }
    private function creativeOut(array $cr){
        return array('id'=>(int)$cr['id'],'set_id'=>(int)$cr['set_id'],'campaign_id'=>(int)$cr['campaign_id'],'name'=>$cr['name'],'status'=>$cr['status']??'active','body'=>$cr['body'],'cta'=>$cr['cta'],'tracking_link'=>$cr['tracking_link'],'subject'=>$cr['subject'],'html_body'=>$cr['html_body'],'email_account_id'=>$cr['email_account_id']!==null?(int)$cr['email_account_id']:null,'template_id'=>$cr['template_id']!==null?(int)$cr['template_id']:null,'allocation'=>(int)($cr['allocation']??100),'send_quota'=>$cr['send_quota']!==null?(int)$cr['send_quota']:null,'current_version'=>(int)($cr['current_version']??1),'created_at'=>Records::iso($cr['created_at']??null),'updated_at'=>Records::iso($cr['updated_at']??null));
    }
    private function audienceOut(array $a){
        return array('id'=>(int)$a['id'],'name'=>$a['name'],'description'=>$a['description'],'list_ids'=>self::jlist($a['list_ids']??null),'contact_ids'=>self::jlist($a['contact_ids']??null),'include_tags'=>self::jlist($a['include_tags']??null),'exclude_tags'=>self::jlist($a['exclude_tags']??null),'include_statuses'=>self::jlist($a['include_statuses']??null),'exclude_statuses'=>self::jlist($a['exclude_statuses']??null),'city'=>$a['city'],'state'=>$a['state'],'industry'=>$a['industry'],'activity_filter'=>$a['activity_filter'],'exclude_campaign_ids'=>self::jlist($a['exclude_campaign_ids']??null),'created_at'=>Records::iso($a['created_at']??null),'updated_at'=>Records::iso($a['updated_at']??null));
    }
    private function stepOut(array $st){
        return array('id'=>(int)$st['id'],'campaign_id'=>(int)$st['campaign_id'],'step_order'=>(int)$st['step_order'],'name'=>$st['name'],'wait_hours'=>(int)$st['wait_hours'],'delay_hours'=>(int)$st['wait_hours'],'condition'=>$st['condition'],'action'=>$st['action'],'body'=>$st['body'],'subject'=>$st['subject'],'template_id'=>$st['template_id']!==null?(int)$st['template_id']:null,'action_value'=>$st['action_value'],'is_active'=>(bool)$st['is_active'],'created_at'=>Records::iso($st['created_at']??null),'updated_at'=>Records::iso($st['updated_at']??null));
    }
    private function tplOut(array $t){
        return array('id'=>(int)$t['id'],'name'=>$t['name'],'description'=>$t['description'],'channel'=>$t['channel']??'sms','subject'=>$t['subject'],'body'=>$t['body'],'condition'=>$t['condition']??'no_reply','action'=>$t['action']??'send_sms','delay_hours'=>(int)($t['delay_hours']??48),'is_active'=>(bool)$t['is_active'],'usage_count'=>(int)($t['usage_count']??0),'created_at'=>Records::iso($t['created_at']??null),'updated_at'=>Records::iso($t['updated_at']??null));
    }
    private function campaignOr404($id){$c=$this->db->one('ads_campaigns',array('id'=>$id));if(!$c){throw new AuthFailure(404,'Ads campaign not found');}return $c;}
    private function setOr404($id){$s=$this->db->one('ads_sets',array('id'=>$id));if(!$s){throw new AuthFailure(404,'Ad set not found');}return $s;}
    private function creativeOr404($id){$c=$this->db->one('ads_creatives',array('id'=>$id));if(!$c||!empty($c['is_deleted'])){throw new AuthFailure(404,'Ad creative not found');}return $c;}
    private function audienceOr404($id){$a=$this->db->one('ads_audiences',array('id'=>$id));if(!$a){throw new AuthFailure(404,'Audience not found');}return $a;}
    private function valChannel($ch){$v=strtolower(Compat::strip((string)($ch?:'sms')));if(!in_array($v,self::CHANNELS,true)){throw new ValidationFailure('channel must be sms or email','channel');}return $v;}
    public function overview(array $q){
        $chRaw=DataRoutes::text($q,'channel',null,null,20);
        $ch=($chRaw!==null&&$chRaw!==''&&$chRaw!=='all')?$this->valChannel($chRaw):null;
        $cRows=$ch!==null?$this->db->rows('SELECT * FROM '.$this->table('ads_campaigns').' WHERE channel=%s',array($ch)):$this->db->rows('SELECT * FROM '.$this->table('ads_campaigns'));
        $active=0;$cIds=array();foreach($cRows as $cr){if(($cr['status']??'')==='active'){$active++;}$cIds[]=(int)$cr['id'];}
        if($ch!==null&&!$cIds){$totals=$this->countsWhere('1=0',array());}
        elseif($ch!==null){$ph=implode(',',array_fill(0,count($cIds),'%d'));$totals=$this->countsWhere('campaign_id IN ('.$ph.')',$cIds);}
        else{$totals=$this->countsWhere('1=1',array());}
        $now=$this->records->now();
        $fuDue=(int)$this->db->rows("SELECT COUNT(*) AS n FROM ".$this->table('ads_followup_tasks')." WHERE status='pending' AND due_at<=%s",array($now))[0]['n'];
        $meetings=(int)$this->db->rows("SELECT COUNT(*) AS n FROM ".$this->table('ads_calendar_events')." WHERE event_type='meeting' AND status='scheduled'")[0]['n'];
        $suppressed=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table($ch==='email'?'email_suppressions':'suppression_entries'))[0]['n'];
        $nowDt=new \DateTimeImmutable($now,new \DateTimeZone('UTC'));
        $sinceDt=$nowDt->modify('-13 days');$series=array();
        for($i=0;$i<14;$i++){$d=$sinceDt->modify('+'.$i.' days')->format('Y-m-d');$series[$d]=array('date'=>$d,'sent'=>0,'replies'=>0);}
        foreach($this->db->rows("SELECT SUBSTR(created_at,1,10) AS d,event_type,COUNT(*) AS n FROM ".$this->table('ads_events')." WHERE event_type IN ('MESSAGE_SENT','REPLY_RECEIVED') AND created_at>=%s GROUP BY SUBSTR(created_at,1,10),event_type",array($sinceDt->format('Y-m-d 00:00:00.000000'))) as $er){
            if(isset($series[$er['d']])){if($er['event_type']==='MESSAGE_SENT'){$series[$er['d']]['sent']+=(int)$er['n'];}else{$series[$er['d']]['replies']+=(int)$er['n'];}}
        }
        $senders=(new EmailManager($this->db,$this->config))->listSenders()['items'];
        return array('channel'=>$ch?:'all','active_campaigns'=>$active,'total_campaigns'=>count($cRows),'totals'=>$totals,'followups_due'=>$fuDue,'meetings'=>$meetings,'suppressed'=>$suppressed,'credits_used'=>$totals['sent'],'series'=>array_values($series),'email_accounts'=>$senders);
    }
    public function listCampaigns(array $q){
        $page=DataRoutes::integer($q['page']??1,'page',1,100000);$per=DataRoutes::integer($q['per_page']??50,'per_page',1,200);
        $where=array('1=1');$args=array();
        if(isset($q['channel'])&&$q['channel']!==''&&$q['channel']!=='all'){$where[]='channel=%s';$args[]=$this->valChannel($q['channel']);}
        if(isset($q['status'])&&$q['status']!==''){$where[]='status=%s';$args[]=DataRoutes::text($q,'status','',null,30);}
        if(isset($q['search'])&&Compat::strip((string)$q['search'])!==''){$where[]='LOWER(name) LIKE %s';$args[]='%'.strtolower(DataRoutes::like(Compat::strip(DataRoutes::text($q,'search','',null,200)))).'%';}
        $wSql=implode(' AND ',$where);
        $total=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('ads_campaigns').' WHERE '.$wSql,$args)[0]['n'];
        $items=array();
        foreach($this->db->rows('SELECT * FROM '.$this->table('ads_campaigns').' WHERE '.$wSql.' ORDER BY updated_at DESC,id DESC LIMIT %d OFFSET %d',array_merge($args,array($per,($page-1)*$per))) as $r){
            $c=$this->db->decode('ads_campaigns',$r);
            $st=$this->countsWhere('campaign_id=%d',array($c['id']),(int)$c['id']);
            $items[]=array_merge($this->campaignOut($c),array('stats'=>$st,'state'=>$c['status']??'draft','score'=>self::score($c['objective']??'replies',$st)));
        }
        return array('total'=>$total,'items'=>$items);
    }
    public function createCampaign($body,array $user,&$status){
        if(!is_object($body)){throw new ValidationFailure('Supply a JSON object');}
        $this->db->requireTransaction();
        $name=Compat::strip(DataRoutes::text(array('name'=>$body->name??''),'name','',1,255));
        $ch=$this->valChannel($body->channel??'sms');
        $row=array(
            'name'=>$name,'description'=>isset($body->description)&&$body->description!==null?DataRoutes::text(array('description'=>$body->description),'description','',null,5000):null,
            'objective'=>Compat::strip(DataRoutes::text(array('objective'=>$body->objective??'replies'),'objective','replies',null,50))?:'replies',
            'status'=>'draft','channel'=>$ch,'owner_id'=>isset($user['id'])?(int)$user['id']:null,
            'email_account_id'=>isset($body->email_account_id)&&$body->email_account_id!==null?DataRoutes::integer($body->email_account_id,'email_account_id'):null,
            'subject'=>isset($body->subject)&&$body->subject!==null?DataRoutes::text(array('subject'=>$body->subject),'subject','',null,998):null,
            'daily_limit'=>isset($body->daily_limit)&&$body->daily_limit!==null?DataRoutes::integer($body->daily_limit,'daily_limit',1,1000000):null,
            'total_limit'=>isset($body->total_limit)&&$body->total_limit!==null?DataRoutes::integer($body->total_limit,'total_limit',1,10000000):null,
            'send_start_hour'=>isset($body->send_start_hour)?DataRoutes::integer($body->send_start_hour,'send_start_hour',0,23):9,
            'send_end_hour'=>isset($body->send_end_hour)?DataRoutes::integer($body->send_end_hour,'send_end_hour',0,24):18,
            'send_days'=>isset($body->send_days)&&$body->send_days!==null?substr(Compat::strip((string)$body->send_days),0,30):'0,1,2,3,4,5,6',
            'timezone_name'=>isset($body->timezone_name)&&$body->timezone_name!==null?substr(Compat::strip((string)$body->timezone_name),0,120):'UTC',
            'drip_mode'=>isset($body->drip_mode)?DataRoutes::text(array('drip_mode'=>$body->drip_mode),'drip_mode','immediate',null,30):'immediate',
            'drip_batch_size'=>isset($body->drip_batch_size)?DataRoutes::integer($body->drip_batch_size,'drip_batch_size',1,10000):25,
            'drip_interval_minutes'=>isset($body->drip_interval_minutes)?DataRoutes::integer($body->drip_interval_minutes,'drip_interval_minutes',1,1440):5,
            'track_opens'=>isset($body->track_opens)?DataRoutes::boolean($body->track_opens,'track_opens'):true,
            'track_clicks'=>isset($body->track_clicks)?DataRoutes::boolean($body->track_clicks,'track_clicks'):true,
            'auto_optimize'=>isset($body->auto_optimize)?DataRoutes::boolean($body->auto_optimize,'auto_optimize'):false,
            'test_mode'=>isset($body->test_mode)?DataRoutes::boolean($body->test_mode,'test_mode'):false,
            'state_changed_at'=>$this->records->now()
        );
        if($ch==='email'){
            $acct=$row['email_account_id']!==null?$this->db->one('email_accounts',array('id'=>$row['email_account_id'])):$this->db->one('email_accounts',array('is_default'=>1));
            if(!$acct||!$acct['is_active']){throw new AuthFailure(400,'No active email sender is configured. Add and enable a sender first.');}
            $row['email_account_id']=(int)$acct['id'];
        }
        $created=$this->records->create('ads_campaigns',$row);
        $this->records->create('ads_campaign_state_transitions',array('campaign_id'=>$created['id'],'from_state'=>null,'to_state'=>'draft','reason'=>'Campaign created','actor'=>$user['username']??'admin'));
        $this->logActivity('campaign_created',$created['id'],'campaign',$created['id'],$user['username']??'admin',$ch.': '.$name);
        $status=201;
        return $this->campaignOut($created);
    }
    public function getCampaign($id){
        $c=$this->campaignOr404($id);
        $sets=array();foreach($this->db->rows('SELECT * FROM '.$this->table('ads_sets').' WHERE campaign_id=%d ORDER BY id ASC',array($id)) as $sr){$sets[]=$this->setOut($this->db->decode('ads_sets',$sr));}
        $creatives=array();foreach($this->db->rows('SELECT * FROM '.$this->table('ads_creatives').' WHERE campaign_id=%d AND is_deleted=0 ORDER BY id ASC',array($id)) as $cr){$creatives[]=$this->creativeOut($this->db->decode('ads_creatives',$cr));}
        $steps=array();foreach($this->db->rows('SELECT * FROM '.$this->table('ads_followup_steps').' WHERE campaign_id=%d AND deleted_at IS NULL ORDER BY step_order ASC,id ASC',array($id)) as $fr){$steps[]=$this->stepOut($this->db->decode('ads_followup_steps',$fr));}
        return array_merge($this->campaignOut($c),array('state'=>$c['status']??'draft','sets'=>$sets,'creatives'=>$creatives,'followup_steps'=>$steps));
    }
    public function updateCampaign($id,$body,array $user){
        if(!is_object($body)){throw new ValidationFailure('Supply a JSON object');}
        $this->db->requireTransaction();
        $c=$this->campaignOr404($id);$patch=array('updated_at'=>$this->records->now());
        foreach(array('name'=>255,'description'=>5000,'objective'=>50,'subject'=>998,'drip_mode'=>30,'pacing'=>30,'optimization_mode'=>30,'optimize_metric'=>40,'optimize_action'=>40,'sender_distribution'=>40) as $tf=>$cap){
            if(property_exists($body,$tf)){$patch[$tf]=$body->$tf===null?null:DataRoutes::text(array($tf=>$body->$tf),$tf,'',null,$cap);}
        }
        foreach(array('email_account_id','fallback_email_account_id','daily_limit','total_limit','send_start_hour','send_end_hour','drip_batch_size','drip_interval_minutes','max_per_contact_per_day','max_per_contact_per_week','optimize_min_sends','priority') as $if){
            if(property_exists($body,$if)){$patch[$if]=$body->$if===null?null:DataRoutes::integer($body->$if,$if);}
        }
        foreach(array('track_opens','track_clicks','plain_personal_email','continuous','always_on','auto_optimize','test_mode') as $bf){
            if(property_exists($body,$bf)&&$body->$bf!==null){$patch[$bf]=DataRoutes::boolean($body->$bf,$bf);}
        }
        if(property_exists($body,'timezone_name')){$patch['timezone_name']=$body->timezone_name===null?'UTC':substr(Compat::strip((string)$body->timezone_name),0,120);}
        if(property_exists($body,'send_days')){$patch['send_days']=$body->send_days===null?'0,1,2,3,4,5,6':substr(Compat::strip((string)$body->send_days),0,30);}
        $this->db->update('ads_campaigns',$patch,array('id'=>$id));
        $this->logActivity('campaign_updated',$id,'campaign',$id,$user['username']??'admin',$patch['name']??$c['name']);
        return $this->campaignOut($this->campaignOr404($id));
    }
    public function deleteCampaign($id){
        $this->db->requireTransaction();
        $this->campaignOr404($id);
        foreach(array('ads_assignments','ads_events','ads_followup_tasks','ads_followup_steps','ads_creatives','ads_sets','ads_campaign_jobs','ads_campaign_state_transitions','ads_activity_logs') as $tbl){
            $this->db->delete($tbl,array('campaign_id'=>$id));
        }
        $this->db->delete('ads_campaigns',array('id'=>$id));
        return null;
    }
    private function resolveTargetingContacts(array $spec,$channel='sms'){
        $listIds=array_values(array_filter(array_map('intval',self::jlist($spec['list_ids']??null)),function($v){return $v>0;}));
        $contactIds=array_values(array_filter(array_map('intval',self::jlist($spec['contact_ids']??null)),function($v){return $v>0;}));
        $clauses=array();$args=array();
        if($listIds){$ph=implode(',',array_fill(0,count($listIds),'%d'));$clauses[]='id IN (SELECT contact_id FROM '.$this->table('contact_list_members').' WHERE list_id IN ('.$ph.'))';$args=array_merge($args,$listIds);}
        if($contactIds){$ph=implode(',',array_fill(0,count($contactIds),'%d'));$clauses[]='id IN ('.$ph.')';$args=array_merge($args,$contactIds);}
        $where=$clauses?('('.implode(' OR ',$clauses).')'):'1=1';
        if(!empty($spec['city'])){$where.=' AND LOWER(COALESCE(city,\'\'))=LOWER(%s)';$args[]=(string)$spec['city'];}
        if(!empty($spec['state'])){$where.=' AND LOWER(COALESCE(state,\'\'))=LOWER(%s)';$args[]=(string)$spec['state'];}
        if(!empty($spec['industry'])){$where.=' AND LOWER(COALESCE(industry,\'\'))=LOWER(%s)';$args[]=(string)$spec['industry'];}
        $rows=$this->db->rows('SELECT id,first_name,last_name,phone_number,email,is_opted_out,is_email_opted_out,is_email_undeliverable,lead_status FROM '.$this->table('contacts').' WHERE '.$where.' ORDER BY id ASC LIMIT 5000',$args);
        $matched=count($rows);$eligible=array();$skipped=array('opted_out'=>0,'missing_channel'=>0);
        foreach($rows as $r){
            if($channel==='email'){
                if(!empty($r['is_email_opted_out'])||!empty($r['is_email_undeliverable'])){$skipped['opted_out']++;continue;}
                if(empty($r['email'])||strpos((string)$r['email'],'@')===false){$skipped['missing_channel']++;continue;}
            }else{
                if(!empty($r['is_opted_out'])){$skipped['opted_out']++;continue;}
                if(empty($r['phone_number'])){$skipped['missing_channel']++;continue;}
            }
            $eligible[]=$r;
        }
        return array('matched'=>$matched,'eligible'=>$eligible,'skipped'=>$skipped);
    }
    public function validateCampaign($id){
        $this->db->requireTransaction();
        $c=$this->campaignOr404($id);
        $errors=array();$warnings=array();$checks=array();
        $add=function($k,$pass,$msg,$req=true)use(&$errors,&$warnings,&$checks){$checks[$k]=array('pass'=>(bool)$pass,'message'=>$msg);if(!$pass){if($req){$errors[]=$msg;}else{$warnings[]=$msg;}}};
        $add('campaign_name',Compat::strip((string)$c['name'])!=='','Campaign name is set.');
        $sets=$this->db->rows('SELECT * FROM '.$this->table('ads_sets').' WHERE campaign_id=%d AND status=\'active\'',array($id));
        $add('ad_sets',count($sets)>0,count($sets)>0?(count($sets).' active ad set(s) configured.'):'Add at least one active ad set.');
        $creativesPerSet=array();$totalEligible=0;
        foreach($sets as $sr){
            $s=$this->db->decode('ads_sets',$sr);
            $crs=$this->db->rows("SELECT * FROM ".$this->table('ads_creatives')." WHERE set_id=%d AND is_deleted=0 AND status='active'",array($s['id']));
            $creativesPerSet[]=array('set_id'=>(int)$s['id'],'set_name'=>$s['name'],'active_creatives'=>count($crs));
            if(!$crs){$add('creatives_set_'.$s['id'],false,"Ad set '".$s['name']."' has no active creatives.");}
            $res=$this->resolveTargetingContacts($s,$c['channel']??'sms');
            $totalEligible+=count($res['eligible']);
        }
        $add('audience',$totalEligible>0,$totalEligible>0?($totalEligible.' eligible recipient(s) matched.'):'No eligible recipients match the ad set targeting.');
        if(($c['channel']??'sms')==='email'){
            $acct=$c['email_account_id']!==null?$this->db->one('email_accounts',array('id'=>(int)$c['email_account_id'])):null;
            $add('sender_identity',$acct&&$acct['is_active'],$acct&&$acct['is_active']?('Sender '.$acct['from_email'].' is configured.'):'Configure an active email sender.');
        }else{
            $phones=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('gateway_settings').' WHERE COALESCE(is_enabled,0)=1')[0]['n'];
            $add('sender_identity',$phones>0,$phones>0?($phones.' enabled SMS gateway phone(s).'):'Add and enable at least one SMS gateway phone in Settings.',false);
        }
        $stepsCnt=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('ads_followup_steps').' WHERE campaign_id=%d AND is_active=1 AND deleted_at IS NULL',array($id))[0]['n'];
        $errStr=$errors?substr(implode('; ',$errors),0,5000):null;
        $this->db->update('ads_campaigns',array('last_error'=>$errStr,'last_activity_at'=>$this->records->now()),array('id'=>$id));
        return array('ok'=>count($errors)===0,'valid'=>count($errors)===0,'errors'=>$errors,'warnings'=>$warnings,'checks'=>$checks,'recommendation'=>count($errors)===0?'Preflight checks passed.':'Resolve the reported errors before launching.','summary'=>array('audience_size'=>$totalEligible,'creatives_per_set'=>$creativesPerSet,'followup_configured'=>$stepsCnt>0,'send_window'=>array('start_hour'=>(int)($c['send_start_hour']??9),'end_hour'=>(int)($c['send_end_hour']??18),'timezone'=>$c['timezone_name']??'UTC'),'daily_limit'=>$c['daily_limit']!==null?(int)$c['daily_limit']:null));
    }
    public function simulateCampaign($id){
        $c=$this->campaignOr404($id);
        $sets=$this->db->rows('SELECT * FROM '.$this->table('ads_sets').' WHERE campaign_id=%d ORDER BY id ASC',array($id));
        $perSet=array();$matched=0;$eligible=0;$skipped=array('opted_out'=>0,'missing_channel'=>0);
        foreach($sets as $sr){
            $s=$this->db->decode('ads_sets',$sr);
            $res=$this->resolveTargetingContacts($s,$c['channel']??'sms');
            $crs=$this->db->rows("SELECT id,name FROM ".$this->table('ads_creatives')." WHERE set_id=%d AND is_deleted=0 AND status='active' ORDER BY id ASC",array($s['id']));
            $split=array();$elCnt=count($res['eligible']);$crCnt=count($crs);
            foreach($crs as $idx=>$cr){$share=$crCnt>0?(int)floor($elCnt/$crCnt)+($idx<($elCnt%$crCnt)?1:0):0;$split[]=array('creative_id'=>(int)$cr['id'],'name'=>$cr['name'],'contacts'=>$share);}
            $perSet[]=array('set_id'=>(int)$s['id'],'set_name'=>$s['name'],'matched'=>$res['matched'],'eligible'=>$elCnt,'split'=>$split,'skipped'=>$res['skipped']);
            $matched+=$res['matched'];$eligible+=$elCnt;$skipped['opted_out']+=$res['skipped']['opted_out'];$skipped['missing_channel']+=$res['skipped']['missing_channel'];
        }
        $pending=(int)$this->db->rows("SELECT COUNT(*) AS n FROM ".$this->table('ads_assignments')." WHERE campaign_id=%d AND send_status='pending'",array($id))[0]['n'];
        $toSend=$eligible+$pending;$daily=(int)($c['daily_limit']??0);
        $estDays=$daily>0?(int)ceil($toSend/$daily):($toSend>0?1:0);
        $steps=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('ads_followup_steps').' WHERE campaign_id=%d AND is_active=1 AND deleted_at IS NULL',array($id))[0]['n'];
        return array('audience'=>$matched,'eligible'=>$eligible,'already_queued'=>$pending,'skipped'=>$skipped,'per_set'=>$perSet,'daily_limit'=>$c['daily_limit']!==null?(int)$c['daily_limit']:null,'total_limit'=>$c['total_limit']!==null?(int)$c['total_limit']:null,'estimated_days'=>$estDays,'drip'=>array('mode'=>$c['drip_mode']??'immediate','batch'=>(int)($c['drip_batch_size']??25),'interval_minutes'=>max(1,(int)($c['drip_interval_minutes']??5))),'followup_steps'=>$steps);
    }
    public function transitionState($id,$target,$reason,array $user){
        $this->db->requireTransaction();
        $c=$this->campaignOr404($id);$cur=$c['status']??'draft';
        if($target==='paused'&&!in_array($cur,array('active','scheduled'),true)){throw new AuthFailure(409,'Cannot pause a campaign that is '.$cur);}
        if($target==='completed'&&!in_array($cur,array('active','paused','scheduled'),true)){throw new AuthFailure(409,'Cannot complete a campaign that is '.$cur);}
        $now=$this->records->now();
        $patch=array('status'=>$target,'last_state'=>$cur,'last_state_at'=>$now,'state_changed_at'=>$now,'updated_at'=>$now);
        if($target==='paused'){$patch['paused_reason']=$reason;$patch['paused_at']=$now;}
        $this->db->update('ads_campaigns',$patch,array('id'=>$id));
        $this->records->create('ads_campaign_state_transitions',array('campaign_id'=>$id,'from_state'=>$cur,'to_state'=>$target,'reason'=>$reason,'actor'=>$user['username']??'admin'));
        $this->logActivity('campaign_'.$target,$id,'campaign',$id,$user['username']??'admin',$reason);
        return array('ok'=>true,'status'=>$target);
    }
    public function duplicateCampaign($id,array $user,&$status){
        $this->db->requireTransaction();
        $c=$this->campaignOr404($id);
        $copy=$this->records->create('ads_campaigns',array('name'=>substr($c['name'].' (Copy)',0,255),'description'=>$c['description'],'objective'=>$c['objective'],'status'=>'draft','channel'=>$c['channel'],'email_account_id'=>$c['email_account_id'],'subject'=>$c['subject'],'daily_limit'=>$c['daily_limit'],'total_limit'=>$c['total_limit'],'send_start_hour'=>$c['send_start_hour'],'send_end_hour'=>$c['send_end_hour'],'send_days'=>$c['send_days'],'timezone_name'=>$c['timezone_name'],'drip_mode'=>$c['drip_mode'],'drip_batch_size'=>$c['drip_batch_size'],'drip_interval_minutes'=>$c['drip_interval_minutes'],'owner_id'=>$user['id']??$c['owner_id'],'state_changed_at'=>$this->records->now()));
        foreach($this->db->rows('SELECT * FROM '.$this->table('ads_sets').' WHERE campaign_id=%d ORDER BY id ASC',array($id)) as $sr){
            $s=$this->db->decode('ads_sets',$sr);
            $newSet=$this->records->create('ads_sets',array('campaign_id'=>$copy['id'],'name'=>$s['name'],'status'=>$s['status'],'list_ids'=>$s['list_ids'],'contact_ids'=>$s['contact_ids'],'audience_id'=>$s['audience_id'],'include_tags'=>$s['include_tags'],'exclude_tags'=>$s['exclude_tags'],'city'=>$s['city'],'state'=>$s['state'],'industry'=>$s['industry'],'split_mode'=>$s['split_mode']));
            foreach($this->db->rows('SELECT * FROM '.$this->table('ads_creatives').' WHERE set_id=%d AND is_deleted=0 ORDER BY id ASC',array($s['id'])) as $cr){
                $cd=$this->db->decode('ads_creatives',$cr);
                $this->records->create('ads_creatives',array('set_id'=>$newSet['id'],'campaign_id'=>$copy['id'],'name'=>$cd['name'],'status'=>$cd['status'],'is_deleted'=>false,'body'=>$cd['body'],'cta'=>$cd['cta'],'subject'=>$cd['subject'],'html_body'=>$cd['html_body'],'allocation'=>$cd['allocation'],'current_version'=>1));
            }
        }
        $status=201;
        return $this->campaignOut($copy);
    }
    public function campaignAudience($id,array $q){
        $this->campaignOr404($id);
        $page=DataRoutes::integer($q['page']??1,'page',1,100000);$per=DataRoutes::integer($q['per_page']??50,'per_page',1,200);
        $where=array('a.campaign_id=%d');$args=array($id);
        if(isset($q['status'])&&$q['status']!==''){$where[]='a.send_status=%s';$args[]=DataRoutes::text($q,'status','',null,30);}
        $wSql=implode(' AND ',$where);
        $total=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('ads_assignments').' a WHERE '.$wSql,$args)[0]['n'];
        $items=array();
        foreach($this->db->rows('SELECT a.*,c.first_name,c.last_name,c.email FROM '.$this->table('ads_assignments').' a LEFT JOIN '.$this->table('contacts').' c ON c.id=a.contact_id WHERE '.$wSql.' ORDER BY a.id DESC LIMIT %d OFFSET %d',array_merge($args,array($per,($page-1)*$per))) as $r){
            $items[]=array('id'=>(int)$r['id'],'contact_id'=>(int)$r['contact_id'],'first_name'=>$r['first_name'],'last_name'=>$r['last_name'],'phone_number'=>$r['phone_number'],'email'=>$r['email'],'set_id'=>(int)$r['set_id'],'creative_id'=>(int)$r['creative_id'],'send_status'=>$r['send_status'],'delivery_status'=>$r['delivery_status'],'reply_status'=>$r['reply_status'],'skip_reason'=>$r['skip_reason'],'sent_at'=>Records::iso($r['sent_at']??null),'assigned_at'=>Records::iso($r['assigned_at']??null));
        }
        return array('total'=>$total,'page'=>$page,'per_page'=>$per,'items'=>$items);
    }
    public function removeAssignment($campaignId,$assignmentId){
        $this->db->requireTransaction();
        $row=$this->db->one('ads_assignments',array('id'=>$assignmentId,'campaign_id'=>$campaignId));
        if(!$row){throw new AuthFailure(404,'Audience assignment not found');}
        if($row['sent_at']!==null){throw new AuthFailure(409,'Cannot remove a recipient who has already been sent to.');}
        $this->db->delete('ads_assignments',array('id'=>$assignmentId));
        return null;
    }
    public function campaignJobs($id){
        $this->campaignOr404($id);$items=array();
        foreach($this->db->rows('SELECT * FROM '.$this->table('ads_campaign_jobs').' WHERE campaign_id=%d ORDER BY created_at DESC LIMIT 50',array($id)) as $jr){
            $j=$this->db->decode('ads_campaign_jobs',$jr);
            $items[]=array('id'=>$j['id'],'campaign_id'=>(int)$j['campaign_id'],'job_type'=>$j['job_type'],'status'=>$j['status'],'progress_completed'=>(int)$j['progress_completed'],'progress_total'=>(int)$j['progress_total'],'attempts'=>(int)$j['attempts'],'error'=>$j['error']?'Campaign job reported an error.':null,'created_at'=>Records::iso($j['created_at']),'finished_at'=>Records::iso($j['finished_at']??null));
        }
        return array('items'=>$items);
    }
    public function campaignSendLogs($id,array $q){
        $this->campaignOr404($id);
        $page=DataRoutes::integer($q['page']??1,'page',1,100000);$per=DataRoutes::integer($q['per_page']??50,'per_page',1,200);
        $total=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('ads_assignments').' WHERE campaign_id=%d AND sent_at IS NOT NULL',array($id))[0]['n'];
        $items=array();
        foreach($this->db->rows('SELECT a.*,c.email FROM '.$this->table('ads_assignments').' a LEFT JOIN '.$this->table('contacts').' c ON c.id=a.contact_id WHERE a.campaign_id=%d AND a.sent_at IS NOT NULL ORDER BY a.sent_at DESC,a.id DESC LIMIT %d OFFSET %d',array($id,$per,($page-1)*$per)) as $r){
            $items[]=array('id'=>(int)$r['id'],'contact_id'=>(int)$r['contact_id'],'phone_number'=>$r['phone_number'],'email'=>$r['email'],'creative_id'=>(int)$r['creative_id'],'send_status'=>$r['send_status'],'delivery_status'=>$r['delivery_status'],'reply_status'=>$r['reply_status'],'last_error'=>$r['last_error'],'sent_at'=>Records::iso($r['sent_at']));
        }
        return array('total'=>$total,'page'=>$page,'per_page'=>$per,'items'=>$items);
    }
    public function campaignAnalytics($id){
        $c=$this->campaignOr404($id);
        $totals=$this->countsWhere('campaign_id=%d',array($id),$id);
        $sets=array();
        foreach($this->db->rows('SELECT * FROM '.$this->table('ads_sets').' WHERE campaign_id=%d ORDER BY id ASC',array($id)) as $sr){
            $s=$this->db->decode('ads_sets',$sr);
            $sets[]=array('id'=>(int)$s['id'],'name'=>$s['name'],'stats'=>$this->countsWhere('set_id=%d',array($s['id']),$id,(int)$s['id']));
        }
        $creatives=array();
        foreach($this->db->rows('SELECT * FROM '.$this->table('ads_creatives').' WHERE campaign_id=%d AND is_deleted=0 ORDER BY id ASC',array($id)) as $cr){
            $cd=$this->db->decode('ads_creatives',$cr);
            $st=$this->countsWhere('creative_id=%d',array($cd['id']),$id,(int)$cd['set_id'],(int)$cd['id']);
            $creatives[]=array('id'=>(int)$cd['id'],'set_id'=>(int)$cd['set_id'],'name'=>$cd['name'],'status'=>$cd['status'],'allocation'=>(int)$cd['allocation'],'stats'=>$st,'score'=>self::score($c['objective']??'replies',$st));
        }
        return array('campaign_id'=>(int)$id,'totals'=>$totals,'sets'=>$sets,'creatives'=>$creatives,'score'=>self::score($c['objective']??'replies',$totals));
    }
    public function listSets($campaignId){
        $this->campaignOr404($campaignId);$out=array();
        foreach($this->db->rows('SELECT * FROM '.$this->table('ads_sets').' WHERE campaign_id=%d ORDER BY id ASC',array($campaignId)) as $sr){
            $s=$this->db->decode('ads_sets',$sr);
            $out[]=array_merge($this->setOut($s),array('stats'=>$this->countsWhere('set_id=%d',array($s['id']),$campaignId,(int)$s['id'])));
        }
        return array('items'=>$out);
    }
    private static function jstr($v){
        if(is_string($v)){return $v;}
        if(is_array($v)){return json_encode(array_values($v),JSON_THROW_ON_ERROR);}
        return '[]';
    }
    public function createSet($campaignId,$body,array $user,&$status){
        if(!is_object($body)){throw new ValidationFailure('Supply a JSON object');}
        $this->db->requireTransaction();
        $this->campaignOr404($campaignId);
        $audId=isset($body->audience_id)&&$body->audience_id!==null?DataRoutes::integer($body->audience_id,'audience_id',1):null;
        if($audId!==null){$this->audienceOr404($audId);}
        $name=Compat::strip(DataRoutes::text(array('name'=>$body->name??'Ad Set'),'name','Ad Set',1,255));
        $s=$this->records->create('ads_sets',array('campaign_id'=>$campaignId,'name'=>$name,'status'=>DataRoutes::text(array('status'=>$body->status??'active'),'status','active',null,30),'list_ids'=>self::jstr(isset($body->list_ids)?(is_array($body->list_ids)?array_map('intval',$body->list_ids):self::jlist($body->list_ids)):array()),'contact_ids'=>self::jstr(isset($body->contact_ids)?(is_array($body->contact_ids)?array_map('intval',$body->contact_ids):self::jlist($body->contact_ids)):array()),'audience_id'=>$audId,'include_tags'=>self::jstr($body->include_tags??array()),'exclude_tags'=>self::jstr($body->exclude_tags??array()),'city'=>isset($body->city)&&$body->city!==null?DataRoutes::text(array('city'=>$body->city),'city',null,null,120):null,'state'=>isset($body->state)&&$body->state!==null?DataRoutes::text(array('state'=>$body->state),'state',null,null,120):null,'industry'=>isset($body->industry)&&$body->industry!==null?DataRoutes::text(array('industry'=>$body->industry),'industry',null,null,120):null,'split_mode'=>DataRoutes::text(array('split_mode'=>$body->split_mode??'even'),'split_mode','even',null,30),'auto_optimize'=>!empty($body->auto_optimize)));
        $this->logActivity('set_created',$campaignId,'set',$s['id'],$user['username']??'admin',$name);
        $status=201;
        return $this->setOut($s);
    }
    public function updateSet($setId,$body,array $user){
        if(!is_object($body)){throw new ValidationFailure('Supply a JSON object');}
        $this->db->requireTransaction();
        $s=$this->setOr404($setId);$patch=array('updated_at'=>$this->records->now());
        foreach(array('name'=>255,'status'=>30,'city'=>120,'state'=>120,'industry'=>120,'split_mode'=>30) as $tf=>$cap){if(property_exists($body,$tf)){$patch[$tf]=$body->$tf===null?null:DataRoutes::text(array($tf=>$body->$tf),$tf,'',null,$cap);}}
        foreach(array('list_ids','contact_ids','include_tags','exclude_tags','include_statuses','exclude_statuses','exclude_campaign_ids') as $lf){if(property_exists($body,$lf)&&is_array($body->$lf)){$patch[$lf]=self::jstr($body->$lf);}}
        if(property_exists($body,'audience_id')){$patch['audience_id']=$body->audience_id===null?null:DataRoutes::integer($body->audience_id,'audience_id',1);if($patch['audience_id']!==null){$this->audienceOr404($patch['audience_id']);}}
        if(property_exists($body,'daily_limit')){$patch['daily_limit']=$body->daily_limit===null?null:DataRoutes::integer($body->daily_limit,'daily_limit',1,1000000);}
        if(property_exists($body,'auto_optimize')&&$body->auto_optimize!==null){$patch['auto_optimize']=DataRoutes::boolean($body->auto_optimize,'auto_optimize');}
        $this->db->update('ads_sets',$patch,array('id'=>$setId));
        $this->logActivity('set_updated',$s['campaign_id'],'set',$setId,$user['username']??'admin',$patch['name']??$s['name']);
        return $this->setOut($this->setOr404($setId));
    }
    public function deleteSet($setId){
        $this->db->requireTransaction();
        $this->setOr404($setId);
        $this->db->delete('ads_assignments',array('set_id'=>$setId));
        $this->db->delete('ads_creatives',array('set_id'=>$setId));
        $this->db->delete('ads_sets',array('id'=>$setId));
        return null;
    }
    public function previewTargeting(array $spec,$channel='sms'){
        $res=$this->resolveTargetingContacts($spec,$channel);
        $sample=array();
        foreach(array_slice($res['eligible'],0,10) as $r){
            $sample[]=array('id'=>(int)$r['id'],'name'=>trim(($r['first_name']??'').' '.($r['last_name']??''))?:($r['email']?:$r['phone_number']),'phone_number'=>$r['phone_number'],'email'=>$r['email']);
        }
        return array('matched'=>$res['matched'],'eligible'=>count($res['eligible']),'skipped'=>$res['skipped'],'sample'=>$sample);
    }
    public function listCreatives($setId){
        $s=$this->setOr404($setId);$c=$this->campaignOr404($s['campaign_id']);$out=array();
        foreach($this->db->rows('SELECT * FROM '.$this->table('ads_creatives').' WHERE set_id=%d AND is_deleted=0 ORDER BY id ASC',array($setId)) as $cr){
            $cd=$this->db->decode('ads_creatives',$cr);
            $st=$this->countsWhere('creative_id=%d',array($cd['id']),(int)$s['campaign_id'],(int)$setId,(int)$cd['id']);
            $out[]=array_merge($this->creativeOut($cd),array('stats'=>$st,'score'=>self::score($c['objective']??'replies',$st)));
        }
        return array('items'=>$out);
    }
    public function createCreative($setId,$body,array $user,&$status){
        if(!is_object($body)){throw new ValidationFailure('Supply a JSON object');}
        $this->db->requireTransaction();
        $s=$this->setOr404($setId);
        $name=Compat::strip(DataRoutes::text(array('name'=>$body->name??'Variant A'),'name','Variant A',1,255));
        $txt=DataRoutes::text(array('body'=>$body->body??''),'body','',1,50000);
        $cr=$this->records->create('ads_creatives',array('set_id'=>$setId,'campaign_id'=>$s['campaign_id'],'name'=>$name,'status'=>DataRoutes::text(array('status'=>$body->status??'active'),'status','active',null,30),'is_deleted'=>false,'body'=>$txt,'cta'=>isset($body->cta)?DataRoutes::text(array('cta'=>$body->cta),'cta',null,null,500):null,'tracking_link'=>isset($body->tracking_link)?DataRoutes::text(array('tracking_link'=>$body->tracking_link),'tracking_link',null,null,1000):null,'subject'=>isset($body->subject)?DataRoutes::text(array('subject'=>$body->subject),'subject',null,null,998):null,'html_body'=>isset($body->html_body)?DataRoutes::text(array('html_body'=>$body->html_body),'html_body',null,null,200000):null,'allocation'=>isset($body->allocation)?DataRoutes::integer($body->allocation,'allocation',0,100):100,'current_version'=>1));
        $this->records->create('ads_creative_versions',array('creative_id'=>$cr['id'],'version'=>1,'body'=>$txt,'cta'=>$cr['cta'],'subject'=>$cr['subject'],'html_body'=>$cr['html_body']));
        $this->logActivity('creative_created',$s['campaign_id'],'creative',$cr['id'],$user['username']??'admin',$name);
        $status=201;
        return $this->creativeOut($cr);
    }
    public function updateCreative($creativeId,$body,array $user){
        if(!is_object($body)){throw new ValidationFailure('Supply a JSON object');}
        $this->db->requireTransaction();
        $cr=$this->creativeOr404($creativeId);$patch=array('updated_at'=>$this->records->now());
        $bump=false;
        foreach(array('name'=>255,'status'=>30,'body'=>50000,'cta'=>500,'tracking_link'=>1000,'subject'=>998,'html_body'=>200000) as $tf=>$cap){
            if(property_exists($body,$tf)){$patch[$tf]=$body->$tf===null?null:DataRoutes::text(array($tf=>$body->$tf),$tf,'',null,$cap);if(in_array($tf,array('body','cta','subject','html_body'),true)&&$patch[$tf]!==$cr[$tf]){$bump=true;}}
        }
        if(property_exists($body,'allocation')&&$body->allocation!==null){$patch['allocation']=DataRoutes::integer($body->allocation,'allocation',0,100);}
        if($bump){
            $nv=(int)($cr['current_version']??1)+1;$patch['current_version']=$nv;
            $this->records->create('ads_creative_versions',array('creative_id'=>$creativeId,'version'=>$nv,'body'=>$patch['body']??$cr['body'],'cta'=>$patch['cta']??$cr['cta'],'subject'=>$patch['subject']??$cr['subject'],'html_body'=>$patch['html_body']??$cr['html_body']));
        }
        $this->db->update('ads_creatives',$patch,array('id'=>$creativeId));
        return $this->creativeOut($this->creativeOr404($creativeId));
    }
    public function deleteCreative($creativeId){
        $this->db->requireTransaction();
        $this->creativeOr404($creativeId);
        $this->db->update('ads_creatives',array('is_deleted'=>true,'status'=>'archived','updated_at'=>$this->records->now()),array('id'=>$creativeId));
        return null;
    }
    public function creativeVersions($creativeId){
        $this->creativeOr404($creativeId);$items=array();
        foreach($this->db->rows('SELECT * FROM '.$this->table('ads_creative_versions').' WHERE creative_id=%d ORDER BY version DESC',array($creativeId)) as $vr){
            $v=$this->db->decode('ads_creative_versions',$vr);
            $items[]=array('id'=>(int)$v['id'],'creative_id'=>(int)$v['creative_id'],'version'=>(int)$v['version'],'body'=>$v['body'],'cta'=>$v['cta'],'subject'=>$v['subject'],'html_body'=>$v['html_body'],'created_at'=>Records::iso($v['created_at']));
        }
        return array('items'=>$items);
    }
    public function promoteCreative($creativeId,array $user){
        $this->db->requireTransaction();
        $cr=$this->creativeOr404($creativeId);
        $this->db->execute("UPDATE ".$this->table('ads_creatives')." SET status='paused',allocation=0 WHERE set_id=%d AND id<>%d AND is_deleted=0",array($cr['set_id'],$creativeId));
        $this->db->update('ads_creatives',array('status'=>'active','allocation'=>100,'updated_at'=>$this->records->now()),array('id'=>$creativeId));
        $this->logActivity('creative_promoted',$cr['campaign_id'],'creative',$creativeId,$user['username']??'admin',$cr['name']);
        return array('ok'=>true,'winner_id'=>(int)$creativeId);
    }
    public function listAudiences(){
        $items=array();
        foreach($this->db->rows('SELECT * FROM '.$this->table('ads_audiences').' ORDER BY id DESC LIMIT 200') as $ar){$items[]=$this->audienceOut($this->db->decode('ads_audiences',$ar));}
        return array('items'=>$items);
    }
    public function createAudience($body,array $user,&$status){
        if(!is_object($body)){throw new ValidationFailure('Supply a JSON object');}
        $this->db->requireTransaction();
        $name=Compat::strip(DataRoutes::text(array('name'=>$body->name??''),'name','',1,255));
        $a=$this->records->create('ads_audiences',array('name'=>$name,'description'=>isset($body->description)&&$body->description!==null?DataRoutes::text(array('description'=>$body->description),'description',null,null,2000):null,'owner_id'=>$user['id']??null,'list_ids'=>self::jstr(isset($body->list_ids)?(is_array($body->list_ids)?array_map('intval',$body->list_ids):self::jlist($body->list_ids)):array()),'contact_ids'=>self::jstr(isset($body->contact_ids)?(is_array($body->contact_ids)?array_map('intval',$body->contact_ids):self::jlist($body->contact_ids)):array()),'include_tags'=>self::jstr($body->include_tags??array()),'exclude_tags'=>self::jstr($body->exclude_tags??array()),'city'=>isset($body->city)&&$body->city!==null?DataRoutes::text(array('city'=>$body->city),'city',null,null,120):null,'state'=>isset($body->state)&&$body->state!==null?DataRoutes::text(array('state'=>$body->state),'state',null,null,120):null,'industry'=>isset($body->industry)&&$body->industry!==null?DataRoutes::text(array('industry'=>$body->industry),'industry',null,null,120):null));
        $status=201;
        return $this->audienceOut($a);
    }
    public function updateAudience($id,$body){
        if(!is_object($body)){throw new ValidationFailure('Supply a JSON object');}
        $this->db->requireTransaction();
        $this->audienceOr404($id);$patch=array('updated_at'=>$this->records->now());
        foreach(array('name'=>255,'description'=>2000,'city'=>120,'state'=>120,'industry'=>120) as $tf=>$cap){if(property_exists($body,$tf)){$patch[$tf]=$body->$tf===null?null:DataRoutes::text(array($tf=>$body->$tf),$tf,'',null,$cap);}}
        foreach(array('list_ids','contact_ids','include_tags','exclude_tags','include_statuses','exclude_statuses','exclude_campaign_ids') as $lf){if(property_exists($body,$lf)&&is_array($body->$lf)){$patch[$lf]=self::jstr($body->$lf);}}
        $this->db->update('ads_audiences',$patch,array('id'=>$id));
        return $this->audienceOut($this->audienceOr404($id));
    }
    public function deleteAudience($id){
        $this->db->requireTransaction();
        $this->audienceOr404($id);
        $this->db->execute('UPDATE '.$this->table('ads_sets').' SET audience_id=NULL WHERE audience_id=%d',array($id));
        $this->db->delete('ads_audiences',array('id'=>$id));
        return null;
    }
    public function listSteps($campaignId){
        $this->campaignOr404($campaignId);$items=array();
        foreach($this->db->rows('SELECT * FROM '.$this->table('ads_followup_steps').' WHERE campaign_id=%d AND deleted_at IS NULL ORDER BY step_order ASC,id ASC',array($campaignId)) as $r){$items[]=$this->stepOut($this->db->decode('ads_followup_steps',$r));}
        return array('items'=>$items,'steps'=>$items);
    }
    public function createStep($campaignId,$body,&$status){
        if(!is_object($body)){throw new ValidationFailure('Supply a JSON object');}
        $this->db->requireTransaction();
        $this->campaignOr404($campaignId);
        $maxOrd=(int)$this->db->rows('SELECT COALESCE(MAX(step_order),0) AS m FROM '.$this->table('ads_followup_steps').' WHERE campaign_id=%d AND deleted_at IS NULL',array($campaignId))[0]['m'];
        $wait=isset($body->wait_hours)?DataRoutes::integer($body->wait_hours,'wait_hours',1,8760):(isset($body->delay_hours)?DataRoutes::integer($body->delay_hours,'delay_hours',1,8760):48);
        $st=$this->records->create('ads_followup_steps',array('campaign_id'=>$campaignId,'step_order'=>isset($body->step_order)?DataRoutes::integer($body->step_order,'step_order',1,100):($maxOrd+1),'name'=>DataRoutes::text(array('n'=>$body->name??('Step '.($maxOrd+1))),'name','Step 1',1,255),'wait_hours'=>$wait,'condition'=>DataRoutes::text(array('condition'=>$body->condition??'no_reply'),'condition','no_reply',null,50),'action'=>DataRoutes::text(array('action'=>$body->action??'send_sms'),'action','send_sms',null,50),'body'=>isset($body->body)?DataRoutes::text(array('body'=>$body->body),'body',null,null,50000):null,'subject'=>isset($body->subject)?DataRoutes::text(array('subject'=>$body->subject),'subject',null,null,998):null,'is_active'=>isset($body->is_active)?DataRoutes::boolean($body->is_active,'is_active'):true));
        $status=201;
        return $this->stepOut($st);
    }
    public function updateStep($stepId,$body){
        if(!is_object($body)){throw new ValidationFailure('Supply a JSON object');}
        $this->db->requireTransaction();
        $st=$this->db->one('ads_followup_steps',array('id'=>$stepId));
        if(!$st||$st['deleted_at']!==null){throw new AuthFailure(404,'Follow-up step not found');}
        $patch=array('updated_at'=>$this->records->now());
        foreach(array('name'=>255,'condition'=>50,'action'=>50,'body'=>50000,'subject'=>998,'action_value'=>255) as $tf=>$cap){if(property_exists($body,$tf)){$patch[$tf]=$body->$tf===null?null:DataRoutes::text(array($tf=>$body->$tf),$tf,'',null,$cap);}}
        if(isset($body->wait_hours)){$patch['wait_hours']=DataRoutes::integer($body->wait_hours,'wait_hours',1,8760);}
        elseif(isset($body->delay_hours)){$patch['wait_hours']=DataRoutes::integer($body->delay_hours,'delay_hours',1,8760);}
        if(isset($body->step_order)){$patch['step_order']=DataRoutes::integer($body->step_order,'step_order',1,100);}
        if(isset($body->is_active)){$patch['is_active']=DataRoutes::boolean($body->is_active,'is_active');}
        $this->db->update('ads_followup_steps',$patch,array('id'=>$stepId));
        return $this->stepOut($this->db->one('ads_followup_steps',array('id'=>$stepId)));
    }
    public function deleteStep($stepId){
        $this->db->requireTransaction();
        $st=$this->db->one('ads_followup_steps',array('id'=>$stepId));
        if(!$st||$st['deleted_at']!==null){throw new AuthFailure(404,'Follow-up step not found');}
        $this->db->update('ads_followup_steps',array('deleted_at'=>$this->records->now(),'is_active'=>false),array('id'=>$stepId));
        return array('ok'=>true,'deleted'=>true,'step_id'=>(int)$stepId);
    }
    public function listFollowupTemplates(array $q){
        $inc=isset($q['include_inactive'])?DataRoutes::boolean($q['include_inactive'],'include_inactive'):false;
        $where=array($inc?'1=1':'is_active=1');$args=array();
        if(isset($q['channel'])&&$q['channel']!==''&&$q['channel']!=='all'){$where[]='channel=%s';$args[]=$this->valChannel($q['channel']);}
        $items=array();
        foreach($this->db->rows('SELECT * FROM '.$this->table('ads_followup_templates').' WHERE '.implode(' AND ',$where).' ORDER BY name ASC,id ASC LIMIT 200',$args) as $r){$items[]=$this->tplOut($this->db->decode('ads_followup_templates',$r));}
        return array('items'=>$items,'templates'=>$items,'total'=>count($items));
    }
    public function createFollowupTemplate($body,&$status){
        if(!is_object($body)){throw new ValidationFailure('Supply a JSON object');}
        $this->db->requireTransaction();
        $name=Compat::strip(DataRoutes::text(array('name'=>$body->name??''),'name','',1,255));
        $t=$this->records->create('ads_followup_templates',array('name'=>$name,'description'=>isset($body->description)?DataRoutes::text(array('description'=>$body->description),'description',null,null,1000):null,'channel'=>$this->valChannel($body->channel??'sms'),'subject'=>isset($body->subject)?DataRoutes::text(array('subject'=>$body->subject),'subject',null,null,998):null,'body'=>DataRoutes::text(array('body'=>$body->body??''),'body','',1,50000),'condition'=>DataRoutes::text(array('condition'=>$body->condition??'no_reply'),'condition','no_reply',null,50),'action'=>DataRoutes::text(array('action'=>$body->action??'send_sms'),'action','send_sms',null,50),'delay_hours'=>isset($body->delay_hours)?DataRoutes::integer($body->delay_hours,'delay_hours',1,8760):48,'is_active'=>true,'usage_count'=>0));
        $status=201;
        return $this->tplOut($t);
    }
    public function updateFollowupTemplate($id,$body){
        if(!is_object($body)){throw new ValidationFailure('Supply a JSON object');}
        $this->db->requireTransaction();
        if(!$this->db->one('ads_followup_templates',array('id'=>$id))){throw new AuthFailure(404,'Template not found');}
        $patch=array('updated_at'=>$this->records->now());
        foreach(array('name'=>255,'description'=>1000,'subject'=>998,'body'=>50000,'condition'=>50,'action'=>50) as $tf=>$cap){if(property_exists($body,$tf)){$patch[$tf]=$body->$tf===null?null:DataRoutes::text(array($tf=>$body->$tf),$tf,'',null,$cap);}}
        if(isset($body->channel)){$patch['channel']=$this->valChannel($body->channel);}
        if(isset($body->delay_hours)){$patch['delay_hours']=DataRoutes::integer($body->delay_hours,'delay_hours',1,8760);}
        if(isset($body->is_active)){$patch['is_active']=DataRoutes::boolean($body->is_active,'is_active');}
        $this->db->update('ads_followup_templates',$patch,array('id'=>$id));
        return $this->tplOut($this->db->one('ads_followup_templates',array('id'=>$id)));
    }
    public function deleteFollowupTemplate($id){
        $this->db->requireTransaction();
        if(!$this->db->one('ads_followup_templates',array('id'=>$id))){throw new AuthFailure(404,'Template not found');}
        $this->db->update('ads_followup_templates',array('is_active'=>false,'updated_at'=>$this->records->now()),array('id'=>$id));
        return array('ok'=>true,'deleted'=>true,'template_id'=>(int)$id,'soft_deleted'=>true);
    }
    public function listFollowups(array $q){
        $bucket=DataRoutes::text($q,'bucket','today',null,30)?:'today';
        $today=substr($this->records->now(),0,10);
        $where=array('1=1');$args=array();
        if($bucket==='today'){$where[]="t.status='pending' AND SUBSTR(t.due_at,1,10)=%s";$args[]=$today;}
        elseif($bucket==='overdue'){$where[]="t.status='pending' AND SUBSTR(t.due_at,1,10)<%s";$args[]=$today;}
        elseif($bucket==='upcoming'){$where[]="t.status='pending' AND SUBSTR(t.due_at,1,10)>%s";$args[]=$today;}
        elseif($bucket==='completed'){$where[]="t.status='completed'";}
        elseif($bucket==='cancelled'){$where[]="t.status='cancelled'";}
        if(isset($q['channel'])&&$q['channel']!==''&&$q['channel']!=='all'){$where[]='t.campaign_id IN (SELECT id FROM '.$this->table('ads_campaigns').' WHERE channel=%s)';$args[]=$this->valChannel($q['channel']);}
        $items=array();
        foreach($this->db->rows('SELECT t.*,c.first_name,c.last_name,c.phone_number,c.email FROM '.$this->table('ads_followup_tasks').' t LEFT JOIN '.$this->table('contacts').' c ON c.id=t.contact_id WHERE '.implode(' AND ',$where).' ORDER BY t.due_at ASC,t.id DESC LIMIT 200',$args) as $r){
            $items[]=array('id'=>(int)$r['id'],'campaign_id'=>$r['campaign_id']!==null?(int)$r['campaign_id']:null,'contact_id'=>(int)$r['contact_id'],'contact_name'=>trim(($r['first_name']??'').' '.($r['last_name']??'')),'phone_number'=>$r['phone_number'],'email'=>$r['email'],'due_at'=>Records::iso($r['due_at']),'status'=>$r['status'],'priority'=>$r['priority'],'note'=>$r['note'],'body'=>$r['body'],'completed_at'=>Records::iso($r['completed_at']??null));
        }
        return array('bucket'=>$bucket,'items'=>$items,'total'=>count($items));
    }
    public function createFollowup($body,&$status){
        if(!is_object($body)){throw new ValidationFailure('Supply a JSON object');}
        $this->db->requireTransaction();
        $cid=DataRoutes::integer($body->contact_id??0,'contact_id',1);
        if(!$this->db->one('contacts',array('id'=>$cid))){throw new AuthFailure(404,'Contact not found');}
        $due=isset($body->due_at)&&$body->due_at!==null?DataRoutes::text(array('due_at'=>$body->due_at),'due_at','',1,40):$this->records->now();
        $t=$this->records->create('ads_followup_tasks',array('campaign_id'=>isset($body->campaign_id)&&$body->campaign_id!==null?DataRoutes::integer($body->campaign_id,'campaign_id',1):null,'contact_id'=>$cid,'due_at'=>$due,'status'=>'pending','priority'=>DataRoutes::text(array('priority'=>$body->priority??'normal'),'priority','normal',null,20),'note'=>isset($body->note)?DataRoutes::text(array('note'=>$body->note),'note',null,null,2000):null,'body'=>isset($body->body)?DataRoutes::text(array('body'=>$body->body),'body',null,null,10000):null));
        $status=201;
        return array('id'=>(int)$t['id'],'contact_id'=>$cid,'status'=>$t['status'],'due_at'=>Records::iso($t['due_at']));
    }
    public function followupAction($taskId,$action){
        $this->db->requireTransaction();
        $t=$this->db->one('ads_followup_tasks',array('id'=>$taskId));
        if(!$t){throw new AuthFailure(404,'Follow-up task not found');}
        if($action==='complete'||$action==='done'){$this->db->update('ads_followup_tasks',array('status'=>'completed','completed_at'=>$this->records->now()),array('id'=>$taskId));return array('ok'=>true,'status'=>'completed');}
        if($action==='cancel'||$action==='dismiss'){$this->db->update('ads_followup_tasks',array('status'=>'cancelled'),array('id'=>$taskId));return array('ok'=>true,'status'=>'cancelled');}
        if($action==='snooze'){$dt=(new \DateTimeImmutable($this->records->now(),new \DateTimeZone('UTC')))->modify('+24 hours')->format('Y-m-d H:i:s.u');$this->db->update('ads_followup_tasks',array('due_at'=>$dt,'status'=>'pending'),array('id'=>$taskId));return array('ok'=>true,'status'=>'pending','due_at'=>Records::iso($dt));}
        throw new ValidationFailure('Unsupported follow-up action','action');
    }
    public function listCalendar(array $q){
        $items=array();
        foreach($this->db->rows('SELECT e.*,c.first_name,c.last_name,c.phone_number FROM '.$this->table('ads_calendar_events').' e LEFT JOIN '.$this->table('contacts').' c ON c.id=e.contact_id ORDER BY e.starts_at ASC,e.id DESC LIMIT 500') as $r){
            $items[]=array('id'=>(int)$r['id'],'title'=>$r['title'],'event_type'=>$r['event_type'],'contact_id'=>$r['contact_id']!==null?(int)$r['contact_id']:null,'contact_name'=>trim(($r['first_name']??'').' '.($r['last_name']??'')),'campaign_id'=>$r['campaign_id']!==null?(int)$r['campaign_id']:null,'starts_at'=>Records::iso($r['starts_at']),'duration_minutes'=>(int)($r['duration_minutes']??30),'notes'=>$r['notes'],'priority'=>$r['priority'],'status'=>$r['status']);
        }
        return array('items'=>$items);
    }
    public function createCalendar($body,&$status){
        if(!is_object($body)){throw new ValidationFailure('Supply a JSON object');}
        $this->db->requireTransaction();
        $title=Compat::strip(DataRoutes::text(array('title'=>$body->title??''),'title','',1,255));
        $starts=Compat::strip(DataRoutes::text(array('s'=>$body->starts_at??$this->records->now()),'starts_at','',1,40));
        $ev=$this->records->create('ads_calendar_events',array('title'=>$title,'event_type'=>DataRoutes::text(array('event_type'=>$body->event_type??'meeting'),'event_type','meeting',null,40),'contact_id'=>isset($body->contact_id)&&$body->contact_id!==null?DataRoutes::integer($body->contact_id,'contact_id',1):null,'campaign_id'=>isset($body->campaign_id)&&$body->campaign_id!==null?DataRoutes::integer($body->campaign_id,'campaign_id',1):null,'starts_at'=>$starts,'duration_minutes'=>isset($body->duration_minutes)?DataRoutes::integer($body->duration_minutes,'duration_minutes',5,1440):30,'notes'=>isset($body->notes)?DataRoutes::text(array('notes'=>$body->notes),'notes',null,null,5000):null,'priority'=>DataRoutes::text(array('priority'=>$body->priority??'normal'),'priority','normal',null,20),'status'=>DataRoutes::text(array('status'=>$body->status??'scheduled'),'status','scheduled',null,30)));
        $status=201;
        return array('id'=>(int)$ev['id'],'title'=>$ev['title'],'event_type'=>$ev['event_type'],'starts_at'=>Records::iso($ev['starts_at']),'status'=>$ev['status']);
    }
    public function updateCalendar($id,$body){
        if(!is_object($body)){throw new ValidationFailure('Supply a JSON object');}
        $this->db->requireTransaction();
        if(!$this->db->one('ads_calendar_events',array('id'=>$id))){throw new AuthFailure(404,'Event not found');}
        $patch=array();
        foreach(array('title'=>255,'event_type'=>40,'starts_at'=>40,'notes'=>5000,'priority'=>20,'status'=>30) as $tf=>$cap){if(property_exists($body,$tf)){$patch[$tf]=$body->$tf===null?null:DataRoutes::text(array($tf=>$body->$tf),$tf,'',null,$cap);}}
        if(isset($body->duration_minutes)){$patch['duration_minutes']=DataRoutes::integer($body->duration_minutes,'duration_minutes',5,1440);}
        if($patch){$this->db->update('ads_calendar_events',$patch,array('id'=>$id));}
        $ev=$this->db->one('ads_calendar_events',array('id'=>$id));
        return array('id'=>(int)$ev['id'],'title'=>$ev['title'],'event_type'=>$ev['event_type'],'starts_at'=>Records::iso($ev['starts_at']),'status'=>$ev['status']);
    }
    public function deleteCalendar($id){
        $this->db->requireTransaction();
        if(!$this->db->one('ads_calendar_events',array('id'=>$id))){throw new AuthFailure(404,'Event not found');}
        $this->db->delete('ads_calendar_events',array('id'=>$id));
        return null;
    }
    public function listSuppression(array $q){
        $page=DataRoutes::integer($q['page']??1,'page',1,100000);$per=DataRoutes::integer($q['per_page']??50,'per_page',1,200);
        $where='1=1';$args=array();
        if(isset($q['search'])&&Compat::strip((string)$q['search'])!==''){$where='LOWER(phone_number) LIKE %s';$args[]='%'.strtolower(DataRoutes::like(Compat::strip(DataRoutes::text($q,'search','',null,100)))).'%';}
        $total=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('suppression_entries').' WHERE '.$where,$args)[0]['n'];
        $items=array();
        foreach($this->db->rows('SELECT * FROM '.$this->table('suppression_entries').' WHERE '.$where.' ORDER BY id DESC LIMIT %d OFFSET %d',array_merge($args,array($per,($page-1)*$per))) as $r){
            $items[]=array('id'=>(int)$r['id'],'phone_number'=>$r['phone_number'],'contact_id'=>$r['contact_id']!==null?(int)$r['contact_id']:null,'reason'=>$r['reason'],'source'=>$r['source'],'suppressed_at'=>Records::iso($r['suppressed_at']??null));
        }
        return array('total'=>$total,'items'=>$items);
    }
    public function addSuppression($body,array $user,&$status){
        if(!is_object($body)){throw new ValidationFailure('Supply a JSON object');}
        $this->db->requireTransaction();
        $cid=isset($body->contact_id)&&$body->contact_id!==null?DataRoutes::integer($body->contact_id,'contact_id',1):null;
        $contact=$cid!==null?$this->db->one('contacts',array('id'=>$cid)):null;
        $phone=isset($body->phone_number)&&$body->phone_number!==null?Phone::clean(DataRoutes::text(array('phone_number'=>$body->phone_number),'phone_number','',null,40)):($contact?$contact['phone_number']:null);
        if(!$phone){throw new AuthFailure(400,'Provide a valid phone number or contact');}
        if(!$contact){$contact=$this->db->one('contacts',array('phone_number'=>$phone));}
        $reason=Compat::strip(DataRoutes::text(array('reason'=>$body->reason??'manual'),'reason','manual',null,100))?:'manual';
        if($contact){$this->db->update('contacts',array('is_opted_out'=>true,'opted_out_at'=>$this->records->now()),array('id'=>$contact['id']));}
        if(!$this->db->one('suppression_entries',array('phone_number'=>$phone))){
            $this->records->create('suppression_entries',array('phone_number'=>$phone,'contact_id'=>$contact?$contact['id']:null,'reason'=>$reason,'source'=>'manual','suppressed_at'=>$this->records->now()));
        }
        $this->logActivity('contact_suppressed',null,'suppression',null,$user['username']??'admin',$reason);
        $status=201;
        return array('ok'=>true);
    }
    public function removeSuppression($entryId){
        $this->db->requireTransaction();
        $entry=$this->db->one('suppression_entries',array('id'=>$entryId));
        if(!$entry){throw new AuthFailure(404,'Entry not found');}
        $this->db->execute('UPDATE '.$this->table('contacts').' SET is_opted_out=0,opted_out_at=NULL WHERE phone_number=%s',array($entry['phone_number']));
        $this->db->delete('suppression_entries',array('id'=>$entryId));
        return null;
    }
    public function contactAction($contactId,$body){
        if(!is_object($body)||!isset($body->action)){throw new ValidationFailure('action is required','action');}
        $this->db->requireTransaction();
        $c=$this->db->one('contacts',array('id'=>$contactId));
        if(!$c){throw new AuthFailure(404,'Contact not found');}
        $act=Compat::strip(DataRoutes::text(array('action'=>$body->action),'action','',1,40));
        if($act==='interested'){
            $this->db->update('contacts',array('lead_status'=>'interested','updated_at'=>$this->records->now()),array('id'=>$contactId));
            $this->db->execute("UPDATE ".$this->table('ads_assignments')." SET reply_status='positive' WHERE contact_id=%d",array($contactId));
        }elseif($act==='not_interested'){
            $this->db->update('contacts',array('lead_status'=>'not_interested','updated_at'=>$this->records->now()),array('id'=>$contactId));
            if(!empty($body->stop_campaigns)){
                $this->db->execute("UPDATE ".$this->table('ads_assignments')." SET send_status='cancelled',skip_reason='not_interested' WHERE contact_id=%d AND send_status='pending'",array($contactId));
                $this->db->execute("UPDATE ".$this->table('ads_followup_tasks')." SET status='cancelled' WHERE contact_id=%d AND status='pending'",array($contactId));
            }
        }elseif($act==='converted'){
            $this->db->update('contacts',array('lead_status'=>'converted','updated_at'=>$this->records->now()),array('id'=>$contactId));
            $this->db->execute("UPDATE ".$this->table('ads_assignments')." SET conversion_status='converted' WHERE contact_id=%d",array($contactId));
        }else{throw new ValidationFailure('Unsupported contact action','action');}
        return array('ok'=>true,'contact_id'=>(int)$contactId,'action'=>$act);
    }
    public function contactTimeline($contactId){
        $c=$this->db->one('contacts',array('id'=>$contactId));
        if(!$c){throw new AuthFailure(404,'Contact not found');}
        $events=array();
        foreach($this->db->rows('SELECT * FROM '.$this->table('ads_events').' WHERE contact_id=%d ORDER BY id DESC LIMIT 100',array($contactId)) as $er){
            $e=$this->db->decode('ads_events',$er);
            $events[]=array('id'=>(int)$e['id'],'campaign_id'=>(int)$e['campaign_id'],'event_type'=>$e['event_type'],'detail'=>$e['detail'],'created_at'=>Records::iso($e['created_at']));
        }
        return array('contact_id'=>(int)$contactId,'lead_status'=>$c['lead_status'],'events'=>$events);
    }
    public function search(array $q){
        $raw=Compat::strip(DataRoutes::text($q,'q','',null,200));
        if($raw===''){return array('campaigns'=>array(),'audiences'=>array(),'contacts'=>array());}
        $s='%'.strtolower(DataRoutes::like($raw)).'%';
        $camps=array();foreach($this->db->rows('SELECT id,name,status,channel FROM '.$this->table('ads_campaigns').' WHERE LOWER(name) LIKE %s LIMIT 15',array($s)) as $r){$camps[]=array('id'=>(int)$r['id'],'name'=>$r['name'],'status'=>$r['status'],'channel'=>$r['channel']);}
        $auds=array();foreach($this->db->rows('SELECT id,name FROM '.$this->table('ads_audiences').' WHERE LOWER(name) LIKE %s LIMIT 15',array($s)) as $r){$auds[]=array('id'=>(int)$r['id'],'name'=>$r['name']);}
        $cts=array();foreach($this->db->rows('SELECT id,first_name,last_name,phone_number,email FROM '.$this->table('contacts').' WHERE LOWER(COALESCE(first_name,\'\')) LIKE %s OR LOWER(COALESCE(last_name,\'\')) LIKE %s OR LOWER(COALESCE(email,\'\')) LIKE %s OR phone_number LIKE %s LIMIT 15',array($s,$s,$s,$s)) as $r){$cts[]=array('id'=>(int)$r['id'],'name'=>trim(($r['first_name']??'').' '.($r['last_name']??'')),'phone_number'=>$r['phone_number'],'email'=>$r['email']);}
        return array('campaigns'=>$camps,'audiences'=>$auds,'contacts'=>$cts);
    }
    public function reference(){
        $lists=array();
        foreach($this->db->rows('SELECT l.id,l.name,(SELECT COUNT(*) FROM '.$this->table('contact_list_members').' m WHERE m.list_id=l.id) AS cnt FROM '.$this->table('contact_lists').' l ORDER BY l.name ASC LIMIT 500') as $l){
            $lists[]=array('id'=>(int)$l['id'],'name'=>$l['name'],'count'=>(int)$l['cnt']);
        }
        $tags=array();foreach($this->db->rows('SELECT name FROM '.$this->table('tags').' ORDER BY name ASC LIMIT 500') as $t){$tags[]=$t['name'];}
        $statuses=array();foreach($this->db->rows('SELECT DISTINCT lead_status FROM '.$this->table('contacts').' WHERE lead_status IS NOT NULL AND lead_status<>\'\' ORDER BY lead_status ASC') as $st){$statuses[]=$st['lead_status'];}
        $totalContacts=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('contacts'))[0]['n'];
        $emailable=(int)$this->db->rows("SELECT COUNT(*) AS n FROM ".$this->table('contacts')." WHERE COALESCE(email,'')<>'' AND COALESCE(is_email_opted_out,0)=0")[0]['n'];
        $phones=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('gateway_settings').' WHERE COALESCE(is_enabled,0)=1')[0]['n'];
        $senders=(new EmailManager($this->db,$this->config))->listSenders()['items'];
        $defId=null;foreach($senders as $sn){if(!empty($sn['is_default'])){$defId=$sn['id'];break;}}
        return array('lists'=>$lists,'tags'=>$tags,'statuses'=>$statuses,'total_contacts'=>$totalContacts,'emailable_contacts'=>$emailable,'channels'=>array(array('value'=>'sms','label'=>'SMS','available'=>$phones>0),array('value'=>'email','label'=>'Email','available'=>count($senders)>0)),'email_accounts'=>$senders,'default_email_account_id'=>$defId,'objectives'=>array(array('value'=>'replies','label'=>'Get replies'),array('value'=>'leads','label'=>'Generate leads'),array('value'=>'meetings','label'=>'Book meetings'),array('value'=>'website_visits','label'=>'Drive website visits'),array('value'=>'promotion','label'=>'Product promotion'),array('value'=>'reengagement','label'=>'Customer re-engagement'),array('value'=>'followup','label'=>'Follow-up'),array('value'=>'retention','label'=>'Customer retention'),array('value'=>'review','label'=>'Review request'),array('value'=>'custom','label'=>'Custom')));
    }
    public function activity(array $q,$campaignId=null){
        $limit=DataRoutes::integer($q['limit']??150,'limit',1,500);
        $where=array('1=1');$args=array();
        if($campaignId!==null){$where[]='campaign_id=%d';$args[]=$campaignId;}
        elseif(isset($q['channel'])&&$q['channel']!==''&&$q['channel']!=='all'){$where[]='(campaign_id IS NULL OR campaign_id IN (SELECT id FROM '.$this->table('ads_campaigns').' WHERE channel=%s))';$args[]=$this->valChannel($q['channel']);}
        $items=array();
        foreach($this->db->rows('SELECT * FROM '.$this->table('ads_activity_logs').' WHERE '.implode(' AND ',$where).' ORDER BY created_at DESC,id DESC LIMIT %d',array_merge($args,array($limit))) as $r){
            $items[]=array('id'=>(int)$r['id'],'action'=>$r['action'],'detail'=>$r['detail'],'actor'=>$r['actor'],'campaign_id'=>$r['campaign_id']!==null?(int)$r['campaign_id']:null,'created_at'=>Records::iso($r['created_at']));
        }
        return array('items'=>$items);
    }
    private static function csvCell($v){$s=(string)($v??'');if($s!==''&&in_array($s[0],array('=','+','-','@',"\t","\r"),true)){$s="'".$s;}return '"'.str_replace('"','""',$s).'"';}
    public function exportCsv($kind,array $q){
        $lines=array();
        $addRow=function(array $cols)use(&$lines){$lines[]=implode(',',array_map(array(self::class,'csvCell'),$cols));};
        if($kind==='campaigns'){
            $addRow(array('id','name','status','channel','objective','assigned','sent','replies','score'));
            $where='1=1';$args=array();
            if(isset($q['channel'])&&$q['channel']!==''&&$q['channel']!=='all'){$where='channel=%s';$args[]=$this->valChannel($q['channel']);}
            foreach($this->db->rows('SELECT * FROM '.$this->table('ads_campaigns').' WHERE '.$where.' ORDER BY id ASC LIMIT 5000',$args) as $c){
                $st=$this->countsWhere('campaign_id=%d',array($c['id']),(int)$c['id']);
                $addRow(array($c['id'],$c['name'],$c['status'],$c['channel'],$c['objective'],$st['assigned'],$st['sent'],$st['replies'],self::score($c['objective']??'replies',$st)));
            }
        }elseif($kind==='suppression'){
            $addRow(array('phone_number','reason','source','suppressed_at'));
            foreach($this->db->rows('SELECT * FROM '.$this->table('suppression_entries').' ORDER BY id DESC LIMIT 10000') as $r){$addRow(array($r['phone_number'],$r['reason'],$r['source'],Records::iso($r['suppressed_at']??null)));}
        }elseif($kind==='followups'){
            $addRow(array('id','campaign_id','contact_id','due_at','status','priority'));
            foreach($this->db->rows('SELECT * FROM '.$this->table('ads_followup_tasks').' ORDER BY id DESC LIMIT 10000') as $r){$addRow(array($r['id'],$r['campaign_id'],$r['contact_id'],Records::iso($r['due_at']),$r['status'],$r['priority']));}
        }elseif($kind==='audience'){
            $cid=isset($q['campaign_id'])?DataRoutes::integer($q['campaign_id'],'campaign_id',1):0;
            if($cid<=0){throw new AuthFailure(400,'campaign_id is required');}
            $addRow(array('contact_id','phone_number','email_address','creative_id','send_status','skip_reason','reply_status','sent_at'));
            foreach($this->db->rows('SELECT a.*,c.email FROM '.$this->table('ads_assignments').' a LEFT JOIN '.$this->table('contacts').' c ON c.id=a.contact_id WHERE a.campaign_id=%d ORDER BY a.id ASC LIMIT 10000',array($cid)) as $r){
                $addRow(array($r['contact_id'],$r['phone_number'],$r['email'],$r['creative_id'],$r['send_status'],$r['skip_reason'],$r['reply_status'],Records::iso($r['sent_at']??null)));
            }
        }elseif($kind==='creatives'){
            $addRow(array('id','campaign_id','name','status','assigned','sent','delivered','delivery_rate','replies','reply_rate'));
            foreach($this->db->rows('SELECT * FROM '.$this->table('ads_creatives').' WHERE is_deleted=0 ORDER BY id ASC LIMIT 5000') as $cr){
                $st=$this->countsWhere('creative_id=%d',array($cr['id']),(int)$cr['campaign_id'],(int)$cr['set_id'],(int)$cr['id']);
                $addRow(array($cr['id'],$cr['campaign_id'],$cr['name'],$cr['status'],$st['assigned'],$st['sent'],$st['delivered'],$st['delivery_rate'],$st['replies'],$st['reply_rate']));
            }
        }else{throw new AuthFailure(400,'Unknown export type');}
        $dl=new ContactCsvDownload(implode("\n",$lines)."\n");
        $dl->filename='ads-'.$kind.'.csv';
        return $dl;
    }
    public static function handle($request,array $c,array $user,&$status){
        $path=trim(substr($request->get_route(),strlen('/sender-suite/v1/')),'/');$method=$request->get_method();
        if(strlen($request->get_body())>524288){throw new AuthFailure(413,'Ads Manager request exceeds size limit.');}
        $s=new self($c['db'],$c['config']??null);$q=$request->get_query_params();
        $adminReq=function()use($user){if(($user['role']??'')!=='admin'){throw new AuthFailure(403,'Application administrator role required.');}};
        if($path==='ads/overview'&&$method==='GET'){return $s->overview($q);}
        if($path==='ads/reference'&&$method==='GET'){return $s->reference();}
        if($path==='ads/activity'&&$method==='GET'){return $s->activity($q);}
        if($path==='ads/search'&&$method==='GET'){return $s->search($q);}
        if(preg_match('~^ads/export/([a-z_]+)$~D',$path,$m)&&$method==='GET'){return $s->exportCsv($m[1],$q);}
        if($path==='ads/campaigns'&&$method==='GET'){return $s->listCampaigns($q);}
        if($path==='ads/campaigns'&&$method==='POST'){$adminReq();return $c['db']->transaction(function()use($s,$request,$user,&$status){return $s->createCampaign(DataRoutes::body($request),$user,$status);});}
        if(preg_match('~^ads/campaigns/(-?[0-9]+)$~D',$path,$m)){
            $id=DataRoutes::integer($m[1],'campaign_id');
            if($method==='GET'){return $s->getCampaign($id);}
            $adminReq();
            if($method==='PATCH'){return $c['db']->transaction(function()use($s,$id,$request,$user){return $s->updateCampaign($id,DataRoutes::body($request),$user);});}
            if($method==='DELETE'){$c['db']->transaction(function()use($s,$id){$s->deleteCampaign($id);});$status=204;return null;}
        }
        if(preg_match('~^ads/campaigns/(-?[0-9]+)/(validate|simulate|pause|complete|archive|duplicate|audience|jobs|send-logs|analytics|optimization|activity)$~D',$path,$m)){
            $id=DataRoutes::integer($m[1],'campaign_id');$op=$m[2];
            if($method==='GET'){
                if($op==='audience'){return $s->campaignAudience($id,$q);}
                if($op==='jobs'){return $s->campaignJobs($id);}
                if($op==='send-logs'){return $s->campaignSendLogs($id,$q);}
                if($op==='analytics'){return $s->campaignAnalytics($id);}
                if($op==='optimization'){$camp=$s->campaignOr404($id);return array('campaign_id'=>$id,'auto_optimize'=>(bool)$camp['auto_optimize'],'mode'=>$camp['optimization_mode']??'manual','metric'=>$camp['optimize_metric']??'reply_rate','min_sends'=>(int)($camp['optimize_min_sends']??30),'last_run_at'=>Records::iso($camp['optimize_last_run_at']??null));}
                if($op==='activity'){return $s->activity($q,$id);}
            }
            if($method==='POST'){
                if($op==='validate'){return $c['db']->transaction(function()use($s,$id){return $s->validateCampaign($id);});}
                if($op==='simulate'){return $s->simulateCampaign($id);}
                $adminReq();
                if($op==='pause'){return $c['db']->transaction(function()use($s,$id,$user){return $s->transitionState($id,'paused','Paused manually',$user);});}
                if($op==='complete'){return $c['db']->transaction(function()use($s,$id,$user){return $s->transitionState($id,'completed','Completed manually',$user);});}
                if($op==='archive'){return $c['db']->transaction(function()use($s,$id,$user){return $s->transitionState($id,'archived','Archived manually',$user);});}
                if($op==='duplicate'){return $c['db']->transaction(function()use($s,$id,$user,&$status){return $s->duplicateCampaign($id,$user,$status);});}
            }
        }
        if(preg_match('~^ads/campaigns/(-?[0-9]+)/audience/(-?[0-9]+)$~D',$path,$m)&&$method==='DELETE'){
            $adminReq();$cid=DataRoutes::integer($m[1],'campaign_id');$aid=DataRoutes::integer($m[2],'assignment_id');
            $c['db']->transaction(function()use($s,$cid,$aid){$s->removeAssignment($cid,$aid);});$status=204;return null;
        }
        if(preg_match('~^ads/campaigns/(-?[0-9]+)/sets$~D',$path,$m)){
            $cid=DataRoutes::integer($m[1],'campaign_id');
            if($method==='GET'){return $s->listSets($cid);}
            if($method==='POST'){$adminReq();return $c['db']->transaction(function()use($s,$cid,$request,$user,&$status){return $s->createSet($cid,DataRoutes::body($request),$user,$status);});}
        }
        if($path==='ads/sets/preview'&&$method==='POST'){$b=DataRoutes::body($request);if(!is_object($b)){throw new ValidationFailure('Supply a JSON object');}return $s->previewTargeting((array)$b,$b->channel??'sms');}
        if(preg_match('~^ads/sets/(-?[0-9]+)(?:/(preview|creatives|duplicate))?$~D',$path,$m)){
            $sid=DataRoutes::integer($m[1],'set_id');$sub=$m[2]??null;
            if($sub===null){
                $adminReq();
                if($method==='PATCH'){return $c['db']->transaction(function()use($s,$sid,$request,$user){return $s->updateSet($sid,DataRoutes::body($request),$user);});}
                if($method==='DELETE'){$c['db']->transaction(function()use($s,$sid){$s->deleteSet($sid);});$status=204;return null;}
            }
            if($sub==='preview'&&$method==='GET'){$set=$s->setOr404($sid);$camp=$s->campaignOr404($set['campaign_id']);return $s->previewTargeting($set,$camp['channel']??'sms');}
            if($sub==='creatives'){
                if($method==='GET'){return $s->listCreatives($sid);}
                if($method==='POST'){$adminReq();return $c['db']->transaction(function()use($s,$sid,$request,$user,&$status){return $s->createCreative($sid,DataRoutes::body($request),$user,$status);});}
            }
            if($sub==='duplicate'&&$method==='POST'){$adminReq();$set=$s->setOr404($sid);return $c['db']->transaction(function()use($s,$set,$user,&$status){return $s->createSet($set['campaign_id'],(object)array_merge($set,array('name'=>substr($set['name'].' (Copy)',0,255))),$user,$status);});}
        }
        if(preg_match('~^ads/creatives/(-?[0-9]+)(?:/(versions|analytics|duplicate|promote))?$~D',$path,$m)){
            $crid=DataRoutes::integer($m[1],'creative_id');$sub=$m[2]??null;
            if($sub===null){
                $adminReq();
                if($method==='PATCH'){return $c['db']->transaction(function()use($s,$crid,$request,$user){return $s->updateCreative($crid,DataRoutes::body($request),$user);});}
                if($method==='DELETE'){$c['db']->transaction(function()use($s,$crid){$s->deleteCreative($crid);});$status=204;return null;}
            }
            if($sub==='versions'&&$method==='GET'){return $s->creativeVersions($crid);}
            if($sub==='analytics'&&$method==='GET'){$cr=$s->creativeOr404($crid);$camp=$s->campaignOr404($cr['campaign_id']);$st=$s->countsWhere('creative_id=%d',array($crid),(int)$cr['campaign_id'],(int)$cr['set_id'],$crid);return array('creative'=>$s->creativeOut($cr),'stats'=>$st,'score'=>self::score($camp['objective']??'replies',$st));}
            if($sub==='duplicate'&&$method==='POST'){$adminReq();$cr=$s->creativeOr404($crid);return $c['db']->transaction(function()use($s,$cr,$user,&$status){return $s->createCreative($cr['set_id'],(object)array('name'=>substr($cr['name'].' (Copy)',0,255),'body'=>$cr['body'],'cta'=>$cr['cta'],'subject'=>$cr['subject'],'html_body'=>$cr['html_body'],'allocation'=>$cr['allocation']),$user,$status);});}
            if($sub==='promote'&&$method==='POST'){$adminReq();return $c['db']->transaction(function()use($s,$crid,$user){return $s->promoteCreative($crid,$user);});}
        }
        if($path==='ads/audiences'){
            if($method==='GET'){return $s->listAudiences();}
            if($method==='POST'){$adminReq();return $c['db']->transaction(function()use($s,$request,$user,&$status){return $s->createAudience(DataRoutes::body($request),$user,$status);});}
        }
        if(preg_match('~^ads/audiences/(-?[0-9]+)(?:/(preview|duplicate|attach))?$~D',$path,$m)){
            $aid=DataRoutes::integer($m[1],'audience_id');$sub=$m[2]??null;
            if($sub===null){
                if($method==='GET'){return $s->audienceOut($s->audienceOr404($aid));}
                $adminReq();
                if($method==='PATCH'){return $c['db']->transaction(function()use($s,$aid,$request){return $s->updateAudience($aid,DataRoutes::body($request));});}
                if($method==='DELETE'){$c['db']->transaction(function()use($s,$aid){$s->deleteAudience($aid);});$status=204;return null;}
            }
            if($sub==='preview'&&$method==='GET'){return $s->previewTargeting($s->audienceOr404($aid),DataRoutes::text($q,'channel','sms',null,20));}
            if($sub==='duplicate'&&$method==='POST'){$adminReq();$aud=$s->audienceOr404($aid);return $c['db']->transaction(function()use($s,$aud,$user,&$status){return $s->createAudience((object)array_merge($aud,array('name'=>substr($aud['name'].' (Copy)',0,255))),$user,$status);});}
            if($sub==='attach'&&$method==='POST'){
                $adminReq();$b=DataRoutes::body($request);$aud=$s->audienceOr404($aid);
                $cid=DataRoutes::integer($b->campaign_id??0,'campaign_id',1);
                return $c['db']->transaction(function()use($s,$cid,$aud,$user,&$status){return $s->createSet($cid,(object)array_merge($aud,array('audience_id'=>$aud['id'],'name'=>$aud['name'])),$user,$status);});
            }
        }
        if(preg_match('~^ads/campaigns/(-?[0-9]+)/followup-steps(?:/(-?[0-9]+))?$~D',$path,$m)){
            $cid=DataRoutes::integer($m[1],'campaign_id');$stid=isset($m[2])?DataRoutes::integer($m[2],'step_id'):null;
            if($stid===null&&$method==='GET'){return $s->listSteps($cid);}
            $adminReq();
            if($stid===null&&$method==='POST'){return $c['db']->transaction(function()use($s,$cid,$request,&$status){return $s->createStep($cid,DataRoutes::body($request),$status);});}
            if($stid!==null&&$method==='PATCH'){return $c['db']->transaction(function()use($s,$stid,$request){return $s->updateStep($stid,DataRoutes::body($request));});}
            if($stid!==null&&$method==='DELETE'){return $c['db']->transaction(function()use($s,$stid){return $s->deleteStep($stid);});}
        }
        if(preg_match('~^ads/followup-steps/(-?[0-9]+)$~D',$path,$m)){
            $stid=DataRoutes::integer($m[1],'step_id');
            if($method==='GET'){$st=$c['db']->one('ads_followup_steps',array('id'=>$stid));if(!$st||$st['deleted_at']!==null){throw new AuthFailure(404,'Step not found');}return $s->stepOut($st);}
            $adminReq();
            if($method==='PATCH'){return $c['db']->transaction(function()use($s,$stid,$request){return $s->updateStep($stid,DataRoutes::body($request));});}
            if($method==='DELETE'){return $c['db']->transaction(function()use($s,$stid){return $s->deleteStep($stid);});}
        }
        if($path==='ads/followup-templates'){
            if($method==='GET'){return $s->listFollowupTemplates($q);}
            if($method==='POST'){$adminReq();return $c['db']->transaction(function()use($s,$request,&$status){return $s->createFollowupTemplate(DataRoutes::body($request),$status);});}
        }
        if(preg_match('~^ads/followup-templates/(-?[0-9]+)$~D',$path,$m)){
            $tid=DataRoutes::integer($m[1],'template_id');
            if($method==='GET'){$t=$c['db']->one('ads_followup_templates',array('id'=>$tid));if(!$t){throw new AuthFailure(404,'Template not found');}return $s->tplOut($t);}
            $adminReq();
            if($method==='PATCH'){return $c['db']->transaction(function()use($s,$tid,$request){return $s->updateFollowupTemplate($tid,DataRoutes::body($request));});}
            if($method==='DELETE'){return $c['db']->transaction(function()use($s,$tid){return $s->deleteFollowupTemplate($tid);});}
        }
        if($path==='ads/followups'){
            if($method==='GET'){return $s->listFollowups($q);}
            if($method==='POST'){return $c['db']->transaction(function()use($s,$request,&$status){return $s->createFollowup(DataRoutes::body($request),$status);});}
        }
        if(preg_match('~^ads/followups/(-?[0-9]+)/([a-z_]+)$~D',$path,$m)&&$method==='POST'){
            return $c['db']->transaction(function()use($s,$m){return $s->followupAction(DataRoutes::integer($m[1],'task_id'),$m[2]);});
        }
        if($path==='ads/calendar'){
            if($method==='GET'){return $s->listCalendar($q);}
            if($method==='POST'){return $c['db']->transaction(function()use($s,$request,&$status){return $s->createCalendar(DataRoutes::body($request),$status);});}
        }
        if(preg_match('~^ads/calendar/(-?[0-9]+)$~D',$path,$m)){
            $eid=DataRoutes::integer($m[1],'event_id');
            if($method==='PATCH'){return $c['db']->transaction(function()use($s,$eid,$request){return $s->updateCalendar($eid,DataRoutes::body($request));});}
            if($method==='DELETE'){$c['db']->transaction(function()use($s,$eid){$s->deleteCalendar($eid);});$status=204;return null;}
        }
        if($path==='ads/suppression'){
            if($method==='GET'){return $s->listSuppression($q);}
            if($method==='POST'){return $c['db']->transaction(function()use($s,$request,$user,&$status){return $s->addSuppression(DataRoutes::body($request),$user,$status);});}
        }
        if(preg_match('~^ads/suppression/(-?[0-9]+)$~D',$path,$m)&&$method==='DELETE'){
            $eid=DataRoutes::integer($m[1],'entry_id');
            $c['db']->transaction(function()use($s,$eid){$s->removeSuppression($eid);});$status=204;return null;
        }
        if(preg_match('~^ads/contacts/(-?[0-9]+)/(action|timeline)$~D',$path,$m)){
            $cid=DataRoutes::integer($m[1],'contact_id');
            if($m[2]==='timeline'&&$method==='GET'){return $s->contactTimeline($cid);}
            if($m[2]==='action'&&$method==='POST'){return $c['db']->transaction(function()use($s,$cid,$request){return $s->contactAction($cid,DataRoutes::body($request));});}
        }
        if(preg_match('~^ads/(?:campaigns/bulk|creatives/bulk|contacts/bulk|track/click|followups/process|sets/-?[0-9]+/pause-losers|campaigns/-?[0-9]+/(?:launch|resume|dispatch|contacts|rebuild-audience|optimize|jobs/retry|audience/bulk-remove))$~D',$path)){
            return (new CampaignWorker($c['db'],$c['config']??null))->handleAdsRoute($path,$method,$request,$user,$status);
        }
        throw new AuthFailure(404,'Not found');
    }
}
