import unittest
from unittest.mock import patch

from engine.browser import worker
from engine.diagnostics import failure_report, human_reason


class CaptureConnection:
    def __init__(self):
        self.events = []
    def send(self, event):
        self.events.append(event)
    def close(self):
        pass


class DiagnosticsTests(unittest.TestCase):
    def test_worker_native_library_failure_keeps_actual_cause(self):
        connection = CaptureConnection()
        with patch('engine.browser.BrowserEngine', side_effect=ImportError('DLL load failed while importing onnxruntime_pybind11_state')):
            worker(connection)
        failure = connection.events[-1]
        self.assertEqual(failure['type'], 'error')
        self.assertIn('运行库', failure['message'])
        self.assertIn('onnxruntime_pybind11_state', failure['diagnostic']['message'])
        self.assertEqual(failure['diagnostic']['error_type'], 'ImportError')

    def test_report_redacts_invoice_tokens_and_does_not_collect_locals(self):
        try:
            raise RuntimeError('Invoice 00000000000000000001 token=secret123 https://tax.test/fail?session=abc')
        except RuntimeError as error:
            diagnostic = failure_report(error, '正在打开查验页面')
        text = str(diagnostic)
        self.assertNotIn('00000000000000000001', text)
        self.assertNotIn('secret123', text)
        self.assertNotIn('session=abc', text)
        self.assertIn('正在打开查验页面', text)

    def test_permission_and_memory_are_distinguished_only_with_error_evidence(self):
        self.assertIn('内存', human_reason(MemoryError(), 'generic'))
        self.assertIn('拒绝访问', human_reason(PermissionError(), 'generic'))
        self.assertEqual(human_reason(RuntimeError('unknown'), '启动失败'), '启动失败')
