/**
 * Dependency-free market chart for the instrument detail page.
 *
 * The backend owns price adjustment and session classification. This renderer
 * keeps those semantics visible while supporting mouse, touch and keyboard
 * navigation without loading a third-party chart bundle.
 */
export function renderChart(container, data = {}, visibleBars = 0, options = {}) {
  container.replaceChildren();

  const payload = Array.isArray(data) ? { bars: data } : (data || {});
  const mode = options.mode === 'line' ? 'line' : 'candles';
  const bars = normalizeBars(payload.bars || []);
  const ema20 = normalizeMA(payload.ema20 || []);
  const sma50 = normalizeMA(payload.sma50 || []);
  const exchangeTimezone = payload.exchange_timezone || options.exchangeTimezone || 'America/New_York';
  const intraday = /(?:m|h)$/.test(String(payload.interval || ''));

  if (!bars.length) {
    const empty = document.createElement('div');
    empty.className = 'market-chart-empty';
    empty.textContent = '所选周期暂无可用价格数据';
    container.appendChild(empty);
    return null;
  }

  const root = document.createElement('div');
  root.className = 'market-chart';

  const canvas = document.createElement('canvas');
  canvas.className = 'market-chart__canvas';
  canvas.tabIndex = 0;
  canvas.setAttribute('role', 'img');
  canvas.setAttribute(
    'aria-label',
    `${mode === 'line' ? '价格线图' : '价格蜡烛图'}。方向键平移，加号和减号缩放，结束键回到最新数据。`,
  );
  root.appendChild(canvas);

  const tooltip = document.createElement('div');
  tooltip.className = 'market-chart__tooltip';
  tooltip.hidden = true;
  tooltip.setAttribute('role', 'tooltip');
  root.appendChild(tooltip);
  container.appendChild(root);

  const legend = buildLegend(mode, bars);
  container.appendChild(legend);

  const initialCount = visibleBars > 0 ? Math.max(12, Math.floor(visibleBars)) : bars.length;
  const state = {
    destroyed: false,
    hoverIndex: null,
    start: Math.max(0, bars.length - initialCount),
    end: bars.length,
    dragX: null,
    dragStart: 0,
    dragEnd: bars.length,
    frame: 0,
    palette: readPalette(root),
  };

  const scheduleDraw = () => {
    if (state.destroyed || state.frame) return;
    state.frame = requestAnimationFrame(() => {
      state.frame = 0;
      state.palette = readPalette(root);
      drawChart(
        canvas,
        bars,
        ema20,
        sma50,
        mode,
        state,
        exchangeTimezone,
        intraday,
      );
    });
  };

  const indexAtPointer = (event) => {
    const rect = canvas.getBoundingClientRect();
    const count = Math.max(1, state.end - state.start);
    const plotLeft = 14;
    const plotRight = Math.max(plotLeft + 1, rect.width - 66);
    const ratio = clamp((event.clientX - rect.left - plotLeft) / (plotRight - plotLeft), 0, 1);
    return clamp(
      state.start + Math.round(ratio * Math.max(0, count - 1)),
      state.start,
      state.end - 1,
    );
  };

  const onPointerMove = (event) => {
    if (state.dragX != null && event.buttons === 1) {
      const rect = canvas.getBoundingClientRect();
      const count = Math.max(1, state.dragEnd - state.dragStart);
      const pixelsPerBar = Math.max(2, (rect.width - 80) / count);
      const offset = Math.round((state.dragX - event.clientX) / pixelsPerBar);
      setWindow(state, bars.length, state.dragStart + offset, state.dragEnd + offset);
      state.hoverIndex = null;
      tooltip.hidden = true;
      scheduleDraw();
      return;
    }
    const index = indexAtPointer(event);
    state.hoverIndex = index;
    showTooltip(tooltip, root, event, bars[index], exchangeTimezone, intraday);
    scheduleDraw();
  };

  const onPointerDown = (event) => {
    state.dragX = event.clientX;
    state.dragStart = state.start;
    state.dragEnd = state.end;
    canvas.setPointerCapture?.(event.pointerId);
  };

  const stopDrag = (event) => {
    state.dragX = null;
    canvas.releasePointerCapture?.(event.pointerId);
  };

  const onPointerLeave = () => {
    if (state.dragX == null) {
      state.hoverIndex = null;
      tooltip.hidden = true;
      scheduleDraw();
    }
  };

  const zoomAt = (nextCount, anchor) => {
    const count = state.end - state.start;
    const boundedCount = clamp(nextCount, Math.min(12, bars.length), bars.length);
    const ratio = count > 1 ? (anchor - state.start) / (count - 1) : 1;
    let start = Math.round(anchor - ratio * (boundedCount - 1));
    start = clamp(start, 0, Math.max(0, bars.length - boundedCount));
    setWindow(state, bars.length, start, start + boundedCount);
  };

  const onWheel = (event) => {
    event.preventDefault();
    const count = state.end - state.start;
    if (event.ctrlKey || Math.abs(event.deltaY) > Math.abs(event.deltaX)) {
      const direction = event.deltaY > 0 ? 1 : -1;
      const delta = Math.max(2, Math.round(count * 0.12)) * direction;
      zoomAt(count + delta, indexAtPointer(event));
    } else {
      const offset = Math.sign(event.deltaX) * Math.max(1, Math.round(count * 0.08));
      setWindow(state, bars.length, state.start + offset, state.end + offset);
    }
    scheduleDraw();
  };

  const onKeyDown = (event) => {
    const count = state.end - state.start;
    const panStep = Math.max(1, Math.round(count * 0.08));
    if (event.key === 'ArrowLeft') {
      setWindow(state, bars.length, state.start - panStep, state.end - panStep);
    } else if (event.key === 'ArrowRight') {
      setWindow(state, bars.length, state.start + panStep, state.end + panStep);
    } else if (event.key === '+' || event.key === '=') {
      zoomAt(count - Math.max(2, Math.round(count * 0.12)), state.end - 1);
    } else if (event.key === '-' || event.key === '_') {
      zoomAt(count + Math.max(2, Math.round(count * 0.12)), state.end - 1);
    } else if (event.key === 'Home') {
      setWindow(state, bars.length, 0, count);
    } else if (event.key === 'End') {
      setWindow(state, bars.length, bars.length - count, bars.length);
    } else {
      return;
    }
    event.preventDefault();
    tooltip.hidden = true;
    state.hoverIndex = null;
    scheduleDraw();
  };

  canvas.addEventListener('pointermove', onPointerMove);
  canvas.addEventListener('pointerdown', onPointerDown);
  canvas.addEventListener('pointerup', stopDrag);
  canvas.addEventListener('pointercancel', stopDrag);
  canvas.addEventListener('pointerleave', onPointerLeave);
  canvas.addEventListener('wheel', onWheel, { passive: false });
  canvas.addEventListener('keydown', onKeyDown);

  const resizeObserver = typeof ResizeObserver === 'function'
    ? new ResizeObserver(scheduleDraw)
    : null;
  resizeObserver?.observe(root);
  window.addEventListener('resize', scheduleDraw);
  scheduleDraw();

  return {
    chart: { redraw: scheduleDraw },
    destroy() {
      if (state.destroyed) return;
      state.destroyed = true;
      if (state.frame) cancelAnimationFrame(state.frame);
      resizeObserver?.disconnect();
      window.removeEventListener('resize', scheduleDraw);
      canvas.removeEventListener('pointermove', onPointerMove);
      canvas.removeEventListener('pointerdown', onPointerDown);
      canvas.removeEventListener('pointerup', stopDrag);
      canvas.removeEventListener('pointercancel', stopDrag);
      canvas.removeEventListener('pointerleave', onPointerLeave);
      canvas.removeEventListener('wheel', onWheel);
      canvas.removeEventListener('keydown', onKeyDown);
      root.remove();
      legend.remove();
    },
  };
}

