<?php
namespace SenderSuite;

/** Stored AI Agent sessions, memory, skills, files, presets and encrypted settings. Live LLM/audio/tool execution remains gated. */
final class AiAgent {
    const CONSOLE_URL='https://openrouter.ai/settings/keys';
    const PRESET_URL='https://openrouter.ai/settings/presets';
    const KEY_SETTING='ai_agent.openrouter_api_key';
    const PRESET_SETTING='ai_agent.preset.openrouter';
    const AUTO_FALLBACK='ai_agent.auto_fallback';
    const AUTO_APPROVE='ai_agent.auto_approve';
    const VOICE_SETTING='ai_agent.voice';
    const MEMORY_SETTING='ai_agent.global_memory';
    private $db;private $config;private $records;
    public function __construct(Database $db,Config $config=null,callable $clock=null){$this->db=$db;$this->config=$config;$this->records=new Records($db,$clock);}
    private function table($n){return Schema::identifier($this->db->schema()->table($n));}
    public static function definitions(){
        return array(
            '/ai-agent/models'=>array('GET'),
            '/ai-agent/presets'=>array('GET','PUT'),
            '/ai-agent/health'=>array('GET'),
            '/ai-agent/settings'=>array('GET','PUT'),
            '/ai-agent/settings/voice'=>array('PUT'),
            '/ai-agent/settings/test'=>array('POST'),
            '/ai-agent/sessions'=>array('GET','POST'),
            '/ai-agent/sessions/(?P<session_id>[A-Za-z0-9_-]+)'=>array('GET','PATCH','DELETE'),
            '/ai-agent/sessions/(?P<session_id>[A-Za-z0-9_-]+)/chat'=>array('POST'),
            '/ai-agent/sessions/(?P<session_id>[A-Za-z0-9_-]+)/files'=>array('POST'),
            '/ai-agent/actions/(?P<action_id>-?[0-9]+)/approve'=>array('POST'),
            '/ai-agent/actions/(?P<action_id>-?[0-9]+)/cancel'=>array('POST'),
            '/ai-agent/activity'=>array('GET'),
            '/ai-agent/activity/(?P<action_id>-?[0-9]+)/undo'=>array('POST'),
            '/ai-agent/files'=>array('GET','POST'),
            '/ai-agent/files/(?P<file_id>-?[0-9]+)'=>array('GET','DELETE'),
            '/ai-agent/transcribe'=>array('POST'),
            '/ai-agent/memory/global'=>array('GET','PUT','DELETE'),
            '/ai-agent/memory/(?P<session_id>[A-Za-z0-9_-]+)'=>array('GET','PUT','DELETE'),
            '/ai-agent/skills'=>array('GET','POST'),
            '/ai-agent/skills/json'=>array('POST'),
            '/ai-agent/skills/(?P<skill_id>-?[0-9]+)'=>array('GET','DELETE'),
            '/ai-agent/tools'=>array('GET')
        );
    }
    private function getSetting($key,$default=''){
        $r=$this->db->one('system_settings',array('key'=>$key));
        return ($r&&$r['value']!==null)?(string)$r['value']:$default;
    }
    private function putSetting($key,$value,$secret=false){
        $r=$this->db->one('system_settings',array('key'=>$key));
        if(!$r){$r=$this->records->create('system_settings',array('key'=>$key,'category'=>'ai_agent','value'=>null));}
        $stored=$value===null||$value===''?null:(($secret&&$this->config!==null)?$this->config->sealRuntimeSecret($this->db->schema()->table('system_settings').'/'.$r['id'].'/'.$key,$value):(string)$value);
        $this->db->update('system_settings',array('value'=>$stored,'updated_at'=>$this->records->now()),array('id'=>$r['id']));
    }
    private function openSecret($key){
        $r=$this->db->one('system_settings',array('key'=>$key));
        if(!$r||$r['value']===null||$r['value']===''||$this->config===null){return '';}
        try{return (string)$this->config->openRuntimeSecret($this->db->schema()->table('system_settings').'/'.$r['id'].'/'.$key,$r['value']);}catch(\Throwable $e){return '';}
    }
    private static function maskKey($v){
        if(!is_string($v)||$v===''){return '';}
        return strlen($v)<=11?str_repeat('*',strlen($v)):substr($v,0,7).'***'.substr($v,-4);
    }
    private function keysSummary(){
        $k=$this->openSecret(self::KEY_SETTING);
        return array('openrouter_configured'=>$k!=='','openrouter_hint'=>self::maskKey($k),'console_url'=>self::CONSOLE_URL,'preset_console_url'=>self::PRESET_URL,'preset'=>$this->getSetting(self::PRESET_SETTING,''));
    }
    private function flag($key,$default){
        $v=strtolower(Compat::strip($this->getSetting($key,'')));
        if($v===''){return $default;}
        return in_array($v,array('1','true','yes','on'),true);
    }
    private function voiceSettings(){
        $raw=$this->getSetting(self::VOICE_SETTING,'');
        $d=array();if($raw!==''){try{$dec=json_decode($raw,true,8,JSON_THROW_ON_ERROR);if(is_array($dec)){$d=$dec;}}catch(\Throwable $e){}}
        return array('auto_send'=>(bool)($d['auto_send']??false),'language'=>(string)($d['language']??''),'read_aloud'=>(bool)($d['read_aloud']??false));
    }
    public function models(){
        return array('provider'=>'openrouter','models'=>array(),'preset'=>$this->getSetting(self::PRESET_SETTING,''),'total'=>0,'error'=>array('code'=>'disabled','message'=>'Live OpenRouter catalogue requests are disabled in this WordPress build.'));
    }
    public function presets(){
        return array('available'=>false,'items'=>array(),'saved'=>$this->getSetting(self::PRESET_SETTING,''),'preset_console_url'=>self::PRESET_URL);
    }
    public function savePreset($body,array $user){
        if(($user['role']??'')!=='admin'){throw new AuthFailure(403,'Application administrator role required.');}
        if(!is_object($body)){throw new ValidationFailure('Supply a JSON object');}
        foreach($body as $k=>$v){if(!in_array($k,array('preset','check'),true)){throw new ValidationFailure('Unknown preset field',$k);}}
        $this->db->requireTransaction();
        if(property_exists($body,'preset')&&$body->preset!==null){
            $raw=Compat::strip(DataRoutes::text(array('preset'=>$body->preset),'preset','',null,160));
            $slug=ltrim($raw,'@');
            if($slug!==''&&!preg_match('/^[A-Za-z0-9._\/-]+$/D',$slug)){throw new ValidationFailure('Invalid preset identifier','preset');}
            $this->putSetting(self::PRESET_SETTING,$slug,false);
        }
        return array('success'=>true,'preset'=>$this->getSetting(self::PRESET_SETTING,''),'warnings'=>!empty($body->check)?array('Live OpenRouter preset verification is disabled in this WordPress build; saved preset locally.'):array());
    }
    public function health(){
        $areas=array('analytics','calendar','campaigns','contacts','inbox','invoices','lists','sequences','templates','validator');
        return array('providers'=>array(array('provider'=>'openrouter','ok'=>false,'state'=>'disabled','configured'=>$this->openSecret(self::KEY_SETTING)!=='','model'=>null,'message'=>'External OpenRouter completion runtime is disabled in this WordPress build.')),'active_provider'=>'openrouter','active_model'=>null,'state'=>'down','tools'=>count($this->tools()['tools']),'tool_areas'=>$areas,'checked_at'=>Records::iso($this->records->now()));
    }
    public function settings(){
        return array_merge($this->keysSummary(),array('auto_fallback'=>$this->flag(self::AUTO_FALLBACK,true),'auto_approve'=>$this->flag(self::AUTO_APPROVE,false),'voice'=>$this->voiceSettings()));
    }
    public function saveSettings($body,array $user){
        if(($user['role']??'')!=='admin'){throw new AuthFailure(403,'Application administrator role required.');}
        if(!is_object($body)){throw new ValidationFailure('Supply a JSON object');}
        foreach($body as $k=>$v){if(!in_array($k,array('api_key','clear_key','auto_fallback','auto_approve'),true)){throw new ValidationFailure('Unknown AI Agent setting',$k);}}
        $this->db->requireTransaction();
        $clear=isset($body->clear_key)?DataRoutes::boolean($body->clear_key,'clear_key'):false;
        if($clear){$this->putSetting(self::KEY_SETTING,'',true);}
        elseif(isset($body->api_key)&&$body->api_key!==null){
            $k=Compat::strip(DataRoutes::text(array('api_key'=>$body->api_key),'api_key','',null,512));
            if($k!==''){if($this->config===null){throw new AuthFailure(503,'Encrypted runtime vault is required to store API keys.');}$this->putSetting(self::KEY_SETTING,$k,true);}
        }
        if(isset($body->auto_fallback)&&$body->auto_fallback!==null){$this->putSetting(self::AUTO_FALLBACK,DataRoutes::boolean($body->auto_fallback,'auto_fallback')?'true':'false');}
        if(isset($body->auto_approve)&&$body->auto_approve!==null){$this->putSetting(self::AUTO_APPROVE,DataRoutes::boolean($body->auto_approve,'auto_approve')?'true':'false');}
        return array_merge(array('success'=>true,'provider'=>'openrouter'),$this->keysSummary(),array('auto_fallback'=>$this->flag(self::AUTO_FALLBACK,true),'auto_approve'=>$this->flag(self::AUTO_APPROVE,false)));
    }
    public function saveVoice($body,array $user){
        if(($user['role']??'')!=='admin'){throw new AuthFailure(403,'Application administrator role required.');}
        if(!is_object($body)){throw new ValidationFailure('Supply a JSON object');}
        foreach($body as $k=>$v){if(!in_array($k,array('auto_send','language','read_aloud'),true)){throw new ValidationFailure('Unknown voice field',$k);}}
        $this->db->requireTransaction();
        $cur=$this->voiceSettings();
        if(isset($body->auto_send)&&$body->auto_send!==null){$cur['auto_send']=DataRoutes::boolean($body->auto_send,'auto_send');}
        if(property_exists($body,'language')&&$body->language!==null){$cur['language']=Compat::strip(DataRoutes::text(array('language'=>$body->language),'language','',null,32));}
        if(isset($body->read_aloud)&&$body->read_aloud!==null){$cur['read_aloud']=DataRoutes::boolean($body->read_aloud,'read_aloud');}
        $this->putSetting(self::VOICE_SETTING,json_encode($cur,JSON_THROW_ON_ERROR),false);
        return array('success'=>true,'voice'=>$cur);
    }
    private function sessionOr404($sessionId){
        if(!is_string($sessionId)||!preg_match('/^[A-Za-z0-9_-]{1,64}$/D',$sessionId)){throw new ValidationFailure('Invalid session_id','session_id');}
        $r=$this->db->one('ai_agent_sessions',array('id'=>$sessionId));
        if(!$r){throw new AuthFailure(404,'Session not found');}
        return $r;
    }
    public function sessions(array $user){
        $uid=(int)($user['id']??0);
        $rows=$uid>0?$this->db->rows('SELECT * FROM '.$this->table('ai_agent_sessions').' WHERE user_id=%d ORDER BY updated_at DESC,created_at DESC LIMIT 100',array($uid)):array();
        if(!$rows){$rows=$this->db->rows('SELECT * FROM '.$this->table('ai_agent_sessions').' ORDER BY updated_at DESC,created_at DESC LIMIT 100');}
        $out=array();
        foreach($rows as $raw){
            $s=$this->db->decode('ai_agent_sessions',$raw);
            $cnt=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('ai_agent_messages').' WHERE session_id=%s',array($s['id']))[0]['n'];
            $last=$this->db->rows("SELECT SUBSTR(COALESCE(content,''),1,160) AS preview FROM ".$this->table('ai_agent_messages')." WHERE session_id=%s AND role IN ('user','assistant') ORDER BY id DESC LIMIT 1",array($s['id']));
            $out[]=array('id'=>$s['id'],'name'=>$s['name'],'skills'=>is_array($s['skills'])?$s['skills']:array(),'message_count'=>$cnt,'last_message'=>$last?(string)$last[0]['preview']:'','created_at'=>Records::iso($s['created_at']),'updated_at'=>Records::iso($s['updated_at']));
        }
        return array('sessions'=>$out);
    }
    public function createSession($body,array $user){
        if(!is_object($body)){throw new ValidationFailure('Supply a JSON object');}
        foreach($body as $k=>$v){if(!in_array($k,array('name','system_prompt'),true)){throw new ValidationFailure('Unknown session field',$k);}}
        $this->db->requireTransaction();
        $name=isset($body->name)&&$body->name!==null?Compat::strip(DataRoutes::text(array('name'=>$body->name),'name','',null,200)):'';
        if($name===''){$name='New chat';}
        $prompt=isset($body->system_prompt)&&$body->system_prompt!==null?DataRoutes::text(array('system_prompt'=>$body->system_prompt),'system_prompt',null,null,20000):null;
        $row=$this->records->create('ai_agent_sessions',array('id'=>bin2hex(random_bytes(16)),'user_id'=>isset($user['id'])?(int)$user['id']:null,'name'=>$name,'skills'=>array(),'system_prompt'=>$prompt));
        return array('session'=>array('id'=>$row['id'],'name'=>$row['name'],'created_at'=>Records::iso($row['created_at'])));
    }
    public function getSession($sessionId){
        $s=$this->sessionOr404($sessionId);
        $msgs=array();
        foreach($this->db->rows('SELECT * FROM '.$this->table('ai_agent_messages').' WHERE session_id=%s ORDER BY id ASC LIMIT 500',array($sessionId)) as $mr){
            $m=$this->db->decode('ai_agent_messages',$mr);
            $msgs[]=array('id'=>(int)$m['id'],'role'=>$m['role'],'content'=>$m['content']??'','tool_calls'=>$m['tool_calls'],'tool_call_id'=>$m['tool_call_id'],'created_at'=>Records::iso($m['created_at']));
        }
        $files=array();
        foreach($this->db->rows('SELECT id,session_id,filename,preview,size_bytes,created_at FROM '.$this->table('ai_agent_files').' WHERE session_id=%s ORDER BY created_at DESC,id DESC LIMIT 100',array($sessionId)) as $fr){
            $f=$this->db->decode('ai_agent_files',array_merge($fr,array('content'=>'')));
            $files[]=array('id'=>(int)$f['id'],'filename'=>$f['filename'],'size'=>(int)$f['size_bytes'],'preview'=>$f['preview'],'created_at'=>Records::iso($f['created_at']));
        }
        $cards=array();
        foreach($this->db->rows("SELECT * FROM ".$this->table('ai_agent_actions')." WHERE session_id=%s AND status='pending' ORDER BY id ASC LIMIT 50",array($sessionId)) as $ar){
            $a=$this->db->decode('ai_agent_actions',$ar);
            $cards[]=array('kind'=>'confirmation','action_id'=>(int)$a['id'],'tool'=>$a['tool'],'title'=>$a['tool'],'summary'=>$a['summary']??'Pending confirmation','details'=>$a['args']?:new \stdClass());
        }
        return array('session'=>array('id'=>$s['id'],'name'=>$s['name'],'memory'=>$s['memory'],'skills'=>is_array($s['skills'])?$s['skills']:array(),'system_prompt'=>$s['system_prompt'],'created_at'=>Records::iso($s['created_at']),'updated_at'=>Records::iso($s['updated_at'])),'messages'=>$msgs,'files'=>$files,'pending_actions'=>$cards);
    }
    public function updateSession($sessionId,$body){
        if(!is_object($body)){throw new ValidationFailure('Supply a JSON object');}
        foreach($body as $k=>$v){if(!in_array($k,array('name','skills','system_prompt','memory'),true)){throw new ValidationFailure('Unknown session field',$k);}}
        $this->db->requireTransaction();
        $s=$this->sessionOr404($sessionId);$patch=array('updated_at'=>$this->records->now());
        if(isset($body->name)&&$body->name!==null){$nm=Compat::strip(DataRoutes::text(array('name'=>$body->name),'name','',null,200));if($nm!==''){$patch['name']=$nm;}}
        if(property_exists($body,'skills')&&$body->skills!==null){if(!is_array($body->skills)||count($body->skills)>100){throw new ValidationFailure('skills must be a list','skills');}$sk=array();foreach($body->skills as $it){$sk[]=DataRoutes::text(array('skills'=>$it),'skills','',null,100);}$patch['skills']=$sk;}
        if(property_exists($body,'system_prompt')){$patch['system_prompt']=$body->system_prompt===null?null:DataRoutes::text(array('system_prompt'=>$body->system_prompt),'system_prompt','',null,20000);}
        if(property_exists($body,'memory')){$patch['memory']=$body->memory===null?null:DataRoutes::text(array('memory'=>$body->memory),'memory','',null,20000);}
        $this->db->update('ai_agent_sessions',$patch,array('id'=>$sessionId));
        $fresh=$this->sessionOr404($sessionId);
        return array('success'=>true,'session'=>array('id'=>$fresh['id'],'name'=>$fresh['name']));
    }
    public function deleteSession($sessionId){
        $this->db->requireTransaction();
        $this->sessionOr404($sessionId);
        $this->db->delete('ai_agent_messages',array('session_id'=>$sessionId));
        $this->db->delete('ai_agent_files',array('session_id'=>$sessionId));
        $this->db->delete('ai_agent_actions',array('session_id'=>$sessionId));
        $this->db->delete('ai_agent_sessions',array('id'=>$sessionId));
        return array('success'=>true);
    }
    public function cancelAction($actionId){
        $this->db->requireTransaction();
        $r=$this->db->one('ai_agent_actions',array('id'=>$actionId));
        if(!$r){throw new AuthFailure(404,'Action not found');}
        if($r['status']!=='pending'){throw new AuthFailure(409,'This action is already '.$r['status']);}
        $this->db->update('ai_agent_actions',array('status'=>'cancelled'),array('id'=>$actionId,'status'=>'pending'));
        return array('success'=>true,'action_id'=>(int)$actionId,'status'=>'cancelled');
    }
    public function activity(array $q){
        $limit=DataRoutes::integer($q['limit']??50,'limit',1,200);
        $sid=DataRoutes::text($q,'session_id',null,null,64);
        $where='1=1';$args=array();
        if($sid!==null&&$sid!==''){$where='session_id=%s';$args=array($sid);}
        $actions=array();
        foreach($this->db->rows('SELECT * FROM '.$this->table('ai_agent_actions').' WHERE '.$where.' ORDER BY id DESC LIMIT %d',array_merge($args,array($limit))) as $raw){
            $r=$this->db->decode('ai_agent_actions',$raw);
            $actions[]=array('id'=>(int)$r['id'],'session_id'=>$r['session_id'],'tool'=>$r['tool'],'args'=>$r['args'],'summary'=>$r['summary'],'status'=>$r['status'],'error'=>$r['error']?'Action reported an error.':null,'duration_ms'=>$r['duration_ms']!==null?(int)$r['duration_ms']:null,'undoable'=>(bool)($r['undoable']&&$r['undone_at']===null),'undone_at'=>Records::iso($r['undone_at']),'created_at'=>Records::iso($r['created_at']));
        }
        return array('actions'=>$actions);
    }
    private function filePayload(array $f){
        $summary=is_array($f['preview'])?($f['preview']['summary']??null):(is_object($f['preview'])?($f['preview']->summary??null):null);
        return array('id'=>(int)$f['id'],'filename'=>$f['filename'],'size'=>(int)$f['size_bytes'],'preview'=>$f['preview'],'summary'=>$summary,'created_at'=>Records::iso($f['created_at']));
    }
    public function listFiles(array $q){
        $sid=DataRoutes::text($q,'session_id',null,null,64);
        $where='1=1';$args=array();if($sid!==null&&$sid!==''){$where='session_id=%s';$args=array($sid);}
        $out=array();
        foreach($this->db->rows('SELECT id,session_id,filename,preview,size_bytes,created_at FROM '.$this->table('ai_agent_files').' WHERE '.$where.' ORDER BY created_at DESC,id DESC LIMIT 50',$args) as $r){
            $out[]=$this->filePayload($this->db->decode('ai_agent_files',array_merge($r,array('content'=>''))));
        }
        return array('files'=>$out);
    }
    public function getFile($fileId,array $q){
        $offset=DataRoutes::integer($q['offset']??0,'offset',0,100000);
        $limit=DataRoutes::integer($q['limit']??50,'limit',1,200);
        $raw=$this->db->rows('SELECT id,session_id,filename,CASE WHEN OCTET_LENGTH(content)<=1048576 THEN content ELSE SUBSTR(content,1,1048576) END AS content,preview,size_bytes,created_at FROM '.$this->table('ai_agent_files').' WHERE id=%d',array($fileId));
        if(!$raw){throw new AuthFailure(404,'File not found');}
        $f=$this->db->decode('ai_agent_files',$raw[0]);
        $rows=array();
        if(strtolower(substr((string)$f['filename'],-4))==='.csv'&&is_string($f['content'])&&$f['content']!==''){
            try{
                $all=array();
                foreach(CsvReader::dictionaries($f['content']) as $item){if(isset($item['row'])){$all[]=(array)$item['row'];}}
                $rows=array_slice($all,$offset,$limit);
            }catch(\Throwable $e){}
        }
        return array('file'=>$this->filePayload($f),'rows'=>$rows,'offset'=>$offset,'limit'=>$limit);
    }
    public function deleteFile($fileId){
        $this->db->requireTransaction();
        if(!$this->db->one('ai_agent_files',array('id'=>$fileId))){throw new AuthFailure(404,'File not found');}
        $this->db->delete('ai_agent_files',array('id'=>$fileId));
        return array('success'=>true);
    }
    public function globalMemory(){return array('scope'=>'global','text'=>$this->getSetting(self::MEMORY_SETTING,''));}
    public function saveGlobalMemory($body){
        if(!is_object($body)||!property_exists($body,'text')||!is_string($body->text)){throw new ValidationFailure('text string is required','text');}
        $this->db->requireTransaction();
        $txt=implode('',array_map(array(Compat::class,'chr'),array_slice(Compat::points(DataRoutes::text(array('text'=>$body->text),'text','',null,20000)),0,20000)));
        $this->putSetting(self::MEMORY_SETTING,$txt,false);
        return array('success'=>true,'text'=>$txt);
    }
    public function clearGlobalMemory(){$this->db->requireTransaction();$this->putSetting(self::MEMORY_SETTING,'',false);return array('success'=>true);}
    public function sessionMemory($sessionId){$s=$this->sessionOr404($sessionId);return array('scope'=>'session','session_id'=>$s['id'],'text'=>(string)($s['memory']??''));}
    public function saveSessionMemory($sessionId,$body){
        if(!is_object($body)||!property_exists($body,'text')||!is_string($body->text)){throw new ValidationFailure('text string is required','text');}
        $this->db->requireTransaction();
        $this->sessionOr404($sessionId);
        $txt=implode('',array_map(array(Compat::class,'chr'),array_slice(Compat::points(DataRoutes::text(array('text'=>$body->text),'text','',null,20000)),0,20000)));
        $this->db->update('ai_agent_sessions',array('memory'=>$txt,'updated_at'=>$this->records->now()),array('id'=>$sessionId));
        return array('success'=>true,'text'=>$txt);
    }
    public function clearSessionMemory($sessionId){$this->db->requireTransaction();$this->sessionOr404($sessionId);$this->db->update('ai_agent_sessions',array('memory'=>'','updated_at'=>$this->records->now()),array('id'=>$sessionId));return array('success'=>true);}
    public function listSkills(){
        $out=array();
        foreach($this->db->rows('SELECT * FROM '.$this->table('ai_agent_skills').' ORDER BY name ASC LIMIT 200') as $raw){
            $s=$this->db->decode('ai_agent_skills',$raw);
            $out[]=array('id'=>(int)$s['id'],'name'=>$s['name'],'description'=>$s['description'],'content'=>substr((string)($s['content']??''),0,2000),'is_active'=>(bool)$s['is_active'],'created_at'=>Records::iso($s['created_at']));
        }
        return array('skills'=>$out);
    }
    public function upsertSkill($nameRaw,$descRaw,$contentRaw){
        $this->db->requireTransaction();
        $content=Compat::strip(DataRoutes::text(array('content'=>$contentRaw),'content','',null,50000));
        $rawName=Compat::strip(DataRoutes::text(array('name'=>$nameRaw),'name','',null,100));
        if($rawName===''||$content===''){throw new AuthFailure(400,'name and content are required');}
        $safe=substr(preg_replace('/[^A-Za-z0-9_-]/','_',$rawName),0,100);
        if($safe===''){$safe='skill_'.bin2hex(random_bytes(3));}
        $desc=$descRaw!==null?DataRoutes::text(array('description'=>$descRaw),'description',null,null,1000):null;
        $existing=$this->db->one('ai_agent_skills',array('name'=>$safe));
        if($existing){
            $newDesc=$desc!==null&&$desc!==''?$desc:$existing['description'];
            $this->db->update('ai_agent_skills',array('description'=>$newDesc,'content'=>$content,'is_active'=>true,'updated_at'=>$this->records->now()),array('id'=>$existing['id']));
            return array('skill'=>array('id'=>(int)$existing['id'],'name'=>$safe,'description'=>$newDesc));
        }
        $created=$this->records->create('ai_agent_skills',array('name'=>$safe,'description'=>$desc,'content'=>$content,'is_active'=>true));
        return array('skill'=>array('id'=>(int)$created['id'],'name'=>$created['name'],'description'=>$created['description']));
    }
    public function getSkill($skillId){
        $s=$this->db->one('ai_agent_skills',array('id'=>$skillId));
        if(!$s){throw new AuthFailure(404,'Skill not found');}
        return array('skill'=>array('id'=>(int)$s['id'],'name'=>$s['name'],'description'=>$s['description'],'content'=>$s['content'],'is_active'=>(bool)$s['is_active']));
    }
    public function deleteSkill($skillId){
        $this->db->requireTransaction();
        $this->db->delete('ai_agent_skills',array('id'=>$skillId));
        return array('success'=>true);
    }
    public function tools(){
        return array(
            'tools'=>array(
                array('name'=>'list_contacts','area'=>'contacts','description'=>'Search and inspect stored contacts.','danger'=>'safe'),
                array('name'=>'list_campaigns','area'=>'campaigns','description'=>'Inspect classic and Ads campaigns.','danger'=>'safe'),
                array('name'=>'validate_recipients','area'=>'validator','description'=>'Run local email and Nigerian mobile checks.','danger'=>'safe'),
                array('name'=>'list_conversations','area'=>'inbox','description'=>'Inspect stored inbox conversations.','danger'=>'safe')
            ),
            'areas'=>array('campaigns'=>1,'contacts'=>1,'inbox'=>1,'validator'=>1)
        );
    }
    public static function handle($request,array $c,array $user){
        $path=trim(substr($request->get_route(),strlen('/sender-suite/v1/')),'/');$method=$request->get_method();
        if(strlen($request->get_body())>262144){throw new AuthFailure(413,'AI Agent request exceeds the size limit.');}
        $s=new self($c['db'],$c['config']??null);$q=$request->get_query_params();
        if($path==='ai-agent/models'&&$method==='GET'){return $s->models();}
        if($path==='ai-agent/presets'&&$method==='GET'){return $s->presets();}
        if($path==='ai-agent/presets'&&$method==='PUT'){return $c['db']->transaction(function()use($s,$request,$user){return $s->savePreset(DataRoutes::body($request),$user);});}
        if($path==='ai-agent/health'&&$method==='GET'){return $s->health();}
        if($path==='ai-agent/settings'&&$method==='GET'){return $s->settings();}
        if($path==='ai-agent/settings'&&$method==='PUT'){return $c['db']->transaction(function()use($s,$request,$user){return $s->saveSettings(DataRoutes::body($request),$user);});}
        if($path==='ai-agent/settings/voice'&&$method==='PUT'){return $c['db']->transaction(function()use($s,$request,$user){return $s->saveVoice(DataRoutes::body($request),$user);});}
        if($path==='ai-agent/tools'&&$method==='GET'){return $s->tools();}
        if($path==='ai-agent/activity'&&$method==='GET'){return $s->activity($q);}
        if($path==='ai-agent/sessions'&&$method==='GET'){return $s->sessions($user);}
        if($path==='ai-agent/sessions'&&$method==='POST'){$raw=$request->get_body();$b=$raw!==''?DataRoutes::body($request):(object)array();return $c['db']->transaction(function()use($s,$b,$user){return $s->createSession($b,$user);});}
        if($path==='ai-agent/memory/global'){
            if($method==='GET'){return $s->globalMemory();}
            if(($user['role']??'')!=='admin'){throw new AuthFailure(403,'Application administrator role required.');}
            if($method==='PUT'){return $c['db']->transaction(function()use($s,$request){return $s->saveGlobalMemory(DataRoutes::body($request));});}
            if($method==='DELETE'){return $c['db']->transaction(function()use($s){return $s->clearGlobalMemory();});}
        }
        if(preg_match('~^ai-agent/memory/([A-Za-z0-9_-]+)$~D',$path,$mm)&&$mm[1]!=='global'){
            if($method==='GET'){return $s->sessionMemory($mm[1]);}
            if($method==='PUT'){return $c['db']->transaction(function()use($s,$mm,$request){return $s->saveSessionMemory($mm[1],DataRoutes::body($request));});}
            if($method==='DELETE'){return $c['db']->transaction(function()use($s,$mm){return $s->clearSessionMemory($mm[1]);});}
        }
        if($path==='ai-agent/skills'&&$method==='GET'){return $s->listSkills();}
        if(($path==='ai-agent/skills'||$path==='ai-agent/skills/json')&&$method==='POST'){
            if(($user['role']??'')!=='admin'){throw new AuthFailure(403,'Application administrator role required.');}
            $raw=$request->get_body();$b=$raw!==''?DataRoutes::body($request):(object)$q;
            if(!is_object($b)){throw new ValidationFailure('Supply a JSON object');}
            return $c['db']->transaction(function()use($s,$b){return $s->upsertSkill($b->name??'',$b->description??null,$b->content??'');});
        }
        if(preg_match('~^ai-agent/skills/(-?[0-9]+)$~D',$path,$sm)){
            $sid=DataRoutes::integer($sm[1],'skill_id',1);
            if($method==='GET'){return $s->getSkill($sid);}
            if($method==='DELETE'){if(($user['role']??'')!=='admin'){throw new AuthFailure(403,'Application administrator role required.');}return $c['db']->transaction(function()use($s,$sid){return $s->deleteSkill($sid);});}
        }
        if($path==='ai-agent/files'&&$method==='GET'){return $s->listFiles($q);}
        if(preg_match('~^ai-agent/files/(-?[0-9]+)$~D',$path,$fm)){
            $fid=DataRoutes::integer($fm[1],'file_id',1);
            if($method==='GET'){return $s->getFile($fid,$q);}
            if($method==='DELETE'){if(($user['role']??'')!=='admin'){throw new AuthFailure(403,'Application administrator role required.');}return $c['db']->transaction(function()use($s,$fid){return $s->deleteFile($fid);});}
        }
        if(preg_match('~^ai-agent/actions/(-?[0-9]+)/cancel$~D',$path,$am)&&$method==='POST'){
            return $c['db']->transaction(function()use($s,$am){return $s->cancelAction(DataRoutes::integer($am[1],'action_id',1));});
        }
        if(preg_match('~^ai-agent/sessions/([A-Za-z0-9_-]+)$~D',$path,$ssm)){
            if($method==='GET'){return $s->getSession($ssm[1]);}
            if($method==='PATCH'){return $c['db']->transaction(function()use($s,$ssm,$request){return $s->updateSession($ssm[1],DataRoutes::body($request));});}
            if($method==='DELETE'){return $c['db']->transaction(function()use($s,$ssm){return $s->deleteSession($ssm[1]);});}
        }
        if(preg_match('~^ai-agent/(?:settings/test|transcribe|files|sessions/[A-Za-z0-9_-]+/(?:chat|files)|actions/-?[0-9]+/approve|activity/-?[0-9]+/undo)$~D',$path)){
            return (new McpServer($c['db'],$c['config']??null))->handleAiRoute($path,$method,$request,$user);
        }
        throw new AuthFailure(404,'Not found');
    }
}
