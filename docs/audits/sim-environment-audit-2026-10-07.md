# 模拟环境前后端审计（2026-10-07）

基准：`origin/main` `63ab9bdf`（修复首页异动未随登录账户切换个人自选）。

模拟环境：`VITE_API_MODE=mock` 的 Vite 开发服务器（`http://127.0.0.1:3000`），确定性 fixture，不连接外部行情。浏览器走本机 Chrome，桌面 1440×900 与手机 390×844，语言覆盖简体中文、English。身份预取仍会打到未启动的本机后端，控制台出现一次 `/api/access/status` 500；mock 身份不消费这次预取，页面照常挂载。

## 已确认并修复

| 问题 | 证据 | 修复 |
| --- | --- | --- |
| 英文界面指数名「费城半导体」未走词典 | fixture 把 SOX 名称写成裸中文；英文首页与大盘页直接显示中文 | `getIndices` 改为 `__t('费城半导体')`，英文显示 PHLX Semiconductor |
| 大盘时段说明停在中文 | mock 的 `phase` 是中文 msgid，`PHASE_LABEL` 只认 `premarket` 等键，未命中就原样渲染 | 未命中时改走 `t(phase)`。英文大盘页为 Pre-market · 4:00–9:30 ET |
| 选股「更多筛选」里的板块名停在中文 | mock 板块字典为空，选项直接用扫描行里的中文板块名 | 回退选项的展示名走 `__t`。展开后为 Information Technology、Semiconductors 等 |
| 英文雷达行「AMD AMD」 | 「超威半导体」的英/日译文都是 AMD，和代码并列 | 译文改为 Advanced Micro Devices / アドバンスト・マイクロ・デバイセズ |
| 英文形态「Volume surge」被截成 Volume s… | 雷达行形态列只有 5rem，中文两字够用，英文标签不够 | 形态列改为 7.5rem，时间列 5.5rem；公司名和形态补 `title` |
| 通知压住指数条和演示条 | 通知只按顶栏高度下移。实测桌面通知顶在 72px，指数条 65–101、演示条 101–137，重叠面积约 9000px² | 按仍留在视口里的顶栏、指数条、演示条底边定位。修复后通知顶 145、演示条底 137，重叠面积 0 |
| 「延迟行情」盖住右侧报价并吃掉点击 | 标签 `absolute` 叠在跑马灯上，`elementFromPoint` 命中标签本身 | 标签改为独立一列，`pointer-events-none`；滚动区右缘只留淡出。视口右缘与标签左缘相接，不再叠住报价 |
| 下次扫描倒计时在非法时间上会变成 NaN:NaN | `Math.max(0, NaN)` 仍是 NaN，再 `padStart` | 非法时间显示「—」。单测覆盖过期、倒计和非法字符串 |
| 指数卡英文长名被截断后无法悬停看全称 | PHLX Semiconductor 在窄卡里显示为 PHLX Semiconduct… | 名称加上 `title` |
| 没有调用方的 `HatchLegend` | 全库无 import；财报图例已内联在 `EpsHatchChart` | 删除组件，并改掉 README 与图表注释里的过时说法 |
| 手机键盘焦点会滚进底部 Dock 下面 | Dock 为 `fixed`，文档没有 `scroll-padding-bottom` | 小于 1280px 时为 `html` 增加约 5.75rem 的滚动留白 |

手机宽度下，选股「查询诊断」在首屏会被 Dock 挡住一截；`scrollIntoView` 之后按钮在 y=354，Dock 在 y=768，可以滚出来。底部导航盖住当前视口最下一行是悬浮条的常态，页脚已有 6rem 留白。

## 查过、未改

- **新闻与催化正文保持中文。** 词典明确不翻译模拟新闻、焦点周期和模型正文。英文界面里这些段落仍是中文，和「模型正文原样保留」的约定一致。
- **`theme-boot.js` 在 mock 下仍预取 `/api/access/status`。** 后端没开时控制台 500。正式模式需要这次预取，现有契约测试锁着这段脚本，没有为 mock 关掉。
- **首页、自选的时段灯在页头和状态卡各出现一次。** 一个是页头摘要，一个是状态卡正文，不是叠层。
- **页脚 `OPTIX PRO · PAPER TERMINAL v2`。** 品牌锁字，不进词典。
- **研究包死代码。** `backend/app/services/research_eod_v1/runs.py` 没有任何 import。同包里还有约 38 个零引用函数（`m3_diversified`、`m4_regime`、`parent_pool`、`assemble_unranked_factors` 等），生产选股走 `eod_limited`，不引用它们。这是研究协议残留，不是这次模拟界面能看到的故障，没有在本次删除。
- **前端导出。** `frontend-src/src` 里除已删除的 `HatchLegend` 外，没有全库零引用的导出函数。五个 mapper（如 `mapCtaTrend`）只在本文件里被 API 对象使用，仍然是活代码。

## 复核

- `node --experimental-strip-types --test`：倒计时、跑马灯、i18n 词典同步与覆盖率、主题脚本，28 项通过。
- 英文首页无可见中文界面词；标题为 Optix Pro — Equity Research Desk。
- 中文首页指数、雷达与数据覆盖在 fixture 返回后为 13/13，无脚本异常。
