<?php
namespace SenderSuite;

/** Marker handled only by the private REST CSV serializer, never a filesystem path. */
final class ImportErrorReport {
    public $body;
    public $filename;
    public function __construct($body,$id) { $this->body=$body; $this->filename='import-errors-'.preg_replace('/[^A-Za-z0-9_-]/','',$id).'.csv'; }
}

/** Durable, non-sending WordPress queue. One advisory lock serializes its workers. */
final class ImportJobs {
    const HOOK='sender_suite_import_queue';
    private $db;
    private $records;
    private $clock;
    public function __construct(Database $db,callable $clock=null) { $this->db=$db; $this->clock=$clock; $this->records=new Records($db,$clock); }
    public static function schedules($schedules) { $schedules['sender_suite_five_minutes']=array('interval'=>300,'display'=>'Sender Suite contact import queue'); return $schedules; }
    public static function ensureSchedule() {
        if (wp_next_scheduled(self::HOOK)!==false) { return true; }
        return wp_schedule_event(time()+1,'sender_suite_five_minutes',self::HOOK,array(),true)===true;
    }
    public static function deactivate() { wp_clear_scheduled_hook(self::HOOK); }
    public static function tick() {
        // Cron is an internal worker, not an HTTP or WordPress-admin authentication bypass.
        try { self::process(AuthRoutes::context()['db']); } catch (\Throwable $e) { /* Leave durable queued/running rows for the next tick. Do not log contact data. */ }
    }
    private static function json($value) { return json_encode($value,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_THROW_ON_ERROR); }
    private static function cut($value,$limit) { return implode('',array_map(array(Compat::class,'chr'),array_slice(Compat::points($value),0,$limit))); }
    public static function options(array $form) {
        $out=array();
        foreach (array('column_mapping'=>new \stdClass(),'auto_tag_rules'=>array()) as $key=>$default) {
            $raw=$form[$key]??null;
            try { $out[$key]=$raw===null || $raw===''?$default:json_decode($raw,false,512,JSON_THROW_ON_ERROR); }
            catch (\Throwable $e) { throw new AuthFailure(422,'Invalid JSON in import options'); }
        }
        if (!is_object($out['column_mapping']) || !is_array($out['auto_tag_rules'])) { throw new AuthFailure(422,'column_mapping must be an object and auto_tag_rules an array'); }
        $out['list_id']=$form['list_id']??null;
        $out['new_list_name']=$form['new_list_name']??null;
        $out['skip_duplicates']=$form['skip_duplicates']??true;
        $out['tags']=CsvImport::tags($form['tags']??'');
        return $out;
    }
    /** Caller owns the transaction: a validation error also rolls back a new destination list. */
    public function create($bytes,$filename,array $options,array $user) {
        $reader=CsvReader::records($bytes); $reader->rewind();
        if (!$reader->valid() || !$reader->current()) { throw new AuthFailure(422,'No headers found in CSV'); }
        $mapping=CsvPreview::mapping($reader->current());
        foreach ($options['column_mapping'] as $key=>$value) { $key=Compat::lower(Compat::strip((string)$key)); $mapping->$key=$value; }
        $listId=$options['list_id'];
        if ($listId!==null) { (new ContactLists($this->db,$this->clock))->find($listId); }
        elseif (Compat::strip($options['new_list_name']??'')!=='') { $listId=(new ContactLists($this->db,$this->clock))->importDestination($options['new_list_name'])['id']; }
        $preview=CsvPreview::preview($bytes,0);
        $uuid=random_bytes(16); $uuid[6]=chr((ord($uuid[6])&15)|64); $uuid[8]=chr((ord($uuid[8])&63)|128); $uuid=bin2hex($uuid);
        $id=substr($uuid,0,8).'-'.substr($uuid,8,4).'-'.substr($uuid,12,4).'-'.substr($uuid,16,4).'-'.substr($uuid,20);
        return $this->records->create('contact_import_jobs',array(
            'id'=>$id,'owner_id'=>$user['id'],'file_name'=>self::cut($filename===''?'contacts.csv':$filename,255),
            'content'=>CsvReader::utf8($bytes),'mapping_json'=>self::json($mapping),'tags_json'=>self::json($options['tags']),
            'auto_tag_rules_json'=>self::json($options['auto_tag_rules']),'list_id'=>$listId,
            'skip_duplicates'=>$options['skip_duplicates']?1:0,'total_rows'=>$preview['total_rows']
        ));
    }
    private static function iso($value) { return $value===null?null:str_replace('Z','+00:00',Records::iso($value)); }
    public static function out(array $job) {
        $out=array_intersect_key($job,array_flip(array('id','status','file_name','total_rows','processed_rows','imported','merged','duplicates','invalid','error','list_id')));
        $out['progress_percent']=$job['total_rows']?round($job['processed_rows']*100/$job['total_rows'],1,PHP_ROUND_HALF_EVEN):0;
        foreach (array('created_at','started_at','completed_at') as $key) { $out[$key]=self::iso($job[$key]); }
        return $out;
    }
    private function owned($id,array $user) {
        $job=$this->db->one('contact_import_jobs',array('id'=>$id));
        if (!$job || ($job['owner_id']!==$user['id'] && ($user['role']??null)!=='admin')) { throw new AuthFailure(404,'Import job not found'); }
        return $job;
    }
    public function read($id,array $user) {
        $job=$this->owned($id,$user); $out=self::out($job);
        if ($job['result_json']) { $out['result']=json_decode($job['result_json'],false,512,JSON_THROW_ON_ERROR); }
        if ($job['list_id']!==null) {
            $list=$this->db->one('contact_lists',array('id'=>$job['list_id']));
            if ($list!==null) {
                $n=$this->db->rows('SELECT COUNT(*) AS n FROM '.Schema::identifier($this->db->schema()->table('contact_list_members')).' WHERE list_id=%d',array($list['id']))[0]['n'];
                $out['list']=array('id'=>$list['id'],'name'=>$list['name'],'contact_count'=>(int)$n);
            }
        }
        return $out;
    }
    public function errors($id,array $user) {
        $job=$this->owned($id,$user);
        try { $errors=json_decode($job['errors_json']?:'[]',true,512,JSON_THROW_ON_ERROR); } catch (\Throwable $e) { $errors=array(); }
        if (!is_array($errors)) { $errors=array(); }
        $csv="row,level,error\r\n";
        foreach ($errors as $item) {
            if (!is_array($item)) { continue; }
            $cells=array($item['row']??'',$item['level']??'error',$item['error']??'');
            $cells=array_map(function($cell){$cell=is_scalar($cell)?(string)$cell:'';return strpbrk($cell,",\"\r\n")!==false?'"'.str_replace('"','""',$cell).'"':$cell;},$cells);
            $csv.=implode(',',$cells)."\r\n";
        }
        return new ImportErrorReport($csv,$job['id']);
    }
    private function counters($processed,array $result,$total) {
        return array('processed_rows'=>$processed,'total_rows'=>max($total,$result['total_rows']),
            'imported'=>$result['imported'],'merged'=>$result['merged'],'duplicates'=>$result['duplicates'],
            'invalid'=>$result['invalid'],'errors_json'=>self::json($result['errors']));
    }
    private function fail($id,$message) {
        $this->db->transaction(function()use($id,$message){
            $this->db->update('contact_import_jobs',array('status'=>'failed','error'=>$message,'content'=>null,'completed_at'=>$this->records->now()),array('id'=>$id,'status'=>'running'));
        });
    }
    private function run(array $job) {
        $id=$job['id'];
        $claimed=$this->db->transaction(function()use($job,$id){return $this->db->update('contact_import_jobs',array('status'=>'running','started_at'=>$this->records->now()),array('id'=>$id,'status'=>$job['status']));});
        if ($claimed!==1) { return; }
        if ($job['processed_rows']>0) { $this->fail($id,'A partially committed import cannot be replayed. Re-upload to start a new import.'); return; }
        try {
            $mapping=json_decode($job['mapping_json'],false,512,JSON_THROW_ON_ERROR);
            $tags=json_decode($job['tags_json'],true,512,JSON_THROW_ON_ERROR);
            $rules=json_decode($job['auto_tag_rules_json'],false,512,JSON_THROW_ON_ERROR);
            if (!is_object($mapping) || !is_array($tags) || !is_array($rules)) { throw new \RuntimeException('Invalid saved options.'); }
            $this->db->transaction(function()use($job,$id,$mapping,$tags,$rules){
                $progress=function($n,$r)use($job,$id){
                    $this->db->update('contact_import_jobs',$this->counters($n,$r,$job['total_rows']),array('id'=>$id));
                    $this->db->checkpoint(); // Contact writes and counters become durable together, every 100 rows.
                };
                $result=(new CsvImport($this->db,$this->clock))->run($job['content']??'',$mapping,$job['list_id'],(bool)$job['skip_duplicates'],$tags,$rules,$progress);
                $discovered=0;
                try { $stats=$this->db->transaction(function(){return (new Variables($this->db,$this->clock))->sync();}); $discovered=(int)$stats['discovered']; } catch (\Throwable $e) { /* Convenience discovery must not invalidate committed imports. */ }
                $counters=$this->counters($result['total_rows'],$result,$result['total_rows']);
                $result['errors']=array_slice($result['errors'],0,50); $result['new_variables']=$discovered; unset($result['imported_ids']);
                $this->db->update('contact_import_jobs',array_merge($counters,array('status'=>'completed','result_json'=>self::json($result),'content'=>null,'completed_at'=>$this->records->now())),array('id'=>$id,'status'=>'running'));
            });
        } catch (\Throwable $e) {
            // Do not expose driver messages, credentials, SQL or CSV cells through public errors.
            $this->fail($id,'Import failed; any previously committed progress has been retained.');
        }
    }
    public static function process(Database $db,callable $clock=null) {
        $name='ss_import_'.substr(hash('sha256',$db->schema()->table('contact_import_jobs')),0,54);
        $lock=$db->rows('SELECT GET_LOCK(%s,0) AS acquired',array($name));
        if (!isset($lock[0]['acquired'])) { throw new DatabaseFailure(); }
        if ((int)$lock[0]['acquired']!==1) { return; } // Another live queue worker owns the connection lock.
        try {
            $worker=new self($db,$clock); $table=Schema::identifier($db->schema()->table('contact_import_jobs'));
            $jobs=$db->rows("SELECT * FROM ".$table." WHERE status IN ('queued','retrying','running') ORDER BY created_at,id LIMIT 5");
            $deadline=microtime(true)+40;
            foreach ($jobs as $raw) {
                $job=$db->decode('contact_import_jobs',$raw);
                if ($job['status']==='running') {
                    // Lock acquisition proves no earlier queue worker still holds this queue lock.
                    // Never replay a partially committed file: that could duplicate/incorrectly merge records.
                    $worker->fail($job['id'],'Import worker interrupted; committed progress retained. Re-upload to start a new import.');
                } else { $worker->run($job); }
                if (microtime(true)>=$deadline) { break; }
            }
        } finally { $db->rows('SELECT RELEASE_LOCK(%s) AS released',array($name)); }
    }
    public static function serve($served,$response,$request,$server=null) {
        if ($served || !($response instanceof \WP_REST_Response) || stripos($request->get_route(),'/sender-suite/v1/contacts/import/jobs/')!==0) { return $served; }
        $data=$response->get_data();
        if (!($data instanceof ImportErrorReport) || $response->get_status()!==200) { return $served; }
        echo $data->body; return true;
    }
}
