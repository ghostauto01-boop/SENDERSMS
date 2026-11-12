<?php
namespace SenderSuite;

/** Source hard/soft SMS-number checks. A soft flag is never a live-send verdict. */
final class NumberFilter {
    private const PREFIXES=array('0701','07020','07025','07026','07027','07028','07029','0703','0704','0705','0706','0707','0708','0709','0801','0802','0803','0804','0805','0806','0807','0808','0809','0810','0811','0812','0813','0814','0815','0816','0817','0818','0819','0901','0902','0903','0904','0905','0906','0907','0908','0909','0911','0912','0913','0915','0916');
    private static function cleaned($raw) {
        $s=preg_replace('/[^\p{Nd}+]/u','',Compat::strip($raw));
        return strpos($s,'+')===0?'+'.str_replace('+','',substr($s,1)):str_replace('+','',$s);
    }
    private static function national($raw) {
        $s=preg_replace('/[^\p{Nd}]/u','',self::cleaned($raw??''));
        if(strpos($s,'234')===0){$s=substr($s,3);}elseif(strpos($s,'0')===0){$s=substr($s,1);}
        return Compat::length($s)===10?$s:null;
    }
    private static function hardFake($s) {
        if(!$s){return false;}
        return count(array_unique(Compat::points($s)))===1||preg_match('/^\d0{9}$/uD',$s)||in_array($s,array('0123456789','9876543210'),true);
    }
    public static function soft($raw) {
        $s=self::national($raw);if(!$s){return null;}
        if(in_array($s,array('8123456789','8012345678','8001234567'),true)){return 'test_data_suspected';}
        $digits=array_map(array(Compat::class,'decimal'),Compat::points($s));
        for($i=0;$i<count($digits)-7;$i++){$up=true;$down=true;for($j=0;$j<7;$j++){$up=$up&&$digits[$i+$j+1]-$digits[$i+$j]===1;$down=$down&&$digits[$i+$j]-$digits[$i+$j+1]===1;}if($up||$down){return 'test_data_suspected';}}
        return preg_match('/(\d)\1{7}/u',$s)?'test_data_suspected':null;
    }
    public static function classify($raw) {
        $normalized=null;$reason=null;$detail='';$raw=$raw??'';
        $clean=self::cleaned($raw);$digits=preg_replace('/[^\p{Nd}]/u','',$clean);$n=Compat::length($clean);
        if(Compat::strip($raw)===''){$reason='empty_number';$detail='No phone number on record.';}
        elseif(preg_match('/^\d+$/uD',$clean)&&$n>=3&&$n<=8){$reason='short_code';$detail="'".$raw."' looks like a short code, not a mobile number.";}
        elseif($clean===''||Compat::length($digits)<7){$reason='invalid_format';$detail="'".$raw."' is too short to be a phone number.";}
        else{
            $normalized=Phone::clean($raw);
            if($normalized===null){
                if(self::hardFake(self::national($raw))){$reason='test_number';$detail="'".$raw."' looks like a test/fake number.";}
                elseif(strpos($clean,'+')===0&&Compat::length($digits)>=8&&Compat::length($digits)<=15){$reason='foreign_number';$detail="'".$raw."' is not a Nigerian number — the SIM cannot deliver to it.";}
                else{$reason='invalid_format';$detail="'".$raw."' is not a valid Nigerian mobile number.";}
            }elseif(strpos($normalized,'+234')!==0){$reason='foreign_number';$detail="'".$raw."' is not a Nigerian number — the SIM cannot deliver to it.";}
            elseif(self::hardFake(substr($normalized,4))){$reason='test_number';$detail="'".$raw."' looks like a test/fake number.";}
            else{
                $mobile=false;foreach(self::PREFIXES as $p){if(strpos('0'.substr($normalized,4),$p)===0){$mobile=true;break;}}
                if(!$mobile){$reason='not_mobile';$detail="'".$raw."' is not a Nigerian mobile prefix (landline/special range).";}
            }
        }
        return array('sendable'=>$reason===null,'normalized'=>$normalized,'reason'=>$reason,'detail'=>$detail);
    }
}
