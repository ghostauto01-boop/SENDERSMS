<?php

namespace SenderSuite\Vendor\Phone\Leniency;

use SenderSuite\Vendor\Phone\PhoneNumber;
use SenderSuite\Vendor\Phone\PhoneNumberUtil;

class Possible extends AbstractLeniency
{
    protected static $level = 1;

    /**
     * Phone numbers accepted are PhoneNumberUtil::isPossibleNumber(), but not necessarily
     * PhoneNumberUtil::isValidNumber().
     *
     * @param string $candidate
     * @return bool
     */
    public static function verify(PhoneNumber $number, $candidate, PhoneNumberUtil $util)
    {
        return $util->isPossibleNumber($number);
    }
}
