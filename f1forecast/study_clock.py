"""Recover the advertised start from a FastF1 calendar revision before T−30."""

from datetime import datetime
from functools import cache
import json
from pathlib import Path
import subprocess
from zoneinfo import ZoneInfo

from .engine import utc

REPOSITORY = "https://github.com/theOehrly/f1schedule"
REVISION = "e50468712d66daaaf8d637551798c8ce70891e82"  # December 2025, before the sealed final period.
# The early vintages contain local wall times without UTC offsets.
_ZONES = {
    "albert_park": "Australia/Melbourne", "americas": "America/Chicago", "bahrain": "Asia/Bahrain",
    "baku": "Asia/Baku", "catalunya": "Europe/Madrid", "hungaroring": "Europe/Budapest",
    "imola": "Europe/Rome", "interlagos": "America/Sao_Paulo", "jeddah": "Asia/Riyadh",
    "marina_bay": "Asia/Singapore", "miami": "America/New_York", "monaco": "Europe/Monaco",
    "monza": "Europe/Rome", "red_bull_ring": "Europe/Vienna", "ricard": "Europe/Paris",
    "rodriguez": "America/Mexico_City", "silverstone": "Europe/London", "spa": "Europe/Brussels",
    "suzuka": "Asia/Tokyo", "villeneuve": "America/Toronto", "yas_marina": "Asia/Dubai",
    "zandvoort": "Europe/Amsterdam",
}


def _git(repository, *args):
    try:
        return subprocess.check_output(["git", "-C", str(repository), *args], encoding="utf-8", stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"Need the calendar history at {repository}: git clone {REPOSITORY}.git \"{repository}\"") from exc


@cache
def _versions(repository, year):
    rows = _git(repository, "log", "--first-parent", "--format=%ct %H", REVISION, "--", f"schedule_{year}.json")
    return [(int(at), sha) for at, sha in (line.split() for line in rows.splitlines())]


@cache
def _calendar(repository, year, revision):
    return json.loads(_git(repository, "show", f"{revision}:schedule_{year}.json"))


def _start(calendar, gp):
    expected = "São Paulo Grand Prix" if gp["name"] == "Brazilian Grand Prix" else gp["name"]
    # Identify the named event, not its round: cancellations renumbered Monaco in 2023.
    keys = [k for k, name in calendar["event_name"].items() if name == expected and calendar["session5"][k] == "Race"]
    if len(keys) != 1:
        raise ValueError(f"{gp['id']}: ambiguous or absent event in published schedule")
    key = keys[0]
    local = calendar["session5_date"][key]
    offset = calendar.get("gmt_offset", {}).get(key)
    if not local or not (offset or gp["circuit"] in _ZONES):
        raise ValueError(f"{gp['id']}: unknown scheduled time or timezone")
    if offset:
        start = datetime.fromisoformat(local + offset)
    else:
        start = datetime.fromisoformat(local).replace(tzinfo=ZoneInfo(_ZONES[gp["circuit"]]))
    if abs(start.timestamp() - gp["race"]) > 86400:
        raise ValueError(f"{gp['id']}: schedule identifies a different race weekend")
    return start.timestamp()


def schedule_asof(gp, repository: Path):
    """Use a single advertised schedule known before both the source and corrected cutoff."""
    lower_cut = gp["race"] - 1800
    for at, sha in _versions(repository, gp["year"]):
        if at >= lower_cut:
            continue
        race = _start(_calendar(repository, gp["year"], sha), gp)
        lower_cut = min(lower_cut, race - 1800)
        if at < lower_cut:
            return {"status": "confirmed" if race == gp["race"] else "corrected", "race_utc": utc(race),
                    "revision": sha, "revision_utc": utc(at), "nominal_utc": utc(gp["race"])}
    raise ValueError(f"{gp['id']}: no dated schedule before both T−30 cutoffs")
