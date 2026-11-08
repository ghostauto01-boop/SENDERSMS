<?php
namespace SenderSuite;

/** Read-only stored dashboard. Does not claim a healthy gateway, running worker or transport connection. */
final class Dashboard {
    private $db;private $records;private $metrics;private $zone;
    public function __construct(Database $db,callable $clock=null,$zone='Africa/Lagos') { $this->db=$db;$this->records=new Records($db,$clock);$this->metrics=new Analytics($db,$clock);$this->zone=$zone; }
    private function table($n) { return Schema::identifier($this->db->schema()->table($n)); }
    public static function definitions() { return array('/dashboard/stats'=>array('GET'),'/dashboard/charts'=>array('GET')); }
    private function n($table,$where='1=1',array $args=array()) { return (int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table($table).' WHERE '.$where,$args)[0]['n']; }
    private function campaigns($channel,$classic,$ads) {
        $out=array();foreach(array('campaign'=>array('campaigns',$classic),'ads'=>array('ads_campaigns',$ads)) as $kind=>$pair) {
            $where='status=%s';$args=array($pair[1]);if($channel!==null) { $where.=" AND COALESCE(channel,'sms')=%s";$args[]=$channel; }$out[$kind]=$this->n($pair[0],$where,$args);
        }return $out;
    }
    public function stats($channel=null,$days=null) {
        $wanted=Analytics::channel($channel,'all');$wanted=$wanted==='all'?null:$wanted;
        $counts=$this->metrics->counts($wanted,$days);$active=$this->campaigns($wanted,'running','active');$completed=$this->campaigns($wanted,'completed','completed');
        $clock=function(){return $this->records->now();};list($start,$end)=(new Followups($this->db,$clock,$this->zone))->bounds();
        $filter=$wanted===null?'1=1':"COALESCE(v.channel,'sms')=%s";$args=$wanted===null?array():array($wanted);
        $previewBytes=(int)$this->db->rows('SELECT COALESCE(SUM(COALESCE(OCTET_LENGTH(x.last_message_preview),0)),0) AS n FROM (SELECT v.last_message_preview FROM '.$this->table('conversations').' v JOIN '.$this->table('contacts').' c ON c.id=v.contact_id WHERE '.$filter.' ORDER BY (v.last_message_at IS NULL) ASC,v.last_message_at DESC,v.id ASC LIMIT 10) x',$args)[0]['n'];
        if($previewBytes>1048576) { throw new AuthFailure(413,'Dashboard previews are too large.'); }
        $groups=$this->db->rows('SELECT lead_status FROM '.$this->table('contacts').' GROUP BY lead_status LIMIT 1001');
        if(count($groups)>1000) { throw new AuthFailure(413,'Dashboard lead distribution is too large.'); }
        // Project only data consumed by the dashboard, never provider responses or raw message bodies.
        $rows=$this->db->rows('SELECT v.id,v.contact_id,v.status,v.last_message_preview,v.last_message_at,v.unread_count,c.business_name,c.first_name,c.last_name FROM '.$this->table('conversations').' v JOIN '.$this->table('contacts').' c ON c.id=v.contact_id WHERE '.$filter.' ORDER BY (v.last_message_at IS NULL) ASC,v.last_message_at DESC,v.id ASC LIMIT 10',$args);
        $recent=array();foreach($rows as $r) {
            $name=$r['business_name'];if($name===null||$name==='') { $name=Compat::strip(($r['first_name']??'').' '.($r['last_name']??'')); }if($name==='') { $name='Unknown'; }
            $recent[]=array('id'=>(int)$r['id'],'contact_id'=>(int)$r['contact_id'],'contact_name'=>$name,'status'=>$r['status'],'last_message_preview'=>$r['last_message_preview'],'last_message_at'=>Records::iso($r['last_message_at']),'unread_count'=>(int)$r['unread_count']);
        }
        $filter=$wanted===null?'1=1':"COALESCE(channel,'sms')=%s";
        $rows=$this->db->rows('SELECT id,name,status,channel,messages_sent,replies,updated_at,created_at FROM '.$this->table('campaigns').' WHERE '.$filter.' ORDER BY updated_at DESC,id ASC LIMIT 5',$args);
        $campaigns=array();foreach($rows as $r) { $campaigns[]=array('id'=>(int)$r['id'],'kind'=>'campaign','name'=>$r['name'],'status'=>$r['status'],'channel'=>$r['channel']?:'sms','messages_sent'=>(int)($r['messages_sent']??0),'replies'=>(int)($r['replies']??0),'updated_at'=>Records::iso($r['updated_at']??$r['created_at'])); }
        $distribution=new \stdClass();foreach($this->db->rows('SELECT lead_status,COUNT(*) AS n FROM '.$this->table('contacts').' GROUP BY lead_status') as $r) { $key=$r['lead_status']===null||$r['lead_status']===''?'new':$r['lead_status'];$distribution->$key=($distribution->$key??0)+(int)$r['n']; }
        $out=array('total_contacts'=>$this->n('contacts'),'contacts_contacted'=>$counts['contacted'],'messages_sent'=>$counts['sent'],'messages_delivered'=>$counts['delivered'],'messages_failed'=>$counts['failed'],'messages_queued'=>$counts['queued'],'replies'=>$counts['replies'],'reply_rate'=>Analytics::rate($counts['replies'],max($counts['sent'],1)),'delivery_rate'=>Analytics::rate($counts['delivered'],max($counts['sent'],1)),'interested_leads'=>$this->n('contacts',"lead_status='interested'"),'active_campaigns'=>array_sum($active),'active_campaigns_by_kind'=>$active,'scope'=>array('channel'=>$wanted??'all','days'=>$days),'definitions'=>json_decode(file_get_contents(dirname(__DIR__).'/schema/message-metrics.json'),false,512,JSON_THROW_ON_ERROR),'followups_due_today'=>$this->n('followups',"status='pending' AND scheduled_at>=%s AND scheduled_at<%s",array($start,$end)),'overdue_followups'=>$this->n('followups',"status='pending' AND scheduled_at<%s",array($start)),'completed_campaigns'=>array_sum($completed),'gateway_status'=>'not_verified','recent_conversations'=>$recent,'recent_campaigns'=>$campaigns,'lead_distribution'=>$distribution);
        if(strlen(json_encode($out,JSON_THROW_ON_ERROR))>1048576) { throw new AuthFailure(413,'Dashboard response is too large.'); }return $out;
    }
    public function charts($channel=null,$days=30) {
        $wanted=Analytics::channel($channel,'all');$since=(new \DateTimeImmutable($this->records->now(),new \DateTimeZone('UTC')))->modify('-'.$days.' days')->format('Y-m-d H:i:s.u');$out=array();
        foreach(array('messages_per_day'=>'outgoing','replies_per_day'=>'incoming') as $key=>$direction) {
            $where='direction=%s AND created_at>=%s';$args=array($direction,$since);if($wanted!=='all') { $where.=" AND COALESCE(channel,'sms')=%s";$args[]=$wanted; }
            $rows=$this->db->rows('SELECT DATE(created_at) AS day,COUNT(*) AS n FROM '.$this->table('messages').' WHERE '.$where.' GROUP BY DATE(created_at) ORDER BY DATE(created_at) LIMIT 1001',$args);if(count($rows)>1000) { throw new AuthFailure(413,'Dashboard chart has too many date groups.'); }$out[$key]=array_map(function($r){return array('day'=>$r['day'],'count'=>(int)$r['n']);},$rows);
        }$out['days']=$days;return $out;
    }
    public static function handle($request,array $c) {
        if($request->get_method()!=='GET') { throw new AuthFailure(404,'Not found'); }$q=$request->get_query_params();$channel=DataRoutes::text($q,'channel',null,null,32);$path=trim(substr($request->get_route(),strlen('/sender-suite/v1/')),'/');$s=new self($c['db'],null,$c['config']->get('DEFAULT_TIMEZONE'));
        if($path==='dashboard/stats') { return $s->stats($channel,isset($q['days'])?DataRoutes::integer($q['days'],'days',1,365):null); }
        if($path==='dashboard/charts') { return $s->charts($channel,DataRoutes::integer($q['days']??30,'days',1,365)); }throw new AuthFailure(404,'Not found');
    }
}
