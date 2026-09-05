export function series({n = 240, drift = 0.15, amplitude = 7, period = 24, center = 100} = {}) {
  return Array.from({length:n}, (_, i) => {
    const t = new Date(Date.UTC(2025, 0, 1 + i, 21)).toISOString();
    const c = center + drift * i + amplitude * Math.sin(i * 2 * Math.PI / period);
    const o = c + 0.15 * Math.cos(i * 2 * Math.PI / period);
    return {t, key:t.slice(0,10), o, c, h:Math.max(c,o)+0.55, l:Math.min(c,o)-0.55, closed:true};
  });
}
export function barAt(i,c, extra={}) {
  const t = new Date(Date.UTC(2025,0,1+i,21)).toISOString();
  return {t,key:t.slice(0,10),o:c,c,h:c+0.5,l:c-0.5,closed:true,...extra};
}
export function overlay(bars, kind, ys, {id=kind, start=20, end=bars.length-1, quality=0.8, status='forming', ...extra}={}) {
  const rail = (y1,y2) => [{time:bars[start].t,barKey:bars[start].key,price:y1},{time:bars[end].t,barKey:bars[end].key,price:y2}];
  return {id,sourceId:'test',algorithmVersion:'test-v1',group:'price',kind,
    geometry:{anchors:ys.length===4 ? [...rail(...ys.slice(0,2)),...rail(...ys.slice(2))] : rail(...ys)},
    status,direction:'neutral',shapeQuality:quality,displayPriority:80,evidence:{touches:5},
    formationStart:bars[start].key,formationEnd:bars[end].key,dataThrough:bars.at(-1).key,label:'',detail:'',...extra};
}
