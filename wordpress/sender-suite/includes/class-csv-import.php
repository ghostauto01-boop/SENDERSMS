<?php
namespace SenderSuite;

/** Synchronous native CSV persistence. No URL fetching, enrichment, provider calls or sends. */
final class CsvImport {
    private $db; private $records;
    private const FIELDS=array('first_name','last_name','business_name','phone_number','email','city','state','country','website','industry','source','lead_status','notes');
    public function __construct(Database $db,callable $clock=null) { $this->db=$db; $this->records=new Records($db,$clock); }
    private function table($name) { return Schema::identifier($this->db->schema()->table($name)); }
    private static function data() { static $d; if ($d===null) { $d=json_decode(file_get_contents(dirname(__DIR__).'/schema/csv-import-data.json'),true,512,JSON_THROW_ON_ERROR); } return $d; }
    private static function json($v) { if (is_object($v)) { $parts=array(); foreach ($v as $k=>$x) { $parts[]=self::json((string)$k).': '.self::json($x); } return '{'.implode(', ',$parts).'}'; } if (is_array($v)) { return '['.implode(', ',array_map(array(self::class,'json'),$v)).']'; } if (is_float($v)) { return Templating::text($v); } return json_encode($v,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_UNESCAPED_LINE_TERMINATORS|JSON_THROW_ON_ERROR); }
    private static function text($v) { return Attachments::pythonText($v); }
    private static function truth($v) { return Attachments::truth($v); }
    private static function cut($s,$n) { preg_match('/\A.{0,'.$n.'}/us',$s,$m); return $m[0]??''; }
    public static function tags($raw) { if (!self::truth($raw)) { return array(); } $out=array(); foreach (preg_split('/[;,|]/',self::text($raw)) as $v) { $v=Compat::strip($v); if ($v!=='' && Compat::length($v)<=100 && !in_array($v,$out,true)) { $out[]=$v; } } return $out; }
    public static function emails($raw,$limit=5) { if (!self::truth($raw)) { return array(); } $out=array(); foreach (preg_split('/[;,\s\x{001c}-\x{001f}]+/u',self::text($raw)) as $v) { $v=Compat::email($v); if ($v!==null && !in_array($v,$out,true)) { $out[]=$v; } if (count($out)>=$limit) { break; } } if (!$out) { $v=Compat::email(self::text($raw)); if ($v!==null) { $out[]=$v; } } return $out; }
    public static function domain($website) {
        if (!self::truth($website)) { return ''; } $raw=Compat::strip(self::text($website)); if ($raw==='') { return ''; } if (strpos($raw,'//')===false) { $raw='https://'.$raw; }
        $raw=str_replace(array("\r","\n","\t"),'',$raw); $p=parse_url($raw); if ($p===false) { if (strpos($raw,'[')!==false || strpos($raw,']')!==false || !preg_match('~^(?:[A-Za-z][A-Za-z0-9+.-]*:)?//([^/?#]*)~',$raw,$m)) { return ''; } $p=array('host'=>$m[1]); }
        $host=$p['host']??($p['path']??''); if (isset($p['host']) && isset($p['user'])) { $host=$p['host']; }
        $host=Compat::lower(Compat::strip(explode(':',substr(strrchr('@'.$host,'@'),1))[0])); if (substr($host,0,4)==='www.') { $host=substr($host,4); } return strpos($host,'.')!==false?$host:'';
    }
    public static function country($country,$email,$website) { $explicit=Compat::strip($country??''); if ($explicit!=='') { return self::cut($explicit,100); } $domains=array(); $address=Compat::email($email); if ($address!==null) { $domains[]=substr(strrchr($address,'@'),1); } $site=self::domain($website); if ($site!=='') { $domains[]=$site; } $map=self::data()['countries']; foreach ($domains as $domain) { $parts=explode('.',Compat::lower($domain)); $tld=end($parts); if (isset($map[$tld])) { return $map[$tld]; } } return null; }
    private function find($phone,$email,array $d) {
        if ($phone!==null) { $r=$this->db->one('contacts',array('phone_number'=>$phone)); if ($r) { return array($r,'phone'); } }
        if ($email!==null) {
            $rows=$this->db->rows('SELECT * FROM '.$this->table('contacts').' WHERE LOWER(email)=%s LIMIT 1',array($email)); if ($rows) { return array($this->db->decode('contacts',$rows[0]),'email'); }
            $rows=$this->db->rows('SELECT c.* FROM '.$this->table('contacts').' c JOIN '.$this->table('email_contact_addresses').' a ON a.contact_id=c.id WHERE LOWER(a.email_address)=%s LIMIT 1',array($email)); if ($rows) { return array($this->db->decode('contacts',$rows[0]),'email_alias'); }
        }
        $first=Compat::strip($d['first_name']??''); $last=Compat::strip($d['last_name']??''); $business=Compat::strip($d['business_name']??''); $where=array(); $args=array();
        if ($first!=='' && $last!=='') { $where=array('LOWER(first_name)=%s','LOWER(last_name)=%s'); $args=array(Compat::lower($first),Compat::lower($last)); }
        elseif ($business!=='') { $where=array('LOWER(business_name)=%s'); $args=array(Compat::lower($business)); }
        else { return array(null,''); }
        if ($phone!==null) { $where[]='email IS NOT NULL'; $where[]="(phone_number IS NULL OR phone_number='')"; } else { $where[]="(email IS NULL OR email='')"; }
        $rows=$this->db->rows('SELECT * FROM '.$this->table('contacts').' WHERE '.implode(' AND ',$where).' LIMIT 2',$args); return count($rows)===1?array($this->db->decode('contacts',$rows[0]),'name'):array(null,'');
    }
    private function secondary(array $c,$email) { if ($email===null || $email===Compat::email($c['email'])) { return false; } $r=$this->db->one('email_contact_addresses',array('email_address'=>$email)); if ($r) { return $r['contact_id']===$c['id']; } return $this->db->transaction(function()use($c,$email){$this->records->create('email_contact_addresses',array('contact_id'=>$c['id'],'email_address'=>$email));return true;}); }
    private function merge(array $c,array $d,array $custom,$phone,$email) {
        $updates=array(); $changed=false;
        if ($email!==null && $email!==Compat::email($c['email'])) { if (!self::truth($c['email'])) { $updates['email']=$email; $updates['email_lower']=$email; $updates['email_source']=self::truth($c['email_source'])?$c['email_source']:'csv'; $changed=true; } else { try { $changed=$this->secondary($c,$email)||$changed; } catch (DatabaseFailure $e) { /* Source secondary-address bookkeeping is best effort. */ } } }
        if ($phone!==null && !self::truth($c['phone_number'])) { $updates['phone_number']=$phone; $changed=true; }
        foreach ($d as $k=>$v) { if (in_array($k,array('phone_number','email','email_lower'),true) || $v===null || $v==='' || $v===array()) { continue; } if (($c[$k]??null)===null || $c[$k]==='' || $c[$k]===array()) { $updates[$k]=$v; $changed=true; } }
        try { $parsed=json_decode($c['custom_fields']??'{}',false,512,JSON_THROW_ON_ERROR); } catch (\Throwable $e) { $parsed=null; } if (!is_object($parsed)) { $parsed=new \stdClass(); }
        foreach ($custom as $k=>$v) { if ($v===null || $v==='' || self::truth($parsed->$k??null)) { continue; } $parsed->$k=$v; $changed=true; }
        if (get_object_vars($parsed)) { $raw=self::json($parsed); if ($raw!==($c['custom_fields']??null)) { $updates['custom_fields']=$raw; } }
        if ($changed) { $updates['updated_at']=$this->records->now(); } if ($updates) { $this->db->update('contacts',$updates,array('id'=>$c['id'])); }
        return array($this->db->one('contacts',array('id'=>$c['id'])),$changed);
    }
    private function tag($name,array &$cache) { $key=Compat::lower($name); if (!isset($cache[$key])) { $cache[$key]=$this->records->create('tags',array('name'=>$name))['id']; } return $cache[$key]; }
    private static function note(array &$r,array $c) { $p=self::truth($c['phone_number']); $e=self::truth($c['email']); if ($p) { $r['with_phone']++; } if ($e) { $r['with_email']++; } if ($p || $e) { $r[$p && $e?'both':($e?'email_only':'phone_only')]++; } }
    public function import($bytes,array $options=array()) {
        $listId=$options['list_id']??null; $target=null;
        if ($listId!==null) { $target=$this->db->one('contact_lists',array('id'=>$listId)); if (!$target) { throw new AuthFailure(404,'List not found'); } }
        elseif (Compat::strip($options['new_list_name']??'')!=='') { $target=$this->records->create('contact_lists',array('name'=>self::cut(Compat::strip($options['new_list_name']),255))); $listId=$target['id']; }
        $reader=CsvReader::dictionaries($bytes); $reader->rewind(); if (!$reader->valid() || !$reader->current()['headers']) { throw new AuthFailure(400,'No headers found in CSV'); }
        $map=CsvPreview::mapping($reader->current()['headers']);
        foreach (array('column_mapping'=>new \stdClass(),'auto_tag_rules'=>array()) as $key=>$default) {
            $raw=$options[$key]??null; if ($raw===null || $raw==='') { $decoded=$default; } else { try { $decoded=json_decode($raw,false,512,JSON_THROW_ON_ERROR); } catch (\Throwable $e) { throw new AuthFailure(422,'Invalid JSON in import options'); } }
            if ($key==='column_mapping') { if (!is_object($decoded)) { throw new AuthFailure(422,'column_mapping must be a JSON object'); } foreach ($decoded as $header=>$field) { $header=Compat::lower(Compat::strip((string)$header)); $map->$header=self::truth($field)?$field:'ignore'; } }
            else { if (!is_array($decoded)) { throw new AuthFailure(422,'auto_tag_rules must be a JSON array'); } $rules=$decoded; }
        }
        $result=$this->run($bytes,$map,$listId,$options['skip_duplicates']??true,self::tags($options['tags']??''),$rules); $discovered=0;
        try { $stats=$this->db->transaction(function(){return (new Variables($this->db,function(){return $this->records->now();}))->sync();}); $discovered=$stats['discovered']; } catch (\Throwable $e) { /* Original registry refresh is best effort; keep the completed import. */ }
        $result['errors']=array_slice($result['errors'],0,50); $result['new_variables']=$discovered; $result['list']=null;
        if ($target!==null) { $target=$this->db->one('contact_lists',array('id'=>$listId)); $result['list']=array_intersect_key($target,array_flip(array('id','name','contact_count'))); }
        return $result;
    }
    public function run($bytes,$mapping,$listId=null,$skip=true,array $tags=array(),array $rules=array()) {
        $r=array_fill_keys(array('imported','merged','merged_by_name','skipped','invalid','duplicates','total_rows','company_duplicates','with_phone','with_email','email_only','phone_only','both'),0); $r['company_duplicate_candidates']=array(); $r['errors']=array(); $r['imported_ids']=array();
        $reader=CsvReader::dictionaries($bytes); $reader->rewind(); if (!$reader->valid() || !$reader->current()['headers']) { $r['errors'][]=array('row'=>0,'error'=>'No headers found'); return $r; }
        $map=array(); foreach ($mapping as $key=>$v) { $map[Compat::lower(Compat::strip((string)$key))]=Compat::strip(self::text($v)); }
        $list=$listId!==null?$this->db->one('contact_lists',array('id'=>$listId)):null; $global=self::tags(implode(', ',$tags)); $cache=array(); foreach ($this->db->select('tags',array(),2147483647) as $t) { $cache[Compat::lower($t['name'])]=$t['id']; }
        $companies=array(); $free=self::data()['free_mail_domains']; foreach ($this->db->rows('SELECT id,email,website,business_name FROM '.$this->table('contacts')) as $c) { $c['id']=(int)$c['id']; $domain=self::domain($c['website']); $address=Compat::email($c['email']); if ($domain==='' && $address!==null) { $candidate=substr(strrchr($address,'@'),1); if (!in_array($candidate,$free,true)) { $domain=$candidate; } } if ($domain!=='') { $companies[Compat::lower($domain)][]=$c['id']; } if (self::truth($c['business_name'])) { $companies['name:'.Compat::lower(Compat::strip($c['business_name']))][]=$c['id']; } }
        $seen=array(); $added=0; $rowNum=0; $reader->next();
        while ($reader->valid()) {
            $rowNum++; $r['total_rows']++; $lower=array(); foreach ($reader->current()['row'] as $k=>$v) { $lower[Compat::lower(Compat::strip((string)$k))]=$v; } $d=array(); $custom=array(); $rowTags=array();
            foreach ($map as $key=>$target) { $v=$lower[$key]??''; if (!self::truth($v)) { $v=''; } if (!is_string($v)) { throw new \InvalidArgumentException('CSV cell is not scalar text'); } $v=Compat::strip($v); if ($v==='' || $target==='' || $target==='ignore') { continue; }
                if (in_array($target,self::FIELDS,true)) { if ($target==='email') { $addresses=self::emails($v); if ($addresses) { $d['email']=$addresses[0]; if (count($addresses)>1) { $custom['additional_emails']=implode(', ',array_slice($addresses,1)); } } } else { $d[$target]=$v; } }
                elseif ($target==='tags') { $rowTags=array_merge($rowTags,self::tags($v)); }
                elseif (substr($target,0,7)==='custom:') { $k=CsvPreview::customKey(substr($target,7)); if (!in_array($k,self::FIELDS,true)) { $custom[$k]=$v; } }
            }
            foreach ($rules as $rule) { if (!is_object($rule)) { continue; } $field=Compat::strip(self::text(self::truth($rule->field??null)?$rule->field:'')); $contains=Compat::strip(self::text(self::truth($rule->contains??null)?$rule->contains:'')); $name=self::cut(Compat::strip(self::text(self::truth($rule->tag??null)?$rule->tag:'')),100); $v=$d[$field]??null; if (!self::truth($v)) { $v=$custom[CsvPreview::customKey($field)]??null; } if (!self::truth($v)) { $v=$lower[Compat::lower($field)]??''; } if ($field!=='' && $contains!=='' && $name!=='' && strpos(Compat::lower(self::text($v)),Compat::lower($contains))!==false) { $rowTags[]=$name; } }
            $phone=Phone::clean($d['phone_number']??null); $email=Compat::email($d['email']??null); $raw=Compat::strip($d['phone_number']??'');
            if ($raw!=='' && $phone===null) { $r['errors'][]=array('row'=>$rowNum,'error'=>'Phone number skipped (not a valid Nigerian mobile): '.$raw,'level'=>'warning'); }
            if ($phone===null && $email===null) { $r['invalid']++; $r['errors'][]=array('row'=>$rowNum,'error'=>'No phone number and no email address'.($raw!==''?" (phone '".$raw."' is not valid)":'')); $reader->next(); continue; }
            $key=$phone!==null?'p:'.$phone:'e:'.$email; $companyKeys=array(); $domain=self::domain($d['website']??null); $emailDomain=$email!==null?substr(strrchr($email,'@'),1):''; if ($domain!=='') { $companyKeys[]=array(Compat::lower($domain),'domain'); } elseif ($emailDomain!=='' && !in_array($emailDomain,$free,true)) { $companyKeys[]=array(Compat::lower($emailDomain),'domain'); } $business=Compat::lower(Compat::strip($d['business_name']??'')); if ($business!=='') { $companyKeys[]=array('name:'.$business,'company_name'); }
            if ($skip) {
                if (isset($seen[$key])) { $r['duplicates']++; $reader->next(); continue; }
                list($existing,$matchedBy)=$this->find($phone,$email,$d);
                if ($existing!==null) {
                    $seen[$key]=true; if ($matchedBy==='name') { $r['merged_by_name']++; $r['errors'][]=array('row'=>$rowNum,'level'=>'info','error'=>'Merged into existing contact #'.$existing['id'].' by name match'); }
                    list($contact,$changed)=$this->merge($existing,$d,$custom,$phone,$email); $r[$changed?'merged':'duplicates']++; self::note($r,$contact);
                    if ($list!==null && !$this->db->one('contact_list_members',array('list_id'=>$listId,'contact_id'=>$contact['id']))) { $this->records->create('contact_list_members',array('list_id'=>$listId,'contact_id'=>$contact['id'])); $added++; }
                    foreach (array_merge($global,$rowTags) as $name) { $tag=$this->tag($name,$cache); if (!$this->db->one('contact_tags',array('contact_id'=>$contact['id'],'tag_id'=>$tag))) { $this->records->create('contact_tags',array('contact_id'=>$contact['id'],'tag_id'=>$tag)); } }
                    $reader->next(); continue;
                }
            }
            $pairs=array(); foreach ($companyKeys as $entry) { foreach ($companies[$entry[0]]??array() as $id) { $pairs[]=array('row'=>$rowNum,'contact_id'=>$id,'matched_by'=>$entry[1],'company_key'=>$entry[0]); } } if ($pairs) { $r['company_duplicates']++; foreach (array_slice($pairs,0,5) as $pair) { if (count($r['company_duplicate_candidates'])<1000) { $r['company_duplicate_candidates'][]=$pair; } } }
            $seen[$key]=true; if ($phone!==null) { $d['phone_number']=$phone; } else { unset($d['phone_number']); } if ($email!==null) { $d['email']=$email; } $d['country']=self::country($d['country']??null,$email,$d['website']??null); if (!array_key_exists('lead_status',$d)) { $d['lead_status']='new'; } if ($email!==null) { $d['email_lower']=$email; $d['email_source']='csv'; } if ($custom) { $d['custom_fields']=self::json((object)$custom); }
            $contact=$this->records->create('contacts',$d); $r['imported']++; $r['imported_ids'][]=$contact['id']; self::note($r,$contact); $applied=array(); foreach (array_merge($global,$rowTags) as $name) { $k=Compat::lower($name); if (isset($applied[$k])) { continue; } $applied[$k]=true; $this->records->create('contact_tags',array('contact_id'=>$contact['id'],'tag_id'=>$this->tag($name,$cache))); }
            foreach ($companyKeys as $entry) { $companies[$entry[0]][]=$contact['id']; } if ($list!==null) { $this->records->create('contact_list_members',array('list_id'=>$listId,'contact_id'=>$contact['id'])); $added++; } $reader->next();
        }
        if ($list!==null && $added) { $this->db->update('contact_lists',array('contact_count'=>($list['contact_count']??0)+$added,'updated_at'=>$this->records->now()),array('id'=>$listId)); }
        return $r;
    }
}
