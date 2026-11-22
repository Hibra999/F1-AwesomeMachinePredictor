"""Offline-only study of timing-position gains; never imported by the forecast."""

from collections import Counter, defaultdict
from dataclasses import dataclass
import json
from pathlib import Path

from lightgbm import LGBMClassifier
import numpy as np
from sklearn.metrics import roc_auc_score
from threadpoolctl import threadpool_limits

from .dataset import TRAIN_YEARS, examples, load_corpus, records_for, snapshots
from .engine import FEATURES, GROUPS, epoch
from .probability import marginals


_CORPUS = Path(".local/validation-data.joblib")
_REPORT = Path(".local/gain-report.json")
_SEED = 55293
_BLOCKS = tuple(epoch(f"{year}-{month}-01T00:00:00Z")
                for year, month in ((2022, "07"), (2023, "01"), (2023, "07"), (2024, "01")))
_CALIBRATION_START, _TEST_START = _BLOCKS[-2:]
_TEMPERATURES = (0.40, 0.55, 0.75, 1.0, 1.3, 1.7, 2.2, 3.0, 4.0)
_POSITION = FEATURES.index("position")
_GAIN_COLS = [i for i, key in enumerate(FEATURES) if key not in GROUPS["circuit"]]


@dataclass(slots=True)
class _RacePairs:
    gp: dict
    ends_at: float
    x: np.ndarray
    y: np.ndarray
    weights: np.ndarray
    cuts: int


def _race_pairs(rows, records):
    pairs, coverage = [], Counter()
    for row in rows:
        current = None
        xs, ys = [], []
        for nxt in snapshots(row, records):
            if nxt.horizon != "race":
                continue
            previous, current = current, nxt
            if previous is None:
                continue
            if previous.cutoff >= current.cutoff or previous.lap >= current.lap:
                raise ValueError("RaceState race cuts must have increasing receipt times and laps")
            if previous.ids != current.ids:
                coverage["roster_change_pairs_excluded"] += 1
                continue
            coverage["retained_pairs_skipping_laps"] += current.lap - previous.lap > 1
            xs.append(previous.x)
            ys.append(current.x[:, _POSITION] < previous.x[:, _POSITION])
        if xs:
            weights = np.repeat([1 / (len(xs) * len(x)) for x in xs], [len(x) for x in xs])
            pairs.append(_RacePairs(row["gp"], current.cutoff, np.concatenate(xs), np.concatenate(ys),
                                    weights, len(xs)))
        else:
            coverage["gps_without_usable_pairs"] += 1
    return pairs, dict(coverage)


def _fit(x, y, weights):
    if not y.any() or y.all():
        return None
    model = LGBMClassifier(n_estimators=35, num_leaves=9, learning_rate=0.075, min_child_samples=25,
                           reg_lambda=2.0, random_state=_SEED, n_jobs=1, verbosity=-1,
                           deterministic=True, force_col_wise=True)
    model.fit(x, y, sample_weight=weights / weights.mean())
    return model.booster_


def _gain_fit(races, before):
    train = [r for r in races if r.gp["race"] < before and r.ends_at < before]
    if not train:
        return None, None, 0
    x = np.concatenate([r.x for r in train])
    y = np.concatenate([r.y for r in train])
    w = np.concatenate([r.weights for r in train])
    movable = x[:, _POSITION] > 1
    prior = float(np.average(y[movable], weights=w[movable]))
    return _fit(x[:, _GAIN_COLS], y, w), prior, len(train)


def _gain_predict(model, x):
    p = model.predict(x[:, _GAIN_COLS], num_threads=1)
    p[x[:, _POSITION] == 1] = 0.0  # First cannot improve its position.
    return p


def _gain_test(model, prior, races):
    if not races:
        return []
    x = np.concatenate([r.x for r in races])
    endpoints = np.cumsum([len(r.y) for r in races[:-1]])
    pred = np.split(_gain_predict(model, x), endpoints)
    ref = np.split(prior * (x[:, _POSITION] > 1), endpoints)
    return list(zip(races, pred, ref, strict=True))


def _gain_metrics(chunks):
    if not chunks:
        return None
    y = np.concatenate([r.y for r, _, _ in chunks])
    w = np.concatenate([r.weights for r, _, _ in chunks])
    p = np.concatenate([p for _, p, _ in chunks])
    ref = np.concatenate([ref for _, _, ref in chunks])
    return {"gps": len(chunks), "cuts": sum(r.cuts for r, _, _ in chunks), "driver_pairs": len(y),
            "positive_rate": float(np.average(y, weights=w)),
            "mean_probability": float(np.average(p, weights=w)),
            "roc_auc": float(roc_auc_score(y, p, sample_weight=w)) if y.any() and not y.all() else None,
            "brier": float(np.average((p - y) ** 2, weights=w)),
            "prior_brier": float(np.average((ref - y) ** 2, weights=w))}


