"""Shared accounting and durable receipt boundaries across both providers."""
from __future__ import annotations

import copy
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.services.ai_jobs import repository as storage, runtime
from app.services.ai_jobs.repository import AIJobRepository
from app.services.model_budget import JOB_MODELS, SharedModelBudget

NOW = datetime(2026, 10, 9, 5, tzinfo=timezone.utc)
HAIKU, SONNET, LUNA, TERRA = JOB_MODELS


def job(repo, model, *, name=None, lane='scheduled'):
    version, digest = runtime.schema_identity('earnings_impact')
    row, _ = repo.create_job(
        job_type='earnings_impact', payload={'ticker': name or model, 'name': '测试'},
        model=model, reasoning='xhigh' if runtime.uses_claude(model) else 'max',
        execution_mode='background', prompt_version='mixed-model-test',
        schema_version=version, schema_sha256=digest, max_queued=100,
        submission_source=lane,
    )
    return row['job_id']


def seed(repo, ident, *, amount=100, at=NOW, status='failed', error='schema_validation_failed'):
    with sqlite3.connect(repo.path) as con:
        con.execute('UPDATE ai_jobs SET status=?,error_code=?,submission_started_at=?,submitted_at=?,budget_charge_microusd=? WHERE job_id=?',
                    (status, error, storage._iso(at), storage._iso(at), amount, ident))


def lease(repo, ident):
    with sqlite3.connect(repo.path) as con:
        con.execute('UPDATE ai_jobs SET lease_owner=?,lease_expires_at=? WHERE job_id=?',
                    ('owner', storage._iso(NOW + timedelta(hours=1)), ident))


def start(repo, ident, **kwargs):
    lease(repo, ident)
    return repo.mark_submission_started(ident, 'owner', daily_limit=0,
        shared_daily_budget_usd=10, shared_budget_enforce_limit=False,
        max_concurrency=4, **kwargs)


def receipt(model=SONNET):
    return {'provider': 'anthropic', 'model': model, 'id': 'msg_test', 'output_text': '{}',
        'stop_reason': 'end_turn', 'terminal_error': None, 'evidence_sources': [],
        'usage': {'input_tokens': 1000, 'cached_input_tokens': 0, 'output_tokens': 100,
            'reasoning_tokens': 0, 'total_tokens': 1100, 'cache_creation_input_tokens': 0,
            'cache_creation_5m_input_tokens': 0, 'cache_creation_1h_input_tokens': 0,
            'web_search_requests': 0, 'web_fetch_requests': 0}}


def test_all_job_models_share_reference_with_opus_without_changing_schema(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, '_utcnow', lambda: NOW)
    repo = AIJobRepository(tmp_path / 'jobs.db')
    for index, model in enumerate(JOB_MODELS, 1):
        seed(repo, job(repo, model), amount=index * 1_000_000)
    # Before today's window does not leak into it.
    seed(repo, job(repo, LUNA, name='yesterday'), amount=99_000_000, at=NOW-timedelta(days=1))
    budget = SharedModelBudget(repo.path, 10, enforce_limit=False)
    budget.reserve_brief_request('opus', 1, 6_060_000, NOW)
    budget.settle_brief_request('opus', 1, cost_microusd=500_000, accounting_complete=True)
    snapshot = budget.snapshot(NOW)
    assert snapshot['haiku_charge_microusd'] == 1_000_000
    assert snapshot['job_charge_microusd'] == 10_000_000
    assert snapshot['job_model_charges_microusd'] == dict(zip(JOB_MODELS, [1_000_000,2_000_000,3_000_000,4_000_000]))
    assert snapshot['used_microusd'] == 10_500_000 and snapshot['budget_available']
    for model in JOB_MODELS:
        status = repo.budget_snapshot(daily_limit=0, daily_budget_usd=0,
            shared_daily_budget_usd=10, shared_budget_enforce_limit=False, model=model, now=NOW, max_concurrency=4)
        assert status['budget_used_usd'] == 10.5 and status['dollar_budget_available']
        assert status['job_model_charges_microusd'] == snapshot['job_model_charges_microusd']


