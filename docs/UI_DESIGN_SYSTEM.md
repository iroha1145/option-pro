# 界面与动效规范

核验日期：2026-09-05。适用于研究概览、自选、筛选器、个股详情与移动端。本文说明共享组件的使用规则与验收标准；测试结果以本次审查报告为准。产品定位沿用根目录 `.impeccable.md`：冷静、清楚的市场研究工作台，持续说明行情时间、来源与降级状态。

## 1. 统一实现入口

沿用 React 19、Vite 7、Tailwind CSS 3.4、Framer Motion 12 与 ECharts 体系。五个参考站提供设计与源码依据，不要求同时安装五套组件，也不因此整体升级样式框架或替换图表。依赖安全更新单独核验兼容性。

| 内容 | 项目入口 | 规则 |
| --- | --- | --- |
| 颜色、字体、圆角、间距 | `frontend-src/src/index.css`、`frontend-src/tailwind.config.js` | 同名颜色保持一致；页面不得另建品牌色或涨跌色 |
| 过渡参数 | `frontend-src/src/styles/transitions-root.css` | 打开、关闭、提示、内容显现按用途选参数 |
| 过渡样式与适配 | `frontend-src/src/styles/transitions-catalog.css` | 保留来源样式，项目适配集中书写 |
| JavaScript 动效 | `frontend-src/src/lib/motion.ts`、`frontend-src/src/lib/transitions.ts` | 共享参数与计时读取，不在调用处随意新增时长 |
| 选择与提示 | `FilterButton`、`Segmented`、`GlidePill`、`MenuSelect`、`InfoHint` | 复用键盘行为、定位与减少动态效果处理 |
| 信息展示 | `DataTable`、`InsightCard`、`StatCard`、`SourceNote` | 信息结构和数据口径在页面间一致 |
| 浮层与反馈 | `Drawer`、`ConfirmDialog`、`Toast`、`CommandPalette` | 统一焦点、关闭、背景滚动与状态表达 |
| 加载与异常 | `SkeletonReveal`、`EmptyState`、`InlineFallback`、`StaleStrip` | 加载、空结果、错误和旧数据分别呈现 |

源码注释中的历史 `design.md` 指向旧规范；后续变更以本文、`.impeccable.md` 与上述实际入口共同核对，避免引用不存在的设计文件。

## 2. 五个来源的具体用法

### Beautiful UI：信息层次与搜索反馈

采用其洞察卡片（Insight Cards）的组织方式：标题和口径在上，图形居中，读数、变化与比较基准相邻。现有 `InsightCard`、`InsightFrame`、`InsightValue` 和 `Sparkline` 承接此规则。搜索沿用其内嵌空态、清除按钮与轻量淡入方式，对应 `CommandPalette`。

价格和涨跌必须说明比较基准；来源和时间不藏在悬停操作里。卡片布局用来解释数据，不增加虚构置信度、示例值或自动推断出的交易建议。

