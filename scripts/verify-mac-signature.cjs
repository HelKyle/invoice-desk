const path = require('node:path');
const { execFileSync } = require('node:child_process');

function verify(app) {
  execFileSync('codesign', ['--verify', '--deep', '--strict', '--verbose=2', app], { stdio: 'inherit' });
}

module.exports = async context => {
  if (context.electronPlatformName === 'darwin') {
    verify(path.join(context.appOutDir, `${context.packager.appInfo.productFilename}.app`));
  }
};

if (require.main === module) {
  if (!process.argv[2]) throw new Error('请提供待验证的 .app 路径');
  verify(path.resolve(process.argv[2]));
}
