<?php
/** Parser coverage only. Never represented as functional dependency coverage. */
ini_set('memory_limit','512M'); // Test-only parser budget for generated vendor metadata.
$count=0;$root=__DIR__.'/../sender-suite/vendor';foreach (new RecursiveIteratorIterator(new RecursiveDirectoryIterator($root,FilesystemIterator::SKIP_DOTS)) as $file) { if ($file->getExtension()!=='php' || (PHP_VERSION_ID<80000 && $file->getFilename()==='bootstrap80.php')) { continue; }token_get_all(file_get_contents($file->getPathname()),TOKEN_PARSE);$count++; }
echo json_encode(array('ok'=>true,'assertions'=>$count,'php'=>PHP_VERSION,'scope'=>'Vendor syntax TOKEN_PARSE only, not runtime/library functional coverage'),JSON_THROW_ON_ERROR);
