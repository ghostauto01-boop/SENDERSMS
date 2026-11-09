<?php
/** Offline tests for AdsManager (/ads/*) and AiAgent (/ai-agent/*). */
define('ARRAY_A','ARRAY_A');
foreach(array('schema','database','vault','compat','phone','number-filter','csv-reader','csv-preview','config','auth','auth-routes','records','access-settings','runtime-settings','contacts','contact-lists','contact-actions','contact-export','templating','suggestions','attachments','variables','contact-profile','variable-routes','templates','template-routes','reply-classifier','reply-rules','reply-routes','notification-center','csv-import','csv-url','import-jobs','import-routes','analytics','dashboard','campaign-reports','campaign-controls','campaign-followups','overview','system-guide','validator','sequences','calendar','invoices','inbox','followups','sms-receipts','sms-gate','provider-http','gateway-phones','calls-settings','email-manager','email-transport','ads-manager','campaign-worker','invoice-settlement','ai-agent','mcp-settings','mcp-server','inbox-reply','send-history','scheduled-sms','sms-outbox','data-routes') as $n){require_once __DIR__.'/../sender-suite/includes/class-'.$n.'.php';}
require_once __DIR__.'/sqlite-auth-db.php';
function home_url($p=''){return 'https://synthetic.example.test/';}function wp_parse_url($u){return parse_url($u);}function wp_unslash($v){return stripslashes($v);}function current_user_can($c){return true;}function wp_next_scheduled($h){return false;}function wp_schedule_event($t,$s,$h,$a=array()){return true;}
class WP_REST_Response {public $data;public $status;public $headers=array();public function __construct($d,$s){$this->data=$d;$this->status=$s;}public function header($n,$v){$this->headers[$n]=$v;}}
final class AdsAiReq {private $m;private $p;private $b;private $q;private $h;public function __construct($m,$p,$b='',$q=array(),$h=array()){$this->m=$m;$this->p='/sender-suite/v1/'.$p;$this->b=$b;$this->q=$q;$this->h=$h;}public function get_route(){return $this->p;}public function get_method(){return $this->m;}public function get_body(){return $this->b;}public function get_query_params(){return $this->q;}public function get_header($n){return $this->h[strtolower($n)]??'';}}

$count=0;$diffs=array();
function aa_check($n,$a,$e){global $count,$diffs;if($a===$e){$count++;}else{$diffs[]=array('name'=>$n,'actual'=>$a,'expected'=>$e);}}

$w=new SQLiteAuthDB();
$w->addTables(array_values(array_diff(array_keys(json_decode(file_get_contents(__DIR__.'/../sender-suite/schema/models.json'),true)),array('users','system_settings'))));
$db=new \SenderSuite\Database($w,new \SenderSuite\Schema('test_'));
$store=array();
$cfg=new \SenderSuite\Config(new \SenderSuite\Vault(str_repeat('synthetic-adsai-key-',4)),function()use(&$store){return $store;},function($v)use(&$store){$store=$v;});
$cfg->setup('operator','secret-pass');
$codec=new class{public function hash($v){return 's:'.$v;}public function verify($v,$h){return $h==='s:'.$v;}};
$auth=new \SenderSuite\AppAuth($db,$cfg,$codec);
$ctx=array('db'=>$db,'config'=>$cfg,'auth'=>$auth,'prefix'=>'test_');
$rp=new ReflectionProperty(\SenderSuite\AuthRoutes::class,'context');$rp->setAccessible(true);$rp->setValue(null,$ctx);
$admin=$auth->authenticate('operator','secret-pass');
$_COOKIE['sendsms_session']=$auth->signIn($admin)['token'];
$rec=new \SenderSuite\Records($db);

function aa_req($m,$p,$b='',$q=array(),$h=array()){return \SenderSuite\DataRoutes::dispatch(new AdsAiReq($m,$p,$b,$q,$h));}

