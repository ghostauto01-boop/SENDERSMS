<?php
namespace SenderSuite;

/** Encrypted native SMS-Gate phone configuration. No environment sender, routing engine or SIM failover implied. */
final class GatewayPhones {
    private $db;private $config;private $records;
    const FENCE='native.gateway_phones.version';
    public function __construct(Database $db,Config $config,callable $clock=null){$this->db=$db;$this->config=$config;$this->records=new Records($db,$clock);}
    public static function definitions(){return array('/settings/gateway/phones'=>array('GET','POST'),'/settings/gateway/phones/(?P<phone_id>env|-?[0-9]+)'=>array('PUT','DELETE'),'/settings/gateway/phones/(?P<phone_id>env|-?[0-9]+)/test'=>array('POST'));}
    private function table(){return Schema::identifier($this->db->schema()->table('gateway_settings'));}
    private function scope($id,$field){return $this->db->schema()->table('gateway_settings').'/'.$id.'/'.$field;}
    private function stamp($old){$now=$this->records->now();return $old!==null&&$now<=$old?(new \DateTimeImmutable($old,new \DateTimeZone('UTC')))->modify('+1 microsecond')->format('Y-m-d H:i:s.u'):$now;}
    /** Caller transaction owns this ordered common default/mutation fence. */
    private function fence(){
        $r=$this->db->one('system_settings',array('key'=>self::FENCE));if(!$r){$this->records->create('system_settings',array('key'=>self::FENCE,'category'=>'gateway','value'=>'1'));return;}
        $stamp=$this->stamp($r['updated_at']);if($this->db->update('system_settings',array('value'=>$stamp,'updated_at'=>$stamp),array('id'=>$r['id'],'value'=>$r['value'],'updated_at'=>$r['updated_at']))!==1){throw new AuthFailure(409,'Gateway configuration changed. Retry.');}
    }
    private function projection(){ $names=array();foreach(array_keys($this->db->schema()->tables()['gateway_settings']['columns']) as $key){$names[]=$key==='last_error'?"CASE WHEN last_error IS NULL OR last_error='' THEN NULL ELSE 'error' END AS last_error":Schema::identifier($key);}return implode(',',$names); }
    private function row($id){if((string)$id==='env'){throw new AuthFailure(400,'A Render environment sender is not managed by this native installation.');}$rows=$this->db->rows('SELECT '.$this->projection().' FROM '.$this->table().' WHERE id=%d',array(DataRoutes::integer($id,'phone_id',1)));$r=$rows?$this->db->decode('gateway_settings',$rows[0]):null;if(!$r){throw new AuthFailure(404,'Sender phone not found');}return $r;}
    public function credentials($id){$r=$this->row($id);if(!$r['is_enabled']){throw new AuthFailure(503,'Sender phone is disabled.');}if(($r['provider']??'smsgate')!=='smsgate'||($r['mode']??'cloud')!=='cloud'){throw new AuthFailure(503,'Only cloud SMS-Gate senders are supported by this transport.');}
        try{$password=$this->config->openRuntimeSecret($this->scope($r['id'],'password'),$r['password_encrypted']);}catch(\Throwable $e){throw new AuthFailure(503,'Sender credentials need explicit replacement.');}
        return array('base_url'=>SmsGate::base($r['base_url']),'username'=>$r['username'],'password'=>$password,'sim_number'=>$r['sim_number'],'device_id'=>$r['device_id']);
    }
    private function out(array $r){
        $has=false;$repair=false;try{$has=$r['password_encrypted']!==null&&$this->config->openRuntimeSecret($this->scope($r['id'],'password'),$r['password_encrypted'])!=='';}catch(\Throwable $e){$repair=true;}
        $webhook=false;try{$webhook=$r['webhook_secret_encrypted']!==null&&$this->config->openRuntimeSecret($this->scope($r['id'],'webhook_secret'),$r['webhook_secret_encrypted'])!=='';}catch(\Throwable $e){$repair=true;}
        return array('id'=>(string)$r['id'],'db_id'=>$r['id'],'name'=>$r['name'],'provider'=>$r['provider']?:'smsgate','mode'=>$r['mode']?:'cloud','is_render_env'=>false,'protected'=>false,'is_enabled'=>$r['is_enabled'],'is_default'=>$r['is_default'],'configured'=>!$repair&&$has&&Compat::strip($r['username']??'')!=='','phone_number'=>$r['phone_number']??'','device_id'=>$r['device_id']??'','base_url'=>$r['base_url']?:SmsGate::BASE,'username'=>$r['username']??'','password_masked'=>$has?'********':'','has_password'=>$has,'sim_number'=>$r['sim_number']?:1,'priority'=>$r['priority']?:10,'daily_limit'=>$r['daily_limit']??0,'notes'=>$r['notes']??'','webhook_url'=>$r['webhook_url']??'','has_webhook_secret'=>$webhook,'webhook_secret_masked'=>$webhook?'********':'','connection_status'=>$repair?'needs_repair':($r['connection_status']?:'unknown'),'last_successful_connection'=>Records::iso($r['last_successful_connection']),'last_error'=>$repair?'Credentials need replacement.':($r['last_error']?'Gateway connection reported an error.':null),'created_at'=>Records::iso($r['created_at']));
    }
    public function phones(){
        $n=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table())[0]['n'];if($n>1000){throw new AuthFailure(413,'Too many gateway phones.');}$out=array();foreach($this->db->rows('SELECT '.$this->projection().' FROM '.$this->table().' ORDER BY priority ASC,id ASC LIMIT 1000') as $r){$out[]=$this->out($this->db->decode('gateway_settings',$r));}return $out;
    }
    public function listing(){return array('phones'=>$this->phones(),'render_sender_active'=>false,'routing_mode'=>'default_phone','sim_failover'=>false);}
    private function input($body,$create){
        if(!is_object($body)){throw new ValidationFailure('Supply a JSON object');}$q=get_object_vars($body);$allowed=array('name','mode','is_enabled','is_default','phone_number','device_id','base_url','username','password','webhook_secret','sim_number','sim_slot','priority','daily_limit','notes');foreach($q as $key=>$v){if(!in_array($key,$allowed,true)){throw new ValidationFailure('Unknown phone field',$key);}}
        $d=array();foreach(array('name'=>100,'phone_number'=>32,'device_id'=>120,'base_url'=>500,'username'=>255,'password'=>128,'webhook_secret'=>128,'notes'=>500,'mode'=>30) as $key=>$max){if(array_key_exists($key,$q)&&$q[$key]!==null){$v=DataRoutes::text($q,$key,null,null,$max);$v=Compat::strip($v);if(preg_match('/[\x00-\x1f\x7f]/',$v)){throw new ValidationFailure('Control characters are not allowed',$key);}$d[$key]=$v;}}
        foreach(array('is_enabled','is_default') as $key){if(isset($q[$key])){$d[$key]=DataRoutes::boolean($q[$key],$key);}}
        foreach(array('priority'=>array(1,2147483647),'daily_limit'=>array(0,2147483647),'sim_number'=>array(1,2),'sim_slot'=>array(1,2)) as $key=>$bounds){if(isset($q[$key])){$d[$key]=DataRoutes::integer($q[$key],$key,$bounds[0],$bounds[1]);}}
        if(isset($d['sim_slot'])&&!isset($d['sim_number'])){$d['sim_number']=$d['sim_slot'];}unset($d['sim_slot']);
        foreach(array('password','webhook_secret') as $key){if(isset($d[$key])&&strlen($d[$key])>256){throw new ValidationFailure('Credential text exceeds the encrypted storage byte limit',$key);}}
        if(isset($d['mode'])){$d['mode']=Compat::lower($d['mode'])?:'cloud';}
        if(isset($d['mode'])&&$d['mode']!=='cloud'){throw new ValidationFailure('This native transport currently supports cloud mode only','mode');}
        if(isset($d['base_url'])){$d['base_url']=SmsGate::base($d['base_url']);}if(isset($d['username'])&&strpos($d['username'],':')!==false){throw new ValidationFailure('Username must not contain a colon','username');}
        if($create&&((($d['username']??'')==='')||(($d['password']??'')===''))){throw new AuthFailure(400,'Both SMS-Gate username and password are required to connect a phone.');}
        if(isset($d['username'])&&$d['username']===''){unset($d['username']);}if(isset($d['name'])&&$d['name']===''){unset($d['name']);}return $d;
    }
    public function create($body){
        $d=$this->input($body,true);$this->fence();if(count($this->phones())>=1000){throw new AuthFailure(413,'Too many gateway phones.');}$password=$d['password'];$wh=$d['webhook_secret']??'';unset($d['password'],$d['webhook_secret']);
        if(!empty($d['is_default'])){$this->db->execute('UPDATE '.$this->table().' SET is_default=0,updated_at=%s WHERE is_default=1',array($this->records->now()));}
        $d=array_replace(array('name'=>'Connected Android Phone','provider'=>'smsgate','mode'=>'cloud','is_enabled'=>true,'base_url'=>SmsGate::BASE,'password_encrypted'=>null),$d);$r=$this->records->create('gateway_settings',$d);$patch=array('password_encrypted'=>$this->config->sealRuntimeSecret($this->scope($r['id'],'password'),$password),'webhook_secret_encrypted'=>$wh!==''?$this->config->sealRuntimeSecret($this->scope($r['id'],'webhook_secret'),$wh):null);
        if($this->db->update('gateway_settings',$patch,array('id'=>$r['id'],'password_encrypted'=>null))!==1){throw new AuthFailure(409,'Phone configuration changed. Retry.');}$phone=$this->out(array_replace($r,$patch));return array('success'=>true,'phone'=>$phone,'phones'=>$this->phones());
    }
    public function update($id,$body){
        $d=$this->input($body,false);$this->fence();$r=$this->row($id);foreach(array('password','webhook_secret') as $key){if(array_key_exists($key,$d)){$v=$d[$key];unset($d[$key]);if($key==='password'&&$v===''){continue;}$d[$key.'_encrypted']=$v===''?null:$this->config->sealRuntimeSecret($this->scope($r['id'],$key),$v);}}
        if(!empty($d['is_default'])){$this->db->execute('UPDATE '.$this->table().' SET is_default=0,updated_at=%s WHERE is_default=1 AND id<>%d',array($this->records->now(),$r['id']));}
        $d['updated_at']=$this->stamp($r['updated_at']);if(array_intersect(array_keys($d),array('username','password_encrypted','base_url','device_id','sim_number'))){$d['connection_status']='unknown';$d['last_successful_connection']=null;$d['last_error']=null;}
        if($this->db->update('gateway_settings',$d,array('id'=>$r['id'],'updated_at'=>$r['updated_at']))!==1){throw new AuthFailure(409,'Phone configuration changed. Retry.');}return array('success'=>true,'phone'=>$this->out(array_replace($r,$d)),'phones'=>$this->phones());
    }
    public function delete($id){$this->fence();$r=$this->row($id);if($this->db->delete('gateway_settings',array('id'=>$r['id'],'updated_at'=>$r['updated_at']))!==1){throw new AuthFailure(409,'Phone configuration changed. Retry.');}return array('success'=>true,'phones'=>$this->phones());}
    /** A reachability/authentication read only. Unlike source, never sends a diagnostic SMS. */
    public function probe($id,$body){
        if($body!==null&&(!is_object($body)||count(get_object_vars($body))>0)){throw new ValidationFailure('Connection testing accepts an empty object only; it does not send test SMS.');}$c=$this->credentials($id);$r=$this->row($id);$result=SmsGate::probe($c);$patch=array('connection_status'=>$result['success']?'online':'error','last_error'=>$result['success']?null:'Gateway probe failed.','updated_at'=>$this->stamp($r['updated_at']));if($result['success']){$patch['last_successful_connection']=$this->records->now();}if($this->db->update('gateway_settings',$patch,array('id'=>$r['id'],'updated_at'=>$r['updated_at']))!==1){throw new AuthFailure(409,'Phone configuration changed during probe. Retry.');}return $result;
    }
    public static function handle($request,array $c,array $user){
        $s=new self($c['db'],$c['config']);$method=$request->get_method();$path=trim(substr($request->get_route(),strlen('/sender-suite/v1/')),'/');$q=$request->get_query_params();unset($q['rest_route']);if($q){throw new ValidationFailure('Phone configuration accepts JSON only, never URL credentials.');}
        if($method==='GET'&&$path==='settings/gateway/phones'){return $s->listing();}if(($user['role']??'')!=='admin'){throw new AuthFailure(403,'Application administrator role required.');}if(strlen($request->get_body())>8192){throw new AuthFailure(413,'Phone configuration is too large.');}
        if($method==='POST'&&$path==='settings/gateway/phones'){return $s->create(DataRoutes::body($request));}if(preg_match('~^settings/gateway/phones/(env|-?[0-9]+)(/test)?$~D',$path,$m)){if(($m[2]??'')==='/test'&&$method==='POST'){return $s->probe($m[1],$request->get_body()===''?null:DataRoutes::body($request));}if(!isset($m[2])){if($method==='PUT'){return $s->update($m[1],DataRoutes::body($request));}if($method==='DELETE'){return $s->delete($m[1]);}}}throw new AuthFailure(404,'Not found');
    }
}
