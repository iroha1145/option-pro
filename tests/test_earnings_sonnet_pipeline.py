"""Full paid lifecycle via the real SDK, HTTP simulation, database and worker."""
import asyncio
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3

import httpx2
import pytest

from app.config import Settings
from app.services.ai_jobs import claude_provider as provider, runtime, worker
from app.services.ai_jobs.repository import AIJobRepository
from app.services.model_budget import SharedModelBudget
from test_claude_provider import _sse, _transport_client
from test_claude_job_worker import message as legacy_message, settings as legacy_settings, create_job as legacy_job


RESULT = json.loads(legacy_message().content[0].text)
THOUGHT = {'type': 'thinking', 'thinking': '', 'signature': 'signed-earnings-thought'}
CODE = [
    {'type': 'server_tool_use', 'id': 'code1', 'name': 'bash_code_execution', 'input': {'command': 'python verify.py'}},
    {'type': 'bash_code_execution_tool_result', 'tool_use_id': 'code1', 'content': {
        'type': 'bash_code_execution_result', 'stdout': 'verified.csv', 'stderr': '', 'return_code': 0, 'content': []}},
]
SEARCH = [
    {'type': 'server_tool_use', 'id': 'search1', 'name': 'web_search', 'input': {'query': 'issuer earnings release'}},
    {'type': 'web_search_tool_result', 'tool_use_id': 'search1', 'content': [
        {'type': 'web_search_result', 'url': 'https://issuer.org/release', 'title': 'Quarterly release', 'encrypted_content': 'opaque'}]},
]


def sse_message(ident, model, blocks, *, stop='end_turn', searches=0, container=None):
    usage = {'input_tokens': 100, 'cache_creation_input_tokens': 50, 'cache_read_input_tokens': 20,
        'output_tokens': 1, 'cache_creation': {'ephemeral_5m_input_tokens': 0, 'ephemeral_1h_input_tokens': 50},
        'server_tool_use': {'web_search_requests': searches, 'web_fetch_requests': 0}}
    initial = {'id': ident, 'type': 'message', 'role': 'assistant', 'model': model,
        'content': [], 'stop_reason': None, 'stop_sequence': None, 'usage': usage}
    if container:
        initial['container'] = {'id': container, 'expires_at': '2026-11-30T00:00:00Z'}
    events = [{'type': 'message_start', 'message': initial}]
    for index, block in enumerate(blocks):
        if block['type'] == 'text':
            events.extend([
                {'type': 'content_block_start', 'index': index, 'content_block': {'type': 'text', 'text': ''}},
                {'type': 'content_block_delta', 'index': index, 'delta': {'type': 'text_delta', 'text': block['text']}},
            ])
        elif block['type'] == 'thinking':
            events.extend([
                {'type': 'content_block_start', 'index': index, 'content_block': {'type': 'thinking', 'thinking': '', 'signature': ''}},
                {'type': 'content_block_delta', 'index': index, 'delta': {'type': 'signature_delta', 'signature': block['signature']}},
            ])
        else:
            events.append({'type': 'content_block_start', 'index': index, 'content_block': block})
        events.append({'type': 'content_block_stop', 'index': index})
    events.extend([
        {'type': 'message_delta', 'delta': {'stop_reason': stop, 'stop_sequence': None}, 'usage': {'output_tokens': 10}},
        {'type': 'message_stop'},
    ])
    return _sse(events)


def setup(tmp_path, *, budget=100):
    repo = AIJobRepository(tmp_path / 'jobs.sqlite3')
    config = Settings(_env_file=None, openai_job_db_path=repo.path, anthropic_api_key='test',
        openai_manual_cooldown_seconds=0).model_copy(update={
            'model_daily_budget_usd': budget, 'model_budget_start_at': None, 'model_budget_enforce_limit': True})
    payload = {'ticker': 'AAPL', 'name': '苹果', 'analysis_stage': 'pre_release', 'as_of': '2026-10-11T00:00:00Z'}
    version, digest = runtime.schema_identity('earnings_impact', model=runtime.SONNET_EARNINGS_MODEL)
    created, _ = repo.create_job(job_type='earnings_impact', payload=payload,
        model=runtime.SONNET_EARNINGS_MODEL, reasoning='xhigh', execution_mode='background',
        prompt_version=runtime.PROMPT_VERSIONS['earnings_impact'], schema_version=version,
        schema_sha256=digest, max_queued=10, earnings_featured_verified=True)
    return repo, config, created['job_id']


def process(repo, config):
    claimed = repo.claim_due('owner', 600)
    assert claimed is not None
    asyncio.run(worker.process_job(repo, config, claimed, 'owner'))


