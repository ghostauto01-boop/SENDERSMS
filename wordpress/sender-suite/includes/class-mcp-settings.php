<?php
namespace SenderSuite;

/** Stored MCP token, OAuth client, connector preference, activity, and execution-health dashboard APIs. External MCP/OAuth2 wire endpoints remain gated. */
final class McpSettings {
    const PROFILES=array(
        'chatgpt'=>array('key'=>'chatgpt','label'=>'ChatGPT','vendor'=>'OpenAI','path'=>'/connectors/chatgpt/mcp','oauth_path'=>'/connectors/chatgpt/oauth','auth_modes'=>array('oauth'),'toolset'=>'core','redirect_uris'=>array('https://chatgpt.com/connector_platform_oauth_redirect','https://chatgpt.com/backend-api/aip/connectors/links/oauth/callback'),'cimd'=>true,'docs_url'=>'https://developers.openai.com/api/docs/mcp','blurb'=>'ChatGPT connects with OAuth 2.1. External MCP wire endpoints are gated in this WordPress build.'),
        'claude'=>array('key'=>'claude','label'=>'Claude','vendor'=>'Anthropic','path'=>'/connectors/claude/mcp','oauth_path'=>'/connectors/claude/oauth','auth_modes'=>array('oauth','bearer'),'toolset'=>'full','redirect_uris'=>array('https://claude.ai/api/mcp/auth_callback','https://claude.ai/api/auth/callback'),'cimd'=>true,'docs_url'=>'https://claude.com/docs/connectors/building/authentication','blurb'=>'Claude connects with OAuth 2.1 + PKCE or static bearer tokens. External MCP wire endpoints are gated in this WordPress build.'),
        'arena'=>array('key'=>'arena','label'=>'Arena AI agent','vendor'=>'Arena.ai','path'=>'/connectors/arena/mcp','oauth_path'=>'/connectors/arena/oauth','auth_modes'=>array('bearer','oauth'),'toolset'=>'full','redirect_uris'=>array(),'cimd'=>false,'docs_url'=>'https://help.arena.ai/articles/5432423882-how-to-use-agent-mode','blurb'=>'Arena Agent Mode connects via bearer token or OAuth. External MCP wire endpoints are gated in this WordPress build.'),
        'generic'=>array('key'=>'generic','label'=>'Generic MCP client','vendor'=>'MCP','path'=>'/mcp','oauth_path'=>'/oauth','auth_modes'=>array('bearer','oauth'),'toolset'=>'full','redirect_uris'=>array(),'cimd'=>false,'docs_url'=>null,'blurb'=>'Standard MCP client profile. External MCP wire endpoints are gated in this WordPress build.')
    );
    private $db;private $config;private $records;
    public function __construct(Database $db,Config $config=null,callable $clock=null){$this->db=$db;$this->config=$config;$this->records=new Records($db,$clock);}
    private function table($n){return Schema::identifier($this->db->schema()->table($n));}
    public static function definitions(){
        return array(
            '/mcp/tokens'=>array('GET','POST'),
            '/mcp/tokens/(?P<token_id>-?[0-9]+)'=>array('DELETE'),
            '/mcp/activity'=>array('GET'),
            '/mcp/connectors'=>array('GET'),
            '/mcp/connectors/(?P<key>[A-Za-z0-9_-]+)'=>array('POST'),
            '/mcp/connectors/(?P<key>[A-Za-z0-9_-]+)/revoke'=>array('POST'),
            '/mcp/connectors/(?P<key>[A-Za-z0-9_-]+)/test'=>array('POST'),
            '/mcp/bridge'=>array('GET'),
            '/mcp/clients'=>array('GET'),
            '/mcp/clients/(?P<client_pk>-?[0-9]+)'=>array('DELETE'),
            '/mcp/endpoints'=>array('GET'),
            '/mcp/execution-health'=>array('GET')
        );
    }
    private static function tokenOut(array $t){
        return array('id'=>(int)$t['id'],'name'=>$t['name'],'prefix'=>$t['prefix'],'scope'=>$t['scope'],'is_active'=>(bool)$t['is_active'],'call_count'=>(int)($t['call_count']??0),'last_used_at'=>Records::iso($t['last_used_at']??null),'last_error'=>$t['last_error']?'Token call reported an error.':null,'created_at'=>Records::iso($t['created_at']??null));
    }
    public function listTokens(){
        $items=array();
        foreach($this->db->rows('SELECT * FROM '.$this->table('mcp_tokens').' ORDER BY id DESC LIMIT 200') as $r){$items[]=self::tokenOut($this->db->decode('mcp_tokens',$r));}
        return array('items'=>$items);
    }
    public function createToken($body,&$status){
        if(!is_object($body)){throw new ValidationFailure('Supply a JSON object');}
        foreach($body as $k=>$v){if(!in_array($k,array('name','scope','connector'),true)){throw new ValidationFailure('Unknown token field',$k);}}
        $this->db->requireTransaction();
        $name=isset($body->name)&&$body->name!==null?Compat::strip(DataRoutes::text(array('name'=>$body->name),'name','',null,120)):'';
        if($name===''){$name='AI assistant';}
        $scope=strtolower(Compat::strip(DataRoutes::text(array('scope'=>$body->scope??'write'),'scope','write',null,20)));
        if(!in_array($scope,array('read','write'),true)){throw new ValidationFailure("scope must be 'read' or 'write'",'scope');}
        $conn=strtolower(Compat::strip(DataRoutes::text(array('connector'=>$body->connector??'generic'),'connector','generic',null,40)));
        if(!isset(self::PROFILES[$conn])){$conn='generic';}
        $raw='smsmcp_'.bin2hex(random_bytes(24));
        $prefix=substr($raw,0,12);
        $hashed=hash('sha256',$raw);
        $row=$this->records->create('mcp_tokens',array('name'=>$name,'token_hash'=>$hashed,'prefix'=>$prefix,'scope'=>$scope,'is_active'=>true,'call_count'=>0));
        $status=201;
        return array('id'=>(int)$row['id'],'name'=>$row['name'],'scope'=>$row['scope'],'prefix'=>$row['prefix'],'connector'=>$conn,'token'=>$raw);
    }
    public function revokeToken($id){
        $this->db->requireTransaction();
        if(!$this->db->one('mcp_tokens',array('id'=>$id))){throw new AuthFailure(404,'Token not found');}
        $this->db->update('mcp_tokens',array('is_active'=>false),array('id'=>$id));
        return null;
    }
    public function activity(array $q){
        $limit=DataRoutes::integer($q['limit']??50,'limit',1,200);
        $total=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('mcp_calls'))[0]['n'];
        $items=array();
        foreach($this->db->rows('SELECT * FROM '.$this->table('mcp_calls').' ORDER BY id DESC LIMIT %d',array($limit)) as $r){
            $c=$this->db->decode('mcp_calls',$r);
            $items[]=array('id'=>(int)$c['id'],'tool'=>$c['tool'],'token_name'=>$c['token_name'],'request'=>$c['request'],'ok'=>(bool)$c['ok'],'status_code'=>$c['status_code']!==null?(int)$c['status_code']:null,'error'=>$c['error']?'MCP call reported an error.':null,'arguments'=>null,'duration_ms'=>$c['duration_ms']!==null?(int)$c['duration_ms']:null,'created_at'=>Records::iso($c['created_at']??null));
        }
        return array('total'=>$total,'items'=>$items);
    }
    private function ensureConnection($key){
        if(!isset(self::PROFILES[$key])){throw new AuthFailure(404,'Unknown connector');}
        $p=self::PROFILES[$key];
        $row=$this->db->one('mcp_connections',array('connector'=>$key));
        if(!$row){
            $row=$this->records->create('mcp_connections',array('connector'=>$key,'oauth_enabled'=>in_array('oauth',$p['auth_modes'],true),'default_scope'=>'write','toolset'=>$p['toolset'],'status'=>'unknown','authorize_count'=>0,'token_count'=>0,'call_count'=>0));
        }
        return $row;
    }
    private function connectionOut(array $conn,array $p){
        $rep=array();if(!empty($conn['last_test_report'])){$dec=json_decode((string)$conn['last_test_report'],true);if(is_array($dec)){$rep=$dec;}}
        $base=McpServer::wireBase();
        return array(
            'key'=>$p['key'],'label'=>$p['label'],'vendor'=>$p['vendor'],'blurb'=>$p['blurb'],'docs_url'=>$p['docs_url'],
            'setup_steps'=>array('Paste the WordPress MCP server URL below into your AI connector settings.','Authenticate with OAuth 2.1 or a static Bearer token from Access tokens.'),
            'endpoint'=>$base.$p['path'],'endpoint_ready'=>true,'auth_modes'=>$p['auth_modes'],'toolset'=>$p['toolset'],
            'tool_count'=>count(McpServer::toolCatalog($p['toolset'])),
            'protected_resource_metadata'=>$base.'/.well-known/oauth-protected-resource'.$p['path'],
            'authorization_server_metadata'=>$base.'/.well-known/oauth-authorization-server',
            'registration_endpoint'=>$base.$p['oauth_path'].'/register',
            'authorize_endpoint'=>$base.$p['oauth_path'].'/authorize',
            'token_endpoint'=>$base.$p['oauth_path'].'/token',
            'redirect_uris'=>$p['redirect_uris'],'allow_loopback_redirects'=>true,
            'client_id_metadata_documents'=>(bool)$p['cimd'],
            'protocol_versions'=>array('2025-06-18','2025-03-26','2024-11-05'),
            'oauth_enabled'=>(bool)$conn['oauth_enabled'],'default_scope'=>$conn['default_scope']?:'write',
            'toolset_active'=>$conn['toolset']?:$p['toolset'],'status'=>$conn['status']?:'unknown',
            'last_test_at'=>Records::iso($conn['last_test_at']??null),'last_test_report'=>$rep,'last_error'=>$conn['last_error'],
            'authorize_count'=>(int)($conn['authorize_count']??0),'token_count'=>(int)($conn['token_count']??0),
            'call_count'=>(int)($conn['call_count']??0),'last_call_at'=>Records::iso($conn['last_call_at']??null)
        );
    }
    public function listConnectors(){
        $this->db->requireTransaction();
        $base=McpServer::wireBase();
        $items=array();
        foreach(self::PROFILES as $k=>$p){$items[]=$this->connectionOut($this->ensureConnection($k),$p);}
        return array('items'=>$items,'public_base_url'=>$base,'base_url_source'=>'wordpress','ready'=>true,'problem'=>null,'note'=>null,'require_login'=>true,'protocol_versions'=>array('2025-06-18','2025-03-26','2024-11-05'),'bridge'=>array('available'=>true,'download_url'=>'/sender-suite/v1/mcp/bridge','script'=>'arena_mcp_bridge.py','endpoint'=>$base.'/connectors/arena/mcp','run'=>'python3 arena_mcp_bridge.py','config'=>array('mcpServers'=>array('sendersms'=>array('command'=>'python3','args'=>array('arena_mcp_bridge.py'))))));
    }
    public function updateConnector($key,$body){
        if(!is_object($body)){throw new ValidationFailure('Supply a JSON object');}
        foreach($body as $k=>$v){if(!in_array($k,array('oauth_enabled','default_scope','toolset'),true)){throw new ValidationFailure('Unknown connector field',$k);}}
        $this->db->requireTransaction();
        $conn=$this->ensureConnection($key);$p=self::PROFILES[$key];
        $patch=array('updated_at'=>$this->records->now());
        if(isset($body->oauth_enabled)&&$body->oauth_enabled!==null){$patch['oauth_enabled']=DataRoutes::boolean($body->oauth_enabled,'oauth_enabled');}
        if(isset($body->default_scope)&&$body->default_scope!==null){$sc=strtolower(Compat::strip(DataRoutes::text(array('default_scope'=>$body->default_scope),'default_scope','write',null,20)));$patch['default_scope']=in_array($sc,array('read','write'),true)?$sc:'write';}
        if(isset($body->toolset)&&$body->toolset!==null){$ts=strtolower(Compat::strip(DataRoutes::text(array('toolset'=>$body->toolset),'toolset',$p['toolset'],null,20)));$patch['toolset']=in_array($ts,array('core','full'),true)?$ts:$p['toolset'];}
        $this->db->update('mcp_connections',$patch,array('id'=>$conn['id']));
        return $this->connectionOut($this->db->one('mcp_connections',array('id'=>$conn['id'])),$p);
    }
    public function revokeConnector($key){
        $this->db->requireTransaction();
        $conn=$this->ensureConnection($key);
        $now=$this->records->now();
        $cnt=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('mcp_oauth_tokens').' WHERE connector=%s AND revoked_at IS NULL',array($key))[0]['n'];
        $this->db->execute('UPDATE '.$this->table('mcp_oauth_tokens').' SET revoked_at=%s WHERE connector=%s AND revoked_at IS NULL',array($now,$key));
        $this->db->update('mcp_connections',array('status'=>'unknown','updated_at'=>$now),array('id'=>$conn['id']));
        return array('revoked'=>$cnt);
    }
    public function listClients(){
        $grants=array();
        foreach($this->db->rows("SELECT client_id,COUNT(*) AS n FROM ".$this->table('mcp_oauth_tokens')." WHERE kind='refresh' AND revoked_at IS NULL GROUP BY client_id") as $gr){$grants[$gr['client_id']]=(int)$gr['n'];}
        $items=array();
        foreach($this->db->rows('SELECT * FROM '.$this->table('mcp_oauth_clients').' ORDER BY id DESC LIMIT 100') as $r){
            $c=$this->db->decode('mcp_oauth_clients',$r);
            $uris=array();if(!empty($c['redirect_uris'])){$d=json_decode((string)$c['redirect_uris'],true);if(is_array($d)){$uris=array_values($d);}else{$uris=array_filter(array_map('trim',explode("\n",(string)$c['redirect_uris'])));}}
            $items[]=array('id'=>(int)$c['id'],'client_id'=>$c['client_id'],'client_name'=>$c['client_name'],'connector'=>$c['connector'],'source'=>$c['source'],'redirect_uris'=>array_values($uris),'auth_method'=>$c['token_endpoint_auth_method'],'scope'=>$c['scope'],'is_active'=>(bool)$c['is_active'],'active_grants'=>$grants[$c['client_id']]??0,'created_at'=>Records::iso($c['created_at']??null),'last_used_at'=>Records::iso($c['last_used_at']??null));
        }
        return array('items'=>$items);
    }
    public function removeClient($pk){
        $this->db->requireTransaction();
        $c=$this->db->one('mcp_oauth_clients',array('id'=>$pk));
        if(!$c){throw new AuthFailure(404,'Unknown client');}
        $this->db->execute('UPDATE '.$this->table('mcp_oauth_tokens').' SET revoked_at=%s WHERE client_id=%s AND revoked_at IS NULL',array($this->records->now(),$c['client_id']));
        $this->db->update('mcp_oauth_clients',array('is_active'=>false),array('id'=>$pk));
        return null;
    }
    public function endpoints(){
        $items=array();
        foreach(self::PROFILES as $p){
            $items[]=array('key'=>$p['key'],'label'=>$p['label'],'endpoint'=>$p['path'],'protected_resource_metadata'=>'/.well-known/oauth-protected-resource'.$p['path'],'authorization_server_metadata'=>'/.well-known/oauth-authorization-server','register'=>$p['oauth_path'].'/register','authorize'=>$p['oauth_path'].'/authorize','token'=>$p['oauth_path'].'/token','auth_modes'=>$p['auth_modes']);
        }
        return array('public_base_url'=>null,'items'=>$items);
    }
    public function executionHealth($request){
        $activeTokens=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('mcp_tokens').' WHERE COALESCE(is_active,0)=1')[0]['n'];
        $phones=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('gateway_settings').' WHERE COALESCE(is_enabled,0)=1')[0]['n'];
        $aiSettings=(new AiAgent($this->db,$this->config))->settings();
        $authHdr=(string)$request->get_header('authorization');
        $sigv4=stripos($authHdr,'AWS4-HMAC-SHA256')!==false;
        $alerts=$sigv4?array('Remove AWS SigV4 Authorization header; use Bearer token or session cookie.'):array();
        return array('status'=>empty($alerts)?'healthy':'degraded','service'=>'sendersms-mcp-execution','auth_scheme'=>'Bearer (OAuth 2.1 / MCP Access Token — never AWS SigV4)','credential_isolation_ok'=>!$sigv4,'clock'=>array('healthy'=>true,'skew_seconds'=>0),'mcp_active_tokens'=>$activeTokens,'openrouter_configured'=>(bool)$aiSettings['openrouter_configured'],'sms_sender_ready'=>$phones>0,'alerts'=>$alerts,'redacted_request_headers'=>new \stdClass());
    }
    public static function handle($request,array $c,array $user,&$status){
        $path=trim(substr($request->get_route(),strlen('/sender-suite/v1/')),'/');$method=$request->get_method();
        if(strlen($request->get_body())>65536){throw new AuthFailure(413,'MCP settings request exceeds size limit.');}
        $s=new self($c['db'],$c['config']??null);$q=$request->get_query_params();
        $adminReq=function()use($user){if(($user['role']??'')!=='admin'){throw new AuthFailure(403,'Application administrator role required.');}};
        if($path==='mcp/execution-health'&&$method==='GET'){return $s->executionHealth($request);}
        if($path==='mcp/endpoints'&&$method==='GET'){return $s->endpoints();}
        if($path==='mcp/activity'&&$method==='GET'){return $s->activity($q);}
        if($path==='mcp/connectors'&&$method==='GET'){return $s->listConnectors();}
        if($path==='mcp/clients'&&$method==='GET'){return $s->listClients();}
        if($path==='mcp/tokens'&&$method==='GET'){return $s->listTokens();}
        if($path==='mcp/tokens'&&$method==='POST'){$adminReq();$raw=$request->get_body();return $s->createToken($raw!==''?DataRoutes::body($request):(object)array(),$status);}
        if(preg_match('~^mcp/tokens/(-?[0-9]+)$~D',$path,$m)&&$method==='DELETE'){$adminReq();$s->revokeToken(DataRoutes::integer($m[1],'token_id'));$status=204;return null;}
        if(preg_match('~^mcp/clients/(-?[0-9]+)$~D',$path,$m)&&$method==='DELETE'){$adminReq();$s->removeClient(DataRoutes::integer($m[1],'client_pk'));$status=204;return null;}
        if(preg_match('~^mcp/connectors/([A-Za-z0-9_-]+)(?:/(revoke|test))?$~D',$path,$m)&&$method==='POST'){
            $key=strtolower($m[1]);$sub=$m[2]??null;
            if($sub==='test'){return (new McpServer($c['db'],$c['config']??null))->runConnectorSelfTest($key);}
            $adminReq();
            if($sub==='revoke'){return $s->revokeConnector($key);}
            $raw=$request->get_body();return $s->updateConnector($key,$raw!==''?DataRoutes::body($request):(object)array());
        }
        if($path==='mcp/bridge'&&$method==='GET'){return array('script'=>'arena_mcp_bridge.py','content'=>McpServer::bridgeScript());}
        throw new AuthFailure(404,'Not found');
    }
}
