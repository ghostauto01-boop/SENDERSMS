<?php
namespace SenderSuite;

/** Contact data operations; sending, suppression changes and destructive deletion are separate workflows. */
final class Contacts {
    private $db;
    private $records;
    private const TEXT = array('first_name','last_name','business_name','phone_number','email','city','state','country','website','industry','source','lead_status','notes','custom_fields');
    private const FLAGS = array('has_consented','is_opted_out','is_email_opted_out');

    public function __construct(Database $db, callable $clock = null) { $this->db = $db; $this->records = new Records($db, $clock); }
    private function table($name) { return Schema::identifier($this->db->schema()->table($name)); }

    public static function tagsInput($value) {
        if ($value === null) { return null; }
        if (is_string($value)) { $value = explode(',', $value); }
        if (!is_array($value)) { throw new ValidationFailure('Value error, tags must be an array of tag names or a comma-separated string','tags'); }
        $out = array(); $seen = array();
        foreach ($value as $tag) {
            if (!is_string($tag)) { throw new ValidationFailure('Value error, each tag must be a string','tags'); }
            $tag = Compat::strip($tag);
            if ($tag === '') { continue; }
            if (Compat::length($tag) > 100) { throw new ValidationFailure('Value error, tag names must be 100 characters or fewer','tags'); }
            $key = Compat::lower($tag);
            if (!isset($seen[$key])) { $out[] = $tag; $seen[$key] = true; }
        }
        return $out;
    }

    public static function input($body, $update = false) {
        if (!is_object($body)) { throw new ValidationFailure('Input should be a valid dictionary or object to extract fields from'); }
        $data = $update ? array() : array_merge(array_fill_keys(self::TEXT, null), array('lead_status'=>'new','tags'=>null));
        $text = array_merge(self::TEXT, $update ? array('email_status') : array());
        foreach ($text as $name) {
            if (!property_exists($body, $name)) { continue; }
            $v = $body->$name;
            if ($v !== null && !is_string($v)) { throw new ValidationFailure('Input should be a valid string', $name); }
            if (!$update && $name === 'lead_status' && $v === null) { throw new ValidationFailure('Input should be a valid string', $name); }
            $cap = $name === 'phone_number' ? 20 : ($name === 'country' ? 100 : null);
            if ($cap !== null && $v !== null && Compat::length($v) > $cap) { throw new ValidationFailure('String should have at most '.$cap.' characters', $name); }
            if ($name === 'email') {
                if ($v !== null && $v !== '' && Compat::email($v) === null) { throw new ValidationFailure('Value error, Enter one valid email address', $name); }
                $v = $v ? Compat::email($v) : null;
            }
            $data[$name] = $v;
        }
        if (!$update && !array_key_exists('lead_status', $data)) { $data['lead_status'] = 'new'; }
        if ($update) {
            foreach (self::FLAGS as $name) {
                if (!property_exists($body, $name)) { continue; }
                $v = $body->$name;
                if ($v !== null && !is_bool($v)) {
                    if (in_array($v, array(0,1,0.0,1.0), true)) { $v = (bool)$v; }
                    elseif (is_string($v) && in_array(strtolower($v), array('0','1','false','true','f','t','off','on','no','yes','n','y'), true)) { $v = in_array(strtolower($v), array('1','true','t','on','yes','y'), true); }
                    else { throw new ValidationFailure('Input should be a valid boolean, unable to interpret input', $name); }
                }
                $data[$name] = $v;
            }
            $operation = property_exists($body, 'tag_operation') ? $body->tag_operation : 'replace';
            if (!in_array($operation, array('replace','add','remove'), true)) { throw new ValidationFailure("Input should be 'replace', 'add' or 'remove'", 'tag_operation'); }
            if (property_exists($body, 'tag_operation')) { $data['tag_operation'] = $operation; }
        }
        if (property_exists($body, 'tags')) { $data['tags'] = self::tagsInput($body->tags); }
        return $data;
    }

