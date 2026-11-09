<?php
namespace SenderSuite;

/** Explicit non-sending application endpoints. Never generic table exposure or successful stubs. */
final class DataRoutes {
    public static function definitions() {
        return array_merge(InboxReply::definitions(),SendHistory::definitions(),SmsOutbox::definitions(),GatewayPhones::definitions(),CampaignReports::definitions(),Dashboard::definitions(),AccessSettings::definitions(),Followups::definitions(),SmsReceipts::definitions(),Inbox::definitions(),Invoices::definitions(),Calendar::definitions(),RuntimeSettings::definitions(),ContactExport::definitions(),Sequences::definitions(),Analytics::definitions(),VariableRoutes::definitions(),TemplateRoutes::definitions(),ReplyRoutes::definitions(),NotificationCenter::definitions(),ImportRoutes::definitions(),array('/contacts/?'=>array('GET','POST'), '/contacts/tags/?'=>array('GET'), '/contacts/(?P<contact_id>-?[0-9]+)'=>array('GET','PUT','DELETE'), '/contacts/bulk'=>array('POST'), '/contacts/clean'=>array('POST'), '/contacts/quarantine/generated'=>array('POST'), '/contacts/(?P<contact_id>-?[0-9]+)/activity'=>array('GET'), '/contacts/(?P<contact_id>-?[0-9]+)/(?P<action>email-opt-in|email-opt-out|sms-opt-in)'=>array('POST'), '/lists/?'=>array('GET','POST'), '/lists/(?P<list_id>-?[0-9]+)'=>array('PUT','DELETE'), '/lists/(?P<list_id>-?[0-9]+)/contacts/delete'=>array('POST'), '/lists/(?P<list_id>-?[0-9]+)/clean'=>array('POST'), '/lists/(?P<list_id>-?[0-9]+)/contacts'=>array('GET','POST'), '/lists/(?P<list_id>-?[0-9]+)/contacts/add-all'=>array('POST'), '/lists/(?P<list_id>-?[0-9]+)/contacts/remove'=>array('POST'), '/lists/(?P<list_id>-?[0-9]+)/stats'=>array('GET')));
    }
    public static function register() {
        foreach (self::definitions() as $path=>$methods) {
            $spec=array(); foreach ($methods as $method) { $spec[]=array('methods'=>$method,'permission_callback'=>'__return_true','callback'=>array(self::class,'dispatch')); }
            register_rest_route('sender-suite/v1',$path,$spec);
        }
    }
    public static function integer($v,$field,$min=null,$max=null) {
        $out=null;
        if (is_bool($v) || is_int($v)) { $out=(int)$v; }
        elseif (is_float($v) && is_finite($v) && floor($v)===$v && $v<PHP_INT_MAX && $v>PHP_INT_MIN) { $out=(int)$v; }
        elseif (is_string($v)) {
            $s=Compat::strip($v);
            if (preg_match('/^[+-]?[0-9]+(?:\.0+)?$/D',$s)) {
                $s=preg_replace('/\.0+$/','',$s); $n=(float)$s;
                if ($n<PHP_INT_MAX && $n>PHP_INT_MIN) { $out=(int)$s; }
            }
        }
        if ($out===null) { throw new ValidationFailure('Input should be a valid integer, unable to parse string as an integer',$field); }
        if ($min!==null && $out<$min) { throw new ValidationFailure('Input should be greater than or equal to '.$min,$field); }
        if ($max!==null && $out>$max) { throw new ValidationFailure('Input should be less than or equal to '.$max,$field); }
        return $out;
    }
    public static function boolean($value,$field){if(is_bool($value)){return $value;}if(in_array($value,array(0,1,0.0,1.0),true)){return (bool)$value;}if(is_string($value)&&in_array(strtolower($value),array('0','1','false','true','f','t','off','on','no','yes','n','y'),true)){return in_array(strtolower($value),array('1','true','t','on','yes','y'),true);}throw new ValidationFailure('Input should be a valid boolean, unable to interpret input',$field);}
    public static function ids($value,$field='contact_ids') {
        if (!is_array($value)) { throw new ValidationFailure('Input should be a valid list',$field); }
        $out=array(); foreach ($value as $i=>$v) { $out[]=self::integer($v,$field.'.'.$i); } return $out;
    }
    public static function text(array $q,$name,$default=null,$min=null,$max=null) {
        if (!array_key_exists($name,$q)) { return $default; }
        $v=$q[$name];
        if (!is_string($v)) { throw new ValidationFailure('Input should be a valid string',$name); }
        $n=Compat::length($v);
        if ($min!==null && $n<$min) { throw new ValidationFailure('String should have at least '.$min.' character',$name); }
        if ($max!==null && $n>$max) { throw new ValidationFailure('String should have at most '.$max.' characters',$name); }
        return $v;
    }
    private static function filters(array $q) {
        $out=array(); foreach (array('search','lead_status','tag','undeliverable','email_state','channel','sort_by','sort_dir') as $name) { $out[$name]=self::text($q,$name); }
        foreach (array('list_id','exclude_list_id') as $name) { if (array_key_exists($name,$q) && $q[$name]!==null) { $out[$name]=self::integer($q[$name],$name); } }
        return $out;
    }
    public static function body($request) {
        $raw=$request->get_body();
        if ($raw==='') { throw new ValidationFailure('Field required'); }
        try { return json_decode($raw,false,512,JSON_THROW_ON_ERROR); } catch (\Throwable $e) { throw new ValidationFailure('JSON decode error'); }
    }
    public static function dispatch($request) {
        $id=bin2hex(random_bytes(16));
        try {
            if (!AuthRoutes::origin($request)) { throw new AuthFailure(403,'Cross-origin request rejected'); }
            $c=AuthRoutes::context();
            if (!$c['config']->ready()) { throw new AuthFailure(503,'Application credentials are not configured. Open WordPress Settings > Sender Suite.'); }
            $user=$c['auth']->current(isset($_COOKIE['sendsms_session'])?wp_unslash($_COOKIE['sendsms_session']):null);
            if ($user===null) { throw new AuthFailure(401,'Not authenticated'); }
            $path=trim(substr($request->get_route(),strlen('/sender-suite/v1/')),'/'); $method=$request->get_method(); $q=$request->get_query_params();
            if (in_array($path,array('contacts/import/jobs','contacts/import/url'),true) && $method==='POST' && !ImportJobs::ensureSchedule()) { throw new AuthFailure(503,'WordPress could not schedule the import queue.'); }
            if (($path==='send'||preg_match('~^inbox/conversations/-?[0-9]+/reply$~D',$path)) && $method==='POST' && SmsOutbox::enabled() && $user['role']==='admin' && !SmsOutbox::schedule()) { throw new AuthFailure(503,'WordPress could not schedule SMS delivery.'); }
            $remote=$path==='contacts/import/url' && $method==='POST'?CsvUrl::fetch(CsvUrl::input(self::body($request))):null;
            $contacts=new Contacts($c['db']); $lists=new ContactLists($c['db']); $actions=new ContactActions($c['db']); $status=200; $accessCookie=null;
            $data=$c['db']->transaction(function()use($path,$method,$q,$request,$contacts,$lists,$actions,$c,$user,$remote,&$status,&$accessCookie){
                if ($remote!==null) { $status=202;return ImportJobs::out((new ImportJobs($c['db']))->create($remote['bytes'],$remote['filename'],$remote['options'],$user)); }
                if (strpos($path,'contacts/import/')===0) { return ImportRoutes::handle($request,$c,$status,$user); }
                if (preg_match('~^inbox/conversations/-?[0-9]+/reply$~D',$path)) { return InboxReply::handle($request,$c,$user,$status); }
                if ($path==='send/history') { return SendHistory::handle($request,$c); }
                if ($path==='send'||strpos($path,'send/')===0) { return SmsOutbox::handle($request,$c,$user,$status); }
                if ($path==='settings/gateway/phones'||strpos($path,'settings/gateway/phones/')===0) { return GatewayPhones::handle($request,$c,$user); }
                if ($path==='settings/access') { return AccessSettings::handle($request,$c,$user,$accessCookie); }
                if ($path==='settings/gateway/webhook-signing') { return SmsReceipts::settings($request,$c,$user); }
                if (strpos($path,'settings/')===0) { return RuntimeSettings::handle($request,$c,$user); }
                if ($path==='followups'||strpos($path,'followups/')===0) { return Followups::handle($request,$c,$status); }
                if (strpos($path,'inbox/')===0) { return Inbox::handle($request,$c); }
                if ($path==='invoices'||strpos($path,'invoices/')===0) { return Invoices::handle($request,$c,$user,$status); }
                if ($path==='calendar'||strpos($path,'calendar/')===0) { return Calendar::handle($request,$c,$user,$status); }
                if ($path==='sequences'||strpos($path,'sequences/')===0) { return Sequences::handle($request,$c,$status); }
                if ($path==='campaigns'||strpos($path,'campaigns/')===0) { return CampaignReports::handle($request,$c); }
                if (strpos($path,'dashboard/')===0) { return Dashboard::handle($request,$c); }
                if (strpos($path,'analytics/')===0) { return Analytics::handle($request,$c); }
                if ($path==='notifications'||strpos($path,'notifications/')===0) { return NotificationCenter::handle($request,$c); }
                if ($path==='ai/classify'||$path==='autoreply'||strpos($path,'autoreply/')===0||$path==='automations'||strpos($path,'automations/')===0) { return ReplyRoutes::handle($request,$c,$status); }
                if ($path==='templates'||strpos($path,'templates/')===0) { return TemplateRoutes::handle($request,$c,$status); }
                if ($path==='variables'||strpos($path,'variables/')===0) { return VariableRoutes::handle($request,$c,$status); }
                if (in_array($path,array('contacts/export','contacts/export/csv'),true) && $method==='GET') { return ContactExport::handle($request,$c); }
                if ($path==='contacts/bulk' && $method==='POST') { return $actions->bulk(self::body($request)); }
                if ($path==='contacts/clean' && $method==='POST') { return $actions->clean(null,false,array_key_exists('delete_bad',$q)?self::boolean($q['delete_bad'],'delete_bad'):false); }
                if ($path==='contacts/quarantine/generated' && $method==='POST') { return $actions->quarantine(array_key_exists('dry_run',$q)?self::boolean($q['dry_run'],'dry_run'):true); }
                if (preg_match('#^contacts/(-?[0-9]+)/(email-opt-in|email-opt-out|sms-opt-in|activity)$#D',$path,$m)) {
                    $contactId=self::integer($m[1],'contact_id');
                    if ($m[2]==='activity' && $method==='GET') { return $actions->activity($contactId,self::integer($q['page']??1,'page',1),self::integer($q['per_page']??20,'per_page',1,100)); }
                    if ($method==='POST' && $m[2]==='sms-opt-in') { return $actions->smsOptIn($contactId,self::body($request)); }
                    if ($method==='POST' && in_array($m[2],array('email-opt-in','email-opt-out'),true)) { return $actions->emailPermission($contactId,$m[2]==='email-opt-in',self::text($q,'reason')); }
                }
                if (preg_match('#^lists/(-?[0-9]+)/clean$#D',$path,$m) && $method==='POST') { return $actions->clean(self::integer($m[1],'list_id'),array_key_exists('remove_from_list',$q)?self::boolean($q['remove_from_list'],'remove_from_list'):true,array_key_exists('delete_contacts',$q)?self::boolean($q['delete_contacts'],'delete_contacts'):false); }
                if ($path==='contacts') {
                    if ($method==='POST') { $status=201; return $contacts->create(self::body($request)); }
                    if ($method==='GET') { return $contacts->listing(self::filters($q),self::integer($q['page']??1,'page',1),self::integer($q['per_page']??25,'per_page',1,100)); }
                }
                if ($path==='contacts/tags' && $method==='GET') { return $contacts->tags(); }
                if (preg_match('#^contacts/(-?[0-9]+)$#D',$path,$m)) {
                    $contactId=self::integer($m[1],'contact_id');
                    if ($method==='GET') { return $contacts->out($contacts->find($contactId)); }
                    if ($method==='PUT') { return $contacts->update($contactId,self::body($request)); }
                    if ($method==='DELETE') { $status=204;return $actions->deleteOne($contactId); }
                }
                if ($path==='lists') {
                    if ($method==='GET') { return $lists->listing(self::integer($q['page']??1,'page',1),self::integer($q['per_page']??25,'per_page',1,100),self::text($q,'search')); }
                    if ($method==='POST') {
                        if (!array_key_exists('name',$q)) { throw new ValidationFailure('Field required','name'); }
                        $status=201; return $lists->create(self::text($q,'name',null,1,255),self::text($q,'description'));
                    }
                }
                if (preg_match('#^lists/(-?[0-9]+)(?:/(contacts(?:/(?:add-all|remove|delete))?|stats))?$#D',$path,$m)) {
                    $listId=self::integer($m[1],'list_id'); $action=$m[2]??'';
                    if ($action==='' && $method==='DELETE') { $status=204;return $actions->deleteList($listId,array_key_exists('delete_contacts',$q)?self::boolean($q['delete_contacts'],'delete_contacts'):false); }
                    if ($action==='' && $method==='PUT') { return $lists->update($listId,self::text($q,'name',null,1,255),self::text($q,'description')); }
                    if ($action==='stats' && $method==='GET') { return $lists->stats($listId); }
                    if ($action==='contacts' && $method==='GET') { return $lists->members($listId,self::integer($q['page']??1,'page',1),self::integer($q['per_page']??25,'per_page',1,100),self::text($q,'search')); }
                    if ($action==='contacts' && $method==='POST') { return $lists->add($listId,self::ids(self::body($request))); }
                    if ($action==='contacts/add-all' && $method==='POST') {
                        $body=self::body($request);
                        if (!is_object($body)) { throw new ValidationFailure('Input should be a valid dictionary or object to extract fields from'); }
                        $q=array(); foreach (array('search','lead_status','channel','email_state','list_id') as $key) { if (property_exists($body,$key) && $body->$key!==null) { $q[$key]=$body->$key; } }
                        return $lists->addAll($listId,self::filters($q));
                    }
                    if (in_array($action,array('contacts/remove','contacts/delete'),true) && $method==='POST') {
                        $body=self::body($request);
                        if (!is_object($body)) { throw new ValidationFailure('Input should be a valid dictionary or object to extract fields from'); }
                        $scope=property_exists($body,'scope')?$body->scope:'ids';
                        if (!in_array($scope,array('ids','all'),true)) { throw new ValidationFailure("Input should be 'ids' or 'all'",'scope'); }
                        $search=self::text(isset($body->search)?array('search'=>$body->search):array(),'search');
                        if ($action==='contacts/delete') { return $actions->deleteMembers($listId,self::ids(property_exists($body,'contact_ids')?$body->contact_ids:array()),$scope==='all',$search); }
                        return $lists->remove($listId,self::ids(property_exists($body,'contact_ids')?$body->contact_ids:array()),$scope==='all',$search);
                    }
                }
                throw new AuthFailure(404,'Not Found');
            });
            $response=AuthRoutes::response($data,$status,$id);
            if ($accessCookie!==null) { $response->header('Set-Cookie',$accessCookie); }
            if ($data instanceof ImportErrorReport || $data instanceof ContactCsvDownload) { $response->header('Content-Type','text/csv; charset=UTF-8'); $response->header('Content-Disposition','attachment; filename="'.$data->filename.'"'); $response->header('X-Content-Type-Options','nosniff'); }
            return $response;
        } catch (ValidationFailure $e) {
            return AuthRoutes::response(AuthRoutes::error(422,$e->getMessage(),$id,$e->field,true),422,$id);
        } catch (AuthFailure $e) {
            $body=AuthRoutes::error($e->status,$e->getMessage(),$id);
            if ($e instanceof DetailedFailure) { foreach ($e->detail as $k=>$v) { if (in_array($k,array('code','field','hint'),true) || !array_key_exists($k,$body)) { $body[$k]=$v; } } }
            if ($e instanceof ListNameConflict) { $body['code']='LIST_NAME_EXISTS'; $body['existing_id']=$e->existingId; }
            return AuthRoutes::response($body,$e->status,$id);
        } catch (\Throwable $e) {
            return AuthRoutes::response(AuthRoutes::error(503,'Application service is unavailable.',$id),503,$id);
        }
    }
}
