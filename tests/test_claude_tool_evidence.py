from types import SimpleNamespace as N
from app.services.ai_jobs.claude_provider import response_tool_evidence


def message(*blocks):
    return N(content=blocks)


def test_only_matched_successful_network_results_are_evidence():
    call=N(type='server_tool_use',id='srv1',name='web_search')
    hits=[N(type='web_search_result',url=f'https://news.testsite.org/item/{i}',title=f'Item {i}',encrypted_content='opaque') for i in range(20)]
    result=N(type='web_search_tool_result',tool_use_id='srv1',content=hits)
    text=N(type='text',text='claim',citations=[N(url='https://fabricated.org/')])
    evidence=response_tool_evidence(message(call,result,text))
    assert len(evidence)==20
    assert all(row['tool_use_id']=='srv1' and row['tool_name']=='web_search' and len(row['content_sha256'])==64 for row in evidence)
    assert response_tool_evidence(message(result))==[]
    assert response_tool_evidence(message(text))==[]
    assert response_tool_evidence(message(call,N(type='web_search_tool_result',tool_use_id='srv1',content=N(type='web_search_tool_result_error'))))==[]


def test_private_url_and_code_are_not_network_evidence():
    assert response_tool_evidence(message(N(type='server_tool_use',id='code',name='code_execution'),N(type='code_execution_tool_result',tool_use_id='code',content=N(type='code_execution_result'))))==[]
    assert response_tool_evidence(message(N(type='server_tool_use',id='web',name='web_search'),N(type='web_search_tool_result',tool_use_id='web',content=[N(type='web_search_result',url='http://127.0.0.1/',title='private')])) )==[]

# Multi-request continuations remain one paid job and one absolute deadline.
import asyncio
import copy
import json
import pytest
from anthropic.types import Message
from app.services.ai_jobs import claude_provider as provider, runtime, worker
from app.services.ai_jobs.repository import AIJobRepository
from test_claude_job_worker import settings as base_settings
from test_claude_market_focus_output import valid_result


def _round(ident, stop='pause_turn', content=None, output=10):
    return Message(id=ident, model=runtime.SONNET_MODEL, role='assistant', type='message', stop_reason=stop,
        content=content or [{'type':'text','text':'{"answer":"done"}'}],
        usage={'input_tokens':100,'output_tokens':output,'cache_creation_input_tokens':0,'cache_read_input_tokens':0,
               'cache_creation':{'ephemeral_5m_input_tokens':0,'ephemeral_1h_input_tokens':0},
               'server_tool_use':{'web_search_requests':0,'web_fetch_requests':0}})


def _prepared(max_tokens=100):
    return provider.PreparedMessage(params={'model':runtime.SONNET_MODEL,'max_tokens':max_tokens,'messages':[{'role':'user','content':'input'}]}, api_key='test', timeout_seconds=1, diagnostic_task='ai_jobs:market_focus')


def test_pause_continues_original_content_and_sums_complete_receipts(monkeypatch):
    paused=_round('msg_one', content=[{'type':'thinking','thinking':'private reasoning','signature':'preserve-signature'},
        {'type':'server_tool_use','id':'tool1','name':'web_search','input':{'query':'release'}},
        {'type':'web_search_tool_result','tool_use_id':'tool1','content':[{'type':'web_search_result','url':'https://issuer.org/release','title':'Release','encrypted_content':'opaque'}]},
        {'type':'text','text':'paused narrative must not enter final JSON'}])
    final=_round('msg_two','end_turn')
    calls=[]; progress=[]; first_ids=[]
    async def stream(prepared, *, on_message_start, tool_counts):
        calls.append(copy.deepcopy(prepared.params))
        message=paused if len(calls)==1 else final
        await on_message_start(message.id)
        return message
    async def started(ident): first_ids.append(ident)
    async def record(snapshot, **options): progress.append((copy.deepcopy(snapshot),options))
    monkeypatch.setattr(provider,'stream_message',stream)
    result=asyncio.run(provider.stream_market_focus(_prepared(),on_message_start=started,on_progress=record)).receipt
    assert first_ids==['msg_one']
    assert calls[1]['messages'][-1]['content']==paused.content
    assert calls[1]['max_tokens']==90
    assert result['id']=='msg_one' and result['request_ids']==['msg_one','msg_two']
    assert result['usage']['input_tokens']==200 and result['usage']['output_tokens']==20
    assert result['output_text']=='{"answer":"done"}' and result['terminal_error'] is None
    assert result['tool_evidence'][0]['tool_use_id']=='tool1'
    assert any(options['will_continue'] for _,options in progress)
    assert 'private reasoning' not in json.dumps(progress)


