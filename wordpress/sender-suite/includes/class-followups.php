<?php
namespace SenderSuite;

/** Stored scheduling and operator controls. No transport, queue claim or execution worker. */
final class Followups {
    const CONTROLS=array('id','contact_id','campaign_id','campaign_contact_id','sequence_id','sequence_step_order','channel','email_account_id','status','scheduled_at','executed_at','message_id','attempt_count','max_attempts','updated_at');
    private $db;
    private $records;
    private $zone;

    public function __construct(Database $db,callable $clock=null,$timezone='Africa/Lagos') {
        $this->db=$db;
        $this->records=new Records($db,$clock);
        $this->zone=new \DateTimeZone($timezone);
    }
    private function table($name) { return Schema::identifier($this->db->schema()->table($name)); }
    public static function definitions() {
        return array('/followups/?'=>array('GET','POST'),'/followups/(?P<followup_id>-?[0-9]+)'=>array('DELETE'),'/followups/(?P<followup_id>-?[0-9]+)/(?P<action>skip|reschedule|send-now)'=>array('POST'));
    }
    public function bounds() {
        $start=(new \DateTimeImmutable($this->records->now(),new \DateTimeZone('UTC')))->setTimezone($this->zone)->setTime(0,0);
        // A local calendar day is not always 86,400 seconds (DST).
        return array($start->setTimezone(new \DateTimeZone('UTC'))->format('Y-m-d H:i:s.u'),$start->modify('+1 day')->setTimezone(new \DateTimeZone('UTC'))->format('Y-m-d H:i:s.u'));
    }
    public static function date($raw) {
        if(!is_string($raw)||!preg_match('/[Tt ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:[Zz]|[+-]\d{2}:\d{2})$/D',$raw)) {
            throw new ValidationFailure('scheduled_at must include a timezone','scheduled_at');
        }
        return Calendar::date($raw,'scheduled_at');
    }
    public static function input($body) {
        if(!is_object($body)) { throw new ValidationFailure('Input should be a valid object'); }
        foreach(array('contact_id','scheduled_at','message_text') as $key) {
            if(!property_exists($body,$key)) { throw new ValidationFailure('Field required',$key); }
        }
        $out=array('contact_id'=>DataRoutes::integer($body->contact_id,'contact_id',1),'scheduled_at'=>self::date($body->scheduled_at),'message_text'=>$body->message_text,'channel'=>$body->channel??'sms','subject'=>$body->subject??null,'email_account_id'=>$body->email_account_id??null,'notify_on_due'=>false,'max_attempts'=>3);
        foreach(array('message_text'=>5000,'subject'=>500) as $key=>$cap) {
            if($out[$key]===null&&$key==='subject') { continue; }
            if(!is_string($out[$key])||preg_match('//u',$out[$key])!==1||strpos($out[$key],"\0")!==false||Compat::length($out[$key])>$cap) { throw new ValidationFailure('Invalid bounded text',$key); }
            $out[$key]=Compat::strip($out[$key]);
        }
        if($out['message_text']==='') { throw new ValidationFailure('message_text must not be blank','message_text'); }
        $out['subject']=$out['subject']===''?null:$out['subject'];
        if(!is_string($out['channel'])) { throw new ValidationFailure('channel must be a string','channel'); }
        $out['channel']=strtolower($out['channel']);
        if(!in_array($out['channel'],array('sms','email'),true)) { throw new ValidationFailure("channel must be 'sms' or 'email'",'channel'); }
        if(property_exists($body,'channel')&&$body->channel===null) { throw new ValidationFailure('channel must not be null','channel'); }
        if($out['email_account_id']!==null) { $out['email_account_id']=DataRoutes::integer($out['email_account_id'],'email_account_id'); }
        if(property_exists($body,'notify_on_due')) { $out['notify_on_due']=DataRoutes::boolean($body->notify_on_due,'notify_on_due'); }
        if(property_exists($body,'max_attempts')) { $out['max_attempts']=DataRoutes::integer($body->max_attempts,'max_attempts',1,10); }
        return $out;
    }
    private function contact($id) {
        $rows=$this->db->rows('SELECT id,first_name,last_name,business_name,phone_number,email,is_opted_out,is_email_opted_out FROM '.$this->table('contacts').' WHERE id=%d',array($id));
        return $rows?$this->db->decode('contacts',$rows[0]):null;
    }
    private function contacts(array $ids) {
        $ids=array_values(array_unique($ids));$out=array();if(!$ids) { return $out; }
        $rows=$this->db->rows('SELECT id,first_name,last_name,business_name,phone_number,email,is_opted_out,is_email_opted_out FROM '.$this->table('contacts').' WHERE id IN ('.implode(',',array_fill(0,count($ids),'%d')).')',$ids);
        foreach($rows as $row) { $c=$this->db->decode('contacts',$row);$out[$c['id']]=$c; }return $out;
    }
    private function eligible($c,$channel) {
        if(!$c) { throw new AuthFailure(404,'Contact not found'); }
        if($channel==='email') {
            if($c['is_email_opted_out']) { throw new AuthFailure(400,'Contact has opted out of email'); }
            if(Compat::strip($c['email']??'')==='') { throw new AuthFailure(400,'Contact has no email address'); }
        } elseif($c['is_opted_out']) { throw new AuthFailure(400,'Cannot create a follow-up for an opted-out contact'); }
    }
    private static function name($c) {
        if(!$c) { return 'Unknown'; }
        $name=Compat::strip(Compat::strip($c['first_name']??'').' '.Compat::strip($c['last_name']??''));
        if($name!=='') { return $name; }
        foreach(array('business_name','phone_number','email') as $key) { $v=Compat::strip($c[$key]??'');if($v!=='') { return $v; } }
        return 'Unknown';
    }
    private function item($r,$c) {
        $out=array_intersect_key($r,array_flip(array('id','contact_id','campaign_id','sequence_id','sequence_step_order','status','message_text','subject','email_account_id','attempt_count','max_attempts')));
        $out['is_overdue']=$r['status']==='pending'&&$r['scheduled_at']!==null&&$r['scheduled_at']<$this->records->now();
        $out['contact_name']=self::name($c);
        $out['contact_phone']=$c['phone_number']??null;
        $out['contact_email']=$c['email']??null;
        $out['scheduled_at']=Records::iso($r['scheduled_at']);
        $out['executed_at']=Records::iso($r['executed_at']);
        $out['channel']=$r['channel']?:'sms';
        $out['notify_on_due']=(bool)$r['notify_on_due'];
        $out['last_error']=($r['last_error']===null||$r['last_error']==='')?null:'Follow-up processing reported an error.';
        return $out;
    }
    public function listing(array $q) {
        $page=isset($q['page'])?DataRoutes::integer($q['page'],'page',1,1000000):1;
        $per=isset($q['per_page'])?DataRoutes::integer($q['per_page'],'per_page',1,100):25;
        $view=DataRoutes::text($q,'view','due_today',null,32);
        $channel=Analytics::channel(DataRoutes::text($q,'channel','sms',null,32));
        list($start,$end)=$this->bounds();$where=array('1=1');$args=array();
        if($channel!=='all') { $where[]="COALESCE(channel,'sms')=%s";$args[]=$channel; }
        if(in_array($view,array('due_today','overdue','upcoming','pending'),true)) {
            $where[]="status='pending'";
            if($view==='due_today') { $where[]='scheduled_at>=%s AND scheduled_at<%s';$args[]=$start;$args[]=$end; }
            elseif($view==='overdue') { $where[]='scheduled_at<%s';$args[]=$start; }
            elseif($view==='upcoming') { $where[]='scheduled_at>=%s';$args[]=$end; }
        } elseif($view==='completed') { $where[]="status IN ('sent','delivered')"; }
        elseif($view==='skipped') { $where[]="status IN ('skipped','cancelled','failed')"; }
        elseif($view!=='all') { throw new ValidationFailure('view must be one of due_today, overdue, upcoming, pending, completed, skipped, all','view'); }
        $filter=implode(' AND ',$where);$table=$this->table('followups');
        $total=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$table.' WHERE '.$filter,$args)[0]['n'];
        $pageArgs=array_merge($args,array($per,($page-1)*$per));
        $limited=' FROM '.$table.' WHERE '.$filter.' ORDER BY scheduled_at ASC,id ASC LIMIT %d OFFSET %d';
        $size=(int)$this->db->rows('SELECT COALESCE(SUM(COALESCE(OCTET_LENGTH(x.message_text),0)+COALESCE(OCTET_LENGTH(x.subject),0)+4096),0) AS n FROM (SELECT message_text,subject'.$limited.') x',$pageArgs)[0]['n'];
        if($size>8388608) { throw new AuthFailure(413,'Follow-up page is too large. Use a smaller page size.'); }
        $columns=array('id','contact_id','campaign_id','sequence_id','sequence_step_order','status','scheduled_at','executed_at','message_text','channel','subject','email_account_id','attempt_count','max_attempts','notify_on_due');
        $rows=$this->db->rows('SELECT '.implode(',',array_map(array(Schema::class,'identifier'),$columns)).",CASE WHEN last_error IS NULL OR OCTET_LENGTH(last_error)=0 THEN NULL ELSE 'redacted' END AS last_error".$limited,$pageArgs);
        $contacts=$this->contacts(array_column($rows,'contact_id'));
        $items=array();foreach($rows as $r) { $r=$this->db->decode('followups',$r);$items[]=$this->item($r,$contacts[$r['contact_id']]??null); }
        $out=array('total'=>$total,'items'=>$items,'page'=>$page,'per_page'=>$per,'next_page'=>$page*$per<$total?$page+1:null);
        if(strlen(json_encode($out,JSON_THROW_ON_ERROR))>8388608) { throw new AuthFailure(413,'Serialized follow-up page is too large.'); }
        return $out;
    }
    public function create($body) {
        $d=self::input($body);$c=$this->contact($d['contact_id']);$this->eligible($c,$d['channel']);
        if($d['channel']==='email') {
            if($d['subject']===null) { throw new ValidationFailure('An email follow-up needs a subject','subject'); }
            // Preparation without any sender is source-supported; never guess an unresolved sender/default.
            if($d['email_account_id']!==null) {
                $id=$d['email_account_id'];$sender=($id===0||$id===PHP_INT_MIN)?array():$this->db->rows('SELECT id FROM '.$this->table($id<0?'email_mailboxes':'email_accounts').' WHERE id=%d',array(abs($id)));
                if(!$sender) { throw new AuthFailure(404,'Selected email sender not found'); }
                throw new AuthFailure(503,'Native email sender resolution is not implemented. No follow-up was created.');
            }
            foreach(array('email_accounts','email_mailboxes') as $table) {
                if($this->db->rows('SELECT id FROM '.$this->table($table).' LIMIT 1')) { throw new AuthFailure(503,'Native email default selection is not implemented. No follow-up was created.'); }
            }
        }
        if($d['scheduled_at']<=$this->records->now()) { throw new AuthFailure(400,'Follow-up time must be in the future'); }
        return $this->item($this->records->create('followups',$d),$c);
    }
    private function find($id) {
        $rows=$this->db->rows('SELECT '.implode(',',array_map(array(Schema::class,'identifier'),self::CONTROLS)).' FROM '.$this->table('followups').' WHERE id=%d',array($id));
        if(!$rows) { throw new AuthFailure(404,'Follow-up not found'); }
        return $this->db->decode('followups',$rows[0]);
    }
    private function where($r) { return array_intersect_key($r,array_flip(self::CONTROLS)); }
    private function change($r,array $patch) {
        $stamp=$this->records->now();if($stamp<=$r['updated_at']) { $stamp=(new \DateTimeImmutable($r['updated_at'],new \DateTimeZone('UTC')))->modify('+1 microsecond')->format('Y-m-d H:i:s.u'); }
        $patch['updated_at']=$stamp;
        if($this->db->update('followups',$patch,$this->where($r))!==1) { throw new AuthFailure(409,'Follow-up changed. Reload and retry.'); }
    }
    private function membership($r,$reopen=false,$when=null) {
        if(!$r['campaign_contact_id']) {
            if($r['campaign_id']!==null||$r['sequence_id']!==null) { throw new AuthFailure(409,'Follow-up automation linkage needs repair.'); }
            return;
        }
        $cc=$this->db->one('campaign_contacts',array('id'=>$r['campaign_contact_id']));
        if(!$cc||$cc['contact_id']!==$r['contact_id']||$cc['campaign_id']!==$r['campaign_id']) { throw new AuthFailure(409,'Follow-up campaign membership is inconsistent.'); }
        if(!in_array($cc['status'],array('pending','queued','sent','completed'),true)) {
            if($reopen) { throw new AuthFailure(409,'This contact automation is no longer resumable.'); }
            return; // Do not turn a reply/opt-out/cancellation back into an active automation.
        }
        $patch=array('status'=>$reopen?'queued':'completed','next_action_at'=>$reopen?$when:null);
        $stamp=$this->records->now();if($stamp<=$cc['updated_at']) { $stamp=(new \DateTimeImmutable($cc['updated_at'],new \DateTimeZone('UTC')))->modify('+1 microsecond')->format('Y-m-d H:i:s.u'); }$patch['updated_at']=$stamp;
        if($this->db->update('campaign_contacts',$patch,array('id'=>$cc['id'],'contact_id'=>$cc['contact_id'],'campaign_id'=>$cc['campaign_id'],'status'=>$cc['status'],'next_action_at'=>$cc['next_action_at'],'updated_at'=>$cc['updated_at']))!==1) { throw new AuthFailure(409,'Campaign membership changed. Reload and retry.'); }
    }
    public function action($id,$action,$newTime=null) {
        $r=$this->find($id);
        if($action==='send-now') {
            if($r['status']!=='pending') { throw new AuthFailure(409,'Only pending follow-ups can be sent'); }
            throw new AuthFailure(503,'Native follow-up transport and execution are not implemented. Nothing was queued or sent.');
        }
        if($action==='skip') {
            if($r['status']!=='pending') { throw new AuthFailure(409,'Only pending follow-ups can be skipped'); }
            $this->change($r,array('status'=>'skipped'));$this->membership($r);
            return array('success'=>true);
        }
        if($action==='delete'||$action==='reschedule') {
            if(!in_array($r['status'],array('pending','skipped','cancelled','failed'),true)||$r['executed_at']!==null||$r['message_id']!==null||$r['attempt_count']!==0) { throw new AuthFailure(409,'Sent, in-flight or attempted follow-ups must retain their history and cannot be rearmed or removed.'); }
        }
        if($action==='delete') {
            if($this->db->delete('followups',$this->where($r))!==1) { throw new AuthFailure(409,'Follow-up changed. Reload and retry.'); }
            $this->membership($r);return null;
        }
        if($action==='reschedule') {
            try { $when=self::date($newTime); } catch(ValidationFailure $e) { throw new AuthFailure(400,'Invalid datetime format'); }
            if($when<=$this->records->now()) { throw new AuthFailure(400,'Follow-up time must be in the future'); }
            $channel=$r['channel']?:'sms';
            if(!in_array($channel,array('sms','email'),true)) { throw new AuthFailure(409,'Stored follow-up channel needs repair.'); }
            $this->eligible($this->contact($r['contact_id']),$channel);
            $paused=$this->db->select('conversations',array('contact_id'=>$r['contact_id'],'sequence_paused'=>1),1);
            if($paused) { throw new AuthFailure(409,'This conversation is paused. Resume it explicitly before rescheduling.'); }
            $this->change($r,array('status'=>'pending','scheduled_at'=>$when,'last_error'=>null));$this->membership($r,true,$when);
            return array('success'=>true,'scheduled_at'=>Records::iso($when));
        }
        throw new AuthFailure(404,'Not found');
    }
    public static function handle($request,array $c,&$status) {
        $path=trim(substr($request->get_route(),strlen('/sender-suite/v1/')),'/');$q=$request->get_query_params();
        $s=new self($c['db'],null,$c['config']->get('DEFAULT_TIMEZONE'));
        if($path==='followups') {
            if($request->get_method()==='GET') { return $s->listing($q); }
            if($request->get_method()!=='POST') { throw new AuthFailure(404,'Not found'); }
            if(strlen($request->get_body())>65536) { throw new AuthFailure(413,'Follow-up request is too large.'); }
            $out=$s->create(DataRoutes::body($request));$status=201;return $out;
        }
        $parts=explode('/',$path);$id=DataRoutes::integer($parts[1],'followup_id');$action=$parts[2]??'delete';
        if($request->get_method()!==($action==='delete'?'DELETE':'POST')) { throw new AuthFailure(404,'Not found'); }
        if($action==='reschedule') { $newTime=DataRoutes::text($q,'new_time',null,null,128);if($newTime===null) { throw new ValidationFailure('Field required','new_time'); } }
        else { $newTime=null; }
        $out=$s->action($id,$action,$newTime);if($action==='delete') { $status=204; }return $out;
    }
}
