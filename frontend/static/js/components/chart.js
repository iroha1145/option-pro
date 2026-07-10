/**
 * Dependency-free market chart.
 *
 * Canvas keeps the detail page usable in offline/private deployments while
 * retaining candles/line mode, volume, EMA20, SMA50 and extended-hours styling.
 */
export function renderChart(container, data = {}, visibleBars = 0, options = {}) {
  container.replaceChildren();

  const mode = options.mode === 'line' ? 'line' : 'candles';
  const bars = normalizeBars(Array.isArray(data) ? data : data.bars || []);
  const ema20 = normalizeMA(Array.isArray(data) ? [] : data.ema20 || []);
  const sma50 = normalizeMA(Array.isArray(data) ? [] : data.sma50 || []);
  if (!bars.length) {
    const empty = document.createElement('div');
    empty.style.cssText = 'height:100%;min-height:256px;display:flex;align-items:center;justify-content:center;color:#70726f;font-size:14px';
    empty.textContent = '暂无数据';
    container.appendChild(empty);
    return null;
  }

  const root = document.createElement('div');
  root.className = 'chart-viewport native-chart-viewport';
  root.style.cssText = 'position:relative;width:100%;height:calc(100% - 28px);min-height:320px;touch-action:none;user-select:none';

  const canvas = document.createElement('canvas');
  canvas.setAttribute('role', 'img');
  canvas.setAttribute('aria-label', mode === 'line' ? '价格线图' : '价格蜡烛图');
  canvas.style.cssText = 'display:block;width:100%;height:100%;cursor:crosshair';
  root.appendChild(canvas);

  const tooltip = document.createElement('div');
  tooltip.hidden = true;
  tooltip.style.cssText = 'position:absolute;z-index:3;pointer-events:none;padding:7px 9px;border:1px solid rgba(20,22,25,.12);border-radius:6px;background:rgba(255,255,255,.94);box-shadow:0 4px 16px rgba(0,0,0,.08);font:11px/1.45 JetBrains Mono,monospace;color:#202320;white-space:nowrap';
  root.appendChild(tooltip);
  container.appendChild(root);

  const legend = buildLegend(mode, bars.some((bar) => bar.extended));
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
  };

  const scheduleDraw = () => {
    if (state.destroyed || state.frame) return;
    state.frame = requestAnimationFrame(() => {
      state.frame = 0;
      drawChart(canvas, bars, ema20, sma50, mode, state);
    });
  };

  const indexAtPointer = (event) => {
    const rect = canvas.getBoundingClientRect();
    const count = Math.max(1, state.end - state.start);
    const plotLeft = 12;
    const plotRight = Math.max(plotLeft + 1, rect.width - 58);
    const ratio = clamp((event.clientX - rect.left - plotLeft) / (plotRight - plotLeft), 0, 1);
    return clamp(state.start + Math.round(ratio * Math.max(0, count - 1)), state.start, state.end - 1);
  };

  const onPointerMove = (event) => {
    if (state.dragX != null && event.buttons === 1) {
      const rect = canvas.getBoundingClientRect();
      const count = Math.max(1, state.dragEnd - state.dragStart);
      const pixelsPerBar = Math.max(2, (rect.width - 70) / count);
      const offset = Math.round((state.dragX - event.clientX) / pixelsPerBar);
      setWindow(state, bars.length, state.dragStart + offset, state.dragEnd + offset);
      state.hoverIndex = null;
      tooltip.hidden = true;
      scheduleDraw();
      return;
    }
    const index = indexAtPointer(event);
    state.hoverIndex = index;
    showTooltip(tooltip, root, event, bars[index]);
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
  const onWheel = (event) => {
    event.preventDefault();
    const count = state.end - state.start;
    if (event.ctrlKey || Math.abs(event.deltaY) > Math.abs(event.deltaX)) {
      const direction = event.deltaY > 0 ? 1 : -1;
      const delta = Math.max(2, Math.round(count * 0.12)) * direction;
      const nextCount = clamp(count + delta, 12, bars.length);
      const anchor = indexAtPointer(event);
      const ratio = count > 1 ? (anchor - state.start) / (count - 1) : 1;
      let start = Math.round(anchor - ratio * (nextCount - 1));
      start = clamp(start, 0, Math.max(0, bars.length - nextCount));
      setWindow(state, bars.length, start, start + nextCount);
    } else {
      const offset = Math.sign(event.deltaX) * Math.max(1, Math.round(count * 0.08));
      setWindow(state, bars.length, state.start + offset, state.end + offset);
    }
    scheduleDraw();
  };

  canvas.addEventListener('pointermove', onPointerMove);
  canvas.addEventListener('pointerdown', onPointerDown);
  canvas.addEventListener('pointerup', stopDrag);
  canvas.addEventListener('pointercancel', stopDrag);
  canvas.addEventListener('pointerleave', onPointerLeave);
  canvas.addEventListener('wheel', onWheel, { passive: false });

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
      root.remove();
      legend.remove();
    },
  };
}

