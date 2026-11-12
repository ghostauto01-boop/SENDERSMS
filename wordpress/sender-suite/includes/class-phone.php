<?php
namespace SenderSuite;

final class Phone {
    private static $loaded=false;
    private static function load() { if (self::$loaded) { return; }$root=dirname(__DIR__).'/vendor/';spl_autoload_register(function($class)use($root) { $prefixes=array('SenderSuite\\Vendor\\Phone\\'=>'phone/src/','SenderSuite\\Vendor\\Locale\\'=>'locale/src/','SenderSuite\\Vendor\\Mbstring\\'=>'mbstring/');foreach ($prefixes as $prefix=>$dir) { if (strpos($class,$prefix)===0) { $file=$root.$dir.str_replace('\\','/',substr($class,strlen($prefix))).'.php';if (is_file($file)) { require_once $file; }return; } } });require_once $root.'mbstring/bootstrap.php';self::$loaded=true; }
    public static function clean($raw) { if ($raw===null) { return null; }$text=Compat::strip($raw);if ($text==='' || in_array(Compat::lower($text),array('-','n/a','na','none','null','nil','0','x'),true)) { return null; }$clean='';foreach (Compat::points($text) as $cp) { if ($cp===43 || Compat::decimal($cp)!==null) { $clean.=Compat::chr($cp); } }if ($clean==='') { return null; }$clean=strpos($clean,'+')===0?'+'.str_replace('+','',substr($clean,1)):str_replace('+','',$clean);
        if (strpos($clean,'+234')===0) { $number=$clean; }elseif (strpos($clean,'234')===0) { $number='+'.$clean; }elseif (strpos($clean,'0')===0) { $number='+234'.substr($clean,1); }elseif (Compat::length($clean)===10 && in_array(substr($clean,0,1),array('7','8','9'),true)) { $number='+234'.$clean; }else { $number=$clean; }self::load();$util=\SenderSuite\Vendor\Phone\PhoneNumberUtil::getInstance();try { $parsed=$util->parse($number,$number===$clean && strpos($clean,'+234')!==0?'NG':null);return $util->isValidNumber($parsed)?$util->format($parsed,\SenderSuite\Vendor\Phone\PhoneNumberFormat::E164):null; }catch (\SenderSuite\Vendor\Phone\NumberParseException $e) { return null; }
    }
}
