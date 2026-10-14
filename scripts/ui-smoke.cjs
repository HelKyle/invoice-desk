const { _electron: electron } = require('playwright');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const { report } = require('../desktop/diagnostics.cjs');

(async () => {
  const root = path.join(__dirname, '..');
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'invoice-desk-ui-'));
  const workbook = path.join(directory, '发票列表.xlsx');
  const python = process.env.INVOICE_PYTHON || path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  const setup = spawnSync(python, ['-c', 'from openpyxl import Workbook; import sys; w=Workbook(); s=w.active; s.append(["报销列表"]); s.append(["发票号", "日期", "价税合计（元）"]); s.append(["00000000000000000001", "2026-01-23", 1234.56]); w.save(sys.argv[1])', workbook]);
  assert.equal(setup.status, 0, setup.stderr.toString());
  const launchOptions = { args: process.env.INVOICE_APP_EXE ? [] : [root], env: { ...process.env, INVOICE_APP_DATA: directory }, timeout: 30000 };
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
    assert.equal(await page.locator('table').count(), 0);
    await page.screenshot({ path: path.join(root, 'test-artifacts', 'imported.png') });
    const imported = await page.evaluate(() => window.invoiceDesk.getState());
    assert.equal(imported.import.total, 1);
    assert.equal(imported.import.issues.length, 0);
    await page.evaluate(() => { localStorage.setItem('test', 'no privileged renderer'); });
    assert.equal(await page.evaluate(() => typeof window.require), 'undefined');
    const navigation = page.url();
    await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].webContents.send('workspace:changed', {
      phase: 'running', busy: true, import: { filename: '发票列表.xlsx', total: 10, duplicates: 0, issues: [], warnings: [], mappings: [] },
      progress: { total: 10, completed: 4, success: 3, rejected: 1, failed: 0 }, task: { output: '/final-invoices' },
      current: { number: '00000000000000000001', retry: 3 }, waiting: 30, message: '页面响应超时，正在自动恢复', failures: [],
    }));
    await page.waitForFunction(() => document.querySelector('#percentage').textContent === '40%');
    assert.match(await page.locator('#retry-badge').innerText(), /3\/10/);
    assert.equal(await page.locator('#import-button').isDisabled(), true);
    assert.equal(await page.locator('#pause-button').isVisible(), true);
    await page.screenshot({ path: path.join(root, 'test-artifacts', 'progress.png') });
    assert.equal(page.url(), navigation);
    assert.deepEqual(errors, []);
    // Exercise real cancellation during model initialization, before any tax-page submission.
    await app.evaluate(({ dialog }, folder) => { dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [folder] }); }, directory);
    const outputChoice = await page.evaluate(() => window.invoiceDesk.chooseOutput());
    assert.equal(outputChoice.error, undefined);
    await page.locator('#start-button').click();
    await page.waitForFunction(() => document.querySelector('#status-title').textContent.includes('正在加载本地识别模型'));
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
      const workspace = JSON.parse(await fs.readFile(path.join(directory, 'workspace.json'), 'utf8'));
      const ledger = JSON.parse(await fs.readFile(workspace.task.ledger, 'utf8'));
      assert.equal(Object.values(ledger.items)[0].attempts, 1);
      await restored.locator('#copy-diagnostics').click();
      const diagnostic = await reopened.evaluate(({ clipboard }) => clipboard.readText());
      assert.match(diagnostic, /app_version/);
      assert.doesNotMatch(diagnostic, /00000000000000000001|1234\.56|发票列表.xlsx/);
    } finally { await reopened.close(); }
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
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
