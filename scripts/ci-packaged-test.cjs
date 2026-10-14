const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const root = path.join(__dirname, '..');
const executable = process.platform === 'win32'
  ? path.join(root, 'release', 'win-unpacked', '发票查验.exe')
  : path.join(root, 'release', 'mac-arm64', '发票查验.app', 'Contents', 'MacOS', '发票查验');
if (!fs.existsSync(executable)) throw new Error('CI 安装包中找不到应用入口');
const startup = spawnSync(process.execPath, ['scripts/startup-smoke.cjs'], {
  cwd: root, stdio: 'inherit', env: { ...process.env, INVOICE_APP_EXE: executable },
});
if (startup.status !== 0) process.exit(startup.status ?? 1);
const result = spawnSync(process.execPath, ['scripts/ui-smoke.cjs'], {
  cwd: root, stdio: 'inherit', env: { ...process.env, INVOICE_APP_EXE: executable },
});
process.exit(result.status ?? 1);
