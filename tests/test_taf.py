from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from f1forecast.taf import _forecast, load_pilot


DRY = "12011KT P6SM SCT025"
WET = "15008KT 4SM -SHRA BKN020"


class ForecastPeriodTest(unittest.TestCase):
    def wet(self, body, target):
        issue = datetime.fromisoformat("2024-05-31T17:20:00+00:00")
        row = {}
        _forecast("3118/0124 " + body, issue, datetime.fromisoformat(target + "+00:00"), row)
        return row["wet"]

    def test_only_active_groups_at_scheduled_start(self):
        body = (f"{DRY} TEMPO 3118/3120 3SM TSRA FM312000 {DRY} "
                "PROB30 TEMPO 3122/3124 +TSRASN FM010100 " + WET)
        for target, expected in [("2024-05-31T18:00", True), ("2024-05-31T20:00", False),
                                 ("2024-05-31T22:00", True), ("2024-06-01T00:00", False),
                                 ("2024-06-01T01:00", True)]:
            with self.subTest(target=target):
                self.assertIs(self.wet(body, target), expected)
        with self.assertRaisesRegex(ValueError, "not_valid_at_start"):
            self.wet(body, "2024-06-02T00:00")

    def test_becoming_does_not_impute_exact_rain_onset(self):
        body = DRY + " BECMG 3121/3123 -FZDZ BECMG 0101/0103 OVC030"
        self.assertIs(self.wet(body, "2024-05-31T20:00"), False)
        self.assertIsNone(self.wet(body, "2024-05-31T21:30"))
        self.assertIs(self.wet(body, "2024-06-01T02:00"), True)
        self.assertIs(self.wet(WET + " TEMPO 3118/3120 NSW", "2024-05-31T19:00"), True)

    def test_vicinity_and_clouds_do_not_assert_airport_rain(self):
        self.assertIs(self.wet("12011KT P6SM VCTS VCSH BKN025CB", "2024-05-31T19:00"), False)
        with self.assertRaisesRegex(ValueError, "incomplete_group"):
            self.wet("SCT025", "2024-05-31T19:00")
        with self.assertRaisesRegex(ValueError, "unsupported_group_element"):
            self.wet(DRY + " PROBXX 3118/3120 RA", "2024-05-31T19:00")


