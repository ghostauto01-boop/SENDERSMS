<?php
namespace SenderSuite;

/** Deterministic local email/phone validator and stored verification-job controls. No live DNS/SMTP/Reacher network probes. */
final class Validator {
    const BATCH_SIZE=25;
    const TYPOS=array('gmial.com'=>'gmail.com','gmai.com'=>'gmail.com','gmil.com'=>'gmail.com','gmail.co'=>'gmail.com','gmail.cm'=>'gmail.com','gmail.con'=>'gmail.com','gmail.comm'=>'gmail.com','gmaill.com'=>'gmail.com','gamil.com'=>'gmail.com','gnail.com'=>'gmail.com','gmaul.com'=>'gmail.com','gmal.com'=>'gmail.com','hotmial.com'=>'hotmail.com','hotmai.com'=>'hotmail.com','hotmal.com'=>'hotmail.com','hotmail.co'=>'hotmail.com','homail.com'=>'hotmail.com','hotamil.com'=>'hotmail.com','outlok.com'=>'outlook.com','outllook.com'=>'outlook.com','outlook.co'=>'outlook.com','otlook.com'=>'outlook.com','yaho.com'=>'yahoo.com','yahooo.com'=>'yahoo.com','yahoo.co'=>'yahoo.com','yahho.com'=>'yahoo.com','yahoo.cm'=>'yahoo.com','yandex.co'=>'yandex.com','yandx.com'=>'yandex.com','iclod.com'=>'icloud.com','icloud.co'=>'icloud.com','icoud.com'=>'icloud.com','protonmai.com'=>'protonmail.com','protonmal.com'=>'protonmail.com','zohomail.com'=>'zoho.com','zoh.com'=>'zoho.com','googlmail.com'=>'gmail.com','mail.ru.com'=>'mail.ru');
    const DISPOSABLE=array('mailinator.com'=>1,'guerrillamail.com'=>1,'guerrillamail.net'=>1,'10minutemail.com'=>1,'tempmail.com'=>1,'temp-mail.org'=>1,'throwawaymail.com'=>1,'trashmail.com'=>1,'yopmail.com'=>1,'yopmail.fr'=>1,'sharklasers.com'=>1,'getnada.com'=>1,'nada.email'=>1,'dispostable.com'=>1,'maildrop.cc'=>1,'fakeinbox.com'=>1,'mailnesia.com'=>1,'spamgourmet.com'=>1,'mytrashmail.com'=>1,'tempinbox.com'=>1,'mailcatch.com'=>1,'mohmal.com'=>1,'emailondeck.com'=>1,'mintemail.com'=>1,'tempr.email'=>1,'discard.email'=>1,'spam4.me'=>1,'grr.la'=>1,'mail-temporaire.fr'=>1);
    const FREE_MAIL=array('gmail.com'=>1,'googlemail.com'=>1,'yahoo.com'=>1,'yahoo.co.uk'=>1,'ymail.com'=>1,'hotmail.com'=>1,'outlook.com'=>1,'live.com'=>1,'msn.com'=>1,'icloud.com'=>1,'me.com'=>1,'aol.com'=>1,'protonmail.com'=>1,'proton.me'=>1,'zoho.com'=>1,'gmx.com'=>1,'gmx.net'=>1,'mail.com'=>1,'yandex.com'=>1,'yandex.ru'=>1,'mail.ru'=>1,'web.de'=>1,'inbox.com'=>1);
    const ROLE_PARTS=array('info'=>1,'sales'=>1,'support'=>1,'admin'=>1,'contact'=>1,'hello'=>1,'hi'=>1,'help'=>1,'enquiries'=>1,'enquiry'=>1,'inquiries'=>1,'inquiry'=>1,'office'=>1,'mail'=>1,'team'=>1,'marketing'=>1,'billing'=>1,'accounts'=>1,'accounting'=>1,'hr'=>1,'jobs'=>1,'careers'=>1,'orders'=>1,'bookings'=>1,'reservations'=>1,'reservation'=>1,'customercare'=>1,'customerservice'=>1,'service'=>1,'general'=>1,'reception'=>1,'frontdesk'=>1,'webmaster'=>1,'postmaster'=>1,'noreply'=>1,'no-reply'=>1,'donotreply'=>1);
    const PLACEHOLDER_DOMAINS=array('example.com'=>1,'example.org'=>1,'example.net'=>1,'test.com'=>1,'test.local'=>1,'localhost'=>1,'email.com'=>1,'domain.com'=>1,'yourdomain.com'=>1,'sentry.io'=>1,'example.co'=>1,'mailinator.com'=>1);
    private $db;private $records;private $contacts;
    public function __construct(Database $db,callable $clock=null){$this->db=$db;$this->records=new Records($db,$clock);$this->contacts=new Contacts($db,$clock);}
    private function table($n){return Schema::identifier($this->db->schema()->table($n));}
    public static function definitions(){return array('/validator/status'=>array('GET'),'/validator/self-test'=>array('POST'),'/validator/selection'=>array('POST'),'/validator/batch'=>array('POST'),'/validator/jobs'=>array('GET','POST'),'/validator/jobs/(?P<job_id>[A-Za-z0-9._-]+)'=>array('GET'),'/validator/jobs/(?P<job_id>[A-Za-z0-9._-]+)/cancel'=>array('POST'),'/contacts/enrich/status'=>array('GET'),'/contacts/enrich'=>array('POST'),'/contacts/validate-emails'=>array('POST'),'/lists/(?P<list_id>-?[0-9]+)/validate-emails'=>array('POST'));}
    public static function validSyntax($address){
        if(!is_string($address)||strlen($address)>254||preg_match('/^[A-Za-z0-9.!#$%&\'*+\/=?^_`{|}~\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$/D',$address)!==1){return false;}
        $pos=strrpos($address,'@');$local=substr($address,0,$pos);$domain=substr($address,$pos+1);
        if(strlen($local)>64||$local[0]==='.'||substr($local,-1)==='.'||strpos($local,'..')!==false){return false;}
        foreach(explode('.',$domain) as $lbl){if($lbl===''||strlen($lbl)>63||$lbl[0]==='-'||substr($lbl,-1)==='-'){return false;}}
        return true;
    }
    public function checkEmail($raw,$deep=false){
        $nowIso=Records::iso($this->records->now());
        $cleaned=Compat::email($raw);
        if($cleaned===null||!self::validSyntax($cleaned)){
            return array('email'=>$raw,'verdict'=>'undeliverable','status'=>'invalid','confidence'=>0,'is_reachable'=>'invalid','accepts_mail'=>null,'is_catch_all'=>null,'is_disposable'=>false,'is_role_account'=>false,'is_free'=>false,'is_valid_syntax'=>false,'suggested_email'=>null,'smtp_can_connect'=>null,'provider'=>'builtin','problems'=>array('invalid syntax'),'checked_at'=>$nowIso);
        }
        $pos=strrpos($cleaned,'@');$local=substr($cleaned,0,$pos);$domain=strtolower(rtrim(substr($cleaned,$pos+1),'.'));
        $stem=explode('.',explode('+',$local,2)[0],2)[0];
        $isRole=isset(self::ROLE_PARTS[$stem]);
        $isDisp=isset(self::DISPOSABLE[$domain]);
        $isFree=isset(self::FREE_MAIL[$domain]);
        $isPlaceholder=isset(self::PLACEHOLDER_DOMAINS[$domain])||in_array($local,array('example','yourname','youremail','noreply','no-reply'),true);
        $suggested=isset(self::TYPOS[$domain])&&self::TYPOS[$domain]!==$domain?($local.'@'.self::TYPOS[$domain]):null;
        $problems=array();
        if($suggested!==null){$problems[]="domain '{$domain}' looks like a typo";}
        if($isDisp){$problems[]='disposable/throwaway domain';}
        if($isPlaceholder){$problems[]='placeholder address';}
        $tld=substr($domain,(int)strrpos($domain,'.')+1);
        if($domain===''||in_array($tld,array('invalid','test','localhost','local'),true)){
            $problems[]="domain '{$domain}' has no MX records";
            return array('email'=>$cleaned,'verdict'=>'undeliverable','status'=>'invalid','confidence'=>0,'is_reachable'=>'invalid','accepts_mail'=>false,'is_catch_all'=>null,'is_disposable'=>$isDisp,'is_role_account'=>$isRole,'is_free'=>$isFree,'is_valid_syntax'=>true,'suggested_email'=>$suggested,'smtp_can_connect'=>null,'provider'=>'builtin','problems'=>$problems,'checked_at'=>$nowIso);
        }
        if($isRole){$problems[]='Shared/role mailbox';}
        $problems[]='DNS unavailable or timed out; domain is not confirmed bad';
        $problems[]=$deep?'Built-in SMTP checks are disabled; mailbox not checked':'Mailbox not checked (quick mode)';
        $verdict=($isDisp||$isRole)?'risky':'unknown';
        return array('email'=>$cleaned,'verdict'=>$verdict,'status'=>$verdict==='risky'?'risky':'unknown','confidence'=>$verdict==='risky'?60:null,'is_reachable'=>$verdict,'accepts_mail'=>null,'is_catch_all'=>null,'is_disposable'=>$isDisp,'is_role_account'=>$isRole,'is_free'=>$isFree,'is_valid_syntax'=>true,'suggested_email'=>$suggested,'smtp_can_connect'=>null,'provider'=>'builtin','problems'=>$problems,'checked_at'=>$nowIso);
    }
    public static function checkPhone($raw){
        if($raw===null||Compat::strip($raw)===''){return array('verdict'=>'missing','normalized'=>null,'problems'=>array('No phone number'));}
        $r=NumberFilter::classify($raw);
        $soft=$r['sendable']?NumberFilter::soft($raw):null;
        return array('verdict'=>$soft!==null?'risky':($r['sendable']?'good':'bad'),'normalized'=>$r['normalized'],'reason'=>$soft?:$r['reason'],'problems'=>array($soft!==null?'Looks like sample data; review manually':($r['sendable']?'Valid Nigerian mobile format; active SIM and delivery not verified':$r['detail'])));
    }
    private static function overall($email,$phone){
        $map=array('deliverable'=>'good','undeliverable'=>'bad');$vals=array();
        foreach(array($email,$phone) as $v){if($v&&($v['verdict']??'missing')!=='missing'){$vals[$map[$v['verdict']]??$v['verdict']]=true;}}
        foreach(array('bad','risky','unknown','good') as $st){if(isset($vals[$st])){return $st;}}
        return 'missing';
    }
    public function status(){
        return array('engine'=>'builtin','api_key_required'=>false,'reacher_configured'=>false,'reacher_key_configured'=>false,'dns_available'=>false,'smtp_enabled'=>false,'smtp_reason'=>'Live DNS/SMTP probes are disabled in this WordPress build; syntax, reserved-domain, role/disposable and Nigerian mobile format checks run locally.','can_confirm_mailboxes'=>false,'batch_size'=>self::BATCH_SIZE,'phone_region'=>'NG','notice'=>'No key is needed for built-in checks. Live SMTP/DNS probes are disabled in this WordPress build, so valid-syntax non-reserved addresses stay Unknown unless risky. Phone checks confirm Nigerian mobile format, not whether a SIM is active. No messages are sent.');
    }
    public function selfTest(){
        $malformed=$this->checkEmail('not-an-email',false);
        $reserved=$this->checkEmail('person@validator.invalid',false);
        $goodPhone=NumberFilter::classify('08034567891');
        $shortPhone=NumberFilter::classify('123');
        $checks=array(
            array('name'=>'Reject malformed email','passed'=>$malformed['verdict']==='undeliverable'),
            array('name'=>'Reject reserved .invalid domain','passed'=>$reserved['verdict']==='undeliverable'),
            array('name'=>'Accept Nigerian mobile format','passed'=>$goodPhone['sendable']===true),
            array('name'=>'Reject short phone number','passed'=>$shortPhone['sendable']===false)
        );
        $passed=true;foreach($checks as $c){if(!$c['passed']){$passed=false;}}
        return array('passed'=>$passed,'checks'=>$checks,'checked_at'=>Records::iso($this->records->now()),'notice'=>'Local engine tests only. This does not test Reacher credentials, live DNS/SMTP connectivity or actual delivery.');
    }
    private function parseSelection($body,$allowJobFields=false){
        if(!is_object($body)){throw new ValidationFailure('Supply a JSON object');}
        $allowed=array('scope','contact_ids','list_id','search','lead_status','channel','email_state');
        if($allowJobFields){$allowed=array_merge($allowed,array('deep','recheck'));}
        foreach($body as $k=>$v){if(!in_array($k,$allowed,true)){throw new ValidationFailure('Unknown selection field',$k);}}
        $scope=$body->scope??'all';if(!is_string($scope)||!in_array($scope,array('all','list','ids'),true)){throw new ValidationFailure("scope must be 'all', 'list' or 'ids'",'scope');}
        $ids=isset($body->contact_ids)?DataRoutes::ids($body->contact_ids,'contact_ids'):array();
        if(count($ids)>1000){throw new ValidationFailure('contact_ids cannot exceed 1000 items','contact_ids');}
        foreach($ids as $i=>$cid){if($cid<1){throw new ValidationFailure('Input should be greater than 0','contact_ids.'.$i);}}
        $listId=isset($body->list_id)&&$body->list_id!==null?DataRoutes::integer($body->list_id,'list_id',1):null;
        $q=array();foreach(array('search'=>255,'lead_status'=>50,'channel'=>10,'email_state'=>50) as $k=>$max){if(property_exists($body,$k)&&$body->$k!==null){$q[$k]=DataRoutes::text(array($k=>$body->$k),$k,null,null,$max);}}
        if(isset($q['channel'])&&!in_array($q['channel'],array('sms','email'),true)){throw new ValidationFailure("channel must be 'sms' or 'email'",'channel');}
        return array('scope'=>$scope,'contact_ids'=>$ids,'list_id'=>$listId,'filters'=>$q,'deep'=>isset($body->deep)?DataRoutes::boolean($body->deep,'deep'):true,'recheck'=>isset($body->recheck)?DataRoutes::boolean($body->recheck,'recheck'):false);
    }
    public function selection($body){
        $s=$this->parseSelection($body,false);
        if($s['scope']==='ids'){
            if(!$s['contact_ids']){throw new AuthFailure(400,'No contacts selected');}
            $u=array_values(array_unique($s['contact_ids']));
            $rows=$this->db->rows('SELECT id FROM '.$this->table('contacts').' WHERE id IN ('.implode(',',array_fill(0,count($u),'%d')).') ORDER BY id ASC',$u);
            $out=array_map('intval',array_column($rows,'id'));
            return array('contact_ids'=>$out,'total'=>count($out));
        }
        if($s['scope']==='list'&&$s['list_id']===null){throw new AuthFailure(400,'Choose a list');}
        if($s['list_id']!==null&&!$this->db->rows('SELECT id FROM '.$this->table('contact_lists').' WHERE id=%d',array($s['list_id']))){throw new AuthFailure(404,'List not found');}
        $filters=$s['filters'];if($s['list_id']!==null){$filters['list_id']=$s['list_id'];}
        $args=array();$where=$this->contacts->filters($filters,$args);
        $count=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('contacts').' c WHERE '.$where,$args)[0]['n'];
        if($count>50000){throw new AuthFailure(413,'Selection exceeds synchronous limit.');}
        $rows=$this->db->rows('SELECT c.id FROM '.$this->table('contacts').' c WHERE '.$where.' ORDER BY c.id ASC',$args);
        $out=array_map('intval',array_column($rows,'id'));
        return array('contact_ids'=>$out,'total'=>count($out));
    }
    private function applyEmailVerdict(array $contact,array $verdict){
        if($verdict['email']===null||$verdict['email']===''){return;}
        $now=$this->records->now();
        $note=$verdict['provider'].': '.($verdict['problems']?implode('; ',$verdict['problems']):$verdict['verdict']);
        $patch=array('email_enriched_at'=>$now,'email_enrichment_note'=>implode('',array_map(array(Compat::class,'chr'),array_slice(Compat::points($note),0,255))),'updated_at'=>$now);
        if($verdict['verdict']==='deliverable'){$patch['email_verified']=true;$patch['email_verified_at']=$now;$patch['email_verdict']='deliverable';$patch['email_confidence']=95;}
        elseif($verdict['verdict']==='undeliverable'){$patch['email_verified']=false;$patch['email_verified_at']=null;$patch['email_status']='invalid';$patch['is_email_undeliverable']=true;$patch['email_verdict']='undeliverable';$patch['email_confidence']=0;}
        elseif($verdict['verdict']==='risky'){$patch['email_verified']=false;$patch['email_verified_at']=null;$patch['email_verdict']='risky';$patch['email_confidence']=60;}
        elseif((($contact['email_verdict']??'unknown')?:'unknown')==='unknown'&&!$contact['email_verified']){$patch['email_verdict']='unknown';}
        $this->db->update('contacts',$patch,array('id'=>$contact['id']));
    }
    public function batch($body,array $user){
        if(!is_object($body)){throw new ValidationFailure('Supply a JSON object');}
        foreach($body as $k=>$v){if(!in_array($k,array('items','contact_ids','check','deep','save'),true)){throw new ValidationFailure('Unknown batch field',$k);}}
        $rawItems=$body->items??array();if(!is_array($rawItems)||count($rawItems)>self::BATCH_SIZE){throw new ValidationFailure('items must be a list of at most '.self::BATCH_SIZE.' entries','items');}
        $ids=isset($body->contact_ids)?DataRoutes::ids($body->contact_ids,'contact_ids'):array();
        if(count($ids)>self::BATCH_SIZE){throw new ValidationFailure('contact_ids must contain at most '.self::BATCH_SIZE.' IDs','contact_ids');}
        foreach($ids as $i=>$cid){if($cid<1){throw new ValidationFailure('Input should be greater than 0','contact_ids.'.$i);}}
        if((bool)$rawItems===(bool)$ids){throw new ValidationFailure('Provide either items or contact_ids, not both or neither');}
        if(count(array_unique($ids))!==count($ids)){throw new ValidationFailure('Duplicate contact IDs in batch','contact_ids');}
        $check=$body->check??'both';if(!is_string($check)||!in_array($check,array('both','email','phone'),true)){throw new ValidationFailure("check must be 'both', 'email' or 'phone'",'check');}
        $deep=isset($body->deep)?DataRoutes::boolean($body->deep,'deep'):true;
        $save=isset($body->save)?DataRoutes::boolean($body->save,'save'):false;
        if($save&&!$ids){throw new ValidationFailure('Only existing contacts can be updated; CSV and single checks are previews','save');}
        if($save&&($user['role']??'')!=='admin'){throw new AuthFailure(403,'Application administrator role required.');}
        if($save){$this->db->requireTransaction();}
        $contacts=array();$entries=array();
        if($ids){
            foreach($this->db->rows('SELECT * FROM '.$this->table('contacts').' WHERE id IN ('.implode(',',array_fill(0,count($ids),'%d')).')',$ids) as $cr){$c=$this->db->decode('contacts',$cr);$contacts[(int)$c['id']]=$c;}
            foreach($ids as $cid){
                if(isset($contacts[$cid])){$c=$contacts[$cid];$nm=Compat::strip(($c['first_name']??'').' '.($c['last_name']??''));$entries[]=array('email'=>$c['email'],'phone_number'=>$c['phone_number'],'name'=>$nm!==''?$nm:($c['business_name']?:'Contact #'.$cid),'row'=>null);}
                else{$entries[]=array('email'=>null,'phone_number'=>null,'name'=>"Contact #{$cid} (deleted)",'row'=>null);}
            }
        }else{
            foreach($rawItems as $i=>$it){
                if(!is_object($it)){throw new ValidationFailure('Supply an entry object','items.'.$i);}
                foreach($it as $ek=>$ev){if(!in_array($ek,array('email','phone_number','name','row'),true)){throw new ValidationFailure('Unknown entry field','items.'.$i.'.'.$ek);}}
                $em=property_exists($it,'email')&&$it->email!==null?DataRoutes::text(array('items.'.$i.'.email'=>$it->email),'items.'.$i.'.email',null,null,1000):null;
                $ph=property_exists($it,'phone_number')&&$it->phone_number!==null?DataRoutes::text(array('items.'.$i.'.phone_number'=>$it->phone_number),'items.'.$i.'.phone_number',null,null,1000):null;
                $nm=property_exists($it,'name')&&$it->name!==null?DataRoutes::text(array('items.'.$i.'.name'=>$it->name),'items.'.$i.'.name','',null,500):'';
                $rw=property_exists($it,'row')&&$it->row!==null?DataRoutes::integer($it->row,'items.'.$i.'.row',1):null;
                $entries[]=array('email'=>$em,'phone_number'=>$ph,'name'=>$nm,'row'=>$rw);
            }
        }
        $smsSup=array();$emailSup=array();
        if($contacts){
            $phones=array();$emails=array();
            foreach($contacts as $c){if($c['phone_number']){$phones[]=$c['phone_number'];}if($c['email']){$emails[]=Compat::lower($c['email']);}}
            if($phones){foreach($this->db->rows('SELECT phone_number FROM '.$this->table('suppression_entries').' WHERE phone_number IN ('.implode(',',array_fill(0,count($phones),'%s')).')',$phones) as $sr){$smsSup[$sr['phone_number']]=true;}}
            if($emails){foreach($this->db->rows('SELECT LOWER(email_address) AS em FROM '.$this->table('email_suppressions').' WHERE LOWER(email_address) IN ('.implode(',',array_fill(0,count($emails),'%s')).')',$emails) as $er){$emailSup[$er['em']]=true;}}
        }
        $nowIso=Records::iso($this->records->now());$rows=array();
        foreach($entries as $idx=>$entry){
            $emailOut=null;$phoneOut=null;$hasVerdict=false;
            if(in_array($check,array('email','both'),true)){
                if($entry['email']!==null&&Compat::strip($entry['email'])!==''){$emailOut=$this->checkEmail($entry['email'],$deep);$hasVerdict=true;}
                else{$emailOut=array('verdict'=>'missing','problems'=>array('No email address'));}
            }
            if(in_array($check,array('phone','both'),true)){$phoneOut=self::checkPhone($entry['phone_number']);}
            $cid=$ids?$ids[$idx]:null;
            $row=array('row'=>$entry['row'],'name'=>$entry['name'],'input_email'=>$entry['email'],'input_phone'=>$entry['phone_number'],'email'=>$emailOut,'phone'=>$phoneOut,'status'=>self::overall($emailOut,$phoneOut),'saved'=>false,'blocked'=>array(),'checked_at'=>$nowIso,'contact_id'=>$cid);
            $contact=$cid!==null?($contacts[$cid]??null):null;
            if($cid!==null&&$contact===null){$row['status']='unknown';$row['note']='Contact was deleted; no changes saved';}
            if($contact!==null){
                if($save){
                    if($hasVerdict){$this->applyEmailVerdict($contact,$emailOut);}
                    if($phoneOut&&$phoneOut['verdict']==='bad'&&!$contact['is_undeliverable']){
                        $reason=substr((string)($phoneOut['reason']?:'invalid_format'),0,500);
                        $this->db->update('contacts',array('is_undeliverable'=>true,'undeliverable_reason'=>$reason,'delivery_fail_count'=>((int)($contact['delivery_fail_count']??0))+1,'updated_at'=>$this->records->now()),array('id'=>$contact['id']));
                        $contact['is_undeliverable']=true;
                    }
                    $row['saved']=$hasVerdict||($phoneOut&&$phoneOut['verdict']==='bad');
                    $contact=$this->db->decode('contacts',$this->db->rows('SELECT * FROM '.$this->table('contacts').' WHERE id=%d',array($contact['id']))[0]);
                }
                if($contact['is_opted_out']){$row['blocked'][]='SMS opted out';}
                if($contact['is_undeliverable']){$row['blocked'][]='SMS quarantined';}
                if($contact['phone_number']&&isset($smsSup[$contact['phone_number']])){$row['blocked'][]='SMS suppressed';}
                if($contact['is_email_opted_out']){$row['blocked'][]='Email unsubscribed';}
                if($contact['is_email_undeliverable']){$row['blocked'][]='Email quarantined';}
                if($contact['email']&&isset($emailSup[Compat::lower($contact['email'])])){$row['blocked'][]='Email suppressed';}
            }
            $rows[]=$row;
        }
        return array('items'=>$rows,'processed'=>count($rows));
    }
    private function jobDict(array $raw){
        $j=$this->db->decode('email_verification_jobs',$raw);
        $total=(int)($j['total']??0);$proc=(int)($j['processed']??0);
        return array('id'=>$j['id'],'status'=>$j['status'],'total'=>$total,'processed'=>$proc,'remaining'=>max($total-$proc,0),'valid'=>(int)($j['valid']??0),'invalid'=>(int)($j['invalid']??0),'risky'=>(int)($j['risky']??0),'unknown'=>(int)($j['unknown']??0),'skipped'=>(int)($j['skipped']??0),'deep'=>(bool)$j['deep'],'recheck'=>(bool)$j['recheck'],'engine'=>$j['engine']?:'builtin','smtp_enabled'=>(bool)$j['smtp_enabled'],'smtp_reason'=>$j['smtp_reason'],'error'=>$j['error'],'created_at'=>Records::iso($j['created_at']),'started_at'=>Records::iso($j['started_at']),'completed_at'=>Records::iso($j['completed_at']));
    }
    public function jobs(){
        $rows=$this->db->rows('SELECT id,owner_id,status,deep,recheck,\'[]\' AS contact_ids_json,total,processed,valid,invalid,risky,unknown,skipped,engine,smtp_enabled,smtp_reason,error,created_at,started_at,completed_at FROM '.$this->table('email_verification_jobs').' ORDER BY created_at DESC,id DESC LIMIT 25');
        $items=array();foreach($rows as $r){$items[]=$this->jobDict($r);}
        return array('items'=>$items);
    }
    public function job($jobId){
        $rows=$this->db->rows('SELECT id,owner_id,status,deep,recheck,\'[]\' AS contact_ids_json,total,processed,valid,invalid,risky,unknown,skipped,engine,smtp_enabled,smtp_reason,error,created_at,started_at,completed_at FROM '.$this->table('email_verification_jobs').' WHERE id=%s LIMIT 1',array($jobId));
        if(!$rows){throw new AuthFailure(404,'Verification job not found');}
        return $this->jobDict($rows[0]);
    }
    public function createJob($body,array $user){
        if(($user['role']??'')!=='admin'){throw new AuthFailure(403,'Application administrator role required.');}
        $this->parseSelection($body,true);
        return (new CampaignWorker($this->db))->createValidatorJob($body,$user);
    }
    public function cancelJob($jobId,array $user){
        if(($user['role']??'')!=='admin'){throw new AuthFailure(403,'Application administrator role required.');}
        $this->db->requireTransaction();
        $rows=$this->db->rows('SELECT id,owner_id,status,deep,recheck,\'[]\' AS contact_ids_json,total,processed,valid,invalid,risky,unknown,skipped,engine,smtp_enabled,smtp_reason,error,created_at,started_at,completed_at FROM '.$this->table('email_verification_jobs').' WHERE id=%s LIMIT 1',array($jobId));
        if(!$rows){throw new AuthFailure(404,'Verification job not found');}
        $cur=$this->db->decode('email_verification_jobs',$rows[0]);
        if(!in_array($cur['status'],array('queued','running'),true)){throw new AuthFailure(409,'This job is already '.$cur['status']);}
        $now=$this->records->now();
        if($this->db->update('email_verification_jobs',array('status'=>'cancelled','completed_at'=>$now),array('id'=>$jobId,'status'=>$cur['status']))!==1){throw new AuthFailure(409,'Verification job state changed. Reload and retry.');}
        $rows[0]['status']='cancelled';$rows[0]['completed_at']=$now;
        return $this->jobDict($rows[0]);
    }
    public function enrichmentStatus(){
        $r=$this->db->rows("SELECT COUNT(*) AS total,COALESCE(SUM(CASE WHEN email IS NOT NULL AND email<>'' THEN 1 ELSE 0 END),0) AS with_email,COALESCE(SUM(CASE WHEN email_verified=1 THEN 1 ELSE 0 END),0) AS verified,COALESCE(SUM(CASE WHEN email IS NOT NULL AND email<>'' AND email_verified=0 THEN 1 ELSE 0 END),0) AS unverified,COALESCE(SUM(CASE WHEN email_source='inferred' THEN 1 ELSE 0 END),0) AS inferred FROM ".$this->table('contacts'))[0];
        $total=(int)$r['total'];$withEmail=(int)$r['with_email'];
        return array('enabled'=>false,'providers'=>array(),'providers_active'=>false,'allow_inferred'=>false,'send_inferred'=>false,'dns_available'=>false,'free_stages'=>array('clean','diagnose'),'paid_stages'=>array('find','verify'),'contacts'=>array('total'=>$total,'with_email'=>$withEmail,'without_email'=>max($total-$withEmail,0),'verified'=>(int)$r['verified'],'unverified'=>(int)$r['unverified'],'inferred'=>(int)$r['inferred']));
    }
    private function validateContactsRows(array $rows,$deep){
        $counters=array('scanned'=>0,'with_email'=>0,'no_email'=>0,'deliverable'=>0,'undeliverable'=>0,'risky'=>0,'unknown'=>0,'already_verified'=>0,'invalid_syntax'=>0);
        $items=array();
        foreach($rows as $raw){
            $c=$this->db->decode('contacts',$raw);$counters['scanned']++;
            $addr=Compat::strip($c['email']??'');
            if($addr===''){$counters['no_email']++;continue;}
            $counters['with_email']++;
            if($c['email_verified']){$counters['already_verified']++;}
            $v=$this->checkEmail($addr,$deep);
            if(in_array('invalid syntax',$v['problems'],true)){$counters['invalid_syntax']++;}
            $counters[$v['verdict']]=($counters[$v['verdict']]??0)+1;
            $this->applyEmailVerdict($c,$v);
            $items[]=array_merge(array('contact_id'=>(int)$c['id']),$v);
        }
        return array_merge(array('success'=>true),$counters,array('deep'=>$deep,'engine'=>'builtin','items'=>$items));
    }
    public function validateContactEmails(array $q,array $user){
        if(($user['role']??'')!=='admin'){throw new AuthFailure(403,'Application administrator role required.');}
        $this->db->requireTransaction();
        $scope=DataRoutes::text($q,'scope',null,null,32);
        $deep=isset($q['deep'])?DataRoutes::boolean($q['deep'],'deep'):true;
        $limit=DataRoutes::integer($q['limit']??500,'limit',1,2000);
        $ids=array();
        if(isset($q['contact_ids'])){$rawIds=is_array($q['contact_ids'])?$q['contact_ids']:explode(',',(string)$q['contact_ids']);$ids=DataRoutes::ids($rawIds,'contact_ids');}
        if($scope==='all'){
            $filters=array();foreach(array('search'=>255,'lead_status'=>50,'channel'=>10,'email_state'=>50) as $k=>$max){if(isset($q[$k])){$filters[$k]=DataRoutes::text($q,$k,null,null,$max);}}
            if(isset($q['list_id'])&&$q['list_id']!==''){$filters['list_id']=DataRoutes::integer($q['list_id'],'list_id',1);}
            $args=array();$where=$this->contacts->filters($filters,$args);
            $rows=$this->db->rows('SELECT c.* FROM '.$this->table('contacts').' c WHERE '.$where." AND c.email IS NOT NULL AND c.email<>'' ORDER BY c.id ASC LIMIT %d",array_merge($args,array($limit)));
        }elseif($ids){
            $u=array_values(array_unique($ids));if(count($u)>2000){throw new ValidationFailure('contact_ids cannot exceed 2000 items','contact_ids');}
            $rows=$this->db->rows('SELECT * FROM '.$this->table('contacts').' WHERE id IN ('.implode(',',array_fill(0,count($u),'%d')).") AND email IS NOT NULL AND email<>'' ORDER BY id ASC LIMIT %d",array_merge($u,array($limit)));
        }else{throw new AuthFailure(400,'No contacts selected');}
        $res=$this->validateContactsRows($rows,$deep);$res['matched']=count($rows);
        return $res;
    }
    public function validateListEmails($listId,array $q,array $user){
        if(($user['role']??'')!=='admin'){throw new AuthFailure(403,'Application administrator role required.');}
        $this->db->requireTransaction();
        if(!$this->db->rows('SELECT id FROM '.$this->table('contact_lists').' WHERE id=%d',array($listId))){throw new AuthFailure(404,'List not found');}
        $remove=isset($q['remove_from_list'])?DataRoutes::boolean($q['remove_from_list'],'remove_from_list'):true;
        $delete=isset($q['delete_contacts'])?DataRoutes::boolean($q['delete_contacts'],'delete_contacts'):false;
        $deep=isset($q['deep'])?DataRoutes::boolean($q['deep'],'deep'):true;
        $limit=DataRoutes::integer($q['limit']??200,'limit',1,2000);
        $rows=$this->db->rows('SELECT c.* FROM '.$this->table('contacts').' c JOIN '.$this->table('contact_list_members').' m ON m.contact_id=c.id WHERE m.list_id=%d ORDER BY c.id ASC LIMIT %d',array($listId,$limit));
        $res=$this->validateContactsRows($rows,$deep);
        $removed=0;$deleted=0;
        if(($remove||$delete)&&$rows){
            $ids=array_map(function($r){return (int)$r['id'];},$rows);
            $fresh=$this->db->rows('SELECT id,phone_number,is_undeliverable,is_email_undeliverable FROM '.$this->table('contacts').' WHERE id IN ('.implode(',',array_fill(0,count($ids),'%d')).')',$ids);
            $badIds=array();
            foreach($fresh as $fr){
                $fc=$this->db->decode('contacts',$fr);
                if(!$fc['is_email_undeliverable']){continue;}
                $phoneOk=Compat::strip($fc['phone_number']??'')!==''&&!$fc['is_undeliverable'];
                if(!$phoneOk){$badIds[]=(int)$fc['id'];}
            }
            if($badIds){
                if($delete){$deleted=(new ContactActions($this->db))->deleteContacts($badIds);$removed=$deleted;}
                elseif($remove){
                    $this->db->execute('DELETE FROM '.$this->table('contact_list_members').' WHERE list_id=%d AND contact_id IN ('.implode(',',array_fill(0,count($badIds),'%d')).')',array_merge(array($listId),$badIds));
                    $removed=count($badIds);
                    $cnt=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('contact_list_members').' WHERE list_id=%d',array($listId))[0]['n'];
                    $this->db->update('contact_lists',array('contact_count'=>$cnt,'updated_at'=>$this->records->now()),array('id'=>$listId));
                }
            }
        }
        return array_merge(array('success'=>true,'removed_from_list'=>$removed,'deleted'=>$deleted),$res);
    }
    public static function handle($request,array $c,array $user,&$status){
        $path=trim(substr($request->get_route(),strlen('/sender-suite/v1/')),'/');$method=$request->get_method();
        if($method==='POST'&&strlen($request->get_body())>262144){throw new AuthFailure(413,'Validator request exceeds the size limit.');}
        $s=new self($c['db']);
        if($path==='contacts/enrich/status'&&$method==='GET'){return $s->enrichmentStatus();}
        if($path==='contacts/enrich'&&$method==='POST'){if(($user['role']??'')!=='admin'){throw new AuthFailure(403,'Application administrator role required.');}$raw=$request->get_body();$b=$raw!==''?DataRoutes::body($request):(object)array();return (new CampaignWorker($c['db'],$c['config']??null))->enrichContacts($b);}
        if($path==='contacts/validate-emails'&&$method==='POST'){return $s->validateContactEmails($request->get_query_params(),$user);}
        if(preg_match('~^lists/(-?[0-9]+)/validate-emails$~D',$path,$lm)&&$method==='POST'){return $s->validateListEmails(DataRoutes::integer($lm[1],'list_id',1),$request->get_query_params(),$user);}
        if($path==='validator/status'&&$method==='GET'){return $s->status();}
        if($path==='validator/self-test'&&$method==='POST'){return $s->selfTest();}
        if($path==='validator/selection'&&$method==='POST'){return $s->selection(DataRoutes::body($request));}
        if($path==='validator/batch'&&$method==='POST'){return $s->batch(DataRoutes::body($request),$user);}
        if($path==='validator/jobs'&&$method==='GET'){return $s->jobs();}
        if($path==='validator/jobs'&&$method==='POST'){$status=202;return $s->createJob(DataRoutes::body($request),$user);}
        if(preg_match('~^validator/jobs/([A-Za-z0-9._-]+)(/cancel)?$~D',$path,$m)){
            if(($m[2]??'')==='/cancel'&&$method==='POST'){return $s->cancelJob($m[1],$user);}
            if(($m[2]??'')===''&&$method==='GET'){return $s->job($m[1]);}
        }
        throw new AuthFailure(404,'Not found');
    }
}