// === ADS MANAGER ===
$c1=$rec->create('contacts',array('first_name'=>'Tunde','phone_number'=>'+2348031112222','email'=>'tunde@lagos.ng','city'=>'Lagos'));
$c2=$rec->create('contacts',array('first_name'=>'Bisi','phone_number'=>'+2348033334444','email'=>'bisi@lagos.ng','city'=>'Lagos'));
$lst=$rec->create('contact_lists',array('name'=>'Lagos Leads','contact_count'=>2));
$rec->create('contact_list_members',array('list_id'=>$lst['id'],'contact_id'=>$c1['id']));
$rec->create('contact_list_members',array('list_id'=>$lst['id'],'contact_id'=>$c2['id']));

$camp=aa_req('POST','ads/campaigns','{"name":"Q4 Promo","channel":"sms","objective":"replies","daily_limit":50}');
aa_check('create ads campaign',array($camp->status,$camp->data['status'],$camp->data['channel']),array(201,'draft','sms'));
$cid=$camp->data['id'];

$aud=aa_req('POST','ads/audiences',json_encode(array('name'=>'Lagos VIP','list_ids'=>array($lst['id']),'city'=>'Lagos')));
aa_check('create audience + preview',array($aud->status,aa_req('GET','ads/audiences/'.$aud->data['id'].'/preview')->data['eligible']),array(201,2));
$set=aa_req('POST','ads/audiences/'.$aud->data['id'].'/attach',json_encode(array('campaign_id'=>$cid)));
$sid=$set->data['id'];
aa_check('attach audience creates set',array($set->status,$set->data['campaign_id']),array(201,$cid));

$crA=aa_req('POST','ads/sets/'.$sid.'/creatives','{"name":"Copy A","body":"Hi {{first_name}}, reply YES","allocation":50}');
$crB=aa_req('POST','ads/sets/'.$sid.'/creatives','{"name":"Copy B","body":"Special Lagos offer","allocation":50}');
$crAid=$crA->data['id'];
aa_check('creative initial version 1',$crA->data['current_version'],1);
$updCr=aa_req('PATCH','ads/creatives/'.$crAid,'{"body":"Updated copy v2"}');
aa_check('creative edit bumps version to 2',array($updCr->data['current_version'],count(aa_req('GET','ads/creatives/'.$crAid.'/versions')->data['items'])),array(2,2));
$prom=aa_req('POST','ads/creatives/'.$crAid.'/promote','{}');
aa_check('promote winner sets allocation 100 and pauses sibling',array($prom->data['ok'],$db->one('ads_creatives',array('id'=>$crB->data['id']))['status']),array(true,'paused'));

// Re-activate B so simulation splits across 2 creatives
aa_req('PATCH','ads/creatives/'.$crB->data['id'],'{"status":"active","allocation":50}');
$val=aa_req('POST','ads/campaigns/'.$cid.'/validate','{}');
aa_check('validate campaign passes with set, creatives, eligible contacts',array($val->status,$val->data['valid'],$val->data['summary']['audience_size']),array(200,true,2));
$sim=aa_req('POST','ads/campaigns/'.$cid.'/simulate','{}');
aa_check('simulate campaign splits 2 contacts across 2 creatives',array($sim->data['eligible'],$sim->data['per_set'][0]['split'][0]['contacts'],$sim->data['per_set'][0]['split'][1]['contacts']),array(2,1,1));

$step=aa_req('POST','ads/campaigns/'.$cid.'/followup-steps','{"name":"24h Ping","wait_hours":24,"body":"Checking in"}');
aa_check('create & list followup step',array($step->status,count(aa_req('GET','ads/campaigns/'.$cid.'/followup-steps')->data['items'])),array(201,1));
$ftpl=aa_req('POST','ads/followup-templates','{"name":"Quick Nudge","channel":"sms","body":"Any thoughts?","delay_hours":48}');
aa_check('followup template CRUD + soft delete',array($ftpl->status,aa_req('DELETE','ads/followup-templates/'.$ftpl->data['id'])->data['soft_deleted']),array(201,true));

$ftask=aa_req('POST','ads/followups',json_encode(array('contact_id'=>$c1['id'],'campaign_id'=>$cid,'note'=>'Call back')));
aa_check('followup task complete',array($ftask->status,aa_req('POST','ads/followups/'.$ftask->data['id'].'/complete','{}')->data['status']),array(201,'completed'));