    public function find($id) {
        $row = $this->db->one('contacts', array('id' => $id));
        if ($row === null) { throw new AuthFailure(404, 'Contact not found'); }
        return $row;
    }

    private function checkIdentity($phone, $email, $exclude = null, $fields = null) {
        if (!$phone && !$email) { throw new AuthFailure(400, 'A contact needs a phone number or an email address'); }
        foreach (array('phone_number' => $phone, 'email' => $email) as $field => $value) {
            if ($value === null || ($fields !== null && !array_key_exists($field, $fields))) { continue; }
            $column = $field === 'email' ? 'LOWER(email)' : 'phone_number';
            $args = array($value);
            $sql = 'SELECT id FROM '.$this->table('contacts').' WHERE '.$column.'=%s';
            if ($exclude !== null) { $sql .= ' AND id<>%d'; $args[] = $exclude; }
            if ($this->db->rows($sql.' LIMIT 1', $args)) {
                $msg = $exclude === null ? 'Contact with this '.($field === 'email' ? 'email' : 'phone number').' already exists' : 'Another contact already has this '.($field === 'email' ? 'email' : 'phone number');
                throw new AuthFailure(409, $msg);
            }
        }
    }

    public function create($body) {
        $data = self::input($body);
        return $this->db->transaction(function () use ($data) {
            $tags = $data['tags'] ?? array(); unset($data['tags']);
            $raw = $data['phone_number'] ?? null;
            $phone = Phone::clean($raw);
            if ($raw !== null && $raw !== '' && !$phone) { throw new AuthFailure(422, 'Invalid Nigerian phone number'); }
            $email = $data['email'] ?? null;
            $this->checkIdentity($phone, $email);
            $data['phone_number'] = $phone; $data['email'] = $email; $data['email_lower'] = $email;
            $data['email_source'] = $email ? 'manual' : null;
            $country = Compat::strip($data['country'] ?? ''); $data['country'] = $country !== '' ? $country : null;
            $row = $this->records->create('contacts', $data);
            if ($tags) { $this->setTags($row['id'], $tags, 'replace'); }
            return $this->out($row);
        });
    }

    public function update($id, $body) {
        $data = self::input($body, true);
        return $this->db->transaction(function () use ($id, $data) {
            $row = $this->find($id);
            $supplied = array_key_exists('tags', $data); $tags = $data['tags'] ?? array(); $op = $data['tag_operation'] ?? 'replace';
            unset($data['tags'], $data['tag_operation']);
            if (array_key_exists('phone_number', $data)) {
                $raw = $data['phone_number']; $data['phone_number'] = $raw === null || $raw === '' ? null : Phone::clean($raw);
                if ($raw !== null && $raw !== '' && !$data['phone_number']) { throw new AuthFailure(422, 'Invalid Nigerian phone number'); }
            }
            if (array_key_exists('email', $data)) {
                $data['email_lower'] = $data['email'];
                if ($data['email'] && $data['email'] !== Compat::lower($row['email'] ?? '')) {
                    $data['email_source'] = 'manual'; $data['email_verified'] = false; $data['email_verified_at'] = null; $data['email_verdict'] = null; $data['email_confidence'] = null;
                }
            }
            $next = array_merge($row, $data);
            $this->checkIdentity($next['phone_number'], $next['email'], $id, $data);
            $data['updated_at'] = $this->records->now();
            $this->db->update('contacts', $data, array('id' => $id));
            if ($supplied) { $this->setTags($id, $tags, $op); }
            return $this->out($this->find($id));
        });
    }

