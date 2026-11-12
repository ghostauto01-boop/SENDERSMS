<?php
namespace SenderSuite;

/**
 * Native WordPress Email Transport, Brevo v3 API, DNS SPF/DKIM/DMARC verification,
 * signed HMAC unsubscribe, Brevo webhooks, Google OAuth 2.0 & Gmail REST sync/reply.
 */
final class EmailTransport {
    private $db;private $config;private $records;
    public function __construct(Database $db,Config $config=null,callable $clock=null){
        $this->db=$db;$this->config=$config;$this->records=new Records($db,$clock);
    }
    private function table($n){return Schema::identifier($this->db->schema()->table($n));}
    private function openAcctSecret($id,$field,$cipher){
        if(!is_string($cipher)||$cipher===''||$this->config===null){return '';}
        try{return (string)$this->config->openRuntimeSecret($this->db->schema()->table('email_accounts').'/'.$id.'/'.$field,$cipher);}catch(\Throwable $e){return '';}
    }
    private function openMbSecret($id,$field,$cipher){
        if(!is_string($cipher)||$cipher===''||$this->config===null){return '';}
        try{return (string)$this->config->openRuntimeSecret($this->db->schema()->table('email_mailboxes').'/'.$id.'/'.$field,$cipher);}catch(\Throwable $e){return '';}
    }
    private function hmacKey(){
        $sec=$this->config!==null?(string)$this->config->get('SECRET_KEY'):'';
        if($sec===''){throw new AuthFailure(503,'Application signing secret is not configured.');}
        return hash_hmac('sha256','sender-suite-email-unsub-v1',$sec,true);
    }
    public function signUnsubscribe($contactId,$email){
        $payload=$contactId.':'.strtolower(trim((string)$email));
        $sig=rtrim(strtr(base64_encode(hash_hmac('sha256',$payload,$this->hmacKey(),true)),'+/','-_'),'=');
        return rtrim(strtr(base64_encode($payload),'+/','-_'),'=').'.'.$sig;
    }
    public function verifyUnsubscribe($token){
        if(!is_string($token)||strpos($token,'.')===false||strlen($token)>512){return null;}
        list($b64,$sig)=explode('.',$token,2);
        $payload=base64_decode(strtr($b64,'-_','+/'),true);
        if(!is_string($payload)||strpos($payload,':')===false){return null;}
        $expect=rtrim(strtr(base64_encode(hash_hmac('sha256',$payload,$this->hmacKey(),true)),'+/','-_'),'=');
        if(!hash_equals($expect,$sig)){return null;}
        list($cid,$email)=explode(':',$payload,2);
        return ctype_digit($cid)?array('contact_id'=>(int)$cid,'email'=>$email):null;
    }
    public static function dispatchPublicUnsubscribe($request){
        $reqId=bin2hex(random_bytes(16));
        try{
            $c=AuthRoutes::context();
            if(!$c['config']->ready()){throw new AuthFailure(503,'Application is not configured.');}
            $q=$request->get_query_params();
            $tok=(string)($q['token']??'');
            if($tok===''&&$request->get_body()!==''){
                $dec=json_decode($request->get_body(),true);
                if(is_array($dec)&&isset($dec['token'])){$tok=(string)$dec['token'];}
            }
            $svc=new self($c['db'],$c['config']);
            $parsed=$svc->verifyUnsubscribe($tok);
            if($parsed===null){throw new AuthFailure(400,'Invalid or expired unsubscribe token.');}
            $out=$c['db']->transaction(function()use($c,$svc,$parsed){
                $now=$svc->records->now();
                $contact=$c['db']->one('contacts',array('id'=>$parsed['contact_id']));
                if($contact){
                    $c['db']->update('contacts',array('is_email_opted_out'=>true,'updated_at'=>$now),array('id'=>$contact['id']));
                }
                if(!$c['db']->one('email_suppressions',array('email_address'=>$parsed['email']))){
                    $svc->records->create('email_suppressions',array('email_address'=>$parsed['email'],'contact_id'=>$contact?$contact['id']:null,'reason'=>'unsubscribe','source'=>'link','hard_bounce'=>false,'suppressed_at'=>$now));
                }
                $svc->records->create('email_events',array('event_type'=>'unsubscribed','contact_id'=>$contact?$contact['id']:null,'email_address'=>$parsed['email'],'detail'=>'Public one-click unsubscribe','occurred_at'=>$now));
                return array('ok'=>true,'unsubscribed'=>true,'email'=>$parsed['email']);
            });
            return AuthRoutes::response($out,200,$reqId);
        }catch(AuthFailure $e){
            return AuthRoutes::response(AuthRoutes::error($e->status,$e->getMessage(),$reqId),$e->status,$reqId);
        }catch(\Throwable $e){
            return AuthRoutes::response(AuthRoutes::error(503,'Unsubscribe service unavailable.',$reqId),503,$reqId);
        }
    }
    public static function dispatchBrevoWebhook($request){
        $reqId=bin2hex(random_bytes(16));
        try{
            if(strlen($request->get_body())>262144){throw new AuthFailure(413,'Webhook payload too large.');}
            $c=AuthRoutes::context();
            if(!$c['config']->ready()){throw new AuthFailure(503,'Application is not configured.');}
            $svc=new self($c['db'],$c['config']);
            $q=$request->get_query_params();
            $supplied=(string)($q['token']??$request->get_header('x-sib-webhook-token')??'');
            if($supplied===''){
                $auth=(string)$request->get_header('authorization');
                if(stripos($auth,'Bearer ')===0){$supplied=trim(substr($auth,7));}
            }
            if($supplied===''){throw new AuthFailure(401,'Missing Brevo webhook token.');}
            $route=$request->get_route();
            $acctId=null;
            if(preg_match('~/webhooks/brevo/(-?[0-9]+)$~D',$route,$m)){$acctId=(int)$m[1];}
            $matched=null;
            $rows=$acctId!==null?($c['db']->one('email_accounts',array('id'=>$acctId))?array($c['db']->one('email_accounts',array('id'=>$acctId))):array()):$c['db']->rows('SELECT * FROM '.Schema::identifier($c['db']->schema()->table('email_accounts')).' LIMIT 100');
            foreach($rows as $r){
                $dec=$c['db']->decode('email_accounts',$r);
                $tok=$svc->openAcctSecret($dec['id'],'webhook_token',$dec['webhook_token']??'');
                if($tok!==''&&hash_equals($tok,$supplied)){$matched=$dec;break;}
            }
            if(!$matched){throw new AuthFailure(401,'Invalid Brevo webhook token.');}
            $body=DataRoutes::body($request);
            $events=is_array($body)?$body:(is_object($body)?array($body):array());
            if(!$events){throw new ValidationFailure('Supply a JSON webhook event');}
            $processed=$c['db']->transaction(function()use($c,$svc,$matched,$events){
                $now=$svc->records->now();$n=0;
                foreach($events as $ev){
                    if(!is_object($ev)){continue;}
                    $rawType=strtolower((string)($ev->event??'delivered'));
                    $map=array('delivered'=>'delivered','request'=>'sent','deferred'=>'deferred','opened'=>'opened','unique_opened'=>'opened','click'=>'clicked','hard_bounce'=>'bounced','soft_bounce'=>'soft_bounce','invalid_email'=>'bounced','spam'=>'complained','blocked'=>'blocked','unsubscribed'=>'unsubscribed');
                    $etype=$map[$rawType]??'delivered';
                    $email=strtolower(trim((string)($ev->email??'')));
                    $pmid=(string)($ev->{'message-id'}??$ev->message_id??'');
                    $peid=(string)($ev->id??($pmid!==''?$pmid.':'.$etype.':'.$email:''));
                    if($peid!==''&&$c['db']->one('email_events',array('provider_event_id'=>$peid))){continue;}
                    $msg=$pmid!==''?$c['db']->one('messages',array('provider_message_id'=>$pmid)):null;
                    $contact=$msg&&$msg['contact_id']?$c['db']->one('contacts',array('id'=>$msg['contact_id'])):($email!==''?$c['db']->one('contacts',array('email_lower'=>$email)):null);
                    $svc->records->create('email_events',array('event_type'=>$etype,'account_id'=>(int)$matched['id'],'message_id'=>$msg?(int)$msg['id']:null,'contact_id'=>$contact?(int)$contact['id']:null,'campaign_id'=>$msg&&$msg['campaign_id']?(int)$msg['campaign_id']:null,'email_address'=>$email?:null,'subject'=>isset($ev->subject)?substr((string)$ev->subject,0,255):($msg?$msg['subject']:null),'provider_event_id'=>$peid?:null,'link'=>isset($ev->link)?substr((string)$ev->link,0,1000):null,'detail'=>$rawType,'occurred_at'=>$now));
                    if($msg){
                        $mp=array();
                        if($etype==='delivered'){$mp['status']='delivered';$mp['delivered_at']=$now;}
                        elseif($etype==='opened'){$mp['open_count']=((int)($msg['open_count']??0))+1;if(empty($msg['opened_at'])){$mp['opened_at']=$now;}}
                        elseif($etype==='clicked'){$mp['click_count']=((int)($msg['click_count']??0))+1;if(empty($msg['clicked_at'])){$mp['clicked_at']=$now;}}
                        elseif($etype==='bounced'){$mp['status']='failed';$mp['bounced_at']=$now;$mp['bounced_hard']=$rawType==='hard_bounce';$mp['failed_at']=$now;}
                        if($mp){$c['db']->update('messages',$mp,array('id'=>$msg['id']));}
                    }
                    if($contact){
                        if($rawType==='hard_bounce'||$rawType==='invalid_email'){$c['db']->update('contacts',array('is_email_undeliverable'=>true,'updated_at'=>$now),array('id'=>$contact['id']));}
                        elseif($etype==='unsubscribed'||$etype==='complained'){$c['db']->update('contacts',array('is_email_opted_out'=>true,'updated_at'=>$now),array('id'=>$contact['id']));}
                    }
                    $n++;
                }
                $c['db']->update('email_accounts',array('last_webhook_event_at'=>$now,'updated_at'=>$now),array('id'=>$matched['id']));
                return $n;
            });
            return AuthRoutes::response(array('ok'=>true,'processed'=>$processed),200,$reqId);
        }catch(ValidationFailure $e){
            return AuthRoutes::response(AuthRoutes::error(422,$e->getMessage(),$reqId,$e->field,true),422,$reqId);
        }catch(AuthFailure $e){
            return AuthRoutes::response(AuthRoutes::error($e->status,$e->getMessage(),$reqId),$e->status,$reqId);
        }catch(\Throwable $e){
            return AuthRoutes::response(AuthRoutes::error(503,'Brevo webhook processing failed.',$reqId),503,$reqId);
        }
    }
    private function accountWithKey($id){
        $acct=$this->db->one('email_accounts',array('id'=>$id));
        if(!$acct){throw new AuthFailure(404,'Email account not found');}
        $key=$this->openAcctSecret($acct['id'],'api_key',$acct['api_key_encrypted']??'');
        if($key===''){throw new AuthFailure(503,'Brevo API key is missing or unreadable for this email account.');}
        return array($acct,$key);
    }
    public function testAccount($id){
        list($acct,$key)=$this->accountWithKey($id);
        $r=ProviderHttp::request('GET','https://api.brevo.com/v3/account',array('api-key'=>$key));
        $now=$this->records->now();
        $ok=!empty($r['ok'])&&is_array($r['json']);
        $this->db->transaction(function()use($id,$ok,$now,$r){
            $this->db->update('email_accounts',array('connection_status'=>$ok?'ok':'error','last_tested_at'=>$now,'last_error'=>$ok?null:('Brevo HTTP '.(int)($r['http']??0)),'updated_at'=>$now),array('id'=>$id));
        });
        return array('ok'=>$ok,'status'=>$ok?'ok':'error','http'=>(int)($r['http']??0),'account_email'=>$ok?($r['json']['email']??$acct['from_email']):null,'company'=>$ok?($r['json']['companyName']??null):null);
    }
    public function accountSenders($id){
        list($acct,$key)=$this->accountWithKey($id);
        return $this->brevoSenders($key);
    }
    public function brevoSenders($apiKey){
        $apiKey=Compat::strip((string)$apiKey);
        if($apiKey===''){throw new ValidationFailure('Brevo API key is required','api_key');}
        $r=ProviderHttp::request('GET','https://api.brevo.com/v3/senders',array('api-key'=>$apiKey));
        if(empty($r['ok'])||!is_array($r['json'])){throw new AuthFailure(502,'Brevo senders lookup failed (HTTP '.(int)($r['http']??0).').');}
        $out=array();
        foreach(($r['json']['senders']??array()) as $s){
            if(is_array($s)&&!empty($s['email'])){$out[]=array('name'=>(string)($s['name']??''),'email'=>strtolower((string)$s['email']),'active'=>(bool)($s['active']??true));}
        }
        return array('senders'=>$out);
    }
    public function createDomain($body){
        if(!is_object($body)||empty($body->domain)){throw new ValidationFailure('Supply a domain name','domain');}
        $dom=strtolower(Compat::strip(DataRoutes::text(array('domain'=>$body->domain),'domain','',3,253)));
        if(!preg_match('/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$/D',$dom)){throw new ValidationFailure('Invalid domain name','domain');}
        if($this->db->one('sending_domains',array('domain'=>$dom))){throw new AuthFailure(409,'Domain already exists');}
        $acctId=isset($body->email_account_id)&&$body->email_account_id!==null?DataRoutes::integer($body->email_account_id,'email_account_id'):null;
        $rua=isset($body->dmarc_rua)&&$body->dmarc_rua!==null?Compat::strip(DataRoutes::text(array('dmarc_rua'=>$body->dmarc_rua),'dmarc_rua','',null,255)):('mailto:dmarc@'.$dom);
        $records=array(
            array('type'=>'TXT','host'=>$dom,'value'=>'v=spf1 include:spf.brevo.com ~all','purpose'=>'spf','verified'=>false),
            array('type'=>'TXT','host'=>'mail._domainkey.'.$dom,'value'=>'k=rsa; p=MIGfMA0GCSqGSIb3DQEBAQUAA4GN','purpose'=>'dkim','verified'=>false),
            array('type'=>'TXT','host'=>'_dmarc.'.$dom,'value'=>'v=DMARC1; p=none; rua='.$rua,'purpose'=>'dmarc','verified'=>false)
        );
        $row=$this->records->create('sending_domains',array('domain'=>$dom,'status'=>'pending','records_json'=>json_encode($records,JSON_UNESCAPED_SLASHES),'dmarc_rua'=>$rua,'email_account_id'=>$acctId));
        return (new EmailManager($this->db,$this->config))->getDomain((int)$row['id']);
    }
    public function verifyDomain($id){
        $d=$this->db->one('sending_domains',array('id'=>$id));
        if(!$d){throw new AuthFailure(404,'Sending domain not found');}
        $dom=$d['domain'];
        $spfTxt=ProviderHttp::dnsRecords($dom,'TXT');
        $dkimTxt=array_merge(ProviderHttp::dnsRecords('mail._domainkey.'.$dom,'TXT'),ProviderHttp::dnsRecords('brevo1._domainkey.'.$dom,'CNAME'));
        $dmarcTxt=ProviderHttp::dnsRecords('_dmarc.'.$dom,'TXT');
        $hasSpf=false;foreach($spfTxt as $r){if(stripos((string)($r['txt']??''),'v=spf1')!==false){$hasSpf=true;break;}}
        $hasDkim=false;foreach($dkimTxt as $r){if(stripos((string)($r['txt']??($r['target']??'')),'dkim')!==false||stripos((string)($r['txt']??''),'k=rsa')!==false){$hasDkim=true;break;}}
        $hasDmarc=false;foreach($dmarcTxt as $r){if(stripos((string)($r['txt']??''),'v=DMARC1')!==false){$hasDmarc=true;break;}}
        $recs=is_array($d['records_json'])?$d['records_json']:(json_decode((string)$d['records_json'],true)?:array());
        foreach($recs as &$rc){
            $p=$rc['purpose']??'';
            if($p==='spf'){$rc['verified']=$hasSpf;}
            elseif($p==='dkim'){$rc['verified']=$hasDkim;}
            elseif($p==='dmarc'){$rc['verified']=$hasDmarc;}
        }unset($rc);
        $now=$this->records->now();
        $ok=$hasSpf&&$hasDkim;
        $this->db->transaction(function()use($id,$ok,$recs,$now,$d){
            $this->db->update('sending_domains',array('status'=>$ok?'authenticated':'pending','records_json'=>json_encode($recs,JSON_UNESCAPED_SLASHES),'authenticated_at'=>$ok?($d['authenticated_at']?:$now):null,'last_checked_at'=>$now,'last_error'=>$ok?null:'Pending SPF/DKIM DNS propagation.','updated_at'=>$now),array('id'=>$id));
        });
        return (new EmailManager($this->db,$this->config))->getDomain($id);
    }
    private function defaultAccount($explicitId=null){
        if($explicitId!==null){
            if($explicitId<0){
                $mb=$this->db->one('email_mailboxes',array('id'=>abs($explicitId)));
                if(!$mb||empty($mb['is_active'])||empty($mb['send_replies'])){throw new AuthFailure(400,'Selected mailbox is not active for sending.');}
                return array('kind'=>'mailbox','row'=>$mb);
            }
            $a=$this->db->one('email_accounts',array('id'=>$explicitId));
            if(!$a||empty($a['is_active'])){throw new AuthFailure(400,'Selected email account is not active.');}
            return array('kind'=>'account','row'=>$a);
        }
        $rows=$this->db->rows('SELECT * FROM '.$this->table('email_accounts').' WHERE COALESCE(is_active,0)=1 ORDER BY is_default DESC,id ASC LIMIT 1');
        if($rows){return array('kind'=>'account','row'=>$this->db->decode('email_accounts',$rows[0]));}
        $mbs=$this->db->rows('SELECT * FROM '.$this->table('email_mailboxes').' WHERE COALESCE(is_active,0)=1 AND COALESCE(send_replies,0)=1 ORDER BY id ASC LIMIT 1');
        if($mbs){return array('kind'=>'mailbox','row'=>$this->db->decode('email_mailboxes',$mbs[0]));}
        throw new AuthFailure(503,'No active email sender account or connected mailbox is configured.');
    }
    public function sendEmail(array $opts){
        $to=strtolower(Compat::strip((string)($opts['to']??'')));
        if($to===''||strpos($to,'@')===false){throw new ValidationFailure('Valid recipient email address required','to');}
        $subject=Compat::strip((string)($opts['subject']??''));
        $body=(string)($opts['body']??'');
        $html=isset($opts['html_body'])&&$opts['html_body']!==null?(string)$opts['html_body']:nl2br(htmlspecialchars($body,ENT_QUOTES,'UTF-8'));
        if($subject===''&&$body===''){throw new ValidationFailure('Subject or body is required','body');}
        $contact=$opts['contact']??$this->db->one('contacts',array('email_lower'=>$to));
        $isTest=!empty($opts['is_test']);
        if(!$isTest){
            if($contact&&(!empty($contact['is_email_opted_out'])||!empty($contact['is_email_undeliverable']))){
                throw new AuthFailure(409,'Recipient has opted out or is marked undeliverable.');
            }
            if($this->db->one('email_suppressions',array('email_address'=>$to))){
                throw new AuthFailure(409,'Recipient address is on the email suppression list.');
            }
        }
        $sender=$this->defaultAccount($opts['account_id']??null);
        $headers=array();
        if($contact&&!$isTest){
            try{
                $tok=$this->signUnsubscribe((int)$contact['id'],$to);
                $base=function_exists('rest_url')?rtrim((string)rest_url('sender-suite/v1/email/unsubscribe'),'/'):'/wp-json/sender-suite/v1/email/unsubscribe';
                $uUrl=$base.'?token='.rawurlencode($tok);
                $headers['List-Unsubscribe']='<'.$uUrl.'>';
                $headers['List-Unsubscribe-Post']='List-Unsubscribe=One-Click';
            }catch(\Throwable $e){}
        }
        $providerMsgId=null;$provider='brevo';$fromAddr='';$acctId=null;
        if($sender['kind']==='account'){
            $acct=$sender['row'];$acctId=(int)$acct['id'];$fromAddr=$acct['brevo_from_email']?:$acct['from_email'];
            $key=$this->openAcctSecret($acctId,'api_key',$acct['api_key_encrypted']??'');
            if($key===''){throw new AuthFailure(503,'Email account API key is not configured.');}
            $payload=array('sender'=>array('name'=>$acct['from_name']?:$acct['name'],'email'=>$fromAddr),'to'=>array(array('email'=>$to)),'subject'=>$subject!==''?$subject:'(no subject)','textContent'=>$body!==''?$body:strip_tags($html),'htmlContent'=>$html);
            if(!empty($acct['reply_to'])){$payload['replyTo']=array('email'=>$acct['reply_to']);}
            if($headers){$payload['headers']=$headers;}
            $res=ProviderHttp::request('POST','https://api.brevo.com/v3/smtp/email',array('api-key'=>$key),$payload);
            if(empty($res['ok'])){throw new AuthFailure(502,'Brevo rejected send request (HTTP '.(int)($res['http']??0).').');}
            $providerMsgId=(string)($res['json']['messageId']??('brevo-'.bin2hex(random_bytes(8))));
        }else{
            $mb=$sender['row'];$provider=$mb['provider']??'gmail';$fromAddr=$mb['email_address'];
            $cred=$this->openMbSecret($mb['id'],'credential',$mb['credential_encrypted']??'');
            if($cred===''){throw new AuthFailure(503,'Mailbox credential is not configured.');}
            $rawMime='From: '.$mb['name'].' <'.$fromAddr.">\r\nTo: <".$to.">\r\nSubject: ".($subject!==''?$subject:'(no subject)')."\r\nMIME-Version: 1.0\r\nContent-Type: text/html; charset=UTF-8\r\n\r\n".$html;
            $rawB64=rtrim(strtr(base64_encode($rawMime),'+/','-_'),'=');
            $res=ProviderHttp::request('POST','https://gmail.googleapis.com/gmail/v1/users/me/messages/send',array('Authorization'=>'Bearer '.$cred),array('raw'=>$rawB64));
            if(empty($res['ok'])){throw new AuthFailure(502,'Mailbox provider rejected send request (HTTP '.(int)($res['http']??0).').');}
            $providerMsgId=(string)($res['json']['id']??('mb-'.bin2hex(random_bytes(8))));
        }
        $msgRow=$this->db->transaction(function()use($contact,$opts,$to,$fromAddr,$subject,$body,$html,$acctId,$provider,$providerMsgId,$sender){
            $now=$this->records->now();
            $m=null;
            if($contact){
                $convId=$opts['conversation_id']??null;
                if($convId===null){
                    $conv=$this->db->one('conversations',array('contact_id'=>$contact['id'],'channel'=>'email'));
                    if(!$conv){$conv=$this->records->create('conversations',array('contact_id'=>$contact['id'],'channel'=>'email','last_message_at'=>$now,'last_message_preview'=>substr($subject!==''?$subject:$body,0,200)));}
                    else{$this->db->update('conversations',array('last_message_at'=>$now,'last_message_preview'=>substr($subject!==''?$subject:$body,0,200)),array('id'=>$conv['id']));}
                    $convId=(int)$conv['id'];
                }
                $m=$this->records->create('messages',array('conversation_id'=>$convId,'contact_id'=>(int)$contact['id'],'campaign_id'=>$opts['campaign_id']??null,'direction'=>'outgoing','channel'=>'email','subject'=>$subject,'body'=>$body!==''?$body:strip_tags($html),'html_body'=>$html,'from_address'=>$fromAddr,'to_address'=>$to,'email_account_id'=>$acctId,'status'=>'sent','provider'=>$provider,'provider_message_id'=>$providerMsgId,'idempotency_key'=>'em_'.bin2hex(random_bytes(12)),'sent_at'=>$now));
            }
            if($acctId!==null){
                $this->db->execute('UPDATE '.$this->table('email_accounts').' SET sent_today=COALESCE(sent_today,0)+1,total_sent=COALESCE(total_sent,0)+1,updated_at=%s WHERE id=%d',array($now,$acctId));
            }else{
                $this->db->execute('UPDATE '.$this->table('email_mailboxes').' SET total_sent=COALESCE(total_sent,0)+1,updated_at=%s WHERE id=%d',array($now,(int)$sender['row']['id']));
            }
            $this->records->create('email_events',array('event_type'=>'sent','account_id'=>$acctId,'message_id'=>$m?(int)$m['id']:null,'contact_id'=>$contact?(int)$contact['id']:null,'email_address'=>$to,'subject'=>$subject,'provider_event_id'=>$providerMsgId.':sent','occurred_at'=>$now));
            return $m;
        });
        return array('ok'=>true,'message_id'=>$msgRow?(int)$msgRow['id']:null,'provider_message_id'=>$providerMsgId,'status'=>'sent','to'=>$to);
    }
    public function createMailbox($body,&$status){
        if(!is_object($body)){throw new ValidationFailure('Supply a JSON object');}
        $email=strtolower(Compat::strip(DataRoutes::text(array('email_address'=>$body->email_address??''),'email_address','',3,255)));
        if(strpos($email,'@')===false){throw new ValidationFailure('Valid email_address required','email_address');}
        if($this->db->one('email_mailboxes',array('email_address'=>$email))){throw new AuthFailure(409,'Mailbox already connected');}
        $name=Compat::strip(DataRoutes::text(array('name'=>$body->name??$email),'name',$email,1,120));
        $provider=strtolower(Compat::strip(DataRoutes::text(array('provider'=>$body->provider??'imap'),'provider','imap',2,40)));
        $cred=isset($body->credential)&&$body->credential!==null?(string)$body->credential:(isset($body->password)&&$body->password!==null?(string)$body->password:'');
        $row=$this->db->transaction(function()use($name,$provider,$email,$cred,$body){
            $r=$this->records->create('email_mailboxes',array('name'=>$name,'provider'=>$provider,'email_address'=>$email,'folders'=>'INBOX','import_all'=>!empty($body->import_all),'rescue_from_spam'=>!empty($body->rescue_from_spam),'send_replies'=>isset($body->send_replies)?(bool)$body->send_replies:true,'is_active'=>true,'last_sync_status'=>'idle'));
            if($cred!==''&&$this->config!==null){
                $enc=$this->config->sealRuntimeSecret($this->db->schema()->table('email_mailboxes').'/'.$r['id'].'/credential',$cred);
                $this->db->update('email_mailboxes',array('credential_encrypted'=>$enc),array('id'=>$r['id']));
                $r=$this->db->one('email_mailboxes',array('id'=>$r['id']));
            }
            return $r;
        });
        $status=201;
        return (new EmailManager($this->db,$this->config))->readMailbox((int)$row['id']);
    }
    public function googleOAuthStart(){
        $cidRow=$this->db->one('system_settings',array('key'=>'google_oauth.client_id'));
        $cid=$cidRow&&$cidRow['value']!==null?(string)$cidRow['value']:'';
        if($cid===''){throw new AuthFailure(400,'Configure Google OAuth Client ID and Secret in Settings first.');}
        $cb=function_exists('rest_url')?(string)rest_url('sender-suite/v1/mailbox/google/callback'):'https://localhost/wp-json/sender-suite/v1/mailbox/google/callback';
        $state=rtrim(strtr(base64_encode(hash_hmac('sha256',$cid,$this->hmacKey(),true)),'+/','-_'),'=');
        $url='https://accounts.google.com/o/oauth2/v2/auth?'.http_build_query(array('client_id'=>$cid,'redirect_uri'=>$cb,'response_type'=>'code','access_type'=>'offline','prompt'=>'consent','scope'=>'https://www.googleapis.com/auth/gmail.modify https://www.googleapis.com/auth/gmail.send email','state'=>$state));
        return array('auth_url'=>$url,'redirect_uri'=>$cb,'state'=>$state);
    }
    public function googleOAuthCallback(array $q){
        $code=Compat::strip((string)($q['code']??''));
        $state=Compat::strip((string)($q['state']??''));
        if($code===''||$state===''){throw new ValidationFailure('Missing OAuth code or state','code');}
        $cidRow=$this->db->one('system_settings',array('key'=>'google_oauth.client_id'));
        $secRow=$this->db->one('system_settings',array('key'=>'google_oauth.client_secret'));
        $cid=$cidRow?(string)$cidRow['value']:'';
        $csec=($secRow&&$secRow['value']&&$this->config)?$this->config->openRuntimeSecret($this->db->schema()->table('system_settings').'/'.$secRow['id'].'/google_oauth.client_secret',$secRow['value']):'';
        if($cid===''||$csec===''){throw new AuthFailure(400,'Google OAuth credentials are not configured.');}
        $expect=rtrim(strtr(base64_encode(hash_hmac('sha256',$cid,$this->hmacKey(),true)),'+/','-_'),'=');
        if(!hash_equals($expect,$state)){throw new AuthFailure(400,'Invalid Google OAuth state.');}
        $cb=function_exists('rest_url')?(string)rest_url('sender-suite/v1/mailbox/google/callback'):'https://localhost/wp-json/sender-suite/v1/mailbox/google/callback';
        $tokRes=ProviderHttp::request('POST','https://oauth2.googleapis.com/token',array('Content-Type'=>'application/x-www-form-urlencoded'),http_build_query(array('code'=>$code,'client_id'=>$cid,'client_secret'=>$csec,'redirect_uri'=>$cb,'grant_type'=>'authorization_code')));
        if(empty($tokRes['ok'])||empty($tokRes['json']['access_token'])){throw new AuthFailure(502,'Google token exchange failed.');}
        $access=(string)$tokRes['json']['access_token'];
        $prof=ProviderHttp::request('GET','https://gmail.googleapis.com/gmail/v1/users/me/profile',array('Authorization'=>'Bearer '.$access));
        $email=strtolower((string)($prof['json']['emailAddress']??''));
        if($email===''){throw new AuthFailure(502,'Could not fetch Gmail profile address.');}
        $mb=$this->db->transaction(function()use($email,$access){
            $existing=$this->db->one('email_mailboxes',array('email_address'=>$email));
            if(!$existing){$existing=$this->records->create('email_mailboxes',array('name'=>$email,'provider'=>'gmail','email_address'=>$email,'folders'=>'INBOX','send_replies'=>true,'is_active'=>true,'last_sync_status'=>'ok'));}
            $enc=$this->config->sealRuntimeSecret($this->db->schema()->table('email_mailboxes').'/'.$existing['id'].'/credential',$access);
            $this->db->update('email_mailboxes',array('credential_encrypted'=>$enc,'provider'=>'gmail','is_active'=>true,'last_sync_status'=>'ok','updated_at'=>$this->records->now()),array('id'=>$existing['id']));
            return $this->db->one('email_mailboxes',array('id'=>$existing['id']));
        });
        return (new EmailManager($this->db,$this->config))->readMailbox((int)$mb['id']);
    }
    public function syncMailbox($id,$checkOnly=false){
        $mb=$this->db->one('email_mailboxes',array('id'=>$id));
        if(!$mb){throw new AuthFailure(404,'Mailbox not found');}
        $cred=$this->openMbSecret($mb['id'],'credential',$mb['credential_encrypted']??'');
        if($cred===''){throw new AuthFailure(503,'Mailbox credential is missing.');}
        $prof=ProviderHttp::request('GET','https://gmail.googleapis.com/gmail/v1/users/me/profile',array('Authorization'=>'Bearer '.$cred));
        $now=$this->records->now();
        if(empty($prof['ok'])){
            $this->db->transaction(function()use($id,$now,$prof){$this->db->update('email_mailboxes',array('last_sync_at'=>$now,'last_sync_status'=>'error','last_error'=>'HTTP '.(int)($prof['http']??0),'updated_at'=>$now),array('id'=>$id));});
            return array('ok'=>false,'status'=>'error','http'=>(int)($prof['http']??0));
        }
        if($checkOnly){
            $this->db->transaction(function()use($id,$now){$this->db->update('email_mailboxes',array('last_sync_at'=>$now,'last_sync_status'=>'ok','last_error'=>null,'updated_at'=>$now),array('id'=>$id));});
            return array('ok'=>true,'status'=>'ok','email_address'=>$prof['json']['emailAddress']??$mb['email_address']);
        }
        $list=ProviderHttp::request('GET','https://gmail.googleapis.com/gmail/v1/users/me/messages?maxResults=25',array('Authorization'=>'Bearer '.$cred));
        $imported=0;
        if(!empty($list['ok'])&&!empty($list['json']['messages'])&&is_array($list['json']['messages'])){
            foreach($list['json']['messages'] as $stub){
                $pmid=(string)($stub['id']??'');
                if($pmid===''||$this->db->one('mailbox_messages',array('mailbox_id'=>$id,'provider_message_id'=>$pmid))){continue;}
                $det=ProviderHttp::request('GET','https://gmail.googleapis.com/gmail/v1/users/me/messages/'.rawurlencode($pmid).'?format=metadata',array('Authorization'=>'Bearer '.$cred));
                if(empty($det['ok'])||!is_array($det['json'])){continue;}
                $hdrs=array();foreach(($det['json']['payload']['headers']??array()) as $h){if(isset($h['name'],$h['value'])){$hdrs[strtolower($h['name'])]=(string)$h['value'];}}
                $fromRaw=(string)($hdrs['from']??'unknown@example.com');
                $fromEmail=preg_match('/<([^>]+)>/',$fromRaw,$fm)?strtolower(trim($fm[1])):strtolower(trim($fromRaw));
                $subj=substr((string)($hdrs['subject']??''),0,255);
                $snip=substr((string)($det['json']['snippet']??''),0,255);
                $this->db->transaction(function()use($id,$pmid,$det,$fromEmail,$subj,$snip,$now,&$imported){
                    $contact=$this->db->one('contacts',array('email_lower'=>$fromEmail));
                    $conv=$contact?$this->db->one('conversations',array('contact_id'=>$contact['id'],'channel'=>'email')):null;
                    if($contact&&!$conv){$conv=$this->records->create('conversations',array('contact_id'=>$contact['id'],'channel'=>'email','last_message_at'=>$now,'last_message_preview'=>$snip,'unread_count'=>1));}
                    $this->records->create('mailbox_messages',array('mailbox_id'=>$id,'provider_message_id'=>$pmid,'thread_id'=>(string)($det['json']['threadId']??$pmid),'folder'=>'INBOX','subject'=>$subj,'from_address'=>$fromEmail,'date'=>$now,'first_seen_at'=>$now,'snippet'=>$snip,'body_text'=>$snip,'is_read'=>false,'is_starred'=>false,'conversation_id'=>$conv?(int)$conv['id']:null,'contact_id'=>$contact?(int)$contact['id']:null));
                    if($conv&&$contact){$this->records->create('messages',array('conversation_id'=>(int)$conv['id'],'contact_id'=>(int)$contact['id'],'direction'=>'incoming','channel'=>'email','subject'=>$subj,'body'=>$snip!==''?$snip:'(no text)','from_address'=>$fromEmail,'status'=>'received','provider'=>'gmail','provider_message_id'=>$pmid,'idempotency_key'=>'gmb_'.substr(hash('sha256',$id.':'.$pmid),0,28)));}
                    $imported++;
                });
            }
        }
        $this->db->transaction(function()use($id,$now,$imported){$this->db->execute('UPDATE '.$this->table('email_mailboxes').' SET last_sync_at=%s,last_sync_status=%s,last_error=NULL,total_synced=COALESCE(total_synced,0)+%d,updated_at=%s WHERE id=%d',array($now,'ok',$imported,$now,$id));});
        return array('ok'=>true,'synced'=>$imported,'mailbox_id'=>(int)$id);
    }
    public function handleRoute($path,$method,$request,array $user,&$status){
        $adminReq=function()use($user){if(($user['role']??'')!=='admin'){throw new AuthFailure(403,'Application administrator role required.');}};
        $q=$request->get_query_params();
        if($path==='email/domains'&&$method==='POST'){$adminReq();$status=201;return $this->db->transaction(function()use($request){return $this->createDomain(DataRoutes::body($request));});}
        if(preg_match('~^email/domains/(-?[0-9]+)/verify$~D',$path,$m)&&$method==='POST'){$adminReq();return $this->verifyDomain(DataRoutes::integer($m[1],'domain_id'));}
        if($path==='email/senders-preview'&&$method==='POST'){$adminReq();$b=DataRoutes::body($request);return $this->brevoSenders($b->api_key??'');}
        if(preg_match('~^email/accounts/(-?[0-9]+)/(test|test-send|senders)$~D',$path,$m)){
            $aid=DataRoutes::integer($m[1],'account_id');$act=$m[2];$adminReq();
            if($act==='senders'&&$method==='GET'){return $this->accountSenders($aid);}
            if($act==='test'&&$method==='POST'){return $this->testAccount($aid);}
            if($act==='test-send'&&$method==='POST'){$b=DataRoutes::body($request);return $this->sendEmail(array('account_id'=>$aid,'to'=>$b->to??$b->email??'','subject'=>$b->subject??'Sender Suite Test Email','body'=>$b->body??'Test email from WordPress Sender Suite.','is_test'=>true));}
        }
        if($path==='email/test-send'&&$method==='POST'){$adminReq();$b=DataRoutes::body($request);return $this->sendEmail(array('account_id'=>isset($b->account_id)?DataRoutes::integer($b->account_id,'account_id'):null,'to'=>$b->to??$b->email??'','subject'=>$b->subject??'Sender Suite Test Email','body'=>$b->body??'Test email from WordPress Sender Suite.','is_test'=>true));}
        if($path==='email/send'&&$method==='POST'){
            $b=DataRoutes::body($request);
            $cid=isset($b->contact_id)&&$b->contact_id!==null?DataRoutes::integer($b->contact_id,'contact_id'):null;
            $contact=$cid?$this->db->one('contacts',array('id'=>$cid)):null;
            $to=isset($b->to)?(string)$b->to:($contact?(string)$contact['email']:'');
            return $this->sendEmail(array('account_id'=>isset($b->account_id)&&$b->account_id!==null?DataRoutes::integer($b->account_id,'account_id'):null,'contact'=>$contact,'to'=>$to,'subject'=>$b->subject??'','body'=>$b->body??'','html_body'=>$b->html_body??null));
        }
        if(preg_match('~^email/inbox/conversations/(-?[0-9]+)/reply$~D',$path,$m)&&$method==='POST'){
            $convId=DataRoutes::integer($m[1],'conversation_id');
            $conv=$this->db->one('conversations',array('id'=>$convId));
            if(!$conv){throw new AuthFailure(404,'Conversation not found');}
            $contact=$conv['contact_id']?$this->db->one('contacts',array('id'=>$conv['contact_id'])):null;
            $b=DataRoutes::body($request);
            return $this->sendEmail(array('conversation_id'=>$convId,'contact'=>$contact,'account_id'=>isset($b->account_id)&&$b->account_id!==null?DataRoutes::integer($b->account_id,'account_id'):null,'to'=>$contact?(string)$contact['email']:($b->to??''),'subject'=>$b->subject??'Re: Conversation','body'=>$b->body??'','html_body'=>$b->html_body??null));
        }
        if(preg_match('~^email/messages/(-?[0-9]+)/retry$~D',$path,$m)&&$method==='POST'){
            $mid=DataRoutes::integer($m[1],'message_id');
            $msg=$this->db->one('messages',array('id'=>$mid));
            if(!$msg||($msg['channel']??'')!=='email'){throw new AuthFailure(404,'Email message not found');}
            $contact=$msg['contact_id']?$this->db->one('contacts',array('id'=>$msg['contact_id'])):null;
            return $this->sendEmail(array('conversation_id'=>$msg['conversation_id']?(int)$msg['conversation_id']:null,'contact'=>$contact,'account_id'=>$msg['email_account_id']?(int)$msg['email_account_id']:null,'to'=>(string)$msg['to_address'],'subject'=>(string)$msg['subject'],'body'=>(string)$msg['body'],'html_body'=>$msg['html_body']??null));
        }
        if($path==='mailbox'&&$method==='POST'){$adminReq();return $this->createMailbox(DataRoutes::body($request),$status);}
        if($path==='mailbox/google/start'&&$method==='GET'){$adminReq();return $this->googleOAuthStart();}
        if($path==='mailbox/google/callback'&&$method==='GET'){$adminReq();return $this->googleOAuthCallback($q);}
        if(preg_match('~^mailbox/(-?[0-9]+)/(check|sync|filters|test-send)$~D',$path,$m)&&$method==='POST'){
            $mbid=DataRoutes::integer($m[1],'mailbox_id');$act=$m[2];$adminReq();
            if($act==='check'){return $this->syncMailbox($mbid,true);}
            if($act==='sync'){return $this->syncMailbox($mbid,false);}
            if($act==='filters'){
                $mb=$this->db->one('email_mailboxes',array('id'=>$mbid));if(!$mb){throw new AuthFailure(404,'Mailbox not found');}
                $this->db->transaction(function()use($mbid){$this->db->update('email_mailboxes',array('never_spam_filter'=>true,'filter_ids'=>json_encode(array('flt_native_1')),'updated_at'=>$this->records->now()),array('id'=>$mbid));});
                return array('ok'=>true,'filter_ids'=>array('flt_native_1'));
            }
            if($act==='test-send'){$b=DataRoutes::body($request);return $this->sendEmail(array('account_id'=>-$mbid,'to'=>$b->to??$b->email??'','subject'=>$b->subject??'Mailbox Test','body'=>$b->body??'Mailbox test email.','is_test'=>true));}
        }
        if(preg_match('~^mail/refresh(?:/(-?[0-9]+))?$~D',$path,$m)&&$method==='POST'){
            if(!empty($m[1])){return $this->syncMailbox(DataRoutes::integer($m[1],'mailbox_id'),false);}
            $synced=0;foreach($this->db->rows('SELECT id FROM '.$this->table('email_mailboxes').' WHERE COALESCE(is_active,0)=1 LIMIT 20') as $r){$res=$this->syncMailbox((int)$r['id'],false);$synced+=(int)($res['synced']??0);}
            return array('ok'=>true,'synced'=>$synced);
        }
        if(preg_match('~^mail/messages/(-?[0-9]+)/([A-Za-z0-9._-]+)/(move|delete|reply)$~D',$path,$m)&&$method==='POST'){
            $mbid=DataRoutes::integer($m[1],'mailbox_id');$pmid=$m[2];$act=$m[3];
            $row=$this->db->one('mailbox_messages',array('mailbox_id'=>$mbid,'provider_message_id'=>$pmid));
            if(!$row){throw new AuthFailure(404,'That message is not in the mirror yet.');}
            if($act==='delete'){
                $this->db->transaction(function()use($row){$this->db->update('mailbox_messages',array('folder'=>'TRASH','updated_at'=>$this->records->now()),array('id'=>$row['id']));});
                return array('ok'=>true,'deleted'=>true,'folder'=>'TRASH');
            }
            if($act==='move'){
                $b=DataRoutes::body($request);$folder=strtoupper(Compat::strip(DataRoutes::text(array('folder'=>$b->folder??'INBOX'),'folder','INBOX',1,30)));
                if(!in_array($folder,EmailManager::FOLDERS,true)){throw new ValidationFailure('Unsupported folder','folder');}
                $this->db->transaction(function()use($row,$folder){$this->db->update('mailbox_messages',array('folder'=>$folder,'updated_at'=>$this->records->now()),array('id'=>$row['id']));});
                return array('ok'=>true,'folder'=>$folder);
            }
            if($act==='reply'){
                $b=DataRoutes::body($request);
                return $this->sendEmail(array('account_id'=>-$mbid,'conversation_id'=>$row['conversation_id']?(int)$row['conversation_id']:null,'to'=>(string)$row['from_address'],'subject'=>'Re: '.(string)$row['subject'],'body'=>$b->body??'','html_body'=>$b->html_body??null));
            }
        }
        throw new AuthFailure(404,'Not found');
    }
}
