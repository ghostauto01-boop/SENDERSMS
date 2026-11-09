<?php
/** System health, scrubbed admin error log reads, dbProbe, and read-only setup guide tests. */
define('ARRAY_A','ARRAY_A');
foreach(array('schema','database','vault','compat','config','auth-routes','records','sms-receipts','sms-outbox','system-guide','data-routes') as $n){require __DIR__.'/../sender-suite/includes/class-'.$n.'.php';}
require __DIR__.'/sqlite-auth-db.php';
$count=0;$diff=array();
function sg_check($label,$a,$e){global $count,$diff;if($a===$e){$count++;}elseif(count($diff)<6){$diff[]=array('case'=>$label,'actual'=>$a,'expected'=>$e);}}
function sg_error($label,$fn,$status){try{$fn();sg_check($label,'accepted',$status);}catch(\SenderSuite\ValidationFailure $e){sg_check($label,422,$status);}catch(\SenderSuite\AuthFailure $e){sg_check($label,$e->status,$status);}}
$w=new SQLiteAuthDB();$w->addTables(array('system_error_records','followups','ads_followup_tasks','messages','ads_campaign_jobs','gateway_settings','contacts','contact_lists','notification_providers'));
$db=new \SenderSuite\Database($w,new \SenderSuite\Schema('test_'));
$vault=new \SenderSuite\Vault('test-auth-salt-32-bytes-minimum!!','test-secure-salt-32-bytes-minimum');
$store=array();
$config=new \SenderSuite\Config($vault,function()use(&$store){return $store;},function($v)use(&$store){$store=$v;});
$config->setup('operator','StrongOperatorPass!2026');
$clock=function(){return '2026-01-10 12:00:00.000000';};
$r=new \SenderSuite\Records($db,$clock);
$sg=new \SenderSuite\SystemGuide($db,$config,$clock);

// dbProbe healthy vs database failure.
$probeOk=\SenderSuite\SystemGuide::dbProbe($db);
sg_check('dbProbe 200 ok',array($probeOk['status'],$probeOk['body']['ok'],$probeOk['body']['consecutive_failures']),array(200,true,0));
$probeFail=\SenderSuite\SystemGuide::dbProbe(new \SenderSuite\Database($w,new \SenderSuite\Schema('missing_prefix_')));
sg_check('dbProbe 503 on DB error',array($probeFail['status'],$probeFail['body']['ok'],$probeFail['body']['kind']),array(503,false,'unavailable'));

// /system/health honest non-Redis/non-Celery WordPress reporting + due/overdue calculation.
$r->create('followups',array('contact_id'=>1,'status'=>'pending','scheduled_at'=>'2026-01-10 11:30:00.000000'));
$r->create('ads_campaign_jobs',array('id'=>'ads-job-1','campaign_id'=>1,'status'=>'failed'));
$h=$sg->health();
sg_check('health honest components',array($h['ok'],$h['degraded'],$h['components']['database']['ok'],$h['components']['redis']['ok'],$h['components']['celery_workers']['ok'],$h['due_followups'],$h['overdue_followups'],$h['components']['campaign_jobs']['failed']),array(false,true,true,false,false,1,1,1));

// /system/errors admin-only + secret scrubbing + request_id lookup.
$r->create('system_error_records',array('request_id'=>'req-123','code'=>'UPSTREAM_ERR','exception_type'=>'RuntimeError','message'=>'Failed with Bearer sk-live-secret999 and password=topsecret','traceback'=>'postgres://admin:pw123@db.internal:5432/app token:abc123','method'=>'POST','path'=>'/send/'));
sg_error('viewer denied system errors',function()use($sg){$sg->errors(array(),array('id'=>2,'role'=>'viewer'));},403);
$list=$sg->errors(array(),array('id'=>1,'role'=>'admin'));
sg_check('error list scrubs secrets and omits traceback',array(count($list['items']),isset($list['items'][0]['traceback']),strpos($list['items'][0]['message'],'sk-live-secret999'),strpos($list['items'][0]['message'],'topsecret')),array(1,false,false,false));
$one=$sg->errors(array('request_id'=>'req-123'),array('id'=>1,'role'=>'admin'));
sg_check('single error scrubs dsn and token in traceback',array(isset($one['traceback']),strpos($one['traceback'],'pw123'),strpos($one['traceback'],'abc123')),array(true,false,false));
sg_error('unknown request_id 404',function()use($sg){$sg->errors(array('request_id'=>'missing-id'),array('id'=>1,'role'=>'admin'));},404);

// /guide and /guide/summary: read-only, docs empty (no broken root /DEPLOY.md links), ready_to_send false while outbox disabled.
$encPw=$config->sealRuntimeSecret($db->schema()->table('gateway_settings').'/1/password','gw-pass');
$r->create('gateway_settings',array('name'=>'Phone 1','base_url'=>'https://api.sms-gate.app','username'=>'user1','password_encrypted'=>$encPw,'is_enabled'=>true));
$snap=hash('sha256',json_encode(array($db->select('system_settings'),$db->select('gateway_settings'))));
$g=$sg->guide();
sg_check('guide structure and ready_to_send conservative gate',array(count($g['groups']),$g['progress']['ready_to_send'],$g['steps'][0]['docs']),array(6,false,array()));
$sum=$sg->summary();
sg_check('guide summary includes next_step',is_string($sum['next_step'])&&$sum['next_step']!=='',true);
sg_check('guide never mutates DB',hash('sha256',json_encode(array($db->select('system_settings'),$db->select('gateway_settings')))),$snap);

echo json_encode(array('ok'=>!$diff,'assertions'=>$count,'diffs'=>$diff,'php'=>PHP_VERSION,'native_wordpress_verified'=>false,'native_mysql_verified'=>false,'scope'=>'Honest WordPress system health, dbProbe, scrubbed error log reads, and read-only setup guide.'),JSON_THROW_ON_ERROR);
