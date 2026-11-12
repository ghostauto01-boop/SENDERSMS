<?php
namespace SenderSuite;

/** Explicit contact lifecycle operations. All mutations require a surrounding transaction. */
final class ContactActions {
    private $db;private $records;private $contacts;private $lists;
    public function __construct(Database $db,callable $clock=null){$this->db=$db;$this->records=new Records($db,$clock);$this->contacts=new Contacts($db,$clock);$this->lists=new ContactLists($db,$clock);}
    private function table($name){return Schema::identifier($this->db->schema()->table($name));}
    private static function marks(array $ids){return implode(',',array_fill(0,count($ids),'%d'));}
    private function matching(array $filters){$args=array();$where=$this->contacts->filters($filters,$args);return array_map('intval',array_column($this->db->rows('SELECT c.id FROM '.$this->table('contacts').' c WHERE '.$where.' ORDER BY c.id ASC',$args),'id'));}
    private function syncLists(array $ids){foreach(array_unique($ids) as $id){$n=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('contact_list_members').' WHERE list_id=%d',array($id))[0]['n'];$this->db->update('contact_lists',array('contact_count'=>$n,'updated_at'=>$this->records->now()),array('id'=>(int)$id));}}
    /** No missing-table suppression: partial schemas must fail and roll back, not silently orphan data. */
    public function deleteContacts(array $ids){
        $ids=array_values(array_unique(array_map('intval',$ids)));sort($ids,SORT_NUMERIC);if(!$ids){return 0;}
        $all=array_fill_keys($ids,true);$lists=array();$meetings=array();$deleted=0;
        // Preflight inconsistent legacy conversations before any write. Never cascade away another person's mail.
        foreach(array_chunk($ids,400) as $chunk){
            $p=self::marks($chunk);
            if($this->db->rows('SELECT id FROM '.$this->table('messages')." WHERE contact_id IN (".$p.") AND direction='outgoing' AND status IN ('sending','dispatching','unknown') LIMIT 1",$chunk)){throw new AuthFailure(409,'Reconcile in-flight or uncertain messages before deleting their contact.');}
            $rows=$this->db->rows('SELECT m.contact_id FROM '.$this->table('messages').' m JOIN '.$this->table('conversations').' v ON v.id=m.conversation_id WHERE v.contact_id IN ('.$p.') AND m.contact_id<>v.contact_id',$chunk);
            foreach($rows as $r){if(!isset($all[(int)$r['contact_id']])){throw new AuthFailure(409,'Conversation contains another contact\'s messages. Repair its ownership before deletion.');}}
            $rows=$this->db->rows('SELECT v.contact_id FROM '.$this->table('messages').' m JOIN '.$this->table('conversations').' v ON v.id=m.conversation_id WHERE m.contact_id IN ('.$p.') AND m.contact_id<>v.contact_id',$chunk);
            foreach($rows as $r){if(!isset($all[(int)$r['contact_id']])){throw new AuthFailure(409,'Message belongs to another contact\'s conversation. Repair its ownership before deletion.');}}
            foreach($this->db->rows('SELECT DISTINCT list_id FROM '.$this->table('contact_list_members').' WHERE contact_id IN ('.$p.')',$chunk) as $r){$lists[]=(int)$r['list_id'];}
            foreach($this->db->rows('SELECT id FROM '.$this->table('meetings').' WHERE contact_id IN ('.$p.')',$chunk) as $r){$meetings[]=(int)$r['id'];}
        }
        // Remove links for the complete selection first, including selections spanning several batches.
        foreach(array_chunk($ids,400) as $chunk){$this->db->execute('DELETE FROM '.$this->table('meeting_attendees').' WHERE contact_id IN ('.self::marks($chunk).')',$chunk);}
        foreach(array_chunk($ids,400) as $chunk){
            $p=self::marks($chunk);
            $conversationIds='SELECT id FROM '.$this->table('conversations').' WHERE contact_id IN ('.$p.')';
            $messageIds='SELECT id FROM '.$this->table('messages').' WHERE contact_id IN ('.$p.')';
            // FK references to messages must be cleared BEFORE messages, not in the original unsafe order.
            foreach(array('followups','scheduled_messages','email_send_log','email_events') as $t){$this->db->execute('UPDATE '.$this->table($t).' SET message_id=NULL WHERE message_id IN ('.$messageIds.')',$chunk);}
            foreach(array('meetings','mailbox_messages') as $t){$this->db->execute('UPDATE '.$this->table($t).' SET conversation_id=NULL WHERE conversation_id IN ('.$conversationIds.')',$chunk);}
            // Keep invoices, call/mail history, attribution and suppression tombstones; detach deleted identities.
            foreach(array('meetings','mailbox_messages','call_logs','invoices','ads_events','email_events','email_send_log','email_suppressions','suppression_entries') as $t){$this->db->execute('UPDATE '.$this->table($t).' SET contact_id=NULL WHERE contact_id IN ('.$p.')',$chunk);}
            foreach(array('scheduled_messages','followups','campaign_followup_logs','campaign_contacts','ads_followup_tasks','ads_calendar_events','ads_assignments','contact_tags','contact_list_members','email_contact_addresses','messages') as $t){$this->db->execute('DELETE FROM '.$this->table($t).' WHERE contact_id IN ('.$p.')',$chunk);}
        }
        // All selected messages have now gone, even for cross-batch legacy conversations.
        foreach(array_chunk($ids,400) as $chunk){$p=self::marks($chunk);$this->db->execute('DELETE FROM '.$this->table('conversations').' WHERE contact_id IN ('.$p.')',$chunk);$deleted+=$this->db->execute('DELETE FROM '.$this->table('contacts').' WHERE id IN ('.$p.')',$chunk);}
        foreach(array_chunk(array_unique($meetings),400) as $chunk){$this->db->execute('DELETE FROM '.$this->table('meetings').' WHERE id IN ('.self::marks($chunk).') AND contact_id IS NULL AND NOT EXISTS (SELECT 1 FROM '.$this->table('meeting_attendees').' a WHERE a.meeting_id='.$this->table('meetings').'.id)',$chunk);}
        $this->syncLists($lists);return $deleted;
    }
    public function deleteOne($id){$this->contacts->find($id);$this->deleteContacts(array($id));return null;}
    public function deleteList($id,$deleteContacts=false){
        $this->lists->find($id);
        $this->db->delete('scheduled_messages',array('list_id'=>$id));
        $this->db->update('campaigns',array('list_id'=>null),array('list_id'=>$id));
        if($deleteContacts){$ids=array_map('intval',array_column($this->db->rows('SELECT contact_id FROM '.$this->table('contact_list_members').' WHERE list_id=%d',array($id)),'contact_id'));$this->deleteContacts($ids);}
        $this->db->delete('contact_list_members',array('list_id'=>$id));$this->db->delete('contact_lists',array('id'=>$id));return null;
    }
    public function deleteMembers($id,array $ids,$all=false,$search=null){
        $this->lists->find($id);$filters=array('list_id'=>$id);if($all){$filters['search']=$search;$ids=$this->matching($filters);}else{
            $found=array();foreach(array_chunk(array_unique($ids),400) as $chunk){foreach($this->db->rows('SELECT contact_id FROM '.$this->table('contact_list_members').' WHERE list_id=%d AND contact_id IN ('.self::marks($chunk).')',array_merge(array($id),$chunk)) as $r){$found[]=(int)$r['contact_id'];}}$ids=$found;
        }
        $deleted=$this->deleteContacts($ids);$this->syncLists(array($id));$list=$this->lists->find($id);return array('success'=>true,'deleted'=>$deleted,'contact_count'=>$list['contact_count']);
    }
    public static function bulkInput($body){
        if(!is_object($body)){throw new ValidationFailure('Input should be a valid dictionary or object to extract fields from');}
        $d=get_object_vars($body);if(!array_key_exists('action',$d)){throw new ValidationFailure('Field required','action');}
        foreach(array('action','scope','value','search','lead_status','tag','channel','email_state') as $key){if(array_key_exists($key,$d)&&$d[$key]!==null&&!is_string($d[$key])){throw new ValidationFailure('Input should be a valid string',$key);}}
        if(!is_string($d['action'])){throw new ValidationFailure('Input should be a valid string','action');}
        if(array_key_exists('scope',$d)&&$d['scope']===null){throw new ValidationFailure('Input should be a valid string','scope');}$d['scope']=$d['scope']??'ids';if(!in_array($d['scope'],array('ids','all'),true)){throw new ValidationFailure("Input should be 'ids' or 'all'",'scope');}
        if(!in_array($d['action'],array('delete','status','tag'),true)){throw new AuthFailure(400,'Unsupported bulk action');}
        $d['contact_ids']=DataRoutes::ids($d['contact_ids']??array());
        if(property_exists($body,'contact_ids')&&$body->contact_ids===null){throw new ValidationFailure('Input should be a valid list','contact_ids');}
        if(isset($d['list_id'])){$d['list_id']=DataRoutes::integer($d['list_id'],'list_id');}
        return $d;
    }
    public function bulk($body){
        $d=self::bulkInput($body);
        if($d['scope']==='all'){
            if($d['action']!=='delete'){throw new AuthFailure(400,"scope='all' is only supported for action='delete'");}
            $filters=array_intersect_key($d,array_flip(array('search','lead_status','tag','channel','email_state','list_id')));
            $n=$this->deleteContacts($this->matching($filters));return array('success'=>true,'affected'=>$n,'scope'=>'all');
        }
        $ids=array_values(array_unique($d['contact_ids']));$found=array();foreach(array_chunk($ids,400) as $chunk){$found=array_merge($found,array_map('intval',array_column($this->db->rows('SELECT id FROM '.$this->table('contacts').' WHERE id IN ('.self::marks($chunk).')',$chunk),'id')));}
        if($d['action']==='delete'){$this->deleteContacts($found);}elseif($found){
            $value=$d['value']??null;
            if($d['action']==='status'){
                $value=$value===null||$value===''?'new':$value;
                if(Compat::length($value)>50){throw new ValidationFailure('String should have at most 50 characters','value');}
                foreach($found as $id){$this->db->update('contacts',array('lead_status'=>$value,'updated_at'=>$this->records->now()),array('id'=>$id));}
            }else{
                if($value===null||Compat::strip($value)===''){throw new AuthFailure(400,'A bulk tag needs a nonblank name');}
                if(Compat::length($value)>100){throw new ValidationFailure('String should have at most 100 characters','value');}
                $tag=$this->db->one('tags',array('name'=>$value));if(!$tag){$tag=$this->records->create('tags',array('name'=>$value));}
                foreach($found as $id){if(!$this->db->one('contact_tags',array('contact_id'=>$id,'tag_id'=>$tag['id']))){$this->records->create('contact_tags',array('contact_id'=>$id,'tag_id'=>$tag['id']));}}
            }
        }
        return array('success'=>true,'affected'=>count($found));
    }
    public function smsOptIn($id,$body){
        if(!is_object($body)){throw new ValidationFailure('Input should be a valid dictionary');}
        if(($body->consent_confirmed??null)!==true){throw new AuthFailure(400,'Confirm that the contact has explicitly consented before restoring SMS');}
        $c=$this->contacts->find($id);if(!$c['phone_number']){throw new AuthFailure(400,'Contact has no phone number');}
        $this->db->delete('suppression_entries',array('phone_number'=>$c['phone_number']));
        $this->db->update('contacts',array('is_opted_out'=>false,'opted_out_at'=>null,'opt_out_reason'=>null,'has_consented'=>true,'consent_status'=>'opted_in','updated_at'=>$this->records->now()),array('id'=>$id));
        return array('success'=>true,'is_opted_out'=>false,'consent_status'=>'opted_in');
    }
    public function emailPermission($id,$optIn,$reason=null){
        $c=$this->contacts->find($id);$email=Compat::email($c['email']);$status=$c['email_status'];
        if($optIn){
            if($email){$this->db->delete('email_suppressions',array('email_address'=>$email));}
            if(in_array($status,array('unsubscribed','complained'),true)){$status='active';}
            $data=array('is_email_opted_out'=>false,'email_opted_out_at'=>null,'email_opt_out_reason'=>null,'email_status'=>$status);
        }else{
            $status='unsubscribed';$reason=$reason===null||$reason===''?'Manual unsubscribe':$reason;
            $data=array('is_email_opted_out'=>true,'email_opted_out_at'=>$this->records->now(),'email_opt_out_reason'=>self::cut($reason,500),'email_status'=>$status);
            if($email&&!$this->db->one('email_suppressions',array('email_address'=>$email))){$this->records->create('email_suppressions',array('email_address'=>$email,'contact_id'=>$id,'reason'=>self::cut($reason,500),'source'=>'manual'));}
        }
        $data['updated_at']=$this->records->now();$this->db->update('contacts',$data,array('id'=>$id));return array('success'=>true,'is_email_opted_out'=>!$optIn,'email_status'=>$status);
    }
    private static function cut($s,$n){return implode('',array_map(array(Compat::class,'chr'),array_slice(Compat::points($s),0,$n)));}
    public function activity($id,$page=1,$perPage=20){
        $rows=$this->db->rows('SELECT id,direction,body,status,created_at FROM '.$this->table('messages').' WHERE contact_id=%d ORDER BY created_at DESC LIMIT %d OFFSET %d',array($id,$perPage,($page-1)*$perPage));$out=array();
        foreach($rows as $r){$r['id']=(int)$r['id'];$r['body']=self::cut($r['body'],200);$r['created_at']=Records::iso($r['created_at']);$out[]=$r;}return array('total'=>count($out),'items'=>$out);
    }
    public function quarantine($dryRun=true){
        $tags=array('pattern_generated','sendable:false');$rows=$this->db->rows('SELECT id,email FROM '.$this->table('contacts')." WHERE email_source='inferred' AND email IS NOT NULL ORDER BY id ASC");$ids=array_map('intval',array_column($rows,'id'));$map=$this->contacts->tagMap($ids);$already=0;$tagged=0;
        foreach($ids as $id){$current=$map[$id]??array();if(count(array_intersect($tags,$current))===2){$already++;}elseif(!$dryRun){$this->contacts->setTags($id,$tags,'add');$tagged++;}}
        $roles=explode(',', 'info,sales,contact,hello,hi,admin,support,office,enquiries,enquiry,mail,team,hr,accounts,accounting,billing,marketing,service,customerservice,help,careers,jobs,press,media,webmaster,postmaster,noreply,no-reply,reception');$roleCount=0;
        foreach($this->db->rows('SELECT email FROM '.$this->table('contacts').' WHERE email IS NOT NULL AND email_verified=0') as $row){$local=Compat::lower(Compat::strip(explode('@',$row['email'])[0]));if(in_array($local,$roles,true)){$roleCount++;}}
        return array('dry_run'=>$dryRun,'matched'=>count($ids),'tagged'=>$tagged,'already_tagged'=>$already,'tags'=>$tags,'role_mailboxes_unverified'=>$roleCount,'examples'=>array_slice(array_column($rows,'email'),0,5),'note'=>$dryRun?'Dry run: nothing was changed. Repeat with dry_run=false to tag them.':'Tagged; no contact was deleted. Legacy tags do not block sending. Review and suppress unwanted addresses on the Validator page.');
    }
    public function clean($listId=null,$remove=false,$delete=false){
        if($listId!==null){$this->lists->find($listId);}
        $ids=$this->matching($listId===null?array():array('list_id'=>$listId));$counts=array('scanned'=>count($ids),'sendable'=>0,'invalid_format'=>0,'previous_failures'=>0,'already_undeliverable'=>0,'no_phone_number'=>0,'quarantined'=>0,'by_reason'=>array(),'removed_from_list'=>0,'deleted'=>0);$act=array();
        $updates=array();foreach(array_chunk($ids,400) as $chunk){
        $rows=$this->db->rows('SELECT c.*,EXISTS (SELECT 1 FROM '.$this->table('suppression_entries').' s WHERE s.phone_number=c.phone_number) AS __ss_suppressed,EXISTS (SELECT 1 FROM '.$this->table('messages')." m WHERE m.contact_id=c.id AND m.direction='outgoing' AND m.status IN ('failed','cancelled')) AS __ss_failed FROM ".$this->table('contacts').' c WHERE c.id IN ('.self::marks($chunk).') ORDER BY c.id ASC',$chunk);
        foreach($rows as $raw){
            $suppressed=(bool)$raw['__ss_suppressed'];$failed=(bool)$raw['__ss_failed'];$c=$this->db->decode('contacts',$raw);$id=$c['id'];$phone=$c['phone_number']??'';$reason=null;$field=null;
            if(Compat::strip($phone)===''){$counts['no_phone_number']++;$counts['by_reason']['no_phone_number']=($counts['by_reason']['no_phone_number']??0)+1;continue;}
            if($c['is_undeliverable']){$field='already_undeliverable';$reason='already_undeliverable';}
            else{
                $r=NumberFilter::classify($phone);
                if(!$r['sendable']){$field='invalid_format';$reason=$r['reason']==='not_mobile'?'not_nigerian_mobile':$r['reason'];}
                elseif(NumberFilter::soft($phone)!==null){$field='invalid_format';$reason=NumberFilter::soft($phone);}
                elseif($c['is_opted_out']||$suppressed){continue;}
                elseif($failed){$field='previous_failures';$reason='previous_delivery_failed';}
                else{$counts['sendable']++;continue;}
            }
            $counts[$field]++;$counts['by_reason'][$reason]=($counts['by_reason'][$reason]??0)+1;$act[]=$id;
            if($field!=='already_undeliverable'){$updates[$reason][]=$id;}
        }}
        foreach($updates as $reason=>$group){foreach(array_chunk($group,400) as $chunk){$this->db->execute('UPDATE '.$this->table('contacts').' SET is_undeliverable=1,undeliverable_reason=%s,delivery_fail_count=COALESCE(delivery_fail_count,0)+1,updated_at=%s WHERE id IN ('.self::marks($chunk).')',array_merge(array($reason,$this->records->now()),$chunk));}}
        if($delete&&$act){$counts['deleted']=$this->deleteContacts($act);}elseif($remove&&$listId!==null&&$act){$r=$this->lists->remove($listId,$act);$counts['removed_from_list']=$r['removed'];}
        $counts['quarantined']=$counts['invalid_format']+$counts['previous_failures'];if(!$counts['by_reason']){$counts['by_reason']=new \stdClass();}return array_merge(array('success'=>true),$counts);
    }
}
