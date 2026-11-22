import unittest
from datetime import datetime
from unittest.mock import patch

from f1forecast.grid_decisions import _ledger, load_decisions


def event(gp_id, cutoff):
    return {"id": gp_id, "race": datetime.fromisoformat(cutoff).timestamp() + 1800}


class GridDecisionsTests(unittest.TestCase):
    def test_primary_ruling_and_conservative_summer_clock(self):
        gp = event("2024-14", "2024-07-28T12:30:00Z")
        source = load_decisions(gp)
        rows = {row["driver"]: row for row in source["decisions"]}
        self.assertEqual(source["reason"], "ok")
        self.assertEqual((rows["VER"]["document"], rows["VER"]["places"]), (13, 10))
        self.assertEqual(rows["VER"]["available_utc"], "2024-07-26T13:37:00Z")
        self.assertEqual(rows["TSU"]["kind"], "back")

    def test_qatar_same_title_different_session(self):
        source = load_decisions(event("2025-23", "2025-11-30T15:30:00Z"))
        self.assertEqual([(r["driver"], r["kind"], r["document"]) for r in source["decisions"]],
                         [("COL", "pit", 55)])  # Document 32 applied to the Sprint.

    def test_partial_coverage_excludes_identified_drivers(self):
        source = load_decisions(event("2024-15", "2024-08-25T12:30:00Z"))
        self.assertEqual(source["reason"], "partial_drivers")
        self.assertEqual(set(source["unresolved_drivers"]), {"SAR", "ALB"})
        self.assertEqual({r["driver"] for r in source["decisions"]}, {"HAM", "MAG"})

    def test_late_ruling_or_unresolved_case_cannot_erase_a_known_sanction(self):
        cutoff = "2024-07-28T12:30:00Z"
        gp = event("2024-14", cutoff)
        original = _ledger()[gp["id"]]
        prefix = load_decisions(gp)
        record = {**original, "decisions": [*original["decisions"], {"available_utc": cutoff, "driver": "VER", "kind": "pit"}],
                  "reason": "partial_drivers", "unresolved": [{"available_utc": "2024-07-28T18:00:00Z", "driver": "VER"}]}
        with patch("f1forecast.grid_decisions._ledger", return_value={gp["id"]: record}):
            self.assertEqual(load_decisions(gp), prefix)
            gp["race"] += 3600
            self.assertEqual(load_decisions(gp)["reason"], "cutoff_not_reviewed")
        self.assertEqual(load_decisions({"id": "outside"})["decisions"], [])