class PilotEvidenceTest(unittest.TestCase):
    gp = dict(id="2024-06", year=2024, circuit="miami",
              race=datetime(2024, 5, 5, 20, tzinfo=UTC).timestamp())

    @staticmethod
    def bulletin(issue, body, *, wmo=None, modifier="", bbb=""):
        return (f"853\nFTUS42 KMFL 05{wmo or issue}{bbb}\nTAFFLL\nTAF{modifier}\n"
                f"KFLL 05{issue}Z {body}=\n")

    def rows(self, archives=(), gps=None):
        with TemporaryDirectory() as directory, patch("f1forecast.taf.requests.get") as get:
            cache = Path(directory)
            if archives:
                path = cache / "taf" / "TAFFLL_202405051930.zip"
                path.parent.mkdir()
                with ZipFile(path, "w") as z:
                    for stamp, bulletin in archives:
                        z.writestr(f"TAFFLL_20240505{stamp}.txt", bulletin)
            result = load_pilot(cache, gps if gps is not None else [self.gp], offline=True)
            get.assert_not_called()
            return result

    def test_all_three_clocks_precede_cut_and_amd_supersedes(self):
        archives = [("1720", self.bulletin("1720", "0518/0624 " + DRY)),
                    ("1902", self.bulletin("1858", "0519/0624 " + WET,
                                           wmo="1901", modifier=" AMD", bbb=" AAA")),
                    ("1910", self.bulletin("1910", "0519/0624 " + DRY, wmo="1930")),
                    ("1915", self.bulletin("1935", "0519/0624 " + DRY)),
                    ("1931", self.bulletin("1925", "0519/0624 " + DRY))]
        row = self.rows(archives)["2024-06"]
        self.assertIs(row["wet"], True)
        self.assertEqual(row["issued_utc"], "2024-05-05T18:58:00+00:00")
        self.assertEqual(row["wmo_utc"], "2024-05-05T19:01:00+00:00")
        self.assertEqual(row["available_utc"], "2024-05-05T19:02:00+00:00")
        self.assertEqual(row["issuer"], "KMFL")
        self.assertTrue(row["source"].endswith("202405051902-KMFL-FTUS42-TAFFLL-AAA"))

    def test_never_resurrect_old_after_unknown_or_invalid_revision(self):
        old = self.bulletin("1720", "0518/0624 " + WET)
        variants = [(self.bulletin("1859", "0518/0624 CNL"), "cancelled_or_nil"),
                    (self.bulletin("1859", "NIL"), "cancelled_or_nil"),
                    (self.bulletin("1859", "0518/0520 " + DRY), "not_valid_at_start"),
                    (self.bulletin("1859", "0518/0624 " + DRY, modifier=" COR", bbb=" CCA"),
                     "undated_correction"), ("invalid", "unrecognized_bulletin")]
        for new, reason in variants:
            with self.subTest(reason=reason):
                row = self.rows([("1720", old), ("1859", new)])["2024-06"]
                self.assertIsNone(row["wet"])
                self.assertEqual(row["reason"], reason)

    def test_equally_timed_but_different_bulletins_are_unknown(self):
        rows = self.rows([("1805", self.bulletin("1805", "0518/0624 " + DRY)),
                          ("1804", self.bulletin("1804", "0518/0624 " + WET, wmo="1805"))])
        self.assertEqual(rows["2024-06"]["reason"], "ambiguous_product_order")
        self.assertIsNone(rows["2024-06"]["wet"])

    def test_missing_and_outside_scope_stay_unknown_without_network(self):
        gps = [self.gp, {**self.gp, "id": "2024-01", "circuit": "bahrain"},
               {**self.gp, "id": "2026-21", "year": 2026, "circuit": "vegas"}]
        rows = self.rows(gps=gps)
        self.assertEqual([r["station"] for r in rows.values()], ["KFLL", None, "KLAS"])
        self.assertEqual([r["reason"] for r in rows.values()], ["cache_miss", "outside_pilot", "outside_pilot"])
        self.assertTrue(all(r["wet"] is None and r["issued_utc"] is None for r in rows.values()))

    def test_download_pins_raw_response_for_offline_reuse(self):
        data = BytesIO()
        with ZipFile(data, "w") as z:
            z.writestr("TAFFLL_202405051720.txt", self.bulletin("1720", "0518/0624 " + DRY))
        with TemporaryDirectory() as directory, patch("f1forecast.taf.requests.get") as get:
            get.return_value.content = data.getvalue()
            online = load_pilot(Path(directory), [self.gp])
            self.assertIs(online["2024-06"]["wet"], False)
            self.assertEqual(online, load_pilot(Path(directory), [self.gp], offline=True))
            get.assert_called_once()

    def test_wrong_vegas_utc_day_is_not_silently_repaired(self):
        gp = dict(id="2024-22", year=2024, circuit="vegas",
                  race=datetime(2024, 11, 23, 6, tzinfo=UTC).timestamp())
        row = self.rows(gps=[gp])[gp["id"]]
        self.assertEqual(row["reason"], "published_schedule_mismatch")
        self.assertEqual(row["target_utc"], "2024-11-23T06:00:00+00:00")
        self.assertEqual(row["scheduled_utc"], "2024-11-24T06:00:00+00:00")
        self.assertEqual(row["cutoff_utc"], "2024-11-23T05:30:00+00:00")
        self.assertTrue(row["schedule_source"].startswith("https://www.fia.com/"))
        self.assertIsNone(row["wet"])
