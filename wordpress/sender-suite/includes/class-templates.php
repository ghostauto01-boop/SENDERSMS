<?php
namespace SenderSuite;

/** SMS/email template library. Persistence and previews only; never invokes a transport. */
final class Templates {
    private $db; private $records; private $variables;
    public function __construct(Database $db,callable $clock=null) { $this->db=$db; $this->records=new Records($db,$clock); $this->variables=new Variables($db,$clock); }
    private function table() { return Schema::identifier($this->db->schema()->table('templates')); }
    public static function input($body,$update=false) {
        if (!is_object($body)) { throw new ValidationFailure('Input should be a valid dictionary or object to extract fields from'); }
        $defaults=array('name'=>null,'category'=>null,'body'=>'','channel'=>'sms','subject'=>null,'html_body'=>null,'preheader'=>null,'email_account_id'=>null,'attachments'=>null,'include_unsubscribe'=>true);
        $out=$update?array():$defaults;
        $fields=$update?array('name','category','body','is_active','subject','html_body','preheader','email_account_id','attachments','include_unsubscribe'):array_keys($defaults);
        foreach ($fields as $field) {
            if (!property_exists($body,$field)) { if (!$update && $field==='name') { throw new ValidationFailure('Field required',$field); } continue; }
            $v=$body->$field;
            if ($field==='channel') { if (!in_array($v,array('sms','email'),true)) { throw new ValidationFailure("Input should be 'sms' or 'email'",$field); } }
            elseif (in_array($field,array('is_active','include_unsubscribe'),true)) {
                if ($v===null && ($update || $field==='is_active')) { $out[$field]=null; continue; }
                if ($v===null) { throw new ValidationFailure('Input should be a valid boolean',$field); }
                $v=DataRoutes::boolean($v,$field);
            }
            elseif ($field==='email_account_id') { if ($v!==null) { $v=DataRoutes::integer($v,$field); } }
            elseif ($field==='attachments') {
                if ($v!==null && !is_array($v)) { throw new ValidationFailure('Input should be a valid list',$field); }
                foreach ($v??array() as $i=>$item) { if (!is_object($item)) { throw new ValidationFailure('Input should be a valid dictionary',$field.'.'.$i); } }
            }
            else {
                if (($v===null && !$update && in_array($field,array('name','body'),true)) || ($v!==null && !is_string($v))) { throw new ValidationFailure('Input should be a valid string',$field); }
                $cap=in_array($field,array('subject','preheader'),true)?500:(!$update && $field==='name'?255:null);
                if ($v!==null && $cap!==null && Compat::length($v)>$cap) { throw new ValidationFailure('String should have at most '.$cap.' characters',$field); }
            }
            $out[$field]=$v;
        }
        return $out;
    }
    private static function channel($v) {
        if ($v===null || $v==='' || $v==='all') { return null; }
        if (!in_array($v,array('sms','email'),true)) { throw new AuthFailure(422,"channel must be one of ['sms', 'email']"); }
        return $v;
    }
    private static function subject($value) { if (Compat::strip($value??'')==='') { throw new AuthFailure(422,'An email template needs a subject line'); } }
    private function requireVariables(array $texts) { list($unknown,$known)=$this->variables->unknownIn($texts); if ($unknown) { throw new DetailedFailure(422,Variables::unknownError($unknown,$known)); } }
    private static function metrics($channel,$subject,$body,$html=null) {
        if ($channel!=='email') { if ($body===null) { throw new \InvalidArgumentException('Null SMS body'); } return Templating::segments($body); }
        $length=Compat::length($body??''); return array($length,($subject!==null && $subject!=='')||$length?max(1,intdiv(Compat::length($subject??'')+$length,1000)+1):1);
    }
    public function find($id) { $r=$this->db->one('templates',array('id'=>$id)); if (!$r) { throw new AuthFailure(404,'Template not found'); } return $r; }
    public function out(array $r) { $r['is_active']=(bool)$r['is_active']; $r['include_unsubscribe']=(bool)$r['include_unsubscribe']; $r['attachments']=Attachments::summary($r['attachments']); $r['created_at']=Records::iso($r['created_at']); $r['updated_at']=Records::iso($r['updated_at']); return $r; }
    public function create($body) {
        $d=self::input($body); $channel=$d['channel']; $text=$d['body'];
        if ($channel==='email' && $text==='' && ($d['html_body']===null || $d['html_body']==='')) { throw new AuthFailure(422,'An email template needs a body (or an HTML body)'); }
        if ($channel==='email') { self::subject($d['subject']); }
        $this->requireVariables(array($text,$channel==='email'?$d['subject']:null,$channel==='email'?$d['html_body']:null));
        list($d['char_count'],$d['segment_count'])=self::metrics($channel,$d['subject'],$text,$d['html_body']);
        foreach (array('subject','html_body','preheader','email_account_id') as $key) { if ($channel!=='email') { $d[$key]=null; } }
        $d['attachments']=$channel==='email'?Attachments::dump(Attachments::clean($d['attachments'])):null;
        if ($channel!=='email') { $d['include_unsubscribe']=true; }
        return $this->out($this->records->create('templates',$d));
    }
    public function update($id,$body) {
        $d=self::input($body,true); $r=$this->find($id); $next=array_merge($r,$d);
        if ($r['channel']==='email' && array_key_exists('subject',$d)) { self::subject($d['subject']); }
        if (array_intersect(array('body','subject','html_body'),array_keys($d))) {
            $this->requireVariables(array($next['body'],$r['channel']==='email'?$next['subject']:null,$r['channel']==='email'?$next['html_body']:null));
            list($d['char_count'],$d['segment_count'])=self::metrics($r['channel'],$next['subject'],$next['body'],$next['html_body']);
        }
        if (array_key_exists('attachments',$d)) { $d['attachments']=Attachments::dump(Attachments::clean($d['attachments'])); }
        if (isset($d['is_active'])) { $d['is_active']=(int)$d['is_active']; }
        foreach ($d as $k=>$v) { if ($v===$r[$k]) { unset($d[$k]); } }
        if ($d) { $d['updated_at']=$this->records->now(); $this->db->update('templates',$d,array('id'=>$id)); }
        return $this->out($this->find($id));
    }
    public function listing($page=1,$perPage=25,$search=null,$category=null,$channel=null) {
        $where=array('1=1'); $args=array(); $wanted=self::channel($channel);
        if ($wanted!==null) { $where[]='channel=%s'; $args[]=$wanted; }
        if ($search!==null && $search!=='') { $where[]='LOWER(name) LIKE LOWER(%s)'; $args[]='%'.$search.'%'; }
        if ($category!==null && $category!=='') { $where[]='category=%s'; $args[]=$category; }
        $suffix=' FROM '.$this->table().' WHERE '.implode(' AND ',$where);
        $total=(int)$this->db->rows('SELECT COUNT(*) AS n'.$suffix,$args)[0]['n'];
        $rows=$this->db->rows('SELECT *'.$suffix.' ORDER BY updated_at DESC LIMIT %d OFFSET %d',array_merge($args,array($perPage,($page-1)*$perPage))); $items=array();
        foreach ($rows as $r) { $items[]=$this->out($this->db->decode('templates',$r)); }
        return array('total'=>$total,'items'=>$items);
    }
    public function categories($channel=null) {
        $wanted=self::channel($channel); $sql='SELECT DISTINCT category FROM '.$this->table().' WHERE category IS NOT NULL'; $args=array();
        if ($wanted!==null) { $sql.=' AND channel=%s'; $args[]=$wanted; }
        $items=array(); foreach ($this->db->rows($sql,$args) as $r) { if ($r['category']!=='') { $items[]=$r['category']; } } sort($items,SORT_STRING); return array('items'=>$items);
    }
    public function duplicate($id,$email=false) {
        $r=$this->find($id);
        if ($email && $r['channel']==='email') { throw new AuthFailure(400,'That template is already an email template'); }
        $fields=$email?array('name','category','body'):array('name','category','channel','body','subject','html_body','preheader','email_account_id','attachments','include_unsubscribe','char_count','segment_count'); $d=array();
        foreach ($fields as $key) { $d[$key]=$r[$key]; }
        $d['name'].=$email?' (Email)':' (Copy)';
        if ($email) { $d['channel']='email'; $d['subject']=implode('',array_map(array(Compat::class,'chr'),array_slice(Compat::points($r['name']),0,120))); list($d['char_count'],$d['segment_count'])=self::metrics('email',null,$r['body']); }
        else { $d['channel']=$d['channel']!==null && $d['channel']!==''?$d['channel']:'sms'; }
        return $this->out($this->records->create('templates',$d));
    }
    public function delete($id) { $this->find($id); $this->db->delete('templates',array('id'=>$id)); }
    public function preview($body='',$subject=null,$html=null,$channel='sms',$contactId=null,array $overrides=array()) {
        $contact=$contactId!==null?(new Contacts($this->db))->find($contactId):null;
        $r=$this->variables->preview($body,$subject,$html,$contact,$overrides); list($n,$segments)=Templating::segments($r['body']);
        return array('preview'=>$r['body'],'subject'=>$r['subject'],'html'=>$r['html'],'char_count'=>$n,'segment_count'=>$segments,'channel'=>$channel==='email'?'email':'sms','contact'=>$r['contact'],'sample'=>$r['sample'],'unknown_variables'=>$r['unknown_variables'],'warnings'=>$r['warnings']);
    }
}