function drawChart(canvas, bars, ema20, sma50, mode, state) {
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
    left: 12,
    right: width - 58,
    top: 10,
    bottom: Math.round(height * 0.74),
    volumeTop: Math.round(height * 0.8),
    volumeBottom: height - 24,
  };
  const timeToIndex = new Map(visible.map((bar, index) => [bar.time, index]));
  const maValues = [...ema20, ...sma50]
    .filter((point) => timeToIndex.has(point.time))
    .map((point) => point.value);
  let minPrice = Math.min(...visible.map((bar) => bar.low), ...maValues);
  let maxPrice = Math.max(...visible.map((bar) => bar.high), ...maValues);
  const pad = Math.max((maxPrice - minPrice) * 0.06, maxPrice * 0.001);
  minPrice -= pad;
  maxPrice += pad;
  const priceRange = maxPrice - minPrice || 1;
  const xFor = (index) => plot.left + ((index + 0.5) / visible.length) * (plot.right - plot.left);
  const yFor = (price) => plot.bottom - ((price - minPrice) / priceRange) * (plot.bottom - plot.top);

  drawGrid(ctx, plot, minPrice, maxPrice, width, visible);
  drawVolumes(ctx, visible, xFor, plot);
  if (mode === 'line') drawCloseLine(ctx, visible, xFor, yFor);
  else drawCandles(ctx, visible, xFor, yFor, plot);
  drawMovingAverage(ctx, ema20, timeToIndex, xFor, yFor, '#2d66c3', []);
  drawMovingAverage(ctx, sma50, timeToIndex, xFor, yFor, '#747571', [5, 4]);

  const hoverLocal = state.hoverIndex == null ? null : state.hoverIndex - state.start;
  if (hoverLocal != null && hoverLocal >= 0 && hoverLocal < visible.length) {
    const x = xFor(hoverLocal);
    const y = yFor(visible[hoverLocal].close);
    ctx.save();
    ctx.strokeStyle = 'rgba(20,22,25,.22)';
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

function drawGrid(ctx, plot, minPrice, maxPrice, width, visible) {
  ctx.save();
  ctx.font = '11px JetBrains Mono, monospace';
  ctx.textBaseline = 'middle';
  ctx.fillStyle = '#747571';
  ctx.strokeStyle = 'rgba(20,22,25,.07)';
  for (let step = 0; step <= 4; step += 1) {
    const y = plot.top + ((plot.bottom - plot.top) * step) / 4;
    const price = maxPrice - ((maxPrice - minPrice) * step) / 4;
    ctx.beginPath();
    ctx.moveTo(plot.left, y);
    ctx.lineTo(plot.right, y);
    ctx.stroke();
    ctx.fillText(formatPriceAxis(price), plot.right + 6, y);
  }
  ctx.textBaseline = 'bottom';
  const tickCount = Math.min(5, visible.length);
  for (let step = 0; step < tickCount; step += 1) {
    const index = tickCount === 1 ? 0 : Math.round((step / (tickCount - 1)) * (visible.length - 1));
    const x = plot.left + ((index + 0.5) / visible.length) * (plot.right - plot.left);
    const date = new Date(visible[index].time * 1000);
    const label = date.toLocaleDateString([], { month: 'numeric', day: 'numeric' });
    const textWidth = ctx.measureText(label).width;
    ctx.fillText(label, clamp(x - textWidth / 2, plot.left, width - textWidth), plot.volumeBottom + 19);
  }
  ctx.restore();
}

function drawVolumes(ctx, visible, xFor, plot) {
  const maxVolume = Math.max(1, ...visible.map((bar) => Math.max(0, bar.volume)));
  const slot = (plot.right - plot.left) / visible.length;
  const barWidth = Math.max(1, slot * 0.68);
  visible.forEach((bar, index) => {
    const height = (Math.max(0, bar.volume) / maxVolume) * (plot.volumeBottom - plot.volumeTop);
    const positive = bar.close >= bar.open;
    ctx.fillStyle = positive
      ? (bar.extended ? 'rgba(0,140,114,.10)' : 'rgba(0,140,114,.22)')
      : (bar.extended ? 'rgba(216,71,71,.10)' : 'rgba(216,71,71,.20)');
    ctx.fillRect(xFor(index) - barWidth / 2, plot.volumeBottom - height, barWidth, height);
  });
}

function drawCandles(ctx, visible, xFor, yFor, plot) {
  const slot = (plot.right - plot.left) / visible.length;
  const bodyWidth = clamp(slot * 0.62, 1, 14);
  visible.forEach((bar, index) => {
    const x = xFor(index);
    const up = bar.close >= bar.open;
    const color = up ? '#008c72' : '#d84747';
    ctx.save();
    ctx.globalAlpha = bar.extended ? 0.46 : 1;
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x, yFor(bar.high));
    ctx.lineTo(x, yFor(bar.low));
    ctx.stroke();
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

function drawCloseLine(ctx, visible, xFor, yFor) {
  ctx.save();
  ctx.strokeStyle = '#008c72';
  ctx.lineWidth = 2.5;
  ctx.lineJoin = 'round';
  ctx.lineCap = 'round';
  ctx.beginPath();
  visible.forEach((bar, index) => {
    const x = xFor(index);
    const y = yFor(bar.close);
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
  const last = visible[visible.length - 1];
  ctx.fillStyle = '#008c72';
  ctx.beginPath();
  ctx.arc(xFor(visible.length - 1), yFor(last.close), 3.5, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

function drawMovingAverage(ctx, points, timeToIndex, xFor, yFor, color, dash) {
  const visible = points.filter((point) => timeToIndex.has(point.time));
  if (visible.length < 2) return;
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.6;
  ctx.setLineDash(dash);
  ctx.beginPath();
  visible.forEach((point, index) => {
    const x = xFor(timeToIndex.get(point.time));
    const y = yFor(point.value);
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
  ctx.restore();
}

function buildLegend(mode, hasExtendedBars) {
  const legend = document.createElement('div');
  legend.className = 'chart-legend';
  const items = mode === 'line'
    ? [['#008c72', 'CLOSE', false], ['#2d66c3', 'EMA 20', false], ['#747571', 'SMA 50', true]]
    : [['#2d66c3', 'EMA 20', false], ['#747571', 'SMA 50', true]];
  if (hasExtendedBars) items.push(['rgba(20,22,25,.28)', 'EXT 盘前/盘后', false]);
  items.forEach(([color, label, dashed]) => {
    const item = document.createElement('span');
    item.className = 'chart-legend__item';
    const swatch = document.createElement('span');
    swatch.className = 'chart-legend__swatch';
    swatch.style.background = dashed ? 'transparent' : color;
    if (dashed) swatch.style.borderTop = `2px dashed ${color}`;
    item.append(swatch, document.createTextNode(label));
    legend.appendChild(item);
  });
  return legend;
}

function showTooltip(tooltip, root, event, bar) {
  if (!bar) return;
  const date = new Date(bar.time * 1000);
  tooltip.textContent = `${date.toLocaleString()}  O ${formatNumber(bar.open)}  H ${formatNumber(bar.high)}  L ${formatNumber(bar.low)}  C ${formatNumber(bar.close)}  V ${formatVolume(bar.volume)}${bar.extended ? '  EXT' : ''}`;
  tooltip.hidden = false;
  const rootRect = root.getBoundingClientRect();
  const maxLeft = Math.max(6, rootRect.width - tooltip.offsetWidth - 6);
  tooltip.style.left = `${clamp(event.clientX - rootRect.left + 12, 6, maxLeft)}px`;
  tooltip.style.top = `${clamp(event.clientY - rootRect.top - tooltip.offsetHeight - 10, 6, rootRect.height - tooltip.offsetHeight - 6)}px`;
}

function setWindow(state, total, requestedStart, requestedEnd) {
  const count = clamp(requestedEnd - requestedStart, 1, total);
  let start = requestedStart;
  if (start < 0) start = 0;
  if (start + count > total) start = total - count;
  state.start = Math.round(start);
  state.end = Math.round(start + count);
}

function formatPriceAxis(value) {
  const abs = Math.abs(value);
  if (abs >= 1000) return value.toLocaleString(undefined, { maximumFractionDigits: 0 });
  if (abs >= 10) return value.toFixed(2);
  return value.toFixed(3);
}

function formatNumber(value) {
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 });
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
  return bars
    .map((bar) => ({
      time: Math.floor(Number(bar.t ?? bar.time) > 1e12 ? Number(bar.t ?? bar.time) / 1000 : Number(bar.t ?? bar.time)),
      open: Number(bar.o ?? bar.open),
      high: Number(bar.h ?? bar.high),
      low: Number(bar.l ?? bar.low),
      close: Number(bar.c ?? bar.close),
      volume: Number(bar.v ?? bar.volume ?? 0),
      extended: Boolean(bar.ext ?? bar.extended),
      quoteOnly: Boolean(bar.quote_only ?? bar.quoteOnly),
      session: String(bar.session || ''),
    }))
    .filter((bar) => (
      Number.isFinite(bar.time)
      && Number.isFinite(bar.open)
      && Number.isFinite(bar.high)
      && Number.isFinite(bar.low)
      && Number.isFinite(bar.close)
      && bar.time > 0
      && bar.low > 0
      && bar.high >= Math.max(bar.open, bar.close)
      && bar.low <= Math.min(bar.open, bar.close)
    ))
    .sort((a, b) => a.time - b.time);
}

function normalizeMA(points) {
  return points
    .map((point) => ({
      time: Math.floor(Number(point.time ?? point.t) > 1e12 ? Number(point.time ?? point.t) / 1000 : Number(point.time ?? point.t)),
      value: Number(point.value),
    }))
    .filter((point) => Number.isFinite(point.time) && Number.isFinite(point.value) && point.time > 0)
    .sort((a, b) => a.time - b.time);
}
