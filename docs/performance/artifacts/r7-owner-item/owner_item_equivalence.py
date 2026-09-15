from pathlib import Path
from datetime import datetime,timedelta,timezone
import importlib.util, inspect, textwrap, json, hashlib, tempfile, os
import pytest
root=Path(os.environ['REPRO_ROOT'])
def helper(name,filename):
    spec=importlib.util.spec_from_file_location(name,root/'tests'/filename)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
h=helper('owner_local_helpers','test_catalyst_local_intelligence.py')
p=helper('owner_personal_helpers','test_personal_catalyst_service.py')
m=h.local_module
before=Path(__file__).with_name('owner_item_before.py').read_text()
assert before.count('if current_request_is_owner():')==1
namespace=dict(m.__dict__)
exec(compile(before,'<before-owner-item>','exec'),namespace)
original=namespace['_item']; after=m.LocalCatalystIntelligence._item
assert 'if result is None and current_request_is_owner():' in inspect.getsource(after)
base=datetime(2030,7,16,20,0,tzinfo=timezone.utc);clock=[base]
patch=pytest.MonkeyPatch();patch.setattr(m,'_utc_now',lambda:clock[0]);patch.setattr(h.ai_jobs_repository_module,'_utcnow',lambda:clock[0])
report=[]
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
with tempfile.TemporaryDirectory(prefix='owner-item-equivalence-',dir='/private/tmp') as td:
  with h.request_owner_access_context(True):
    etl,ai,engine=h._stack(Path(td))
    h._apply_news(etl,[h._news_change(1,231,available_at=base-timedelta(minutes=10))],as_of=base)
    engine.reconcile()
    service=p._service('read',engine=engine,repository=ai,cache_path=Path(td)/'catalyst-cache.db')
    def snapshot(label,as_of):
      results=[];counts=[]
      linked=engine._linked_news_job_at
      for method in (original,after):
        m.LocalCatalystIntelligence._item=method;m._reset_revision_cache()
        calls=[0]
        def counted(*a,**kw):calls[0]+=1;return linked(*a,**kw)
        engine._linked_news_job_at=counted
        feed=engine.feed(as_of=as_of,window_hours=24,limit=12)
        counts.append(calls[0]);engine._linked_news_job_at=linked
        outputs={'local_feed':feed,'local_news':engine.news(231,as_of=as_of),'local_batch':engine.batch(['NVDA'],as_of=as_of,include_neutral=True)}
        for owner in (True,False):
          with h.request_owner_access_context(owner):
            outputs[str(owner)]=[service.feed(as_of=as_of,limit=12),service.feed(as_of=as_of,limit=12,page_mode='visible'),service.news(231,as_of=as_of)]
        results.append(outputs)
      assert results[0]==results[1],label
      report.append({'case':label,'identical':True,'hash':digest(results[0]),'local_feed_job_projection_calls_before_after':counts,'analysis_status':results[0]['local_feed']['items'][0]['analysis_status'],'has_analysis':results[0]['local_feed']['items'][0]['analysis'] is not None})
      m.LocalCatalystIntelligence._item=original
    snapshot('not_requested',base)
    job=engine.request_analysis(231,force=False)
    pending_at=base+timedelta(seconds=30);clock[0]=pending_at
    snapshot('pending',pending_at)
    clock[0]=base+timedelta(minutes=1)
    h._finish_job(ai,job['job_id'],h._news_result(news_id=231,change_sequence=1,content_hash='hash-231-1'));engine.reconcile()
    snapshot('completed',clock[0])
    snapshot('historical_before_job',base-timedelta(minutes=1))
    snapshot('historical_pending_before_result',pending_at)
    clock[0]=base+timedelta(minutes=2)
    forced=engine.request_analysis(231,force=True)
    snapshot('published_old_result_new_pending_job',clock[0])
    h._fail_job(ai,forced['job_id'],'forced_news_failed');engine.reconcile()
    snapshot('published_old_result_new_failed_job',clock[0])
m.LocalCatalystIntelligence._item=original;patch.undo()
out=Path(__file__).with_name('owner-item-equivalence.json')
out.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
