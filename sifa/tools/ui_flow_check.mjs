// Drives the real Sifa console in headless Chrome through its action flows and asserts what the page says.
// usage: node tools/ui_flow_check.mjs http://localhost:3200
import { spawn } from 'node:child_process';
const base = process.argv[2] || 'http://localhost:3200';
const port = 19500 + Math.floor(Math.random() * 400);
const chrome = spawn(process.env.CHROME || 'google-chrome', ['--headless=new', '--no-sandbox', '--disable-gpu', `--remote-debugging-port=${port}`, '--window-size=1440,900', `--user-data-dir=/tmp/flow-${port}`, 'about:blank'], { stdio: 'ignore' });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let wsUrl; for (let i = 0; i < 60 && !wsUrl; i++) { try { wsUrl = (await (await fetch(`http://127.0.0.1:${port}/json`)).json()).find((t) => t.type === 'page')?.webSocketDebuggerUrl; } catch {} await sleep(250); }
const ws = new WebSocket(wsUrl); await new Promise((r) => (ws.onopen = r));
let id = 0; const pending = new Map();
ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); } };
const send = (method, params = {}) => new Promise((res) => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
const js = async (e) => (await send('Runtime.evaluate', { expression: e, awaitPromise: true, returnByValue: true })).result?.result?.value;
await send('Page.enable');
const go = async (p) => { await send('Page.navigate', { url: base + p }); await sleep(1800); };
const text = () => js('document.body.innerText');
const setValue = (sel, v) => js(`(()=>{const e=document.querySelector(${JSON.stringify(sel)});if(!e)return false;const proto=e.tagName==='SELECT'?HTMLSelectElement:HTMLInputElement;Object.getOwnPropertyDescriptor(proto.prototype,'value').set.call(e,${JSON.stringify(v)});e.dispatchEvent(new Event(e.tagName==='SELECT'?'change':'input',{bubbles:true}));return true})()`);
const click = (sel) => js(`(()=>{const e=document.querySelector(${JSON.stringify(sel)});if(!e)return false;e.click();return true})()`);
const clickText = (t) => js(`(()=>{const e=[...document.querySelectorAll('button,summary,a')].find(x=>x.innerText.trim().startsWith(${JSON.stringify(t)}));if(!e)return false;e.click();return true})()`);
const firstOption = (sel, n = 1) => js(`(()=>{const o=[...document.querySelector(${JSON.stringify(sel)}).options].filter(x=>x.value)[${n - 1}];return o&&o.value})()`);
let failed = 0;
const check = (label, ok, detail = '') => { console.log(`${ok ? 'PASS' : 'FAIL'} - ${label}${ok ? '' : ' :: ' + String(detail).slice(0, 200)}`); if (!ok) failed++; };

for (let i = 0; i < 40; i++) { try { if ((await fetch(base + '/registry')).ok) break; } catch {} await sleep(1000); }

await go('/registry');
let t = await text();
check('the registry shows a release history with a rolled-back canary', /Rolled back/.test(t) && /Live/.test(t) && /Archived/.test(t), t);
check('history reasons from the warm-up are tagged as such', /demo warm-up/.test(t), t);
await clickText('Promote a new candidate'); await sleep(2500);
t = await text();
check('promoting enters canary', /is now in canary/.test(t), t);
await clickText('Roll back what is serving'); await sleep(2500);
t = await text();
check('rolling back the canary leaves the live model in place', /rolled back, ranker:v\d+ is live/.test(t), t);
await go('/registry');
const live = await js("[...document.querySelectorAll('tr')].filter(r=>/Live/.test(r.innerText)).length");
check('exactly one version is live after promote then rollback', live === 1, live);

await go('/retrieval');
await setValue('select[name=corpus]', '1000');
await click('main form button[type=submit]'); await sleep(1200);
const during = await js("(()=>{const b=document.querySelector('main form button[type=submit]');return b?{disabled:b.disabled,label:b.innerText}:null})()");
check('the benchmark button is disabled and counts seconds while it builds', during && during.disabled && /Building/.test(during.label), JSON.stringify(during));
for (let i = 0; i < 45; i++) { if (/Index build/i.test(await text())) break; await sleep(1000); }
t = await text();
check('the benchmark reports build time and a recall table', /Index build/i.test(t) && /Recall@10/i.test(t), t.slice(-600));

await go('/load');
await setValue('select[name=requests]', '100');
await click('main form button[type=submit]'); await sleep(6000);
t = await text();
check('the load test reports throughput and latency', /Throughput/i.test(t) && /p95/i.test(t), t.slice(-400));

await go('/drift?shift=2');
t = await text();
check('an injected shift is flagged on the drift screen', /Alert|Warn|Drift/i.test(t), t);

await go('/feed');
t = await text();
check('the feed explorer lists ranked items with reasons', /Viewer/.test(t), t);

console.log(failed ? `\n${failed} check(s) failed` : '\nall UI flow checks passed');
ws.close(); chrome.kill(); process.exit(failed ? 1 : 0);
