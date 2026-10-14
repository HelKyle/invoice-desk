const { spawn, spawnSync } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');
const readline = require('node:readline');
const { redact } = require('./diagnostics.cjs');

function engineCommand({ packaged, resourcesPath, root }) {
  if (packaged) {
    const embedded = path.join(resourcesPath, 'engine', 'python.exe');
    if (process.platform === 'win32' && fs.existsSync(embedded)) {
      return { executable: embedded, prefix: ['-u', path.join(resourcesPath, 'engine', 'app', 'desktop_engine.py')] };
    }
    const executable = path.join(resourcesPath, 'engine', process.platform === 'win32' ? 'invoice-engine.exe' : 'invoice-engine');
    if (!fs.existsSync(executable)) throw new Error('查验引擎缺失，请重新安装应用');
    return { executable, prefix: [] };
  }
  const suffix = process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python';
  const isolated = path.join(root, '.venv-engine', suffix);
  const python = process.env.INVOICE_PYTHON || (fs.existsSync(isolated) ? isolated : path.join(root, '.venv', suffix));
  if (!fs.existsSync(python)) throw new Error('开发环境缺少 Python。请先按 README 安装查验依赖。');
  return { executable: python, prefix: ['-u', path.join(root, 'desktop_engine.py')] };
}

function terminateTree(child) {
  if (!child?.pid || child.exitCode !== null) return;
  if (process.platform === 'win32') {
    spawnSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore', timeout: 5000 });
  } else {
    try { process.kill(-child.pid, 'SIGKILL'); } catch { try { child.kill('SIGKILL'); } catch {} }
  }
}

function launchEngine(config, args, onEvent) {
  const command = engineCommand(config);
  const debug = config.debugLog;
  const env = { ...process.env, PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8', OMP_NUM_THREADS: '2' };
  if (debug?.directory) {
    env.INVOICE_DEBUG_DIR = debug.directory;
    env.PYTHONFAULTHANDLER = '1';
  }
  if (config.packaged) env.PLAYWRIGHT_BROWSERS_PATH = path.join(config.resourcesPath, 'engine', 'browsers');
  debug?.write('engine-spawn-begin', { executable: command.executable, command: args[0], packaged: config.packaged });
  // Embedded Python ignores PYTHON* environment flags; pass -X before the script as well.
  const prefix = debug?.directory && command.prefix.length ? ['-X', 'faulthandler', ...command.prefix] : command.prefix;
  const child = spawn(command.executable, [...prefix, ...args], {
    env, cwd: config.packaged ? path.dirname(command.executable) : config.root,
    stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true, detached: process.platform !== 'win32',
  });
  let lastActivity = Date.now();
  let stderr = '';
  let settled = false;
  const lines = readline.createInterface({ input: child.stdout });
  lines.on('line', line => {
    lastActivity = Date.now();
    try {
      const event = JSON.parse(line);
      if (event && typeof event.type === 'string') {
        // Only lifecycle/failure fields; protocol events also contain invoice data and workbook names.
        if (['stage', 'error', 'retry', 'attempt-failed', 'ready', 'finished'].includes(event.type)) {
          debug?.write('engine-event', { type: event.type, message: event.message, reason: event.reason, diagnostic: event.diagnostic });
        }
        onEvent(event);
      }
    } catch { /* Libraries may log non-protocol output; never expose it to the UI. */ }
  });
  child.stderr.on('data', chunk => {
    debug?.write('engine-stderr', { child_pid: child.pid, detail: chunk.toString('utf8') });
    stderr = redact(stderr + chunk.toString('utf8'));
    onEvent({ type: 'engine-stderr', detail: stderr });
  });
  child.stdin.on('error', () => {});
  const watchdog = setInterval(() => {
    if (Date.now() - lastActivity > 95000) {
      debug?.write('engine-watchdog-timeout', { child_pid: child.pid, idle_ms: Date.now() - lastActivity });
      onEvent({ type: 'error', message: '查验引擎长时间未响应，进度已保存，可点击继续查验。' });
      terminateTree(child);
    }
  }, 5000);
  const completion = new Promise((resolve, reject) => {
    child.on('spawn', () => debug?.write('engine-spawned', { child_pid: child.pid }));
    child.on('error', error => {
      debug?.write('engine-spawn-error', { error_type: error.name, code: error.code, message: error.message });
      if (!settled) { settled = true; clearInterval(watchdog); reject(new Error(`无法启动查验引擎：${error.message}`)); }
    });
    child.on('close', (code, signal) => {
      debug?.write('engine-closed', { child_pid: child.pid, code, signal, exit_status: code === null ? null : `0x${(code >>> 0).toString(16).padStart(8, '0')}` });
      clearInterval(watchdog);
      lines.close();
      if (!settled) { settled = true; resolve({ code, signal, diagnostic: stderr }); }
    });
  });
  return {
    child, completion,
    send(command) { if (child.stdin.writable) child.stdin.write(`${JSON.stringify({ command })}\n`); },
    kill() { debug?.write('engine-terminate', { child_pid: child.pid }); terminateTree(child); },
  };
}

module.exports = { engineCommand, launchEngine, terminateTree };
