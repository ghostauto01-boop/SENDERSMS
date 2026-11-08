<?php
namespace SenderSuite;

/** Authenticated contact data projection with the source's bounded one-contact registry refresh. */
final class ContactProfile {
    private $db;
    private $variables;
    public function __construct(Database $db,callable $clock=null) { $this->db=$db;$this->variables=new Variables($db,$clock); }
    private function table($n) { return Schema::identifier($this->db->schema()->table($n)); }
    public static function definitions() { return array('/variables/contact/(?P<contact_id>-?[0-9]+)/profile'=>array('GET')); }
    private function registryBudget() {
        $r=$this->db->rows('SELECT COUNT(*) AS n,COALESCE(SUM(COALESCE(OCTET_LENGTH(label),0)+COALESCE(OCTET_LENGTH(field_key),0)+COALESCE(OCTET_LENGTH(shortcode),0)+COALESCE(OCTET_LENGTH(description),0)+COALESCE(OCTET_LENGTH(sample_value),0)+COALESCE(OCTET_LENGTH(fallback_text),0)+256),0) AS bytes FROM '.$this->table('contact_variables'))[0];
        if((int)$r['n']>10000||(int)$r['bytes']>2097152) { throw new AuthFailure(413,'Variable registry is too large for the contact profile.'); }
    }
    private function custom($raw) {
        try { $plain=json_decode($raw??'{}',false,32,JSON_THROW_ON_ERROR);$big=json_decode($raw??'{}',false,32,JSON_THROW_ON_ERROR|JSON_BIGINT_AS_STRING); }
        catch(\JsonException $e) { if($e->getCode()===JSON_ERROR_DEPTH) { throw new AuthFailure(413,'Contact custom data is too deeply nested.'); }return new \stdClass(); }
        $nodes=0;$check=function($a,$b)use(&$check,&$nodes) {
            if(++$nodes>10000) { throw new AuthFailure(413,'Contact custom data has too many values.'); }
            if(is_float($a)&&(!is_finite($a)||is_string($b))) { throw new AuthFailure(413,'Contact profile numeric values exceed the supported range.'); }
            if(is_array($a)||is_object($a)) { foreach($a as $key=>$v) { $check($v,is_array($b)?$b[$key]:$b->$key); } }
        };$check($plain,$big);
        if(!is_object($plain)) { return new \stdClass(); }
        if(count(get_object_vars($plain))>1000) { throw new AuthFailure(413,'Too many contact profile fields.'); }
        return $plain;
    }
    private function contactBudget($id=null) {
        $rows=$this->db->rows('SELECT id,COALESCE(OCTET_LENGTH(custom_fields),0)+COALESCE(OCTET_LENGTH(notes),0) AS bytes FROM '.$this->table('contacts').($id===null?' ORDER BY id ASC LIMIT 1':' WHERE id=%d'),$id===null?array():array($id));
        if($id!==null&&!$rows) { throw new AuthFailure(404,'Contact not found'); }
        if($rows&&(int)$rows[0]['bytes']>1048576) { throw new AuthFailure(413,'Contact data is too large for a profile response.'); }
        if($rows) {
            $raw=$this->db->rows('SELECT custom_fields FROM '.$this->table('contacts').' WHERE id=%d',array((int)$rows[0]['id']))[0]['custom_fields'];
            $this->custom($raw);
        }
    }
    public function read($id) {
        $this->contactBudget($id);$this->contactBudget();$this->registryBudget();
        $contact=$this->db->one('contacts',array('id'=>$id));if(!$contact) { throw new AuthFailure(404,'Contact not found'); }
        $custom=$this->custom($contact['custom_fields']);
        // Preserve the source: sync scans the first contact, not necessarily this requested contact.
        $this->variables->sync(1);$this->registryBudget();
        $byKey=array();foreach($this->db->select('contact_variables',array(),10000) as $r) { $byKey[$r['field_key']]=$r; }
        $entry=function($key,$value,$source)use($byKey) {
            $v=$byKey[$key]??null;
            return array('field_key'=>$key,'label'=>$v?$v['label']:Variables::humanize($key),'shortcode'=>$v?$v['shortcode']:$key,'value'=>$value===null?'':Attachments::pythonText($value),'source'=>$source,'is_active'=>$v?$v['is_active']:true);
        };
        $fields=array();foreach(array('first_name','last_name','business_name','phone_number','email','city','state','country','website','industry','source','lead_status') as $key) { $fields[]=$entry($key,$contact[$key]??null,'standard'); }
        foreach($custom as $key=>$value) { $fields[]=$entry(Templating::key((string)$key),$value,'imported'); }
        $parts=array();foreach(array('first_name','last_name') as $key) { if($contact[$key]!==null&&$contact[$key]!=='') { $parts[]=$contact[$key]; } }
        $name=implode(' ',$parts);if($name==='') { $name=$contact['business_name']?:$contact['phone_number']; }
        $tags=(new Contacts($this->db))->tagMap(array($id));
        $out=array('contact_id'=>$id,'display_name'=>$name,'phone_number'=>$contact['phone_number'],'lead_status'=>$contact['lead_status'],'is_opted_out'=>$contact['is_opted_out'],'notes'=>$contact['notes'],'tags'=>$tags[$id]??array(),'messages_sent'=>$contact['messages_sent'],'messages_received'=>$contact['messages_received'],'last_contacted_at'=>Records::iso($contact['last_contacted_at']),'last_reply_at'=>Records::iso($contact['last_reply_at']),'created_at'=>Records::iso($contact['created_at']),'fields'=>$fields);
        if(strlen(json_encode($out,JSON_THROW_ON_ERROR))>8388608) { throw new AuthFailure(413,'Serialized contact profile is too large.'); }
        return $out;
    }
}
