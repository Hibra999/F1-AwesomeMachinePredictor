"""Local-only event corpus. Official classification never enters the feature reducer."""

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import joblib
import numpy as np

from .archive import ArchiveUnavailable, load_archive
from .engine import RaceState, asof, history_at, ordered, race_snapshots
from .results import get_documents, get_results, get_schedule

TRAIN_YEARS = (2022, 2023, 2024, 2025)
FINAL_YEAR = 2026
# Locked before final labels are loaded; 21 is in progress at the evidence freeze.
FINAL_LAST_ROUND = 20
FP1_LEAD = 60
LABEL_EMBARGO = 14 * 86400

TEAM_ALIASES = {
    "red bull racing": "red_bull", "red bull": "red_bull", "red_bull": "red_bull",
    "haas f1 team": "haas", "haas": "haas", "kick sauber": "sauber", "sauber": "sauber",
    "alfa romeo": "alfa", "alfa": "alfa", "aston martin": "aston_martin", "aston_martin": "aston_martin",
    "alphatauri": "alphatauri", "alpha tauri": "alphatauri", "rb": "rb", "racing bulls": "rb",
    "racing_bulls": "rb", "visa cash app rb": "rb",
}


def identity(entry):
    team = entry["team"].strip().lower()
    return {**entry, "team": TEAM_ALIASES.get(team, team.replace(" ", "_"))}


@dataclass(slots=True)
class Example:
    snapshot: object
    rank: np.ndarray
    podium_ids: tuple[str, str, str]
    official_ids: tuple[str, ...]
    label_at: float

    @property
    def gp_id(self):
        return self.snapshot.gp["id"]

    @property
    def brier_size(self):
        n_missing = len(set(self.official_ids).difference(self.snapshot.ids))
        return len(self.rank) + n_missing


def _publication(doc, key):
    return doc.get(f"{key}_source") or "FIA"


def document_events(doc):
    if doc.get("entries") and doc.get("entries_at") is not None:
        yield {"at": doc["entries_at"], "kind": "roster", "source": _publication(doc, "entries"),
               "entries": [identity(d) for d in doc["entries"]]}
    if doc.get("grid") and doc.get("grid_at") is not None:
        yield {"at": doc["grid_at"], "kind": "grid", "source": _publication(doc, "grid"),
               "positions": doc["grid"]}
    yield from doc.get("events", ())


def weekend(gp, archive, docs):
    """Convert observed identities only when a driver first appears in a timed session."""
    events = list(document_events(docs))
    entry_by_id = {d["id"]: identity(d) for d in archive.get("entries", ())}
    seen = set()
    for e in ordered(archive.get("events", ())):
        session = e.get("session")
        if session == "Practice 1":
            seconds = e.get("seconds")
            if e.get("kind") == "lap" and seconds is not None and 45 <= seconds <= 155:
                events.append({**e, "kind": "practice"})
            continue
        if session not in {"Qualifying", "Race", "Sprint"}:
            continue
        if session == "Sprint" and e["kind"] != "sprint":
            continue
        if session == "Qualifying" and e["kind"] not in {"qualifying", "penalty"}:
            continue
        code = e.get("driver")
        if code in entry_by_id and (session, code) not in seen and e["kind"] in {"lap", "qualifying", "sprint"}:
            events.append({"at": e["at"], "source": e["source"], "kind": "entry", "session": session,
                           "entry": entry_by_id[code]})
            seen.add((session, code))
        events.append(e)
    return ordered(events)


def timing_record(gp, archive):
    events = [e for e in archive.get("events", ()) if e.get("session") == "Race"]
    entries = [identity(d) for d in archive.get("entries", ())]
    if not events or len(entries) < 3:
        return None
    state = RaceState(gp, history_at((), gp["race"]))
    for e in ordered(events):
        state.apply(e)
    try:
        return state.record(entries)
    except ValueError:
        return None


