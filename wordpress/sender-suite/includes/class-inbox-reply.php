<?php
namespace SenderSuite;

/** Explicit single-SMS replies through the opt-in durable queue; never performs inline network I/O. */
final class InboxReply {
    public static function definitions(){return array('/inbox/conversations/(?P<conversation_id>-?[0-9]+)/reply'=>array('POST'));}
    public static function handle($request,array $context,array $user,&$status){
        if(($user['role']??'')!=='admin'){throw new AuthFailure(403,'Application administrator role required.');}
        if(!SmsOutbox::enabled()){throw new AuthFailure(503,'Native SMS delivery is disabled pending host qualification.');}
        $path=trim(substr($request->get_route(),strlen('/sender-suite/v1/')),'/');
        if($request->get_method()!=='POST'||!preg_match('~^inbox/conversations/(-?[0-9]+)/reply$~D',$path,$match)){throw new AuthFailure(404,'Application endpoint not found.');}
        $id=DataRoutes::integer($match[1],'conversation_id',1);
        $query=$request->get_query_params();unset($query['rest_route']);
        if($query){throw new ValidationFailure('Queued replies accept JSON only. Do not put message text in the URL.');}
        if(strlen($request->get_body())>8192){throw new AuthFailure(413,'Reply request is too large.');}
        $body=DataRoutes::body($request);
        if(!is_object($body)){throw new ValidationFailure('Supply a JSON object');}
        foreach($body as $key=>$value){
            if(in_array($key,array('template_id','subject','schedule_at'),true)){throw new AuthFailure(503,'Template, email and scheduled inbox replies are not implemented on this queued path.');}
            if(!in_array($key,array('body','gateway_setting_id'),true)){throw new ValidationFailure('Unknown queued reply field',$key);}
        }
        $db=$context['db'];
        $rows=$db->rows('SELECT id,contact_id,channel FROM '.Schema::identifier($db->schema()->table('conversations')).' WHERE id=%d',array($id));
        if(!$rows){throw new AuthFailure(404,'Conversation not found');}
        $conversation=$db->decode('conversations',$rows[0]);
        if(($conversation['channel']??'sms')!=='sms'){throw new AuthFailure(503,'Email replies require a native email transport; no SMS fallback is permitted.');}
        $text=Compat::strip(DataRoutes::text(get_object_vars($body),'body',null,1,1600)??'');
        if($text===''){throw new ValidationFailure('Message body or template is required');}
        $gateway=DataRoutes::integer($body->gateway_setting_id??null,'gateway_setting_id',1);
        $reply=(new SmsOutbox($db,$context['config']))->enqueue($conversation['contact_id'],$gateway,$text,$request->get_header('idempotency-key'),$user);
        $message=$db->rows('SELECT conversation_id FROM '.Schema::identifier($db->schema()->table('messages')).' WHERE id=%d',array($reply['message_id']));
        if(!$message||(int)$message[0]['conversation_id']!==$id){throw new AuthFailure(409,'Reply conversation changed. No message was submitted.');}
        $status=202;$reply['channel']='sms';return $reply;
    }
}