def assert_cache_points(params):
    # One explicit stable point plus one automatic growth point, within the
    # four-point limit, with a consistent TTL and unmodified signed content.
    explicit = [block for block in params['system'] if 'cache_control' in block]
    for message in params['messages']:
        explicit.extend(block for block in message['content'] if isinstance(block, dict) and 'cache_control' in block)
    assert len(explicit) == 1 and explicit[0]['cache_control']['ttl'] == '1h'
    assert params.get('cache_control') == {'type': 'ephemeral', 'ttl': '1h'}


def test_sonnet_pipeline_three_rounds_completes_and_shared_budget_counts_once(tmp_path, monkeypatch):
    repo, config, ident = setup(tmp_path)
    shared = SharedModelBudget(repo.path, 100)
    assert shared.reserve_brief_request('independent_brief', 0, 2000)
    shared.settle_brief_request('independent_brief', 0, cost_microusd=1000, accounting_complete=True)
    before_brief = shared.snapshot()['opus_charge_microusd']
    requests = []; states = []
    bodies = [
        sse_message('earnings1', runtime.SONNET_EARNINGS_MODEL, [THOUGHT, *CODE], stop='pause_turn', container='container_earnings'),
        sse_message('earnings2', runtime.SONNET_EARNINGS_MODEL, [THOUGHT, *SEARCH, {'type': 'text', 'text': '已经核验，继续整理最终结果。'}], searches=1, container='container_earnings'),
        sse_message('earnings3', runtime.SONNET_EARNINGS_MODEL, [{'type': 'text', 'text': '核验完成。草稿 {"ticker":"AAPL","draft":true} 最终结果：' + json.dumps(RESULT, ensure_ascii=False)}]),
        sse_message('haiku1', 'claude-haiku-5-5', [{'type': 'text', 'text': json.dumps(RESULT, ensure_ascii=False)}]),
    ]
    def handler(request):
        requests.append(json.loads(request.content))
        # These are real durable writes performed before the next HTTP request.
        states.append({'row': repo.get_job(ident), 'progress': repo.get_provider_progress(ident), 'budget': shared.snapshot()})
        return httpx2.Response(200, headers={'content-type': 'text/event-stream'}, text=bodies[len(requests)-1])
    clients, _ = _transport_client(monkeypatch, handler)
    process(repo, config)
    row = repo.get_job(ident)
    assert row['status'] == 'completed', row
    assert row['anthropic_message_id'] == 'earnings1' and row['openai_response_id'] is None
    receipt = repo.get_provider_result(ident)
    assert receipt['request_ids'] == ['earnings1', 'earnings2', 'earnings3']
    assert receipt['job_type'] == 'earnings_impact'
    assert receipt['rounds'][1]['continuation_reason'] == 'incomplete_final_json'
    assert receipt['usage']['input_tokens'] == 510 and receipt['usage']['output_tokens'] == 30
    assert receipt['usage']['code_execution_requests'] == 1 and receipt['usage']['web_search_requests'] == 1
    assert row['budget_charge_microusd'] == 11_506
    assert json.loads(row['result_json']) == RESULT
    assert states[1]['row']['anthropic_message_id'] == 'earnings1'
    assert states[1]['progress']['rounds'][0]['stop_reason'] == 'pause_turn'
    assert states[2]['progress']['rounds'][1]['continuation_reason'] == 'incomplete_final_json'
    assert states[2]['progress']['request_ids'] == ['earnings1', 'earnings2']
    assert states[2]['budget']['job_model_charges_microusd'][runtime.SONNET_EARNINGS_MODEL] >= runtime.budget_reservation_microusd('earnings_impact', model=runtime.SONNET_EARNINGS_MODEL)
    assert 'cache_control' not in requests[0] and 'container' not in requests[0]
    for params in requests[1:]:
        assert_cache_points(params)
        assert params['container'] == 'container_earnings'
    assert requests[1]['messages'][1]['content'][0] == THOUGHT
    assert requests[2]['messages'][:len(requests[1]['messages'])] == requests[1]['messages']
    assert requests[2]['messages'][-2]['content'][0] == THOUGHT
    assert requests[2]['messages'][-2]['content'][1]['id'] == 'search1'
    assert requests[2]['max_tokens'] == 65_516
    assert shared.snapshot()['opus_charge_microusd'] == before_brief == 1000
    assert shared.snapshot()['used_microusd'] == 12_506
    # A separate historical Haiku job keeps its prior route and pricing. Its
    # new request must contain no container or conversation from the Sonnet earnings job.
    legacy_ident = legacy_job(repo)
    process(repo, legacy_settings(repo.path).model_copy(update={
        'model_daily_budget_usd': 100, 'model_budget_start_at': None}))
    legacy = repo.get_job(legacy_ident)
    assert legacy['status'] == 'completed' and legacy['model'] == 'claude-haiku-5-5'
    assert legacy['budget_charge_microusd'] == 26
    assert 'container' not in requests[-1] and len(requests[-1]['messages']) == 1
    final = shared.snapshot()
    assert final['job_model_charges_microusd'][runtime.SONNET_EARNINGS_MODEL] == 11_506
    assert final['job_model_charges_microusd']['claude-haiku-5-5'] == 26
    assert final['opus_charge_microusd'] == 1000 and final['used_microusd'] == 12_532
    with sqlite3.connect(repo.path) as connection:
        assert connection.execute('SELECT COUNT(*) FROM model_budget_brief_requests').fetchone()[0] == 1
    # Same-model market focus shares the Sonnet charge bucket, while keeping
    # its original 5m prefix, code budget and pause-only continuation contract.
    from test_claude_tool_evidence import _focus_job
    from test_claude_market_focus_output import valid_result
    focus_job, focus_payload, focus_config = _focus_job(repo)
    bodies.append(sse_message('market1', runtime.SONNET_MODEL, [
        {'type': 'text', 'text': json.dumps({**valid_result(focus_payload), "event_verifications": []}, ensure_ascii=False)}]))
    process(repo, focus_config.model_copy(update={'model_daily_budget_usd': 100, 'model_budget_start_at': None}))
    focus_row = repo.get_job(focus_job['job_id'])
    assert focus_row['status'] == 'completed' and focus_row['budget_charge_microusd'] == 502, focus_row
    assert requests[-1]['system'][0]['cache_control']['ttl'] == '5m'
    assert 'cache_control' not in requests[-1] and 'container' not in requests[-1]
    assert len(requests[-1]['messages']) == 1
    focus_receipt = repo.get_provider_result(focus_job['job_id'])
    assert 'job_type' not in focus_receipt and 'continuation_reason' not in focus_receipt['rounds'][0]
    final = shared.snapshot()
    assert final['job_model_charges_microusd'][runtime.SONNET_MODEL] == 12_008
    assert final['opus_charge_microusd'] == 1000 and final['used_microusd'] == 13_034
    assert all(client.is_closed() for client in clients)
    evidence = os.environ.get('OPTION_PRO_EARNINGS_EVIDENCE_DIR')
    if evidence:
        root = Path(evidence); root.mkdir(parents=True, exist_ok=True)
        (root / 'sonnet-pipeline.json').write_text(json.dumps({
            'status': row['status'], 'message_id': row['anthropic_message_id'],
            'receipt': receipt, 'legacy_cost_microusd': legacy['budget_charge_microusd'],
            'market_focus_receipt': focus_receipt, 'market_focus_cost_microusd': focus_row['budget_charge_microusd'],
            'snapshot': final, 'requests': requests, 'durable_before_requests': states,
            'network': 'SDK MockTransport only; no paid request',
        }, ensure_ascii=False, indent=2))
        # Include the live WAL and close a standalone, consistent evidence DB.
        with closing(sqlite3.connect(repo.path)) as live, closing(sqlite3.connect(root / 'sonnet-pipeline-test.sqlite3')) as snapshot:
            live.backup(snapshot)
            snapshot.execute('PRAGMA journal_mode=DELETE')
            assert snapshot.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


