"""Windows-friendly CLI for the same deployment, audit and replay code paths."""

import argparse
import json
from pathlib import Path
from time import sleep

from .dataset import (FINAL_LAST_ROUND, FINAL_YEAR, TRAIN_YEARS, document_events, examples,
                      load_corpus, prepare, records_for, snapshots)
from .engine import RaceContext, RaceState, asof, history_at, ordered, utc
from .leakage import verify_leakage
from .pages import SITE, UPCOMING, verify_forecast
from .publication import append_cut, event_card, new_feed, now, pre_race_events, prediction, read_file, write_file
from .results import get_documents, get_schedule
from .training import evaluate, sealed_model, train


SOURCE_RECORDING = "TracingInsights / FastF1: reloj histórico reconstruido, no recepción en directo. Resultados: Jolpica-F1 / FIA."
SOURCE_LIVE = "Live Timing SignalR Core sin cuenta; hora UTC de recepción en el PC. Pronósticos conservados en local."
SOURCE_FORECAST = "FIA y calendario oficial; forma y ritmo de cronometraje anterior (TracingInsights/FastF1). Resultados: Jolpica-F1 / FIA."
# Weekend timetable, not the last intended LapCount in the retrospective archive.
REPLAY_TIMETABLES = {"2026-20": (71, "https://www.formula1.com/en/latest/article/"
                                 "formula-1-msc-cruises-grande-premio-de-sao-paulo-2026.3nFWRAkowAY3uUyvw6O0eJ")}


def paths(work):
    return work / "validation-data.joblib", work / "final-data.joblib"


def corpora(work):
    return [load_corpus(path) for path in paths(work) if path.exists()]


def lookup(gp_id, cache, refresh=False):
    year = int(gp_id.split("-", 1)[0])
    gp = next((g for g in get_schedule((year,), cache, refresh=refresh) if g["id"] == gp_id), None)
    if gp is None:
        raise ValueError(f"unknown GP {gp_id}")
    if gp_id == "2026-22":
        gp = {**gp, "name": "Gran Premio de Catar", "scheduled_laps": 57}
    return gp


def row_for(gp_id, data):
    for rows in data:
        for row in rows:
            if row["gp"]["id"] == gp_id:
                return row
    raise ValueError(f"{gp_id} is not in the prepared corpus")


def _read_events(path: Path | None):
    if path is None:
        return []
    with path.open(encoding="utf-8") as fp:
        return [json.loads(line) for line in fp if line.strip()]


def _publish(feed, out):
    if out.resolve().is_relative_to(SITE):
        verify_forecast(feed)
    write_file(out, feed)
    print(feed["event"]["id"], feed["mode"], len(feed["predictions"]), feed["updated_utc"], str(out), flush=True)


def freeze(args, work, bundle, records):
    gp = lookup(args.gp, work, refresh=True)
    pre = gp["race"] - 1800
    initial = now()
    if args.horizon == "fp1" and initial >= gp["fp1"]:
        raise ValueError("FP1 cutoff is past; use replay, not a real publication")
    if args.horizon == "pre_race" and initial < pre:
        if not args.wait:
            raise ValueError(f"T−30 is future: {utc(pre)}. Use --wait to arm the cutoff.")
        print(f"Armed for {utc(pre)}", flush=True)
        while now() < pre:
            sleep(max(0.0, min(pre - now(), 30.0)))
    if args.horizon == "pre_race" and now() > pre + 120:
        raise ValueError(f"missed real publication window for {utc(pre)}; use replay")
    when = now() if args.horizon == "fp1" else pre
    docs = get_documents(gp, work, as_of=when, refresh=True)
    events = list(document_events(docs)) + _read_events(args.events)
    if args.horizon == "pre_race":
        events = pre_race_events(events, args.gp, when)
    snap = asof(gp, records, events, args.horizon, when)
    if snap is None:
        raise ValueError("no dated or provisional roster available at the cutoff")
    if args.horizon == "fp1" and now() >= gp["fp1"]:
        raise ValueError("cannot label a publication as pre-FP1 after practice has started")
    if args.horizon == "pre_race" and now() > pre + 120:
        raise ValueError("official source did not complete inside the publication window")
    prior = args.out if args.out.exists() else args.seed_feed
    feed = read_file(prior) if prior else new_feed(gp, "forecast", SOURCE_FORECAST)
    if feed["event"]["id"] != args.gp or feed["mode"] != "forecast":
        raise ValueError("existing frozen forecast belongs to another event or mode")
    feed["event"] = event_card(gp)
    feed["source"] = SOURCE_FORECAST
    feed["entries_status"] = docs.get("entries_status", "carried_prior_timed_roster")
    feed["evidence_urls"] = docs.get("source", [])
    if feed["entries_status"] == "provisional_previous_gp_qualifying":
        feed["source"] = SOURCE_FORECAST + " Alineación provisional: clasificación del GP de Las Vegas, no lista oficial del GP de Catar."
    cut = prediction(bundle, snap, published=now())
    append_cut(feed, cut)
    _publish(feed, args.out)
    print(json.dumps({"cutoff_utc": cut["cutoff_utc"], "probable_order": cut["podium"],
                      "entrants": len(cut["drivers"]), "method": cut["method"]}, ensure_ascii=False))


