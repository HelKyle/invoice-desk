const { _electron: electron } = require('playwright');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const { report } = require('../desktop/diagnostics.cjs');

async function waitForState(page, predicate, timeout = 30000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const state = await page.evaluate(() => window.invoiceDesk.getState());
    if (predicate(state)) return state;
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  throw new Error('Timed out waiting for the actual application state');
}

(async () => {
  const root = path.join(__dirname, '..');
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'invoice-desk-ui-'));
  const workbook = path.join(directory, '发票列表.xlsx');
  const python = process.env.INVOICE_PYTHON || path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  const setup = spawnSync(python, ['-c', 'from openpyxl import Workbook; import sys; w=Workbook(); s=w.active; s.append(["报销列表"]); s.append(["发票号", "日期", "金额（元）", "发票代码"]); s.append(["00000000000000000001", "2026-01-23", 1234.56, "001"]); w.save(sys.argv[1])', workbook]);
  assert.equal(setup.status, 0, setup.stderr.toString());
  await fs.mkdir(path.join(root, 'test-artifacts'), { recursive: true });
  const launchOptions = { args: process.env.INVOICE_APP_EXE ? [] : [root], env: { ...process.env, INVOICE_APP_DATA: directory, INVOICE_DEBUG: '1' }, timeout: 30000 };
  if (process.env.INVOICE_APP_EXE) launchOptions.executablePath = process.env.INVOICE_APP_EXE;
  const app = await electron.launch(launchOptions);
  const errors = [];
  let page;
  try {
    page = await app.firstWindow();
    page.on('pageerror', error => errors.push(error.message));
    await page.waitForSelector('#start-button:disabled');
    await page.screenshot({ path: path.join(root, 'test-artifacts', 'welcome.png') });
    await app.evaluate(({ dialog }, filename) => { dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [filename] }); }, workbook);
    await page.locator('#import-button').click();
    await page.waitForFunction(() => document.querySelector('#total').textContent === '1' && !document.querySelector('#start-button').disabled);
    assert.match(await page.locator('#file-title').innerText(), /发票列表.xlsx/);
    assert.equal(await page.locator('#invoice-table-body tr').count(), 1);
    assert.match(await page.locator('#invoice-table-body').innerText(), /00000000000000000001.*待查验/s);
    await page.screenshot({ path: path.join(root, 'test-artifacts', 'imported.png') });
    const imported = await page.evaluate(() => window.invoiceDesk.getState());
    assert.equal(imported.import.total, 1);
    assert.equal(imported.import.issues.length, 0);
    assert.equal(imported.import.invoices[0].invoice_code, '001');
    assert.match(await page.locator('#mapping-description').innerText(), /发票代码/);
    assert.equal(await page.locator('#invoice-table-body tr td').nth(2).innerText(), '001');
    if (launchOptions.env.INVOICE_DEBUG === '1') {
      assert.equal(imported.debug, true);
      assert.equal(await page.locator('#open-diagnostics').isVisible(), true);
      await app.evaluate(({ shell }) => { shell.openPath = async folder => { global.debugOpened = folder; return ''; }; });
      await page.locator('#open-diagnostics').click();
      const opened = await app.evaluate(() => global.debugOpened);
      assert.equal(path.dirname(opened), path.join(directory, 'debug-logs'));
    }
    await page.evaluate(() => { localStorage.setItem('test', 'no privileged renderer'); });
    assert.equal(await page.evaluate(() => typeof window.require), 'undefined');
    const navigation = page.url();
    await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].webContents.send('workspace:changed', {
      phase: 'running', busy: true, import: { filename: '发票列表.xlsx', total: 10, duplicates: 0, issues: [], warnings: [], mappings: [] },
      progress: { total: 10, completed: 4, success: 3, rejected: 1, failed: 0 }, task: { output: '/final-invoices' },
      current: { number: '00000000000000000001', retry: 1 }, retryLimit: 2, waiting: 30, message: '页面响应超时，正在自动恢复', failures: [],
    }));
    await page.waitForFunction(() => document.querySelector('#percentage').textContent === '40%');
    assert.match(await page.locator('#retry-badge').innerText(), /1\/2/);
    assert.equal(await page.locator('#import-button').isDisabled(), true);
    assert.equal(await page.locator('#pause-button').isVisible(), true);
    await page.screenshot({ path: path.join(root, 'test-artifacts', 'progress.png') });
    assert.equal(page.url(), navigation);
    assert.deepEqual(errors, []);
    await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].webContents.send('workspace:changed', {
      phase: 'finished', busy: false, retryLimit: 2,
      import: { filename: '发票列表.xlsx', fingerprint: 'preview', total: 2, duplicates: 0, issues: [], warnings: [], mappings: [], invoices: [
        { id: 'a', invoice_number: '00000000000000000001', sheet: 'Sheet1', excel_row: 3 },
        { id: 'b', invoice_number: '00000000000000000002', sheet: 'Sheet1', excel_row: 4 },
      ] },
      invoiceStates: { a: { kind: 'failed', attempts: 3, reason: '网络超时' }, b: { kind: 'success', attempts: 1, reason: '已保存发票详情' } },
      progress: { total: 2, completed: 2, success: 1, rejected: 0, failed: 1 }, task: { output: '/final-invoices' }, failures: [],
    }));
    await page.waitForFunction(() => document.querySelector('#invoice-table-body tr[data-status="failed"]'));
    assert.match(await page.locator('#invoice-table-body').innerText(), /00000000000000000001.*重试后失败.*3.*网络超时/s);
    await page.locator('#invoice-filter').selectOption('failed');
    assert.equal(await page.locator('#invoice-table-body tr').count(), 1);
    await page.screenshot({ path: path.join(root, 'test-artifacts', 'failed-invoice-table.png'), fullPage: true });
    await page.locator('#invoice-filter').selectOption('all');
    // Pause before verification: no attempt/ledger. Full worker readiness is checked by startup-smoke.cjs.
    await app.evaluate(({ dialog }, folder) => { dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [folder] }); }, directory);
    const outputChoice = await page.evaluate(() => window.invoiceDesk.chooseOutput());
    assert.equal(outputChoice.error, undefined);
    await page.locator('#start-button').click();
    await waitForState(page, current => ['running', 'stopped'].includes(current.phase));
    await page.evaluate(() => window.invoiceDesk.pause());
    const initialization = await waitForState(page, current => ['paused', 'stopped'].includes(current.phase));
    assert.equal(initialization.diagnostic, null, 'Pausing before verification must not introduce an engine error');
    assert.equal(initialization.phase, 'paused');
    assert.equal(initialization.current, null);
    assert.equal(Object.values(initialization.invoiceStates)[0].attempts, 0);
    await app.close();
    const reopened = await electron.launch(launchOptions);
    try {
      const restored = await reopened.firstWindow();
      await restored.waitForFunction(() => document.querySelector('#total').textContent === '1');
      assert.match(await restored.locator('#file-title').innerText(), /发票列表.xlsx/);
      const recovery = await restored.evaluate(() => window.invoiceDesk.getState());
      assert.equal(recovery.phase, 'stopped');
      assert.equal(recovery.progress.completed, 0);
      assert.equal(await restored.locator('#start-button').isEnabled(), true);
      assert.match(await restored.locator('#invoice-table-body').innerText(), /00000000000000000001.*待查验/s);
      const workspace = JSON.parse(await fs.readFile(path.join(directory, 'workspace.json'), 'utf8'));
      await assert.rejects(fs.readFile(workspace.task.ledger, 'utf8'), { code: 'ENOENT' });
      await restored.locator('#copy-diagnostics').click();
      const diagnostic = await reopened.evaluate(({ clipboard }) => clipboard.readText());
      assert.match(diagnostic, /app_version/);
      assert.doesNotMatch(diagnostic, /00000000000000000001|1234\.56|发票列表.xlsx/);
    } finally { await reopened.close(); }
    // Restore a completed failed record from the actual ledger, rather than relying on a transient renderer event.
    const archived = JSON.parse(await fs.readFile(path.join(directory, 'workspace.json'), 'utf8'));
    const invoiceId = archived.import.invoices[0].id;
    await fs.mkdir(path.dirname(archived.task.ledger), { recursive: true });
    await fs.writeFile(archived.task.ledger, JSON.stringify({ fingerprint: archived.task.fingerprint, attempt_accounting: 'verification-v1',
      items: { [invoiceId]: { kind: 'failed', attempts: 3, reason: '网络超时' } } }));
    archived.phase = 'finished'; archived.progress = { total: 1, completed: 1, success: 0, rejected: 0, failed: 1 };
    await fs.writeFile(path.join(directory, 'workspace.json'), JSON.stringify(archived));
    const completed = await electron.launch(launchOptions);
    try {
      const completedPage = await completed.firstWindow();
      await completedPage.waitForSelector('#invoice-table-body tr[data-status="failed"]');
      assert.match(await completedPage.locator('#invoice-table-body').innerText(), /00000000000000000001.*重试后失败.*3.*网络超时/s);
      assert.equal(await completedPage.locator('#start-button').isEnabled(), true);
    } finally { await completed.close(); }
    if (launchOptions.env.INVOICE_DEBUG === '1') {
      let logs = '';
      const debugDirectory = path.join(directory, 'debug-logs');
      for (const session of await fs.readdir(debugDirectory)) {
        for (const file of await fs.readdir(path.join(debugDirectory, session))) {
          logs += await fs.readFile(path.join(debugDirectory, session, file), 'utf8');
        }
      }
      assert.match(logs, /engine-spawned/);
      assert.doesNotMatch(logs, /00000000000000000001|1234\.56|发票列表\.xlsx/);
      console.log('Debug 日志按钮、引擎进程追踪和发票数据隔离检查通过。');
    }
    console.log('Electron 界面、实际 Excel 导入、进度展示、隔离与重启恢复检查通过。');
  } catch (error) {
    if (page && !page.isClosed()) {
      try {
        const failedState = await page.evaluate(() => window.invoiceDesk.getState());
        console.error(report(failedState, require('../package.json').version));
      } catch {}
    }
    throw error;
  } finally {
    await app.close().catch(() => {});
    await fs.cp(path.join(directory, 'debug-logs'), path.join(root, 'test-artifacts', 'ui-debug-logs'), { recursive: true }).catch(() => {});
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
