<?php
/** Offline tests for CallsSettings, per-phone SmsReceipts, and non-settlement Invoices extensions. */
define('ARRAY_A','ARRAY_A');
foreach(array('schema','database','vault','compat','phone','number-filter','csv-reader','csv-preview','config','auth','auth-routes','records','access-settings','runtime-settings','contacts','contact-lists','contact-actions','contact-export','templating','suggestions','attachments','variables','contact-profile','variable-routes','templates','template-routes','reply-classifier','reply-rules','reply-routes','notification-center','csv-import','csv-url','import-jobs','import-routes','analytics','dashboard','campaign-reports','campaign-controls','campaign-followups','overview','system-guide','validator','sequences','calendar','invoices','inbox','followups','sms-receipts','sms-gate','provider-http','gateway-phones','calls-settings','email-manager','email-transport','ads-manager','campaign-worker','invoice-settlement','ai-agent','mcp-settings','mcp-server','inbox-reply','send-history','scheduled-sms','sms-outbox','data-routes') as $n){require_once __DIR__.'/../sender-suite/includes/class-'.$n.'.php';}
require_once __DIR__.'/sqlite-auth-db.php';
function home_url($p=''){return 'https://synthetic.example.test/';}function wp_parse_url($u){return parse_url($u);}function wp_unslash($v){return stripslashes($v);}function current_user_can($c){return true;}function wp_next_scheduled($h){return false;}function wp_schedule_event($t,$s,$h,$a=array()){return true;}
class WP_REST_Response {public $data;public $status;public $headers=array();public function __construct($d,$s){$this->data=$d;$this->status=$s;}public function header($n,$v){$this->headers[$n]=$v;}}
final class Wave2Req {private $m;private $p;private $b;private $q;private $h;public function __construct($m,$p,$b='',$q=array(),$h=array()){$this->m=$m;$this->p='/sender-suite/v1/'.$p;$this->b=$b;$this->q=$q;$this->h=$h;}public function get_route(){return $this->p;}public function get_method(){return $this->m;}public function get_body(){return $this->b;}public function get_query_params(){return $this->q;}public function get_header($n){return $this->h[strtolower($n)]??($this->h[$n]??'');}}

$count=0;$diffs=array();
function cs_check($name,$a,$e){global $count,$diffs;if($a===$e){$count++;}else{$diffs[]=array('name'=>$name,'actual'=>$a,'expected'=>$e);}}

$w=new SQLiteAuthDB();
$w->addTables(array_values(array_diff(array_keys(json_decode(file_get_contents(__DIR__.'/../sender-suite/schema/models.json'),true)),array('users','system_settings'))));
$db=new \SenderSuite\Database($w,new \SenderSuite\Schema('test_'));
$store=array();
$cfg=new \SenderSuite\Config(new \SenderSuite\Vault(str_repeat('synthetic-calls-key-',4)),function()use(&$store){return $store;},function($v)use(&$store){$store=$v;});
$cfg->setup('operator','secret-pass');
$codec=new class{public function hash($v){return 's:'.$v;}public function verify($v,$h){return $h==='s:'.$v;}};
$auth=new \SenderSuite\AppAuth($db,$cfg,$codec);
$ctx=array('db'=>$db,'config'=>$cfg,'auth'=>$auth,'prefix'=>'test_');
$rp=new ReflectionProperty(\SenderSuite\AuthRoutes::class,'context');$rp->setAccessible(true);$rp->setValue(null,$ctx);

$admin=$auth->authenticate('operator','secret-pass');
$adminToken=$auth->signIn($admin)['token'];
$rec=new \SenderSuite\Records($db);
$viewer=$rec->create('users',array('username'=>'viewer1','email'=>'v@example.ng','password_hash'=>'s:vp','role'=>'viewer','is_active'=>true));
$viewerToken=$auth->signIn($viewer)['token'];
$_COOKIE['sendsms_session']=$adminToken;

function cs_req($m,$p,$b='',$q=array(),$h=array()){return \SenderSuite\DataRoutes::dispatch(new Wave2Req($m,$p,$b,$q,$h));}

