const { app, BrowserWindow, ipcMain, dialog, shell, clipboard } = require('electron');
const fs = require('node:fs/promises');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { launchEngine } = require('./engine-client.cjs');
const { report } = require('./diagnostics.cjs');
const { createDebugLog } = require('./debug-log.cjs');
const { startupProbe } = require('./startup-probe.cjs');

const startupProbeMode = process.argv.includes('--startup-test') || process.argv.includes('--startup-test-close-stdin');
const debugEnabled = require('../package.json').invoiceDebug === true || process.env.INVOICE_DEBUG === '1';
app.setName(debugEnabled ? '发票查验 Debug' : '发票查验');
if (process.env.INVOICE_APP_DATA) app.setPath('userData', process.env.INVOICE_APP_DATA);
else if (debugEnabled) app.setPath('userData', path.join(app.getPath('appData'), '发票查验 Debug'));
const debugLog = createDebugLog(debugEnabled || startupProbeMode ? path.join(app.getPath('userData'), 'debug-logs', new Date().toISOString().replace(/[:.]/g, '-')) : null);
debugLog.write('app-start', { version: app.getVersion(), platform: process.platform, architecture: process.arch, packaged: app.isPackaged });
let window;
let engine;
let importing = false;
let quitting = false;
let persistQueue = Promise.resolve();
let state = { phase: 'empty', import: null, progress: null, task: null, message: '', failures: [] };
const root = path.join(__dirname, '..');
const entryURL = pathToFileURL(path.join(__dirname, 'index.html')).href;
const config = { packaged: app.isPackaged, resourcesPath: process.resourcesPath, root, debugLog };
const stateFile = () => path.join(app.getPath('userData'), 'workspace.json');
const singleInstance = startupProbeMode || app.requestSingleInstanceLock();
if (!singleInstance) app.quit();
app.on('second-instance', () => { if (window) { if (window.isMinimized()) window.restore(); window.focus(); } });

function snapshot() {
  const { source, ledger, ...visibleTask } = state.task || {};
  return { ...state, version: app.getVersion(), retryLimit: state.import?.retry_limit ?? 2, debug: debugEnabled, task: state.task ? visibleTask : null, busy: importing || Boolean(engine) };
}

function settleInterruptedRows() {
  for (const row of Object.values(state.invoiceStates || {})) {
    if (row.kind === 'checking') { row.kind = 'pending'; row.reason = '上次查验已中断，继续后重新尝试'; }
  }
}

async function restoreInvoiceStates() {
  state.invoiceStates ||= {};
  if (state.task?.ledger) {
    try {
      const ledger = JSON.parse(await fs.readFile(state.task.ledger, 'utf8'));
      if (ledger.fingerprint === state.task.fingerprint) {
        state.invoiceStates = Object.fromEntries(Object.entries(ledger.items || {}).map(([id, record]) => [id, {
          kind: record.kind || 'pending', attempts: record.attempts || 0, reason: record.reason || '',
        }]));
      }
    } catch {}
  }
  settleInterruptedRows();
}

function publish(persist = true) {
  if (window && !window.isDestroyed()) window.webContents.send('workspace:changed', snapshot());
  if (persist) {
    const data = JSON.stringify(state);
    persistQueue = persistQueue.catch(() => {}).then(async () => {
      await fs.mkdir(app.getPath('userData'), { recursive: true });
      const temporary = `${stateFile()}.tmp`;
      await fs.writeFile(temporary, data, 'utf8');
      await fs.rename(temporary, stateFile());
    }).catch(() => {
      state.message = '无法保存续跑进度，请检查磁盘空间和应用目录权限。';
      publish(false);
    });
  }
}

function assertSender(event) {
  if (!window || event.sender !== window.webContents || event.senderFrame?.url !== entryURL || event.senderFrame !== window.webContents.mainFrame) {
    throw new Error('无效的应用请求');
  }
}

