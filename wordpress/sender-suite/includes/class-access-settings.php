<?php
namespace SenderSuite;

/** Optional shared operator credential, not passwordless mode or a user/profile manager. */
final class AccessSettings {
    const HASH='auth.password_hash';
    const REQUIRED='auth.password_required';
    private $db;
    private $auth;
    private $records;
    public function __construct(Database $db,AppAuth $auth,callable $clock=null) {
        $this->db=$db;$this->auth=$auth;$this->records=new Records($db,$clock);
    }
    public static function definitions() { return array('/settings/access'=>array('GET','PUT')); }
    public static function input($body) {
        if(!is_object($body)) { throw new ValidationFailure('Input should be a valid object'); }
        if(!property_exists($body,'password_required')) { throw new ValidationFailure('Field required','password_required'); }
        $required=DataRoutes::boolean($body->password_required,'password_required');
        $password=$body->password??null;
        if($password!==null&&(!is_string($password)||strlen($password)>4096||preg_match('//u',$password)!==1||strpos($password,"\0")!==false)) { throw new ValidationFailure('Invalid bounded password text','password'); }
        return array('password_required'=>$required,'password'=>$password);
    }
    private function flag($row,$hash) {
        if(!$row) { return $hash!==null&&$hash!==''; } // Imported legacy credentials are active: never display them as off.
        $v=strtolower(Compat::strip($row['value']??''));
        if(in_array($v,array('1','true','yes','on'),true)) { return true; }
        if(in_array($v,array('','0','false','no','off'),true)) { return false; }
        throw new AuthFailure(503,'Site access configuration needs repair.');
    }
    public function metadata() {
        $hash=$this->db->one('system_settings',array('key'=>self::HASH))['value']??null;
        return array('password_required'=>$this->flag($this->db->one('system_settings',array('key'=>self::REQUIRED)),$hash),'password_set'=>$hash!==null&&$hash!=='');
    }
    private function save($key,$value,$description,$old) {
        if(!$old) {
            $this->records->create('system_settings',array('key'=>$key,'value'=>$value,'category'=>'auth','description'=>$description));return;
        }
        $stamp=$this->records->now();
        if($stamp<=$old['updated_at']) { $stamp=(new \DateTimeImmutable($old['updated_at'],new \DateTimeZone('UTC')))->modify('+1 microsecond')->format('Y-m-d H:i:s.u'); }
        if($this->db->update('system_settings',array('value'=>$value,'category'=>'auth','description'=>$description,'updated_at'=>$stamp),array('id'=>$old['id'],'value'=>$old['value'],'category'=>$old['category'],'description'=>$old['description'],'updated_at'=>$old['updated_at']))!==1) { throw new AuthFailure(409,'Site access changed. Reload and retry.'); }
    }
    /** Both setting rows are conditionally claimed, hash first, even on repeated saves. Caller owns transaction. */
    public function update($body) {
        $d=self::input($body);$clean=Compat::strip($d['password']??'');
        $hashRow=$this->db->one('system_settings',array('key'=>self::HASH));
        $flagRow=$this->db->one('system_settings',array('key'=>self::REQUIRED));
        $hash=$hashRow['value']??null;
        if(!$d['password_required']) {
            if($clean!=='') { throw new ValidationFailure('Do not supply a new password while disabling site access','password'); }
            $hash=null;
        } else {
            if($clean!=='') {
                $length=Compat::length($clean);
                if($length<4) { throw new AuthFailure(400,'Password must be at least 4 characters'); }
                if($length>128) { throw new AuthFailure(400,'Password must be 128 characters or fewer'); }
                $hash=$this->auth->hashSitePassword($clean);
            }
            if($hash===null||$hash==='') { throw new AuthFailure(400,'Set a password before turning the gate on'); }
        }
        $this->save(self::HASH,$hash,'Site access password (Argon2id)',$hashRow);
        $this->save(self::REQUIRED,$d['password_required']?'true':'false','Enable an alternate shared operator password',$flagRow);
        return $this->metadata();
    }
    public static function handle($request,array $c,array $user,&$cookie) {
        $s=new self($c['db'],$c['auth']);$method=$request->get_method();
        $q=$request->get_query_params();unset($q['rest_route']);
        if($q) { throw new ValidationFailure('Supply access settings only in JSON, never in the URL'); }
        if($method==='GET') { return $s->metadata(); }
        if($method!=='PUT') { throw new AuthFailure(404,'Not found'); }
        if(($user['role']??'')!=='admin') { throw new AuthFailure(403,'Application administrator role required.'); }
        if(strlen($request->get_body())>8192) { throw new AuthFailure(413,'Access settings request is too large.'); }
        $out=$s->update(DataRoutes::body($request));
        if($out['password_required']) {
            $token=$c['auth']->renewSessionToken($user);
            $cookie='sendsms_session='.$token.'; Path=/; Max-Age='.($c['config']->get('ACCESS_TOKEN_EXPIRE_MINUTES')*60).'; HttpOnly; SameSite=lax';
            if($c['config']->get('APP_ENV')==='production') { $cookie.='; Secure'; }
        }
        return $out;
    }
}