// 1. CallGate config + encrypted secret + viewer guard
$r=cs_req('GET','calls/config');
cs_check('initial callgate unconfigured',array($r->status,$r->data['configured'],$r->data['dial_mode']),array(200,false,'direct'));
$_COOKIE['sendsms_session']=$viewerToken;
cs_check('viewer cannot save callgate config',cs_req('PUT','settings/callgate','{"dial_mode":"direct"}')->status,403);
$_COOKIE['sendsms_session']=$adminToken;
$r=cs_req('PUT','settings/callgate',json_encode(array('base_url'=>'https://callgate.example.ng','username'=>'cguser','password'=>'super-secret-call-pass','webhook_secret'=>str_repeat('k',32),'dial_mode'=>'direct')));
$cgGet=cs_req('GET','calls/config')->data;
cs_check('save callgate config',array($r->status,$r->data['configured'],$r->data['dial_mode'],$cgGet['password_set'],$cgGet['webhook_secret_set']),array(200,true,'direct',true,true));
cs_check('callgate secret never leaked in response',strpos(json_encode($cgGet),'super-secret-call-pass'),false);

// 2. Start call (direct mode) + active call + log-direct + call logs
$contact=$rec->create('contacts',array('first_name'=>'Chidi','phone_number'=>'+2348031234567'));
$r=cs_req('POST','calls/start',json_encode(array('contact_id'=>$contact['id'])));
cs_check('direct mode startCall logs outgoing call',array($r->status,$r->data['success'],$r->data['mode'],$r->data['phone']),array(200,true,'direct','+2348031234567'));
$r=cs_req('POST','calls/log-direct',json_encode(array('phone_number'=>'+2348031234567')));
cs_check('logDirect creates direct_dial call',array($r->status,$r->data['success']),array(200,true));
$initCall=$rec->create('call_logs',array('contact_id'=>$contact['id'],'phone_number'=>'+2348031234567','direction'=>'outgoing','status'=>'initiated'));
$r=cs_req('GET','calls/active');
cs_check('activeCall returns latest open call',array($r->status,$r->data['active'],$r->data['status']),array(200,true,'initiated'));
$r=cs_req('GET','calls/logs','',array('search'=>'Chidi'));
cs_check('callLogs lists contact calls',array($r->status,$r->data['total'],$r->data['items'][0]['contact_name']),array(200,2,'Chidi'));

// 3. Signed CallGate webhook (HMAC, dedup, 409 mismatch, call lifecycle)
$cs=new \SenderSuite\CallsSettings($db,$cfg,function(){return '2026-11-07 12:00:10.000000';});
$ts=(string)(new DateTimeImmutable('2026-11-07 12:00:10',new DateTimeZone('UTC')))->getTimestamp();
$secret=str_repeat('k',32);
$rawStart=json_encode(array('id'=>'cg-evt-1','event'=>'call:started','payload'=>array('phoneNumber'=>'+2348031234567')));
$sigStart=hash_hmac('sha256',$rawStart.$ts,$secret);
$out1=$db->transaction(function()use($cs,$rawStart,$sigStart,$ts){return $cs->receiveCallgateWebhook($rawStart,$sigStart,$ts);});
cs_check('callgate webhook started',$out1,array('ok'=>true));
$dup=$db->transaction(function()use($cs,$rawStart,$sigStart,$ts){return $cs->receiveCallgateWebhook($rawStart,$sigStart,$ts);});
cs_check('callgate webhook duplicate idempotent',$dup,array('ok'=>true,'duplicate'=>true));
$tampered=json_encode(array('id'=>'cg-evt-1','event'=>'call:ended','payload'=>array('phoneNumber'=>'+2348031234567')));
$sigTampered=hash_hmac('sha256',$tampered.$ts,$secret);
try{$db->transaction(function()use($cs,$tampered,$sigTampered,$ts){$cs->receiveCallgateWebhook($tampered,$sigTampered,$ts);});cs_check('reused event id conflict',200,409);}catch(\SenderSuite\AuthFailure $e){cs_check('reused event id conflict',$e->status,409);}

$csLater=new \SenderSuite\CallsSettings($db,$cfg,function(){return '2026-11-07 12:01:05.000000';});
$tsEnd=(string)(new DateTimeImmutable('2026-11-07 12:01:05',new DateTimeZone('UTC')))->getTimestamp();
$rawEnd=json_encode(array('id'=>'cg-evt-2','event'=>'call:ended','payload'=>array('phoneNumber'=>'+2348031234567')));
$sigEnd=hash_hmac('sha256',$rawEnd.$tsEnd,$secret);
$db->transaction(function()use($csLater,$rawEnd,$sigEnd,$tsEnd){return $csLater->receiveCallgateWebhook($rawEnd,$sigEnd,$tsEnd);});
$logs=cs_req('GET','calls/logs')->data['items'];
cs_check('call:ended records 55s duration',array($logs[0]['status'],$logs[0]['duration_seconds']),array('ended',55));

