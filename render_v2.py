"""v2 interactive renderer: InteractiveNotes -> self-contained interactive HTML.

ONE client-side block-dispatch renderer (JS `renderBlock`). The Python side only
escapes + embeds the JSON island and substitutes the shell.

Safety doctrine:
  * the renderer OWNS all SVG geometry (SVG_TEMPLATES) and sim arithmetic (a
    tokenized evaluator — never eval/new Function);
  * model-authored strings reach the DOM via textContent, NEVER innerHTML — the
    one exception is the `prose` block, which runs `marked` under a math-protect
    step (same discipline as v1).

The JS `renderBlock` dispatcher's block-type set MUST equal schemas_v2.BLOCK_TYPES
and its SVG template names MUST equal schemas_v2.SVG_TEMPLATES — asserted by _smoke.py.
"""
from __future__ import annotations

from schemas_v2 import InteractiveNotes

# CSS lifted verbatim from the approved target (enthalpy-interactive-full.html),
# plus a .prose helper and an unknown-block soft box.
_CSS = r"""
:root{
  --paper:#FAFAF7; --surface:#F1F0EA; --line:#E3E1D8;
  --ink:#16213A; --ink-soft:#5B6478;
  --exo:#E8590C; --exo-soft:#FFF0E6;
  --endo:#1971C2; --endo-soft:#E7F1FB;
  --good:#2F9E44; --good-soft:#EBFAEE;
  --bad:#C92A2A;  --bad-soft:#FDEDED;
  --edex:#6741D9; --edex-soft:#F1EDFD;
  --radius:14px;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.65 Inter,system-ui,sans-serif}
.mono{font-family:'IBM Plex Mono',monospace}
h1,h2,h3{font-family:Fraunces,serif;line-height:1.15;margin:0}
button{font:inherit;cursor:pointer}
:focus-visible{outline:3px solid var(--endo);outline-offset:2px;border-radius:6px}
@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important}}
.topbar{position:sticky;top:0;z-index:50;background:rgba(250,250,247,.92);backdrop-filter:blur(8px);border-bottom:1px solid var(--line)}
.topbar-inner{max-width:880px;margin:0 auto;padding:.6rem 1rem;display:flex;align-items:center;gap:1rem}
.topbar .t{font-weight:600;font-size:.9rem;white-space:nowrap}
.bar{flex:1;height:8px;background:var(--surface);border-radius:99px;overflow:hidden}
.bar > i{display:block;height:100%;width:0%;border-radius:99px;background:linear-gradient(90deg,var(--endo),var(--exo));transition:width .5s ease}
.score{font-family:'IBM Plex Mono',monospace;font-size:.85rem;color:var(--ink-soft);white-space:nowrap}
main{max-width:880px;margin:0 auto;padding:0 1rem 5rem}
.hero{padding:2.6rem 0 0}
.eyebrow{font-size:.78rem;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:var(--ink-soft)}
.hero h1{font-size:clamp(2.3rem,6vw,3.4rem);margin:.35rem 0 .8rem}
.hero p.lede{font-size:1.05rem;color:var(--ink-soft);max-width:58ch;margin:0}
.exam-map{margin-top:1.6rem;border:1px solid #D8CFF5;border-radius:var(--radius);background:var(--edex-soft);padding:1.1rem 1.2rem}
.exam-map .em-title{display:flex;align-items:center;gap:.5rem;font-family:Fraunces,serif;font-size:1.1rem;font-weight:700;color:var(--edex)}
.em-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:.8rem;margin-top:.9rem}
.em-cell{background:#fff;border:1px solid #E4DCF8;border-radius:10px;padding:.7rem .85rem}
.em-cell .k{font-size:.72rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--edex)}
.em-cell .v{font-size:.9rem;margin-top:.2rem;line-height:1.45}
.hook{margin-top:1.4rem;border:1px solid var(--line);border-radius:var(--radius);overflow:hidden;background:#fff}
.hook-head{display:flex;gap:.8rem;align-items:center;padding:1rem 1.2rem;background:linear-gradient(90deg,var(--exo-soft),#fff)}
.hook-head .emoji{font-size:1.6rem}
.hook-head b{font-family:Fraunces,serif;font-size:1.15rem}
.hook-body{padding:0 1.2rem 1.2rem}
.hook-body p{margin:.8rem 0}
.reveal-btn{background:var(--ink);color:#fff;border:0;border-radius:99px;padding:.55rem 1.2rem;font-weight:600;font-size:.9rem}
.reveal-btn:hover{background:#25314f}
.hook-answer{display:none;border-left:4px solid var(--exo);background:var(--exo-soft);border-radius:0 10px 10px 0;padding:.7rem 1rem;margin-top:.9rem}
.hook-answer.show{display:block;animation:pop .35s ease}
@keyframes pop{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.profile{margin-top:1.4rem;border:1px solid var(--line);border-radius:var(--radius);background:#fff;padding:1.1rem 1.2rem}
.profile .row{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:.6rem}
.seg{display:inline-flex;background:var(--surface);border-radius:99px;padding:4px}
.seg button{border:0;background:transparent;border-radius:99px;padding:.4rem 1rem;font-weight:600;font-size:.85rem;color:var(--ink-soft)}
.seg button[aria-pressed="true"].exo{background:var(--exo);color:#fff}
.seg button[aria-pressed="true"].endo{background:var(--endo);color:#fff}
.profile svg{width:100%;height:auto;margin-top:.6rem}
.profile .cap{font-size:.85rem;color:var(--ink-soft);margin:.4rem 0 0}
section{margin-top:3.2rem}
.sec-head{display:flex;align-items:baseline;gap:.7rem;margin-bottom:1rem;flex-wrap:wrap}
.sec-head h2{font-size:1.7rem}
.tick{font-size:1.1rem;color:var(--good);opacity:0;transform:scale(.5);transition:all .3s ease}
.tick.on{opacity:1;transform:scale(1)}
.spec{font-size:.75rem;font-weight:600;color:var(--edex);background:var(--edex-soft);border-radius:99px;padding:.15rem .7rem}
section > p, .prose{max-width:66ch}
.prose :first-child{margin-top:0}
.callout{margin-top:1.2rem;border-radius:var(--radius);padding:.9rem 1.1rem;font-size:.93rem;border:1px solid var(--line);border-left-width:5px;background:#fff}
.callout b.ct{display:block;margin-bottom:.25rem}
.callout.cp{border-left-color:var(--edex);background:var(--edex-soft)}
.callout.cp b.ct{color:var(--edex)}
.callout.warn{border-left-color:var(--bad);background:var(--bad-soft)}
.callout.warn b.ct{color:var(--bad)}
.callout.form{border-left-color:var(--good);background:var(--good-soft)}
.callout.form b.ct{color:var(--good)}
.callout.tip{border-left-color:var(--endo);background:var(--endo-soft)}
.callout.tip b.ct{color:var(--endo)}
.callout.remember{border-left-color:var(--ink-soft)}
table{width:100%;border-collapse:collapse;margin-top:1.2rem;font-size:.9rem;background:#fff;border:1px solid var(--line);border-radius:var(--radius);overflow:hidden}
th,td{text-align:left;padding:.55rem .8rem;border-bottom:1px solid var(--line)}
th{background:var(--surface);font-size:.78rem;letter-spacing:.06em;text-transform:uppercase}
tr:last-child td{border-bottom:0}
td .neg{color:var(--exo);font-weight:600}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:.9rem;margin-top:1.2rem}
.card{perspective:900px;height:175px}
.card-inner{position:relative;width:100%;height:100%;transition:transform .5s;transform-style:preserve-3d}
.card.flipped .card-inner{transform:rotateY(180deg)}
.face{position:absolute;inset:0;backface-visibility:hidden;border-radius:var(--radius);padding:1rem;display:flex;flex-direction:column;justify-content:center;gap:.4rem;border:1px solid var(--line)}
.face.front{background:#fff}
.face.front b{font-family:Fraunces,serif;font-size:1.08rem}
.face.front span{font-size:.8rem;color:var(--ink-soft)}
.face.back{background:var(--ink);color:#F2F4FA;transform:rotateY(180deg);font-size:.83rem;line-height:1.5}
.card button.flip-hit{position:absolute;inset:0;background:none;border:0;border-radius:var(--radius)}
.check{margin-top:1.4rem;border:1px solid var(--line);border-radius:var(--radius);background:#fff;padding:1.1rem 1.2rem}
.check .tag{display:inline-block;font-size:.72rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--endo);background:var(--endo-soft);border-radius:99px;padding:.2rem .7rem;margin-bottom:.5rem}
.check p.q{font-weight:600;margin:.2rem 0 .8rem}
.opts{display:grid;gap:.55rem}
.opt{text-align:left;border:1.5px solid var(--line);background:#fff;border-radius:10px;padding:.65rem .9rem;font-size:.95rem;transition:border .15s, background .15s}
.opt:hover:not(:disabled){border-color:var(--ink)}
.opt:disabled{cursor:default}
.opt.correct{border-color:var(--good);background:var(--good-soft);font-weight:600}
.opt.wrong{border-color:var(--bad);background:var(--bad-soft)}
.expl{display:none;margin-top:.8rem;font-size:.9rem;border-left:4px solid var(--good);background:var(--good-soft);padding:.6rem .9rem;border-radius:0 10px 10px 0}
.expl.show{display:block;animation:pop .3s ease}
.expl.bad{border-color:var(--bad);background:var(--bad-soft)}
.steps{margin-top:1.4rem;border:1px solid var(--line);border-radius:var(--radius);background:#fff;padding:1.1rem 1.2rem}
.steps .tag{display:inline-block;font-size:.72rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--exo);background:var(--exo-soft);border-radius:99px;padding:.2rem .7rem;margin-bottom:.5rem}
.step{display:none;border-top:1px dashed var(--line);padding:.85rem 0}
.step.show{display:block;animation:pop .3s ease}
.step b.sn{color:var(--exo)}
.step .mono{background:var(--surface);border-radius:8px;padding:.5rem .8rem;display:inline-block;margin-top:.4rem;font-size:.9rem}
.next-step{margin-top:.9rem;background:var(--exo);color:#fff;border:0;border-radius:99px;padding:.5rem 1.1rem;font-weight:600;font-size:.88rem}
.next-step:hover{background:#c94c09}
.think{font-size:.88rem;color:var(--ink-soft);font-style:italic;margin:.6rem 0 0}
.sim{margin-top:1.4rem;border:1px solid var(--line);border-radius:var(--radius);background:#fff;overflow:hidden}
.sim-head{padding:.9rem 1.2rem;background:linear-gradient(90deg,var(--endo-soft),#fff);font-weight:600;font-family:Fraunces,serif;font-size:1.1rem}
.sim-body{display:grid;grid-template-columns:1fr 1fr;gap:1.4rem;padding:1.2rem}
@media (max-width:640px){.sim-body{grid-template-columns:1fr}}
.slider-row{margin-bottom:1rem}
.slider-row label{display:flex;justify-content:space-between;font-size:.85rem;font-weight:600;margin-bottom:.25rem}
.slider-row label output{font-family:'IBM Plex Mono',monospace;color:var(--endo)}
input[type=range]{width:100%;accent-color:var(--endo)}
.sim-out{background:var(--ink);border-radius:12px;color:#fff;padding:1.1rem;display:flex;flex-direction:column;gap:.5rem;justify-content:center}
.sim-out .lbl{font-size:.75rem;letter-spacing:.1em;text-transform:uppercase;color:#9AA5C0}
.sim-out .big{font-family:'IBM Plex Mono',monospace;font-size:1.9rem;font-weight:600;color:#FFB088}
.sim-out .qline{font-family:'IBM Plex Mono',monospace;font-size:.85rem;color:#C6CDE0}
.toggle{display:flex;align-items:center;gap:.5rem;font-size:.85rem;margin-top:.4rem;color:#C6CDE0}
.toggle input{accent-color:var(--exo);width:18px;height:18px}
.heatloss-note{display:none;font-size:.8rem;color:#FFB088}
.heatloss-note.show{display:block}
.sort{margin-top:1.4rem;border:1px solid var(--line);border-radius:var(--radius);background:#fff;padding:1.1rem 1.2rem}
.sort .rxn{font-family:'IBM Plex Mono',monospace;background:var(--surface);border-radius:10px;padding:.6rem 1rem;display:inline-block;margin:.4rem 0 .8rem}
.chips{display:flex;flex-wrap:wrap;gap:.5rem;margin:.6rem 0 1rem}
.chip{border:1.5px solid var(--line);background:#fff;border-radius:99px;padding:.45rem .95rem;font-family:'IBM Plex Mono',monospace;font-size:.87rem;font-weight:600;transition:all .15s}
.chip[data-state="broken"]{border-color:var(--endo);background:var(--endo-soft);color:var(--endo)}
.chip[data-state="made"]{border-color:var(--exo);background:var(--exo-soft);color:var(--exo)}
.legend{display:flex;gap:1.1rem;font-size:.8rem;color:var(--ink-soft);flex-wrap:wrap}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:.35rem}
.check-btn{background:var(--ink);color:#fff;border:0;border-radius:99px;padding:.5rem 1.2rem;font-weight:600;font-size:.88rem;margin-top:.9rem}
.sort-result{display:none;margin-top:.9rem;padding:.7rem 1rem;border-radius:10px;font-size:.92rem}
.sort-result.show{display:block;animation:pop .3s ease}
.sort-result.ok{background:var(--good-soft);border-left:4px solid var(--good)}
.sort-result.no{background:var(--bad-soft);border-left:4px solid var(--bad)}
.hess-fig{margin-top:1.2rem;border:1px solid var(--line);border-radius:var(--radius);background:#fff;padding:1rem 1.2rem}
.hess-fig svg{width:100%;height:auto}
.hess-fig .cap{font-size:.85rem;color:var(--ink-soft);margin:.3rem 0 0}
.note-img{margin:1.2rem 0 0;text-align:center}
.note-img img{max-width:100%;border:1px solid var(--line);border-radius:10px}
.note-img figcaption{font-size:.82rem;color:var(--ink-soft);margin-top:.4rem}
.note-img .credit{font-size:.75rem}
.cw-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:.8rem;margin-top:1.2rem}
.cw{background:#fff;border:1px solid var(--line);border-radius:10px;padding:.75rem .9rem;font-size:.87rem}
.cw b{color:var(--edex);font-family:'IBM Plex Mono',monospace}
.speclist{margin-top:1.2rem;border:1px solid #D8CFF5;border-radius:var(--radius);background:#fff;overflow:hidden}
.speclist .sl-head{padding:.9rem 1.2rem;background:var(--edex-soft);font-family:Fraunces,serif;font-weight:700;color:var(--edex);display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:.4rem}
.sl-head .count{font-family:'IBM Plex Mono',monospace;font-size:.85rem}
.sl-item{display:flex;gap:.7rem;align-items:flex-start;padding:.65rem 1.2rem;border-top:1px solid var(--line);font-size:.92rem}
.sl-item input{margin-top:.3rem;width:17px;height:17px;accent-color:var(--edex);flex-shrink:0}
.sl-item .code{font-family:'IBM Plex Mono',monospace;font-size:.78rem;color:var(--edex);font-weight:600;flex-shrink:0;padding-top:.15rem}
.sl-item label{cursor:pointer}
.sl-body{flex:1}
.sl-more{display:block;background:none;border:0;padding:.15rem 0 0;font-size:.8rem;font-weight:600;color:var(--edex);text-align:left}
.sl-more:hover{text-decoration:underline}
.sl-info{display:none;margin-top:.45rem;font-size:.85rem;background:var(--edex-soft);border-left:3px solid var(--edex);border-radius:0 8px 8px 0;padding:.55rem .8rem;line-height:1.5}
.sl-info.show{display:block;animation:pop .3s ease}
details.mist{margin-top:.6rem;background:#fff;border:1px solid var(--line);border-radius:10px;padding:.6rem .9rem}
details.mist summary{cursor:pointer;font-weight:600;font-size:.93rem}
details.mist p{margin:.5rem 0 .2rem;font-size:.9rem}
.numq{margin-top:1.4rem;border:1px solid var(--line);border-radius:var(--radius);background:#fff;padding:1.1rem 1.2rem}
.numq .marks{font-size:.75rem;font-weight:700;color:var(--ink-soft)}
.ansrow{display:flex;gap:.6rem;margin-top:.9rem;flex-wrap:wrap;align-items:center}
.ansrow input{font-family:'IBM Plex Mono',monospace;font-size:1rem;padding:.55rem .8rem;border:1.5px solid var(--line);border-radius:10px;width:150px}
.ansrow .unit{font-family:'IBM Plex Mono',monospace;color:var(--ink-soft)}
.markscheme{display:none;margin-top:1rem;border-top:1px dashed var(--line);padding-top:.9rem;font-size:.9rem}
.markscheme.show{display:block;animation:pop .3s ease}
.markscheme li{margin:.3rem 0}
.finish{margin-top:3.5rem;text-align:center;border:1px solid var(--line);border-radius:var(--radius);background:linear-gradient(180deg,#fff,var(--surface));padding:2.2rem 1.2rem}
.finish h2{font-size:1.8rem}
.finish .mono{font-size:1.05rem;margin-top:.5rem;display:block;color:var(--ink-soft)}
footer{margin-top:2rem;text-align:center;font-size:.8rem;color:var(--ink-soft)}
.blk-unknown{margin-top:1rem;border:1px dashed var(--line);border-radius:10px;padding:.7rem 1rem;color:var(--ink-soft);font-size:.85rem}
"""

