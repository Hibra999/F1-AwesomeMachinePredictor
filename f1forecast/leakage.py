"""Regenerate real archived cuts after physically removing or corrupting the future."""

import numpy as np

from .dataset import snapshots
from .engine import asof, utc
from .training import SEED, distribution

ATOL = 1e-12


def verify_leakage(row, records, bundle):
    baseline = list(snapshots(row, records))
    race = [s for s in baseline if s.horizon == "race"]
    if len(race) < 3:
        raise ValueError("need an actual mid-race timing cut")
    fixed = [s for s in baseline if s.horizon != "race"] + [race[len(race) // 2]]
    if len(fixed) != 3:
        raise ValueError("need pre-FP1, T−30 and a mid-race cut of the same GP")
    rng = np.random.default_rng(SEED)
    reports = []
    for snap in fixed:
        past = [e for e in row["events"] if e["at"] <= snap.cutoff]
        future = [e for e in row["events"] if e["at"] > snap.cutoff]
        past_records = [r for r in records if r["at"] <= snap.cutoff]
        future_records = [r for r in records if r["at"] > snap.cutoff]
        if not future:
            raise ValueError("the leakage test must exercise real future events")
        # Arbitrarily reverse final pace and results; poison weather and announced
        # penalties. Every changed row keeps its original availability boundary.
        bad_records = [{**r, "order": list(reversed(r["order"])), "pace": {d: 900 for d in r["order"]}}
                       for r in future_records]
        bad_events = [{"at": e["at"], "kind": "penalty", "source": "poison:test", "driver": snap.ids[0],
                       "seconds": float(rng.uniform(100, 1000))} if i % 2 else
                      {"at": e["at"], "kind": "weather", "source": "poison:test", "air": 100.0,
                       "track_temp": 150.0, "rain": True} for i, e in enumerate(future)]
        p = distribution(bundle, snap)
        deltas = []
        for event_set, history in ((past, past_records), (past + bad_events, past_records + bad_records)):
            rebuilt = asof(row["gp"], history, event_set, snap.horizon, snap.cutoff)
            if rebuilt is None or rebuilt.ids != snap.ids or rebuilt.lap != snap.lap:
                raise AssertionError("the past changed its entrants or cutoff")
            np.testing.assert_allclose(rebuilt.x, snap.x, rtol=0, atol=ATOL, equal_nan=True)
            q = distribution(bundle, rebuilt)
            np.testing.assert_allclose(p, q, rtol=0, atol=ATOL)
            deltas.append(float(np.abs(p - q).max()))
        reports.append({"horizon": snap.horizon, "lap": snap.lap, "cutoff_utc": utc(snap.cutoff),
                        "events_withheld": len(future), "history_rows_withheld": len(future_records),
                        "max_delta_removed": deltas[0], "max_delta_poisoned": deltas[1]})
    return {"event": row["gp"]["id"], "seed": SEED, "absolute_tolerance": ATOL, "tests": reports}