def test_shared_cutoff_applies_to_every_model_and_does_not_erase_old_charges(tmp_path):
    repo=AIJobRepository(tmp_path/'jobs.db')
    cutoff=NOW.replace(microsecond=230098)
    for model in JOB_MODELS:
        seed(repo,job(repo,model,name=model+'old'),amount=300,at=NOW)
        seed(repo,job(repo,model,name=model+'new'),amount=700,at=cutoff)
    snapshot=SharedModelBudget(repo.path,10,accounting_start_at=cutoff,enforce_limit=False).snapshot(cutoff)
    assert snapshot['job_charge_microusd']==2800
    with sqlite3.connect(repo.path) as con:
        assert con.execute('SELECT SUM(budget_charge_microusd) FROM ai_jobs').fetchone()[0]==4000


def test_global_four_slots_and_single_openai_across_lanes(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, '_utcnow', lambda: NOW)
    repo=AIJobRepository(tmp_path/'jobs.db')
    luna=job(repo,LUNA)
    assert start(repo,luna)=='started'
    terra=job(repo,TERRA,lane='manual')
    assert start(repo,terra)=='concurrency_limit'
    for index,model in enumerate([HAIKU,SONNET,HAIKU]):
        assert start(repo,job(repo,model,name=f'c{index}'))=='started'
    assert start(repo,job(repo,SONNET,name='overflow'))=='concurrency_limit'
    for model in JOB_MODELS:
        status=repo.budget_snapshot(daily_limit=0,daily_budget_usd=0,model=model,now=NOW,max_concurrency=4)
        assert status['active_jobs_count']==4 and not status['concurrency_available']


def test_unknown_openai_holds_provider_slot_but_claude_can_continue(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, '_utcnow', lambda: NOW)
    repo=AIJobRepository(tmp_path/'jobs.db')
    old=job(repo,TERRA)
    seed(repo,old,status='failed',error='submission_outcome_unknown')
    new=job(repo,LUNA)
    assert start(repo,new)=='concurrency_limit'
    assert start(repo,job(repo,SONNET))=='started'
    assert repo.get_job(old)['attempt_count']==0
    assert repo.get_job(old)['budget_charge_microusd']==100


def test_sonnet_receipt_exact_model_binding_and_versioned_tool_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, '_utcnow', lambda: NOW)
    repo=AIJobRepository(tmp_path/'jobs.db'); ident=job(repo,SONNET)
    assert start(repo,ident)=='started'
    repo.link_anthropic_message(ident,'owner','msg_test')
    with pytest.raises(RuntimeError):
        repo.record_provider_result(ident,'owner',receipt(HAIKU))
    saved=receipt()
    saved.update(tool_evidence_version='v1',tool_evidence=[{
        'tool_use_id':'srvtoolu_1','tool_name':'web_search','status':'success',
        'url':'https://example.com/article','title':'公司公告','content_sha256':'a'*64}])
    repo.record_provider_result(ident,'owner',saved)
    before=repo.get_job(ident)
    assert before['budget_charge_microusd']==runtime.settled_usage_cost_microusd('earnings_impact',saved['usage'],fallback_microusd=1,model=SONNET)
    assert repo.get_provider_result(ident)==saved
    repo.record_provider_result(ident,'owner',saved)
    assert repo.get_job(ident)['provider_result_json']==before['provider_result_json']
    repo.fail(ident,'owner','schema_validation_failed',usage={})
    after=repo.get_job(ident)
    assert after['provider_result_json']==before['provider_result_json']
    assert after['budget_charge_microusd']==before['budget_charge_microusd']


def test_legacy_haiku_receipt_has_no_added_version_or_evidence(tmp_path,monkeypatch):
    monkeypatch.setattr(storage,'_utcnow',lambda:NOW)
    repo=AIJobRepository(tmp_path/'jobs.db');ident=job(repo,HAIKU)
    start(repo,ident);saved=receipt(HAIKU)
    repo.record_provider_result(ident,'owner',saved)
    before=repo.get_job(ident)['provider_result_json']
    assert 'tool_evidence' not in repo.get_provider_result(ident)
    AIJobRepository(repo.path).ensure_initialized()
    assert repo.get_job(ident)['provider_result_json']==before


@pytest.mark.parametrize('change', [
    {'tool_evidence_version':'v2','tool_evidence':[]},
    {'tool_evidence':[{}]},
    {'tool_evidence_version':'v1','tool_evidence':[{'tool_name':'web_search'}]},
])
def test_malformed_tool_evidence_is_rejected(change):
    value=receipt();value.update(change)
    with pytest.raises(ValueError,match='tool_evidence'):
        AIJobRepository._provider_receipt_json(value)


