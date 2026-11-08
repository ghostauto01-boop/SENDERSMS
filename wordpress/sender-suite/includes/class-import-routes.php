<?php
namespace SenderSuite;

/** Own-application multipart uploads. Temporary input is never saved to WP media. */
final class ImportRoutes {
    private static $uploadReader=null; // Reflection-only synthetic upload seam used by offline tests.
    public static function definitions() { return array('/contacts/import/preview'=>array('POST'),'/contacts/import/csv'=>array('POST')); }
    private static function upload($request) {
        $files=$request->get_file_params(); $file=$files['file']??null;
        if (!is_array($file)) { throw new ValidationFailure('Field required','file'); }
        if (self::$uploadReader!==null) { $bytes=call_user_func(self::$uploadReader,$file); }
        else {
            $error=$file['error']??UPLOAD_ERR_NO_FILE;
            if (in_array($error,array(UPLOAD_ERR_INI_SIZE,UPLOAD_ERR_FORM_SIZE),true)) { throw new AuthFailure(413,'CSV file is larger than 20 MB'); }
            if ($error!==UPLOAD_ERR_OK || !is_string($file['tmp_name']??null) || !is_uploaded_file($file['tmp_name'])) { throw new ValidationFailure('A valid uploaded CSV file is required','file'); }
            $bytes=file_get_contents($file['tmp_name'],false,null,0,20*1024*1024+1);
        }
        if (!is_string($bytes)) { throw new AuthFailure(400,'Unable to read uploaded CSV'); }
        if (strlen($bytes)>20*1024*1024) { throw new AuthFailure(413,'CSV file is larger than 20 MB'); } return $bytes;
    }
    public static function handle($request,array $context) {
        $path=trim(substr($request->get_route(),strlen('/sender-suite/v1/')),'/'); if ($request->get_method()!=='POST') { throw new AuthFailure(404,'Not Found'); }
        $bytes=self::upload($request);
        if ($path==='contacts/import/preview') { return CsvPreview::preview($bytes); }
        if ($path!=='contacts/import/csv') { throw new AuthFailure(404,'Not Found'); }
        $form=$request->get_body_params(); $options=array();
        foreach (array('new_list_name','column_mapping','tags','auto_tag_rules') as $key) { if (isset($form[$key])) { $options[$key]=DataRoutes::text($form,$key); } }
        if (isset($form['list_id']) && $form['list_id']!=='') { $options['list_id']=DataRoutes::integer($form['list_id'],'list_id'); }
        if (isset($form['skip_duplicates']) && $form['skip_duplicates']!=='') { $options['skip_duplicates']=DataRoutes::boolean($form['skip_duplicates'],'skip_duplicates'); }
        return (new CsvImport($context['db']))->import($bytes,$options);
    }
}
