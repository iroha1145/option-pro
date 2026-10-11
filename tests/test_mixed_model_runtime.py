from types import SimpleNamespace
from pydantic import SecretStr
import pytest
from app.services.ai_jobs import runtime, claude_provider


def settings(**kw):
    return SimpleNamespace(**dict(openai_model='claude-haiku-5-5', openai_reasoning='xhigh', openai_max_concurrency=4, openai_execution_mode='background', openai_daily_token_limit=10_000_000, anthropic_api_key=SecretStr('test'), openai_timeout_seconds=900, **kw))


def test_routes_are_per_task_and_do_not_mutate_settings():
    original = settings(openai_news_model='gpt-5.6-luna', openai_news_reasoning='max', openai_market_focus_model='claude-sonnet-5-5', openai_market_focus_reasoning='xhigh')
    assert runtime.model_identity_for_job(original, 'news_impact') == ('gpt-5.6-luna', 'max')
    assert runtime.model_identity_for_job(original, 'market_focus') == ('claude-sonnet-5-5', 'xhigh')
    assert runtime.model_identity_for_job(original, 'earnings_impact') == ('claude-haiku-5-5', 'xhigh')
    selected = runtime.settings_for_job(original, 'news_impact')
    assert runtime.runtime_configuration_valid(selected)
    assert original.openai_model == 'claude-haiku-5-5'


def test_sonnet_schema_and_tools_are_separate_from_historical_haiku():
    old = runtime.schema_identity('market_focus', model='claude-haiku-5-5')
    new = runtime.schema_identity('market_focus', model='claude-sonnet-5-5')
    assert old != new
    tools = runtime.claude_tools_for('market_focus', {}, model='claude-sonnet-5-5')
    assert {t['name']: t.get('max_uses') for t in tools} == {'web_search':12,'web_fetch':12,'code_execution':None}
    assert tools[-1]['type'] == 'code_execution_20260521'
    assert runtime.claude_tools_for('market_focus', {})[0]['max_uses'] == 1
    assert 'event_verifications' in runtime.build_runtime_request('market_focus', {}, model='claude-sonnet-5-5').schema['properties']


def test_transport_uses_selected_identity():
    chosen = runtime.settings_for_job(settings(openai_market_focus_model='claude-sonnet-5-5'), 'market_focus')
    result = claude_provider.prepare_message(chosen, instructions='test', input_text='test', schema={'type':'object','properties':{},'required':[],'additionalProperties':False}, max_tokens=100)
    assert result.params['model'] == 'claude-sonnet-5-5'
    assert result.params['output_config']['effort'] == 'xhigh'


@pytest.mark.parametrize('tokens,expected', [(100_000,145_000),(300_000,330_000)])
def test_luna_short_and_long_price(tokens, expected):
    usage={'input_tokens':tokens,'cached_input_tokens':0,'output_tokens':100_000}
    assert runtime.settled_usage_cost_microusd('news_impact', usage, fallback_microusd=10_000_000, model='gpt-5.6-luna') == expected


def test_sonnet_has_no_long_context_multiplier():
    usage={'input_tokens':300_000,'cached_input_tokens':0,'cache_creation_input_tokens':0,'cache_creation_5m_input_tokens':0,'cache_creation_1h_input_tokens':0,'output_tokens':100_000,'web_search_requests':2}
    assert runtime.settled_usage_cost_microusd('market_focus',usage,fallback_microusd=9_000_000,model='claude-sonnet-5-5') == 1_620_000


# 官方定价页（platform.claude.com/docs/en/about-claude/pricing，2026-10-11 核对）：Sonnet 5.5 输入 2、
# 5 分钟写入 2.50、1 小时写入 4、缓存命中 0.10、输出 10 美元每百万 tokens。Sonnet 5.5 与 Opus 5.5 的
# 缓存命中按输入价的 0.05 倍计，Sonnet 5 和其余型号是 0.1 倍，所以读缓存是 0.10 美元而不是 0.20 美元。
def _sonnet_usage(*, ordinary=0, cached=0, five_minute=0, one_hour=0, output=0):
    writes = five_minute + one_hour
    return {
        'input_tokens': ordinary + cached + writes, 'cached_input_tokens': cached,
        'cache_creation_input_tokens': writes, 'cache_creation_5m_input_tokens': five_minute,
        'cache_creation_1h_input_tokens': one_hour, 'output_tokens': output, 'web_search_requests': 0,
    }