def test_claim_skips_openai_backlog_but_still_claims_claude_and_existing_response(tmp_path, monkeypatch):
    monkeypatch.setattr(storage,'_utcnow',lambda:NOW)
    repo=AIJobRepository(tmp_path/'jobs.db')
    old=job(repo,TERRA,name='active');seed(repo,old,status='in_progress',error=None)
    # Keep the submitted job leased to its current worker.
    lease(repo,old)
    job(repo,LUNA,name='blocked')
    sonnet=job(repo,SONNET,name='ready')
    claimed=repo.claim_due('next',60,max_concurrency=4)
    assert claimed['job_id']==sonnet
    with sqlite3.connect(repo.path) as con:
        con.execute('UPDATE ai_jobs SET lease_owner=NULL,lease_expires_at=NULL,openai_response_id=? WHERE job_id=?',('resp_existing',old))
    resumed=repo.claim_due('poller',60,max_concurrency=4)
    assert resumed['job_id']==old and resumed['openai_response_id']=='resp_existing'
    assert resumed['submission_started_at'] is not None


def test_sonnet_recovery_uses_saved_receipt_and_preserves_accounting(tmp_path, monkeypatch):
    from test_claude_job_worker import message
    monkeypatch.setattr(storage,'_utcnow',lambda:NOW)
    repo=AIJobRepository(tmp_path/'jobs.db')
    ident=job(repo,SONNET,name='AAPL');start(repo,ident)
    saved=receipt()
    saved['output_text']=message().content[0].text
    repo.record_provider_result(ident,'owner',saved)
    repo.fail(ident,'owner','schema_validation_failed',usage={})
    before=repo.get_job(ident)
    result=json.loads(saved['output_text'])
    recovered=repo.recover_schema_validation_failure(ident,saved['id'],result)
    assert recovered['status']=='completed'
    after=repo.get_job(ident)
    for key in ('model','provider_result_json','budget_charge_microusd','usage_input_tokens',
                'usage_output_tokens','usage_total_tokens','attempt_count','submission_started_at','completed_at'):
        assert after[key]==before[key]
    with pytest.raises(RuntimeError,match='recovery_rejected'):
        repo.recover_schema_validation_failure(ident,saved['id'],result)


@pytest.mark.parametrize('model',JOB_MODELS)
def test_tracking_admits_each_supported_model_above_reference_with_full_hold(tmp_path,monkeypatch,model):
    monkeypatch.setattr(storage,'_utcnow',lambda:NOW)
    repo=AIJobRepository(tmp_path/'jobs.db')
    seed(repo,job(repo,HAIKU,name='previous'),amount=11_000_000)
    ident=job(repo,model,name='next')
    assert start(repo,ident,daily_token_limit=102_400)=='started'
    row=repo.get_job(ident)
    assert row['budget_charge_microusd']==runtime.budget_reservation_microusd('earnings_impact',model=model)
    assert row['budget_charge_microusd']>0
    assert row['submission_started_at'] is not None


def progress_for(rounds, ids=None):
    from app.services.ai_jobs.claude_provider import sum_round_usage
    return {'provider':'anthropic','model':SONNET,'id':'msg_test',
            'request_ids':ids or [r['id'] for r in rounds], 'rounds':rounds,
            'confirmed_usage':sum_round_usage(rounds)}


