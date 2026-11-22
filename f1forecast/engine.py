"""One causal reducer for backtest, recorded replay and operator-received events."""

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import groupby
from math import isfinite, log1p
from statistics import mean, median

import numpy as np


GROUPS = {
    "driver_form": ("driver_form", "driver_podium", "driver_elo", "driver_starts"),
    "team_form": ("team_form", "team_podium", "team_elo"),
    "circuit": ("driver_here", "team_here", "latitude", "longitude"),
    "pace": ("driver_pace", "team_pace", "practice_gap", "lap_pace", "gap_same_lap"),
    "qualifying": ("qual_position", "qual_gap"),
    "grid": ("grid_position",),
    "sprint": ("sprint_position",),
    "position": ("position", "laps_down", "progress"),
    "degradation": ("stint_slope", "tyre_age"),
    "pit": ("stops", "laps_since_stop"),
    "weather": ("air", "track_temp", "rain", "forecast_air", "forecast_rain"),
    "neutralization": ("neutralized", "track_yellow"),
    "penalties": ("announced_seconds",),
}
BASE_GROUPS = ("driver_form", "team_form", "circuit", "pace")
PRE_GROUPS = (*BASE_GROUPS, "qualifying", "grid", "sprint", "weather", "penalties")
HORIZON_GROUPS = {"fp1": BASE_GROUPS, "pre_race": PRE_GROUPS, "race": tuple(GROUPS)}
FEATURES = tuple(key for group in GROUPS.values() for key in group)
NAN = float("nan")


def utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def epoch(iso: str) -> float:
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        raise ValueError(f"UTC offset required: {iso}")
    return dt.timestamp()


def ordered(events, until=float("inf")):
    return sorted((e for e in events if e["at"] <= until), key=lambda e: e["at"])


@dataclass(slots=True)
class Snapshot:
    gp: dict
    horizon: str
    cutoff: float
    lap: int | None
    progress: float
    drivers: tuple[dict, ...]
    x: np.ndarray
    reference: np.ndarray
    reference_name: str
    sources: tuple[str, ...]

    @property
    def ids(self):
        return tuple(d["id"] for d in self.drivers)


class History:
    """Only prior timing classifications enter features; final official results are targets."""

    def __init__(self):
        self.runs = defaultdict(lambda: deque(maxlen=8))
        self.team_runs = defaultdict(lambda: deque(maxlen=8))
        self.here = defaultdict(lambda: deque(maxlen=4))
        self.starts = defaultdict(int)
        self.elo = defaultdict(float)
        self.team_elo = defaultdict(float)
        self.year = None
        self.roster = []
        self.sources = set()

    @staticmethod
    def _update_elo(rating, values, step):
        deltas = {a: mean((1.0 if rank_a > rank_b else 0.5 if rank_a == rank_b else 0.0)
                          - 1.0 / (1.0 + 10.0 ** ((rating[b] - rating[a]) / 400.0))
                          for b, rank_b in values.items() if b != a)
                  for a, rank_a in values.items()} if len(values) > 1 else {}
        for a, change in deltas.items():
            rating[a] += step * change

    def add(self, race: dict):
        year = race["year"]
        if self.year is not None and year != self.year:
            for rating in (self.elo, self.team_elo):
                for key in rating:
                    rating[key] *= 0.75
        self.year = year
        self.roster = race["entries"]
        self.sources.update(race["sources"])
        n = len(race["order"])
        team_stats = defaultdict(list)
        drivers = {d["id"]: d for d in race["entries"]}
        strength = {}
        for pos, code in enumerate(race["order"], 1):
            if code not in drivers:
                continue
            d = drivers[code]
            t = d["team"]
            score = (n - pos) / max(n - 1, 1)
            rec = (score, float(pos <= 3), race["pace"].get(code, NAN))
            self.runs[code].append(rec)
            self.starts[code] += 1
            self.here[("driver", code, race["circuit"])].append(score)
            team_stats[t].append(rec)
            strength[code] = score
        team_strength = {}
        for team, records in team_stats.items():
            pace = [r[2] for r in records if isfinite(r[2])]
            stats = (mean(r[0] for r in records), mean(r[1] for r in records),
                     mean(pace) if pace else NAN)
            self.team_runs[team].append(stats)
            self.here[("team", team, race["circuit"])].append(stats[0])
            team_strength[team] = stats[0]
        self._update_elo(self.elo, strength, 32.0)
        self._update_elo(self.team_elo, team_strength, 38.0)

    @staticmethod
    def _form(records):
        weights = (0.8 ** np.arange(len(records) - 1, -1, -1)).tolist()
        total = sum(weights) + 1.4
        finish = (0.7 + sum(w * x[0] for w, x in zip(weights, records))) / total
        podium = (0.21 + sum(w * x[1] for w, x in zip(weights, records))) / total
        pace = [(w, x[2]) for w, x in zip(weights, records) if isfinite(x[2])]
        return finish, podium, sum(w * p for w, p in pace) / sum(w for w, _ in pace) if pace else NAN

    def row(self, d, circuit):
        code, team = d["id"], d["team"]
        form, pod, pace = self._form(self.runs.get(code, ()))
        tform, tpod, tpace = self._form(self.team_runs.get(team, ()))
        here_d = self.here.get(("driver", code, circuit))
        here_t = self.here.get(("team", team, circuit))
        return {"driver_form": form, "team_form": tform, "driver_podium": pod, "team_podium": tpod,
                "driver_elo": self.elo.get(code, 0.0), "team_elo": self.team_elo.get(team, 0.0),
                "driver_starts": log1p(self.starts.get(code, 0)), "driver_pace": pace, "team_pace": tpace,
                "driver_here": mean(here_d) if here_d else NAN, "team_here": mean(here_t) if here_t else NAN}


