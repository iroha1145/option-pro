"""No paid requests: Claude earnings routing, tools, cache prefix, pricing and continuation."""
import asyncio
from copy import deepcopy
from datetime import datetime as real_datetime, timezone
import json
from types import SimpleNamespace

from anthropic.types import Message
from pydantic import SecretStr
import pytest

from app.config import Settings
from app.services.ai_jobs import runtime, claude_provider as provider

HAIKU = runtime.OFFICIAL_CLAUDE_MODEL


def prepared(payload=None, *, model=HAIKU, job_type='earnings_impact'):
    settings = SimpleNamespace(openai_model=model, openai_reasoning='xhigh',
        openai_max_concurrency=4, openai_execution_mode='background', openai_daily_token_limit=10_000_000,
        anthropic_api_key=SecretStr('test'), openai_timeout_seconds=900)
    return runtime.prepare_claude(settings, job_type, payload or {'ticker': 'AAPL', 'name': '苹果'})


def message(ident, *, stop='end_turn', text='{"answer":"done"}', content=None, output=10, searches=0):
    return Message(id=ident, model=HAIKU, role='assistant', type='message',
        content=content or [{'type': 'text', 'text': text}], stop_reason=stop,
        usage={'input_tokens': 100, 'output_tokens': output, 'cache_creation_input_tokens': 0,
            'cache_read_input_tokens': 0, 'cache_creation': {'ephemeral_5m_input_tokens': 0, 'ephemeral_1h_input_tokens': 0},
            'server_tool_use': {'web_search_requests': searches, 'web_fetch_requests': 0}})


async def noop(*args, **kwargs):
    pass


def scripted(monkeypatch, responses):
    """Replace the transport; capture each request's params and the shared counter."""
    calls, counters = [], []

    async def stream(item, *, on_message_start, tool_counts):
        calls.append(deepcopy(item.params))
        counters.append(tool_counts)
        response = responses[len(calls) - 1]
        await on_message_start(response.id)
        return response

    monkeypatch.setattr(provider, 'stream_message', stream)
    return calls, counters


