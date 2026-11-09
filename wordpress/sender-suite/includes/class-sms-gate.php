<?php
namespace SenderSuite;

/** Explicit SMS-Gate transport. No automatic POST retry, no diagnostic SMS, no raw provider data in results. */
final class SmsGate {
    const BASE='https://api.sms-gate.app/3rdparty/v1';
    private static $transport=null; // Reflection-only offline seam, never configured by a request.
    public static function base($raw){
        if($raw!==null&&(!is_string($raw)||preg_match('/[\x00-\x1f\x7f]/',$raw))){throw new ValidationFailure('Invalid gateway URL','base_url');}
        $raw=rtrim(Compat::strip($raw?:self::BASE),'/');$p=parse_url($raw);
        if(strlen($raw)>500||preg_match('/[\x00-\x20\x7f\\\\]/',$raw)||!$p||($p['scheme']??'')!=='https'||empty($p['host'])||isset($p['user'])||isset($p['pass'])||isset($p['query'])||isset($p['fragment'])||(($p['port']??443)!==443)) { throw new ValidationFailure('Gateway requires an HTTPS API root on port 443 without credentials/query/fragment','base_url'); }
        // All addresses, including imported ones, are additionally resolved and pinned at request time.
        return $raw;
    }
    private static function result($outcome,$status=null,$id=null,$http=null){return array('outcome'=>$outcome,'status'=>$status,'provider_message_id'=>$id,'http'=>$http);}
    private static function identifier($v){return is_string($v)&&$v!==''&&strlen($v)<=255&&preg_match('//u',$v)===1&&!preg_match('/[\x00-\x20\x7f]/',$v);}
    public static function state($raw){
        if(!is_string($raw)){return null;}$s=strtolower($raw);
        $map=array('pending'=>'queued','queued'=>'queued','processing'=>'sending','sending'=>'sending','sent'=>'sent','delivered'=>'delivered','failed'=>'failed','cancelled'=>'cancelled','canceled'=>'cancelled');return $map[$s]??null;
    }
    private static function request(array $credentials,$method,$path,$payload=null){
        if(!in_array($method,array('GET','POST'),true)||!preg_match('#^/(?:devices|messages(?:/[A-Za-z0-9%._~-]+)?)$#D',$path)){throw new \LogicException('Unsupported gateway operation');}
        $root=self::base($credentials['base_url']??null);$u=$credentials['username']??'';$p=$credentials['password']??'';
        if(!is_string($u)||!is_string($p)||$u===''||$p===''||strlen($u)>255||strlen($p)>512||strpos($u,':')!==false||preg_match('/[\x00-\x1f\x7f]/',$u.$p)){throw new AuthFailure(503,'Gateway credentials need repair.');}
        if(defined('WP_HTTP_BLOCK_EXTERNAL')&&WP_HTTP_BLOCK_EXTERNAL){throw new AuthFailure(503,'External gateway requests are disabled.');}
        $url=$root.$path;
        if(function_exists('apply_filters')&&apply_filters('pre_http_request',false,array('method'=>$method,'timeout'=>45,'redirection'=>0,'sslverify'=>true,'blocking'=>true,'headers'=>array(),'body'=>null),$url)!==false){throw new AuthFailure(503,'Gateway request is blocked by WordPress HTTP policy.');}
        $plan=CsvUrl::plan($url);$headers=array('Content-Type: application/json','Accept: application/json','Accept-Encoding: identity','Authorization: Basic '.base64_encode($u.':'.$p));$body=$payload===null?null:json_encode($payload,JSON_UNESCAPED_UNICODE|JSON_THROW_ON_ERROR);
        if(self::$transport!==null){$r=call_user_func(self::$transport,$plan,$method,$headers,$body);if(!is_array($r)){return array('http'=>0,'body'=>'','ok'=>false);}return $r;}
        if(!function_exists('curl_init')||!defined('CURLOPT_RESOLVE')){throw new AuthFailure(503,'Gateway transport requires PHP cURL with address pinning.');}
        $ch=curl_init();if($ch===false){throw new AuthFailure(503,'Gateway transport is unavailable.');}$data='';$headerBytes=0;$invalid=false;
        try{
            $options=array(CURLOPT_URL=>$plan['url'],CURLOPT_CUSTOMREQUEST=>$method,CURLOPT_FOLLOWLOCATION=>false,CURLOPT_MAXREDIRS=>0,CURLOPT_PROXY=>'',CURLOPT_NETRC=>CURL_NETRC_IGNORED,CURLOPT_CONNECTTIMEOUT=>10,CURLOPT_TIMEOUT=>45,CURLOPT_SSL_VERIFYPEER=>true,CURLOPT_SSL_VERIFYHOST=>2,CURLOPT_HTTPHEADER=>$headers,CURLOPT_USERAGENT=>'SenderSuite native SMS',CURLOPT_WRITEFUNCTION=>function($c,$chunk)use(&$data,&$invalid){if(strlen($data)+strlen($chunk)>65536){$invalid=true;return 0;}$data.=$chunk;return strlen($chunk);},CURLOPT_HEADERFUNCTION=>function($c,$line)use(&$headerBytes,&$invalid){$headerBytes+=strlen($line);if($headerBytes>65536||preg_match('/^Content-Encoding:\s*(?!identity\s*$)\S+/i',trim($line))){$invalid=true;return 0;}return strlen($line);});
            if($body!==null){$options[CURLOPT_POSTFIELDS]=$body;}
            if(!$plan['literal']){$ip=strpos($plan['ip'],':')!==false?'['.$plan['ip'].']':$plan['ip'];$options[CURLOPT_RESOLVE]=array($plan['host'].':443:'.$ip);}
            if(defined('CURLOPT_PROTOCOLS_STR')){$options[CURLOPT_PROTOCOLS_STR]='https';}else{$options[CURLOPT_PROTOCOLS]=CURLPROTO_HTTPS;}
            if(!curl_setopt_array($ch,$options)){throw new AuthFailure(503,'Gateway transport could not be configured.');}
            $ok=curl_exec($ch)!==false;return array('http'=>(int)curl_getinfo($ch,CURLINFO_HTTP_CODE),'body'=>$data,'ok'=>$ok&&!$invalid);
        }finally{curl_close($ch);}
    }
    private static function decode($r){if(empty($r['ok'])||!isset($r['body'])||!is_string($r['body'])||strlen($r['body'])>65536){return null;}try{$d=json_decode($r['body'],false,32,JSON_THROW_ON_ERROR);}catch(\Throwable $e){return null;}return is_object($d)?$d:null;}
    public static function send(array $credentials,$phone,$text,$sim=1,$device=null){
        if(!is_string($phone)||!preg_match('/^\+[1-9][0-9]{7,14}$/D',$phone)||!is_string($text)||$text===''||Compat::length($text)>1600||preg_match('//u',$text)!==1||strpos($text,"\0")!==false||!in_array($sim,array(1,2),true)){throw new ValidationFailure('Invalid SMS transport payload');}
        if($device!==null&&(!is_string($device)||strlen($device)>120||preg_match('/[\x00-\x1f\x7f]/',$device))){throw new ValidationFailure('Invalid gateway device');}
        $payload=array('textMessage'=>array('text'=>$text),'phoneNumbers'=>array($phone),'simNumber'=>$sim,'ttl'=>3600);if($device!==null&&$device!==''){$payload['deviceId']=$device;}
        $r=self::request($credentials,'POST','/messages',$payload);$http=(int)($r['http']??0);$d=self::decode($r);
        if(!empty($r['ok'])&&in_array($http,array(400,401,403,404,405,413,415,422,429),true)){return self::result('rejected',null,null,$http);}
        if($http>=200&&$http<300&&$d!==null){$id=$d->id??$d->messageId??null;if(self::identifier($id)){return self::result('accepted',self::state($d->state??null)??'queued',$id,$http);}}
        // The gateway may have accepted the POST even if its response was lost. Never invent IDs or retry automatically.
        return self::result('unknown',null,null,$http?:null);
    }
    public static function probe(array $credentials){
        $r=self::request($credentials,'GET','/devices');$http=(int)($r['http']??0);$valid=false;
        if(!empty($r['ok'])&&$http===200&&is_string($r['body']??null)&&strlen($r['body'])<=65536){try{$d=json_decode($r['body'],false,32,JSON_THROW_ON_ERROR);$valid=is_array($d)||(is_object($d)&&is_array($d->devices??null));}catch(\Throwable $e){}}
        return array('success'=>$valid,'online'=>$valid,'http'=>$http?:null,'sends_test_sms'=>false);
    }
    public static function poll(array $credentials,$id){
        if(!self::identifier($id)){throw new ValidationFailure('Invalid provider message identity');}$r=self::request($credentials,'GET','/messages/'.rawurlencode($id));$d=self::decode($r);if((int)($r['http']??0)!==200||$d===null){return self::result('unknown');}$returned=$d->id??$d->messageId??null;if($returned!==$id){return self::result('unknown');}$state=self::state($d->state??null);return self::result($state===null?'unknown':'observed',$state,$id,200);
    }
}
