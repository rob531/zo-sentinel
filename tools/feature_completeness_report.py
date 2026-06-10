#!/usr/bin/env python3
"""feature_completeness_report.py -- render a capability-completeness map
(capmap.json) into ONE self-contained, interactive HTML file you can open in a
browser straight from a terminal (no server, no deps).

This is the FEATURE-LEVEL evaluation surface: capability area -> endpoints ->
I/O wiring -> UI -> served-or-orphaned -> gaps. It deliberately judges
*wiring/completeness*, never code syntax (which a terminal user can't rank).

Designed to be the E2E weekly-runner's output: regenerate capmap.json (today an
analysis pass; later a deterministic scanner), then re-run this to refresh the
report. Rendering is fully data-driven -- the JSON is embedded and drawn
client-side, so filters/expand work offline.

Usage:
  python tools/feature_completeness_report.py [capmap.json] [out.html]
Defaults: C:/tmp/capmap.json  ->  feature_completeness.html
"""
import json, sys, html, datetime

CAP = sys.argv[1] if len(sys.argv) > 1 else "C:/tmp/capmap.json"
OUT = sys.argv[2] if len(sys.argv) > 2 else "feature_completeness.html"

with open(CAP, "r", encoding="utf-8") as f:
    data = json.load(f)

# Build-time stamp passed in (scripts can't call now() deterministically in some
# harnesses, but a standalone CLI can).
stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Zo Sentinel — Feature Completeness</title>
<style>
  :root{
    --bg:#0d1117; --panel:#161b22; --panel2:#1c2129; --line:#30363d;
    --fg:#e6edf3; --mut:#8b949e;
    --ok:#3fb950; --warn:#d29922; --bad:#f85149; --info:#58a6ff;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);
    font:14px/1.5 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
  header{padding:20px 24px;border-bottom:1px solid var(--line);
    background:linear-gradient(180deg,#11161d,#0d1117)}
  h1{margin:0 0 4px;font-size:18px;letter-spacing:.3px}
  .sub{color:var(--mut);font-size:12px}
  .cards{display:flex;gap:12px;flex-wrap:wrap;margin-top:14px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:8px;
    padding:10px 14px;min-width:96px}
  .card .n{font-size:22px;font-weight:700}
  .card .l{font-size:11px;color:var(--mut);text-transform:uppercase;letter-spacing:.5px}
  .wrap{padding:18px 24px;max-width:1100px;margin:0 auto}
  .filters{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 16px}
  .chip{background:var(--panel);border:1px solid var(--line);color:var(--fg);
    padding:6px 12px;border-radius:999px;cursor:pointer;font-size:12px}
  .chip.on{background:var(--info);border-color:var(--info);color:#04111f;font-weight:600}
  .chip.act{margin-left:auto}
  details.area{background:var(--panel);border:1px solid var(--line);
    border-radius:10px;margin:0 0 12px;overflow:hidden}
  details.area[open]{border-color:#3d444d}
  summary{list-style:none;cursor:pointer;padding:12px 16px;display:flex;
    align-items:center;gap:10px}
  summary::-webkit-details-marker{display:none}
  .caret{color:var(--mut);transition:transform .15s;font-size:12px}
  details[open] .caret{transform:rotate(90deg)}
  .aname{font-weight:600;font-size:15px}
  .badge{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;
    padding:3px 8px;border-radius:5px}
  .b-complete{background:rgba(63,185,80,.16);color:var(--ok)}
  .b-partial{background:rgba(210,153,34,.16);color:var(--warn)}
  .b-gap{background:rgba(248,81,73,.16);color:var(--bad)}
  .count{color:var(--mut);font-size:12px;margin-left:auto;white-space:nowrap}
  .body{padding:4px 16px 16px;border-top:1px solid var(--line)}
  .tables{color:var(--mut);font-size:12px;margin:10px 0;font-family:ui-monospace,monospace}
  .sec{font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:var(--mut);
    margin:14px 0 6px}
  .row{display:flex;gap:10px;align-items:baseline;padding:6px 8px;border-radius:6px}
  .row:hover{background:var(--panel2)}
  .dot{width:8px;height:8px;border-radius:50%;flex:none;margin-top:6px}
  .d-ok{background:var(--ok)} .d-warn{background:var(--warn)} .d-bad{background:var(--bad)}
  .meth{font-family:ui-monospace,monospace;font-size:11px;font-weight:700;
    padding:1px 6px;border-radius:4px;flex:none;min-width:46px;text-align:center}
  .m-GET{background:rgba(88,166,255,.16);color:var(--info)}
  .m-POST{background:rgba(63,185,80,.16);color:var(--ok)}
  .m-PUT{background:rgba(210,153,34,.16);color:var(--warn)}
  .m-DELETE{background:rgba(248,81,73,.16);color:var(--bad)}
  .path{font-family:ui-monospace,monospace;font-size:12.5px}
  .file{color:var(--mut);font-size:11px}
  .io{color:var(--mut);font-size:11.5px;margin-left:auto;text-align:right;max-width:48%}
  .io.bug{color:var(--warn)}
  .ui-served{color:var(--ok);font-weight:700;font-size:11px}
  .ui-orphan{color:var(--bad);font-weight:700;font-size:11px}
  .ev{color:var(--mut);font-size:11.5px;margin-left:auto;text-align:right;max-width:55%}
  .gaps{margin:8px 0 0;padding:0;list-style:none}
  .gaps li{padding:6px 10px;margin:4px 0;border-left:3px solid var(--bad);
    background:rgba(248,81,73,.06);border-radius:0 6px 6px 0;font-size:12.5px}
  .gaps li.warn{border-left-color:var(--warn);background:rgba(210,153,34,.06)}
  .hidden{display:none}
  footer{color:var(--mut);font-size:11px;text-align:center;padding:24px}
  code{font-family:ui-monospace,monospace;background:var(--panel2);padding:1px 5px;
    border-radius:4px;font-size:12px}
</style></head><body>
<header>
  <h1>Zo Sentinel — Feature Completeness</h1>
  <div class="sub">capability area → endpoints → I/O wiring → UI → gaps &nbsp;·&nbsp; generated __STAMP__ &nbsp;·&nbsp; wiring-level, not syntax</div>
  <div class="cards" id="cards"></div>
</header>
<div class="wrap">
  <div class="filters" id="filters">
    <div class="chip on" data-f="all">All areas</div>
    <div class="chip" data-f="gap">Gaps only</div>
    <div class="chip" data-f="orphan">Has orphaned UI</div>
    <div class="chip" data-f="bug">Has storage bug</div>
    <div class="chip act" id="toggleAll">Expand all</div>
  </div>
  <div id="tree"></div>
</div>
<footer>self-contained · open in any browser · re-run <code>feature_completeness_report.py</code> after refreshing capmap.json</footer>
<script>
const DATA = __DATA__;
const BUGWORDS = ['wrong table','not in schema','absent from schema','mismatch','WRONG','effectively false'];
const isBug = e => !e.reaches_storage || BUGWORDS.some(w => (e.io||'').toLowerCase().includes(w.toLowerCase()));
const epDot = e => !e.reaches_storage ? 'd-bad' : (isBug(e) ? 'd-warn' : 'd-ok');
const areaHasOrphan = a => (a.ui||[]).some(u => !u.served);
const areaHasBug = a => (a.endpoints||[]).some(isBug);

// summary cards
const s = DATA.summary || {};
const cards = [
  ['Areas', s.areas_total, 'info'],
  ['Complete', s.areas_complete, 'ok'],
  ['Partial', s.areas_partial, 'warn'],
  ['Gap', s.areas_gap, 'bad'],
  ['Orphaned UI', s.orphaned_ui_count, 'bad'],
  ['Endpoints', s.endpoints_total, 'info'],
];
document.getElementById('cards').innerHTML = cards.map(([l,n,c]) =>
  `<div class="card"><div class="n" style="color:var(--${c})">${n??'—'}</div><div class="l">${l}</div></div>`).join('');

const esc = t => (t==null?'':String(t)).replace(/[&<>"]/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m]));

function areaHTML(a){
  const eps = (a.endpoints||[]).map(e => `
    <div class="row">
      <span class="dot ${epDot(e)}"></span>
      <span class="meth m-${esc(e.method)}">${esc(e.method)}</span>
      <span><span class="path">${esc(e.path)}</span> <span class="file">${esc(e.file)}</span></span>
      <span class="io ${isBug(e)?'bug':''}">${esc(e.io)}</span>
    </div>`).join('') || '<div class="row"><span class="file">no HTTP endpoints</span></div>';

  const uis = (a.ui||[]).map(u => `
    <div class="row">
      <span class="dot ${u.served?'d-ok':'d-bad'}"></span>
      <span><span class="path">${esc(u.file)}</span> <span class="file">${esc(u.kind)}</span></span>
      <span class="${u.served?'ui-served':'ui-orphan'}">${u.served?'SERVED':'ORPHANED'}</span>
      <span class="ev">${esc(u.evidence)}</span>
    </div>`).join('') || '<div class="row"><span class="file">no UI for this area</span></div>';

  const gaps = (a.gaps||[]).map(g => {
    const warn = /no ui|no dedicated|direct api|raw|bypass/i.test(g) && !/wrong|orphan/i.test(g);
    return `<li class="${warn?'warn':''}">${esc(g)}</li>`;
  }).join('');

  const orphanN = (a.ui||[]).filter(u=>!u.served).length;
  const meta = `${(a.endpoints||[]).length} ep · ${orphanN} orphaned UI`;
  return `<details class="area" data-status="${esc(a.status)}" data-orphan="${areaHasOrphan(a)}" data-bug="${areaHasBug(a)}">
    <summary>
      <span class="caret">▶</span>
      <span class="aname">${esc(a.name)}</span>
      <span class="badge b-${esc(a.status)}">${esc(a.status)}</span>
      <span class="count">${meta}</span>
    </summary>
    <div class="body">
      <div class="tables">tables: ${(a.tables||[]).map(esc).join(', ')||'—'}</div>
      <div class="sec">Endpoints &amp; I/O wiring</div>${eps}
      <div class="sec">UI</div>${uis}
      ${gaps?`<div class="sec">Gaps</div><ul class="gaps">${gaps}</ul>`:''}
    </div>
  </details>`;
}

const tree = document.getElementById('tree');
tree.innerHTML = (DATA.areas||[]).map(areaHTML).join('');

// filters
let filter = 'all';
function apply(){
  document.querySelectorAll('details.area').forEach(d => {
    let show = true;
    if(filter==='gap')    show = d.dataset.status==='gap';
    if(filter==='orphan') show = d.dataset.orphan==='true';
    if(filter==='bug')    show = d.dataset.bug==='true';
    d.classList.toggle('hidden', !show);
  });
}
document.querySelectorAll('.chip[data-f]').forEach(c => c.onclick = () => {
  document.querySelectorAll('.chip[data-f]').forEach(x=>x.classList.remove('on'));
  c.classList.add('on'); filter = c.dataset.f; apply();
});
let allOpen=false;
document.getElementById('toggleAll').onclick = e => {
  allOpen=!allOpen;
  document.querySelectorAll('details.area:not(.hidden)').forEach(d=>d.open=allOpen);
  e.target.textContent = allOpen?'Collapse all':'Expand all';
};
</script></body></html>
"""

out = (PAGE
       .replace("__STAMP__", html.escape(stamp))
       .replace("__DATA__", json.dumps(data)))
with open(OUT, "w", encoding="utf-8") as f:
    f.write(out)
print(f"wrote {OUT}  ({len(data.get('areas', []))} areas, "
      f"{data.get('summary', {}).get('orphaned_ui_count', '?')} orphaned UIs)")
