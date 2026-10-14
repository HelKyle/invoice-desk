#!/usr/bin/env python3
"""从 Excel 读取发票信息，并逐张在国家税务总局查验页查验。"""

import argparse
import csv
import datetime as dt
import re
import time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

SITE = "https://inv-veri.chinatax.gov.cn/fpcygzfw/national-invoice-check"
FIELDS = ["file", "sheet", "excel_row", "invoice_code", "invoice_number", "issue_date", "amount", "amount_type", "status", "note"]


def read_excel(path):
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=True)
    rows = []
    try:
        for sheet in workbook:
            headers = None
            for excel_row, cells in enumerate(sheet.iter_rows(), start=1):
                values = [cell.value for cell in cells]
                if headers is None:
                    labels = {str(value).strip(): index for index, value in enumerate(values) if value is not None}
                    # Accept common concise column names used in exported invoice lists.
                    if "发票号码" not in labels and "发票号" in labels:
                        labels["发票号码"] = labels["发票号"]
                    if "开票日期" not in labels and "日期" in labels:
                        labels["开票日期"] = labels["日期"]
                    if "开票日期" not in labels and "发票日期" in labels:
                        labels["开票日期"] = labels["发票日期"]
                    if "金额" not in labels and "发票金额" in labels:
                        labels["金额"] = labels["发票金额"]
                    if "发票号码" not in labels:
                        continue
                    required = {"发票号码", "金额"}
                    if not required <= labels.keys() or not ({"开票时间", "开票日期"} & labels.keys()):
                        raise ValueError(f"{sheet.title} 第 {excel_row} 行缺少必要列")
                    headers = labels
                    continue
                number = str(values[headers["发票号码"]] or "").strip()
                if not number and all(value is None for value in values):
                    continue
                raw_date = values[headers.get("开票时间", headers.get("开票日期"))]
                raw_amount = values[headers["金额"]]
                raw_code = values[headers["发票代码"]] if "发票代码" in headers else None
                code = str(raw_code or "").strip()
                date = ""
                try:
                    if isinstance(raw_date, (dt.datetime, dt.date)):
                        date = raw_date.strftime("%Y%m%d")
                    elif isinstance(raw_date, (int, float)) and not isinstance(raw_date, bool):
                        # 未设置日期格式的 Excel 单元格存的是 1900 日期系统序列号。
                        date = (dt.date(1899, 12, 30) + dt.timedelta(days=int(raw_date))).strftime("%Y%m%d")
                    elif isinstance(raw_date, str):
                        parts = re.findall(r"\d+", raw_date)
                        if len(parts) == 3:
                            # 兼容非补零写法，如 2026-2-6。
                            date = dt.date(int(parts[0]), int(parts[1]), int(parts[2])).strftime("%Y%m%d")
                        else:
                            cleaned = re.sub(r"[年月./-]", "", raw_date.strip()).replace("日", "")
                            date = dt.datetime.strptime(cleaned, "%Y%m%d").strftime("%Y%m%d")
                except (ValueError, OverflowError):
                    pass
                amount = ""
                try:
                    decimal = Decimal(str(raw_amount).replace(",", "")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    amount = f"{decimal:.2f}"
                except (InvalidOperation, ValueError):
                    pass
                issues = []
                if not re.fullmatch(r"\d{20}", number):
                    issues.append("发票号码需为20位数字")
                if raw_code and not re.fullmatch(r"\d{10,12}", code):
                    issues.append("发票代码格式错误")
                if not date or dt.datetime.strptime(date, "%Y%m%d").date() > dt.date.today():
                    issues.append("开票日期需复核")
                if not amount or Decimal(amount) <= 0:
                    issues.append("金额需复核")
                rows.append(dict(file=str(path), sheet=sheet.title, excel_row=excel_row,
                                 invoice_code=code, invoice_number=number, issue_date=date,
                                 amount=amount, amount_type="价税合计",
                                 status="待复核" if issues else "待查验", note="；".join(issues)))
            if headers is None:
                raise ValueError(f"{sheet.title} 中未找到“发票号码”表头")
    finally:
        workbook.close()
    return rows


def choose_date(page, value):
    year, month, day = int(value[:4]), int(value[4:6]), int(value[6:])
    page.locator('input[placeholder="YYYYMMDD"]').click()
    panel = page.locator(".t-date-picker__panel")
    panel.locator(".t-date-picker__header-controller-year").click()
    page.locator(f'.t-select-option[title="{year}"]').click()
    panel.locator(".t-date-picker__header-controller-month").click()
    page.locator(".t-select-option").filter(has_text=re.compile(rf"^\s*{month}\s*月\s*$"))\
        .first.click(timeout=10000)
    panel.locator("td.t-date-picker__cell:not(.t-date-picker__cell--additional):not(.t-date-picker__cell--disabled)")\
        .filter(has=page.locator(".t-date-picker__cell-inner", has_text=re.compile(f"^{day}$"))).first.click()
    actual = page.locator('input[placeholder="YYYYMMDD"]').input_value()
    if actual != value:
        raise RuntimeError(f"日期未正确填入：{actual!r}，预期 {value}")


def read_result(page, row):
    texts = []
    for active_page in page.context.pages:
        for frame in active_page.frames:
            try:
                texts.append(frame.locator("body").inner_text(timeout=2000))
            except Exception:
                pass
    result = "\n".join(texts)
    if "您的访问过于频繁" in result:
        status = "访问过于频繁"
    elif "查无此票" in result:
        status = "查无此票"
    elif "不一致" in result:
        status = "不一致"
    elif re.search(r"(?:查验)?结果[：:\s]*一致", result):
        status = "一致"
    elif "发票详情" in result and "当日查验次数" in result and row["invoice_number"] in result.split("发票详情", 1)[1]:
        status = "查验成功"
    elif re.search(r"验证码.{0,12}(?:错误|不正确|有误|失效|过期)", result):
        status = "验证码错误"
    else:
        status = "结果待复核"
    if status == "查验成功":
        match = re.search(r"查验时间[：:\s]*([^\n]+)", result)
        note = f"税务平台显示发票详情；{match.group(0).strip() if match else '查验时间未读取'}"
        return status, note
    if status == "访问过于频繁":
        return status, "税务平台提示：您的访问过于频繁，请稍后再试"
    marker = result.rfind("查验结果")
    note = (result[marker:] if marker >= 0 else result[-1200:])[:1200].replace("\n", " ")
    return status, note


def refresh_captcha(page):
    image = page.locator('img[width="120"][height="50"]')
    old_source = image.get_attribute("src")
    image.click()
    page.wait_for_function('old => document.querySelector(\'img[width="120"][height="50"]\')?.getAttribute("src") !== old', arg=old_source, timeout=10000)


def recognize_captcha(page, models, image_path=None):
    image = page.locator('img[width="120"][height="50"]')
    image.wait_for(state="visible", timeout=15000)
    page.wait_for_function('() => document.querySelector(\'img[width="120"][height="50"]\')?.src.startsWith("data:image/")', timeout=15000)
    # 截取浏览器实际显示的图片，避免直接请求验证码 URL 导致会话不匹配。
    data = image.screenshot(**({"path": str(image_path)} if image_path is not None else {}))
    candidates = [re.sub(r"\s", "", model.classification(data)) for model in models]
    if candidates[0].casefold() == candidates[1].casefold() and re.fullmatch(r"[A-Za-z0-9]{4,6}", candidates[0]):
        return candidates[0]
    return None


def save_result_screenshot(page, path, status):
    if status == "查验成功":
        dialog = page.locator(".t-dialog:visible").filter(has_text="发票详情").last
        if dialog.count():
            # 弹窗内容独立滚动；先展开所有被限制高度的容器，再截取弹窗本身。
            dialog.evaluate("""root => {
                const nodes = [root, ...root.querySelectorAll('*')];
                for (const node of nodes.reverse()) {
                    const overflow = getComputedStyle(node).overflowY;
                    if (node.scrollHeight > node.clientHeight + 2 && /auto|scroll|hidden/.test(overflow)) {
                        node.style.maxHeight = 'none';
                        node.style.height = `${node.scrollHeight}px`;
                        node.style.overflowY = 'visible';
                    }
                }
                for (let parent = root.parentElement; parent && parent !== document.body; parent = parent.parentElement) {
                    parent.style.maxHeight = 'none';
                    parent.style.overflow = 'visible';
                }
                root.style.maxHeight = 'none';
                root.style.overflow = 'visible';
            }""")
            dialog.screenshot(path=str(path), animations="disabled", timeout=15000)
            return
    page.screenshot(path=str(path), full_page=True)


def submit_once(page, row, models, captcha_path):
    page.goto(SITE, wait_until="domcontentloaded")
    page.locator("input.t-input__inner").first.wait_for(timeout=20000)
    inputs = page.locator("input.t-input__inner")
    if row["invoice_code"]:
        inputs.nth(0).fill(row["invoice_code"])
    inputs.nth(1).fill(row["invoice_number"])
    choose_date(page, row["issue_date"])
    inputs.nth(3).fill(row["amount"])
    captcha_input = inputs.nth(4)
    captcha = None
    placeholder_mismatch = False
    for refresh in range(5):
        placeholder = captcha_input.get_attribute("placeholder")
        # 查验页验证码输入框实际的 placeholder 是“请输入”；旁边的说明文字不属于 placeholder。
        if placeholder != "请输入":
            placeholder_mismatch = True
            if refresh < 4:
                refresh_captcha(page)
            continue
        placeholder_mismatch = False
        captcha = recognize_captcha(page, models, captcha_path)
        if captcha:
            break
        if refresh < 4:
            refresh_captcha(page)
    if not captcha:
        if placeholder_mismatch:
            return "验证码识别失败", "验证码输入框 placeholder 不是“请输入”，刷新验证码后仍未恢复，未提交查验"
        return "验证码识别失败", "两个 OCR 模型未能一致识别验证码，未提交查验"
    captcha_input.fill(captcha)
    page.get_by_role("button", name=re.compile(r"查\s*验")).click()
    status, note = "结果待复核", "未在等待时间内读到明确结果"
    for _ in range(8):
        page.wait_for_timeout(1000)
        status, note = read_result(page, row)
        if status != "结果待复核":
            break
    return status, f"OCR验证码={captcha}；{note}"


def check_one(browser, row, models, results_dir, attempts=2, rate_retries=4):
    stem = f'{row["sheet"]}-r{row["excel_row"]}-{row["invoice_number"]}'
    screenshot = results_dir / f'{stem}.png'
    captcha_path = results_dir / f'{stem}-captcha.png'
    captcha_errors = 0
    rate_limits = 0
    context = browser.new_context(locale="zh-CN")
    try:
        # 同一张发票的重试复用当前页面；submit_once 会在该页面重新打开查验网址。
        page = context.new_page()
        while True:
            try:
                row["status"], row["note"] = submit_once(page, row, models, captcha_path)
            except Exception as exc:
                try:
                    body = page.locator("body").inner_text(timeout=2000)
                except Exception:
                    body = ""
                if "您的访问过于频繁" in body:
                    row["status"] = "访问过于频繁"
                    row["note"] = "税务平台提示：您的访问过于频繁，请稍后再试"
                else:
                    row["status"] = "查验失败"
                    row["note"] = str(exc)[:1200]
            try:
                save_result_screenshot(page, screenshot, row["status"])
                if row["status"] == "访问过于频繁":
                    page.screenshot(path=str(results_dir / f'{stem}-rate.png'), full_page=True)
            except Exception as exc:
                row["note"] += f"；截图失败：{exc}"
            print(f'{row["status"]}；截图：{screenshot}', flush=True)
            if row["status"] in ("验证码识别失败", "验证码错误") and captcha_errors < attempts - 1:
                captcha_errors += 1
                print(f"{row['status']}，在当前页面重新查验（{captcha_errors}/{attempts - 1}）", flush=True)
                continue
            if row["status"] == "访问过于频繁" and rate_limits < rate_retries:
                rate_limits += 1
                print(f"访问频控，等待 30 秒后在当前页面重试（{rate_limits}/{rate_retries}）", flush=True)
                time.sleep(30)
                continue
            break
    finally:
        context.close()


def check(rows, output, headed=False, attempts=2, rate_retries=4, limit=None, delay=10):
    try:
        from playwright.sync_api import sync_playwright
        import ddddocr
    except ImportError as exc:
        raise RuntimeError("查验模式需要 playwright 和 ddddocr；见 README.md") from exc
    models = (ddddocr.DdddOcr(show_ad=False), ddddocr.DdddOcr(show_ad=False, beta=True))
    results_dir = Path(output).with_suffix("")
    results_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not headed)
        try:
            processed = 0
            for row in rows:
                if row["status"] != "待查验":
                    continue
                if limit is not None and processed >= limit:
                    break
                processed += 1
                print(f'\n发票 {row["invoice_number"]}：{row["issue_date"]}，{row["amount_type"]} {row["amount"]}')
                try:
                    check_one(browser, row, models, results_dir, attempts=attempts,
                              rate_retries=rate_retries)
                except Exception as exc:
                    row["status"] = "查验失败"
                    row["note"] = str(exc)[:1200]
                    print(f'{row["status"]}：{row["note"]}')
                write_csv(rows, output)
                if row["status"] == "访问过于频繁":
                    print("平台持续限制访问，批量查验已停止；稍后重新运行可继续。", flush=True)
                    break
                time.sleep(delay)
        finally:
            browser.close()