def test_sonnet_shared_budget_blocks_before_first_network_request(tmp_path, monkeypatch):
    repo, config, ident = setup(tmp_path, budget=4)
    def handler(request):
        raise AssertionError('blocked job must not reach the provider')
    _transport_client(monkeypatch, handler)
    process(repo, config)
    row = repo.get_job(ident)
    assert row['status'] == 'budget_blocked' and row['error_code'] == 'daily_budget_usd_reached'
    assert row['submission_started_at'] is None and row['anthropic_message_id'] is None
    assert SharedModelBudget(repo.path, 4).snapshot()['used_microusd'] == 0


def test_sonnet_progress_admission_precedes_second_paid_request(tmp_path, monkeypatch):
    reserve = runtime.budget_reservation_microusd('earnings_impact', model=runtime.SONNET_EARNINGS_MODEL)
    repo, config, ident = setup(tmp_path, budget=(reserve + 100) / 1_000_000)
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        assert len(requests) == 1, 'second request requires a durable budget admission'
        return httpx2.Response(200, headers={'content-type': 'text/event-stream'}, text=sse_message(
            'earnings_limited', runtime.SONNET_EARNINGS_MODEL, [{'type': 'text', 'text': '继续核验。'}]))
    _transport_client(monkeypatch, handler)
    process(repo, config)
    row = repo.get_job(ident)
    assert len(requests) == 1
    assert row['status'] == 'failed' and row['error_code'] == 'submission_outcome_unknown'
    assert row['anthropic_message_id'] == 'earnings_limited'
    assert len(repo.get_provider_progress(ident)['rounds']) == 1
    assert row['budget_charge_microusd'] >= reserve
    assert SharedModelBudget(repo.path, 10).snapshot()['job_model_charges_microusd'][runtime.SONNET_EARNINGS_MODEL] == row['budget_charge_microusd']


