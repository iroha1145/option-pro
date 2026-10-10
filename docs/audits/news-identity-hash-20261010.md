# 新闻身份只比对编号与版本（2026-10-10）

分支 `claude/news-identity-hash-2026-10-10`，基于 main `2a5f7d36`。单独成文，没有追加到 `ai-analysis-fixes-20261010.md` 末尾：另外两个未合并的分支（#244、#247）也在往那份文档末尾追加。

## 起因

2026-10-03 起，约 13 条 Luna 新闻分析因 `news_identity_mismatch` 失败（协调方统计）。第三轮（PR #242）诊断过其中 4 条，样本在 `tests/fixtures/ai_round3_failures_20261010.json` 的 `identity_mismatch` 组：

- 4 条的 news_id 和 change_sequence 都和载荷一致。
- content_hash 全是模型抄错：载荷是 64 位十六进制摘要，模型写成 62 位、65 位、62 位，最后一条长度对但改错了 1 位。

比对规则本身没问题，问题是要求模型逐字抄回一个 64 位摘要并不可靠。

## 改动

- `backend/app/services/ai_jobs/models.py` 的 `validate_result`（新闻）：
  - 身份只比对 news_id 与 change_sequence，不一致仍报 `news_identity_mismatch`。
  - content_hash 一律改用载荷里的值（去掉首尾空白，与原来的比对口径相同）。
- 提示词保留「news_id、change_sequence和content_hash必须原样复制」，只是不再校验 content_hash。没有删这句：改提示词会移动任务身份。结构（content_hash 仍是必填字段）和任务身份都不变，有测试钉住。
- `backend/app/services/ai_jobs/repository.py` 的 `RECOVERABLE_FAILURE_CODES` 加入 `news_identity_mismatch`。找回工具（`backend/app/tools/recover_ai_schema_results.py`）和 `recover_schema_validation_failure` 因此会选中这些行，按现行规则重验后落库；不重新提交，用量和费用不变。工具的说明文字补上了这一类。

## 影响

- 落库的结果带载荷的摘要，读取时重验也一样。引擎发布分析时的身份比对（`local_intelligence._news_result_identity_matches`）照常通过，新闻流里能看到分析。
- 付费回执原样保留模型写的内容。
- 库里若有摘要与载荷不同、但编号和版本都对的结果（正常完成路径不会产生），读取时也会改用载荷摘要并正常发布；编号或版本不对的仍然隐藏。
- 搜过 `ai_jobs` 与 `catalysts` 两个目录：除上面两处，没有别的地方拿结果里的 content_hash 做比对。

## 部署后找回

1. 先看有多少行（只读 SQL）：

   ```sql
   SELECT model, COUNT(*) AS failed, SUM(provider_result_json IS NOT NULL) AS with_receipt
   FROM ai_jobs
   WHERE job_type = 'news_impact' AND status = 'failed'
     AND error_code = 'news_identity_mismatch'
     AND updated_at >= '2026-10-03T00:00:00Z'
   GROUP BY model;
   ```

2. 试运行（只读），命令写法与 `ai-analysis-fixes-20261010.md` 的「部署后操作」相同：

   ```
   docker exec option-pro-backend-1 \
     python -m app.tools.recover_ai_schema_results \
     --failed-since 2026-10-03T00:00:00Z --job-type news_impact > /tmp/ai-recover-identity-dry.json
   ```

   `--failed-since` 也会选中同一时间窗里其他可找回的失败码（如 `schema_validation_failed`），按现行规则一起重验。只想处理这一类时，把第 1 步查出的 job_id 逐个用 `--job-id` 传入。
3. 确认后加 `--apply`。
4. 本地没存回执的行不会被 `--failed-since` 选中，要用 `--job-id` 逐条找回。这会向供应商取回已付费的响应（不重新提交）；响应过了供应商的保留期就取不回了。

## 测试

- 新增 `tests/test_news_identity_hash_20261010.py`，15 项：
  - 摘要漏两位、多一位、改错一位或完全不同，都改用载荷的值，并能通过引擎的身份比对。
  - news_id 或 change_sequence 不一致仍报 `news_identity_mismatch`，摘要对或错都一样。
  - 提示词仍要求原样复制；新闻任务身份（Luna、Terra）不变。
  - 4 条生产回执经 worker 完成路径入库成功，库里的摘要是载荷值，回执保持原样。
  - 找回工具：4 条以 `news_identity_mismatch` 失败的行，试运行全部 validated，`--apply` 全部 recovered，用量、费用、回执不变；再跑一次没有可选的行。
  - 引擎：失败时新闻流里没有分析；找回后重新整理，新闻流里出现分析。
- 改动的既有测试：
  - `test_ai_jobs_zh_contract.py::test_news_result_is_bound_to_the_exact_local_revision` 去掉 content_hash 这一组参数（news_id、change_sequence 两组照旧要求报错）。
  - `test_ai_prose_round3_20261010.py::test_identity_mismatch_is_a_mistyped_content_hash` 的 4 条样本从「仍被拒」改为「通过且摘要为载荷值」，模块说明同步更新。
  - `test_catalyst_local_intelligence.py::test_projection_requires_exact_identity_and_simplified_chinese` 里代表「绑错版本」的那条结果，原来只把摘要写错，现在改成 change_sequence 写错。按新规则，只有摘要写错的结果会改用载荷摘要并正常发布；身份绑定要靠编号和版本来测。
- 变异实验 5 个全部被抓住，每次都核对源文件已复原：重新比对摘要、保留模型写的摘要、不列为可找回、不比对 news_id、不比对 change_sequence。
- 完整后端测试：6,917 项通过，7 项跳过，7 个子测试通过，pytest 退出码 0。第一次跑全量时 `test_projection_requires_exact_identity_and_simplified_chinese` 失败（见上），改正后重跑。
- `python -m compileall -q backend/app` 通过；`git diff --check origin/main...HEAD -- . ':(top,exclude)frontend'` 干净。

## 未覆盖

- 生产上的找回没有跑，要部署后执行。约 13 条的数字来自协调方，我只核对过其中 4 条样本。
- 模型把 news_id 或 change_sequence 抄错时仍会失败，这是有意保留的。
- 提示词仍要求原样复制摘要。以后若删掉这句，要同时升新闻的任务身份。
