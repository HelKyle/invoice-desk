const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync, spawnSync } = require('node:child_process');
const checker = path.resolve(__dirname, '../scripts/check-release-version.cjs');

function repository(t) {
  const cwd = fs.mkdtempSync(path.join(os.tmpdir(), 'invoice-version-'));
  t.after(() => fs.rmSync(cwd, { recursive: true, force: true }));
  const git = (...args) => execFileSync('git', args, { cwd, encoding: 'utf8' }).trim();
  const write = (file, contents) => {
    fs.mkdirSync(path.dirname(path.join(cwd, file)), { recursive: true });
    fs.writeFileSync(path.join(cwd, file), contents);
  };
  const versions = version => {
    write('package.json', JSON.stringify({ version }));
    write('package-lock.json', JSON.stringify({ version, packages: { '': { version } } }));
  };
  const commit = () => {
    git('add', '.');
    git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', '-c', 'commit.gpgsign=false', 'commit', '-qm', 'fixture');
    return git('rev-parse', 'HEAD');
  };
  git('init', '-q');
  versions('1.0.3');
  write('engine/excel.py', '# original');
  const base = commit();
  const check = (args = ['--base', base], env = {}) => spawnSync(process.execPath, [checker, ...args], {
    cwd, encoding: 'utf8', env: { ...process.env, ...env },
  });
  return { write, versions, commit, check, base };
}

test('app changes without a version bump fail before release', t => {
  const repo = repository(t);
  repo.write('engine/excel.py', '# added legacy invoices');
  repo.commit();
  const result = repo.check();
  assert.equal(result.status, 1);
  assert.match(result.stderr, /版本仍为 1\.0\.3/);
});

test('a whole push may contain a feature commit followed by its version commit', t => {
  const repo = repository(t);
  repo.write('engine/excel.py', '# added legacy invoices');
  repo.commit();
  repo.versions('1.0.4');
  repo.commit();
  repo.write('event.json', JSON.stringify({ before: repo.base }));
  const result = repo.check([], { GITHUB_EVENT_NAME: 'push', GITHUB_EVENT_PATH: 'event.json' });
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /1\.0\.3 → 1\.0\.4/);
});

test('documentation and tests may be pushed without changing the version', t => {
  const repo = repository(t);
  repo.write('README.md', '# updated usage');
  repo.write('tests/test_excel.py', '# regression test');
  repo.commit();
  assert.equal(repo.check().status, 0);
});

test('a dependency lockfile mismatch blocks publication', t => {
  const repo = repository(t);
  repo.write('package.json', JSON.stringify({ version: '1.0.4' }));
  const result = repo.check();
  assert.equal(result.status, 1);
  assert.match(result.stderr, /版本不一致/);
});

test('version rollback fails while a minor increment succeeds', t => {
  const repo = repository(t);
  repo.versions('1.0.2');
  assert.equal(repo.check().status, 1);
  repo.versions('1.1.0');
  assert.equal(repo.check().status, 0);
});

test('manual rebuilds accept the existing version', t => {
  const repo = repository(t);
  const result = repo.check([], { GITHUB_EVENT_NAME: 'workflow_dispatch' });
  assert.equal(result.status, 0, result.stderr);
});
