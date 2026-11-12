<?php
/** Offline tests for EmailManager (/email/*, /mailbox/*, /mail/*). */
define('ARRAY_A','ARRAY_A');
foreach(array('schema','database','vault','compat','phone','number-filter','csv-reader','csv-preview','config','auth','auth-routes','records','access-settings','runtime-settings','contacts','contact-lists','contact-actions','contact-export','templating','suggestions','attachments','variables','contact-profile','variable-routes','templates','template-routes','reply-classifier','reply-rules','reply-routes','notification-center','csv-import','csv-url','import-jobs','import-routes','analytics','dashboard','campaign-reports','campaign-controls','campaign-followups','overview','system-guide','validator','sequences','calendar','invoices','inbox','followups','sms-receipts','sms-gate','provider-http','gateway-phones','calls-settings','email-manager','email-transport','ads-manager','campaign-worker','invoice-settlement','ai-agent','mcp-settings','mcp-server','inbox-reply','send-history','scheduled-sms','sms-outbox','data-routes') as $n){require_once __DIR__.'/../sender-suite/includes/class-'.$n.'.php';}
require_once __DIR__.'/sqlite-auth-db.php';
function home_url($p=''){return 'https://synthetic.example.test/';}function wp_parse_url($u){return parse_url($u);}function wp_unslash($v){return stripslashes($v);}function current_user_can($c){return true;}function wp_next_scheduled($h){return false;}function wp_schedule_event($t,$s,$h,$a=array()){return true;}
class WP_REST_Response {public $data;public $status;public $headers=array();public function __construct($d,$s){$this->data=$d;$this->status=$s;}public function header($n,$v){$this->headers[$n]=$v;}}
final class EmReq {private $m;private $p;private $b;private $q;private $h;public function __construct($m,$p,$b='',$q=array(),$h=array()){$this->m=$m;$this->p='/sender-suite/v1/'.$p;$this->b=$b;$this->q=$q;$this->h=$h;}public function get_route(){return $this->p;}public function get_method(){return $this->m;}public function get_body(){return $this->b;}public function get_query_params(){return $this->q;}public function get_header($n){return $this->h[strtolower($n)]??'';}}

$count=0;$diffs=array();
function em_check($n,$a,$e){global $count,$diffs;if($a===$e){$count++;}else{$diffs[]=array('name'=>$n,'actual'=>$a,'expected'=>$e);}}

$w=new SQLiteAuthDB();
$w->addTables(array_values(array_diff(array_keys(json_decode(file_get_contents(__DIR__.'/../sender-suite/schema/models.json'),true)),array('users','system_settings'))));
$db=new \SenderSuite\Database($w,new \SenderSuite\Schema('test_'));
$store=array();
$cfg=new \SenderSuite\Config(new \SenderSuite\Vault(str_repeat('synthetic-email-key-',4)),function()use(&$store){return $store;},function($v)use(&$store){$store=$v;});
$cfg->setup('operator','secret-pass');
$codec=new class{public function hash($v){return 's:'.$v;}public function verify($v,$h){return $h==='s:'.$v;}};
$auth=new \SenderSuite\AppAuth($db,$cfg,$codec);
$ctx=array('db'=>$db,'config'=>$cfg,'auth'=>$auth,'prefix'=>'test_');
$rp=new ReflectionProperty(\SenderSuite\AuthRoutes::class,'context');$rp->setAccessible(true);$rp->setValue(null,$ctx);
$admin=$auth->authenticate('operator','secret-pass');
$_COOKIE['sendsms_session']=$auth->signIn($admin)['token'];
$rec=new \SenderSuite\Records($db);

function em_req($m,$p,$b='',$q=array()){return \SenderSuite\DataRoutes::dispatch(new EmReq($m,$p,$b,$q));}

