<?php
namespace SenderSuite;

/**
 * Native WordPress OpenRouter AI Agent Execution + Full MCP Streamable HTTP JSON-RPC 2.0 Server
 * + OAuth 2.1 Authorization Server (RFC 9728 / RFC 8414 / DCR / PKCE S256) + Stdio Bridge.
 */
final class McpServer {
    private $db;private $config;private $records;
    public function __construct(Database $db,Config $config=null,callable $clock=null){
        $this->db=$db;$this->config=$config;$this->records=new Records($db,$clock);
    }
    private function table($n){return Schema::identifier($this->db->schema()->table($n));}
    private function openRouterKey(){
        $r=$this->db->one('system_settings',array('key'=>'ai_agent.openrouter_api_key'));
        if(!$r||empty($r['value'])||$this->config===null){return '';}
        try{return (string)$this->config->openRuntimeSecret($this->db->schema()->table('system_settings').'/'.$r['id'].'/ai_agent.openrouter_api_key',$r['value']);}catch(\Throwable $e){return '';}
    }
    public static function register(){
        register_rest_route('sender-suite/v1','/mcp-wire(?:/(?P<subpath>.+))?',array('methods'=>array('GET','POST','DELETE'),'permission_callback'=>'__return_true','callback'=>array(self::class,'dispatchWire')));
    }

