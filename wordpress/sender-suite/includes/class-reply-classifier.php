<?php
namespace SenderSuite;

/** Deterministic source reply lexicon. No AI API, model service or network dependency. */
final class ReplyClassifier {
    private static function data() { static $d; if ($d===null) { $d=json_decode(file_get_contents(dirname(__DIR__).'/schema/reply-classifier.json'),true,512,JSON_THROW_ON_ERROR); } return $d; }
    private static function hit($pattern,$text) { return preg_match('~(*UCP)'.str_replace('~','\\~',$pattern).'~iu',str_replace(array('İ','ı'),'i',$text))===1; }
    public static function classify($text,$contact=null) {
        $d=self::data(); $message=Compat::strip($text); $lower=Compat::lower($message); $score=0.0; $hits=array('positive'=>array(),'negative'=>array());
        preg_match_all("/[a-z0-9']+/",$lower,$m); $tokens=array_fill_keys($m[0],true);
        foreach (array('positive','negative') as $kind) {
            $words=$d[$kind]; $ordered=array_keys($words); $order=array_flip($ordered); usort($ordered,function($a,$b)use($order){return (strlen($b)-strlen($a))?:($order[$a]-$order[$b]);});
            foreach ($ordered as $phrase) { if (strpos($phrase,' ')!==false && strpos($lower,$phrase)!==false) { $score+=($kind==='positive'?1:-1)*$words[$phrase]; $hits[$kind][]=$phrase; } }
        }
        foreach (array('positive','negative') as $kind) { foreach ($d[$kind] as $phrase=>$weight) { if (strpos($phrase,' ')===false && isset($tokens[$phrase])) { $score+=($kind==='positive'?1:-1)*$weight; $hits[$kind][]=$phrase; } } }
        if (self::hit($d['leading_negation'],$lower)) { $score-=1.0; } $score=round($score,2,PHP_ROUND_HALF_EVEN);
        $intent='general'; $matched=null; foreach ($d['intents'] as $entry) { if (self::hit($entry[1],$message)) { $intent=$entry[0]; $matched=$entry[1]; break; } }
        $yes=self::hit($d['positive_confirm'],$message); $no=self::hit($d['negative_confirm'],$message);
        if (in_array($intent,array('opt_out','wrong_number','not_interested'),true)) { $label='negative'; }
        elseif (in_array($intent,array('interested','thanks'),true)) { $label='positive'; }
        elseif ($no) { $label='negative'; } elseif ($yes) { $label='positive'; } elseif ($score>=1) { $label='positive'; } elseif ($score<=-1) { $label='negative'; } else { $label='neutral'; }
        $labels=array(); if ($yes) { $labels[]='yes'; } if ($no) { $labels[]='no'; } $labels[]=$label; if ($intent!=='general') { $labels[]=$intent; } $question=$intent==='confirm_business_question'; if ($question) { $labels[]='business_question'; }
        if ($intent==='opt_out') { $action='opt_out'; } elseif ($intent==='wrong_number') { $action='remove_contact'; } elseif ($intent==='not_interested'||$no||$label==='negative') { $action='stop_and_remove'; } elseif ($intent==='interested'||$yes||$label==='positive') { $action='continue_or_send_next'; } elseif ($intent==='pricing_question') { $action='send_pricing'; } elseif ($intent==='hours_question') { $action='send_hours'; } else { $action='none'; }
        $c=(array)$contact;
        return array('sentiment'=>$label,'score'=>$score,'confidence'=>round(min(0.99,0.5+abs($score)*0.14),2,PHP_ROUND_HALF_EVEN),'intent'=>$intent,'matched_pattern'=>$matched,'is_business_question'=>$question,'confirm_yes'=>$yes,'confirm_no'=>$no,'positive_hits'=>array_slice($hits['positive'],0,10),'negative_hits'=>array_slice($hits['negative'],0,10),'labels'=>$labels,'business_name'=>Compat::strip($c['business_name']??''),'suggested_action'=>$action);
    }
}
