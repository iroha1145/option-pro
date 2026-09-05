import test from 'node:test';
import assert from 'node:assert/strict';
import { detectSmartLines, confirmedPivots, smartTolerance, selectSmartOverlays, SMART_MAX_PROPOSALS } from '../src/components/detail/chart-drawings/analysis/smartLines.ts';
import { renderPatternInk, projectPatternRails, manualLineInk, LINE_INK } from '../src/components/detail/chart-drawings/linePresentation.ts';
import { candidatesAtBar, railCandidatesFromOverlays } from '../src/components/detail/chart-drawings/railSnap.ts';
import { series, barAt, overlay } from './fixtures/smart-lines.mjs';

const ctx = {xMin:0,xMax:99,yMin:1,yMax:220};
const segment = (x1,y1,x2,y2) => ({a:{x:x1,y:y1},b:{x:x2,y:y2}});
const inkPattern = (extra={}) => ({id:'a',kind:'support_trend',status:'forming',confidence:80,label:'支撑',...extra});
const stroke = mark => mark[0].lineStyle;
const empty = result => result.lines.length + result.polygons.length + result.points.length === 0;
const freeze = value => { if (value && typeof value === 'object') {Object.freeze(value); Object.values(value).forEach(freeze);} return value; };
const brokenBars = count => {
 const bars=series();
 for (let i=bars.length-count;i<bars.length;i++) {const c=100+0.15*i+12;Object.assign(bars[i],{o:c-0.5,c,h:c+0.5,l:c-0.5});}
 return bars;
};

