"""Diagnostic failures must not be reported as healthy hardware or steal capture."""
import importlib.util
import io
from pathlib import Path
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('hardware_diagnostics', Path(__file__).resolve().parents[2] / 'scripts/hardware_diagnostics.py')
diag = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diag)


class HardwareDiagnosticsTests(unittest.TestCase):
    def test_no_signal_is_not_broken_hardware(self):
        state, detail = diag.signal_result([{'frames': 0}])
        self.assertEqual(state, 'WARN')
        self.assertIn('不等于采集卡损坏', detail)

    def test_summary_separates_hardware_health_from_untested_signal(self):
        checks = [dict(key='gpu_0', state='PASS'), dict(key='capture_signal', state='SKIP')]
        self.assertIn('输入信号尚未验证', diag.summarize(checks))
        self.assertNotIn('发现异常', diag.summarize(checks))

    def test_failed_driver_takes_priority_over_skipped_signal(self):
        checks = [dict(key='capture_node', state='FAIL'), dict(key='capture_signal', state='SKIP')]
        self.assertIn('发现异常', diag.summarize(checks))

    def test_no_signal_summary_is_not_a_pass(self):
        checks = [dict(key='capture_signal', state='WARN')]
        self.assertIn('待确认', diag.summarize(checks))

    def test_command_timeout_is_bounded(self):
        code, _, error = diag.command([diag.sys.executable, '-c', 'import time; time.sleep(10)'], timeout=0.05)
        self.assertEqual(code, 124)
        self.assertIn('超时', error)

    def test_missing_command_is_reported(self):
        code, _, error = diag.command(['/nonexistent/surgr1-diagnostic-command'])
        self.assertEqual(code, 127)
        self.assertTrue(error)

    def test_pipeline_failure_is_not_no_signal(self):
        state, detail = diag.signal_result([{'last_error': 'driver initialization failed'}])
        self.assertEqual(state, 'FAIL')
        self.assertIn('driver initialization failed', detail)

    def test_black_frames_do_not_claim_healthy_input(self):
        state, _ = diag.signal_result([dict(frames=3, near_black=True, connection='sdi', mode='auto')])
        self.assertEqual(state, 'WARN')

    def test_good_frames_report_connector_and_format(self):
        state, detail = diag.signal_result([dict(frames=3, near_black=False, connection='sdi', mode='1080p50', width=1920, height=1080, fps=50)])
        self.assertEqual(state, 'PASS')
        self.assertIn('1920×1080', detail)
        self.assertIn('SDI', detail)

    def test_device_busy_is_skipped(self):
        with patch.object(diag.shutil, 'which', return_value='/usr/bin/fuser'), patch.object(diag, 'command', return_value=(0, '1234', '/dev/blackmagic/io0:')):
            busy, detail = diag.device_busy(['/dev/blackmagic/io0'])
        self.assertTrue(busy)
        self.assertIn('1234', detail)
        self.assertEqual(diag.signal_result([{'busy': True}])[0], 'SKIP')

    def test_uncertain_ownership_never_means_free(self):
        for response in [(127, '', 'missing'), (124, '', 'timeout'), (1, '', 'permission denied')]:
            with patch.object(diag.shutil, 'which', return_value='/usr/bin/fuser'), patch.object(diag, 'command', return_value=response):
                self.assertIsNone(diag.device_busy(['/dev/blackmagic/io0'])[0])
        with patch.object(diag.shutil, 'which', return_value=None):
            self.assertIsNone(diag.device_busy(['/dev/blackmagic/io0'])[0])

    def test_no_owner_means_free(self):
        with patch.object(diag.shutil, 'which', return_value='/usr/bin/fuser'), patch.object(diag, 'command', return_value=(1, '', '')):
            self.assertFalse(diag.device_busy(['/dev/blackmagic/io0'])[0])

    def collect_fixture(self, *, no_signal, node_exists=True, busy=False):
        args = SimpleNamespace(device=0, no_signal=no_signal, connection='auto', mode='auto')

        def run(args, **kwargs):
            if args[0] == 'lspci':
                return 0, '17:00.0 Blackmagic [bdbd:a143]', ''
            if args[0] == 'nvidia-smi':
                return 0, '0, NVIDIA RTX, 580, 24564, 50, 40', ''
            if '-c' in args:
                return 0, '[{"index":0,"ok":true}]', ''
            if args[0] == 'BlackmagicFirmwareUpdater':
                return 0, '0: /dev/blackmagic/io0 [DeckLink] 0x119 OK', ''
            if args[0] == 'gst-inspect-1.0':
                return 0, 'Decklink Video Source', ''
            self.fail(f'Unexpected command; must not open capture: {args}')

        with patch.object(diag, 'command', side_effect=run), \
                patch.object(diag.Path, 'exists', return_value=node_exists), \
                patch.object(diag.os, 'access', return_value=True), \
                patch.object(diag, 'device_busy', return_value=(busy, '设备占用')) as owner_check, \
                redirect_stdout(io.StringIO()):
            report = diag.collect(args)
        return {item['key']: item for item in report['checks']}, owner_check

    def test_quick_check_never_opens_capture(self):
        checks, owner_check = self.collect_fixture(no_signal=True)
        self.assertEqual(checks['cuda_0']['state'], 'PASS')
        self.assertEqual(checks['capture_signal']['state'], 'SKIP')
        owner_check.assert_not_called()

    def test_busy_card_is_not_probed(self):
        checks, owner_check = self.collect_fixture(no_signal=False, busy=True)
        self.assertEqual(checks['capture_signal']['state'], 'SKIP')
        owner_check.assert_called_once_with([Path('/dev/blackmagic/io0')])

    def test_missing_node_with_pci_card_points_to_driver(self):
        checks, owner_check = self.collect_fixture(no_signal=False, node_exists=False)
        self.assertEqual(checks['capture_node']['state'], 'FAIL')
        self.assertIn('PCIe 已识别采集卡', checks['capture_node']['detail'])
        self.assertEqual(checks['capture_signal']['state'], 'SKIP')
        owner_check.assert_not_called()

    def test_reports_are_private_and_unique(self):
        with tempfile.TemporaryDirectory() as folder:
            report = dict(time='now', summary='test', checks=[], limits='test')
            first = diag.save_report(report, Path(folder))
            second = diag.save_report(report, Path(folder))
            self.assertNotEqual(first, second)
            self.assertEqual(first.stat().st_mode & 0o777, 0o600)
            self.assertEqual(first.with_suffix('.json').stat().st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
