import test from 'node:test';
import assert from 'node:assert/strict';
import { aiModelLabel } from '../src/lib/aiModelLabel.ts';
import { setLocale } from '../src/i18n/testing.ts';
import { aiJobKnownErrorMessage } from '../src/api/aiJobNormalize.ts';

test('actual task model and reasoning determine the label', () => {
  assert.equal(aiModelLabel('claude-haiku-5-5', 'xhigh'), 'Claude Haiku 5.5 · xhigh');
  assert.equal(aiModelLabel('gpt-5.6-terra', 'max'), 'GPT-5.6 Terra · max');
  assert.equal(aiModelLabel('historical-provider-model', 'high'), 'historical-provider-model · high');
  assert.equal(aiModelLabel('gpt-5.6-terra'), 'GPT-5.6 Terra');
  assert.equal(aiModelLabel(null, 'xhigh'), null);
  assert.equal(aiModelLabel('', 'xhigh'), null);
});

test('Claude empty output and model mismatch have readable failure reasons', () => {
  assert.ok(aiJobKnownErrorMessage('provider_empty_response'));
  assert.ok(aiJobKnownErrorMessage('provider_model_mismatch'));
});


test('new provider failure explanations have English and Japanese translations', () => {
  try {
    for (const locale of ['en', 'ja']) {
      setLocale(locale);
      assert.notEqual(aiJobKnownErrorMessage('provider_model_mismatch'), '模型服务返回的模型与分析设置不符，请检查服务配置');
      assert.notEqual(aiJobKnownErrorMessage('provider_empty_response'), '模型没有返回内容，请重试');
    }
  } finally { setLocale('zh'); }
});
