<?php
namespace SenderSuite;

/** Internal dispatcher; DataRoutes provides origin, session and transaction checks. */
final class TemplateRoutes {
    public static function definitions() {
        return array('/templates/?'=>array('GET','POST'),'/templates/categories'=>array('GET'),'/templates/preview'=>array('POST'),'/templates/(?P<template_id>-?[0-9]+)'=>array('GET','PUT','DELETE'),'/templates/(?P<template_id>-?[0-9]+)/duplicate'=>array('POST'),'/templates/(?P<template_id>-?[0-9]+)/to-email'=>array('POST'));
    }
    public static function handle($request,array $context,&$status) {
        $path=trim(substr($request->get_route(),strlen('/sender-suite/v1/')),'/'); $method=$request->get_method(); $q=$request->get_query_params(); $s=new Templates($context['db']);
        if ($path==='templates') {
            if ($method==='GET') { return $s->listing(DataRoutes::integer($q['page']??1,'page',1),DataRoutes::integer($q['per_page']??25,'per_page',1,200),DataRoutes::text($q,'search'),DataRoutes::text($q,'category'),DataRoutes::text($q,'channel')); }
            if ($method==='POST') { $status=201; return $s->create(DataRoutes::body($request)); }
        }
        if ($path==='templates/categories' && $method==='GET') { return $s->categories(DataRoutes::text($q,'channel')); }
        if ($path==='templates/preview' && $method==='POST') {
            $overrides=array(); foreach (array('first_name','last_name','business_name','phone_number','email','city','state','website','industry') as $key) { $overrides[$key]=DataRoutes::text($q,$key); }
            return $s->preview(DataRoutes::text($q,'body',''),DataRoutes::text($q,'subject'),DataRoutes::text($q,'html_body'),DataRoutes::text($q,'channel','sms'),isset($q['contact_id'])?DataRoutes::integer($q['contact_id'],'contact_id'):null,$overrides);
        }
        if (preg_match('#^templates/(-?[0-9]+)(?:/(duplicate|to-email))?$#D',$path,$m)) {
            $id=DataRoutes::integer($m[1],'template_id'); $action=$m[2]??'';
            if ($action==='duplicate' && $method==='POST') { return $s->duplicate($id); }
            if ($action==='to-email' && $method==='POST') { return $s->duplicate($id,true); }
            if ($action==='') {
                if ($method==='GET') { return $s->out($s->find($id)); }
                if ($method==='PUT') { return $s->update($id,DataRoutes::body($request)); }
                if ($method==='DELETE') { $s->delete($id); $status=204; return null; }
            }
        }
        throw new AuthFailure(404,'Not Found');
    }
}
