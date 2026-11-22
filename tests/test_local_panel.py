import json
from contextlib import contextmanager
from pathlib import Path
from subprocess import DEVNULL, PIPE, Popen
import sys
from tempfile import TemporaryDirectory
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen


@contextmanager
def panel(work, *args):
    with Popen([sys.executable, "-m", "f1forecast", "--work", str(work), "panel", "--port", "0", *args],
               stdout=PIPE, stderr=DEVNULL, text=True) as process:
        try:
            url = process.stdout.readline().strip().split()[-1]
            yield url
        finally:
            process.terminate()


def document(url):
    with urlopen(url, timeout=5) as response:
        return json.load(response)


class LocalPanelTest(unittest.TestCase):
    def test_capture_and_backtest_stay_separate_until_a_new_watcher_publishes(self):
        with TemporaryDirectory() as directory, panel(directory) as url:
            live = document(url + "data/live.json")
            self.assertEqual((live["mode"], live["event"]["id"], live["complete"]), ("live", "2026-21", True))
            self.assertEqual({cut["origin"] for cut in live["predictions"]}, {"published"})
            replay = document(url + "data/replay.json")
            self.assertEqual((replay["mode"], replay["event"]["id"]), ("replay", "2026-20"))
            self.assertEqual({cut["origin"] for cut in replay["predictions"]}, {"backtest"})
            live.update(event={"id": "next-race"}, complete=False, predictions=[])
            (Path(directory) / "live.json").write_text(json.dumps(live), encoding="utf-8")
            self.assertEqual(document(url + "data/live.json")["event"]["id"], "next-race")

    def test_missing_explicit_watch_does_not_substitute_another_race(self):
        with TemporaryDirectory() as directory, panel(directory, "--live", str(Path(directory) / "next.json")) as url:
            with self.assertRaises(HTTPError) as error:
                document(url + "data/live.json")
            self.assertEqual(error.exception.code, 404)
            error.exception.close()
