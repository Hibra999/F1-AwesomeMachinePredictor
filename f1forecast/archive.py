"""Normalize the pinned TracingInsights exports as a historical timing proxy."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import quote

import requests


_REVISIONS = {
    2022: "df396d091c4a40a3a6b733631da3497f8c91d89f",
    2023: "737c84d3ae14b9883a671ca248d015b69cf1cb58",
    2024: "d1543a71370d9b617b627319f8ad80b2f85dfd5e",
    2025: "dfb3ea3f50b69857fe8e40cabe5e899743c59f6a",
    2026: "0894cac61e37c98e8846378b7f07a3cc9be41c5b",
}
_ALIASES = {
    "Brazilian Grand Prix": "São Paulo Grand Prix",
    "Bahrain Grand Prix in Malaysia": "Bahrain Grand Prix",
}
_TIMING = "session_laptimes.json"
_INFO = "session_info.json"
_WEATHER = "weather.json"
_DRIVERS = "drivers.json"
_CONTROL = "rcm.json"
_DRIVER = re.compile(r"[A-Z]{3}")
_PENALTY = re.compile(
    r"FIA STEWARDS:\s+(\d+(?:\.\d+)?)\s*[- ]?SECONDS? TIME PENALTY FOR "
    r"CAR (\d+) \(([A-Z]{3})\)\s+-\s+\S.*"
)
_PACE_NOTE = (
    "seconds = sesT - lST, a reconstructed crossing-to-crossing interval, not a "
    "published lap time. Race/Sprint lap 1, pit-in/out laps and intervals crossing "
    "a session interruption have no pace value. Stint and tyre age are unknown; "
    "laps_since_pit counts observed full laps after a timestamped pit-in/out cycle "
    "and does not prove a tyre change."
)
_LATENCY_NOTE = (
    "UTC lap clock is t0_date + sesT. FastF1 may align crossing clocks using later "
    "timing data; archived clocks approximate event time, not first packet arrival. "
    "First-publication latency and exact as-of replay cannot be verified. "
    "Weather/track use their own stream offsets, and penalties use message UTC."
)


class ArchiveUnavailable(RuntimeError):
    """A required pinned artifact is absent or unusable."""


def _number(value):
    return float(value) if type(value) in (float, int) and math.isfinite(value) else None


def _offset(value):
    value = _number(value)
    return value if value is not None and value >= 0 else None


def _utc(value):
    if not isinstance(value, str) or "T" not in value:
        return None
    try:
        date = datetime.fromisoformat(value)
        # FastF1 emits naive ISO values for documented UTC clock fields only.
        return (date if date.tzinfo else date.replace(tzinfo=timezone.utc)).timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def _columns(data, *names):
    if not isinstance(data, dict):
        raise ValueError("expected column-oriented JSON object")
    columns = [data.get(name) for name in names]
    if not all(isinstance(col, list) for col in columns):
        raise ValueError(f"missing archive columns {', '.join(names)}")
    return zip(*columns, strict=True)


def _fetch(url, path):
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        with requests.Session() as http:
            # Public archive only; do not pick up .netrc credentials.
            http.trust_env = False
            response = http.get(url, timeout=(10, 30))
            if response.status_code == 404:
                return None
            response.raise_for_status()
            raw = response.content
        data = json.loads(raw)
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
        try:
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return data
    return json.loads(raw)


def _entries(data):
    entries = {}
    numbers = {}
    for row in data["drivers"]:
        driver, number = row.get("driver"), str(row.get("dn", ""))
        if not isinstance(driver, str) or not _DRIVER.fullmatch(driver) or not number.isdecimal():
            continue
        name = " ".join(part for key in ("fn", "ln") if isinstance(part := row.get(key), str))
        team = row.get("team")
        if not name.strip() or not isinstance(team, str) or not team.strip():
            continue
        number = int(number)
        entry = {"id": driver, "name": name, "team": team, "number": number}
        if (driver in entries and entries[driver] != entry) or (
            number in numbers and numbers[number] != driver
        ):
            raise ValueError(f"conflicting session identity for {driver}/{number}")
        entries[driver] = entry
        numbers[number] = driver
    if not entries:
        raise ValueError("no corroborated Race entries")
    return [entries[driver] for driver in sorted(entries)], numbers


def _signals(info):
    rows = info.get("session_status")
    if not rows:
        return []
    signals = [(offset, status) for value, status in _columns(rows, "Time", "Status")
               if (offset := _offset(value)) is not None and isinstance(status, str)]
    signals.sort(key=lambda signal: signal[0])
    return signals


def _running_windows(signals):
    """Started, timed cutoff, last permissible crossing, segment for each run."""
    windows = []
    start = None
    segment = 1
    for index, (at, status) in enumerate(signals):
        if status == "Started":
            start = at
        elif status == "Aborted":
            if start is not None:
                windows.append((start, at, at, segment))
            start = None
        elif status == "Finished":
            if start is not None:
                # A lap already begun before the chequered flag can finish during rundown.
                end = next((clock for clock, state in signals[index + 1:]
                            if state in {"Inactive", "Started", "Finalised", "Ends"}), at)
                windows.append((start, at, end, segment))
            start = None
            segment += 1
    if start is not None:
        # Incomplete exports may still provide observed laps during an open run.
        windows.append((start, math.inf, math.inf, segment))
    return windows


def _timing_events(data, session, signals, emit):
    windows = _running_windows(signals)
    finished = next((at for at, status in reversed(signals) if status == "Finished"), None)
    finalised = next((at for at, status in signals
                      if status == "Finalised" and finished is not None and at >= finished), None)
    stops = {}
    progress = {}
    rows = []
    for driver, lap, end, start, pin, pout, generated, s1, s2, s3 in _columns(
        data, "drv", "lap", "sesT", "lST", "pin", "pout", "ff1G", "s1T", "s2T", "s3T"
    ):
        # The flag is only an exclusion filter: do not replay synthesized laps.
        if generated is not False or not isinstance(driver, str) or not _DRIVER.fullmatch(driver):
            continue
        pin, pout = _offset(pin), _offset(pout)
        if pin is not None or pout is not None:
            transitions = stops.setdefault(driver, set())
            if pin is not None:
                transitions.add((pin, True))
            if pout is not None:
                transitions.add((pout, False))
        end = _offset(end)
        if end is not None and type(lap) is int and lap > 0:
            rows.append((end, driver, lap, _offset(start), pin, pout, s1, s2, s3))

    first_start = next((at for at, status in signals if status == "Started"), None)
    pit_clocks = {}
    for driver, transitions in stops.items():
        inside = False
        exits = []
        for at, entry in sorted(transitions):
            if first_start is None or at < first_start:
                continue
            if entry:
                emit(_TIMING, at, "pit", driver=driver)
                inside = True
            elif inside:
                exits.append(at)
                inside = False
        pit_clocks[driver] = iter(exits)

    seen = set()
    last_exit = {}
    completed = Counter()
    upcoming = {driver: next(exits, None) for driver, exits in pit_clocks.items()}
    for end, driver, lap, start, pin, pout, s1, s2, s3 in sorted(rows, key=lambda row: row[0]):
        if (driver, lap) in seen or first_start is None or end <= first_start:
            continue
        seen.add((driver, lap))
        segment = next((part for opened, cutoff, closed, part in windows
                        if start is not None and opened <= start <= cutoff and start < end <= closed), None)
        seconds = None
        if segment is not None and pin is None and pout is None and (
            lap != 1 or session not in {"Race", "Sprint"}
        ):
            seconds = round(end - start, 3)
        if session == "Qualifying":
            # Require three ordered sector passages; never read deleted, PB or official time.
            a, b, c = map(_offset, (s1, s2, s3))
            if (seconds is not None and a is not None and b is not None and c is not None
                    and start < a < b < min(c, end)):
                emit(_TIMING, end, "qualifying", driver=driver, seconds=seconds, segment=segment)
            continue

        while (exit_at := upcoming.get(driver)) is not None and exit_at <= end:
            last_exit[driver] = exit_at
            completed[driver] = 0
            upcoming[driver] = next(pit_clocks[driver], None)
        pit_exit = last_exit.get(driver)
        if pit_exit is not None and seconds is not None and pit_exit <= start:
            completed[driver] += 1
        emit(_TIMING, end, "lap", driver=driver, lap=lap, seconds=seconds,
             stint=None, tyre_age=None, laps_since_pit=completed[driver] if pit_exit is not None else None)
        if session == "Sprint" and finalised is not None and end <= finalised:
            candidate = (-lap, end, driver)
            if driver not in progress or candidate < progress[driver]:
                progress[driver] = candidate

    if session != "Sprint":
        return None
    if finalised is None:
        return "Sprint order unavailable: missing Finished/Finalised timing markers."
    if not progress:
        return "Sprint order unavailable: no observed crossing before Finalised."
    # Wait until Finalised: earlier publication would retrospectively know there were no more crossings.
    for position, (_, _, driver) in enumerate(sorted(progress.values()), 1):
        emit(_TIMING, finalised, "sprint", driver=driver, position=position)
    return None


def _normalise_session(session, files, urls, numbers, events, warnings):
    info = files.get(_INFO)
    if not info:
        return
    origin = _utc(info.get("t0_date"))
    if origin is None:
        if session == "Race":
            raise ValueError("Race has no valid UTC t0_date")
        warnings.append(f"{session} skipped: missing UTC t0_date.")
        return

    def emit(filename, offset, kind, **fields):
        events.append({"at": origin + offset, "kind": kind, "source": urls[filename],
                       "session": session, **fields})

    signals = _signals(info)
    if not signals or not any(status == "Started" for _, status in signals):
        if session == "Race":
            raise ValueError("Race has no timestamped Started marker")
        warnings.append(f"{session} has no timestamped Started marker; no laps replayed.")
    for at, status in signals:
        if status in {"Started", "Aborted", "Finished"}:
            emit(_INFO, at, "session", status=status)
    if track := info.get("track_status"):
        for at, status in _columns(track, "Time", "Status"):
            if (at := _offset(at)) is not None and status in {"1", "2", "3", "4", "5", "6", "7"}:
                emit(_INFO, at, "track", status=status)
    else:
        warnings.append(f"{session} has no timestamped track status.")
    if data := files.get(_WEATHER):
        for at, rain, air, track in _columns(data, "wT", "wR", "wAT", "wTT"):
            at, air, track = _offset(at), _number(air), _number(track)
            if type(rain) is bool and at is not None and air is not None and track is not None:
                emit(_WEATHER, at, "weather", rain=rain, air=air, track_temp=track)
    if data := files.get(_TIMING):
        if warning := _timing_events(data, session, signals, emit):
            warnings.append(warning)
    if session == "Race" and (data := files.get(_CONTROL)):
        close = max((at for at, _ in signals), default=0.0)
        seen = set()
        for raw_at, message in _columns(data, "time", "msg"):
            match = _PENALTY.fullmatch(message) if isinstance(message, str) else None
            if match is None or (at := _utc(raw_at)) is None or not origin <= at <= origin + close:
                continue
            raw_seconds, number, driver = match.groups()
            seconds = float(raw_seconds)
            key = (at, message)
            if numbers.get(int(number)) == driver and math.isfinite(seconds) and seconds > 0 and key not in seen:
                seen.add(key)
                # rcm.time is the message's UTC field, not timing-feed receipt time.
                events.append({"at": at, "kind": "penalty", "source": urls[_CONTROL],
                               "session": session, "driver": driver, "seconds": seconds})


def load_archive(gp: dict, cache: Path) -> dict:
    """Replay pinned archive timestamps, not packet arrival; entries are hindsight evidence only."""
    year = gp["year"]
    revision = _REVISIONS.get(year)
    if revision is None:
        raise ArchiveUnavailable(f"No pinned TracingInsights archive for {year}.")
    name = quote(_ALIASES.get(gp["name"], gp["name"]), safe="")
    root = f"https://raw.githubusercontent.com/TracingInsights/{year}/{revision}/{name}"
    source = f"https://github.com/TracingInsights/{year}/tree/{revision}/{name}"
    directory = cache / "tracinginsights" / str(year) / revision / f"{gp['round']:02d}"
    sessions = ["Practice 1", "Qualifying"]
    if "Sprint" in gp["sessions"]:
        sessions.append("Sprint")
    sessions.append("Race")
    urls = {
        session: {file: f"{root}/{quote(session, safe='')}/{file}" for file in files}
        for session in sessions
        for files in [(_INFO, _TIMING, _WEATHER, _DRIVERS, _CONTROL) if session == "Race"
                      else (_INFO, _TIMING, _WEATHER)]
    }
    required = {("Race", _INFO), ("Race", _TIMING), ("Race", _DRIVERS)}
    data = {session: {} for session in sessions}
    missing = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        pending = {(session, filename): pool.submit(_fetch, url, directory / session / filename)
                   for session, files in urls.items() for filename, url in files.items()}
        for (session, filename), job in pending.items():
            url = urls[session][filename]
            try:
                result = job.result()
            except (requests.RequestException, OSError, ValueError) as exc:
                raise ArchiveUnavailable(f"{gp['id']}: cannot load {url}: {exc}") from exc
            if result is None:
                if (session, filename) in required:
                    raise ArchiveUnavailable(f"{gp['id']}: missing pinned artifact {url}")
                missing.append(url)
            else:
                data[session][filename] = result

    warnings = []
    events = []
    coverage = {}
    try:
        entries, numbers = _entries(data["Race"][_DRIVERS])
        scheduled = data["Race"][_INFO].get("total_laps")
        if type(scheduled) is not int or scheduled < 1:
            raise ValueError("Race metadata has no positive intended total lap count")
        for session in sessions:
            first = len(events)
            _normalise_session(session, data[session], urls[session], numbers, events, warnings)
            coverage[session] = dict(Counter(events[index]["kind"] for index in range(first, len(events))))
            if session == "Qualifying" and not coverage[session].get("qualifying"):
                warnings.append("Qualifying has no verified elapsed laps in the archive.")
        if not coverage["Race"].get("lap"):
            raise ValueError("no observed Race crossings")
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise ArchiveUnavailable(f"{gp['id']}: unusable timing archive {source}: {exc}") from exc

    events.sort(key=lambda event: event["at"])
    return {
        "events": events,
        "source": source,
        "scheduled_laps": scheduled,
        "entries": entries,
        "coverage": {
            "mode": "retrospective_timing_proxy",
            "revision": revision,
            "sessions": coverage,
            "missing": missing,
            "warnings": warnings,
            "pace": _PACE_NOTE,
            "latency": _LATENCY_NOTE,
            "entries": "Race archive corroboration only; do not use to enumerate pre-FP1 candidates.",
            "scheduled_laps": "Last intended LapCount in archive metadata; pre-FP1 availability unverified.",
            "qualifying": "Elapsed laps inside timestamped session segments, corroborated by ordered sector "
                          "passages; no retrospective deletion or official classification.",
            "sprint": "Provisional order by observed lap count then crossing time, released at Finalised.",
            "penalty": "Race only: unequivocal fixed-time award announcements timed by the message UTC field.",
        },
    }
