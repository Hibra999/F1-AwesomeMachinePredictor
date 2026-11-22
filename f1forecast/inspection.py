"""Reproduce the public aggregate inspection without loading final data or model weights."""

from collections import Counter
from hashlib import sha256
import json
from math import isclose, isfinite
from pathlib import Path

import numpy as np

from .dataset import TRAIN_YEARS, examples, load_corpus, records_for
from .engine import FEATURES, GROUPS, HORIZON_GROUPS, utc
from .gain_study import _podium_losses
from .publication import read_file, write_file
from .training import HALVES, TEMPERATURES, _sample, _tuning_folds, _weight, digest, fit

_CORPUS = Path(".local/validation-data.joblib")
_RUSTUNA = Path(".local/rustuna")
_PUBLISHED = Path("reports/rustuna-t30.json")
_REPORT = Path("reports/analysis-inspection.json")
_PRESET = "lightgbm/small"
_PARAMS = {
    "lightgbm/rustuna": ("learning_rate", "n_estimators", "num_leaves"),
    "ngboost/rustuna": ("learning_rate", "max_depth", "n_estimators"),
    "catboost/rustuna": ("depth", "iterations", "learning_rate"),
}


def _coverage(rows):
    return {"gps": len({r.gp_id for r in rows}), "cuts": len(rows),
            "driver_rows": sum(len(r.snapshot.x) for r in rows)}


def _fit_before(rows, cutoff):
    return [r for r in rows if max(r.label_at, r.snapshot.gp["race"]) < cutoff]


def _importance(horizon, rows, preset=_PRESET, params_by_start=None):
    previous, previous_losses, folds = [], [], []
    deltas, evidence_gps = Counter(), Counter()
    baseline, held_out = 0.0, []
    for lower, upper in zip(HALVES, HALVES[1:]):
        train = _fit_before(rows, lower)
        test = [r for r in rows if lower <= r.snapshot.gp["race"] < upper]
        params = params_by_start[utc(lower)] if params_by_start is not None else None
        learner = fit(horizon, preset, train, params=params)
        x = np.concatenate([r.snapshot.x for r in test])
        scores = learner.predict(x)
        losses = _podium_losses(test, scores, TEMPERATURES)
        if lower >= HALVES[2]:
            calibration = [i for i, r in enumerate(previous) if r.label_at < lower]
            cal_rows = [previous[i] for i in calibration]
            grid = np.concatenate(previous_losses)
            k = int((_weight(cal_rows) @ grid[calibration]).argmin())
            temp = TEMPERATURES[k]
            original = losses[:, k]
            n_gps = _coverage(test)["gps"]
            weights = _weight(test) * n_gps
            baseline += float(weights @ original)
            held_out.extend(test)
            sampled_train = _sample(train, horizon)
            # Every scored arm uses the same cuts, temperature and weights.
            offsets = np.cumsum([0, *(len(r.snapshot.x) for r in test[:-1])])
            for col in learner.columns:
                values = x[:, col].copy()
                present = np.isfinite(values)
                if not present.any() or not any(np.isfinite(r.snapshot.x[:, col]).any() for r in sampled_train):
                    continue
                evidence_gps[col] += len({r.gp_id for r, n in zip(test, np.add.reduceat(present, offsets), strict=True) if n})
                x[:, col] = np.nan
                masked = _podium_losses(test, learner.predict(x), (temp,))[:, 0]
                x[:, col] = values
                deltas[col] += float(weights @ (masked - original))
            folds.append({"start": utc(lower), "fit_gps": _coverage(train)["gps"],
                          "fit_cuts": len(sampled_train), "calibration_gps": _coverage(cal_rows)["gps"],
                          "oos_gps": n_gps, "oos_cuts": len(test), "temperature": temp})
        previous.extend(test)
        previous_losses.append(losses)
    coverage = _coverage(held_out)
    n_gps = coverage["gps"]
    return ({**coverage, "baseline_brier": baseline / n_gps, "folds": folds},
            {col: deltas[col] / n_gps for col in evidence_gps}, evidence_gps)