@pytest.mark.parametrize('failure',[RuntimeError('connection lost'),asyncio.CancelledError()])
def test_failed_continuation_keeps_confirmed_round_and_next_identity(monkeypatch,failure):
    calls=[]; progress=[]
    async def stream(prepared, *, on_message_start, tool_counts):
        calls.append(prepared)
        await on_message_start(f'msg_{len(calls)}')
        if len(calls)==2: raise failure
        return _round('msg_1')
    async def noop(ident): pass
    async def record(snapshot, **kw): progress.append(copy.deepcopy(snapshot))
    monkeypatch.setattr(provider,'stream_message',stream)
    with pytest.raises(type(failure)):
        asyncio.run(provider.stream_market_focus(_prepared(),on_message_start=noop,on_progress=record))
    assert progress[-1]['request_ids']==['msg_1','msg_2']
    assert len(progress[-1]['rounds'])==1 and progress[-1]['confirmed_usage']['total_tokens']==110


def test_continuation_round_cap_and_total_output_are_hard_limits(monkeypatch):
    calls=[]
    async def stream(prepared, *, on_message_start, tool_counts):
        calls.append(prepared)
        await on_message_start(f'msg_{len(calls)}')
        return _round(f'msg_{len(calls)}')
    async def noop(*args,**kwargs): pass
    monkeypatch.setattr(provider,'stream_message',stream)
    result=asyncio.run(provider.stream_market_focus(_prepared(),on_message_start=noop,on_progress=noop)).receipt
    assert len(calls)==4 and result['terminal_error']=='provider_continuation_limit'
    calls.clear()
    result=asyncio.run(provider.stream_market_focus(_prepared(10),on_message_start=noop,on_progress=noop)).receipt
    assert len(calls)==1 and result['terminal_error']=='provider_incomplete_max_output_tokens'


def _focus_job(repo):
    payload={'cycle_id':'mfc_continuation_test','as_of':'2026-10-09T00:00:00Z','input_hash':'a'*64,
             'allowed_event_group_ids':[],'allowed_tickers':[],'no_new_material_catalyst':True,
             'verification_version':'web-evidence-v1','events':[]}
    version,digest=runtime.schema_identity('market_focus',model=runtime.SONNET_MODEL)
    job,_=repo.create_job(job_type='market_focus',payload=payload,model=runtime.SONNET_MODEL,reasoning='xhigh',
        execution_mode='background',prompt_version=runtime.PROMPT_VERSIONS['market_focus'],schema_version=version,schema_sha256=digest,max_queued=10)
    config=base_settings(repo.path).model_copy(update={'openai_market_focus_model':runtime.SONNET_MODEL,'openai_market_focus_reasoning':'xhigh'})
    return job,payload,config


def test_worker_unknown_continuation_preserves_reservation_and_never_replays(tmp_path,monkeypatch):
    repo=AIJobRepository(tmp_path/'jobs.db'); job,payload,config=_focus_job(repo); calls=[]
    async def stream(prepared, *, on_message_start, tool_counts):
        calls.append(prepared)
        await on_message_start(f'msg_{len(calls)}')
        if len(calls)==2: raise RuntimeError('lost response')
        return _round('msg_1')
    monkeypatch.setattr(provider,'stream_message',stream)
    asyncio.run(worker.run_once(repo,config,'owner'))
    row=repo.get_job(job['job_id']); progress=repo.get_provider_progress(job['job_id'])
    assert row['error_code']=='submission_outcome_unknown'
    assert row['anthropic_message_id']=='msg_1' and progress['request_ids']==['msg_1','msg_2']
    assert row['budget_charge_microusd'] > runtime.budget_reservation_microusd('market_focus',model=runtime.SONNET_MODEL)
    assert row['usage_total_tokens'] is None and progress['confirmed_usage']['total_tokens']==110
    asyncio.run(worker.run_once(repo,config,'restart'))
    assert len(calls)==2


