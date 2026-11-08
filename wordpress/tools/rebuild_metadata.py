#!/usr/bin/env python3
"""Reproducible AST-only inventory. Never imports app/config or reads environment files."""
import ast,hashlib,json,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'wordpress/sender-suite/schema';OUT.mkdir(parents=True,exist_ok=True)
def name(node):return ast.unparse(node).split('.')[-1]
def literal(node,default=None):
 try:return ast.literal_eval(node)
 except (ValueError,TypeError):return default
def fields(call):return {k.arg:k.value for k in call.keywords}
def loc(path,node):return str(path.relative_to(ROOT))+':'+str(node.lineno)
tables={}
for path in sorted((ROOT/'backend/app/models').glob('*.py')):
 for cls in ast.parse(path.read_text()).body:
  if not isinstance(cls,ast.ClassDef):continue
  table=next((literal(n.value) for n in cls.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='__tablename__' for t in n.targets)),None)
  if not table:continue
  columns={};indexes=[];foreign=[];relationships=[]
  for node in cls.body:
   if isinstance(node,ast.AnnAssign) and isinstance(node.target,ast.Name) and isinstance(node.value,ast.Call):
    call=node.value;column=node.target.id;kw=fields(call)
    if name(call.func)=='relationship':relationships.append({'name':column,'source':loc(path,node),'expression':ast.unparse(call)});continue
    if name(call.func)!='mapped_column':continue
    typ=None;length=None
    for arg in call.args:
     if isinstance(arg,ast.Call) and name(arg.func)=='ForeignKey':
      target=literal(arg.args[0]);fk=fields(arg);foreign.append({'column':column,'target_table':target.split('.')[0],'target_column':target.split('.')[1],'on_delete':literal(fk.get('ondelete')) if 'ondelete' in fk else None,'on_update':literal(fk.get('onupdate')) if 'onupdate' in fk else None,'source':loc(path,node)})
     elif isinstance(arg,ast.Call):typ=name(arg.func);length=literal(arg.args[0]) if arg.args and typ=='String' else None
     elif isinstance(arg,ast.Name):typ=arg.id
    annotation=ast.unparse(node.annotation)
    if typ is None:
     inner=annotation.replace('Mapped[','').rstrip(']');typ=next((v for k,v in [('int','Integer'),('str','String'),('bool','Boolean'),('float','Float'),('datetime','DateTime')] if inner==k or inner.startswith(k+' |')),None)
    if typ not in ('Integer','String','Text','Boolean','DateTime','Date','JSON','Float'):raise RuntimeError('Unmapped type '+loc(path,node)+' '+str(typ))
    primary=literal(kw.get('primary_key'),False);nullable=literal(kw.get('nullable'),('None' in annotation or 'Optional[' in annotation) and not primary)
    defaults={k:ast.unparse(kw[k]) for k in ('default','onupdate','server_default') if k in kw}
    factory=None
    if 'default' in kw:
     value=kw['default'];expr=ast.unparse(value)
     if expr in ('_now','lambda: datetime.now(timezone.utc)'):factory={'clock':True}
     elif expr=='lambda: str(uuid4())':factory={'uuid':True}
     elif expr=='_token' and path.name=='invoice.py':factory={'uuid_hex':True}
     elif expr=='DEFAULT_FOLLOWUP_WAIT_HOURS':
      constant=next(n.value for n in ast.parse(path.read_text()).body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id==expr for t in n.targets));factory={'value':ast.literal_eval(constant)}
     else:
      try:factory={'value':ast.literal_eval(value)}
      except (ValueError,TypeError):factory={'unsupported':expr}
    columns[column]={'source':loc(path,node),'type':typ,'length':length,'primary':primary,'nullable':nullable,'auto_increment':literal(kw.get('autoincrement'),primary and typ=='Integer'),'defaults':defaults,'factory':factory}
    if literal(kw.get('index'),False) or literal(kw.get('unique'),False):indexes.append({'name':('ix_' if literal(kw.get('index'),False) else 'uq_')+table+'_'+column,'columns':[column],'unique':literal(kw.get('unique'),False),'source':loc(path,node)})
   if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='__table_args__' for t in node.targets):
    args=node.value.elts if isinstance(node.value,(ast.Tuple,ast.List)) else [node.value]
    for call in args:
     if not isinstance(call,ast.Call):continue
     kw=fields(call);kind=name(call.func)
     if kind not in ('Index','UniqueConstraint'):raise RuntimeError('Unknown table constraint '+loc(path,node))
     raw=[literal(a) for a in call.args];cols=raw[1:] if kind=='Index' else raw
     if not cols or any(not isinstance(c,str) for c in cols):raise RuntimeError('Dynamic index '+loc(path,node))
     indexes.append({'name':raw[0] if kind=='Index' else literal(kw.get('name'),'uq_'+table+'_'+'_'.join(cols)),'columns':cols,'unique':kind=='UniqueConstraint' or literal(kw.get('unique'),False),'source':loc(path,node)})
  for index in indexes:
   assert all(c in columns for c in index['columns']),(table,index)
  tables[table]={'model':cls.name,'source':loc(path,cls),'columns':columns,'indexes':indexes,'foreign_keys':foreign,'relationships':relationships}