    public function setTags($id, array $names, $operation) {
        $links = $this->db->rows('SELECT ct.id,t.name FROM '.$this->table('contact_tags').' ct JOIN '.$this->table('tags').' t ON t.id=ct.tag_id WHERE ct.contact_id=%d', array($id));
        $requested = array(); foreach ($names as $name) { $requested[Compat::lower($name)] = $name; }
        $current = array();
        foreach ($links as $link) {
            $key = Compat::lower($link['name']); $current[$key] = true;
            if ($operation === 'replace' || ($operation === 'remove' && isset($requested[$key]))) { $this->db->delete('contact_tags', array('id' => (int)$link['id'])); }
        }
        foreach ($requested as $key => $name) {
            if ($operation === 'remove' || ($operation === 'add' && isset($current[$key]))) { continue; }
            $found = $this->db->rows('SELECT id FROM '.$this->table('tags').' WHERE LOWER(name)=%s LIMIT 1', array($key));
            $tagId = $found ? (int)$found[0]['id'] : $this->records->create('tags', array('name' => $name))['id'];
            $this->records->create('contact_tags', array('contact_id' => $id, 'tag_id' => $tagId));
        }
    }

    public function tagMap(array $ids) {
        if (!$ids) { return array(); }
        $rows = $this->db->rows('SELECT ct.contact_id,t.name FROM '.$this->table('contact_tags').' ct JOIN '.$this->table('tags').' t ON t.id=ct.tag_id WHERE ct.contact_id IN ('.implode(',', array_fill(0, count($ids), '%d')).') ORDER BY t.name ASC', $ids);
        $out = array(); foreach ($rows as $row) { $out[(int)$row['contact_id']][] = $row['name']; } return $out;
    }

    public function out(array $row, array $tags = null) {
        if ($tags === null) { $tags = $this->tagMap(array($row['id']))[$row['id']] ?? array(); }
        foreach (array('email_fail_count','email_last_error','email_lower','last_email_reply_at') as $internal) { unset($row[$internal]); }
        foreach (array('has_consented','is_opted_out','is_undeliverable','is_email_opted_out','is_email_undeliverable','email_verified') as $key) { $row[$key] = (bool)($row[$key] ?? false); }
        foreach (array('delivery_fail_count','emails_sent','emails_received','messages_sent','messages_received') as $key) { $row[$key] = (int)($row[$key] ?? 0); }
        foreach (array('lead_status'=>'new','consent_status'=>'unknown','email_status'=>'active') as $key=>$default) { $row[$key] = $row[$key] ?? $default; }
        foreach ($this->db->schema()->tables()['contacts']['columns'] as $key => $spec) { if ($spec['type'] === 'DateTime' && array_key_exists($key, $row)) { $row[$key] = Records::iso($row[$key]); } }
        $row['tags'] = $tags; return $row;
    }