def _local_cut(bundle, snap, context, published=None):
    cut = prediction(bundle, snap, published=published)
    cut["race_context"] = context.snapshot(snap.cutoff, snap.ids)
    return cut


def replay(args, bundle, data, records):
    row = row_for(args.gp, data)
    if not row["recorded_laps"]:
        raise ValueError("no timing archive for this GP")
    through = None
    if args.through_lap is not None:
        through = min((e["at"] for e in row["events"] if e["kind"] == "lap" and e["lap"] == args.through_lap),
                      default=None)
        if through is None:
            raise ValueError("requested lap crossing was not recorded")
    planned, timetable = REPLAY_TIMETABLES.get(args.gp, (row["gp"].get("scheduled_laps"), None))
    context = RaceContext(planned)
    feed = new_feed(row["gp"], "replay", SOURCE_RECORDING, planned)
    feed["evidence_urls"] = [row["source"]] + ([timetable] if timetable else [])
    for snap in snapshots(row, records, through=through, race_context=context):
        append_cut(feed, _local_cut(bundle, snap, context))
    feed["complete"] = through is None
    if not feed["predictions"]:
        raise ValueError("no reconstructed predictions to publish")
    _publish(feed, args.out)


def watch(args, work, bundle, records):
    from .live import live_batches

    gp = lookup(args.gp, work)
    # Recover earlier frozen predictions without applying them as measurements.
    feed = new_feed(gp, "live", SOURCE_LIVE)
    if args.seed_feed:
        seeded = read_file(args.seed_feed)
        if seeded["event"]["id"] != args.gp or seeded["mode"] != "forecast":
            raise ValueError("--seed-feed is not a frozen forecast for this GP")
        feed["predictions"] = seeded["predictions"]
    if args.out.exists():
        saved = read_file(args.out)
        if saved["mode"] != "live" or saved["event"]["id"] != args.gp:
            raise ValueError("cannot replace another GP or mode's frozen cuts")
        feed = saved
    published_laps = {p["lap"] for p in feed["predictions"] if p["lap"] is not None}
    prior = next((p["race_context"] for p in reversed(feed["predictions"])
                  if p["horizon"] == "race" and p.get("race_context")), None)
    context = RaceContext(gp.get("scheduled_laps"), prior=prior)
    state = RaceState(gp, history_at(records, now(), gp["id"]), race_context=context)
    docs = get_documents(gp, work, as_of=min(now(), gp["race"]))
    for e in ordered([*document_events(docs), *_read_events(args.events)], now()):
        state.apply(e)
    finish_seen = False
    for batch in live_batches(gp, seconds=args.seconds):
        if not batch:
            continue
        previous = state.last_lap
        at = batch[0]["at"]
        for e in batch:
            state.apply(e)
            if e.get("total_laps"):
                gp["scheduled_laps"] = int(e["total_laps"])
        lap = state.last_lap
        phase = next((e["status"] for e in batch if e["kind"] == "session"), None)
        initial = any(e.get("initial") for e in batch)
        if not initial and lap > previous and lap not in published_laps:
            snap = state.snapshot("race", at, lap=lap)
            if snap is not None:
                append_cut(feed, _local_cut(bundle, snap, context, published=now()))
                published_laps.add(lap)
                _publish(feed, args.out)
                lag = next((at - e["source_at"] for e in batch if e.get("source_at") is not None), None)
                print(f"Live lap {lap} · cutoff {utc(at)} · packet lag {lag}", flush=True)
        finish_seen |= phase == "Finished"
        distance = context.total_laps
        if phase in {"Finalised", "Ends"} or (finish_seen and distance is not None and lap >= distance):
            if not initial and published_laps:
                feed["complete"] = True
                feed["updated_utc"] = utc(now())
                _publish(feed, args.out)
            break
    if not published_laps:
        raise RuntimeError("no real lap forecast published; the existing local feed was not replaced")