// 1. Email accounts CRUD + encrypted API key + webhook reveal/rotate + 409 campaign guard
$r=em_req('POST','email/accounts','{"name":"Primary Brevo","from_name":"Sendify Team","from_email":"hello@sendify.ng","api_key":"xkeysib-secret-key-12345678","reply_to":"support@sendify.ng"}');
em_check('create email account 201',array($r->status,$r->data['from_email'],$r->data['is_default'],$r->data['has_api_key'],strpos(json_encode($r->data),'xkeysib-secret-key-12345678')===false),array(201,'hello@sendify.ng',true,true,true));
$acctId=$r->data['id'];
$r2=em_req('POST','email/accounts','{"name":"Backup Brevo","from_name":"Alerts","from_email":"alerts@sendify.ng","api_key":"xkeysib-backup-key-87654321"}');
$acct2=$r2->data['id'];
em_check('second account not default',$r2->data['is_default'],false);
$def=em_req('POST','email/accounts/'.$acct2.'/default','{}');
em_check('switch default account',array($def->status,$def->data['is_default']),array(200,true));
$rev=em_req('POST','email/accounts/'.$acctId.'/webhook/reveal','{}');
$tok1=$rev->data['token'];
$rot=em_req('POST','email/accounts/'.$acctId.'/webhook/rotate','{}');
em_check('rotate webhook token changes token',array($rot->status,strlen($tok1)===32,$rot->data['token']!==$tok1),array(200,true,true));
$camp=$rec->create('campaigns',array('name'=>'Email Ref','channel'=>'email','email_account_id'=>$acctId));
em_check('delete account referenced by campaign returns 409',em_req('DELETE','email/accounts/'.$acctId)->status,409);
$db->delete('campaigns',array('id'=>$camp['id']));
em_check('delete unused account returns 204',em_req('DELETE','email/accounts/'.$acct2)->status,204);

// 2. Senders, health, domains, reference, preview, suppression, stats, overview, engagement
$contact=$rec->create('contacts',array('first_name'=>'Ngozi','last_name'=>'Okafor','email'=>'ngozi@business.ng','phone_number'=>'+2348039998888'));
$tpl=$rec->create('templates',array('name'=>'Welcome Email','channel'=>'email','subject'=>'Welcome {{first_name}}','body'=>'Hello {{full_name}}!'));
$prev=em_req('POST','email/preview',json_encode(array('template_id'=>$tpl['id'],'contact_id'=>$contact['id'])));
em_check('email preview personalizes variables',array($prev->status,$prev->data['subject'],$prev->data['text']),array(200,'Welcome Ngozi','Hello Ngozi Okafor!'));

$sup=em_req('POST','email/suppression','{"email_address":"NGOZI@business.ng","reason":"unsubscribe","hard_bounce":false}');
em_check('add email suppression syncs contact opt-out',array($sup->status,(bool)$db->one('contacts',array('id'=>$contact['id']))['is_email_opted_out']),array(201,true));
$supId=$sup->data['id'];
em_check('list suppression',em_req('GET','email/suppression')->data['total'],1);
em_check('remove suppression restores contact opt-in',array(em_req('DELETE','email/suppression/'.$supId)->status,(bool)$db->one('contacts',array('id'=>$contact['id']))['is_email_opted_out']),array(204,false));

$dom=$rec->create('sending_domains',array('domain'=>'sendify.ng','status'=>'authenticated','records_json'=>json_encode(array(array('type'=>'TXT','host'=>'_dmarc.sendify.ng')))));
em_check('list & get domain',array(em_req('GET','email/domains')->data['total'],em_req('GET','email/domains/'.$dom['id'])->data['domain']),array(1,'sendify.ng'));
em_check('delete domain 204',em_req('DELETE','email/domains/'.$dom['id'])->status,204);

$conv=$rec->create('conversations',array('contact_id'=>$contact['id'],'phone_number'=>'ngozi@business.ng','channel'=>'email','unread_count'=>2,'status'=>'active'));
$msg=$rec->create('messages',array('conversation_id'=>$conv['id'],'contact_id'=>$contact['id'],'direction'=>'outgoing','channel'=>'email','email_account_id'=>$acctId,'subject'=>'Offer','body'=>'Body','to_address'=>'ngozi@business.ng','status'=>'delivered','open_count'=>2,'click_count'=>1,'idempotency_key'=>'em-msg-1'));
$rec->create('email_events',array('event_type'=>'clicked','account_id'=>$acctId,'message_id'=>$msg['id'],'contact_id'=>$contact['id'],'email_address'=>'ngozi@business.ng','link'=>'https://sendify.ng/deal'));