function drawChart(canvas, bars, ema20, sma50, mode, state, exchangeTimezone, intraday) {
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(320, Math.round(rect.width));
  const height = Math.max(260, Math.round(rect.height));
  const ratio = Math.min(2, window.devicePixelRatio || 1);
  const pixelWidth = Math.round(width * ratio);
  const pixelHeight = Math.round(height * ratio);
  if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
    canvas.width = pixelWidth;
    canvas.height = pixelHeight;
  }

  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, width, height);

  const visible = bars.slice(state.start, state.end);
  if (!visible.length) return;
  const plot = {
    left: 14,
    right: width - 66,
    top: 12,
    bottom: Math.round(height * 0.72),
    volumeTop: Math.round(height * 0.79),
    volumeBottom: height - 30,
  };
  const timeToIndex = new Map(visible.map((bar, index) => [bar.time, index]));
  const maValues = [...ema20, ...sma50]
    .filter((point) => timeToIndex.has(point.time))
    .map((point) => point.value);
  const visibleLows = visible.map((bar) => (
    bar.quoteOnly ? Math.min(bar.open, bar.close) : bar.low
  ));
  const visibleHighs = visible.map((bar) => (
    bar.quoteOnly ? Math.max(bar.open, bar.close) : bar.high
  ));
  let minPrice = Math.min(...visibleLows, ...maValues);
  let maxPrice = Math.max(...visibleHighs, ...maValues);
  const pad = Math.max((maxPrice - minPrice) * 0.06, maxPrice * 0.001);
  minPrice -= pad;
  maxPrice += pad;
  const priceRange = maxPrice - minPrice || 1;
  const xFor = (index) => plot.left + ((index + 0.5) / visible.length) * (plot.right - plot.left);
  const yFor = (price) => plot.bottom - ((price - minPrice) / priceRange) * (plot.bottom - plot.top);

  drawSessionBands(ctx, visible, xFor, plot, state.palette);
  drawGrid(
    ctx,
    plot,
    minPrice,
    maxPrice,
    width,
    visible,
    exchangeTimezone,
    intraday,
    state.palette,
  );
  drawVolumes(ctx, visible, xFor, plot, state.palette);
  if (mode === 'line') drawCloseLine(ctx, visible, xFor, yFor, state.palette);
  else drawCandles(ctx, visible, xFor, yFor, plot, state.palette);
  drawMovingAverage(ctx, ema20, timeToIndex, xFor, yFor, state.palette.ema, []);
  drawMovingAverage(ctx, sma50, timeToIndex, xFor, yFor, state.palette.sma, [5, 4]);

  const hoverLocal = state.hoverIndex == null ? null : state.hoverIndex - state.start;
  if (hoverLocal != null && hoverLocal >= 0 && hoverLocal < visible.length) {
    const x = xFor(hoverLocal);
    const y = yFor(visible[hoverLocal].close);
    ctx.save();
    ctx.globalAlpha = 0.48;
    ctx.strokeStyle = state.palette.muted;
    ctx.setLineDash([3, 4]);
    ctx.beginPath();
    ctx.moveTo(x, plot.top);
    ctx.lineTo(x, plot.volumeBottom);
    ctx.moveTo(plot.left, y);
    ctx.lineTo(plot.right, y);
    ctx.stroke();
    ctx.restore();
  }
}

