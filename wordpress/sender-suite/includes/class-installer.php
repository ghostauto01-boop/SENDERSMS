<?php
namespace SenderSuite;

/** Fail closed on unsupported engines/crypto; never registers callbacks or runs sending jobs. */
final class Installer {
    public static function requirements($wpdb) {
        if ((defined('DB_ENGINE') && DB_ENGINE==='sqlite') || (defined('SQLITE_DB_DROPIN_VERSION')) || !isset($wpdb->dbh) || !($wpdb->dbh instanceof \mysqli)) { throw new \RuntimeException('Sender Suite requires native MySQL/MariaDB; SQLite cannot certify installation.'); }
        if (!defined('PASSWORD_ARGON2ID') || !in_array('argon2id',password_algos(),true)) { throw new \RuntimeException('Sender Suite requires native Argon2id support.'); }
        if (!function_exists('openssl_encrypt') || !in_array('aes-256-gcm',openssl_get_cipher_methods(),true)) { throw new \RuntimeException('Sender Suite requires AES-256-GCM.'); }
    }
    public static function activate($wpdb) {
        self::requirements($wpdb);$schema=new Schema($wpdb->prefix);$db=new Database($wpdb,$schema);$plans=array();foreach ($schema->tables() as $name=>$spec) { $plans[$name]=$schema->plan($name);$schema->foreignKeys($name); }
        $lock='sender-suite-install-'.substr(hash('sha256',$wpdb->prefix),0,32);$rows=$db->rows('SELECT GET_LOCK(%s,10) AS acquired',array($lock));if (!$rows || (int)$rows[0]['acquired']!==1) { throw new \RuntimeException('Another Sender Suite installer is running.'); }
        try {
            $prior=get_option('sender_suite_schema_fingerprint',null);if ($prior!==null && !hash_equals($prior,$schema->fingerprint())) { throw new \RuntimeException('Schema changed; a reviewed versioned migration is required.'); }
            foreach ($plans as $name=>$plan) {
                $db->execute($plan['sql']);$columns=$db->rows('SHOW FULL COLUMNS FROM '.Schema::identifier($schema->table($name)));$actual=array_column($columns,null,'Field');foreach ($schema->tables()[$name]['columns'] as $column=>$spec) { if (!isset($actual[$column])) { throw new \RuntimeException('Installed schema is incomplete; no destructive repair attempted.'); }$a=$actual[$column];$type=strtolower($a['Type']);$expected=strtolower($schema->sqlType($spec));if (preg_replace('/\bint\([0-9]+\)/','int',$type)!==$expected && !($spec['type']==='JSON' && $type==='longtext')) { throw new \RuntimeException('Installed column type differs from the schema plan.'); }if (($a['Null']==='YES')!==$spec['nullable'] || (strpos($a['Extra'],'auto_increment')!==false)!==$spec['auto_increment']) { throw new \RuntimeException('Installed column flags differ from the schema plan.'); } }
                $status=$db->rows('SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s',array($schema->table($name)));if (!$status || strtoupper($status[0]['ENGINE'])!=='INNODB') { throw new \RuntimeException('Application tables require InnoDB.'); }
            }
            foreach ($schema->tables() as $name=>$spec) { foreach ($schema->foreignKeys($name) as $label=>$sql) { $exists=$db->rows('SELECT CONSTRAINT_NAME FROM information_schema.REFERENTIAL_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME=%s AND CONSTRAINT_NAME=%s',array($schema->table($name),$label));if (!$exists) { $db->execute($sql); } } }
            $db->execute('CREATE TABLE IF NOT EXISTS '.Schema::identifier($wpdb->prefix.'sender_suite_config').' (name VARCHAR(150) NOT NULL,payload LONGTEXT NOT NULL,updated_at DATETIME(6) NOT NULL,PRIMARY KEY (name)) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin');
            $db->execute('CREATE TABLE IF NOT EXISTS '.Schema::identifier($wpdb->prefix.'sender_suite_rate_limits').' (name CHAR(64) NOT NULL,hits INT NOT NULL,expires_at DATETIME(6) NOT NULL,PRIMARY KEY (name),KEY expires_at (expires_at)) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin');
            update_option('sender_suite_schema_fingerprint',$schema->fingerprint(),false);
            update_option('sender_suite_build_status','INCOMPLETE development rebuild; not production ready',false);
        }finally { $db->rows('SELECT RELEASE_LOCK(%s)',array($lock)); }
    }
}
