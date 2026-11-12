<?php
namespace SenderSuite;

/** Site/field-bound authenticated encryption, deliberately not source Fernet ciphertext. */
final class Vault {
    private $key;
    public function __construct($material) { if (!is_string($material) || strlen($material)<32) { throw new \InvalidArgumentException('Insufficient site key material.'); }if (!function_exists('openssl_encrypt') || !in_array('aes-256-gcm',openssl_get_cipher_methods(),true)) { throw new \RuntimeException('AES-256-GCM is required.'); }$this->key=hash_hkdf('sha256',$material,32,'sender-suite/v1/config'); }
    public function seal($field,$plaintext) { if (!is_string($field) || $field==='' || !is_string($plaintext)) { throw new \InvalidArgumentException('String field and value required.'); }$iv=random_bytes(12);$tag='';$cipher=openssl_encrypt($plaintext,'aes-256-gcm',$this->key,OPENSSL_RAW_DATA,$iv,$tag,'sender-suite/v1/'.$field,16);if ($cipher===false) { throw new \RuntimeException('Unable to protect configuration.'); }return 'v1:'.base64_encode($iv.$tag.$cipher); }
    public function open($field,$envelope) { if (!is_string($field) || $field==='' || !is_string($envelope) || substr($envelope,0,3)!=='v1:') { throw new \RuntimeException('Invalid protected configuration.'); }$raw=base64_decode(substr($envelope,3),true);if ($raw===false || strlen($raw)<28) { throw new \RuntimeException('Invalid protected configuration.'); }$out=openssl_decrypt(substr($raw,28),'aes-256-gcm',$this->key,OPENSSL_RAW_DATA,substr($raw,0,12),substr($raw,12,16),'sender-suite/v1/'.$field);if ($out===false) { throw new \RuntimeException('Protected configuration could not be authenticated.'); }return $out; }
    public function __debugInfo() { return array('key'=>'[redacted]'); }
    public function __serialize() { throw new \RuntimeException('Vault serialization is forbidden.'); }
    public function __sleep() { throw new \RuntimeException('Vault serialization is forbidden.'); }
}
