# 发票查验 App

本地 Electron 桌面应用。导入一个 Excel，自动识别发票列表，逐张提交至税务查验平台，保存平台返回的发票详情图片。OCR 在本机运行。安装包包含 Python、OCR 模型、浏览器和 Windows C++ 运行库，使用者无需另外安装开发环境。

## 使用

Windows 10/11 x64：运行 `release/InvoiceDesk-1.0.6-win-x64.exe`，安装后从桌面或开始菜单打开“发票查验”。

macOS 14 及以上、Apple Silicon：解压 `release/InvoiceDesk-1.0.6-mac-arm64.zip`，打开“发票查验.app”。应用使用 ad-hoc 签名保证包内文件完整性，尚无 Developer ID 签名和 Apple 公证；macOS 首次拦截时，可在“系统设置 → 隐私与安全性”允许打开。1.0.5 修复旧包跳过签名导致的“已损坏”提示，并在 CI 验证 ZIP 解压后的完整签名；1.0.6 在保存截图前直接将发票详情提升至最高层，避免被税务平台的提示遮挡。

1. 选择或拖入 Excel（`.xlsx`、`.xlsm`、`.xls`）。
2. App 显示导入数量和逐张发票明细表；每张显示号码、代码、Excel 位置、状态、尝试次数及最近原因。无效行显示“已跳过”及原因，可按状态筛选、翻页。
3. 点击“开始查验”。可以暂停、继续、停止；关闭后再次打开可继续。
4. 完成后在明细表查看已保存、平台返回结果和重试后失败的发票；100% 表示全部处理过，失败的发票仍留在表中。可点击“再次查验未完成部分”开启新一轮，已保存的发票跳过。
5. 点击“打开发票文件夹”。每张成功发票对应一个 PNG：以发票号码命名；含特殊字符或过长的标识使用稳定摘要命名。

默认保存到用户的“文档 / 发票查验”目录，每次新任务有独立文件夹；开始前可以更改保存位置。输出目录只保留最终发票详情 PNG，没有验证码图片、失败截图或 CSV 状态表。续跑元数据仅保存在 App 的用户数据目录中。

## Excel 自动识别

支持数电发票和需要发票代码的旧版发票。发票代码可空，代码和号码均不限制长度，文本按录入值传入查验页面。支持多个工作表、前置标题和空行、表头换行及常见单位标注、部分双行表头。

- 号码：发票号码、发票号、数电发票号码、电子发票号码、Invoice No. / Invoice Number 等。
- 代码（可选）：发票代码、票据代码、Invoice Code；兼容原有三列文件，按表头识别，列顺序不限。
- 日期：开票日期、开票时间、发票日期、日期、Invoice Date 等；支持 Excel 日期、1900/1904 日期系统序列号、`YYYYMMDD`、`2026-1-3`、`2026年1月3日` 等格式。
- 金额：优先价税合计、含税金额，其次发票金额、金额、Total Amount 等；支持货币符号、千分位和两位小数转换。也识别不含税金额、税前金额、Net Amount。始终复用所选金额列，不做税额换算；有代码的旧票请填不含税金额，无代码的数电票请填含税金额。若有多列金额，仍优先选择价税合计／含税金额，请在导入后核对识别结果。

表头在各工作表前100行内识别。说明工作表、合计行和重复表头会跳过；完全相同的发票记录合并。同一号码对应不同代码、日期或金额时跳过冲突记录并提示核对。

**建议发票号码和代码从原始数据以文本导出，以保留前导零和长号码精度。** 数字单元格按 Excel 已存储的数值传入，App 不补零，也无法恢复 Excel 已丢失的精度。

## 重试与超时

每张发票首次查验后最多自动重试 **2 次**，即最多 **3 次尝试**。验证码错误、页面加载和操作超时、JavaScript 异常未返回结果、网络错误、初始化完成后的浏览器或引擎崩溃、最终图片保存失败都会进入统一重试。

