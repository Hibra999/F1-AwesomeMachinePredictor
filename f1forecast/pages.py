"""The Pages artifact contains issued forecasts and aggregate analysis, never race feeds."""

import json
from datetime import date, timedelta
from math import log
from pathlib import Path
import re
import unicodedata
from urllib.parse import unquote, urlsplit

from .analysis_data import ANALYSIS_JSON, RECENT_JSON, analysis_data
from .race_evaluation import distribution, driver_id, probability, utc_time

SITE = Path(__file__).resolve().parents[1] / "site"
UPCOMING = SITE / "data" / "upcoming.json"
FILES = {
    "index.html", "styles.css", "app.js", "data/upcoming.json",
    "analysis.html", "analysis.css", "analysis.js", "insights.js", ANALYSIS_JSON, RECENT_JSON,
    "vendor/plotly-basic.min.js", "vendor/plotly.LICENSE.txt",
}


def _url(value):
    if not isinstance(value, str):
        raise ValueError("expected a public HTTPS URL")
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise ValueError("expected a public HTTPS URL")
    return parts


def _slug(text):
    ascii_text = unicodedata.normalize("NFKD", text.casefold()).encode("ascii", "ignore").decode()
    return re.sub("[^a-z0-9]", "", ascii_text)


def verify_forecast(document):
    base = {"event", "mode", "source", "updated_utc", "complete", "predictions"}
    if (not base <= set(document) <= base | {"entries_status", "evidence_urls"}
            or document["mode"] != "forecast" or document["complete"] is not False
            or not isinstance(document["predictions"], list) or not document["predictions"]
            or not isinstance(document["source"], str) or not document["source"].strip()):
        raise ValueError("Pages requires a nonempty published forecast")
    event = document["event"]
    if (set(event) != {"id", "name", "fp1_utc", "pre_race_utc", "race_utc", "scheduled_laps"}
            or not isinstance(event["id"], str) or not re.fullmatch(r"\d{4}-\d{2}", event["id"])
            or not isinstance(event["name"], str) or not event["name"]
            or event["scheduled_laps"] is not None and (type(event["scheduled_laps"]) is not int or event["scheduled_laps"] < 1)):
        raise ValueError("invalid public forecast event")
    if not isinstance(document.get("evidence_urls", []), list) or not isinstance(document.get("entries_status", ""), str):
        raise ValueError("invalid forecast provenance")
    for url in document.get("evidence_urls", []):
        _url(url)
    updated = utc_time(document["updated_utc"])
    fp1, cutoff, race = (utc_time(event[k]) for k in ("fp1_utc", "pre_race_utc", "race_utc"))
    if not fp1 < cutoff or race - cutoff != timedelta(minutes=30):
        raise ValueError("inconsistent FP1, T−30 and race schedule")
    seen = set()
    for cut in document["predictions"]:
        horizon = cut["horizon"]
        if (cut["origin"] != "published" or cut["lap"] is not None or horizon in seen
                or horizon not in {"fp1", "pre_race"} or "race_context" in cut):
            raise ValueError("Pages cannot contain reconstructed, live or duplicate cuts")
        seen.add(horizon)
        at, issued = (utc_time(cut[k]) for k in ("cutoff_utc", "published_utc"))
        if issued < at or issued > updated or (horizon == "fp1" and issued >= fp1) or (
                horizon == "pre_race" and (at != cutoff or issued > cutoff + timedelta(seconds=120))):
            raise ValueError(f"{horizon}: invalid cutoff or publication time")
        distribution(cut)


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
    recent = json.loads((root / RECENT_JSON).read_text(encoding="utf-8"))
    if recent != json.loads((root.parent / "reports" / "recent-podiums.json").read_text(encoding="utf-8")):
        raise ValueError("public podium comparisons do not match the tracked report")
    verify_recent(recent)


