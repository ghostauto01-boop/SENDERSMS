<?php
namespace SenderSuite;

/** Sequence builder data and immutable versions only. Does not execute or send steps. */
final class Sequences {
    const TYPES=array('send_sms','send_email','wait','condition','stop');
    const CONDITIONS=array('contact_replied','contact_did_not_reply','message_delivered','message_failed','contact_opted_out');
    private $db;
    private $records;
    public function __construct(Database $db,callable $clock=null){$this->db=$db;$this->records=new Records($db,$clock);}
    private function table($name){return Schema::identifier($this->db->schema()->table($name));}
    public static function definitions(){return array('/sequences/?'=>array('GET','POST'),'/sequences/(?P<sequence_id>-?[0-9]+)'=>array('GET','PUT','DELETE'));}
    private static function step($body,$index){
        $prefix='steps.'.$index.'.';if(!is_object($body)){throw new ValidationFailure('Input should be a valid dictionary or object to extract fields from','steps.'.$index);}
        $out=array_fill_keys(array('step_order','step_type','config','wait_duration_hours','template_id','condition_type','condition_value','true_branch_step_order','false_branch_step_order','subject','email_account_id'),null);
        foreach($out as $key=>$default){
            if(!property_exists($body,$key)){if(in_array($key,array('step_order','step_type'),true)){throw new ValidationFailure('Field required',$prefix.$key);}continue;}$v=$body->$key;
            if($v===null&&!in_array($key,array('step_order','step_type'),true)){continue;}
            if(in_array($key,array('step_order','wait_duration_hours','template_id','true_branch_step_order','false_branch_step_order','email_account_id'),true)){
                if($v===null){throw new ValidationFailure('Input should be a valid integer',$prefix.$key);}$min=in_array($key,array('step_order','true_branch_step_order','false_branch_step_order'),true)?0:($key==='email_account_id'?null:1);$max=in_array($key,array('step_order','true_branch_step_order','false_branch_step_order'),true)?49:($key==='wait_duration_hours'?8760:null);$v=DataRoutes::integer($v,$prefix.$key,$min,$max);
            }elseif($key==='step_type'||$key==='condition_type'){
                if(!in_array($v,$key==='step_type'?self::TYPES:self::CONDITIONS,true)){throw new ValidationFailure('Input should be a supported '.$key,$prefix.$key);}
            }else{if(!is_string($v)){throw new ValidationFailure('Input should be a valid string',$prefix.$key);}$cap=$key==='config'?10000:($key==='subject'?500:255);if(Compat::length($v)>$cap){throw new ValidationFailure('String should have at most '.$cap.' characters',$prefix.$key);}}
            $out[$key]=$v;
        }
        if($out['step_type']==='wait'&&$out['wait_duration_hours']===null){throw new ValidationFailure('Value error, Wait steps require wait_duration_hours','steps.'.$index);}
        if($out['step_type']==='condition'&&$out['condition_type']===null){throw new ValidationFailure('Value error, Condition steps require condition_type','steps.'.$index);}
        if($out['step_type']==='send_email'&&Compat::strip($out['subject']??'')===''){throw new ValidationFailure('Value error, Send email steps require a subject','steps.'.$index);}
        return $out;
    }
    public static function input($body,$update=false){
        if(!is_object($body)){throw new ValidationFailure('Input should be a valid dictionary or object to extract fields from');}
        $out=$update?array():array('name'=>null,'description'=>null,'channel'=>'sms','steps'=>array());
        foreach(array('name','description','channel','steps') as $key){
            if(!property_exists($body,$key)){if(!$update&&$key==='name'){throw new ValidationFailure('Field required','name');}continue;}$v=$body->$key;
            if($v===null&&($update||$key==='description')){$out[$key]=null;continue;}
            if($key==='steps'){if(!is_array($v)){throw new ValidationFailure('Input should be a valid list','steps');}if(count($v)<1||count($v)>50){throw new ValidationFailure('List should have between 1 and 50 items','steps');}$v=array_map(function($step,$i){return self::step($step,$i);},$v,array_keys($v));}
            elseif($key==='channel'){if(!in_array($v,array('sms','email'),true)){throw new ValidationFailure("Input should be 'sms' or 'email'",'channel');}}
            else{if(!is_string($v)){throw new ValidationFailure('Input should be a valid string',$key);}$cap=$key==='name'?255:5000;if(Compat::length($v)>$cap||($key==='name'&&$v==='')){throw new ValidationFailure('String length is outside the allowed range',$key);}}
            $out[$key]=$v;
        }return $out;
    }
    public static function messageBody($config){
        if($config===null||$config===''){return null;}
        try{$value=json_decode($config,false,512,JSON_THROW_ON_ERROR);}catch(\Throwable $e){$value=$config;}
        if(is_object($value)){$value=$value->message_body??null;}elseif(!is_string($value)){$value=null;}
        $body=Compat::strip(Attachments::truth($value)?Attachments::pythonText($value):'');return $body===''?null:$body;
    }
    public function validate(array $steps,$channel='sms',$fallback=false){
        $channel=strtolower($channel===null||$channel===''?'sms':$channel);if(!in_array($channel,array('sms','email'),true)){throw new AuthFailure(400,'A sequence channel must be sms or email');}
        $type=$channel==='email'?'send_email':'send_sms';$label=$channel==='email'?'Send email':'Send SMS';
        if(!$steps){throw new AuthFailure(400,'A sequence needs at least one step');}if(count($steps)>50){throw new AuthFailure(400,'A sequence can have at most 50 steps');}
        usort($steps,function($a,$b){return $a['step_order']<=>$b['step_order'];});if(array_column($steps,'step_order')!==range(0,count($steps)-1)){throw new AuthFailure(400,'Sequence step numbers must be consecutive, starting at 0');}
        $ids=array();$sendCount=0;
        foreach($steps as $step){$n=$step['step_order']+1;$kind=$step['step_type'];if(!in_array($kind,self::TYPES,true)){throw new AuthFailure(400,'Unsupported sequence step: '.$kind);}
            if(in_array($kind,array('send_sms','send_email'),true)){
                if($kind!==$type){throw new AuthFailure(400,'Step '.$n.' is '.$kind.', but this is an '.$channel.' sequence — use '.$type);}$sendCount++;$body=self::messageBody($step['config']);$limit=$channel==='email'?100000:5000;
                if($body!==null&&Compat::length($body)>$limit){throw new AuthFailure(400,'Step '.$n.' message is longer than '.$limit.' characters');}
                if($channel==='email'){$subject=Compat::strip($step['subject']??'');if($subject===''){throw new AuthFailure(400,'Step '.$n.' needs a subject line');}if(Compat::length($subject)>500){throw new AuthFailure(400,'Step '.$n.' subject is longer than 500 characters');}}
                if($step['template_id']){$ids[$step['template_id']]=true;}elseif($body===null&&!$fallback){throw new AuthFailure(400,'Step '.$n.' must have a written message or template');}
            }elseif($kind==='wait'){if(!$step['wait_duration_hours']||$step['wait_duration_hours']<1){throw new AuthFailure(400,'Step '.$n.' wait must be at least 1 hour');}}
            elseif($kind==='condition'){
                if(!in_array($step['condition_type'],self::CONDITIONS,true)){throw new AuthFailure(400,'Step '.$n.' has an invalid condition');}
                foreach(array('true','false') as $branch){$target=$step[$branch.'_branch_step_order'];if($target===null){continue;}if($target>=count($steps)){throw new AuthFailure(400,'Step '.$n.' '.$branch.' branch points outside the sequence');}if($target<=$step['step_order']){throw new AuthFailure(400,'Step '.$n.' '.$branch.' branch must point to a later step');}}
            }
        }
        if(!$sendCount){throw new AuthFailure(400,'A sequence needs at least one '.$label.' step');}
        if($ids){$keys=array_keys($ids);sort($keys);$rows=$this->db->rows('SELECT * FROM '.$this->table('templates').' WHERE id IN ('.implode(',',array_fill(0,count($keys),'%d')).')',$keys);$templates=array();foreach($rows as $row){$row=$this->db->decode('templates',$row);$templates[$row['id']]=$row;}
            $missing=array_values(array_diff($keys,array_keys($templates)));if($missing){throw new AuthFailure(400,'Sequence template not found: '.implode(', ',$missing));}
            foreach(array('inactive','channel','subject') as $check){$bad=array();foreach($keys as $id){$t=$templates[$id];if(($check==='inactive'&&(!$t['is_active']||Compat::strip($t['body']??'')===''))||($check==='channel'&&!in_array(($t['channel']===null||$t['channel']===''?'any':$t['channel']),array('any',$channel),true))||($check==='subject'&&$channel==='email'&&Compat::strip($t['subject']??'')==='')){$bad[]=$id;}}
                if($bad){$messages=array('inactive'=>'Sequence template is inactive or empty: ','channel'=>'Sequence template is for the other channel: ','subject'=>'Email sequence template has no subject: ');throw new AuthFailure(400,$messages[$check].implode(', ',$bad));}}
        }return $steps;
    }
    public function find($id){$row=$this->db->one('sequences',array('id'=>$id));if(!$row){throw new AuthFailure(404,'Sequence not found');}return $row;}
    private function steps(array $row){return array_map(function($r){return $this->db->decode('sequence_steps',$r);},$this->db->rows('SELECT * FROM '.$this->table('sequence_steps').' WHERE sequence_id=%d AND version=%d ORDER BY step_order',array($row['id'],$row['current_version'])));}
    public static function stepItem(array $step){return array_intersect_key($step,array_flip(array('id','step_order','step_type','config','wait_duration_hours','template_id','subject','email_account_id','condition_type','condition_value','true_branch_step_order','false_branch_step_order')));}
    private function out(array $row){$out=array_intersect_key($row,array_flip(array('id','name','description','channel','current_version','is_active')));$out['channel']=$out['channel']===null||$out['channel']===''?'sms':$out['channel'];$out['steps']=array_map(array(self::class,'stepItem'),$this->steps($row));foreach(array('created_at','updated_at') as $key){$out[$key]=str_replace('Z','+00:00',Records::iso($row[$key]));}return $out;}
    private function saveSteps($id,$version,array $steps){foreach($steps as $step){$this->records->create('sequence_steps',array_merge($step,array('sequence_id'=>$id,'version'=>$version)));}}
    public function create($body){$d=self::input($body);$steps=$this->validate($d['steps'],$d['channel']);unset($d['steps']);$d['name']=Compat::strip($d['name']);$row=$this->records->create('sequences',$d);$this->saveSteps($row['id'],1,$steps);return array('success'=>true,'id'=>$row['id'],'name'=>$row['name']);}
    public function read($id){return $this->out($this->find($id));}
    public function listing($page,$per,$search=null,$channel=null){$where=array('1=1');$args=array();if(in_array($channel,array('sms','email'),true)){$where[]='channel=%s';$args[]=$channel;}if($search!==null&&$search!==''){$where[]='LOWER(name) LIKE LOWER(%s)';$args[]='%'.$search.'%';}$where=implode(' AND ',$where);$total=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('sequences').' WHERE '.$where,$args)[0]['n'];$args[]=$per;$args[]=($page-1)*$per;$rows=$this->db->rows('SELECT * FROM '.$this->table('sequences').' WHERE '.$where.' ORDER BY updated_at DESC LIMIT %d OFFSET %d',$args);return array('total'=>$total,'items'=>array_map(function($r){return $this->out($this->db->decode('sequences',$r));},$rows));}
    public function update($id,$body){$d=self::input($body,true);$row=$this->find($id);$values=array();if(isset($d['name'])){$values['name']=Compat::strip($d['name']);}if(array_key_exists('description',$d)){$values['description']=$d['description'];}
        $channel=$row['channel']===null||$row['channel']===''?'sms':$row['channel'];if(isset($d['channel'])&&$d['channel']!==$channel){$n=$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('campaigns')." WHERE sequence_id=%d AND status IN ('running','scheduled','paused')",array($id))[0]['n'];if((int)$n){throw new AuthFailure(409,'This sequence is used by a live campaign. Duplicate it to change its channel.');}$channel=$d['channel'];$values['channel']=$channel;if(!isset($d['steps'])){$this->validate($this->steps($row),$channel);}}
        $version=$row['current_version'];if(isset($d['steps'])){$steps=$this->validate($d['steps'],$channel);$old=$this->steps($row);if($old){$snapshot=array_map(function($s){$s=self::stepItem($s);unset($s['id']);return $s;},$old);$this->records->create('sequence_versions',array('sequence_id'=>$id,'version'=>$version,'snapshot'=>json_encode($snapshot,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_THROW_ON_ERROR)));}$values['current_version']=++$version;$this->saveSteps($id,$version,$steps);}
        foreach($values as $key=>$v){if($v===$row[$key]){unset($values[$key]);}}if($values){$values['updated_at']=$this->records->now();$changed=$this->db->update('sequences',$values,array('id'=>$id,'current_version'=>$row['current_version']));if($changed!==1){throw new AuthFailure(409,'Sequence changed concurrently; reload and try again.');}}
        return array('success'=>true,'version'=>$version);
    }
    public function delete($id){$this->find($id);foreach(array('campaigns','followups') as $table){if((int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table($table).' WHERE sequence_id=%d',array($id))[0]['n']){throw new AuthFailure(409,'This sequence is used by a campaign and cannot be deleted. Duplicate or edit it instead.');}}$this->db->delete('sequence_steps',array('sequence_id'=>$id));$this->db->delete('sequence_versions',array('sequence_id'=>$id));$this->db->delete('sequences',array('id'=>$id));}
    public static function handle($request,array $context,&$status){$path=trim(substr($request->get_route(),strlen('/sender-suite/v1/')),'/');$method=$request->get_method();$service=new self($context['db']);$q=$request->get_query_params();
        if($path==='sequences'){if($method==='GET'){return $service->listing(DataRoutes::integer($q['page']??1,'page',1),DataRoutes::integer($q['per_page']??25,'per_page',1,100),DataRoutes::text($q,'search'),DataRoutes::text($q,'channel'));}if($method==='POST'){$status=201;return $service->create(DataRoutes::body($request));}}
        if(preg_match('#^sequences/(-?[0-9]+)$#D',$path,$m)){$id=DataRoutes::integer($m[1],'sequence_id');if($method==='GET'){return $service->read($id);}if($method==='PUT'){return $service->update($id,DataRoutes::body($request));}if($method==='DELETE'){$service->delete($id);$status=204;return null;}}
        throw new AuthFailure(404,'Not Found');
    }
}
