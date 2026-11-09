<?php
namespace SenderSuite;

/** Stored outgoing history and dry-run recipient validation. Never calls a transport or starts a worker. */
final class SendHistory {
    private $db;
    public function __construct(Database $db){$this->db=$db;}
    public static function definitions(){return array('/send/history'=>array('GET'),'/send/validate'=>array('POST'));}
    private function table($name){return Schema::identifier($this->db->schema()->table($name));}
    private function contactSendable(array $c,$strict=false){
        if($c['is_opted_out']){return array('sendable'=>false,'normalized'=>null,'reason'=>'opted_out','detail'=>'Contact opted out (STOP).');}
        if($c['is_undeliverable']){return array('sendable'=>false,'normalized'=>null,'reason'=>'undeliverable','detail'=>($c['undeliverable_reason']!==null&&$c['undeliverable_reason']!=='')?$c['undeliverable_reason']:'Previously marked undeliverable.');}
        $r=NumberFilter::classify($c['phone_number']??'');if(!$r['sendable']){return $r;}
        if($c['phone_number']!==null&&$this->db->one('suppression_entries',array('phone_number'=>$c['phone_number']))){return array('sendable'=>false,'normalized'=>$r['normalized'],'reason'=>'suppressed','detail'=>'Number is on the suppression list.');}
        if($strict){$soft=NumberFilter::soft($c['phone_number']??'');if($soft!==null){return array('sendable'=>false,'normalized'=>$r['normalized'],'reason'=>$soft,'detail'=>"'".($c['phone_number']??'')."' looks like sample/test data.");}}
        return $r;
    }
    public function validate(array $q){
        $cid=isset($q['contact_id'])&&$q['contact_id']!==''?DataRoutes::integer($q['contact_id'],'contact_id',1):null;
        $phone=DataRoutes::text($q,'phone_number',null,null,1000);
        $lid=isset($q['list_id'])&&$q['list_id']!==''?DataRoutes::integer($q['list_id'],'list_id',1):null;
        if($cid!==null){
            $rows=$this->db->rows('SELECT id,phone_number,is_opted_out,is_undeliverable,undeliverable_reason FROM '.$this->table('contacts').' WHERE id=%d',array($cid));if(!$rows){throw new AuthFailure(404,'Contact not found');}$c=$this->db->decode('contacts',$rows[0]);$chk=$this->contactSendable($c,false);
            return array('total'=>1,'sendable'=>$chk['sendable']?1:0,'blocked'=>$chk['sendable']?0:1,'items'=>array(array('contact_id'=>$c['id'],'phone'=>$c['phone_number'],'sendable'=>$chk['sendable'],'reason'=>$chk['reason'],'detail'=>$chk['detail'])));
        }
        if($phone!==null&&$phone!==''){
            if(strpos($phone,"\0")!==false||preg_match('//u',$phone)!==1){throw new ValidationFailure('Supply valid text without NUL characters','phone_number');}
            $chk=NumberFilter::classify($phone);
            return array('total'=>1,'sendable'=>$chk['sendable']?1:0,'blocked'=>$chk['sendable']?0:1,'items'=>array(array('phone'=>$phone,'normalized'=>$chk['normalized'],'sendable'=>$chk['sendable'],'reason'=>$chk['reason'],'detail'=>$chk['detail'])));
        }
        if($lid!==null){
            $count=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('contact_list_members').' WHERE list_id=%d',array($lid))[0]['n'];if($count>50000){throw new AuthFailure(413,'List exceeds the synchronous preview limit.');}
            $rows=$this->db->rows('SELECT c.id,c.phone_number,c.is_opted_out,c.is_undeliverable,c.undeliverable_reason FROM '.$this->table('contact_list_members').' m JOIN '.$this->table('contacts').' c ON c.id=m.contact_id WHERE m.list_id=%d ORDER BY m.id ASC',array($lid));
            $items=array();$reasons=array();$sendable=0;
            foreach($rows as $raw){
                $c=$this->db->decode('contacts',$raw);$chk=$this->contactSendable($c,true);
                if($chk['sendable']){$sendable++;}
                else{$r=$chk['reason']?:'unknown';$reasons[$r]=($reasons[$r]??0)+1;if(count($items)<100){$items[]=array('contact_id'=>$c['id'],'phone'=>$c['phone_number'],'reason'=>$chk['reason'],'detail'=>$chk['detail']);}}
            }
            $total=$sendable+array_sum($reasons);
            return array('total'=>$total,'sendable'=>$sendable,'blocked'=>$total-$sendable,'by_reason'=>$reasons?(object)$reasons:new \stdClass(),'blocked_preview'=>$items);
        }
        throw new AuthFailure(400,'Provide contact_id, phone_number, or list_id');
    }
    public function listing(array $query){
        $page=DataRoutes::integer($query['page']??1,'page',1,1000000);
        $per=DataRoutes::integer($query['per_page']??50,'per_page',1,100);
        $status=DataRoutes::text($query,'status',null,null,100);
        $search=DataRoutes::text($query,'search',null,null,1000);
        foreach(array('status'=>$status,'search'=>$search) as $key=>$value){if($value!==null&&(strpos($value,"\0")!==false||preg_match('//u',$value)!==1)){throw new ValidationFailure('Supply valid text without NUL characters',$key);}}
        $channel=Analytics::channel(DataRoutes::text($query,'channel','sms',null,32));
        $where=array("m.direction='outgoing'");$args=array();
        if($channel!=='all'){$where[]="COALESCE(m.channel,'sms')=%s";$args[]=$channel;}
        if($status!==null&&$status!==''&&$status!=='all'){
            if($status==='failed'){$where[]="m.status IN ('failed','cancelled')";}
            else{$where[]='m.status=%s';$args[]=$status;}
        }
        // Preserve source wildcard matching. Arguments remain prepared; no interpolated input.
        if($search!==null&&$search!==''){
            $where[]='(LOWER(m.body) LIKE LOWER(%s) OR LOWER(m.last_error) LIKE LOWER(%s))';
            $args[]='%'.$search.'%';$args[]='%'.$search.'%';
        }
        $base=' FROM '.$this->table('messages').' m WHERE '.implode(' AND ',$where);
        $total=(int)$this->db->rows('SELECT COUNT(*) AS n'.$base,$args)[0]['n'];
        $order=' ORDER BY m.created_at DESC,m.id ASC LIMIT %d OFFSET %d';
        $params=array_merge($args,array($per,($page-1)*$per));
        // Reject oversized stored pages before loading their unbounded source TEXT columns.
        $bytes=(int)$this->db->rows('SELECT COALESCE(SUM(p.bytes),0) AS n FROM (SELECT COALESCE(OCTET_LENGTH(m.body),0) AS bytes'.$base.$order.') p',$params)[0]['n'];
        if($bytes>4194304){throw new AuthFailure(413,'History page exceeds the text limit. Reduce the page size.');}
        $picked='SELECT m.id'.$base.$order;
        $from=' FROM ('.$picked.') picked JOIN '.$this->table('messages').' m ON m.id=picked.id LEFT JOIN '.$this->table('contacts').' c ON c.id=m.contact_id';
        // Never fetch encrypted provider metadata, raw provider errors, attachments or email headers.
        $rows=$this->db->rows("SELECT m.id,m.contact_id,m.body,m.status,CASE WHEN m.last_error IS NULL OR m.last_error='' THEN m.last_error ELSE 'Message delivery reported an error.' END AS error,m.created_at,m.sent_at,m.failed_at,m.delivered_at,m.provider_message_id,m.campaign_id,m.conversation_id,c.first_name,c.last_name,c.business_name,c.phone_number".$from.' ORDER BY m.created_at DESC,m.id ASC',$params);
        $items=array();$size=0;
        foreach($rows as $row){
            $name=null;$phone=$row['phone_number'];
            if($phone!==null||$row['first_name']!==null||$row['last_name']!==null||$row['business_name']!==null){
                $name=Compat::strip(($row['first_name']??'').' '.($row['last_name']??''));
                if($name===''){$name=$row['business_name']!==null&&$row['business_name']!==''?$row['business_name']:$phone;}
            }
            $item=array('id'=>(int)$row['id'],'contact_id'=>$row['contact_id']!==null?(int)$row['contact_id']:null,'contact_name'=>$name,'phone'=>$phone,'body'=>$row['body'],'body_preview'=>$row['body']!==null?implode('',array_map(array(Compat::class,'chr'),array_slice(Compat::points($row['body']),0,100))):'','status'=>$row['status'],'error'=>$row['error'],'provider_message_id'=>$row['provider_message_id'],'campaign_id'=>$row['campaign_id']!==null?(int)$row['campaign_id']:null,'conversation_id'=>$row['conversation_id']!==null?(int)$row['conversation_id']:null);
            foreach(array('created_at','sent_at','failed_at','delivered_at') as $key){$item[$key]=Records::iso($row[$key]);}
            $size+=strlen(json_encode($item,JSON_THROW_ON_ERROR));
            if($size>8388608){throw new AuthFailure(413,'History response is too large. Reduce the page size.');}
            $items[]=$item;
        }
        return array('total'=>$total,'page'=>$page,'per_page'=>$per,'items'=>$items);
    }
    public static function handle($request,array $context){
        $path=trim(substr($request->get_route(),strlen('/sender-suite/v1/')),'/');$method=$request->get_method();$s=new self($context['db']);
        if($path==='send/history'&&$method==='GET'){return $s->listing($request->get_query_params());}
        if($path==='send/validate'&&$method==='POST'){return $s->validate($request->get_query_params());}
        throw new AuthFailure(404,'Application endpoint not found.');
    }
}
