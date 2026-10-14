/* Separate, portable debug bundle. --reuse-engine refreshes Python sources in an existing runtime. */
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { build, Platform, Arch } = require('electron-builder');
const root = path.join(__dirname, '..');
const destination = path.join(root, 'build', 'engine-win');

function run(executable, args) {
  const result = spawnSync(executable, args, { cwd: root, stdio: 'inherit' });
  if (result.status !== 0) throw new Error(result.error?.message || `Debug 构建失败：${result.status}`);
}

(async () => {
  if (process.argv.includes('--reuse-engine')) {
    for (const name of ['python.exe', 'python312.dll', 'python312._pth', 'browsers', 'Lib/site-packages']) {
      if (!fs.existsSync(path.join(destination, name))) throw new Error(`缺少已有 Windows 引擎 ${name}，请先执行 npm run engine:build:win`);
    }
    fs.mkdirSync(path.join(destination, 'app'), { recursive: true });
    for (const name of ['desktop_engine.py', 'invoice_batch.py']) fs.copyFileSync(path.join(root, name), path.join(destination, 'app', name));
    fs.cpSync(path.join(root, 'engine'), path.join(destination, 'app', 'engine'), { recursive: true, filter: source => !source.includes('__pycache__') });
    const suffix = process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python';
    const isolated = path.join(root, '.venv-engine', suffix);
    const python = process.env.INVOICE_PYTHON || (fs.existsSync(isolated) ? isolated : path.join(root, '.venv', suffix));
    run(python, ['scripts/check-windows.py', destination]);
  } else {
    run(process.execPath, ['scripts/build-windows.cjs']);
  }
  const base = require('../package.json').build;
  const artifacts = await build({
    publish: 'never',
    targets: Platform.WINDOWS.createTarget(['zip'], Arch.x64),
    config: {
      ...base,
      appId: 'local.invoice.desk.debug', productName: '发票查验 Debug',
      extraMetadata: { invoiceDebug: true },
      directories: { ...base.directories, output: 'release/debug' },
      artifactName: 'InvoiceDesk-${version}-win-x64-debug.${ext}',
      win: { ...base.win, executableName: 'InvoiceDesk-Debug', target: [{ target: 'zip', arch: ['x64'] }] },
      extraFiles: [{ from: 'scripts/debug-windows', to: '.' }],
      afterPack: async context => {
        await require('./verify-package.cjs')(context);
        for (const name of ['check-startup.cmd', 'check-startup-electron.cmd']) {
          const file = path.join(context.appOutDir, name);
          fs.writeFileSync(file, fs.readFileSync(file, 'utf8').replace(/\r?\n/g, '\r\n'), 'utf8');
        }
      },
    },
  });
  for (const artifact of artifacts) console.log(artifact);
})().catch(error => { console.error(error.message); process.exitCode = 1; });