def _assemble(gp, label, cache):
    try:
        archive = load_archive(gp, cache)
    except ArchiveUnavailable as exc:
        print(exc, flush=True)
        archive = {"coverage": {"mode": "missing", "warnings": [str(exc)]}}
    docs = get_documents(gp, cache, as_of=gp["race"] - 1800)
    return {"gp": gp, "events": weekend(gp, archive, docs), "record": timing_record(gp, archive),
            "archive": archive.get("coverage", {}), "label": label,
            "documents": {k: v for k, v in docs.items() if k not in {"entries", "grid", "events"}},
            "recorded_laps": archive.get("scheduled_laps"), "source": archive.get("source")}


def prepare(years, cache: Path, target: Path, *, refresh=False, last_round=None):
    """Load one disjoint corpus at a time; provider errors are reported per missing GP."""
    years = tuple(years)
    schedule = get_schedule(years, cache, refresh=refresh)
    labels = {}
    for year in years:
        labels.update(get_results(year, cache, refresh=refresh))
    schedule = [g for g in schedule if (last_round is None or g["round"] <= last_round) and g["id"] in labels]
    with ThreadPoolExecutor(max_workers=6) as pool:
        # Keep failures visible; absence of a race archive never fabricates observations.
        jobs = [(g, pool.submit(_assemble, g, labels[g["id"]], cache)) for g in schedule]
        rows = []
        for gp, job in jobs:
            row = job.result()
            rows.append(row)
            print(gp["id"], len(row["events"]), row["recorded_laps"], flush=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(rows, target, compress=2)
    return rows


def load_corpus(path):
    return joblib.load(path)


def snapshots(row, records, *, pre_fp1_cutoff=None, through=None):
    gp = row["gp"]
    events = row["events"]
    fp1 = gp["fp1"] - FP1_LEAD if pre_fp1_cutoff is None else pre_fp1_cutoff
    pre = gp["race"] - 1800
    for horizon, cut in (("fp1", fp1), ("pre_race", pre)):
        if through is None or cut <= through:
            snap = asof(gp, records, events, horizon, cut)
            if snap is not None:
                yield snap
    if through is not None and through < gp["race"]:
        return
    state = RaceState(gp, history_at(records, gp["race"], gp["id"]))
    yield from race_snapshots(state, events, through if through is not None else float("inf"))


def records_for(*corpora):
    return [r["record"] for rows in corpora for r in rows if r["record"] is not None]


def examples(rows, records, *, embargo=LABEL_EMBARGO):
    by_horizon = defaultdict(list)
    skipped = Counter()
    for row in rows:
        official = row["label"]["labels"]
        top = tuple(code for code, rank in sorted(official.items(), key=lambda kv: kv[1]) if rank <= 3)
        if len(top) != 3:
            skipped["classification_without_three"] += 1
            continue
        at = row["label"].get("available_at")
        label_at = at if isinstance(at, (int, float)) else row["gp"]["race"] + embargo
        for snap in snapshots(row, records):
            rank = np.array([official.get(code, 0) for code in snap.ids], dtype=np.int16)
            by_horizon[snap.horizon].append(Example(snap, rank, top, tuple(official), label_at))
        if not row["record"]:
            skipped["without_timing_record"] += 1
    return dict(by_horizon), dict(skipped)


def counts(examples):
    result = {}
    for horizon, rows in examples.items():
        groups = {row.gp_id for row in rows}
        reference = defaultdict(set)
        availability = defaultdict(set)
        for row in rows:
            reference[row.snapshot.reference_name].add(row.gp_id)
            for i, present in enumerate(np.isfinite(row.snapshot.x).any(axis=0)):
                if present:
                    availability[i].add(row.gp_id)
        result[horizon] = {"gps": len(groups), "cuts": len(rows),
                           "reference_gps": {k: len(v) for k, v in reference.items()},
                           "roster_exact_cuts": sum(set(r.snapshot.ids) == set(r.official_ids) for r in rows),
                           "feature_gps": {k: len(v) for k, v in availability.items()}}
    return result


def current_year():
    return datetime.now(UTC).year