# The interactive JS. `renderBlock` is the ONE dispatcher; each widget has one
# generic behaviour fn reading its block data from the BLOCKS map.
_JS = r"""
(function(){
"use strict";
var data = JSON.parse(document.getElementById('notes-data').textContent);
var BLOCKS = new Map(); var BID = 0;
var PROG = {total:new Set(), done:new Set(), sec:new Map(), correct:0, attempts:0};

/* ---------- DOM helpers (model text -> textContent, never innerHTML) ---------- */
function h(tag, attrs, kids){
  var e=document.createElement(tag);
  if(attrs) for(var k in attrs){ var v=attrs[k]; if(v==null) continue;
    if(k==='class') e.className=v; else e.setAttribute(k, v); }
  if(kids!=null) (Array.isArray(kids)?kids:[kids]).forEach(function(c){
    if(c==null) return; e.appendChild(typeof c==='string'?document.createTextNode(c):c); });
  return e;
}
function svgEl(markup){ var d=h('div'); d.innerHTML=markup; return d.firstChild; }  // renderer-owned SVG only

/* ---------- markdown (prose only) + math protect ---------- */
function md(s, inline){ if(s==null) return ''; var store=[];
  var x=String(s).replace(/\$\$[\s\S]*?\$\$|\\\([\s\S]*?\\\)/g, function(m){store.push(m); return '@@M'+(store.length-1)+'@@';});
  x=(inline?marked.parseInline:marked.parse)(x);
  return x.replace(/@@M(\d+)@@/g, function(_,i){return store[+i];});
}
function proseEl(s){ var d=h('div',{class:'prose'}); d.innerHTML=md(s,false); return d; }

/* ---------- safe arithmetic evaluator (no eval) ---------- */
function evalExpr(expr, scope){
  var s=String(expr), i=0;
  function ws(){ while(i<s.length && s[i]===' ') i++; }
  function E(){ var v=T(); ws(); while(s[i]==='+'||s[i]==='-'){ var o=s[i++]; var r=T(); v=(o==='+')?v+r:v-r; ws(); } return v; }
  function T(){ var v=F(); ws(); while(s[i]==='*'||s[i]==='/'){ var o=s[i++]; var r=F(); v=(o==='*')?v*r:v/r; ws(); } return v; }
  function F(){ ws();
    if(s[i]==='+'){ i++; return F(); }
    if(s[i]==='-'){ i++; return -F(); }
    if(s[i]==='('){ i++; var v=E(); ws(); if(s[i]!==')') throw new Error('paren'); i++; return v; }
    var m=/^[0-9]*\.?[0-9]+/.exec(s.slice(i)); if(m){ i+=m[0].length; return parseFloat(m[0]); }
    var id=/^[A-Za-z_][A-Za-z0-9_]*/.exec(s.slice(i)); if(id){ i+=id[0].length; if(!(id[0] in scope)) throw new Error('unknown '+id[0]); return scope[id[0]]; }
    throw new Error('token@'+i);
  }
  ws(); var val=E(); ws(); if(i<s.length) throw new Error('trailing'); return val;
}
function fmt(v, kind){ if(!isFinite(v)) return '—';
  if(kind==='signed_0dp') return (v<0?'−':'+')+Math.abs(v).toFixed(0);
  if(kind==='plain_2dp') return v.toFixed(2);
  return (v<0?'−':'+')+Math.abs(v).toFixed(1);
}

/* ---------- progress ---------- */
function reg(id, secId){ PROG.total.add(id); if(secId!=null) PROG.sec.set(id, secId); }
function mark(id){
  if(!PROG.total.has(id) || PROG.done.has(id)) return;
  PROG.done.add(id);
  var pct = PROG.total.size ? Math.round(PROG.done.size/PROG.total.size*100) : 0;
  document.getElementById('pfill').style.width = pct+'%';
  document.getElementById('pbar').setAttribute('aria-valuenow', pct);
  document.getElementById('scoreLbl').textContent = PROG.done.size+' / '+PROG.total.size+' done';
  var sec = PROG.sec.get(id);
  if(sec!=null){ var peers=[...PROG.total].filter(function(b){return PROG.sec.get(b)===sec;});
    if(peers.every(function(b){return PROG.done.has(b);})){ var tk=document.getElementById('tick-'+sec); if(tk) tk.classList.add('on'); } }
  updateFinish();
}
function updateFinish(){ var f=document.getElementById('finishLine'); if(!f) return;
  var nx=(data.finish&&data.finish.next_topic)?(' Next: '+data.finish.next_topic+'.'):'';
  f.textContent = (PROG.total.size>0 && PROG.done.size===PROG.total.size)
    ? ('🎉 All '+PROG.total.size+' activities complete — '+PROG.correct+'/'+Math.max(PROG.attempts,1)+' checks right.'+nx)
    : (PROG.done.size+' of '+PROG.total.size+' activities complete · '+PROG.correct+'/'+Math.max(PROG.attempts,1)+' checks correct');
}

/* ---------- SVG templates (renderer owns geometry; labels via textContent) ---------- */
var SVG = {
  energy_profile: function(state){
    var below = state.product_position==='below';
    var color = state.accent==='endo' ? '#1971C2' : '#E8590C';
    var py=below?170:60, cy=below?30:25, arrY2=below?168:64, lblY=below?150:95;
    return '<svg viewBox="0 0 560 240" aria-label="Energy profile diagram">'
      +'<line x1="40" y1="210" x2="540" y2="210" stroke="#C9CBD4" stroke-width="1.5"/>'
      +'<line x1="40" y1="210" x2="40" y2="20" stroke="#C9CBD4" stroke-width="1.5"/>'
      +'<text x="14" y="120" font-size="12" fill="#5B6478" transform="rotate(-90 14 120)">Energy</text>'
      +'<text x="290" y="232" font-size="12" fill="#5B6478" text-anchor="middle">Progress of reaction</text>'
      +'<line x1="55" y1="120" x2="150" y2="120" stroke="#16213A" stroke-width="3"/>'
      +'<text x="60" y="110" font-size="12" fill="#16213A" font-weight="600">Reactants</text>'
      +'<path d="M150,120 C 230,'+cy+' 330,'+cy+' 410,'+py+'" fill="none" stroke="'+color+'" stroke-width="3"/>'
      +'<line x1="410" y1="'+py+'" x2="510" y2="'+py+'" stroke="#16213A" stroke-width="3"/>'
      +'<text x="425" y="'+(py-10)+'" font-size="12" fill="#16213A" font-weight="600">Products</text>'
      +'<line x1="480" y1="120" x2="480" y2="'+arrY2+'" stroke="'+color+'" stroke-width="2.5" marker-end="url(#arrEP)"/>'
      +'<text class="dhlabel" x="492" y="'+lblY+'" font-size="13" fill="'+color+'" font-weight="700"></text>'
      +'<defs><marker id="arrEP" markerWidth="8" markerHeight="8" refX="4" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="context-stroke"/></marker></defs>'
      +'</svg>';
  }
};
var NODE = { top_left:{x:115,y:52}, top_right:{x:445,y:52}, bottom:{x:280,y:182} };
function cycleSVG(block){
  function box(x,y,label,fill,stroke){ return '<rect x="'+(x-85)+'" y="'+(y-22)+'" width="170" height="44" rx="10" fill="'+fill+'" stroke="'+stroke+'"/>'; }
  var g='<svg viewBox="0 0 560 220" aria-label="Enthalpy cycle">';
  g+=box(NODE.top_left.x,NODE.top_left.y,0,'#E7F1FB','#1971C2');
  g+=box(NODE.top_right.x,NODE.top_right.y,0,'#FFF0E6','#E8590C');
  g+=box(NODE.bottom.x,NODE.bottom.y,0,'#F1F0EA','#5B6478');
  g+='<defs><marker id="harr" markerWidth="8" markerHeight="8" refX="6" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="context-stroke"/></marker></defs>';
  g+='</svg>';
  return g;
}

/* ---------- block dispatch ---------- */
function renderBlock(block, secId){
  var el;
  switch(block.type){
    case 'prose':         el=renderProse(block); break;
    case 'callout':       el=renderCallout(block); break;
    case 'table':         el=renderTable(block); break;
    case 'flip_cards':    el=renderFlipCards(block, secId); break;
    case 'mcq':           el=renderMCQ(block, secId); break;
    case 'step_reveal':   el=renderSteps(block, secId); break;
    case 'numeric':       el=renderNumeric(block, secId); break;
    case 'sim':           el=renderSim(block, secId); break;
    case 'sort':          el=renderSort(block, secId); break;
    case 'toggle_diagram':el=renderToggle(block, secId); break;
    case 'cycle_diagram': el=renderCycle(block); break;
    case 'reveal':        el=renderReveal(block, secId); break;
    case 'accordion':     el=renderAccordion(block); break;
    case 'figure':        el=renderFigure(block); break;
    default:              el=h('div',{class:'blk-unknown'}, 'Unsupported block: '+block.type);
  }
  return el;
}
function newId(){ return 'blk'+(BID++); }

function renderProse(b){ return proseEl(b.body); }

function renderCallout(b){
  var cls={mistake:'warn', practical:'cp', formula:'form', tip:'tip', remember:'remember'}[b.kind]||'';
  var box=h('div',{class:'callout '+cls});
  if(b.title) box.appendChild(h('b',{class:'ct'}, b.title));
  var body=h('span'); body.innerHTML=md(b.body,true); box.appendChild(body);
  return box;
}

function renderTable(b){
  var tbl=h('table',{'aria-label':b.caption||''});
  if(b.headers&&b.headers.length){ var tr=h('tr'); b.headers.forEach(function(hd){ tr.appendChild(h('th',null,hd)); }); tbl.appendChild(tr); }
  (b.rows||[]).forEach(function(row){ var tr=h('tr'); (row||[]).forEach(function(cell){ var td=h('td'); td.innerHTML=md(cell,true); tr.appendChild(td); }); tbl.appendChild(tr); });
  return tbl;
}

function renderFlipCards(b, secId){
  var id=newId(); BLOCKS.set(id,{block:b, flipped:new Set()}); reg(id, secId);
  var grid=h('div',{class:'cards','data-block':id});
  (b.cards||[]).forEach(function(card, ci){
    var back=h('div',{class:'face back'}); back.innerHTML=md(card.back,true);
    var inner=h('div',{class:'card-inner'},[
      h('div',{class:'face front'},[h('b',null,card.front), card.hint?h('span',null,card.hint):null]),
      back,
      h('button',{class:'flip-hit','aria-label':'Flip card','data-action':'flip','data-card':String(ci)})
    ]);
    grid.appendChild(h('div',{class:'card','data-card':String(ci)}, inner));
  });
  return grid;
}

function renderMCQ(b, secId){
  var id=newId(); BLOCKS.set(id,{block:b, done:false}); reg(id, secId);
  var box=h('div',{class:'check','data-block':id});
  box.appendChild(h('span',{class:'tag'}, b.tag||'Quick check'));
  box.appendChild(h('p',{class:'q'}, b.question));
  var opts=h('div',{class:'opts'});
  (b.options||[]).forEach(function(o, oi){ opts.appendChild(h('button',{class:'opt','data-action':'mcq','data-choice':String(oi)}, o.text)); });
  box.appendChild(opts);
  box.appendChild(h('div',{class:'expl'}));
  return box;
}

function renderSteps(b, secId){
  var id=newId(); BLOCKS.set(id,{block:b, i:0}); reg(id, secId);
  var box=h('div',{class:'steps','data-block':id});
  box.appendChild(h('span',{class:'tag'}, b.tag||'Worked example'));
  var p=h('p',{style:'margin:.4rem 0 0'}); p.innerHTML=md(b.prompt,true); box.appendChild(p);
  if(b.think_hint) box.appendChild(h('p',{class:'think'}, '🤔 '+b.think_hint));
  (b.steps||[]).forEach(function(st, si){
    var sd=h('div',{class:'step','data-step':String(si)});
    sd.appendChild(h('b',{class:'sn'}, st.title+' '));
    var body=h('span'); body.innerHTML=md(st.body,true); sd.appendChild(body);
    if(st.formula) sd.appendChild(h('div',{class:'mono'}, st.formula));
    if(st.note){ var nn=h('p',{style:'margin:.6rem 0 0;font-size:.9rem'}); nn.innerHTML=md(st.note,true); sd.appendChild(nn); }
    box.appendChild(sd);
  });
  box.appendChild(h('button',{class:'next-step','data-action':'step'}, 'Show step 1'));
  return box;
}

function renderNumeric(b, secId){
  var id=newId(); BLOCKS.set(id,{block:b, done:false}); reg(id, secId);
  var box=h('div',{class:'numq','data-block':id});
  box.appendChild(h('span',{class:'marks'}, b.label||''));
  var p=h('p',{style:'margin:.5rem 0 0'}); p.innerHTML=md(b.question,true); box.appendChild(p);
  box.appendChild(h('div',{class:'ansrow'},[
    h('label',{style:'font-weight:600;font-size:.9rem'}, 'Answer ='),
    h('input',{inputmode:'decimal', placeholder:'e.g. −12.3','aria-label':'Your answer'}),
    b.unit?h('span',{class:'unit'}, b.unit):null,
    h('button',{class:'check-btn', style:'margin-top:0','data-action':'num'}, 'Check')
  ]));
  box.appendChild(h('div',{class:'expl'}));
  var ms=h('div',{class:'markscheme'});
  ms.appendChild(h('b',null,'Mark scheme'));
  var ol=h('ol');
  (b.mark_scheme||[]).forEach(function(m){ ol.appendChild(h('li',null,[h('b',null,m.label+' '), m.text])); });
  ms.appendChild(ol);
  if(b.sanity_check) ms.appendChild(h('p',{style:'font-size:.85rem;color:var(--ink-soft)'}, b.sanity_check));
  box.appendChild(ms);
  return box;
}

function renderSim(b, secId){
  var id=newId(); BLOCKS.set(id,{block:b}); reg(id, secId);
  var box=h('div',{class:'sim','data-block':id});
  box.appendChild(h('div',{class:'sim-head'}, b.title||'Simulation'));
  var left=h('div');
  (b.inputs||[]).forEach(function(inp){
    left.appendChild(h('div',{class:'slider-row'},[
      h('label',null,[ inp.label+' ', h('output',{'data-out':inp.key}, inp.default+(inp.unit?(' '+inp.unit):'')) ]),
      h('input',{type:'range','data-key':inp.key, min:String(inp.min), max:String(inp.max), step:String(inp.step), value:String(inp.default),'data-action':'sim','aria-label':inp.label})
    ]));
  });
  var out=h('div',{class:'sim-out'});
  out.appendChild(h('span',{class:'lbl'}, b.qline_template?'working':'q'));
  out.appendChild(h('span',{class:'qline','data-q':'1'}, ''));
  out.appendChild(h('span',{class:'lbl'}, b.output_label||'Result'));
  out.appendChild(h('span',{class:'big','data-big':'1'}, ''));
  if(b.toggle){
    out.appendChild(h('label',{class:'toggle'},[ h('input',{type:'checkbox','data-toggle':'1','data-action':'sim'}), ' '+b.toggle.label ]));
    out.appendChild(h('span',{class:'heatloss-note','data-note':'1'}, b.toggle.note||''));
  }
  box.appendChild(h('div',{class:'sim-body'},[left, out]));
  return box;
}

function renderSort(b, secId){
  var id=newId(); BLOCKS.set(id,{block:b, state:{}}); reg(id, secId);
  var box=h('div',{class:'sort','data-block':id});
  if(b.title) box.appendChild(h('b',{style:'font-family:Fraunces,serif'}, b.title));
  box.appendChild(h('div',null, h('span',{class:'rxn'}, b.prompt)));
  box.appendChild(h('p',{style:'margin:.2rem 0 .4rem;font-size:.92rem'}, 'Tap each item to cycle it into a bucket:'));
  var legend=h('div',{class:'legend'});
  (b.buckets||[]).forEach(function(bk){ var col=bk.accent==='endo'?'var(--endo)':(bk.accent==='exo'?'var(--exo)':'#C9CBD4');
    legend.appendChild(h('span',null,[ h('i',{class:'dot', style:'background:'+col}), bk.label ])); });
  box.appendChild(legend);
  var chips=h('div',{class:'chips'});
  (b.items||[]).forEach(function(it, ii){ chips.appendChild(h('button',{class:'chip','data-item':String(ii),'data-state':'none','data-action':'sortcycle'}, it.label)); });
  box.appendChild(chips);
  box.appendChild(h('button',{class:'check-btn','data-action':'sortcheck'}, 'Check my sorting'));
  box.appendChild(h('div',{class:'sort-result'}));
  return box;
}

function renderToggle(b, secId){
  var id=newId(); BLOCKS.set(id,{block:b}); reg(id, secId);
  var box=h('div',{class:'profile','data-block':id});
  var seg=h('div',{class:'seg', role:'group','aria-label':'Reaction type'});
  (b.states||[]).forEach(function(st, si){
    seg.appendChild(h('button',{class:st.accent==='endo'?'endo':'exo','aria-pressed':si===0?'true':'false','data-action':'toggle','data-state-key':st.key}, st.label));
  });
  box.appendChild(h('div',{class:'row'},[ h('b',{style:'font-family:Fraunces,serif'}, b.title||'Interactive diagram'), seg ]));
  var holder=h('div',{'data-svg':'1'});
  box.appendChild(holder);
  box.appendChild(h('p',{class:'cap','data-cap':'1'}, ''));
  return box;
}

function renderCycle(b){
  var fig=h('div',{class:'hess-fig'});
  var wrap=h('div'); wrap.innerHTML=cycleSVG(b); var svg=wrap.firstChild;
  // add node labels + edges via SVG DOM (labels as textContent — no markup path)
  function txt(x,y,s,opts){ var t=document.createElementNS('http://www.w3.org/2000/svg','text');
    t.setAttribute('x',x); t.setAttribute('y',y); t.setAttribute('text-anchor','middle'); t.setAttribute('font-size',(opts&&opts.fs)||'13'); t.setAttribute('font-weight',(opts&&opts.fw)||'600'); t.setAttribute('fill',(opts&&opts.fill)||'#16213A'); t.textContent=s; return t; }
  svg.appendChild(txt(NODE.top_left.x, NODE.top_left.y+5, b.top_left||'Reactants',{fs:'14'}));
  svg.appendChild(txt(NODE.top_right.x, NODE.top_right.y+5, b.top_right||'Products',{fs:'14'}));
  svg.appendChild(txt(NODE.bottom.x, NODE.bottom.y+5, b.bottom||'',{fs:'13'}));
  (b.edges||[]).forEach(function(e){ var a=NODE[e.frm], c=NODE[e.to]; if(!a||!c) return;
    var col=e.accent==='endo'?'#1971C2':(e.accent==='exo'?'#E8590C':'#16213A');
    var ln=document.createElementNS('http://www.w3.org/2000/svg','line');
    // shorten toward boxes
    ln.setAttribute('x1',a.x); ln.setAttribute('y1',a.y+ (c.y>a.y?24:-24)); ln.setAttribute('x2',c.x); ln.setAttribute('y2',c.y+ (a.y>c.y?24:-24));
    ln.setAttribute('stroke',col); ln.setAttribute('stroke-width','2.5'); ln.setAttribute('marker-end','url(#harr)'); svg.appendChild(ln);
    var mx=(a.x+c.x)/2, my=(a.y+c.y)/2; svg.appendChild(txt(mx, my, e.label,{fs:'12', fill:col})); });
  fig.appendChild(svg);
  if(b.caption){ var cap=h('p',{class:'cap'}); cap.innerHTML=md(b.caption,true); fig.appendChild(cap); }
  return fig;
}

function renderReveal(b, secId){
  var id=newId(); BLOCKS.set(id,{block:b}); reg(id, secId);
  var box=h('div',{class:'hook','data-block':id});
  box.appendChild(h('div',{class:'hook-head'},[ h('span',{class:'emoji'}, b.emoji||'🔥'), h('b',null,b.question) ]));
  var body=h('div',{class:'hook-body'});
  var teaser=h('p'); teaser.innerHTML=md(b.teaser,true); body.appendChild(teaser);
  body.appendChild(h('button',{class:'reveal-btn','data-action':'reveal'}, 'Reveal the answer'));
  var ans=h('div',{class:'hook-answer'}); ans.innerHTML=md(b.answer,true); body.appendChild(ans);
  box.appendChild(body);
  return box;
}

function renderAccordion(b){
  var wrap=h('div');
  (b.items||[]).forEach(function(it){ var d=h('details',{class:'mist'}); d.appendChild(h('summary',null,it.summary));
    var p=h('p'); p.innerHTML=md(it.detail,true); d.appendChild(p); wrap.appendChild(d); });
  return wrap;
}

function renderFigure(b){
  var d=b.diagram||{};
  if(d.kind==='image' && d.image_src){
    var fig=h('figure',{class:'note-img'});
    fig.appendChild(h('img',{src:d.image_src, alt:d.caption||'figure', loading:'lazy'}));
    var cap=h('figcaption',null,[ d.caption||'' , d.attribution?h('span',{class:'credit'}, ' — '+d.attribution):null ]);
    fig.appendChild(cap); return fig;
  }
  if(d.kind==='latex' && d.content){ var box=h('div',{class:'hess-fig'}); box.appendChild(h('div',null,'$$'+d.content+'$$'));
    if(d.caption) box.appendChild(h('p',{class:'cap'},d.caption)); return box; }
  return h('div',{class:'blk-unknown'}, 'Figure: '+(d.caption||''));
}

/* ---------- widget behaviours ---------- */
function answerMCQ(id, i){
  var rec=BLOCKS.get(id); if(!rec||rec.done) return; rec.done=true;
  var box=document.querySelector('[data-block="'+id+'"]');
  var opts=[].slice.call(box.querySelectorAll('.opt')); opts.forEach(function(o){o.disabled=true;});
  var options=rec.block.options||[]; var chosen=options[i]||{};
  var expl=box.querySelector('.expl'); PROG.attempts++;
  if(chosen.correct){ opts[i].classList.add('correct'); PROG.correct++; expl.textContent='✓ Correct — '+(chosen.explanation||''); }
  else { opts[i].classList.add('wrong'); expl.classList.add('bad');
    var ci=options.findIndex(function(o){return o.correct;}); if(ci>=0&&opts[ci]) opts[ci].classList.add('correct');
    expl.textContent='✗ Not quite — '+(chosen.explanation||''); }
  expl.classList.add('show'); mark(id); updateFinish();
}
function stepReveal(id){
  var rec=BLOCKS.get(id); if(!rec) return; var steps=rec.block.steps||[]; var box=document.querySelector('[data-block="'+id+'"]');
  if(rec.i>=steps.length) return;
  var el=box.querySelector('[data-step="'+rec.i+'"]'); if(el) el.classList.add('show'); rec.i++;
  var btn=box.querySelector('.next-step');
  if(rec.i>=steps.length){ btn.style.display='none'; mark(id); } else btn.textContent='Show step '+(rec.i+1);
}
function checkNumeric(id){
  var rec=BLOCKS.get(id); var box=document.querySelector('[data-block="'+id+'"]'); var b=rec.block;
  var raw=box.querySelector('input').value.trim().replace('−','-').replace(',','.'); var val=parseFloat(raw);
  var fb=box.querySelector('.expl'); fb.classList.add('show'); fb.classList.remove('bad');
  if(isNaN(val)){ fb.classList.add('bad'); fb.textContent='Enter a number, e.g. −12.3'; return; }
  if(!rec.done){ PROG.attempts++; }
  if(Math.abs(val-b.answer)<=b.tolerance){ if(!rec.done) PROG.correct++; rec.done=true;
    fb.textContent='✓ Correct: '+(b.answer<0?'−':'')+Math.abs(b.answer)+(b.unit?(' '+b.unit):'')+'. Full marks.';
    box.querySelector('.markscheme').classList.add('show'); mark(id); }
  else { fb.classList.add('bad'); rec.done=true;
    var w=(b.wrong_answers||[]).find(function(w){return Math.abs(val-w.value)<=(w.tolerance||0.5);});
    if(w){ fb.textContent='✗ '+w.message; } else { fb.textContent='✗ Not it — mark scheme revealed below.'; box.querySelector('.markscheme').classList.add('show'); }
    mark(id); }
  updateFinish();
}
function runSim(id){
  var rec=BLOCKS.get(id); var box=document.querySelector('[data-block="'+id+'"]'); var b=rec.block;
  var scope={}; (b.constants||[]).forEach(function(c){ scope[c.key]=c.value; });
  (b.inputs||[]).forEach(function(inp){ var el=box.querySelector('input[data-key="'+inp.key+'"]'); var v=el?parseFloat(el.value):inp.default; scope[inp.key]=v;
    var out=box.querySelector('output[data-out="'+inp.key+'"]'); if(out) out.textContent=(''+v)+(inp.unit?(' '+inp.unit):''); });
  var toggled=false; if(b.toggle){ var tg=box.querySelector('input[data-toggle]'); toggled=tg&&tg.checked; }
  var result; try{ result=evalExpr(b.expression, scope); }catch(e){ result=NaN; }
  if(toggled&&b.toggle) result=result*b.toggle.factor;
  box.querySelector('[data-big]').textContent=fmt(result,b.output_format)+(b.output_unit?(' '+b.output_unit):'');
  var qline=b.qline_template||''; if(qline){
    var qval=null; if(b.qline_expression){ try{ qval=evalExpr(b.qline_expression, scope); if(toggled&&b.toggle) qval=qval*b.toggle.factor; }catch(e){} }
    qline=qline.replace(/\{result\}/g, fmt(result,b.output_format));
    if(qval!=null) qline=qline.replace(/\{q\}/g, Math.round(qval));
    Object.keys(scope).forEach(function(k){ qline=qline.replace(new RegExp('\\{'+k+'\\}','g'), scope[k]); });
    box.querySelector('[data-q]').textContent=qline;
  } else { box.querySelector('[data-q]').textContent=''; }
  if(b.toggle){ var note=box.querySelector('[data-note]'); if(note) note.classList.toggle('show', toggled); }
  mark(id);
}
function cycleChip(id, itemIdx, chip){
  var order=['none','broken','made']; chip.setAttribute('data-state', order[(order.indexOf(chip.getAttribute('data-state'))+1)%3]);
}
function checkSort(id){
  var rec=BLOCKS.get(id); var box=document.querySelector('[data-block="'+id+'"]'); var b=rec.block;
  var chips=[].slice.call(box.querySelectorAll('.chip'));
  var ok=true, sums={};
  (b.items||[]).forEach(function(it, ii){ var st=chips[ii].getAttribute('data-state');
    if(st!==it.correct_bucket) ok=false; sums[st]=(sums[st]||0)+(it.value||0); });
  var res=box.querySelector('.sort-result'); res.classList.remove('ok','no'); res.classList.add('show', ok?'ok':'no');
  PROG.attempts++;
  if(ok){ PROG.correct++; res.textContent='✓ Sorted correctly. '+(b.success_note||''); mark(id); }
  else { res.textContent='✗ Not yet. '+(b.failure_hint||'Re-check which bucket each item belongs in.'); }
  updateFinish();
}
function toggleDiagram(id, key){
  var rec=BLOCKS.get(id); var box=document.querySelector('[data-block="'+id+'"]'); var b=rec.block;
  var st=(b.states||[]).find(function(s){return s.key===key;})||b.states[0]; if(!st) return;
  box.querySelectorAll('.seg button').forEach(function(btn){ btn.setAttribute('aria-pressed', btn.getAttribute('data-state-key')===key?'true':'false'); });
  var holder=box.querySelector('[data-svg]'); holder.innerHTML='';
  var svg=svgEl(SVG[b.template](st)); var lbl=svg.querySelector('.dhlabel'); if(lbl) lbl.textContent=(st.dh_label||'ΔH')+' '+(st.dh_sign||'');
  holder.appendChild(svg);
  var cap=box.querySelector('[data-cap]'); cap.innerHTML=md(st.caption,true);
  mark(id);
}
function flipCard(id, ci){
  var rec=BLOCKS.get(id); var box=document.querySelector('[data-block="'+id+'"]');
  var card=box.querySelector('.card[data-card="'+ci+'"]'); card.classList.toggle('flipped'); rec.flipped.add(ci);
  if(rec.flipped.size>=(rec.block.cards||[]).length) mark(id);
}
function specTick(secId){
  var box=document.getElementById('speclist-'+secId); if(!box) return;
  var boxes=[].slice.call(box.querySelectorAll('.sl-item input')); var n=boxes.filter(function(b){return b.checked;}).length;
  var cnt=box.querySelector('.count'); if(cnt) cnt.textContent=n+' / '+boxes.length+' secure';
  if(n===boxes.length && boxes.length>0) mark('spec-'+secId);
}
function revealBlock(id){
  var rec=BLOCKS.get(id); var box=document.querySelector('[data-block="'+id+'"]');
  box.querySelector('.hook-answer').classList.add('show'); box.querySelector('.reveal-btn').style.display='none'; mark(id);
}

/* ---------- event delegation (no model text in attributes) ---------- */
document.addEventListener('click', function(e){
  var el=e.target.closest('[data-action]'); if(!el) return;
  var blockEl=el.closest('[data-block]'); var id=blockEl?blockEl.getAttribute('data-block'):null;
  switch(el.getAttribute('data-action')){
    case 'mcq': answerMCQ(id, +el.getAttribute('data-choice')); break;
    case 'step': stepReveal(id); break;
    case 'num': checkNumeric(id); break;
    case 'sortcycle': cycleChip(id, +el.getAttribute('data-item'), el); break;
    case 'sortcheck': checkSort(id); break;
    case 'toggle': toggleDiagram(id, el.getAttribute('data-state-key')); break;
    case 'flip': flipCard(id, el.getAttribute('data-card')); break;
    case 'reveal': revealBlock(id); break;
    case 'specmore': { var info=el.nextElementSibling; if(info) info.classList.toggle('show'); break; }
  }
});
document.addEventListener('input', function(e){ var el=e.target.closest('[data-action="sim"]'); if(el){ var b=el.closest('[data-block]'); if(b) runSim(b.getAttribute('data-block')); } });
document.addEventListener('change', function(e){ var sp=e.target.closest('.sl-item'); if(sp){ var box=sp.closest('.speclist'); if(box) specTick(box.getAttribute('data-sec')); }
  var sim=e.target.closest('[data-toggle]'); if(sim){ var b=sim.closest('[data-block]'); if(b) runSim(b.getAttribute('data-block')); } });

/* ---------- top-level build ---------- */
function examMapEl(em, extraClass){
  var box=h('div',{class:'exam-map'+(extraClass?(' '+extraClass):'')});
  box.appendChild(h('div',{class:'em-title'}, em.title||''));
  var grid=h('div',{class:'em-grid'});
  (em.cells||[]).forEach(function(c){ var cell=h('div',{class:'em-cell'}); cell.appendChild(h('div',{class:'k'},c.key));
    var v=h('div',{class:'v'}); v.innerHTML=md(c.value,true); cell.appendChild(v); grid.appendChild(cell); });
  box.appendChild(grid); return box;
}
function sectionEl(sec, idx){
  var secId='sec'+idx;
  var s=h('section',{id:'s-'+secId});
  var head=h('div',{class:'sec-head'},[ h('h2',null,sec.heading),
    sec.spec_label?h('span',{class:'spec'},sec.spec_label):null, h('span',{class:'tick', id:'tick-'+secId},'✓') ]);
  s.appendChild(head);
  (sec.blocks||[]).forEach(function(b){ s.appendChild(renderBlock(b, secId)); });
  // if no interactive block registered under this section, drop the empty tick
  var any=[...PROG.total].some(function(id){return PROG.sec.get(id)===secId;});
  if(!any){ var tk=document.getElementById('tick-'+secId); if(tk) tk.remove(); }
  return s;
}
function build(){
  var main=document.getElementById('main');
  document.getElementById('topTitle').textContent=(data.hero&&data.hero.icon?data.hero.icon+' ':'')+data.topic;
  // hero
  var hero=h('div',{class:'hero'});
  if(data.hero){ hero.appendChild(h('div',{class:'eyebrow'}, data.hero.eyebrow||''));
    hero.appendChild(h('h1',null,data.hero.title||data.topic));
    var lede=h('p',{class:'lede'}); lede.innerHTML=md(data.hero.lede||'',true); hero.appendChild(lede); }
  if(data.exam_map && (data.exam_map.cells||[]).length) hero.appendChild(examMapEl(data.exam_map));
  if(data.hook) hero.appendChild(renderBlock(data.hook, null));
  main.appendChild(hero);
  // sections
  (data.sections||[]).forEach(function(sec, i){ main.appendChild(sectionEl(sec, i)); });
  // practice ladder
  if((data.practice||[]).length){
    var ps=h('section',{id:'s-prac'});
    ps.appendChild(h('div',{class:'sec-head'},[ h('h2',null,'Practice questions — the full ladder'), h('span',{class:'tick', id:'tick-prac'},'✓') ]));
    data.practice.forEach(function(b){ ps.appendChild(renderBlock(b, 'prac')); });
    main.appendChild(ps);
  }
  // command words
  if((data.command_words||[]).length){
    var cs=h('section'); cs.appendChild(h('div',{class:'sec-head'},[h('h2',null,'Command words in this topic')]));
    var grid=h('div',{class:'cw-grid'}); data.command_words.forEach(function(cw){ var cell=h('div',{class:'cw'});
      cell.appendChild(h('b',null,cw.word)); cell.appendChild(document.createTextNode(' — '+cw.gloss)); grid.appendChild(cell); });
    cs.appendChild(grid); main.appendChild(cs);
  }
  // mistakes
  if((data.mistakes||[]).length){
    var ms=h('section'); ms.appendChild(h('div',{class:'sec-head'},[h('h2',null,'Where the marks go missing')]));
    ms.appendChild(renderAccordion({items:data.mistakes})); main.appendChild(ms);
  }
  // spec checklist
  if(data.spec_checklist && (data.spec_checklist.items||[]).length){
    var scId='0'; var ss=h('section',{id:'s-spec'});
    ss.appendChild(h('div',{class:'sec-head'},[ h('h2',null,'Spec checklist — can you honestly tick these?'), h('span',{class:'tick', id:'tick-spec-'+scId},'✓') ]));
    var list=h('div',{class:'speclist', id:'speclist-'+scId,'data-sec':scId});
    var chk=data.spec_checklist;
    list.appendChild(h('div',{class:'sl-head'},[ h('span',null,chk.source_title||'Specification checklist'), h('span',{class:'count'}, '0 / '+(chk.items.length)+' secure') ]));
    chk.items.forEach(function(it, ii){
      var body=h('div',{class:'sl-body'});
      body.appendChild(h('label',null,it.can_do));
      if(it.recap){ body.appendChild(h('button',{class:'sl-more','data-action':'specmore','aria-expanded':'false'}, 'Not sure? Quick recap ▾'));
        var info=h('div',{class:'sl-info'}); info.innerHTML=md(it.recap,true); body.appendChild(info); }
      list.appendChild(h('div',{class:'sl-item'},[ h('input',{type:'checkbox'}), h('span',{class:'code'}, it.code), body ]));
    });
    ss.appendChild(list);
    if(chk.source_citation) ss.appendChild(h('p',{style:'font-size:.8rem;color:var(--ink-soft)'}, chk.source_citation));
    reg('spec-'+scId, 'spec-'+scId); PROG.sec.set('spec-'+scId, null);  // standalone tracked item
    main.appendChild(ss);
  }
  // past papers (generated + PDF-verified, or curated; only if present)
  if(data.past_papers && ((data.past_papers.verified||[]).length || (data.past_papers.resources||[]).length)){
    var pp=data.past_papers; var sec=h('section');
    var box=h('div',{class:'exam-map'});
    box.appendChild(h('div',{class:'em-title'}, '📄 Past-paper practice'));
    if(pp.intro){ var intro=h('p',{style:'margin:.7rem 0 .4rem;font-size:.92rem'}); intro.innerHTML=md(pp.intro,true); box.appendChild(intro); }
    var grid=h('div',{class:'em-grid'});
    (pp.resources||[]).forEach(function(r){ var cell=h('div',{class:'em-cell'}); cell.appendChild(h('div',{class:'k'},r.key)); var v=h('div',{class:'v'}); v.innerHTML=md(r.value,true); cell.appendChild(v); grid.appendChild(cell); });
    // summary via textContent + a programmatic anchor whose href is scheme-guarded:
    // these fields are model-generated, so they must never reach innerHTML or a raw href.
    var safeUrl=function(u){ return (/^https?:\/\//i.test(u||'')) ? u : ''; };
    (pp.verified||[]).forEach(function(vp){ var cell=h('div',{class:'em-cell', style:'grid-column:1/-1'}); cell.appendChild(h('div',{class:'k'}, '✓ Verified · '+vp.label));
      var v=h('div',{class:'v'}, vp.summary||''); var u=safeUrl(vp.url); if(u){ v.appendChild(document.createTextNode(' ')); v.appendChild(h('a',{href:u, target:'_blank', rel:'noopener', style:'color:var(--edex);font-weight:600'}, 'Sit the paper →')); } cell.appendChild(v); grid.appendChild(cell); });
    box.appendChild(grid);
    if(pp.disclaimer){ var disc=h('div',{class:'em-cell', style:'grid-column:1/-1'}); disc.appendChild(h('div',{class:'v', style:'font-size:.8rem;color:var(--ink-soft)'}, pp.disclaimer)); grid.appendChild(disc); }
    sec.appendChild(box); main.appendChild(sec);
  }
  // finish + footer
  var fin=h('div',{class:'finish', id:'finish'});
  fin.appendChild(h('h2',null,(data.finish&&data.finish.heading)||'Topic progress'));
  fin.appendChild(h('span',{class:'mono', id:'finishLine'}, 'Work through the activities above — your score appears here.'));
  main.appendChild(fin);
  main.appendChild(h('footer',null, 'AP Guru · Interactive notes · '+data.topic+' · '+data.board+(data.finish&&data.finish.next_topic?(' · Next: '+data.finish.next_topic):'')));

  // init: draw toggle diagrams' first state, run sims once, set progress totals
  document.querySelectorAll('[data-block]').forEach(function(el){ var id=el.getAttribute('data-block'); var rec=BLOCKS.get(id); if(!rec) return;
    if(rec.block.type==='toggle_diagram') toggleDiagram(id, (rec.block.states[0]||{}).key);
    if(rec.block.type==='sim') runSim(id);
  });
  // toggleDiagram/runSim call mark() on init — undo that so init doesn't pre-complete them
  PROG.done.clear(); PROG.correct=0; PROG.attempts=0;
  document.getElementById('pfill').style.width='0%'; document.getElementById('scoreLbl').textContent='0 / '+PROG.total.size+' done';
  updateFinish();
  if(window.MathJax && MathJax.typesetPromise) MathJax.typesetPromise([main]);
  else { var iv=setInterval(function(){ if(window.MathJax&&MathJax.typesetPromise){ clearInterval(iv); MathJax.typesetPromise([main]); } }, 200); }
}
build();
})();
"""