def write_csv(rows, output):
    with open(output, "w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def resume(rows, output):
    if not Path(output).exists():
        return
    with open(output, newline="", encoding="utf-8-sig") as stream:
        previous = {
            (row["file"], row["sheet"], row["excel_row"], row["invoice_number"], row["issue_date"], row["amount"]): row
            for row in csv.DictReader(stream)
        }
    for row in rows:
        key = (row["file"], row["sheet"], str(row["excel_row"]), row["invoice_number"], row["issue_date"], row["amount"])
        old = previous.get(key)
        if old and old["status"] not in ("待查验", "待复核", "读取失败", "查验失败", "验证码识别失败", "验证码错误", "结果待复核", "访问过于频繁"):
            row["status"] = old["status"]
            row["note"] = old["note"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", default="查验发票.xlsx", help="Excel 文件（默认：查验发票.xlsx）")
    parser.add_argument("-o", "--output", default="checked-xlsx.csv", help="结果 CSV 文件")
    parser.add_argument("--check", action="store_true", help="自动识别验证码并逐张查验")
    parser.add_argument("--headed", action="store_true", help="查验时显示浏览器窗口")
    parser.add_argument("--attempts", type=int, choices=(1, 2, 3), default=3, help="每张票验证码错误时最多提交次数（默认 3）")
    parser.add_argument("--rate-retries", type=int, default=4, help="触发频控后最多重试次数（默认 4，每次等待 30 秒）")
    parser.add_argument("--limit", type=int, help="本次最多查验多少张，便于分批执行")
    parser.add_argument("--delay", type=int, default=10, help="每张票之间的等待秒数（默认 10 秒）")
    parser.add_argument("--force", action="store_true", help="重新查验已有结果的发票")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit 必须大于 0")
    if args.delay < 0:
        parser.error("--delay 不能小于 0")
    if args.rate_retries < 0:
        parser.error("--rate-retries 不能小于 0")
    source = Path(args.input)
    if not source.is_file() or source.suffix.lower() != ".xlsx":
        parser.error(f"未找到 Excel 文件：{source}")
    try:
        rows = read_excel(source)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    if not rows:
        parser.error("Excel 中没有发票数据")
    if args.check and not args.force:
        resume(rows, args.output)
    write_csv(rows, args.output)
    for row in rows:
        print(f'{row["sheet"]} 第 {row["excel_row"]} 行: {row["status"]}，号码={row["invoice_number"]}，日期={row["issue_date"]}，{row["amount_type"]}={row["amount"]}', flush=True)
    print(f"已写入 {args.output}", flush=True)
    if args.check:
        check(rows, args.output, headed=args.headed, attempts=args.attempts,
              rate_retries=args.rate_retries, limit=args.limit, delay=args.delay)


if __name__ == "__main__":
    main()
