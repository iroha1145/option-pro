"""News-only metadata translations and source-bound names; no paid requests."""
import json

import pytest

from app.services.ai_jobs import runtime
from app.services.ai_jobs.models import _translate_news_metadata, validate_result, validate_simplified_chinese_text
from test_ai_jobs_zh_contract import _news_payload, _news_result


def check(text, *, source='', field='uncertainty_notes', **payload_changes):
    payload = {**_news_payload(), 'article_status': 'unavailable', 'article_reason': 'no_matching_article_body', 'title': source, **payload_changes}
    result = _news_result()
    result[field] = [text] if field in {'uncertainty_notes', 'affected_sectors'} else text
    return validate_result('news_impact', json.dumps(result), payload)


def test_exact_metadata_is_translated_without_changing_claims_or_numbers():
    text = 'article_status为unavailable且article_reason为no_matching_article_body，article缺失；allowed_tickers为空，affected_stocks留空。已抓取节选，未读全文；预期下降29.51%。'
    result = check(text)
    assert result['uncertainty_notes'] == ['正文状态为不可用且正文缺失原因为未找到匹配正文，新闻正文缺失；允许股票代码名单为空，受影响个股留空。已抓取节选，未读全文；预期下降29.51%。']
    assert result['overall_sentiment'] == _news_result()['overall_sentiment']
    assert result['affected_stocks'] == _news_result()['affected_stocks']


@pytest.mark.parametrize('text', ['输入my_article_status为不可用', '输入article_status_extra为不可用', 'The article is unavailable and stocks are falling', '股票代码allowed_tickers上涨', '股票代码SOX认证上升'])
def test_unknown_tokens_english_prose_and_security_context_stay_rejected(text):
    with pytest.raises(ValueError):
        check(text)


def test_metadata_conversion_does_not_touch_url_or_non_news_validation():
    payload = {'article_status': 'unavailable'}
    url = '来源https://example.com/article_status/unavailable?allowed_tickers=x'
    assert _translate_news_metadata(url, payload) == url
    with pytest.raises(ValueError):
        validate_simplified_chinese_text('正文article_status为unavailable', None)
    # 2026-10-10 口径变更：状态值对不上时照旧不翻译，「unavailable」按词条原样发布（原先被拒）。
    assert check('正文article_status为unavailable', article_status='not_requested')['uncertainty_notes'] == ['正文正文状态为unavailable']


def test_product_series_is_a_term_but_not_a_security_reference():
    assert check('新车V系列将发布', source='The iCAUR V series welcomes a new member', field='title_zh')['title_zh'] == '新车V系列将发布'
    # 2026-10-10 口径变更：来源里没有「V series」时，「V系列」也按词条放行（原先被拒）。
    for source in ['', 'The iCAUR X series', 'The vehicle is V25', 'The AV series']:
        assert check('新车V系列将发布', source=source, field='title_zh')['title_zh'] == '新车V系列将发布'
    for text in ['V股票将上涨', 'V。系列将发布', '股票代码V上涨']:
        with pytest.raises(ValueError):
            check(text, source='The iCAUR V series', field='title_zh')


def test_hyphenated_legal_name_is_source_bound_not_a_ticker_allowlist():
    source = 'M-tron Industries, Inc. (MPTI) declined today'
    assert check('M-tron Industries股价下跌1.68%', source=source, field='title_zh')['title_zh'] == 'M-tron Industries股价下跌1.68%'
    # 2026-10-10 第三轮：名称不是代码样词元，来源不符时接股价也按词条发布（原先被拒）。
    for source in ['', 'M-tron Industries. Inc.', 'Other Industries, Inc.', 'M-tron IndustriesExtra, Inc.']:
        assert check('M-tron Industries股价下跌', source=source, field='title_zh')['title_zh'] == 'M-tron Industries股价下跌'
    with pytest.raises(ValueError):
        check('MPTI股价下跌', source='M-tron Industries, Inc. (MPTI)', field='title_zh')
    with pytest.raises(ValueError):
        check('Company-Beats Earnings Estimates股价上涨', source='Company-Beats Earnings Estimates, Inc.', field='title_zh')


