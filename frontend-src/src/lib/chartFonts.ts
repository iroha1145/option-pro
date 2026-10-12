/** 与 Tailwind `font-mono` 同一栈。单引号包带空格的族名，才能安全插进 HTML style="…"。 */
export const CHART_MONO_FONT =
  "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, 'Liberation Mono', monospace";

/** 轴标签与小窗正文同页面字体；数字表格需要对齐时仍可使用上面的等宽栈。
    栈与 tailwind.config.js 的 fontFamily.sans 保持一致（含日文与 Windows 中文回退）。 */
export const CHART_TEXT_FONT =
  "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Hiragino Sans', 'Noto Sans SC', 'Microsoft YaHei UI', 'Microsoft YaHei', 'Yu Gothic', 'Meiryo', sans-serif";
