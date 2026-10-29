"""Browser/OCR worker. All Playwright operations stay on this process's main thread."""
import os
import re
import time
from pathlib import Path

from invoice_batch import SITE, choose_date, recognize_captcha, refresh_captcha, read_result, save_result_screenshot
from engine.diagnostics import failure_report, human_reason
from engine.startup_debug import trace
from engine.invoice import image_name


class VerificationError(Exception):
    pass


class BrowserEngine:
    def __init__(self, notify, site=SITE, models=None, response_timeout=25):
        self.notify = notify
        self.stage = "正在加载 OCR 运行库"
        self.notify(self.stage)
        trace("ddddocr-import-begin")
        import ddddocr
        trace("ddddocr-import-end")
        trace("playwright-import-begin")
        from playwright.sync_api import sync_playwright
        trace("playwright-import-end")
        if models is not None:
            self.models = models
        else:
            self.update_stage("正在加载本地识别模型（1/2）")
            standard = ddddocr.DdddOcr(show_ad=False)
            self.update_stage("正在加载本地识别模型（2/2）")
            beta = ddddocr.DdddOcr(show_ad=False, beta=True)
            self.models = (standard, beta)
        self.site = site
        self.response_timeout = response_timeout
        self.update_stage("正在启动浏览器驱动")
        self.playwright = sync_playwright().start()
        self.browser = None
        self.context = None
        self.page = None
        self.invoice_id = None

    def update_stage(self, message):
        self.stage = message
        trace("browser-stage", message=message)
        self.notify(message)

    def prepare(self, row):
        if self.browser is None or not self.browser.is_connected():
            self.browser = self.playwright.chromium.launch(headless=True, timeout=30000)
            self.context = None
        if self.context is None or self.invoice_id != row["id"]:
            if self.context:
                try:
                    self.context.close()
                except Exception:
                    pass
            self.context = self.browser.new_context(locale="zh-CN")
            self.invoice_id = row["id"]
            self.page = self.context.new_page()
        for popup in list(self.context.pages):
            if popup != self.page:
                popup.close()
        if self.page.is_closed():
            self.page = self.context.new_page()
        self.page.set_default_timeout(12000)
        self.page.set_default_navigation_timeout(30000)
        return self.page

    def verify(self, row, output):
        page = None
        errors = []
        def record_error(error):
            errors.append(str(error)[:300])
        try:
            self.update_stage("正在启动查验浏览器")
            page = self.prepare(row)
            page.on("pageerror", record_error)
            self.update_stage("正在打开查验页面")
            page.goto(self.site, wait_until="domcontentloaded")
            inputs = page.locator("input.t-input__inner")
            inputs.first.wait_for(state="visible", timeout=20000)
            if inputs.count() != 5:
                raise VerificationError("查验页面结构异常，正在重新加载")
            self.update_stage("正在填写发票信息")
            inputs.nth(0).fill(row.get("invoice_code", ""))
            inputs.nth(1).fill(row["invoice_number"])
            choose_date(page, row["issue_date"])
            inputs.nth(3).fill(row["amount"])
            if any(inputs.nth(index).input_value() != value for index, value in
                   ((0, row.get("invoice_code", "")), (1, row["invoice_number"]), (3, row["amount"]))):
                raise VerificationError("页面未正确接收发票信息")
            self.update_stage("正在自动识别验证码")
            captcha = None
            for refresh in range(5):
                if inputs.nth(4).get_attribute("placeholder") == "请输入":
                    captcha = recognize_captcha(page, self.models)  # Memory only: never saves a captcha file.
                if captcha:
                    break
                if refresh < 4:
                    refresh_captcha(page)
            if not captcha:
                raise VerificationError("验证码暂时无法识别")
            inputs.nth(4).fill(captcha)
            page.get_by_role("button", name=re.compile(r"查\s*验")).click()
            self.update_stage("正在等待税务平台返回")
            deadline = time.monotonic() + self.response_timeout
            while time.monotonic() < deadline:
                status, note = read_result(page, row)
                if status == "访问过于频繁":
                    return {"kind": "retry", "reason": "税务平台访问频繁，稍后自动重试", "rate_limited": True}
                if status in ("不一致", "查无此票"):
                    return {"kind": "rejected", "reason": status}
                if status == "验证码错误":
                    return {"kind": "retry", "reason": "税务平台提示验证码错误"}
                if status in ("查验成功", "一致"):
                    # An explicit positive result alone is insufficient: save only an actual matching invoice dialog.
                    dialog = page.locator(".t-dialog:visible").filter(has_text="发票详情").filter(has_text=row["invoice_number"]).last
                    if dialog.count():
                        self.update_stage("正在保存最终发票")
                        destination = Path(output) / image_name(row)
                        temporary = destination.with_suffix(".partial.png")
                        try:
                            save_result_screenshot(page, temporary, "查验成功")
                            if not temporary.is_file() or temporary.stat().st_size < 100:
                                raise VerificationError("发票图片保存不完整")
                            os.replace(temporary, destination)
                        finally:
                            temporary.unlink(missing_ok=True)
                        return {"kind": "success", "file": destination.name, "reason": "已保存发票详情"}
                page.wait_for_timeout(500)
            if errors:
                raise VerificationError("查验页面 JavaScript 执行异常")
            raise VerificationError("税务平台响应超时，未返回完整发票详情")
        except Exception as exc:
            try:
                if "您的访问过于频繁" in page.locator("body").inner_text(timeout=1500):
                    return {"kind": "retry", "reason": "税务平台访问频繁，稍后自动重试", "rate_limited": True}
            except Exception:
                pass
            message = str(exc)
            if isinstance(exc, VerificationError):
                reason = message
            elif "Timeout" in type(exc).__name__ or "Timeout" in message:
                reason = "页面加载或操作超时"
            elif "closed" in message.lower() or "crash" in message.lower():
                reason = "查验浏览器意外关闭"
                self.context = None
            elif "net::" in message:
                reason = "网络连接失败"
            else:
                reason = "页面操作失败，正在自动恢复"
            return {"kind": "retry", "reason": human_reason(exc, reason), "diagnostic": failure_report(exc, self.stage)}
        finally:
            if page:
                page.remove_listener("pageerror", record_error)

    def close(self):
        try:
            if self.browser:
                self.browser.close()
        finally:
            self.playwright.stop()


