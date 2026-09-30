"""Offline queue safety checks; no network, remote writes, or paid requests."""
import base64
import copy
import json
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import review as r
import queue_worker as q


B, H = 'a' * 40, 'b' * 40
ENV = {'GITHUB_REPOSITORY': q.REPO, 'GITHUB_REF': 'refs/heads/master',
       'GITHUB_RUN_ID': '101', 'GITHUB_RUN_ATTEMPT': '1', 'GH_TOKEN': 'offline',
       'GITHUB_EVENT_NAME': 'schedule', 'GITHUB_SHA': H,
       'AI_REVIEW_ENABLED': 'true', 'AI_PUBLISH_ENABLED': 'true',
       'PRICING_VALID_UNTIL': '2026-10-31'}
SAFE = datetime(2026, 9, 15, 12, tzinfo=timezone.utc)


def document(data):
    return {'type': 'file', 'encoding': 'base64', 'sha': 'c' * 40,
            'content': base64.b64encode(json.dumps(data).encode()).decode()}


def claimed():
    key = q.event_key(B, H)
    data = {'version': 2, 'months': {'2026-09': ['101:1']}, 'events': {
        key: {'ref': 'refs/heads/master', 'before': B, 'after': H,
              'state': 'claimed', 'created_at': '2026-09-15T12:00:00Z',
              'claim': {'id': '101:1', 'month': '2026-09'}}}}
    return document(data), data, key, data['events'][key]


class QueueAuditTests(unittest.TestCase):
    def test_migration_preserves_all_prior_reservations(self):
        months = {'2026-08': ['77:1'], '2026-09': ['101:1', '101:2']}
        data, migrated = q.parse(document({'version': 1, 'months': months}))
        self.assertTrue(migrated)
        self.assertEqual(data, {'version': 2, 'months': months, 'events': {}})

    def test_queue_rejects_corrupt_months_before_using_claim(self):
        for entries in ['prefix101:1suffix', ['101:1'] * 2,
                        [str(i) + ':1' for i in range(101)]]:
            with self.subTest(entries=type(entries).__name__):
                doc, data, _, _ = claimed()
                data['months']['2026-09'] = entries
                with self.assertRaises(r.Stop):
                    q.parse(document(data))

    def test_queue_rejects_unreserved_claim(self):
        _, data, _, _ = claimed()
        data['months']['2026-09'] = []
        with self.assertRaisesRegex(r.Stop, 'claim missing reservation'):
            q.parse(document(data))

    def test_delayed_review_defers_before_payment(self):
        selected = claimed()
        for now in [datetime(2026, 9, 30, 23, 59, tzinfo=timezone.utc),
                    datetime(2026, 10, 1, 1, tzinfo=timezone.utc)]:
            def main(mode, budget_check, event_override):
                budget_check()
                self.fail('Payment must be unreachable for an expired/windowed claim')
            with self.subTest(now=now), patch.object(q, 'selected', return_value=selected), \
                    patch.object(q, 'now_utc', return_value=now), \
                    patch.object(r, 'main', side_effect=main), patch.object(q, 'output') as output:
                q.run_review()
                output.assert_called_once_with('review_outcome', 'deferred')

    def test_ambiguous_payment_is_never_marked_deferred(self):
        def main(mode, budget_check, event_override):
            budget_check()
            raise TimeoutError('Paid request outcome unknown')
        with patch.object(q, 'selected', return_value=claimed()), \
                patch.object(q, 'now_utc', return_value=SAFE), \
                patch.object(r, 'main', side_effect=main) as review, patch.object(q, 'output') as output:
            with self.assertRaises(TimeoutError):
                q.run_review()
            review.assert_called_once()
            output.assert_called_once_with('review_outcome', 'uncertain')

    def test_safe_deferral_requeues_without_refunding_reservation(self):
        selected = claimed()
        env = dict(ENV, REVIEW_OUTCOME='deferred', REVIEW_RESULT='success', PUBLISH_RESULT='skipped')
        with patch.dict(os.environ, env), patch.object(q, 'selected', return_value=selected), \
                patch.object(q, 'write') as write:
            q.finalize()
            data = write.call_args.args[1]
            self.assertEqual(data['months'], {'2026-09': ['101:1']})
            self.assertEqual(data['events'][selected[2]]['state'], 'pending')
            self.assertNotIn('claim', data['events'][selected[2]])

    def test_unknown_or_failed_publication_cannot_requeue_payment(self):
        for outcome, review, publication in [('', 'cancelled', 'skipped'),
                                              ('uncertain', 'failure', 'skipped'),
                                              ('completed', 'success', 'failure')]:
            selected = claimed()
            env = dict(ENV, REVIEW_OUTCOME=outcome, REVIEW_RESULT=review, PUBLISH_RESULT=publication)
            with self.subTest(outcome=outcome), patch.dict(os.environ, env), \
                    patch.object(q, 'selected', return_value=selected), patch.object(q, 'write') as write:
                q.finalize()
                data = write.call_args.args[1]
                self.assertEqual(data['events'][selected[2]]['state'], 'attention')
                self.assertEqual(data['months']['2026-09'], ['101:1'])

    def test_cancelled_enqueue_has_no_schedule_reconciliation(self):
        # A known limitation: a missing push record cannot be recovered by the
        # scheduler; it consults existing ledger entries only after migration.
        data = {'version': 2, 'months': {}, 'events': {}}
        with patch.dict(os.environ, ENV), patch.object(q, 'read', return_value=(document(data), data, False)), \
                patch.object(q, 'write') as write, patch.object(r, 'request_json') as api:
            q.coordinate()
            write.assert_not_called()
            api.assert_not_called()


if __name__ == '__main__':
    unittest.main()
