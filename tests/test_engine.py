import contextlib
import datetime as dt
import io
import json
import os
import queue
import tempfile
import time
import unittest
from pathlib import Path

from openpyxl import Workbook
from openpyxl.utils.datetime import CALENDAR_MAC_1904, to_excel
from engine.excel import read_workbook
from engine.service import Controls, WorkerClient, run_batch

NUMBER = "00000000000000000001"
NUMBER2 = "00000000000000000002"


def hanging_worker(connection):
    connection.send({"type": "ready"})
    connection.recv()
    time.sleep(60)


def crashing_worker(connection):
    connection.send({"type": "ready"})
    connection.recv()
    os._exit(7)


class NonBlockingControls(Controls):
    def __init__(self):
        self.commands = queue.Queue()
        self.paused = False
        self.cancelled = False
        self.last_heartbeat = time.monotonic()
        self.delays = []

    def wait(self, seconds):
        self.delays.append(seconds)
        return not self.cancelled


class ScriptedClient:
    def __init__(self, results, callback=None):
        self.results = iter(results)
        self.calls = []
        self.closed = False
        self.callback = callback

    def request(self, invoice, output):
        self.calls.append(invoice["id"])
        if self.callback:
            self.callback()
        result = next(self.results)
        if result["kind"] == "success":
            Path(output, f"{invoice['invoice_number']}.png").write_bytes(b"final-invoice" * 20)
        return result

    def close(self):
        self.closed = True


class ExcelTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def workbook(self, rows, epoch=None, extra_sheet=False):
        workbook = Workbook()
        if epoch:
            workbook.epoch = epoch
        for row in rows:
            workbook.active.append(row)
        if extra_sheet:
            workbook.create_sheet("说明").append(["本表说明"])
        filename = self.directory / "invoice.xlsx"
        workbook.save(filename)
        return filename

    def test_title_blank_rows_aliases_and_gross_amount_priority(self):
        path = self.workbook([["报销明细"], [], [" 发票号\n", "开票日期", "金额", "价税合计（元）"],
                              [NUMBER, "2026年1月3日", 100, "￥1,234.56"], ["合计", None, 100, 1234.56]], extra_sheet=True)
        result = read_workbook(path)
        self.assertEqual(len(result["invoices"]), 1)
        self.assertEqual(result["invoices"][0]["amount"], "1234.56")
        self.assertEqual(result["invoices"][0]["issue_date"], "20260103")
        self.assertEqual(result["mappings"][0]["row"], 3)
        self.assertFalse(result["warnings"])

    def test_date_system_1904_and_yyyymmdd_number(self):
        path = self.workbook([["Invoice No.", "Invoice Date", "Total Amount"],
                              [NUMBER, to_excel(dt.datetime(2026, 2, 6), CALENDAR_MAC_1904), 100],
                              [NUMBER2, 20260123, 20]], epoch=CALENDAR_MAC_1904)
        self.assertEqual([i["issue_date"] for i in read_workbook(path)["invoices"]], ["20260206", "20260123"])

    def test_numeric_identifiers_are_rejected_instead_of_silently_rounded(self):
        path = self.workbook([["发票号码", "日期", "金额"], [int(NUMBER), "2026-01-23", 123]])
        result = read_workbook(path)
        self.assertEqual(result["total"], 0)
        self.assertIn("精度", result["issues"][0]["message"])

    def test_duplicates_merge_but_conflicting_invoice_is_excluded(self):
        path = self.workbook([["发票号码", "日期", "含税金额"],
                              [NUMBER, "2026-01-23", 123], [NUMBER, "2026-01-23", 123],
                              [NUMBER2, "2026-01-23", 123], [NUMBER2, "2026-01-23", 456]])
        result = read_workbook(path)
        self.assertEqual([i["invoice_number"] for i in result["invoices"]], [NUMBER])
        self.assertEqual(result["duplicates"], 1)
        self.assertEqual(len(result["issues"]), 2)

    def test_net_amount_only_is_not_treated_as_gross(self):
        path = self.workbook([["发票号码", "日期", "不含税金额"], [NUMBER, "2026-01-23", 123]])
        with self.assertRaisesRegex(ValueError, "表头"):
            read_workbook(path)

    def test_ambiguous_invoice_columns_require_correction(self):
        path = self.workbook([["发票号码", "发票号码", "日期", "金额"], [NUMBER, NUMBER2, "2026-01-23", 123]])
        with self.assertRaisesRegex(ValueError, "表头"):
            read_workbook(path)

    def test_fingerprint_survives_move_and_reordering(self):
        path = self.workbook([["发票号码", "日期", "金额"], [NUMBER, "2026-01-23", 123], [NUMBER2, "2026-01-24", 456]])
        first = read_workbook(path)
        moved = self.directory / "renamed.xlsx"
        path.rename(moved)
        second = read_workbook(moved)
        self.assertEqual(first["fingerprint"], second["fingerprint"])


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.output = self.directory / "final"
        self.ledger = self.directory / "private" / "state.json"
        self.parsed = {"fingerprint": "batch", "total": 1, "invoices": [{"id": "a", "invoice_number": NUMBER, "sheet": "Sheet1", "excel_row": 2}]}
        self.controls = NonBlockingControls()

    def run_batch(self, client, **kwargs):
        capture = io.StringIO()
        with contextlib.redirect_stdout(capture):
            run_batch(self.parsed, self.output, self.ledger, self.controls, client, delay=0, **kwargs)
        return [json.loads(line) for line in capture.getvalue().splitlines()]

    def test_timeout_then_javascript_error_then_success_only_keeps_final_invoice(self):
        results = [{"kind": "retry", "reason": "页面超时"}, {"kind": "retry", "reason": "JS异常"}, {"kind": "success"}]
        client = ScriptedClient(results)
        events = self.run_batch(client)
        self.assertEqual(len(client.calls), 3)
        self.assertEqual(events[-1]["success"], 1)
        self.assertEqual([p.name for p in self.output.iterdir()], [f"{NUMBER}.png"])
        self.assertTrue(client.closed)

    def test_retry_budget_is_ten_retries_after_first_attempt_and_persists(self):
        client = ScriptedClient([{"kind": "retry", "reason": "超时"}] * 11)
        events = self.run_batch(client)
        self.assertEqual(len(client.calls), 11)
        self.assertEqual(events[-1]["failed"], 1)
        resumed = ScriptedClient([])
        self.run_batch(resumed)
        self.assertEqual(resumed.calls, [])
        self.assertEqual(list(self.output.iterdir()), [])

    def test_cancel_preserves_budget_and_resume_skips_success(self):
        def cancel_after_request():
            self.controls.cancelled = True
        first = ScriptedClient([{"kind": "retry", "reason": "断网"}], cancel_after_request)
        self.run_batch(first)
        state = json.loads(self.ledger.read_text())
        self.assertEqual(state["items"]["a"]["attempts"], 1)
        self.assertNotEqual(state["items"]["a"]["kind"], "failed")
        self.controls.cancelled = False
        second = ScriptedClient([{"kind": "success"}])
        self.run_batch(second)
        third = ScriptedClient([])
        self.run_batch(third)
        self.assertEqual(third.calls, [])

    def test_frequency_limits_back_off_but_definitive_rejection_does_not_repeat(self):
        client = ScriptedClient([{"kind": "retry", "reason": "频控", "rate_limited": True},
                                 {"kind": "retry", "reason": "频控", "rate_limited": True}, {"kind": "rejected", "reason": "不一致"}])
        events = self.run_batch(client)
        self.assertIn(30, self.controls.delays)
        self.assertIn(60, self.controls.delays)
        self.assertEqual(events[-1]["rejected"], 1)
        self.assertEqual(len(client.calls), 3)

    def test_missing_final_image_is_recovered_and_explicit_retry_keeps_success(self):
        self.run_batch(ScriptedClient([{"kind": "success"}]))
        final = self.output / f"{NUMBER}.png"
        final.unlink()
        recovered = ScriptedClient([{"kind": "success"}])
        self.run_batch(recovered)
        self.assertEqual(len(recovered.calls), 1)
        retry = ScriptedClient([])
        self.run_batch(retry, reset_failed=True)
        self.assertEqual(retry.calls, [])

    def test_hung_process_is_terminated_and_can_restart(self):
        client = WorkerClient(self.controls, timeout=0.2, worker_target=hanging_worker, startup_timeout=5)
        self.addCleanup(client.close)
        for _ in range(2):
            result = client.request(self.parsed["invoices"][0], self.output)
            self.assertEqual(result["kind"], "retry")
            self.assertIn("超时", result["reason"])
            self.assertIsNone(client.process)

    def test_crashed_process_returns_retry_instead_of_hanging_batch(self):
        client = WorkerClient(self.controls, timeout=2, worker_target=crashing_worker, startup_timeout=5)
        self.addCleanup(client.close)
        result = client.request(self.parsed["invoices"][0], self.output)
        self.assertEqual(result["kind"], "retry")
        self.assertIsNone(client.process)

    def test_crashed_worker_reports_its_exit_code_instead_of_blank_retry_reason(self):
        client = WorkerClient(self.controls, timeout=2, worker_target=crashing_worker, startup_timeout=5)
        self.addCleanup(client.close)
        result = client.request(self.parsed["invoices"][0], self.output)
        self.assertTrue(result["reason"], "A crashing worker must not leave the user at an unexplained retry")
        self.assertEqual(result["diagnostic"]["exit_code"], 7)

    def test_timeout_reports_the_last_stage_to_distinguish_loading_from_network(self):
        client = WorkerClient(self.controls, timeout=0.2, worker_target=hanging_worker, startup_timeout=5)
        self.addCleanup(client.close)
        result = client.request(self.parsed["invoices"][0], self.output)
        self.assertIn("diagnostic", result)
        self.assertEqual(result["diagnostic"]["stage"], "正在等待查验子进程返回")


if __name__ == "__main__":
    unittest.main()