function handle(channel, action) {
  ipcMain.handle(channel, async (event, ...args) => {
    assertSender(event);
    try { return await action(...args); }
    catch (error) { debugLog.write('action-error', { channel, error_type: error.name, message: error.message }); publish(false); return { error: error.message || '操作失败，请重试' }; }
  });
}

async function importExcel(source) {
  if (engine || importing) throw new Error('请先停止当前任务');
  if (typeof source !== 'string' || !['.xlsx', '.xlsm', '.xls'].includes(path.extname(source).toLowerCase())) throw new Error('请选择 Excel 文件');
  const stat = await fs.stat(source);
  if (!stat.isFile()) throw new Error('请选择 Excel 文件');
  importing = true;
  state.message = '正在自动识别 Excel 表头…';
  publish(false);
  let imported;
  let error;
  let client;
  try {
    client = launchEngine(config, ['inspect', '--input', source], event => {
      if (event.type === 'imported') imported = event;
      if (event.type === 'error') error = event.message;
    });
    engine = client;
    const result = await client.completion;
    if (!imported || result.code !== 0) throw new Error(error || 'Excel 读取失败，请确认文件未加密或损坏');
    const previous = state.task;
    state.import = imported;
    state.message = '';
    state.phase = imported.total ? 'ready' : 'invalid';
    if (previous?.fingerprint === imported.fingerprint) {
      state.task.source = source;
      state.phase = imported.total ? (state.progress?.completed === imported.total ? 'finished' : 'stopped') : 'invalid';
    } else {
      state.failures = [];
      state.invoiceStates = {};
      state.task = { source, fingerprint: imported.fingerprint, output: null, ledger: null };
      state.progress = { total: imported.total, completed: 0, success: 0, rejected: 0, failed: 0 };
    }
    await restoreInvoiceStates();
    return snapshot();
  } finally {
    if (engine === client) engine = null;
    importing = false;
    state.message = '';
    publish();
  }
}

