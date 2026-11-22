"""Una conexión al cronometraje SignalR público; nunca usa la clasificación oficial."""

import math
import re
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime, timedelta, timezone
from queue import Empty, Queue
from threading import Lock
from time import monotonic, time

import requests
from signalrcore.hub_connection_builder import HubConnectionBuilder

from .archive import _PENALTY
from .dataset import identity

_SOURCE = "wss://livetiming.formula1.com/signalrcore"
_NEGOTIATE = "https://livetiming.formula1.com/signalrcore/negotiate"
_TOPICS = ("SessionInfo", "SessionStatus", "LapCount", "TimingData", "DriverList",
           "TrackStatus", "WeatherData", "RaceControlMessages", "Heartbeat")
_STATUSES = {"Inactive", "Started", "Aborted", "Finished", "Finalised", "Ends"}
_COUNTING = {"Started", "Finished"}
_TRACK = {"1", "2", "3", "4", "5", "6", "7"}


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _integer(value):
    if type(value) is int:
        return value if value >= 0 else None
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,8}", value):
        return int(value)
    return None


def _number(value):
    if type(value) not in (float, int, str):
        return None
    try:
        value = float(value)
    except (ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def _epoch(value, default_tz=UTC):
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
        return (dt if dt.tzinfo is not None else dt.replace(tzinfo=default_tz)).timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def _matches(info, gp):
    if info.get("Name") != "Race" or info.get("Type", "Race") != "Race":
        return False
    meeting = _integer(_mapping(info.get("Meeting")).get("Number"))
    if meeting is None or meeting != _integer(gp["round"]):
        return False
    stamp, offset = info.get("StartDate"), info.get("GmtOffset")
    scheduled = _number(gp["race"])
    if not isinstance(stamp, str) or scheduled is None:
        return False
    try:
        start = datetime.fromisoformat(stamp)
        if start.tzinfo is None:
            match = re.fullmatch(r"([+-]?)([0-9]{2}):([0-5][0-9])(?::([0-5][0-9]))?", str(offset))
            if not match:
                return False
            sign, hh, mm, ss = match.groups()
            delta = timedelta(hours=int(hh), minutes=int(mm), seconds=int(ss or 0))
            start = start.replace(tzinfo=timezone(-delta if sign == "-" else delta))
        return abs(start.timestamp() - scheduled) <= 3 * 3600
    except (ValueError, OverflowError, OSError):
        return False


def _entry(number, row):
    code, team = row.get("Tla"), row.get("TeamName")
    if not isinstance(code, str) or not re.fullmatch(r"[A-Z]{3}", code):
        return None
    if not isinstance(team, str) or not team.strip():
        return None
    name = " ".join(s.strip() for k in ("FirstName", "LastName")
                    if isinstance(s := row.get(k), str) and s.strip())
    if not name and isinstance(full := row.get("FullName"), str):
        name = full.strip()
    if name:
        return identity({"id": code, "name": name, "team": team, "number": number})
    return None


def _lap_seconds(value):
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"(?:(\d{1,2}):([0-5]\d)|([0-9]{1,3}))(\.\d{1,3})?", value)
    if not match:
        return None
    minutes, within, whole, decimal = match.groups()
    return (int(minutes or 0) * 60 + float((whole or within) + (decimal or ""))) or None


