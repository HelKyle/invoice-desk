"""Integration with real Chromium and a local tax-page fixture; no live platform submissions."""
import base64
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from engine.browser import BrowserEngine

NUMBER = "00000000000000000001"


class FixedOCR:
    def classification(self, data):
        if not data.startswith(b"\x89PNG"):
            raise ValueError("OCR did not receive rendered PNG bytes")
        return "AB12"


def fixture(outcome, number=NUMBER, expected=None):
    image = io.BytesIO()
    Image.new("RGB", (120, 50), "white").save(image, format="PNG")
    source = "data:image/png;base64," + base64.b64encode(image.getvalue()).decode()
    html = f'''<!doctype html><html><head><meta charset="UTF-8"></head><body>
      <input class="t-input__inner" value="stale-code"><input class="t-input__inner">
      <input class="t-input__inner" placeholder="YYYYMMDD"><input class="t-input__inner">
      <input class="t-input__inner" placeholder="请输入">
      <img width="120" height="50" src="{source}">
      <button id="submit">查 验</button><div id="result"></div>
      <script>document.getElementById('submit').onclick=()=>{{
      const expected={json.dumps(expected)};
      const inputs=document.querySelectorAll('input');
      if(expected && ![0,1,2,3].every(i=>inputs[i].value===expected[i])) {{
        document.getElementById('result').textContent='查验结果：不一致'; return;
      }}
      const outcome={outcome!r};
      if(outcome==='js') throw Error('simulated broken JavaScript');
      if(outcome==='timeout') return;
      const result=document.getElementById('result');
      if(outcome==='success') result.innerHTML='<div class="t-dialog" style="width:400px;height:240px;overflow:auto"><h2>发票详情</h2><p>{number}</p><p>当日查验次数：1</p><div style="height:600px">最终发票内容</div></div>';
      if(outcome==='rejected') result.textContent='查验结果：不一致';
      if(outcome==='rate') result.textContent='您的访问过于频繁';
      if(outcome==='captcha') result.textContent='验证码错误';
      if(outcome==='wrong') result.innerHTML='<div class="t-dialog">发票详情 当日查验次数 00000000000000000000</div>';
      }};</script></body></html>'''
    return html


class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = BrowserEngine(lambda message: None, site="https://invoice.test/", models=(FixedOCR(), FixedOCR()), response_timeout=0.7)

    @classmethod
    def tearDownClass(cls):
        cls.engine.close()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.output = Path(self.temporary.name)
        self.row = {"id": self.id(), "invoice_number": NUMBER, "invoice_code": "", "issue_date": "20260123", "amount": "1234.56"}

    def verify(self, outcome, expected=None):
        page = self.engine.prepare(self.row)
        page.route("https://invoice.test/**", lambda route: route.fulfill(body=fixture(outcome, self.row["invoice_number"], expected), content_type="text/html"))
        with patch("engine.browser.choose_date", lambda p, date: p.locator('input[placeholder="YYYYMMDD"]').fill(date)):
            return self.engine.verify(self.row, self.output)

    def test_final_dialog_saves_one_image_with_expanded_scroll_content(self):
        result = self.verify("success")
        self.assertEqual(result["kind"], "success")
        files = list(self.output.iterdir())
        self.assertEqual([p.name for p in files], [f"{NUMBER}.png"])
        with Image.open(files[0]) as image:
            self.assertGreater(image.height, 600)

    def test_old_invoice_code_and_amount_reach_real_page_unchanged(self):
        self.row.update(invoice_number="00123456", invoice_code="001", amount="100.00")
        result = self.verify("success", ["001", "00123456", "20260123", "100.00"])
        self.assertEqual(result["kind"], "success")
        self.assertEqual([p.name for p in self.output.iterdir()], ["00123456.png"])

    def test_blank_code_clears_page_default(self):
        result = self.verify("success", ["", NUMBER, "20260123", "1234.56"])
        self.assertEqual(result["kind"], "success")

    def test_arbitrary_identifiers_are_submitted_but_cannot_escape_output_directory(self):
        self.row.update(invoice_number="../" + "n" * 300, invoice_code="../../code")
        result = self.verify("success", ["../../code", "../" + "n" * 300, "20260123", "1234.56"])
        self.assertEqual(result["kind"], "success")
        files = list(self.output.iterdir())
        self.assertEqual(len(files), 1)
        self.assertRegex(files[0].name, r"^invoice-[0-9a-f]{64}\.png$")

    def test_javascript_error_and_timeout_are_retryable_and_do_not_save_images(self):
        for outcome, reason in (("js", "JavaScript"), ("timeout", "超时")):
            with self.subTest(outcome=outcome):
                self.row["id"] = outcome
                result = self.verify(outcome)
                self.assertEqual(result["kind"], "retry")
                self.assertIn(reason, result["reason"])
                self.assertEqual(list(self.output.iterdir()), [])

    def test_frequency_and_captcha_failures_are_distinguished(self):
        rate = self.verify("rate")
        self.assertTrue(rate["rate_limited"])
        self.row["id"] = "captcha"
        captcha = self.verify("captcha")
        self.assertIn("验证码错误", captcha["reason"])
        self.assertEqual(list(self.output.iterdir()), [])

    def test_wrong_invoice_dialog_is_never_saved(self):
        result = self.verify("wrong")
        self.assertEqual(result["kind"], "retry")
        self.assertEqual(list(self.output.iterdir()), [])

    def test_platform_rejection_is_final_and_keeps_output_clean(self):
        result = self.verify("rejected")
        self.assertEqual(result, {"kind": "rejected", "reason": "不一致"})
        self.assertEqual(list(self.output.iterdir()), [])