for (const [drift,subtype] of [[0.15,'rising'],[-0.15,'falling'],[0,'horizontal']]) {
 test(`detects a ${subtype} channel from independent confirmed swings`, () => {
  const result=detectSmartLines(series({drift}));
  assert.ok(result.some(o=>o.kind==='channel' && o.geometry.subtype===subtype));
 });
}
test('channels have genuinely parallel, positively separated rails', () => {
 const bars=series();
 for(const o of detectSmartLines(bars).filter(o=>o.kind==='channel')) {
  const [a,b]=o.geometry.supportRail, [c,d]=o.geometry.resistanceRail;
  assert.ok(c.price>a.price && d.price>b.price);
  assert.ok(Math.abs((b.price-a.price)-(d.price-c.price))<1e-8);
  assert.ok(o.evidence.touches>=5);
 }
});
test('standalone trends survive a channel break with broken resistance explicitly marked',()=>{
 const result=detectSmartLines(brokenBars(2));
 assert.ok(result.some(o=>o.kind==='resistance_trend'&&o.status==='broken_up'));
 assert.ok(result.some(o=>o.kind==='support_trend'&&!o.status.startsWith('broken')));
 assert.ok(!result.some(o=>o.kind==='channel'));
});
test('one closing excursion is not a confirmed two-close break',()=>{
 assert.ok(!detectSmartLines(brokenBars(1)).some(o=>o.status==='broken_up'));
});
test('broken rails stop at the first confirmed break, not at the latest candle',()=>{
 const bars=brokenBars(3); const broken=detectSmartLines(bars).find(o=>o.status==='broken_up');
 assert.ok(broken); assert.equal(broken.formationEnd,bars.at(-2).key);
});
test('horizontal support/resistance proposals are retained independently of channel visibility',()=>{
 const result=detectSmartLines(series({drift:0}));
 assert.ok(result.some(o=>o.kind==='level'&&o.geometry.role==='support'));
 assert.ok(result.some(o=>o.kind==='level'&&o.geometry.role==='resistance'));
});
test('short series do not invent two-point patterns',()=>assert.deepEqual(detectSmartLines(series({n:31})),[]));
test('flat prices do not invent channels or levels',()=>assert.deepEqual(detectSmartLines(Array.from({length:200},(_,i)=>barAt(i,100))),[]));
for(const [name,mutate] of [
 ['NaN',b=>b[50].c=NaN], ['infinity',b=>b[50].h=Infinity], ['zero price',b=>b[50].l=0],
 ['invalid OHLC ordering',b=>b[50].l=b[50].h+1], ['duplicate time',b=>b[50].t=b[49].t],
 ['duplicate key',b=>b[50].key=b[49].key], ['invalid timestamp',b=>b[50].t='garbage'],
 ['reversed order',b=>b.reverse()],
]) test(`rejects ${name} without manufacturing annotations`,()=>{
 const bars=series();mutate(bars);assert.deepEqual(detectSmartLines(bars),[]);
});
for(const flag of ['ext','quote_only','closed']) test(`ignores ineligible final bar (${flag})`,()=>{
 const bars=series();const b=barAt(bars.length,999,{[flag]:flag==='closed'?false:true});
 assert.deepEqual(detectSmartLines([...bars,b]),detectSmartLines(bars));
});
test('no detector output refers to unknown/future anchors',()=>{
 const bars=series();const keys=new Set(bars.map(b=>b.key));
 for(const o of detectSmartLines(bars)) {
  assert.ok(keys.has(o.dataThrough)); assert.ok(keys.has(o.formationStart));assert.ok(keys.has(o.formationEnd));
  for(const a of o.geometry.anchors) assert.ok(keys.has(a.barKey)&&a.price>0&&Number.isFinite(a.price));
  assert.equal(o.evidence.closedBarsOnly,true); assert.equal(o.evidence.visualizationOnly,true);
 }
});
test('confirmed pivots exclude the unconfirmed right edge',()=>{
 const bars=series();const wing=5;
 for(const p of confirmedPivots(bars,wing)) assert.ok(p.x<bars.length-wing);
});
test('already confirmed pivots are causal when later bars are appended',()=>{
 const bars=series();const wing=3, prefix=bars.slice(0,150);
 const historical=confirmedPivots(prefix,wing);
 assert.deepEqual(confirmedPivots(bars,wing).filter(p=>p.x<prefix.length-wing),historical);
});
test('plateau highs/lows do not create a duplicate pivot at every equal bar',()=>{
 assert.deepEqual(confirmedPivots(Array.from({length:40},(_,i)=>barAt(i,100)),2),[]);
});
test('invalid confirmation windows produce no pivots',()=>{
 for(const w of [0,-1,1.5,13,Infinity]) assert.deepEqual(confirmedPivots(series(),w),[]);
});
test('detection is deterministic and does not mutate frozen bars',()=>{
 const bars=freeze(series());assert.deepEqual(detectSmartLines(bars),detectSmartLines(bars));
});
test('work and anchor range are bounded to the most recent 360 bars',()=>{
 const bars=series({n:2000});const result=detectSmartLines(bars);
 assert.ok(result.length<=SMART_MAX_PROPOSALS);
 const recent=new Set(bars.slice(-360).map(b=>b.key));
 for(const o of result) assert.ok(recent.has(o.formationStart));
});
test('median true-range tolerance scales with prices',()=>{
 const bars=series();const scaled=bars.map(b=>({...b,o:b.o*10,h:b.h*10,l:b.l*10,c:b.c*10}));
 assert.ok(Math.abs(smartTolerance(scaled)-smartTolerance(bars)*10)<1e-8);
});
test('one isolated price spike cannot inflate median tolerance arbitrarily',()=>{
 const bars=series();const reference=smartTolerance(bars);bars.at(-5).h*=10;
 assert.ok(smartTolerance(bars)<reference*1.5);
});
test('near duplicate rails are collapsed after filtering',()=>{
 const bars=series();const a=overlay(bars,'support_trend',[100,130],{id:'a'}),b=overlay(bars,'support_trend',[100.01,130.01],{id:'b'});
 assert.equal(selectSmartOverlays([a,b],bars,3).length,1);
});
test('rails crossing only near the last candle are not treated as duplicates',()=>{
 const bars=series();const a=overlay(bars,'support_trend',[100,130],{id:'a'}),b=overlay(bars,'resistance_trend',[150,130],{id:'b'});
 assert.equal(selectSmartOverlays([a,b],bars,3).length,2);
});
test('the selected channel suppresses its duplicate standalone boundaries',()=>{
 const bars=series({drift:0});const c=overlay(bars,'channel',[92,92,108,108]);
 const l=overlay(bars,'support_trend',[92,92]);const h=overlay(bars,'resistance_trend',[108,108]);
 assert.deepEqual(selectSmartOverlays([l,h,c],bars,3).map(o=>o.kind),['channel']);
});
test('filtered-out channels cannot hide retained support/resistance levels',()=>{
 const bars=series({drift:0});const result=detectSmartLines(bars).filter(o=>o.kind==='level');
 assert.equal(selectSmartOverlays(result,bars,0).length,2);
});
test('pattern budget zero still preserves separately enabled non-pattern overlays',()=>{
 const bars=series();const ma=overlay(bars,'ma',[100,120]);const p=overlay(bars,'support_trend',[100,130]);
 assert.deepEqual(selectSmartOverlays([p,ma],bars,0),[ma]);
});
test('non-finite pattern quality is rejected rather than ranked first',()=>{
 const bars=series();const p=overlay(bars,'support_trend',[100,130],{quality:NaN});
 assert.deepEqual(selectSmartOverlays([p],bars,3),[]);
});
test('selection only changes copied display priority, never backend evidence or stored geometry',()=>{
 const bars=series();const p=freeze(overlay(bars,'support_trend',[100,130]));const result=selectSmartOverlays([p],bars,3);
 assert.equal(p.displayPriority,80);assert.equal(result[0].geometry,p.geometry);assert.equal(result[0].evidence,p.evidence);
});
test('manual strokes preserve explicit saved widths and clamp invalid extremes',()=>{
 for(const w of [1,2,3,4]) assert.equal(manualLineInk('#123456',w).width,w);
 assert.equal(manualLineInk('#123456',99).width,4);assert.equal(manualLineInk('#123456',NaN).width,3);
});
test('observed boundaries are solid and distinct from dashed extensions',()=>{
 const out=renderPatternInk(inkPattern(),{segments:[segment(10,70,60,90)],fill:null},ctx);
 assert.equal(out.lines.length,2);assert.equal(stroke(out.lines[0]).type,'solid');assert.deepEqual(stroke(out.lines[1]).type,[7,4]);
 assert.ok(stroke(out.lines[0]).width>=2.5);assert.equal(out.lines[0][0].label.show,false);assert.equal(out.lines[1][0].label.show,true);
});
test('support and resistance use fixed non-candle semantic colors',()=>{
 const geom={segments:[segment(10,70,60,90)],fill:null};
 assert.equal(stroke(renderPatternInk(inkPattern(),geom,ctx).lines[0]).color,LINE_INK.support);
 assert.equal(stroke(renderPatternInk(inkPattern({kind:'resistance_trend'}),geom,ctx).lines[0]).color,LINE_INK.resistance);
 assert.notEqual(LINE_INK.support,LINE_INK.resistance);
});
test('a paired channel uses support color on its actual lower rail',()=>{
 const out=renderPatternInk(inkPattern({kind:'channel'}),{segments:[segment(10,100,60,120),segment(10,80,60,100)],fill:null},ctx);
 assert.equal(stroke(out.lines[0]).color,LINE_INK.resistance);
 assert.equal(stroke(out.lines.find(l=>l[0].coord[1]===80)).color,LINE_INK.support);
 assert.equal(out.polygons[0].opacity,0.035);
});
for(const status of ['broken_up','broken_down','invalidated','failed','expired']) test(`${status} structures fade without extending or filling`,()=>{
 const out=renderPatternInk(inkPattern({status}),{segments:[segment(10,70,60,90)],fill:[{x:10,y:60},{x:60,y:80},{x:60,y:90}]},ctx);
 assert.equal(out.lines.length,1);assert.equal(out.polygons.length,0);assert.equal(stroke(out.lines[0]).opacity,0.38);
 assert.deepEqual(stroke(out.lines[0]).type,[2,4]);
});
test('hidden, NaN, and invalid confidence values cannot produce visible patterns',()=>{
 const geom={segments:[segment(10,70,60,90)],fill:null};
 for(const changes of [{hidden:true},{confidence:NaN},{confidence:101},{confidence:-1}]) assert.ok(empty(renderPatternInk(inkPattern(changes),geom,ctx)));
});
test('bad or non-positive geometry produces no drawing',()=>{
 for(const y of [0,-1,NaN,Infinity]) assert.ok(empty(renderPatternInk(inkPattern(),{segments:[segment(10,y,60,90)],fill:null},ctx)));
});
test('crossed channel rails are rejected, never filled as a bow-tie',()=>{
 assert.ok(empty(renderPatternInk(inkPattern({kind:'channel'}),{segments:[segment(10,100,60,50),segment(10,80,60,90)],fill:null},ctx)));
});
test('incomplete paired geometries are not mislabelled as a channel',()=>{
 assert.ok(empty(renderPatternInk(inkPattern({kind:'channel'}),{segments:[segment(10,70,60,90)],fill:null},ctx)));
});
test('pair fill is clipped to the common observed interval',()=>{
 const out=renderPatternInk(inkPattern({kind:'channel'}),{segments:[segment(10,70,60,90),segment(20,100,70,120)],fill:null},ctx);
 assert.deepEqual(out.polygons[0].vertices.map(p=>p.x),[20,60,60,20]);
});
test('extensions stop strictly before a converging triangle apex',()=>{
 const rails=[segment(10,70,60,90),segment(10,130,60,110)];
 const result=projectPatternRails(rails,'triangle','forming',{...ctx,xMax:160});
 assert.equal(result.length,2);assert.ok(result.every(s=>s.b.x<85));
});
test('extensions never invent a future candle beyond xMax',()=>{
 const result=projectPatternRails([segment(10,70,60,90)],'support_trend','forming',{...ctx,xMax:65});
 assert.equal(result[0].b.x,65);
});
test('extensions are bounded to 48 bars even when the chart contains far more history',()=>{
 const result=projectPatternRails([segment(10,70,110,90)],'support_trend','forming',{...ctx,xMax:1000});
 assert.equal(result[0].b.x,158);
});
test('falling projections stop at a positive chart price instead of running negative',()=>{
 const result=projectPatternRails([segment(10,110,60,10)],'resistance_trend','forming',ctx);
 assert.ok(result[0].b.y>=ctx.yMin);assert.ok(result[0].b.x<65);
});
test('invalid context does not emit extrapolated NaN geometry',()=>{
 assert.deepEqual(projectPatternRails([segment(10,70,60,90)],'support_trend','forming',{...ctx,xMax:NaN}),[]);
});
test('boxes do not grow silently beyond the observed rectangle',()=>{
 assert.deepEqual(projectPatternRails([segment(10,70,60,90)],'box','forming',ctx),[]);
});
test('labels remain explicitly disabled when the caller exhausts its label budget',()=>{
 const result=renderPatternInk(inkPattern({label:undefined}),{segments:[segment(10,70,60,90)],fill:null},ctx);
 assert.ok(result.lines.every(l=>l[0].label.show===false));
});
test('rendering does not mutate shared geometry',()=>{
 const geometry=freeze({segments:[segment(10,70,60,90)],fill:null});
 renderPatternInk(inkPattern(),geometry,ctx);assert.equal(geometry.segments[0].b.x,60);
});
test('sloping snap targets interpolate at the cursor bar, not their historical endpoint',()=>{
 const c={price:100,kind:'level',rail:{from:10,to:30,startPrice:100,endPrice:120}};
 assert.equal(candidatesAtBar([c],20)[0].price,110);assert.equal(c.price,100);
});
test('off-rail or unconfirmed bars have no sloping snap target',()=>{
 const c={price:100,kind:'level',rail:{from:10,to:30,startPrice:100,endPrice:120}};
 for(const x of [9,31,NaN,Infinity]) assert.deepEqual(candidatesAtBar([c],x),[]);
});
test('static horizontal targets remain compatible',()=>{
 const c={price:100,kind:'level'};assert.deepEqual(candidatesAtBar([c],20),[c]);
});
test('bad rail data and degenerate intervals are rejected',()=>{
 for(const r of [{from:1,to:1,startPrice:100,endPrice:200},{from:1,to:2,startPrice:NaN,endPrice:100}])
 assert.deepEqual(candidatesAtBar([{price:100,kind:'level',rail:r}],1),[]);
});
test('explicit point targets do not attract the cursor on distant bars',()=>{
 assert.deepEqual(candidatesAtBar([{price:100,kind:'anchor',atIndex:12}],50),[]);
});
test('rail targets resolve canonical date keys from the current displayed series',()=>{
 const bars=series();const o=overlay(bars,'support_trend',[100,130],{start:20,end:100});
 const result=railCandidatesFromOverlays([o],bars.map(b=>b.key));
 assert.equal(result.length,1);assert.equal(candidatesAtBar(result,60)[0].price,115);
});
test('channels expose two independent snap rails',()=>{
 const bars=series();const o=overlay(bars,'channel',[100,130,115,145]);
 assert.equal(railCandidatesFromOverlays([o],bars.map(b=>b.key)).length,2);
});
test('broken rails do not remain active snap magnets',()=>{
 const bars=series();const o=overlay(bars,'support_trend',[100,130],{status:'broken_down'});
 assert.deepEqual(railCandidatesFromOverlays([o],bars.map(b=>b.key)),[]);
});
test('unresolved and malformed anchor keys produce no snap targets',()=>{
 const bars=series();const o=overlay(bars,'support_trend',[100,130]);
 assert.deepEqual(railCandidatesFromOverlays([o],[]),[]);
 o.geometry={anchors:[{},null]};assert.deepEqual(railCandidatesFromOverlays([o],bars.map(b=>b.key)),[]);
});

test('axis-aligned boxes keep the existing markArea rather than custom-polygon contract',()=>{
 const out=renderPatternInk(inkPattern({kind:'box'}),{segments:[segment(10,70,60,70),segment(60,70,60,90),segment(60,90,10,90),segment(10,90,10,70)],fill:[{x:10,y:70},{x:60,y:70},{x:60,y:90},{x:10,y:90}]},ctx);
 assert.equal(out.areas.length,1);assert.equal(out.polygons.length,0);assert.equal(out.lines.length,4);
});
