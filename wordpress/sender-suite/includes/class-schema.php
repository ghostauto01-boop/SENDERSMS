<?php
namespace SenderSuite;

/** Source-derived schema plan. SQL dialect alternatives are explicit, not parity certification. */
final class Schema {
    private $prefix;private $tables;
    public function __construct($prefix,$tables=null) { self::identifier($prefix.'probe');$this->prefix=$prefix;$this->tables=$tables===null?json_decode(file_get_contents(dirname(__DIR__).'/schema/models.json'),true,512,JSON_THROW_ON_ERROR):$tables; }
    public static function identifier($name) { if (!is_string($name) || !preg_match('/^[A-Za-z_][A-Za-z0-9_]*$/D',$name) || strlen($name)>64) { throw new \InvalidArgumentException('Invalid database identifier.'); }return '`'.$name.'`'; }
    public function tables() { return $this->tables; }
    public function table($name) { if (!isset($this->tables[$name])) { throw new \InvalidArgumentException('Unknown application table.'); }$name=$this->prefix.'sender_suite_'.$name;self::identifier($name);return $name; }
    public function column($table,$column) { $this->table($table);if (!isset($this->tables[$table]['columns'][$column])) { throw new \InvalidArgumentException('Unknown application column.'); }return $this->tables[$table]['columns'][$column]; }
    public function sqlType(array $column) {
        switch ($column['type']) { case 'Integer':return 'INT';case 'Boolean':return 'TINYINT(1)';case 'Float':return 'DOUBLE';case 'Date':return 'DATE';case 'DateTime':return 'DATETIME(6)';case 'JSON':return 'JSON';case 'Text':return 'LONGTEXT';case 'String':if (!$column['length'] || $column['length']>16383) { throw new \RuntimeException('Unsupported VARCHAR size.'); }return 'VARCHAR('.(int)$column['length'].')'; }throw new \RuntimeException('Unsupported SQL type.');
    }
    public function plan($table) {
        $spec=$this->tables[$table]??null;if ($spec===null) { throw new \InvalidArgumentException('Unknown application table.'); }$parts=array();$primary=array();$alternatives=array();$indexNames=array();
        foreach ($spec['columns'] as $name=>$column) { $parts[]=self::identifier($name).' '.$this->sqlType($column).($column['nullable']?' NULL':' NOT NULL').($column['auto_increment']?' AUTO_INCREMENT':'');if ($column['primary']) { $primary[]=self::identifier($name); } }
        if ($primary) { $parts[]='PRIMARY KEY ('.implode(',',$primary).')'; }
        foreach ($spec['indexes'] as $index) {
            $label='ss_i_'.substr(hash('sha256',$this->table($table).':'.$index['name']),0,24);if (isset($indexNames[$label])) { throw new \RuntimeException('Duplicate index name.'); }$indexNames[$label]=true;$columns=array();$bytes=0;$long=false;
            foreach ($index['columns'] as $name) { $c=$this->column($table,$name);$bytes+=($c['type']==='String'?4*$c['length']:8);if (in_array($c['type'],array('Text','JSON'),true)) { $long=true; } }
            if ($index['unique'] && ($long || $bytes>3072)) {
                $digest='__ss_'.substr(hash('sha256',$index['name']),0,16);$nulls=array();$encoded=array();foreach ($index['columns'] as $name) { $q=self::identifier($name);$nulls[]=$q.' IS NULL';$encoded[]="CONCAT(OCTET_LENGTH(".$q."),':',".$q.")"; }
                $parts[]=self::identifier($digest).' BINARY(32) GENERATED ALWAYS AS (CASE WHEN '.implode(' OR ',$nulls).' THEN NULL ELSE UNHEX(SHA2(CONCAT('.implode(',',$encoded).'),256)) END) STORED';$columns[]=self::identifier($digest);$alternatives[]=array('index'=>$index['name'],'kind'=>'full-value SHA-256 unique digest; theoretical collision difference','column'=>$digest);
            } else { foreach ($index['columns'] as $name) { $c=$this->column($table,$name);$prefix=in_array($c['type'],array('Text','JSON'),true) || ($c['type']==='String' && $bytes>3072);$columns[]=self::identifier($name).($prefix?'(191)':'');if ($prefix) { $alternatives[]=array('index'=>$index['name'],'kind'=>'nonunique prefix index; full column predicates retained'); } } }
            $parts[]=($index['unique']?'UNIQUE KEY ':'KEY ').self::identifier($label).' ('.implode(',',$columns).')';
        }
        return array('sql'=>'CREATE TABLE IF NOT EXISTS '.self::identifier($this->table($table))." (\n  ".implode(",\n  ",$parts)."\n) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin",'alternatives'=>$alternatives);
    }
    public function foreignKeys($table) {
        $out=array();foreach ($this->tables[$table]['foreign_keys'] as $fk) { $label='ss_f_'.substr(hash('sha256',$this->table($table).':'.$fk['column']),0,24);$delete=strtoupper($fk['on_delete']??'RESTRICT');$update=strtoupper($fk['on_update']??'RESTRICT');foreach (array($delete,$update) as $action) { if (!in_array($action,array('CASCADE','RESTRICT','NO ACTION','SET NULL'),true)) { throw new \RuntimeException('Unsupported foreign key action.'); } }
            $out[$label]='ALTER TABLE '.self::identifier($this->table($table)).' ADD CONSTRAINT '.self::identifier($label).' FOREIGN KEY ('.self::identifier($fk['column']).') REFERENCES '.self::identifier($this->table($fk['target_table'])).' ('.self::identifier($fk['target_column']).') ON DELETE '.$delete.' ON UPDATE '.$update;
        }return $out;
    }
    public function fingerprint() { return hash('sha256',json_encode($this->tables,JSON_THROW_ON_ERROR)); }
}
