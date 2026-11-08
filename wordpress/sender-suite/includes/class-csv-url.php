<?php
namespace SenderSuite;

/** Public CSV fetcher. No redirects, ambient proxy/cookies, credentials, or unpinned DNS connections. */
final class CsvUrl {
    const LIMIT=20971520;
    private static $resolver=null; // Reflection-only offline DNS seam.
    private static $transport=null; // Reflection-only offline streaming transport seam.
    public static function input($body) {
        if (!is_object($body)) { throw new ValidationFailure('Input should be a valid dictionary or object to extract fields from'); }
        $allowed=array('url','file_name','list_id','new_list_name','skip_duplicates','column_mapping','tags','auto_tag_rules');
        foreach ($body as $key=>$v) { if (!in_array($key,$allowed,true)) { throw new ValidationFailure('Extra inputs are not permitted',$key); } }
        $q=get_object_vars($body);
        if (!array_key_exists('url',$q)) { throw new ValidationFailure('Field required','url'); }
        $url=DataRoutes::text($q,'url',null,8,2048);
        $options=array('list_id'=>null,'new_list_name'=>null,'skip_duplicates'=>true,'column_mapping'=>new \stdClass(),'tags'=>array(),'auto_tag_rules'=>array());
        $filename=isset($q['file_name'])?DataRoutes::text($q,'file_name'):null;
        if (isset($q['new_list_name'])) { $options['new_list_name']=DataRoutes::text($q,'new_list_name'); }
        if (isset($q['list_id'])) { $options['list_id']=DataRoutes::integer($q['list_id'],'list_id'); }
        if (array_key_exists('skip_duplicates',$q)) { $options['skip_duplicates']=DataRoutes::boolean($q['skip_duplicates'],'skip_duplicates'); }
        if (isset($q['column_mapping'])) {
            if (!is_object($q['column_mapping'])) { throw new ValidationFailure('Input should be a valid dictionary','column_mapping'); }
            foreach ($q['column_mapping'] as $key=>$value) { if (!is_string($value)) { throw new ValidationFailure('Input should be a valid string','column_mapping.'.$key); } }
            $options['column_mapping']=$q['column_mapping'];
        }
        if (array_key_exists('auto_tag_rules',$q)) {
            if (!is_array($q['auto_tag_rules'])) { throw new ValidationFailure('Input should be a valid list','auto_tag_rules'); }
            foreach ($q['auto_tag_rules'] as $i=>$rule) { if (!is_object($rule)) { throw new ValidationFailure('Input should be a valid dictionary','auto_tag_rules.'.$i); } }
            $options['auto_tag_rules']=$q['auto_tag_rules'];
        }
        if (isset($q['tags'])) {
            $tags=is_string($q['tags'])?explode(',',$q['tags']):$q['tags'];
            if (!is_array($tags)) { throw new ValidationFailure('tags must be an array of tag names or a comma-separated string','tags'); }
            $clean=array();$seen=array();
            foreach ($tags as $tag) {
                if (!is_string($tag)) { throw new ValidationFailure('each tag must be a string','tags'); }
                $tag=Compat::strip($tag); if ($tag==='') { continue; }
                if (Compat::length($tag)>100) { throw new ValidationFailure('tag names must be 100 characters or fewer','tags'); }
                $key=Compat::lower($tag); if (!isset($seen[$key])) { $clean[]=$tag;$seen[$key]=true; }
            }
            $options['tags']=CsvImport::tags(implode(', ',$clean));
        }
        return array('url'=>$url,'filename'=>$filename,'options'=>$options);
    }
    private static function cidr($packed,$network,$bits) {
        $base=inet_pton($network); if ($base===false || strlen($packed)!==strlen($base)) { return false; }
        $whole=intdiv($bits,8); $rest=$bits%8;
        return substr($packed,0,$whole)===substr($base,0,$whole) && (!$rest || (ord($packed[$whole]) & (255<<(8-$rest)))===(ord($base[$whole]) & (255<<(8-$rest))));
    }
    public static function publicAddress($ip) {
        if (!is_string($ip) || strpos($ip,'%')!==false) { return false; }
        $packed=@inet_pton($ip); if ($packed===false) { return false; }
        if (strlen($packed)===4) {
            foreach (array('0.0.0.0/8','10.0.0.0/8','100.64.0.0/10','127.0.0.0/8','168.63.129.16/32','169.254.0.0/16','172.16.0.0/12','192.0.0.0/24','192.0.2.0/24','192.88.99.0/24','192.168.0.0/16','198.18.0.0/15','198.51.100.0/24','203.0.113.0/24','224.0.0.0/3') as $range) { list($network,$bits)=explode('/',$range); if (self::cidr($packed,$network,(int)$bits)) { return false; } }
            return true;
        }
        // Conservative public global unicast only; mapped/NAT64/transition/special ranges fail closed.
        if (!self::cidr($packed,'2000::',3)) { return false; }
        foreach (array('2001::/23','2001:db8::/32','2002::/16','3fff::/20') as $range) { list($network,$bits)=explode('/',$range); if (self::cidr($packed,$network,(int)$bits)) { return false; } }
        return true;
    }
    private static function resolve($host) {
        if (self::$resolver!==null) { return call_user_func(self::$resolver,$host); }
        if (function_exists('socket_addrinfo_lookup') && function_exists('socket_addrinfo_explain')) {
            $records=@socket_addrinfo_lookup($host,null,array('ai_socktype'=>SOCK_STREAM));$ips=array();
            foreach ($records?:array() as $record) { $data=socket_addrinfo_explain($record);$address=$data['ai_addr']['sin_addr']??($data['ai_addr']['sin6_addr']??null);if ($address!==null) { $ips[]=$address; } }
            return array_values(array_unique($ips));
        }
        if (!function_exists('dns_get_record')) { throw new AuthFailure(503,'Public CSV URL resolution is unavailable on this host'); }
        $pending=array($host);$seen=array();$ips=array();
        while ($pending) {
            $name=array_shift($pending); if (isset($seen[$name])) { continue; } if (count($seen)>=8) { return array(); }$seen[$name]=true;
            $records=@dns_get_record($name,DNS_A|DNS_AAAA|DNS_CNAME);
            foreach ($records?:array() as $record) {
                if (isset($record['ip'])) { $ips[]=$record['ip']; }if (isset($record['ipv6'])) { $ips[]=$record['ipv6']; }
                if (($record['type']??'')==='CNAME') { $pending[]=$record['target']; }
            }
        }
        return array_values(array_unique($ips));
    }
    public static function plan($url) {
        if (preg_match('/[\x00-\x20\x7f\\\\]/',$url)) { throw new AuthFailure(422,'URL must be a public HTTP or HTTPS address'); }
        $p=parse_url($url);
        if (!$p || !in_array(strtolower($p['scheme']??''),array('http','https'),true) || empty($p['host']) || isset($p['user']) || isset($p['pass']) || isset($p['fragment'])) { throw new AuthFailure(422,'URL must be a public HTTP or HTTPS address'); }
        $scheme=strtolower($p['scheme']);$host=strtolower(rtrim($p['host'],'.'));$host=trim($host,'[]');$port=$p['port']??($scheme==='https'?443:80);
        if (!in_array($port,array(80,443),true)) { throw new AuthFailure(422,'CSV URLs must use port 80 or 443'); }
        if ($host==='localhost' || $host==='metadata.google.internal' || preg_match('/\.(localhost|local|internal|test|invalid|example|home\.arpa)$/D',$host)) { throw new AuthFailure(422,'Private or local URLs are not allowed'); }
        $literal=filter_var($host,FILTER_VALIDATE_IP)!==false;
        if (!$literal && (strpos($host,'.')===false || !preg_match('/^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/D',$host) || preg_match('/^(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+))*$/D',$host))) { throw new AuthFailure(422,'URL must be a public HTTP or HTTPS address'); }
        try { $ips=$literal?array($host):self::resolve($host); }
        catch (AuthFailure $e) { throw $e; } catch (\Throwable $e) { throw new AuthFailure(422,'Could not resolve the public CSV URL'); }
        if (!is_array($ips) || !$ips) { throw new AuthFailure(422,'Could not resolve the public CSV URL'); }
        foreach ($ips as $ip) { if (!self::publicAddress($ip)) { throw new AuthFailure(422,'URL host must resolve to a public address'); } }
        $ip=inet_ntop(inet_pton($ips[0])); if ($literal) { $host=$ip; }
        $authority=strpos($host,':')!==false?'['.$host.']':$host;
        return array('url'=>$scheme.'://'.$authority.':'.$port.($p['path']??'/').(isset($p['query'])?'?'.$p['query']:''),'host'=>$host,'port'=>$port,'ip'=>$ip,'literal'=>$literal);
    }
    private static function execute(array $plan,callable $header,callable $write) {
        if (self::$transport!==null) { return call_user_func(self::$transport,$plan,$header,$write); }
        if (!function_exists('curl_init') || !defined('CURLOPT_RESOLVE')) { throw new AuthFailure(503,'Pinned public CSV downloads require PHP cURL support'); }
        $curl=curl_init(); if ($curl===false) { throw new AuthFailure(503,'Public CSV downloads are unavailable'); }
        try {
            $options=array(CURLOPT_URL=>$plan['url'],CURLOPT_FOLLOWLOCATION=>false,CURLOPT_MAXREDIRS=>0,CURLOPT_PROXY=>'',CURLOPT_NETRC=>CURL_NETRC_IGNORED,CURLOPT_CONNECTTIMEOUT=>10,CURLOPT_TIMEOUT=>20,CURLOPT_SSL_VERIFYPEER=>true,CURLOPT_SSL_VERIFYHOST=>2,CURLOPT_HTTPHEADER=>array('Accept: text/csv, text/plain;q=0.9, */*;q=0.1','Accept-Encoding: identity'),CURLOPT_USERAGENT=>'SenderSuite CSV Import',CURLOPT_HEADERFUNCTION=>function($c,$line)use($header){return $header($line);},CURLOPT_WRITEFUNCTION=>function($c,$bytes)use($write){return $write($bytes);});
            if (!$plan['literal']) { $address=strpos($plan['ip'],':')!==false?'['.$plan['ip'].']':$plan['ip'];$options[CURLOPT_RESOLVE]=array($plan['host'].':'.$plan['port'].':'.$address); }
            if (defined('CURLOPT_PROTOCOLS_STR')) { $options[CURLOPT_PROTOCOLS_STR]='http,https'; } else { $options[CURLOPT_PROTOCOLS]=CURLPROTO_HTTP|CURLPROTO_HTTPS; }
            if (!curl_setopt_array($curl,$options)) { return false; }
            return curl_exec($curl)!==false;
        } finally { curl_close($curl); }
    }
    public static function fetch(array $input) {
        // Honor WordPress' external-network kill switch even though the pinned transport uses cURL directly.
        if (defined('WP_HTTP_BLOCK_EXTERNAL') && WP_HTTP_BLOCK_EXTERNAL) { throw new AuthFailure(503,'External CSV downloads are disabled on this WordPress host'); }
        // Honor explicit WordPress HTTP short-circuit policies without trusting cached/unpinned response bytes.
        if (function_exists('apply_filters') && apply_filters('pre_http_request',false,array('method'=>'GET','timeout'=>20,'redirection'=>0,'httpversion'=>'1.1','user-agent'=>'SenderSuite CSV Import','reject_unsafe_urls'=>true,'blocking'=>true,'headers'=>array('Accept-Encoding'=>'identity'),'cookies'=>array(),'body'=>null,'compress'=>false,'decompress'=>false,'sslverify'=>true,'sslcertificates'=>null,'stream'=>false,'filename'=>null,'limit_response_size'=>self::LIMIT+1),$input['url'])!==false) { throw new AuthFailure(503,'Public CSV downloads are blocked by WordPress HTTP policy'); }
        $plan=self::plan($input['url']);$status=0;$body='';$tooLarge=false;$badEncoding=false;$headerBytes=0;
        $header=function($line)use(&$status,&$tooLarge,&$badEncoding,&$headerBytes){
            $headerBytes+=strlen($line); if ($headerBytes>65536) { return 0; }
            if (preg_match('#^HTTP/[^ ]+ ([0-9]{3})(?: |\r|\n)#',$line,$m)) { $status=(int)$m[1]; }
            if (preg_match('/^Content-Length:\s*([0-9]+)\s*$/iD',trim($line),$m) && $status>=200 && $status<300 && (float)$m[1]>self::LIMIT) { $tooLarge=true;return 0; }
            if (preg_match('/^Content-Encoding:\s*(.*)$/iD',trim($line),$m) && strtolower(trim($m[1]))!=='identity') { $badEncoding=true;return 0; }
            return strlen($line);
        };
        $write=function($chunk)use(&$body,&$tooLarge,&$status){if ($status<200 || $status>=300) { return 0; }if (strlen($body)+strlen($chunk)>self::LIMIT) { $tooLarge=true;return 0; }$body.=$chunk;return strlen($chunk);};
        try { $ok=self::execute($plan,$header,$write); } catch (AuthFailure $e) { throw $e; } catch (\Throwable $e) { $ok=false; }
        if ($tooLarge) { throw new AuthFailure(413,'CSV file is larger than 20 MB'); }
        if ($status!==0 && ($status<200 || $status>=300)) { throw new AuthFailure(422,'CSV URL returned HTTP '.$status); }
        if (!$ok || $badEncoding || $headerBytes>65536 || $status===0) { throw new AuthFailure(422,'Could not download CSV from the provided URL'); }
        return array('bytes'=>$body,'filename'=>$input['filename']?:explode('.',$plan['host'])[0].'.csv','options'=>$input['options']);
    }
}
