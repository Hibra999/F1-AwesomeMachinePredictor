from copy import deepcopy
import json
from math import log
from pathlib import Path
import unittest

from f1forecast.pages import verify_recent
from f1forecast.race_evaluation import race_evaluation, utc_time


class RaceEvaluationTest(unittest.TestCase):
    def setUp(self):
        start = '2025-01-01T12:00:00Z'
        self.gp = dict(id='2025-01', race=utc_time(start).timestamp())
        self.feed = dict(event=dict(id=self.gp['id'], race_utc=start, scheduled_laps=70), mode='live',
                         updated_utc='2025-01-01T13:00:00Z', complete=True, predictions=[])
        for lap in (2, 5, 7):
            stamp = f'2025-01-01T12:{lap:02d}:00Z'
            drivers = []
            for code, p in zip(('AAA', 'BBB', 'CCC', 'DDD'), ((.35, .2, .2), (.3, .2, .25), (.2, .3, .3), (.15, .3, .25))):
                drivers.append(dict(id=code, name=code, team='Team', first=p[0], second=p[1], third=p[2], podium=sum(p)))
            self.feed['predictions'].append(dict(lap=lap, cutoff_utc=stamp, published_utc=stamp, origin='published',
                model_version='pl-test', method='mix', reference='positions', horizon='race', drivers=drivers,
                podium=dict(drivers=['BBB', 'CCC', 'DDD'], probability=.1)))
        self.official = ['EEE', 'AAA', 'BBB']
        self.frame = ['EEE', 'AAA', 'BBB', 'CCC', 'FFF']
        self.classified = '2025-01-01T14:00:00Z'

    def score(self, status='final', distance=7):
        return race_evaluation(self.feed, self.gp, status, self.official, self.frame, distance, self.classified)

    def test_union_missing_winner_gaps_and_actual_shortened_distance(self):
        score = self.score()
        self.assertEqual((score['cuts'], score['first_lap'], score['last_lap'], score['excluded_finish']), (2, 2, 5, 1))
        self.assertAlmostEqual(score['brier_podium'], (1 + .25**2 + .25**2 + .8**2 + .7**2) / 6)
        self.assertAlmostEqual(score['winner_log_loss'], -log(1e-15))
        self.assertEqual((score['winner_hits'], score['exact_hits']), (0, 0))

        self.official, self.frame = ['BBB', 'CCC', 'DDD'], ['AAA', 'BBB', 'CCC', 'DDD']
        score = self.score()
        self.assertEqual((score['winner_hits'], score['exact_hits']), (0, 2))
        self.assertAlmostEqual(score['winner_log_loss'], -log(.3))

    def test_no_hindsight_or_late_emission(self):
        self.assertIsNone(self.score('provisional'))
        self.assertIsNone(self.score('pending'))
        self.assertIsNone(self.score(distance=None))
        self.assertIsNone(self.score(distance=70))  # Planned laps and updated_utc do not certify a flag.
        self.feed['predictions'][0]['origin'] = 'backtest'
        self.feed['predictions'][1]['published_utc'] = '2025-01-01T12:07:01Z'
        self.assertIsNone(self.score())
        self.feed['predictions'][0]['origin'] = 'published'
        self.feed['finish_utc'] = '2025-01-01T12:04:00Z'
        result = self.score()
        self.assertEqual((result['cuts'], result['last_lap'], result['excluded_finish']), (1, 2, 2))
        self.feed['event']['id'] = '2025-02'
        self.assertIsNone(self.score())
        self.feed['event']['id'] = '2025-01'
        self.feed['mode'] = 'replay'
        self.assertIsNone(self.score())
        self.feed['mode'] = 'live'
        self.feed['complete'] = False
        self.assertIsNone(self.score())

    def test_rejects_duplicate_nan_or_incoherent_joint(self):
        cut = self.feed['predictions'][0]
        clean = deepcopy(cut)
        for change in (lambda c: c.update(lap=True), lambda c: c['drivers'][0].update(id='BBB'),
                       lambda c: c['drivers'][0].update(first=float('nan')),
                       lambda c: c['drivers'][0].update(podium=.2), lambda c: c['podium'].update(probability=.9)):
            with self.subTest(change=change):
                cut = self.feed['predictions'][0] = deepcopy(clean)
                change(cut)
                with self.assertRaises(ValueError):
                    self.score()

    def test_vegas_capture_counts_only_received_pre_flag_laps(self):
        capture = json.loads(Path('local-panel/las-vegas-2026.json').read_text(encoding='utf-8'))
        gp = dict(id='2026-21', race=utc_time('2026-11-22T04:00:00Z').timestamp())
        # Formula 1's 22 participants include LIN/PER/ALB without numeric position.
        frame = 'ALB ALO ANT BEA BOR BOT CAM COL GAS HAD HAM HUL LAW LEC LIN NOR PER PIA RUS SAI STR VER'.split()
        value = race_evaluation(capture, gp, 'final', ['VER', 'NOR', 'PIA'], frame, 50, '2026-11-22T08:20:00Z')
        self.assertEqual((value['cuts'], value['first_lap'], value['last_lap'], value['excluded_finish']), (24, 13, 49, 1))

    def test_public_evaluation_requires_matching_final_and_only_totals(self):
        row = dict(gp='2025-01', date='2025-01-01', name='Test Grand Prix', status='final', official=self.official,
                   names={k: k for k in self.official}, cutoff_utc='2025-01-01T11:30:00Z', classification_utc=self.classified,
                   source_url='https://www.fia.com/system/files/decision-document/2025_test_grand_prix_-_final_race_classification.pdf',
                   result_url='https://www.formula1.com/en/results/2025/races/1/test/race-result',
                   forecast=None, forecast_urls=[], origin=None, model=None, published_utc=None, race_evaluation=self.score())
        other = dict(row, gp='2025-02', name='Other Grand Prix', race_evaluation=None,
                     source_url=row['source_url'].replace('test_grand_prix', 'other_grand_prix'),
                     result_url=row['result_url'].replace('/1/test/', '/2/other/'))
        report = dict(races=[row, other], pending=[], as_of_utc='2025-01-01T16:00:00Z')
        verify_recent(report)
        for change in (lambda r: r.update(classification_utc=None), lambda r: r.update(status='provisional'),
                       lambda r: r['race_evaluation'].update(lap_probabilities=[]),
                       lambda r: r['race_evaluation'].update(cuts=True), lambda r: r['race_evaluation'].update(cuts=12),
                       lambda r: r['race_evaluation'].update(winner_log_loss=float('inf')),
                       lambda r: r.update(source_url=r['result_url'])):
            with self.subTest(change=change):
                bad = deepcopy(report)
                change(bad['races'][0])
                with self.assertRaises(ValueError):
                    verify_recent(bad)
