# 界面与动效规范

核验日期：2026-09-05（第 5 节于 2026-09-26、第 6 节于 2026-10-04 补充）。适用于研究概览、自选、筛选器、个股详情与移动端。本文说明共享组件的使用规则与验收标准；测试结果以本次审查报告为准。产品定位沿用根目录 `.impeccable.md`：冷静、清楚的市场研究工作台，持续说明行情时间、来源与降级状态。

## 1. 统一实现入口

沿用 React 19、Vite 7、Tailwind CSS 3.4、Framer Motion 12 与 ECharts 体系。五个参考站提供设计与源码依据，不要求同时安装五套组件，也不因此整体升级样式框架或替换图表。依赖安全更新单独核验兼容性。

| 内容 | 项目入口 | 规则 |
| --- | --- | --- |
| 颜色、字体、圆角、间距 | `frontend-src/src/index.css`、`frontend-src/tailwind.config.js` | 同名颜色保持一致；页面不得另建品牌色或涨跌色 |
| 过渡参数 | `frontend-src/src/styles/transitions-root.css` | 打开、关闭、提示、内容显现按用途选参数 |
| 过渡样式与适配 | `frontend-src/src/styles/transitions-catalog.css` | 保留来源样式，项目适配集中书写 |
| JavaScript 动效 | `frontend-src/src/lib/motion.ts`、`frontend-src/src/lib/transitions.ts` | 共享参数与计时读取，不在调用处随意新增时长 |
| 选择与提示 | `FilterButton`、`Segmented`、`GlidePill`、`MenuSelect`、`InfoHint` | 复用键盘行为、定位与减少动态效果处理 |
| 信息展示 | `DataTable`、`InsightFrame` / `InsightValue`、`StatCard`、`SourceNote` | 信息结构和数据口径在页面间一致 |
| 浮层与反馈 | `Drawer`、`ConfirmDialog`、`Toast`、`CommandPalette` | 统一焦点、关闭、背景滚动与状态表达 |
| 加载与异常 | `SkeletonReveal`、`EmptyState`、`InlineFallback`、`StaleStrip` | 加载、空结果、错误和旧数据分别呈现 |
| 按钮与进行中反馈 | `.btn-primary`、`.btn-primary.btn-sm`、`Spinner`、`BusyIcon` / `IconSwap`、`ThinkingLabel` | 主操作不再手写样式；请求在途用同格切换的加载圈，模型任务在途用扫光文字 |
| 收放与高度变化 | `CollapsePresence`、`AutoHeight`、`MatrixLoader` | 条件内容用 grid 行收放，视图切换用高度补间，不用 framer 的 `height: auto` |

源码注释中的历史 `design.md` 指向旧规范；后续变更以本文、`.impeccable.md` 与上述实际入口共同核对，避免引用不存在的设计文件。

## 2. 五个来源的具体用法

### Beautiful UI：信息层次与搜索反馈

采用其洞察卡片（Insight Cards）的组织方式：标题和口径在上，图形居中，读数、变化与比较基准相邻。现有 `InsightFrame`、`InsightValue` 和 `Sparkline` 承接此规则。搜索沿用其内嵌空态、清除按钮与轻量淡入方式，对应 `CommandPalette`。

价格和涨跌必须说明比较基准；来源和时间不藏在悬停操作里。卡片布局用来解释数据，不增加虚构置信度、示例值或自动推断出的交易建议。

