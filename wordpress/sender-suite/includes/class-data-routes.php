<?php
namespace SenderSuite;

/** Explicit non-sending application endpoints. Never generic table exposure or successful stubs. */
final class DataRoutes {
    public static function definitions() {
        return array_merge(VariableRoutes::definitions(),TemplateRoutes::definitions(),ReplyRoutes::definitions(),NotificationCenter::definitions(),array('/contacts/?'=>array('GET','POST'), '/contacts/tags/?'=>array('GET'), '/contacts/(?P<contact_id>-?[0-9]+)'=>array('GET','PUT'), '/lists/?'=>array('GET','POST'), '/lists/(?P<list_id>-?[0-9]+)'=>array('PUT'), '/lists/(?P<list_id>-?[0-9]+)/contacts'=>array('GET','POST'), '/lists/(?P<list_id>-?[0-9]+)/contacts/add-all'=>array('POST'), '/lists/(?P<list_id>-?[0-9]+)/contacts/remove'=>array('POST'), '/lists/(?P<list_id>-?[0-9]+)/stats'=>array('GET')));
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
            $contacts=new Contacts($c['db']); $lists=new ContactLists($c['db']); $status=200;
            $data=$c['db']->transaction(function()use($path,$method,$q,$request,$contacts,$lists,$c,&$status){
                if ($path==='notifications'||strpos($path,'notifications/')===0) { return NotificationCenter::handle($request,$c); }
                if ($path==='ai/classify'||$path==='autoreply'||strpos($path,'autoreply/')===0||$path==='automations'||strpos($path,'automations/')===0) { return ReplyRoutes::handle($request,$c,$status); }
                if ($path==='templates'||strpos($path,'templates/')===0) { return TemplateRoutes::handle($request,$c,$status); }
                if ($path==='variables'||strpos($path,'variables/')===0) { return VariableRoutes::handle($request,$c,$status); }
                if ($path==='contacts') {
                    if ($method==='POST') { $status=201; return $contacts->create(self::body($request)); }
                    if ($method==='GET') { return $contacts->listing(self::filters($q),self::integer($q['page']??1,'page',1),self::integer($q['per_page']??25,'per_page',1,100)); }
                }
                if ($path==='contacts/tags' && $method==='GET') { return $contacts->tags(); }
                if (preg_match('#^contacts/(-?[0-9]+)$#D',$path,$m)) {
                    $contactId=self::integer($m[1],'contact_id');
                    if ($method==='GET') { return $contacts->out($contacts->find($contactId)); }
                    if ($method==='PUT') { return $contacts->update($contactId,self::body($request)); }
                }
                if ($path==='lists') {
                    if ($method==='GET') { return $lists->listing(self::integer($q['page']??1,'page',1),self::integer($q['per_page']??25,'per_page',1,100),self::text($q,'search')); }
                    if ($method==='POST') {
                        if (!array_key_exists('name',$q)) { throw new ValidationFailure('Field required','name'); }
                        $status=201; return $lists->create(self::text($q,'name',null,1,255),self::text($q,'description'));
                    }
                }
                if (preg_match('#^lists/(-?[0-9]+)(?:/(contacts(?:/(?:add-all|remove))?|stats))?$#D',$path,$m)) {
                    $listId=self::integer($m[1],'list_id'); $action=$m[2]??'';
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
                    if ($action==='contacts/remove' && $method==='POST') {
                        $body=self::body($request);
                        if (!is_object($body)) { throw new ValidationFailure('Input should be a valid dictionary or object to extract fields from'); }
                        $scope=property_exists($body,'scope')?$body->scope:'ids';
                        if (!in_array($scope,array('ids','all'),true)) { throw new ValidationFailure("Input should be 'ids' or 'all'",'scope'); }
                        $search=self::text(isset($body->search)?array('search'=>$body->search):array(),'search');
                        return $lists->remove($listId,self::ids(property_exists($body,'contact_ids')?$body->contact_ids:array()),$scope==='all',$search);
                    }
                }
                throw new AuthFailure(404,'Not Found');
            });
            return AuthRoutes::response($data,$status,$id);
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
