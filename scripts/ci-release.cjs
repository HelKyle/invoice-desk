const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

function windowsAssets(directory) {
  const assets = fs.readdirSync(directory).filter(name => /^InvoiceDesk-.*-win-x64\.exe$/.test(name)).sort();
  if (assets.length !== 1) throw new Error('Windows 安装包必须构建、验收成功后才发布');
  return assets;
}

function releaseNotes(repository, commit, run, runId) {
  return `main 自动构建 #${run}\n\n提交：${commit}\n\n- 截图前将发票详情及其承载层提升至最高层，避免被税务平台提示遮挡。\n- 支持可选发票代码，代码和号码不限长度；旧票填写不含税金额，金额不做换算。\n- 兼容原三列 Excel，图片按发票号码命名。\n- 发票明细表逐张显示状态、尝试次数、最后原因，可筛选失败及未完成发票。\n- 自动重试最多 2 次，首次查验加重试最多 3 次尝试。\n- Windows 查验 worker 使用直接启动 Python 和 JSON 管道，初始化失败不占用发票预算。\n- Windows 10/11 x64：下载 .exe 安装。\n- Python、OCR 模型、浏览器及 Windows C++ 运行库已内置。\n- 发布前已在 Windows 上执行引擎测试、真实 Electron 管道启动检查、运行库自检和打包版界面测试。\n- Windows 安装包未签名；SHA256SUMS 用于核对下载文件。\n\n[构建记录](https://github.com/${repository}/actions/runs/${runId})\n`;
}

async function main() {
  const repository = process.env.GITHUB_REPOSITORY;
  const commit = process.env.GITHUB_SHA;
  const run = process.env.GITHUB_RUN_NUMBER;
  const runId = process.env.GITHUB_RUN_ID;
  if (!/^[\w.-]+\/[\w.-]+$/.test(repository || '') || !/^[a-f0-9]{40}$/.test(commit || '') || !/^\d+$/.test(run || '') || !/^\d+$/.test(runId || '')) {
    throw new Error('Release 必须在 GitHub Actions 中运行');
  }
  const version = require('../package.json').version;
  const tag = `v${version}-main.${run}-${commit.slice(0, 7)}`;
  const directory = path.resolve('release-assets');
  const assets = windowsAssets(directory);
  const sums = [];
  for (const name of assets) {
    const hash = crypto.createHash('sha256');
    for await (const chunk of fs.createReadStream(path.join(directory, name))) hash.update(chunk);
    sums.push(`${hash.digest('hex')}  ${name}`);
  }
  fs.writeFileSync(path.join(directory, 'SHA256SUMS'), sums.join('\n') + '\n');
  const notes = path.resolve('release-notes.md');
  fs.writeFileSync(notes, releaseNotes(repository, commit, run, runId));
  function gh(args, optional = false) {
    const result = spawnSync('gh', [...args, '--repo', repository], { encoding: 'utf8', maxBuffer: 1024 * 1024 });
    if (result.status !== 0 && !optional) throw new Error(result.stderr || 'GitHub release 操作失败');
    return result;
  }
  const existing = gh(['release', 'view', tag, '--json', 'url'], true);
  if (existing.status !== 0) {
    gh(['release', 'create', tag, '--target', commit, '--draft', '--title', `Invoice Desk ${version} · main #${run}`, '--notes-file', notes]);
  }
  gh(['release', 'upload', tag, ...assets.map(name => path.join(directory, name)), path.join(directory, 'SHA256SUMS'), '--clobber']);
  const branch = spawnSync('gh', ['api', `repos/${repository}/git/ref/heads/main`, '--jq', '.object.sha'], { encoding: 'utf8' });
  if (branch.status !== 0) throw new Error('无法检查 main 最新提交，Release 暂保留为草稿');
  gh(['release', 'edit', tag, '--draft=false', `--latest=${branch.stdout.trim() === commit}`, '--notes-file', notes]);
  const published = gh(['release', 'view', tag, '--json', 'url']);
  const url = JSON.parse(published.stdout).url;
  console.log(url);
  if (process.env.GITHUB_STEP_SUMMARY) fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY, `[下载本次 Release](${url})\n`);
}
if (require.main === module) main().catch(error => { console.error(error.message); process.exitCode = 1; });
module.exports = { releaseNotes, windowsAssets };