def _eda_summary(horizon, rows, importance, deltas, evidence_gps):
    x = np.concatenate([r.snapshot.x for r in rows])
    entrants = Counter(len(r.snapshot.x) for r in rows)
    features = []
    for group in HORIZON_GROUPS[horizon]:
        for name in GROUPS[group]:
            i = FEATURES.index(name)
            values = x[:, i]
            observed = values[np.isfinite(values)]
            missing = len(values) - len(observed)
            p10, median, p90 = np.quantile(observed.astype(float), (0.1, 0.5, 0.9)).tolist() if observed.size else (None,) * 3
            features.append({"name": name, "group": group, "n": len(values), "missing": missing,
                             "missing_pct": 100 * missing / len(values), "p10": p10, "median": median, "p90": p90,
                             "oos_delta_brier": deltas.get(i), "oos_evidence_gps": evidence_gps.get(i, 0)})
    return {**_coverage(rows), "entrants": [{"drivers": n, "cuts": count} for n, count in sorted(entrants.items())],
            "by_year": [{"year": year, **_coverage([r for r in rows if r.snapshot.gp["year"] == year])}
                        for year in TRAIN_YEARS],
            "features": features, "importance_coverage": importance}


def _verify_seal(selection, published):
    tuning = selection["tuning"]
    recipes = {h: (r["selected"], r["recipes_for_final"]) for h, r in selection["horizons"].items()}
    cycles = selection["horizons"]["pre_race"]["rustuna_cycles"]
    identity = [selection["training_sha256"], recipes, selection["seed"], selection["implementation_sha256"],
                tuning, {name: s["params"] for name, s in cycles[-1]["studies"].items()}]
    seal = "pl-" + sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:12]
    if not (seal == selection["version"] == published["experimental_seal"]
            and digest(_RUSTUNA / "model.joblib") == selection["model_sha256"] == published["experimental_model_sha256"]
            and digest(_CORPUS) == selection["training_sha256"] == published["training_corpus_sha256"]
            and utc(HALVES[-1]) == selection["train_max_time_exclusive"] == published["training_cutoff_exclusive_utc"]
            and selection["seed"] == tuning["seed"] == published["search"]["seed"]
            and tuning["trials_per_family"] == published["search"]["trials_per_family_per_window"]):
        raise ValueError("Rustuna seal or search settings do not match the published experiment")


def _trial_data(raw, name):
    data = {key: raw[key] for key in ("params", "brier", "seconds")}
    params = data["params"]
    if (params.keys() != set(_PARAMS[name]) or
            any(type(v) not in (int, float) or not isfinite(v) for v in data.values() if v is not params) or
            any(type(v) not in (int, float) or not isfinite(v) for v in params.values()) or
            not 0 <= data["brier"] <= 1 or data["seconds"] < 0):
        raise ValueError(f"invalid Rustuna trial data for {name}")
    return data


def _study(raw, name, n_trials):
    trials = [{"trial": t["trial"], **_trial_data(t, name)} for t in raw["trials"]]
    if (any(type(t["trial"]) is not int for t in trials) or
            [t["trial"] for t in trials] != list(range(n_trials)) or
            type(raw["best_trial"]) is not int or not 0 <= raw["best_trial"] < n_trials):
        raise ValueError(f"incomplete Rustuna trial history for {name}")
    selected = _trial_data(raw, name)
    best = trials[raw["best_trial"]]
    if not (best["params"] == selected["params"] and best["brier"] == selected["brier"] == min(t["brier"] for t in trials)):
        raise ValueError(f"invalid Rustuna best trial for {name}")
    return {**selected, "best_trial": best["trial"], "trials": trials}


def _rustuna_cycles(selection, published, rows):
    search = published["search"]
    cycles = selection["horizons"]["pre_race"]["rustuna_cycles"]
    if len(cycles) != len(HALVES) or len(cycles) != search["outer_windows_including_calibration_and_final_refit"]:
        raise ValueError("Rustuna window count does not match the published experiment")
    result, total = [], 0
    for cutoff, cycle in zip(HALVES, cycles, strict=True):
        fit_rows = _fit_before(rows, cutoff)
        folds = [{"cutoff": utc(min(r.snapshot.cutoff for r in oos)),
                  "fit_gps": sorted({r.gp_id for r in train}), "oos_gps": sorted({r.gp_id for r in oos})}
                 for train, oos in _tuning_folds(fit_rows)]
        if (cycle["start"] != utc(cutoff) or folds != cycle["inner_folds"] or
                len(folds) != search["chronological_gp_splits_per_study"] or cycle["studies"].keys() != _PARAMS.keys()):
            raise ValueError(f"Rustuna window {utc(cutoff)} does not match the eligible GP splits and families")
        studies = {name: _study(study, name, search["trials_per_family_per_window"]) for name, study in cycle["studies"].items()}
        total += sum(len(study["trials"]) for study in studies.values())
        result.append({"start": cycle["start"], "fit_gps": _coverage(fit_rows)["gps"],
                       "inner_folds": [{"cutoff": f["cutoff"], "fit_gps": len(f["fit_gps"]), "oos_gps": len(f["oos_gps"])}
                                       for f in folds], "studies": studies})
    if total != search["trials_total"]:
        raise ValueError("Rustuna trial count does not match the published experiment")
    return result


