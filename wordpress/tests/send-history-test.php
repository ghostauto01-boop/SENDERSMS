<?php
/** Source history DTO comparison: bounded reads only, no provider or native WP connection. */
define('ARRAY_A','ARRAY_A');
foreach(array('schema','database','compat','phone','number-filter','auth-routes','records','analytics','data-routes','send-history') as $n){require __DIR__.'/../sender-suite/includes/class-'.$n.'.php';}require __DIR__.'/sqlite-auth-db.php';
$count=0;$diff=array();
function sh_norm($v){if(is_object($v)){$v=get_object_vars($v);}if(is_array($v)){ksort($v);foreach($v as &$x){$x=sh_norm($x);}unset($x);}elseif(is_string($v)&&preg_match('/^\d{4}-\d{2}-\d{2}[T ]\d{2}:/',$v)){$v=(new DateTimeImmutable($v,new DateTimeZone('UTC')))->setTimezone(new DateTimeZone('UTC'))->format('Y-m-d H:i:s.u');}return $v;}
function sh_check($name,$actual,$expected){global $count,$diff;if(sh_norm($actual)===sh_norm($expected)){$count++;}elseif(count($diff)<5){$diff[]=array('case'=>$name,'actual'=>$actual,'expected'=>$expected);}}
function sh_error($label,$fn,$status){try{$fn();sh_check($label,'accepted',$status);}catch(\SenderSuite\ValidationFailure $e){sh_check($label,422,$status);}catch(\SenderSuite\AuthFailure $e){sh_check($label,$e->status,$status);}catch(\SenderSuite\DatabaseFailure $e){sh_check($label,503,$status);}}
$f=json_decode(file_get_contents(__DIR__.'/send-history-source-fixtures.json'));$w=new SQLiteAuthDB();$w->addTables(array('contacts','messages','contact_lists','contact_list_members','suppression_entries'));$db=new \SenderSuite\Database($w,new \SenderSuite\Schema('test_'));$r=new \SenderSuite\Records($db,function(){return '2026-01-02 12:00:00.123456';});
foreach($f->seeds as $seed){$values=(array)$seed->values;$nullChannel=false;foreach($values as $key=>&$value){if($value!==null&&$db->schema()->column($seed->table,$key)['type']==='DateTime'){$value=(new DateTimeImmutable($value))->setTimezone(new DateTimeZone('UTC'))->format('Y-m-d H:i:s.u');}if($seed->table==='messages'&&$key==='channel'&&$value===null){$value='sms';$nullChannel=true;}}unset($value);$row=$r->create($seed->table,$values);if($nullChannel){$db->execute('UPDATE test_sender_suite_messages SET channel=NULL WHERE id=%d',array($row['id']));}}
$s=new \SenderSuite\SendHistory($db);$before=json_encode(array($db->select('contacts'),$db->select('messages')));
foreach($f->operations as $i=>$op){if($op->status!==200){sh_error('source invalid channel '.$i,function()use($s,$op){$s->listing((array)$op->query);},$op->status);continue;}$expected=$op->expected;foreach($expected->items as $item){if($item->error!==null&&$item->error!==''){$item->error='Message delivery reported an error.';}}$out=$s->listing((array)$op->query);sh_check('actual source history '.$i,$out,$expected);$json=json_encode($out);sh_check('provider metadata excluded '.$i,strpos($json,'NEVER EXPOSE'),false);sh_check('provider failure text excluded '.$i,strpos($json,'PRIVATE'),false);}
sh_check('read-only service changes no rows',json_encode(array($db->select('contacts'),$db->select('messages'))),$before);
foreach(array(array('page'=>0),array('page'=>1000001),array('per_page'=>101),array('page'=>array()),array('channel'=>array()),array('status'=>str_repeat('x',101)),array('search'=>str_repeat('x',1001)),array('search'=>"bad\0input")) as $q){sh_error('bounded query validation',function()use($s,$q){$s->listing($q);},422);}
$db->update('messages',array('body'=>str_repeat('x',4194305)),array('id'=>29));sh_error('oversized stored page fails before body load',function()use($s){$s->listing(array('per_page'=>1));},413);$db->update('messages',array('body'=>'small'),array('id'=>29));sh_check('smaller history page still usable',$s->listing(array('page'=>2,'per_page'=>1))['items'][0]['id'],1);
// TEXT can fit a raw budget yet expand beyond the JSON response budget.
$db->update('messages',array('body'=>str_repeat("\x01",1500000)),array('id'=>29));sh_error('JSON expansion response budget',function()use($s){$s->listing(array('per_page'=>1));},413);
$db->update('messages',array('body'=>'small'),array('id'=>29));$db->execute('DELETE FROM test_sender_suite_messages');sh_check('empty native history actual zeros',$s->listing(array()),array('total'=>0,'page'=>1,'per_page'=>50,'items'=>array()));
// Dry-run /send/validate checks for phone_number, contact_id, and list_id.
sh_error('empty send validate rejected',function()use($s){$s->validate(array());},400);
sh_error('missing contact send validate 404',function()use($s){$s->validate(array('contact_id'=>999999));},404);
$pvOk=$s->validate(array('phone_number'=>'08031234567'));
sh_check('phone_number sendable',array($pvOk['sendable'],$pvOk['items'][0]['normalized']),array(1,'+2348031234567'));
$pvBad=$s->validate(array('phone_number'=>'12345'));
sh_check('phone_number invalid',array($pvBad['sendable'],$pvBad['blocked']),array(0,1));
$valOk=$r->create('contacts',array('phone_number'=>'+2348039876543'));
$valSup=$r->create('contacts',array('phone_number'=>'+2348059876543'));
$valOpt=$r->create('contacts',array('phone_number'=>'+2348079876543','is_opted_out'=>true));
$r->create('suppression_entries',array('phone_number'=>'+2348059876543','reason'=>'STOP'));
sh_check('contact_id sendable',$s->validate(array('contact_id'=>$valOk['id']))['sendable'],1);
sh_check('contact_id suppressed blocked',$s->validate(array('contact_id'=>$valSup['id']))['items'][0]['reason'],'suppressed');
$lst=$r->create('contact_lists',array('name'=>'Send list','contact_count'=>3));
foreach(array($valOk['id'],$valSup['id'],$valOpt['id']) as $cid){$r->create('contact_list_members',array('list_id'=>$lst['id'],'contact_id'=>$cid));}
$lv=$s->validate(array('list_id'=>$lst['id']));
sh_check('list_id validation breakdown',array($lv['total'],$lv['sendable'],$lv['blocked']),array(3,1,2));
echo json_encode(array('ok'=>!$diff,'assertions'=>$count,'diffs'=>$diff,'php'=>PHP_VERSION,'source_operations'=>count($f->operations),'provider_messages_sent'=>0,'native_wordpress_verified'=>false,'native_mysql_verified'=>false,'scope'=>'Selected actual source outgoing history/channel AST and synthetic SQLite projection/privacy/budget tests. No HTTP, native collation/transactions/WordPress or browser qualification.'),JSON_THROW_ON_ERROR);
