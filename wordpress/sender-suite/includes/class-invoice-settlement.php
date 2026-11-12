<?php
namespace SenderSuite;

/**
 * Native WordPress Paystack Settlement, Public Invoice Views, Checkout Links,
 * Manual Payment Recording, HMAC-SHA512 Webhooks, and Email/SMS Invoice Delivery.
 */
final class InvoiceSettlement {
    private $db;private $config;private $records;
    public function __construct(Database $db,Config $config=null,callable $clock=null){
        $this->db=$db;$this->config=$config;$this->records=new Records($db,$clock);
    }
    private function table($n){return Schema::identifier($this->db->schema()->table($n));}
    private function paystackSecret(){
        $r=$this->db->one('system_settings',array('key'=>'paystack.secret_key'));
        if(!$r||empty($r['value'])||$this->config===null){return '';}
        try{return (string)$this->config->openRuntimeSecret($this->db->schema()->table('system_settings').'/'.$r['id'].'/paystack.secret_key',$r['value']);}catch(\Throwable $e){return '';}
    }
    private function publicUrl($token){
        $base=function_exists('rest_url')?rtrim((string)rest_url('sender-suite/v1/invoices/public/'.$token),'/'):('/wp-json/sender-suite/v1/invoices/public/'.$token);
        return $base;
    }
    public function testPaystack(){
        $sec=$this->paystackSecret();
        if($sec===''){throw new AuthFailure(400,'Paystack secret key is not configured.');}
        $r=ProviderHttp::request('GET','https://api.paystack.co/bank?perPage=1',array('Authorization'=>'Bearer '.$sec));
        if(empty($r['ok'])){throw new AuthFailure(502,'Paystack connection test failed (HTTP '.(int)($r['http']??0).').');}
        return array('ok'=>true,'mode'=>strpos($sec,'sk_test_')===0?'test':'live','message'=>(string)($r['json']['message']??'Connected'));
    }
    public function createLink($body,array $user,&$status){
        if(!is_object($body)){throw new ValidationFailure('Supply a JSON object');}
        $title=Compat::strip(DataRoutes::text(array('title'=>$body->title??($body->link_title??'Payment Link')),'title','Payment Link',1,255));
        $amount=DataRoutes::integer($body->amount_minor??0,'amount_minor',100,Invoices::MAX_MINOR);
        $currency=strtoupper(Compat::strip(DataRoutes::text(array('currency'=>$body->currency??'NGN'),'currency','NGN',3,10)));
        if(!in_array($currency,Invoices::CURRENCIES,true)){throw new ValidationFailure('Unsupported currency','currency');}
        $email=isset($body->customer_email)&&$body->customer_email!==null?strtolower(Compat::strip((string)$body->customer_email)):'customer@example.com';
        $name=isset($body->customer_name)&&$body->customer_name!==null?Compat::strip((string)$body->customer_name):'Customer';
        $sec=$this->paystackSecret();
        $now=$this->records->now();
        $tok=bin2hex(random_bytes(16));
        $ref='ps_'.bin2hex(random_bytes(10));
        $payLink=$this->publicUrl($tok);
        if($sec!==''){
            $init=ProviderHttp::request('POST','https://api.paystack.co/transaction/initialize',array('Authorization'=>'Bearer '.$sec),array('email'=>$email,'amount'=>$amount,'currency'=>$currency,'reference'=>$ref,'metadata'=>array('link_title'=>$title)));
            if(!empty($init['ok'])&&!empty($init['json']['data']['authorization_url'])){
                $payLink=(string)$init['json']['data']['authorization_url'];
            }
        }
        $inv=$this->db->transaction(function()use($title,$amount,$currency,$email,$name,$tok,$ref,$payLink,$now,$user){
            $num='LNK-'.substr($now,0,4).'-'.strtoupper(substr($tok,0,6));
            $r=$this->records->create('invoices',array('number'=>$num,'public_token'=>$tok,'kind'=>'link','link_title'=>$title,'customer_name'=>$name,'customer_email'=>$email,'currency'=>$currency,'subtotal_minor'=>$amount,'discount_minor'=>0,'tax_rate'=>0.0,'tax_minor'=>0,'total_minor'=>$amount,'amount_paid_minor'=>0,'status'=>'sent','issue_date'=>$now,'sent_at'=>$now,'paystack_reference'=>$ref,'payment_link'=>$payLink,'created_by'=>$user['username']??'operator'));
            $this->records->create('invoice_items',array('invoice_id'=>(int)$r['id'],'description'=>$title,'quantity'=>1.0,'unit_price_minor'=>$amount,'amount_minor'=>$amount,'position'=>0));
            $this->records->create('invoice_events',array('invoice_id'=>(int)$r['id'],'event_type'=>'created','title'=>'Payment link created'));
            return $r;
        });
        $status=201;
        $detail=(new Invoices($this->db,null,$this->config))->read((int)$inv['id']);
        $detail['public_url']=$this->publicUrl($tok);
        $detail['payment_link']=$payLink;
        return array('invoice'=>$detail,'public_url'=>$detail['public_url'],'payment_link'=>$payLink);
    }
    public function recordPayment($invoiceId,$body,&$status){
        if(!is_object($body)){throw new ValidationFailure('Supply a JSON object');}
        $inv=$this->db->one('invoices',array('id'=>$invoiceId));
        if(!$inv){throw new AuthFailure(404,'Invoice not found');}
        if($inv['status']==='void'){throw new AuthFailure(409,'Cannot record payment on a void invoice.');}
        $amount=DataRoutes::integer($body->amount_minor??0,'amount_minor',1,Invoices::MAX_MINOR);
        $channel=Compat::strip(DataRoutes::text(array('channel'=>$body->channel??'bank_transfer'),'channel','bank_transfer',1,40));
        $ref=isset($body->reference)&&Compat::strip((string)$body->reference)!==''?Compat::strip((string)$body->reference):('manual_'.bin2hex(random_bytes(8)));
        $note=isset($body->note)&&$body->note!==null?Compat::strip(DataRoutes::text(array('note'=>$body->note),'note','',null,500)):null;
        if($this->db->one('invoice_payments',array('reference'=>$ref))){throw new AuthFailure(409,'Payment reference already recorded.');}
        $out=$this->db->transaction(function()use($inv,$amount,$channel,$ref,$note){
            $now=$this->records->now();
            $pay=$this->records->create('invoice_payments',array('invoice_id'=>(int)$inv['id'],'provider'=>'manual','reference'=>$ref,'amount_minor'=>$amount,'currency'=>$inv['currency'],'status'=>'success','channel'=>$channel,'payer_email'=>$inv['customer_email'],'note'=>$note,'paid_at'=>$now));
            $newPaid=min(Invoices::MAX_MINOR,((int)$inv['amount_paid_minor'])+$amount);
            $newStatus=$newPaid>=(int)$inv['total_minor']?'paid':'partially_paid';
            $this->db->update('invoices',array('amount_paid_minor'=>$newPaid,'status'=>$newStatus,'paid_at'=>$newStatus==='paid'?$now:$inv['paid_at'],'updated_at'=>$now),array('id'=>$inv['id']));
            $this->records->create('invoice_events',array('invoice_id'=>(int)$inv['id'],'event_type'=>'payment','title'=>'Payment recorded ('.$channel.')'));
            return $pay;
        });
        $status=201;
        return array('payment'=>$out,'invoice'=>(new Invoices($this->db,null,$this->config))->read($invoiceId));
    }
    public function initializeCheckout($invoiceId,$emailOverride=null){
        $inv=$this->db->one('invoices',array('id'=>$invoiceId));
        if(!$inv){throw new AuthFailure(404,'Invoice not found');}
        if(in_array($inv['status'],array('paid','void'),true)){throw new AuthFailure(409,'Invoice is already '.$inv['status'].'.');}
        $sec=$this->paystackSecret();
        if($sec===''){throw new AuthFailure(503,'Paystack secret key is not configured in Invoice Settings.');}
        $due=max(0,((int)$inv['total_minor'])-((int)$inv['amount_paid_minor']));
        if($due<=0){throw new AuthFailure(409,'No balance due on this invoice.');}
        $email=$emailOverride?:($inv['customer_email']?:'customer@example.com');
        $ref='inv_'.$inv['id'].'_'.bin2hex(random_bytes(6));
        $res=ProviderHttp::request('POST','https://api.paystack.co/transaction/initialize',array('Authorization'=>'Bearer '.$sec),array('email'=>$email,'amount'=>$due,'currency'=>$inv['currency'],'reference'=>$ref,'metadata'=>array('invoice_id'=>(int)$inv['id'],'invoice_number'=>$inv['number'])));
        if(empty($res['ok'])||empty($res['json']['data']['authorization_url'])){
            throw new AuthFailure(502,'Paystack checkout initialization failed (HTTP '.(int)($res['http']??0).').');
        }
        $authUrl=(string)$res['json']['data']['authorization_url'];
        $this->db->transaction(function()use($inv,$ref,$authUrl){
            $now=$this->records->now();
            $this->db->update('invoices',array('paystack_reference'=>$ref,'payment_link'=>$authUrl,'status'=>$inv['status']==='draft'?'sent':$inv['status'],'updated_at'=>$now),array('id'=>$inv['id']));
        });
        return array('authorization_url'=>$authUrl,'access_code'=>$res['json']['data']['access_code']??null,'reference'=>$ref,'amount_minor'=>$due,'currency'=>$inv['currency']);
    }
    public function sendOrRemind($invoiceId,$remind=false,$body=null){
        $inv=$this->db->one('invoices',array('id'=>$invoiceId));
        if(!$inv){throw new AuthFailure(404,'Invoice not found');}
        if($inv['status']==='void'){throw new AuthFailure(409,'Cannot send a void invoice.');}
        $pubUrl=$this->publicUrl($inv['public_token']);
        $now=$this->records->now();
        $deliveredChannel=null;
        if(!empty($inv['customer_email'])){
            try{
                $et=new EmailTransport($this->db,$this->config);
                $subj=($remind?'Reminder: ':'').'Invoice '.$inv['number'];
                $msg='Hello '.($inv['customer_name']?:'there').",\n\nPlease view your invoice ".$inv['number'].' here: '.$pubUrl;
                $et->sendEmail(array('to'=>$inv['customer_email'],'subject'=>$subj,'body'=>$msg,'is_test'=>false));
                $deliveredChannel='email';
            }catch(\Throwable $e){}
        }
        $this->db->transaction(function()use($inv,$remind,$pubUrl,$now,$deliveredChannel){
            $patch=array('updated_at'=>$now,'payment_link'=>$inv['payment_link']?:$pubUrl);
            if($inv['status']==='draft'){$patch['status']='sent';$patch['sent_at']=$now;}
            if($remind){$patch['last_reminder_at']=$now;$patch['reminders_sent']=((int)($inv['reminders_sent']??0))+1;}
            elseif(empty($inv['sent_at'])){$patch['sent_at']=$now;}
            $this->db->update('invoices',$patch,array('id'=>$inv['id']));
            $this->records->create('invoice_events',array('invoice_id'=>(int)$inv['id'],'event_type'=>$remind?'reminder':'sent','title'=>($remind?'Invoice reminder prepared':'Invoice issued').($deliveredChannel?' via '.$deliveredChannel:'')));
        });
        $detail=(new Invoices($this->db,null,$this->config))->read($invoiceId);
        $detail['public_url']=$pubUrl;
        return array('ok'=>true,'delivered_via'=>$deliveredChannel,'public_url'=>$pubUrl,'invoice'=>$detail);
    }
    private function settleVerified($inv,$ref,$amountMinor,$currency,$channel,$payerEmail,$gatewayId,$rawMeta){
        return $this->db->transaction(function()use($inv,$ref,$amountMinor,$currency,$channel,$payerEmail,$gatewayId,$rawMeta){
            $existing=$this->db->one('invoice_payments',array('reference'=>$ref));
            if($existing&&$existing['status']==='success'){return array('already_settled'=>true,'payment'=>$existing);}
            $now=$this->records->now();
            if($existing){
                $this->db->update('invoice_payments',array('status'=>'success','amount_minor'=>$amountMinor,'channel'=>$channel,'gateway_id'=>$gatewayId,'paid_at'=>$now),array('id'=>$existing['id']));
                $pay=$this->db->one('invoice_payments',array('id'=>$existing['id']));
            }else{
                $pay=$this->records->create('invoice_payments',array('invoice_id'=>(int)$inv['id'],'provider'=>'paystack','reference'=>$ref,'amount_minor'=>$amountMinor,'currency'=>$currency,'status'=>'success','channel'=>$channel,'gateway_id'=>$gatewayId,'payer_email'=>$payerEmail,'paid_at'=>$now,'raw_json'=>is_string($rawMeta)?$rawMeta:json_encode($rawMeta)));
            }
            $freshInv=$this->db->one('invoices',array('id'=>$inv['id']));
            $newPaid=min(Invoices::MAX_MINOR,((int)$freshInv['amount_paid_minor'])+$amountMinor);
            $newStatus=$newPaid>=(int)$freshInv['total_minor']?'paid':'partially_paid';
            $this->db->update('invoices',array('amount_paid_minor'=>$newPaid,'status'=>$newStatus,'paid_at'=>$newStatus==='paid'?$now:$freshInv['paid_at'],'updated_at'=>$now),array('id'=>$inv['id']));
            $this->records->create('invoice_events',array('invoice_id'=>(int)$inv['id'],'event_type'=>'payment','title'=>'Paystack payment verified ('.$ref.')'));
            return array('already_settled'=>false,'payment'=>$pay);
        });
    }
    public static function dispatchPublic($request){
        $reqId=bin2hex(random_bytes(16));
        try{
            $c=AuthRoutes::context();
            if(!$c['config']->ready()){throw new AuthFailure(503,'Application is not configured.');}
            $svc=new self($c['db'],$c['config']);
            $route=$request->get_route();
            if(!preg_match('~/invoices/public/([A-Za-z0-9_-]+)(?:/(pay|verify))?$~D',$route,$m)){throw new AuthFailure(404,'Invoice not found');}
            $token=$m[1];$sub=$m[2]??null;$method=$request->get_method();
            $inv=$c['db']->one('invoices',array('public_token'=>$token));
            if(!$inv||$inv['status']==='void'){throw new AuthFailure(404,'Public invoice not found.');}
            if($sub===null&&$method==='GET'){
                $c['db']->transaction(function()use($c,$svc,$inv){
                    if(empty($inv['viewed_at'])){
                        $now=$svc->records->now();
                        $c['db']->update('invoices',array('viewed_at'=>$now,'status'=>$inv['status']==='draft'?'sent':$inv['status']),array('id'=>$inv['id']));
                        $svc->records->create('invoice_events',array('invoice_id'=>(int)$inv['id'],'event_type'=>'viewed','title'=>'Public invoice viewed'));
                    }
                });
                $items=$c['db']->rows('SELECT description,quantity,unit_price_minor,amount_minor,position FROM '.Schema::identifier($c['db']->schema()->table('invoice_items')).' WHERE invoice_id=%d ORDER BY position ASC,id ASC',array($inv['id']));
                $fresh=$c['db']->one('invoices',array('id'=>$inv['id']));
                $pub=array('number'=>$fresh['number'],'kind'=>$fresh['kind'],'link_title'=>$fresh['link_title'],'customer_name'=>$fresh['customer_name'],'customer_business'=>$fresh['customer_business'],'currency'=>$fresh['currency'],'subtotal_minor'=>(int)$fresh['subtotal_minor'],'discount_minor'=>(int)$fresh['discount_minor'],'tax_rate'=>(float)$fresh['tax_rate'],'tax_minor'=>(int)$fresh['tax_minor'],'total_minor'=>(int)$fresh['total_minor'],'amount_paid_minor'=>(int)$fresh['amount_paid_minor'],'amount_due_minor'=>max(0,((int)$fresh['total_minor'])-((int)$fresh['amount_paid_minor'])),'status'=>$fresh['status'],'issue_date'=>Records::iso($fresh['issue_date']),'due_date'=>Records::iso($fresh['due_date']),'terms'=>$fresh['terms'],'footer'=>$fresh['footer'],'items'=>$items);
                return AuthRoutes::response(array('invoice'=>$pub),200,$reqId);
            }
            if($sub==='pay'&&$method==='POST'){
                $email=null;if($request->get_body()!==''){$b=DataRoutes::body($request);if(isset($b->email)){$email=Compat::strip((string)$b->email);}}
                return AuthRoutes::response($svc->initializeCheckout((int)$inv['id'],$email),200,$reqId);
            }
            if($sub==='verify'&&$method==='GET'){
                $q=$request->get_query_params();
                $ref=Compat::strip((string)($q['reference']??$inv['paystack_reference']??''));
                if($ref===''){throw new ValidationFailure('Missing payment reference','reference');}
                $sec=$svc->paystackSecret();
                if($sec===''){throw new AuthFailure(503,'Paystack secret key is not configured.');}
                $vr=ProviderHttp::request('GET','https://api.paystack.co/transaction/verify/'.rawurlencode($ref),array('Authorization'=>'Bearer '.$sec));
                if(empty($vr['ok'])||(($vr['json']['data']['status']??'')!=='success')){
                    throw new AuthFailure(400,'Payment has not succeeded on Paystack.');
                }
                $d=$vr['json']['data'];
                $settled=$svc->settleVerified($inv,$ref,(int)($d['amount']??0),strtoupper((string)($d['currency']??$inv['currency'])),(string)($d['channel']??'card'),(string)($d['customer']['email']??$inv['customer_email']),isset($d['id'])?(string)$d['id']:null,array('verified'=>true));
                return AuthRoutes::response(array('ok'=>true,'status'=>'paid','already_settled'=>$settled['already_settled']),200,$reqId);
            }
            throw new AuthFailure(405,'Method not allowed');
        }catch(ValidationFailure $e){
            return AuthRoutes::response(AuthRoutes::error(422,$e->getMessage(),$reqId,$e->field,true),422,$reqId);
        }catch(AuthFailure $e){
            return AuthRoutes::response(AuthRoutes::error($e->status,$e->getMessage(),$reqId),$e->status,$reqId);
        }catch(\Throwable $e){
            return AuthRoutes::response(AuthRoutes::error(503,'Public invoice service unavailable.',$reqId),503,$reqId);
        }
    }
    public static function dispatchPaystackWebhook($request){
        $reqId=bin2hex(random_bytes(16));
        try{
            $raw=$request->get_body();
            if($raw===''||strlen($raw)>262144){throw new AuthFailure(400,'Invalid Paystack webhook payload.');}
            $c=AuthRoutes::context();
            if(!$c['config']->ready()){throw new AuthFailure(503,'Application is not configured.');}
            $svc=new self($c['db'],$c['config']);
            $sec=$svc->paystackSecret();
            if($sec===''){throw new AuthFailure(503,'Paystack webhook secret is not configured.');}
            $sig=strtolower(trim((string)$request->get_header('x-paystack-signature')));
            $expect=hash_hmac('sha512',$raw,$sec);
            if($sig===''||!hash_equals($expect,$sig)){throw new AuthFailure(401,'Invalid X-Paystack-Signature.');}
            $ev=json_decode($raw,true);
            if(!is_array($ev)){throw new AuthFailure(400,'Malformed JSON.');}
            if(($ev['event']??'')!=='charge.success'){return AuthRoutes::response(array('ok'=>true,'ignored'=>true),200,$reqId);}
            $d=$ev['data']??array();
            $ref=(string)($d['reference']??'');
            $invId=isset($d['metadata']['invoice_id'])?(int)$d['metadata']['invoice_id']:0;
            $inv=$invId>0?$c['db']->one('invoices',array('id'=>$invId)):($ref!==''?$c['db']->one('invoices',array('paystack_reference'=>$ref)):null);
            if(!$inv){return AuthRoutes::response(array('ok'=>true,'matched'=>false),200,$reqId);}
            $settled=$svc->settleVerified($inv,$ref!==''?$ref:('ps_wh_'.bin2hex(random_bytes(6))),(int)($d['amount']??0),strtoupper((string)($d['currency']??$inv['currency'])),(string)($d['channel']??'card'),(string)($d['customer']['email']??$inv['customer_email']),isset($d['id'])?(string)$d['id']:null,array('webhook'=>true));
            return AuthRoutes::response(array('ok'=>true,'matched'=>true,'already_settled'=>$settled['already_settled']),200,$reqId);
        }catch(AuthFailure $e){
            return AuthRoutes::response(AuthRoutes::error($e->status,$e->getMessage(),$reqId),$e->status,$reqId);
        }catch(\Throwable $e){
            return AuthRoutes::response(AuthRoutes::error(503,'Paystack webhook failed.',$reqId),503,$reqId);
        }
    }
}
