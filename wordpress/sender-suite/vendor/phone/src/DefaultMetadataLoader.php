<?php

namespace SenderSuite\Vendor\Phone;

/**
 * @internal
 */
class DefaultMetadataLoader implements MetadataLoaderInterface
{
    public function loadMetadata($metadataFileName)
    {
        return include $metadataFileName;
    }
}
