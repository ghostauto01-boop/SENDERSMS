<?php
namespace SenderSuite;

/** ASCII normalized-key matching compatible with difflib's no-junk SequenceMatcher. */
final class Suggestions {
    public static function ratio($a, $b) {
        $a=(string)$a; $b=(string)$b; $na=strlen($a); $nb=strlen($b);
        if (!$na && !$nb) { return 1.0; }
        $positions=array();
        for ($j=0;$j<$nb;$j++) { $positions[$b[$j]][]=$j; }
        if ($nb>=200) { foreach ($positions as $c=>$ids) { if (count($ids)>intdiv($nb,100)+1) { unset($positions[$c]); } } }
        $queue=array(array(0,$na,0,$nb)); $matched=0;
        while ($queue) {
            list($alo,$ahi,$blo,$bhi)=array_pop($queue);
            $bi=$alo; $bj=$blo; $best=0; $previous=array();
            for ($i=$alo;$i<$ahi;$i++) {
                $next=array();
                foreach ($positions[$a[$i]]??array() as $j) {
                    if ($j<$blo) { continue; } if ($j>=$bhi) { break; }
                    $k=($previous[$j-1]??0)+1; $next[$j]=$k;
                    if ($k>$best) { $bi=$i-$k+1; $bj=$j-$k+1; $best=$k; }
                }
                $previous=$next;
            }
            while ($bi>$alo && $bj>$blo && $a[$bi-1]===$b[$bj-1]) { $bi--; $bj--; $best++; }
            while ($bi+$best<$ahi && $bj+$best<$bhi && $a[$bi+$best]===$b[$bj+$best]) { $best++; }
            if (!$best) { continue; }
            $matched+=$best;
            if ($alo<$bi && $blo<$bj) { $queue[]=array($alo,$bi,$blo,$bj); }
            if ($bi+$best<$ahi && $bj+$best<$bhi) { $queue[]=array($bi+$best,$ahi,$bj+$best,$bhi); }
        }
        return 2.0*$matched/($na+$nb);
    }
    public static function closest($name,array $known) {
        $word=Templating::key($name); $best=null; $score=0.7;
        foreach ($known as $candidate) {
            $candidate=(string)$candidate; $sum=strlen($candidate)+strlen($word); if ($sum && 2.0*min(strlen($candidate),strlen($word))/$sum<$score) { continue; }
            $ratio=self::ratio($candidate,$word);
            if ($ratio>$score || ($ratio===$score && ($best===null || strcmp($candidate,$best)>0))) { $best=$candidate; $score=$ratio; }
        }
        return $best;
    }
}
