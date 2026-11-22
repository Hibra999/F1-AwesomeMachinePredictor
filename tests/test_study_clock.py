from pathlib import Path
import unittest
from unittest.mock import patch

from f1forecast import study_clock
from f1forecast.engine import epoch


def race(day, zone):
    return {"event_name": {"7": "Monaco Grand Prix"}, "session5": {"7": "Race"},
            "session5_date": {"7": day}, "gmt_offset": {"7": zone}}


class ScheduleEvidenceTest(unittest.TestCase):
    def test_ignores_revision_that_was_too_late_for_its_own_advertised_cut(self):
        gp = {"id": "2023-06", "year": 2023, "name": "Monaco Grand Prix", "circuit": "monaco",
              "race": epoch("2023-05-28T15:00:00Z")}
        late = epoch("2023-05-28T14:00:00Z")
        versions = [(late + 4000, "after-both"), (late, "too-late-for-new-start"), (late - 86400, "prior")]
        calendars = {"prior": race("2023-05-28T15:00:00", None),
                     "too-late-for-new-start": race("2023-05-28T15:00:00", "+02:00")}
        with patch.object(study_clock, "_versions", return_value=versions), patch.object(
                study_clock, "_calendar", side_effect=lambda _path, _year, sha: calendars[sha]):
            row = study_clock.schedule_asof(gp, Path("."))
        self.assertEqual(row["revision"], "prior")
        self.assertEqual(row["race_utc"], "2023-05-28T13:00:00.000Z")
        self.assertEqual(row["status"], "corrected")