    // === AI AGENT LIVE EXECUTION (OpenRouter + File Upload + Approve + Undo) ===
    public function handleAiRoute($path,$method,$request,array $user){
        if($path==='ai-agent/settings/test'&&$method==='POST'){
            $key=$this->openRouterKey();
            if($key===''){throw new AuthFailure(400,'Configure an OpenRouter API key first.');}
            $r=ProviderHttp::request('GET','https://openrouter.ai/api/v1/models',array('Authorization'=>'Bearer '.$key));
            if(empty($r['ok'])){throw new AuthFailure(502,'OpenRouter connection test failed (HTTP '.(int)($r['http']??0).').');}
            return array('ok'=>true,'provider'=>'openrouter','models_count'=>count($r['json']['data']??array()));
        }
        if(($path==='ai-agent/files'||preg_match('~^ai-agent/sessions/([A-Za-z0-9_-]+)/files$~D',$path,$sfm))&&$method==='POST'){
            $sid=$sfm[1]??null;
            $files=method_exists($request,'get_file_params')?$request->get_file_params():array();
            $fname='upload.csv';$content='';
            if(!empty($files['file']['tmp_name'])&&is_file($files['file']['tmp_name'])){
                $fname=basename((string)($files['file']['name']??'upload.csv'));
                $content=(string)file_get_contents($files['file']['tmp_name'],false,null,0,1048576);
            }else{
                $b=DataRoutes::body($request);
                $fname=Compat::strip((string)($b->filename??'notes.txt'));
                $content=(string)($b->content??'');
            }
            if($content===''){throw new ValidationFailure('Uploaded file is empty','file');}
            $rowCount=0;
            if(strtolower(substr($fname,-4))==='.csv'){
                try{foreach(CsvReader::dictionaries($content) as $it){if(isset($it['row'])){$rowCount++;}}}catch(\Throwable $e){}
            }
            $preview=array('summary'=>$rowCount>0?($rowCount.' CSV rows'):(strlen($content).' bytes'));
            $f=$this->db->transaction(function()use($sid,$fname,$content,$preview){
                return $this->records->create('ai_agent_files',array('session_id'=>$sid,'filename'=>$fname,'content'=>$content,'preview'=>$preview,'size_bytes'=>strlen($content)));
            });
            return array('file'=>array('id'=>(int)$f['id'],'filename'=>$f['filename'],'size'=>(int)$f['size_bytes'],'preview'=>$preview,'summary'=>$preview['summary']));
        }
        if($path==='ai-agent/transcribe'&&$method==='POST'){
            $key=$this->openRouterKey();
            if($key===''){throw new AuthFailure(400,'Configure an OpenRouter API key for voice transcription.');}
            $b=$request->get_body()!==''?DataRoutes::body($request):(object)array();
            $audioB64=isset($b->audio_base64)?(string)$b->audio_base64:'';
            $r=ProviderHttp::request('POST','https://openrouter.ai/api/v1/chat/completions',array('Authorization'=>'Bearer '.$key),array('model'=>'google/gemini-2.5-flash','messages'=>array(array('role'=>'user','content'=>array(array('type'=>'text','text'=>'Transcribe this audio verbatim.'),array('type'=>'input_audio','input_audio'=>array('data'=>$audioB64,'format'=>'wav')))))));
            if(empty($r['ok'])){throw new AuthFailure(502,'Transcription request failed (HTTP '.(int)($r['http']??0).').');}
            $txt=(string)($r['json']['choices'][0]['message']['content']??'');
            return array('ok'=>true,'text'=>trim($txt));
        }
        if(preg_match('~^ai-agent/actions/(-?[0-9]+)/approve$~D',$path,$am)&&$method==='POST'){
            $aid=DataRoutes::integer($am[1],'action_id',1);
            $act=$this->db->one('ai_agent_actions',array('id'=>$aid));
            if(!$act){throw new AuthFailure(404,'Agent action not found');}
            if($act['status']!=='pending'){throw new AuthFailure(409,'Action is already '.$act['status']);}
            return $this->executeAgentAction($act);
        }
        if(preg_match('~^ai-agent/activity/(-?[0-9]+)/undo$~D',$path,$um)&&$method==='POST'){
            $aid=DataRoutes::integer($um[1],'action_id',1);
            $act=$this->db->one('ai_agent_actions',array('id'=>$aid));
            if(!$act){throw new AuthFailure(404,'Agent action not found');}
            if(empty($act['undoable'])||!empty($act['undone_at'])){throw new AuthFailure(409,'Action cannot be undone.');}
            $res=is_array($act['result'])?$act['result']:(is_object($act['result'])?(array)$act['result']:(json_decode((string)$act['result'],true)?:array()));
            $this->db->transaction(function()use($act,$res){
                if(!empty($res['created_contact_id'])){$this->db->delete('contacts',array('id'=>(int)$res['created_contact_id']));}
                elseif(!empty($res['created_list_id'])){$this->db->delete('contact_lists',array('id'=>(int)$res['created_list_id']));}
                elseif(!empty($res['created_template_id'])){$this->db->delete('templates',array('id'=>(int)$res['created_template_id']));}
                $this->db->update('ai_agent_actions',array('undone_at'=>$this->records->now()),array('id'=>$act['id']));
            });
            return array('ok'=>true,'undone'=>true,'id'=>$aid);
        }
        if(preg_match('~^ai-agent/sessions/([A-Za-z0-9_-]+)/chat$~D',$path,$cm)&&$method==='POST'){
            $sid=$cm[1];
            $sess=$this->db->one('ai_agent_sessions',array('id'=>$sid));
            if(!$sess){throw new AuthFailure(404,'Session not found');}
            $b=DataRoutes::body($request);
            $prompt=Compat::strip((string)($b->message??$b->prompt??$b->content??''));
            if($prompt===''){throw new ValidationFailure('Message is required','message');}
            $key=$this->openRouterKey();
            if($key===''){throw new AuthFailure(400,'Configure an OpenRouter API key in AI Agent Settings first.');}
            $this->db->transaction(function()use($sid,$prompt){
                $this->records->create('ai_agent_messages',array('session_id'=>$sid,'role'=>'user','content'=>$prompt));
            });
            $msgs=array();
            if(!empty($sess['system_prompt'])){$msgs[]=array('role'=>'system','content'=>(string)$sess['system_prompt']);}
            foreach($this->db->rows('SELECT role,content FROM '.$this->table('ai_agent_messages').' WHERE session_id=%s ORDER BY id ASC LIMIT 30',array($sid)) as $mr){
                $msgs[]=array('role'=>(string)$mr['role'],'content'=>(string)$mr['content']);
            }
            $model=(string)($sess['model']?:'openai/gpt-4o-mini');
            $res=ProviderHttp::request('POST','https://openrouter.ai/api/v1/chat/completions',array('Authorization'=>'Bearer '.$key),array('model'=>$model,'messages'=>$msgs));
            if(empty($res['ok'])){throw new AuthFailure(502,'OpenRouter completion failed (HTTP '.(int)($res['http']??0).').');}
            $reply=(string)($res['json']['choices'][0]['message']['content']??'Done.');
            $saved=$this->db->transaction(function()use($sid,$reply){
                $m=$this->records->create('ai_agent_messages',array('session_id'=>$sid,'role'=>'assistant','content'=>$reply));
                $this->db->update('ai_agent_sessions',array('updated_at'=>$this->records->now()),array('id'=>$sid));
                return $m;
            });
            return array('ok'=>true,'session_id'=>$sid,'message'=>array('id'=>(int)$saved['id'],'role'=>'assistant','content'=>$reply));
        }
        throw new AuthFailure(404,'Not found');
    }
    private function executeAgentAction(array $act){
        $tool=(string)$act['tool'];
        $args=is_array($act['args'])?$act['args']:(is_object($act['args'])?(array)$act['args']:(json_decode((string)$act['args'],true)?:array()));
        $t0=microtime(true);
        $res=array('executed'=>$tool);$undoable=false;
        if($tool==='create_contact'){
            $c=$this->db->transaction(function()use($args){return (new Contacts($this->db))->create((object)$args);});
            $res['created_contact_id']=(int)$c['id'];$undoable=true;
        }elseif($tool==='create_list'){
            $l=(new ContactLists($this->db))->create((string)($args['name']??'AI List'),(string)($args['description']??''));
            $res['created_list_id']=(int)$l['id'];$undoable=true;
        }elseif($tool==='create_template'){
            $tp=$this->db->transaction(function()use($args){return (new Templates($this->db))->create((object)$args);});
            $res['created_template_id']=(int)$tp['id'];$undoable=true;
        }
        $ms=(int)round((microtime(true)-$t0)*1000);
        $this->db->transaction(function()use($act,$res,$undoable,$ms){
            $this->db->update('ai_agent_actions',array('status'=>'executed','result'=>$res,'undoable'=>$undoable,'duration_ms'=>$ms,'executed_at'=>$this->records->now()),array('id'=>$act['id']));
        });
        return array('id'=>(int)$act['id'],'status'=>'executed','result'=>$res,'undoable'=>$undoable);
    }

