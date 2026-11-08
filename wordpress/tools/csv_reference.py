"""Load only original local normalization and CSV preview AST; no application imports/config."""
import ast,csv,io,re,sys
from pathlib import Path
D=Path(__file__).resolve().parents[1];ROOT=D.parent;sys.path.insert(0,str(D/'.cache/python'))
def load():
 import phonenumbers,unicodedata2
 assert phonenumbers.__version__=='8.13.52' and unicodedata2.unidata_version=='15.0.0'
 from typing import Optional
 phone=dict(re=re,Optional=Optional,phonenumbers=phonenumbers,PhoneNumberFormat=phonenumbers.PhoneNumberFormat,NumberParseException=phonenumbers.NumberParseException)
 def execute(path,names,context):
  nodes=[]
  for n in ast.parse((ROOT/path).read_text()).body:
   name=getattr(n,'name',None)
   if isinstance(n,ast.Assign):name=next((t.id for t in n.targets if isinstance(t,ast.Name) and t.id in names),None)
   if name in names:nodes.append(n)
  exec(compile(ast.Module(body=nodes,type_ignores=[]),'original-source-'+path,'exec'),context)
 execute('backend/app/utils/phone.py',{'clean_phone_number','normalize_nigerian_number'},phone)
 identity=dict(phone);execute('backend/app/utils/contact_identity.py',{'_EMAIL_RE','normalize_email','clean_phone'},identity)
 ns=dict(identity,AsyncSession=object,csv=csv,io=io,unicodedata=unicodedata2)
 execute('backend/app/services/csv_service.py',{'HEADER_ALIASES','custom_field_key','detect_column_mapping','extract_emails','split_tags'},ns)
 cls=next(n for n in ast.parse((ROOT/'backend/app/services/csv_service.py').read_text()).body if isinstance(n,ast.ClassDef) and n.name=='CSVImportService');cls.body=[n for n in cls.body if getattr(n,'name',None) in ('__init__','preview_csv')];exec(compile(ast.Module(body=[cls],type_ignores=[]),'original-csv-preview','exec'),ns)
 return ns