class _RaceFeed:
    def __init__(self, gp):
        self.gp = gp
        self.info = None
        self.closed = False
        self.status = None
        self.clock = {}
        self.leader_lap = 0
        self.drivers = {}
        self.roster = {}
        self.ranks = {}
        self.laps = {}
        self.in_pit = {}
        self.weather = {}
        self.penalties = set()

    def packet(self, kind, at, packet):
        initial = kind == "snapshot"
        source_at = None
        if initial:
            if self.info is not None:
                return []
            updates = _mapping(packet)
            self.info = _mapping(updates.get("SessionInfo"))
        elif kind == "feed" and self.info is not None and isinstance(packet, (list, tuple)) and len(packet) >= 3:
            topic, value, stamp = packet[:3]
            if not isinstance(topic, str):
                return []
            updates = {topic: _mapping(value)}
            source_at = _epoch(stamp)
            if topic == "SessionInfo":
                patch = updates[topic]
                key = patch.get("Key", self.info.get("Key"))
                if self.info.get("Key") is not None and key != self.info["Key"]:
                    self.closed = True
                    return []
                self.info = {**self.info, **patch}
                if "Meeting" in patch:
                    self.info["Meeting"] = {**self.meeting, **_mapping(patch["Meeting"])}
        else:
            return []
        if initial or "SessionInfo" in updates:
            if not _matches(self.info, self.gp):
                self.closed = True
                return []
            self.meeting = _mapping(self.info.get("Meeting"))

        batch = []
        base = {"at": at, "source": _SOURCE, "session": "Race", "initial": initial}
        if source_at is not None:
            base["source_at"] = source_at

        def emit(event_kind, **fields):
            batch.append({**base, "kind": event_kind, **fields})

        clock = _mapping(updates.get("LapCount"))
        clock_changed = False
        for source_key, key in (("CurrentLap", "current_lap"), ("TotalLaps", "total_laps")):
            value = _integer(clock.get(source_key))
            if value and value != self.clock.get(key):
                self.clock[key] = value
                clock_changed = True
        status = _mapping(updates.get("SessionStatus")).get("Status")
        if not isinstance(status, str) and "SessionInfo" in updates:
            status = _mapping(updates["SessionInfo"]).get("SessionStatus")
        status_changed = isinstance(status, str) and status in _STATUSES and status != self.status
        if status_changed:
            self.status = status
        if initial or clock_changed or status_changed:
            emit("session", status=self.status, **self.clock)

        roster_changed = False
        for key, patch in _mapping(updates.get("DriverList")).items():
            if not (number := _integer(key)) or not isinstance(patch, dict):
                continue
            row = self.drivers.setdefault(number, {})
            row.update(patch)
            entry = _entry(number, row)
            if entry is not None and self.roster.get(number) != entry:
                self.roster[number] = entry
                roster_changed = True
        if roster_changed:
            entries = sorted(self.roster.values(), key=lambda row: row["id"])
            if len({row["id"] for row in entries}) == len(entries):
                emit("roster", entries=entries)

        track = _mapping(updates.get("TrackStatus")).get("Status")
        if isinstance(track, (str, int)) and str(track) in _TRACK:
            emit("track", status=str(track))
        weather = _mapping(updates.get("WeatherData"))
        if weather:
            for source_key, key in (("AirTemp", "air"), ("TrackTemp", "track_temp")):
                if (value := _number(weather.get(source_key))) is not None:
                    self.weather[key] = value
            rain = _integer(weather.get("Rainfall"))
            if rain in (0, 1):
                self.weather["rain"] = bool(rain)
            if len(self.weather) == 3 and any(key in weather for key in ("AirTemp", "TrackTemp", "Rainfall")):
                emit("weather", **self.weather)

        rank_changed = had_crossing = False
        lines = _mapping(_mapping(updates.get("TimingData")).get("Lines"))
        for key, patch in lines.items():
            if not (number := _integer(key)) or not isinstance(patch, dict):
                continue
            entry = self.roster.get(number)
            if "Position" in patch or "Line" in patch:
                rank = _integer(patch.get("Position")) or _integer(patch.get("Line"))
                if rank != self.ranks.get(number):
                    rank_changed = True
                    self.ranks[number] = rank
            in_pit = patch.get("InPit")
            if type(in_pit) is bool:
                if (entry and not initial and self.status == "Started" and in_pit
                        and self.in_pit.get(number) is False):
                    emit("pit", driver=entry["id"])
                self.in_pit[number] = in_pit
            if (lap := _integer(patch.get("NumberOfLaps"))) is None or lap <= self.laps.get(number, 0):
                continue
            if self.status not in _COUNTING:
                continue
            self.laps[number] = lap
            if entry and not initial:
                seconds = _lap_seconds(_mapping(patch.get("LastLapTime")).get("Value"))
                emit("lap", driver=entry["id"], lap=lap, seconds=seconds, stint=None, tyre_age=None)
                had_crossing = True

        lap = self.clock.get("current_lap", 0) - 1
        total = self.clock.get("total_laps")
        confirmed = (self.status in _COUNTING and lap > self.leader_lap
                     and (total is None or lap < total)
                     and (initial or (clock_changed or had_crossing) and max(self.laps.values(), default=0) >= lap))
        if confirmed:
            self.leader_lap = lap
            emit("leader_lap", lap=lap)
        if self.roster and (initial or roster_changed or rank_changed or had_crossing or confirmed):
            positions = {row["id"]: self.ranks.get(n) for n, row in self.roster.items()}
            if set(positions.values()) == set(range(1, len(self.roster) + 1)):
                emit("positions", positions=positions)

        messages = _mapping(updates.get("RaceControlMessages")).get("Messages")
        if isinstance(messages, (dict, list)):
            for item in messages.values() if isinstance(messages, dict) else messages:
                row = _mapping(item)
                stamp = _epoch(row.get("Utc"))
                text = row.get("Message")
                if stamp is None or not isinstance(text, str) or (stamp, text) in self.penalties:
                    continue
                match = _PENALTY.fullmatch(text)
                if match:
                    raw, number, code = match.groups()
                    entry = self.roster.get(_integer(number))
                    if entry and entry["id"] == code and (seconds := _number(raw)) and seconds > 0:
                        self.penalties.add((stamp, text))
                        emit("penalty", driver=code, seconds=seconds, source_at=stamp)
        return batch


