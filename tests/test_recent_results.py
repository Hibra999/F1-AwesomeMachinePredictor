from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import requests

from bs4 import BeautifulSoup

from f1forecast.engine import epoch
from f1forecast.recent_results import classification, fia_documents, forecast, produce, recent_calendar


class RecentPodiumsTest(unittest.TestCase):
    def test_classification_requires_a_race_result_at_the_observation_time(self):
        listing = BeautifulSoup('''<li class="document-row"><a href="/final.pdf">
            <div class="title">Doc 81 - Final Race Classification</div>
            <div class="published">Published on 08.11.26 22:45 CET</div></a></li>''', 'html.parser')
        docs = fia_documents(listing, epoch('2026-11-08T21:44:59Z'))
        self.assertEqual(docs, {})
        self.assertIn('final race classification', fia_documents(listing, epoch('2026-11-08T21:45:00Z')))
        empty = BeautifulSoup('<table><tr><td>No results available</td></tr></table>', 'html.parser')
        gp = {'year': 2026, 'name': 'São Paulo Grand Prix', 'race': epoch('2026-11-08T17:00:00Z')}
        args = 'f1', 'fia', None, gp, date(2026, 11, 8)
        self.assertEqual(classification(docs, empty, *args)[:3], ('pending', 'fia', []))
        rows = ''.join(f'<tr><td>{i}</td><td>{i}</td><td><span>Name</span><span>{c}</span></td></tr>'
                       for i, c in enumerate(('ANT', 'NOR', 'VER'), 1))
        page = BeautifulSoup(f'<table><tbody>{rows}</tbody></table>', 'html.parser')
        status, source, podium, stamp, _, _ = classification(docs, page, *args)
        self.assertIsNone(stamp)
        self.assertEqual((status, source, [code for code, _ in podium]), ('provisional', 'f1', ['ANT', 'NOR', 'VER']))

        pdf = Mock()
        pdf.pages = [Mock(), Mock()]
        pdf.pages[0].extract_text.return_value = ('2026 São Paulo Grand Prix 06 - 08 November 2026 '
            'Document 82 Date 08 November 2026 Time 18:45 Title Final Race Classification')
        url = 'https://www.fia.com/system/files/decision-document/2026_sao_paulo_grand_prix_-_final_race_classification.pdf'
        docs = {'final race classification': (epoch('2026-11-08T21:45:00Z'), url, 81)}
        with patch('f1forecast.recent_results._pdf', return_value=(pdf, {})):
            with self.assertRaisesRegex(ValueError, 'cover'):
                classification(docs, page, *args)
        with patch('f1forecast.recent_results._pdf', side_effect=requests.ConnectionError('FIA unavailable')):
            status, source, _, stamp, _, _ = classification(docs, page, *args)
        self.assertEqual((status, source, stamp), ('provisional', 'f1', None))

    def test_final_uses_fia_listing_clock_and_all_f1_participants(self):
        gp = {'year': 2026, 'name': 'Las Vegas Grand Prix', 'race': epoch('2026-11-22T04:00:00Z')}
        url = 'https://www.fia.com/system/files/decision-document/2026_las_vegas_grand_prix_-_final_race_classification.pdf'
        listing = BeautifulSoup(f'''<li class="document-row"><a href="{url}"><div class="title">
            Doc 65 - Final Race Classification</div><div class="published">Published on 22.11.26 09:20 CET</div>
            </a></li>''', 'html.parser')
        docs = fia_documents(listing, epoch('2026-11-22T08:20:00Z'))
        headers = ''.join(f'<th>{v}</th>' for v in ('Pos.', 'No.', 'Driver', 'Team', 'Laps'))
        rows = ''.join(f'<tr><td>{rank}</td><td>{i}</td><td>Name<span>{code}</span></td><td>T</td><td>50</td></tr>'
                       for i, (rank, code) in enumerate(zip((1, 2, 3, 'NC'), ('VER', 'NOR', 'PIA', 'ALB')), 1))
        page = BeautifulSoup(f'<table><thead><tr>{headers}</tr></thead><tbody>{rows}</tbody></table>', 'html.parser')
        pdf = Mock()
        pdf.pages = [Mock(), Mock()]
        pdf.pages[0].extract_text.return_value = ('2026 Las Vegas Grand Prix 19 - 21 November 2026 '
            'Document 65 Date 22 November 2026 Time 00:20 Title Final Race Classification')
        pdf.pages[1].extract_text.return_value = '\n'.join([
            'Doc 65 Time 00:20', '1  1  Name  Team', '2  2  Name  Team', '3  3  Name  Team', '4  Name  Team'])
        with patch('f1forecast.recent_results._pdf', return_value=(pdf, {})):
            status, source, _, stamp, frame, distance = classification(docs, page, 'f1', 'fia', None, gp, date(2026, 11, 21))
        self.assertEqual((status, source, stamp, frame, distance),
                         ('final', url, '2026-11-22T08:20:00.000Z', ['VER', 'NOR', 'PIA', 'ALB'], 50))

    def test_issued_only_preserves_verified_rows_and_clock_during_outage(self):
        rows = [dict(gp=f'2025-{n:02}', date='2025-01-01', status='final', official=['AAA', 'BBB', 'CCC'],
                     forecast=['CCC', 'AAA', 'BBB'], names={c: c for c in ('AAA', 'BBB', 'CCC')},
                     cutoff_utc='2025-01-01T11:30:00Z', name='Test Grand Prix', forecast_urls=['https://test.test'],
                     source_url='https://www.fia.com/final.pdf', result_url='https://www.formula1.com/result',
                     model='test', origin='backtest', published_utc=None, race_evaluation=None,
                     classification_utc='2025-01-01T15:00:00Z') for n in (2, 1)]
        previous = dict(races=rows, pending=[], as_of_utc='2025-01-01T18:00:00Z')
        schedule = [(dict(id=r['gp'], race=epoch('2025-01-01T12:00:00Z'), name=r['name'], year=2025),
                     r['result_url'], None) for r in rows]
        provisional = 'provisional', rows[0]['result_url'], [('AAA', 'A'), ('BBB', 'B'), ('CCC', 'C')], None, [], None
        with TemporaryDirectory() as d, patch('f1forecast.recent_results.recent_calendar', return_value=schedule), \
                patch('f1forecast.recent_results.sources', side_effect=[
                    (date(2025, 1, 1), {}, provisional), requests.ConnectionError('down')]), \
                patch('f1forecast.recent_results.history', return_value=[]), \
                patch('f1forecast.recent_results.sealed_model', side_effect=AssertionError('private model loaded')), \
                patch('f1forecast.recent_results.load_corpus', side_effect=AssertionError('private corpus loaded')):
            report = produce(Path(d), epoch('2025-01-01T19:00:00Z'), True, issued_only=True, previous=previous)
        self.assertEqual(report, previous)

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
