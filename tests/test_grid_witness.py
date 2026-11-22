import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from f1forecast.engine import epoch
from f1forecast.grid_witness import load_witness


GP = {'id': '2024-14', 'year': 2024, 'round': 14, 'name': 'Belgian Grand Prix',
      'race': epoch('2024-07-28T13:00:00Z')}
HEADER = '''{| class="wikitable"
! {{Tooltip|Pos.|Qualifying position}}
! {{Tooltip|No.|Car number}}
! Driver
! Constructor
! {{nowrap|Qualifying times}}
! {{Tooltip|Final<br>grid|Final grid position}}
|-
! Q1
! Q2
! Q3'''


def row(pos, name, grid):
    return f'|-\n! scope="row" |{pos}\n| 10\n|[[{name}]]\n|Team\n|1:00\n|1:00\n|1:00\n| {grid}\n'


# Synthetic positions; no historical result rows stored in Git.
TEXT = '| Year = 2024\n| Race_No = 14\n== Sprint qualifying ==\n' + HEADER + '\n|}\n== Qualifying ==\n' + HEADER + '\n' + ''.join([
    row(1, 'Zhou Guanyu', '2{{ref|7|1}}'), row(2, 'George Russell (racing driver)|George Russell', '1'),
    row(3, 'Max Verstappen', '3{{ref|8|2}}'), row(4, 'Charles Leclerc', '{{abbr|PL|Pit lane}}'),
    row('{{abbr|DSQ|Disqualified}}', 'Carlos Sainz Jr.', '4'), row(5, 'Pierre Gasly', '5{{ref|9|3}}')]) + '''|}
* {{note|7|1}} – [[Zhou Guanyu]] received a three-place grid penalty for impeding [[George Russell (racing driver)|George Russell]].
* {{note|8|2}} – [[Max Verstappen]] was required to start the race from the back of the grid.
* {{note|9|3}} – [[Pierre Gasly]] was under investigation for a potential grid penalty.
== Race ==
''' + HEADER + '\n' + row(1, 'Lewis Hamilton', '9') + '|}'


def fetch(work, text=TEXT, stamp='2024-07-28T12:29:59Z', sha=None):
    rev = {'revid': 999, 'sha1': sha or hashlib.sha1(text.encode()).hexdigest(), 'timestamp': stamp,
           'slots': {'main': {'contentmodel': 'wikitext', 'content': text}}}
    response = Mock(content=json.dumps({'query': {'pages': [{'title': '2024 Belgian Grand Prix', 'ns': 0,
                                                             'revisions': [rev]}]}}).encode())
    with patch('f1forecast.grid_witness.requests.get', return_value=response) as get:
        result = load_witness(GP, work)
    return result, get.call_args.kwargs['params']


class GridWitnessTest(unittest.TestCase):
    def test_only_main_qualifying_and_subjects_in_strictly_older_revision(self):
        with TemporaryDirectory() as tmp:
            work = Path(tmp)
            self.assertEqual(load_witness(GP, work, offline=True)['reason'], 'cache_missing')
            result, query = fetch(work)
            self.assertEqual((query['rvstart'], query['rvlimit'], query['rvdir']), ('2024-07-28T12:29:59Z', 1, 'older'))
            self.assertEqual(result['available_utc'], '2024-07-28T12:29:59Z')
            self.assertTrue(result['revision_url'].endswith('999'))
            self.assertEqual(result['sha256'], hashlib.sha256(TEXT.encode()).hexdigest())
            self.assertEqual(result['drivers'], {
                'ZHO': {'qual': 1, 'grid': 2, 'kind': 'places'}, 'RUS': {'qual': 2, 'grid': 1, 'kind': 'ordinary'},
                'VER': {'qual': 3, 'grid': 3, 'kind': 'back'}, 'LEC': {'qual': 4, 'grid': None, 'kind': 'pit'},
                'SAI': {'qual': None, 'grid': 4, 'kind': 'other'}, 'GAS': {'qual': 5, 'grid': 5, 'kind': 'unknown'}})
            with patch('f1forecast.grid_witness.requests.get', side_effect=AssertionError):
                self.assertEqual(load_witness(GP, work, offline=True), result)

    def test_shared_sanction_keeps_signed_movement_and_pit_lane_infraction_is_not_pit_start(self):
        text = TEXT.replace('[[Zhou Guanyu]] received a three-place grid penalty for impeding',
                            '[[Zhou Guanyu]] and [[George Russell]] both received a one-place grid penalty '
                            'for entering the fast lane in the pit lane before')
        with TemporaryDirectory() as tmp:
            result, _ = fetch(Path(tmp), text)
            for code, signed_loss in (('ZHO', 1), ('RUS', -1)):
                driver = result['drivers'][code]
                self.assertEqual((driver['kind'], driver['grid'] - driver['qual']), ('places', signed_loss))

    def test_fails_closed_on_boundary_wrong_round_duplicate_position_and_corruption(self):
        for changes, reason in (({'stamp': '2024-07-28T12:30:00Z'}, 'invalid_revision'),
                                ({'text': TEXT.replace('Race_No = 14', 'Race_No = 15')}, 'invalid_revision'),
                                ({'text': TEXT.replace('2{{ref|7', '1{{ref|7')}, 'unusable_table'),
                                ({'sha': '0' * 40}, 'cache_invalid')):
            with self.subTest(reason=reason), TemporaryDirectory() as tmp:
                result, _ = fetch(Path(tmp), **changes)
                self.assertTrue(result['reason'].startswith(reason), result)
                self.assertEqual(result['drivers'], {})
        with self.assertRaises(ValueError):
            load_witness({**GP, 'year': 2026}, Path('.'), offline=True)
