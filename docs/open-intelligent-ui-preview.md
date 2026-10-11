# 市场报告：图文版

首页「市场报告」有图表数据时，结论和四段正文显示为图文版；没有图表数据时显示原来的文字版。
没有开关，也不需要网址参数。图表由程序按该次报告的证据数据绘制，不调用模型、不改提示词、
不增加费用。上游来源与许可见 [归属说明](open-intelligent-ui-attribution.md)。

## 数据从哪里来

`backend/app/services/market_brief/visuals.py` 在读取 `GET /api/market-brief/latest` 时，
从该次运行记录（`runs/*.json`）里的证据包现算 `brief.visuals`。证据包本来就随运行记录保存，
所以部署前生成的报告也能画。一共四张图：

- 美股指数涨跌幅：标普 500、纳指综合、道琼斯，证据里有罗素 2000 时一起画。日经、上证不画。
  图注写数据时点（纽约时间）；快照不新鲜时写明。
- 行业广度：11 只行业 ETF 里站上 50 日均线的只数，分子和分母照证据原值。
- 主题一个月超额收益：只画报告正文引用过的主题（`theme:` 证据编号），单位是相对标普 500 的百分点。
- 宏观分项评分：0–100 分。宏观数据截止日期早于报告交易日时，图下加一行提醒。

接口只给标签、数字和日期，不给证据原文或新闻文字。某一块缺失或数值不合格，那张图就是
`null`，不补值；四张都画不出来时 `visuals` 是 `null`。前端 `api/modules/marketBrief.ts`
再校验一遍：数值必须有限并在合理范围内，广度必须是整数且分子不超过分母。运行记录被清理后
`visuals` 会变成 `null`，所以接口字节缓存的版本键也包含 `visuals`。

## 怎么显示

- `frontend-src/src/components/home/marketBriefReport.ts` 拼出静态 HTML 和 CSS，沿用 PR #253 的完成态
  协议（`open-intelligent-ui/schema.ts`）：只有 `html` 和 `css`，`jsFunctions` 为空、`jsExpressions` 为空数组。
  协议校验不通过就显示文字版。
- 模型写的正文全部转义后原样放入，不翻译；界面文字和程序给的标签（指数、主题、宏观分项名）经 `t()`，
  有英文和日文译文。
- `MarketBriefModelOutput.tsx` 在 `useLayoutEffect` 里把内容写进 Shadow DOM，和卡片其他部分同一帧排版。
  同一份内容轮询回来不重写，展开的明细不会被收起。
- 颜色读页面的 CSS 变量。深色、浅色和涨跌配色一切换就生效，不用重新生成内容。

## 为什么不用 PR #253 的 iframe

1. 生产页面的 CSP（`backend/app/main.py` 的 `script-src 'self'`）会被 `srcdoc` 沙箱框架继承。
   用真实 Chrome 按生产 CSP 测试：Websandbox 的内联启动脚本被拦下，框架始终没有初始化。
   原实现会先显示文字版，15 秒后再出现一行失败提示。要让 iframe 工作只能放宽全站 CSP，这次不做。
2. iframe 版先显示文字，框架就绪后再替换，高度会变。主分支 `f8bd6821` 模拟模式实测（每种情况 3 次）：
   报告出现后，卡片高度还要再变 1 到 2 次，桌面 2099→1544→2561 像素，390 像素手机 5323→1713 像素，
   320 像素手机 6359→1763 像素。
   Shadow DOM 版在 1440、390、320 像素，浅色和深色共 18 次加载中都是 0 次。

PR #253 的 Websandbox 渲染器（`renderer.tsx`、`frame-content.ts`、`websandbox-loader.ts`）和 `@jetbrains/websandbox` 依赖已经移除。
`components/open-intelligent-ui/` 里只剩 `schema.ts`，即完成态协议和它的校验。

## 本地预览

```sh
cd frontend-src
npm ci
VITE_API_MODE=mock npm run dev -- --host 127.0.0.1 --port 5217 --strictPort
```

打开 <http://127.0.0.1:5217/>。模拟接口返回带示例图表数据的报告，图文版顶部标明「演示数据」。

## 验证

```sh
node --experimental-strip-types --test frontend-src/tests/market-brief-report.test.mjs frontend-src/tests/market-brief-mock-contract.test.mjs frontend-src/tests/market-brief-contract.test.mjs frontend-src/tests/open-intelligent-ui.test.mjs
.venv/bin/python -m pytest tests/test_market_brief_visuals.py tests/test_market_brief_store.py tests/test_market_brief_api.py -q
```

接口样例 `tests/fixtures/market_brief_sample.json` 里的 `visuals` 是三方共用的契约：后端测试用同形的
证据包算出同一份 `visuals`，前端模拟数据与样例逐字段一致。

## 已知限制

- 浏览器里还存着部署前那份报告（没有 `visuals`）时，首屏先按旧数据显示文字版，网络结果回来后换成
  图文版。每个浏览器只会发生一次。
- 行业隐含波动率、突破雷达等其他证据没有画图。
- 卡片从加载骨架换成正文时的位移是原有行为：骨架至少撑到首屏以下，位移发生在视口外。
