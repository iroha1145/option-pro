"""No paid requests: exercise routing, cache prefixes and native continuation."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace

from anthropic.types import Message
from pydantic import SecretStr
import pytest

from app.config import Settings
from app.services.ai_jobs import runtime, claude_provider as provider


def prepared(payload=None):
    settings = SimpleNamespace(openai_model=runtime.SONNET_EARNINGS_MODEL, openai_reasoning='xhigh',
        openai_max_concurrency=4, openai_execution_mode='background', openai_daily_token_limit=10_000_000,
        anthropic_api_key=SecretStr('test'), openai_timeout_seconds=900)
    return runtime.prepare_claude(settings, 'earnings_impact', payload or {'ticker': 'AAPL', 'name': '苹果'})


def message(ident, *, stop='end_turn', text='{"answer":"done"}', content=None, output=10):
    return Message(id=ident, model=runtime.SONNET_EARNINGS_MODEL, role='assistant', type='message',
        content=content or [{'type': 'text', 'text': text}], stop_reason=stop,
        usage={'input_tokens': 100, 'output_tokens': output, 'cache_creation_input_tokens': 0,
            'cache_read_input_tokens': 0, 'cache_creation': {'ephemeral_5m_input_tokens': 0, 'ephemeral_1h_input_tokens': 0},
            'server_tool_use': {'web_search_requests': 0, 'web_fetch_requests': 0}})


async def noop(*args, **kwargs):
    pass


def test_settings_route_earnings_to_sonnet(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    original = Settings(_env_file=None)
    assert runtime.model_identity_for_job(original, 'earnings_impact') == (runtime.SONNET_EARNINGS_MODEL, 'xhigh')
    selected = runtime.settings_for_job(original, 'earnings_impact')
    assert runtime.runtime_configuration_valid(selected)
    assert original.openai_model == 'claude-haiku-5-5'
    assert runtime.model_identity_for_job(original, 'option_alerts') == ('claude-haiku-5-5', 'xhigh')
    assert runtime.model_identity_for_job(original, 'news_impact')[0] == 'gpt-5.6-luna'
    assert runtime.model_identity_for_job(original, 'market_focus')[0] == runtime.SONNET_MODEL


def test_stable_cached_prefix_cross_company_and_stage_does_not_include_dynamic_input():
    before = prepared({'ticker': 'AAPL', 'name': '苹果', 'analysis_stage': 'pre_release'})
    after = prepared({'ticker': 'MSFT', 'name': '微软', 'analysis_stage': 'post_release_final'})
    assert before.params['system'] == after.params['system']
    assert before.params['tools'] == after.params['tools']
    assert before.params['messages'] != after.params['messages']
    assert before.params['system'][0]['cache_control'] == {'type': 'ephemeral', 'ttl': '1h'}
    assert 'cache_control' not in before.params['messages'][0]['content'][0]
    assert before.params['thinking'] == {'type': 'adaptive'}
    assert before.params['output_config'] == {'effort': 'xhigh'}
    assert before.params['max_tokens'] == 65_536
    assert not {'temperature', 'top_p', 'top_k'} & before.params.keys()
    assert '内部推理' in before.params['system'][0]['text']
    assert '币种' in before.params['system'][0]['text']


def test_tools_limits_and_identity_are_specific_to_earnings():
    item = prepared()
    assert provider.tool_limits(item) == runtime.EARNINGS_TOOL_LIMITS
    assert runtime.max_tool_calls_for('earnings_impact', model=runtime.SONNET_EARNINGS_MODEL) == 30
    assert {t['name']: t.get('max_uses') for t in item.params['tools']} == {'web_search': 12, 'web_fetch': 12, 'code_execution': None}
    assert '代码执行最多六次' in item.params['system'][0]['text']
    assert runtime.schema_identity('earnings_impact', model=runtime.SONNET_EARNINGS_MODEL) != runtime.schema_identity('earnings_impact', model=runtime.OFFICIAL_CLAUDE_MODEL)


@pytest.mark.parametrize('tokens', [100_000, 300_000])
def test_sonnet_prices_all_cache_categories_without_long_context_multiplier(tokens):
    usage = {'input_tokens': tokens, 'output_tokens': 100_000, 'cached_input_tokens': 10_000,
        'cache_creation_input_tokens': 30_000, 'cache_creation_5m_input_tokens': 10_000,
        'cache_creation_1h_input_tokens': 20_000, 'web_search_requests': 2}
    expected = (tokens - 40_000) * 2 + 1_000 + 25_000 + 80_000 + 1_000_000 + 20_000
    assert runtime.settled_usage_cost_microusd('earnings_impact', usage, fallback_microusd=99_000_000, model=runtime.SONNET_EARNINGS_MODEL) == expected
    assert runtime.budget_reservation_microusd('earnings_impact', model=runtime.SONNET_EARNINGS_MODEL) == 4_513_216


def test_progress_only_and_pause_continue_with_verbatim_thinking_and_shared_counts(monkeypatch):
    thought = {'type': 'thinking', 'thinking': '', 'signature': 'opaque-signature'}
    responses = [message('msg_1', text='还在核验'), message('msg_2', stop='pause_turn', content=[thought]), message('msg_3')]
    calls = []; count_objects = []; progress = []
    async def stream(item, *, on_message_start, tool_counts):
        calls.append(deepcopy(item.params)); count_objects.append(tool_counts)
        response = responses[len(calls) - 1]
        await on_message_start(response.id)
        return response
    async def record(value, **kwargs):
        progress.append(deepcopy(value))
    monkeypatch.setattr(provider, 'stream_message', stream)
    original = prepared()
    result = asyncio.run(provider.stream_earnings(original, on_message_start=noop, on_progress=record)).receipt
    assert len(calls) == 3
    assert calls[1]['messages'][-2]['content'] == responses[0].content
    assert calls[1]['messages'][-1]['role'] == 'user'
    assert calls[2]['messages'][-1]['content'] == responses[1].content
    assert calls[2]['messages'][-1]['content'][0].signature == 'opaque-signature'
    assert calls[1]['cache_control'] == {'type': 'ephemeral', 'ttl': '1h'}
    assert calls[2]['cache_control'] == calls[1]['cache_control']
    assert 'cache_control' not in calls[1]['messages'][0]['content'][0]
    assert 'cache_control' not in original.params['messages'][0]['content'][0]
    assert all(count is count_objects[0] for count in count_objects)
    assert result['usage']['output_tokens'] == 30 and result['usage']['input_tokens'] == 300
    assert result['request_ids'] == ['msg_1', 'msg_2', 'msg_3']
    assert result['output_text'] == '{"answer":"done"}' and result['terminal_error'] is None
    assert 'opaque-signature' not in str(progress)


def test_progress_continuations_stop_after_four_requests(monkeypatch):
    calls = []
    async def stream(item, *, on_message_start, tool_counts):
        calls.append(item)
        response = message(f'msg_{len(calls)}', text='仍在分析')
        await on_message_start(response.id)
        return response
    monkeypatch.setattr(provider, 'stream_message', stream)
    result = asyncio.run(provider.stream_earnings(prepared(), on_message_start=noop, on_progress=noop)).receipt
    assert len(calls) == 4 and result['terminal_error'] == 'provider_continuation_limit'


def test_unknown_usage_never_authorizes_paid_continuation(monkeypatch):
    calls = []
    async def stream(item, *, on_message_start, tool_counts):
        calls.append(item)
        response = message('msg_1', stop='pause_turn')
        response.usage.cache_read_input_tokens = None
        await on_message_start(response.id)
        return response
    monkeypatch.setattr(provider, 'stream_message', stream)
    result = asyncio.run(provider.stream_earnings(prepared(), on_message_start=noop, on_progress=noop)).receipt
    assert len(calls) == 1 and result['terminal_error'] == 'provider_usage_incomplete'


@pytest.mark.parametrize('name,group', [('web_search', 'web_search'), ('web_fetch', 'web_fetch'), ('bash_code_execution', 'code')])
def test_stream_enforces_cumulative_tool_limit_across_requests(monkeypatch, name, group):
    import httpx2
    from test_claude_provider import _sse, _sse_events, _transport_client
    events = _sse_events()
    events[0]['message']['model'] = runtime.SONNET_EARNINGS_MODEL
    tool_events = []
    for index in range(2):
        tool_events.extend([
            {'type': 'content_block_start', 'index': index, 'content_block': {
                'type': 'server_tool_use', 'id': f'tool_{index}', 'name': name, 'input': {}}},
            {'type': 'content_block_stop', 'index': index},
        ])
    events = [events[0], *tool_events, *events[-2:]]
    def handler(request):
        return httpx2.Response(200, headers={'content-type': 'text/event-stream'}, text=_sse(events))
    clients, _ = _transport_client(monkeypatch, handler)
    counts = {'web_search': 0, 'web_fetch': 0, 'code': 0, 'total': 0}
    counts[group] = runtime.EARNINGS_TOOL_LIMITS[group] - 1
    counts['total'] = counts[group]
    with pytest.raises(RuntimeError, match='provider_tool_limit_exceeded'):
        asyncio.run(provider.stream_message(prepared(), tool_counts=counts))
    assert counts[group] == runtime.EARNINGS_TOOL_LIMITS[group] + 1
    assert clients[0].is_closed()



def test_same_sonnet_model_keeps_market_focus_original_contract():
    from test_claude_tool_evidence import _prepared as focus_prepared
    item = prepared()
    old = provider.prepare_message(
        SimpleNamespace(openai_model=runtime.SONNET_MODEL, openai_reasoning='xhigh',
            anthropic_api_key=SecretStr('test'), openai_timeout_seconds=900),
        instructions='Stable market focus rules', input_text='focus evidence',
        schema={'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False},
        max_tokens=65_536, job_type='market_focus', tools=runtime.claude_tools_for('market_focus', {}, model=runtime.SONNET_MODEL))
    assert old.params['model'] == item.params['model']
    assert old.params['system'][0]['cache_control']['ttl'] == '5m'
    assert 'cache_control' not in old.params and 'container' not in old.params
    assert provider.tool_limits(old) == {'web_search': 12, 'web_fetch': 12, 'code': 2, 'total': 26}
    assert provider.tool_limits(focus_prepared())['code'] == 2
    assert runtime.max_tool_calls_for('market_focus', model=runtime.SONNET_MODEL) == 0
    assert runtime.uses_claude('claude-opus-5-5') is False
    assert runtime.analysis_identity_supported('claude-opus-5-5', 'xhigh') is False



@pytest.mark.parametrize('text,expected', [
    ('核验说明。 {"answer":"done"}', '{"answer":"done"}'),
    ('草稿 {"answer":"draft"} 最终 {"answer":"done"}', '{"answer":"done"}'),
    ('说明 {"answer":{"nested":{"value":"done"}},"items":[{"value":"inner"}]}', '{"answer":{"nested":{"value":"done"}},"items":[{"value":"inner"}]}'),
    ('还在核验，请等待。', None),
    ('[{"answer":"nested only"}]', None),
])
def test_earnings_recovers_last_complete_json_without_nested_candidates(text, expected):
    assert provider._last_earnings_json(text) == expected


@pytest.mark.parametrize('stop,code', [('max_tokens', 'provider_incomplete_max_output_tokens'), ('refusal', 'provider_refusal')])
def test_complete_looking_json_never_overrides_terminal_stop(monkeypatch, stop, code):
    calls = []
    async def stream(item, *, on_message_start, tool_counts):
        calls.append(item)
        result = message('msg_terminal', stop=stop, text='前文 {"answer":"done"}')
        await on_message_start(result.id)
        return result
    monkeypatch.setattr(provider, 'stream_message', stream)
    receipt = asyncio.run(provider.stream_earnings(prepared(), on_message_start=noop, on_progress=noop)).receipt
    assert len(calls) == 1 and receipt['terminal_error'] == code and receipt['output_text'] == ''