// 4. Gateway summary, SIM, routing, Google OAuth, Push subscriptions, Webhook logs, Device info
$r=cs_req('PUT','settings/gateway/sim','{"sim":2}');
cs_check('set gateway sim',array($r->status,$r->data['sim_number']),array(200,2));
$r=cs_req('PUT','settings/gateway/routing','{"routing_mode":"default_phone","sim_failover":false}');
cs_check('set gateway routing default_phone',array($r->status,$r->data['routing_mode']),array(200,'default_phone'));
cs_check('multi-phone rotation round_robin supported',cs_req('PUT','settings/gateway/routing','{"routing_mode":"round_robin"}')->status,200);
cs_req('PUT','settings/gateway/routing','{"routing_mode":"default_phone"}');
$r=cs_req('GET','settings/gateway');
cs_check('gateway summary reflects sim & routing',array($r->status,$r->data['sim_number'],$r->data['routing_mode']),array(200,2,'default_phone'));

$r=cs_req('PUT','settings/google-oauth','{"client_id":"1234567890-abcdef.apps.googleusercontent.com","client_secret":"GOCSPX-secret123456"}');
cs_check('save google oauth encrypted',array($r->status,$r->data['configured'],$r->data['has_secret'],strpos(json_encode($r->data),'GOCSPX-secret123456')===false),array(200,true,true,true));

$r=cs_req('POST','notifications/push/subscribe','{"endpoint":"https://fcm.googleapis.com/fcm/send/abc","keys":{"p256dh":"pkey1","auth":"akey1"},"user_agent":"Firefox"}');
cs_check('push subscribe',array($r->status,$r->data['devices']),array(200,1));
$r=cs_req('GET','notifications/push/subscriptions');
cs_check('push list host only',array($r->status,$r->data['total'],$r->data['items'][0]['endpoint_host']),array(200,1,'fcm.googleapis.com'));
$r=cs_req('POST','notifications/push/unsubscribe','{"endpoint":"https://fcm.googleapis.com/fcm/send/abc"}');
cs_check('push unsubscribe',array($r->status,cs_req('GET','notifications/push/subscriptions')->data['total']),array(200,0));

$r=cs_req('GET','webhooks/logs');
cs_check('webhook logs lists processed callgate events',array($r->status,$r->data['total']),array(200,2));
$r=cs_req('GET','inbox/device-info');
cs_check('inbox device info returns stored summary',array($r->status,$r->data['success']),array(200,true));

// Inject deterministic offline ProviderHttp mock for CallGate, SMS-Gate, Pushover, and Paystack!
$rpHttp=new ReflectionProperty(\SenderSuite\ProviderHttp::class,'transport');$rpHttp->setAccessible(true);
$rpHttp->setValue(null,function($method,$url,$headers,$body){
    if(strpos($url,'api.paystack.co/bank')!==false){return array('ok'=>true,'http'=>200,'body'=>'{}','json'=>array('status'=>true,'data'=>array(array('name'=>'GTBank'))));}
    if(strpos($url,'api.paystack.co/transaction/initialize')!==false){return array('ok'=>true,'http'=>200,'body'=>'{}','json'=>array('status'=>true,'data'=>array('authorization_url'=>'https://checkout.paystack.com/pay_abc','access_code'=>'ac_123','reference'=>'INV-REF-1')));}
    if(strpos($url,'api.paystack.co/transaction/verify/')!==false){return array('ok'=>true,'http'=>200,'body'=>'{}','json'=>array('status'=>true,'data'=>array('status'=>'success','amount'=>10750,'channel'=>'card','paid_at'=>'2026-11-07T12:00:00Z')));}
    return array('ok'=>true,'http'=>200,'body'=>'{"id":"ext-1","messages":[]}','json'=>array('id'=>'ext-1','messages'=>array()));
});
$rpCsv=new ReflectionProperty(\SenderSuite\CsvUrl::class,'resolver');$rpCsv->setAccessible(true);
$rpCsv->setValue(null,function($h){return array('93.184.216.34');});
$rpSms=new ReflectionProperty(\SenderSuite\SmsGate::class,'transport');$rpSms->setAccessible(true);
$rpSms->setValue(null,function($plan,$method,$headers,$body){return array('ok'=>true,'http'=>202,'body'=>json_encode(array('id'=>'smsg-live-1')));});