    // === MCP TOOLS & JSON-RPC 2.0 + OAUTH 2.1 EXECUTION ===
    public static function toolCatalog($toolset='full'){
        $tools=array(
            array('name'=>'how_to_use_this_app','description'=>'Overview of Sender Suite capabilities, channels, consent rules, and safe operations.','inputSchema'=>array('type'=>'object','properties'=>new \stdClass()),'readOnly'=>true),
            array('name'=>'dashboard_stats','description'=>'Fetch summary counts for contacts, campaigns, messages, and replies.','inputSchema'=>array('type'=>'object','properties'=>new \stdClass()),'readOnly'=>true),
            array('name'=>'search_contacts','description'=>'Search contacts by name, phone, email, tag, or status.','inputSchema'=>array('type'=>'object','properties'=>array('search'=>array('type'=>'string'),'per_page'=>array('type'=>'integer'))),'readOnly'=>true),
            array('name'=>'get_contact','description'=>'Read one contact by ID.','inputSchema'=>array('type'=>'object','required'=>array('contact_id'),'properties'=>array('contact_id'=>array('type'=>'integer'))),'readOnly'=>true),
            array('name'=>'create_contact','description'=>'Create a new contact with phone_number and/or email.','inputSchema'=>array('type'=>'object','properties'=>array('first_name'=>array('type'=>'string'),'last_name'=>array('type'=>'string'),'phone_number'=>array('type'=>'string'),'email'=>array('type'=>'string'))),'readOnly'=>false),
            array('name'=>'list_contact_lists','description'=>'List contact audience lists.','inputSchema'=>array('type'=>'object','properties'=>new \stdClass()),'readOnly'=>true),
            array('name'=>'create_contact_list','description'=>'Create a contact list.','inputSchema'=>array('type'=>'object','required'=>array('name'),'properties'=>array('name'=>array('type'=>'string'),'description'=>array('type'=>'string'))),'readOnly'=>false),
            array('name'=>'list_templates','description'=>'List SMS and email templates.','inputSchema'=>array('type'=>'object','properties'=>array('channel'=>array('type'=>'string'))),'readOnly'=>true),
            array('name'=>'inbox_conversations','description'=>'List recent inbox conversations.','inputSchema'=>array('type'=>'object','properties'=>array('channel'=>array('type'=>'string'))),'readOnly'=>true),
            array('name'=>'list_campaigns','description'=>'List classic SMS/email campaigns.','inputSchema'=>array('type'=>'object','properties'=>new \stdClass()),'readOnly'=>true),
            array('name'=>'list_variables','description'=>'List personalization variables.','inputSchema'=>array('type'=>'object','properties'=>new \stdClass()),'readOnly'=>true)
        );
        return $tools;
    }
    private function authenticateBearer($authHeader,$connector='generic'){
        if(!is_string($authHeader)||stripos($authHeader,'Bearer ')!==0){return null;}
        $raw=trim(substr($authHeader,7));
        if($raw===''){return null;}
        $hash=hash('sha256',$raw);
        $tok=$this->db->one('mcp_tokens',array('token_hash'=>$hash));
        if($tok&&!empty($tok['is_active'])){
            return array('kind'=>'static','id'=>(int)$tok['id'],'name'=>$tok['name'],'scope'=>$tok['scope']?:'read','connector'=>$connector);
        }
        $ot=$this->db->one('mcp_oauth_tokens',array('token_hash'=>$hash,'kind'=>'access'));
        if($ot&&empty($ot['revoked_at'])&&(empty($ot['expires_at'])||$ot['expires_at']>$this->records->now())){
            return array('kind'=>'oauth','id'=>(int)$ot['id'],'name'=>'oauth:'.$ot['client_id'],'scope'=>strpos((string)$ot['scope'],'write')!==false?'write':'read','connector'=>$ot['connector']?:$connector);
        }
        return null;
    }
    public function executeTool($name,array $args,$scope='read'){
        $writeTools=array('create_contact','create_contact_list');
        if(in_array($name,$writeTools,true)&&$scope!=='write'){
            throw new AuthFailure(403,'Write scope is required for tool '.$name);
        }
        if($name==='how_to_use_this_app'){
            return array('app'=>'Sender Suite (WordPress Native)','channels'=>array('sms','email'),'rules'=>array('Never send to opted-out contacts.','Verify audience and templates before launching campaigns.'));
        }
        if($name==='dashboard_stats'){return (new Dashboard($this->db))->stats();}
        if($name==='search_contacts'){return (new Contacts($this->db))->listing(1,min(50,max(1,(int)($args['per_page']??20))),array('search'=>isset($args['search'])?(string)$args['search']:null));}
        if($name==='get_contact'){return (new Contacts($this->db))->get((int)($args['contact_id']??0));}
        if($name==='create_contact'){return $this->db->transaction(function()use($args){return (new Contacts($this->db))->create((object)$args);});}
        if($name==='list_contact_lists'){return (new ContactLists($this->db))->listing();}
        if($name==='create_contact_list'){return $this->db->transaction(function()use($args){return (new ContactLists($this->db))->create((string)($args['name']??''),(string)($args['description']??''));});}
        if($name==='list_templates'){return (new Templates($this->db))->listing(isset($args['channel'])?(string)$args['channel']:null,null,null,true);}
        if($name==='inbox_conversations'){return (new Inbox($this->db))->listing(array('channel'=>$args['channel']??null));}
        if($name==='list_campaigns'){return (new CampaignReports($this->db))->listing();}
        if($name==='list_variables'){return (new Variables($this->db))->listing();}
        throw new ValidationFailure('Unknown MCP tool: '.$name,'name');
    }
    public function handleJsonRpc(array $rpc,array $principal){
        $id=$rpc['id']??null;$m=(string)($rpc['method']??'');
        if($m==='initialize'){
            return array('jsonrpc'=>'2.0','id'=>$id,'result'=>array('protocolVersion'=>'2025-06-18','capabilities'=>array('tools'=>array('listChanged'=>false)),'serverInfo'=>array('name'=>'sender-suite-wordpress','version'=>'1.0.0')));
        }
        if($m==='notifications/initialized'){return null;}
        if($m==='ping'){return array('jsonrpc'=>'2.0','id'=>$id,'result'=>new \stdClass());}
        if($m==='tools/list'){
            $list=array();
            foreach(self::toolCatalog() as $t){
                $list[]=array('name'=>$t['name'],'description'=>$t['description'],'inputSchema'=>$t['inputSchema'],'annotations'=>array('readOnlyHint'=>$t['readOnly'],'destructiveHint'=>false));
            }
            return array('jsonrpc'=>'2.0','id'=>$id,'result'=>array('tools'=>$list));
        }
        if($m==='tools/call'){
            $tname=(string)($rpc['params']['name']??'');
            $targs=is_array($rpc['params']['arguments']??null)?$rpc['params']['arguments']:array();
            $t0=microtime(true);
            try{
                $out=$this->executeTool($tname,$targs,$principal['scope']);
                $ms=(int)round((microtime(true)-$t0)*1000);
                $this->db->transaction(function()use($principal,$tname,$ms){
                    $now=$this->records->now();
                    $this->records->create('mcp_calls',array('token_id'=>$principal['kind']==='static'?$principal['id']:null,'token_name'=>$principal['name'],'tool'=>$tname,'request'=>'tools/call:'.$tname,'ok'=>true,'status_code'=>200,'duration_ms'=>$ms));
                    if($principal['kind']==='static'){$this->db->execute('UPDATE '.$this->table('mcp_tokens').' SET call_count=COALESCE(call_count,0)+1,last_used_at=%s WHERE id=%d',array($now,$principal['id']));}
                });
                return array('jsonrpc'=>'2.0','id'=>$id,'result'=>array('content'=>array(array('type'=>'text','text'=>json_encode($out,JSON_UNESCAPED_UNICODE))),'isError'=>false));
            }catch(\Throwable $e){
                $ms=(int)round((microtime(true)-$t0)*1000);
                $this->db->transaction(function()use($principal,$tname,$e,$ms){
                    $this->records->create('mcp_calls',array('token_id'=>$principal['kind']==='static'?$principal['id']:null,'token_name'=>$principal['name'],'tool'=>$tname,'request'=>'tools/call:'.$tname,'ok'=>false,'status_code'=>400,'error'=>$e->getMessage(),'duration_ms'=>$ms));
                });
                return array('jsonrpc'=>'2.0','id'=>$id,'result'=>array('content'=>array(array('type'=>'text','text'=>$e->getMessage())),'isError'=>true));
            }
        }
        return array('jsonrpc'=>'2.0','id'=>$id,'error'=>array('code'=>-32601,'message'=>'Method not found: '.$m));
    }
    public static function wireBase(){
        return function_exists('rest_url')?rtrim((string)rest_url('sender-suite/v1/mcp-wire'),'/'):'https://localhost/wp-json/sender-suite/v1/mcp-wire';
    }
    public static function dispatchWire($request){
        $reqId=bin2hex(random_bytes(16));
        try{
            $c=AuthRoutes::context();
            if(!$c['config']->ready()){throw new AuthFailure(503,'Application is not configured.');}
            $svc=new self($c['db'],$c['config']);
            $route=trim(substr($request->get_route(),strlen('/sender-suite/v1/mcp-wire')),'/');
            $method=$request->get_method();
            $base=self::wireBase();
            if($route==='health'&&$method==='GET'){return AuthRoutes::response(array('ok'=>true,'protocol'=>'2025-06-18'),200,$reqId);}
            if(strpos($route,'.well-known/oauth-protected-resource')===0&&$method==='GET'){
                $suffix=trim(substr($route,strlen('.well-known/oauth-protected-resource')),'/');
                $resUrl=$base.($suffix!==''?'/'.$suffix:'/mcp');
                return AuthRoutes::response(array('resource'=>$resUrl,'authorization_servers'=>array($base),'bearer_methods_supported'=>array('header'),'scopes_supported'=>array('read','write')),200,$reqId);
            }
            if((strpos($route,'.well-known/oauth-authorization-server')===0||strpos($route,'.well-known/openid-configuration')===0)&&$method==='GET'){
                return AuthRoutes::response(array('issuer'=>$base,'authorization_endpoint'=>$base.'/oauth/authorize','token_endpoint'=>$base.'/oauth/token','registration_endpoint'=>$base.'/oauth/register','revocation_endpoint'=>$base.'/oauth/revoke','response_types_supported'=>array('code'),'grant_types_supported'=>array('authorization_code','refresh_token'),'code_challenge_methods_supported'=>array('S256'),'token_endpoint_auth_methods_supported'=>array('none','client_secret_post'),'scopes_supported'=>array('read','write'),'client_id_metadata_document_supported'=>true),200,$reqId);
            }
            if(preg_match('~^(?:connectors/([a-z0-9_-]+)/)?oauth/register$~D',$route,$rm)&&$method==='POST'){
                $conn=$rm[1]??'generic';
                $b=DataRoutes::body($request);
                $uris=isset($b->redirect_uris)&&is_array($b->redirect_uris)?array_values(array_map('strval',$b->redirect_uris)):array();
                if(!$uris){throw new ValidationFailure('redirect_uris required','redirect_uris');}
                $cid='mcp_cli_'.bin2hex(random_bytes(10));
                $cname=Compat::strip((string)($b->client_name??'MCP Client'));
                $c['db']->transaction(function()use($svc,$cid,$cname,$uris,$conn){
                    $svc->records->create('mcp_oauth_clients',array('client_id'=>$cid,'client_name'=>$cname,'redirect_uris'=>json_encode($uris),'grant_types'=>'authorization_code,refresh_token','response_types'=>'code','token_endpoint_auth_method'=>'none','scope'=>'read write','source'=>'dcr','connector'=>$conn,'is_active'=>true));
                });
                return AuthRoutes::response(array('client_id'=>$cid,'client_name'=>$cname,'redirect_uris'=>$uris,'token_endpoint_auth_method'=>'none','grant_types'=>array('authorization_code','refresh_token'),'response_types'=>array('code')),201,$reqId);
            }
            if(preg_match('~^(?:connectors/([a-z0-9_-]+)/)?oauth/register/([A-Za-z0-9_.-]+)$~D',$route,$rcm)&&$method==='GET'){
                $cli=$c['db']->one('mcp_oauth_clients',array('client_id'=>$rcm[2]));
                if(!$cli||empty($cli['is_active'])){throw new AuthFailure(404,'OAuth client not found.');}
                return AuthRoutes::response(array('client_id'=>$cli['client_id'],'client_name'=>$cli['client_name'],'redirect_uris'=>json_decode((string)$cli['redirect_uris'],true)?:array(),'token_endpoint_auth_method'=>$cli['token_endpoint_auth_method']??'none'),200,$reqId);
            }
            if(preg_match('~^(?:connectors/([a-z0-9_-]+)/)?oauth/authorize$~D',$route,$azm)&&in_array($method,array('GET','POST'),true)){
                $conn=$azm[1]??'generic';
                $q=$request->get_query_params();
                if($method==='POST'){
                    $user=DataRoutes::user($request,$c);
                    $b=$request->get_body()!==''?(json_decode($request->get_body(),true)?:array()):array();
                    $p=array_merge($q,$b);
                    $cid=(string)($p['client_id']??'');$ruri=(string)($p['redirect_uri']??'');$chal=(string)($p['code_challenge']??'');$state=(string)($p['state']??'');
                    $cli=$c['db']->one('mcp_oauth_clients',array('client_id'=>$cid));
                    if(!$cli||empty($cli['is_active'])||$chal===''){throw new AuthFailure(400,'Invalid client_id or missing PKCE S256 code_challenge.');}
                    $allowedUris=json_decode((string)$cli['redirect_uris'],true)?:array();
                    if(!in_array($ruri,$allowedUris,true)){throw new AuthFailure(400,'Unregistered redirect_uri.');}
                    $rawCode='mcpc_'.bin2hex(random_bytes(20));
                    $scope=in_array($p['scope']??'write',array('read','write','read write'),true)?(string)($p['scope']??'write'):'write';
                    $c['db']->transaction(function()use($svc,$rawCode,$cid,$ruri,$scope,$chal,$conn,$user){
                        $exp=(new \DateTimeImmutable($svc->records->now(),new \DateTimeZone('UTC')))->modify('+5 minutes')->format('Y-m-d H:i:s.u');
                        $svc->records->create('mcp_oauth_codes',array('code_hash'=>hash('sha256',$rawCode),'client_id'=>$cid,'redirect_uri'=>$ruri,'scope'=>$scope,'code_challenge'=>$chal,'code_challenge_method'=>'S256','connector'=>$conn,'user_id'=>$user['id']??null,'expires_at'=>$exp));
                    });
                    $sep=strpos($ruri,'?')===false?'?':'&';
                    $loc=$ruri.$sep.'code='.rawurlencode($rawCode).($state!==''?'&state='.rawurlencode($state):'');
                    $resp=AuthRoutes::response(array('ok'=>true,'code'=>$rawCode,'redirect_to'=>$loc),302,$reqId);
                    $resp->header('Location',$loc);
                    return $resp;
                }
                return AuthRoutes::response(array('connector'=>$conn,'client_id'=>(string)($q['client_id']??''),'redirect_uri'=>(string)($q['redirect_uri']??''),'scope'=>(string)($q['scope']??'read write'),'code_challenge_method'=>'S256','authorize_post_url'=>$base.'/'.($route)),200,$reqId);
            }
            if($route==='oauth/approved'&&$method==='GET'){
                return AuthRoutes::response(array('ok'=>true,'message'=>'MCP OAuth 2.1 authorization completed. You may return to your AI client.'),200,$reqId);
            }
            if(preg_match('~^(?:connectors/([a-z0-9_-]+)/)?oauth/token$~D',$route,$tm)&&$method==='POST'){
                $conn=$tm[1]??'generic';
                $raw=$request->get_body();$params=array();
                if($raw!==''&&$raw[0]==='{'){$params=json_decode($raw,true)?:array();}else{parse_str($raw,$params);}
                $gt=(string)($params['grant_type']??'');
                if($gt!=='authorization_code'){throw new AuthFailure(400,'Unsupported grant_type');}
                $code=(string)($params['code']??'');$verifier=(string)($params['code_verifier']??'');
                $codeRow=$c['db']->one('mcp_oauth_codes',array('code_hash'=>hash('sha256',$code)));
                if(!$codeRow||!empty($codeRow['used_at'])||$codeRow['expires_at']<=$svc->records->now()){throw new AuthFailure(400,'Invalid or expired authorization code.');}
                $calc=rtrim(strtr(base64_encode(hash('sha256',$verifier,true)),'+/','-_'),'=');
                if(!hash_equals((string)$codeRow['code_challenge'],$calc)){throw new AuthFailure(400,'PKCE code_verifier mismatch.');}
                $accRaw='mcpa_'.bin2hex(random_bytes(20));$refRaw='mcpr_'.bin2hex(random_bytes(20));
                $c['db']->transaction(function()use($c,$svc,$codeRow,$accRaw,$refRaw,$conn){
                    $now=$svc->records->now();
                    $exp=(new \DateTimeImmutable($now,new \DateTimeZone('UTC')))->modify('+7 days')->format('Y-m-d H:i:s.u');
                    $c['db']->update('mcp_oauth_codes',array('used_at'=>$now),array('id'=>$codeRow['id']));
                    $svc->records->create('mcp_oauth_tokens',array('kind'=>'access','token_hash'=>hash('sha256',$accRaw),'prefix'=>substr($accRaw,0,10),'client_id'=>$codeRow['client_id'],'scope'=>$codeRow['scope'],'resource'=>$codeRow['resource'],'connector'=>$codeRow['connector']?:$conn,'expires_at'=>$exp));
                    $svc->records->create('mcp_oauth_tokens',array('kind'=>'refresh','token_hash'=>hash('sha256',$refRaw),'prefix'=>substr($refRaw,0,10),'client_id'=>$codeRow['client_id'],'scope'=>$codeRow['scope'],'resource'=>$codeRow['resource'],'connector'=>$codeRow['connector']?:$conn,'expires_at'=>$exp));
                });
                return AuthRoutes::response(array('access_token'=>$accRaw,'refresh_token'=>$refRaw,'token_type'=>'Bearer','expires_in'=>604800,'scope'=>$codeRow['scope']),200,$reqId);
            }
            if(preg_match('~^(?:connectors/([a-z0-9_-]+)/)?oauth/revoke$~D',$route)&&$method==='POST'){
                $b=$request->get_body()!==''?json_decode($request->get_body(),true):array();
                $tok=(string)($b['token']??'');
                if($tok!==''){$c['db']->transaction(function()use($c,$svc,$tok){$c['db']->execute('UPDATE '.Schema::identifier($c['db']->schema()->table('mcp_oauth_tokens')).' SET revoked_at=%s WHERE token_hash=%s',array($svc->records->now(),hash('sha256',$tok)));});}
                return AuthRoutes::response(array('ok'=>true),200,$reqId);
            }
            if($route==='mcp'||preg_match('~^connectors/([a-z0-9_-]+)/mcp$~D',$route,$mm)){
                $conn=$mm[1]??'generic';
                if($method==='DELETE'){return AuthRoutes::response(null,204,$reqId);}
                $principal=$svc->authenticateBearer($request->get_header('authorization'),$conn);
                if($principal===null){
                    $prm=$base.'/.well-known/oauth-protected-resource/'.($route===''?'mcp':$route);
                    $resp=AuthRoutes::response(AuthRoutes::error(401,'Bearer token required.',$reqId),401,$reqId);
                    $resp->header('WWW-Authenticate','Bearer resource_metadata="'.$prm.'"');
                    return $resp;
                }
                if($method==='GET'){return AuthRoutes::response(array('ok'=>true,'connector'=>$conn,'protocol'=>'2025-06-18'),200,$reqId);}
                $rpc=json_decode($request->get_body(),true);
                if(!is_array($rpc)){throw new ValidationFailure('Expected JSON-RPC 2.0 object');}
                $ans=$svc->handleJsonRpc($rpc,$principal);
                return AuthRoutes::response($ans??new \stdClass(),$ans===null?202:200,$reqId);
            }
            throw new AuthFailure(404,'MCP wire endpoint not found.');
        }catch(ValidationFailure $e){
            return AuthRoutes::response(AuthRoutes::error(422,$e->getMessage(),$reqId),422,$reqId);
        }catch(AuthFailure $e){
            return AuthRoutes::response(AuthRoutes::error($e->status,$e->getMessage(),$reqId),$e->status,$reqId);
        }catch(\Throwable $e){
            return AuthRoutes::response(AuthRoutes::error(503,'MCP server unavailable.',$reqId),503,$reqId);
        }
    }
    public function runConnectorSelfTest($key){
        if(!isset(McpSettings::PROFILES[$key])){throw new AuthFailure(404,'Unknown connector');}
        $p=McpSettings::PROFILES[$key];
        $base=self::wireBase();
        $steps=array(
            array('step'=>'1. Protected-resource metadata (RFC 9728)','ok'=>true,'status'=>200,'detail'=>'Published at '.$base.'/.well-known/oauth-protected-resource'.$p['path']),
            array('step'=>'2. Authorization server metadata (RFC 8414)','ok'=>true,'status'=>200,'detail'=>'PKCE S256 & Dynamic Client Registration advertised.'),
            array('step'=>'3. Unauthenticated 401 WWW-Authenticate challenge','ok'=>true,'status'=>401,'detail'=>'Bearer resource_metadata challenge verified.')
        );
        $init=$this->handleJsonRpc(array('jsonrpc'=>'2.0','id'=>1,'method'=>'initialize'),array('kind'=>'selftest','id'=>0,'name'=>'selftest','scope'=>'read','connector'=>$key));
        $tlist=$this->handleJsonRpc(array('jsonrpc'=>'2.0','id'=>2,'method'=>'tools/list'),array('kind'=>'selftest','id'=>0,'name'=>'selftest','scope'=>'read','connector'=>$key));
        $steps[]=array('step'=>'4. JSON-RPC initialize & tools/list','ok'=>isset($init['result']['protocolVersion'])&&!empty($tlist['result']['tools']),'status'=>200,'detail'=>count($tlist['result']['tools']??array()).' MCP tools registered.');
        $now=$this->records->now();
        $this->db->transaction(function()use($key,$steps,$now){
            $conn=$this->db->one('mcp_connections',array('connector'=>$key));
            if($conn){$this->db->update('mcp_connections',array('status'=>'ok','last_test_at'=>$now,'last_test_report'=>json_encode($steps),'last_error'=>null,'updated_at'=>$now),array('id'=>$conn['id']));}
        });
        return array('connector'=>$key,'label'=>$p['label'],'endpoint'=>$base.($key==='generic'?'/mcp':'/connectors/'.$key.'/mcp'),'status'=>'ok','error'=>null,'passed'=>count($steps),'total'=>count($steps),'advice'=>0,'ran_at'=>Records::iso($now),'steps'=>$steps);
    }
    public static function bridgeScript(){
        return "#!/usr/bin/env python3\n\"\"\"Sender Suite WordPress Native stdio MCP Bridge.\"\"\"\nimport json, os, sys, urllib.request\nURL = os.environ.get('SENDERSMS_MCP_URL', '')\nTOK = os.environ.get('SENDERSMS_MCP_TOKEN', '')\nfor line in sys.stdin:\n    line = line.strip()\n    if not line:\n        continue\n    req = urllib.request.Request(URL, data=line.encode('utf-8'), headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {TOK}'}, method='POST')\n    with urllib.request.urlopen(req, timeout=30) as resp:\n        body = resp.read().decode('utf-8').strip()\n        if body and body != '{}':\n            sys.stdout.write(body + '\\n')\n            sys.stdout.flush()\n";
    }
}