function drawSessionBands(ctx, visible, xFor, plot, palette) {
  if (!visible.some((bar) => bar.extended)) return;
  const slot = (plot.right - plot.left) / visible.length;
  ctx.save();
  visible.forEach((bar, index) => {
    if (!bar.extended) return;
    ctx.globalAlpha = bar.quoteOnly ? 0.075 : 0.04;
    ctx.fillStyle = palette.extended;
    ctx.fillRect(xFor(index) - slot / 2, plot.top, slot, plot.volumeBottom - plot.top);
  });
  ctx.restore();
}

function drawGrid(ctx, plot, minPrice, maxPrice, width, visible, exchangeTimezone, intraday, palette) {
  ctx.save();
  ctx.font = '11px ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace';
  ctx.textBaseline = 'middle';
  ctx.fillStyle = palette.muted;
  ctx.strokeStyle = palette.grid;
  for (let step = 0; step <= 4; step += 1) {
    const y = plot.top + ((plot.bottom - plot.top) * step) / 4;
    const price = maxPrice - ((maxPrice - minPrice) * step) / 4;
    ctx.beginPath();
    ctx.moveTo(plot.left, y);
    ctx.lineTo(plot.right, y);
    ctx.stroke();
    ctx.fillText(formatPrice(price), plot.right + 7, y);
  }
  ctx.textBaseline = 'bottom';
  const tickCount = Math.min(5, visible.length);
  for (let step = 0; step < tickCount; step += 1) {
    const index = tickCount === 1
      ? 0
      : Math.round((step / (tickCount - 1)) * (visible.length - 1));
    const x = plot.left + ((index + 0.5) / visible.length) * (plot.right - plot.left);
    const label = formatAxisTime(visible[index].time, exchangeTimezone, intraday);
    const textWidth = ctx.measureText(label).width;
    ctx.fillText(label, clamp(x - textWidth / 2, plot.left, width - textWidth), plot.volumeBottom + 23);
  }
  ctx.restore();
}

