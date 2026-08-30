#!/usr/bin/env node
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { spawn } from 'node:child_process';

const sleep = (ms) => new Promise((resolvePromise) => setTimeout(resolvePromise, ms));
async function waitFor(fn, label) { const deadline = Date.now() + 20000; while (Date.now() < deadline) { try { const value = await fn(); if (value) return value; } catch {} await sleep(100); } throw new Error(label + ' timed out'); }

class CdpClient {
  constructor(url) { this.socket = new WebSocket(url); this.nextId = 1; this.pending = new Map(); this.listeners = []; }
  async connect() { await new Promise((resolvePromise, reject) => { this.socket.addEventListener('open', resolvePromise, { once: true }); this.socket.addEventListener('error', reject, { once: true }); }); this.socket.addEventListener('message', (event) => { const message = JSON.parse(event.data); if (message.id) { const pending = this.pending.get(message.id); if (!pending) return; this.pending.delete(message.id); message.error ? pending.reject(new Error(message.error.message)) : pending.resolve(message.result || {}); } else this.listeners.forEach((listener) => listener(message.method, message.params || {})); }); }
  send(method, params = {}) { const id = this.nextId++; return new Promise((resolvePromise, reject) => { this.pending.set(id, { resolve: resolvePromise, reject }); this.socket.send(JSON.stringify({ id, method, params })); }); }
  on(listener) { this.listeners.push(listener); }
}

async function evaluate(client, expression, awaitPromise = false) { const result = await client.send('Runtime.evaluate', { expression, awaitPromise, returnByValue: true, userGesture: true }); if (result.exceptionDetails) throw new Error('browser evaluation failed'); return result.result?.value; }