def _cut_weights(rows):
    counts = Counter(r.gp_id for r in rows)
    return np.array([1 / counts[r.gp_id] for r in rows])


def _podium_data(rows):
    x = np.concatenate([r.snapshot.x for r in rows])
    y = np.concatenate([(r.rank > 0) & (r.rank <= 3) for r in rows])
    return x, y


def _with_gain(x, model):
    return np.column_stack((x, _gain_predict(model, x)))


def _podium_losses(rows, flat_scores, temperatures):
    groups = defaultdict(list)
    n = [len(r.rank) for r in rows]
    scores = np.split(flat_scores, np.cumsum(n[:-1]))
    for i, size in enumerate(n):
        groups[size].append(i)
    losses = np.empty((len(rows), len(temperatures)))
    for indices in groups.values():
        group = [rows[i] for i in indices]
        y = np.stack([(r.rank > 0) & (r.rank <= 3) for r in group])
        missing = 3 - y.sum(axis=1)
        denom = np.array([r.brier_size for r in group])
        x = np.stack([scores[i] for i in indices])
        for j, temp in enumerate(temperatures):
            p = marginals(x / temp).sum(axis=-1)
            losses[indices, j] = (((p - y) ** 2).sum(axis=1) + missing) / denom
    return losses


def _downstream(races, train, test, outer_gain):
    fit_rows, fit_blocks, fit_targets, fit_gps = [], [], [], []
    calibration = None
    blocks = []
    for lo, hi in zip(_BLOCKS, _BLOCKS[1:]):
        is_cal = lo == _CALIBRATION_START
        rows = [r for r in train if lo <= r.snapshot.gp["race"] < hi
                and r.label_at < (_TEST_START if is_cal else _CALIBRATION_START)
                and (is_cal or r.snapshot.lap in (1, 2) or r.snapshot.lap % 5 == 0)]
        if not rows:
            continue
        gain, _, gps = _gain_fit(races, lo)
        if gain is None:
            continue
        x, y = _podium_data(rows)
        x = _with_gain(x, gain)
        n_gps = len({r.gp_id for r in rows})
        blocks.append({"purpose": "temperature" if is_cal else "podium_fit", "starts_at_utc_epoch": lo,
                       "gain_train_gps": gps, "podium_gps": n_gps, "cuts": len(rows)})
        if is_cal:
            calibration = rows, x
        else:
            fit_rows.extend(rows)
            fit_blocks.append(x)
            fit_targets.append(y)
            fit_gps.append(n_gps)

    scope = {"inner_blocks": blocks, "podium_train_gps": sum(fit_gps),
             "podium_train_cuts": len(fit_rows), "eligible_test_gps": len({r.gp_id for r in test})}
    if not fit_rows or calibration is None or not test:
        return {**scope, "reason": "insufficient chronologically cross-fitted train, calibration or test cuts"}, []
    train_x = np.concatenate(fit_blocks)
    train_y = np.concatenate(fit_targets)
    n = np.array([len(r.rank) for r in fit_rows])
    w = np.repeat(_cut_weights(fit_rows) / n, n)
    cal_rows, cal_x = calibration
    cal_weight = _cut_weights(cal_rows)
    test_x = _with_gain(_podium_data(test)[0], outer_gain)
    arms, test_losses = {}, []
    for name, limit in (("without_gain", -1), ("with_gain", train_x.shape[1])):
        model = _fit(train_x[:, :limit], train_y, w)
        if model is None:
            return {**scope, "reason": "podium training needs both classes"}, []
        scores = model.predict(cal_x[:, :limit], raw_score=True, num_threads=1)
        briers = np.average(_podium_losses(cal_rows, scores, _TEMPERATURES), axis=0, weights=cal_weight)
        best = int(briers.argmin())
        temp = _TEMPERATURES[best]
        scores = model.predict(test_x[:, :limit], raw_score=True, num_threads=1)
        test_losses.append(_podium_losses(test, scores, (temp,))[:, 0])
        arms[name] = {"temperature": temp, "calibration_brier": float(briers[best])}

    by_gp = defaultdict(list)
    for row, losses in zip(test, np.column_stack(test_losses), strict=True):
        by_gp[(row.gp_id, row.snapshot.gp["circuit"])].append(losses)
    paired = [(circuit, np.mean(losses, axis=0), len(losses)) for (_, circuit), losses in by_gp.items()]
    return {**scope, **arms, **_podium_metrics(paired)}, paired


