<?php
namespace SenderSuite;

final class ListNameConflict extends AuthFailure {
    public $existingId;
    public function __construct(array $row) {
        $this->existingId = $row['id'];
        parent::__construct(409, "A list named '".$row['name']."' already exists (id ".$row['id']."). Use that list, or choose a different name.");
    }
}

/** Non-destructive list/membership workflows. Contact deletion is deliberately not substituted. */
final class ContactLists {
    private $db;
    private $records;
    private $contacts;
    public function __construct(Database $db, callable $clock = null) { $this->db=$db; $this->records=new Records($db,$clock); $this->contacts=new Contacts($db,$clock); }
    private function table($name) { return Schema::identifier($this->db->schema()->table($name)); }
    public function find($id) { $r=$this->db->one('contact_lists',array('id'=>$id)); if (!$r) { throw new AuthFailure(404,'List not found'); } return $r; }

    private function freeName($name, $exclude=null) {
        $name=Compat::strip($name);
        if ($name==='') { throw new AuthFailure(422,'A list needs a name'); }
        $args=array(Compat::lower($name)); $sql='SELECT id,name FROM '.$this->table('contact_lists').' WHERE LOWER(TRIM(name))=%s';
        if ($exclude!==null) { $sql.=' AND id<>%d'; $args[]=$exclude; }
        $rows=$this->db->rows($sql.' ORDER BY id ASC LIMIT 1',$args);
        if ($rows) { $row=$rows[0]; $row['id']=(int)$row['id']; throw new ListNameConflict($row); }
        return $name;
    }

