<?php

namespace SenderSuite\Vendor\Phone;

use SenderSuite\Vendor\Phone\Leniency\Possible;
use SenderSuite\Vendor\Phone\Leniency\StrictGrouping;
use SenderSuite\Vendor\Phone\Leniency\Valid;
use SenderSuite\Vendor\Phone\Leniency\ExactGrouping;

class Leniency
{
    public static function POSSIBLE()
    {
        return new Possible();
    }

    public static function VALID()
    {
        return new Valid();
    }

    public static function STRICT_GROUPING()
    {
        return new StrictGrouping();
    }

    public static function EXACT_GROUPING()
    {
        return new ExactGrouping();
    }
}
