/* Actual Electron parent, production pipe/window flags, real OCR/browser and optional packaged embedded Python. */
const { _electron: electron } = require('playwright');
const fs = require('node:fs/promises');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const artifactRoot = path.join(__dirname, '..', 'test-artifacts');
  await fs.mkdir(artifactRoot, { recursive: true });
  const directory = await fs.mkdtemp(path.join(artifactRoot, 'startup-'));
  const options = {
    args: process.env.INVOICE_APP_EXE ? [] : [path.join(__dirname, '..')],
    env: { ...process.env, INVOICE_APP_DATA: directory }, timeout: 30000,
  };
  if (process.env.INVOICE_APP_EXE) options.executablePath = process.env.INVOICE_APP_EXE;
  const app = await electron.launch(options);
  try {
    await app.firstWindow();
    for (const closeStdin of [false, true]) {
      const result = await app.evaluate(async ({ app }, { directory, closeStdin }) => {
        const require = process.getBuiltinModule('node:module').createRequire(app.getAppPath() + '/package.json');
        const path = require('node:path');
        const { startupProbe } = require(path.join(app.getAppPath(), 'desktop', 'startup-probe.cjs'));
        const { createDebugLog } = require(path.join(app.getAppPath(), 'desktop', 'debug-log.cjs'));
        return startupProbe({
          root: app.getAppPath(), packaged: app.isPackaged, resourcesPath: process.resourcesPath,
          debugLog: createDebugLog(path.join(directory, closeStdin ? 'closed-stdin' : 'open-stdin')),
        }, closeStdin);
      }, { directory, closeStdin });
      console.log(JSON.stringify(result));
      await fs.writeFile(path.join(directory, closeStdin ? 'closed-stdin.json' : 'open-stdin.json'), JSON.stringify(result, null, 2));
      assert.equal(result.ok, true, `Electron startup failed; logs: ${directory}`);
    }
    console.log(`Electron 真实父进程的管道启动、ready/result、离线 OCR/Chromium 和关闭 stdin 回归通过。日志：${directory}`);
  } finally { await app.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