// Live CallGate, SMS-Gate, Pushover & message retry verification
$rec->create('system_settings',array('key'=>'callgate.url','value'=>'https://callgate.example.test'));
$rec->create('system_settings',array('key'=>'pushover.app_token','value'=>'tok123'));
$rec->create('system_settings',array('key'=>'pushover.user_key','value'=>'usr123'));
cs_check('live callgate test 200',cs_req('POST','calls/test','{}')->status,200);
cs_check('live callgate end 200',cs_req('POST','calls/end','{}')->status,200);
cs_check('live callgate delete webhook 200',cs_req('DELETE','calls/webhooks/wh-1','{}')->status,200);
cs_check('live inbox poll-now 200',cs_req('POST','inbox/poll-now','{}')->status,200);
cs_check('live pushover test 200',cs_req('POST','notifications/push/test','{}')->status,200);

// 5. Per-phone SmsReceipts + Inbound SMS HMAC + STOP opt-out + probe
$pr=cs_req('POST','settings/gateway/phones','{"name":"SIM1","username":"u1","password":"p1","webhook_secret":"'.str_repeat('w',32).'"}');
$phoneId=$pr->data['phone']['db_id'];
$msg=$rec->create('messages',array('conversation_id'=>1,'contact_id'=>$contact['id'],'direction'=>'outgoing','channel'=>'sms','body'=>'Hi','status'=>'failed','provider'=>'smsgate','provider_message_id'=>'prov-phone-1','idempotency_key'=>'idem-ph-1'));
$retMsg=cs_req('POST','send/messages/'.$msg['id'].'/retry','{}');
cs_check('retry failed sms dispatches via SmsGate',array($retMsg->status,$retMsg->data['ok']),array(200,true));
$sr=new \SenderSuite\SmsReceipts($db,$cfg,function(){return '2026-11-07 12:00:00.000000';});
$tsP=(string)(new DateTimeImmutable('2026-11-07 12:00:00',new DateTimeZone('UTC')))->getTimestamp();
$rawP=json_encode(array('event'=>'sms:delivered','payload'=>array('messageId'=>'prov-phone-1')));
$sigP=hash_hmac('sha256',$rawP.$tsP,str_repeat('w',32));
$resP=$db->transaction(function()use($sr,$phoneId,$rawP,$sigP,$tsP){return $sr->receivePhone($phoneId,$rawP,$sigP,$tsP);});
cs_check('per-phone webhook delivered',array($resP['matched'],$db->one('messages',array('id'=>$msg['id']))['status']),array(true,'delivered'));
cs_check('smsgateway probe returns 200 ok',\SenderSuite\SmsReceipts::probe()->status,200);

// Inbound SMS webhook with auto-reply rule + STOP opt-out
$inSecRow=$rec->create('system_settings',array('key'=>\SenderSuite\SmsReceipts::KEY,'value'=>''));
$db->update('system_settings',array('value'=>$cfg->sealRuntimeSecret($db->schema()->table('system_settings').'/'.$inSecRow['id'].'/'.\SenderSuite\SmsReceipts::KEY,str_repeat('w',32))),array('id'=>$inSecRow['id']));
$rec->create('auto_reply_rules',array('name'=>'Price Info','keywords'=>'PRICE','match_type'=>'contains','reply_body'=>'Our starter plan is NGN 5,000.','is_enabled'=>true,'channel'=>'sms','priority'=>10,'times_triggered'=>0));
$inRaw1=json_encode(array('event'=>'sms:received','payload'=>array('messageId'=>'in-1','phoneNumber'=>$contact['phone_number'],'message'=>'What is the PRICE?')));
$inRes1=\SenderSuite\CampaignWorker::dispatchInboundSms(new Wave2Req('POST','webhooks/smsgate/inbound',$inRaw1,array(),array('x-timestamp'=>$tsP,'x-signature'=>hash_hmac('sha256',$inRaw1.$tsP,str_repeat('w',32)))));
cs_check('inbound SMS creates message and triggers auto_reply_rule',array($inRes1->status,$inRes1->data['auto_replied']),array(200,true));
$inRawStop=json_encode(array('event'=>'sms:received','payload'=>array('messageId'=>'in-2','phoneNumber'=>$contact['phone_number'],'message'=>'STOP')));
$inResStop=\SenderSuite\CampaignWorker::dispatchInboundSms(new Wave2Req('POST','webhooks/smsgate/inbound',$inRawStop,array(),array('x-timestamp'=>$tsP,'x-signature'=>hash_hmac('sha256',$inRawStop.$tsP,str_repeat('w',32)))));
cs_check('inbound STOP immediately opts out contact',array($inResStop->data['opted_out'],(bool)$db->one('contacts',array('id'=>$contact['id']))['is_opted_out']),array(true,true));