def _podium_metrics(paired):
    by_circuit = defaultdict(list)
    losses = np.stack([loss for _, loss, _ in paired])
    mean = losses.mean(axis=0)
    for (circuit, _, _), delta in zip(paired, losses[:, 1] - losses[:, 0], strict=True):
        by_circuit[circuit].append(delta)
    interval = None
    if len(by_circuit) >= 3:
        groups = [by_circuit[c] for c in sorted(by_circuit)]
        sums = np.array([sum(g) for g in groups])
        sizes = np.array([len(g) for g in groups])
        draw = np.random.default_rng(_SEED).integers(len(groups), size=(2000, len(groups)))
        interval = np.quantile(sums[draw].sum(axis=1) / sizes[draw].sum(axis=1), [0.025, 0.975]).tolist()
    return {"gps": len(paired), "cuts": sum(n for _, _, n in paired), "circuits": len(by_circuit),
            "test_brier_without_gain": float(mean[0]), "test_brier_with_gain": float(mean[1]),
            "delta_brier_with_minus_without": float(mean[1] - mean[0]), "delta_circuit_bootstrap_95": interval}


def _study(rows):
    if not rows or any(r["gp"]["year"] not in TRAIN_YEARS for r in rows):
        raise ValueError("gain study requires only the 2022–2025 validation corpus")
    records = records_for(rows)
    if any(r["year"] not in TRAIN_YEARS for r in records):
        raise ValueError("gain study timing history must also be pre-2026")
    pairs, coverage = _race_pairs(rows, records)
    by_horizon, skipped = examples(rows, records)
    official = by_horizon.get("race", [])
    circuit_list = sorted({r["gp"]["circuit"] for r in rows if r["gp"]["race"] >= _TEST_START})
    np.random.default_rng(_SEED).shuffle(circuit_list)
    folds, proxy_results, podium_results = [], [], []
    for i in range(3):
        held = set(circuit_list[i::3])
        train_pairs = [r for r in pairs if r.gp["circuit"] not in held and r.gp["race"] < _TEST_START]
        test_pairs = [r for r in pairs if r.gp["circuit"] in held and r.gp["race"] >= _TEST_START]
        train = [r for r in official if r.snapshot.gp["circuit"] not in held and r.snapshot.gp["race"] < _TEST_START]
        test = [r for r in official if r.snapshot.gp["circuit"] in held and r.snapshot.gp["race"] >= _TEST_START]
        gain, prior, n_train = _gain_fit(train_pairs, _TEST_START)
        fold = {"held_out_circuits": sorted(held), "gain_train_gps": n_train,
                "eligible_gain_test_gps": len(test_pairs), "gain_train_prior_given_not_first": prior}
        if gain is not None:
            predictions = _gain_test(gain, prior, test_pairs)
            proxy_results.extend(predictions)
            fold["proxy"] = _gain_metrics(predictions)
            fold["downstream"], paired = _downstream(train_pairs, train, test, gain)
            podium_results.extend(paired)
        else:
            fold.update(proxy=None, downstream={"reason": "insufficient pre-test proxy training classes"})
        folds.append(fold)

    podium = _podium_metrics(podium_results) if podium_results else None
    all_tested = all(f["proxy"] and "test_brier_with_gain" in f["downstream"] for f in folds)
    interval = podium["delta_circuit_bootstrap_95"] if podium else None
    improves = all_tested and interval is not None and interval[1] < 0
    return {"target": "timing position at the next observed race cut is numerically lower than at the current cut",
            "not_an_overtake_label": True, "selected_for_forecast": False, "seed": _SEED,
            "limitations": ["Position gains can arise from pits, retirements, corrections or timing order; not passes.",
                            "Proxy uses only same-roster adjacent RaceState cuts and prior timing records; official ranks enter only the podium comparison.",
                            "No model fit uses held-out circuit rows; causal timing history may still include past rounds from any circuit.",
                            "Both podium models use the same GP and cuts, and calibration uses later pre-test blocks only.",
                            "Circuit bootstrap is descriptive for these historical splits and excludes model refitting uncertainty."],
            "corpus_years": sorted({r["gp"]["year"] for r in rows}), "corpus_gps": len(rows),
            "test_years": [2024, 2025], "expected_test_gps": sum(r["gp"]["race"] >= _TEST_START for r in rows),
            "proxy_coverage": coverage, "official_examples_skipped": skipped, "folds": folds,
            "proxy": _gain_metrics(proxy_results), "downstream": podium,
            "downstream_status": "improvement_on_historical_holdouts_only" if improves else "improvement_not_demonstrated"}


def main():
    # Fixed validation-only path: no final-corpus option, network reads, or model writes.
    with threadpool_limits(limits=1):
        result = _study(load_corpus(_CORPUS))
    _REPORT.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