em_check('email overview',array(em_req('GET','email/overview')->status,em_req('GET','email/overview')->data['unread_conversations']),array(200,1));
em_check('email reference emailable count',em_req('GET','email/reference')->data['emailable_contacts'],1);
em_check('email history',em_req('GET','email/history')->data['total'],1);
em_check('email deliverability',em_req('GET','email/accounts/'.$acctId.'/deliverability')->status,200);
em_check('message events unique link count',em_req('GET','email/messages/'.$msg['id'].'/events')->data['summary']['unique_links_clicked'],1);
em_check('contact engagement clicked links',em_req('GET','email/contacts/'.$contact['id'].'/engagement')->data['links'][0]['url'],'https://sendify.ng/deal');
em_check('email inbox unread + mark read',array(em_req('GET','email/inbox/unread-count')->data['unread_conversations'],em_req('POST','email/inbox/conversations/'.$conv['id'].'/read','{}')->status,em_req('GET','email/inbox/unread-count')->data['unread_conversations']),array(1,200,0));

// 3. Mailbox + Mail mirror
$mb=$rec->create('email_mailboxes',array('name'=>'Support Gmail','provider'=>'gmail_api','email_address'=>'replies@sendify.ng','credential_encrypted'=>'enc','folders'=>'INBOX,SPAM','is_active'=>true,'poll_interval'=>60));
$mm=$rec->create('mailbox_messages',array('mailbox_id'=>$mb['id'],'provider_message_id'=>'gmsg-101','thread_id'=>'gthr-1','folder'=>'INBOX','subject'=>'Re: Offer','from_name'=>'Ngozi','from_address'=>'ngozi@business.ng','snippet'=>'Interested!','body_text'=>'Full reply text','is_read'=>false,'is_starred'=>false));
em_check('list mailboxes & report',array(em_req('GET','mailbox')->data['count'],em_req('GET','mailbox/report')->data['connected']),array(1,1));
em_check('patch mailbox name',em_req('PATCH','mailbox/'.$mb['id'],'{"name":"Sales Gmail"}')->data['mailbox']['name'],'Sales Gmail');
em_check('mail overview unread count',em_req('GET','mail/overview')->data['total_unread'],1);
em_check('mail list messages',em_req('GET','mail/messages','',array('folder'=>'INBOX'))->data['total'],1);
em_check('mail read single message with thread',em_req('GET','mail/messages/'.$mb['id'].'/gmsg-101')->data['body_text'],'Full reply text');
$fl=em_req('POST','mail/messages/'.$mb['id'].'/gmsg-101/flags','{"read":true,"starred":true}');
em_check('mail set flags updates mirror',array($fl->status,$fl->data['is_read'],$fl->data['is_starred'],em_req('GET','mail/overview')->data['total_unread']),array(200,true,true,0));

