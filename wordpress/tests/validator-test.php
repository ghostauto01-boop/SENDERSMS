<?php
/** Offline local email/phone validator, enrichment status, and contacts/lists email validation tests. */
define('ARRAY_A','ARRAY_A');
foreach(array('schema','database','compat','phone','number-filter','auth-routes','records','contacts','contact-lists','contact-actions','provider-http','email-manager','campaign-worker','validator','data-routes') as $n){require __DIR__.'/../sender-suite/includes/class-'.$n.'.php';}
require __DIR__.'/sqlite-auth-db.php';
$count=0;$diff=array();$admin=array('id'=>1,'role'=>'admin');$viewer=array('id'=>2,'role'=>'viewer');
function vl_check($label,$a,$e){global $count,$diff;if($a===$e){$count++;}elseif(count($diff)<6){$diff[]=array('case'=>$label,'actual'=>$a,'expected'=>$e);}}
function vl_error($label,$fn,$status){try{$fn();vl_check($label,'accepted',$status);}catch(\SenderSuite\ValidationFailure $e){vl_check($label,422,$status);}catch(\SenderSuite\AuthFailure $e){vl_check($label,$e->status,$status);}}
$w=new SQLiteAuthDB();$w->addTables(array('contacts','contact_lists','contact_list_members','contact_tags','tags','contact_variables','suppression_entries','email_suppressions','email_verification_jobs','campaigns','campaign_contacts','campaign_followup_logs','conversations','messages','followups','meetings','meeting_attendees','scheduled_messages','quarantined_sms','ads_assignments','ads_events','ads_followup_tasks','email_contact_addresses','email_send_log','email_events','invoices'));
$db=new \SenderSuite\Database($w,new \SenderSuite\Schema('test_'));
$clock=function(){return '2026-01-10 12:00:00.000000';};
$r=new \SenderSuite\Records($db,$clock);$v=new \SenderSuite\Validator($db,$clock);

// /validator/status and /validator/self-test
$st=$v->status();
vl_check('validator status offline builtin',array($st['engine'],$st['dns_available'],$st['smtp_enabled'],$st['batch_size']),array('builtin',false,false,25));
$self=$v->selfTest();
vl_check('validator self-test passes all cases',$self['passed'],true);

// /validator/batch inline preview (read-only, allowed for viewer)
$inline=$v->batch((object)array('items'=>array(
    (object)array('email'=>'ada@gmail.com','phone_number'=>'08031234567','name'=>'Ada','row'=>1),
    (object)array('email'=>'user@gmial.com','phone_number'=>'123','name'=>'Typo','row'=>2),
    (object)array('email'=>'x@mailinator.com','phone_number'=>null,'name'=>'Disp','row'=>3),
    (object)array('email'=>'info@company.ng','phone_number'=>'+2348051234567','name'=>'Role','row'=>4),
)),$viewer);
vl_check('inline batch verdicts',array($inline['items'][0]['email']['verdict'],$inline['items'][0]['phone']['verdict'],$inline['items'][1]['email']['verdict'],$inline['items'][1]['email']['suggested_email'],$inline['items'][1]['phone']['verdict'],$inline['items'][2]['email']['verdict'],$inline['items'][3]['email']['verdict']),array('unknown','good','unknown','user@gmail.com','bad','risky','risky'));

// Stored contacts + selection + batch save=true
$cGood=$r->create('contacts',array('first_name'=>'Good','phone_number'=>'+2348031112222','email'=>'good@business.ng'));
$cBadEmailTextable=$r->create('contacts',array('first_name'=>'Textable','phone_number'=>'+2348032223333','email'=>'bad@@invalid'));
$cBadBoth=$r->create('contacts',array('first_name'=>'Unreachable','phone_number'=>'999','email'=>'trash@bad.invalid'));
$lst=$r->create('contact_lists',array('name'=>'Mixed List','contact_count'=>3));
foreach(array($cGood,$cBadEmailTextable,$cBadBoth) as $ct){$r->create('contact_list_members',array('list_id'=>$lst['id'],'contact_id'=>$ct['id']));}

