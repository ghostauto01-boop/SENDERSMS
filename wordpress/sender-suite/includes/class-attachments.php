<?php
namespace SenderSuite;

/** Attachment payload validation/storage only. No file writes, URL fetches or provider calls. */
final class Attachments {
    public static function truth($v) {
        if (is_string($v)) { return $v!==''; }
        if (is_object($v)) { return count(get_object_vars($v))>0; }
        return (bool)$v;
    }
    private static function quote($value) {
        $quote=strpos($value,"'")!==false && strpos($value,'"')===false?'"':"'"; $out=$quote;
        foreach (Compat::points($value) as $cp) {
            $c=Compat::chr($cp);
            if ($c==='\\' || $c===$quote) { $out.='\\'.$c; }
            elseif ($cp===10) { $out.='\\n'; } elseif ($cp===13) { $out.='\\r'; } elseif ($cp===9) { $out.='\\t'; }
            elseif ($cp!==32 && preg_match('/^[\p{C}\p{Z}]$/u',$c)) { $out.=sprintf($cp<256?'\\x%02x':($cp<65536?'\\u%04x':'\\U%08x'),$cp); }
            else { $out.=$c; }
        }
        return $out.$quote;
    }
    private static function repr($v) {
        if ($v===null) { return 'None'; } if (is_string($v)) { return self::quote($v); }
        if (is_array($v)) { return '['.implode(', ',array_map(array(self::class,'repr'),$v)).']'; }
        if (is_object($v)) { $out=array(); foreach ($v as $k=>$value) { $out[]=self::quote((string)$k).': '.self::repr($value); } return '{'.implode(', ',$out).'}'; }
        return Templating::text($v);
    }
    private static function text($v) { return is_array($v)||is_object($v)?self::repr($v):Templating::text($v); }
    private static function cut($s,$n) { preg_match('/\A.{0,'.$n.'}/us',$s,$m); return $m[0]??''; }
    /** Python's permissive ASCII base64 decoder size semantics, without allocating decoded files. */
    public static function decodedSize($s) {
        if (preg_match('/[^\x00-\x7F]/',$s)) { return null; }
        $s=preg_replace('/[^A-Za-z0-9+\/=]/','',$s); $n=0;
        if (strpos($s,'=')===false) { return strlen($s)%4===0?intdiv(strlen($s),4)*3:null; }
        foreach (preg_split('/(=+)/',$s,-1,PREG_SPLIT_DELIM_CAPTURE|PREG_SPLIT_NO_EMPTY) as $part) {
            if ($part[0]!=='=') { $n+=strlen($part); continue; }
            if ($n%4===3) { return intdiv($n,4)*3+2; }
            if ($n%4===2 && strlen($part)>=2) { return intdiv($n,4)*3+1; }
        }
        return $n%4===0?intdiv($n,4)*3:null;
    }
    public static function clean($items) {
        $out=array(); $total=0;
        foreach ($items??array() as $item) {
            if (!is_object($item)) { continue; }
            $name=self::cut(Compat::strip(self::text(self::truth($item->name??null)?$item->name:'')),255);
            $content=self::truth($item->content_base64??null)?$item->content_base64:($item->content??null);
            if ($name==='' || !self::truth($content)) { continue; }
            if (is_string($content) && substr($content,0,5)==='data:' && strpos(self::cut($content,80),',')!==false) { $content=explode(',',$content,2)[1]; }
            $content=Compat::strip(self::text($content)); $size=self::decodedSize($content);
            if ($size===null || $size>4*1024*1024 || $total+$size>8*1024*1024) { continue; }
            if (count($out)>=10) { break; }
            $type=self::truth($item->content_type??null)?$item->content_type:'application/octet-stream';
            $out[]=array('name'=>$name,'content'=>$content,'content_type'=>self::cut(self::text($type),120),'size'=>$size); $total+=$size;
        }
        return $out;
    }
    public static function dump(array $items) {
        if (!$items) { return null; } $rows=array();
        foreach ($items as $item) { $parts=array(); foreach ($item as $k=>$v) { $parts[]=json_encode((string)$k,JSON_UNESCAPED_SLASHES|JSON_THROW_ON_ERROR).': '.json_encode($v,JSON_UNESCAPED_SLASHES|JSON_PRESERVE_ZERO_FRACTION|JSON_THROW_ON_ERROR); } $rows[]='{'.implode(', ',$parts).'}'; }
        return substr('['.implode(', ',$rows).']',0,12000000);
    }
    public static function summary($raw) {
        if (is_string($raw)) { try { $raw=json_decode($raw,false,512,JSON_THROW_ON_ERROR); } catch (\Throwable $e) { return array(); } }
        if (!is_array($raw)) { return array(); } $out=array();
        foreach ($raw as $a) { if (!is_object($a)) { continue; } $r=array('name'=>$a->name??null,'content_type'=>$a->content_type??null,'size'=>$a->size??null); if (self::truth($a->url??null) && !self::truth($a->content??null)) { $r['url']=$a->url; } $out[]=$r; }
        return $out;
    }
}
