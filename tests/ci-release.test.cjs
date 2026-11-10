const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const releaseScript = path.resolve(__dirname, '../scripts/ci-release.cjs');
const commit = 'c37c202843e97bca3fd527e5c24c8ced908f2847';

test('publishes a Windows-only release with its checksum', t => {
  const cwd = fs.mkdtempSync(path.join(os.tmpdir(), 'invoice-release-'));
  t.after(() => fs.rmSync(cwd, { recursive: true, force: true }));
  const assets = path.join(cwd, 'release-assets');
  const bin = path.join(cwd, 'bin');
  fs.mkdirSync(assets);
  fs.mkdirSync(bin);
  const installer = 'InvoiceDesk-1.0.7-win-x64.exe';
  fs.writeFileSync(path.join(assets, installer), 'tested installer');
  const gh = path.join(bin, 'gh');
  fs.writeFileSync(gh, `#!/bin/sh\nif [ "$1" = "api" ]; then printf '%s\\n' '${commit}'; else printf '%s\\n' '{"url":"https://example.invalid/release"}'; fi\n`);
  fs.chmodSync(gh, 0o755);

  const result = spawnSync(process.execPath, [releaseScript], {
    cwd,
    encoding: 'utf8',
    env: {
      ...process.env,
      PATH: `${bin}${path.delimiter}${process.env.PATH}`,
      GITHUB_REPOSITORY: 'HelKyle/invoice-desk',
      GITHUB_SHA: commit,
      GITHUB_RUN_NUMBER: '12',
      GITHUB_RUN_ID: '123456',
    },
  });

  assert.equal(result.status, 0, result.stderr);
  assert.match(fs.readFileSync(path.join(assets, 'SHA256SUMS'), 'utf8'), new RegExp(`${installer}$`, 'm'));
  const notes = fs.readFileSync(path.join(cwd, 'release-notes.md'), 'utf8');
  assert.match(notes, /Windows 10\/11 x64/);
  assert.doesNotMatch(notes, /macOS/);
});
