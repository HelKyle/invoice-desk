# Repository instructions

Tautological tests considered harmful.

修改 App、引擎、依赖、scripts 或 GitHub Actions 工作流时，在同次推送中提升 package.json 和 package-lock.json 的版本（通常运行 `npm version patch --no-git-tag-version`），并更新发布说明。纯文档或 tests 下的测试改动可保留版本。

提交前运行 `node scripts/check-release-version.cjs --base origin/main`；CI 会按整次 push 的提交范围再次检查。用户要求交付安装包时，须确认对应提交的 Windows 构建、打包验收及 Release 发布成功，再提供下载链接；仅推送源码不能宣称安装包已就绪。
