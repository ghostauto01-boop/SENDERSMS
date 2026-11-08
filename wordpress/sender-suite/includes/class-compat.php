<?php
namespace SenderSuite;

/** Explicit Python Unicode15 whitespace/case/decimal behavior, independent of host locale. */
final class Compat {
    private static $unicode;
    private static function data() { if (self::$unicode===null) { self::$unicode=json_decode(file_get_contents(dirname(__DIR__).'/schema/unicode15.json'),true,512,JSON_THROW_ON_ERROR); }return self::$unicode; }
    public static function points($s) { if (!is_string($s) || preg_match('//u',$s)!==1) { throw new \InvalidArgumentException('Expected valid UTF-8 text.'); }$out=array();for ($i=0,$n=strlen($s);$i<$n;) { $b=ord($s[$i++]);if ($b<128) { $out[]=$b;continue; }$length=$b<224?2:($b<240?3:4);$cp=$b&($length===2?31:($length===3?15:7));for ($j=1;$j<$length;$j++) { $cp=($cp<<6)|(ord($s[$i++])&63); }$out[]=$cp; }return $out; }
    public static function chr($cp) { if ($cp<128) { return chr($cp); }if ($cp<2048) { return chr(192|($cp>>6)).chr(128|($cp&63)); }if ($cp<65536) { return chr(224|($cp>>12)).chr(128|(($cp>>6)&63)).chr(128|($cp&63)); }return chr(240|($cp>>18)).chr(128|(($cp>>12)&63)).chr(128|(($cp>>6)&63)).chr(128|($cp&63)); }
    public static function length($s) { return count(self::points($s)); }
    public static function strip($s) { if (!is_string($s)) { throw new \InvalidArgumentException('Expected text.'); }$space='[\x09-\x0D\x1C-\x20\x{0085}\x{00A0}\x{1680}\x{2000}-\x{200A}\x{2028}\x{2029}\x{202F}\x{205F}\x{3000}]';$out=preg_replace('/^'.$space.'+|'.$space.'+$/u','',$s);if ($out===null) { throw new \InvalidArgumentException('Invalid UTF-8.'); }return $out; }
    private static function contains($cp,array $ranges) { $lo=0;$hi=count($ranges)-1;while ($lo<=$hi) { $m=intdiv($lo+$hi,2);if ($cp<$ranges[$m][0]) { $hi=$m-1; }elseif ($cp>$ranges[$m][1]) { $lo=$m+1; }else { return true; } }return false; }
    public static function lower($s) { $d=self::data();$p=self::points($s);$out='';foreach ($p as $i=>$cp) { if ($cp===931) { $before=false;$after=false;for ($j=$i-1;$j>=0;$j--) { if (self::contains($p[$j],$d['ignorable'])) { continue; }$before=self::contains($p[$j],$d['cased']);break; }for ($j=$i+1;$j<count($p);$j++) { if (self::contains($p[$j],$d['ignorable'])) { continue; }$after=self::contains($p[$j],$d['cased']);break; }$out.=self::chr($before && !$after?962:963); }else { foreach ($d['lower'][$cp]??array($cp) as $c) { $out.=self::chr($c); } } }return $out; }
    public static function decimal($cp) { return self::data()['decimal'][$cp]??null; }
    public static function email($raw) { if ($raw===null) { return null; }$value=str_replace(array("\xe2\x80\x8b","\xef\xbb\xbf","\xc2\xa0"),array('','',' '),$raw);$value=self::lower(trim($value," \t\r\n<>\"'.,;:()[]"));$space='\x09-\x0D\x1C-\x20\x{0085}\x{00A0}\x{1680}\x{2000}-\x{200A}\x{2028}\x{2029}\x{202F}\x{205F}\x{3000}';return preg_match('/^[^@'.$space.']+@[^@'.$space.']+\.[A-Za-z]{2,}$/uD',$value)?$value:null; }
}
