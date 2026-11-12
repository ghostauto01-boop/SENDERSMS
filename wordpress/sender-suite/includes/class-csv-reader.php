<?php
namespace SenderSuite;
final class CsvFailure extends \RuntimeException {}
/** CPython comma/excel reader, permissive quotes, exact CR/LF and field-limit behavior. */
final class CsvReader {
    public static function utf8($bytes) {
        if (substr($bytes,0,3)==="\xef\xbb\xbf") { $bytes=substr($bytes,3); }$out='';$len=strlen($bytes);
        for ($i=0;$i<$len;) { $b=ord($bytes[$i]);if ($b<128) { $out.=$bytes[$i++];continue; }$n=$b>=194 && $b<=223?2:($b>=224 && $b<=239?3:($b>=240 && $b<=244?4:0));if (!$n) { $out.="\xef\xbf\xbd";$i++;continue; }$j=1;
            while ($j<$n && $i+$j<$len) { $c=ord($bytes[$i+$j]);$lo=128;$hi=191;if ($j===1) { if ($b===224) { $lo=160; }elseif ($b===237) { $hi=159; }elseif ($b===240) { $lo=144; }elseif ($b===244) { $hi=143; } }if ($c<$lo || $c>$hi) { break; }$j++; }if ($j===$n) { $out.=substr($bytes,$i,$n);$i+=$n; }else { $out.="\xef\xbf\xbd";$i+=$j; }
        }return $out;
    }
    public static function records($bytes) { $text=self::utf8($bytes);$length=strlen($text);$state='record';$row=array();$field='';$size=0;
        for ($i=0;$i<$length;) { $b=ord($text[$i]);$n=$b<128?1:($b<224?2:($b<240?3:4));$ch=substr($text,$i,$n);$i+=$n;
            if ($state==='cr') { if ($ch==="\r") { continue; }if ($ch==="\n") { yield $row;$row=array();$state='record';continue; }throw new CsvFailure('new-line character seen in unquoted field'); }
            if ($state==='record') { if ($ch==="\n") { yield array();continue; }if ($ch==="\r") { $state='cr';continue; }$state='start'; }
            if ($state==='quoted') { if ($ch==='"') { $state='quote';continue; } }
            elseif ($state==='quote' && $ch==='"') { $state='quoted'; }
            elseif ($ch===',' || $ch==="\r" || $ch==="\n") { $row[]=$field;$field='';$size=0;if ($ch===',') { $state='start'; }elseif ($ch==="\n") { yield $row;$row=array();$state='record'; }else { $state='cr'; }continue; }
            elseif ($state==='start' && $ch==='"') { $state='quoted';continue; }else { $state='field'; }
            if (++$size>131072) { throw new CsvFailure('field larger than field limit (131072)'); }$field.=$ch;
        }if ($state==='cr') { yield $row; }elseif ($state!=='record') { $row[]=$field;yield $row; }
    }
    public static function dictionaries($bytes) { $records=self::records($bytes);$records->rewind();if (!$records->valid()) { return; }$headers=$records->current();yield array('headers'=>$headers);$records->next();while ($records->valid()) { $row=$records->current();if (!$row) { $records->next();continue; }$obj=new \stdClass();foreach ($headers as $i=>$name) { $obj->$name=array_key_exists($i,$row)?$row[$i]:null; }if (count($row)>count($headers)) { $obj->{''}=array_slice($row,count($headers)); }yield array('row'=>$obj);$records->next(); } }
}
