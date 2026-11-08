<?php
namespace SenderSuite;

/** Protected by the common own-application session/origin/transaction dispatcher. */
final class ReplyRoutes {
    public static function definitions() {
        $out=array('/ai/classify'=>array('POST')); foreach (array('autoreply'=>'rule_id','automations'=>'automation_id') as $root=>$id) {
            $out['/'.$root.'/?']=array('GET','POST'); $out['/'.$root.'/test']=array('POST'); $out['/'.$root.'/(?P<'.$id.'>-?[0-9]+)']=array('GET','PUT','DELETE');
        } return $out;
    }
    public static function handle($request,array $context,&$status) {
        $path=trim(substr($request->get_route(),strlen('/sender-suite/v1/')),'/'); $method=$request->get_method(); $q=$request->get_query_params(); $service=new ReplyRules($context['db']);
        if ($path==='ai/classify' && $method==='POST') { $body=DataRoutes::body($request); if (!is_object($body)) { throw new ValidationFailure('Input should be a valid dictionary or object to extract fields from'); } if (!property_exists($body,'text')) { throw new ValidationFailure('Field required','text'); } if (!is_string($body->text)) { throw new ValidationFailure('Input should be a valid string','text'); } return ReplyClassifier::classify($body->text); }
        if (!preg_match('#^(autoreply|automations)(?:/(test|-?[0-9]+))?$#D',$path,$m)) { throw new AuthFailure(404,'Not Found'); }
        $automation=$m[1]==='automations'; $action=$m[2]??'';
        if ($action==='') {
            if ($method==='GET') { return $service->listing(DataRoutes::text($q,'channel'),$automation); }
            if ($method==='POST') { $status=201; return $service->create(DataRoutes::body($request),$automation); }
        } elseif ($action==='test' && $method==='POST') { return $automation?$service->testAutomation(DataRoutes::body($request)):$service->testReply(DataRoutes::body($request)); }
        elseif ($action!=='test') {
            $id=DataRoutes::integer($action,$automation?'automation_id':'rule_id');
            if ($method==='GET') { return $service->out($service->find($id,$automation),$automation); }
            if ($method==='PUT') { return $service->update($id,DataRoutes::body($request),$automation); }
            if ($method==='DELETE') { $service->delete($id,$automation); $status=204; return null; }
        }
        throw new AuthFailure(404,'Not Found');
    }
}
