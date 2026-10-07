import unittest
from unittest.mock import AsyncMock, Mock, call

from scripts.audit_surgr1_updates import audit, distribution
from scripts.verify_surgr1_updates import settled_snapshot


class SettledSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetches_rows_after_observing_settled(self):
        final = [{'window_id': 124, 'others': {'stage': 2}}]
        api = Mock(get=AsyncMock(side_effect=[Mock(json=lambda: {'settled': True}), Mock(json=lambda: final)]))
        state, rows = await settled_snapshot(api, 'test')
        self.assertTrue(state['settled'])
        self.assertEqual(rows, final)
        self.assertEqual(api.get.call_args_list, [call('/api/analysis/pending-refinements/test'),
                                                 call('/api/analysis/summaries/test')])

    async def test_does_not_treat_pending_rows_as_a_final_snapshot(self):
        api = Mock(get=AsyncMock(return_value=Mock(json=lambda: {'settled': False})))
        state, rows = await settled_snapshot(api, 'test')
        self.assertFalse(state['settled'])
        self.assertIsNone(rows)
        self.assertEqual(api.get.await_count, 1)


class VerificationAuditTests(unittest.TestCase):
    def test_draft_breakdown_does_not_count_refinement_time(self):
        record = {'session_id': 'test', 'rows': [{'window_id': 0, 'start_time': 0, 'end_time': 5}],
                  'receipts': [{'window_id': wid, 'stage': stage, 'end': 5, 'received_wall': wall,
                    'timing': {'source_window_end_wall': 100, 'window_ready_wall': 100.2,
                               'stage1_ready_to_save_wall': 101.9, 'expert_ms': 1500,
                               'expert_components_ms': {'phase_ms': 120}}}
                   for wid, stage, wall in [(0, 1, 102), (0, 2, 120), (5, 1, 106)]]}
        result = audit(record)
        self.assertEqual(result['draft_first_five_windows_seconds']['count'], 1)
        self.assertEqual(result['draft_remaining_windows_seconds']['p50'], 6)
        self.assertEqual(result['draft_over_five_seconds'], [{'ui_window': 6, 'lag_seconds': 6}])
        parts = result['draft_component_milliseconds']
        self.assertEqual(parts['expert_ms']['count'], 2)
        self.assertEqual(parts['window_poll_ms']['p50'], 200)

    def test_distribution_ignores_missing_and_nonfinite_measurements(self):
        self.assertEqual(distribution([None, float('nan')]), {'count': 0})
        self.assertEqual(distribution([1, 3]), {'count': 2, 'p50': 2, 'p95': 2.9, 'max': 3})

    def test_eof_wall_latency_is_retained_and_duplicate_updates_not_double_counted(self):
        record = {'session_id': 'test', 'rows': [
            {'window_id': 0, 'start_time': 0, 'end_time': 5, 'surgical_phase': 'Preparation'}],
            'receipts': [
                {'window_id': 0, 'end': 5, 'stage': 2, 'received_wall': 120,
                 'video_time': 5, 'timing': {'source_window_end_wall': 100}},
                {'window_id': 0, 'end': 5, 'stage': 2, 'received_wall': 125,
                 'video_time': 5, 'timing': {'source_window_end_wall': 100}}]}
        result = audit(record)
        self.assertEqual(result['capture_window_end_to_ui_seconds']['2']['p50'], 20)
        self.assertEqual(result['capture_window_end_to_ui_seconds']['2']['count'], 1)
        self.assertEqual(result['moving_playback_clock_lag_seconds']['2']['count'], 0)
        self.assertEqual(result['r1_status_counts'], {'missing': 1})

    def test_audit_flags_gaps_revisits_and_cross_window_observations(self):
        rows = [
            {'window_id': 0, 'start_time': 0, 'end_time': 5, 'surgical_phase': 'Preparation'},
            {'window_id': 1, 'start_time': 5, 'end_time': 10, 'surgical_phase': 'CalotTriangleDissection'},
            {'window_id': 3, 'start_time': 15, 'end_time': 20, 'surgical_phase': 'Preparation',
             'others': {'experts': {'surgr1': {'status': 'completed', 'observations': [
                 {'timestamp': 14.9}, {'timestamp': 16}, {'timestamp': 20}]}}}}]
        result = audit({'session_id': 'test', 'rows': list(reversed(rows))})
        self.assertEqual(len(result['window_gaps']), 1)
        self.assertEqual(len(result['r1_timestamp_errors']), 2)
        self.assertEqual(result['phase_revisits_for_review'][0]['ui_window'], 4)


if __name__ == '__main__':
    unittest.main()