async function start(resetFailed = false) {
  if (engine || importing) throw new Error('当前任务正在运行');
  if (!state.import?.total || !state.task?.source) throw new Error('请先导入 Excel');
  if (!state.task.output) {
    const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
    const basename = path.basename(state.import.filename, path.extname(state.import.filename)).replace(/[<>:"/\\|?*\x00-\x1f]/g, '_').slice(0, 70);
    state.task.output = path.join(app.getPath('documents'), '发票查验', `${basename}-${timestamp}`);
  }
  // Re-read the source before using a previous ledger: a changed workbook must not silently enter an old task.
  let checked;
  let importError;
  importing = true;
  state.message = '正在检查 Excel…';
  publish(false);
  try {
    const checker = launchEngine(config, ['inspect', '--input', state.task.source], event => {
      if (event.type === 'imported') checked = event;
      if (event.type === 'error') importError = event.message;
    });
    engine = checker;
    const result = await checker.completion;
    if (result.code !== 0 || !checked) throw new Error(importError || '无法读取 Excel，请重新导入');
    if (checked.fingerprint !== state.task.fingerprint) throw new Error('Excel 内容已修改，请重新导入文件');
    state.import = checked;
  } finally { engine = null; importing = false; }
  await fs.mkdir(state.task.output, { recursive: true });
  await fs.access(state.task.output, require('node:fs').constants.W_OK);
  state.task.ledger ||= path.join(app.getPath('userData'), 'tasks', `${state.task.fingerprint}-${Date.now()}.json`);
  state.phase = 'running';
  state.message = '正在准备查验引擎';
  state.failures = [];
  state.current = null;
  state.waiting = null;
  state.diagnostic = null;
  state.engineStderr = '';
  publish();
  const args = ['run', '--input', state.task.source, '--output', state.task.output, '--state', state.task.ledger];
  if (resetFailed) args.push('--reset-failed');
  let finished = false;
  const client = launchEngine(config, args, event => {
    switch (event.type) {
      case 'heartbeat': return;
      case 'task-state': state.invoiceStates = event.items || {}; state.progress = pickProgress(event); break;
      case 'item-state':
        state.invoiceStates ||= {};
        state.invoiceStates[event.id] = { kind: event.kind, attempts: event.attempts, reason: event.reason };
        break;
      case 'stage':
        state.message = event.message; state.waiting = null;
        if (state.current?.id && state.invoiceStates?.[state.current.id]?.kind === 'checking') state.invoiceStates[state.current.id].reason = event.message;
        break;
      case 'invoice':
        state.current = { id: event.id, number: event.number, index: event.index, retry: event.retry };
        state.progress = pickProgress(event); state.waiting = null; state.message = '正在查验发票'; break;
      case 'retry':
        state.message = event.reason;
        state.current = { ...state.current, retry: event.retry };
        state.waiting = event.seconds;
        state.diagnostic = event.diagnostic || null;
        break;
      case 'attempt-failed': state.diagnostic = event.diagnostic || null; state.message = event.reason; break;
      case 'engine-stderr': state.engineStderr = event.detail; publish(false); return;
      case 'waiting': state.waiting = event.seconds; break;
      case 'control': state.phase = event.paused ? 'paused' : 'running'; break;
      case 'progress': state.progress = pickProgress(event); state.current = null; state.message = '准备查验下一张发票'; break;
      case 'finished':
        finished = true;
        state.progress = pickProgress(event); state.failures = event.failures || [];
        state.phase = event.cancelled ? 'stopped' : 'finished';
        if (event.cancelled) settleInterruptedRows();
        state.current = null; state.waiting = null; state.message = ''; break;
      case 'error': state.phase = 'stopped'; state.message = event.message; state.diagnostic = event.diagnostic || state.diagnostic; state.waiting = null; break;
      default: return;
    }
    publish(event.type !== 'waiting' && event.type !== 'stage');
  });
  engine = client;
  publish(false);
  client.completion.then(() => {
    if (!finished && state.phase !== 'stopped') {
      state.phase = 'stopped';
      state.message = '查验引擎意外停止。已保存进度，点击继续即可恢复。';
    }
  }).catch(error => {
    state.phase = 'stopped'; state.message = error.message;
  }).finally(() => {
    if (engine === client) engine = null;
    settleInterruptedRows();
    state.waiting = null;
    publish();
  });
  return snapshot();
}

function pickProgress(event) {
  return Object.fromEntries(['total', 'completed', 'success', 'rejected', 'failed'].map(key => [key, event[key] || 0]));
}

async function stop() {
  if (!engine || importing) return snapshot();
  state.phase = 'stopping'; state.message = '正在停止并保存进度'; publish(false);
  const client = engine;
  client.send('cancel');
  const timer = setTimeout(() => client.kill(), 8000);
  await client.completion.catch(() => {});
  clearTimeout(timer);
  return snapshot();
}

app.whenReady().then(async () => {
  if (!singleInstance) return;
  if (startupProbeMode) {
    const result = await startupProbe(config, process.argv.includes('--startup-test-close-stdin'));
    await fs.writeFile(path.join(debugLog.directory, 'startup-probe.json'), JSON.stringify(result, null, 2), 'utf8');
    app.exit(result.ok ? 0 : 1);
    return;
  }
  try {
    const restored = JSON.parse(await fs.readFile(stateFile(), 'utf8'));
    if (restored.task && restored.import && restored.progress) state = restored;
    if (['running', 'paused', 'stopping'].includes(state.phase)) {
      state.phase = 'stopped'; state.message = '上次任务尚未完成，点击继续查验即可接着处理。';
    }
    state.waiting = null; state.current = null;
  } catch {}
  if (state.task?.source && !Array.isArray(state.import?.invoices)) {
    let restoredImport;
    try {
      const inspector = launchEngine(config, ['inspect', '--input', state.task.source], event => { if (event.type === 'imported') restoredImport = event; });
      engine = inspector; importing = true;
      const result = await inspector.completion;
      if (result.code === 0 && restoredImport?.fingerprint === state.task.fingerprint) state.import = restoredImport;
      else { state.phase = 'stopped'; state.message = '请重新导入原 Excel，以查看每张发票的明细和已保存进度。'; }
    } finally { engine = null; importing = false; }
  }
  await restoreInvoiceStates();
  handle('workspace:get', () => snapshot());
  handle('diagnostics:copy', () => { clipboard.writeText(report(state, app.getVersion())); return { copied: true }; });
  handle('diagnostics:open', async () => {
    if (!debugLog.directory) throw new Error('请使用 debug 包打开日志');
    const error = await shell.openPath(debugLog.directory);
    if (error) throw new Error('无法打开 debug 日志文件夹');
    return null;
  });
  handle('excel:choose', async () => {
    if (engine || importing) throw new Error('请先停止当前任务');
    const result = await dialog.showOpenDialog(window, { title: '导入发票 Excel', properties: ['openFile'], filters: [{ name: 'Excel', extensions: ['xlsx', 'xlsm', 'xls'] }] });
    return result.canceled ? null : importExcel(result.filePaths[0]);
  });
  handle('excel:drop', source => importExcel(source));
  handle('task:start', reset => start(reset === true));
  handle('task:pause', () => { if (engine && !importing) engine.send('pause'); return snapshot(); });
  handle('task:resume', () => { if (engine && !importing) engine.send('resume'); return snapshot(); });
  handle('task:stop', stop);
  handle('output:choose', async () => {
    if (engine || importing || !state.task || state.task.ledger) throw new Error('任务开始前可以更改保存位置');
    const result = await dialog.showOpenDialog(window, { title: '选择发票保存文件夹', properties: ['openDirectory', 'createDirectory'] });
    if (!result.canceled) {
      const name = `发票查验-${new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)}`;
      state.task.output = path.join(result.filePaths[0], name); publish();
    }
    return snapshot();
  });
  handle('output:open', async () => {
    if (!state.task?.output) throw new Error('开始查验后即可查看发票文件夹');
    const error = await shell.openPath(state.task.output);
    if (error) throw new Error('无法打开保存文件夹，请检查路径是否存在');
    return null;
  });
  window = new BrowserWindow({
    width: 1060, height: 900, minWidth: 850, minHeight: 660, title: debugEnabled ? '发票查验 Debug' : '发票查验', backgroundColor: '#f6f7f4',
    autoHideMenuBar: true, show: false,
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'), contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('will-navigate', event => event.preventDefault());
  window.webContents.session.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  await window.loadFile(path.join(__dirname, 'index.html'));
  window.show();
  window.on('close', event => {
    if (engine && !quitting) {
      event.preventDefault(); quitting = true;
      if (importing) engine.kill();
      stop().finally(() => { engine?.kill(); app.quit(); });
    }
  });
}).catch(async error => {
  debugLog.write('app-error', { error_type: error.name, message: error.message });
  if (startupProbeMode) {
    try { await fs.writeFile(path.join(debugLog.directory, 'startup-probe.json'), JSON.stringify({ ok: false, error: error.message }, null, 2), 'utf8'); } catch {}
    app.exit(1);
  } else { dialog.showErrorBox('无法打开应用', error.message); app.quit(); }
});

app.on('window-all-closed', () => app.quit());
app.on('before-quit', event => {
  if (engine && !quitting) {
    event.preventDefault(); quitting = true;
    if (importing) {
      const client = engine;
      client.kill();
      client.completion.catch(() => {}).finally(() => app.quit());
    } else {
      stop().finally(() => app.quit());
    }
  } else if (engine) engine.kill();
});
process.on('exit', () => { debugLog.write('app-exit'); engine?.kill(); });