def test_continuation_progress_holds_paid_plus_next_round_and_preserves_unknown(tmp_path,monkeypatch):
    monkeypatch.setattr(storage,'_utcnow',lambda:NOW)
    repo=AIJobRepository(tmp_path/'jobs.db');ident=job(repo,SONNET);start(repo,ident)
    repo.link_anthropic_message(ident,'owner','msg_test')
    initial=repo.get_job(ident)['budget_charge_microusd']
    begun=progress_for([],['msg_test'])
    repo.record_provider_progress(ident,'owner',begun)
    usage={**receipt()['usage'],'code_execution_requests':0}
    rounds=[{'id':'msg_test','stop_reason':'pause_turn','usage':usage}]
    progress=progress_for(rounds)
    repo.record_provider_progress(ident,'owner',progress)
    known=runtime.settled_usage_cost_microusd('earnings_impact',usage,fallback_microusd=initial,model=SONNET)
    repo.record_provider_progress(ident,'owner',progress,will_continue=True,
                                  shared_daily_budget_usd=10,shared_budget_enforce_limit=False)
    assert repo.get_job(ident)['budget_charge_microusd']==initial+known
    # The same before-send callback cannot add the same round twice.
    repo.record_provider_progress(ident,'owner',progress,will_continue=True)
    assert repo.get_job(ident)['budget_charge_microusd']==initial+known
    in_flight=progress_for(rounds,['msg_test','msg_second'])
    repo.record_provider_progress(ident,'owner',in_flight)
    repo.fail(ident,'owner','submission_outcome_unknown')
    after=repo.get_job(ident)
    assert after['budget_charge_microusd']==initial+known
    assert after['provider_result_json'] is None and after['anthropic_message_id']=='msg_test'
    assert AIJobRepository(repo.path).get_provider_progress(ident)==in_flight
    assert repo.claim_due('restart',60,max_concurrency=4) is None


def test_progress_requires_exact_identity_monotonic_rounds_and_complete_final_accounting(tmp_path,monkeypatch):
    from app.services.ai_jobs.claude_provider import sum_round_usage
    monkeypatch.setattr(storage,'_utcnow',lambda:NOW)
    repo=AIJobRepository(tmp_path/'jobs.db');ident=job(repo,SONNET);start(repo,ident)
    repo.link_anthropic_message(ident,'owner','msg_test')
    usage={**receipt()['usage'],'code_execution_requests':0}
    rounds=[{'id':'msg_test','stop_reason':'pause_turn','usage':usage}]
    prior=progress_for(rounds);repo.record_provider_progress(ident,'owner',prior)
    mutated=copy.deepcopy(prior);mutated['rounds'][0]['usage']['output_tokens']+=1;mutated['rounds'][0]['usage']['total_tokens']+=1
    mutated['confirmed_usage']=sum_round_usage(mutated['rounds'])
    with pytest.raises(RuntimeError,match='progress_conflict'):
        repo.record_provider_progress(ident,'owner',mutated)
    wrong=copy.deepcopy(prior);wrong['model']=HAIKU
    with pytest.raises(RuntimeError,match='progress_rejected'):
        repo.record_provider_progress(ident,'owner',wrong)
    rounds.append({'id':'msg_second','stop_reason':'end_turn','usage':usage})
    progress=progress_for(rounds);repo.record_provider_progress(ident,'owner',progress)
    final={**receipt(),'request_ids':progress['request_ids'],'rounds':rounds,'usage':sum_round_usage(rounds)}
    incomplete=copy.deepcopy(final);incomplete['usage']=usage
    with pytest.raises(ValueError,match='round_usage_mismatch'):
        repo.record_provider_result(ident,'owner',incomplete)
    repo.record_provider_result(ident,'owner',final)
    expected=runtime.settled_usage_cost_microusd('earnings_impact',final['usage'],fallback_microusd=1,model=SONNET)
    assert repo.get_job(ident)['budget_charge_microusd']==expected
    assert repo.get_provider_result(ident)==final
    with pytest.raises(RuntimeError,match='progress_rejected'):
        repo.record_provider_progress(ident,'owner',progress)


def test_enforced_continuation_cannot_increase_hold_past_remaining_budget(tmp_path,monkeypatch):
    monkeypatch.setattr(storage,'_utcnow',lambda:NOW)
    repo=AIJobRepository(tmp_path/'jobs.db');ident=job(repo,SONNET);start(repo,ident)
    repo.link_anthropic_message(ident,'owner','msg_test')
    held=repo.get_job(ident)['budget_charge_microusd']
    usage={**receipt()['usage'],'code_execution_requests':0}
    progress=progress_for([{'id':'msg_test','stop_reason':'pause_turn','usage':usage}])
    repo.record_provider_progress(ident,'owner',progress)
    with pytest.raises(RuntimeError,match='daily_budget_usd_reached'):
        repo.record_provider_progress(ident,'owner',progress,will_continue=True,
                                      shared_daily_budget_usd=held/1_000_000,shared_budget_enforce_limit=True)
    assert repo.get_job(ident)['budget_charge_microusd']==held
