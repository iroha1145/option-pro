import { createRoot } from 'react-dom/client';
import { useEffect, useState } from 'react';
import { BrowserRouter } from 'react-router';
import { ToastProvider } from '../../src/components/Toast';
import { AccessProvider } from '../../src/hooks/useAccess';
import { useToast } from '../../src/hooks/useToast';
import Drawer from '../../src/components/Drawer';
import CommandPalette from '../../src/components/CommandPalette';
import ConfirmDialog from '../../src/components/catalysts/ConfirmDialog';
import LayerMenu from '../../src/components/detail/chart-drawings/LayerMenu';
import DrawingWorkspace from '../../src/components/detail/chart-drawings/DrawingWorkspace';
import type { DrawingController } from '../../src/components/detail/chart-drawings/useDrawingController';
import { settingsFromPreset } from '../../src/components/detail/chart-drawings/analysis/settings';
import MenuSelect from '../../src/components/shared/MenuSelect';
import InfoHint from '../../src/components/shared/InfoHint';
import MobileDock from '../../src/components/MobileDock';
import EventDetail from '../../src/components/breakouts/EventDetail';
import { asFullDetail } from '../../src/components/breakouts/types';
import { getBreakoutEvents, getBreakoutEventDetail } from '../../src/mocks/fixtures2';
import ScanHistoryPopover from '../../src/components/screener/ScanHistoryPopover';
import { stocksApi } from '../../src/api/modules/stocks';
import '../../src/index.css';
import '../../src/styles/transitions-root.css';
import '../../src/styles/transitions-catalog.css';

const searchCalls: string[] = [];
const searchReplies = new Map<string, (results: { ticker: string; name: string; sector: string }[]) => void>();
stocksApi.search = (q) => { searchCalls.push(q); return new Promise((resolve) => searchReplies.set(q, resolve)); };
const noop = () => {};
const eventFixture = asFullDetail(getBreakoutEventDetail(getBreakoutEvents().items[0].event_id));

export function Harness() {
  const toast = useToast();
  const [drawer, setDrawer] = useState(false);
  const [palette, setPalette] = useState(false);
  const [eventOpen, setEventOpen] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [layers, setLayers] = useState(false);
  const [workspace, setWorkspace] = useState(false);
  const [trigger, setTrigger] = useState(true);
  const [value, setValue] = useState(0);
  const [settings, setSettings] = useState(() => settingsFromPreset('structure'));
  const controller = new Proxy({
    tool: 'select', drawings: [], unresolvedIds: [], selected: null, importError: null,
    canUndo: false, canRedo: false, autoPatternsEnabled: true, expanded: workspace,
    syncStatus: 'guest', syncHint: null, setExpanded: setWorkspace, hasRejectedImport: false,
  }, { get: (target, name) => name in target ? target[name as keyof typeof target] : noop }) as unknown as DrawingController;
  useEffect(() => {
    Object.assign(window, { overlayHarness: {
      drawer: setDrawer, palette: setPalette, confirm: setConfirm, layers: setLayers,
      workspace: setWorkspace, event: setEventOpen, trigger: setTrigger, searchCalls,
      reply: (q: string, results: { ticker: string; name: string; sector: string }[]) => searchReplies.get(q)?.(results),
    } });
  }, []);
  return <>
    <main id="background" tabIndex={-1} className="min-h-[180vh] p-4">
      {trigger && <button id="drawer-trigger" onClick={() => setDrawer(true)}>打开测试抽屉</button>}
      <button id="palette-trigger" onClick={() => setPalette(true)}>打开命令</button>
      <button onClick={() => setWorkspace(true)}>打开绘图工作区</button>
      <button id="background-button">背景按钮</button>
      <div id="already-inert" inert><button>原有禁用区域</button></div>
      <ScanHistoryPopover history={[]} />
    </main>
    <Drawer open={drawer} onClose={() => setDrawer(false)} title={<h2>测试详情</h2>}>
      <div className="flex flex-col gap-3 p-4">
        <button id="open-command" onClick={() => setPalette(true)}>抽屉内打开命令</button>
        <button onClick={() => setConfirm(true)}>打开确认</button>
        <button id="open-event" onClick={() => setEventOpen(true)}>打开突破详情</button>
        <button onClick={() => toast.error('测试错误通知', '仍然可以关闭通知')}>显示通知</button>
        <MenuSelect ariaLabel="抽屉选择" value={value} onChange={setValue} options={[{value: 0, label:'全部'}, {value: 10, label:'前十项'}, {value:20, label:'前二十项'}]} />
        <output aria-label="选择结果">{value}</output>
        <InfoHint hint={{ title: '测试说明', body: '背景隔离仍然保留说明浮层' }} />
        <fieldset disabled><button id="fieldset-disabled">不应获得焦点</button></fieldset>
        <div aria-hidden="true"><button id="ancestor-hidden">不应获得焦点</button></div>
        <button id="fixed-focusable" style={{ position: 'fixed', bottom: 8, right: 8 }}>固定位置按钮</button>
      </div>
    </Drawer>
    <CommandPalette open={palette} onClose={() => setPalette(false)} onOpenTicker={noop} onForceRefresh={noop} />
    <ConfirmDialog open={confirm} title="确认测试" onCancel={() => setConfirm(false)} onConfirm={() => setConfirm(false)} />
    <DrawingWorkspace open={workspace} controller={controller} reducedMotion layersOpen={layers} onOpenLayers={() => setLayers(true)}>
      <button id="workspace-chart">图表内容</button>
    </DrawingWorkspace>
    <LayerMenu open={layers} onClose={() => setLayers(false)} settings={settings} onChange={setSettings} />
    <EventDetail event={eventOpen ? eventFixture : null} onClose={() => setEventOpen(false)} onOpenTicker={noop} onShowTickerEvents={noop} />
    <MobileDock />
  </>;
}
createRoot(document.getElementById('root')!).render(<BrowserRouter><AccessProvider><ToastProvider><Harness /></ToastProvider></AccessProvider></BrowserRouter>);