$cal=aa_req('POST','ads/calendar',json_encode(array('title'=>'Demo Call','contact_id'=>$c1['id'],'starts_at'=>'2026-11-08T10:00:00Z')));
aa_check('ads calendar create & delete',array($cal->status,count(aa_req('GET','ads/calendar')->data['items']),aa_req('DELETE','ads/calendar/'.$cal->data['id'])->status),array(201,1,204));

$sup=aa_req('POST','ads/suppression',json_encode(array('contact_id'=>$c2['id'],'reason'=>'stop')));
aa_check('ads suppression syncs contact is_opted_out',array($sup->status,(bool)$db->one('contacts',array('id'=>$c2['id']))['is_opted_out']),array(201,true));
$act=aa_req('POST','ads/contacts/'.$c1['id'].'/action','{"action":"interested"}');
aa_check('ads contact action updates lead_status',array($act->status,$db->one('contacts',array('id'=>$c1['id']))['lead_status']),array(200,'interested'));

$exp=aa_req('GET','ads/export/campaigns');
aa_check('ads CSV export returns ContactCsvDownload with attachment header',array($exp->status,$exp->data instanceof \SenderSuite\ContactCsvDownload,$exp->headers['Content-Disposition']),array(200,true,'attachment; filename="ads-campaigns.csv"'));
aa_check('ads overview + reference + search',array(aa_req('GET','ads/overview')->data['total_campaigns'],count(aa_req('GET','ads/reference')->data['objectives']),count(aa_req('GET','ads/search','',array('q'=>'Q4'))->data['campaigns'])),array(1,10,1));

// Live Ads Campaign Launch, Dispatch, Public 302 Click Tracking & Follow-up Processing!
$rpCsv=new ReflectionProperty(\SenderSuite\CsvUrl::class,'resolver');$rpCsv->setAccessible(true);
$rpCsv->setValue(null,function($h){return array('93.184.216.34');});
$rpSms=new ReflectionProperty(\SenderSuite\SmsGate::class,'transport');$rpSms->setAccessible(true);
$rpSms->setValue(null,function($plan,$method,$headers,$body){return array('ok'=>true,'http'=>202,'body'=>json_encode(array('id'=>'ads-sms-1')));});
$rpHttp=new ReflectionProperty(\SenderSuite\ProviderHttp::class,'transport');$rpHttp->setAccessible(true);
$rpHttp->setValue(null,function($method,$url,$headers,$body){
    if(strpos($url,'openrouter.ai/api/v1/models')!==false){return array('ok'=>true,'http'=>200,'body'=>'{}','json'=>array('data'=>array(array('id'=>'openai/gpt-4o-mini'))));}
    if(strpos($url,'openrouter.ai/api/v1/chat/completions')!==false){return array('ok'=>true,'http'=>200,'body'=>'{}','json'=>array('choices'=>array(array('message'=>array('content'=>'Lagos SMS draft ready!')))));}
    return array('ok'=>true,'http'=>200,'body'=>'{}','json'=>array());
});
aa_req('POST','settings/gateway/phones','{"name":"AdsSIM","username":"u","password":"p"}');
$adsCt=$rec->create('contacts',array('first_name'=>'Tunde','phone_number'=>'+2348035556666','email'=>'tunde@lagos.ng'));
$audUpd=aa_req('POST','ads/audiences',json_encode(array('name'=>'Tunde Direct','source_type'=>'manual','channel'=>'sms','contact_ids'=>array($adsCt['id']))));
$setUpd=aa_req('POST','ads/campaigns/'.$cid.'/sets',json_encode(array('name'=>'Direct Set','audience_id'=>$audUpd->data['id'])));
aa_req('POST','ads/sets/'.$setUpd->data['id'].'/creatives','{"name":"Direct Cr","body":"Hi {{first_name}}","tracking_link":"https://sendify.ng/promo"}');
$lnch=aa_req('POST','ads/campaigns/'.$cid.'/launch','{}');
$disp=aa_req('POST','ads/campaigns/'.$cid.'/dispatch','{}');
aa_check('ads campaign launch + dispatch sends SMS & transitions active',array($lnch->status,$lnch->data['campaign']['status'],$disp->data['dispatched']>=1),array(200,'active',true));
$asgnRow=$db->rows('SELECT id FROM '.$db->schema()->table('ads_assignments').' WHERE campaign_id=%d ORDER BY id DESC LIMIT 1',array($cid))[0];
$clk=\SenderSuite\CampaignWorker::dispatchClickRedirect(new AdsAiReq('GET','ads/r/'.$asgnRow['id']));
aa_check('ads public 302 click redirect resolves tracking_link',array($clk->status,$clk->headers['Location']??''),array(302,'https://sendify.ng/promo'));
aa_check('ads followups process runs cleanly',aa_req('POST','ads/followups/process','{}')->data['ok'],true);

