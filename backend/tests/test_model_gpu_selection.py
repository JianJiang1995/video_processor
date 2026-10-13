import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location('select_model_gpus', Path(__file__).resolve().parents[2]
                                            / 'scripts/select_model_gpus.py')
gpu = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gpu)


class ModelGpuSelectionTests(unittest.TestCase):
    def test_monitor_moved_to_zero_moves_sustained_vlm_to_one(self):
        rows = gpu.parse_gpus('0, 23000, Enabled\n1, 24500, Disabled')
        self.assertEqual(gpu.select_gpus(rows, 1), ('1', '0'))

    def test_original_monitor_on_one_keeps_established_allocation(self):
        rows = gpu.parse_gpus('0, 24500, Disabled\n1, 23000, Enabled')
        self.assertEqual(gpu.select_gpus(rows, 1), ('0', '1'))

    def test_explicit_overrides_are_respected_and_never_silently_swapped(self):
        rows = gpu.parse_gpus('0, 23000, Enabled\n1, 24500, Disabled')
        self.assertEqual(gpu.select_gpus(rows, 1, r1_override='1'), ('0', '1'))
        self.assertEqual(gpu.select_gpus(rows, 1, vlm_override='0'), ('0', '1'))
        with self.assertRaises(ValueError):
            gpu.select_gpus(rows, 1, r1_override='1', vlm_override='1')
        with self.assertRaises(ValueError):
            gpu.select_gpus(rows, 1, vlm_override='2')

    def test_unknown_display_status_retains_preferred_r1(self):
        rows = gpu.parse_gpus('0, 23000, N/A\n1, 24500, N/A')
        self.assertEqual(gpu.select_gpus(rows, 1), ('0', '1'))

    def test_three_gpus_use_free_non_display_gpu(self):
        rows = gpu.parse_gpus('0, 24500, Enabled\n1, 15000, Disabled\n2, 23000, Disabled')
        self.assertEqual(gpu.select_gpus(rows, 1), ('2', '1'))

    def test_single_gpu_can_run_vlm_with_r1_disabled(self):
        rows = gpu.parse_gpus('0, 24500, Enabled')
        self.assertEqual(gpu.select_gpus(rows, 1, r1_enabled=False), ('0', '0'))
        with self.assertRaises(ValueError):
            gpu.select_gpus(rows, 1)


if __name__ == '__main__':
    unittest.main()
