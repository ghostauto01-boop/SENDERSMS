<?php
/** Read-only unified overview directory, ambiguous-ID 409 guard, classic detail, and metrics tests. */
define('ARRAY_A','ARRAY_A');
foreach(array('schema','database','compat','phone','auth-routes','records','analytics','attachments','contact-export','campaign-reports','ads-manager','overview','data-routes') as $n){require __DIR__.'/../sender-suite/includes/class-'.$n.'.php';}
require __DIR__.'/sqlite-auth-db.php';
$count=0;$diff=array();
function ov_check($label,$a,$e){global $count,$diff;if($a===$e){$count++;}elseif(count($diff)<6){$diff[]=array('case'=>$label,'actual'=>$a,'expected'=>$e);}}
function ov_error($label,$fn,$status,$code=null){try{$fn();ov_check($label,'accepted',$status);}catch(\SenderSuite\DetailedFailure $e){ov_check($label,$e->status,$status);if($code!==null){ov_check($label.' code',$e->detail['code']??null,$code);}}catch(\SenderSuite\ValidationFailure $e){ov_check($label,422,$status);}catch(\SenderSuite\AuthFailure $e){ov_check($label,$e->status,$status);}}
$w=new SQLiteAuthDB();$w->addTables(array('campaigns','ads_campaigns','ads_sets','ads_creatives','ads_followup_steps','ads_campaign_state_transitions','ads_assignments','contacts','conversations','messages'));
$db=new \SenderSuite\Database($w,new \SenderSuite\Schema('test_'));
$clock=function(){return '2026-01-10 12:00:00.000000';};
$r=new \SenderSuite\Records($db,$clock);$ov=new \SenderSuite\Overview($db,$clock);
$c1=$r->create('contacts',array('first_name'=>'Ada','phone_number'=>'+2348031234567','email'=>'ada@example.ng','is_opted_out'=>true));
$c2=$r->create('contacts',array('first_name'=>'Bola','phone_number'=>'+2348051234567','email'=>'bola@example.ng','is_email_opted_out'=>true));
// Classic campaign #1 (running SMS) and #2 (draft email), plus Ads campaign #1 (ambiguous ID) and #3 (Ads-only active).
$r->create('campaigns',array('name'=>'Live SMS','channel'=>'sms','status'=>'running','total_contacts'=>10));
$r->create('campaigns',array('name'=>'Draft Email','channel'=>'email','status'=>'draft','total_contacts'=>5));
$r->create('ads_campaigns',array('name'=>'Ads Twin','channel'=>'sms','status'=>'draft','objective'=>'leads'));
$ads3=$r->create('ads_campaigns',array('name'=>'Ads Active','channel'=>'email','status'=>'active','objective'=>'traffic'));
$r->create('ads_assignments',array('campaign_id'=>$ads3['id'],'set_id'=>1,'creative_id'=>1,'contact_id'=>$c2['id'],'send_status'=>'sent','delivery_status'=>'delivered'));
$r->create('ads_assignments',array('campaign_id'=>$ads3['id'],'set_id'=>1,'creative_id'=>1,'contact_id'=>$c1['id'],'send_status'=>'pending','delivery_status'=>'pending'));
$convSms=$r->create('conversations',array('contact_id'=>$c1['id'],'channel'=>'sms','campaign_id'=>1,'status'=>'interested','unread_count'=>2));
$convEmail=$r->create('conversations',array('contact_id'=>$c2['id'],'channel'=>'email','ads_campaign_id'=>$ads3['id'],'status'=>'open','unread_count'=>0));
$r->create('messages',array('conversation_id'=>$convSms['id'],'contact_id'=>$c1['id'],'campaign_id'=>1,'channel'=>'sms','direction'=>'outgoing','status'=>'delivered','body'=>'Promo','created_at'=>'2026-01-09 10:00:00.000000','idempotency_key'=>'ov-out-1'));
$r->create('messages',array('conversation_id'=>$convSms['id'],'contact_id'=>$c1['id'],'campaign_id'=>1,'channel'=>'sms','direction'=>'outgoing','status'=>'failed','body'=>'Promo2','created_at'=>'2026-01-09 10:05:00.000000','idempotency_key'=>'ov-out-2'));
$r->create('messages',array('conversation_id'=>$convSms['id'],'contact_id'=>$c1['id'],'campaign_id'=>1,'channel'=>'sms','direction'=>'incoming','status'=>'received','body'=>'Yes please','ai_sentiment'=>'positive','created_at'=>'2026-01-09 11:00:00.000000','idempotency_key'=>'ov-in-1'));
$snap=hash('sha256',json_encode(array($db->select('campaigns'),$db->select('ads_campaigns'),$db->select('conversations'),$db->select('messages'))));

