# Personal Edition 五阶段交付报告

更新时间：2026-07-16。

本报告记录 Option Pro PR #17、#18、#19、#20 与 News-feed PR #18 的最终叠加复核。五个拉取请求（Pull Request）保持开放、未合并；没有生产部署，也没有调用真实 OpenAI、新闻源或行情源。

## 交付核对表

1. **两仓库起始主分支提交。** Option Pro 为 `d04ef67703316c52279fb020e10278eb7e3e82f5`，News-feed 为 `a5e896d3d248cf658075a91baf9120c94f1d70c4`；最终复核时，两仓库远端 `main` 仍分别指向这两个提交。工作目录中原有的本地 `main` 分别为 `18420ae32c15d7572b20e901027eafaf46d08d03` 与 `a3c9e3fce76c96c0ff84303f566fa2b0e2afd1bb`，它们不是本轮堆叠基线，本轮也没有推送这些本地引用。

2. **五个原始头提交。** Option Pro #17：`1f3773cf9dd2eca46aa148a2041e78a2af1e79f1`；Option Pro #18：`a30944fa0ecd50d31a3d233f4b64259b64945f79`；News-feed #18：`4551f71cf1446814e6069b4ac53731978a661e15`；Option Pro #19：`190d1f0f855526ad2cc5f42d8d3307a69e9c6f08`；Option Pro #20：`11a97b845bfa72952bd2d82ca19ab52168554640`。

3. **五个最终头提交。** Option Pro #17：`54a4b788e4dae7290502cbe481b151463d4543a1`；Option Pro #18：`511e73a7749514ba790eecbbf21bbdb9cd6dac26`；News-feed #18：`b2a77a2b36cf427de37b929c138a18e65e5c1997`；Option Pro #19：`d955c3d64904ffded4565bf5491f78ba60179695`。Option Pro #20 的代码验证头为 `f4f8e58103df628c3bd7203b124263bff734bc62`，本报告是其后的文档提交；最终分支头由 `refs/heads/refactor/personal-runtime-cleanup` 与 [PR #20](https://github.com/iroha1145/option-pro/pull/20) 页面共同确认。

4. **每个拉取请求的基础分支。** Option Pro #17 → `main`；Option Pro #18 → `refactor/personal-edition`；News-feed #18 → `main`；Option Pro #19 → `refactor/personal-worker`；Option Pro #20 → `feat/local-catalyst-ai`。

5. **精确提交列表。** 五个阶段的完整提交哈希和标题列在文末“提交附录”。第 20 号的报告提交因无法在自身内容中预先写入自身哈希，以 `HEAD（本报告）`标识；其父提交为上项所列代码验证头。

6. **堆叠分支更新方式。** 先为旧头与本地待传播头建立远端 `backup/` 引用，再使用 `git rebase --merge --onto` 依次把 #18 建到新 #17、把 #19 建到新 #18、把 #20 建到新 #19。推送 #18、#19、#20 时只使用带旧远端哈希的精确 `--force-with-lease`；每层均先核对远端未出现第三方新提交。

7. **审查线程。** 五个拉取请求共 10 条有效线程：Option Pro #17 四条、#18 一条、#19 两条、#20 一条，News-feed #18 两条。最终交付要求为 10 条全部回复并解决；#20 的旧 `contracts/` 线程只在最终头镜像构建和持续集成（Continuous Integration）通过后关闭。

8. **`private_network` 最终规则。** 只允许本机回环、安全外壳（SSH）转发、RFC1918、Tailscale、WireGuard、运营者明确批准的私网地址和 IPv6 本地地址；禁止通配或公网监听、域名允许列表以及可信代理头。应用启动、部署脚本与 `personal.sh doctor` 共用同一个 Python 校验器。

9. **`password` 最终规则。** 必须存在格式合法的 `APP_PASSWORD_HASH`，必须显式设置 `ALLOWED_HOSTS`，登录请求必须被应用识别为超文本传输安全协议（HTTPS），会话继续使用 `Secure`、`HttpOnly`、`SameSite=Strict` Cookie。

10. **公网反向代理门禁。** `ALLOWED_HOSTS` 只要含域名，就必须设置 `TRUST_PROXY_HEADERS=true`，并配置非空且收窄到实际代理来源的 `TRUSTED_PROXY_CIDRS`。缺少代理信任的“公网域名 + 密码模式”组合不再通过部署校验；反向代理仍须正确传递超文本传输安全协议（HTTPS）信息。