def main():
    selection, published = read_file(_RUSTUNA / "selection.json"), read_file(_PUBLISHED)
    _verify_seal(selection, published)
    corpus = load_corpus(_CORPUS)
    records = records_for(corpus)
    if not corpus or any(r["gp"]["year"] not in TRAIN_YEARS for r in corpus) or any(r["year"] not in TRAIN_YEARS for r in records):
        raise ValueError("inspection requires only the 2022–2025 validation corpus")
    by_horizon, skipped = examples(corpus, records)
    cycles = _rustuna_cycles(selection, published, by_horizon["pre_race"])
    horizons = {}
    for horizon in HORIZON_GROUPS:
        rows = by_horizon[horizon]
        importance, deltas, evidence = _importance(horizon, rows)
        expected = selection["horizons"][horizon]["coverage"]
        if (importance["gps"], importance["cuts"]) != (expected["gps"], expected["cuts"]):
            raise ValueError(f"inspection OOS coverage does not match the selection: {horizon}")
        horizons[horizon] = _eda_summary(horizon, rows, importance, deltas, evidence)
        print(f"{horizon}: {importance['gps']} OOS GP, {importance['cuts']} cuts, "
              f"baseline Brier {importance['baseline_brier']:.6f}", flush=True)
    tuned_importance = {}
    tuned_validation = selection["horizons"]["pre_race"]["validation"]
    for preset in _PARAMS:
        params = {c["start"]: c["studies"][preset]["params"] for c in cycles}
        coverage, deltas, evidence = _importance("pre_race", by_horizon["pre_race"], preset, params)
        published_score = tuned_validation[preset]
        if (any(coverage[k] != published_score[k] for k in ("gps", "cuts"))
                or not isclose(coverage["baseline_brier"], published_score["brier"], abs_tol=1e-12, rel_tol=0)):
            raise ValueError(f"importance did not reproduce the Rustuna baseline: {preset}")
        tuned_importance[preset] = {
            **coverage, "features": [{"name": FEATURES[col], "oos_delta_brier": delta,
                                      "oos_evidence_gps": evidence[col]} for col, delta in deltas.items()],
        }
        print(f"{preset}: OOS baseline Brier {coverage['baseline_brier']:.6f}", flush=True)
    write_file(_REPORT, {"eda": {
        "period": {"start_year": TRAIN_YEARS[0], "end_year": TRAIN_YEARS[-1]},
        "importance": {"method": "Neutralizar una variable a faltante en predicción y restar el Brier original",
                       "period": {"start_year": 2024, "end_year": 2025}, "reference": _PRESET,
                       "notes": [
                           "LightGBM pequeño fijo; semestres cronológicos con embargo de etiquetas.",
                           "La temperatura Plackett–Luce se elige en cortes OOS anteriores y se mantiene al enmascarar.",
                           "Brier de podio promedio por piloto, corte y GP, con el mismo peso por GP.",
                           "Se puntúan todos los cortes elegibles. El ajuste de carrera usa vueltas 1, 2 y múltiplos de 5.",
                           "Se omite el Δ si no hay ningún bloque con datos finitos tanto en ajuste como en evaluación.",
                           "El Δ mide sensibilidad de este LightGBM; no indica causalidad ni explica Elo.",
                       ]},
        "horizons": horizons, "examples_skipped": skipped,
    }, "rustuna_cycles": cycles, "rustuna_importance": tuned_importance})
    print(f"{_REPORT}: {len(cycles)} Rustuna windows, {published['search']['trials_total']} trials; seal {selection['version']}")


if __name__ == "__main__":
    main()
