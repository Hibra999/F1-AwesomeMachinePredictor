import json
from pathlib import Path
from shutil import copytree
from tempfile import TemporaryDirectory
import unittest

from f1forecast.analysis_data import ANALYSIS_JSON, ROOT
from f1forecast.pages import verify_site


class PagesAnalysisTest(unittest.TestCase):
    def test_analysis_cannot_publish_data_outside_the_aggregate_export(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            copytree(ROOT / "site", root / "site")
            copytree(ROOT / "reports", root / "reports")
            verify_site(root / "site")

            path = root / "site" / ANALYSIS_JSON
            report = json.loads(path.read_text(encoding="utf-8"))
            report["race_feed"] = {"mode": "live", "predictions": []}
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "does not match the tracked aggregate reports"):
                verify_site(root / "site")
