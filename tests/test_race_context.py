import unittest

from f1forecast.engine import History, RaceContext, RaceState, epoch, race_snapshots, utc
from f1forecast.live import normalize_packets


def event(at, kind, **fields):
    return {"at": at, "kind": kind, "source": "test-clock", "session": "Race", **fields}


class RaceContextTest(unittest.TestCase):
    def test_archive_prefix_and_episode_counts(self):
        entries = [{"id": d, "team": d} for d in ("AAA", "BBB", "CCC")]
        events = [event(1, "roster", entries=entries), event(2, "track", status="4"),
                  event(3, "track", status="1"),
                  event(10, "session", status="Started"),
                  event(100, "lap", driver="AAA", lap=1), event(105, "lap", driver="BBB", lap=1, seconds=95),
                  event(108, "lap", driver="CCC", lap=1),
                  event(115, "track", status="4", session="Sprint"),
                  *(event(t, "track", status=s) for t, s in enumerate(("4", "4", "2", "4", "1"), 130)),
                  event(190, "lap", driver="AAA", lap=2, seconds=90),
                  event(200, "session", status="Started", total_laps=4),
                  *(event(t, "track", status=s) for t, s in enumerate(("6", "7", "7", "4", "4"), 210)),
                  event(280, "lap", driver="AAA", lap=3, seconds=90),
                  event(282, "lap", driver="BBB", lap=3, seconds=88)]

        def cuts(events):
            context = RaceContext(5)
            state = RaceState({"circuit": "example"}, History(), race_context=context)
            return {s.lap: context.snapshot(s.cutoff, s.ids) for s in race_snapshots(state, events)}

        result = cuts(events)
        self.assertEqual(result[1], {"total_laps": 5, "safety_cars": 0, "virtual_safety_cars": 0,
                                     "partial": False, "markers": {"AAA": 0.0}})
        self.assertEqual(result[2], cuts([e for e in events if e["at"] <= 190])[2])
        self.assertEqual(result[2], {"total_laps": 5, "safety_cars": 1, "virtual_safety_cars": 0,
                                     "partial": False, "markers": {"AAA": 0.0, "BBB": round(85 / 95, 5)}})
        self.assertEqual(result[3], {"total_laps": 4, "safety_cars": 2, "virtual_safety_cars": 1,
                                     "partial": False, "markers": {"AAA": 0.0}})

    def test_live_late_join_missing_pace_and_retirement(self):
        base = epoch("2026-01-01T10:00:00Z")
        gp = {"round": 1, "race": base}
        # Mid-race status and gap are not crossing timestamps, nor evidence of earlier SC deployments.
        initial = {"kind": "snapshot", "received": base + 600, "packet": {
            "SessionInfo": {"Meeting": {"Number": 1}, "Name": "Race", "StartDate": utc(base)},
            "SessionStatus": {"Status": "Started"}, "LapCount": {"CurrentLap": 8, "TotalLaps": 50},
            "DriverList": {"1": {"Tla": "AAA", "FullName": "Test Driver", "TeamName": "Example"}},
            "TimingData": {"Lines": {"1": {"NumberOfLaps": 7, "GapToLeader": "+0.2"}}}}}

        def packet(received, topic, patch, source=None):
            return {"kind": "feed", "received": base + received,
                    "packet": [topic, patch, utc(base + (received - 1 if source is None else source))]}

        def timing(received, patch, **kwargs):
            return packet(received, "TimingData", {"Lines": {"1": patch}}, **kwargs)

        records = [initial, packet(601, "TrackStatus", {"Status": "1"}), timing(610, {"NumberOfLaps": 8}),
                   timing(700, {"NumberOfLaps": 9}), timing(710, {"Stopped": True}),
                   timing(712, {"Stopped": False}), timing(714, {"NumberOfLaps": 10}, source=800),
                   timing(800, {"NumberOfLaps": 11, "LastLapTime": {"Value": "1:30.000"}}),
                   timing(810, {"Retired": True}), timing(850, {"NumberOfLaps": 12})]
        context = RaceContext()
        cuts = {}
        for batch in normalize_packets(records, gp):
            for e in batch:
                context.apply(e)
            cutoff = batch[0]["at"]
            cuts[int(cutoff - base)] = context.snapshot(cutoff, ["AAA"])
        self.assertEqual(cuts[600], {"total_laps": 50, "safety_cars": None, "virtual_safety_cars": None,
                                     "partial": True, "markers": {}})
        self.assertEqual(cuts[601]["safety_cars"], 0)
        self.assertTrue(cuts[601]["partial"])
        self.assertEqual(cuts[700]["markers"], {"AAA": round(1 / 90, 5)})
        self.assertEqual(cuts[800]["markers"], {"AAA": round(1 / 90, 5)})
        for offset in (610, 710, 712, 714, 810, 850):
            self.assertEqual(cuts[offset]["markers"], {}, offset)

    def test_reconnect_keeps_observed_minimum_without_recounting_an_active_car(self):
        prior = {"safety_cars": 2, "virtual_safety_cars": 1, "total_laps": 50, "markers": {"AAA": .7}}
        context = RaceContext(prior=prior)
        context.apply(event(10, "session", status="Started", initial=True, current_lap=22))
        for at, status in ((11, "2"), (12, "4"), (13, "4"), (14, "1"), (15, "4")):
            context.apply(event(at, "track", status=status))
        self.assertEqual(context.snapshot(15, ["AAA"]), {"total_laps": 50, "safety_cars": 3,
                         "virtual_safety_cars": 1, "partial": True, "markers": {}})


if __name__ == "__main__":
    unittest.main()