_INTERACTIVE_SHELL = (
    "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">\n"
    "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
    "<title>__TITLE__</title>\n"
    "<link rel=\"preconnect\" href=\"https://fonts.googleapis.com\">\n"
    "<link href=\"https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600;9..144,700&family=Inter:wght@400;500;600;700&family=IBM+Plex+Mono:wght@500;600&display=swap\" rel=\"stylesheet\">\n"
    "<style>" + _CSS + "</style>\n"
    "<script>window.MathJax={tex:{inlineMath:[['\\\\(','\\\\)']],displayMath:[['$$','$$']]},svg:{fontCache:'global'}};</script>\n"
    "<script src=\"https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js\" id=\"MathJax-script\" async></script>\n"
    "<script src=\"https://cdn.jsdelivr.net/npm/marked/marked.min.js\"></script>\n"
    "</head>\n<body>\n"
    "<div class=\"topbar\"><div class=\"topbar-inner\">"
    "<span class=\"t\" id=\"topTitle\"></span>"
    "<div class=\"bar\" role=\"progressbar\" aria-label=\"Topic progress\" aria-valuemin=\"0\" aria-valuemax=\"100\" aria-valuenow=\"0\" id=\"pbar\"><i id=\"pfill\"></i></div>"
    "<span class=\"score\" id=\"scoreLbl\">0 / 0 done</span>"
    "</div></div>\n"
    "<main id=\"main\"></main>\n"
    "<script type=\"application/json\" id=\"notes-data\">__DATA_JSON__</script>\n"
    "<script>" + _JS + "</script>\n"
    "</body></html>\n"
)