11. **规范密钥（Canonical Secret）列表。** 只有 `OPENAI_API_KEY`、`FINNHUB_API_KEY`、`MARKETDATA_TOKEN`、`INTERNAL_API_TOKEN`、`APP_PASSWORD_HASH` 五项。网页、接口、日志和报告只显示是否配置，不显示值、片段、长度或摘要。

12. **MarketData 迁移语义。** 运行时只读取 `MARKETDATA_TOKEN`；`MARKETDATA_API_TOKEN` 仅由迁移器识别。新旧键同时存在且值不一致时失败关闭，报告只记录冲突键名。

13. **`machine.env` 字段。** 只有 `HOST_BIND`、`PORT`、`MACROLENS_URL`、`ALLOWED_HOSTS`、`TRUST_PROXY_HEADERS`、`TRUSTED_PROXY_CIDRS`、`DATA_DIR` 七项。升级器会从旧 `.env` 与旧 `secrets.env` 恢复机器字段，识别 `MACROLENS_BASE_URL`，并以模板补齐缺失字段；文件权限为 `0600`。

14. **迁移报告字段。** 只有 `mapped_keys`、`deprecated_keys`、`removed_keys`、`conflicting_keys`、`unmapped_keys`、`requires_owner_password`、`warnings`。`removed_keys` 只含键名与 `removed_by_personal_edition` 状态；报告权限为 `0600`。

15. **迁移密钥泄漏扫描。** 迁移与密钥专项 49 项通过；报告断言不含密钥值、网址、长度、摘要或片段。受跟踪文件的密钥形态扫描只命中一个专门验证“不把误贴密钥当键名回显”的合成测试哨兵，生产代码、配置模板、文档和前端均无命中。

16. **PR #17 独立模型预算验证。** #17 全量 Python 790 项通过；模型任务、个人配置与突破工作进程专项 37 项通过。每日任务上限在供应商提交前原子预留，并发争用不能越过上限；未调用真实 OpenAI。

17. **PR #17 独立突破验证。** `breakout_enabled` 以 `personal.toml` 为准，旧环境值不能改变正式行为。慢扫描租约测试连续运行 10 次均通过，随后 #17 两条完整持续集成均成功。

18. **备份精确标签实现。** 备份文件以严格文件名表达式和版本化 Manifest 为入口，核对完整标签、数据库、Manifest、摘要三件套、大小与摘要后才进入删除计划；不再使用前缀 Glob 判定标签归属。

19. **备份保留测试。** 覆盖 `optix`、`optix-worker`、`optix-worker-state` 三组重叠标签、每标签独立保留、损坏 Manifest、不完整三件套、目录级锁与并发保留；不同标签不会互删。

20. **日历持久化语义。** MacroLens 先抓取与规范化，再以 `BEGIN IMMEDIATE` 在一个事务中保存日历快照与来源健康；提交成功后才更新内存缓存。任何写入失败都会回滚，并保留上一份数据库与缓存。

21. **大历史去重实现。** 当前内容哈希、旧版内容哈希与规范化网址按批次分块执行全库精确查询；最近 2000 条只用于模糊标题。5000 条历史、旧哈希、网址、来源证据、变化流和并发幂等均有回归覆盖。

22. **统一工作进程配置加载。** 工作进程入口在导入任务模块前统一加载 `.env`、`machine.env`、`secrets.env`，并把一个 `Settings` 对象注入九类任务。文件归属在统一加载器中执行：七个机器字段不能被 `secrets.env` 覆盖，五个密钥不能被 `machine.env` 覆盖；`BaseSettings` 不再绕过加载器重读三份文件。

23. **队列已满错误映射。** 相同任务先复用已有记录；只有新任务在队列饱和时返回 429、`ai_job_queue_full` 与 60 秒重试时间。数据库、预算、冷却和工作进程错误保持独立，不再把队列已满伪装成缓存或 SQLite 故障。

24. **最终服务数量。** Option Pro 的正式组合配置只有 `backend` 与 `worker`；MacroLens 只有 `macrolens`。两仓库长期业务进程合计 3 个。

25. **最终环境变量数量。** 正式模板合计 12 项：`machine.env` 七项、`secrets.env` 五项；`.env.example` 不含正式字段，只保留一个迁移版本的兼容说明。相较旧模板 189 项减少 177 项，约 93.7%。

26. **最终配置项数量。** `config/personal.toml` 按叶子键统计为 21 项，数组整体算一个键；若逐个展开数组值则为 28 个值。加上 12 个环境字段，运营者可配置入口合计 33 项。

27. **最终删除代码行数。** 统计范围为 Option Pro 起始提交 `d04ef677...` 到代码验证头 `f4f8e581...` 的整个仓库，包含程序、测试和文档：删除 68,467 行，新增 34,015 行，涉及 210 个文件。本报告提交不计入该代码瘦身数字。

