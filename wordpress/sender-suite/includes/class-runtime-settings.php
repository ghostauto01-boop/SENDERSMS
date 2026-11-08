<?php
namespace SenderSuite;

/** Native workspace settings, not a provider test/sending implementation. Caller owns the transaction. */
final class RuntimeSettings {
    const APPEARANCE='appearance.preferences';const MUTED='notifications.muted_senders';
    const APPEARANCE_DEFAULTS=array('mode'=>'system','palette'=>'emerald','density'=>'comfortable','radius'=>'rounded','font'=>'default');
    const CHOICES=array('mode'=>array('light','dark','system'),'palette'=>array('emerald','sapphire','indigo','obsidian','teal','amber','crimson','plum','nordic','terracotta'),'density'=>array('compact','comfortable','spacious'),'radius'=>array('sharp','rounded','soft'),'font'=>array('default','inter','editorial','mono'));
    const COMPLIANCE=array('ec'=>'enable_consent_requirement','ea'=>'enable_auto_opt_out','es'=>'enable_suppression_list','ek'=>'enable_campaign_suppression');
    const EVENTS=array('notify_new_reply','notify_campaign_completed','notify_campaign_failed','notify_gateway_offline','notify_followup_due','notify_system_error');
    private $db;private $records;private $config;
    public function __construct(Database $db,Config $config,callable $clock=null){$this->db=$db;$this->config=$config;$this->records=new Records($db,$clock);}
    public static function definitions(){return array('/settings/appearance'=>array('GET','PUT'),'/settings/compliance'=>array('GET','PUT'),'/settings/notifications'=>array('GET'),'/settings/notifications/muted-senders'=>array('GET','PUT'),'/settings/notifications/(?P<provider>[A-Za-z0-9_-]+)'=>array('PUT'));}
    private function setting($key){return $this->db->one('system_settings',array('key'=>$key));}
    private function save($key,$value,$category,$description=null){
        $old=$this->setting($key);if(!$old){$this->records->create('system_settings',array('key'=>$key,'value'=>$value,'category'=>$category,'description'=>$description));return;}
         $data=array();if($old['value']!==$value){$data['value']=$value;}if($old['category']!==$category){$data['category']=$category;}if($description!==null&&$old['description']!==$description){$data['description']=$description;}if(!$data){return;}$data['updated_at']=$this->records->now();
        if($this->db->update('system_settings',$data,array('id'=>$old['id'],'value'=>$old['value'],'category'=>$old['category'],'description'=>$old['description']))!==1){throw new AuthFailure(409,'Settings changed concurrently. Reload and try again.');}
    }
    public function appearance(){
        $out=self::APPEARANCE_DEFAULTS;$row=$this->setting(self::APPEARANCE);if(!$row||!$row['value']){return $out;}
        try{$v=json_decode($row['value'],false,16,JSON_THROW_ON_ERROR);if(is_object($v)){foreach(self::CHOICES as $k=>$values){if(isset($v->$k)&&is_string($v->$k)&&in_array($v->$k,$values,true)){$out[$k]=$v->$k;}}}}catch(\Throwable $e){}return $out;
    }
    public static function appearanceInput($body){
        if(!is_object($body)){throw new ValidationFailure('Input should be a valid dictionary or object to extract fields from');}$out=array();foreach(array_merge(array_keys(self::CHOICES),array('font_scale')) as $k){if(property_exists($body,$k)){if($body->$k!==null&&!is_string($body->$k)){throw new ValidationFailure('Input should be a valid string',$k);}$out[$k]=$body->$k;}}return $out;
    }
    public function putAppearance($body){
        $d=self::appearanceInput($body);foreach(array('palette','mode','density','radius','font') as $k){if(isset($d[$k])&&!in_array($d[$k],self::CHOICES[$k],true)){throw new AuthFailure(400,"Unsupported ".$k." '".$d[$k]."'");}}
        if(isset($d['font_scale'])){throw new AuthFailure(400,'Font scale is not a supported appearance setting.');}
        $current=$this->appearance();foreach($d as $k=>$v){if($v!==null&&array_key_exists($k,$current)){$current[$k]=$v;}}
        $this->save(self::APPEARANCE,json_encode($current,JSON_UNESCAPED_SLASHES|JSON_THROW_ON_ERROR),'appearance','Workspace theme, color palette, density, radius, and typography');return array_merge(array('success'=>true),$current,array('appearance'=>$current));
    }
    public function compliance(){
        $out=new \stdClass();foreach(self::COMPLIANCE as $key){$row=$this->setting($key);if($row&&$row['category']==='compliance'){if($row['value']!==null&&!in_array($row['value'],array('true','false'),true)){throw new AuthFailure(503,'Stored compliance preferences are invalid. Re-save the compliance controls.');}$out->$key=$row['value'];}}return $out;
    }
    public function putCompliance(array $q){
        foreach(self::COMPLIANCE as $alias=>$key){$hasAlias=array_key_exists($alias,$q)&&$q[$alias]!==null;$hasKey=array_key_exists($key,$q)&&$q[$key]!==null;if(!$hasAlias&&!$hasKey){continue;}$v=DataRoutes::boolean($q[$hasAlias?$alias:$key],$hasAlias?$alias:$key);if($hasAlias&&$hasKey&&$v!==DataRoutes::boolean($q[$key],$key)){throw new ValidationFailure('Conflicting compliance aliases',$key);}$this->save($key,$v?'true':'false','compliance');}return array('success'=>true);
    }
    private static function normalizeSenders($raw){$out=array();$seen=array();foreach(explode(',',$raw) as $part){$value=Compat::upper(Compat::strip($part));if($value!==''&&!isset($seen[$value])){$out[]=$value;$seen[$value]=true;}}return $out;}
    public function muted(){ $row=$this->setting(self::MUTED);$raw=$row&&$row['value']!==null?$row['value']:'MTN,AIRTEL';return array('senders'=>self::normalizeSenders($raw),'defaults'=>array('MTN','AIRTEL'));}
    public function putMuted($senders){if(!is_string($senders)){throw new ValidationFailure('Input should be a valid string','senders');}if(strlen($senders)>65536){throw new ValidationFailure('Muted sender list is too large','senders');}$values=self::normalizeSenders($senders);$this->save(self::MUTED,implode(',',$values),'notifications','Sender IDs that never trigger Pushover alerts (comma-separated)');return array('success'=>true,'senders'=>$values);}
    public function notifications(){
        $table=Schema::identifier($this->db->schema()->table('notification_providers'));$items=array();foreach($this->db->rows('SELECT * FROM '.$table.' ORDER BY id ASC') as $raw){$p=$this->db->decode('notification_providers',$raw);$out=array('id'=>$p['id'],'provider'=>$p['provider'],'is_enabled'=>$p['is_enabled']);foreach(self::EVENTS as $k){$out[$k]=$p[$k];}$items[]=$out;}return array('providers'=>$items);
    }
    private function scope(array $row){return $this->db->schema()->table('notification_providers').'/notification-providers/'.$row['id'].'/'.$row['provider'].'/credentials';}
    /** Trusted internal consumers only; no REST read of credential plaintext exists. Fail closed on unreadable legacy secrets. */
    public function notificationCredentials($provider){$row=$this->db->one('notification_providers',array('provider'=>$provider));if(!$row){return array('user_key'=>'','app_token'=>'');}return $this->readCredentials($row);}
    private function readCredentials(array $row){
        if($row['config_json']===null||$row['config_json']===''){return array('user_key'=>'','app_token'=>'');}
        try{$v=json_decode($this->config->openRuntimeSecret($this->scope($row),$row['config_json']),true,8,JSON_THROW_ON_ERROR);if(!is_array($v)||!isset($v['user_key'],$v['app_token'])||!is_string($v['user_key'])||!is_string($v['app_token'])){throw new \RuntimeException();}return array('user_key'=>$v['user_key'],'app_token'=>$v['app_token']);}
        catch(\Throwable $e){throw new AuthFailure(503,'Stored notification credentials cannot be read. Re-enter both credentials or clear both explicitly.');}
    }
    public static function notificationInput($body){
        if(!is_object($body)){throw new ValidationFailure('Input should be a valid dictionary or object to extract fields from');}foreach(get_object_vars($body) as $key=>$unused){if(!in_array($key,array('user_key','app_token','is_enabled'),true)){throw new ValidationFailure('Unsupported notification setting',$key);}}$d=array();foreach(array('user_key','app_token') as $k){if(!property_exists($body,$k)||$body->$k===null){continue;}$v=$body->$k;if(!is_string($v)){throw new ValidationFailure('Input should be a valid string',$k);}if(strlen($v)>4096||preg_match('/[\x00-\x1F\x7F]/',$v)){throw new ValidationFailure('Invalid notification credential',$k);}$d[$k]=$v;}
        if(property_exists($body,'is_enabled')&&$body->is_enabled!==null){$d['is_enabled']=DataRoutes::boolean($body->is_enabled,'is_enabled');}return $d;
    }
    public function putNotification($provider,$body){
        if($provider!=='pushover'){throw new AuthFailure(400,'Invalid');}$d=self::notificationInput($body);$row=$this->db->one('notification_providers',array('provider'=>$provider));
        if(!$row){$row=$this->records->create('notification_providers',array('provider'=>$provider));}
        $credentials=isset($d['user_key'],$d['app_token'])?array('user_key'=>$d['user_key'],'app_token'=>$d['app_token']):$this->readCredentials($row);
        foreach(array('user_key','app_token') as $k){if(array_key_exists($k,$d)){$credentials[$k]=$d[$k];}}
        $sealed=$this->config->sealRuntimeSecret($this->scope($row),json_encode($credentials,JSON_THROW_ON_ERROR));$values=array('config_json'=>$sealed,'updated_at'=>$this->records->now());if(isset($d['is_enabled'])){$values['is_enabled']=$d['is_enabled'];}
        if($this->db->update('notification_providers',$values,array('id'=>$row['id'],'config_json'=>$row['config_json']))!==1){throw new AuthFailure(409,'Notification settings changed concurrently. Reload and try again.');}return array('success'=>true);
    }
    public static function handle($request,array $c,array $user){
        $path=trim(substr($request->get_route(),strlen('/sender-suite/v1/')),'/');$method=$request->get_method();$q=$request->get_query_params();$service=new self($c['db'],$c['config']);
        if(!in_array($method,array('GET','PUT'),true)){throw new AuthFailure(404,'Not Found');}
        if($method==='PUT'&&($user['role']??'')!=='admin'){throw new AuthFailure(403,'Administrator role required to change workspace settings.');}
        if($path==='settings/appearance'){return $method==='GET'?$service->appearance():$service->putAppearance(DataRoutes::body($request));}
        if($path==='settings/compliance'){return $method==='GET'?$service->compliance():$service->putCompliance($q);}
        if($path==='settings/notifications/muted-senders'){return $method==='GET'?$service->muted():$service->putMuted(DataRoutes::text($q,'senders',''));}
        if($path==='settings/notifications'&&$method==='GET'){return $service->notifications();}
        if(preg_match('#^settings/notifications/([A-Za-z0-9_-]+)$#D',$path,$m)&&$method==='PUT'){
            if(array_key_exists('user_key',$q)||array_key_exists('app_token',$q)){throw new ValidationFailure('Send notification credentials in a JSON body, never in the URL.');}
            $body=$request->get_body()===''?new \stdClass():DataRoutes::body($request);if(array_key_exists('is_enabled',$q)){if(!is_object($body)){throw new ValidationFailure('Input should be a valid dictionary or object to extract fields from');}if(property_exists($body,'is_enabled')){throw new ValidationFailure('Supply is_enabled only once','is_enabled');}$body->is_enabled=$q['is_enabled'];}return $service->putNotification($m[1],$body);
        }
        throw new AuthFailure(404,'Not Found');
    }
}
