import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import Icon, { type IconName } from '@/components/icons';
import { BusyIcon } from '@/components/shared/IconSwap';
import '@/styles/transitions-root.css';
import '@/index.css';
import '@/styles/transitions-catalog.css';
import './icon-motion-preview.css';

const examples: { name: IconName; label: string; description: string }[] = [
  { name: 'search', label: '搜索', description: '镜片轻移，发现新的线索。' },
  { name: 'sliders', label: '显示设置', description: '旋钮错落滑动，调整阅读偏好。' },
  { name: 'star-line', label: '我的关注', description: '星标轻转，回应你的关注。' },
  { name: 'radar', label: '突破雷达', description: '信号轻闪，呼应探查动作。' },
  { name: 'candle', label: '行情', description: '短促的线条反馈，保留原有轮廓。' },
  { name: 'filter-funnel', label: '条件选股', description: '漏斗轻收，聚焦筛选条件。' },
  { name: 'wallet-gauge', label: '市场', description: '指针轻摆，随后回到原位。' },
  { name: 'calendar-spark', label: '财报日历', description: '日期标记轻亮，回应查看动作。' },
  { name: 'layers', label: '行业表现', description: '层次错落，保持安静的节奏。' },
  { name: 'bolt', label: '新闻', description: '短促闪动，点到即止。' },
  { name: 'refresh', label: '刷新', description: '箭头转动一次，不表示正在加载。' },
  { name: 'sun-bmo', label: '浅色外观', description: '光线舒展，主题切换仍即时响应。' },
  { name: 'moon-amc', label: '深色外观', description: '星光轻闪，月牙保留安静轮廓。' },
  { name: 'menu', label: '更多', description: '线条轻移，提示展开更多入口。' },
];

export default function Preview() {
  const [dark, setDark] = useState(false);
  const [staticOnly, setStaticOnly] = useState(false);
  const [replay, setReplay] = useState(0);
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState('star-line');
  const [playing, setPlaying] = useState<string[]>([]);
  useEffect(() => {
    if (!playing.length) return;
    const timer = window.setTimeout(() => setPlaying([]), 1200);
    return () => window.clearTimeout(timer);
  }, [playing]);
  return <main className="motion-preview">
    <header className="preview-header">
      <a href="/" className="preview-brand"><img src="/logo.svg" alt="" />Optix Pro</a>
      <button className="preview-control" onClick={() => {
        setDark(!dark);
        document.documentElement.classList.toggle('dark', !dark);
      }} aria-pressed={dark}>{dark ? '浅色预览' : '深色预览'}</button>
    </header>
    <section className="preview-intro">
      <p className="preview-eyebrow">图标动效 · 第二版</p>
      <h1>让熟悉的图标，<br />多一点回应。</h1>
      <p>沿用原来的细线轮廓，参考 <a href="https://lucide-animated.com/">Lucide Animated</a> 的局部动作。悬停、键盘聚焦或轻点图标，即可观看。</p>
      <div className="preview-actions">
        <button className="preview-control preview-primary" onClick={() => {
          setReplay(value => value + 1);
          setPlaying(examples.map(item => item.name));
        }}>播放全部一次</button>
        <button className="preview-control" aria-pressed={staticOnly} onClick={() => setStaticOnly(value => !value)}>{staticOnly ? '开启动效' : '静态对照'}</button>
        <a className="preview-control" href="/" target="_blank" rel="noreferrer">查看应用试作 ↗</a>
      </div>
      <p className="preview-note">动作约 0.8–1 秒，幅度加大，播放后停下。系统开启“减少动态效果”时保持静态。应用入口使用示例行情。</p>
    </section>
    <section className="preview-grid" aria-label="图标动效对照">
      {examples.map(({ name, label, description }) => <article className="preview-item" key={name}>
        <div className="preview-comparison">
          <div className="preview-static"><Icon name={name} size={32} /><span>原图标</span></div>
          <span className="preview-divider" aria-hidden="true">→</span>
          <button className="icon-motion-trigger preview-play" aria-label={`播放${label}动效`} onClick={() => {
            setPlaying([name]);
          }}>
            <Icon key={replay} name={name} size={32} motion={!staticOnly} motionActive={playing.includes(name)} />
            <span>{staticOnly ? '静态对照' : '动效试作'}</span>
          </button>
        </div>
        <h2>{label}</h2><p>{description}</p>
      </article>)}
    </section>
    <section className="preview-context" aria-label="实际尺寸演示">
      <div><p className="preview-eyebrow">放回界面里</p><h2>动作更清楚，操作如常。</h2><p>这里使用导航的实际尺寸。加载状态继续显示加载圈，避免与悬停动作混淆。</p></div>
      <div className="preview-samples">
        <div className="preview-dock" aria-label="导航尺寸演示">
          {examples.filter(item => ['candle', 'star-line', 'filter-funnel', 'wallet-gauge'].includes(item.name)).map(item => <button key={item.name} className="icon-motion-trigger" aria-pressed={selected === item.name} onClick={() => setSelected(item.name)}>
            <Icon name={item.name} size={19} motion={!staticOnly} motionActive={selected === item.name} /><span>{item.label}</span>
          </button>)}
        </div>
        <button className="preview-control preview-refresh" onClick={() => setBusy(value => !value)} aria-pressed={busy}><BusyIcon busy={busy} />{busy ? '结束加载演示' : '查看加载状态'}</button>
      </div>
    </section>
    <footer className="preview-footer">14 种原图标 · 单次反馈 · 支持键盘与触控 · 尊重系统动态效果偏好</footer>
  </main>;
}

createRoot(document.getElementById('root')!).render(<Preview />);
