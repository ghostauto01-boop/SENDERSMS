<?php
namespace SenderSuite;

/** Explicit source-model defaults; application defaults are not SQL server defaults. */
final class Records {
    private $db;
    private $clock;

    public function __construct(Database $db, callable $clock = null) {
        $this->db = $db;
        $this->clock = $clock ?: function () { return (new \DateTimeImmutable('now', new \DateTimeZone('UTC')))->format('Y-m-d H:i:s.u'); };
    }

    public function now() { return ($this->clock)(); }

    public function create($table, array $values) {
        $spec = $this->db->schema()->tables()[$table];
        $data = array();
        $now = $this->now();
        foreach ($spec['columns'] as $name => $column) {
            if ($column['auto_increment']) { continue; }
            if (array_key_exists($name, $values)) { $data[$name] = $values[$name]; continue; }
            $factory = $column['factory'];
            if (isset($factory['clock'])) { $data[$name] = $now; }
            elseif (is_array($factory) && array_key_exists('value', $factory)) { $data[$name] = $factory['value']; }
            elseif ($column['nullable']) { $data[$name] = null; }
            else { throw new \InvalidArgumentException('Required model value missing: '.$name); }
        }
        foreach ($data as $name => &$value) { if ($spec['columns'][$name]['type'] === 'Integer' && is_bool($value)) { $value = (int)$value; } } unset($value);
        $id = $this->db->insert($table, $data);
        if (!$spec['columns']['id']['auto_increment']) { $id = $data['id']; }
        return $this->db->one($table, array('id' => $id));
    }

    public static function iso($value) {
        if ($value === null) { return null; }
        $d = (new \DateTimeImmutable($value, new \DateTimeZone('UTC')))->setTimezone(new \DateTimeZone('UTC'));
        return $d->format($d->format('u') === '000000' ? 'Y-m-d\TH:i:s\Z' : 'Y-m-d\TH:i:s.u\Z');
    }
}
