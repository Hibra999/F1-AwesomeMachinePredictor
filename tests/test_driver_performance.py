import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from f1forecast import driver_performance as study
from f1forecast.engine import FEATURES, Snapshot


class PerformanceTests(unittest.TestCase):
    def setUp(self):
        self.gp = {'id': '2024-01', 'year': 2024, 'race': 3800}
        self.snap = Snapshot(self.gp, 'pre_race', 2000, None, 0,
                             tuple({'id': d, 'name': d, 'team': t} for d, t in [('A', 'X'), ('B', 'X'), ('C', 'Y')]),
                             np.zeros((3, len(FEATURES))), np.array([2., 2., 1.]), 'forma', ())
        self.docs = {'entries_at': 10, 'entries_status': 'dated_fia_event_document', 'entries_source': 'FIA'}
        self.roster = {'kind': 'roster', 'at': 10, 'entries': self.snap.drivers, 'source': 'FIA'}

    def test_last_shared_segment_official_pair_and_cut(self):
        events = [self.roster] + [dict(kind='qualifying', session='Qualifying', source='clock',
                                       at=at, driver=d, segment=s, seconds=t) for d, s, t, at in
                                      [('A', 1, 90, 15), ('B', 1, 92, 16), ('A', 2, 89, 17), ('B', 2, 90, 18),
                                       ('A', 2, 91, 19), ('A', 3, 85, 20), ('B', 3, 80, 2001)]]
        x = study.matrix(self.snap, events, self.docs)
        self.assertAlmostEqual(x[0, -2], 100 * (89 / 90 - 1))
        self.assertAlmostEqual(x[1, -2], 100 * (90 / 89 - 1))
        self.assertTrue(np.isnan(x[2, -2]))
        self.assertTrue(np.isnan(study.matrix(self.snap, events, {})[:, -2]).all())
        self.roster['entries'] += ({'id': 'D', 'team': 'X'},)
        self.assertTrue(np.isnan(study.matrix(self.snap, events, self.docs)[:, -2]).all())

    def test_fp1_requires_contiguous_clean_numbered_laps_and_two_drivers(self):
        source = 'https://raw.githubusercontent.com/TracingInsights/2024/' + '0' * 40 + '/GP/Practice%201/session_laptimes.json'
        events = [dict(kind='practice', session='Practice 1', at=(i + 1) * t, source=source,
                       driver=d, lap=i + 1, seconds=t) for d, t in [('A', 90), ('B', 100)] for i in range(3)]
        x = study.matrix(self.snap, events, {})
        self.assertAlmostEqual(x[0, -1], 100 * (90 / 95 - 1))
        for damaged in ([*events, events[-1]], events[:-1], [*events[:-1], {**events[-1], 'at': 301}],
                        [*events[:-1], {**events[-1], 'source': 'unverified'}]):
            self.assertTrue(np.isnan(study.matrix(self.snap, damaged, {})[:, -1]).all())

    def test_training_intersection_and_equal_gp_weight(self):
        a = SimpleNamespace(gp_id='a', snapshot=self.snap, rank=np.array([1, 0, 4]),
                            official_ids=list('ACDE'), label_at=3900)
        b = SimpleNamespace(gp_id='b', snapshot=self.snap, rank=np.array([3, 1, 2]),
                            official_ids=list('ABC'), label_at=4000)
        xs = {r.gp_id: np.ones((3, len(study.BASE) + 2)) for r in (a, b)}
        with patch.object(study, 'CatBoostRegressor') as estimator:
            study._fit([a, b], 4001, xs, 'base')
            args, kwargs = estimator.return_value.fit.call_args
            self.assertEqual(args[0].shape, (5, len(study.BASE)))
            np.testing.assert_allclose(args[1], [0, 1, 1, 0, .5])
            np.testing.assert_allclose(kwargs['sample_weight'], [.5, .5, 1/3, 1/3, 1/3])
            with self.assertRaisesRegex(ValueError, 'future training'):
                study._fit([b], 4000, xs, 'extended')

    def test_emit_all_including_ties_without_official_labels(self):
        model = Mock()
        model.predict.return_value = np.array([1.2, .5, .5])
        x = study.matrix(self.snap, [], {})
        values = study._positions(model, x, 'extended')
        np.testing.assert_equal(values, [3, 2, 2])
        self.assertEqual([d['id'] for d in study._drivers(self.snap, values)], ['B', 'C', 'A'])

    def test_future_cut_cannot_be_simulated(self):
        with patch('f1forecast.__main__.lookup', return_value=self.gp), patch.object(study, 'now', return_value=1999), \
                patch.object(study, 'load_corpus') as load:
            with self.assertRaisesRegex(ValueError, 'aún no ocurrió'):
                study.main(['--gp', '2026-22'])
            load.assert_not_called()