def test_earnings_follow_the_configured_model_without_a_separate_knob(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    original = Settings(_env_file=None)
    assert not hasattr(original, 'openai_earnings_model')
    assert runtime.model_identity_for_job(original, 'earnings_impact') == (original.openai_model, original.openai_reasoning)
    assert runtime.settings_for_job(original, 'earnings_impact').openai_model == original.openai_model
    assert runtime.claude_earnings('earnings_impact', None) and runtime.claude_earnings('earnings_impact', HAIKU)
    assert not runtime.claude_earnings('earnings_impact', 'gpt-5.6-luna')
    assert not runtime.claude_earnings('market_focus', HAIKU)
    # The default (None) and the explicit Haiku contract are the same identity.
    assert runtime.schema_identity('earnings_impact') == runtime.schema_identity('earnings_impact', model=HAIKU)


def test_stable_system_prefix_and_dated_first_turn(monkeypatch):
    monkeypatch.setattr(runtime, '_earnings_request_date', lambda: '2026-10-11')
    before = prepared({'ticker': 'AAPL', 'name': '苹果', 'analysis_stage': 'pre_release'})
    after = prepared({'ticker': 'MSFT', 'name': '微软', 'analysis_stage': 'post_release_final'})
    system = before.params['system'][0]['text']
    assert before.params['system'] == after.params['system']
    assert before.params['tools'] == after.params['tools']
    assert before.params['messages'] != after.params['messages']
    assert before.params['system'][0]['cache_control'] == {'type': 'ephemeral', 'ttl': '5m'}
    first_turn = before.params['messages'][0]['content'][0]
    assert 'cache_control' not in first_turn
    preamble, data = first_turn['text'].split('\n', 1)
    assert preamble.startswith('今天是2026-10-11（美国东部时间）。')
    # The date is trusted text outside the untrusted data, never in the payload.
    assert data.startswith('<untrusted_earnings_data>') and '2026-10-11' not in data
    assert '2026-10-11' not in system
    assert 'evidence_as_of' not in system and 'analysis_as_of' not in system
    assert '请求开头给出今天的日期' in system
    assert before.params['thinking'] == {'type': 'adaptive'}
    assert before.params['output_config'] == {'effort': 'xhigh'}
    assert before.params['max_tokens'] == 65_536
    assert not {'temperature', 'top_p', 'top_k', 'cache_control', 'container'} & before.params.keys()
    assert '内部推理' in system and '币种' in system


def test_request_date_is_the_new_york_calendar_date(monkeypatch):
    class Clock(real_datetime):
        @classmethod
        def now(cls, tz=None):
            # 02:00 UTC on Oct 12 is still Oct 11 in New York.
            return real_datetime(2026, 10, 12, 2, 0, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(runtime, 'datetime', Clock)
    assert runtime._earnings_request_date() == '2026-10-11'


def test_tool_caps_safety_net_prompt_and_reservation_agree():
    item = prepared()
    tools = {tool['name']: tool for tool in item.params['tools']}
    assert tools['web_search']['max_uses'] == runtime.EARNINGS_TOOL_MAX_USES['web_search'] == 5
    assert tools['web_fetch']['max_uses'] == runtime.EARNINGS_TOOL_MAX_USES['web_fetch'] == 4
    assert tools['web_fetch']['max_content_tokens'] == 6_000
    assert tools['code_execution'] == {'type': 'code_execution_20260120', 'name': 'code_execution'}
    assert runtime.EARNINGS_TOOL_LIMITS == {'web_search': 6, 'web_fetch': 5, 'code': 3, 'total': 14}
    assert provider.tool_limits(item) == runtime.EARNINGS_TOOL_LIMITS
    assert '网页搜索最多5次，网页抓取最多4次，代码执行最多2次' in item.params['system'][0]['text']
    assert runtime.max_tool_calls_for('earnings_impact', model=HAIKU) == 14
    # 934,464 input tokens at the >100K 5m-write rate ($0.625/MTok), 65,536
    # output tokens at $2.50/MTok, and the six searches the safety net admits.
    assert runtime.budget_reservation_microusd('earnings_impact', model=HAIKU) == 584_040 + 163_840 + 60_000


@pytest.mark.parametrize('job_type', ['news_impact', 'market_focus'])
def test_other_haiku_tasks_keep_their_one_search_one_fetch_contract(job_type):
    tools = {tool['name']: tool for tool in runtime.claude_tools_for(job_type, {}, model=HAIKU)}
    assert {name: tool.get('max_uses') for name, tool in tools.items()} == {
        'web_search': 1, 'web_fetch': 1, 'code_execution': None,
    }
    assert tools['web_fetch']['max_content_tokens'] == 8_000
    stand_in = SimpleNamespace(params={'model': HAIKU}, diagnostic_task=f'ai_jobs:{job_type}')
    assert provider.tool_limits(stand_in) == {'web_search': 1, 'web_fetch': 1, 'code': 2, 'total': 4}


def test_sonnet_market_focus_keeps_its_original_contract():
    focus = provider.prepare_message(
        SimpleNamespace(openai_model=runtime.SONNET_MODEL, openai_reasoning='xhigh',
            anthropic_api_key=SecretStr('test'), openai_timeout_seconds=900),
        instructions='Stable market focus rules', input_text='focus evidence',
        schema={'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False},
        max_tokens=65_536, job_type='market_focus', tools=runtime.claude_tools_for('market_focus', {}, model=runtime.SONNET_MODEL))
    assert focus.params['system'][0]['cache_control'] == {'type': 'ephemeral', 'ttl': '5m'}
    assert 'cache_control' not in focus.params and 'container' not in focus.params
    assert provider.tool_limits(focus) == {'web_search': 12, 'web_fetch': 12, 'code': 2, 'total': 26}
    assert {tool['name']: tool['type'] for tool in focus.params['tools']}['code_execution'] == 'code_execution_20260521'
    assert runtime.max_tool_calls_for('market_focus', model=runtime.SONNET_MODEL) == 0
    assert runtime.uses_claude('claude-opus-5-5') is False
    assert runtime.analysis_identity_supported('claude-opus-5-5', 'xhigh') is False


def _usage(input_tokens, *, cached, writes, output=1_000, searches=0):
    return {'input_tokens': input_tokens, 'cached_input_tokens': cached, 'cache_creation_input_tokens': writes,
        'cache_creation_5m_input_tokens': writes, 'cache_creation_1h_input_tokens': 0,
        'output_tokens': output, 'reasoning_tokens': 0, 'total_tokens': input_tokens + output,
        'web_search_requests': searches, 'web_fetch_requests': 0, 'code_execution_requests': 0}


def test_each_request_is_priced_on_its_own_prompt_size():
    # Two 60K-token requests: 120K together, yet each is billed on the <=100K card.
    rounds = [
        {'id': 'm1', 'stop_reason': 'pause_turn', 'usage': _usage(60_000, cached=40_000, writes=10_000, searches=2)},
        {'id': 'm2', 'stop_reason': 'end_turn', 'usage': _usage(60_000, cached=50_000, writes=5_000, searches=1)},
    ]
    total = provider.sum_round_usage(rounds)
    # Per MTok: input $0.10, cache read $0.01, 5m write $0.125, output $0.50.
    # m1: 10,000 x 0.10 + 40,000 x 0.01 + 10,000 x 0.125 + 1,000 x 0.50 = 3,150 microdollars
    # m2:  5,000 x 0.10 + 50,000 x 0.01 +  5,000 x 0.125 + 1,000 x 0.50 = 2,125
    # three searches at $0.01 each = 30,000
    assert runtime.settled_usage_cost_microusd(
        'earnings_impact', total, fallback_microusd=9_999_999, model=HAIKU, rounds=rounds) == 3_150 + 2_125 + 30_000
    # Pricing the sum as one request would wrongly apply the 5x card.
    assert runtime.settled_usage_cost_microusd(
        'earnings_impact', total, fallback_microusd=9_999_999, model=HAIKU) == 5 * (3_150 + 2_125) + 30_000


def test_only_the_request_over_100k_uses_the_long_prompt_card():
    rounds = [
        {'id': 'm1', 'stop_reason': 'pause_turn', 'usage': _usage(120_000, cached=100_000, writes=10_000)},
        {'id': 'm2', 'stop_reason': 'end_turn', 'usage': _usage(30_000, cached=20_000, writes=5_000)},
    ]
    # m1 at 5x: (10,000 x 0.10 + 100,000 x 0.01 + 10,000 x 0.125 + 1,000 x 0.50) x 5 = 18,750
    # m2 at 1x:   5,000 x 0.10 +  20,000 x 0.01 +  5,000 x 0.125 + 1,000 x 0.50      =  1,825
    assert runtime.settled_usage_cost_microusd(
        'earnings_impact', provider.sum_round_usage(rounds), fallback_microusd=9_999_999,
        model=HAIKU, rounds=rounds) == 18_750 + 1_825


def test_unknown_round_usage_keeps_the_conservative_fallback():
    rounds = [
        {'id': 'm1', 'stop_reason': 'pause_turn', 'usage': _usage(60_000, cached=40_000, writes=10_000)},
        {'id': 'm2', 'stop_reason': 'end_turn', 'usage': {**_usage(60_000, cached=50_000, writes=5_000), 'cache_creation_input_tokens': None}},
    ]
    total = {**provider.sum_round_usage(rounds), 'input_tokens': 120_000}
    assert runtime.settled_usage_cost_microusd(
        'earnings_impact', total, fallback_microusd=777, model=HAIKU, rounds=rounds) == 777


def test_progress_only_and_pause_continue_with_verbatim_thinking_and_shared_counts(monkeypatch):
    thought = {'type': 'thinking', 'thinking': '', 'signature': 'opaque-signature'}
    responses = [message('msg_1', text='还在核验'), message('msg_2', stop='pause_turn', content=[thought]), message('msg_3')]
    progress = []

    async def record(value, **kwargs):
        progress.append(deepcopy(value))

    calls, counters = scripted(monkeypatch, responses)
    original = prepared()
    result = asyncio.run(provider.stream_conversation(original, on_message_start=noop, on_progress=record)).receipt
    assert len(calls) == 3
    assert calls[1]['messages'][-2]['content'] == responses[0].content
    assert calls[1]['messages'][-1]['role'] == 'user'
    assert calls[2]['messages'][-1]['content'] == responses[1].content
    assert calls[2]['messages'][-1]['content'][0].signature == 'opaque-signature'
    assert 'cache_control' not in calls[0]
    assert calls[1]['cache_control'] == calls[2]['cache_control'] == {'type': 'ephemeral'}
    assert 'cache_control' not in calls[1]['messages'][0]['content'][0]
    assert 'cache_control' not in original.params['messages'][0]['content'][0]
    assert all(count is counters[0] for count in counters)
    assert result['usage']['output_tokens'] == 30 and result['usage']['input_tokens'] == 300
    assert result['request_ids'] == ['msg_1', 'msg_2', 'msg_3']
    assert result['job_type'] == 'earnings_impact'
    assert result['output_text'] == '{"answer":"done"}' and result['terminal_error'] is None
    assert 'opaque-signature' not in str(progress)


def test_pause_then_nudge_then_pause_keeps_an_append_only_history(monkeypatch):
    # A nudged round that calls more tools pauses again; every request extends
    # the previous one, never sends two user turns in a row, and ends with
    # either the paused assistant turn or the nudge.
    thought = {'type': 'thinking', 'thinking': '', 'signature': 'signed'}
    search = [
        {'type': 'server_tool_use', 'id': 'search_more', 'name': 'web_search', 'input': {'query': 'AAPL guidance'}},
        {'type': 'web_search_tool_result', 'tool_use_id': 'search_more', 'content': [
            {'type': 'web_search_result', 'url': 'https://investor.apple.com/guidance', 'title': 'Guidance', 'encrypted_content': 'opaque'}]},
    ]
    responses = [
        message('msg_1', stop='pause_turn', content=[thought]),
        message('msg_2', text='已核验收入，仍需确认指引。'),
        message('msg_3', stop='pause_turn', content=[thought, *search], searches=1),
        message('msg_4'),
    ]
    calls, _ = scripted(monkeypatch, responses)
    receipt = asyncio.run(provider.stream_conversation(prepared(), on_message_start=noop, on_progress=noop)).receipt
    assert len(calls) == 4
    assert receipt['terminal_error'] is None and receipt['output_text'] == '{"answer":"done"}'
    assert [item['stop_reason'] for item in receipt['rounds']] == ['pause_turn', 'end_turn', 'pause_turn', 'end_turn']
    assert [item.get('continuation_reason') for item in receipt['rounds']] == [None, 'incomplete_final_json', None, None]
    for earlier, later in zip(calls, calls[1:]):
        assert later['messages'][:len(earlier['messages'])] == earlier['messages']
    for params in calls:
        roles = [item['role'] for item in params['messages']]
        assert roles[0] == 'user'
        assert all(not (first == second == 'user') for first, second in zip(roles, roles[1:]))
    assert [params['messages'][-1]['role'] for params in calls] == ['user', 'assistant', 'user', 'assistant']
    assert calls[2]['messages'][-1]['content'][0]['text'].startswith('分析尚未交付最终JSON')
    assert calls[3]['messages'][-1]['content'] == responses[2].content
    assert all('cache_control' in params for params in calls[1:])


_TTL_RANK = {'5m': 0, '1h': 1}


def _ttl(marker):
    return _TTL_RANK[marker.get('ttl', '5m')]


def test_continuation_cache_ttl_never_exceeds_the_system_marker_and_prefix_is_unchanged(monkeypatch):
    thought = {'type': 'thinking', 'thinking': '', 'signature': 'signed'}
    calls, _ = scripted(monkeypatch, [message('msg_1', stop='pause_turn', content=[thought]), message('msg_2')])
    asyncio.run(provider.stream_conversation(prepared(), on_message_start=noop, on_progress=noop))
    first, second = calls
    markers = [block['cache_control'] for block in second['system'] if 'cache_control' in block]
    assert markers and all(_ttl(second['cache_control']) <= _ttl(marker) for marker in markers)
    # Longer TTLs must come before shorter ones; changing system or tools would
    # also invalidate the replayed thinking block.
    dump = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True)
    assert dump(second['system']) == dump(first['system'])
    assert dump(second['tools']) == dump(first['tools'])
    assert second['messages'][0] == first['messages'][0]
    assert {key: value for key, value in second.items() if key not in {'messages', 'max_tokens', 'cache_control'}} == {
        key: value for key, value in first.items() if key not in {'messages', 'max_tokens'}
    }


def test_progress_continuations_stop_after_four_requests(monkeypatch):
    calls, _ = scripted(monkeypatch, [message(f'msg_{index}', text='仍在分析') for index in range(1, 6)])
    result = asyncio.run(provider.stream_conversation(prepared(), on_message_start=noop, on_progress=noop)).receipt
    assert len(calls) == 4 and result['terminal_error'] == 'provider_continuation_limit'


def test_unknown_usage_never_authorizes_paid_continuation(monkeypatch):
    response = message('msg_1', stop='pause_turn')
    response.usage.cache_read_input_tokens = None
    calls, _ = scripted(monkeypatch, [response])
    result = asyncio.run(provider.stream_conversation(prepared(), on_message_start=noop, on_progress=noop)).receipt
    assert len(calls) == 1 and result['terminal_error'] == 'provider_usage_incomplete'


def test_max_uses_exceeded_result_is_not_terminal_and_is_not_evidence(monkeypatch):
    content = [
        {'type': 'server_tool_use', 'id': 'search_ok', 'name': 'web_search', 'input': {'query': 'AAPL results'}},
        {'type': 'web_search_tool_result', 'tool_use_id': 'search_ok', 'content': [
            {'type': 'web_search_result', 'url': 'https://investor.apple.com/q4', 'title': 'Q4 results', 'encrypted_content': 'opaque'}]},
        {'type': 'server_tool_use', 'id': 'search_over', 'name': 'web_search', 'input': {'query': 'AAPL again'}},
        {'type': 'web_search_tool_result', 'tool_use_id': 'search_over', 'content': {
            'type': 'web_search_tool_result_error', 'error_code': 'max_uses_exceeded'}},
        {'type': 'text', 'text': '{"answer":"done"}'},
    ]
    calls, _ = scripted(monkeypatch, [message('msg_1', content=content, searches=1)])
    result = asyncio.run(provider.stream_conversation(prepared(), on_message_start=noop, on_progress=noop)).receipt
    assert len(calls) == 1
    assert result['terminal_error'] is None and result['output_text'] == '{"answer":"done"}'
    assert [item['tool_use_id'] for item in result['tool_evidence']] == ['search_ok']
    assert [item['url'] for item in result['evidence_sources']] == ['https://investor.apple.com/q4']
    assert result['usage']['web_search_requests'] == 1


def _tool_stream(monkeypatch, name, calls):
    import httpx2
    from test_claude_provider import _sse, _sse_events, _transport_client
    events = _sse_events()
    events[0]['message']['model'] = HAIKU
    tool_events = []
    for index in range(calls):
        tool_events.extend([
            {'type': 'content_block_start', 'index': index, 'content_block': {
                'type': 'server_tool_use', 'id': f'tool_{index}', 'name': name, 'input': {}}},
            {'type': 'content_block_stop', 'index': index},
        ])
    body = _sse([events[0], *tool_events, *events[-2:]])

    def handler(request):
        return httpx2.Response(200, headers={'content-type': 'text/event-stream'}, text=body)

    clients, _ = _transport_client(monkeypatch, handler)
    return clients


@pytest.mark.parametrize('name,group', [('web_search', 'web_search'), ('web_fetch', 'web_fetch'), ('bash_code_execution', 'code')])
def test_one_call_over_max_uses_reaches_the_provider_cap_without_aborting(monkeypatch, name, group):
    # The provider answers that call with max_uses_exceeded and lets the model
    # finish; the client counter must not turn it into an unknown outcome.
    clients = _tool_stream(monkeypatch, name, 2)
    limit = runtime.EARNINGS_TOOL_LIMITS[group]
    counts = {'web_search': 0, 'web_fetch': 0, 'code': 0, 'total': 0}
    counts[group] = counts['total'] = limit - 2
    asyncio.run(provider.stream_message(prepared(), tool_counts=counts))
    assert counts[group] == limit
    assert clients[0].is_closed()


@pytest.mark.parametrize('name,group', [('web_search', 'web_search'), ('web_fetch', 'web_fetch'), ('bash_code_execution', 'code')])
def test_safety_net_still_aborts_a_runaway_stream(monkeypatch, name, group):
    clients = _tool_stream(monkeypatch, name, 2)
    limit = runtime.EARNINGS_TOOL_LIMITS[group]
    counts = {'web_search': 0, 'web_fetch': 0, 'code': 0, 'total': 0}
    counts[group] = counts['total'] = limit - 1
    with pytest.raises(RuntimeError, match='provider_tool_limit_exceeded'):
        asyncio.run(provider.stream_message(prepared(), tool_counts=counts))
    assert counts[group] == limit + 1
    assert clients[0].is_closed()


@pytest.mark.parametrize('text,expected', [
    ('核验说明。 {"answer":"done"}', '{"answer":"done"}'),
    ('草稿 {"answer":"draft"} 最终 {"answer":"done"}', '{"answer":"done"}'),
    ('说明 {"answer":{"nested":{"value":"done"}},"items":[{"value":"inner"}]}', '{"answer":{"nested":{"value":"done"}},"items":[{"value":"inner"}]}'),
    # A trailing citation marker or array is not a later answer.
    ('{"a":1} 来源见[1]', '{"a":1}'),
    ('{"a":1} [{"b":2}]', '{"a":1}'),
    ('还在核验，请等待。', None),
    ('[{"answer":"nested only"}]', None),
])
def test_earnings_recovers_last_complete_json_object(text, expected):
    assert provider._last_earnings_json(text) == expected


@pytest.mark.parametrize('stop,code', [('max_tokens', 'provider_incomplete_max_output_tokens'), ('refusal', 'provider_refusal')])
def test_complete_looking_json_never_overrides_terminal_stop(monkeypatch, stop, code):
    calls, _ = scripted(monkeypatch, [message('msg_terminal', stop=stop, text='前文 {"answer":"done"}')])
    receipt = asyncio.run(provider.stream_conversation(prepared(), on_message_start=noop, on_progress=noop)).receipt
    assert len(calls) == 1 and receipt['terminal_error'] == code and receipt['output_text'] == ''


def test_single_request_tasks_cannot_enter_the_conversation_path():
    with pytest.raises(ValueError, match='provider_continuation_not_supported'):
        asyncio.run(provider.stream_conversation(
            prepared({'ticker': 'AAPL', 'alerts': [], 'underlying_price': 100}, job_type='option_alerts'),
            on_message_start=noop, on_progress=noop,
        ))