核验来源：[官方组件展示](https://www.beautifului.dev/)、[洞察卡片源码](https://github.com/slev12397/beautiful-ui/blob/06557d7ff33a1eb70d5987bae9ac4c70fa0e20c4/components/primitives/InsightCards.tsx)、[搜索源码](https://github.com/slev12397/beautiful-ui/blob/06557d7ff33a1eb70d5987bae9ac4c70fa0e20c4/components/primitives/SearchList.tsx)。官方站点自 2026-08-28 起提供注册源 `https://www.beautifului.dev/r/registry.json`（27 项，用法 `npx shadcn add https://www.beautifului.dev/r/<名>.json`）；不要使用名称相似的第三方注册源冒充官方。其原始样式依赖 Tailwind CSS 4 的 `@theme inline`，现有项目采用设计模式与本地组件适配，不整份覆盖全局样式。

### beUI：选择控件的连续反馈

采用标签页（Tabs）的共享位置指示器、按钮按压反馈与触控区分。现有 `Segmented`、`GlidePill`、主导航和主按钮继续复用。选中项的文字必须即时可读，滑块位于文字下方，且不能遮挡相邻标签。

滑块弹簧取标签页组件自己的参数（它不引用 `lib/ease.ts` 的 `SPRING_LAYOUT`）。项目原先的 170/24/1.2 是 2026-09 初审查时的上游原值；上游 2026-09-18 改为 245/36/1.2（阻尼比约 1.05，不过冲），理由是可滚动标签条里过冲会闪出滚动条，本项目的 `Segmented` 正在 `HorizontalScroller` 里，于是 2026-10-05 同步为 `lib/motion.ts` 的 `SPRING_INDICATOR`。上游 `layout="position"` 有意不采用（审查 #113，宽度会瞬跳）。

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

## 5. 2026-09-26 动效与按钮统一

本轮在不改数据口径的前提下，把散落在页面里的按钮、加载圈与动效参数收口到共享入口，并补齐几种此前缺失的状态过渡。具体规则：

- **主按钮**：实心主操作一律用 `.btn-primary`（44 像素高）或 `.btn-primary.btn-sm`（32 像素高，粗指针设备仍为 44 像素）。二者自带按下回缩、只在可悬停设备上生效的悬停色和统一的禁用透明度。请求在途的按钮加 `aria-busy`，禁用态保持可读、指针显示为进行中。不要再手写 `bg-brand-600 … hover:brightness-105`。
- **加载圈与刷新反馈**：统一用 `Spinner`（`on-accent` / `brand` / `muted` 三种色调）。带图标的刷新、重试按钮用 `BusyIcon`：图标与加载圈叠在同一格里交叉淡换（transitions.dev 09），按钮宽度与文字位置不变，请求在途期间持续转动。不再使用只转一圈的 `animate-spin-once`。
- **模型相关按钮**：不再使用 `bg-ai-600` 实心青块。列表里每行都有的入口（财报列表「AI 影响」、新闻流的分析入口）用 `.control-button.ai-action`：白底发丝边、青瓷色图标，只在悬停时上浅青；该行已有分析结果可看时加 `.is-ready`，用浅青底标出。面板里单个的发起操作（生成分析、开始分析、确认弹窗）用 `.btn-ai`：浅青底、青瓷字、细边。仅图标的按钮加 `.btn-icon`。AI 结论卡片不加顶部或侧边的装饰色条；引文段落左侧的引用线属于排版，保留。
- **模型任务在途**：状态文字用 `ThinkingLabel` 扫光（transitions.dev 15，beautifului ThinkingState 同一手法），文字作为子节点传入；只有任务确实在跑时扫光，暂停、待确认、已请求取消保持静止。
- **状态型按钮**：加入/移出自选这类三态按钮参照 beUI button-stateful 的节奏：空闲图标 → 加载圈 → 成功勾（transitions.dev 10 描边出现），三者共用一个图标格。`IconSwap` 可以嵌套，内层靠子选择器适配保持自己的状态。
- **收放与高度**：条件渲染的展开内容用 `CollapsePresence`（transitions.dev 21 的 grid 行 0fr↔1fr，收起播完再卸载，展开到位后放开裁剪与滤镜）；两种视图之间切换用 `AutoHeight`（transitions.dev 01）补间容器高度，新视图按 key 重挂后淡入。不要再用 `AnimatePresence mode="wait"` 加 `height: 0 ↔ auto`：它先收成 0 再撑开，帧循环被节流时还会卡在旧视图。
- **空状态与分包占位**：`EmptyState` 按插画、标题、说明、操作逐行浮现（transitions.dev 18 的关键帧版，40 毫秒一档）；路由分包占位用 `MatrixLoader` 点阵（transitions.dev 31），比转圈安静。
- **悬停与按压**：Tailwind 已开启 `future.hoverOnlyWhenSupported`，所有 `hover:` / `group-hover:` 工具类只在「可悬停的精确指针」下生成，与 `index.css` 手写规则的媒体查询同一口径，触屏点按后不再粘住悬停态。`.control-button` 按下回缩到 0.97（beautifului 表格芯片、beUI 按钮的按压反馈）。桌面主导航增加悬停浅底滑块（beUI shared-layout-bg），位置由 `placeGlide` 写入。
- **动效参数**：Framer Motion 一律引用 `lib/motion.ts` 的 `EASE_PAPER`、`DUR_FAST` / `DUR_UI` / `DUR_SECTION`、`SPRING_POP`、`SPRING_INDICATOR`，按用途而不是按最近的数字取档；0.7 秒以上的条形增长、仪表扫动保留原值。CSS 侧引用 `transitions-root.css` 的令牌。键盘驱动的高亮（命令面板）用 quick 档（150 毫秒），跟得上方向键连按。
- **热力色阶**：`heatColor(pct, span)` 的色阶两端按展示口径定：日内涨跌 ±3%，板块 1 / 3 / 6 个月收益分别 ±6% / ±12% / ±18%。中点随冷灰纸面，夜间的半程色单独一套，从深灰逐步加深。板块砖入场改为自左上角铺开的对角波（beUI heat-calendar、rareui github-activity 的错落节奏），总时长封顶约 0.6 秒。
- **跑马灯**（2026-09-27，参考 ObsidianUI 的 draggable-marquee）：动画元素只含第一套内容，其余副本绝对定位在它后面，每轮平移 `-100%`，正好是一套的宽度（含尾部间距），接缝不跳。副本数由 `lib/marquee.ts` 的 `marqueeCopies` 按轨道宽度计算，至多六套，覆盖常见屏宽的完整循环。键盘焦点进入时暂停，并把焦点项挪到轨道起点；焦点停留期间暂时隐藏右侧标签的视觉层，保留布局占位，离开后恢复。减少动态时副本隐藏，轨道改为可横向滑动，并主动滚动到键盘焦点项，避免原生滚动只露出被标签遮住的部分。运行中切换动态偏好时清除旧的手动滚动偏移，并保持当前键盘焦点可见。
- **测试镜子**：沙箱编译组件的测试对未声明依赖一律报错。组件新增上述共享依赖时，在 `frontend-src/tests/helpers/shared-ui-stubs.mjs` 补桩（令牌用真实模块，展示件桩成元素类型并保留子节点文字），不要在各测试里各抄一份。

## 6. 2026-10-04 执行精度整理

本轮按「获奖级执行精度」逐页审查后收口了几条系统规则，均有测试或源码约束：

- **状态色与涨跌色分开**：成功、正常、在线、强度高档用 `ok`，错误、失败、删除、高追高风险用 `danger`。两组令牌的默认取值与绿涨红跌下的涨跌色相同，但红涨绿跌模式不互换，「操作成功」不会因行情偏好变红。`up` / `down` 只用于价格、涨跌幅和多空解读。`color-scale-contract` 测试禁止在红涨绿跌样式块里覆盖 `ok` / `danger`。
- **类别不借语义色**：盘前盘后、经济事件重要度、热度、IV 异动这类类别信息用中性墨色深浅或图标区分，不借用警示琥珀色或 AI 青瓷色。
- **文字对比度**：`ink-300` 只用于线条、轨道、圆点、拖拽把手和装饰分隔符，文字最低用 `ink-400`。警示文字用 `warn-700`（浅色 #935B00，在 warn-50 上 5.1:1），`warn-600` 只用于圆点、图标和填充。
- **字阶**：不再使用 8 到 10 像素的文字，最小为 `micro`（11/14；2026-10-06 第二轮上调为 12/17，见 §8）。`color-scale-contract` 同时检查字阶类名是否都在 `tailwind.config.js` 里定义。
- **圆角三档**：`--r-chip` 4（组内小件、徽章）、`--r-control` 6（普通控件、下拉触发器）、`--r-group` 8（控件组外框、主按钮、浮层；第二轮整体上调为 6 / 8 / 10，见 §8）；组内滑块用 `calc(var(--r-group) - 3px)` 与外框同心。卡片 12、抽屉 16 沿用 Tailwind 的 `rounded-lg` / `rounded-xl`。
- **焦点环**：需要用 ring 代替全局 outline 的地方（被裁切的表格行、轨道）一律用不透明的 `ring-brand-600`，不用半透明品牌色。
- **数据条入场**：数据条用 `lib/motion` 的 `GROW_X` / `GROW_Y` 变体，`initial="hidden"`、`whileInView="shown"` 挂在轨道或外层容器上，不让缩放到 0 的条自己观察视口（零面积元素有一部分永远判不进视口，条会一直空着）。
- **公司标志底板**：标志图片垫 `--logo-plate`（白天白色、夜间 #E6E8EC），首字母兜底仍用卡片色。
- **次级操作**：查看全部、重试、显示更多、刷新等普通操作一律用 `.control-button`，只保留外边距和固定行高这类布局类。
- **数字字体**：价格、涨跌幅、分数、倍数、数量用正文字体加等宽数字（`tnum`），大读数用 `.metric-value`；时间、倒计时、日期、代码、序号和图表坐标用 `font-mono`。同一种数字在不同卡片上不换字体。
- **卡片标题**：每张卡只有一个中文标题（`text-h3 text-ink-900`），不在卡片上叠「中文 · ENGLISH」眉题，也不在中文名旁边重复大写英文名。页面级眉题（`§04 SECTORS · LIVE AGGREGATES` 这类页头）是全站统一的页头格式，保留。
- **成对的卡片**：同一行两列的卡片由栅格拉伸到同高，卡片本身用纵向弹性布局，底部的更新时间行用 `mt-auto` 贴底。某一列内容天然偏短时，把相关的卡叠进这一列，或让图表随栏高拉伸，而不是留出半张空卡。
- **`cn()` 与字阶**：`lib/utils.ts` 把 `tailwind.config.js` 的自定义字阶登记进 tailwind-merge 的字号组，否则 `cn('text-micro', 'text-ink-400')` 会把字号当颜色合并掉。新增字阶要两处同时加，`cn-font-size` 测试按配置逐个核对。
- **指数卡**：首页与大盘页共用 `components/shared/IndexCard`，手机三列；页面只负责栅格、闪烁、空态和定位。
- **文案**：不写免责与合规腔（仅供参考、不构成建议、以公告为准、不是预测之类），也不写复述屏幕内容的说明（页头一句话描述、卡片副标题、显而易见的图例句子）。保留的是读者靠界面本身看不出来的东西：数据状态与陈旧提示、估算口径、数据来源、时区与单位、图上两种线型的含义、AI 生成标注。页头 `PageHeader` 不再有描述行。
- **触屏行情带**：粗指针下行情带与每个按钮 44px，按钮内各段仍按基线对齐，用 `flex-wrap: wrap; align-content: center` 把这一行放在按钮正中；`audit-touch-targets` 同时检查基金与指数两种模式。
- **类别不借语义色（补）**：技术信号（突破、放量、跳空、IV 异动等）、热点热度、IV 排位都是类别或强弱，用纸面底加墨色，靠文字或段数区分；警示琥珀只给真正的提醒（已过期、风险提醒），AI 青瓷只给模型状态。
- **状态文字原位切换**：按钮与状态文字在阶段之间换句时包 `TextSwap`（transitions.dev 04-text-swap 的进场半程，`--text-swap-*`），按 `swapKey` 只在阶段变化时播；倒计时数字留在同一个 key 里。
- **可撤销的即时操作**：一点就生效、误点代价大的操作（移除自选）在提示条上给「撤销」；带操作的提示停留 8 秒，切换页面后仍可使用。移除开始时绑定当前登录身份；身份变化或暂时无法确认后，旧操作失效。服务器在删除时记录原顺序，恢复单个代码时保留其他页面同时新增的成员及其相对顺序，不用旧清单覆盖当前清单。
- **图表读数游标**：类目横轴的历史折线（宏观综合分、CTA 仓位）用 `useChartCursor`：指针或方向键指到哪天，表头读数就换成哪天，离开或 Esc 回到最新，并清除旧游标和点高亮；日期序列变化时当次渲染即复位。绘图区是 `role="slider"`，`aria-valuetext` 写日期与数值。K 线图沿用自己的十字线。
- **排序表**：正在排序的列整列垫浅底、表头加深；右对齐数值列的排序箭头放在标签前。

## 7. 2026-10-06 平面表面与字重

用同一个脚本量了 Arc UI、Beautiful UI、beUI、Transitions.dev、60fps 五个参考站和本站（按字数统计字重分布、带阴影的元素个数）。参考站常规字重占七成以上、卡片不投影；本站当时中等及以上字重占七到八成、每页 19 到 26 处阴影。据此收口：

- **静止不投影**：卡片（`--card-shadow`）、描边按钮（`--btn-shadow`）、主按钮（`--btn-hi-shadow` 与 `.btn-primary`）静止时都不投影，分层只靠发丝边。选中的分段滑块与芯片（`--chip-shadow`、`--control-selected-shadow`）只留一圈淡描边，夜间用 `--line-strong`，不带白色高光。真正浮起的下拉、抽屉、对话框、提示条和手机底栏保留阴影。
- **悬停加深描边**：`.card-lift` / `.card-hover` 悬停时描边换成 `--line-strong`，不再上浮投影；仍只在可悬停的精确指针下生效。
- **字重只用 400 / 500**：字阶里 display、h2、h3、eyebrow 为 500，caption 为 400，数据字阶仍为 500；组件里不再写 `font-semibold`（统一 `font-medium`），`font-bold` 只留给「Optix Pro」品牌字样，个股页头的代码用 600。中文字重到 550 以上会落到苹方「中粗」，整页发重。
- **提示由网页绘制**：只读说明照常写 `title` 属性；全站挂着的 `TitleTooltipLayer` 在鼠标悬停时借走 title，用站内提示样式画出来，离开时原样还回（未悬停时 DOM 不变，`getByTitle` 测试照常可用）。不要为只读说明另包一层可聚焦的提示组件：放进整行链接里会形成交互嵌套。需要标题加正文的算法说明仍用 `InfoHint`。
- **标题更新与读屏说明**：数据清空时撤销旧提示，悬停期间补齐时正常显示；点击、滚动或 Esc 主动收起后，等指针离开重入再显示。借用期间保留等价的可访问说明，作者提供的说明优先；只能从标题获得名称的元素保留原生标题。元素或提示层卸载时恢复最新属性并结束监听。
- **段落避免孤字**：`p`、`li` 等正文默认 `text-wrap: pretty`，手机上不再出现末行只剩一个字。
- **大标题词内不断行**：`h1` 默认 `word-break: keep-all` 加 `line-break: strict`，日文片假名词（如「レーダー」）不从中间或长音符前断开，改在「・」、空格处换行；整词放不下时 `overflow-wrap: anywhere` 兜底。
- **不变的部分**：冷蓝灰色系、代码与时间用等宽字体、11 到 12 像素的密集字阶、圆角三档，都沿用之前的决定（等宽字体、字阶与圆角在同日第二轮调整，见 §8；色系不变）。

## 8. 2026-10-06 第二轮：参照 uiarc.dev 的结构与字阶

用户点名喜欢 [Arc UI](https://uiarc.dev/)，同时要求原有配色基本保留。所以只学它的结构、字阶、留白与组件形态，品牌蓝、涨跌红绿、AI 青、警示琥珀与冷蓝灰底色都不变。量化对照（同一统计脚本，按字数）：Arc 正文 13 到 14 像素占约八成、行高 1.5 到 1.6、等宽字体只用于代码、控件圆角 8 像素、大容器 16 到 18 像素；本站改前 11 到 12 像素占九成、行高约 1.1、等宽字体占两到三成。

- **字阶上调一档**：`micro` 12/17、`caption` 13/19、`body-s` 14/21、`body` 15/24、`h3` 16/24、`data-m` 15/22；`index.css` 里写死的按钮字号同步上调（主按钮 14、小号主按钮与描边按钮 13）。窄屏排版按新字号复查过 320、390 两档。
- **不再用等宽字体**：数字、时间、代码改用正文字体加 `tnum`（等宽数字）；图表轴与提示框改用 `CHART_TEXT_FONT`，提示框加 `font-variant-numeric: tabular-nums`。只有快捷键提示和事件编号保留等宽字体。
- **圆角上调一档**：`--r-chip` 6、`--r-control` 8、`--r-group` 10；`rounded-sm` 6、`rounded-md` 8、`rounded-lg`（卡片）16、`rounded-xl` 20；`.card-surface` 16。筛选与分段的组内按钮仍守 §3 的克制圆角：与外框同心（桌面内衬 3px 得 7px，窄屏内衬 2px 得 8px），`feedback-layout` 测试按 ≤ 8 / ≤ 9 像素检查。
- **页头只留标题与状态**：去掉「§0X + 英文」装饰行和页头下的发丝线；`.eyebrow` 不再全大写、不加字距；导航不显示编号，品牌只留「Optix Pro」。
- **涨跌只用着色文字**：`ChangeBadge` 与涨跌额去掉浅色底块，保留箭头与涨跌色。
- **指数指标带**：xl 起首页与大盘页的指数排成一行，合成一条带外框的指标带（格子之间 1px 线色分隔），窄屏仍是独立小卡。
- **分段控件无描边**：底槽只靠浅灰底色，选中项白底加淡阴影，选中文字仍用品牌色表示状态。
- **报价说明精简**：列表里「暂无新成交 · 最后报价 2026-10-05」显示为「最后报价 10-05」（今年省年份），完整说明与年份在悬停提示里。
- **去掉页面点阵纹理**，页面底色保持纯色。
- **用户反馈后的调整（同日）**：手机上分段与筛选的选中块改回原版——只有柔和投影（`--control-selected-shadow-raised`），不加描边；财报时段图标按类别着色（盘前太阳黄、盘后月亮蓝、时间待定闹钟橙，`--timing-*` 浅深两套）；选股结果表 xl 起显示（结果区 9 列、侧栏 3 列，价格说明放到第二行），xl 以下改两列结果卡，避免表格右侧被截；选股行展开的「近 6 日 · 点阵面积」改为近半年日线走势。

## 9. 来源维护

2026-10-05 另参考了两个站点，只借做法、未复制源码：[Arc UI](https://uiarc.dev/)（图表读数随游标变化、排序列标识、可撤销提示条；其全站去焦点环、缺失值按零、系列色随强调色旋转与本项目规则冲突，不采用）与 [60fps](https://60fps.design/)（移动端应用动效录屏库，没有公开参数；用来核对「跟手、即时反馈、只动 transform 与 opacity」的做法）。

许可与核验版本记录见根目录 `THIRD_PARTY_NOTICES.md`。上游提交号表示本次审查参考的版本，并不反推历史代码的原始复制版本。复制源码时保留完整许可，记录适配点；设计借鉴与直接复制代码分别说明。不得把样例交互、虚构数据、付费图标或新的远程资源直接带入产品。
