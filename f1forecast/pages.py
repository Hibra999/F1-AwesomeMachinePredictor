"""The Pages artifact contains issued forecasts and aggregate analysis, never race feeds."""

import json
from datetime import datetime, timedelta
from math import isclose
from pathlib import Path

from .analysis_data import ANALYSIS_JSON, analysis_data

SITE = Path(__file__).resolve().parents[1] / "site"
UPCOMING = SITE / "data" / "upcoming.json"
FILES = {
    "index.html", "styles.css", "app.js", "data/upcoming.json",
    "analysis.html", "analysis.css", "analysis.js", ANALYSIS_JSON,
    "vendor/plotly-basic.min.js", "vendor/plotly.LICENSE.txt",
}
POSITIONS = ("first", "second", "third")


def _time(value):
    stamp = datetime.fromisoformat(value)
    if stamp.utcoffset() != timedelta(0):
        raise ValueError("forecast times must be UTC")
    return stamp


def _probability(value):
    if type(value) not in (int, float) or not 0 <= value <= 1:
        raise ValueError("expected a probability between 0 and 1")
    return value


def verify_forecast(document):
    if document["mode"] != "forecast" or not document["predictions"]:
        raise ValueError("Pages requires a nonempty published forecast")
    event = document["event"]
    fp1, cutoff, race = (_time(event[k]) for k in ("fp1_utc", "pre_race_utc", "race_utc"))
    if not fp1 < cutoff or race - cutoff != timedelta(minutes=30):
        raise ValueError("inconsistent FP1, T−30 and race schedule")
    seen = set()
    for cut in document["predictions"]:
        horizon = cut["horizon"]
        if (cut["origin"] != "published" or cut["lap"] is not None or horizon in seen
                or horizon not in {"fp1", "pre_race"}):
            raise ValueError("Pages cannot contain reconstructed, live or duplicate cuts")
        seen.add(horizon)
        at, issued = _time(cut["cutoff_utc"]), _time(cut["published_utc"])
        if issued < at or (horizon == "fp1" and issued >= fp1) or (
                horizon == "pre_race" and (at != cutoff or issued >= race)):
            raise ValueError(f"{horizon}: invalid cutoff or publication time")
        drivers = cut["drivers"]
        names = {driver["id"] for driver in drivers}
        podium = cut["podium"]["drivers"]
        if len(names) != len(drivers) or len(podium) != 3 or len(set(podium)) != 3 or not set(podium) <= names:
            raise ValueError("duplicate entrants or invalid ordered podium")
        _probability(cut["podium"]["probability"])
        for k in POSITIONS:
            if not isclose(sum(_probability(d[k]) for d in drivers), 1.0, rel_tol=0, abs_tol=1e-12):
                raise ValueError(f"position {k} must sum to 1")
        for d in drivers:
            if not isclose(_probability(d["podium"]), sum(d[k] for k in POSITIONS), rel_tol=0, abs_tol=1e-12):
                raise ValueError(f"{d['id']}: incoherent podium probability")


def verify_site(root=SITE):
    entries = list(root.rglob("*"))
    if any(p.is_symlink() for p in entries):
        raise ValueError("Pages does not publish symlinks")
    files = {p.relative_to(root).as_posix() for p in entries if p.is_file()}
    if files != FILES:
        raise ValueError(f"unexpected Pages files: {sorted(files - FILES)}; missing: {sorted(FILES - files)}")
    verify_forecast(json.loads((root / "data" / "upcoming.json").read_text(encoding="utf-8")))
    if json.loads((root / ANALYSIS_JSON).read_text(encoding="utf-8")) != analysis_data(root.parent):
        raise ValueError("public analysis does not match the tracked aggregate reports")


if __name__ == "__main__":
    verify_site()
    print("Pages: issued pre-race forecasts and aggregate analysis only")
