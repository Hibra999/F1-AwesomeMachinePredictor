import unittest
from types import SimpleNamespace

import numpy as np

from f1forecast.dataset import Example
from f1forecast.engine import FEATURES, epoch, utc
from f1forecast.grid_penalties import _evidence, _study
from f1forecast.probability import joint, summarize


def snapshot(year=2024, round_=1, reference='clasificación'):
    race = epoch(f'{year}-{3 if round_ <= 12 else 9:02d}-{(round_ - 1) % 12 + 1:02d}T13:00:00Z')
    return SimpleNamespace(gp={'year': year, 'id': f'{year}-{round_:02d}', 'race': race}, cutoff=race - 1800,
                           horizon='pre_race', reference_name=reference, ids=('AAA', 'BBB', 'CCC', 'DDD'),
                           reference=np.array([1.5, .5, 0., -1.]), x=np.zeros((4, len(FEATURES))))


def witness(s, *items, lead=300, status='ok'):
    return {'reason': status, 'available_utc': utc(s.cutoff - lead), 'revision_url': 'revision:test',
            'drivers': {d: {'qual': q, 'grid': g, 'kind': k} for d, q, g, k in items}}


def decision(s, n, driver='AAA', kind='places', lead=3600):
    return dict(driver=driver, kind=kind, document=n, url=f'fia:test:{n}', available_utc=utc(s.cutoff - lead))


def board(*decisions, status='ok'):
    return {'reason': status, 'decisions': decisions}


class GridPenaltyStudyTest(unittest.TestCase):
    def test_only_same_driver_and_numbered_net_loss_change_strength_once(self):
        s = snapshot()
        w = witness(s, ('AAA', 2, 8, 'places'), ('BBB', 3, 4, 'ordinary'), ('CCC', 4, None, 'pit'),
                    ('DDD', 7, 7, 'back'), ('EEE', 5, 9, 'back'))
        b = board(decision(s, 4), decision(s, 5), decision(s, 6, 'CCC', 'pit'), decision(s, 7, 'DDD', 'back'),
                  decision(s, 8, 'EEE', 'back'))
        delta, audit = _evidence(s, [w], b)
        np.testing.assert_allclose(delta, [-.70 * 1.65 * np.log(4), 0., 0., 0.])
        c = audit['coverage']
        self.assertEqual((c['affected_drivers'], c['places_lost'], c['pit_excluded'], c['rows_changed']), (2, 10, 1, 1))
        before, _, _ = summarize(joint(s.reference), 4)
        after, _, _ = summarize(joint(s.reference + delta), 4)
        self.assertLess(after[0].sum(), before[0].sum())
        np.testing.assert_allclose(after.sum(axis=0), [1, 1, 1], atol=1e-12, rtol=0)
        # A valid grid is already in the baseline; don't charge again or hide the historical loss.
        s.reference_name = 'parrilla'
        delta, audit = _evidence(s, [w], b)
        self.assertFalse(delta.any())
        self.assertEqual(audit['coverage']['places_lost'], 10)

    def test_distribution_ignores_future_and_precision_boundary_but_uses_eligible_revision(self):
        s = snapshot()
        w = witness(s, ('AAA', 1, 3, 'places'))
        before = decision(s, 2)
        base, _ = _evidence(s, [w], board(before))
        late = witness(s, ('AAA', 1, 20, 'places'), lead=0)
        poisoned, _ = _evidence(s, [late, w], board(before, decision(s, 3, kind='pit', lead=0)))
        np.testing.assert_array_equal(joint(s.reference + base), joint(s.reference + poisoned))
        self.assertFalse(_evidence(s, [w], board(decision(s, 3, lead=300)))[0].any())
        self.assertFalse(_evidence(s, [late], board(before))[0].any())
        updated = witness(s, ('AAA', 1, 5, 'places'), lead=120)
        after, _ = _evidence(s, [updated, w], board(before))
        self.assertLess(after[0], base[0])

    def test_pit_disqualification_ambiguity_and_missing_sources_never_mean_last(self):
        s = snapshot()
        w = witness(s, ('AAA', 3, 8, 'places'))
        doc = decision(s, 1)
        for b, v in ((board(doc, decision(s, 2, kind='pit')), w),
                     (board(doc, decision(s, 2, kind='back')), w),
                     (board(doc, dict(decision(s, 3, kind='other'), detail='dsq')), w),
                     (board(doc, status='unresolved'), w), (board(), w),
                     (board(doc), {**w, 'reason': 'unusable_table'}),
                     (board(doc), witness(s, ('AAA', 3, 8, 'ordinary')))):
            d, facts = _evidence(s, [v], b)
            self.assertFalse(d.any())
            self.assertEqual(facts['coverage']['affected_drivers'], 0)

    def test_missing_ruling_excludes_only_identified_drivers_in_partial_review(self):
        s = snapshot()
        w = witness(s, ('AAA', 1, 6, 'places'), ('BBB', 2, 7, 'places'))
        b = {**board(decision(s, 4), decision(s, 5, 'BBB'), status='partial_drivers'), 'unresolved_drivers': ['AAA']}
        change, facts = _evidence(s, [w], b)
        self.assertEqual(change[0], 0)
        self.assertLess(change[1], 0)
        self.assertEqual(facts['coverage']['places_lost'], 5)
        self.assertEqual(facts['coverage']['fia_reviewed_gps'], 0)
        self.assertEqual(facts['coverage']['fia_partial_gps'], 1)
        with self.assertRaisesRegex(ValueError, 'partial board'):
            _evidence(s, [w], {**b, 'unresolved_drivers': []})

    def test_missing_information_keeps_all_gp_and_uses_only_eligible_calibration(self):
        rounds = [(2022, 1), (2023, 1), (2023, 13), *((y, n) for y in (2024, 2025) for n in range(1, 25))]
        rows, evidence = [], {}
        for y, n in rounds:
            s = snapshot(y, n)
            label = epoch('2024-08-01T00:00:00Z') if (y, n) == (2023, 13) else s.gp['race'] + 14 * 86400
            r = Example(s, np.arange(1, 5), s.ids[:3], s.ids, label)
            rows.append(r)
            if y >= 2024:
                evidence[r.gp_id] = ({'available_utc': None, 'drivers': {}, 'reason': 'no_revision'}, board())
        report = _study(rows, evidence)
        self.assertEqual(report['folds'][0]['calibration_gps'], 1)
        self.assertEqual(report['metrics']['elo']['gps'], 48)
        self.assertEqual(report['metrics']['elo'], report['metrics']['sanctions'])
        self.assertEqual(report['contrasts']['brier']['ci95'], [0., 0.])
        self.assertIsNone(report['sanctioned_finish_mae']['mae']['sanctions'])
        self.assertFalse(report['promoted_to_production'])
        del evidence['2025-24']
        with self.assertRaisesRegex(ValueError, '48'):
            _study(rows, evidence)