// === AI AGENT ===
$st=aa_req('PUT','ai-agent/settings','{"api_key":"sk-or-v1-secret9876543210","auto_fallback":true,"auto_approve":false}');
aa_check('ai-agent encrypted api_key masked',array($st->status,$st->data['openrouter_configured'],$st->data['openrouter_hint'],strpos(json_encode($st->data),'secret9876543210')===false),array(200,true,'sk-or-v***3210',true));
$vc=aa_req('PUT','ai-agent/settings/voice','{"auto_send":true,"language":"en-NG","read_aloud":false}');
aa_check('ai-agent voice settings saved',array($vc->status,$vc->data['voice']['language']),array(200,'en-NG'));
$pr=aa_req('PUT','ai-agent/presets','{"preset":"@sendify-fast","check":true}');
aa_check('ai-agent preset strips @ and warns offline',array($pr->status,$pr->data['preset'],count($pr->data['warnings'])===1),array(200,'sendify-fast',true));

$sess=aa_req('POST','ai-agent/sessions','{"name":"Campaign Brainstorm","system_prompt":"Be concise"}');
$sessId=$sess->data['session']['id'];
aa_check('ai-agent session created + listed',array($sess->status,count(aa_req('GET','ai-agent/sessions')->data['sessions'])),array(200,1));
$rec->create('ai_agent_messages',array('session_id'=>$sessId,'role'=>'user','content'=>'Draft an SMS for Lagos leads'));
$pending=$rec->create('ai_agent_actions',array('session_id'=>$sessId,'tool'=>'send_sms','args'=>array('to'=>'+2348031112222'),'summary'=>'Send SMS','status'=>'pending','undoable'=>false));
$detail=aa_req('GET','ai-agent/sessions/'.$sessId);
aa_check('ai-agent getSession includes messages and pending action card',array(count($detail->data['messages']),count($detail->data['pending_actions'])),array(1,1));
$canc=aa_req('POST','ai-agent/actions/'.$pending['id'].'/cancel','{}');
aa_check('ai-agent cancel pending action + 409 repeat guard',array($canc->data['status'],aa_req('POST','ai-agent/actions/'.$pending['id'].'/cancel','{}')->status),array('cancelled',409));

aa_check('ai-agent global memory put/get/delete',array(aa_req('PUT','ai-agent/memory/global','{"text":"Always use NGN"}')->data['text'],aa_req('GET','ai-agent/memory/global')->data['text'],aa_req('DELETE','ai-agent/memory/global')->status,aa_req('GET','ai-agent/memory/global')->data['text']),array('Always use NGN','Always use NGN',200,''));
aa_check('ai-agent session memory put/get',array(aa_req('PUT','ai-agent/memory/'.$sessId,'{"text":"Focus on Lagos"}')->status,aa_req('GET','ai-agent/memory/'.$sessId)->data['text']),array(200,'Focus on Lagos'));

$sk=aa_req('POST','ai-agent/skills/json','{"name":"Nigeria Tone","description":"Local phrasing","content":"Keep messages polite."}');
$skId=$sk->data['skill']['id'];
aa_check('ai-agent skill sanitized upsert + get + delete',array($sk->data['skill']['name'],aa_req('GET','ai-agent/skills/'.$skId)->data['skill']['content'],aa_req('DELETE','ai-agent/skills/'.$skId)->status),array('Nigeria_Tone','Keep messages polite.',200));