页面导航最多30秒，常规操作最多12秒，查验结果等待最多25秒；单次尝试还有150秒硬超时。OCR/浏览器进程卡死会结束整个子进程树，再重建引擎。引擎初次启动另有90秒超时。

1.0.2 起，查验 worker 通过直接启动 Python / 引擎可执行文件和 JSON 管道通信，
不再依赖 `multiprocessing spawn`。初始化完成后才计入发票尝试次数；初始化超时或失败
会停止任务、保留诊断，可点击继续，首次初始化失败不会创建发票账本。
旧账本中能根据诊断阶段明确识别的初始化失败预算会在恢复时重置，已成功发票保留。
stdin 在初始化期间关闭不会判为用户取消；进入查验后控制管道关闭会停止任务并保存已有进度。

普通异常两次重试分别等待2秒、4秒；平台频控分别等待30秒、60秒。发票之间间隔10秒，串行查验。

平台明确返回“不一致”或“查无此票”时，将其作为最终业务结果显示，不自动重复提交。异常耗尽重试后继续处理下一张。用户可以在整批结束后主动“再次查验未完成部分”，这会为未取得发票的部分开启新一轮预算；已保存发票跳过。

暂停允许当前尝试完成后停在下一次尝试前；停止则结束当前引擎并保留任务进度。重试预算先写入内部任务文件，再开始请求，避免重启导致无限重试。

## 本地开发和打包

### Windows 卡住或反复重试

1.0.1 版修复了子进程异常退出时重试原因可能为空的问题，标题现在显示实际阶段（子进程启动、OCR 运行库、模型1/2、模型2/2、浏览器驱动、查验页面等）。失败后点击底部“复制诊断信息”，可将退出码、错误类型、失败阶段和内存情况粘贴给开发者。报告不包含发票列表、金额或验证码。

正常运行不要求管理员身份。只有具体错误提示“拒绝访问”时才排查文件权限或安全软件策略；只有内存／虚拟内存错误或相关资源数据支持时才判断内存不足。此版改善错误定位，不代表已经验证并修复所有 Windows 环境中的启动故障。

需要 Node.js 24 和 Python 3.11/3.12。可以本地打包；main 每次 push 也会自动运行 GitHub Actions；涉及 App、引擎、依赖或构建发布脚本的改动须提升版本号，检查通过后才构建并发布 Release。提交前可运行 `node scripts/check-release-version.cjs --base origin/main`。手动触发 workflow 可重建同一版本。

```bash
npm ci
node node_modules/electron/install.js
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-desktop.txt
.venv/bin/python -m playwright install chromium --only-shell
npm start
```

Windows 开发时改用 `python -m venv .venv` 和 `.venv\Scripts\python.exe`。

```bash
npm test           # Excel、重试恢复及真实 Chromium 页面集成测试
npm run test:ui    # 真实 Electron 导入、进度展示、隔离及重启恢复
npm run test:startup # 真实 Electron 管道启动、ready/result 和初始化期间关闭 stdin
npm run dist       # 当前平台的完整 App
npm run dist:win   # 本地 Windows x64 安装包，可在 macOS 上构建
npm run dist:win:debug # 独立的 Windows x64 启动诊断 ZIP，可在 macOS 上构建
```

Debug 包输出到 `release/debug/InvoiceDesk-<版本>-win-x64-debug.zip`。
已有 `build/engine-win` 时可执行 `npm run dist:win:debug -- --reuse-engine`，
复用运行库和浏览器，同时更新 Python 源码并重新检查 Windows 原生依赖。
完整解压后运行 `InvoiceDesk-Debug.exe`，重新导入 Excel 开始查验；卡住后
等待至少 60 秒，点击“打开 debug 日志”，将当前会话日志文件夹打包。
日志位于 `%APPDATA%\发票查验 Debug\debug-logs`，每个会话独立保存，
包含 Electron 引擎事件、各 Python 进程的导入阶段和每 30 秒的线程调用栈。
不会记录完整协议事件或主动采集发票内容；原始 Python 调用栈可能包含安装路径。
普通包也可通过环境变量 `INVOICE_DEBUG=1` 启用同样的诊断模式。

