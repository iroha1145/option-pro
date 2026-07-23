/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: ["class"],
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        /* ---- Optix 纸面终端 tokens（design.md §1 精确 HEX）---- */
        paper: {
          DEFAULT: '#F6F5F1', // --paper 页面主背景
          2: '#FBFAF7',       // --paper-2 抬升区
        },
        card: {
          DEFAULT: '#FFFFFF',
          warm: '#FDFCF9',
          foreground: '#0D1626',
        },
        ink: {
          900: '#0D1626',
          800: '#182338',
          600: '#3D4A68',
          500: '#5A6788',
          400: '#8A94B0',
          300: '#B7BFD3',
        },
        line: {
          DEFAULT: '#E9E7E0',
          strong: '#DBD8CE',
          chart: '#EFEDE6',
        },
        brand: {
          700: '#2338C8',
          600: '#2E46E0',
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
          600: '#E8930C',
          50: '#FCF3E2',
        },
        ai: {
          600: '#7C5CFF',
          50: '#F1EDFF',
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
        /* design.md §3.2 锐利收敛圆角 */
        xs: '4px',
        sm: '6px',
        md: '8px',
        lg: '12px',
        xl: '16px',
        '2xl': '20px',
        pill: '999px',
      },
      boxShadow: {
        /* design.md §3.3 纸面抬升三层制 + 内高光 + 聚焦环 */
        'sh-1': '0 1px 2px rgba(13,22,38,.05)',
        'sh-2': '0 1px 2px rgba(13,22,38,.04), 0 8px 24px -12px rgba(13,22,38,.12)',
        'sh-3': '0 2px 4px rgba(13,22,38,.05), 0 24px 48px -16px rgba(13,22,38,.20)',
        'card': '0 1px 2px rgba(13,22,38,.05), inset 0 1px 0 rgba(255,255,255,.9)',
        'card-hover': '0 1px 2px rgba(13,22,38,.04), 0 8px 24px -12px rgba(13,22,38,.12), inset 0 1px 0 rgba(255,255,255,.9)',
        'inset-hi': 'inset 0 1px 0 rgba(255,255,255,.9)',
        'focus-ring': '0 0 0 3px rgba(46,70,224,.18)',
        xs: '0 1px 2px 0 rgb(0 0 0 / 0.05)',
      },
      fontFamily: {
        /* 原版 option-pro 字体栈（系统字体，无 webfont） */
        display: ['Georgia', '"Songti SC"', '"Noto Serif SC"', 'STSong', '"Times New Roman"', 'serif'],
        sans: ['-apple-system', 'BlinkMacSystemFont', '"Segoe UI"', '"PingFang SC"', '"Hiragino Sans GB"', '"Noto Sans SC"', '"Microsoft YaHei UI"', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', '"SF Mono"', 'Menlo', 'Consolas', '"Liberation Mono"', 'monospace'],
      },
      fontSize: {
        /* design.md §2.2 字阶（size/lineHeight，weight 由工具类控制） */
        'display-xl': ['56px', { lineHeight: '60px', fontWeight: '700', letterSpacing: '-0.01em' }],
        'display-l': ['40px', { lineHeight: '46px', fontWeight: '700' }],
        'display-m': ['28px', { lineHeight: '34px', fontWeight: '600' }],
        h2: ['20px', { lineHeight: '26px', fontWeight: '600' }],
        h3: ['15px', { lineHeight: '22px', fontWeight: '600' }],
        body: ['14px', { lineHeight: '22px', fontWeight: '400' }],
        'body-s': ['13px', { lineHeight: '20px', fontWeight: '400' }],
        caption: ['12px', { lineHeight: '16px', fontWeight: '500' }],
        eyebrow: ['11px', { lineHeight: '14px', fontWeight: '600', letterSpacing: '0.14em' }],
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