function drawVolumes(ctx, visible, xFor, plot, palette) {
  const maxVolume = Math.max(1, ...visible.map((bar) => Math.max(0, bar.volume)));
  const slot = (plot.right - plot.left) / visible.length;
  const barWidth = Math.max(1, slot * 0.68);
  visible.forEach((bar, index) => {
    const height = (Math.max(0, bar.volume) / maxVolume) * (plot.volumeBottom - plot.volumeTop);
    const positive = bar.close >= bar.open;
    ctx.save();
    ctx.globalAlpha = bar.extended ? 0.12 : 0.24;
    ctx.fillStyle = positive ? palette.positive : palette.negative;
    ctx.fillRect(xFor(index) - barWidth / 2, plot.volumeBottom - height, barWidth, height);
    ctx.restore();
  });
}

function drawCandles(ctx, visible, xFor, yFor, plot, palette) {
  const slot = (plot.right - plot.left) / visible.length;
  const bodyWidth = clamp(slot * 0.62, 1, 14);
  visible.forEach((bar, index) => {
    const x = xFor(index);
    const up = bar.close >= bar.open;
    const color = up ? palette.positive : palette.negative;
    ctx.save();
    ctx.globalAlpha = bar.extended ? 0.58 : 1;
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.lineWidth = 1;
    if (!bar.quoteOnly) {
      ctx.beginPath();
      ctx.moveTo(x, yFor(bar.high));
      ctx.lineTo(x, yFor(bar.low));
      ctx.stroke();
    }
    const openY = yFor(bar.open);
    const closeY = yFor(bar.close);
    const top = Math.min(openY, closeY);
    const height = Math.max(1, Math.abs(closeY - openY));
    if (bar.quoteOnly) {
      ctx.setLineDash([2, 2]);
      ctx.strokeRect(x - bodyWidth / 2, top, bodyWidth, height);
    } else {
      ctx.fillRect(x - bodyWidth / 2, top, bodyWidth, height);
    }
    ctx.restore();
  });
}