// 4. Live Brevo Email Transport, DNS SPF/DKIM/DMARC Verification, Signed Unsubscribe, Brevo Webhook & Gmail Sync
$rpHttp=new ReflectionProperty(\SenderSuite\ProviderHttp::class,'transport');$rpHttp->setAccessible(true);
$rpHttp->setValue(null,function($method,$url,$headers,$body){
    if(strpos($url,'api.brevo.com/v3/account')!==false){return array('ok'=>true,'http'=>200,'body'=>'{}','json'=>array('email'=>'hello@sendify.ng'));}
    if(strpos($url,'api.brevo.com/v3/smtp/email')!==false){return array('ok'=>true,'http'=>201,'body'=>'{}','json'=>array('messageId'=>'<brevo-live-99@sendify.ng>'));}
    if(strpos($url,'gmail.googleapis.com/gmail/v1/users/me/messages?')!==false){return array('ok'=>true,'http'=>200,'body'=>'{}','json'=>array('messages'=>array(array('id'=>'gmsg-202'))));}
    if(strpos($url,'gmail.googleapis.com/gmail/v1/users/me/messages/gmsg-202')!==false){return array('ok'=>true,'http'=>200,'body'=>'{}','json'=>array('id'=>'gmsg-202','threadId'=>'gthr-2','snippet'=>'Fresh Gmail sync','labelIds'=>array('INBOX','UNREAD'),'payload'=>array('headers'=>array(array('name'=>'Subject','value'=>'Hello from Gmail'),array('name'=>'From','value'=>'Ngozi <ngozi@business.ng>')))));}
    return array('ok'=>true,'http'=>200,'body'=>'{}','json'=>array());
});
$rpDns=new ReflectionProperty(\SenderSuite\ProviderHttp::class,'dns');$rpDns->setAccessible(true);
$rpDns->setValue(null,function($host,$type){
    if($host==='sendify.ng'&&$type==='TXT'){return array(array('txt'=>'v=spf1 include:spf.brevo.com ~all'));}
    if($host==='_dmarc.sendify.ng'&&$type==='TXT'){return array(array('txt'=>'v=DMARC1; p=quarantine'));}
    if($host==='mail._domainkey.sendify.ng'&&$type==='TXT'){return array(array('txt'=>'v=DKIM1; k=rsa; p=MIGfMA0'));}
    return array();
});
em_check('brevo account test 200',em_req('POST','email/accounts/'.$acctId.'/test','{}')->data['ok'],true);
$dom=em_req('POST','email/domains','{"domain":"sendify.ng"}');
$domId=$dom->data['domain']['id']??$dom->data['id'];
$domVer=em_req('POST','email/domains/'.$domId.'/verify','{}');
$domSt=$domVer->data['domain']['status']??$domVer->data['status'];
em_check('domain created + SPF/DKIM/DMARC verified',array($dom->status,$domSt),array(201,'authenticated'));
$es=em_req('POST','email/send',json_encode(array('contact_id'=>$contact['id'],'subject'=>'Welcome Offer','body'=>'<p>Hello {{first_name}}</p>')));
em_check('brevo email sent + logged',array($es->status,$es->data['provider_message_id']),array(200,'<brevo-live-99@sendify.ng>'));
$et=new \SenderSuite\EmailTransport($db,$cfg);
$whRev=em_req('POST','email/accounts/'.$acctId.'/webhook/reveal','{}');
$whTok=$whRev->data['token'];
$bw=\SenderSuite\EmailTransport::dispatchBrevoWebhook(new EmReq('POST','webhooks/brevo/'.$acctId,json_encode(array('event'=>'opened','message-id'=>'<brevo-live-99@sendify.ng>','email'=>'ngozi@business.ng')),array('token'=>$whTok)));
em_check('brevo webhook records open event',$bw->data['processed'],1);
$encCred=$cfg->sealRuntimeSecret($db->schema()->table('email_mailboxes').'/'.$mb['id'].'/credential','ya29.mock');
$db->update('email_mailboxes',array('credential_encrypted'=>$encCred,'send_replies'=>true),array('id'=>$mb['id']));
em_check('gmail sync + reply 200',array(em_req('POST','mailbox/'.$mb['id'].'/sync','{}')->data['synced'],em_req('POST','mail/refresh','{}')->data['ok'],em_req('POST','mail/messages/'.$mb['id'].'/gmsg-101/reply','{"body":"Thanks Ngozi!"}')->data['ok']),array(1,true,true));
$unsubTok=$et->signUnsubscribe($contact['id'],$contact['email']);
$unsubRes=\SenderSuite\EmailTransport::dispatchPublicUnsubscribe(new EmReq('POST','email/unsubscribe','',array('token'=>$unsubTok)));
em_check('signed public unsubscribe opts out email contact',array($unsubRes->data['ok'],(bool)$db->one('contacts',array('id'=>$contact['id']))['is_email_opted_out']),array(true,true));
em_req('PUT','settings/google-oauth','{"client_id":"gid-123","client_secret":"gsec-456"}');
em_check('google oauth start url generated',strpos(em_req('GET','mailbox/google/start')->data['auth_url'],'accounts.google.com')!==false,true);

echo json_encode(array('ok'=>empty($diffs),'assertions'=>$count,'diffs'=>$diffs,'php'=>PHP_VERSION,'native_wordpress_verified'=>false,'native_mysql_verified'=>false),JSON_THROW_ON_ERROR);
