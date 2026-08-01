/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: ["class"],
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        /* ---- Optix 纸面终端 tokens（design.md §1 精确 HEX）---- */
        paper: {
          DEFAULT: '#F6F7F9', // --paper 页面主背景（v8 降温：冷灰蓝纸面）
          2: '#FAFBFC',       // --paper-2 抬升区
        },
        card: {
          DEFAULT: '#FFFFFF',
          warm: '#FBFCFD',
          foreground: '#0D1626',
        },
        ink: {
          900: '#0D1626',
          800: '#182338',
          /* 700：源码里 18 处「比正文重一档」的用法此前落在不存在的档位上，
             类名不生成任何规则、全部退回继承色（审计 2.4.7/3.2）。 */
          700: '#2A3550',
          600: '#3D4A68',
          500: '#5A6788',
          400: '#8A94B0',
          300: '#B7BFD3',
        },
        line: {
          DEFAULT: '#E9ECF1', // v8.1 随纸面降温：冷纸面配暖灰线会显脏，同族蓝灰
          strong: '#DBE0E8',
          chart: '#EDF0F4',
        },
        /* 群青只做同色相深浅：hover 一律深一档 brand-700（#2338C8），不用更浅的 500/400 */
        brand: {
          700: '#2338C8', // hover / 按压态
          600: '#2E46E0', // 主色（主按钮/激活态/关键数据）
          500: '#3B59F2',
          400: '#6B82FF',
          100: '#E4E9FF',
          50: '#F0F3FF',
        },
        up: {
          700: '#0B7A55',
          600: '#0E9F6E',
          50: '#E5F6EF',
        },
        down: {
          700: '#C4302B',
          600: '#E5484D',
          50: '#FCECEC',
        },
        warn: {
          /* 700：警示横幅主文案用档（warn-50 底上比 600 重一档，审计 2.4.5） */
          700: '#B87109',
          600: '#E8930C',
          50: '#FCF3E2',
        },
        /* v8.1 弃「AI 紫」：紫+AI 是生成式设计最强签名。青瓷 teal（印刷第二油墨色），
           与群青相距 60°+、与涨绿差 36°，不误读为行情方向。 */
        ai: {
          600: '#0B7285',
          50: '#E7F3F6',
        },
        /* ---- shadcn/ui 兼容令牌（ui/ 基座仍可用）---- */
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive) / <alpha-value>)",
          foreground: "hsl(var(--destructive-foreground) / <alpha-value>)",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
      },
      borderRadius: {
        /* v8 清新回暖：chip/badge 4 · 按钮/输入 6 · 卡片/容器 12 · 抽屉/模态 16 */
        xs: '4px',
        sm: '4px',
        md: '6px',
        lg: '12px',
        xl: '16px',
        '2xl': '20px',
        pill: '999px',
      },
      boxShadow: {
        /* v8 清新：墨色 rgba(16,24,40,*) 三层制，更柔更大；内高光 .7 保持；聚焦环即时出现 */
        'sh-1': '0 1px 2px rgba(16,24,40,.03)',
        'sh-2': '0 1px 2px rgba(16,24,40,.03), 0 12px 32px -14px rgba(16,24,40,.10)',
        'sh-3': '0 2px 4px rgba(16,24,40,.04), 0 24px 56px -16px rgba(16,24,40,.16)',
        'card': '0 1px 2px rgba(16,24,40,.03), inset 0 1px 0 rgba(255,255,255,.7)',
        'card-hover': '0 1px 2px rgba(16,24,40,.03), 0 12px 32px -14px rgba(16,24,40,.10), inset 0 1px 0 rgba(255,255,255,.7)',
        'inset-hi': 'inset 0 1px 0 rgba(255,255,255,.7)',
        'focus-ring': '0 0 0 3px rgba(46,70,224,.18)',
        /* 开关旋钮：小件白色控件在彩色轨道上需要比 sh-1 更实的两层墨影才有层次 */
        knob: '0 1px 3px rgba(16,24,40,.18), 0 1px 1px rgba(16,24,40,.10)',
        /* 区带/轨道色块：内高光 + 一层薄墨影，翻出层次但不打破扁平轨道语言 */
        zone: 'inset 0 1px 0 rgba(255,255,255,.45), 0 1px 2px rgba(16,24,40,.10)',
        xs: '0 1px 2px 0 rgba(16,24,40,.05)', // v8.1 纯黑→墨色，与三层制同源
        /* 按钮立体三档（与 sh-* 同族墨影）：btn-hi 实心主按钮 > btn 描边次按钮 >
           幽灵/文字按钮平面。chip 给选中态胶囊/页码/分段滑块（介于两者之间）；
           track 是开关轨道的内凹井。按下收拢/禁用摊平的全局规则在 index.css。 */
        /* v8.2 收小：次按钮/选中胶囊多为 26–36px 小件，2px/6px 晕染层在这个
           尺度上发飘（用户实测反馈）——btn 只留 1px 贴身影，chip 收到 1px/3px。 */
        btn: '0 1px 2px rgba(16,24,40,.07), inset 0 1px 0 rgba(255,255,255,.75)',
        'btn-hi': 'inset 0 1px 0 rgba(255,255,255,.16), 0 1px 2px rgba(16,24,40,.18), 0 4px 12px -4px rgba(16,24,40,.34)',
        chip: 'inset 0 1px 0 rgba(255,255,255,.14), 0 1px 3px -1px rgba(16,24,40,.25)',
        track: 'inset 0 1px 2px rgba(16,24,40,.16)',
      },
      fontFamily: {
        /* v8：display 换系统 sans 栈（与 sans 相同但独立变量保留，大标不再用衬线） */
        display: ['-apple-system', 'BlinkMacSystemFont', '"Segoe UI"', '"PingFang SC"', '"Hiragino Sans GB"', '"Noto Sans SC"', '"Microsoft YaHei UI"', 'sans-serif'],
        /* 衬线只保留给「编辑式引文」场景：font-quote */
        quote: ['Georgia', '"Songti SC"', '"Noto Serif SC"', 'serif'],
        sans: ['-apple-system', 'BlinkMacSystemFont', '"Segoe UI"', '"PingFang SC"', '"Hiragino Sans GB"', '"Noto Sans SC"', '"Microsoft YaHei UI"', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', '"SF Mono"', 'Menlo', 'Consolas', '"Liberation Mono"', 'monospace'],
      },
      fontSize: {
        /* design.md §2.2 字阶（size/lineHeight，weight 由工具类控制） */
        /* v8 display 字阶：tracking -0.02em · lineHeight 1.15 · 700/700/600（sans 大标） */
        'display-xl': ['56px', { lineHeight: '64px', fontWeight: '700', letterSpacing: '-0.02em' }],
        'display-l': ['40px', { lineHeight: '46px', fontWeight: '700', letterSpacing: '-0.02em' }],
        'display-m': ['28px', { lineHeight: '32px', fontWeight: '600', letterSpacing: '-0.02em' }],
        h2: ['20px', { lineHeight: '26px', fontWeight: '600' }],
        h3: ['15px', { lineHeight: '22px', fontWeight: '600' }],
        body: ['14px', { lineHeight: '22px', fontWeight: '400' }],
        'body-s': ['13px', { lineHeight: '20px', fontWeight: '400' }],
        caption: ['12px', { lineHeight: '16px', fontWeight: '500' }],
        eyebrow: ['11px', { lineHeight: '14px', fontWeight: '600', letterSpacing: '0.08em' }], // v8 字距收紧 0.14→0.08em
        'data-xxl': ['44px', { lineHeight: '48px', fontWeight: '500', letterSpacing: '-0.02em' }],
        'data-xl': ['30px', { lineHeight: '36px', fontWeight: '500' }],
        'data-l': ['20px', { lineHeight: '26px', fontWeight: '500' }],
        'data-m': ['14px', { lineHeight: '20px', fontWeight: '400' }],
        micro: ['11px', { lineHeight: '14px', fontWeight: '400' }],
      },
      maxWidth: {
        shell: '1440px',
      },
      transitionTimingFunction: {
        /* design.md §4.1 缓动 */
        paper: 'cubic-bezier(.16,1,.3,1)',
        snap: 'cubic-bezier(.22,1,.36,1)',
        'in-out-circ': 'cubic-bezier(.45,0,.15,1)',
      },
      transitionDuration: {
        instant: '90ms',
        fast: '160ms',
        /* 卡片 hover 抬升的统一时长。之前 5 处写 duration-[240ms] 任意值，
           构建报「匹配多个 utility」二义警告——收进 token 一并消除。 */
        240: '240ms',
        ui: '260ms',
        section: '560ms',
      },
      keyframes: {
        "accordion-down": {
          from: { height: "0" },
          to: { height: "var(--radix-accordion-content-height)" },
        },
        "accordion-up": {
          from: { height: "var(--radix-accordion-content-height)" },
          to: { height: "0" },
        },
        "caret-blink": {
          "0%,70%,100%": { opacity: "1" },
          "20%,50%": { opacity: "0" },
        },
        /* design.md §4.2 具名动效库 */
        'rise-in': {
          from: { opacity: '0', transform: 'translateY(14px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        'page-fade-in': {
          from: { opacity: '0', transform: 'translateY(10px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        'page-fade-out': {
          from: { opacity: '1' },
          to: { opacity: '0' },
        },
        'tick-flash-up': {
          '0%': { backgroundColor: '#E5F6EF' },
          '100%': { backgroundColor: 'transparent' },
        },
        'tick-flash-down': {
          '0%': { backgroundColor: '#FCECEC' },
          '100%': { backgroundColor: 'transparent' },
        },
        'led-pulse': {
          '0%': { boxShadow: '0 0 0 0 rgba(14,159,110,.55)' },
          '70%': { boxShadow: '0 0 0 6px rgba(14,159,110,0)' },
          '100%': { boxShadow: '0 0 0 0 rgba(14,159,110,0)' },
        },
        'radar-sweep': {
          from: { transform: 'rotate(0deg)' },
          to: { transform: 'rotate(360deg)' },
        },
        'blip-ripple': {
          '0%': { transform: 'scale(0)', opacity: '.8' },
          '100%': { transform: 'scale(1)', opacity: '0' },
        },
        'grow-bar': {
          from: { transform: 'scaleX(0)' },
          to: { transform: 'scaleX(1)' },
        },
        shimmer: {
          from: { transform: 'translateX(-100%)' },
          to: { transform: 'translateX(100%)' },
        },
        marquee: {
          from: { transform: 'translateX(0)' },
          to: { transform: 'translateX(-50%)' },
        },
        'nudge-shake': {
          '0%,100%': { transform: 'translateX(0)' },
          '20%,60%': { transform: 'translateX(-6px)' },
          '40%,80%': { transform: 'translateX(6px)' },
        },
        'spin-once': {
          from: { transform: 'rotate(0deg)' },
          to: { transform: 'rotate(360deg)' },
        },
      },
      animation: {
        "accordion-down": "accordion-down 0.2s ease-out",
        "accordion-up": "accordion-up 0.2s ease-out",
        "caret-blink": "caret-blink 1.25s ease-out infinite",
        'rise-in': 'rise-in 560ms cubic-bezier(.16,1,.3,1) both',
        'page-fade-in': 'page-fade-in 280ms cubic-bezier(.16,1,.3,1) both',
        'page-fade-out': 'page-fade-out 160ms cubic-bezier(.16,1,.3,1) both',
        'tick-flash-up': 'tick-flash-up 600ms cubic-bezier(.22,1,.36,1)',
        'tick-flash-down': 'tick-flash-down 600ms cubic-bezier(.22,1,.36,1)',
        'led-pulse': 'led-pulse 1.5s cubic-bezier(.45,0,.15,1) infinite',
        'radar-sweep': 'radar-sweep 3.2s linear infinite',
        'blip-ripple': 'blip-ripple 2s cubic-bezier(.45,0,.15,1) infinite',
        'grow-bar': 'grow-bar 700ms cubic-bezier(.16,1,.3,1) both',
        shimmer: 'shimmer 1.6s linear infinite',
        marquee: 'marquee 28s linear infinite',
        'nudge-shake': 'nudge-shake 400ms cubic-bezier(.22,1,.36,1)',
        'spin-once': 'spin-once 600ms cubic-bezier(.16,1,.3,1)',
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
}