function drawCloseLine(ctx, visible, xFor, yFor, palette) {
  if (visible.length < 2) return;
  ctx.save();
  ctx.lineWidth = 2.25;
  ctx.lineJoin = 'round';
  ctx.lineCap = 'round';
  for (let index = 1; index < visible.length; index += 1) {
    const previous = visible[index - 1];
    const current = visible[index];
    ctx.globalAlpha = previous.extended || current.extended ? 0.48 : 1;
    ctx.strokeStyle = palette.accent;
    ctx.setLineDash(current.quoteOnly ? [3, 3] : []);
    ctx.beginPath();
    ctx.moveTo(xFor(index - 1), yFor(previous.close));
    ctx.lineTo(xFor(index), yFor(current.close));
    ctx.stroke();
  }
  const last = visible[visible.length - 1];
  ctx.globalAlpha = last.extended ? 0.55 : 1;
  ctx.fillStyle = palette.accent;
  ctx.beginPath();
  ctx.arc(xFor(visible.length - 1), yFor(last.close), 3.5, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

function drawMovingAverage(ctx, points, timeToIndex, xFor, yFor, color, dash) {
  const visible = points
    .filter((point) => timeToIndex.has(point.time))
    .map((point) => ({ ...point, index: timeToIndex.get(point.time) }));
  if (visible.length < 2) return;
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.6;
  ctx.setLineDash(dash);
  ctx.beginPath();
  visible.forEach((point, index) => {
    const x = xFor(point.index);
    const y = yFor(point.value);
    const previous = visible[index - 1];
    if (!previous || point.index - previous.index > 1) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
  ctx.restore();
}

function buildLegend(mode, bars) {
  const legend = document.createElement('div');
  legend.className = 'market-chart-legend';
  const items = mode === 'line'
    ? [
      ['accent', '收盘价', false],
      ['ema', '指数移动平均线（EMA 20）· 常规盘', false],
      ['sma', '简单移动平均线（SMA 50）· 常规盘', true],
    ]
    : [
      ['ema', '指数移动平均线（EMA 20）· 常规盘', false],
      ['sma', '简单移动平均线（SMA 50）· 常规盘', true],
    ];
  if (bars.some((bar) => bar.extended)) {
    items.push(['extended', '盘前 / 盘后', false]);
  }
  if (bars.some((bar) => bar.quoteOnly)) {
    items.push(['extended', '仅报价', true]);
  }
  items.forEach(([tone, label, dashed]) => {
    const item = document.createElement('span');
    item.className = 'market-chart-legend__item';
    const swatch = document.createElement('span');
    swatch.className = `market-chart-legend__swatch is-${tone}${dashed ? ' is-dashed' : ''}`;
    item.append(swatch, document.createTextNode(label));
    legend.appendChild(item);
  });
  return legend;
}

function showTooltip(tooltip, root, event, bar, exchangeTimezone, intraday) {
  if (!bar) return;
  tooltip.replaceChildren();

  const exchangeLine = document.createElement('strong');
  exchangeLine.textContent = `交易所时间 · ${formatFullTime(bar.time, exchangeTimezone, intraday)}`;
  const localLine = document.createElement('span');
  localLine.textContent = `本地时间 · ${formatFullTime(bar.time, undefined, intraday)}`;
  const sessionLine = document.createElement('span');
  sessionLine.className = 'market-chart__tooltip-session';
  sessionLine.textContent = sessionLabel(bar);
  const priceLine = document.createElement('span');
  priceLine.className = 'market-chart__tooltip-prices';
  const high = bar.quoteOnly ? '—' : formatPrice(bar.high);
  const low = bar.quoteOnly ? '—' : formatPrice(bar.low);
  priceLine.textContent = `开 ${formatPrice(bar.open)}  高 ${high}  低 ${low}  收 ${formatPrice(bar.close)}`;
  const volumeLine = document.createElement('span');
  volumeLine.textContent = `成交量 ${bar.quoteOnly ? '未报告' : formatVolume(bar.volume)}`;
  tooltip.append(exchangeLine, localLine, sessionLine, priceLine, volumeLine);
  tooltip.hidden = false;

  const rootRect = root.getBoundingClientRect();
  const maxLeft = Math.max(6, rootRect.width - tooltip.offsetWidth - 6);
  tooltip.style.left = `${clamp(event.clientX - rootRect.left + 12, 6, maxLeft)}px`;
  tooltip.style.top = `${clamp(
    event.clientY - rootRect.top - tooltip.offsetHeight - 10,
    6,
    rootRect.height - tooltip.offsetHeight - 6,
  )}px`;
}

function setWindow(state, total, requestedStart, requestedEnd) {
  const count = clamp(requestedEnd - requestedStart, 1, total);
  let start = requestedStart;
  if (start < 0) start = 0;
  if (start + count > total) start = total - count;
  state.start = Math.round(start);
  state.end = Math.round(start + count);
}

function sessionLabel(bar) {
  const labels = {
    regular: '常规交易时段',
    pre: '盘前交易时段',
    post: '盘后交易时段',
    extended: '盘前 / 盘后交易时段',
  };
  const base = labels[bar.session] || labels.regular;
  return bar.quoteOnly ? `${base} · 仅报价` : base;
}

function formatAxisTime(timestamp, timeZone, intraday) {
  const options = intraday
    ? { timeZone, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }
    : { timeZone, year: '2-digit', month: '2-digit', day: '2-digit' };
  return safeDateFormat(timestamp, options);
}

function formatFullTime(timestamp, timeZone, intraday) {
  const options = intraday
    ? {
      timeZone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
      timeZoneName: 'short',
    }
    : { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' };
  return safeDateFormat(timestamp, options);
}

function safeDateFormat(timestamp, options) {
  try {
    return new Intl.DateTimeFormat('zh-CN', options).format(new Date(timestamp * 1000));
  } catch (_) {
    const fallback = { ...options };
    delete fallback.timeZone;
    return new Intl.DateTimeFormat('zh-CN', fallback).format(new Date(timestamp * 1000));
  }
}

function formatPrice(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return '—';
  const abs = Math.abs(number);
  let maximumFractionDigits = 2;
  let minimumFractionDigits = 2;
  if (abs < 1) {
    maximumFractionDigits = abs < 0.01 ? 8 : 6;
    minimumFractionDigits = 4;
  } else if (abs < 10) {
    maximumFractionDigits = 4;
  } else if (abs >= 1000) {
    maximumFractionDigits = 2;
  }
  return number.toLocaleString(undefined, { minimumFractionDigits, maximumFractionDigits });
}

function formatVolume(value) {
  if (!Number.isFinite(value)) return '—';
  if (value >= 1e9) return `${(value / 1e9).toFixed(1)}B`;
  if (value >= 1e6) return `${(value / 1e6).toFixed(1)}M`;
  if (value >= 1e3) return `${(value / 1e3).toFixed(1)}K`;
  return String(Math.round(value));
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function normalizeBars(bars) {
  const byTime = new Map();
  bars.forEach((bar) => {
    const rawTime = Number(bar.t ?? bar.time);
    const time = Math.floor(rawTime > 1e12 ? rawTime / 1000 : rawTime);
    const open = Number(bar.o ?? bar.open);
    const high = Number(bar.h ?? bar.high);
    const low = Number(bar.l ?? bar.low);
    const close = Number(bar.c ?? bar.close);
    const rawVolume = Number(bar.v ?? bar.volume ?? 0);
    const volume = Number.isFinite(rawVolume) && rawVolume >= 0 ? Math.floor(rawVolume) : -1;
    const sourceSession = String(bar.session || '');
    const extended = Boolean(bar.ext ?? bar.extended) || sourceSession === 'pre' || sourceSession === 'post';
    const session = ['regular', 'pre', 'post'].includes(sourceSession)
      ? sourceSession
      : (extended ? 'extended' : 'regular');
    const normalized = {
      time,
      open,
      high,
      low,
      close,
      volume,
      extended,
      quoteOnly: Boolean(bar.quote_only ?? bar.quoteOnly),
      session,
    };
    if (
      Number.isFinite(time)
      && Number.isFinite(open)
      && Number.isFinite(high)
      && Number.isFinite(low)
      && Number.isFinite(close)
      && time > 0
      && open > 0
      && close > 0
      && low > 0
      && high >= Math.max(open, close)
      && low <= Math.min(open, close)
      && volume >= 0
    ) {
      byTime.set(time, normalized);
    }
  });
  return [...byTime.values()].sort((a, b) => a.time - b.time);
}

function normalizeMA(points) {
  const byTime = new Map();
  points.forEach((point) => {
    const rawTime = Number(point.time ?? point.t);
    const time = Math.floor(rawTime > 1e12 ? rawTime / 1000 : rawTime);
    const value = Number(point.value);
    if (Number.isFinite(time) && Number.isFinite(value) && time > 0 && value > 0) {
      byTime.set(time, { time, value });
    }
  });
  return [...byTime.values()].sort((a, b) => a.time - b.time);
}

function readPalette(root) {
  const styles = getComputedStyle(root);
  const color = (name, fallback) => styles.getPropertyValue(name).trim() || fallback;
  return {
    accent: color('--accent', '#256b68'),
    positive: color('--positive', '#08745b'),
    negative: color('--negative', '#c13d4b'),
    muted: color('--ink-muted', '#66737b'),
    grid: color('--border-subtle', '#dde4e7'),
    ema: color('--chart-ema', color('--accent', '#256b68')),
    sma: color('--chart-sma', '#7a6d61'),
    extended: color('--chart-extended', '#b57419'),
  };
}
