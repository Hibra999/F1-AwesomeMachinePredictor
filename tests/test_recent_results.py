import unittest
from unittest.mock import patch

from bs4 import BeautifulSoup

from f1forecast.engine import epoch
from f1forecast.recent_results import classification, fia_documents, forecast, recent_calendar


class RecentPodiumsTest(unittest.TestCase):
    def test_classification_requires_a_race_result_at_the_observation_time(self):
        listing = BeautifulSoup('''<li class="document-row"><a href="/final.pdf">
            <div class="title">Doc 81 - Final Race Classification</div>
            <div class="published">Published on 08.11.26 22:45 CET</div></a></li>''', 'html.parser')
        docs = fia_documents(listing, epoch('2026-11-08T21:44:59Z'))
        self.assertEqual(docs, {})
        self.assertIn('final race classification', fia_documents(listing, epoch('2026-11-08T21:45:00Z')))
        empty = BeautifulSoup('<table><tr><td>No results available</td></tr></table>', 'html.parser')
        self.assertEqual(classification(docs, empty, 'f1', 'fia', None, {}), ('pending', 'fia', []))
        rows = ''.join(f'<tr><td>{i}</td><td>7</td><td><span>Name</span><span>{c}</span></td></tr>'
                       for i, c in enumerate(('ANT', 'NOR', 'VER'), 1))
        page = BeautifulSoup(f'<table><tbody>{rows}</tbody></table>', 'html.parser')
        status, source, podium = classification(docs, page, 'f1', 'fia', None, {})
        self.assertEqual((status, source, [code for code, _ in podium]), ('provisional', 'f1', ['ANT', 'NOR', 'VER']))

    def test_published_t30_wins_and_wrong_cut_fails(self):
        gp = {'race': epoch('2026-11-08T17:00:00Z')}
        issued = {'cutoff_utc': '2026-11-08T16:30:00Z', 'published_utc': '2026-11-08T16:30:01Z'}
        args = (gp, {}, [], None, (issued, ['public feed']), {}, None, {})
        self.assertEqual(forecast(*args), (issued, ['public feed']))
        issued['cutoff_utc'] = '2026-11-08T16:30:01Z'
        with self.assertRaisesRegex(ValueError, 'T−30'):
            forecast(*args)

    def test_january_uses_previous_season_not_future_races(self):
        clock = epoch('2027-01-01T00:00:00Z')
        schedules = [[{'race': clock + 86400}], [{'race': clock - 86400}]]
        previous = BeautifulSoup('<a href="/en/results/2026/races/1/a/race-result">A</a>', 'html.parser')
        with patch('f1forecast.recent_results.get_schedule', side_effect=schedules) as calendar, \
                patch('f1forecast.recent_results.html', return_value=previous):
            candidates = list(recent_calendar(clock, None, False))
        self.assertEqual([call.args[0] for call in calendar.call_args_list], [[2027], [2026]])
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0][0]['race'], clock - 86400)