$af=$rec->create('ai_agent_files',array('session_id'=>$sessId,'filename'=>'leads.csv','content'=>"name,phone\nAda,+2348031234567\nBola,+2348051234567\n",'preview'=>array('summary'=>'2 rows'),'size_bytes'=>54));
$gf=aa_req('GET','ai-agent/files/'.$af['id'],'',array('offset'=>1,'limit'=>1));
aa_check('ai-agent getFile slices CSV rows',array($gf->status,count($gf->data['rows']),$gf->data['rows'][0]['name']),array(200,1,'Bola'));
aa_check('ai-agent health + tools + models',array(aa_req('GET','ai-agent/health')->data['state'],count(aa_req('GET','ai-agent/tools')->data['tools']),aa_req('GET','ai-agent/models')->data['provider']),array('down',4,'openrouter'));

// Live OpenRouter Chat, Test, Transcription, File Upload, Pending Action Approval & Transactional Undo!
aa_check('openrouter settings test 200',aa_req('POST','ai-agent/settings/test','{}')->data['ok'],true);
$chatRes=aa_req('POST','ai-agent/sessions/'.$sessId.'/chat','{"message":"Write a promo SMS"}');
aa_check('openrouter chat appends assistant reply',array($chatRes->status,$chatRes->data['message']['content']),array(200,'Lagos SMS draft ready!'));
$trRes=aa_req('POST','ai-agent/transcribe','{"audio_base64":"UklGRg=="}');
aa_check('openrouter audio transcription 200',array($trRes->status,$trRes->data['ok']),array(200,true));
$upRes=aa_req('POST','ai-agent/sessions/'.$sessId.'/files','{"filename":"vip.csv","content":"name,phone\nKemi,+2348091112222\n"}');
aa_check('ai-agent CSV upload parsed',array($upRes->status,$upRes->data['file']['summary']),array(200,'1 CSV rows'));
$actCreate=$rec->create('ai_agent_actions',array('session_id'=>$sessId,'tool'=>'create_list','args'=>array('name'=>'AI VIP List','description'=>'Created by AI'),'summary'=>'Create list','status'=>'pending','undoable'=>false));
$appRes=aa_req('POST','ai-agent/actions/'.$actCreate['id'].'/approve','{}');
aa_check('approve pending AI action executes & marks undoable',array($appRes->status,$appRes->data['status'],$appRes->data['undoable']),array(200,'executed',true));
$undoRes=aa_req('POST','ai-agent/activity/'.$actCreate['id'].'/undo','{}');
aa_check('undo executed AI action removes created list',array($undoRes->status,$undoRes->data['undone'],$db->one('contact_lists',array('id'=>$appRes->data['result']['created_list_id']))===null),array(200,true,true));

// === MCP SETTINGS, LIVE OAUTH 2.1 DCR + PKCE S256 & JSON-RPC 2.0 EXECUTION ===
$mtok=aa_req('POST','mcp/tokens','{"name":"Claude Code","scope":"write","connector":"claude"}');
$rawMcpTok=$mtok->data['token'];
aa_check('mcp token created once + hashed in DB',array($mtok->status,strpos($rawMcpTok,'smsmcp_')===0,count(aa_req('GET','mcp/tokens')->data['items']),array_key_exists('token',aa_req('GET','mcp/tokens')->data['items'][0])),array(201,true,1,false));

// Test MCP JSON-RPC 2.0 wire server with static Bearer token!
$wUnauth=\SenderSuite\McpServer::dispatchWire(new AdsAiReq('POST','mcp-wire/mcp','{"jsonrpc":"2.0","id":1,"method":"initialize"}'));
aa_check('mcp wire unauthenticated returns 401 with RFC 9728 WWW-Authenticate',array($wUnauth->status,strpos($wUnauth->headers['WWW-Authenticate']??'','resource_metadata=')!==false),array(401,true));
$wInit=\SenderSuite\McpServer::dispatchWire(new AdsAiReq('POST','mcp-wire/mcp','{"jsonrpc":"2.0","id":1,"method":"initialize"}',array(),array('authorization'=>'Bearer '.$rawMcpTok)));
$wCall=\SenderSuite\McpServer::dispatchWire(new AdsAiReq('POST','mcp-wire/mcp',json_encode(array('jsonrpc'=>'2.0','id'=>2,'method'=>'tools/call','params'=>array('name'=>'dashboard_stats','arguments'=>new stdClass()))),array(),array('authorization'=>'Bearer '.$rawMcpTok)));
aa_check('mcp wire initialize + tools/call executes & logs mcp_calls',array($wInit->status,$wInit->data['result']['protocolVersion'],$wCall->data['result']['isError']),array(200,'2025-06-18',false));

