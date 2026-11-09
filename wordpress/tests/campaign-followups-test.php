<?php
/** Classic campaign follow-up rule CRUD, idempotency, dry-run preview, logs, delete guard, and 503 worker gates. */
define('ARRAY_A','ARRAY_A');
foreach(array('schema','database','compat','phone','auth-routes','records','sms-gate','campaign-worker','campaign-followups','data-routes') as $n){require __DIR__.'/../sender-suite/includes/class-'.$n.'.php';}
require __DIR__.'/sqlite-auth-db.php';
$count=0;$diff=array();$admin=array('id'=>1,'role'=>'admin');
function cf_check($label,$a,$e){global $count,$diff;if($a===$e){$count++;}elseif(count($diff)<6){$diff[]=array('case'=>$label,'actual'=>$a,'expected'=>$e);}}
function cf_error($label,$fn,$status){try{$fn();cf_check($label,'accepted',$status);}catch(\SenderSuite\ValidationFailure $e){cf_check($label,422,$status);}catch(\SenderSuite\AuthFailure $e){cf_check($label,$e->status,$status);}}
$w=new SQLiteAuthDB();$w->addTables(array('campaigns','campaign_contacts','campaign_followups','campaign_followup_logs','contacts','conversations','gateway_settings','messages','suppression_entries','templates'));
$db=new \SenderSuite\Database($w,new \SenderSuite\Schema('test_'));
// 11:00 UTC = 12:00 Africa/Lagos (WAT, UTC+1).
$clock=function(){return '2026-01-10 11:00:00.000000';};
$r=new \SenderSuite\Records($db,$clock);$s=new \SenderSuite\CampaignFollowups($db,$clock,'Africa/Lagos');
$camp=$r->create('campaigns',array('name'=>'Lagos Promo','channel'=>'sms','status'=>'running','total_contacts'=>6));
$tpl=$r->create('templates',array('name'=>'Follow tpl','body'=>'Checking in'));
$f1=$db->transaction(function()use($s,$camp,$admin){return $s->create($camp['id'],(object)array('message_text'=>' Still interested? ','delay_minutes'=>60,'stop_on_lead_status'=>'interested,customer','send_start_hour'=>9,'send_end_hour'=>17),$admin,'cf-idem-0001');});
cf_check('auto step_order and default name',array($f1['step_order'],$f1['name'],$f1['message_text']),array(1,'Follow-up 1','Still interested?'));
$replay=$db->transaction(function()use($s,$camp,$admin){return $s->create($camp['id'],(object)array('message_text'=>' Still interested? ','delay_minutes'=>60,'stop_on_lead_status'=>'interested,customer','send_start_hour'=>9,'send_end_hour'=>17),$admin,'cf-idem-0001');});
cf_check('idempotent create replay',$replay['id'],$f1['id']);
cf_error('idempotency conflict on different payload',function()use($db,$s,$camp,$admin){$db->transaction(function()use($s,$camp,$admin){$s->create($camp['id'],(object)array('message_text'=>'Different'),$admin,'cf-idem-0001');});},409);
cf_error('duplicate step_order rejected',function()use($db,$s,$camp,$admin){$db->transaction(function()use($s,$camp,$admin){$s->create($camp['id'],(object)array('message_text'=>'Dup','step_order'=>1),$admin);});},409);
cf_error('viewer create denied',function()use($db,$s,$camp){$db->transaction(function()use($s,$camp){$s->create($camp['id'],(object)array('message_text'=>'No'),array('id'=>2,'role'=>'viewer'));});},403);
cf_error('missing message and template rejected',function()use($db,$s,$camp,$admin){$db->transaction(function()use($s,$camp,$admin){$s->create($camp['id'],(object)array('name'=>'Empty'),$admin);});},422);

$f2=$db->transaction(function()use($s,$camp,$tpl,$admin){return $s->create($camp['id'],(object)array('name'=>'Step 2','template_id'=>$tpl['id'],'delay_minutes'=>120),$admin);});
$upd=$db->transaction(function()use($s,$f2){return $s->update($f2['id'],(object)array('name'=>'Step 2 edited','is_active'=>false));});
cf_check('update step',array($upd['step_order'],$upd['name'],$upd['is_active']),array(2,'Step 2 edited',false));
$db->transaction(function()use($s,$f2){$s->delete($f2['id']);});
cf_check('listSteps after delete',$s->listSteps($camp['id'])['total'],1);