28. **Python 测试结果。** Option Pro 最终叠加树 987 项通过、4 条依赖弃用警告；News-feed 最终头 66 项通过；PR #17 独立全量 790 项通过。测试输出未报告失败或跳过项。

29. **Node.js 测试结果。** 最终叠加树 37 项行为测试全部通过；11 个正式前端文件的静态断言通过，旧路径不存在或为空；全部正式 JavaScript 文件通过语法检查。

30. **Playwright 结果。** 34 项全部通过，包括 19 种 Catalyst Desk 状态、键盘焦点、整页重载、故障隔离、真实密码模式与自选页并发场景。

31. **Docker 结果。** 正式镜像构建成功；隔离项目中的 `backend` 与 `worker` 均健康，工作进程严格报告九类任务；两容器用户编号均为 100，根文件系统不可写；本地突破与 Catalyst 夹具通过；两个容器在独立数据卷上正确观察同一 SQLite 预写日志（WAL）事务的提交前后可见性。测试容器、网络和临时卷均已删除。

32. **持续集成链接。** [Option Pro #17 检查](https://github.com/iroha1145/option-pro/pull/17/checks)、[Option Pro #18 检查](https://github.com/iroha1145/option-pro/pull/18/checks)、[News-feed #18 检查](https://github.com/iroha1145/News-feed/pull/18/checks)、[Option Pro #19 检查](https://github.com/iroha1145/option-pro/pull/19/checks)、[Option Pro #20 检查](https://github.com/iroha1145/option-pro/pull/20/checks)。这些链接始终指向各拉取请求当前头提交；最终批准以页面上全部检查成功为准。

33. **浏览器产物。** 本地完整报告位于 `frontend/test-results/report/index.html`，状态截图位于 `frontend/test-results/visual-evidence/`。Option Pro 的持续集成在各个最终头提交上传 `catalyst-desk-visual-evidence-<SHA>`，保留 14 天。

34. **生产部署。** 未执行；没有访问、重启或变更生产服务器，也没有合并任何拉取请求。

35. **模型与简体中文输出。** 正式模型保持 `gpt-5.6-terra`，推理等级为 `max`，模型并发为 1；新闻标题、摘要、等待文案与分析结果必须为简体中文。相关回归均使用本地夹具或模拟传输，没有调用真实 OpenAI、新闻源或行情源。

36. **已知 P2。** 本轮复核范围内没有发现未处理的功能性 P2。测试仍报告两类上游弃用提醒：Starlette `TestClient` 的 `httpx2` 迁移，以及 `HTTP_422_UNPROCESSABLE_ENTITY` 常量替换；它们不影响本轮运行结果，后续可随依赖升级处理。本轮复核范围内未发现未处理的 P0/P1。

37. **未来正式合并顺序。** 先合并 Option Pro #17，再更新并合并 Option Pro #18；随后合并 News-feed #18，确认纯采集接口稳定；再更新并合并 Option Pro #19；最后重新核对并合并 Option Pro #20。每一步都重新检查基础分支、当前头、持续集成与部署前配置，但本轮不执行这些合并和生产发布动作。

## 提交附录

### Option Pro PR #17

```text
6894f17cca29bed66d4391aa43270c2c7ba14f32 refactor: add personal config and retire legacy frontend
b1c8014b5e8cdf4eefefa92dc3daacad2e428277 refactor: replace browser tokens with owner access
1f3773cf9dd2eca46aa148a2041e78a2af1e79f1 test: preserve private-network peer semantics
3eece12265075e2259e153ba491a1875c39b0b3e fix: fail closed for proxied private-network deployments
de97c701bdf28c506863fd8a2c852a77cead575c fix: migrate machine settings and canonical service secrets
08a50c98249c8df41460e9b834f53d5d3accc5e9 fix: enforce the stage-one daily AI job limit
72b50e60c1169e4ab40bae46d3662f0e424c62ce fix: keep breakout behavior aligned with personal configuration
51deb5c971743bb33d5e07827045baf1876cb807 test: keep legacy breakout compatibility deterministic
8269fb9f722331e41b25baa2e8cf3270afa00974 test: preserve breakout worker isolation
2c18addfe6fd258ca8301b03bd796318d4d6ca2f test: align breakout CI smoke with personal configuration
3a361b9e82aac94388f4a99013cad41d568b18e1 fix: require trusted proxy for password domains
54a4b788e4dae7290502cbe481b151463d4543a1 test: harden worker lease timing
```

### Option Pro PR #18

```text
b7d1601a79308aa91797d11f133b4d8a112c702e refactor: consolidate personal background work
3d98d761dfaa707d53cabfa2b7dd74ab174f4f44 test: harden worker lease timing
4c014b68a69e4546853f7857cefc884e94471084 feat: queue manual work in the unified worker
db72a26ea9d2c2c88887ad08847b60bad106342e feat: expose unified worker actions
e39db56ebdfc63e3f4fcdcca58c551986392be2c fix: preserve SQLite backups by exact manifest label
511e73a7749514ba790eecbbf21bbdb9cd6dac26 fix: fail the unified worker when a task loop dies
```

### News-feed PR #18

```text
df79f46698788316e9508f1b67bd0db6dc559e68 refactor: reduce macrolens to etl service
026921496b9ff3182a66eb786cef4974f4e82f25 refactor: align macrolens internal access settings
d650eb083686881c81cb04331bfa63a7f6a57fa2 fix: resolve packaged macrolens config path
bba424f50b9385479f1d2d41a9d97c0de82c9f6e refactor: add server-only macrolens secret management
4551f71cf1446814e6069b4ac53731978a661e15 fix: prepare server secrets for container CI
2f0fd24d73940b51b945593f1a9b51b68e26a6b3 fix: query exact news keys across the full history
b2a77a2b36cf427de37b929c138a18e65e5c1997 fix: commit calendar snapshots and source health atomically
```

### Option Pro PR #19

```text
96ce1abdb2ed0ad5e3294b7124167e47a26f4d9b feat: move catalyst intelligence into option pro
eeb832c238d2400f415d18d7ff25efdfd49ee00e fix: make personal AI config CI-safe
224e945dd4b8d2e0169f355a923b2f38a8760f3b fix: report official Responses SDK capability
8435eafeb4a2dfb9ec7dad4b7eb1f607aafe934d test: remove worker backoff timing race
6e0bbdd1bb485feeccee052140593f5634389383 fix: expose SDK capability in worker health
a49f973a92ee7dbe5300929a835b700115b2d6f7 feat: complete owner catalyst runtime controls
a3cbecbed40789d4d50d4fa9810bc255e00a3141 test: align Catalyst visual evidence with owner controls
3125157abd715adc95961aa98aad7ee9eac5209b fix: load unified worker configuration through settings
d121c18497c89a245943ccc1e7b835150a3f66e0 fix: preserve AI queue and budget error semantics
5f3933320196173026b76c3f5ae3e78cd684756e fix: preserve focus job identity across local commit failures
c943f157b3dbe69c7a4c8209e525ed60bbabc2f5 fix: support unified worker CLI from the repository root
094b8c51cf7507f4a7de4bfbdc1fca485618f41d fix: enforce complete simplified Chinese output gates
0a8627529d7a137cadbca88753b76ca9f3d873d4 test: keep personal container checks fully offline
4c733a4da1b15d600429038f30bb529701bd472a test: align AI safety with unified runtime environment
528a1b04f2d264b568406eae381f3c725a0f56cb test: align offline breakout checks after parent update
28eb31cf1a42862e9ede82739e2b38c50456742a test: verify offline breakout CI override
da8503bc5a3c0e2013ce8d3e69214e84ee9e35da test: run the Catalyst container fixture from the image path
31909becfe7cdc9bb0a32c65ea4300ae1df940be fix: honor selected worker environment files before imports
c41a54a918c35273fa41d018ddbda509e7e1fe03 fix: reject mismatched active focus cycles
d955c3d64904ffded4565bf5491f78ba60179695 test: keep visual failures in simplified Chinese
```

### Option Pro PR #20

```text
5ccc6f7c95fa6c52cffbf95c790a073f86f6ec79 refactor: remove legacy personal runtime chains
b4b3888c4e56eea0e07779e45ad9eeec40d22edd fix: stabilize personal runtime CI
05b1ecc829efeda08aee0179a7efcb8887e1a350 fix: make compose service check order independent
3d14dc7f6d43b248418202c54fc2632cdfbf692f refactor: separate secrets from deployment edges
8db0377ef72b7efe24b649ed8e89d67a304fc845 refactor: finish personal owner runtime cleanup
6d203604ad56daac9f49e5b458fc3f7091731cc2 fix: align offline CI with manual catalyst mode
1f397597ef183a0c497b85fa49536519b10838ea fix: verify the complete worker v2 inventory
b55760a1bbf1dbf528c1cb1e321e2784df362d7f fix: align final runtime configuration boundaries
f4f8e58103df628c3bd7203b124263bff734bc62 docs: clarify personal runtime configuration
HEAD docs: record final personal edition delivery report
```
