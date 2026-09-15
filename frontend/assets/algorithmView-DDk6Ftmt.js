const t=new Set;let n=0;function r(){return n}function i(){n+=1;for(const e of t)e();return n}function o(e){return t.add(e),()=>{t.delete(e)}}export{i as b,r as g,o as s};
