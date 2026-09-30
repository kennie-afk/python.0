// Headless-Chrome screenshot helper over the DevTools protocol (no dependencies; Node 22+).
// usage: node tools/shot.mjs <baseUrl> <outDir> <loginJson|-> <path> [<path>...]
//   loginJson: {"path":"/login","fields":{"input[name=email]":"a@b.c","input[name=password]":"pw"},"submit":"button[type=submit]"}
import { spawn } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';

const [base, out, loginArg, ...paths] = process.argv.slice(2);
mkdirSync(out, { recursive: true });
const port = 19000 + Math.floor(Math.random() * 500);
const chrome = spawn(process.env.CHROME || 'google-chrome', [
  '--headless=new', '--no-sandbox', '--disable-gpu', `--remote-debugging-port=${port}`,
  '--window-size=1440,900', `--user-data-dir=/tmp/shot-${port}`, 'about:blank'
], { stdio: 'ignore' });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function connect() {
  for (let i = 0; i < 60; i++) {
    try {
      const tabs = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
      const page = tabs.find((t) => t.type === 'page');
      if (page) return page.webSocketDebuggerUrl;
    } catch { /* not up yet */ }
    await sleep(250);
  }
  throw new Error('chrome did not start');
}
const ws = new WebSocket(await connect());
await new Promise((r) => (ws.onopen = r));
let id = 0; const pending = new Map(); const logs = [];
ws.onmessage = (m) => {
  const msg = JSON.parse(m.data);
  if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
  if (msg.method === 'Runtime.consoleAPICalled' && ['error', 'warning'].includes(msg.params.type))
    logs.push(msg.params.args.map((a) => a.value ?? a.description).join(' '));
  if (msg.method === 'Runtime.exceptionThrown') logs.push('EXC ' + msg.params.exceptionDetails.text);
};
const send = (method, params = {}) => new Promise((res) => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
const evaluate = async (expression) => (await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })).result?.result?.value;
await send('Page.enable'); await send('Runtime.enable');
const go = async (url, wait = 1800) => { await send('Page.navigate', { url }); await sleep(wait); };

if (loginArg && loginArg !== '-') {
  const login = JSON.parse(loginArg);
  await go(base + login.path);
  for (const [sel, val] of Object.entries(login.fields)) {
    await evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(sel)});const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;s.call(e,${JSON.stringify(val)});e.dispatchEvent(new Event('input',{bubbles:true}));})()`);
  }
  await evaluate(`document.querySelector(${JSON.stringify(login.submit)}).click()`);
  await sleep(3000);
}
const report = [];
for (const p of paths) {
  logs.length = 0;
  await go(base + p);
  const name = p.replace(/[^a-z0-9]+/gi, '_').replace(/^_|_$/g, '') || 'home';
  const shot = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true });
  writeFileSync(`${out}/${name}.png`, Buffer.from(shot.result.data, 'base64'));
  const text = await evaluate('document.body.innerText.slice(0,1500)');
  const radii = await evaluate(`(()=>{const c={};for(const e of document.querySelectorAll('*')){const r=getComputedStyle(e).borderTopLeftRadius;if(r&&r!=='0px')c[r]=(c[r]||0)+1}return JSON.stringify({root:getComputedStyle(document.documentElement).fontSize,radii:c})})()`);
  report.push({ path: p, url: await evaluate('location.pathname'), radii, errors: [...logs], text });
}
writeFileSync(`${out}/report.json`, JSON.stringify(report, null, 2));
for (const r of report) console.log(`== ${r.path} -> ${r.url}\n${r.radii}\nerrors: ${r.errors.join(' | ') || 'none'}\n${r.text.slice(0, 500).replace(/\n+/g, ' | ')}\n`);
ws.close(); chrome.kill(); process.exit(0);
