import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from f1forecast.__main__ import watch
from f1forecast.engine import epoch, utc


class WatchFinishTest(unittest.TestCase):
    def test_shortened_race_and_terminal_reconnection_close_without_issuing_another_cut(self):
        saved = json.loads(Path('local-panel/las-vegas-2026.json').read_bytes())
        first = saved['predictions'][0]
        at = epoch(first['published_utc']) + 30
        gp = {'id': saved['event']['id'], 'name': 'Las Vegas', 'year': 2026, 'race': at - 3600,
              'fp1': at - 86400, 'scheduled_laps': 50, 'circuit': 'vegas'}
        with TemporaryDirectory() as folder:
            out = Path(folder) / 'live.json'
            args = SimpleNamespace(gp=gp['id'], out=out, seed_feed=None, seconds=None, events=None)
            for phase, initial in [('Finished', False), ('Finalised', True)]:
                with self.subTest(phase=phase):
                    out.write_text(json.dumps({**saved, 'complete': False, 'predictions': [first]}), encoding='utf-8')
                    batch = [{'kind': 'session', 'session': 'Race', 'status': phase, 'at': at,
                              'source': 'clock', 'initial': initial, 'total_laps': 50}]
                    with patch('f1forecast.__main__.lookup', return_value=gp), patch('f1forecast.__main__.get_documents', return_value={}), \
                            patch('f1forecast.live.live_batches', return_value=[batch]) as live:
                        watch(args, Path(folder), None, [])
                        closed = json.loads(out.read_bytes())
                        self.assertTrue(closed['complete'])
                        self.assertEqual(closed['predictions'], [first])
                        self.assertEqual(closed.get('finish_utc'), None if initial else utc(at))
                        before = out.read_bytes()
                        watch(args, Path(folder), None, [])
                        self.assertEqual(out.read_bytes(), before)
                        self.assertEqual(live.call_count, 1)
