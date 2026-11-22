import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from f1forecast.__main__ import freeze


class PreRacePublicationTest(unittest.TestCase):
    def test_race_start_before_the_advertised_cut_aborts_without_replacing_issued_file(self):
        gp = {"id": "2024-21", "race": 10_000}
        cut = gp["race"] - 1800
        with TemporaryDirectory() as folder:
            work = Path(folder)
            issued = work / "issued.json"
            issued.write_bytes(b"existing issued forecast")
            events = work / "events.jsonl"
            events.write_text(json.dumps({"at": cut - 1, "kind": "session", "session": "Race", "status": "Started"}),
                              encoding="utf-8")
            args = SimpleNamespace(gp=gp["id"], horizon="pre_race", events=events, out=issued)
            with patch("f1forecast.__main__.lookup", return_value=gp), patch("f1forecast.__main__.now", return_value=cut), \
                    patch("f1forecast.__main__.get_documents", return_value={}):
                with self.assertRaisesRegex(ValueError, "observed Race start"):
                    freeze(args, work, None, [])
            self.assertEqual(issued.read_bytes(), b"existing issued forecast")