$dir=$ov->directory();
ov_check('directory count',$dir['total'],4);
// Live campaigns sort before non-live; higher sent first among live (both have sent=1, then alphabetical: Ads Active before Live SMS).
ov_check('live sorted first',array($dir['items'][0]['name'],$dir['items'][1]['name'],$dir['items'][0]['is_live'],$dir['items'][2]['is_live']),array('Ads Active','Live SMS',true,false));
ov_check('directory totals',array($dir['totals']['campaigns'],$dir['totals']['live'],$dir['totals']['sent'],$dir['totals']['delivered'],$dir['totals']['replied'],$dir['totals']['interested']),array(4,2,2,2,1,1));
ov_check('sms inbox links',array($dir['items'][1]['inbox_url'],$dir['items'][1]['replies_url']),array('/inbox?campaign_id=1','/inbox?campaign_id=1&replied=1'));
ov_check('email ads inbox links',array($dir['items'][0]['inbox_url'],$dir['items'][0]['replies_url']),array('/email-inbox?ads_campaign_id='.$ads3['id'],'/email-inbox?ads_campaign_id='.$ads3['id'].'&view=replied'));
ov_check('filter kind legacy',$ov->directory(array('kind'=>'legacy'))['total'],2);
ov_check('filter live=true',$ov->directory(array('live'=>'true'))['total'],2);
ov_check('pagination next_page',$ov->directory(array('page'=>1,'per_page'=>2))['next_page'],2);
ov_error('invalid kind rejected',function()use($ov){$ov->directory(array('kind'=>'bogus'));},422);
ov_error('invalid channel rejected',function()use($ov){$ov->directory(array('channel'=>'fax'));},422);

// Detail: ambiguous ID 1 without kind -> 409 AMBIGUOUS_CAMPAIGN_ID; with kind=campaign -> 200; Ads-only -> 503; missing -> 404.
ov_error('ambiguous campaign id without kind',function()use($ov){$ov->detail(1);},409,'AMBIGUOUS_CAMPAIGN_ID');
$det=$ov->detail(1,array('kind'=>'campaign'));
ov_check('classic detail resolved with kind',array($det['id'],$det['kind'],$det['system'],$det['metrics']['sent'],$det['metrics']['replied']),array(1,'campaign','legacy',1,1));
$adsDet=$ov->detail($ads3['id'],array('kind'=>'ads'));
ov_check('ads detail returns full AdsManager detail payload',array($adsDet['kind'],$adsDet['id']),array('ads',$ads3['id']));
ov_error('missing detail 404',function()use($ov){$ov->detail(999);},404);

// Metrics: daily series, channel-scoped opt-out rate, sentiment.
$mAll=$ov->metrics(array('days'=>7));
ov_check('metrics period and series length',array($mAll['channel'],$mAll['period_days'],count($mAll['series'])),array('all',7,7));
ov_check('metrics messaging and sentiment',array($mAll['messaging']['sent'],$mAll['messaging']['failed'],$mAll['messaging']['replies'],$mAll['inbox']['sentiment']['positive']),array(1,1,1,1));
$mEmail=$ov->metrics(array('days'=>7,'channel'=>'email'));
ov_check('email channel uses is_email_opted_out',array($mEmail['audience']['contacts'],$mEmail['audience']['opted_out']),array(2,1));
ov_check('overview never mutates storage',hash('sha256',json_encode(array($db->select('campaigns'),$db->select('ads_campaigns'),$db->select('conversations'),$db->select('messages')))),$snap);

echo json_encode(array('ok'=>!$diff,'assertions'=>$count,'diffs'=>$diff,'php'=>PHP_VERSION,'native_wordpress_verified'=>false,'native_mysql_verified'=>false,'scope'=>'Unified read-only overview directory, ambiguous ID 409 contract, classic detail and rolling metrics on synthetic SQLite.'),JSON_THROW_ON_ERROR);
