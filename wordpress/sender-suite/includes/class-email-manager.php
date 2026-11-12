<?php
namespace SenderSuite;

/** Stored Email Manager, Mailbox and Mail mirror routes; live Brevo/SMTP/DNS/IMAP/Gmail/OAuth transports remain gated. */
final class EmailManager {
    const FOLDERS=array('INBOX','SENT','SPAM','DRAFTS','TRASH','OTHER');
    const FREEMAIL=array('gmail.com','googlemail.com','yahoo.com','outlook.com','hotmail.com','icloud.com','aol.com','proton.me','protonmail.com');
    private $db;private $config;private $records;
    public function __construct(Database $db,Config $config=null,callable $clock=null){$this->db=$db;$this->config=$config;$this->records=new Records($db,$clock);}
    private function table($n){return Schema::identifier($this->db->schema()->table($n));}
    public static function register(){
        register_rest_route('sender-suite/v1','/email/unsubscribe',array('methods'=>array('GET','POST'),'permission_callback'=>'__return_true','callback'=>array(self::class,'publicUnsubscribe')));
    }
    public static function publicUnsubscribe($request=null){
        if($request===null){$id=bin2hex(random_bytes(16));return AuthRoutes::response(AuthRoutes::error(400,'Missing request.',$id),400,$id);}
        return EmailTransport::dispatchPublicUnsubscribe($request);
    }
    public static function definitions(){
        return array(
            '/email/accounts'=>array('GET','POST'),
            '/email/accounts/(?P<account_id>-?[0-9]+)'=>array('PATCH','DELETE'),
            '/email/accounts/(?P<account_id>-?[0-9]+)/default'=>array('POST'),
            '/email/accounts/(?P<account_id>-?[0-9]+)/webhook/reveal'=>array('POST'),
            '/email/accounts/(?P<account_id>-?[0-9]+)/webhook/rotate'=>array('POST'),
            '/email/accounts/(?P<account_id>-?[0-9]+)/deliverability'=>array('GET'),
            '/email/accounts/(?P<account_id>-?[0-9]+)/(?P<live>test|test-send|senders)'=>array('GET','POST'),
            '/email/senders'=>array('GET'),
            '/email/health'=>array('GET'),
            '/email/domains'=>array('GET','POST'),
            '/email/domains/(?P<domain_id>-?[0-9]+)'=>array('GET','DELETE'),
            '/email/domains/(?P<domain_id>-?[0-9]+)/verify'=>array('POST'),
            '/email/overview'=>array('GET'),
            '/email/stats'=>array('GET'),
            '/email/reference'=>array('GET'),
            '/email/preview'=>array('POST'),
            '/email/send'=>array('POST'),
            '/email/senders-preview'=>array('POST'),
            '/email/test-send'=>array('POST'),
            '/email/history'=>array('GET'),
            '/email/messages/(?P<message_id>-?[0-9]+)/retry'=>array('POST'),
            '/email/messages/(?P<message_id>-?[0-9]+)/events'=>array('GET'),
            '/email/contacts/(?P<contact_id>-?[0-9]+)/engagement'=>array('GET'),
            '/email/inbox/conversations'=>array('GET'),
            '/email/inbox/conversations/(?P<conversation_id>-?[0-9]+)'=>array('GET'),
            '/email/inbox/conversations/(?P<conversation_id>-?[0-9]+)/reply'=>array('POST'),
            '/email/inbox/conversations/(?P<conversation_id>-?[0-9]+)/read'=>array('POST'),
            '/email/inbox/conversations/(?P<conversation_id>-?[0-9]+)/status'=>array('POST'),
            '/email/inbox/unread-count'=>array('GET'),
            '/email/suppression'=>array('GET','POST'),
            '/email/suppression/(?P<entry_id>-?[0-9]+)'=>array('DELETE'),
            '/email/events'=>array('GET'),
            '/mailbox/?'=>array('GET','POST'),
            '/mailbox/report'=>array('GET'),
            '/mailbox/google/(?P<step>start|callback)'=>array('GET'),
            '/mailbox/(?P<mailbox_id>-?[0-9]+)'=>array('GET','PATCH','DELETE'),
            '/mailbox/(?P<mailbox_id>-?[0-9]+)/(?P<live>check|sync|filters|test-send)'=>array('POST'),
            '/mail/overview'=>array('GET'),
            '/mail/messages'=>array('GET'),
            '/mail/messages/(?P<mailbox_id>-?[0-9]+)/(?P<provider_message_id>[A-Za-z0-9._-]+)'=>array('GET'),
            '/mail/messages/(?P<mailbox_id>-?[0-9]+)/(?P<provider_message_id>[A-Za-z0-9._-]+)/flags'=>array('POST'),
            '/mail/messages/(?P<mailbox_id>-?[0-9]+)/(?P<provider_message_id>[A-Za-z0-9._-]+)/(?P<live>move|delete|reply)'=>array('POST'),
            '/mail/refresh'=>array('POST'),
            '/mail/refresh/(?P<mailbox_id>-?[0-9]+)'=>array('POST')
        );
    }
    private static function normEmail($raw){
        if(!is_string($raw)){return null;}
        $e=strtolower(Compat::strip(trim($raw,'<> ')));
        if($e===''||substr_count($e,'@')!==1||preg_match('/\s/',$e)){return null;}
        list($loc,$dom)=explode('@',$e,2);
        return ($loc!==''&&strpos($dom,'.')!==false&&substr($dom,-1)!=='.')?$e:null;
    }
    private static function mask($s){
        if(!is_string($s)||$s===''){return '';}
        return strlen($s)<=8?str_repeat('*',strlen($s)):substr($s,0,4).'***'.substr($s,-4);
    }
    private function openAcctSecret($id,$field,$cipher){
        if(!is_string($cipher)||$cipher===''||$this->config===null){return '';}
        try{return (string)$this->config->openRuntimeSecret($this->db->schema()->table('email_accounts').'/'.$id.'/'.$field,$cipher);}catch(\Throwable $e){return '';}
    }
    private static function replyAddrs(array $a){
        $list=array();
        if(!empty($a['reply_to_addresses'])){
            $dec=is_array($a['reply_to_addresses'])?$a['reply_to_addresses']:json_decode((string)$a['reply_to_addresses'],true);
            if(is_array($dec)){foreach($dec as $it){$e=self::normEmail((string)$it);if($e!==null&&!in_array($e,$list,true)){$list[]=$e;}}}
        }
        if(!$list&&!empty($a['reply_to'])){$e=self::normEmail((string)$a['reply_to']);if($e!==null){$list[]=$e;}}
        return $list;
    }
    private function serializeAccount(array $a){
        $key=$this->openAcctSecret($a['id'],'api_key',$a['api_key_encrypted']??'');
        $tok=$this->openAcctSecret($a['id'],'webhook_token',$a['webhook_token']??'');
        $raddrs=self::replyAddrs($a);
        return array(
            'id'=>(int)$a['id'],'name'=>$a['name'],'provider'=>$a['provider']??'brevo',
            'from_name'=>$a['from_name'],'from_email'=>$a['from_email'],'sends_as'=>$a['brevo_from_email']?:null,
            'reply_to'=>$raddrs?$raddrs[0]:null,'reply_to_addresses'=>$raddrs,
            'is_active'=>(bool)$a['is_active'],'is_default'=>(bool)$a['is_default'],
            'has_api_key'=>!empty($a['api_key_encrypted']),'api_key_masked'=>self::mask($key),
            'daily_limit'=>null,'daily_limit_unlimited'=>true,'effective_daily_limit'=>null,'daily_limit_source'=>'none',
            'sent_today'=>(int)($a['sent_today']??0),'total_sent'=>(int)($a['total_sent']??0),
            'track_opens'=>(bool)$a['track_opens'],'track_clicks'=>(bool)$a['track_clicks'],
            'connection_status'=>$a['connection_status']??'unknown','last_tested_at'=>Records::iso($a['last_tested_at']??null),'last_error'=>$a['last_error'],
            'webhook_url'=>null,'webhook_path'=>'/sender-suite/v1/webhooks/brevo','webhook_token_masked'=>self::mask($tok),
            'last_webhook_event_at'=>Records::iso($a['last_webhook_event_at']??null),'created_at'=>Records::iso($a['created_at']??null)
        );
    }
    private function serializeMailbox(array $m){
        $folders=array('INBOX');
        if(!empty($m['folders'])){$parts=array();foreach(explode(',',(string)$m['folders']) as $p){$f=strtoupper(Compat::strip($p));if($f!==''){$parts[]=$f;}}if($parts){$folders=$parts;}}
        $fIds=array();if(!empty($m['filter_ids'])){$dec=json_decode((string)$m['filter_ids'],true);if(is_array($dec)){$fIds=$dec;}}
        return array(
            'id'=>(int)$m['id'],'name'=>$m['name'],'provider'=>$m['provider'],'email_address'=>$m['email_address'],
            'folders'=>$folders,'import_all'=>(bool)$m['import_all'],'rescue_from_spam'=>(bool)$m['rescue_from_spam'],
            'never_spam_filter'=>(bool)$m['never_spam_filter'],'send_replies'=>(bool)$m['send_replies'],
            'poll_interval'=>(int)($m['poll_interval']??60),'is_active'=>(bool)$m['is_active'],
            'last_sync_at'=>Records::iso($m['last_sync_at']??null),'last_sync_status'=>$m['last_sync_status']??'never',
            'last_error'=>$m['last_error'],'total_synced'=>(int)($m['total_synced']??0),
            'total_replies'=>(int)($m['total_replies']??0),'total_rescued'=>(int)($m['total_rescued']??0),
            'total_sent'=>(int)($m['total_sent']??0),'filters_installed'=>count($fIds),
            'created_at'=>Records::iso($m['created_at']??null)
        );
    }
    public function listAccounts(){
        $items=array();
        foreach($this->db->rows('SELECT * FROM '.$this->table('email_accounts').' ORDER BY is_default DESC,id ASC LIMIT 200') as $r){
            $items[]=$this->serializeAccount($this->db->decode('email_accounts',$r));
        }
        return array('total'=>count($items),'items'=>$items);
    }
    public function listSenders(){
        $items=array();
        foreach($this->db->rows('SELECT * FROM '.$this->table('email_accounts').' ORDER BY is_default DESC,id ASC LIMIT 200') as $r){
            $a=$this->db->decode('email_accounts',$r);
            $items[]=array('id'=>(int)$a['id'],'name'=>$a['name'],'provider'=>'brevo','sender_type'=>'brevo','from_name'=>$a['from_name'],'from_email'=>$a['from_email'],'is_active'=>(bool)$a['is_active'],'is_default'=>(bool)$a['is_default'],'connection_status'=>$a['connection_status']??'unknown');
        }
        foreach($this->db->rows('SELECT * FROM '.$this->table('email_mailboxes').' ORDER BY id ASC LIMIT 200') as $mr){
            $m=$this->db->decode('email_mailboxes',$mr);
            $items[]=array('id'=>-(int)$m['id'],'name'=>$m['name'],'provider'=>$m['provider'],'sender_type'=>'mailbox','from_name'=>$m['name'],'from_email'=>$m['email_address'],'is_active'=>(bool)$m['is_active'],'is_default'=>false,'connection_status'=>$m['last_sync_status']??'never');
        }
        return array('total'=>count($items),'items'=>$items);
    }
    public function health(){
        $items=array();
        foreach($this->db->rows('SELECT * FROM '.$this->table('email_accounts').' ORDER BY is_default DESC,id ASC LIMIT 200') as $r){
            $a=$this->db->decode('email_accounts',$r);
            $usable=!empty($a['api_key_encrypted'])&&self::normEmail((string)$a['from_email'])!==null;
            $st=!$a['is_active']?'disabled':(($a['connection_status']==='error'||!$usable)?'error':($a['connection_status']==='connected'?'connected':'untested'));
            $act=$st==='disabled'?'Enable this sender in Email Manager.':($st==='error'?'Fix the sender configuration, then test its Brevo connection.':($st==='untested'?'Test the Brevo connection before launching an email campaign.':'Sender is connected.'));
            $items[]=array('id'=>(int)$a['id'],'name'=>$a['name'],'from_email'=>$a['from_email'],'is_active'=>(bool)$a['is_active'],'is_default'=>(bool)$a['is_default'],'status'=>$st,'connection_status'=>$a['connection_status']??'unknown','last_tested_at'=>Records::iso($a['last_tested_at']??null),'last_error'=>$a['last_error'],'issue'=>$usable?null:'Missing API key or valid From address','action'=>$act);
        }
        $active=array_values(array_filter($items,function($i){return $i['is_active'];}));
        $ok=count($active)>0&&count(array_filter($active,function($i){return $i['status']==='connected';}))===count($active);
        return array('ok'=>$ok,'accounts'=>$items,'live_test_route'=>'/sender-suite/v1/email/accounts/{account_id}/test');
    }
    public function createAccount($body){
        if(!is_object($body)){throw new ValidationFailure('Supply a JSON object');}
        foreach($body as $k=>$v){if(!in_array($k,array('name','from_name','from_email','api_key','reply_to','is_active','is_default','track_opens','track_clicks'),true)){throw new ValidationFailure('Unknown account field',$k);}}
        $this->db->requireTransaction();
        if($this->config===null){throw new AuthFailure(503,'Encrypted runtime vault is required to store Brevo API keys.');}
        $key=Compat::strip(DataRoutes::text(array('api_key'=>$body->api_key??''),'api_key','',null,512));
        if($key===''){throw new ValidationFailure('Paste the Brevo API key','api_key');}
        $fromEmail=self::normEmail(DataRoutes::text(array('from_email'=>$body->from_email??''),'from_email','',null,255));
        if($fromEmail===null){throw new ValidationFailure('Enter a valid From email address','from_email');}
        $name=Compat::strip(DataRoutes::text(array('name'=>$body->name??$fromEmail),'name','',1,150));
        $fromName=Compat::strip(DataRoutes::text(array('from_name'=>$body->from_name??$name),'from_name','',1,150));
        $rlist=array();
        if(isset($body->reply_to)&&$body->reply_to!==null){
            $rawR=is_array($body->reply_to)?$body->reply_to:explode(',',(string)$body->reply_to);
            foreach($rawR as $ri){$re=self::normEmail((string)$ri);if($re!==null&&!in_array($re,$rlist,true)){$rlist[]=$re;}}
        }
        $existingCnt=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('email_accounts'))[0]['n'];
        $isDef=isset($body->is_default)?DataRoutes::boolean($body->is_default,'is_default'):($existingCnt===0);
        if($isDef){$this->db->execute('UPDATE '.$this->table('email_accounts').' SET is_default=0');}
        $created=$this->records->create('email_accounts',array(
            'name'=>$name,'provider'=>'brevo','from_name'=>$fromName,'from_email'=>$fromEmail,
            'reply_to'=>$rlist?$rlist[0]:null,'reply_to_addresses'=>json_encode($rlist,JSON_THROW_ON_ERROR),
            'api_key_encrypted'=>'pending','is_active'=>isset($body->is_active)?DataRoutes::boolean($body->is_active,'is_active'):true,
            'is_default'=>$isDef,'daily_limit'=>300,'daily_limit_unlimited'=>true,'sent_today'=>0,'total_sent'=>0,
            'track_opens'=>isset($body->track_opens)?DataRoutes::boolean($body->track_opens,'track_opens'):true,
            'track_clicks'=>isset($body->track_clicks)?DataRoutes::boolean($body->track_clicks,'track_clicks'):true,
            'connection_status'=>'unknown'
        ));
        $id=(int)$created['id'];
        $encKey=$this->config->sealRuntimeSecret($this->db->schema()->table('email_accounts').'/'.$id.'/api_key',$key);
        $encTok=$this->config->sealRuntimeSecret($this->db->schema()->table('email_accounts').'/'.$id.'/webhook_token',bin2hex(random_bytes(16)));
        $this->db->update('email_accounts',array('api_key_encrypted'=>$encKey,'webhook_token'=>$encTok,'updated_at'=>$this->records->now()),array('id'=>$id));
        return $this->serializeAccount($this->db->one('email_accounts',array('id'=>$id)));
    }
    public function updateAccount($id,$body){
        if(!is_object($body)){throw new ValidationFailure('Supply a JSON object');}
        foreach($body as $k=>$v){if(!in_array($k,array('name','from_name','from_email','api_key','reply_to','is_active','is_default','track_opens','track_clicks'),true)){throw new ValidationFailure('Unknown account field',$k);}}
        $this->db->requireTransaction();
        $acct=$this->db->one('email_accounts',array('id'=>$id));
        if(!$acct){throw new AuthFailure(404,'Email account not found');}
        $patch=array('updated_at'=>$this->records->now());
        if(isset($body->name)&&$body->name!==null){$patch['name']=Compat::strip(DataRoutes::text(array('name'=>$body->name),'name','',1,150));}
        if(isset($body->from_name)&&$body->from_name!==null){$patch['from_name']=Compat::strip(DataRoutes::text(array('from_name'=>$body->from_name),'from_name','',1,150));}
        if(isset($body->from_email)&&$body->from_email!==null){$fe=self::normEmail(DataRoutes::text(array('from_email'=>$body->from_email),'from_email','',1,255));if($fe===null){throw new ValidationFailure('Enter a valid From email address','from_email');}$patch['from_email']=$fe;}
        if(property_exists($body,'reply_to')){
            $rlist=array();
            if($body->reply_to!==null){foreach((is_array($body->reply_to)?$body->reply_to:explode(',',(string)$body->reply_to)) as $ri){$re=self::normEmail((string)$ri);if($re!==null&&!in_array($re,$rlist,true)){$rlist[]=$re;}}}
            $patch['reply_to']=$rlist?$rlist[0]:null;$patch['reply_to_addresses']=json_encode($rlist,JSON_THROW_ON_ERROR);
        }
        if(isset($body->api_key)&&$body->api_key!==null){
            $k=Compat::strip(DataRoutes::text(array('api_key'=>$body->api_key),'api_key','',null,512));
            if($k!==''){if($this->config===null){throw new AuthFailure(503,'Encrypted runtime vault is required.');}$patch['api_key_encrypted']=$this->config->sealRuntimeSecret($this->db->schema()->table('email_accounts').'/'.$id.'/api_key',$k);$patch['connection_status']='unknown';$patch['last_error']=null;}
        }
        foreach(array('is_active','track_opens','track_clicks') as $bf){if(isset($body->$bf)&&$body->$bf!==null){$patch[$bf]=DataRoutes::boolean($body->$bf,$bf);}}
        if(!empty($body->is_default)){$this->db->execute('UPDATE '.$this->table('email_accounts').' SET is_default=0');$patch['is_default']=true;}
        $this->db->update('email_accounts',$patch,array('id'=>$id));
        return $this->serializeAccount($this->db->one('email_accounts',array('id'=>$id)));
    }
    public function deleteAccount($id){
        $this->db->requireTransaction();
        $acct=$this->db->one('email_accounts',array('id'=>$id));
        if(!$acct){throw new AuthFailure(404,'Email account not found');}
        $used=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('campaigns').' WHERE email_account_id=%d',array($id))[0]['n'];
        if($used>0){throw new AuthFailure(409,$used.' campaign(s) still send through this account. Point them at another sender or switch this account off instead of deleting it.');}
        $wasDef=(bool)$acct['is_default'];
        $this->db->delete('email_accounts',array('id'=>$id));
        if($wasDef){
            $next=$this->db->rows('SELECT id FROM '.$this->table('email_accounts').' WHERE is_active=1 ORDER BY id ASC LIMIT 1');
            if($next){$this->db->update('email_accounts',array('is_default'=>true),array('id'=>(int)$next[0]['id']));}
        }
        return null;
    }
    public function makeDefault($id){
        $this->db->requireTransaction();
        if(!$this->db->one('email_accounts',array('id'=>$id))){throw new AuthFailure(404,'Email account not found');}
        $this->db->execute('UPDATE '.$this->table('email_accounts').' SET is_default=0');
        $this->db->update('email_accounts',array('is_default'=>true,'updated_at'=>$this->records->now()),array('id'=>$id));
        return $this->serializeAccount($this->db->one('email_accounts',array('id'=>$id)));
    }
    public function revealWebhook($id){
        $acct=$this->db->one('email_accounts',array('id'=>$id));
        if(!$acct){throw new AuthFailure(404,'Email account not found');}
        $tok=$this->openAcctSecret($id,'webhook_token',$acct['webhook_token']??'');
        return array('account_id'=>(int)$id,'webhook_path'=>'/sender-suite/v1/webhooks/brevo','webhook_url'=>null,'token'=>$tok,'note'=>'Live Brevo webhook ingestion is gated in this WordPress build.');
    }
    public function rotateWebhook($id){
        $this->db->requireTransaction();
        $acct=$this->db->one('email_accounts',array('id'=>$id));
        if(!$acct){throw new AuthFailure(404,'Email account not found');}
        if($this->config===null){throw new AuthFailure(503,'Encrypted runtime vault is required.');}
        $plain=bin2hex(random_bytes(16));
        $enc=$this->config->sealRuntimeSecret($this->db->schema()->table('email_accounts').'/'.$id.'/webhook_token',$plain);
        $this->db->update('email_accounts',array('webhook_token'=>$enc,'updated_at'=>$this->records->now()),array('id'=>$id));
        return array('account_id'=>(int)$id,'webhook_path'=>'/sender-suite/v1/webhooks/brevo','webhook_url'=>null,'token'=>$plain);
    }
    private function serializeDomain(array $d){
        $recs=array();if(!empty($d['records_json'])){$dec=is_array($d['records_json'])?$d['records_json']:json_decode((string)$d['records_json'],true);if(is_array($dec)){$recs=$dec;}}
        return array('id'=>(int)$d['id'],'domain'=>$d['domain'],'status'=>$d['status'],'records'=>$recs,'dmarc_rua'=>$d['dmarc_rua'],'email_account_id'=>$d['email_account_id']!==null?(int)$d['email_account_id']:null,'last_error'=>$d['last_error'],'warmup_started_at'=>Records::iso($d['warmup_started_at']??null),'authenticated_at'=>Records::iso($d['authenticated_at']??null),'last_checked_at'=>Records::iso($d['last_checked_at']??null),'created_at'=>Records::iso($d['created_at']??null));
    }
    public function listDomains(){
        $items=array();
        foreach($this->db->rows('SELECT * FROM '.$this->table('sending_domains').' ORDER BY id DESC LIMIT 200') as $r){$items[]=$this->serializeDomain($this->db->decode('sending_domains',$r));}
        return array('total'=>count($items),'items'=>$items);
    }
    public function getDomain($id){
        $d=$this->db->one('sending_domains',array('id'=>$id));
        if(!$d){throw new AuthFailure(404,'Sending domain not found');}
        return $this->serializeDomain($d);
    }
    public function deleteDomain($id){
        $this->db->requireTransaction();
        if(!$this->db->one('sending_domains',array('id'=>$id))){throw new AuthFailure(404,'Sending domain not found');}
        $this->db->delete('sending_domains',array('id'=>$id));
        return null;
    }
    private static function rate($part,$whole){return $whole>0?round(($part/$whole)*100,1):0.0;}
    public function stats(array $q){
        $days=DataRoutes::integer($q['days']??30,'days',1,365);
        $acctId=isset($q['account_id'])&&$q['account_id']!==''?DataRoutes::integer($q['account_id'],'account_id'):null;
        $nowDt=new \DateTimeImmutable($this->records->now(),new \DateTimeZone('UTC'));
        $since=$nowDt->modify('-'.$days.' days')->format('Y-m-d H:i:s.u');
        $where="COALESCE(channel,'sms')='email' AND created_at>=%s";$args=array($since);
        if($acctId!==null){$where.=' AND email_account_id=%d';$args[]=$acctId;}
        $agg=$this->db->rows("SELECT SUM(CASE WHEN direction='outgoing' AND status IN ('sent','delivered') THEN 1 ELSE 0 END) AS sent,SUM(CASE WHEN direction='outgoing' AND status='delivered' THEN 1 ELSE 0 END) AS delivered,SUM(CASE WHEN direction='outgoing' AND status IN ('failed','undelivered') THEN 1 ELSE 0 END) AS failed,SUM(CASE WHEN direction='outgoing' AND status IN ('queued','pending','sending') THEN 1 ELSE 0 END) AS queued,SUM(CASE WHEN direction='incoming' THEN 1 ELSE 0 END) AS replies,SUM(CASE WHEN direction='outgoing' AND COALESCE(open_count,0)>0 THEN 1 ELSE 0 END) AS opened,SUM(CASE WHEN direction='outgoing' AND COALESCE(click_count,0)>0 THEN 1 ELSE 0 END) AS clicked,COALESCE(SUM(open_count),0) AS opens,COALESCE(SUM(click_count),0) AS clicks FROM ".$this->table('messages').' WHERE '.$where,$args)[0];
        $sent=(int)($agg['sent']??0);$del=(int)($agg['delivered']??0);$fail=(int)($agg['failed']??0);$queued=(int)($agg['queued']??0);$rep=(int)($agg['replies']??0);$opened=(int)($agg['opened']??0);$clicked=(int)($agg['clicked']??0);$opens=(int)($agg['opens']??0);$clicks=(int)($agg['clicks']??0);
        $suppressed=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('email_suppressions'))[0]['n'];
        $bots=array('opened'=>0,'clicked'=>0);
        foreach($this->db->rows("SELECT event_type,COUNT(*) AS n FROM ".$this->table('email_events')." WHERE is_bot=1 AND created_at>=%s AND event_type IN ('opened','clicked') GROUP BY event_type",array($since)) as $br){$bots[$br['event_type']]=(int)$br['n'];}
        $series=array();
        foreach($this->db->rows("SELECT SUBSTR(created_at,1,10) AS d,SUM(CASE WHEN direction='outgoing' AND status IN ('sent','delivered') THEN 1 ELSE 0 END) AS sent,SUM(CASE WHEN direction='outgoing' AND status='delivered' THEN 1 ELSE 0 END) AS delivered,SUM(CASE WHEN direction='outgoing' AND COALESCE(open_count,0)>0 THEN 1 ELSE 0 END) AS opened,SUM(CASE WHEN direction='outgoing' AND COALESCE(click_count,0)>0 THEN 1 ELSE 0 END) AS clicked,SUM(CASE WHEN direction='incoming' THEN 1 ELSE 0 END) AS replies FROM ".$this->table('messages').' WHERE '.$where.' GROUP BY SUBSTR(created_at,1,10) ORDER BY d ASC',$args) as $sr){
            $series[]=array('date'=>$sr['d'],'sent'=>(int)$sr['sent'],'delivered'=>(int)$sr['delivered'],'opened'=>(int)$sr['opened'],'clicked'=>(int)$sr['clicked'],'replies'=>(int)$sr['replies']);
        }
        return array('period_days'=>$days,'sent'=>$sent,'delivered'=>$del,'failed'=>$fail,'unconfirmed'=>0,'queued'=>$queued,'replies'=>$rep,'opened'=>$opened,'clicked'=>$clicked,'opens'=>$opens,'clicks'=>$clicks,'human_opens'=>$opens,'human_clicks'=>$clicks,'bot_opens'=>$bots['opened'],'bot_clicks'=>$bots['clicked'],'unsubscribed'=>$suppressed,'delivery_rate'=>self::rate($del,$sent),'open_rate'=>self::rate($opened,$sent),'click_rate'=>self::rate($clicked,$sent),'reply_rate'=>self::rate($rep,$sent),'failure_rate'=>self::rate($fail,$sent),'unconfirmed_rate'=>0.0,'series'=>$series);
    }
    public function overview(array $q){
        $days=DataRoutes::integer($q['days']??30,'days',1,365);
        $totals=$this->stats($q);
        $accounts=$this->listAccounts()['items'];
        $senders=$this->listSenders()['items'];
        $defId=null;foreach($senders as $sn){if(!empty($sn['is_default'])){$defId=$sn['id'];break;}}
        $unread=(int)$this->db->rows("SELECT COUNT(*) AS n FROM ".$this->table('conversations')." WHERE COALESCE(channel,'sms')='email' AND unread_count>0")[0]['n'];
        $suppressed=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('email_suppressions'))[0]['n'];
        $recentCamps=array();
        foreach($this->db->rows("SELECT * FROM ".$this->table('ads_campaigns')." WHERE channel='email' ORDER BY id DESC LIMIT 8") as $cr){
            $c=$this->db->decode('ads_campaigns',$cr);
            $st=$this->db->rows("SELECT SUM(CASE WHEN status IN ('sent','delivered') THEN 1 ELSE 0 END) AS s,SUM(CASE WHEN status='delivered' THEN 1 ELSE 0 END) AS d,SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS f,SUM(CASE WHEN replied_at IS NOT NULL THEN 1 ELSE 0 END) AS r FROM ".$this->table('ads_assignments').' WHERE campaign_id=%d',array($c['id']))[0];
            $recentCamps[]=array('id'=>(int)$c['id'],'name'=>$c['name'],'status'=>$c['status'],'subject'=>$c['subject'],'messages_sent'=>(int)($st['s']??0),'messages_delivered'=>(int)($st['d']??0),'messages_failed'=>(int)($st['f']??0),'replies'=>(int)($st['r']??0),'created_at'=>Records::iso($c['created_at']));
        }
        $recentEv=array();
        foreach($this->db->rows('SELECT * FROM '.$this->table('email_events').' ORDER BY id DESC LIMIT 12') as $er){
            $e=$this->db->decode('email_events',$er);
            $recentEv[]=array('id'=>(int)$e['id'],'event_type'=>$e['event_type'],'email_address'=>$e['email_address'],'subject'=>$e['subject'],'detail'=>$e['detail'],'created_at'=>Records::iso($e['created_at']));
        }
        return array('days'=>$days,'totals'=>$totals,'series'=>$totals['series'],'accounts'=>$accounts,'senders'=>$senders,'default_account_id'=>$defId,'unread_conversations'=>$unread,'suppressed_total'=>$suppressed,'recent_campaigns'=>$recentCamps,'recent_events'=>$recentEv);
    }
    public function reference(){
        $senders=$this->listSenders()['items'];
        $defId=null;foreach($senders as $sn){if(!empty($sn['is_default'])){$defId=$sn['id'];break;}}
        $tpls=array();
        foreach($this->db->rows("SELECT id,name,subject,category FROM ".$this->table('templates')." WHERE COALESCE(channel,'sms')='email' ORDER BY name ASC LIMIT 500") as $t){
            $tpls[]=array('id'=>(int)$t['id'],'name'=>$t['name'],'subject'=>$t['subject'],'category'=>$t['category']);
        }
        $lists=array();
        foreach($this->db->rows('SELECT id,name,contact_count FROM '.$this->table('contact_lists').' ORDER BY name ASC LIMIT 500') as $l){
            $lists[]=array('id'=>(int)$l['id'],'name'=>$l['name'],'contact_count'=>(int)($l['contact_count']??0));
        }
        $emailable=(int)$this->db->rows("SELECT COUNT(*) AS n FROM ".$this->table('contacts')." WHERE COALESCE(is_email_opted_out,0)=0 AND COALESCE(is_email_undeliverable,0)=0 AND COALESCE(email,'') LIKE '%@%'")[0]['n'];
        return array('accounts'=>$senders,'default_account_id'=>$defId,'templates'=>$tpls,'lists'=>$lists,'emailable_contacts'=>$emailable);
    }
    public function preview($body){
        if(!is_object($body)){throw new ValidationFailure('Supply a JSON object');}
        foreach($body as $k=>$v){if(!in_array($k,array('subject','body','html_body','template_id','contact_id','attachments'),true)){throw new ValidationFailure('Unknown preview field',$k);}}
        $subj=isset($body->subject)&&$body->subject!==null?DataRoutes::text(array('subject'=>$body->subject),'subject','',null,998):'';
        $txt=isset($body->body)&&$body->body!==null?DataRoutes::text(array('body'=>$body->body),'body','',null,200000):'';
        $html=isset($body->html_body)&&$body->html_body!==null?DataRoutes::text(array('html_body'=>$body->html_body),'html_body','',null,500000):null;
        if(isset($body->template_id)&&$body->template_id!==null){
            $tid=DataRoutes::integer($body->template_id,'template_id',1);
            $tpl=$this->db->one('templates',array('id'=>$tid));
            if(!$tpl){throw new AuthFailure(404,'Template not found');}
            if($subj===''){$subj=(string)($tpl['subject']??'');}
            if($txt===''){$txt=(string)($tpl['body']??'');}
            if($html===null&&!empty($tpl['html_body'])){$html=(string)$tpl['html_body'];}
        }
        $contact=null;
        if(isset($body->contact_id)&&$body->contact_id!==null){
            $cid=DataRoutes::integer($body->contact_id,'contact_id',1);
            $contact=$this->db->one('contacts',array('id'=>$cid));
            if(!$contact){throw new AuthFailure(404,'Contact not found');}
        }
        if($contact!==null){
            $map=array('first_name'=>(string)($contact['first_name']??''),'last_name'=>(string)($contact['last_name']??''),'email'=>(string)($contact['email']??''),'phone_number'=>(string)($contact['phone_number']??''),'full_name'=>trim(($contact['first_name']??'').' '.($contact['last_name']??'')));
            if(is_array($contact['custom_fields']??null)){foreach($contact['custom_fields'] as $ck=>$cv){if(is_scalar($cv)){$map[(string)$ck]=(string)$cv;}}}
            foreach($map as $mk=>$mv){
                $subj=str_replace(array('{{'.$mk.'}}','{'.$mk.'}'),$mv,$subj);
                $txt=str_replace(array('{{'.$mk.'}}','{'.$mk.'}'),$mv,$txt);
                if($html!==null){$html=str_replace(array('{{'.$mk.'}}','{'.$mk.'}'),htmlspecialchars($mv,ENT_QUOTES,'UTF-8'),$html);}
            }
        }
        $safeHtml=$html!==null?$html:nl2br(htmlspecialchars($txt,ENT_QUOTES,'UTF-8'));
        return array('subject'=>$subj,'text'=>$txt,'html'=>$safeHtml,'attachments'=>array());
    }
    private function serializeEmailMessage(array $m,?array $acct,?array $contact){
        return array(
            'id'=>(int)$m['id'],'conversation_id'=>$m['conversation_id']!==null?(int)$m['conversation_id']:null,
            'contact_id'=>$m['contact_id']!==null?(int)$m['contact_id']:null,'campaign_id'=>$m['campaign_id']!==null?(int)$m['campaign_id']:null,
            'direction'=>$m['direction'],'channel'=>$m['channel']??'email','subject'=>$m['subject'],'body'=>$m['body'],'html_body'=>$m['html_body']??null,
            'from_address'=>$m['from_address'],'to_address'=>$m['to_address'],'email_account_id'=>$m['email_account_id']!==null?(int)$m['email_account_id']:null,
            'sender_account'=>$m['direction']==='outgoing'&&$acct?array('id'=>(int)$acct['id'],'name'=>$acct['name'],'from_email'=>$acct['from_email'],'from_name'=>$acct['from_name']?:$acct['name']):null,
            'status'=>$m['status'],'delivery_state'=>$m['status'],'provider'=>$m['provider'],'provider_message_id'=>$m['provider_message_id'],
            'open_count'=>(int)($m['open_count']??0),'click_count'=>(int)($m['click_count']??0),
            'opened_at'=>Records::iso($m['opened_at']??null),'clicked_at'=>Records::iso($m['clicked_at']??null),
            'bounced_at'=>Records::iso($m['bounced_at']??null),'bounced_hard'=>(bool)($m['bounced_hard']??false),
            'is_auto_reply'=>(bool)($m['is_auto_reply']??false),'attachments'=>array(),'cc'=>array(),'bcc'=>array(),
            'rfc_message_id'=>$m['rfc_message_id']??null,'in_reply_to'=>$m['in_reply_to']??null,
            'retry_count'=>(int)($m['retry_count']??0),'retry_at'=>Records::iso($m['retry_at']??null),'last_error'=>$m['last_error'],
            'sent_at'=>Records::iso($m['sent_at']??null),'delivered_at'=>Records::iso($m['delivered_at']??null),
            'failed_at'=>Records::iso($m['failed_at']??null),'created_at'=>Records::iso($m['created_at']??null),
            'contact_name'=>$contact?($contact['first_name']?:$contact['last_name']):null,
            'contact_email'=>$contact?$contact['email']:$m['to_address'],'contact_phone'=>$contact?$contact['phone_number']:null
        );
    }
    public function history(array $q){
        $page=DataRoutes::integer($q['page']??1,'page',1,100000);
        $per=DataRoutes::integer($q['per_page']??25,'per_page',1,100);
        $where=array("COALESCE(channel,'sms')='email'");$args=array();
        if(isset($q['direction'])&&$q['direction']!==''){$where[]='direction=%s';$args[]=DataRoutes::text($q,'direction','',null,20);}
        if(isset($q['status'])&&$q['status']!==''){$where[]='status=%s';$args[]=DataRoutes::text($q,'status','',null,30);}
        if(isset($q['account_id'])&&$q['account_id']!==''){$where[]='email_account_id=%d';$args[]=DataRoutes::integer($q['account_id'],'account_id');}
        if(isset($q['search'])&&Compat::strip((string)$q['search'])!==''){
            $s='%'.strtolower(DataRoutes::like(Compat::strip(DataRoutes::text($q,'search','',null,200)))).'%';
            $where[]='(LOWER(COALESCE(subject,\'\')) LIKE %s OR LOWER(COALESCE(to_address,\'\')) LIKE %s OR LOWER(COALESCE(from_address,\'\')) LIKE %s)';
            $args[]=$s;$args[]=$s;$args[]=$s;
        }
        $wSql=implode(' AND ',$where);
        $total=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('messages').' WHERE '.$wSql,$args)[0]['n'];
        $accts=array();foreach($this->db->rows('SELECT * FROM '.$this->table('email_accounts')) as $ar){$accts[(int)$ar['id']]=$ar;}
        $items=array();
        foreach($this->db->rows('SELECT * FROM '.$this->table('messages').' WHERE '.$wSql.' ORDER BY id DESC LIMIT %d OFFSET %d',array_merge($args,array($per,($page-1)*$per))) as $mr){
            $m=$this->db->decode('messages',$mr);
            $c=$m['contact_id']!==null?$this->db->one('contacts',array('id'=>(int)$m['contact_id'])):null;
            $items[]=$this->serializeEmailMessage($m,$m['email_account_id']!==null?($accts[(int)$m['email_account_id']]??null):null,$c);
        }
        return array('total'=>$total,'page'=>$page,'per_page'=>$per,'items'=>$items);
    }
    public function listSuppression(array $q){
        $page=DataRoutes::integer($q['page']??1,'page',1,100000);
        $per=DataRoutes::integer($q['per_page']??50,'per_page',1,200);
        $where='1=1';$args=array();
        if(isset($q['search'])&&Compat::strip((string)$q['search'])!==''){$where='LOWER(email_address) LIKE %s';$args[]= '%'.strtolower(DataRoutes::like(Compat::strip(DataRoutes::text($q,'search','',null,200)))).'%';}
        $total=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('email_suppressions').' WHERE '.$where,$args)[0]['n'];
        $items=array();
        foreach($this->db->rows('SELECT * FROM '.$this->table('email_suppressions').' WHERE '.$where.' ORDER BY id DESC LIMIT %d OFFSET %d',array_merge($args,array($per,($page-1)*$per))) as $r){
            $e=$this->db->decode('email_suppressions',$r);
            $items[]=array('id'=>(int)$e['id'],'email_address'=>$e['email_address'],'contact_id'=>$e['contact_id']!==null?(int)$e['contact_id']:null,'campaign_id'=>$e['campaign_id']!==null?(int)$e['campaign_id']:null,'reason'=>$e['reason'],'source'=>$e['source'],'hard_bounce'=>(bool)$e['hard_bounce'],'suppressed_at'=>Records::iso($e['suppressed_at']??null),'created_at'=>Records::iso($e['created_at']??null));
        }
        return array('total'=>$total,'page'=>$page,'per_page'=>$per,'items'=>$items);
    }
    public function addSuppression($body,&$status){
        if(!is_object($body)){throw new ValidationFailure('Supply a JSON object');}
        foreach($body as $k=>$v){if(!in_array($k,array('email_address','reason','source','hard_bounce'),true)){throw new ValidationFailure('Unknown suppression field',$k);}}
        $this->db->requireTransaction();
        $email=self::normEmail(DataRoutes::text(array('email_address'=>$body->email_address??''),'email_address','',1,320));
        if($email===null){throw new ValidationFailure('Enter a valid email address','email_address');}
        $reason=Compat::strip(DataRoutes::text(array('reason'=>$body->reason??'manual'),'reason','manual',null,100))?:'manual';
        $source=Compat::strip(DataRoutes::text(array('source'=>$body->source??'manual'),'source','manual',null,100))?:'manual';
        $hard=isset($body->hard_bounce)?DataRoutes::boolean($body->hard_bounce,'hard_bounce'):false;
        $contactRows=$this->db->rows('SELECT id FROM '.$this->table('contacts').' WHERE LOWER(email)=%s LIMIT 1',array($email));
        $cid=$contactRows?(int)$contactRows[0]['id']:null;
        if($cid!==null){
            $cPatch=array('is_email_opted_out'=>true,'email_opted_out_at'=>$this->records->now(),'updated_at'=>$this->records->now());
            if($hard){$cPatch['is_email_undeliverable']=true;}
            $this->db->update('contacts',$cPatch,array('id'=>$cid));
        }
        $existing=$this->db->one('email_suppressions',array('email_address'=>$email));
        if($existing){
            $this->db->update('email_suppressions',array('reason'=>$reason,'source'=>$source,'hard_bounce'=>$hard||$existing['hard_bounce'],'contact_id'=>$cid??$existing['contact_id']),array('id'=>$existing['id']));
            $entry=$this->db->one('email_suppressions',array('id'=>$existing['id']));
        }else{
            $entry=$this->records->create('email_suppressions',array('email_address'=>$email,'contact_id'=>$cid,'reason'=>$reason,'source'=>$source,'hard_bounce'=>$hard,'suppressed_at'=>$this->records->now()));
        }
        $status=201;
        return array('id'=>(int)$entry['id'],'email_address'=>$entry['email_address'],'reason'=>$entry['reason'],'source'=>$entry['source'],'hard_bounce'=>(bool)$entry['hard_bounce'],'created_at'=>Records::iso($entry['created_at']));
    }
    public function removeSuppression($entryId){
        $this->db->requireTransaction();
        $entry=$this->db->one('email_suppressions',array('id'=>$entryId));
        if(!$entry){throw new AuthFailure(404,'Suppression entry not found');}
        $this->db->execute('UPDATE '.$this->table('contacts').' SET is_email_opted_out=0,email_opted_out_at=NULL,updated_at=%s WHERE LOWER(email)=%s',array($this->records->now(),strtolower((string)$entry['email_address'])));
        $this->db->delete('email_suppressions',array('id'=>$entryId));
        return null;
    }
    public function events(array $q){
        $page=DataRoutes::integer($q['page']??1,'page',1,100000);
        $per=DataRoutes::integer($q['per_page']??50,'per_page',1,200);
        $where=array('1=1');$args=array();
        if(isset($q['event_type'])&&$q['event_type']!==''){$where[]='event_type=%s';$args[]=DataRoutes::text($q,'event_type','',null,40);}
        if(isset($q['account_id'])&&$q['account_id']!==''){$where[]='account_id=%d';$args[]=DataRoutes::integer($q['account_id'],'account_id');}
        $wSql=implode(' AND ',$where);
        $total=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('email_events').' WHERE '.$wSql,$args)[0]['n'];
        $items=array();
        foreach($this->db->rows('SELECT * FROM '.$this->table('email_events').' WHERE '.$wSql.' ORDER BY id DESC LIMIT %d OFFSET %d',array_merge($args,array($per,($page-1)*$per))) as $r){
            $e=$this->db->decode('email_events',$r);
            $items[]=array('id'=>(int)$e['id'],'event_type'=>$e['event_type'],'account_id'=>$e['account_id']!==null?(int)$e['account_id']:null,'message_id'=>$e['message_id']!==null?(int)$e['message_id']:null,'contact_id'=>$e['contact_id']!==null?(int)$e['contact_id']:null,'campaign_id'=>$e['campaign_id']!==null?(int)$e['campaign_id']:null,'email_address'=>$e['email_address'],'subject'=>$e['subject'],'link'=>$e['link'],'detail'=>$e['detail'],'created_at'=>Records::iso($e['created_at']));
        }
        return array('total'=>$total,'page'=>$page,'per_page'=>$per,'items'=>$items);
    }
    public function deliverability($acctId,array $q){
        $acct=$this->db->one('email_accounts',array('id'=>$acctId));
        if(!$acct){throw new AuthFailure(404,'Email account not found');}
        $days=DataRoutes::integer($q['days']??30,'days',1,365);
        $st=$this->stats(array('days'=>$days,'account_id'=>$acctId));
        $bounced=(int)$this->db->rows("SELECT COUNT(*) AS n FROM ".$this->table('messages')." WHERE email_account_id=%d AND bounced_at IS NOT NULL",array($acctId))[0]['n'];
        $sent=(int)$st['sent'];$bRate=self::rate($bounced,$sent);
        $checks=array(
            array('key'=>'tracking','label'=>'Open and click tracking enabled','ok'=>(bool)($acct['track_opens']||$acct['track_clicks']),'detail'=>($acct['track_opens']||$acct['track_clicks'])?'Engagement tracking is enabled.':'Without tracking there is no evidence of engagement.'),
            array('key'=>'bounce_rate','label'=>'Bounce rate under 2%','ok'=>$bRate<2.0,'detail'=>$bRate.'% of '.$sent.' sends bounced in the last '.$days.' days.'),
            array('key'=>'public_url','label'=>'Public web address configured (one-click unsubscribe)','ok'=>false,'detail'=>'One-click public unsubscribe links are disabled in this WordPress build.'),
            array('key'=>'engagement','label'=>'Recipients are engaging (opens)','ok'=>$st['open_rate']>=10.0||$sent===0,'detail'=>$st['open_rate'].'% opened, '.$st['click_rate'].'% clicked.')
        );
        $okCnt=count(array_filter($checks,function($c){return $c['ok'];}));
        return array('account'=>$this->serializeAccount($acct),'days'=>$days,'domains'=>$this->listDomains()['items'],'domains_error'=>null,'metrics'=>array('sent'=>$sent,'bounced'=>$bounced,'opened'=>$st['opened'],'clicked'=>$st['clicked'],'unsubscribed'=>$st['unsubscribed'],'bounce_rate'=>$bRate,'open_rate'=>$st['open_rate'],'click_rate'=>$st['click_rate']),'checks'=>$checks,'score'=>(int)round(100*$okCnt/count($checks)));
    }
    public function messageEvents($msgId){
        $m=$this->db->one('messages',array('id'=>$msgId));
        if(!$m){throw new AuthFailure(404,'Message not found');}
        $evRows=$this->db->rows('SELECT * FROM '.$this->table('email_events').' WHERE message_id=%d ORDER BY id DESC LIMIT 200',array($msgId));
        $links=array();$events=array();
        foreach($evRows as $er){
            $e=$this->db->decode('email_events',$er);
            if(!empty($e['link'])){$links[$e['link']]=true;}
            $events[]=array('id'=>(int)$e['id'],'event_type'=>$e['event_type'],'link'=>$e['link'],'detail'=>$e['detail'],'created_at'=>Records::iso($e['created_at']));
        }
        return array('message_id'=>(int)$msgId,'summary'=>array('status'=>$m['status'],'sent_at'=>Records::iso($m['sent_at']??null),'delivered_at'=>Records::iso($m['delivered_at']??null),'first_opened_at'=>Records::iso($m['opened_at']??null),'first_clicked_at'=>Records::iso($m['clicked_at']??null),'bounced_at'=>Records::iso($m['bounced_at']??null),'open_count'=>(int)($m['open_count']??0),'click_count'=>(int)($m['click_count']??0),'unique_links_clicked'=>count($links)),'events'=>$events);
    }
    public function contactEngagement($contactId){
        $c=$this->db->one('contacts',array('id'=>$contactId));
        if(!$c){throw new AuthFailure(404,'Contact not found');}
        $msgs=array();$sent=0;$opened=0;$clicked=0;$bounced=0;
        foreach($this->db->rows("SELECT * FROM ".$this->table('messages')." WHERE COALESCE(channel,'sms')='email' AND contact_id=%d ORDER BY id DESC LIMIT 50",array($contactId)) as $mr){
            $m=$this->db->decode('messages',$mr);
            if($m['direction']==='outgoing'){$sent++;}
            if((int)($m['open_count']??0)>0){$opened++;}
            if((int)($m['click_count']??0)>0){$clicked++;}
            if($m['bounced_at']!==null){$bounced++;}
            $msgs[]=array('id'=>(int)$m['id'],'subject'=>$m['subject'],'direction'=>$m['direction'],'status'=>$m['status'],'open_count'=>(int)($m['open_count']??0),'click_count'=>(int)($m['click_count']??0),'created_at'=>Records::iso($m['created_at']),'campaign_id'=>$m['campaign_id']!==null?(int)$m['campaign_id']:null);
        }
        $links=array();
        foreach($this->db->rows("SELECT link,COUNT(*) AS c FROM ".$this->table('email_events')." WHERE contact_id=%d AND event_type='clicked' AND link IS NOT NULL GROUP BY link ORDER BY c DESC LIMIT 20",array($contactId)) as $lr){
            $links[]=array('url'=>$lr['link'],'clicks'=>(int)$lr['c']);
        }
        $events=array();
        foreach($this->db->rows('SELECT * FROM '.$this->table('email_events').' WHERE contact_id=%d ORDER BY id DESC LIMIT 40',array($contactId)) as $er){
            $e=$this->db->decode('email_events',$er);
            $events[]=array('id'=>(int)$e['id'],'event_type'=>$e['event_type'],'link'=>$e['link'],'subject'=>$e['subject'],'created_at'=>Records::iso($e['created_at']));
        }
        return array('contact_id'=>(int)$contactId,'email'=>$c['email'],'is_email_opted_out'=>(bool)$c['is_email_opted_out'],'is_email_undeliverable'=>(bool)$c['is_email_undeliverable'],'email_status'=>$c['email_status'],'emails_sent'=>(int)($c['emails_sent']?:$sent),'last_emailed_at'=>Records::iso($c['last_emailed_at']??null),'totals'=>array('sent'=>$sent,'opened'=>$opened,'clicked'=>$clicked,'bounced'=>$bounced,'open_rate'=>self::rate($opened,$sent),'click_rate'=>self::rate($clicked,$sent)),'links'=>$links,'messages'=>$msgs,'events'=>$events);
    }
    public function emailInboxList(array $q){
        $q['channel']='email';
        return (new Inbox($this->db))->listing($q);
    }
    public function emailInboxDetail($convId){
        $conv=$this->db->one('conversations',array('id'=>$convId));
        if(!$conv||($conv['channel']??'sms')!=='email'){throw new AuthFailure(404,'Email conversation not found');}
        return (new Inbox($this->db))->read($convId);
    }
    public function emailInboxRead($convId){
        $conv=$this->db->one('conversations',array('id'=>$convId));
        if(!$conv||($conv['channel']??'sms')!=='email'){throw new AuthFailure(404,'Email conversation not found');}
        (new Inbox($this->db))->read($convId);
        return array('success'=>true);
    }
    public function emailInboxStatus($convId,$body){
        $conv=$this->db->one('conversations',array('id'=>$convId));
        if(!$conv||($conv['channel']??'sms')!=='email'){throw new AuthFailure(404,'Email conversation not found');}
        return (new Inbox($this->db))->action($convId,'status',$body);
    }
    public function emailInboxUnread(){
        $r=$this->db->rows("SELECT COUNT(*) AS convs,COALESCE(SUM(unread_count),0) AS msgs FROM ".$this->table('conversations')." WHERE COALESCE(channel,'sms')='email' AND unread_count>0")[0];
        return array('unread_conversations'=>(int)($r['convs']??0),'unread_messages'=>(int)($r['msgs']??0));
    }
    public function listMailboxes(array $q){
        $inc=isset($q['include_inactive'])?DataRoutes::boolean($q['include_inactive'],'include_inactive'):false;
        $where=$inc?'1=1':'is_active=1';
        $items=array();
        foreach($this->db->rows('SELECT * FROM '.$this->table('email_mailboxes').' WHERE '.$where.' ORDER BY id ASC LIMIT 200') as $r){
            $items[]=$this->serializeMailbox($this->db->decode('email_mailboxes',$r));
        }
        $oauth=$this->config!==null?(new CallsSettings($this->db,$this->config))->googleOauth():array('configured'=>false,'source'=>'none','client_id_masked'=>'');
        return array('items'=>$items,'count'=>count($items),'google_oauth_ready'=>$oauth['configured'],'google_oauth_source'=>$oauth['source'],'google_client_id_masked'=>$oauth['client_id_masked'],'redirect_uri'=>null,'poll_interval'=>60);
    }
    public function readMailbox($id){
        $m=$this->db->one('email_mailboxes',array('id'=>$id));
        if(!$m){throw new AuthFailure(404,'Mailbox not found');}
        return array('mailbox'=>$this->serializeMailbox($m));
    }
    public function patchMailbox($id,$body){
        if(!is_object($body)){throw new ValidationFailure('Supply a JSON object');}
        foreach($body as $k=>$v){if(!in_array($k,array('name','folders','import_all','rescue_from_spam','never_spam_filter','send_replies','poll_interval','is_active','app_password'),true)){throw new ValidationFailure('Unknown mailbox field',$k);}}
        if(isset($body->app_password)&&$body->app_password!==null){throw new AuthFailure(503,'Live IMAP credential rotation and verification are not enabled in this WordPress build.');}
        $this->db->requireTransaction();
        if(!$this->db->one('email_mailboxes',array('id'=>$id))){throw new AuthFailure(404,'Mailbox not found');}
        $patch=array('updated_at'=>$this->records->now());
        if(isset($body->name)&&$body->name!==null){$patch['name']=Compat::strip(DataRoutes::text(array('name'=>$body->name),'name','',1,150));}
        if(isset($body->folders)&&$body->folders!==null){$patch['folders']=Compat::strip(DataRoutes::text(array('folders'=>$body->folders),'folders','',1,500));}
        if(isset($body->poll_interval)&&$body->poll_interval!==null){$patch['poll_interval']=DataRoutes::integer($body->poll_interval,'poll_interval',15,3600);}
        foreach(array('import_all','rescue_from_spam','never_spam_filter','send_replies','is_active') as $bf){if(isset($body->$bf)&&$body->$bf!==null){$patch[$bf]=DataRoutes::boolean($body->$bf,$bf);}}
        $this->db->update('email_mailboxes',$patch,array('id'=>$id));
        return array('mailbox'=>$this->serializeMailbox($this->db->one('email_mailboxes',array('id'=>$id))));
    }
    public function deleteMailbox($id){
        $this->db->requireTransaction();
        if(!$this->db->one('email_mailboxes',array('id'=>$id))){throw new AuthFailure(404,'Mailbox not found');}
        $this->db->delete('mailbox_messages',array('mailbox_id'=>$id));
        $this->db->delete('email_mailboxes',array('id'=>$id));
        return array('success'=>true);
    }
    public function replyRoutingReport(){
        $accts=$this->db->rows('SELECT * FROM '.$this->table('email_accounts').' ORDER BY id ASC LIMIT 200');
        $mbs=array();$connected=array();
        foreach($this->db->rows('SELECT * FROM '.$this->table('email_mailboxes').' ORDER BY id ASC LIMIT 200') as $mr){
            $m=$this->db->decode('email_mailboxes',$mr);
            $mbs[]=$this->serializeMailbox($m);
            if($m['is_active']){$connected[]=$m;}
        }
        $findings=array();
        foreach($accts as $ar){
            $a=$this->db->decode('email_accounts',$ar);
            if(!$a['is_active']){continue;}
            $dom=strtolower(substr(strrchr((string)$a['from_email'],'@')?:'',1));
            if(in_array($dom,self::FREEMAIL,true)){
                $findings[]=array('severity'=>'high','where'=>"Sender '".$a['name']."' — From",'value'=>$a['from_email'],'problem'=>'From is a free webmail address on mail sent via API; DMARC will fail.','fix'=>'Use an authenticated custom domain for From and keep freemail only as a connected Reply-To.');
            }
        }
        if(!$connected){
            $findings[]=array('severity'=>'high','where'=>'Reply inbox','value'=>'no mailbox connected','problem'=>'No mailbox is connected to mirror replies.','fix'=>'Connect a mailbox once live IMAP/Gmail sync is enabled.');
        }
        $repliesTotal=0;$rescuedTotal=0;$lastSync=null;
        foreach($connected as $cm){
            $repliesTotal+=(int)($cm['total_replies']??0);$rescuedTotal+=(int)($cm['total_rescued']??0);
            if($cm['last_sync_at']!==null&&($lastSync===null||$cm['last_sync_at']>$lastSync)){$lastSync=$cm['last_sync_at'];}
        }
        $high=array_filter($findings,function($f){return $f['severity']==='high';});
        return array('findings'=>$findings,'healthy'=>count($high)===0,'mailboxes'=>$mbs,'connected'=>count($connected),'replies_imported'=>$repliesTotal,'rescued_from_spam'=>$rescuedTotal,'last_sync_at'=>Records::iso($lastSync),'brevo_inbound_ready'=>false,'paths'=>array(array('id'=>'brevo','label'=>'Brevo inbound parsing','status'=>'disabled','detail'=>'Inbound Brevo webhook parsing is gated in this WordPress build.'),array('id'=>'mailbox','label'=>'Connected mailbox sync','status'=>count($connected)>0?'stored':'not configured','detail'=>'Stored mailbox mirror; live background polling is gated in this WordPress build.')));
    }
    private function serializeMailMessage(array $r,?array $mb,$withBody=false){
        $jsonList=function($v){if(is_array($v)){return array_values($v);}if(is_string($v)&&$v!==''){$d=json_decode($v,true);if(is_array($d)){return array_values($d);}}return array();};
        $out=array('id'=>(int)$r['id'],'mailbox_id'=>(int)$r['mailbox_id'],'mailbox_email'=>$mb?$mb['email_address']:null,'mailbox_name'=>$mb?$mb['name']:null,'provider_message_id'=>$r['provider_message_id'],'thread_id'=>$r['thread_id'],'folder'=>$r['folder']??'INBOX','labels'=>$jsonList($r['labels']??null),'subject'=>$r['subject']?:'(no subject)','from_name'=>$r['from_name'],'from_address'=>$r['from_address'],'to'=>$jsonList($r['to_addresses']??null),'cc'=>$jsonList($r['cc_addresses']??null),'date'=>Records::iso($r['date']??null),'snippet'=>$r['snippet'],'is_read'=>(bool)$r['is_read'],'is_starred'=>(bool)$r['is_starred'],'attachments'=>(int)($r['attachment_count']??0),'conversation_id'=>$r['conversation_id']!==null?(int)$r['conversation_id']:null,'contact_id'=>$r['contact_id']!==null?(int)$r['contact_id']:null);
        if($withBody){$out['body_text']=$r['body_text'];$out['body_html']=$r['body_html'];$out['is_truncated']=(bool)($r['is_truncated']??false);}
        return $out;
    }
    public function mailOverview(){
        $mbs=array();foreach($this->db->rows('SELECT * FROM '.$this->table('email_mailboxes').' ORDER BY id ASC LIMIT 200') as $mr){$mbs[]=$this->db->decode('email_mailboxes',$mr);}
        $emptyCounts=function(){$c=array();foreach(self::FOLDERS as $f){$c[$f]=array('total'=>0,'unread'=>0);}$c['ALL']=array('total'=>0,'unread'=>0);return $c;};
        $perAcct=array();$all=$emptyCounts();
        foreach($this->db->rows('SELECT mailbox_id,UPPER(COALESCE(folder,\'OTHER\')) AS f,is_read,COUNT(*) AS n FROM '.$this->table('mailbox_messages').' GROUP BY mailbox_id,UPPER(COALESCE(folder,\'OTHER\')),is_read') as $gr){
            $mid=(int)$gr['mailbox_id'];$f=in_array($gr['f'],self::FOLDERS,true)?$gr['f']:'OTHER';$cnt=(int)$gr['n'];$unr=!((bool)$gr['is_read'])?$cnt:0;
            if(!isset($perAcct[$mid])){$perAcct[$mid]=$emptyCounts();}
            $perAcct[$mid][$f]['total']+=$cnt;$perAcct[$mid][$f]['unread']+=$unr;
            $all[$f]['total']+=$cnt;$all[$f]['unread']+=$unr;
            if($f!=='TRASH'){$perAcct[$mid]['ALL']['total']+=$cnt;$perAcct[$mid]['ALL']['unread']+=$unr;$all['ALL']['total']+=$cnt;$all['ALL']['unread']+=$unr;}
        }
        $accounts=array();$totalUnread=0;
        foreach($mbs as $m){
            $cnts=$perAcct[(int)$m['id']]??$emptyCounts();
            if($m['is_active']){$totalUnread+=$cnts['ALL']['unread'];}
            $accounts[]=array_merge($this->serializeMailbox($m),array('counts'=>$cnts,'needs_reconnect'=>!empty($m['last_error'])&&stripos((string)$m['last_error'],'invalid_grant')!==false));
        }
        return array('accounts'=>$accounts,'all'=>$all,'total_unread'=>$totalUnread,'folder_keys'=>array_merge(array('ALL'),self::FOLDERS));
    }
    public function mailMessages(array $q){
        $folder=strtoupper(DataRoutes::text($q,'folder','INBOX',null,20)?:'INBOX');
        $limit=DataRoutes::integer($q['limit']??50,'limit',1,200);
        $offset=DataRoutes::integer($q['offset']??0,'offset',0,100000);
        $where=array('1=1');$args=array();
        if($folder==='ALL'){$where[]="UPPER(COALESCE(folder,'INBOX'))<>'TRASH'";}else{$where[]="UPPER(COALESCE(folder,'INBOX'))=%s";$args[]=$folder;}
        if(isset($q['mailbox_id'])&&$q['mailbox_id']!==''){$where[]='mailbox_id=%d';$args[]=DataRoutes::integer($q['mailbox_id'],'mailbox_id');}
        if(isset($q['contact_id'])&&$q['contact_id']!==''){$where[]='contact_id=%d';$args[]=DataRoutes::integer($q['contact_id'],'contact_id');}
        if(isset($q['conversation_id'])&&$q['conversation_id']!==''){$where[]='conversation_id=%d';$args[]=DataRoutes::integer($q['conversation_id'],'conversation_id');}
        if(isset($q['q'])&&Compat::strip((string)$q['q'])!==''){
            $s='%'.strtolower(DataRoutes::like(Compat::strip(DataRoutes::text($q,'q','',null,200)))).'%';
            $where[]='(LOWER(COALESCE(subject,\'\')) LIKE %s OR LOWER(COALESCE(from_address,\'\')) LIKE %s OR LOWER(COALESCE(from_name,\'\')) LIKE %s OR LOWER(COALESCE(snippet,\'\')) LIKE %s)';
            $args[]=$s;$args[]=$s;$args[]=$s;$args[]=$s;
        }
        $wSql=implode(' AND ',$where);
        $total=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('mailbox_messages').' WHERE '.$wSql,$args)[0]['n'];
        $mbs=array();foreach($this->db->rows('SELECT * FROM '.$this->table('email_mailboxes')) as $mr){$mbs[(int)$mr['id']]=$mr;}
        $items=array();
        foreach($this->db->rows('SELECT * FROM '.$this->table('mailbox_messages').' WHERE '.$wSql.' ORDER BY date DESC,id DESC LIMIT %d OFFSET %d',array_merge($args,array($limit,$offset))) as $r){
            $dec=$this->db->decode('mailbox_messages',$r);
            $items[]=$this->serializeMailMessage($dec,$mbs[(int)$dec['mailbox_id']]??null,false);
        }
        return array('items'=>$items,'total'=>$total,'limit'=>$limit,'offset'=>$offset,'folder'=>$folder);
    }
    public function readMailMessage($mailboxId,$providerMsgId,$withBody=true){
        $row=$this->db->one('mailbox_messages',array('mailbox_id'=>$mailboxId,'provider_message_id'=>$providerMsgId));
        if(!$row){throw new AuthFailure(404,'That message is not in the mirror yet.');}
        $mb=$this->db->one('email_mailboxes',array('id'=>$mailboxId));
        $payload=$this->serializeMailMessage($row,$mb,$withBody);
        $thread=array();
        if(!empty($row['thread_id'])){
            foreach($this->db->rows('SELECT * FROM '.$this->table('mailbox_messages').' WHERE mailbox_id=%d AND thread_id=%s ORDER BY date ASC,id ASC LIMIT 100',array($mailboxId,$row['thread_id'])) as $tr){
                $td=$this->db->decode('mailbox_messages',$tr);
                $thread[]=$this->serializeMailMessage($td,$mb,(int)$td['id']===(int)$row['id']);
            }
        }else{$thread[]=$payload;}
        $payload['thread']=$thread;
        return $payload;
    }
    public function setMailFlags($mailboxId,$providerMsgId,$body){
        if(!is_object($body)){throw new ValidationFailure('Supply a JSON object');}
        foreach($body as $k=>$v){if(!in_array($k,array('read','starred'),true)){throw new ValidationFailure('Unknown flag field',$k);}}
        $hasRead=isset($body->read)&&$body->read!==null;$hasStar=isset($body->starred)&&$body->starred!==null;
        if(!$hasRead&&!$hasStar){throw new AuthFailure(400,'Nothing to change.');}
        $this->db->requireTransaction();
        $row=$this->db->one('mailbox_messages',array('mailbox_id'=>$mailboxId,'provider_message_id'=>$providerMsgId));
        if(!$row){throw new AuthFailure(404,'That message is not in the mirror yet.');}
        $patch=array('updated_at'=>$this->records->now());
        if($hasRead){$patch['is_read']=DataRoutes::boolean($body->read,'read');}
        if($hasStar){$patch['is_starred']=DataRoutes::boolean($body->starred,'starred');}
        $this->db->update('mailbox_messages',$patch,array('id'=>$row['id']));
        $fresh=$this->db->one('mailbox_messages',array('id'=>$row['id']));
        return array('success'=>true,'is_read'=>(bool)$fresh['is_read'],'is_starred'=>(bool)$fresh['is_starred'],'local_only'=>true);
    }
    public static function handle($request,array $c,array $user,&$status){
        $path=trim(substr($request->get_route(),strlen('/sender-suite/v1/')),'/');$method=$request->get_method();
        if(strlen($request->get_body())>524288){throw new AuthFailure(413,'Email Manager request exceeds size limit.');}
        $s=new self($c['db'],$c['config']??null);$q=$request->get_query_params();
        $adminReq=function()use($user){if(($user['role']??'')!=='admin'){throw new AuthFailure(403,'Application administrator role required.');}};
        if($path==='email/accounts'&&$method==='GET'){return $s->listAccounts();}
        if($path==='email/accounts'&&$method==='POST'){$adminReq();$status=201;return $c['db']->transaction(function()use($s,$request){return $s->createAccount(DataRoutes::body($request));});}
        if(preg_match('~^email/accounts/(-?[0-9]+)$~D',$path,$m)){
            $id=DataRoutes::integer($m[1],'account_id');$adminReq();
            if($method==='PATCH'){return $c['db']->transaction(function()use($s,$id,$request){return $s->updateAccount($id,DataRoutes::body($request));});}
            if($method==='DELETE'){$c['db']->transaction(function()use($s,$id){$s->deleteAccount($id);});$status=204;return null;}
        }
        if(preg_match('~^email/accounts/(-?[0-9]+)/(default|webhook/reveal|webhook/rotate|deliverability)$~D',$path,$m)){
            $id=DataRoutes::integer($m[1],'account_id');$act=$m[2];
            if($act==='deliverability'&&$method==='GET'){return $s->deliverability($id,$q);}
            $adminReq();
            if($act==='default'&&$method==='POST'){return $c['db']->transaction(function()use($s,$id){return $s->makeDefault($id);});}
            if($act==='webhook/reveal'&&$method==='POST'){return $s->revealWebhook($id);}
            if($act==='webhook/rotate'&&$method==='POST'){return $c['db']->transaction(function()use($s,$id){return $s->rotateWebhook($id);});}
        }
        if($path==='email/senders'&&$method==='GET'){return $s->listSenders();}
        if($path==='email/health'&&$method==='GET'){return $s->health();}
        if($path==='email/domains'&&$method==='GET'){return $s->listDomains();}
        if(preg_match('~^email/domains/(-?[0-9]+)$~D',$path,$m)){
            $did=DataRoutes::integer($m[1],'domain_id');
            if($method==='GET'){return $s->getDomain($did);}
            if($method==='DELETE'){$adminReq();$c['db']->transaction(function()use($s,$did){$s->deleteDomain($did);});$status=204;return null;}
        }
        if($path==='email/overview'&&$method==='GET'){return $s->overview($q);}
        if($path==='email/stats'&&$method==='GET'){return $s->stats($q);}
        if($path==='email/reference'&&$method==='GET'){return $s->reference();}
        if($path==='email/preview'&&$method==='POST'){return $s->preview(DataRoutes::body($request));}
        if($path==='email/history'&&$method==='GET'){return $s->history($q);}
        if(preg_match('~^email/messages/(-?[0-9]+)/events$~D',$path,$m)&&$method==='GET'){return $s->messageEvents(DataRoutes::integer($m[1],'message_id'));}
        if(preg_match('~^email/contacts/(-?[0-9]+)/engagement$~D',$path,$m)&&$method==='GET'){return $s->contactEngagement(DataRoutes::integer($m[1],'contact_id'));}
        if($path==='email/inbox/conversations'&&$method==='GET'){return $s->emailInboxList($q);}
        if($path==='email/inbox/unread-count'&&$method==='GET'){return $s->emailInboxUnread();}
        if(preg_match('~^email/inbox/conversations/(-?[0-9]+)(?:/(read|status))?$~D',$path,$m)){
            $cid=DataRoutes::integer($m[1],'conversation_id');$sub=$m[2]??null;
            if($sub===null&&$method==='GET'){return $s->emailInboxDetail($cid);}
            if($sub==='read'&&$method==='POST'){return $c['db']->transaction(function()use($s,$cid){return $s->emailInboxRead($cid);});}
            if($sub==='status'&&$method==='POST'){return $c['db']->transaction(function()use($s,$cid,$request){return $s->emailInboxStatus($cid,DataRoutes::body($request));});}
        }
        if($path==='email/suppression'&&$method==='GET'){return $s->listSuppression($q);}
        if($path==='email/suppression'&&$method==='POST'){return $c['db']->transaction(function()use($s,$request,&$status){return $s->addSuppression(DataRoutes::body($request),$status);});}
        if(preg_match('~^email/suppression/(-?[0-9]+)$~D',$path,$m)&&$method==='DELETE'){
            $eid=DataRoutes::integer($m[1],'entry_id');
            $c['db']->transaction(function()use($s,$eid){$s->removeSuppression($eid);});$status=204;return null;
        }
        if($path==='email/events'&&$method==='GET'){return $s->events($q);}
        if($path==='mailbox'&&$method==='GET'){return $s->listMailboxes($q);}
        if($path==='mailbox/report'&&$method==='GET'){return $s->replyRoutingReport();}
        if(preg_match('~^mailbox/(-?[0-9]+)$~D',$path,$m)){
            $mid=DataRoutes::integer($m[1],'mailbox_id');
            if($method==='GET'){return $s->readMailbox($mid);}
            $adminReq();
            if($method==='PATCH'){return $c['db']->transaction(function()use($s,$mid,$request){return $s->patchMailbox($mid,DataRoutes::body($request));});}
            if($method==='DELETE'){return $c['db']->transaction(function()use($s,$mid){return $s->deleteMailbox($mid);});}
        }
        if($path==='mail/overview'&&$method==='GET'){return $s->mailOverview();}
        if($path==='mail/messages'&&$method==='GET'){return $s->mailMessages($q);}
        if(preg_match('~^mail/messages/(-?[0-9]+)/([A-Za-z0-9._-]+)(?:/flags)?$~D',$path,$m)){
            $mid=DataRoutes::integer($m[1],'mailbox_id');$pmid=$m[2];
            if($method==='GET'){return $s->readMailMessage($mid,$pmid,isset($q['body'])?DataRoutes::boolean($q['body'],'body'):true);}
            if($method==='POST'&&substr($path,-6)==='/flags'){return $c['db']->transaction(function()use($s,$mid,$pmid,$request){return $s->setMailFlags($mid,$pmid,DataRoutes::body($request));});}
        }
        if(preg_match('~^(?:email/(?:domains(?:/-?[0-9]+/verify)?|send|senders-preview|test-send|messages/-?[0-9]+/retry|inbox/conversations/-?[0-9]+/reply|accounts/-?[0-9]+/(?:test|test-send|senders))|mailbox(?:/google/(?:start|callback)|/-?[0-9]+/(?:check|sync|filters|test-send))?|mail/(?:refresh(?:/-?[0-9]+)?|messages/-?[0-9]+/[A-Za-z0-9._-]+/(?:move|delete|reply)))$~D',$path)){
            return (new EmailTransport($c['db'],$c['config']??null))->handleRoute($path,$method,$request,$user,$status);
        }
        throw new AuthFailure(404,'Not found');
    }
}