// 6. Invoices + Live Paystack Settlement, Public Checkout, Manual Payment & HMAC-SHA512 Webhook
$r=cs_req('GET','invoices/currencies');
cs_check('invoices currencies',array($r->status,$r->data['default'],count($r->data['currencies'])),array(200,'NGN',7));
$r=cs_req('PUT','invoices/settings','{"business_name":"Abuja Studio","prefix":"abj-1","default_currency":"USD","default_tax_rate":7.5,"paystack_public_key":"pk_test_123","paystack_secret_key":"sk_test_secret987654","paystack_channels":["card","bank"]}');
cs_check('save invoice settings with encrypted paystack',array($r->status,$r->data['business_name'],$r->data['prefix'],$r->data['default_currency'],$r->data['default_tax_rate'],$r->data['paystack']['configured'],$r->data['paystack']['mode'],strpos(json_encode($r->data),'sk_test_secret987654')===false),array(200,'Abuja Studio','ABJ-1','USD',7.5,true,'test',true));
$inv=cs_req('POST','invoices',json_encode(array('customer_name'=>'Ada','customer_email'=>'ada@studio.ng','currency'=>'USD','tax_rate'=>7.5,'items'=>array(array('description'=>'Consulting','quantity'=>2,'unit_price_minor'=>5000)))))->data['invoice'];
$rot=cs_req('POST','invoices/'.$inv['id'].'/rotate-link','{}');
cs_check('rotate-link preserves null public_url',array($rot->status,$rot->data['success'],$rot->data['public_url']),array(200,true,null));
$an=cs_req('GET','invoices/analytics','',array('currency'=>'USD','days'=>90));
cs_check('invoices analytics shape',array($an->status,$an->data['currency'],count($an->data['monthly'])),array(200,'USD',12));
cs_check('paystack settings test 200',cs_req('POST','invoices/settings/test','{}')->data['ok'],true);
$lnk=cs_req('POST','invoices/links','{"title":"Quick Deposit","amount_minor":25000,"currency":"NGN"}');
cs_check('create reusable payment link',array($lnk->status,$lnk->data['invoice']['kind'],cs_req('GET','invoices/links')->data['total']),array(201,'link',1));
$payInit=cs_req('POST','invoices/'.$inv['id'].'/pay','{}');
cs_check('paystack checkout initialized',array($payInit->status,$payInit->data['authorization_url']),array(200,'https://checkout.paystack.com/pay_abc'));
$pubTok=$db->one('invoices',array('id'=>$inv['id']))['public_token'];
$pubView=\SenderSuite\InvoiceSettlement::dispatchPublic(new Wave2Req('GET','invoices/public/'.$pubTok));
cs_check('public invoice view strips private notes',array($pubView->status,$pubView->data['invoice']['number'],array_key_exists('notes',$pubView->data['invoice'])),array(200,$inv['number'],false));
$whBody=json_encode(array('event'=>'charge.success','data'=>array('reference'=>$payInit->data['reference'],'amount'=>$inv['total_minor'],'channel'=>'card','metadata'=>array('invoice_id'=>$inv['id']))));
$whSig=hash_hmac('sha512',$whBody,'sk_test_secret987654');
$whRes=\SenderSuite\InvoiceSettlement::dispatchPaystackWebhook(new Wave2Req('POST','invoices/paystack/webhook',$whBody,array(),array('x-paystack-signature'=>$whSig)));
$whDup=\SenderSuite\InvoiceSettlement::dispatchPaystackWebhook(new Wave2Req('POST','invoices/paystack/webhook',$whBody,array(),array('x-paystack-signature'=>$whSig)));
cs_check('paystack HMAC-SHA512 webhook settles invoice + idempotent replay',array($whRes->status,$db->one('invoices',array('id'=>$inv['id']))['status'],$whDup->data['already_settled'],count(cs_req('GET','invoices/payments')->data['items'])),array(200,'paid',true,1));

echo json_encode(array('ok'=>empty($diffs),'assertions'=>$count,'diffs'=>$diffs,'php'=>PHP_VERSION,'native_wordpress_verified'=>false,'native_mysql_verified'=>false),JSON_THROW_ON_ERROR);
