const fs = require('node:fs');
const { execFileSync } = require('node:child_process');

function git(...args) {
  return execFileSync('git', args, { encoding: 'utf8' }).trim();
}

function versionParts(version) {
  if (!/^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(version || '')) {
    throw new Error(`发布版本必须为 major.minor.patch：${version}`);
  }
  return version.split('.').map(BigInt);
}

function main() {
  const current = JSON.parse(fs.readFileSync('package.json', 'utf8')).version;
  const lock = JSON.parse(fs.readFileSync('package-lock.json', 'utf8'));
  const parts = versionParts(current);
  if (lock.version !== current || lock.packages?.['']?.version !== current) {
    throw new Error('package.json 与 package-lock.json 版本不一致，请运行 npm version patch --no-git-tag-version');
  }
  let base;
  if (process.argv[2] === '--base' && process.argv.length === 4) {
    base = process.argv[3];
  } else if (process.argv.length > 2) {
    throw new Error('用法：node scripts/check-release-version.cjs --base <提交>');
  } else if (process.env.GITHUB_EVENT_NAME === 'push') {
    base = JSON.parse(fs.readFileSync(process.env.GITHUB_EVENT_PATH, 'utf8')).before;
    if (!/^[a-f0-9]{40}$/.test(base || '')) throw new Error('push 事件缺少有效的 before 提交');
  } else if (process.env.GITHUB_EVENT_NAME === 'workflow_dispatch') {
    console.log(`手动重建 ${current}：版本文件一致`);
    return;
  } else {
    throw new Error('本地检查请提供 --base origin/main（提交前运行）');
  }
  if (/^0{40}$/.test(base)) {
    console.log(`首次推送：${current}`);
    return;
  }
  // Resolve before appending a path; never let a caller-supplied ref become a git option.
  const commit = git('rev-parse', '--verify', '--end-of-options', `${base}^{commit}`);
  const previous = JSON.parse(git('show', `${commit}:package.json`)).version;
  const old = versionParts(previous);
  const changed = git('diff', '--name-only', '--no-renames', '-z', commit, '--').split('\0');
  const affectsApp = changed.some(file => /^(?:\.github\/workflows\/|desktop\/|engine\/|scripts\/|invoice_batch\.py$|desktop_engine\.py$|requirements[^/]*\.txt$|package(?:-lock)?\.json$)/.test(file));
  const firstDifference = parts.findIndex((value, index) => value !== old[index]);
  if (firstDifference >= 0 && parts[firstDifference] < old[firstDifference]) {
    throw new Error(`版本不能降低：${previous} → ${current}`);
  }
  if (affectsApp && firstDifference < 0) {
    throw new Error(`App 或构建逻辑已变更，但版本仍为 ${current}。请运行 npm version patch --no-git-tag-version，并一起提交两个版本文件。`);
  }
  console.log(affectsApp ? `发布版本检查通过：${previous} → ${current}` : `仅文档或测试等改动，保留 ${current}`);
}

try { main(); } catch (error) { console.error(error.message); process.exitCode = 1; }
