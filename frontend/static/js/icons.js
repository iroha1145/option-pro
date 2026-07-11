const SPRITE_URL = '/static/icons.svg';

const ICON_NAMES = new Set([
  'dashboard', 'layers', 'event_note', 'filter_alt',
  'wb_sunny', 'schedule', 'dark_mode',
  'trending_up', 'trending_down', 'help',
  'psychology', 'show_chart', 'candlestick_chart',
  'chevron_left', 'chevron_right', 'close', 'edit', 'check',
  'radar', 'auto_awesome',
  'search', 'chevron_down', 'arrow_up_right', 'refresh', 'info',
  'sliders', 'plus', 'more',
]);

function escapeAttribute(value = '') {
  return String(value).replace(/[&<>"']/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[character]);
}

export function renderIcon(name, options = {}) {
  const iconName = ICON_NAMES.has(name) ? name : 'help';
  const size = Math.max(12, Math.min(64, Number(options.size) || 20));
  const className = String(options.className || '')
    .split(/\s+/)
    .filter((part) => /^[a-zA-Z0-9_-]+$/.test(part))
    .join(' ');
  const label = String(options.label || '').trim();
  const accessibility = label
    ? `role="img" aria-label="${escapeAttribute(label)}"`
    : 'aria-hidden="true" focusable="false"';

  return `<svg class="ui-icon${className ? ` ${className}` : ''}" width="${size}" height="${size}" viewBox="0 0 24 24" ${accessibility}><use href="${SPRITE_URL}#icon-${iconName}"></use></svg>`;
}
