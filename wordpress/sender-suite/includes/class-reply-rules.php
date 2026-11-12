<?php
namespace SenderSuite;

/** Native rule persistence and non-sending evaluation. Inbound execution is not enabled here. */
final class ReplyRules {
    private $db; private $records;
    public function __construct(Database $db,callable $clock=null) { $this->db=$db; $this->records=new Records($db,$clock); }
    private static function table($automation) { return $automation?'automations':'auto_reply_rules'; }
    public static function input($body,$automation=false,$update=false) {
        if (!is_object($body)) { throw new ValidationFailure('Input should be a valid dictionary or object to extract fields from'); }
        $defaults=$automation?array('name'=>null,'description'=>null,'is_enabled'=>true,'priority'=>100,'trigger_type'=>'inbound_reply','channel'=>'sms','email_account_id'=>null,'conditions'=>array(),'match_all'=>true,'actions'=>array()):array('name'=>null,'channel'=>'sms','subject'=>null,'email_account_id'=>null,'keywords'=>null,'match_type'=>'contains','reply_body'=>null,'is_enabled'=>true,'priority'=>100,'cooldown_minutes'=>240,'stop_on_match'=>true);
        $out=$update?array():$defaults;
        foreach ($defaults as $key=>$default) {
            if (!property_exists($body,$key)) { if (!$update && in_array($key,$automation?array('name'):array('name','reply_body'),true)) { throw new ValidationFailure('Field required',$key); } continue; }
            $v=$body->$key;
            if ($v===null && ($update || in_array($key,array('description','subject','email_account_id','keywords'),true))) { $out[$key]=null; continue; }
            if (in_array($key,array('is_enabled','match_all','stop_on_match'),true)) { if ($v===null) { throw new ValidationFailure('Input should be a valid boolean',$key); } $v=DataRoutes::boolean($v,$key); }
            elseif (in_array($key,array('priority','email_account_id','cooldown_minutes'),true)) { if ($v===null) { throw new ValidationFailure('Input should be a valid integer',$key); } $v=DataRoutes::integer($v,$key,$key==='cooldown_minutes'?0:null,$key==='cooldown_minutes'?100000:null); }
            elseif (in_array($key,array('conditions','actions'),true)) {
                if (!is_array($v)) { throw new ValidationFailure('Input should be a valid list',$key); }
                foreach ($v as $i=>$item) { if (!is_object($item)) { throw new ValidationFailure('Input should be a valid dictionary',$key.'.'.$i); } }
                if (!$update) { foreach ($v as $item) {
                    if ($key==='conditions' && !in_array($item->field??null,array('sentiment','intent','label','keyword','has_tag','any'),true)) { throw new ValidationFailure('Value error, Unknown condition field: '.self::value($item->field??null),$key); }
                    if ($key==='actions') {
                        $type=$item->type??null;
                        if (!in_array($type,array('send_sms','send_email','stop_sequence','opt_out','add_tag','remove_tag','set_status','delete_contact'),true)) { throw new ValidationFailure('Value error, Unknown action type: '.self::value($type),$key); }
                        if (in_array($type,array('send_sms','send_email'),true) && self::strip($item->body??'')==='') { throw new ValidationFailure('Value error, '.$type.' action needs a message body',$key); }
                    }
                } }
            }
            else {
                if ($key==='match_type' && !in_array($v,array('contains','exact','starts','any'),true)) { throw new ValidationFailure("Input should be 'contains', 'exact', 'starts' or 'any'",$key); }
                if (!is_string($v)) { throw new ValidationFailure('Input should be a valid string',$key); }
                $cap=$key==='subject'?500:($key==='name'?($automation?($update?null:160):120):null);
                if ($cap!==null && Compat::length($v)>$cap) { throw new ValidationFailure('String should have at most '.$cap.' characters',$key); }
                if (!$update && (($automation && $key==='name') || (!$automation && $key==='reply_body'))) {
                    if ($v==='') { throw new ValidationFailure('String should have at least 1 character',$key); }
                    if (Compat::strip($v)==='') { throw new ValidationFailure('Value error, '.$key.' cannot be blank',$key); }
                }
                if ($key==='match_type' && !in_array($v,array('contains','exact','starts','any'),true)) { throw new ValidationFailure("Input should be 'contains', 'exact', 'starts' or 'any'",$key); }
            }
            $out[$key]=$v;
        }
        return $out;
    }
    private static function value($v) { return $v===null?'None':Attachments::pythonText($v); }
    private static function strip($v) { if (!is_string($v)) { if (!Attachments::truth($v)) { return ''; } throw new \InvalidArgumentException('Expected condition text'); } return Compat::strip($v); }
    private function sender($channel,$id) { if ($channel==='email' && $id!==null && ($id===0 || !$this->db->one($id<0?'email_mailboxes':'email_accounts',array('id'=>abs($id))))) { throw new AuthFailure(404,'Selected email sender not found'); } }
    private static function validateMatch($type,$keywords) { if ($type!==null && $type!=='' && $type!=='any' && self::strip($keywords??'')==='') { throw new AuthFailure(400,"Match type '".$type."' needs at least one keyword. Use match type 'any' for a catch-all rule."); } }
    public function find($id,$automation=false) { $r=$this->db->one(self::table($automation),array('id'=>$id)); if (!$r) { throw new AuthFailure(404,$automation?'Automation not found':'Rule not found'); } return $r; }
    public static function load($raw) { if (!is_string($raw) || $raw==='') { return array(); } try { $a=json_decode($raw,false,512,JSON_THROW_ON_ERROR); return is_array($a)?$a:array(); } catch (\Throwable $e) { return array(); } }
    private static function encode($v) { return json_encode($v,JSON_UNESCAPED_SLASHES|JSON_UNESCAPED_UNICODE|JSON_PRESERVE_ZERO_FRACTION|JSON_THROW_ON_ERROR); }
    public function out(array $r,$automation=false) {
        foreach (array('created_at','updated_at','last_triggered_at') as $key) { $r[$key]=Records::iso($r[$key]); }
        if ($automation) { $r['conditions']=self::load($r['conditions_json']); $r['actions']=self::load($r['actions_json']); unset($r['conditions_json'],$r['actions_json']); if ($r['channel']===null || $r['channel']==='') { $r['channel']='sms'; } }
        return $r;
    }
    public function create($body,$automation=false) {
        $d=self::input($body,$automation);
        if ($automation) { if (!in_array($d['channel'],array('sms','email'),true)) { $d['channel']='sms'; } $d['name']=Compat::strip($d['name']); }
        else { self::validateMatch($d['match_type'],$d['keywords']); if (!in_array($d['channel'],array('sms','email'),true)) { throw new AuthFailure(422,"channel must be 'sms' or 'email'"); } }
        $this->sender($d['channel'],$d['email_account_id']);
        if ($automation) { foreach (array('conditions','actions') as $key) { $d[$key.'_json']=self::encode($d[$key]); unset($d[$key]); } }
        return $this->out($this->records->create(self::table($automation),$d),$automation);
    }
    public function update($id,$body,$automation=false) {
        $d=self::input($body,$automation,true); $r=$this->find($id,$automation); $channel=array_key_exists('channel',$d)?$d['channel']:($r['channel']!==null && $r['channel']!==''?$r['channel']:'sms'); $sender=array_key_exists('email_account_id',$d)?$d['email_account_id']:$r['email_account_id'];
        if (!$automation && !in_array($channel,array('sms','email'),true)) { throw new AuthFailure(422,"channel must be 'sms' or 'email'"); }
        $this->sender($channel,$sender);
        $field=$automation?'name':'reply_body'; if (array_key_exists($field,$d) && self::strip($d[$field]??'')==='') { throw new AuthFailure(400,$field.' cannot be blank'); }
        if (!$automation) { self::validateMatch(array_key_exists('match_type',$d)?$d['match_type']:$r['match_type'],array_key_exists('keywords',$d)?$d['keywords']:$r['keywords']); }
        if ($automation) { foreach (array('conditions','actions') as $key) { if (array_key_exists($key,$d)) { $d[$key.'_json']=self::encode($d[$key]); unset($d[$key]); } } }
        foreach ($d as $key=>$v) { if ($r[$key]===$v) { unset($d[$key]); } }
        if ($d) { $d['updated_at']=$this->records->now(); $this->db->update(self::table($automation),$d,array('id'=>$id)); }
        return $this->out($this->find($id,$automation),$automation);
    }
    public function listing($channel=null,$automation=false) {
        $wanted=Compat::lower($channel!==null && $channel!==''?$channel:'sms'); $sql='SELECT * FROM '.Schema::identifier($this->db->schema()->table(self::table($automation))); $args=array(); if ($wanted!=='all') { $sql.=' WHERE channel=%s'; $args[]=$wanted; } $sql.=' ORDER BY priority ASC,id ASC'; $items=array(); foreach ($this->db->rows($sql,$args) as $r) { $items[]=$this->out($this->db->decode(self::table($automation),$r),$automation); } return array('items'=>$items);
    }
    public function delete($id,$automation=false) { $this->find($id,$automation); $this->db->delete(self::table($automation),array('id'=>$id)); }
    public static function ruleMatches(array $r,$body) {
        $text=Compat::lower(Compat::strip($body)); if ($r['match_type']==='any') { return true; } if ($text==='') { return false; }
        foreach (explode(',',$r['keywords']??'') as $word) { $word=Compat::lower(Compat::strip($word)); if ($word==='') { continue; } if ($r['match_type']==='exact') { if ($text===$word) { return true; } } elseif ($r['match_type']==='starts') { if (strpos($text,$word)===0) { return true; } } elseif (strpos($text,$word)!==false) { return true; } } return false;
    }
    public function matching($body,$channel='sms') { $out=array(); foreach ($this->listing($channel)['items'] as $r) { if ($r['is_enabled'] && self::ruleMatches($r,$body)) { $out[]=$r; if ($r['stop_on_match']) { break; } } } return $out; }
    public function testReply($body) { if (!is_object($body)) { throw new ValidationFailure('Input should be a valid dictionary or object to extract fields from'); } if (!property_exists($body,'body')) { throw new ValidationFailure('Field required','body'); } if (!is_string($body->body)) { throw new ValidationFailure('Input should be a valid string','body'); } $matches=$this->matching($body->body); $r=$matches[0]??null; return array('matched'=>$r!==null,'rule_id'=>$r['id']??null,'rule_name'=>$r['name']??null,'reply_body'=>$r!==null?Templating::render($r['reply_body']):null); }
    public static function condition($cond,array $ctx) {
        if (!is_object($cond)) { throw new \InvalidArgumentException('Expected condition object'); }
        $field=self::strip($cond->field??''); $op=self::strip(Attachments::truth($cond->op??null)?$cond->op:'is'); $v=Compat::lower(self::strip(self::value(Attachments::truth($cond->value??null)?$cond->value:'')));
        if ($field==='any') { return true; }
        if ($field==='sentiment' || $field==='intent') { $actual=Compat::lower($ctx[$field]??''); }
        elseif ($field==='label' || $field==='has_tag') { $a=$ctx[$field==='label'?'labels':'tags']??array(); if ($field==='label') { $a=array_map(function($v){return Compat::lower(self::value($v));},$a); } $contains=in_array($v,$a,true); return in_array($op,array('is_not','not'),true)?!$contains:$contains; }
        elseif ($field==='keyword') { return $v==='' || strpos(Compat::lower($ctx['text']??''),$v)!==false; }
        else { return false; }
        if (in_array($op,array('is_not','not'),true)) { return $actual!==$v; } if (in_array($op,array('contains','in'),true)) { return $v==='' || strpos($actual,$v)!==false; } return $actual===$v;
    }
    public static function conditions(array $r,array $ctx) { $conditions=isset($r['conditions'])?$r['conditions']:self::load($r['conditions_json']??null); if (!$conditions) { return false; } foreach ($conditions as $c) { $hit=self::condition($c,$ctx); if ($r['match_all'] && !$hit) { return false; } if (!$r['match_all'] && $hit) { return true; } } return (bool)$r['match_all']; }
    public function testAutomation($body) { if (!is_object($body)) { throw new ValidationFailure('Input should be a valid dictionary'); } $text=self::strip($body->text??''); if ($text==='') { throw new AuthFailure(400,'text is required'); } $classification=ReplyClassifier::classify($text); $tags=array(); $rawTags=Attachments::truth($body->tags??null)?$body->tags:array(); if (is_string($rawTags)) { $rawTags=array_map(array(Compat::class,'chr'),Compat::points($rawTags)); } elseif (is_object($rawTags)) { $rawTags=array_keys(get_object_vars($rawTags)); } elseif (!is_array($rawTags)) { throw new \InvalidArgumentException('Expected iterable tags'); } foreach ($rawTags as $v) { $tags[]=Compat::lower(self::value($v)); } $ctx=array_merge($classification,array('text'=>$text,'tags'=>$tags)); $matched=array(); foreach ($this->listing('all',true)['items'] as $r) { if ($r['is_enabled'] && $r['trigger_type']==='inbound_reply' && self::conditions($r,$ctx)) { $matched[]=array('id'=>$r['id'],'name'=>$r['name']); } } return array('classification'=>$classification,'matched'=>$matched); }
}