def test_regulation_and_gloss_are_not_general_acronym_exemptions():
    assert check('虚假的SOX认证', source='false Sarbanes-Oxley certifications', field='causal_summary')['causal_summary'] == '虚假的《萨班斯—奥克斯利法案》认证'
    # 2026-10-10 口径变更：没有萨班斯法案来源时照旧不翻译，「SOX」按词条原样发布（原先被拒）。
    assert check('虚假的SOX认证', field='causal_summary')['causal_summary'] == '虚假的SOX认证'
    assert _translate_news_metadata('SOX指数上涨', {'title': 'SOX semiconductor index'}) == 'SOX指数上涨'
    assert _translate_news_metadata('SOX认证', {'title': 'SOX semiconductor index'}) == 'SOX认证'
    assert check('商业发展公司（BDC）', field='affected_sectors')['affected_sectors'] == ['商业发展公司']
    with pytest.raises(ValueError):
        check('BDC股价上涨', field='causal_summary')


def test_identity_and_stock_whitelist_are_unchanged():
    result = _news_result()
    payload = _news_payload()
    result['news_id'] += 1
    with pytest.raises(ValueError, match='news_identity_mismatch'):
        validate_result('news_impact', json.dumps(result), payload)
    result = _news_result()
    with pytest.raises(ValueError, match='news_ticker_binding_mismatch'):
        validate_result('news_impact', json.dumps(result), {**payload, 'allowed_tickers': []})


def test_news_hint_preserves_native_protocol_and_local_contract(tmp_path):
    from test_claude_job_worker import settings
    schema = runtime.build_runtime_request('news_impact', _news_payload()).schema
    enhanced = runtime.claude_output_schema('news_impact', schema)
    assert '不得照抄' in enhanced['properties']['uncertainty_notes']['description']
    assert 'description' not in schema['properties']['uncertainty_notes']
    prepared = runtime.prepare_claude(settings(tmp_path / 'jobs.db'), 'news_impact', _news_payload())
    assert prepared.params['output_config']['format']['type'] == 'json_schema'


def test_paid_news_recovery_preserves_receipt_usage_and_charge(tmp_path, monkeypatch):
    import asyncio
    from app.services.ai_jobs import claude_provider
    from app.services.ai_jobs.repository import AIJobRepository
    from app.tools import recover_ai_schema_results
    from test_claude_job_worker import message, settings

    repo = AIJobRepository(tmp_path / 'jobs.db')
    payload = {**_news_payload(), 'article_status': 'unavailable'}
    version, digest = runtime.schema_identity('news_impact')
    job, _ = repo.create_job(job_type='news_impact', payload=payload, model='claude-haiku-5-5', reasoning='xhigh', execution_mode='background', prompt_version=runtime.PROMPT_VERSIONS['news_impact'], schema_version=version, schema_sha256=digest, max_queued=10)
    ident = job['job_id']
    repo.claim_due('owner', 60)
    repo.mark_submission_started(ident, 'owner', daily_limit=0)
    result = _news_result()
    result['uncertainty_notes'] = ['正文article_status为unavailable，未读到全文。']
    saved = runtime.claude_receipt(message(text=json.dumps(result)))
    repo.record_provider_result(ident, 'owner', saved)
    repo.fail(ident, 'owner', 'schema_validation_failed', usage=saved['usage'])
    before = repo.get_job(ident)
    monkeypatch.setattr(recover_ai_schema_results, 'get_settings', lambda: settings(repo.path))

    async def forbidden(*args, **kwargs):
        raise AssertionError('Recovery must not contact a provider')

    monkeypatch.setattr(runtime, 'retrieve', forbidden)
    monkeypatch.setattr(claude_provider, 'stream_message', forbidden)
    assert asyncio.run(recover_ai_schema_results.recover([ident], apply=True))[0]['status'] == 'recovered'
    after = repo.get_job(ident)
    for key in ['provider_result_json', 'budget_charge_microusd', 'usage_input_tokens', 'usage_output_tokens', 'usage_total_tokens', 'anthropic_message_id', 'payload_json']:
        assert after[key] == before[key]
    assert json.loads(after['result_json'])['uncertainty_notes'] == ['正文正文状态为不可用，未读到全文。']
