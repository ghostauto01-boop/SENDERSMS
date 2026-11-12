<?php
namespace SenderSuite;

/** Private draft invoicing. No checkout URLs, email delivery or financial settlement. Caller owns transaction. */
final class Invoices {
    const CURRENCIES=array('NGN','USD','GHS','ZAR','KES','XOF','EGP');
    const SYMBOLS=array('NGN'=>'₦','USD'=>'$','GHS'=>'GH₵','ZAR'=>'R','KES'=>'KSh','XOF'=>'CFA ','EGP'=>'E£');
    const MAX_MINOR=2147483647;
    private $db;private $records;private $config;
    public function __construct(Database $db,callable $clock=null,Config $config=null){$this->db=$db;$this->records=new Records($db,$clock);$this->config=$config;}
    private function table($n){return Schema::identifier($this->db->schema()->table($n));}
    public static function register(){
        register_rest_route('sender-suite/v1','/invoices/paystack/webhook',array('methods'=>'POST','permission_callback'=>'__return_true','callback'=>array(InvoiceSettlement::class,'dispatchPaystackWebhook')));
        register_rest_route('sender-suite/v1','/invoices/public/(?P<token>[A-Za-z0-9_-]+)(?:/(?P<subaction>pay|verify))?',array('methods'=>array('GET','POST'),'permission_callback'=>'__return_true','callback'=>array(InvoiceSettlement::class,'dispatchPublic')));
    }
    public static function publicDisabled($request=null){return $request!==null?InvoiceSettlement::dispatchPublic($request):AuthRoutes::response(AuthRoutes::error(400,'Missing invoice request.',bin2hex(random_bytes(16))),400,bin2hex(random_bytes(16)));}
    public static function definitions(){return array('/invoices/?'=>array('GET','POST'),'/invoices/settings'=>array('GET','PUT'),'/invoices/settings/test'=>array('POST'),'/invoices/currencies'=>array('GET'),'/invoices/analytics'=>array('GET'),'/invoices/payments'=>array('GET'),'/invoices/links'=>array('GET','POST'),'/invoices/(?P<invoice_id>-?[0-9]+)'=>array('GET','PATCH','DELETE'),'/invoices/(?P<invoice_id>-?[0-9]+)/duplicate'=>array('POST'),'/invoices/(?P<invoice_id>-?[0-9]+)/void'=>array('POST'),'/invoices/(?P<invoice_id>-?[0-9]+)/payments'=>array('GET','POST'),'/invoices/(?P<invoice_id>-?[0-9]+)/events'=>array('GET'),'/invoices/(?P<invoice_id>-?[0-9]+)/rotate-link'=>array('POST'),'/invoices/(?P<invoice_id>-?[0-9]+)/(?P<live>send|remind|pay)'=>array('POST'));}
    /** printf's F uses binary64 nearest/even conversion, independent of LC_NUMERIC. Not PHP round's tie tolerance. */
    public static function pyRound($v,$places=0){if(!is_finite((float)$v)){throw new ValidationFailure('Number must be finite');}return (float)sprintf('%.'.$places.'F',(float)$v);}
    private static function number($v,$field,$min,$max){if(is_string($v)){$v=Compat::strip($v);}if(is_bool($v)){$v=(int)$v;}if((!is_int($v)&&!is_float($v)&&!(is_string($v)&&is_numeric(Compat::strip($v))))||!is_finite((float)$v)||(float)$v<$min||(float)$v>$max){throw new ValidationFailure('Number is outside the supported range',$field);}return (float)$v;}
    private static function text($v,$field,$cap){if(!is_string($v)||strpos($v,"\0")!==false||preg_match('//u',$v)!==1||Compat::length($v)>$cap){throw new ValidationFailure('Input should be bounded valid text',$field);}return $v;}
    public static function input($body,$patch=false){
        if(!is_object($body)){throw new ValidationFailure('Input should be a valid object');}
        $defaults=array('customer_name'=>null,'customer_email'=>null,'customer_phone'=>null,'customer_address'=>null,'customer_business'=>null,'contact_id'=>null,'link_title'=>null,'currency'=>'NGN','issue_date'=>null,'due_date'=>null,'items'=>array(),'discount_minor'=>0,'tax_rate'=>0.0,'notes'=>null,'terms'=>null,'footer'=>null);
        if($patch){$defaults['status']=null;}else{$defaults['kind']=null;$defaults['send']=false;$defaults['send_email']=true;}$out=$patch?array():$defaults;
        foreach($defaults as $key=>$default){if(!property_exists($body,$key)){continue;}$v=$body->$key;if($v===null){if(!$patch&&in_array($key,array('currency','items','discount_minor','tax_rate','send','send_email'),true)){throw new ValidationFailure('Input must not be null',$key);}$out[$key]=null;continue;}
            if($key==='items'){if(!is_array($v)||count($v)>100){throw new ValidationFailure('An invoice can have at most 100 lines','items');}$items=array();foreach($v as $i=>$item){if(!is_object($item)||!property_exists($item,'description')){throw new ValidationFailure('Line description required','items.'.$i);}$desc=self::text($item->description,'items.'.$i.'.description',500);if($desc===''){throw new ValidationFailure('Line description required','items.'.$i.'.description');}$items[]=array('description'=>$desc,'quantity'=>self::number((property_exists($item,'quantity')?$item->quantity:1),'items.'.$i.'.quantity',PHP_FLOAT_MIN,1000000),'unit_price_minor'=>DataRoutes::integer((property_exists($item,'unit_price_minor')?$item->unit_price_minor:0),'items.'.$i.'.unit_price_minor',0,self::MAX_MINOR));}$v=$items;}
            elseif(in_array($key,array('send','send_email'),true)){$v=DataRoutes::boolean($v,$key);}
            elseif($key==='discount_minor'){$v=DataRoutes::integer($v,$key,0,self::MAX_MINOR);}
            elseif($key==='tax_rate'){$v=self::number($v,$key,0,100);}
            elseif($key==='contact_id'){$v=DataRoutes::integer($v,$key);}
            elseif($key==='issue_date'||$key==='due_date'){$v=Calendar::date($v,$key);}
            else{$v=self::text($v,$key,array('customer_name'=>255,'customer_email'=>320,'customer_phone'=>40,'customer_business'=>255,'link_title'=>255)[$key]??100000);}
            $out[$key]=$v;
        }return $out;
    }
    public static function totals(array $items,$discount=0,$rate=0.0){
        if(!$items||count($items)>100){throw new ValidationFailure('An invoice needs 1 to 100 lines','items');}$out=array();$subtotal=0;
        foreach($items as $i=>$item){$description=Compat::strip($item['description']);if($description===''){$description='Item '.($i+1);}$q=self::pyRound($item['quantity'],3);$q=max(0.0,min(1000000.0,$q));if($q==0.0){$q=1.0;}$unit=$item['unit_price_minor'];$amount=self::pyRound($q*$unit);if($amount>self::MAX_MINOR){throw new ValidationFailure('Line amount exceeds the storage limit','items');}$amount=(int)$amount;$subtotal+=$amount;if($subtotal>self::MAX_MINOR){throw new ValidationFailure('Subtotal exceeds the storage limit','items');}$out[]=array('description'=>$description,'quantity'=>$q,'unit_price_minor'=>$unit,'amount_minor'=>$amount,'position'=>$i);}
        $discount=max(0,min($discount??0,$subtotal));$taxable=$subtotal-$discount;$rate=$rate??0.0;$tax=(int)self::pyRound($taxable*$rate/100.0);$total=$taxable+$tax;if($total>self::MAX_MINOR){throw new ValidationFailure('Total exceeds the storage limit','total_minor');}return array('items'=>$out,'subtotal_minor'=>$subtotal,'discount_minor'=>$discount,'tax_rate'=>self::pyRound($rate,2),'tax_minor'=>$tax,'total_minor'=>$total);
    }
    private function setting($key,$fallback=''){$r=$this->db->one('system_settings',array('key'=>$key));return $r&&$r['value']!==null?$r['value']:$fallback;}
    private function nextNumber(){
        $raw=$this->setting('invoice.prefix','INV');$prefix=substr(preg_replace('/[^A-Za-z0-9-]/','',Compat::upper($raw===''?'INV':$raw)),0,12);if($prefix===''){$prefix='INV';}$year=substr($this->records->now(),0,4);$key='invoice.sequence.'.$year;$row=$this->db->one('system_settings',array('key'=>$key));
        if(!$row){$last=0;$highest=0;do{$rows=$this->db->rows('SELECT id,number FROM '.$this->table('invoices').' WHERE id>%d AND number LIKE %s ORDER BY id LIMIT 200',array($last,$prefix.'-'.$year.'-%'));foreach($rows as $r){$last=(int)$r['id'];$parts=explode('-',$r['number']);$tail=end($parts);if(preg_match('/^[0-9]+$/D',$tail)){$v=DataRoutes::integer($tail,'invoice_number',0,self::MAX_MINOR);$highest=max($highest,$v);}}}while(count($rows)===200);try{$row=$this->records->create('system_settings',array('key'=>$key,'value'=>(string)$highest,'category'=>'invoice'));}catch(DatabaseFailure $e){if($this->db->one('system_settings',array('key'=>$key))){throw new AuthFailure(409,'Invoice counter initialization conflicted. Retry.');}throw $e;}}
        if(!is_string($row['value'])||!preg_match('/^[0-9]+$/D',$row['value'])){throw new AuthFailure(503,'Invoice counter is invalid. Operator recovery is required.');}try{$n=DataRoutes::integer($row['value'],'invoice_number',0,self::MAX_MINOR-1)+1;}catch(ValidationFailure $e){throw new AuthFailure(503,'Invoice counter is outside the supported range. Operator recovery is required.');}if($this->db->update('system_settings',array('value'=>(string)$n,'updated_at'=>$this->records->now()),array('id'=>$row['id'],'value'=>$row['value']))!==1){throw new AuthFailure(409,'Invoice number changed. Retry.');}return $prefix.'-'.$year.'-'.str_pad((string)$n,4,'0',STR_PAD_LEFT);
    }
    private function invoice($id){$r=$this->db->one('invoices',array('id'=>$id));if(!$r){throw new AuthFailure(404,'Invoice not found');}return $r;}
    private function children($table,$id,$limit=1000){$rows=$this->db->rows('SELECT * FROM '.$this->table($table).' WHERE invoice_id=%d ORDER BY '.($table==='invoice_items'?'position ASC,id ASC':'id DESC').' LIMIT %d',array($id,$limit+1));if(count($rows)>$limit){throw new AuthFailure(413,'Invoice history is too large. Use the bounded history endpoint.');}return array_map(function($r)use($table){return $this->db->decode($table,$r);},$rows);}
    private function editable(array $r){if($r['kind']!=='invoice'){throw new AuthFailure(503,'Payment-link editing is not available.');}$n=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('invoice_payments').' WHERE invoice_id=%d',array($r['id']))[0]['n'];if($r['status']!=='draft'||$r['amount_paid_minor']!==0||$n){throw new AuthFailure(409,'Only an unpaid draft without payment attempts can be changed or deleted in this build.');}}
    private function change(array $old,array $patch){$stamp=$this->records->now();if($stamp<=$old['updated_at']){$stamp=(new \DateTimeImmutable($old['updated_at'],new \DateTimeZone('UTC')))->modify('+1 microsecond')->format('Y-m-d H:i:s.u');}$where=array('id'=>$old['id'],'updated_at'=>$old['updated_at'],'status'=>$old['status'],'amount_paid_minor'=>$old['amount_paid_minor'],'public_token'=>$old['public_token']);$patch['updated_at']=$stamp;if($this->db->update('invoices',$patch,$where)!==1){throw new AuthFailure(409,'Invoice changed. Reload and retry.');}}
    private function event($id,$type,$title){$this->records->create('invoice_events',array('invoice_id'=>$id,'event_type'=>$type,'title'=>implode('',array_map(array(Compat::class,'chr'),array_slice(Compat::points($title),0,255)))));}
    private function items($id,array $items){foreach($items as $item){$item['invoice_id']=$id;$this->records->create('invoice_items',$item);}}
    private static function emptySetting($v){return $v===''?null:$v;}
    private static function trimmed($v){if($v===null){return null;}$v=Compat::strip($v);return $v===''?null:$v;}
    private static function email($v){$v=self::trimmed($v);if($v!==null&&(strpos($v,'@')===false||strpos(substr($v,strpos($v,'@')+1),'.')===false||preg_match('/[\x00-\x20\x7f]/',$v))){throw new ValidationFailure('Enter a valid customer email address','customer_email');}return $v;}
    private function contact($v){if(!$v){return null;}return $this->db->one('contacts',array('id'=>$v))?$v:null;}
    private static function scalarTotals(array $t){unset($t['items']);return $t;}
    private function actor(array $user){$name=$user['display_name']??'';if($name===''){$name=$user['username']??'admin';}if($name===''){$name='admin';}return implode('',array_map(array(Compat::class,'chr'),array_slice(Compat::points($name),0,150)));}
    public function create($body,array $user){
        $d=self::input($body);if($d['send']){throw new AuthFailure(503,'Invoice sending and checkout are not available. Save a draft instead.');}if(strtolower($d['kind']??'')==='link'){throw new AuthFailure(503,'Public payment links are not available.');}$currency=strtoupper(($d['currency']!==''?$d['currency']:$this->setting('invoice.default_currency','NGN')));if(!in_array($currency,self::CURRENCIES,true)){throw new ValidationFailure('Unsupported currency','currency');}$t=self::totals($d['items'],$d['discount_minor'],$d['tax_rate']);if($t['total_minor']<=0){throw new ValidationFailure('An invoice total must be greater than zero','items');}
        $issue=$d['issue_date']??$this->records->now();$due=$d['due_date']??(new \DateTimeImmutable($issue,new \DateTimeZone('UTC')))->modify('+14 days')->format('Y-m-d H:i:s.u');$due=Calendar::date($due,'due_date');if($due<$issue){throw new ValidationFailure('The due date cannot be before the issue date','due_date');}
        $name=self::trimmed($d['customer_name'])??self::trimmed($d['link_title'])??self::trimmed($d['items'][0]['description'])??'Customer';$name=implode('',array_map(array(Compat::class,'chr'),array_slice(Compat::points($name),0,255)));
        $values=array('number'=>$this->nextNumber(),'public_token'=>bin2hex(random_bytes(16)),'kind'=>'invoice','customer_name'=>$name,'customer_email'=>self::email($d['customer_email']),'contact_id'=>$this->contact($d['contact_id']),'currency'=>$currency,'issue_date'=>$issue,'due_date'=>$due,'created_by'=>$this->actor($user));foreach(array('link_title','customer_phone','customer_address','customer_business') as $k){$values[$k]=self::trimmed($d[$k]);}foreach(array('notes'=>'','terms'=>'Payment is due by the date shown above. Thank you for your business.','footer'=>'') as $k=>$fallback){$key=$k==='footer'?'invoice.footer':'invoice.default_'.$k;$values[$k]=($d[$k]!==null&&$d[$k]!=='')?$d[$k]:self::emptySetting($this->setting($key,$fallback));}
        $r=$this->records->create('invoices',array_merge($values,self::scalarTotals($t)));$this->items($r['id'],$t['items']);$this->event($r['id'],'created','Invoice '.$r['number'].' created');return array('success'=>true,'invoice'=>$this->read($r['id']),'message'=>'Invoice saved as a draft.');
    }
    public function patch($id,$body){
        $old=$this->invoice($id);$d=self::input($body,true);if(!$d){return $this->out($old);}$this->editable($old);$p=array();if(isset($d['customer_name'])&&$d['customer_name']!==''){$p['customer_name']=Compat::strip($d['customer_name']);}foreach(array('link_title','customer_phone','customer_address','customer_business') as $k){if(array_key_exists($k,$d)){$p[$k]=self::trimmed($d[$k]);}}if(array_key_exists('customer_email',$d)){$p['customer_email']=self::email($d['customer_email']);}if(array_key_exists('contact_id',$d)){$p['contact_id']=$this->contact($d['contact_id']);}foreach(array('notes','terms','footer') as $k){if(array_key_exists($k,$d)){$p[$k]=$d[$k];}}
        if(isset($d['currency'])&&$d['currency']!==''){$code=strtoupper($d['currency']);if(!in_array($code,self::CURRENCIES,true)){throw new ValidationFailure('Unsupported currency','currency');}$p['currency']=$code;}$issue=$d['issue_date']??$old['issue_date'];$due=$d['due_date']??$old['due_date'];if($due<$issue){throw new ValidationFailure('The due date cannot be before the issue date','due_date');}$p['issue_date']=$issue;$p['due_date']=$due;
        $replace=isset($d['items']);if($replace||array_key_exists('discount_minor',$d)||array_key_exists('tax_rate',$d)){$items=$replace?$d['items']:$this->children('invoice_items',$id,100);$t=self::totals($items,array_key_exists('discount_minor',$d)?$d['discount_minor']:$old['discount_minor'],array_key_exists('tax_rate',$d)?$d['tax_rate']:$old['tax_rate']);if($t['total_minor']<=0){throw new ValidationFailure('An invoice total must be greater than zero','items');}$p=array_merge($p,self::scalarTotals($t));}
        if(isset($d['status'])&&$d['status']!==''){$wanted=strtolower($d['status']);if($wanted==='paid'){throw new AuthFailure(400,'Record a payment to mark this invoice paid — status alone must not create money.');}if($wanted==='sent'){throw new AuthFailure(503,'Invoice sending is not available.');}if(!in_array($wanted,array('draft','void'),true)){throw new ValidationFailure('Status must be draft or void in this build','status');}$p['status']=$wanted;}
        $this->change($old,$p);if($replace){$this->db->delete('invoice_items',array('invoice_id'=>$id));$this->items($id,$t['items']);}if(($p['status']??'')==='void'){$this->event($id,'voided','Invoice '.$old['number'].' voided');}$this->event($id,'updated','Invoice '.$old['number'].' updated');return $this->read($id);
    }
    public function delete($id){$r=$this->invoice($id);$this->editable($r);$this->change($r,array('status'=>'draft'));foreach(array('invoice_items','invoice_events') as $t){$this->db->delete($t,array('invoice_id'=>$id));}if($this->db->delete('invoices',array('id'=>$id))!==1){throw new AuthFailure(409,'Invoice changed. Retry.');}return array('success'=>true,'deleted'=>$id,'number'=>$r['number']);}
    public function duplicate($id,array $user){$r=$this->invoice($id);if($r['kind']!=='invoice'){throw new AuthFailure(503,'Payment-link creation is not available.');}$items=$this->children('invoice_items',$id,100);if(!$items||$r['total_minor']<=0){throw new AuthFailure(409,'Cannot duplicate an invalid legacy invoice.');}$values=array();foreach(array('customer_name','customer_email','customer_phone','customer_address','customer_business','contact_id','currency','link_title','subtotal_minor','discount_minor','tax_rate','tax_minor','total_minor','notes','terms','footer') as $k){$values[$k]=$r[$k];}$issue=$this->records->now();$values['number']=$this->nextNumber();$values['public_token']=bin2hex(random_bytes(16));$values['issue_date']=$issue;$values['due_date']=Calendar::date((new \DateTimeImmutable($issue,new \DateTimeZone('UTC')))->modify('+14 days')->format('Y-m-d H:i:s.u'),'due_date');$values['created_by']=$this->actor($user);$copy=$this->records->create('invoices',$values);foreach($items as &$item){unset($item['id'],$item['invoice_id']);}unset($item);$this->items($copy['id'],$items);$this->event($copy['id'],'duplicated','Created from invoice '.$r['number']);return array('success'=>true,'invoice'=>$this->read($copy['id']));}
    public function void($id){$old=$this->invoice($id);$this->editable($old);$this->change($old,array('status'=>'void'));$this->event($id,'voided','Invoice '.$old['number'].' voided');return array('success'=>true,'invoice'=>$this->read($id));}
    public function effective(array $r){$stored=strtolower($r['status']?:'draft');if(in_array($stored,array('draft','void'),true)){return $stored;}if($r['total_minor']>0&&$r['amount_paid_minor']>=$r['total_minor']){return 'paid';}if($r['due_date']!==null&&$r['due_date']<$this->records->now()){return 'overdue';}if($r['amount_paid_minor']>0){return 'partial';}return in_array($stored,array('sent','viewed'),true)?$stored:'sent';}
    private function paymentOut(array $r){unset($r['invoice_id'],$r['raw_json']);foreach(array('paid_at','created_at') as $k){$r[$k]=Records::iso($r[$k]);}return $r;}
    private function eventOut(array $r){unset($r['invoice_id'],$r['meta_json']);$r['created_at']=Records::iso($r['created_at']);return $r;}
    public function payments($id){$this->invoice($id);return array('items'=>array_map(array($this,'paymentOut'),$this->children('invoice_payments',$id)));}
    public function events($id,$limit=100){$this->invoice($id);$limit=DataRoutes::integer($limit,'limit',1,500);$rows=$this->db->rows('SELECT * FROM '.$this->table('invoice_events').' WHERE invoice_id=%d ORDER BY id DESC LIMIT %d',array($id,$limit));return array('items'=>array_map(function($r){return $this->eventOut($this->db->decode('invoice_events',$r));},$rows));}
    private function out(array $r){
        $status=$this->effective($r);$symbols=array('NGN'=>'₦','USD'=>'$','GHS'=>'GH₵','ZAR'=>'R','KES'=>'KSh','XOF'=>'CFA ','EGP'=>'E£');$items=$this->children('invoice_items',$r['id'],100);$out=$r;foreach(array('customer_name','customer_email','customer_phone','customer_address','customer_business','contact_id','meta_json','payment_note') as $k){unset($out[$k]);}$out['public_url']=null;$out['payment_link']=null;$out['stored_status']=$r['status'];$out['status']=$status;$out['status_label']=array('draft'=>'Draft','sent'=>'Sent','viewed'=>'Viewed','partial'=>'Part paid','paid'=>'Paid','overdue'=>'Overdue','void'=>'Void')[$status];$out['currency_symbol']=$symbols[$r['currency']]??strtoupper($r['currency']).' ';$out['customer']=array('contact_id'=>$r['contact_id'],'name'=>$r['customer_name'],'email'=>$r['customer_email'],'phone'=>$r['customer_phone'],'address'=>$r['customer_address'],'business'=>$r['customer_business']);$out['title']=self::trimmed($r['link_title'])??self::trimmed($r['customer_name'])??($items[0]['description']??'Payment');$out['balance_minor']=max(0,$r['total_minor']-$r['amount_paid_minor']);$due=new \DateTimeImmutable($r['due_date'],new \DateTimeZone('UTC'));$now=new \DateTimeImmutable($this->records->now(),new \DateTimeZone('UTC'));$seconds=(float)$due->format('U.u')-(float)$now->format('U.u');$out['days_until_due']=(int)floor($seconds/86400);
        foreach(array('issue_date','due_date','created_at','updated_at','sent_at','viewed_at','paid_at','last_reminder_at') as $k){$out[$k]=Records::iso($r[$k]);}$out['items']=array_map(function($i){unset($i['invoice_id']);return $i;},$items);$out['payments']=$this->payments($r['id'])['items'];$out['events']=array_map(array($this,'eventOut'),$this->children('invoice_events',$r['id'],500));if(strlen(json_encode($out,JSON_UNESCAPED_UNICODE|JSON_THROW_ON_ERROR))>2097152){throw new AuthFailure(413,'Invoice response is too large.');}return $out;
    }
    public function read($id){return $this->out($this->invoice($id));}
    public function listing(array $q){$page=isset($q['page'])?DataRoutes::integer($q['page'],'page',1,1000000):1;$per=isset($q['per_page'])?DataRoutes::integer($q['per_page'],'per_page',1,100):20;$where=array('1=1');$args=array();foreach(array('currency','kind','q','status') as $k){$v=DataRoutes::text($q,$k,null,null,1000);if($v===null||$v===''){continue;}if($k==='currency'){$v=strtoupper($v);if(!in_array($v,self::CURRENCIES,true)){throw new ValidationFailure('Unsupported currency',$k);}$where[]='currency=%s';$args[]=$v;}elseif($k==='kind'){if(in_array(strtolower($v),array('invoice','link'),true)){$where[]='kind=%s';$args[]=strtolower($v);}}elseif($k==='q'){$parts=array();foreach(array('number','customer_name','customer_email','customer_phone','customer_business','link_title') as $column){$parts[]='LOWER('.$column.') LIKE LOWER(%s)';$args[]='%'.Compat::strip($v).'%';}$where[]='('.implode(' OR ',$parts).')';}else{$this->statusFilter($v,$where,$args);}}if(isset($q['contact_id'])){$contact=DataRoutes::integer($q['contact_id'],'contact_id');if($contact){$where[]='contact_id=%d';$args[]=$contact;}}$from=' FROM '.$this->table('invoices').' WHERE '.implode(' AND ',$where);$total=(int)$this->db->rows('SELECT COUNT(*) AS n'.$from,$args)[0]['n'];$rows=$this->db->rows('SELECT *'.$from.' ORDER BY created_at DESC,id DESC LIMIT %d OFFSET %d',array_merge($args,array($per,($page-1)*$per)));$items=array();$bytes=0;foreach($rows as $r){$out=$this->out($this->db->decode('invoices',$r));$bytes+=strlen(json_encode($out,JSON_UNESCAPED_UNICODE|JSON_THROW_ON_ERROR));if($bytes>8388608){throw new AuthFailure(413,'Invoice page is too large. Reduce the page size.');}$items[]=$out;}return array('total'=>$total,'page'=>$page,'per_page'=>$per,'items'=>$items,'summary'=>isset($q['currency'])&&$q['currency']!==''?$this->summary(strtoupper($q['currency'])):null);}
    private function statusFilter($status,array &$where,array &$args){$unpaid="amount_paid_minor<total_minor AND status NOT IN ('draft','void')";$paid='(amount_paid_minor>=total_minor AND total_minor>0)';if($status==='paid'){$where[]='('.$paid." OR status='paid')";}elseif($status==='overdue'){$where[]='('.$unpaid.' AND due_date<%s)';$args[]=$this->records->now();}elseif($status==='partial'){$where[]='('.$unpaid.' AND amount_paid_minor>0 AND due_date>=%s)';$args[]=$this->records->now();}elseif(in_array($status,array('draft','void'),true)){$where[]='status=%s';$args[]=$status;}elseif(in_array($status,array('sent','viewed'),true)){$where[]='(status=%s AND NOT '.$paid.')';$args[]=$status;}else{throw new ValidationFailure('Unknown invoice status','status');}}
    private function summary($currency){
        $out=array('invoiced_minor'=>0,'paid_minor'=>0,'outstanding_minor'=>0,'overdue_minor'=>0,'draft_minor'=>0,'invoice_count'=>0,'paid_count'=>0,'overdue_count'=>0,'draft_count'=>0,'partial_count'=>0,'average_invoice_minor'=>0,'by_status'=>array_fill_keys(array('draft','sent','viewed','partial','paid','overdue','void'),0));$last=0;$active=0;
        do{$rows=$this->db->rows('SELECT id,status,total_minor,amount_paid_minor,due_date FROM '.$this->table('invoices').' WHERE id>%d AND currency=%s ORDER BY id LIMIT 100',array($last,$currency));foreach($rows as $row){$r=$this->db->decode('invoices',$row);$last=$r['id'];$s=$this->effective($r);$out['invoice_count']++;$out['by_status'][$s]++;
            if($s==='draft'){$out['draft_minor']+=$r['total_minor'];}elseif($s!=='void'){$active++;$out['invoiced_minor']+=$r['total_minor'];$out['paid_minor']+=min($r['total_minor'],$r['amount_paid_minor']);$out['outstanding_minor']+=max(0,$r['total_minor']-$r['amount_paid_minor']);if($s==='overdue'){$out['overdue_minor']+=max(0,$r['total_minor']-$r['amount_paid_minor']);}}
            foreach(array('invoiced_minor','paid_minor','outstanding_minor','overdue_minor','draft_minor') as $money){if($out[$money]>9007199254740991||$out[$money]<0){throw new AuthFailure(413,'Invoice summary exceeds safe numeric limits.');}}
        }}while(count($rows)===100);foreach(array('paid','overdue','draft','partial') as $s){$out[$s.'_count']=$out['by_status'][$s];}$out['average_invoice_minor']=(int)self::pyRound($out['invoiced_minor']/max(1,$active));return $out;
    }
    private function putSetting($key,$value,$secret=false){
        $r=$this->db->one('system_settings',array('key'=>$key));
        if(!$r){$r=$this->records->create('system_settings',array('key'=>$key,'category'=>'invoice','value'=>null));}
        $stored=$value===null||$value===''?null:(($secret&&$this->config!==null)?$this->config->sealRuntimeSecret($this->db->schema()->table('system_settings').'/'.$r['id'].'/'.$key,$value):(string)$value);
        $this->db->update('system_settings',array('value'=>$stored,'updated_at'=>$this->records->now()),array('id'=>$r['id']));
    }
    private function openSecret($key){
        $r=$this->db->one('system_settings',array('key'=>$key));
        if(!$r||$r['value']===null||$r['value']===''){return '';}
        if($this->config===null){return '';}
        try{return (string)$this->config->openRuntimeSecret($this->db->schema()->table('system_settings').'/'.$r['id'].'/'.$key,$r['value']);}catch(\Throwable $e){return '';}
    }
    private static function maskSecret($s){if($s===''){return '';}$len=strlen($s);return $len<=6?str_repeat('*',$len):substr($s,0,4).str_repeat('*',min(8,$len-6)).substr($s,-2);}
    public function currencies(){
        $def=strtoupper($this->setting('invoice.default_currency','NGN'));if(!in_array($def,self::CURRENCIES,true)){$def='NGN';}
        $list=array();foreach(self::CURRENCIES as $c){$list[]=array('code'=>$c,'symbol'=>self::SYMBOLS[$c]??($c.' '),'label'=>$c);}
        return array('currencies'=>$list,'default'=>$def);
    }
    public function invoiceSettings(){
        $def=strtoupper($this->setting('invoice.default_currency','NGN'));if(!in_array($def,self::CURRENCIES,true)){$def='NGN';}
        $taxRaw=$this->setting('invoice.default_tax_rate','0');$tax=is_numeric($taxRaw)?max(0.0,min(100.0,(float)$taxRaw)):0.0;
        $pub=$this->setting('paystack.public_key','');$sec=$this->openSecret('paystack.secret_key');
        $chRaw=$this->setting('paystack.channels','');$channels=array();
        foreach(explode(',',$chRaw) as $ch){$c=Compat::strip($ch);if($c!==''){$channels[]=$c;}}
        $mode=$sec===''?'not_configured':((strpos($sec,'sk_test_')===0||strpos($sec,'pk_test_')===0)?'test':'live');
        return array(
            'business_name'=>$this->setting('invoice.business_name','Sendify'),
            'business_email'=>$this->setting('invoice.business_email',''),
            'business_phone'=>$this->setting('invoice.business_phone',''),
            'business_address'=>$this->setting('invoice.business_address',''),
            'business_website'=>$this->setting('invoice.business_website',''),
            'business_logo_url'=>$this->setting('invoice.business_logo_url',''),
            'prefix'=>$this->setting('invoice.prefix','INV'),
            'default_currency'=>$def,
            'default_tax_rate'=>self::pyRound($tax,2),
            'default_terms'=>$this->setting('invoice.default_terms','Payment is due by the date shown above. Thank you for your business.'),
            'default_notes'=>$this->setting('invoice.default_notes',''),
            'footer'=>$this->setting('invoice.footer',''),
            'currencies'=>self::CURRENCIES,
            'paystack'=>array('configured'=>$sec!==''&&$pub!=='','secret_key_masked'=>self::maskSecret($sec),'public_key'=>$pub,'channels'=>$channels,'webhook_url'=>'','mode'=>$mode)
        );
    }
    public function saveInvoiceSettings($body){
        if(!is_object($body)){throw new ValidationFailure('Supply a JSON object');}
        $map=array('business_name'=>array('invoice.business_name',255),'business_email'=>array('invoice.business_email',320),'business_phone'=>array('invoice.business_phone',64),'business_address'=>array('invoice.business_address',2000),'business_website'=>array('invoice.business_website',500),'business_logo_url'=>array('invoice.business_logo_url',500),'prefix'=>array('invoice.prefix',12),'default_currency'=>array('invoice.default_currency',10),'default_tax_rate'=>array('invoice.default_tax_rate',0),'default_terms'=>array('invoice.default_terms',10000),'default_notes'=>array('invoice.default_notes',10000),'footer'=>array('invoice.footer',2000),'paystack_public_key'=>array('paystack.public_key',255),'paystack_secret_key'=>array('paystack.secret_key',255),'paystack_channels'=>array('paystack.channels',0));
        foreach($body as $k=>$v){if(!isset($map[$k])){throw new ValidationFailure('Unknown invoice setting',$k);}}
        foreach($map as $prop=>$spec){
            if(!property_exists($body,$prop)){continue;}
            $v=$body->$prop;$dbKey=$spec[0];$cap=$spec[1];
            if($prop==='default_tax_rate'){$num=$v===null?0.0:self::number($v,$prop,0,100);$this->putSetting($dbKey,(string)self::pyRound($num,2));}
            elseif($prop==='default_currency'){$code=strtoupper(self::text((string)($v??'NGN'),$prop,10));if(!in_array($code,self::CURRENCIES,true)){throw new ValidationFailure('Unsupported currency',$prop);}$this->putSetting($dbKey,$code);}
            elseif($prop==='prefix'){$raw=substr(preg_replace('/[^A-Za-z0-9-]/','',Compat::upper(self::text((string)($v??'INV'),$prop,32))),0,12);$this->putSetting($dbKey,$raw!==''?$raw:'INV');}
            elseif($prop==='paystack_channels'){if(!is_array($v)){throw new ValidationFailure('paystack_channels must be a list',$prop);}$clean=array();foreach($v as $ch){$c=Compat::strip(self::text((string)$ch,$prop,40));if($c!==''){$clean[]=$c;}}$this->putSetting($dbKey,implode(',',$clean));}
            elseif($prop==='paystack_secret_key'){if($v!==null){$str=Compat::strip(self::text((string)$v,$prop,255));if($this->config===null&&$str!==''){throw new AuthFailure(503,'Encrypted runtime vault is required to store Paystack secrets.');}$this->putSetting($dbKey,$str,true);}}
            else{$this->putSetting($dbKey,$v===null?'':self::text((string)$v,$prop,$cap));}
        }
        return $this->invoiceSettings();
    }
    public function analytics(array $q){
        $def=strtoupper($this->setting('invoice.default_currency','NGN'));if(!in_array($def,self::CURRENCIES,true)){$def='NGN';}
        $code=isset($q['currency'])&&$q['currency']!==''?strtoupper(DataRoutes::text($q,'currency',$def,null,10)):$def;
        if(!in_array($code,self::CURRENCIES,true)){throw new ValidationFailure('Unsupported currency','currency');}
        $days=DataRoutes::integer($q['days']??365,'days',30,1825);
        $summary=$this->summary($code);
        $nowStr=$this->records->now();$nowDt=new \DateTimeImmutable($nowStr,new \DateTimeZone('UTC'));
        $months=array();$mIdx=array();
        $yr=(int)$nowDt->format('Y');$mo=(int)$nowDt->format('n');
        for($off=11;$off>=0;$off--){
            $y=$yr;$m=$mo-$off;while($m<=0){$m+=12;$y--;}
            $mk=sprintf('%04d-%02d',$y,$m);
            $lbl=(new \DateTimeImmutable(sprintf('%04d-%02d-01 00:00:00',$y,$m),new \DateTimeZone('UTC')))->format('M');
            $mIdx[$mk]=count($months);$months[]=array('month'=>$mk,'label'=>$lbl,'invoiced_minor'=>0,'collected_minor'=>0);
        }
        $customers=array();$dueSoon=0;
        $dueWindow=$nowDt->modify('+14 days')->format('Y-m-d H:i:s.u');
        foreach($this->db->rows('SELECT id,customer_name,status,total_minor,amount_paid_minor,issue_date,due_date,paid_at FROM '.$this->table('invoices').' WHERE currency=%s ORDER BY id DESC LIMIT 10000',array($code)) as $raw){
            $inv=$this->db->decode('invoices',$raw);$eff=$this->effective($inv);
            if($inv['issue_date']!==null&&$eff!=='void'){$ik=substr($inv['issue_date'],0,7);if(isset($mIdx[$ik])){$months[$mIdx[$ik]]['invoiced_minor']+=(int)$inv['total_minor'];}}
            if($inv['paid_at']!==null){$pk=substr($inv['paid_at'],0,7);if(isset($mIdx[$pk])){$months[$mIdx[$pk]]['collected_minor']+=(int)$inv['amount_paid_minor'];}}
            if(!in_array($eff,array('draft','void'),true)){
                $cn=(string)($inv['customer_name']??'Customer');
                if(!isset($customers[$cn])){$customers[$cn]=array('customer_name'=>$cn,'invoiced_minor'=>0,'paid_minor'=>0,'invoices'=>0);}
                $customers[$cn]['invoiced_minor']+=(int)$inv['total_minor'];
                $customers[$cn]['paid_minor']+=min((int)$inv['amount_paid_minor'],(int)$inv['total_minor']);
                $customers[$cn]['invoices']++;
                if($eff!=='paid'&&$inv['due_date']!==null&&$inv['due_date']>=$nowStr&&$inv['due_date']<=$dueWindow){$dueSoon+=max(0,(int)$inv['total_minor']-(int)$inv['amount_paid_minor']);}
            }
        }
        $top=array_values($customers);usort($top,function($a,$b){return $b['paid_minor']<=>$a['paid_minor'];});$top=array_slice($top,0,6);
        $since=$nowDt->modify('-'.$days.' days')->format('Y-m-d H:i:s.u');
        $recent=array();
        foreach($this->db->rows("SELECT p.id,p.invoice_id,p.amount_minor,p.currency,p.provider,p.channel,p.paid_at,i.number AS invoice_number,i.customer_name FROM ".$this->table('invoice_payments').' p JOIN '.$this->table('invoices')." i ON i.id=p.invoice_id WHERE p.status='success' AND p.currency=%s AND p.paid_at>=%s ORDER BY p.paid_at DESC,p.id DESC LIMIT 12",array($code,$since)) as $pr){
            $recent[]=array('id'=>(int)$pr['id'],'invoice_id'=>(int)$pr['invoice_id'],'invoice_number'=>$pr['invoice_number'],'customer_name'=>$pr['customer_name'],'amount_minor'=>(int)$pr['amount_minor'],'currency'=>$pr['currency'],'provider'=>$pr['provider'],'channel'=>$pr['channel'],'paid_at'=>Records::iso($pr['paid_at']));
        }
        return array('currency'=>$code,'summary'=>$summary,'monthly'=>$months,'recent_payments'=>$recent,'top_customers'=>$top,'due_in_14_days_minor'=>$dueSoon,'generated_at'=>Records::iso($nowStr));
    }
    public function allPayments(array $q){
        $page=DataRoutes::integer($q['page']??1,'page',1,1000000);$per=DataRoutes::integer($q['per_page']??25,'per_page',1,100);
        $status=DataRoutes::text($q,'status','success',null,40)?:'success';
        $currency=isset($q['currency'])&&$q['currency']!==''?strtoupper(DataRoutes::text($q,'currency','',null,10)):null;
        if($currency!==null&&!in_array($currency,self::CURRENCIES,true)){throw new ValidationFailure('Unsupported currency','currency');}
        $provider=DataRoutes::text($q,'provider',null,null,60);
        $where=array('p.status=%s');$args=array($status);
        if($currency!==null){$where[]='p.currency=%s';$args[]=$currency;}
        if($provider!==null&&$provider!==''){$where[]='p.provider=%s';$args[]=$provider;}
        $base=' FROM '.$this->table('invoice_payments').' p JOIN '.$this->table('invoices').' i ON i.id=p.invoice_id WHERE '.implode(' AND ',$where);
        $total=(int)$this->db->rows('SELECT COUNT(*) AS n'.$base,$args)[0]['n'];
        $rows=$this->db->rows('SELECT p.*,i.id AS inv_id,i.number AS inv_number,i.customer_name AS inv_customer,i.status AS inv_status,i.total_minor AS inv_total,i.amount_paid_minor AS inv_paid,i.due_date AS inv_due'.$base.' ORDER BY p.paid_at DESC,p.id DESC LIMIT %d OFFSET %d',array_merge($args,array($per,($page-1)*$per)));
        $items=array();
        foreach($rows as $r){
            $rawPay=$r;unset($rawPay['inv_id'],$rawPay['inv_number'],$rawPay['inv_customer'],$rawPay['inv_status'],$rawPay['inv_total'],$rawPay['inv_paid'],$rawPay['inv_due']);
            $po=$this->paymentOut($this->db->decode('invoice_payments',$rawPay));
            $eff=$this->effective(array('status'=>$r['inv_status'],'total_minor'=>(int)$r['inv_total'],'amount_paid_minor'=>(int)$r['inv_paid'],'due_date'=>$r['inv_due']));
            $items[]=array_merge($po,array('invoice_id'=>(int)$r['inv_id'],'invoice_number'=>$r['inv_number'],'customer_name'=>$r['inv_customer'],'invoice_status'=>$eff));
        }
        $cWhere="status='success'".($currency!==null?' AND currency=%s':'');$cArgs=$currency!==null?array($currency):array();
        $collected=(int)$this->db->rows('SELECT COALESCE(SUM(amount_minor),0) AS n FROM '.$this->table('invoice_payments').' WHERE '.$cWhere,$cArgs)[0]['n'];
        $byProv=array();
        foreach($this->db->rows('SELECT provider,COALESCE(SUM(amount_minor),0) AS n FROM '.$this->table('invoice_payments').' WHERE '.$cWhere.' GROUP BY provider',$cArgs) as $bp){$byProv[]=array('provider'=>$bp['provider'],'amount_minor'=>(int)$bp['n']);}
        return array('total'=>$total,'page'=>$page,'per_page'=>$per,'items'=>$items,'total_collected_minor'=>$collected,'by_provider'=>$byProv);
    }
    public function paymentLinks(array $q){
        $q['kind']='link';$per=isset($q['per_page'])?DataRoutes::integer($q['per_page'],'per_page',1,200):50;
        $page=isset($q['page'])?DataRoutes::integer($q['page'],'page',1,1000000):1;
        $where=array("kind='link'");$args=array();
        if(isset($q['status'])&&$q['status']!==''){$this->statusFilter(DataRoutes::text($q,'status','',null,40),$where,$args);}
        $from=' FROM '.$this->table('invoices').' WHERE '.implode(' AND ',$where);
        $total=(int)$this->db->rows('SELECT COUNT(*) AS n'.$from,$args)[0]['n'];
        $rows=$this->db->rows('SELECT *'.$from.' ORDER BY created_at DESC,id DESC LIMIT %d OFFSET %d',array_merge($args,array($per,($page-1)*$per)));
        $items=array();foreach($rows as $r){$items[]=$this->out($this->db->decode('invoices',$r));}
        return array('total'=>$total,'page'=>$page,'per_page'=>$per,'items'=>$items);
    }
    public function rotateLink($id){
        $old=$this->invoice($id);
        $this->change($old,array('public_token'=>bin2hex(random_bytes(16))));
        $this->event($id,'updated','Public payment link rotated');
        return array('success'=>true,'public_url'=>null,'invoice'=>$this->read($id));
    }
    public static function handle($request,array $context,array $user,&$status){
        $path=trim(substr($request->get_route(),strlen('/sender-suite/v1/')),'/');$method=$request->get_method();
        $s=new self($context['db'],null,$context['config']??null);$q=$request->get_query_params();
        if($path==='invoices/currencies'&&$method==='GET'){return $s->currencies();}
        if($path==='invoices/settings'&&$method==='GET'){return $s->invoiceSettings();}
        if($path==='invoices/settings'&&$method==='PUT'){if(($user['role']??'')!=='admin'){throw new AuthFailure(403,'Application administrator role required.');}if(strlen($request->get_body())>65536){throw new AuthFailure(413,'Invoice settings request is too large.');}return $s->saveInvoiceSettings(DataRoutes::body($request));}
        if($path==='invoices/analytics'&&$method==='GET'){return $s->analytics($q);}
        if($path==='invoices/payments'&&$method==='GET'){return $s->allPayments($q);}
        if($path==='invoices/links'&&$method==='GET'){return $s->paymentLinks($q);}
        if($path==='invoices/settings/test'&&$method==='POST'){if(($user['role']??'')!=='admin'){throw new AuthFailure(403,'Application administrator role required.');}return (new InvoiceSettlement($context['db'],$context['config']??null))->testPaystack();}
        if($path==='invoices/links'&&$method==='POST'){return (new InvoiceSettlement($context['db'],$context['config']??null))->createLink(DataRoutes::body($request),$user,$status);}
        if($path==='invoices'){if($method==='GET'){return $s->listing($q);}if($method==='POST'){if(strlen($request->get_body())>1048576){throw new AuthFailure(413,'Invoice request is too large.');}$out=$s->create(DataRoutes::body($request),$user);$status=201;return $out;}}
        if(preg_match('~^invoices/(-?[0-9]+)(?:/(duplicate|void|payments|events|rotate-link|send|remind|pay))?$~D',$path,$m)){
            $id=DataRoutes::integer($m[1],'invoice_id');$action=$m[2]??null;
            if($action===null){if($method==='GET'){return $s->read($id);}if($method==='PATCH'){if(strlen($request->get_body())>1048576){throw new AuthFailure(413,'Invoice request is too large.');}return $s->patch($id,DataRoutes::body($request));}if($method==='DELETE'){return $s->delete($id);}}
            elseif($method==='GET'){if($action==='events'){return $s->events($id,$q['limit']??100);}if($action==='payments'){return $s->payments($id);}}
            elseif($method==='POST'){
                if($action==='duplicate'){$out=$s->duplicate($id,$user);$status=201;return $out;}
                if($action==='void'){return $s->void($id);}
                if($action==='rotate-link'){return $s->rotateLink($id);}
                if($action==='payments'){return (new InvoiceSettlement($context['db'],$context['config']??null))->recordPayment($id,DataRoutes::body($request),$status);}
                if($action==='pay'){return (new InvoiceSettlement($context['db'],$context['config']??null))->initializeCheckout($id);}
                if($action==='send'||$action==='remind'){return (new InvoiceSettlement($context['db'],$context['config']??null))->sendOrRemind($id,$action==='remind',$request->get_body()!==''?DataRoutes::body($request):null);}
            }
        }
        throw new AuthFailure(404,'Application endpoint not found.');
    }
}