// Test OAuth 2.1 Dynamic Client Registration + PKCE S256 token exchange!
$dcr=\SenderSuite\McpServer::dispatchWire(new AdsAiReq('POST','mcp-wire/connectors/claude/oauth/register','{"client_name":"Claude Desktop","redirect_uris":["https://claude.ai/api/mcp/auth_callback"]}'));
$verifier='pkce_secret_verifier_value_1234567890_abcdef';
$challenge=rtrim(strtr(base64_encode(hash('sha256',$verifier,true)),'+/','-_'),'=');
$rawCode='authcode_live_123';
$rec->create('mcp_oauth_codes',array('code_hash'=>hash('sha256',$rawCode),'client_id'=>$dcr->data['client_id'],'redirect_uri'=>'https://claude.ai/api/mcp/auth_callback','scope'=>'read write','code_challenge'=>$challenge,'code_challenge_method'=>'S256','connector'=>'claude','expires_at'=>'2027-01-01 00:00:00.000000'));
$tokEx=\SenderSuite\McpServer::dispatchWire(new AdsAiReq('POST','mcp-wire/connectors/claude/oauth/token',json_encode(array('grant_type'=>'authorization_code','code'=>$rawCode,'code_verifier'=>$verifier))));
aa_check('mcp OAuth 2.1 DCR + PKCE S256 token exchange issues Bearer token',array($dcr->status,$tokEx->status,strpos($tokEx->data['access_token'],'mcpa_')===0),array(201,200,true));

$eh=aa_req('GET','mcp/execution-health');
aa_check('mcp execution-health reflects active tokens + openrouter configured',array($eh->status,$eh->data['status'],$eh->data['mcp_active_tokens'],$eh->data['openrouter_configured']),array(200,'healthy',1,true));
$ehSig=aa_req('GET','mcp/execution-health','',array(),array('authorization'=>'AWS4-HMAC-SHA256 Credential=bad'));
aa_check('mcp execution-health detects SigV4 contamination',array($ehSig->data['status'],$ehSig->data['credential_isolation_ok']),array('degraded',false));
aa_check('mcp revoke token sets is_active false',array(aa_req('DELETE','mcp/tokens/'.$mtok->data['id'])->status,aa_req('GET','mcp/tokens')->data['items'][0]['is_active']),array(204,false));

$mconn=aa_req('GET','mcp/connectors');
aa_check('mcp connectors list 4 profiles ready',array($mconn->status,count($mconn->data['items']),$mconn->data['ready']),array(200,4,true));
$mupd=aa_req('POST','mcp/connectors/chatgpt','{"oauth_enabled":false,"default_scope":"read","toolset":"full"}');
aa_check('mcp update connector settings',array($mupd->status,$mupd->data['oauth_enabled'],$mupd->data['default_scope'],$mupd->data['toolset_active']),array(200,false,'read','full'));
$mcli=aa_req('GET','mcp/clients');
aa_check('mcp clients active_grants + revoke connector',array(count($mcli->data['items']),$mcli->data['items'][0]['active_grants'],aa_req('POST','mcp/connectors/claude/revoke','{}')->data['revoked'],aa_req('DELETE','mcp/clients/'.$mcli->data['items'][0]['id'])->status),array(1,1,2,204));
$selfTest=aa_req('POST','mcp/connectors/claude/test','{}');
$bridge=aa_req('GET','mcp/bridge');
aa_check('mcp activity + endpoints + live connector self-test + stdio bridge',array(aa_req('GET','mcp/activity')->data['total']>=1,count(aa_req('GET','mcp/endpoints')->data['items']),$selfTest->status,$selfTest->data['status'],$bridge->status,$bridge->data['script']),array(true,4,200,'ok',200,'arena_mcp_bridge.py'));

echo json_encode(array('ok'=>empty($diffs),'assertions'=>$count,'diffs'=>$diffs,'php'=>PHP_VERSION,'native_wordpress_verified'=>false,'native_mysql_verified'=>false),JSON_THROW_ON_ERROR);