def render_interactive_html(n: InteractiveNotes) -> str:
    """InteractiveNotes -> one self-contained interactive HTML string.

    Embeds the JSON inline (escaping ``<`` so a stray ``</script>`` in a field
    cannot close the island) and renders it client-side via the block dispatcher.
    """
    payload = n.model_dump_json().replace("<", "\\u003c")
    title = f"{n.topic} · {n.board} · Interactive — AP Guru"
    return _INTERACTIVE_SHELL.replace("__TITLE__", title).replace("__DATA_JSON__", payload)


# ---------------------------------------------------------------------------
# Deterministic post-generation validator (no model). Appends human-readable
# problems to review_flags so a broken interactive can't ship silently. The
# _safe_eval here mirrors the JS `evalExpr` whitelist (arithmetic + names only).
# ---------------------------------------------------------------------------
import ast as _ast
import operator as _op

_SAFE_OPS = {_ast.Add: _op.add, _ast.Sub: _op.sub, _ast.Mult: _op.mul,
             _ast.Div: _op.truediv, _ast.USub: _op.neg, _ast.UAdd: _op.pos}


def _safe_eval(expr: str, scope: dict) -> float:
    def ev(node):
        if isinstance(node, _ast.Expression):
            return ev(node.body)
        if isinstance(node, _ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, _ast.BinOp) and type(node.op) in _SAFE_OPS:
            return _SAFE_OPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, _ast.UnaryOp) and type(node.op) in _SAFE_OPS:
            return _SAFE_OPS[type(node.op)](ev(node.operand))
        if isinstance(node, _ast.Name):
            if node.id in scope:
                return scope[node.id]
            raise ValueError(f"unknown name {node.id!r}")
        raise ValueError(f"disallowed node {type(node).__name__}")
    return ev(_ast.parse(expr, mode="eval"))


