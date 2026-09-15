from pathlib import Path
from datetime import datetime,timezone
import os,json,time,hashlib,statistics,sqlite3,platform
from app.access import request_owner_access_context
from app.services.catalysts import local_intelligence as m
from app.services.catalysts.config import CatalystSettings
from app.services.catalysts.personal_service import PersonalCatalystService
from app.services.ai_jobs.repository import AIJobRepository
store=Path(os.environ.get('OWNER_PERF_STORE','/private/tmp/pr165-cache-review/owner-perf-n1000'));os.environ['DATA_DIR']=str(store)
namespace=dict(m.__dict__);exec(compile(Path(__file__).with_name('owner_item_before.py').read_text(),'<before-owner-item>','exec'),namespace)
before=namespace['_item'];after=m.LocalCatalystIntelligence._item
service=PersonalCatalystService(CatalystSettings(cache_db_path=store/'catalyst-cache.db'))
as_of=datetime.now(timezone.utc)
def digest(payload):return hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
def db_hashes():return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (store/'ai-jobs.db',store/'catalyst-cache.db')}
original_hashes=db_hashes()
with sqlite3.connect(f'file:{store}/catalyst-cache.db?mode=ro',uri=True) as conn:
 counts={table:conn.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in ['catalyst_local_news_revisions','catalyst_local_analysis_links','catalyst_local_analysis_result_audit']}
report={'scope':'Isolated local in-process official seed; not production latency','platform':platform.platform(),'python':platform.python_version(),'as_of':as_of.isoformat(),'store':counts,'samples_per_variant':8,'cases':{}}
for case,owner,mode in [('owner_visible',True,'visible'),('owner_legacy',True,None),('guest_visible',False,'visible')]:
 rows=[];hashes=set();last={}
 with request_owner_access_context(owner):
  for index in range(8):
   for variant,method in ([('before',before),('after',after)] if index%2==0 else [('after',after),('before',before)]):
    m.LocalCatalystIntelligence._item=method;m._reset_revision_cache()
    args={'as_of':as_of,'window_hours':72,'limit':12,'include_unanalyzed':True,'include_neutral':True}
    if mode:args['page_mode']=mode
    start=time.perf_counter();cold=service.feed(**args);cold_ms=(time.perf_counter()-start)*1000
    start=time.perf_counter();warm=service.feed(**args);warm_ms=(time.perf_counter()-start)*1000
    assert cold==warm,(case,variant,'cold_warm_mismatch')
    hashes.add(digest(warm));last[variant]=warm
    rows.append({'iteration':index+1,'variant':variant,'cold_ms':cold_ms,'warm_ms':warm_ms,'hash':digest(warm)})
  assert len(hashes)==1,(case,'before_after_output_mismatch')
  instrumented={}
  for variant,method in [('before',before),('after',after)]:
   m.LocalCatalystIntelligence._item=method;m._reset_revision_cache()
   linked=service.intelligence._linked_news_job_at;public=AIJobRepository.public;c={'linked_job_projection':0,'public_job_validation':0}
   def counted_linked(*a,**kw):c['linked_job_projection']+=1;return linked(*a,**kw)
   def counted_public(*a,**kw):c['public_job_validation']+=1;return public(*a,**kw)
   service.intelligence._linked_news_job_at=counted_linked;AIJobRepository.public=staticmethod(counted_public)
   try:payload=service.feed(**args);assert payload==last[variant]
   finally:service.intelligence._linked_news_job_at=linked;AIJobRepository.public=staticmethod(public)
   instrumented[variant]=c
  by_variant={v:{'cold_median_ms':statistics.median(r['cold_ms'] for r in rows if r['variant']==v),'warm_median_ms':statistics.median(r['warm_ms'] for r in rows if r['variant']==v)} for v in ['before','after']}
  report['cases'][case]={'timings':by_variant,'counts_separate_untimed_run':instrumented,'hash':next(iter(hashes)),'item_count':len(last['after']['items']),'window_count':last['after']['summary']['count'],'samples':rows}
m.LocalCatalystIntelligence._item=after
report['databases_unchanged']=db_hashes()==original_hashes
assert report['databases_unchanged']
out=Path(__file__).with_name('owner-item-perf-n1000.json');out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({**report,'cases':{k:{x:y for x,y in v.items() if x!='samples'} for k,v in report['cases'].items()}},ensure_ascii=False,indent=2))