def verify_recent(recent):
    if (not isinstance(recent, dict) or set(recent) != {"races", "pending", "as_of_utc"}
            or not isinstance(recent["races"], list) or len(recent["races"]) != 2
            or not isinstance(recent["pending"], list)):
        raise ValueError("expected a retrospective of two recent races")
    observation = utc_time(recent["as_of_utc"])
    public_fields = {"gp", "name", "date", "official", "forecast", "source_url", "result_url", "forecast_urls",
                     "status", "cutoff_utc", "origin", "model", "published_utc", "names", "classification_utc",
                     "race_evaluation"}
    seen, result_pages = set(), set()
    for race in [*recent["pending"], *recent["races"]]:
        if set(race) != public_fields or race["status"] not in {"final", "provisional", "pending"}:
            raise ValueError("unexpected data in the public podium comparison")
        gp = race["gp"]
        if (not isinstance(gp, str) or not re.fullmatch(r"\d{4}-\d{2}", gp) or gp in seen
                or not isinstance(race["name"], str) or not race["name"].strip()
                or not isinstance(race["date"], str) or date.fromisoformat(race["date"]).year != int(gp[:4])
                or (race in recent["pending"]) != (race["status"] == "pending")):
            raise ValueError("invalid retrospective race frame")
        seen.add(gp)
        cutoff = utc_time(race["cutoff_utc"])
        start = cutoff + timedelta(minutes=30)
        if start >= observation or abs((date.fromisoformat(race["date"]) - start.date()).days) > 1:
            raise ValueError("invalid retrospective race time")
        for podium in (race["official"], race["forecast"]):
            if podium is not None and (not isinstance(podium, list) or len(podium) != 3
                    or not all(driver_id(code) for code in podium) or len(set(podium)) != 3):
                raise ValueError("invalid retrospective podium")
        if (race["official"] is None) != (race["status"] == "pending"):
            raise ValueError("inconsistent classification and podium")
        if (not isinstance(race["forecast_urls"], list) or not isinstance(race["names"], dict)
                or set(race["names"]) != set((race["forecast"] or []) + (race["official"] or []))
                or not all(isinstance(name, str) and name.strip() for name in race["names"].values())):
            raise ValueError("invalid retrospective driver names or evidence")
        if race["forecast"] is None:
            if any(race[k] is not None for k in ("origin", "model", "published_utc")) or race["forecast_urls"]:
                raise ValueError("provenance without a forecast")
        elif (race["origin"] not in {"published", "backtest"} or not isinstance(race["model"], str)
              or not race["model"].strip() or (race["origin"] == "published") != (race["published_utc"] is not None)
              or race["origin"] == "backtest" and not race["forecast_urls"]):
            raise ValueError("inconsistent retrospective forecast provenance")
        if race["published_utc"] is not None and not cutoff <= utc_time(race["published_utc"]) <= cutoff + timedelta(seconds=120):
            raise ValueError("retrospective T−30 publication outside its window")
        for url in race["forecast_urls"]:
            _url(url)
        result, source = _url(race["result_url"]), _url(race["source_url"])
        if (race["result_url"] in result_pages or result.netloc != "www.formula1.com"
                or not re.fullmatch(rf"/en/results/{gp[:4]}/races/\d+/[^/]+/race-result", result.path)):
            raise ValueError("expected official Formula 1 result URL")
        result_pages.add(race["result_url"])
        stamp = race["classification_utc"]
        if race["status"] == "final" or stamp is not None:
            filename = unquote(source.path).rsplit("/", 1)[-1]
            if (source.netloc not in {"www.fia.com", "api.fia.com"} or not filename.lower().endswith(".pdf")
                    or _slug(gp[:4] + race["name"]) not in _slug(filename)
                    or race["status"] + "raceclassification" not in _slug(filename)):
                raise ValueError("classification requires a matching FIA decision")
        elif source.netloc not in {"www.fia.com", "api.fia.com", "www.formula1.com"}:
            raise ValueError("unrecognized classification source")
        if stamp is not None and (race["status"] == "pending" or not start < utc_time(stamp) <= observation):
            raise ValueError("classification outside observation frame")
        ev = race["race_evaluation"]
        if ev is None:
            continue
        counts = {"cuts", "first_lap", "last_lap", "excluded_finish", "winner_hits", "exact_hits"}
        if (race["status"] != "final" or stamp is None or not isinstance(ev, dict)
                or set(ev) != counts | {"brier_podium", "winner_log_loss", "model"}
                or any(type(ev[k]) is not int for k in counts)
                or ev["first_lap"] < 1 or not 1 <= ev["cuts"] <= ev["last_lap"] - ev["first_lap"] + 1
                or (ev["cuts"] == 1) != (ev["first_lap"] == ev["last_lap"])
                or ev["excluded_finish"] < 0 or not 0 <= ev["winner_hits"] <= ev["cuts"]
                or not 0 <= ev["exact_hits"] <= ev["cuts"] or not isinstance(ev["model"], str) or not ev["model"].strip()
                or type(ev["winner_log_loss"]) not in (int, float) or not 0 <= ev["winner_log_loss"] <= -log(1e-15) + 1e-12):
            raise ValueError("invalid race evaluation or provenance")
        probability(ev["brier_podium"])


if __name__ == "__main__":
    verify_site()
    print("Pages: issued pre-race forecasts and aggregate analysis only")
