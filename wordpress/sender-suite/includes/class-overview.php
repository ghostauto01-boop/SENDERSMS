<?php
namespace SenderSuite;

/** Read-only unified campaign + inbox overview across classic campaigns and stored Ads rows. */
final class Overview {
    private $db;private $records;private $clock;
    public function __construct(Database $db,callable $clock=null){$this->db=$db;$this->clock=$clock;$this->records=new Records($db,$clock);}
    private function table($n){return Schema::identifier($this->db->schema()->table($n));}
    public static function definitions(){return array('/overview/campaigns'=>array('GET'),'/overview/campaigns/(?P<campaign_id>-?[0-9]+)'=>array('GET'),'/overview/metrics'=>array('GET'));}
    public static function kind($raw){
        if($raw===null){return null;}
        if(!is_string($raw)){throw new ValidationFailure('Input should be a valid string','kind');}
        $s=Compat::lower(Compat::strip($raw));if($s===''||$s==='all'){return null;}
        $map=array('campaign'=>'campaign','legacy'=>'campaign','classic'=>'campaign','ads'=>'ads');
        if(!isset($map[$s])){throw new AuthFailure(422,"kind must be 'campaign' (alias 'legacy') or 'ads' — got '".$raw."'. Omit it to search both systems.");}
        return $map[$s];
    }
    public static function channel($raw){
        if($raw===null){return null;}
        if(!is_string($raw)){throw new ValidationFailure('Input should be a valid string','channel');}
        $s=Compat::lower(Compat::strip($raw));if($s===''||$s==='all'){return null;}
        if(!in_array($s,array('sms','email'),true)){throw new AuthFailure(422,"channel must be 'sms', 'email' or 'all' — got '".$raw."'.");}
        return $s;
    }
    private static function links($channel,$id,$param='campaign_id'){
        $email=Compat::lower(Compat::strip($channel?:'sms'))==='email';
        $base=$email?'/email-inbox':'/inbox';
        $flag=$email?'view=replied':'replied=1';
        return array($base.'?'.$param.'='.$id,$base.'?'.$param.'='.$id.'&'.$flag);
    }
    private function repliedIds($channel=null){
        $where="direction='incoming' AND conversation_id IS NOT NULL";$args=array();
        if($channel!==null){$where.=" AND COALESCE(channel,'sms')=%s";$args[]=$channel;}
        $out=array();foreach($this->db->rows('SELECT DISTINCT conversation_id FROM '.$this->table('messages').' WHERE '.$where,$args) as $r){$out[(int)$r['conversation_id']]=true;}
        return $out;
    }
    private function attributedTotals(array $replied,$channel=null){
        $where='(campaign_id IS NOT NULL OR last_campaign_id IS NOT NULL OR ads_campaign_id IS NOT NULL OR last_ads_campaign_id IS NOT NULL)';$args=array();
        if($channel!==null){$where.=" AND COALESCE(channel,'sms')=%s";$args[]=$channel;}
        $totals=array('leads'=>0,'replied'=>0,'unread'=>0,'interested'=>0);
        foreach($this->db->rows('SELECT id,unread_count,status FROM '.$this->table('conversations').' WHERE '.$where,$args) as $v){
            $totals['leads']++;
            if(isset($replied[(int)$v['id']])){$totals['replied']++;}
            if((int)$v['unread_count']>0||$v['status']==='unread'){$totals['unread']++;}
            if($v['status']==='interested'){$totals['interested']++;}
        }
        return $totals;
    }
    private function classicRows(array $replied){
        $count=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('campaigns'))[0]['n'];if($count>10000){throw new AuthFailure(413,'Too many classic campaigns for synchronous overview.');}
        if($count===0){return array();}
        $campaigns=$this->db->rows('SELECT id,name,description,channel,status,total_contacts,scheduled_start_at,started_at,completed_at,updated_at FROM '.$this->table('campaigns').' ORDER BY id ASC');
        $byCamp=array();
        foreach($this->db->rows("SELECT campaign_id,status,COUNT(*) AS n FROM ".$this->table('messages')." WHERE campaign_id IS NOT NULL AND direction='outgoing' GROUP BY campaign_id,status") as $r){$cid=(int)$r['campaign_id'];$st=$r['status']?:'unknown';$byCamp[$cid][$st]=(int)$r['n'];}
        $eng=array();
        foreach($this->db->rows('SELECT id,campaign_id,last_campaign_id,unread_count,status FROM '.$this->table('conversations').' WHERE campaign_id IS NOT NULL OR last_campaign_id IS NOT NULL') as $v){
            $cids=array();if($v['campaign_id']!==null&&(int)$v['campaign_id']>0){$cids[(int)$v['campaign_id']]=true;}if($v['last_campaign_id']!==null&&(int)$v['last_campaign_id']>0){$cids[(int)$v['last_campaign_id']]=true;}
            foreach(array_keys($cids) as $cid){
                if(!isset($eng[$cid])){$eng[$cid]=array('leads'=>0,'replied'=>0,'unread'=>0,'interested'=>0);}
                $eng[$cid]['leads']++;
                if(isset($replied[(int)$v['id']])){$eng[$cid]['replied']++;}
                if((int)$v['unread_count']>0||$v['status']==='unread'){$eng[$cid]['unread']++;}
                if($v['status']==='interested'){$eng[$cid]['interested']++;}
            }
        }
        $out=array();
        foreach($campaigns as $raw){
            $c=$this->db->decode('campaigns',$raw);$cid=(int)$c['id'];$st=$byCamp[$cid]??array();
            $delivered=(int)($st['delivered']??0);$sent=$delivered+(int)($st['sent']??0);$failed=(int)($st['failed']??0)+(int)($st['cancelled']??0);$queued=(int)($st['queued']??0)+(int)($st['sending']??0);
            $e=$eng[$cid]??array('leads'=>0,'replied'=>0,'unread'=>0,'interested'=>0);
            $ch=$c['channel']?:'sms';$status=$c['status']??'draft';list($inboxUrl,$repliesUrl)=self::links($ch,$cid,'campaign_id');
            $out[]=array('id'=>$cid,'kind'=>'campaign','system'=>'legacy','channel'=>$ch,'name'=>$c['name'],'description'=>$c['description'],'status'=>$status,'is_live'=>in_array($status,array('running','scheduled','paused'),true),'audience'=>(int)($c['total_contacts']??0),'sent'=>$sent,'delivered'=>$delivered,'failed'=>$failed,'queued'=>$queued,'leads'=>$e['leads'],'replied'=>$e['replied'],'unread'=>$e['unread'],'interested'=>$e['interested'],'delivery_rate'=>Analytics::rate($delivered,$sent),'reply_rate'=>Analytics::rate($e['replied'],$sent),'scheduled_start_at'=>Records::iso($c['scheduled_start_at']),'started_at'=>Records::iso($c['started_at']),'completed_at'=>Records::iso($c['completed_at']),'updated_at'=>Records::iso($c['updated_at']),'inbox_url'=>$inboxUrl,'replies_url'=>$repliesUrl);
        }
        return $out;
    }
    private function adsRows(array $replied){
        $count=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('ads_campaigns'))[0]['n'];if($count>10000){throw new AuthFailure(413,'Too many Ads campaigns for synchronous overview.');}
        if($count===0){return array();}
        $campaigns=$this->db->rows('SELECT id,name,description,channel,status,objective,start_date,end_date,updated_at FROM '.$this->table('ads_campaigns').' ORDER BY id ASC');
        $stats=array();
        foreach($this->db->rows('SELECT campaign_id,send_status,delivery_status,COUNT(*) AS n FROM '.$this->table('ads_assignments').' GROUP BY campaign_id,send_status,delivery_status') as $r){
            $cid=(int)$r['campaign_id'];$n=(int)$r['n'];$ss=$r['send_status']??'';$ds=$r['delivery_status']??'';
            if(!isset($stats[$cid])){$stats[$cid]=array('assigned'=>0,'sent'=>0,'delivered'=>0,'failed'=>0,'pending'=>0);}
            $stats[$cid]['assigned']+=$n;
            if(in_array($ss,array('sent','delivered'),true)){$stats[$cid]['sent']+=$n;}
            if(in_array($ds,array('delivered','simulated'),true)){$stats[$cid]['delivered']+=$n;}
            if($ss==='failed'){$stats[$cid]['failed']+=$n;}
            if($ss==='pending'){$stats[$cid]['pending']+=$n;}
        }
        $eng=array();
        foreach($this->db->rows('SELECT id,ads_campaign_id,last_ads_campaign_id,unread_count,status FROM '.$this->table('conversations').' WHERE ads_campaign_id IS NOT NULL OR last_ads_campaign_id IS NOT NULL') as $v){
            $cids=array();if($v['ads_campaign_id']!==null&&(int)$v['ads_campaign_id']>0){$cids[(int)$v['ads_campaign_id']]=true;}if($v['last_ads_campaign_id']!==null&&(int)$v['last_ads_campaign_id']>0){$cids[(int)$v['last_ads_campaign_id']]=true;}
            foreach(array_keys($cids) as $cid){
                if(!isset($eng[$cid])){$eng[$cid]=array('leads'=>0,'replied'=>0,'unread'=>0,'interested'=>0);}
                $eng[$cid]['leads']++;
                if(isset($replied[(int)$v['id']])){$eng[$cid]['replied']++;}
                if((int)$v['unread_count']>0||$v['status']==='unread'){$eng[$cid]['unread']++;}
                if($v['status']==='interested'){$eng[$cid]['interested']++;}
            }
        }
        $out=array();
        foreach($campaigns as $raw){
            $c=$this->db->decode('ads_campaigns',$raw);$cid=(int)$c['id'];
            $s=$stats[$cid]??array('assigned'=>0,'sent'=>0,'delivered'=>0,'failed'=>0,'pending'=>0);
            $e=$eng[$cid]??array('leads'=>0,'replied'=>0,'unread'=>0,'interested'=>0);
            $ch=$c['channel']?:'sms';$status=$c['status']??'draft';list($inboxUrl,$repliesUrl)=self::links($ch,$cid,'ads_campaign_id');
            $out[]=array('id'=>$cid,'kind'=>'ads','system'=>'ads','channel'=>$ch,'name'=>$c['name'],'description'=>$c['description'],'status'=>$status,'is_live'=>in_array($status,array('active','scheduled','paused'),true),'objective'=>$c['objective'],'audience'=>$s['assigned'],'sent'=>$s['sent'],'delivered'=>$s['delivered'],'failed'=>$s['failed'],'queued'=>$s['pending'],'leads'=>$e['leads'],'replied'=>$e['replied'],'unread'=>$e['unread'],'interested'=>$e['interested'],'delivery_rate'=>Analytics::rate($s['delivered'],$s['sent']),'reply_rate'=>Analytics::rate($e['replied'],$s['sent']),'scheduled_start_at'=>Records::iso($c['start_date']),'started_at'=>Records::iso($c['start_date']),'completed_at'=>Records::iso($c['end_date']),'updated_at'=>Records::iso($c['updated_at']),'inbox_url'=>$inboxUrl,'replies_url'=>$repliesUrl);
        }
        return $out;
    }
    public function directory(array $q=array()){
        $wantedKind=self::kind($q['kind']??null);$wantedChannel=self::channel($q['channel']??null);
        $status=DataRoutes::text($q,'status',null,null,100);$search=DataRoutes::text($q,'search',null,null,1000);
        $live=isset($q['live'])&&$q['live']!==''?DataRoutes::boolean($q['live'],'live'):null;
        $page=DataRoutes::integer($q['page']??1,'page',1,1000000);
        $per=isset($q['per_page'])&&$q['per_page']!==''?DataRoutes::integer($q['per_page'],'per_page',1,200):null;
        $replied=$this->repliedIds();
        $everything=array_merge($this->classicRows($replied),$this->adsRows($replied));
        usort($everything,function($a,$b){
            $la=$a['is_live']?0:1;$lb=$b['is_live']?0:1;if($la!==$lb){return $la<=>$lb;}
            if($a['sent']!==$b['sent']){return $b['sent']<=>$a['sent'];}
            if($a['name']!==$b['name']){return strcmp((string)$a['name'],(string)$b['name']);}
            if($a['kind']!==$b['kind']){return strcmp($a['kind'],$b['kind']);}
            return $a['id']<=>$b['id'];
        });
        $rows=array();$needle=$search!==null&&Compat::strip($search)!==''?Compat::lower(Compat::strip($search)):null;
        foreach($everything as $r){
            if($wantedKind!==null&&$r['kind']!==$wantedKind){continue;}
            if($wantedChannel!==null&&$r['channel']!==$wantedChannel){continue;}
            if($status!==null&&$status!==''&&$r['status']!==$status){continue;}
            if($live!==null&&$r['is_live']!==$live){continue;}
            if($needle!==null&&strpos(Compat::lower((string)($r['name']??'')),$needle)===false){continue;}
            $rows[]=$r;
        }
        $total=count($rows);$nextPage=null;
        if($per!==null){$start=($page-1)*$per;$rows=array_slice($rows,$start,$per);$nextPage=$start+$per<$total?$page+1:null;}
        $scope=array();foreach($everything as $r){if($wantedChannel===null||$r['channel']===$wantedChannel){$scope[]=$r;}}
        $sent=0;$delivered=0;$audience=0;$failed=0;$queued=0;$liveCount=0;
        foreach($scope as $r){$sent+=$r['sent'];$delivered+=$r['delivered'];$audience+=$r['audience'];$failed+=$r['failed'];$queued+=$r['queued'];if($r['is_live']){$liveCount++;}}
        $people=$this->attributedTotals($replied,$wantedChannel);
        return array('items'=>$rows,'total'=>$total,'page'=>$page,'per_page'=>$per,'next_page'=>$nextPage,'totals'=>array('campaigns'=>count($scope),'live'=>$liveCount,'audience'=>$audience,'sent'=>$sent,'delivered'=>$delivered,'failed'=>$failed,'queued'=>$queued,'leads'=>$people['leads'],'replied'=>$people['replied'],'unread'=>$people['unread'],'interested'=>$people['interested'],'delivery_rate'=>Analytics::rate($delivered,$sent),'reply_rate'=>Analytics::rate($people['replied'],$sent)));
    }
    private static function ref(array $r){return array('kind'=>$r['kind'],'system'=>$r['system'],'id'=>$r['id'],'name'=>$r['name'],'status'=>$r['status'],'channel'=>$r['channel']);}
    public function detail($id,array $q=array()){
        $wanted=self::kind($q['kind']??null);
        $hasClassic=($wanted===null||$wanted==='campaign')&&(bool)$this->db->rows('SELECT id FROM '.$this->table('campaigns').' WHERE id=%d',array($id));
        $hasAds=($wanted===null||$wanted==='ads')&&(bool)$this->db->rows('SELECT id FROM '.$this->table('ads_campaigns').' WHERE id=%d',array($id));
        if(!$hasClassic&&!$hasAds){
            $where=$wanted==='campaign'?'classic campaigns':($wanted==='ads'?'Ads Manager campaigns':'either campaign system');
            throw new AuthFailure(404,'No campaign with id '.$id.' in '.$where.'.');
        }
        $replied=$this->repliedIds();
        $findRow=function($which)use($id,$replied){$list=$which==='campaign'?$this->classicRows($replied):$this->adsRows($replied);foreach($list as $r){if($r['id']===(int)$id){return $r;}}return null;};
        if($hasClassic&&$hasAds){
            $candidates=array(self::ref($findRow('campaign')),self::ref($findRow('ads')));
            $listing=implode('; ',array_map(function($c){return "[{$c['kind']}] '{$c['name']}' ({$c['status']}, {$c['channel']})";},$candidates));
            throw new DetailedFailure(409,array('code'=>'AMBIGUOUS_CAMPAIGN_ID','message'=>"Campaign id {$id} exists in both campaign systems — pass kind=campaign or kind=ads. Candidates: {$listing}",'hint'=>'Repeat the request with kind=campaign or kind=ads.','field'=>'kind','candidates'=>$candidates));
        }
        if(!$hasClassic){
            $row=$findRow('ads');$def=(new AdsManager($this->db))->getCampaign($id);
            $mkeys=array('audience','sent','delivered','failed','queued','leads','replied','unread','interested','delivery_rate','reply_rate','is_live','inbox_url','replies_url');
            $metrics=array();foreach($mkeys as $k){$metrics[$k]=$row[$k]??null;}
            return array_merge($def,array('kind'=>'ads','system'=>'ads_manager','metrics'=>$metrics));
        }
        $row=$findRow('campaign');$def=(new CampaignReports($this->db,$this->clock))->read($id);
        $mkeys=array('audience','sent','delivered','failed','queued','leads','replied','unread','interested','delivery_rate','reply_rate','is_live','inbox_url','replies_url');
        $metrics=array();foreach($mkeys as $k){$metrics[$k]=$row[$k]??null;}
        return array_merge($def,array('kind'=>'campaign','system'=>'legacy','metrics'=>$metrics));
    }
    public function metrics(array $q=array()){
        $days=DataRoutes::integer($q['days']??30,'days',1,365);$wantedChannel=self::channel($q['channel']??null);
        $nowDt=new \DateTimeImmutable($this->records->now(),new \DateTimeZone('UTC'));
        $sinceDt=$nowDt->modify('-'.$days.' days');$since=$sinceDt->format('Y-m-d H:i:s.u');
        $replied=$this->repliedIds($wantedChannel);
        $mWhere='created_at>=%s';$mArgs=array($since);if($wantedChannel!==null){$mWhere.=" AND COALESCE(channel,'sms')=%s";$mArgs[]=$wantedChannel;}
        $mc=$this->db->rows("SELECT COALESCE(SUM(CASE WHEN direction='outgoing' AND status IN ('sent','delivered') THEN 1 ELSE 0 END),0) AS sent,COALESCE(SUM(CASE WHEN direction='outgoing' AND status='delivered' THEN 1 ELSE 0 END),0) AS delivered,COALESCE(SUM(CASE WHEN direction='outgoing' AND status IN ('failed','cancelled') THEN 1 ELSE 0 END),0) AS failed,COALESCE(SUM(CASE WHEN direction='outgoing' AND status IN ('queued','sending') THEN 1 ELSE 0 END),0) AS queued,COALESCE(SUM(CASE WHEN direction='incoming' THEN 1 ELSE 0 END),0) AS inbound FROM ".$this->table('messages').' WHERE '.$mWhere,$mArgs)[0];
        $sent=(int)$mc['sent'];$delivered=(int)$mc['delivered'];$failed=(int)$mc['failed'];$queued=(int)$mc['queued'];$inbound=(int)$mc['inbound'];
        $cWhere='1=1';$cArgs=array();if($wantedChannel!==null){$cWhere="COALESCE(channel,'sms')=%s";$cArgs[]=$wantedChannel;}
        $vc=$this->db->rows("SELECT COUNT(*) AS total,COALESCE(SUM(CASE WHEN unread_count>0 OR status='unread' THEN 1 ELSE 0 END),0) AS unread,COALESCE(SUM(CASE WHEN status='interested' THEN 1 ELSE 0 END),0) AS interested FROM ".$this->table('conversations').' WHERE '.$cWhere,$cArgs)[0];
        $sentiment=array('positive'=>0,'negative'=>0,'neutral'=>0);
        foreach($this->db->rows("SELECT LOWER(COALESCE(ai_sentiment,'neutral')) AS s,COUNT(*) AS n FROM ".$this->table('messages')." WHERE direction='incoming' AND ".$mWhere." GROUP BY LOWER(COALESCE(ai_sentiment,'neutral'))",$mArgs) as $sr){if(array_key_exists($sr['s'],$sentiment)){$sentiment[$sr['s']]+=(int)$sr['n'];}}
        $ctWhere='1=1';$optCol='is_opted_out';
        if($wantedChannel==='sms'){$ctWhere="TRIM(COALESCE(phone_number,''))<>''";}
        elseif($wantedChannel==='email'){$ctWhere="TRIM(COALESCE(email,''))<>''";$optCol='is_email_opted_out';}
        $ctc=$this->db->rows('SELECT COUNT(*) AS total,COALESCE(SUM(CASE WHEN '.$optCol.'=1 THEN 1 ELSE 0 END),0) AS opted FROM '.$this->table('contacts').' WHERE '.$ctWhere)[0];
        $totalContacts=(int)$ctc['total'];$optedOut=(int)$ctc['opted'];
        $series=array();for($i=0;$i<$days;$i++){$day=$sinceDt->modify('+'.$i.' days')->format('Y-m-d');$series[$day]=array('date'=>$day,'sent'=>0,'replies'=>0,'delivered'=>0,'failed'=>0);}
        foreach($this->db->rows('SELECT DATE(created_at) AS d,direction,status,COUNT(*) AS n FROM '.$this->table('messages').' WHERE '.$mWhere.' GROUP BY DATE(created_at),direction,status',$mArgs) as $dr){
            $key=substr((string)$dr['d'],0,10);if(!isset($series[$key])){continue;}$n=(int)$dr['n'];
            if($dr['direction']==='incoming'){$series[$key]['replies']+=$n;}
            else{if(in_array($dr['status'],array('sent','delivered'),true)){$series[$key]['sent']+=$n;}if($dr['status']==='delivered'){$series[$key]['delivered']+=$n;}if(in_array($dr['status'],array('failed','cancelled'),true)){$series[$key]['failed']+=$n;}}
        }
        $campData=$this->directory($wantedChannel!==null?array('channel'=>$wantedChannel):array());
        return array('channel'=>$wantedChannel?:'all','period_days'=>$days,'messaging'=>array('sent'=>$sent,'delivered'=>$delivered,'failed'=>$failed,'queued'=>$queued,'replies'=>$inbound,'delivery_rate'=>Analytics::rate($delivered,$sent),'reply_rate'=>Analytics::rate($inbound,$sent),'failure_rate'=>Analytics::rate($failed,$sent+$failed)),'inbox'=>array('conversations'=>(int)$vc['total'],'replied'=>count($replied),'unread'=>(int)$vc['unread'],'interested'=>(int)$vc['interested'],'sentiment'=>$sentiment),'audience'=>array('contacts'=>$totalContacts,'opted_out'=>$optedOut,'opt_out_rate'=>Analytics::rate($optedOut,$totalContacts)),'campaigns'=>$campData['totals'],'top_campaigns'=>array_slice($campData['items'],0,8),'series'=>array_values($series));
    }
    public static function handle($request,array $c){
        if($request->get_method()!=='GET'){throw new AuthFailure(404,'Not found');}
        $path=trim(substr($request->get_route(),strlen('/sender-suite/v1/')),'/');$s=new self($c['db']);$q=$request->get_query_params();
        if($path==='overview/metrics'){return $s->metrics($q);}
        if($path==='overview/campaigns'){return $s->directory($q);}
        if(preg_match('~^overview/campaigns/(-?[0-9]+)$~D',$path,$m)){return $s->detail(DataRoutes::integer($m[1],'campaign_id'),$q);}
        throw new AuthFailure(404,'Not found');
    }
}