$sel=$v->selection((object)array('scope'=>'list','list_id'=>$lst['id']));
vl_check('selection resolves list contact_ids',$sel['contact_ids'],array($cGood['id'],$cBadEmailTextable['id'],$cBadBoth['id']));
vl_error('viewer cannot save batch verdicts',function()use($v,$cGood,$viewer){$v->batch((object)array('contact_ids'=>array($cGood['id']),'save'=>true),$viewer);},403);
$savedBatch=$db->transaction(function()use($v,$cBadBoth,$admin){return $v->batch((object)array('contact_ids'=>array($cBadBoth['id']),'save'=>true),$admin);});
vl_check('batch save marks undeliverable email and bad phone',array($savedBatch['items'][0]['saved'],(bool)$db->one('contacts',array('id'=>$cBadBoth['id']))['is_email_undeliverable'],(bool)$db->one('contacts',array('id'=>$cBadBoth['id']))['is_undeliverable']),array(true,true,true));

// Jobs: live MX-aware verification job + cancel stored running job.
$rpDns=new ReflectionProperty(\SenderSuite\ProviderHttp::class,'dns');$rpDns->setAccessible(true);
$rpDns->setValue(null,function($host,$type){return $host==='sendify.ng'&&$type==='MX'?array(array('target'=>'mx.sendify.ng')):array();});
$liveJob=$v->createJob((object)array('scope'=>'list','list_id'=>$lst['id']),$admin);
vl_check('createJob runs live verification job to completed',array($liveJob['status'],$liveJob['total']),array('completed',3));
$jobRow=$r->create('email_verification_jobs',array('id'=>'job-abc-123','status'=>'running','deep'=>true,'recheck'=>false,'contact_ids_json'=>'[1,2]','total'=>10,'processed'=>4));
vl_check('jobs list and read',array(count($v->jobs()['items']),$v->job('job-abc-123')['status']),array(2,'running'));
$cancelled=$db->transaction(function()use($v,$admin){return $v->cancelJob('job-abc-123',$admin);});
vl_check('cancelJob transitions running to cancelled',$cancelled['status'],'cancelled');

// /contacts/enrich/status, /contacts/enrich, /contacts/validate-emails, /lists/{id}/validate-emails
$enr=$v->enrichmentStatus();
vl_check('enrichmentStatus SQL counts',array($enr['contacts']['total'],$enr['contacts']['with_email']),array(3,3));
$valEmails=$db->transaction(function()use($v,$cGood,$cBadEmailTextable,$admin){return $v->validateContactEmails(array('contact_ids'=>$cGood['id'].','.$cBadEmailTextable['id']),$admin);});
vl_check('validateContactEmails counts',array($valEmails['scanned'],$valEmails['undeliverable'],$valEmails['unknown']),array(2,1,1));
// List validation with remove_from_list=true: cBadEmailTextable has a valid phone so MUST remain in list; cBadBoth has bad email + bad phone so MUST be removed!
$listVal=$db->transaction(function()use($v,$lst,$admin){return $v->validateListEmails($lst['id'],array('remove_from_list'=>'true','delete_contacts'=>'false'),$admin);});
vl_check('validateListEmails removes only non-textable bad-email contact',array($listVal['removed_from_list'],$listVal['deleted'],(int)$db->one('contact_lists',array('id'=>$lst['id']))['contact_count']),array(1,0,2));

class VlFakeReq{public $r,$m,$b;public function __construct($r,$m,$b='{}'){$this->r=$r;$this->m=$m;$this->b=$b;}public function get_route(){return $this->r;}public function get_method(){return $this->m;}public function get_query_params(){return array();}public function get_body(){return $this->b;}}
$stCode=200;
$enrRes=\SenderSuite\Validator::handle(new VlFakeReq('/sender-suite/v1/contacts/enrich','POST','{}'),array('db'=>$db),$admin,$stCode);
vl_check('contacts/enrich POST enriches contacts',array($stCode,$enrRes['enriched']>=1),array(200,true));

echo json_encode(array('ok'=>!$diff,'assertions'=>$count,'diffs'=>$diff,'php'=>PHP_VERSION,'native_wordpress_verified'=>false,'native_mysql_verified'=>false,'scope'=>'Offline local email/phone validator, enrichment status, and contacts/lists email validation on synthetic SQLite.'),JSON_THROW_ON_ERROR);