def parser():
    p = argparse.ArgumentParser(prog="python -m f1forecast")
    p.add_argument("--work", type=Path, default=Path(".local"))
    commands = p.add_subparsers(dest="cmd", required=True)
    dl = commands.add_parser("prepare", help="download local-only timestamped training or final data")
    dl.add_argument("--period", choices=("training", "final"), default="training")
    dl.add_argument("--refresh", action="store_true")
    fit = commands.add_parser("train", help="fit chronological folds and seal models")
    fit.add_argument("--trials", type=int, default=0, help="Rustuna trials per T-30 learner and outer fold (0: original preset)")
    commands.add_parser("evaluate", help="score the 2026 blind cohort with the sealed model")
    for name in ("forecast", "replay", "watch"):
        cmd = commands.add_parser(name)
        cmd.add_argument("--gp", required=True, help="YYYY-RR (calendar round)")
        cmd.add_argument("--out", type=Path, required=True)
        if name in {"forecast", "watch"}:
            cmd.add_argument("--events", type=Path, help="authorized pre-session JSONL events, max at=cutoff")
            cmd.add_argument("--seed-feed", type=Path, help="existing frozen forecast for the same GP")
        if name == "forecast":
            cmd.add_argument("--horizon", choices=("fp1", "pre_race"), required=True)
            cmd.add_argument("--wait", action="store_true", help="arm exact T−30 cutoff before scheduled start")
        if name == "replay":
            cmd.add_argument("--through-lap", type=int)
        if name == "watch":
            cmd.add_argument("--seconds", type=float, help="stop after this long instead of watching to the finish")
    proof = commands.add_parser("leakage")
    proof.add_argument("--gp", required=True)
    panel = commands.add_parser("panel", help="serve the live and replay dashboard on loopback only")
    panel.add_argument("--port", type=int, default=8070)
    panel.add_argument("--live", type=Path, help="watch output (default: WORK/live.json, or the bundled Las Vegas capture)")
    panel.add_argument("--replay", type=Path, help="local JSON written by replay (default: bundled São Paulo)")
    card = commands.add_parser("schedule")
    card.add_argument("--gp", required=True)
    return p


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    work = args.work
    training, final = paths(work)
    if args.cmd in {"forecast", "watch", "replay"}:
        out = args.out.resolve()
        if out.is_relative_to(SITE) and (args.cmd != "forecast" or out != UPCOMING):
            p.error(f"Pages accepts only forecast --out {UPCOMING}; use a local output for race data")
    if args.cmd == "panel":
        from .local_panel import serve_panel

        serve_panel(port=args.port, work=work, live=args.live, replay=args.replay)
        return
    if args.cmd == "schedule":
        print(json.dumps(event_card(lookup(args.gp, work)), ensure_ascii=False))
        return
    if args.cmd == "prepare":
        if args.period == "training":
            prepare(TRAIN_YEARS, work, training, refresh=args.refresh)
        else:
            sealed_model(work)
            prepare((FINAL_YEAR,), work, final, refresh=args.refresh, last_round=FINAL_LAST_ROUND)
        return
    if args.cmd == "train":
        if args.trials < 0:
            p.error("--trials must be nonnegative")
        if (work / "selection.json").exists() or final.exists():
            raise ValueError("selection is frozen; reproduce training in a clean --work directory")
        data = load_corpus(training)
        by_horizon, _ = examples(data, records_for(data))
        print(train(by_horizon, training, work, trials=args.trials)["version"])
        return
    bundle, seal = sealed_model(work)
    data = corpora(work)
    records = records_for(*data)
    if args.cmd == "evaluate":
        target = load_corpus(final)
        by_horizon, _ = examples(target, records)
        result = evaluate(by_horizon, bundle, seal["model_sha256"], work / "final.json")
        print(json.dumps({h: info["coverage"] for h, info in result["horizons"].items()}))
    elif args.cmd == "leakage":
        proof = verify_leakage(row_for(args.gp, data), records, bundle)
        write_file(work / "leakage.json", proof)
        print(json.dumps(proof))
    elif args.cmd == "replay":
        replay(args, bundle, data, records)
    elif args.cmd == "forecast":
        freeze(args, work, bundle, records)
    elif args.cmd == "watch":
        watch(args, work, bundle, records)


if __name__ == "__main__":
    main()