    /** Shared query builder: all values bound and all SQL identifiers allowlisted. */
    public function filters(array $filters, array &$args) {
        $where = array('1=1');
        if (isset($filters['search']) && $filters['search'] !== '') {
            $term = $filters['search']; $parts = array();
            foreach (array('first_name','last_name','business_name','phone_number','email','city') as $field) { $parts[] = 'LOWER(c.'.Schema::identifier($field).') LIKE LOWER(%s)'; $args[] = '%'.$term.'%'; }
            $digits = preg_replace('/[^\p{Nd}]/u', '', $term);
            if ($digits !== '') {
                $variants = array($digits);
                if (strpos($digits, '0') === 0 && Compat::length($digits) > 1) { $national = substr($digits, 1); $variants[] = $national; $variants[] = '234'.$national; }
                elseif (strpos($digits, '234') === 0 && Compat::length($digits) > 3) { $national = substr($digits, 3); $variants[] = $national; $variants[] = '0'.$national; }
                else { $variants[] = '234'.$digits; $variants[] = '0'.$digits; }
                foreach (array_unique($variants) as $v) { $parts[] = 'c.phone_number LIKE %s'; $args[] = '%'.$v.'%'; }
            }
            $where[] = '('.implode(' OR ', $parts).')';
        }
        if (isset($filters['lead_status']) && $filters['lead_status'] !== '') { $where[] = 'c.lead_status=%s'; $args[] = $filters['lead_status']; }
        if (isset($filters['tag']) && $filters['tag'] !== '') { $where[] = 'EXISTS (SELECT 1 FROM '.$this->table('contact_tags').' ct JOIN '.$this->table('tags').' t ON t.id=ct.tag_id WHERE ct.contact_id=c.id AND t.name=%s)'; $args[] = $filters['tag']; }
        $undeliverable = $filters['undeliverable'] ?? null;
        if (in_array($undeliverable, array('1','true','yes'), true)) { $where[] = 'c.is_undeliverable=1'; }
        elseif (in_array($undeliverable, array('0','false','no'), true)) { $where[] = 'COALESCE(c.is_undeliverable,0)=0'; }
        $channel = Compat::lower(Compat::strip($filters['channel'] ?? ''));
        if ($channel === 'sms') { $where[] = "c.phone_number IS NOT NULL AND c.phone_number<>''"; }
        elseif ($channel === 'email') { $where[] = "c.email IS NOT NULL AND c.email<>''"; }
        $state = Compat::lower($filters['email_state'] ?? ''); $has = "c.email IS NOT NULL AND c.email<>''";
        $states = array('emailable'=>$has.' AND COALESCE(c.is_email_opted_out,0)=0 AND COALESCE(c.is_email_undeliverable,0)=0', 'has_email'=>$has.' AND COALESCE(c.is_email_opted_out,0)=0 AND COALESCE(c.is_email_undeliverable,0)=0', 'no_email'=>"(c.email IS NULL OR c.email='')", 'unsubscribed'=>'c.is_email_opted_out=1', 'bounced'=>'c.is_email_undeliverable=1', 'verified'=>$has.' AND c.email_verified=1', 'unverified'=>$has.' AND COALESCE(c.email_verified,0)=0', 'inferred'=>$has." AND c.email_source='inferred'");
        if (isset($states[$state])) { $where[] = $states[$state]; }
        foreach (array('list_id'=>false,'exclude_list_id'=>true) as $key=>$exclude) {
            if (isset($filters[$key])) { $where[] = ($exclude ? 'NOT ' : '').'EXISTS (SELECT 1 FROM '.$this->table('contact_list_members').' lm WHERE lm.contact_id=c.id AND lm.list_id=%d)'; $args[] = $filters[$key]; }
        }
        return implode(' AND ', $where);
    }

    public function listing(array $filters, $page = 1, $perPage = 25, $stableTies = true) {
        $args = array(); $where = $this->filters($filters, $args); $from = ' FROM '.$this->table('contacts').' c WHERE '.$where;
        $total = (int)$this->db->rows('SELECT COUNT(*) AS n'.$from, $args)[0]['n'];
        $sort = $filters['sort_by'] ?? 'created_at';
        if (!array_key_exists($sort, $this->db->schema()->tables()['contacts']['columns'])) { $sort = 'created_at'; }
        $dir = ($filters['sort_dir'] ?? '') === 'asc' ? 'ASC' : 'DESC';
        $rows = $this->db->rows('SELECT c.*'.$from.' ORDER BY c.'.Schema::identifier($sort).' '.$dir.($stableTies?',c.id '.$dir:'').' LIMIT %d OFFSET %d', array_merge($args, array($perPage, ($page-1)*$perPage)));
        $map = $this->tagMap(array_map('intval', array_column($rows, 'id'))); $items = array();
        foreach ($rows as $row) { $row = $this->db->decode('contacts', $row); $items[] = $this->out($row, $map[$row['id']] ?? array()); }
        return array('total'=>$total,'items'=>$items,'page'=>$page,'per_page'=>$perPage,'next_page'=>$page*$perPage<$total?$page+1:null);
    }

    public function tags() {
        $rows = $this->db->rows('SELECT t.name,COUNT(ct.id) AS n FROM '.$this->table('tags').' t LEFT JOIN '.$this->table('contact_tags').' ct ON ct.tag_id=t.id GROUP BY t.id,t.name ORDER BY t.name ASC');
        return array('items'=>array_map(function($r){return array('name'=>$r['name'],'count'=>(int)$r['n']);},$rows));
    }
}