async function main() {
  const args = Object.fromEntries(process.argv.slice(2).map((value, index, values) => index % 2 === 0 ? [value.replace(/^--/, ''), values[index + 1]] : null).filter(Boolean));
  const base = args['base-url']; if (!base) throw new Error('--base-url is required');
  const profile = args.profile; const browser = spawn(args.browser || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', ['--headless=new', '--no-first-run', '--disable-background-networking', '--disable-extensions', '--remote-debugging-port=0', '--user-data-dir=' + profile, 'about:blank'], { stdio: 'ignore' });
  let client; const failures = []; const external = [];
  try {
    const port = await waitFor(async () => (await readFile(resolve(profile, 'DevToolsActivePort'), 'utf8')).trim().split(/\r?\n/)[0], 'DevTools port');
    const target = await waitFor(async () => { const response = await fetch('http://127.0.0.1:' + port + '/json/list'); const items = await response.json(); return items.find((item) => item.type === 'page' && item.webSocketDebuggerUrl); }, 'CDP target');
    client = new CdpClient(target.webSocketDebuggerUrl); await client.connect();
    client.on((method, params) => { if (method === 'Runtime.consoleAPICalled' && ['error', 'warning'].includes(params.type)) failures.push(params.type); if (method === 'Network.requestWillBeSent') { const origin = new URL(params.request.url).origin; if (origin !== base) external.push(origin); } if (method === 'Fetch.authRequired') client.send('Fetch.continueWithAuth', { requestId: params.requestId, authChallengeResponse: { response: 'ProvideCredentials', username: args.username || '', password: args.token || '' } }); if (method === 'Fetch.requestPaused') client.send('Fetch.continueRequest', { requestId: params.requestId }); });
    await Promise.all([client.send('Page.enable'), client.send('Runtime.enable'), client.send('Network.enable'), client.send('Fetch.enable', { handleAuthRequests: true, patterns: [{ urlPattern: '*' }] })]);
    await client.send('Page.navigate', { url: base + '/' }); await waitFor(() => evaluate(client, "document.readyState !== 'loading'"), 'DOM');
    const request = (path, method, body) => "fetch(" + JSON.stringify(path) + ",{method:" + JSON.stringify(method) + ",credentials:'same-origin',headers:{'Content-Type':'application/json'},body:" + (body ? JSON.stringify(JSON.stringify(body)) : 'undefined') + "}).then(async(r)=>({status:r.status,body:await r.json()}))";
    const briefCreate = await evaluate(client, request('/v1/workbench/content/briefs', 'POST', { topic: '运行时内容', channel: '知乎', format: '长文', audience: '决策者' }), true);
    const briefId = briefCreate.body.brief.briefId;
    const approved = await evaluate(client, request('/v1/workbench/content/briefs/' + briefId + '/approve', 'POST', { expectedVersion: 1 }), true);
    const asset = await evaluate(client, request('/v1/workbench/content/assets', 'POST', { briefId, title: '运行时封面', channel: '图片资产', format: '提示词任务', assetType: 'image', prompt: '工业场景' }), true);
    const itemCreate = await evaluate(client, request('/v1/workbench/calendar/items', 'POST', { title: '运行时日程', date: new Date().toISOString().slice(0, 10), source: '人工安排', note: '' }), true);
    const itemId = itemCreate.body.item.itemId;
    const itemConfirm = await evaluate(client, request('/v1/workbench/calendar/items/' + itemId, 'PATCH', { expectedVersion: 1, status: 'confirmed' }), true);
    const workbenchBrief = await evaluate(client, request('/v1/workbench/briefs', 'POST', { deidentified: true, productName: '运行时产品', productType: '工业品', targetMarket: '印度', audience: '决策者', objective: '验证日程建议', timeframe: '本周', background: '浏览器门禁', constraints: [] }), true);
    const researchRun = await evaluate(client, request('/v1/workbench/research-runs', 'POST', { briefId: workbenchBrief.body.brief.briefId, sources: [{ url: 'https://example.com/runtime-source', title: 'Runtime source', excerpt: 'Bounded runtime observation.', observedAt: new Date().toISOString().slice(0, 10), scope: 'browser gate', confidence: 'high' }], observations: [{ claim: 'Bounded observation', classification: 'fact', confidence: 'high' }] }), true);
    const suggestionsBeforeAcceptance = await evaluate(client, "fetch('/v1/workbench/schedule-suggestions',{credentials:'same-origin'}).then((r)=>r.json())", true);
    const researchSuggestion = suggestionsBeforeAcceptance.suggestions.find((item) => item.suggestionId === 'research:' + researchRun.body.researchRun.runId);
    const brief = await evaluate(client, "fetch('/v1/workbench/content/briefs',{credentials:'same-origin'}).then((r)=>r.json())", true);
    const calendar = await evaluate(client, "fetch('/v1/workbench/calendar/items?period=all',{credentials:'same-origin'}).then((r)=>r.json())", true);
    const openView = async (view, titleSelector) => {
      await evaluate(client, "document.querySelector(" + JSON.stringify('[data-primary-view=\"' + view + '\"]') + ")?.click()");
      await waitFor(() => evaluate(client, "Boolean(document.querySelector(" + JSON.stringify(titleSelector) + "))"), view + ' view');
    };
    await openView('content', '#contentWorkbenchTitle');
    const contentBeforeRefresh = await waitFor(() => evaluate(client, "({topic:document.querySelector('#contentBriefForm input[name=topic]')?.value || '', asset:document.querySelector('#assetTaskList')?.textContent || '', sync:document.querySelector('#contentSyncState')?.textContent || ''})"), 'content UI data');
    await client.send('Page.reload', { ignoreCache: true });
    await waitFor(() => evaluate(client, "document.readyState !== 'loading'"), 'reload DOM');
    await openView('content', '#contentWorkbenchTitle');
    const contentAfterRefresh = await waitFor(() => evaluate(client, "({topic:document.querySelector('#contentBriefForm input[name=topic]')?.value || '', asset:document.querySelector('#assetTaskList')?.textContent || '', sync:document.querySelector('#contentSyncState')?.textContent || ''})"), 'content refresh data');
    await evaluate(client, "document.querySelector('#obsidianForm input[name=vaultPath]').value = " + JSON.stringify(args['vault-path']) + "; document.querySelector('#obsidianForm textarea[name=relativePaths]').value = ''; document.querySelector('#obsidianForm').requestSubmit()");
    const obsidianBeforeRefresh = await waitFor(() => evaluate(client, "({sync:document.querySelector('#obsidianSyncState')?.textContent || '',notes:document.querySelector('#obsidianNoteList')?.textContent || '',page:document.body.textContent || ''})").then((value) => value.sync.includes('只读') && value.notes.includes('浏览器门禁知识') ? value : null), 'Obsidian UI data');
    const obsidianResponse = await evaluate(client, "fetch('/v1/workbench/obsidian/notes',{credentials:'same-origin'}).then(async(r)=>({status:r.status,text:await r.text()}))", true);
    await client.send('Page.reload', { ignoreCache: true });
    await waitFor(() => evaluate(client, "document.readyState !== 'loading'"), 'Obsidian reload DOM');
    await openView('content', '#contentWorkbenchTitle');
    const obsidianAfterRefresh = await waitFor(() => evaluate(client, "({sync:document.querySelector('#obsidianSyncState')?.textContent || '',notes:document.querySelector('#obsidianNoteList')?.textContent || '',page:document.body.textContent || ''})").then((value) => value.sync.includes('只读') && value.notes.includes('浏览器门禁知识') ? value : null), 'Obsidian refresh data');
    await openView('calendar', '#calendarWorkbenchTitle');
    const calendarBeforeRefresh = await waitFor(() => evaluate(client, "document.querySelector('#calendarList')?.textContent || ''"), 'calendar UI data');
    const suggestionVisible = await waitFor(() => evaluate(client, "document.querySelector('#calendarWorkbenchTitle') && Array.from(document.querySelectorAll('[data-suggestion]')).some((button)=>button.textContent.includes('纳入日程'))"), 'calendar suggestion UI');
    await evaluate(client, "Array.from(document.querySelectorAll('[data-suggestion]')).find((button)=>button.textContent.includes('纳入日程'))?.click()");
    const calendarSuggestionAccepted = await waitFor(() => evaluate(client, "({calendar:document.querySelector('#calendarList')?.textContent || '', suggestions:document.querySelector('.calendar-suggestions')?.textContent || ''})").then((value) => researchSuggestion && value.calendar.includes(researchSuggestion.title) && !value.suggestions.includes(researchSuggestion.title) ? value : null), 'calendar suggestion acceptance');
    await client.send('Page.reload', { ignoreCache: true });
    await waitFor(() => evaluate(client, "document.readyState !== 'loading'"), 'second reload DOM');
    await openView('calendar', '#calendarWorkbenchTitle');
    const calendarAfterRefresh = await waitFor(() => evaluate(client, "document.querySelector('#calendarList')?.textContent || ''"), 'calendar refresh data');
    const suggestionsAfterRefresh = await evaluate(client, "fetch('/v1/workbench/schedule-suggestions',{credentials:'same-origin'}).then((r)=>r.json())", true);
    const ui = await evaluate(client, "({content:!!document.querySelector('#contentNav'),calendar:!!document.querySelector('#calendarNav'),width:document.documentElement.scrollWidth<=innerWidth})");
    const marker = args['body-marker'];
    const result = { serverContentRoute: Array.isArray(brief.briefs) && briefCreate.status === 201 && approved.status === 201 && asset.status === 201 && asset.body.asset.status === 'needs_authorization', serverCalendarRoute: Array.isArray(calendar.items) && itemCreate.status === 201 && itemConfirm.status === 200 && calendar.items.some((item) => item.itemId === itemId && item.status === 'confirmed'), serverObsidianRoute: obsidianResponse.status === 200 && obsidianResponse.text.includes('Browser gate.md') && !obsidianResponse.text.includes(marker), contentRefreshRecovery: contentBeforeRefresh.topic === '运行时内容' && contentBeforeRefresh.asset.includes('运行时封面') && contentAfterRefresh.topic === contentBeforeRefresh.topic && contentAfterRefresh.asset.includes('运行时封面') && contentAfterRefresh.sync.includes('已同步'), calendarRefreshRecovery: calendarBeforeRefresh.includes('运行时日程') && calendarAfterRefresh.includes('运行时日程') && calendarAfterRefresh.includes('已确认'), calendarSuggestionAcceptance: Boolean(suggestionVisible && calendarSuggestionAccepted && !suggestionsAfterRefresh.suggestions.some((item) => item.suggestionId === researchSuggestion?.suggestionId)), obsidianRefreshRecovery: obsidianBeforeRefresh.sync.includes('只读') && obsidianAfterRefresh.sync.includes('只读') && obsidianAfterRefresh.notes.includes('浏览器门禁知识'), obsidianBodyRedacted: !obsidianBeforeRefresh.page.includes(marker) && !obsidianAfterRefresh.page.includes(marker), workbenchShell: ui.content && ui.calendar, responsivePassed: ui.width, noConsoleFailures: failures.length === 0, noExternalRequests: external.filter((origin) => origin !== 'null').length === 0 };
    if (Object.values(result).some((value) => value !== true)) throw new Error('gate result failed: ' + JSON.stringify(result) + ' brief=' + JSON.stringify(brief));
    process.stdout.write(JSON.stringify(result) + '\n');
  } finally { client?.socket.close(); browser.kill(); }
}
main().catch((error) => { process.stderr.write('WB-04 browser gate failed: ' + error.message + '\n'); process.exitCode = 1; });
