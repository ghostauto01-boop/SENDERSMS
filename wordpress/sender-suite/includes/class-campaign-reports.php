<?php
namespace SenderSuite;

/** Stored classic campaign reads only. Does not reconcile, launch or execute campaigns. */
final class CampaignReports {
    private $db;private $records;
    public function __construct(Database $db,callable $clock=null){$this->db=$db;$this->records=new Records($db,$clock);}
    private function table($n){return Schema::identifier($this->db->schema()->table($n));}
    private static function fields(){return json_decode(file_get_contents(dirname(__DIR__).'/schema/campaign-out-fields.json'),true,32,JSON_THROW_ON_ERROR);}
    public static function definitions(){return array('/campaigns/?'=>array('GET'),'/campaigns/reconcile'=>array('GET'),'/campaigns/(?P<campaign_id>-?[0-9]+)'=>array('GET'),'/campaigns/(?P<campaign_id>-?[0-9]+)/analytics'=>array('GET'),'/campaigns/(?P<campaign_id>-?[0-9]+)/conversations'=>array('GET'),'/campaigns/(?P<campaign_id>-?[0-9]+)/performance'=>array('GET'));}
    private function columns(){return array_values(array_diff(self::fields(),array('kind')));}
    private function checkedRows($where,array $args,$limit,$offset=0){
        $columns=$this->columns();$names=array_map(array(Schema::class,'identifier'),$columns);$from=' FROM '.$this->table('campaigns').' WHERE '.$where.' ORDER BY updated_at DESC,id ASC LIMIT %d OFFSET %d';$params=array_merge($args,array($limit,$offset));
        $sizes=array();foreach($names as $n){$sizes[]='COALESCE(OCTET_LENGTH('.$n.'),0)';}
        $budget=$this->db->rows('SELECT COALESCE(SUM(x.bytes),0) AS n FROM (SELECT '.implode('+',$sizes).' AS bytes'.$from.') x',$params)[0]['n'];if((int)$budget>12582912){throw new AuthFailure(413,'Stored campaign data exceeds the read limit. Reduce the page size.');}
        $rows=$this->db->rows('SELECT '.implode(',',$names).$from,$params);$out=array();$bytes=0;foreach($rows as $row){$item=$this->out($row);$bytes+=strlen(json_encode($item,JSON_THROW_ON_ERROR));if($bytes>8388608){throw new AuthFailure(413,'Campaign response is too large. Reduce the page size.');}$out[]=$item;}return $out;
    }
    private function out(array $raw){
        // These are structured data, never credentials or the unexposed internal columns.
        foreach(array('email_account_ids','email_sender_weights','sender_daily_limits') as $key){if($raw[$key]!==null){try{json_decode($raw[$key],false,32,JSON_THROW_ON_ERROR);}catch(\JsonException $e){throw new AuthFailure(503,'Stored campaign sender configuration needs repair.');}}}
        $rawAttachments=$raw['attachments'];$raw['attachments']=null;$r=$this->db->decode('campaigns',$raw);$r['kind']='campaign';$r['attachments']=Attachments::summary($rawAttachments);
        foreach(array('track_opens','track_clicks','allow_weekends') as $key){if($r[$key]===null){$r[$key]=true;}}if($r['plain_personal_email']===null){$r['plain_personal_email']=false;}
        foreach(array('total_contacts','messages_sent','messages_delivered','messages_failed','replies','interested') as $key){$r[$key]=$r[$key]??0;}
        foreach(array('channel'=>'sms','sender_distribution'=>'round_robin','status'=>'draft') as $key=>$default){$r[$key]=$r[$key]??$default;}
        foreach(array('scheduled_at','scheduled_start_at','started_at','paused_at','completed_at','created_at','updated_at') as $key){$r[$key]=Records::iso($r[$key]??(in_array($key,array('created_at','updated_at'),true)?$this->records->now():null));}
        return $r;
    }
    public function listing(array $q){
        $page=DataRoutes::integer($q['page']??1,'page',1,1000000);$per=DataRoutes::integer($q['per_page']??25,'per_page',1,100);$channel=Analytics::channel(DataRoutes::text($q,'channel','sms',null,32));$where=array('1=1');$args=array();if($channel!=='all'){$where[]="COALESCE(channel,'sms')=%s";$args[]=$channel;}
        foreach(array('status','search') as $key){$value=DataRoutes::text($q,$key,null,null,$key==='status'?100:1000);if($value!==null&&$value!==''){$where[]=$key==='status'?'status=%s':'LOWER(name) LIKE LOWER(%s)';$args[]=$key==='status'?$value:'%'.$value.'%';}}
        $clause=implode(' AND ',$where);$n=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('campaigns').' WHERE '.$clause,$args)[0]['n'];return array('total'=>$n,'items'=>$this->checkedRows($clause,$args,$per,($page-1)*$per));
    }
    public function read($id){$rows=$this->checkedRows('id=%d',array($id),1);if(!$rows){throw new AuthFailure(404,'Campaign not found');}return $rows[0];}
    public function analytics($id){
        $keys=array('id','name','status','total_contacts','messages_sent','messages_delivered','messages_failed','replies','interested','started_at','completed_at');$rows=$this->db->rows('SELECT '.implode(',',array_map(array(Schema::class,'identifier'),$keys)).' FROM '.$this->table('campaigns').' WHERE id=%d',array($id));if(!$rows){throw new AuthFailure(404,'Campaign not found');}$r=$this->db->decode('campaigns',$rows[0]);
        foreach(array('total_contacts','messages_sent','messages_delivered','messages_failed','replies','interested') as $key){if($r[$key]===null){throw new AuthFailure(503,'Stored campaign counters need repair.');}}
        $r['delivery_rate']=Analytics::rate($r['messages_delivered'],max($r['messages_sent'],1));$r['reply_rate']=Analytics::rate($r['replies'],max($r['messages_sent'],1));foreach(array('started_at','completed_at') as $key){$r[$key]=Records::iso($r[$key]);}
        $statuses=$this->db->rows('SELECT status,COUNT(*) AS n FROM '.$this->table('campaign_contacts').' WHERE campaign_id=%d GROUP BY status LIMIT 1001',array($id));if(count($statuses)>1000){throw new AuthFailure(413,'Too many campaign contact statuses.');}$r['contact_statuses']=new \stdClass();foreach($statuses as $s){$key=$s['status']??'null';$r['contact_statuses']->$key=(int)$s['n'];}return $r;
    }
    public function performance($id){
        $rows=$this->db->rows('SELECT id,name,status,channel,total_contacts FROM '.$this->table('campaigns').' WHERE id=%d',array($id));if(!$rows){throw new AuthFailure(404,'Campaign not found');}$r=$this->db->decode('campaigns',$rows[0]);
        $channel=Compat::lower(Compat::strip($r['channel']??'sms'))?:'sms';
        $m=$this->db->rows("SELECT COALESCE(SUM(CASE WHEN status IN ('sent','delivered') THEN 1 ELSE 0 END),0) AS sent,COALESCE(SUM(CASE WHEN status='delivered' THEN 1 ELSE 0 END),0) AS delivered,COALESCE(SUM(CASE WHEN status IN ('failed','cancelled') THEN 1 ELSE 0 END),0) AS failed,COALESCE(SUM(CASE WHEN status IN ('queued','sending') THEN 1 ELSE 0 END),0) AS queued FROM ".$this->table('messages')." WHERE campaign_id=%d AND direction='outgoing'",array($id))[0];
        $sent=(int)$m['sent'];$delivered=(int)$m['delivered'];$failed=(int)$m['failed'];$queued=(int)$m['queued'];
        $convWhere="COALESCE(v.channel,'sms')=%s AND (v.campaign_id=%d OR v.last_campaign_id=%d)";$convArgs=array($channel,$id,$id);
        $v=$this->db->rows("SELECT COUNT(*) AS leads,COALESCE(SUM(CASE WHEN v.unread_count>0 OR v.status='unread' THEN 1 ELSE 0 END),0) AS unread,COALESCE(SUM(CASE WHEN v.status='interested' THEN 1 ELSE 0 END),0) AS interested,COALESCE(SUM(CASE WHEN EXISTS (SELECT 1 FROM ".$this->table('messages')." r WHERE r.conversation_id=v.id AND r.direction='incoming') THEN 1 ELSE 0 END),0) AS replied FROM ".$this->table('conversations').' v WHERE '.$convWhere,$convArgs)[0];
        $leads=(int)$v['leads'];$replied=(int)$v['replied'];$unread=(int)$v['unread'];$interested=(int)$v['interested'];
        $sentiment=array('positive'=>0,'negative'=>0,'neutral'=>0);
        foreach($this->db->rows("SELECT LOWER(COALESCE(m.ai_sentiment,'neutral')) AS s,COUNT(*) AS n FROM ".$this->table('messages').' m JOIN '.$this->table('conversations').' v ON v.id=m.conversation_id WHERE m.direction=%s AND '.$convWhere." GROUP BY LOWER(COALESCE(m.ai_sentiment,'neutral'))",array_merge(array('incoming'),$convArgs)) as $s){if(array_key_exists($s['s'],$sentiment)){$sentiment[$s['s']]+=(int)$s['n'];}}
        $opted=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('contacts').' WHERE is_opted_out=1 AND id IN (SELECT contact_id FROM '.$this->table('campaign_contacts').' WHERE campaign_id=%d)',array($id))[0]['n'];
        return array('campaign_id'=>(int)$id,'name'=>$r['name'],'status'=>$r['status']??'draft','channel'=>$channel,'audience'=>(int)($r['total_contacts']??0),'sent'=>$sent,'delivered'=>$delivered,'failed'=>$failed,'queued'=>$queued,'leads'=>$leads,'replied'=>$replied,'unread'=>$unread,'interested'=>$interested,'opted_out'=>$opted,'sentiment'=>$sentiment,'delivery_rate'=>Analytics::rate($delivered,$sent),'reply_rate'=>Analytics::rate($replied,$sent),'positive_rate'=>Analytics::rate($sentiment['positive'],$replied),'failure_rate'=>Analytics::rate($failed,$sent+$failed),'opt_out_rate'=>Analytics::rate($opted,$sent));
    }
    public function reconcile(array $q){
        $raw=DataRoutes::text($q,'campaign_ids',null,null,2000);$wanted=array();
        if($raw!==null&&$raw!==''){foreach(explode(',',$raw) as $tok){$tok=Compat::strip($tok);if($tok!==''&&preg_match('/^[0-9]+$/D',$tok)){$n=DataRoutes::integer($tok,'campaign_ids',0);$wanted[]=$n;}}if(count($wanted)>100){throw new ValidationFailure('Supply at most 100 campaign_ids','campaign_ids');}}
        $select='SELECT id,name,channel,status,total_contacts,messages_sent,messages_delivered,messages_failed,replies,scheduled_start_at,started_at,completed_at FROM '.$this->table('campaigns');
        if($wanted){$rows=$this->db->rows($select.' WHERE id IN ('.implode(',',array_fill(0,count($wanted),'%d')).') ORDER BY id ASC',$wanted);}
        else{$rows=$this->db->rows($select." WHERE status IN ('running','scheduled','paused','sending','draft') ORDER BY updated_at DESC,id DESC LIMIT 50");}
        $found=array();$snapshots=array();$active=0;
        foreach($rows as $rawRow){
            $c=$this->db->decode('campaigns',$rawRow);$cid=(int)$c['id'];$found[$cid]=true;
            $rc=$this->db->rows('SELECT COUNT(*) AS total,COALESCE(SUM(CASE WHEN is_active=1 THEN 1 ELSE 0 END),0) AS active FROM '.$this->table('campaign_followups').' WHERE campaign_id=%d',array($cid))[0];
            $logs=new \stdClass();foreach($this->db->rows('SELECT status,COUNT(*) AS n FROM '.$this->table('campaign_followup_logs').' WHERE campaign_id=%d GROUP BY status LIMIT 101',array($cid)) as $lr){$k=(string)($lr['status']??'');$logs->$k=(int)$lr['n'];}
            $status=$c['status']??'draft';$sent=(int)($c['messages_sent']??0);
            $dispatched=in_array($status,array('running','sending','completed'),true)||$sent>0||($status==='scheduled'&&$c['scheduled_start_at']!==null);
            if(in_array($status,array('running','sending','scheduled','paused'),true)){$active++;}
            $snapshots[]=array('id'=>$cid,'name'=>$c['name'],'channel'=>$c['channel']?:'sms','status'=>$status,'total_contacts'=>(int)($c['total_contacts']??0),'messages_sent'=>$sent,'messages_delivered'=>(int)($c['messages_delivered']??0),'messages_failed'=>(int)($c['messages_failed']??0),'replies'=>(int)($c['replies']??0),'scheduled_start_at'=>Records::iso($c['scheduled_start_at']),'started_at'=>Records::iso($c['started_at']),'completed_at'=>Records::iso($c['completed_at']),'followup_rules_total'=>(int)$rc['total'],'followup_rules_active'=>(int)$rc['active'],'followup_logs'=>$logs,'already_active_or_sent'=>$dispatched,'safe_action'=>$dispatched?'DO_NOT_REPLAY_START — campaign already active/scheduled/sent; inspect status only':'SAFE_TO_VALIDATE_OR_SCHEDULE_WITH_IDEMPOTENCY_KEY');
        }
        $missing=array();foreach($wanted as $wid){if(!isset($found[$wid])){$missing[]=$wid;}}
        $byStatus=new \stdClass();$pending=0;
        foreach($this->db->rows('SELECT status,COUNT(*) AS n FROM '.$this->table('scheduled_messages').' GROUP BY status LIMIT 101') as $sr){$st=(string)($sr['status']??'');$byStatus->$st=(int)$sr['n'];if($st==='pending'){$pending=(int)$sr['n'];}}
        $nextDue=$this->db->rows("SELECT MIN(schedule_at) AS due FROM ".$this->table('scheduled_messages')." WHERE status='pending'")[0]['due']??null;
        return array('reconciled_at'=>Records::iso($this->records->now()),'requested_campaign_ids'=>$wanted,'missing_campaign_ids'=>$missing,'active_or_pending_campaigns'=>$active,'campaigns'=>$snapshots,'scheduled_messages'=>array('pending_count'=>$pending,'by_status'=>$byStatus,'next_due_at'=>Records::iso($nextDue)),'replay_protection_advice'=>"Always verify 'already_active_or_sent' and 'scheduled_messages.pending_count' before re-issuing create/start/schedule/follow-up mutations, and pass an X-Idempotency-Key.");
    }
    public function conversations($id,array $q){
        $page=DataRoutes::integer($q['page']??1,'page',1,1000000);$per=DataRoutes::integer($q['per_page']??50,'per_page',1,200);$replied=isset($q['replied_only'])?DataRoutes::boolean($q['replied_only'],'replied_only'):false;
        $campaign=$this->db->rows('SELECT channel FROM '.$this->table('campaigns').' WHERE id=%d',array($id));if(!$campaign){throw new AuthFailure(404,'Campaign not found');}$channel=Compat::lower(Compat::strip($campaign[0]['channel']?:'sms'))?:'sms';
        $where="COALESCE(v.channel,'sms')=%s AND (v.campaign_id=%d OR v.last_campaign_id=%d)";$args=array($channel,$id,$id);if($replied){$where.=' AND EXISTS (SELECT 1 FROM '.$this->table('messages')." r WHERE r.conversation_id=v.id AND r.direction='incoming')";}
        $base=' FROM '.$this->table('conversations').' v WHERE '.$where;$total=(int)$this->db->rows('SELECT COUNT(*) AS n'.$base,$args)[0]['n'];
        $selection='SELECT v.id'.$base.' ORDER BY (v.last_message_at IS NULL) ASC,v.last_message_at DESC,v.id ASC LIMIT %d OFFSET %d';$params=array_merge($args,array($per,($page-1)*$per));
        $from=' FROM ('.$selection.') picked JOIN '.$this->table('conversations').' v ON v.id=picked.id LEFT JOIN '.$this->table('contacts').' c ON c.id=v.contact_id LEFT JOIN '.$this->table('messages')." m ON m.id=(SELECT r.id FROM ".$this->table('messages')." r WHERE r.conversation_id=v.id AND r.direction='incoming' ORDER BY r.created_at DESC,r.id DESC LIMIT 1)";
        $bytes=$this->db->rows('SELECT COALESCE(SUM(COALESCE(OCTET_LENGTH(v.last_message_preview),0)+COALESCE(OCTET_LENGTH(m.body),0)+COALESCE(OCTET_LENGTH(m.ai_sentiment),0)+COALESCE(OCTET_LENGTH(m.ai_intent),0)),0) AS n'.$from,$params)[0]['n'];if((int)$bytes>4194304){throw new AuthFailure(413,'Campaign conversation page is too large. Reduce the page size.');}
        $rows=$this->db->rows('SELECT v.id,v.contact_id,v.channel,v.status,v.unread_count,v.message_count,v.last_message_preview,v.last_message_at,c.id AS contact_exists,c.first_name,c.last_name,c.business_name,c.phone_number,c.email,c.lead_status,m.id AS reply_id,m.body AS reply_body,m.created_at AS reply_at,m.ai_sentiment,m.ai_intent'.$from.' ORDER BY (v.last_message_at IS NULL) ASC,v.last_message_at DESC,v.id ASC',$params);$items=array();$size=0;
        foreach($rows as $r){$exists=$r['contact_exists']!==null;$name='Unknown';if($exists){$person=Compat::strip(Compat::strip($r['first_name']??'').' '.Compat::strip($r['last_name']??''));$name=$person;if($name===''){foreach(array('business_name','phone_number','email') as $k){$name=Compat::strip($r[$k]??'');if($name!==''){break;}}}$name=$name?:'Unknown';}
            $reply=$r['reply_id']!==null?array('body'=>$r['reply_body'],'created_at'=>Records::iso($r['reply_at']),'ai_sentiment'=>$r['ai_sentiment'],'ai_intent'=>$r['ai_intent']):null;
            $item=array('conversation_id'=>(int)$r['id'],'contact_id'=>(int)$r['contact_id'],'contact_name'=>$name,'contact_phone'=>$exists?$r['phone_number']:'','contact_email'=>$exists?$r['email']:'','channel'=>$r['channel']?:$channel,'lead_status'=>$exists?$r['lead_status']:'','status'=>$r['status'],'unread_count'=>(int)$r['unread_count'],'message_count'=>(int)$r['message_count'],'last_message_preview'=>$r['last_message_preview'],'last_message_at'=>Records::iso($r['last_message_at']),'has_replied'=>$reply!==null,'last_reply'=>$reply);$size+=strlen(json_encode($item,JSON_THROW_ON_ERROR));if($size>8388608){throw new AuthFailure(413,'Campaign conversation response is too large.');}$items[]=$item;
        }return array('total'=>$total,'items'=>$items,'page'=>$page,'per_page'=>$per,'channel'=>$channel);
    }
    public static function handle($request,array $c){
        if($request->get_method()!=='GET'){throw new AuthFailure(404,'Not found');}$path=trim(substr($request->get_route(),strlen('/sender-suite/v1/')),'/');$s=new self($c['db']);
        if($path==='campaigns'){return $s->listing($request->get_query_params());}
        if($path==='campaigns/reconcile'){return $s->reconcile($request->get_query_params());}
        if(preg_match('~^campaigns/(-?[0-9]+)(/analytics|/conversations|/performance)?$~D',$path,$m)){$id=DataRoutes::integer($m[1],'campaign_id');$sub=$m[2]??'';if($sub==='/conversations'){return $s->conversations($id,$request->get_query_params());}if($sub==='/performance'){return $s->performance($id);}return $sub==='/analytics'?$s->analytics($id):$s->read($id);}
        throw new AuthFailure(404,'Not found');
    }
}