def test_worker_successful_continuation_receipt_recovers_without_network(tmp_path,monkeypatch):
    repo=AIJobRepository(tmp_path/'jobs.db'); job,payload,config=_focus_job(repo); calls=[]
    result={**valid_result(payload),'event_verifications':[]}
    async def stream(prepared, *, on_message_start, tool_counts):
        calls.append(prepared); ident=f'msg_{len(calls)}'; await on_message_start(ident)
        return _round(ident) if len(calls)==1 else _round(ident,'end_turn',[{'type':'text','text':json.dumps(result,ensure_ascii=False)}])
    monkeypatch.setattr(provider,'stream_message',stream)
    asyncio.run(worker.run_once(repo,config,'owner'))
    row=repo.get_job(job['job_id']); receipt=repo.get_provider_result(job['job_id'])
    assert row['status']=='completed',row
    assert receipt['id']=='msg_1' and receipt['request_ids']==['msg_1','msg_2']
    assert receipt['usage']['total_tokens']==220
    assert row['budget_charge_microusd']==600
    asyncio.run(worker.run_once(repo,config,'restart'))
    assert len(calls)==2


@pytest.mark.parametrize('interrupt',['cancel','timeout'])
def test_worker_interrupts_second_round_with_full_unknown_hold(tmp_path,monkeypatch,interrupt):
    repo=AIJobRepository(tmp_path/'jobs.db'); job,payload,config=_focus_job(repo); calls=[]; closed=[]
    config=config.model_copy(update={'openai_background_poll_timeout_seconds':0.04 if interrupt=='timeout' else 2})
    monkeypatch.setattr(worker,'_CLAUDE_CANCEL_CHECK_SECONDS',0.01)
    async def stream(prepared, *, on_message_start, tool_counts):
        calls.append(prepared); ident=f'msg_{len(calls)}'; await on_message_start(ident)
        if len(calls)==1: return _round(ident)
        if interrupt=='cancel': repo.request_cancel(job['job_id'])
        try: await asyncio.sleep(5)
        finally: closed.append(True)
    monkeypatch.setattr(provider,'stream_message',stream)
    asyncio.run(worker.run_once(repo,config,'owner'))
    row=repo.get_job(job['job_id'])
    assert row['error_code']=='submission_outcome_unknown'
    assert row['usage_total_tokens'] is None
    assert row['budget_charge_microusd'] > runtime.budget_reservation_microusd('market_focus',model=runtime.SONNET_MODEL)
    assert repo.get_provider_progress(job['job_id'])['confirmed_usage']['total_tokens']==110
    assert closed==[True]
    asyncio.run(worker.run_once(repo,config,'restart'))
    assert len(calls)==2


def test_paid_continuation_receipt_publication_retry_never_resends(tmp_path,monkeypatch):
    import sqlite3
    repo=AIJobRepository(tmp_path/'jobs.db'); job,payload,config=_focus_job(repo); calls=[]
    result={**valid_result(payload),'event_verifications':[]}
    async def stream(prepared, *, on_message_start, tool_counts):
        calls.append(prepared); ident=f'msg_{len(calls)}'; await on_message_start(ident)
        return _round(ident) if len(calls)==1 else _round(ident,'end_turn',[{'type':'text','text':json.dumps(result,ensure_ascii=False)}])
    complete=repo.complete
    def locked(*args,**kwargs): raise sqlite3.OperationalError('database is locked')
    monkeypatch.setattr(provider,'stream_message',stream)
    monkeypatch.setattr(worker,'_STORAGE_WRITE_RETRY_DELAY_SECONDS',0)
    monkeypatch.setattr(repo,'complete',locked)
    asyncio.run(worker.run_once(repo,config,'owner'))
    assert repo.get_provider_result(job['job_id'])['request_ids']==['msg_1','msg_2']
    assert repo.get_job(job['job_id'])['error_code']=='local_storage_error'
    with sqlite3.connect(repo.path) as connection:
        connection.execute('UPDATE ai_jobs SET next_attempt_at=NULL WHERE job_id=?',(job['job_id'],))
    monkeypatch.setattr(repo,'complete',complete)
    asyncio.run(worker.run_once(repo,config,'restart'))
    assert repo.get_job(job['job_id'])['status']=='completed'
    assert len(calls)==2


def test_continuation_admission_failure_never_starts_next_request(monkeypatch):
    calls=[]; confirmed=[]
    async def stream(prepared, *, on_message_start, tool_counts):
        calls.append(prepared); await on_message_start('msg_1'); return _round('msg_1')
    async def noop(*args): pass
    async def record(snapshot, *, will_continue=False):
        confirmed.append(copy.deepcopy(snapshot))
        if will_continue: raise RuntimeError('continuation budget admission denied')
    monkeypatch.setattr(provider,'stream_message',stream)
    with pytest.raises(RuntimeError,match='admission denied'):
        asyncio.run(provider.stream_market_focus(_prepared(),on_message_start=noop,on_progress=record))
    assert len(calls)==1 and confirmed[-1]['confirmed_usage']['total_tokens']==110
