<?php
foreach (array('compat','phone','csv-reader','csv-preview') as $n) { require __DIR__.'/../sender-suite/includes/class-'.$n.'.php'; }
$f=json_decode(file_get_contents(__DIR__.'/csv-source-fixtures.json'));$count=0;$diff=array();
function eq_csv($name,$actual,$expected) { global $count,$diff;$a=json_encode($actual,JSON_UNESCAPED_UNICODE|JSON_PRESERVE_ZERO_FRACTION|JSON_THROW_ON_ERROR);$b=json_encode($expected,JSON_UNESCAPED_UNICODE|JSON_PRESERVE_ZERO_FRACTION|JSON_THROW_ON_ERROR);if ($a===$b) { $count++; }elseif (count($diff)<8) { $diff[]=array('name'=>$name,'actual'=>$actual,'expected'=>$expected); } }
foreach ($f->reader as $i=>$c) { try { $a=array('rows'=>iterator_to_array(\SenderSuite\CsvReader::records(base64_decode($c->bytes)),false)); }catch (\SenderSuite\CsvFailure $e) { $a=array('error'=>'csv_parse'); }eq_csv('reader/'.$i,$a,$c->expected); }
foreach ($f->previews as $i=>$c) { eq_csv('preview/'.$i,\SenderSuite\CsvPreview::preview(base64_decode($c->bytes),$c->max_rows),$c->expected); }
foreach ($f->phone as $i=>$c) { eq_csv('phone/'.$i,\SenderSuite\Phone::clean($c->input),$c->expected); }
foreach ($f->keys as $i=>$c) { eq_csv('key/'.$i,\SenderSuite\CsvPreview::customKey($c->input),$c->expected); }
foreach ($f->decodes as $i=>$c) { eq_csv('decode/'.$i,\SenderSuite\CsvReader::utf8(base64_decode($c->bytes)),$c->expected); }
echo json_encode(array('ok'=>!$diff,'assertions'=>$count,'diffs'=>$diff,'php'=>PHP_VERSION,'source_phone_version'=>'8.13.52','native_wordpress_verified'=>false),JSON_THROW_ON_ERROR);