def normalize_packets(records: Iterable[dict], gp: dict) -> Iterator[list[dict]]:
    """Reduce capture envelopes (`kind`, `received`, `packet`) in receiver order."""
    race = _RaceFeed(gp)
    last_at = float("-inf")
    for record in records:
        kind = record.get("kind")
        if kind == "closed":
            break
        if kind not in ("snapshot", "feed") or (at := _number(record.get("received"))) is None:
            continue
        if at < last_at:
            raise ValueError("SignalR records are not in receiver order")
        last_at = at
        if batch := race.packet(kind, at, record.get("packet")):
            yield batch
        if race.closed:
            break


def live_batches(gp: dict, *, seconds: float | None = None) -> Iterator[list[dict]]:
    """Yield confirmed Race batches until timeout, hub close, or session mismatch.

    The optional duration bounds listening after connection setup. An idle hub
    ends the stream after 30 seconds without a subscribed packet. Close the
    iterator if stopping early to release the connection immediately.
    """
    if seconds is not None and (not math.isfinite(seconds) or seconds < 0):
        raise ValueError("seconds must be finite and non-negative")
    if seconds == 0:
        return
    with requests.options(_NEGOTIATE, timeout=6) as response:
        affinity = response.cookies.get("AWSALBCORS")
    headers = {"Cookie": f"AWSALBCORS={affinity}"} if affinity else {}
    hub = HubConnectionBuilder().with_url(_SOURCE, options={"headers": headers, "verify_ssl": True}).build()
    inbox = Queue()
    lock = Lock()
    last_at = float("-inf")

    def receive(kind, packet=None):
        nonlocal last_at
        with lock:
            last_at = max(last_at, time())
            inbox.put((monotonic(), {"kind": kind, "received": last_at, "packet": packet}))

    hub.on_open(lambda: receive("open"))
    hub.on("feed", lambda packet: receive("feed", packet))
    hub.on_close(lambda: receive("closed"))
    hub.on_error(lambda _message: receive("closed"))

    def records():
        end = float("inf") if seconds is None else monotonic() + seconds
        while True:
            try:
                tick, record = inbox.get(timeout=max(0, min(30, end - monotonic())))
            except Empty:
                return
            if tick > end:
                return
            if record["kind"] == "open":
                hub.send("Subscribe", [list(_TOPICS)], on_invocation=lambda msg: receive("snapshot", msg.result))
            else:
                yield record

    try:
        if hub.start():
            yield from normalize_packets(records(), gp)
    finally:
        hub.stop()
