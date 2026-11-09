<?php
namespace SenderSuite;

/** Classic campaign follow-up rule CRUD, dry-run preview and logs. Execution remains worker-gated. */
final class CampaignFollowups {
    const FENCE='native_campaign_followups_fence';
    private $db;private $records;private $timezone;
    public function __construct(Database $db,callable $clock=null,$timezone='Africa/Lagos'){$this->db=$db;$this->records=new Records($db,$clock);$this->timezone=$timezone?:'Africa/Lagos';}
    private function table($n){return Schema::identifier($this->db->schema()->table($n));}
    public static function definitions(){return array('/campaign-followups/campaigns'=>array('GET'),'/campaign-followups/campaigns/(?P<campaign_id>-?[0-9]+)'=>array('GET','POST'),'/campaign-followups/campaigns/(?P<campaign_id>-?[0-9]+)/process-now'=>array('POST'),'/campaign-followups/(?P<followup_id>-?[0-9]+)'=>array('PUT','DELETE'),'/campaign-followups/(?P<followup_id>-?[0-9]+)/(?P<action>preview|log)'=>array('GET'),'/campaign-followups/(?P<followup_id>-?[0-9]+)/run'=>array('POST'));}
    private function stamp($old){$now=$this->records->now();return $now<=$old?(new \DateTimeImmutable($old,new \DateTimeZone('UTC')))->modify('+1 microsecond')->format('Y-m-d H:i:s.u'):$now;}
    private function fence(){
        $this->db->requireTransaction();$r=$this->db->one('system_settings',array('key'=>self::FENCE));
        if(!$r){$this->records->create('system_settings',array('key'=>self::FENCE,'value'=>'1','category'=>'native_campaign_followup'));return;}
        $stamp=$this->stamp($r['updated_at']);if($this->db->update('system_settings',array('value'=>$stamp,'updated_at'=>$stamp),array('id'=>$r['id'],'value'=>$r['value'],'updated_at'=>$r['updated_at']))!==1){throw new AuthFailure(409,'Follow-up configuration changed. Reload and retry.');}
    }
    private function campaign($id){$rows=$this->db->rows('SELECT id,name,status,channel,total_contacts,messages_sent,replies FROM '.$this->table('campaigns').' WHERE id=%d',array($id));if(!$rows){throw new AuthFailure(404,'Campaign not found');}return $this->db->decode('campaigns',$rows[0]);}
    private function rule($id){$row=$this->db->one('campaign_followups',array('id'=>$id));if(!$row){throw new AuthFailure(404,'Follow-up not found');}return $row;}
    private function out(array $r){
        return array('id'=>(int)$r['id'],'campaign_id'=>(int)$r['campaign_id'],'step_order'=>(int)$r['step_order'],'channel'=>$r['channel']?:'sms','subject'=>$r['subject'],'name'=>$r['name'],'message_text'=>$r['message_text'],'template_id'=>$r['template_id']!==null?(int)$r['template_id']:null,'delay_minutes'=>(int)$r['delay_minutes'],'stop_on_reply'=>(bool)$r['stop_on_reply'],'stop_on_opt_out'=>(bool)$r['stop_on_opt_out'],'stop_on_lead_status'=>$r['stop_on_lead_status'],'send_start_hour'=>$r['send_start_hour']!==null?(int)$r['send_start_hour']:null,'send_end_hour'=>$r['send_end_hour']!==null?(int)$r['send_end_hour']:null,'is_active'=>(bool)$r['is_active'],'sent_count'=>(int)($r['sent_count']??0),'stopped_count'=>(int)($r['stopped_count']??0),'created_at'=>Records::iso($r['created_at']),'updated_at'=>Records::iso($r['updated_at']));
    }
    private static function displayName($c,$fallback=''){
        if(!$c){return $fallback;}
        $person=Compat::strip(Compat::strip($c['first_name']??'').' '.Compat::strip($c['last_name']??''));if($person!==''){return $person;}
        foreach(array('business_name','phone_number','email') as $k){$v=Compat::strip($c[$k]??'');if($v!==''){return $v;}}
        return $fallback;
    }
    public function input($body,$update=false){
        if(!is_object($body)){throw new ValidationFailure('Supply a follow-up JSON object');}
        $defaults=array('name'=>null,'channel'=>'sms','subject'=>null,'message_text'=>null,'template_id'=>null,'delay_minutes'=>1440,'step_order'=>null,'stop_on_reply'=>true,'stop_on_opt_out'=>true,'stop_on_lead_status'=>null,'send_start_hour'=>null,'send_end_hour'=>null,'is_active'=>true);
        $out=$update?array():$defaults;
        foreach($body as $key=>$value){
            if($key==='idempotency_key'&&!$update){continue;}
            if(!array_key_exists($key,$defaults)){throw new ValidationFailure('Unknown follow-up field',$key);}
            if($value===null){
                if(in_array($key,array('channel','delay_minutes','stop_on_reply','stop_on_opt_out','is_active'),true)){throw new ValidationFailure('Input must not be null',$key);}
                $out[$key]=null;continue;
            }
            if($key==='template_id'){$value=DataRoutes::integer($value,$key,1);}
            elseif($key==='delay_minutes'){$value=DataRoutes::integer($value,$key,1,129600);}
            elseif($key==='step_order'){$value=DataRoutes::integer($value,$key,1,50);}
            elseif(in_array($key,array('send_start_hour','send_end_hour'),true)){$value=DataRoutes::integer($value,$key,0,23);}
            elseif(in_array($key,array('stop_on_reply','stop_on_opt_out','is_active'),true)){$value=DataRoutes::boolean($value,$key);}
            else{
                if(!is_string($value)||strpos($value,"\0")!==false||preg_match('//u',$value)!==1){throw new ValidationFailure('Supply valid text without NUL',$key);}
                $limit=array('name'=>200,'channel'=>10,'subject'=>500,'message_text'=>5000,'stop_on_lead_status'=>500)[$key];
                if(Compat::length($value)>$limit){throw new ValidationFailure('Follow-up text exceeds the field limit',$key);}
                if($key==='message_text'){$v=Compat::strip($value);$value=$v===''?null:$v;}
                elseif($key==='subject'){$v=Compat::strip($value);$value=$v===''?null:$v;}
                elseif($key==='channel'){$value=Compat::lower(Compat::strip($value));if(!in_array($value,array('sms','email'),true)){throw new ValidationFailure("channel must be 'sms' or 'email'",$key);}}
            }
            $out[$key]=$value;
        }
        return $out;
    }
    public function listCampaigns(array $q=array()){
        $status=DataRoutes::text($q,'status',null,null,100);$channel=DataRoutes::text($q,'channel',null,null,32);
        if($channel!==null&&$channel!==''&&!in_array($channel,array('sms','email','all'),true)){throw new AuthFailure(422,"channel must be 'sms', 'email' or 'all' — got '".$channel."'.");}
        $where=array('1=1');$args=array();
        if($status!==null&&$status!==''){$where[]='status=%s';$args[]=$status;}
        if($channel==='sms'||$channel==='email'){$where[]="COALESCE(channel,'sms')=%s";$args[]=$channel;}
        $count=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('campaigns').' WHERE '.implode(' AND ',$where),$args)[0]['n'];if($count>10000){throw new AuthFailure(413,'Too many campaigns for synchronous follow-up listing.');}
        $rows=$this->db->rows('SELECT id,name,status,channel,total_contacts,messages_sent,replies FROM '.$this->table('campaigns').' WHERE '.implode(' AND ',$where).' ORDER BY created_at DESC,id DESC',$args);
        $ruleCounts=array();foreach($this->db->rows('SELECT campaign_id,COUNT(*) AS n FROM '.$this->table('campaign_followups').' GROUP BY campaign_id') as $r){$ruleCounts[(int)$r['campaign_id']]=(int)$r['n'];}
        $sentCounts=array();foreach($this->db->rows("SELECT campaign_id,COUNT(*) AS n FROM ".$this->table('campaign_followup_logs')." WHERE status='sent' GROUP BY campaign_id") as $r){$sentCounts[(int)$r['campaign_id']]=(int)$r['n'];}
        $items=array();
        foreach($rows as $raw){
            $c=$this->db->decode('campaigns',$raw);$cid=(int)$c['id'];
            $items[]=array('id'=>$cid,'name'=>$c['name'],'status'=>$c['status']??'draft','channel'=>$c['channel']?:'sms','total_contacts'=>(int)($c['total_contacts']??0),'messages_sent'=>(int)($c['messages_sent']??0),'replies'=>(int)($c['replies']??0),'followup_count'=>$ruleCounts[$cid]??0,'followups_sent'=>$sentCounts[$cid]??0);
        }
        return array('total'=>count($items),'items'=>$items);
    }
    public function listSteps($campaignId){
        $c=$this->campaign($campaignId);
        $rows=$this->db->rows('SELECT * FROM '.$this->table('campaign_followups').' WHERE campaign_id=%d ORDER BY step_order ASC,id ASC',array($campaignId));
        $statsByRule=array();
        foreach($this->db->rows('SELECT followup_id,status,COUNT(*) AS n FROM '.$this->table('campaign_followup_logs').' WHERE campaign_id=%d GROUP BY followup_id,status',array($campaignId)) as $sr){$fid=(int)$sr['followup_id'];if(!isset($statsByRule[$fid])){$statsByRule[$fid]=new \stdClass();}$st=(string)$sr['status'];$statsByRule[$fid]->$st=(int)$sr['n'];}
        $items=array();
        foreach($rows as $raw){$r=$this->db->decode('campaign_followups',$raw);$d=$this->out($r);$d['stats']=$statsByRule[(int)$r['id']]??new \stdClass();$items[]=$d;}
        return array('campaign'=>array('id'=>(int)$c['id'],'name'=>$c['name'],'status'=>$c['status']??'draft','total_contacts'=>(int)($c['total_contacts']??0),'messages_sent'=>(int)($c['messages_sent']??0),'replies'=>(int)($c['replies']??0)),'total'=>count($items),'items'=>$items);
    }
    public function create($campaignId,$body,array $user,$headerKey=null){
        if(($user['role']??'')!=='admin'){throw new AuthFailure(403,'Application administrator role required.');}
        $d=$this->input($body,false);$key=$body->idempotency_key??$headerKey;$ledger=null;$digest=hash('sha256',json_encode(array($campaignId,$d),JSON_THROW_ON_ERROR));
        if($key!==null&&$key!==''){if(!is_string($key)||!preg_match('/^[A-Za-z0-9._:-]{8,200}$/D',$key)){throw new ValidationFailure('Supply an 8–200 character idempotency key');}$ledger='native.campaign_followup.create.'.$campaignId.'.'.hash('sha256',$user['id'].':'.$key);}
        $this->fence();
        if($ledger!==null){
            $rows=$this->db->rows('SELECT value FROM '.$this->table('system_settings').' WHERE `key`=%s AND OCTET_LENGTH(value)<=1024',array($ledger));
            if($rows){try{$saved=json_decode($rows[0]['value'],true,8,JSON_THROW_ON_ERROR);}catch(\Throwable $e){throw new AuthFailure(503,'Follow-up idempotency ledger needs repair.');}if(!is_array($saved)||!is_int($saved['id']??null)||!is_string($saved['digest']??null)){throw new AuthFailure(503,'Follow-up idempotency ledger needs repair.');}if(!hash_equals($saved['digest'],$digest)){throw new AuthFailure(409,'Follow-up idempotency key was already used for different content.');}return $this->out($this->rule($saved['id']));}
            if($this->db->rows('SELECT id FROM '.$this->table('system_settings').' WHERE `key`=%s',array($ledger))){throw new AuthFailure(503,'Follow-up idempotency ledger exceeds the repair limit.');}
        }
        $this->campaign($campaignId);
        if(!$d['message_text']&&!$d['template_id']){throw new ValidationFailure('Write a follow-up message or choose a template','message_text');}
        if($d['template_id']!==null&&!$this->db->rows('SELECT id FROM '.$this->table('templates').' WHERE id=%d',array($d['template_id']))){throw new AuthFailure(404,'Selected template not found');}
        $order=$d['step_order'];
        if($order===null){$max=$this->db->rows('SELECT MAX(step_order) AS m FROM '.$this->table('campaign_followups').' WHERE campaign_id=%d',array($campaignId))[0]['m']??0;$order=((int)$max)+1;if($order>50){throw new ValidationFailure('A campaign can have at most 50 follow-up steps','step_order');}}
        if($this->db->rows('SELECT id FROM '.$this->table('campaign_followups').' WHERE campaign_id=%d AND step_order=%d LIMIT 1',array($campaignId,$order))){throw new AuthFailure(409,'Step '.$order.' already exists for this campaign');}
        $d['campaign_id']=(int)$campaignId;$d['step_order']=$order;
        if($d['name']===null||Compat::strip($d['name'])===''){$d['name']='Follow-up '.$order;}
        $row=$this->records->create('campaign_followups',$d);
        if($ledger!==null){$this->records->create('system_settings',array('key'=>$ledger,'value'=>json_encode(array('id'=>$row['id'],'digest'=>$digest),JSON_THROW_ON_ERROR),'category'=>'native_campaign_followup_idempotency'));}
        return $this->out($row);
    }
    public function update($id,$body){
        $d=$this->input($body,true);$this->fence();$row=$this->rule($id);
        if(array_key_exists('template_id',$d)&&$d['template_id']!==null&&!$this->db->rows('SELECT id FROM '.$this->table('templates').' WHERE id=%d',array($d['template_id']))){throw new AuthFailure(404,'Selected template not found');}
        if(isset($d['step_order'])&&(int)$d['step_order']!==(int)$row['step_order']){
            if($this->db->rows('SELECT id FROM '.$this->table('campaign_followups').' WHERE campaign_id=%d AND step_order=%d AND id<>%d LIMIT 1',array($row['campaign_id'],$d['step_order'],$id))){throw new AuthFailure(409,'Step '.$d['step_order'].' already exists');}
        }
        $merged=array_replace($row,$d);
        if(!$merged['message_text']&&!$merged['template_id']){throw new ValidationFailure('A follow-up needs a message or a template','message_text');}
        foreach($d as $k=>$v){if($v===$row[$k]){unset($d[$k]);}}
        if($d){
            $d['updated_at']=$this->stamp($row['updated_at']);
            if($this->db->update('campaign_followups',$d,array('id'=>$id,'updated_at'=>$row['updated_at']))!==1){throw new AuthFailure(409,'Follow-up changed. Reload before modifying it.');}
            $row=array_replace($row,$d);
        }
        return $this->out($row);
    }
    public function delete($id){
        $this->fence();$row=$this->rule($id);
        if((int)($row['sent_count']??0)>0||(int)($row['stopped_count']??0)>0||$this->db->rows('SELECT id FROM '.$this->table('campaign_followup_logs').' WHERE followup_id=%d LIMIT 1',array($id))){throw new AuthFailure(409,'Follow-up step has recorded history; disable it instead of deleting its audit log.');}
        if($this->db->delete('campaign_followups',array('id'=>$id,'updated_at'=>$row['updated_at']))!==1){throw new AuthFailure(409,'Follow-up changed before deletion.');}
    }
    private function withinWindow(array $rule,\DateTimeImmutable $nowUtc){
        if($rule['send_start_hour']===null||$rule['send_end_hour']===null){return true;}
        try{$tz=new \DateTimeZone($this->timezone);}catch(\Throwable $e){$tz=new \DateTimeZone('UTC');}
        $hour=(int)$nowUtc->setTimezone($tz)->format('G');
        $start=(int)$rule['send_start_hour'];$end=(int)$rule['send_end_hour'];
        if($start===$end){return true;}
        return $start<$end?($hour>=$start&&$hour<$end):($hour>=$start||$hour<$end);
    }
    private static function stopStatuses(array $rule){
        $out=array();foreach(explode(',',(string)($rule['stop_on_lead_status']??'')) as $p){$s=Compat::lower(Compat::strip($p));if($s!==''){$out[$s]=true;}}return $out;
    }
    public function preview($id){
        $rule=$this->rule($id);$campaign=$this->campaign($rule['campaign_id']);
        $totalCandidates=(int)$this->db->rows("SELECT COUNT(*) AS n FROM ".$this->table('campaign_contacts')." WHERE campaign_id=%d AND status IN ('sent','delivered','replied','queued')",array($rule['campaign_id']))[0]['n'];
        if($totalCandidates>50000){throw new AuthFailure(413,'Campaign contact count exceeds the synchronous follow-up preview limit.');}
        $candidates=$this->db->rows("SELECT contact_id,status FROM ".$this->table('campaign_contacts')." WHERE campaign_id=%d AND status IN ('sent','delivered','replied','queued') ORDER BY id ASC",array($rule['campaign_id']));
        $logs=array();foreach($this->db->rows('SELECT contact_id,status,reason FROM '.$this->table('campaign_followup_logs').' WHERE followup_id=%d ORDER BY id ASC',array($id)) as $lr){$cid=(int)$lr['contact_id'];if(!isset($logs[$cid])){$logs[$cid]=$lr;}}
        $nowStr=$this->records->now();$nowUtc=new \DateTimeImmutable($nowStr,new \DateTimeZone('UTC'));
        $stopSet=self::stopStatuses($rule);
        $buckets=array('send'=>array(),'stop'=>array(),'wait'=>array(),'done'=>array());
        foreach($candidates as $cc){
            $cid=(int)$cc['contact_id'];
            $crows=$this->db->rows('SELECT id,first_name,last_name,business_name,phone_number,email,is_opted_out,lead_status FROM '.$this->table('contacts').' WHERE id=%d',array($cid));
            $contact=$crows?$this->db->decode('contacts',$crows[0]):null;
            $name=self::displayName($contact,$contact?($contact['phone_number']??''):'');
            if(isset($logs[$cid])){
                $lg=$logs[$cid];
                $buckets['done'][]=array('contact_id'=>$cid,'name'=>$name,'status'=>$lg['status']?:'processed','reason'=>$lg['reason']);
                continue;
            }
            if(!$contact){$buckets['stop'][]=array('contact_id'=>$cid,'name'=>$name,'reason'=>'Contact no longer exists');continue;}
            if($contact['is_opted_out']){$buckets['stop'][]=array('contact_id'=>$cid,'name'=>$name,'reason'=>'Contact opted out');continue;}
            if($contact['phone_number']!==null&&$contact['phone_number']!==''&&$this->db->rows('SELECT id FROM '.$this->table('suppression_entries').' WHERE phone_number=%s LIMIT 1',array($contact['phone_number']))){$buckets['stop'][]=array('contact_id'=>$cid,'name'=>$name,'reason'=>'Number is on the suppression list');continue;}
            $prevRows=$this->db->rows("SELECT id,status,sent_at,created_at FROM ".$this->table('messages')." WHERE campaign_id=%d AND contact_id=%d AND direction='outgoing' ORDER BY created_at DESC,id DESC LIMIT 1",array($campaign['id'],$cid));
            if(!$prevRows){$buckets['wait'][]=array('contact_id'=>$cid,'name'=>$name,'reason'=>'No campaign message sent yet');continue;}
            $prev=$this->db->decode('messages',$prevRows[0]);
            if(in_array($prev['status'],array('queued','sending'),true)){$buckets['wait'][]=array('contact_id'=>$cid,'name'=>$name,'reason'=>'Previous message has not left yet');continue;}
            if(in_array($prev['status'],array('failed','cancelled'),true)){$buckets['stop'][]=array('contact_id'=>$cid,'name'=>$name,'reason'=>'Previous message failed to send');continue;}
            $prevAtStr=$prev['sent_at']?:$prev['created_at'];
            if($rule['stop_on_reply']){
                $repWhere=$prevAtStr!==null?'contact_id=%d AND direction=%s AND created_at>%s':'contact_id=%d AND direction=%s';
                $repArgs=$prevAtStr!==null?array($cid,'incoming',$prevAtStr):array($cid,'incoming');
                if($this->db->rows('SELECT id FROM '.$this->table('messages').' WHERE '.$repWhere.' LIMIT 1',$repArgs)){$buckets['stop'][]=array('contact_id'=>$cid,'name'=>$name,'reason'=>'Contact replied');continue;}
            }
            $leadLower=Compat::lower((string)($contact['lead_status']??''));
            if($stopSet&&isset($stopSet[$leadLower])){$buckets['stop'][]=array('contact_id'=>$cid,'name'=>$name,'reason'=>"Lead status is '".($contact['lead_status']??'')."'");continue;}
            $baseDt=$prevAtStr!==null?new \DateTimeImmutable($prevAtStr,new \DateTimeZone('UTC')):$nowUtc;
            $dueDt=$baseDt->modify('+'.((int)$rule['delay_minutes']).' minutes');
            if($nowUtc<$dueDt){$buckets['wait'][]=array('contact_id'=>$cid,'name'=>$name,'reason'=>'Due at '.Records::iso($dueDt->format('Y-m-d H:i:s.u')));continue;}
            if(!$this->withinWindow($rule,$nowUtc)){$buckets['wait'][]=array('contact_id'=>$cid,'name'=>$name,'reason'=>'Outside the allowed sending hours');continue;}
            $buckets['send'][]=array('contact_id'=>$cid,'name'=>$name,'reason'=>'Due');
        }
        return array('followup_id'=>(int)$rule['id'],'campaign_id'=>(int)$campaign['id'],'counts'=>array('send'=>count($buckets['send']),'stop'=>count($buckets['stop']),'wait'=>count($buckets['wait']),'done'=>count($buckets['done'])),'will_send'=>array_slice($buckets['send'],0,50),'will_stop'=>array_slice($buckets['stop'],0,50),'waiting'=>array_slice($buckets['wait'],0,50),'already_processed'=>array_slice($buckets['done'],0,50));
    }
    public function log($id,array $q=array()){
        $this->rule($id);
        $page=DataRoutes::integer($q['page']??1,'page',1,1000000);$per=DataRoutes::integer($q['per_page']??50,'per_page',1,200);
        $status=DataRoutes::text($q,'status',null,null,50);
        $where='l.followup_id=%d';$args=array($id);
        if($status!==null&&$status!==''){$where.=' AND l.status=%s';$args[]=$status;}
        $total=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('campaign_followup_logs').' l WHERE '.$where,$args)[0]['n'];
        $rows=$this->db->rows('SELECT l.id,l.contact_id,l.status,l.reason,l.body_preview,l.created_at,c.id AS contact_exists,c.first_name,c.last_name,c.business_name,c.phone_number,c.email FROM '.$this->table('campaign_followup_logs').' l LEFT JOIN '.$this->table('contacts').' c ON c.id=l.contact_id WHERE '.$where.' ORDER BY l.created_at DESC,l.id DESC LIMIT %d OFFSET %d',array_merge($args,array($per,($page-1)*$per)));
        $items=array();
        foreach($rows as $r){
            $c=$r['contact_exists']!==null?$r:null;
            $items[]=array('id'=>(int)$r['id'],'contact_id'=>(int)$r['contact_id'],'contact_name'=>self::displayName($c,$c?($c['phone_number']??''):''),'status'=>$r['status'],'reason'=>$r['reason'],'body_preview'=>$r['body_preview'],'created_at'=>Records::iso($r['created_at']));
        }
        return array('total'=>$total,'items'=>$items);
    }
    public static function handle($request,array $c,array $user,&$status){
        $path=trim(substr($request->get_route(),strlen('/sender-suite/v1/')),'/');$method=$request->get_method();$q=$request->get_query_params();
        $tz=isset($c['config'])?$c['config']->get('DEFAULT_TIMEZONE'):'Africa/Lagos';
        $s=new self($c['db'],null,$tz);
        if($method==='GET'){
            if($path==='campaign-followups/campaigns'){return $s->listCampaigns($q);}
            if(preg_match('~^campaign-followups/campaigns/(-?[0-9]+)$~D',$path,$m)){return $s->listSteps(DataRoutes::integer($m[1],'campaign_id',1));}
            if(preg_match('~^campaign-followups/(-?[0-9]+)/(preview|log)$~D',$path,$m)){$fid=DataRoutes::integer($m[1],'followup_id',1);return $m[2]==='preview'?$s->preview($fid):$s->log($fid,$q);}
            throw new AuthFailure(404,'Application endpoint not found.');
        }
        if(($user['role']??'')!=='admin'){throw new AuthFailure(403,'Application administrator role required.');}
        if(strlen($request->get_body())>65536){throw new AuthFailure(413,'Follow-up request exceeds the text limit.');}
        $hdrKey=$request->get_header('x-idempotency-key')?:$request->get_header('idempotency-key');
        if(preg_match('~^campaign-followups/campaigns/(-?[0-9]+)(/process-now)?$~D',$path,$m)&&$method==='POST'){
            $cid=DataRoutes::integer($m[1],'campaign_id',1);
            if(($m[2]??'')==='/process-now'){$s->campaign($cid);return (new CampaignWorker($c['db'],$c['config']??null))->processCampaignFollowups($cid);}
            $status=201;return $s->create($cid,DataRoutes::body($request),$user,$hdrKey);
        }
        if(preg_match('~^campaign-followups/(-?[0-9]+)(/run)?$~D',$path,$m)){
            $fid=DataRoutes::integer($m[1],'followup_id',1);
            if(($m[2]??'')==='/run'&&$method==='POST'){$s->rule($fid);return (new CampaignWorker($c['db'],$c['config']??null))->runCampaignFollowupRule($fid);}
            if(($m[2]??'')===''&&$method==='PUT'){return $s->update($fid,DataRoutes::body($request));}
            if(($m[2]??'')===''&&$method==='DELETE'){$s->delete($fid);$status=204;return null;}
        }
        throw new AuthFailure(404,'Application endpoint not found.');
    }
}