def worker(connection):
    trace("worker-entered")
    engine = None
    last_stage = "正在启动查验子进程"
    def notify(message):
        nonlocal last_stage
        last_stage = message
        connection.send({"type": "stage", "message": message})
    try:
        engine = BrowserEngine(notify)
        trace("worker-ready")
        import sys
        connection.send({"type": "ready", "isolated": bool(sys.flags.isolated), "transport": "stdio"})
        while True:
            request = connection.recv()
            if request["command"] == "close":
                break
            if request["command"] == "probe":
                # Offline browser + OCR check also exercises the ready/request/result pipe.
                notify("正在执行离线浏览器自检")
                page = engine.prepare({"id": "startup-probe"})
                page.set_content("<title>invoice worker ready</title>")
                screenshot = page.screenshot()
                for model in engine.models:
                    model.classification(screenshot)
                connection.send({"type": "result", "result": {"browser": True, "models": 2}})
                continue
            result = engine.verify(request["invoice"], request["output"])
            connection.send({"type": "result", "result": result})
    except (EOFError, BrokenPipeError):
        pass
    except Exception as exc:
        trace("worker-error", error_type=type(exc).__name__, message=str(exc), stage=last_stage)
        try:
            connection.send({"type": "error", "message": human_reason(exc, f"{last_stage}失败，可复制诊断信息查看原因"), "diagnostic": failure_report(exc, last_stage)})
        except (EOFError, BrokenPipeError):
            pass
    finally:
        if engine:
            try:
                engine.close()
            except Exception:
                pass
        connection.close()