# Route prefixes from actual include_router calls, no application imports.
main=ast.parse((ROOT/'backend/app/main.py').read_text());prefixes={};aliases={}
for node in main.body:
 if isinstance(node,ast.ImportFrom) and node.module=='app.api.v1':
  for alias in node.names:aliases[alias.asname or alias.name]=alias.name
for node in ast.walk(main):
 if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='include_router' and node.args:
  prefix=literal(fields(node).get('prefix'),'');ref=ast.unparse(node.args[0]);parts=ref.split('.');prefixes.setdefault((aliases.get(parts[0],parts[0]),parts[-1]),set()).add(prefix)
endpoints=[]
for path in sorted((ROOT/'backend/app').rglob('*.py')):
 tree=ast.parse(path.read_text());module=path.stem
 router_prefixes={}
 for node in tree.body:
  if isinstance(node,ast.Assign) and isinstance(node.value,ast.Call) and name(node.value.func)=='APIRouter':
   for target in node.targets:
    if isinstance(target,ast.Name):router_prefixes[target.id]=literal(fields(node.value).get('prefix'),'')
 for fn in ast.walk(tree):
  if not isinstance(fn,(ast.FunctionDef,ast.AsyncFunctionDef)):continue
  for dec in fn.decorator_list:
   if not isinstance(dec,ast.Call) or not isinstance(dec.func,ast.Attribute) or dec.func.attr not in ('get','post','put','patch','delete','head','options') or not dec.args:continue
   route=literal(dec.args[0]);receiver=ast.unparse(dec.func.value)
   if not isinstance(route,str) or receiver!='app' and receiver not in router_prefixes:continue
   bases=[''] if receiver=='app' else [p+router_prefixes.get(receiver,'') for p in sorted(prefixes.get((module,receiver),{''}))]
   for base in bases:endpoints.append({'method':dec.func.attr.upper(),'path':base+route,'source':loc(path,fn),'handler':fn.name})
settings=[];config=ROOT/'backend/app/config.py'
for cls in ast.parse(config.read_text()).body:
 if isinstance(cls,ast.ClassDef) and cls.name=='Settings':
  for n in cls.body:
   if isinstance(n,ast.AnnAssign) and isinstance(n.target,ast.Name):
    field=n.target.id;secret=any(word in field for word in ('PASSWORD','SECRET','TOKEN','API_KEY','ENCRYPTION_KEY','PRIVATE_KEY','CLIENT_SECRET','VAPID'))
    value=literal(n.value) if n.value else None
    settings.append({'name':field,'type':ast.unparse(n.annotation),'source':loc(config,n),'secret':secret,'default':None if secret or field in ('DATABASE_URL','REDIS_URL') else value,'requires_operator_value':secret,'expression_kind':type(n.value).__name__})
for fk_table,spec in tables.items():
 for fk in spec['foreign_keys']:assert fk['target_table'] in tables and fk['target_column'] in tables[fk['target_table']]['columns']
frontend={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT/'frontend').rglob('*')) if p.is_file() and 'node_modules' not in p.parts and 'dist' not in p.parts and not p.name.startswith('.env')}
source_commit='587969338a9b62f00ab3488b6a4b71ca491feacb' # Frozen source snapshot; rebuild-only commits must not relabel it.
summary={'source_commit':source_commit,'tables':len(tables),'columns':sum(len(t['columns']) for t in tables.values()),'indexes':sum(len(t['indexes']) for t in tables.values()),'foreign_keys':sum(len(t['foreign_keys']) for t in tables.values()),'http_declarations':len(endpoints),'distinct_http_contracts':len({(e['method'],e['path']) for e in endpoints}),'settings':len(settings),'frontend_files':len(frontend),'certified_contracts':0}
for file,data in [('models.json',tables),('endpoints.json',endpoints),('settings.json',settings),('frontend-hashes.json',frontend),('inventory-summary.json',summary)]:
 (OUT/file).write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n')
print(json.dumps(summary,indent=2))
