import unittest
from types import SimpleNamespace

import numpy as np

from f1forecast import rank_study as study
from f1forecast.dataset import Example
from f1forecast.engine import FEATURES, epoch, utc
from f1forecast.probability import joint


def row(year, month, rank=(1, 2, 3, 4)):
    race = epoch(f'{year}-{month:02d}-15T12:00:00Z')
    ids = ('A', 'B', 'C', 'D')
    s = SimpleNamespace(gp={'year': year, 'id': f'{year}-{month:02d}', 'race': race},
                        horizon='pre_race', cutoff=race - 1800, ids=ids,
                        reference=np.arange(4.0)[::-1], x=np.zeros((4, len(FEATURES))))
    order = tuple(next((d for d, k in zip(ids, rank) if k == p), 'E') for p in (1, 2, 3))
    return Example(s, np.array(rank), order, tuple(dict.fromkeys((*ids, *order))), race + 86400)


class RankStudyTest(unittest.TestCase):
    def test_prefix_and_equal_gp_likelihood_with_ridge(self):
        x = np.array([[2.], [1.], [0.]])
        prefixes = [study._prefix(np.array(r)) for r in ((1, 2, 3), (1, 4, 3), (0, 2, 3))]
        self.assertEqual(prefixes, [[0, 1, 2], [0], []])
        stages = study._stages([x, x], prefixes[:2])
        b = np.array([0.4])
        first = np.log(np.exp([0., -.4, -.8]).sum())
        expected = .5 * ((first + np.log1p(np.exp(-.4))) / 3 + first) + .025 * .4**2
        loss, grad = study._objective(b, stages)
        self.assertAlmostEqual(loss, expected)
        numeric = (study._objective(b + 1e-5, stages)[0] - study._objective(b - 1e-5, stages)[0]) / 2e-5
        self.assertAlmostEqual(grad.item(), numeric)

    def test_train_scaler_frozen_and_unknown_weather_neutral(self):
        train = np.array([[1., np.nan], [3., np.nan]])
        scaler = study._scaler([train])
        test = np.array([[10., np.nan]])
        np.testing.assert_array_equal(study._design(test, scaler, False), [[8., 0., 0.]])
        np.testing.assert_array_equal(study._design(test, scaler, True), [[8., 0., 8.]])
        np.testing.assert_array_equal(study._design(test, scaler, None), [[8., 0.]])
        r = row(2024, 1)
        with self.assertRaisesRegex(ValueError, 'future TAF'):
            study._coverage([r], {r.gp_id: {'wet': True, 'issued_utc': utc(r.snapshot.cutoff + 1)}})
        with self.assertRaisesRegex(ValueError, 'available_utc'):
            study._coverage([r], {r.gp_id: {'wet': True, 'issued_utc': utc(r.snapshot.cutoff - 300),
                                           'available_utc': utc(r.snapshot.cutoff)}})

    def test_exact_joint_unchanged_by_late_data_race_events_and_changed_labels(self):
        gp = {**row(2024, 9).snapshot.gp, 'circuit': 'test'}
        cut = gp['race'] - 1800
        ids = ('AAA', 'BBB', 'CCC', 'DDD')
        roster = dict(kind='roster', source='entry sheet', at=cut - 60,
                      entries=[{'id': d, 'team': d} for d in ids])
        grid = dict(kind='grid', source='grid', at=cut, positions=dict(zip(ids, (1, 2, 3, 4))))
        official = {'labels': grid['positions'], 'available_at': None}
        item = {'gp': {**gp, 'race': gp['race'] + 3600}, 'events': [roster, grid], 'label': official}
        clean = study._example(item, [], gp)
        poison_events = [{**roster, 'session': 'Race', 'at': cut, 'entries': []},
                         {**roster, 'at': cut + 1, 'entries': []},
                         {**grid, 'session': 'Race', 'at': cut, 'positions': {'ZZZ': 1}}]
        poisoned = {**item, 'events': [*item['events'], *poison_events],
                    'label': {'labels': {'ZZZ': 1, 'AAA': 2, 'BBB': 3}}}
        other = study._example(poisoned, [{'at': cut - 1, 'gp_id': gp['id']}], gp)
        self.assertNotEqual(clean.podium_ids, other.podium_ids)
        self.assertEqual(clean.label_at, gp['race'] + study.LABEL_EMBARGO)
        self.assertEqual(other.snapshot.ids, ids)
        np.testing.assert_array_equal(clean.snapshot.x, other.snapshot.x)
        np.testing.assert_array_equal(clean.snapshot.reference, other.snapshot.reference)
        test = [clean, other]
        scores, _ = study._rank_scores([row(2023, 9)], test, {})
        for name in study.BASELINES:
            scores[name] = study.sealed._scores(name, test, {})
        for a, b in scores.values():
            np.testing.assert_array_equal(joint(a), joint(b))
        started = dict(kind='session', session='Race', status='Started', at=cut - 1)
        with self.assertRaisesRegex(ValueError, gp['id']):
            study._example({**item, 'events': [*item['events'], started]}, [], gp)

    def test_strict_deadline_includes_earliest_test_cut(self):
        past, test = row(2023, 12), row(2024, 1)
        lower = test.snapshot.gp['race'] - 100
        past.label_at = test.snapshot.cutoff
        train, held, cutoff = study._scope([past, test], lower, lower + 200)
        self.assertEqual((train, held, cutoff), ([], [test], test.snapshot.cutoff))
        past.label_at -= 1
        self.assertEqual(study._scope([past, test], lower, lower + 200)[0], [past])

    def test_same_scored_cohort_even_without_winner_and_paired_weather_null(self):
        rows = [row(y, m) for y in range(2022, 2026) for m in (3, 9)]
        rows[-1] = row(2025, 9, (4, 2, 3, 5))
        rows[-1].snapshot.reference.fill(0)
        report = study._study(rows, {})
        self.assertEqual(len(report['folds']), 4)
        self.assertEqual(report['taf']['unknown'], 4)
        self.assertEqual(report['decision_primary_only'], 'conservar')
        for name in study.BASELINES:
            last = report['folds'][-1]['metrics'][name]
            # Uniform forecast for four entrants. Missing official winner enlarges the union to five.
            self.assertAlmostEqual(last['brier'], (2 * .75**2 + 2 * .25**2 + 1) / 5)
            self.assertAlmostEqual(last['winner_log_loss'], -np.log(1e-15))
        for metrics in report['metrics'].values():
            self.assertEqual(metrics['gps'], 4)
            self.assertGreaterEqual(metrics['winner_log_loss'], -np.log(1e-15) / 4)
        delta = report['contrasts']['rank_pl_taf_minus_rank_pl']['brier']
        np.testing.assert_allclose(delta['ci95'], [0., 0.], atol=1e-12)
        self.assertEqual(report['folds'][0]['inner_oos_gps'], 2)
