<?php
/** Synthetic browser-fixture documents rendered by actual shell code, not a WordPress server. */
require __DIR__.'/../sender-suite/includes/class-app-shell.php';
$out=array();foreach(array('pretty'=>'http://127.0.0.1:4137/sub/wp-json/sender-suite/v1/','plain'=>'http://127.0.0.1:4137/sub/index.php?rest_route=/sender-suite/v1/') as $mode=>$api){$out[$mode]=\SenderSuite\AppShell::document('http://127.0.0.1:4137/sub/wp-content/plugins/sender-suite/app/',$api,true);}
$out['disabled']=\SenderSuite\AppShell::document('http://127.0.0.1:4137/sub/wp-content/plugins/sender-suite/app/','http://127.0.0.1:4137/sub/wp-json/sender-suite/v1/',false);
echo json_encode($out,JSON_THROW_ON_ERROR);
