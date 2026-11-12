<?php
namespace SenderSuite;

/** Read-only preview service; no writes, provider calls, or delivery side effects. */
final class CsvPreview {
    private static $ascii;private static $aliases;
    public static function customKey($heading) { if (self::$ascii===null) { self::$ascii=json_decode(file_get_contents(dirname(__DIR__).'/schema/unicode15-nfkd-ascii.json'),true,512,JSON_THROW_ON_ERROR); }$s='';foreach (Compat::points($heading) as $cp) { $s.=$cp<128?chr($cp):(self::$ascii[$cp]??''); }$s=trim(preg_replace('/[^A-Za-z0-9]+/','_',strtolower(trim($s))),'_');if ($s==='') { $s='custom_field'; }if ($s[0]>='0' && $s[0]<='9') { $s='field_'.$s; }return $s; }
    public static function mapping(array $headers) { if (self::$aliases===null) { self::$aliases=json_decode(file_get_contents(dirname(__DIR__).'/schema/csv-aliases.json'),true,512,JSON_THROW_ON_ERROR); }$map=new \stdClass();$used=array();foreach ($headers as $header) { $raw=Compat::lower(Compat::strip($header));if ($raw==='') { continue; }$target=null;foreach (self::$aliases as $field=>$aliases) { if (in_array($raw,$aliases,true)) { $target=$field;break; } }if ($target===null) { $base=self::customKey($header);$key=$base;$suffix=2;while (isset($used[$key])) { $key=$base.'_'.($suffix++); }$used[$key]=true;$target='custom:'.$key; }$map->$raw=$target; }return $map; }
    public static function preview($bytes,$maxRows=20) {
        $reader=CsvReader::dictionaries($bytes);$reader->rewind();if (!$reader->valid() || !$reader->current()['headers']) { return array('error'=>'No headers found in CSV','headers'=>array(),'rows'=>array(),'total_rows'=>0); }$headers=array_map(array(Compat::class,'strip'),$reader->current()['headers']);$mapping=self::mapping($headers);$rows=array();$total=0;$channels=array('phone'=>0,'email'=>0,'both'=>0,'email_only'=>0,'phone_only'=>0,'neither'=>0);$reader->next();
        while ($reader->valid()) { $row=$reader->current()['row'];$total++;$clean=new \stdClass();foreach ($row as $k=>$v) { $k=Compat::strip((string)$k);$clean->$k=$v; }if (count($rows)<$maxRows) { $rows[]=$clean; }$lower=new \stdClass();foreach ($clean as $key=>$value) { $key=Compat::lower((string)$key);$lower->$key=$value===null || $value==='' || $value===array()?'':$value; }$phone=false;$email=false;
            foreach ($mapping as $key=>$target) { $value=$lower->$key??'';if ($value==='' || $value===array()) { continue; }if ($target==='phone_number' && Phone::clean($value)!==null) { $phone=true; }elseif ($target==='email' && Compat::email($value)!==null) { $email=true; } }if ($phone) { $channels['phone']++; }if ($email) { $channels['email']++; }$channels[$phone && $email?'both':($email?'email_only':($phone?'phone_only':'neither'))]++;$reader->next();
        }return array('headers'=>$headers,'rows'=>$rows,'total_rows'=>$total,'column_mapping'=>$mapping,'channels'=>$channels,'has_phone_column'=>in_array('phone_number',(array)$mapping,true),'has_email_column'=>in_array('email',(array)$mapping,true));
    }
}