核验来源：[官方组件展示](https://www.beautifului.dev/)、[洞察卡片源码](https://github.com/slev12397/beautiful-ui/blob/06557d7ff33a1eb70d5987bae9ac4c70fa0e20c4/components/primitives/InsightCards.tsx)、[搜索源码](https://github.com/slev12397/beautiful-ui/blob/06557d7ff33a1eb70d5987bae9ac4c70fa0e20c4/components/primitives/SearchList.tsx)。官方仓库没有正式组件注册源；不要使用名称相似的第三方注册源冒充官方。其原始样式依赖 Tailwind CSS 4 的 `@theme inline`，现有项目采用设计模式与本地组件适配，不整份覆盖全局样式。

### beUI：选择控件的连续反馈

采用标签页（Tabs）的共享位置指示器、按钮按压反馈与触控区分。现有 `Segmented`、`GlidePill`、主导航和主按钮继续复用。选中项的文字必须即时可读，滑块位于文字下方，且不能遮挡相邻标签。

滑块动画保持项目已验证的参数。当前官网 `SPRING_LAYOUT` 为 `stiffness: 360`、`damping: 32`、`mass: 0.6`，与项目历史参数不同；项目参数属于适配值，不能标成当前官方原值。仅在实际体验与回归检查支持时调整弹性，不因官网更新机械同步。

核验来源：[官方注册目录](https://beui.dev/r/registry.json)、[标签页注册项](https://beui.dev/r/tabs.json)、[按钮注册项](https://beui.dev/r/button-base.json)、[官方动效参数](https://github.com/starc007/ui-components/blob/04d6f76e9e67e35cded996b1b8d08a5ddcebc13a/lib/ease.ts)。需要新增源码时先检查注册项，再使用 `npx shadcn@latest view @beui/<组件名>`；安装前核对对现有工具函数和全局样式的影响。已有 Framer Motion，不并行引入另一套动画运行依赖。

### Rare UI：数值与滚动的无障碍处理

采用动态计数器（Animated Counter）的两项约束：初次呈现显示真实数值；若后续数值有动画，读屏文本立即使用最终值，装饰动画层设为 `aria-hidden`。减少动态效果时直接显示目标值，不进行数字滚动、缩放或位移。

这套处理适配 `StatCard` 与 `useCountUp`。金融价格、失效位置、风险限额不能从零数起，也不能用过渡中的插值作为事实。缺失或非有限数值显示缺失状态，不能照搬上游计数器将非有限值转成零的兜底逻辑。

滚动进度（Scroll Progress）的适用原则用于现有横向滚动容器：位置提示来自真实滚动范围，减少动态效果时使用即时滚动，保留可聚焦的操作按钮。无需为了引入来源增加常驻悬浮菜单。

核验来源：[计数器源码](https://github.com/swamimalode07/rare-ui/blob/b3efd6c290884a852b7af39d34df99a762dbbf3f/components/ui/animated-counter.tsx)、[滚动进度文档](https://www.rareui.com/components/scrollprogressindicator)、[滚动进度源码](https://github.com/swamimalode07/rare-ui/blob/b3efd6c290884a852b7af39d34df99a762dbbf3f/components/ui/scroll-progress.tsx)。官方可复制入口为 `swamimalode07/rare-ui/animated-counter` 与 `swamimalode07/rare-ui/scroll-progress`；本项目复用其合适的行为模式，不需要整套安装。

### Transitions.dev：按用途组织动效

现有 `transitions-root.css`、`transitions-catalog.css` 和 `lib/transitions.ts` 已承担菜单、弹窗、抽屉、骨架与提示的动画。继续围绕这些入口修正动画，不额外叠加页面级动画。

| 用途 | 标准 | 对应位置 |
| --- | --- | --- |
| 菜单、弹窗打开 | 250 毫秒 | `--dropdown-open-dur`、`--modal-open-dur` |
| 菜单、弹窗关闭 | 150 毫秒 | `--dropdown-close-dur`、`--modal-close-dur` |
| 抽屉打开、关闭 | 400 / 350 毫秒 | `--panel-open-dur`、`--panel-close-dur` |
| 骨架切换到内容 | 400 毫秒 | `--reveal-dur` |
| 标签指示器 | 共享滑块或 250 毫秒过渡，单一选择 | `GlidePill` 或 `--tabs-dur` |
| 提示出现 | 150 毫秒，允许短暂延迟 | `--tt-in-dur`、`--tt-delay` |

一般位置变化使用 `cubic-bezier(0.22, 1, 0.36, 1)`，图表和数据排序不要添加明显弹跳。打开和关闭时长分别保留，计时器读取对应变量；减少动态效果时同时取消视觉过渡与等待，不能留下一层透明但仍挡住操作的浮层。

优先改变位移、缩放和透明度。整张数据表不要交叉模糊或逐行错峰出现；刷新也不应反复触发首屏入场。骨架与内容保留相近空间，避免图表因换父节点反复初始化。

核验来源：[官方动效说明](https://transitions.dev/skill.html)、[参数与组件索引](https://github.com/Jakubantalik/transitions.dev/blob/74e572345d809f981250938208bd991314c2e780/skills/transitions-dev/SKILL.md)、[菜单配方](https://github.com/Jakubantalik/transitions.dev/blob/74e572345d809f981250938208bd991314c2e780/skills/transitions-dev/05-menu-dropdown.md)、[骨架配方](https://github.com/Jakubantalik/transitions.dev/blob/74e572345d809f981250938208bd991314c2e780/skills/transitions-dev/14-skeleton-reveal.md)。

### shadcn/ui：语义颜色与组件行为

采用背景、前景、主操作、弱化内容、危险操作、边框与焦点等语义变量，映射到项目既有纸面色系。普通卡片边线与输入边框需区分用途；文字和边框不能只因颜色名字接近就互换。

弹窗采用独立标题、说明和操作区；背景内容在弹窗期间不可操作，焦点进入浮层并在关闭后回到触发按钮。表格保留原生表头、行和单元格语义，排序通过按钮和 `aria-sort` 表达。标签页仅用于确有对应内容面板的切换；筛选值选择应使用合适的选择语义。

共享 `MenuSelect` 采用 shadcn/ui 的组合方式与 Radix Select 原语，实际使用 `Select.Portal`、`Select.Content`、`Select.Item` 和 `Select.ItemIndicator`。列表挂到页面外层，避免被表格的滚动容器截断；焦点管理、键盘搜索与边缘避让由原语处理。项目保留现有颜色与开关过渡。选项以内部索引作为字符串标识，再映射回原值，保留数值及空字符串筛选值的行为。

核验来源：[主题变量](https://ui.shadcn.com/docs/theming)、[选择菜单](https://ui.shadcn.com/docs/components/radix/select)、[弹窗](https://ui.shadcn.com/docs/components/radix/dialog)、[标签页](https://ui.shadcn.com/docs/components/radix/tabs)、[骨架屏](https://ui.shadcn.com/docs/components/radix/skeleton)。官方现有多种组件基础实现；项目无需为外观一致性整体迁移到其中一种。

## 3. 数据与版式

- 页面维持一个主要标题，刷新、筛选与数据时间靠近内容入口。警告、旧数据和错误高于装饰性说明。
- 标题与正文采用现有系统字体，数值使用等宽数字。价格、数量和百分比右对齐；符号、单位与数值不拆散。主要正文维持 14 像素，次要数据 13 像素，小字不承担唯一风险说明。
- 涨跌颜色使用 `up` / `down` 变量，支持既有红涨绿跌偏好；同时呈现正负号、方向或文字，平盘使用中性色。品牌蓝不表达涨跌。
- 对比度需以实际前景和背景计算。普通正文至少 4.5:1，大字至少 3:1；焦点与必要控件边界至少 3:1。状态浅底只用于承托，不应配同样浅的文字。
- 手机重排卡片、允许表格局部横向滚动；整页不能横向溢出。保留筛选、刷新、比较基准、时间与风险入口。触控主要操作区采用至少 44 × 44 像素。
- 详情内出现外部链接时用完整可理解的名称。危险操作确认区清楚说明目标，确认按钮与取消按钮的位置一致。

### 筛选与操作控件：克制的小圆角

本轮参考 Stripe 官方按钮（Button）的主次层级与标签页（Tabs）的内容切换规则，采用以下项目适配值。圆角与颜色数值是本项目选择，不标成 Stripe 官方设计参数，也不引入 Stripe 业务组件依赖。

| 类型 | 外观与尺寸 | 语义与行为 |
| --- | --- | --- |
| 普通操作、筛选按钮 | 白底、细边框、6 像素圆角，桌面最小高度 32 像素 | `control-button`；主要提交操作才用实心品牌底 |
| 同一维度的筛选组 | 白底、8 像素外框，组内按钮 4 像素圆角；窄屏按组及组内换行 | `filter-group` 与 `FilterButton`；状态用 `aria-pressed`，每个按钮都能通过 Tab 到达 |
| 面板分段切换 | `Segmented` 白底外框、`GlidePill` 浅品牌底与细描边；选中项深品牌文字 | 保留 `tablist` / `tab`、方向键、Home / End、单一 Tab 停靠；不能用外观变化替代语义 |
| 移动导航 | 12 像素外框圆角，选中项 6 像素圆角 | 保留页面链接、当前位置标记与至少 44 像素高的操作区 |

突破雷达的状态与评分各自有可见标签，不再将十个实心或描边胶囊混排。选中项使用浅品牌底、深色文字和清楚字重；取消选中时仅改变状态，不进行放大、不增加凸起阴影。评分门槛、事件状态与后端查询口径不变。

筛选器的预设、板块多选、宏观适配、图层预设，以及主题/代码过滤的清除操作复用这套语言。板块切换复用 `Segmented` 的键盘行为，外层滚动坐标交给 `HorizontalScroller` 的 `layoutScroll`，选中项通过真实位置即时滚入视口。减少动态效果时仍能看清选中项并操作所有按钮。粗指针设备上的选择与筛选按钮至少高 44 像素。

圆点、图例、头像、开关轨道、滑杆与非交互状态标签保留各自形状；不通过全局覆盖 `rounded-full` 或 `rounded-pill` 强制统一。

核验来源：[Stripe 按钮及主次操作规范](https://docs.stripe.com/stripe-apps/components/button?app-sdk-version=9)、[Stripe 标签页内容组织](https://docs.stripe.com/stripe-apps/components/tabs?app-sdk-version=9)。本轮仅借鉴公开文档中的设计规则，没有复制 Stripe 组件源码。

## 4. 状态与交互验收

| 场景 | 验收要求 |
| --- | --- |
| 首次加载 | 同尺寸骨架与明确加载说明，不出现伪造零值或“无结果”闪现 |
| 后台刷新 | 保留现有内容，显示刷新状态；结果返回后再更新 |
| 空结果 | 说明当前筛选条件，提供清空或调整筛选入口 |
| 请求失败 | 保留可理解的错误和重试入口，不继续表现为正在加载 |
| 旧数据、降级数据 | 时间与状态持续可见，不只出现一次通知 |
| 弹窗、抽屉 | 键盘可进入、可关闭、焦点不逃到背景，关闭后焦点恢复 |
| 表格、排序、行内操作 | 排序按钮可用键盘；行内移除、收藏等动作不误触详情 |
| 搜索、菜单 | 无匹配结果明确；清除后焦点回输入框；方向键和关闭键行为一致 |
| 减少动态效果 | 页面、滑块、计数、图表、骨架和程序滚动均遵守系统设置 |
| 触控与缩放 | 390 像素宽度及 200% 缩放下，主要操作可见且能完成 |

变更共享组件后至少验证桌面、手机与减少动态效果三种环境；对焦点、数据展示、异步状态有影响的变更补行为回归检查。截图只能证明外观，不能替代键盘与真实数据状态验证。

## 5. 来源维护

许可与核验版本记录见根目录 `THIRD_PARTY_NOTICES.md`。上游提交号表示本次审查参考的版本，并不反推历史代码的原始复制版本。复制源码时保留完整许可，记录适配点；设计借鉴与直接复制代码分别说明。不得把样例交互、虚构数据、付费图标或新的远程资源直接带入产品。