def history_at(records, cutoff: float, gp_id: str | None = None) -> History:
    hist = History()
    for record in sorted(records, key=lambda r: r["at"]):
        if record["at"] > cutoff:
            break
        if record["gp_id"] != gp_id:
            hist.add(record)
    return hist


class RaceState:
    def __init__(self, gp: dict, history: History):
        self.gp = gp
        self.history = history
        self.roster = {d["id"]: d for d in history.roster}
        self.seen_by_team = defaultdict(set)
        self.sources = set(history.sources)
        self.quali = {}
        self.grid = {}
        self.sprint = {}
        self.practice = {}
        self.progress = {}
        self.crossings = defaultdict(lambda: deque(maxlen=2))
        self.position = {}
        self.recent_pace = defaultdict(lambda: deque(maxlen=7))
        self.pace = defaultdict(list)
        self.pits = defaultdict(int)
        self.last_pit = {}
        self.last_lap = 0
        self.stint = {}
        self.stint_laps = defaultdict(lambda: deque(maxlen=9))
        self.tyre_age = {}
        self.penalties = defaultdict(float)
        self.track = None
        self.weather = {}
        self.forecast = {}
        self.last_at = float("-inf")

    def apply(self, e: dict):
        at = e["at"]
        if at < self.last_at or not isfinite(at):
            raise ValueError(f"out-of-order or invalid receipt time: {at}")
        self.last_at = at
        kind, d = e["kind"], e.get("driver")
        sources = e["source"]
        for item in sources if isinstance(sources, list) else (sources,):
            self.sources.add(item["url"] if isinstance(item, dict) else item)
        if kind == "roster":
            self.roster = {x["id"]: x for x in e["entries"]}
        elif kind == "entry":
            x = e["entry"]
            replaced = e.get("replaces")
            if replaced:
                self.roster.pop(replaced, None)
            self.roster[x["id"]] = x
            seen = self.seen_by_team[(e.get("session"), x["team"])]
            seen.add(x["id"])
            if e.get("session") and len(seen) >= 2:
                for code in tuple(self.roster):
                    if code not in seen and self.roster[code]["team"] == x["team"]:
                        self.roster.pop(code)
        elif kind == "grid":
            self.grid = dict(e["positions"])
        elif kind == "practice":
            seconds = e["seconds"]
            self.practice[d] = min(self.practice.get(d, seconds), seconds)
        elif kind == "qualifying":
            if e.get("entry"):
                self.roster[e["entry"]["id"]] = e["entry"]
            seconds = e["seconds"]
            if seconds > 0 and isfinite(seconds):
                self.quali[d] = min(self.quali.get(d, seconds), seconds)
        elif kind == "sprint":
            self.sprint[d] = e["position"]
        elif kind == "position":
            self.position[d] = (e["position"], at)
        elif kind == "positions":
            self.position.update({code: (rank, at) for code, rank in e["positions"].items()})
        elif kind == "track":
            self.track = str(e["status"])
        elif kind == "weather":
            self.weather = e
        elif kind == "forecast_weather":
            self.forecast = e
        elif kind == "penalty":
            self.penalties[d] += e["seconds"]
        elif kind == "pit":
            if d not in self.last_pit or at - self.last_pit[d][0] > 15:
                self.pits[d] += 1
                self.last_pit[d] = (at, self.progress.get(d, (0, 0.0))[0])
                self.stint_laps[d].clear()
        elif kind == "session":
            if e.get("initial"):
                for code, completed in e.get("completed_laps", {}).items():
                    self.progress.setdefault(code, (completed, float("inf")))
        elif kind == "leader_lap":
            self.last_lap = max(self.last_lap, int(e["lap"]))
        elif kind == "lap":
            lap = int(e["lap"])
            if lap <= self.progress.get(d, (0, 0.0))[0]:
                return
            self.progress[d] = (lap, at)
            self.crossings[d].append((lap, at))
            self.last_lap = max(self.last_lap, lap)
            stint = e.get("stint")
            if stint is not None and self.stint.get(d) != stint:
                self.stint_laps[d].clear()
                self.stint[d] = stint
            if e.get("tyre_age") is not None:
                self.tyre_age[d] = e["tyre_age"]
            seconds = e.get("seconds")
            if (seconds is not None and 45.0 <= seconds <= 175.0 and lap > 1
                    and self.track in (None, "1") and at - self.last_pit.get(d, (-1e10, 0))[0] > 130):
                self.recent_pace[d].append(seconds)
                self.stint_laps[d].append((lap, seconds))
                self.pace[d].append(seconds)
        else:
            raise ValueError(f"unknown event {kind}")

    def rank(self, codes, cutoff):
        # Explicit synchronized order only if complete, fresh and a permutation.
        # Else compare last completed laps, not the slower cars' future lap crossings.
        live = [self.position.get(code, (0, -1e10)) for code in codes]
        if all(cutoff - at <= 15 for _, at in live) and {p for p, _ in live} == set(range(1, len(codes) + 1)):
            return sorted(codes, key=lambda d: self.position[d][0])
        return sorted(codes, key=lambda d: (-self.progress.get(d, (0, 0.0))[0],
                                            self.progress.get(d, (0, 0.0))[1],
                                            self.grid.get(d, 999), self.quali.get(d, 999), d))

    def snapshot(self, horizon: str, cutoff: float, *, lap: int | None = None) -> Snapshot | None:
        if self.last_at > cutoff:
            raise ValueError("snapshot cannot look backwards through mutated state")
        if len(self.roster) < 3:
            return None
        codes = sorted(self.roster)
        drivers = tuple(self.roster[d] for d in codes)
        qual = {code: i for i, code in enumerate(sorted(self.quali, key=lambda d: (self.quali[d], d)), 1)}
        qbest = min(self.quali.values(), default=NAN)
        bestlaps = {code: median(v) for code, v in self.recent_pace.items() if v}
        rp = median(bestlaps.values()) if bestlaps else NAN
        ranks = {d: i for i, d in enumerate(self.rank(codes, cutoff), 1)}
        cp = max(1, lap - 1) if lap is not None else 0
        same = {code: t for code, times in self.crossings.items() for n, t in times if n == cp}
        fastest = min(same.values(), default=NAN)
        practice_best = min(self.practice.values(), default=NAN)
        distance = self.gp.get("scheduled_laps") or 65
        progress = min(lap / distance, 1.0) if lap is not None else 0.0
        alt_ranks = self.grid if self.grid else qual
        group = "posiciones" if horizon == "race" else ("parrilla" if self.grid else
                "clasificación" if qual else "forma") if horizon == "pre_race" else "forma"
        rows = []
        ref = []
        for d in drivers:
            code = d["id"]
            row = self.history.row(d, self.gp["circuit"])
            row.update(latitude=self.gp.get("latitude", NAN), longitude=self.gp.get("longitude", NAN))
            strength = 4 * (0.57 * row["driver_form"] + 0.43 * row["team_form"])
            if horizon != "fp1":
                row.update(qual_position=qual.get(code, NAN), qual_gap=self.quali.get(code, NAN) - qbest,
                           practice_gap=self.practice.get(code, NAN) - practice_best,
                           grid_position=self.grid.get(code, NAN), sprint_position=self.sprint.get(code, NAN),
                           announced_seconds=self.penalties.get(code, 0),
                           forecast_air=self.forecast.get("air", NAN), forecast_rain=self.forecast.get("rain_p", NAN))
            if horizon == "pre_race" and alt_ranks:
                strength = -1.65 * np.log(alt_ranks.get(code, max(alt_ranks.values()) + 1))
            if horizon == "race":
                n = self.progress.get(code, (0, NAN))[0]
                v = self.stint_laps.get(code)
                slope = (mean(s for _, s in list(v)[-2:]) - mean(s for _, s in list(v)[:2])) / (
                    v[-1][0] - v[0][0]) if v and len(v) >= 4 else NAN
                row.update(position=ranks[code], laps_down=max(0, lap - n - (n < lap)), progress=progress,
                           gap_same_lap=same.get(code, NAN) - fastest,
                           lap_pace=bestlaps.get(code, NAN) - rp, stint_slope=slope,
                           tyre_age=self.tyre_age.get(code, NAN), stops=self.pits.get(code, 0),
                           laps_since_stop=n - self.last_pit.get(code, (0.0, 0))[1],
                           air=self.weather.get("air", NAN), track_temp=self.weather.get("track_temp", NAN),
                           rain=self.weather.get("rain", NAN),
                           neutralized=self.track in {"4", "5", "6", "7"} if self.track is not None else NAN,
                           track_yellow=self.track == "2" if self.track is not None else NAN)
                strength = -(1.5 + 3.0 * progress) * np.log(ranks[code])
            rows.append([row.get(k, NAN) for k in FEATURES])
            ref.append(strength)
        return Snapshot(self.gp, horizon, cutoff, lap, progress, drivers,
                        np.array(rows, dtype=np.float32), np.array(ref), group, tuple(sorted(self.sources)))

    def record(self, entries):
        codes = [d["id"] for d in entries]
        active = sum(code in self.progress for code in codes)
        if active < 3:
            raise ValueError(f"{self.gp['id']}: no timing classification")
        medians = {d: median(v) for d, v in self.pace.items() if v}
        field = median(medians.values()) if medians else 0.0
        return {"at": self.last_at, "year": self.gp["year"], "gp_id": self.gp["id"],
                "circuit": self.gp["circuit"], "entries": entries,
                "order": self.rank(codes, self.last_at), "pace": {d: t - field for d, t in medians.items()},
                "sources": sorted(self.sources)}


def asof(gp: dict, records, events, horizon: str, cutoff: float) -> Snapshot | None:
    state = RaceState(gp, history_at(records, cutoff, gp["id"]))
    for e in ordered(events, cutoff):
        state.apply(e)
    return state.snapshot(horizon, cutoff, lap=state.last_lap if horizon == "race" else None)


def race_snapshots(state: RaceState, events, until=float("inf")):
    """Yield after all events sharing the lead crossing's timestamp are applied."""
    for at, group in groupby(ordered(events, until), key=lambda e: e["at"]):
        previous = state.last_lap
        for e in group:
            state.apply(e)
        if state.last_lap > previous:
            snap = state.snapshot("race", at, lap=state.last_lap)
            if snap is not None:
                yield snap