    public function create($name,$description=null) {
        return $this->db->transaction(function()use($name,$description){
            $r=$this->records->create('contact_lists',array('name'=>$this->freeName($name),'description'=>$description));
            return array('id'=>$r['id'],'name'=>$r['name'],'description'=>$r['description'],'contact_count'=>$r['contact_count']);
        });
    }
    /** Source queued-import naming: check the full normalized name, then truncate. */
    public function importDestination($name) {
        $name=$this->freeName($name);
        $name=implode('',array_map(array(Compat::class,'chr'),array_slice(Compat::points($name),0,255)));
        return $this->records->create('contact_lists',array('name'=>$name));
    }
    public function update($id,$name=null,$description=null) {
        return $this->db->transaction(function()use($id,$name,$description){
            $r=$this->find($id); $data=array();
            if ($name!==null && $name!=='') { $data['name']=$this->freeName($name,$id); }
            if ($description!==null) { $data['description']=$description; }
            foreach ($data as $k=>$v) { if ($v===$r[$k]) { unset($data[$k]); } }
            if ($data) { $data['updated_at']=$this->records->now(); $this->db->update('contact_lists',$data,array('id'=>$id)); }
            return array('success'=>true);
        });
    }
    private function countMembers(array $list) {
        $n=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('contact_list_members').' WHERE list_id=%d',array($list['id']))[0]['n'];
        if ($n!==$list['contact_count']) { $this->db->update('contact_lists',array('contact_count'=>$n,'updated_at'=>$this->records->now()),array('id'=>$list['id'])); }
        return $n;
    }
    public function listing($page=1,$perPage=25,$search=null) {
        $args=array(); $where='1=1';
        if ($search!==null && $search!=='') { $where='LOWER(l.name) LIKE LOWER(%s)'; $args[]='%'.$search.'%'; }
        $total=(int)$this->db->rows('SELECT COUNT(*) AS n FROM '.$this->table('contact_lists').' l WHERE '.$where,$args)[0]['n'];
        $rows=$this->db->rows('SELECT l.* FROM '.$this->table('contact_lists').' l WHERE '.$where.' ORDER BY l.updated_at DESC LIMIT %d OFFSET %d',array_merge($args,array($perPage,($page-1)*$perPage)));
        $items=array(); foreach ($rows as $r) { $r=$this->db->decode('contact_lists',$r); $r['contact_count']=$this->countMembers($r); $r['created_at']=Records::iso($r['created_at']); $r['updated_at']=Records::iso($r['updated_at']); $items[]=$r; }
        return array('total'=>$total,'items'=>$items);
    }
    public function members($id,$page=1,$perPage=25,$search=null) {
        $this->find($id);
        $out=$this->contacts->listing(array('list_id'=>$id,'search'=>$search),$page,$perPage,false);
        unset($out['next_page']); return $out;
    }
    private function insertMembers($listId,array $ids) {
        $added=0;
        foreach (array_chunk($ids,400) as $chunk) {
            $marks=implode(',',array_fill(0,count($chunk),'%d'));
            $present=$this->db->rows('SELECT contact_id FROM '.$this->table('contact_list_members').' WHERE list_id=%d AND contact_id IN ('.$marks.')',array_merge(array($listId),$chunk));
            $seen=array_fill_keys(array_map('intval',array_column($present,'contact_id')),true);
            foreach ($chunk as $id) { if (isset($seen[$id])) { continue; } $this->records->create('contact_list_members',array('list_id'=>$listId,'contact_id'=>$id)); $seen[$id]=true; $added++; }
        }
        return $added;
    }
    public function add($listId,array $ids) {
        return $this->db->transaction(function()use($listId,$ids){
            $list=$this->find($listId); $ids=array_values(array_unique($ids)); $found=array();
            foreach (array_chunk($ids,400) as $chunk) { $rows=$this->db->rows('SELECT id FROM '.$this->table('contacts').' WHERE id IN ('.implode(',',array_fill(0,count($chunk),'%d')).')',$chunk); foreach ($rows as $r) { $found[(int)$r['id']]=true; } }
            $missing=array_values(array_filter($ids,function($id)use($found){return !isset($found[$id]);})); sort($missing,SORT_NUMERIC);
            if ($missing) { throw new AuthFailure(404,'Contact not found: '.implode(', ',$missing)); }
            $added=$this->insertMembers($listId,$ids);
            return array('success'=>true,'added'=>$added,'contact_count'=>$this->countMembers($list));
        });
    }
    public function addAll($listId,array $filters) {
        return $this->db->transaction(function()use($listId,$filters){
            $list=$this->find($listId); $args=array(); $where=$this->contacts->filters($filters,$args); $cursor=0; $added=0; $matched=0;
            do {
                $rows=$this->db->rows('SELECT c.id FROM '.$this->table('contacts').' c WHERE '.$where.' AND c.id>%d ORDER BY c.id ASC LIMIT 400',array_merge($args,array($cursor)));
                $ids=array_map('intval',array_column($rows,'id')); $matched+=count($ids); $added+=$this->insertMembers($listId,$ids);
                if ($ids) { $cursor=max($ids); }
            } while (count($rows)===400);
            return array('success'=>true,'added'=>$added,'matched'=>$matched,'contact_count'=>$this->countMembers($list));
        });
    }
    public function remove($listId,array $ids,$all=false,$search=null) {
        return $this->db->transaction(function()use($listId,$ids,$all,$search){
            $list=$this->find($listId); $removed=0;
            if ($all) {
                $args=array(); $where=$this->contacts->filters(array('list_id'=>$listId,'search'=>$search),$args);
                $rows=$this->db->rows('SELECT c.id FROM '.$this->table('contacts').' c WHERE '.$where,$args); $ids=array_map('intval',array_column($rows,'id'));
            }
            foreach (array_chunk(array_values(array_unique($ids)),400) as $chunk) {
                $removed+=$this->db->execute('DELETE FROM '.$this->table('contact_list_members').' WHERE list_id=%d AND contact_id IN ('.implode(',',array_fill(0,count($chunk),'%d')).')',array_merge(array($listId),$chunk));
            }
            return array('success'=>true,'removed'=>$removed,'contact_count'=>$this->countMembers($list));
        });
    }
    public function stats($id) {
        $r=$this->find($id); $n=$this->countMembers($r);
        $rows=$this->db->rows('SELECT c.lead_status,COUNT(c.id) AS n FROM '.$this->table('contacts').' c JOIN '.$this->table('contact_list_members').' lm ON lm.contact_id=c.id WHERE lm.list_id=%d GROUP BY c.lead_status',array($id));
        $dist=new \stdClass(); foreach ($rows as $row) { $k=$row['lead_status']; $dist->$k=(int)$row['n']; }
        return array('id'=>$r['id'],'name'=>$r['name'],'contact_count'=>$n,'lead_status_distribution'=>$dist);
    }
}
