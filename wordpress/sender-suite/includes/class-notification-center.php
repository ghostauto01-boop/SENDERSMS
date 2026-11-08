<?php
namespace SenderSuite;

/** In-app notification history/read state only; browser/provider delivery is separate. */
final class NotificationCenter {
    private $db;
    public function __construct(Database $db) { $this->db=$db; }
    private function table() { return Schema::identifier($this->db->schema()->table('notification_events')); }
    private function count($unread=false) { return (int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table().' WHERE provider=%s'.($unread?' AND is_read=0':''),array('browser'))[0]['n']; }
    private static function out(array $r) { $out=array_intersect_key($r,array_flip(array('id','event_type','title','body','status','is_read','url','reference_id','reference_type','created_at'))); $out['created_at']=Records::iso($r['created_at']); return $out; }
    public function listing($page=1,$perPage=20,$unread=false) {
        $rows=$this->db->rows('SELECT * FROM '.$this->table().' WHERE provider=%s'.($unread?' AND is_read=0':'').' ORDER BY id DESC LIMIT %d OFFSET %d',array('browser',$perPage,($page-1)*$perPage)); $items=array(); foreach ($rows as $r) { $items[]=self::out($this->db->decode('notification_events',$r)); }
        return array('total'=>$this->count($unread),'unread'=>$this->count(true),'page'=>$page,'per_page'=>$perPage,'items'=>$items);
    }
    public function unread() { $rows=$this->db->rows('SELECT id FROM '.$this->table().' WHERE provider=%s ORDER BY id DESC LIMIT 1',array('browser')); return array('unread'=>$this->count(true),'latest_id'=>$rows?(int)$rows[0]['id']:null); }
    public function read($id) { $r=$this->db->one('notification_events',array('id'=>$id,'provider'=>'browser')); if (!$r) { throw new AuthFailure(404,'Notification not found'); } $this->db->update('notification_events',array('is_read'=>true),array('id'=>$id,'provider'=>'browser')); return array('success'=>true); }
    public function readAll() { $this->db->execute('UPDATE '.$this->table().' SET is_read=1 WHERE provider=%s AND is_read=0',array('browser')); return array('success'=>true); }
    public static function definitions() { return array('/notifications/?'=>array('GET'),'/notifications/unread-count'=>array('GET'),'/notifications/read-all'=>array('POST'),'/notifications/(?P<event_id>-?[0-9]+)/read'=>array('POST')); }
    public static function handle($request,array $context) {
        $path=trim(substr($request->get_route(),strlen('/sender-suite/v1/')),'/'); $q=$request->get_query_params(); $method=$request->get_method(); $s=new self($context['db']);
        if ($path==='notifications' && $method==='GET') { return $s->listing(DataRoutes::integer($q['page']??1,'page',1),DataRoutes::integer($q['per_page']??20,'per_page',1,100),array_key_exists('unread_only',$q)?DataRoutes::boolean($q['unread_only'],'unread_only'):false); }
        if ($path==='notifications/unread-count' && $method==='GET') { return $s->unread(); }
        if ($path==='notifications/read-all' && $method==='POST') { return $s->readAll(); }
        if ($method==='POST' && preg_match('#^notifications/(-?[0-9]+)/read$#D',$path,$m)) { return $s->read(DataRoutes::integer($m[1],'event_id')); }
        throw new AuthFailure(404,'Not Found');
    }
}