def _iter_blocks(n: InteractiveNotes):
    if n.hook is not None:
        yield ("hook", n.hook)
    for s in n.sections:
        for b in s.blocks:
            yield (f"section '{s.heading}'", b)
    for b in n.practice:
        yield ("practice", b)


def interactive_block_ids(n: InteractiveNotes) -> list[str]:
    """Blocks that count toward the progress tracker (parity with the JS `reg`)."""
    from schemas_v2 import INTERACTIVE_BLOCK_TYPES
    return [f"{b.type}-{i}" for i, (_w, b) in enumerate(_iter_blocks(n))
            if b.type in INTERACTIVE_BLOCK_TYPES]


def validate_interactives(n: InteractiveNotes) -> list[str]:
    """Deterministic checks over the interactive blocks; returns problems to
    append to review_flags. Empty list == clean."""
    from schemas_v2 import SVG_TEMPLATES
    flags: list[str] = []
    for where, b in _iter_blocks(n):
        t = b.type
        if t == "mcq":
            nc = sum(1 for o in b.options if o.correct)
            if nc != 1:
                flags.append(f"[{where}] MCQ '{b.question[:40]}' has {nc} correct options (need exactly 1).")
            if len(b.options) < 2:
                flags.append(f"[{where}] MCQ '{b.question[:40]}' has < 2 options.")
        elif t == "numeric":
            if b.tolerance is None or b.tolerance <= 0:
                flags.append(f"[{where}] numeric '{b.label}' has non-positive tolerance.")
            for w in b.wrong_answers:
                if abs(w.value - b.answer) <= (b.tolerance or 0):
                    flags.append(f"[{where}] numeric '{b.label}' diagnostic {w.value} is within tolerance of the answer {b.answer}.")
            if not b.mark_scheme:
                flags.append(f"[{where}] numeric '{b.label}' has no mark scheme.")
        elif t == "sim":
            scope = {i.key: i.default for i in b.inputs}
            scope.update({c.key: c.value for c in b.constants})
            for expr in (b.expression, b.qline_expression):
                if not expr:
                    continue
                try:
                    val = _safe_eval(expr, scope)
                    if val != val or abs(val) == float("inf"):
                        flags.append(f"[{where}] sim '{b.title}' expression '{expr}' is not finite at defaults.")
                except Exception as e:  # noqa: BLE001
                    flags.append(f"[{where}] sim '{b.title}' expression '{expr}' invalid: {e}")
        elif t == "sort":
            bkeys = {bk.key for bk in b.buckets}
            used = set()
            for it in b.items:
                if it.correct_bucket not in bkeys:
                    flags.append(f"[{where}] sort item '{it.label}' -> unknown bucket '{it.correct_bucket}'.")
                used.add(it.correct_bucket)
            for bk in b.buckets:
                if bk.key not in used:
                    flags.append(f"[{where}] sort bucket '{bk.key}' has no items.")
        elif t == "toggle_diagram":
            if b.template not in SVG_TEMPLATES:
                flags.append(f"[{where}] toggle_diagram uses unknown template '{b.template}'.")
            if len(b.states) < 2:
                flags.append(f"[{where}] toggle_diagram '{b.title}' has < 2 states.")
        elif t == "cycle_diagram":
            nodes = {"top_left", "top_right", "bottom"}
            for e in b.edges:
                if e.frm not in nodes or e.to not in nodes:
                    flags.append(f"[{where}] cycle_diagram edge has an invalid endpoint.")
    return flags
