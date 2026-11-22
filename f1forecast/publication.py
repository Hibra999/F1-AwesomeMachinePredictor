"""Immutable cuts and atomic writes for forecast and local race feeds."""

from datetime import UTC, datetime
import json
from pathlib import Path

import numpy as np

from .engine import utc
from .probability import summarize
from .training import distribution


def now():
    return datetime.now(UTC).timestamp()


def event_card(gp, distance=None):
    return {"id": gp["id"], "name": f"{gp['name']} {gp['year']}",
            "fp1_utc": utc(gp["fp1"]), "pre_race_utc": utc(gp["race"] - 1800),
            "race_utc": utc(gp["race"]), "scheduled_laps": distance or gp.get("scheduled_laps")}


def new_feed(gp, mode, source, distance=None):
    if mode not in {"forecast", "replay", "live"}:
        raise ValueError(f"unknown publication mode: {mode}")
    return {"event": event_card(gp, distance), "mode": mode, "source": source, "updated_utc": utc(now()),
            "complete": False, "predictions": []}


def prediction(bundle, snap, published=None):
    mass = distribution(bundle, snap)
    x, top, top_p = summarize(mass, len(snap.drivers))
    np.testing.assert_allclose(x.sum(axis=0), 1, atol=1e-12, rtol=0)
    if (x < 0).any() or (x.sum(axis=1) > 1 + 1e-12).any() or len(set(top)) != 3:
        raise ValueError("invalid exact podium distribution")
    return {"horizon": snap.horizon, "origin": "published" if published is not None else "backtest",
            "cutoff_utc": utc(snap.cutoff), "published_utc": utc(published) if published is not None else None,
            "lap": snap.lap, "model_version": bundle["version"],
            "method": bundle["horizons"][snap.horizon]["selected"], "reference": snap.reference_name,
            "podium": {"drivers": [snap.drivers[i]["id"] for i in top], "probability": float(top_p)},
            "drivers": [{"id": d["id"], "name": d["name"], "team": d["team"].replace("_", " ").title(),
                         "first": float(v[0]), "second": float(v[1]), "third": float(v[2]),
                         "podium": float(v.sum())} for d, v in zip(snap.drivers, x)]}


def read_file(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_file(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n", encoding="utf-8")
    temp.replace(path)


def append_cut(feed, cut):
    key = (cut["horizon"], cut["lap"])
    if any((p["horizon"], p["lap"]) == key for p in feed["predictions"]):
        raise ValueError(f"{key} already frozen for {feed['event']['id']}")
    feed["predictions"].append(cut)
    feed["predictions"].sort(key=lambda p: p["cutoff_utc"])
    feed["updated_utc"] = utc(now())
