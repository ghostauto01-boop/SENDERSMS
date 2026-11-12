<?php
namespace SenderSuite;

/**
 * Shared pinned-HTTPS provider client & DNS resolver seam.
 * Honors WP_HTTP_BLOCK_EXTERNAL, WordPress pre_http_request, HTTPS port 443, IP pinning, zero redirects, and bounded payloads.
 * Offline tests inject $transport / $dns via Reflection so zero network traffic ever leaves the test runner.
 */
final class ProviderHttp {
    private static $transport=null;
    private static $dns=null;

    public static function httpsUrl($raw,$field='url'){
        if(!is_string($raw)||$raw===''||strlen($raw)>2000||preg_match('/[\x00-\x20\x7f\\\\]/',$raw)){
            throw new ValidationFailure('Invalid HTTPS provider URL',$field);
        }
        $p=parse_url($raw);
        if(!$p||($p['scheme']??'')!=='https'||empty($p['host'])||isset($p['user'])||isset($p['pass'])||isset($p['fragment'])||(($p['port']??443)!==443)){
            throw new ValidationFailure('Provider requires HTTPS on port 443 without embedded credentials',$field);
        }
        return $raw;
    }

    public static function request($method,$url,array $headers=array(),$payload=null,$timeout=30){
        $method=strtoupper((string)$method);
        if(!in_array($method,array('GET','POST','PUT','PATCH','DELETE'),true)){
            throw new \LogicException('Unsupported HTTP method: '.$method);
        }
        $url=self::httpsUrl($url);
        if(defined('WP_HTTP_BLOCK_EXTERNAL')&&WP_HTTP_BLOCK_EXTERNAL){
            throw new AuthFailure(503,'External HTTP requests are disabled by WP_HTTP_BLOCK_EXTERNAL.');
        }
        $body=null;
        if($payload!==null){
            $body=is_string($payload)?$payload:json_encode($payload,JSON_UNESCAPED_UNICODE|JSON_THROW_ON_ERROR);
        }
        if(function_exists('apply_filters')&&apply_filters('pre_http_request',false,array('method'=>$method,'timeout'=>$timeout,'redirection'=>0,'sslverify'=>true,'blocking'=>true,'headers'=>$headers,'body'=>$body),$url)!==false){
            throw new AuthFailure(503,'External provider request blocked by WordPress HTTP policy.');
        }
        $wireHeaders=array('Accept: application/json','Accept-Encoding: identity');
        $hasContentType=false;
        foreach($headers as $k=>$v){
            $line=is_int($k)?(string)$v:($k.': '.$v);
            if(preg_match('/[\r\n\x00]/',$line)){throw new ValidationFailure('Invalid HTTP header');}
            if(stripos($line,'content-type:')===0){$hasContentType=true;}
            $wireHeaders[]=$line;
        }
        if($body!==null&&!$hasContentType){$wireHeaders[]='Content-Type: application/json';}
        if(self::$transport!==null){
            $res=call_user_func(self::$transport,$method,$url,$wireHeaders,$body);
            if(!is_array($res)){return array('ok'=>false,'http'=>0,'body'=>'','json'=>null);}
            $raw=(string)($res['body']??'');
            $dec=array_key_exists('json',$res)?$res['json']:null;
            if($dec===null&&$raw!==''&&strlen($raw)<=524288){$tmp=json_decode($raw,true);if(json_last_error()===JSON_ERROR_NONE){$dec=$tmp;}}
            return array('ok'=>(bool)($res['ok']??(((int)($res['http']??0))>=200&&((int)($res['http']??0))<300)),'http'=>(int)($res['http']??0),'body'=>$raw,'json'=>$dec);
        }
        $plan=CsvUrl::plan($url);
        if(!function_exists('curl_init')||!defined('CURLOPT_RESOLVE')){
            throw new AuthFailure(503,'Provider HTTPS transport requires PHP cURL with address pinning.');
        }
        $ch=curl_init();if($ch===false){throw new AuthFailure(503,'Provider HTTPS transport is unavailable.');}
        $data='';$headerBytes=0;$invalid=false;
        try{
            $options=array(
                CURLOPT_URL=>$plan['url'],
                CURLOPT_CUSTOMREQUEST=>$method,
                CURLOPT_FOLLOWLOCATION=>false,
                CURLOPT_MAXREDIRS=>0,
                CURLOPT_PROXY=>'',
                CURLOPT_NETRC=>CURL_NETRC_IGNORED,
                CURLOPT_CONNECTTIMEOUT=>10,
                CURLOPT_TIMEOUT=>max(5,min(60,(int)$timeout)),
                CURLOPT_SSL_VERIFYPEER=>true,
                CURLOPT_SSL_VERIFYHOST=>2,
                CURLOPT_HTTPHEADER=>$wireHeaders,
                CURLOPT_USERAGENT=>'SenderSuite-WordPress/1.0',
                CURLOPT_WRITEFUNCTION=>function($c,$chunk)use(&$data,&$invalid){if(strlen($data)+strlen($chunk)>524288){$invalid=true;return 0;}$data.=$chunk;return strlen($chunk);},
                CURLOPT_HEADERFUNCTION=>function($c,$line)use(&$headerBytes,&$invalid){$headerBytes+=strlen($line);if($headerBytes>65536){$invalid=true;return 0;}return strlen($line);}
            );
            if($body!==null){$options[CURLOPT_POSTFIELDS]=$body;}
            if(!$plan['literal']){$ip=strpos($plan['ip'],':')!==false?'['.$plan['ip'].']':$plan['ip'];$options[CURLOPT_RESOLVE]=array($plan['host'].':443:'.$ip);}
            if(defined('CURLOPT_PROTOCOLS_STR')){$options[CURLOPT_PROTOCOLS_STR]='https';}elseif(defined('CURLOPT_PROTOCOLS')){$options[CURLOPT_PROTOCOLS]=CURLPROTO_HTTPS;}
            if(!curl_setopt_array($ch,$options)){throw new AuthFailure(503,'Provider HTTPS transport could not be configured.');}
            $execOk=curl_exec($ch)!==false&&!$invalid;
            $http=(int)curl_getinfo($ch,CURLINFO_HTTP_CODE);
            $dec=null;if($execOk&&$data!==''){$tmp=json_decode($data,true);if(json_last_error()===JSON_ERROR_NONE){$dec=$tmp;}}
            return array('ok'=>$execOk&&$http>=200&&$http<300,'http'=>$http,'body'=>$data,'json'=>$dec);
        }finally{curl_close($ch);}
    }

    public static function dnsRecords($host,$type='TXT'){
        $host=strtolower(rtrim(Compat::strip((string)$host),'.'));
        if($host===''||!preg_match('/^[a-z0-9._-]+$/D',$host)){return array();}
        if(self::$dns!==null){
            $r=call_user_func(self::$dns,$host,strtoupper((string)$type));
            return is_array($r)?$r:array();
        }
        if(defined('WP_HTTP_BLOCK_EXTERNAL')&&WP_HTTP_BLOCK_EXTERNAL){return array();}
        if(!function_exists('dns_get_record')){return array();}
        $flag=$type==='MX'?(defined('DNS_MX')?DNS_MX:16384):($type==='CNAME'?(defined('DNS_CNAME')?DNS_CNAME:16):(defined('DNS_TXT')?DNS_TXT:32768));
        $res=@dns_get_record($host,$flag);
        return is_array($res)?$res:array();
    }
}
