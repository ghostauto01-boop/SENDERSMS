<?php
namespace SenderSuite;

/** Opt-in application document; no site-root rewrites, service worker or WordPress-login bypass. */
final class AppShell {
    public static function requested(array $query){return isset($query['sender_suite_app'])&&is_string($query['sender_suite_app'])&&$query['sender_suite_app']==='1';}
    public static function url(){return add_query_arg('sender_suite_app','1',home_url('/'));}
    public static function boot(){add_action('template_redirect',array(self::class,'serve'),0);}
    public static function document($assetBase,$apiRoot,$outboxEnabled=false){
        foreach(array($assetBase,$apiRoot) as $url){if(!is_string($url)||!filter_var($url,FILTER_VALIDATE_URL)||!in_array(strtolower((string)parse_url($url,PHP_URL_SCHEME)),array('http','https'),true)||parse_url($url,PHP_URL_USER)!==null||parse_url($url,PHP_URL_PASS)!==null){throw new \RuntimeException('Invalid application runtime URL.');}}
        $file=dirname(__DIR__).'/app/index.html';$html=file_get_contents($file);
        if($html===false||strpos($html,'sender-suite-native-ui')===false||strpos($html,'/api/v1')!==false||strpos($html,'serviceWorker.register')!==false){throw new \RuntimeException('Native application assets require a current reviewed build.');}
        $nonce=base64_encode(random_bytes(24));
        $html=preg_replace_callback('/\b(src|href)="\.\/([^"<>]+)"/',function($m)use($assetBase){if(strpos($m[2],'..')!==false||strpos($m[2],'\\')!==false){throw new \RuntimeException('Unsafe application asset path.');}return $m[1].'="'.htmlspecialchars(rtrim($assetBase,'/').'/'.$m[2],ENT_QUOTES|ENT_SUBSTITUTE,'UTF-8').'"';},$html);
        $html=preg_replace('/<script\b/','<script nonce="'.$nonce.'"',$html);
        $runtime=json_encode(array('apiRoot'=>$apiRoot,'assetBase'=>rtrim($assetBase,'/').'/','smsOutboxEnabled'=>$outboxEnabled===true,'productionReady'=>false),JSON_HEX_TAG|JSON_HEX_AMP|JSON_HEX_APOS|JSON_HEX_QUOT|JSON_THROW_ON_ERROR);
        $html=str_replace('<head>','<head><script nonce="'.$nonce.'">window.SENDER_SUITE='.$runtime.';</script>',$html);
        return array('body'=>$html,'headers'=>array('Content-Type'=>'text/html; charset=UTF-8','Cache-Control'=>'no-store, private, max-age=0','X-Content-Type-Options'=>'nosniff','Referrer-Policy'=>'same-origin','X-Frame-Options'=>'SAMEORIGIN','Content-Security-Policy'=>"default-src 'self'; script-src 'self' 'nonce-".$nonce."'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; font-src 'self' data:; connect-src 'self'; worker-src 'none'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'",'Permissions-Policy'=>'camera=(), microphone=(), geolocation=()'));
    }
    public static function serve(){
        if(!self::requested($_GET)){return;}
        if(!in_array($_SERVER['REQUEST_METHOD']??'GET',array('GET','HEAD'),true)){status_header(405);header('Allow: GET, HEAD');header('Cache-Control: no-store');exit;}
        try{$page=self::document(plugins_url('app/',dirname(__DIR__).'/sender-suite.php'),rest_url('sender-suite/v1/'),SmsOutbox::enabled());status_header(200);foreach($page['headers'] as $key=>$value){header($key.': '.$value);}if(($_SERVER['REQUEST_METHOD']??'GET')!=='HEAD'){echo $page['body'];}}
        catch(\Throwable $e){status_header(503);header('Content-Type: text/plain; charset=UTF-8');header('Cache-Control: no-store');echo 'Sender Suite application assets are unavailable. Ask the site administrator to install a current native UI build.';}
        exit;
    }
}