@pytest.mark.parametrize('usage,expected', [
    (_sonnet_usage(ordinary=1_000_000), 2_000_000),
    (_sonnet_usage(cached=1_000_000), 100_000),
    (_sonnet_usage(five_minute=1_000_000), 2_500_000),
    (_sonnet_usage(one_hour=1_000_000), 4_000_000),
    (_sonnet_usage(output=1_000_000), 10_000_000),
    (_sonnet_usage(ordinary=1_000_000, cached=1_000_000, five_minute=1_000_000, one_hour=1_000_000, output=1_000_000), 18_600_000),
], ids=['input', 'cache_read', 'write_5m', 'write_1h', 'output', 'all_five'])
def test_sonnet_settled_cost_follows_the_official_price_card(usage, expected):
    assert runtime.settled_usage_cost_microusd('market_focus', usage, fallback_microusd=1, model='claude-sonnet-5-5') == expected


@pytest.mark.parametrize('model,expected', [('gpt-5.6-luna', 7_300), ('gpt-5.6-terra', 91_250)])
def test_known_openai_usage_is_not_capped_by_a_small_reservation(model, expected):
    usage = {'input_tokens': 10_000, 'cached_input_tokens': 0, 'output_tokens': 4_000}
    assert runtime.settled_usage_cost_microusd(
        'news_impact', usage, fallback_microusd=1_000, model=model,
    ) == expected


def test_luna_submission_keeps_selected_identity_and_requires_missing_body_search():
    selected = runtime.settings_for_job(settings(openai_news_model='gpt-5.6-luna'), 'news_impact')
    payload = {'news_id':1,'change_sequence':1,'content_hash':'a'*64,'allowed_tickers':[]}
    params = runtime._create_params(selected, 'news_impact', payload)
    assert params['model']=='gpt-5.6-luna'
    assert params['reasoning']=={'effort':'max'}
    assert params['tools'] == [{
        'type':'web_search','search_context_size':'low','external_web_access':True,
    }]
    assert params['tool_choice'] == 'required'
    assert params['max_tool_calls'] == 1
    assert params['include'] == ['web_search_call.action.sources']


@pytest.mark.parametrize('returned_model',[None,'gpt-5.6-terra','gpt-5.6-luna-unverified-snapshot'])
def test_luna_actual_response_model_must_match_paid_job(tmp_path,monkeypatch,returned_model):
    import asyncio
    from app.services.ai_jobs import worker
    from app.services.ai_jobs.repository import AIJobRepository
    from test_claude_job_worker import settings as base_settings
    repo=AIJobRepository(tmp_path/'jobs.db')
    payload={'news_id':1,'change_sequence':1,'content_hash':'a'*64,'allowed_tickers':[]}
    version,digest=runtime.schema_identity('news_impact',model=runtime.LUNA_MODEL)
    job,_=repo.create_job(job_type='news_impact',payload=payload,model=runtime.LUNA_MODEL,reasoning='max',
        execution_mode='background',prompt_version=runtime.PROMPT_VERSIONS['news_impact'],schema_version=version,schema_sha256=digest,max_queued=10)
    config=base_settings(repo.path).model_copy(update={'openai_news_model':runtime.LUNA_MODEL,'openai_news_reasoning':'max'})
    calls=[]
    async def submit(*args,**kwargs):
        calls.append(True)
        return SimpleNamespace(id='resp_paid_luna',status='completed',model=returned_model,output=[],
            usage=SimpleNamespace(input_tokens=100,output_tokens=200,total_tokens=300,input_tokens_details=SimpleNamespace(cached_tokens=0)))
    monkeypatch.setattr(runtime,'prepare_background',lambda *args: object())
    monkeypatch.setattr(runtime,'submit_background',submit)
    asyncio.run(worker.run_once(repo,config,'owner'))
    row=repo.get_job(job['job_id'])
    assert row['error_code']=='provider_model_mismatch'
    assert row['openai_response_id']=='resp_paid_luna' and row['usage_total_tokens']==300
    asyncio.run(worker.run_once(repo,config,'restart'))
    assert calls==[True]