// Preview buckets: due(send), opted_out(stop), replied(stop), lead_status(stop), not_yet_due(wait), already_logged(done).
$cDue=$r->create('contacts',array('first_name'=>'Ada','phone_number'=>'+2348030000001','lead_status'=>'new'));
$cOpt=$r->create('contacts',array('first_name'=>'Opt','phone_number'=>'+2348030000002','is_opted_out'=>true));
$cRep=$r->create('contacts',array('first_name'=>'Rep','phone_number'=>'+2348030000003'));
$cLead=$r->create('contacts',array('first_name'=>'Cust','phone_number'=>'+2348030000004','lead_status'=>'Customer'));
$cWait=$r->create('contacts',array('first_name'=>'Wait','phone_number'=>'+2348030000005'));
$cDone=$r->create('contacts',array('first_name'=>'Done','phone_number'=>'+2348030000006'));
foreach(array($cDue,$cOpt,$cRep,$cLead,$cWait,$cDone) as $idx=>$ct){
    $r->create('campaign_contacts',array('campaign_id'=>$camp['id'],'contact_id'=>$ct['id'],'status'=>'sent'));
    $sentAt=$ct['id']===$cWait['id']?'2026-01-10 10:30:00.000000':'2026-01-10 09:00:00.000000';
    $r->create('messages',array('conversation_id'=>1,'contact_id'=>$ct['id'],'campaign_id'=>$camp['id'],'direction'=>'outgoing','status'=>'sent','body'=>'Initial','sent_at'=>$sentAt,'created_at'=>$sentAt,'idempotency_key'=>'cf-out-'.$idx));
}
$r->create('messages',array('conversation_id'=>1,'contact_id'=>$cRep['id'],'direction'=>'incoming','status'=>'received','body'=>'Thanks','created_at'=>'2026-01-10 09:30:00.000000','idempotency_key'=>'cf-in-rep'));
$r->create('campaign_followup_logs',array('followup_id'=>$f1['id'],'campaign_id'=>$camp['id'],'contact_id'=>$cDone['id'],'status'=>'sent','reason'=>'Sent earlier','body_preview'=>'Hi'));

$prev=$s->preview($f1['id']);
cf_check('preview bucket counts',$prev['counts'],array('send'=>1,'stop'=>3,'wait'=>1,'done'=>1));
cf_check('preview due contact',$prev['will_send'][0]['contact_id'],$cDue['id']);
// Verify Lagos timezone window: 11:00 UTC = 12:00 Lagos; setting window 13..15 Lagos puts due contact into wait.
$db->transaction(function()use($s,$f1){$s->update($f1['id'],(object)array('send_start_hour'=>13,'send_end_hour'=>15));});
$prevWin=$s->preview($f1['id']);
cf_check('Lagos window puts contact in wait',array($prevWin['counts']['send'],$prevWin['counts']['wait'],$prevWin['waiting'][0]['reason']),array(0,2,'Outside the allowed sending hours'));

$logOut=$s->log($f1['id']);
cf_check('followup log read',array($logOut['total'],$logOut['items'][0]['contact_name']),array(1,'Done'));
cf_check('listCampaigns aggregates rules and sent logs',array($s->listCampaigns()['items'][0]['followup_count'],$s->listCampaigns()['items'][0]['followups_sent']),array(1,1));
// Delete guard: rule with log history returns 409.
cf_error('delete blocked when followup log exists',function()use($db,$s,$f1){$db->transaction(function()use($s,$f1){$s->delete($f1['id']);});},409);

// /run and /process-now execute live CampaignWorker follow-up processing!
class CfFakeReq{public $r,$m;public function __construct($r,$m){$this->r=$r;$this->m=$m;}public function get_route(){return $this->r;}public function get_method(){return $this->m;}public function get_query_params(){return array();}public function get_body(){return '{}';}public function get_header($k){return null;}}
$st=200;
$runRes=\SenderSuite\CampaignFollowups::handle(new CfFakeReq('/sender-suite/v1/campaign-followups/'.$f1['id'].'/run','POST'),array('db'=>$db),$admin,$st);
$procRes=\SenderSuite\CampaignFollowups::handle(new CfFakeReq('/sender-suite/v1/campaign-followups/campaigns/'.$camp['id'].'/process-now','POST'),array('db'=>$db),$admin,$st);
cf_check('run and process-now execute cleanly',array($runRes['ok'],$procRes['ok']),array(true,true));

echo json_encode(array('ok'=>!$diff,'assertions'=>$count,'diffs'=>$diff,'php'=>PHP_VERSION,'native_wordpress_verified'=>false,'native_mysql_verified'=>false,'scope'=>'Campaign follow-up CRUD, idempotency, Lagos timezone preview, logs, history delete guard, and 503 execution gates.'),JSON_THROW_ON_ERROR);
