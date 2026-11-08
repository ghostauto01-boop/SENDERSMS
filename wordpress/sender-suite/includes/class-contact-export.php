<?php
namespace SenderSuite;

/** Read-only, authenticated via DataRoutes. CSV is spreadsheet-safe; JSON preserves raw values. */
final class ContactCsvDownload {
    public $body;public $filename='contacts.csv';
    public function __construct($body){$this->body=$body;}
}
final class ContactExport {
    const COLUMNS=array('first_name','last_name','business_name','phone_number','email','city','state','country','website','industry','source','lead_status','notes');
    const MAX_CUSTOM=1048576;const MAX_READ=8388608;const READ_BATCH=4194304;const MAX_COLUMNS=512;const MAX_CURSOR=32768;const MAX_OUTPUT=20971520;
    private $db;private $contacts;
    public function __construct(Database $db){$this->db=$db;$this->contacts=new Contacts($db);}
    private function table(){return Schema::identifier($this->db->schema()->table('contacts'));}
    public static function definitions(){return array('/contacts/export'=>array('GET'),'/contacts/export/csv'=>array('GET'));}
    public static function filters(array $q){$out=array();foreach(array('search','lead_status','tag','email_state','channel') as $k){$out[$k]=isset($q[$k])?DataRoutes::text($q,$k):null;} $out['list_id']=isset($q['list_id'])?DataRoutes::integer($q['list_id'],'list_id'):null;return $out;}
    private static function fingerprint(array $filters){ksort($filters);$parts=array();foreach($filters as $k=>$v){$parts[]=json_encode($k,JSON_UNESCAPED_SLASHES|JSON_THROW_ON_ERROR).': '.json_encode($v,JSON_UNESCAPED_SLASHES|JSON_THROW_ON_ERROR);}return substr(sha1('{'.implode(', ',$parts).'}'),0,12);}
    private static function custom($raw){if($raw===null||$raw===''){return new \stdClass();}if(strlen($raw)>self::MAX_CUSTOM){throw new AuthFailure(413,'Custom contact data exceeds the 1 MiB export parsing limit.');}try{$v=json_decode($raw,false,512,JSON_THROW_ON_ERROR);return is_object($v)?$v:new \stdClass();}catch(\JsonException $e){if($e->getCode()===JSON_ERROR_DEPTH){throw new AuthFailure(413,'Custom contact data is too deeply nested to export safely.');}return new \stdClass();}}
    private static function cursor($last,array $columns,$fingerprint){$raw=json_encode(array('last_id'=>$last,'columns'=>$columns,'f'=>$fingerprint),JSON_UNESCAPED_SLASHES|JSON_THROW_ON_ERROR);$out=rtrim(strtr(base64_encode($raw),'+/','-_'),'=');if(strlen($out)>self::MAX_CURSOR){throw new AuthFailure(413,'Export cursor is too large. Reduce custom columns or use the CSV download.');}return $out;}
    public static function decodeCursor($token){
        $bad='Invalid export cursor. Use the next_cursor from the previous page, unchanged.';
        if(!is_string($token)||strlen($token)>self::MAX_CURSOR||!preg_match('/^[A-Za-z0-9_-]+={0,2}$/D',$token)){throw new AuthFailure(422,$bad);}
        try{$raw=base64_decode(strtr($token,'-_','+/'),true);if($raw===false){throw new \RuntimeException();}$v=json_decode($raw,false,32,JSON_THROW_ON_ERROR);if(!is_object($v)||!isset($v->last_id)||!is_int($v->last_id)||$v->last_id<0||!isset($v->f)||!is_string($v->f)||!isset($v->columns)||!is_array($v->columns)||count($v->columns)>self::MAX_COLUMNS){throw new \RuntimeException();}
            $seen=array_fill_keys(array_merge(self::COLUMNS,array('tags')),true);foreach($v->columns as $key){if(!is_string($key)||Compat::length($key)>255||isset($seen[$key])||strpos($key,"\0")!==false){throw new \RuntimeException();}$seen[$key]=true;}return $v;
        }catch(\Throwable $e){throw new AuthFailure(422,$bad);}
    }
    /** Python csv.writer-compatible scalar formatting, with deliberate formula neutralization for strings. */
    public static function csvLine(array $values){$cells=array();foreach($values as $v){$isText=is_string($v);$s=$v===null?'':Attachments::pythonText($v);if($isText&&(preg_match('/^[\p{Z}\p{C}]*[=+@-]/uD',$s)||preg_match('/^[\t\r\n]/D',$s))){$s="'".$s;}$cells[]=strpbrk($s,",\"\r\n")!==false?'"'.str_replace('"','""',$s).'"':$s;}return implode(',',$cells)."\r\n";}
    private static function sizeSql(){return implode('+',array_map(function($k){return 'COALESCE(OCTET_LENGTH(c.'.Schema::identifier($k).'),0)';},array_merge(self::COLUMNS,array('custom_fields'))));}
    /** Hydrate only bounded groups; metadata is fetched before potentially large text values. */
    private function hydrate(array $metadata,$customOnly=false){
        $groups=array();$group=array();$used=0;
        foreach($metadata as $m){$bytes=(int)$m['__ss_bytes'];$cap=$customOnly?self::MAX_CUSTOM:self::MAX_READ;if($bytes>$cap){throw new AuthFailure(413,'Contact data exceeds the safe export read limit. Narrow filters or reduce oversized fields.');}if($group&&$used+$bytes+1024>self::READ_BATCH){$groups[]=$group;$group=array();$used=0;}$group[]=(int)$m['id'];$used+=$bytes+1024;}if($group){$groups[]=$group;}
        foreach($groups as $ids){$rows=$this->db->rows('SELECT '.($customOnly?'c.id,c.custom_fields':'c.*').' FROM '.$this->table().' c WHERE c.id IN ('.implode(',',array_fill(0,count($ids),'%d')).')',$ids);$map=array();foreach($rows as $r){$map[(int)$r['id']]=$r;}foreach($ids as $id){if(isset($map[$id])){yield $map[$id];}}}
    }
    private function tags(array $ids){if(!$ids){return array();}$table=Schema::identifier($this->db->schema()->table('contact_tags'));$n=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$table.' WHERE contact_id IN ('.implode(',',array_fill(0,count($ids),'%d')).')',$ids)[0]['n'];if($n>20000){throw new AuthFailure(413,'Too many tags for one export batch. Narrow the filters or reduce the page limit.');}return $this->contacts->tagMap($ids);}
    private function columns($where,array $args,$order='id ASC'){
        $seen=array_fill_keys(array_merge(self::COLUMNS,array('tags')),true);$columns=array();$offset=0;
        do{$rows=$this->db->rows('SELECT c.id,COALESCE(OCTET_LENGTH(c.custom_fields),0) AS __ss_bytes FROM '.$this->table().' c WHERE '.$where.' ORDER BY c.'.$order.' LIMIT 400 OFFSET %d',array_merge($args,array($offset)));foreach($this->hydrate($rows,true) as $r){foreach(self::custom($r['custom_fields']) as $key=>$value){$key=(string)$key;if(isset($seen[$key])){continue;}if(count($columns)>=self::MAX_COLUMNS||Compat::length($key)>255||strpos($key,"\0")!==false){throw new AuthFailure(413,'Too many or oversized custom export columns. Narrow the contact filters.');}$seen[$key]=true;$columns[]=$key;}}$offset+=count($rows);}while(count($rows)===400);return $columns;
    }
    private static function row(array $c,$tags,array $customColumns){$row=array();foreach(self::COLUMNS as $key){$row[]=isset($c[$key])&&$c[$key]!==''?$c[$key]:'';}$row[]=$tags;$custom=self::custom($c['custom_fields']);foreach($customColumns as $key){$row[]=property_exists($custom,$key)?$custom->$key:'';}return $row;}
    public function page(array $filters,$cursor=null,$limit=200,$maxBytes=20000,$format='csv'){
        $filters=self::filters($filters);
        if(!in_array($format,array('csv','json'),true)){throw new ValidationFailure("String should match pattern '^(csv|json)$'",'format');}
        $fingerprint=self::fingerprint($filters);$args=array();$where=$this->contacts->filters($filters,$args);$total=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table().' c WHERE '.$where,$args)[0]['n'];
        $last=0;if($cursor!==null&&$cursor!==''){$state=self::decodeCursor($cursor);if($state->f!==$fingerprint){throw new AuthFailure(422,'This cursor was issued for different filters. Repeat the same filters on every page, or start again without a cursor.');}$last=$state->last_id;$customColumns=$state->columns;}else{$customColumns=$this->columns($where,$args);}
        $columns=array_merge(self::COLUMNS,array('tags'),$customColumns);$rows=$this->db->rows('SELECT c.id,'.self::sizeSql().' AS __ss_bytes FROM '.$this->table().' c WHERE '.$where.' AND c.id>%d ORDER BY c.id ASC LIMIT %d',array_merge($args,array($last,$limit+1)));$more=count($rows)>$limit;$rows=array_slice($rows,0,$limit);$tags=$this->tags(array_map('intval',array_column($rows,'id')));
        $csv=$format==='csv'&&($cursor===null||$cursor==='')?self::csvLine($columns):'';$used=$format==='csv'?strlen($csv):strlen(json_encode($columns,JSON_THROW_ON_ERROR))+1024;$kept=array();$outRows=array();
        foreach($this->hydrate($rows) as $raw){$c=$this->db->decode('contacts',$raw);$row=self::row($c,implode(', ',$tags[$c['id']]??array()),$customColumns);$o=null;if($format==='json'){$o=new \stdClass();foreach($columns as $i=>$key){$o->$key=$row[$i];}}$line=$format==='csv'?self::csvLine($row):json_encode($o,JSON_THROW_ON_ERROR);$cost=strlen($line);if($format==='csv'&&$kept&&$used+$cost>$maxBytes){$more=true;break;}if($used+$cost>self::MAX_OUTPUT){throw new AuthFailure(413,'Export page exceeds the safe response limit. Reduce large contact fields.');}$used+=$cost;$kept[]=$c['id'];if($format==='csv'){$csv.=$line;}else{$outRows[]=$o;}}
        $next=$more&&$kept?self::cursor(end($kept),$customColumns,$fingerprint):null;$result=array('format'=>$format,'columns'=>$columns,'returned'=>count($kept),'total'=>$total,'truncated'=>$next!==null,'next_cursor'=>$next);if($format==='csv'){$result['csv']=$csv;}else{$result['rows']=$outRows;}return $result;
    }
    public function download(array $filters){
        $filters=self::filters($filters);$args=array();$where=$this->contacts->filters($filters,$args);$custom=$this->columns($where,$args,'created_at DESC,c.id DESC');$columns=array_merge(self::COLUMNS,array('tags'),$custom);$csv=self::csvLine($columns);$offset=0;
        do{$rows=$this->db->rows('SELECT c.id,'.self::sizeSql().' AS __ss_bytes FROM '.$this->table().' c WHERE '.$where.' ORDER BY c.created_at DESC,c.id DESC LIMIT 400 OFFSET %d',array_merge($args,array($offset)));$tags=$this->tags(array_map('intval',array_column($rows,'id')));foreach($this->hydrate($rows) as $raw){$c=$this->db->decode('contacts',$raw);$line=self::csvLine(self::row($c,implode(', ',$tags[$c['id']]??array()),$custom));if(strlen($csv)+strlen($line)>self::MAX_OUTPUT){throw new AuthFailure(413,'CSV download exceeds 20 MiB. Use the paginated export or narrower filters.');}$csv.=$line;}$offset+=count($rows);}while(count($rows)===400);return new ContactCsvDownload($csv);
    }
    public static function handle($request,array $c){$q=$request->get_query_params();$service=new self($c['db']);$filters=self::filters($q);if(substr(rtrim($request->get_route(),'/'),-4)==='/csv'){return $service->download($filters);}return $service->page($filters,DataRoutes::text($q,'cursor'),DataRoutes::integer($q['limit']??200,'limit',1,500),DataRoutes::integer($q['max_bytes']??20000,'max_bytes',1000,200000),DataRoutes::text($q,'format','csv'));}
    public static function serve($served,$response,$request,$server=null){if($served||!($response instanceof \WP_REST_Response)||$request->get_method()!=='GET'||trim($request->get_route(),'/')!=='sender-suite/v1/contacts/export/csv'){return $served;}$v=$response->get_data();if(!($v instanceof ContactCsvDownload)||$response->get_status()!==200){return $served;}echo $v->body;return true;}
}
