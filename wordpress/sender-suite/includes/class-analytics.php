<?php
namespace SenderSuite;

/** Read-only source analytics. Never sends, schedules, or synthesizes provider activity. */
final class Analytics {
    private $db;
    private $records;
    public function __construct(Database $db,callable $clock=null) { $this->db=$db;$this->records=new Records($db,$clock); }
    private function table($name) { return Schema::identifier($this->db->schema()->table($name)); }
    public static function definitions() { $out=array();foreach(array('overview','timeseries','funnel','by-campaign','calls') as $path){$out['/analytics/'.$path]=array('GET');}return $out; }
    public static function channel($raw,$default='sms') {
        $value=Compat::lower(Compat::strip($raw??''));if($value===''){return $default;}
        if(!in_array($value,array('sms','email','all'),true)){throw new AuthFailure(422,"channel must be 'sms', 'email' or 'all' — got '".$raw."'.");}return $value;
    }
    private function start($days,$midnight=true) { $date=new \DateTimeImmutable($this->records->now(),new \DateTimeZone('UTC'));return ($midnight?$date->setTime(0,0)->modify('-'.($days-1).' days'):$date->modify('-'.$days.' days'))->format('Y-m-d H:i:s.u'); }
    private function filters($channel,$days,$campaign,array &$args,$account=null) {
        $where=array('1=1');if($channel!==null&&$channel!=='all'){$where[]="COALESCE(channel,'sms')=%s";$args[]=$channel;}
        if($days!==null){$where[]='created_at>=%s';$args[]=$this->start($days);}if($campaign){$where[]='campaign_id=%d';$args[]=$campaign;}if($account){$where[]='email_account_id=%d';$args[]=$account;}return implode(' AND ',$where);
    }
    private static function sum($condition,$as) { return 'COALESCE(SUM(CASE WHEN '.$condition.' THEN 1 ELSE 0 END),0) AS '.$as; }
    private static function conditions() { return array('sent'=>"direction='outgoing'",'delivered'=>"direction='outgoing' AND (status='delivered' OR delivered_at IS NOT NULL OR opened_at IS NOT NULL)",'failed'=>"direction='outgoing' AND status='failed'",'cancelled'=>"direction='outgoing' AND status='cancelled'",'queued'=>"direction='outgoing' AND status IN ('queued','sending','retrying')",'replies'=>"direction='incoming'",'opened'=>"direction='outgoing' AND (open_count>0 OR opened_at IS NOT NULL)",'clicked'=>"direction='outgoing' AND (click_count>0 OR clicked_at IS NOT NULL)"); }
    public function counts($channel=null,$days=null,$campaign=null,$account=null) {
        $select=array();foreach(self::conditions() as $key=>$condition){$select[]=self::sum($condition,$key);}foreach(array('opens'=>'open_count','clicks'=>'click_count') as $key=>$col){$select[]="COALESCE(SUM(CASE WHEN direction='outgoing' THEN ".$col.' ELSE 0 END),0) AS '.$key;}
        $select[]="COUNT(DISTINCT CASE WHEN direction='outgoing' THEN contact_id ELSE NULL END) AS contacted";$args=array();$where=$this->filters($channel,$days,$campaign,$args,$account);$row=$this->db->rows('SELECT '.implode(',',$select).' FROM '.$this->table('messages').' WHERE '.$where,$args)[0];return array_map('intval',$row);
    }
    public static function rate($part,$whole) { return $whole?round($part/$whole*100,1,PHP_ROUND_HALF_EVEN):0.0; }
    private static function scope($channel,$days) { return array('channel'=>$channel?:'all','days'=>$days); }
    private function n($table,$where='1=1',array $args=array()) { return (int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table($table).' WHERE '.$where,$args)[0]['n']; }
    private static function reason($value) { $value=$value===null||$value===''?'Unknown':$value;return implode('',array_map(array(Compat::class,'chr'),array_slice(Compat::points($value),0,80))); }
    public function run($path,$channel='sms',$days=30,$campaign=null,$limit=10) {
        if($path==='calls') {
            $rows=$this->db->rows("SELECT DATE(created_at) AS day,COUNT(*) AS total,".self::sum("status='ended'",'connected').','.self::sum("status='failed'",'failed').' FROM '.$this->table('call_logs').' WHERE created_at>=%s GROUP BY DATE(created_at) ORDER BY DATE(created_at)',array($this->start($days,false)));
            $points=array();$total=0;$connected=0;foreach($rows as $row){$total+=(int)$row['total'];$connected+=(int)$row['connected'];$points[]=array('date'=>$row['day'],'calls'=>(int)$row['total'],'connected'=>(int)$row['connected'],'failed'=>(int)$row['failed']);}return array('period_days'=>$days,'total'=>$total,'connected'=>$connected,'connect_rate'=>self::rate($connected,max($total,1)),'points'=>$points);
        }
        $args=array();$where=$this->filters($channel,$days,$campaign,$args);$scope=self::scope($channel==='all'?null:$channel,$days);
        if($path==='overview') {
            $counts=$this->counts($channel,$days,$campaign);$sent=$counts['sent'];$replies=$counts['replies'];
            return array('total_sms'=>$sent+$replies,'sent'=>$sent,'delivered'=>$counts['delivered'],'failed'=>$counts['failed'],'delivery_rate'=>self::rate($counts['delivered'],max($sent,1)),'replies'=>$replies,'reply_rate'=>self::rate($replies,max($sent,1)),
                'opt_outs'=>$this->n('contacts','is_opted_out=1 AND opted_out_at>=%s',array($this->start($days))),'followups'=>$this->n('followups','created_at>=%s',array($this->start($days))),'interested_leads'=>$this->n('contacts',"lead_status='interested'"),'period_days'=>$days,'queued'=>$counts['queued'],'scope'=>$scope,'definitions'=>json_decode(file_get_contents(dirname(__DIR__).'/schema/message-metrics.json'),false,512,JSON_THROW_ON_ERROR));
        }
        if($path==='timeseries'||$path==='by-campaign') {
            $select=array();foreach(self::conditions() as $key=>$condition){if(in_array($key,$path==='timeseries'?array('sent','delivered','failed','replies'):array('sent','delivered','replies'),true)){$select[]=self::sum($condition,$key);}}
            $group=$path==='timeseries'?'DATE(created_at)':'campaign_id';$sql='SELECT '.$group.' AS bucket,'.implode(',',$select).' FROM '.$this->table('messages').' WHERE '.$where;
            $sql.=$path==='timeseries'?' GROUP BY DATE(created_at) ORDER BY DATE(created_at)':' AND campaign_id IS NOT NULL GROUP BY campaign_id ORDER BY COUNT(*) DESC LIMIT %d';if($path==='by-campaign'){$args[]=$limit;}
            $rows=$this->db->rows($sql,$args);$items=array();$names=array();if($path==='by-campaign'&&$rows){$ids=array_map(function($r){return (int)$r['bucket'];},$rows);$n=$this->db->rows('SELECT id,name FROM '.$this->table('campaigns').' WHERE id IN ('.implode(',',array_fill(0,count($ids),'%d')).')',$ids);foreach($n as $row){$names[(int)$row['id']]=$row['name'];}}
            foreach($rows as $row){$sent=(int)$row['sent'];$item=array('sent'=>$sent,'delivered'=>(int)$row['delivered'],'replies'=>(int)$row['replies'],'delivery_rate'=>self::rate((int)$row['delivered'],max($sent,1)));if($path==='timeseries'){$item['date']=$row['bucket'];$item['failed']=(int)$row['failed'];$item['reply_rate']=self::rate((int)$row['replies'],max($sent,1));}else{$id=(int)$row['bucket'];$item['campaign_id']=$id;$item['campaign_name']=$names[$id]??('Campaign #'.$id);}$items[]=$item;}
            return $path==='timeseries'?array('period_days'=>$days,'points'=>$items,'scope'=>$scope):array('period_days'=>$days,'campaigns'=>$items);
        }
        if($path==='funnel') {
            $counts=$this->counts($channel,$days,$campaign);$reasons=$this->db->rows('SELECT last_error AS reason,COUNT(*) AS n FROM '.$this->table('messages').' WHERE '.$where." AND direction='outgoing' AND status='failed' GROUP BY last_error ORDER BY COUNT(*) DESC LIMIT 8",$args);
            $quarantine=$this->db->rows('SELECT undeliverable_reason AS reason,COUNT(*) AS n FROM '.$this->table('contacts').' WHERE is_undeliverable=1 GROUP BY undeliverable_reason ORDER BY COUNT(*) DESC LIMIT 8');
            $map=function($rows){return array_map(function($r){return array('reason'=>self::reason($r['reason']),'count'=>(int)$r['n']);},$rows);};
            return array('period_days'=>$days,'stages'=>array(array('stage'=>'Sent','count'=>$counts['sent']),array('stage'=>'Delivered','count'=>$counts['delivered']),array('stage'=>'Replied','count'=>$counts['replies']),array('stage'=>'Interested','count'=>$this->n('contacts',"lead_status='interested'"))),'failed'=>$counts['failed'],'cancelled'=>$counts['cancelled'],'delivery_rate'=>self::rate($counts['delivered'],max($counts['sent'],1)),'reply_rate'=>self::rate($counts['replies'],max($counts['sent'],1)),'scope'=>$scope,'failure_reasons'=>$map($reasons),'quarantine_reasons'=>$map($quarantine));
        }
        throw new AuthFailure(404,'Not Found');
    }
    public static function handle($request,array $context) {
        if($request->get_method()!=='GET'){throw new AuthFailure(404,'Not Found');}$path=substr($request->get_route(),strlen('/sender-suite/v1/analytics/'));$q=$request->get_query_params();$days=DataRoutes::integer($q['days']??30,'days',1,365);
        $channel=$path==='calls'?'sms':self::channel(DataRoutes::text($q,'channel'));$campaign=$path==='by-campaign'||$path==='calls'?null:(isset($q['campaign_id'])?DataRoutes::integer($q['campaign_id'],'campaign_id'):null);$limit=$path==='by-campaign'?DataRoutes::integer($q['limit']??10,'limit',1,50):10;
        return (new self($context['db']))->run($path,$channel,$days,$campaign,$limit);
    }
}