包内 `check-startup-electron.cmd` 使用真实 Electron 父进程、`pipe` stdio、`windowsHide`
和内嵌 isolated Python，分别验证保持 stdin 和立即关闭 stdin。每个会话生成
`startup-probe.json`，应为 `ok: true`，父、子进程的 `isolated` 标记均为 true。
日志在 `%LOCALAPPDATA%\InvoiceDesk-Debug-Electron-Probe`。
`npm run test:startup` 可自动执行同样检查；设置 `INVOICE_APP_EXE` 后使用打包应用，
Windows 打包验收已包含此回归，必须确认父子进程确实使用内嵌 isolated Python。

包内 `check-startup.cmd` 可在 cmd 下单独运行直接 worker 初始化路径，无需 Excel，也不访问税务平台；
此方式不能替代真实 Electron 父进程的回归。
必须完整解压后从 App 根目录运行，不能直接在 ZIP 内双击脚本或单独移动脚本；
同一层必须有 `InvoiceDesk-Debug.exe` 和 `resources` 文件夹。缺少运行库时脚本会
显示预期路径并以退出码 2 结束，此时尚未启动 Python，不属于 OCR 初始化失败。
脚本显示日志位置（`%LOCALAPPDATA%\InvoiceDesk-Debug-Probe`），并返回检查退出码。
成功说明子进程、OCR 模型、Playwright 驱动和离线 Chromium 通过，不代表税务网络或完整查验已经通过。
Debug 版使用独立的用户数据目录；反馈时只需提供日志，不用提供 `workspace.json` 或 `tasks`。

`npm run dist` 创建独立的 `.venv-engine`，使用 PyInstaller 打包查验引擎并执行 OCR/浏览器自检。开发启动优先使用该独立环境。

`npm run dist:win` 使用 Python.org 的 Python 3.12.10 x64 嵌入包、Windows 专用 wheels 和对应 Playwright 浏览器。微软官方 C++ Redistributable 的 DLL 随 App 分发，无需管理员安装运行库；构建时检查 x64 架构与运行库完整性。在 Windows 上执行构建还会运行 Windows 引擎自检。打包文件不包含本项目现有 Excel、旧 CSV 或历史截图。

初次构建需要联网下载依赖，App 查验也需要访问税务平台。只有 OCR 可在本地离线执行。Windows 版本在当前 macOS 环境中完成构建及静态依赖检查，尚未在真实 Windows 系统验收。

## 代码

- `desktop/`：Electron 主进程、隔离的 preload 和界面。
- `engine/excel.py`：Excel 表头识别、格式解析、去重与校验。
- `engine/browser.py`：浏览器查验、内存 OCR、最终发票截图。
- `engine/service.py`：进程协议、超时监督、重试、暂停与续跑。
- `scripts/`：本地构建和验收脚本。

原 `invoice_batch.py` 命令行入口保留原行为；App 使用独立的任务与输出流程，复用日期选择、OCR、结果读取和发票详情截图逻辑。

## 本地验收记录

2026-11-10：新增真实 Chromium 层级回归测试；右下角首次使用提示保持原状，截图前直接将发票详情及其承载层提升至最高层，完整测试通过。

2026-10-13：19 项自动化测试通过，覆盖表头识别与金额选择、日期系统、精度错误、去重冲突、重试上限、断点续跑、进程卡死与崩溃、JavaScript 异常、频控、错误验证码、错误发票详情及完整截图。开发版和打包版 Electron 的 Excel 导入、进度界面与重启恢复检查均通过。

已使用打包的 macOS 查验引擎完成一张真实发票查验，成功保存一张最终详情 PNG；该输出目录没有 CSV、验证码或失败截图。Windows 包中81个本地二进制的 x64 架构与必需运行库检查通过；Windows 实机运行尚未验证。