@pytest.mark.parametrize('change', ['missing_reason', 'wrong_reason', 'missing_job_type', 'wrong_job_type'])
def test_progress_end_turn_exception_is_only_explicit_sonnet_earnings(change):
    usage = {key: 0 for key in provider._USAGE_KEYS}
    rounds = [{'id': 'msg1', 'stop_reason': 'end_turn', 'usage': usage,
        'continuation_reason': 'incomplete_final_json'}]
    value = {'provider': 'anthropic', 'model': runtime.SONNET_EARNINGS_MODEL,
        'job_type': 'earnings_impact', 'id': 'msg1', 'request_ids': ['msg1', 'msg2'],
        'rounds': rounds, 'confirmed_usage': provider.sum_round_usage(rounds)}
    if change == 'missing_reason':
        rounds[0].pop('continuation_reason')
    elif change == 'wrong_reason':
        rounds[0]['continuation_reason'] = 'continue_anyway'
    elif change == 'missing_job_type':
        value['model'] = runtime.SONNET_MODEL
        value.pop('job_type')
    else:
        value['job_type'] = 'market_focus'
    with pytest.raises(ValueError, match='ai_job_provider_'):
        AIJobRepository._provider_progress_json(value)


def test_progress_context_must_match_the_actual_database_task(tmp_path):
    repo = AIJobRepository(tmp_path / 'wrong-task.sqlite3')
    version, digest = runtime.schema_identity('option_alerts', model=runtime.SONNET_EARNINGS_MODEL)
    created, _ = repo.create_job(job_type='option_alerts', payload={}, model=runtime.SONNET_EARNINGS_MODEL,
        reasoning='xhigh', execution_mode='background', prompt_version='context-test',
        schema_version=version, schema_sha256=digest, max_queued=10)
    ident = created['job_id']
    assert repo.claim_due('owner', 600)['job_id'] == ident
    assert repo.mark_submission_started(ident, 'owner', daily_limit=0, daily_token_limit=10_000_000, cooldown_seconds=0) == 'started'
    repo.link_anthropic_message(ident, 'owner', 'msg1')
    usage = {key: 0 for key in provider._USAGE_KEYS}
    rounds = [{'id': 'msg1', 'stop_reason': 'end_turn', 'usage': usage,
        'continuation_reason': 'incomplete_final_json'}]
    value = {'provider': 'anthropic', 'model': runtime.SONNET_EARNINGS_MODEL,
        'job_type': 'earnings_impact', 'id': 'msg1', 'request_ids': ['msg1'],
        'rounds': rounds, 'confirmed_usage': provider.sum_round_usage(rounds)}
    with pytest.raises(RuntimeError, match='ai_job_provider_progress_rejected'):
        repo.record_provider_progress(ident, 'owner', value, will_continue=True)
    assert repo.get_provider_progress(ident) is None



def test_sonnet_result_for_prior_round_tool_finishes_without_an_extra_paid_request(tmp_path, monkeypatch):
    repo, config, ident = setup(tmp_path)
    requests = []
    bodies = [
        sse_message('pending_tool1', runtime.SONNET_EARNINGS_MODEL, [THOUGHT, SEARCH[0]], stop='pause_turn', searches=1),
        sse_message('pending_tool2', runtime.SONNET_EARNINGS_MODEL, [SEARCH[1], {
            'type': 'text', 'text': '来源已核验：' + json.dumps(RESULT, ensure_ascii=False)}]),
    ]
    def handler(request):
        requests.append(json.loads(request.content))
        assert len(requests) <= 2, 'a complete result must not trigger a third paid request'
        return httpx2.Response(200, headers={'content-type': 'text/event-stream'}, text=bodies[len(requests)-1])
    _transport_client(monkeypatch, handler)
    process(repo, config)
    row = repo.get_job(ident)
    assert row['status'] == 'completed', row
    receipt = repo.get_provider_result(ident)
    assert len(requests) == 2 and receipt['request_ids'] == ['pending_tool1', 'pending_tool2']
    assert receipt['tool_evidence'][0]['tool_use_id'] == 'search1'
    assert row['budget_charge_microusd'] == 11_004
    assert json.loads(receipt['output_text']) == RESULT
