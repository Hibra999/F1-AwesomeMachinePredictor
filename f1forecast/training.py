"""Chronological base fits, inner OOS calibration, outer selection and blind evaluation."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from importlib.metadata import version as package_version
import json
from pathlib import Path
from time import perf_counter

from catboost import CatBoostClassifier
import joblib
import lightgbm as lgb
from ngboost import NGBClassifier
import numpy as np
from scipy.optimize import minimize
from sklearn.impute import SimpleImputer
from sklearn.tree import DecisionTreeRegressor

from .dataset import counts
from .engine import FEATURES, GROUPS, HORIZON_GROUPS, epoch, utc
from .probability import joint, marginals, summarize

SEED = 55293
THREADS = 4
TEMPERATURES = (0.40, 0.55, 0.75, 1.0, 1.3, 1.7, 2.2, 3.0, 4.0)
HALVES = tuple(epoch(f"{y}-{m}-01T00:00:00Z") for y in range(2023, 2027) for m in ("01", "07"))[:7]
FAMILIES = ("lightgbm", "ngboost", "catboost")
PRESETS = tuple(f"{family}/{size}" for family in FAMILIES for size in ("small", "large"))
TUNED = tuple(f"{family}/rustuna" for family in FAMILIES)
BASELINE = "reference"
METRIC_NAMES = ("brier", "winner_log_loss", "winner_hit", "ordered_hit", "map_members_hit")
RACE_BINS = ("early", "middle", "late")
MODEL_SOURCES = ("archive.py", "dataset.py", "engine.py", "probability.py", "results.py", "training.py")
TPE_STARTUP_TRIALS = 4


def _weight(rows):
    counts = Counter(r.gp_id for r in rows)
    if not counts:
        raise ValueError("no complete GP groups")
    return np.array([1.0 / (len(counts) * counts[r.gp_id]) for r in rows])


def _target(row):
    return (row.rank > 0) & (row.rank <= 3)


@dataclass(slots=True)
class Learner:
    family: str
    columns: tuple[int, ...]
    estimator: object
    imputer: object = None

    def predict(self, x):
        x = x[:, self.columns]
        if self.imputer is not None:
            x = self.imputer.transform(x)
        if self.family == "lightgbm":
            p = self.estimator.booster_.predict(x, num_threads=THREADS)
        else:
            p = self.estimator.predict_proba(x)[:, 1]
        p = np.clip(p, 1e-5, 1 - 1e-5)
        return np.log(p / (1 - p))


def _columns(horizon, drop=None):
    names = {key for group in HORIZON_GROUPS[horizon] if group != drop for key in GROUPS[group]}
    return tuple(i for i, name in enumerate(FEATURES) if name in names)


def _sample(rows, horizon):
    return [r for r in rows if r.snapshot.lap in (1, 2) or r.snapshot.lap % 5 == 0] if horizon == "race" else rows


def fit(horizon, preset, rows, *, params=None):
    family, size = preset.split("/", 1)
    drop = size.split("-", 1)[1] if size.startswith("drop-") else None
    wide = size == "large"
    params = {} if params is None else params
    cols = _columns(horizon, drop)
    rows = _sample(rows, horizon)
    x = np.concatenate([r.snapshot.x[:, cols] for r in rows])
    y = np.concatenate([_target(r) for r in rows]).astype(np.int8)
    w = np.concatenate([np.full(len(r.rank), v / len(r.rank)) for r, v in zip(rows, _weight(rows))]) * len(y)
    imputer = None
    if family == "lightgbm":
        settings = dict(n_estimators=85 if wide else 55, num_leaves=19 if wide else 9,
                        learning_rate=0.045, min_child_samples=25, reg_lambda=2.0) | params
        estimator = lgb.LGBMClassifier(**settings, random_state=SEED, n_jobs=THREADS, verbosity=-1,
                                      deterministic=True, force_col_wise=True)
    elif family == "catboost":
        settings = dict(iterations=100 if wide else 65, depth=5 if wide else 4,
                        learning_rate=0.055, l2_leaf_reg=5) | params
        estimator = CatBoostClassifier(**settings, random_seed=SEED, verbose=False,
                                       thread_count=THREADS, allow_writing_files=False, task_type="CPU")
    elif family == "ngboost":
        imputer = SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True)
        x = imputer.fit_transform(x)
        settings = dict(n_estimators=55 if wide else 35, learning_rate=0.05) | params
        tree = DecisionTreeRegressor(max_depth=settings.pop("max_depth", 3 if wide else 2),
                                     min_samples_leaf=22, random_state=SEED)
        estimator = NGBClassifier(**settings, Base=tree, random_state=SEED, verbose=False)
    else:
        raise ValueError(f"unknown learner {preset}")
    estimator.fit(x, y, sample_weight=w)
    return Learner(family, cols, estimator, imputer)


def _static(name, snap):
    if name == BASELINE:
        return snap.reference
    if name == "elo":
        dr, team = (snap.x[:, FEATURES.index(f"{key}_elo")].astype(float) for key in ("driver", "team"))
        history = 0.007 * (0.62 * dr + 0.38 * team)
        return history if snap.horizon == "fp1" else history + 0.70 * snap.reference
    raise ValueError(f"unknown static candidate {name}")


def _scores(name, rows, learners):
    if name not in learners:
        return [_static(name, r.snapshot) for r in rows]
    data = np.concatenate([r.snapshot.x for r in rows])
    split = np.cumsum([len(r.rank) for r in rows][:-1])
    return np.split(learners[name].predict(data), split)


def _group(rows, indices):
    by_n = defaultdict(list)
    for i in indices:
        by_n[len(rows[i].rank)].append(i)
    return by_n.values()


def _missing(rows):
    return np.array([3 - int(_target(r).sum()) for r in rows])


def _loss_grid(rows, scores):
    losses = np.empty((len(rows), len(TEMPERATURES)))
    for ix in _group(rows, range(len(rows))):
        y = np.stack([_target(rows[i]) for i in ix])
        n = np.array([rows[i].brier_size for i in ix])
        missing = 3 - y.sum(axis=1)
        x = np.stack([scores[i] for i in ix])
        for k, t in enumerate(TEMPERATURES):
            p = marginals(x / t).sum(axis=-1)
            losses[ix, k] = (np.square(p - y).sum(axis=-1) + missing) / n
    return losses


def _best(name, weight, losses, ix):
    agg = weight @ losses[name][ix]
    k = int(agg.argmin())
    return name, TEMPERATURES[k], float(agg[k])


def _blend_weights(rows, scores, specs, ix):
    fitrows = [rows[i] for i in ix]
    weights = dict(zip(ix, _weight(fitrows)))
    terms = np.zeros((len(specs), len(specs)))
    for group in _group(rows, ix):
        y = np.stack([_target(rows[i]) for i in group])
        scale = np.sqrt([weights[i] / rows[i].brier_size for i in group])[:, None]
        errors = np.stack([(marginals(np.stack([scores[name][i] for i in group]) / temp).sum(axis=-1) - y)
                           * scale for name, temp, _ in specs]).reshape(len(specs), -1)
        terms += errors @ errors.T
    equal = np.full(len(specs), 1.0 / len(specs))
    optimum = minimize(lambda w: w @ terms @ w, equal, jac=lambda w: 2 * terms @ w,
                       bounds=[(0.0, 1.0)] * len(specs), method="SLSQP",
                       constraints=[{"type": "eq", "fun": lambda w: w.sum() - 1,
                                     "jac": lambda w: np.ones_like(w)}], options={"ftol": 1e-12})
    if not optimum.success:
        raise RuntimeError(optimum.message)
    w = optimum.x.clip(0)
    return w / w.sum()


def _recipes(rows, scores, losses, ix):
    weights = _weight([rows[i] for i in ix])
    best = {name: _best(name, weights, losses, ix) for name in scores}
    specs = [min((best[f"{fam}/small"], best[f"{fam}/large"]), key=lambda v: v[2]) for fam in FAMILIES]
    simple = [(name, t, 1.0 / len(specs)) for name, t, _ in specs]
    learned = [(name, t, float(w)) for (name, t, _), w in zip(specs, _blend_weights(rows, scores, specs, ix))]
    policies = {name: [(raw, t, 1.0)] for name, (raw, t, _) in best.items() if name in (BASELINE, "elo")}
    policies.update({name: [(raw, t, 1.0)] for name, (raw, t, _) in zip(FAMILIES, specs)})
    policies.update(average=simple, learned=learned)
    if TUNED[0] in best:
        policies.update({name: [(name, best[name][1], 1.0)] for name in (*PRESETS, *TUNED)})
    fixed = {name: (t, float(value)) for name, (_, t, value) in best.items()
             if name.startswith("lightgbm/drop-") or name == "lightgbm/small"}
    return policies, fixed


def _mass(recipe, scores, indices):
    mass = None
    for name, temp, weight in recipe:
        if weight > 0:
            p = joint(np.stack([scores[name][i] for i in indices]) / temp)
            if mass is None:
                mass = p * weight
            else:
                mass += weight * p
    return mass


def _per_cut(rows, recipes, scores):
    """Exact MAP from the joint mixture, plus per-cut raw metrics for equal-GP aggregation."""
    result = {name: [None] * len(rows) for name in recipes}
    for group in _group(rows, range(len(rows))):
        for start in range(0, len(group), 48):
            ix = group[start:start + 48]
            for name, recipe in recipes.items():
                m = _mass(recipe, scores, ix)
                n = len(rows[ix[0]].rank)
                p, mode, _ = summarize(m, n)
                for i, prob, order in zip(ix, p, mode):
                    row = rows[i]
                    y = _target(row)
                    ids = row.snapshot.ids
                    podium = prob.sum(axis=1)
                    win = prob[:, 0]
                    actual = row.rank == 1
                    log_loss = -np.log(max(float(win[actual].sum()), 1e-15))
                    predicted = tuple(ids[j] for j in order)
                    brier = (np.square(podium - y).sum() + 3 - y.sum()) / row.brier_size
                    missing = [d for d in row.official_ids if d not in ids]
                    result[name][i] = ((brier, log_loss, ids[int(win.argmax())] == row.podium_ids[0],
                                        predicted == row.podium_ids, set(predicted) == set(row.podium_ids)),
                                       (np.append(podium, np.zeros(len(missing))),
                                        np.append(y, [d in row.podium_ids for d in missing])),
                                       (np.append(win, np.zeros(len(missing))),
                                        np.append(actual, [d == row.podium_ids[0] for d in missing])))
    return result


def _aggregate(rows, predictions):
    wg = _weight(rows)
    out = {k: float(v) for k, v in zip(METRIC_NAMES, wg @ np.array([x[0] for x in predictions]))}
    out.update(gps=len({r.gp_id for r in rows}), cuts=len(rows))
    for name, col in (("podium", 1), ("winner", 2)):
        bin_w = np.zeros(10)
        bin_p = np.zeros(10)
        bin_y = np.zeros(10)
        for w, report in zip(wg, predictions):
            p, y = report[col]
            bins = np.clip((p * 10).astype(int), 0, 9)
            bin_w += np.bincount(bins, np.full(len(p), w / len(p)), minlength=10)
            bin_p += np.bincount(bins, p * w / len(p), minlength=10)
            bin_y += np.bincount(bins, y * w / len(p), minlength=10)
        out[f"{name}_ece"] = float(np.abs(bin_p - bin_y).sum())
        out[f"{name}_calibration"] = [{"bin": i, "weight": float(bin_w[i]),
                                       "p": float(bin_p[i] / bin_w[i]), "rate": float(bin_y[i] / bin_w[i])}
                                      for i in range(10) if bin_w[i] > 0]
    return out


def _report(rows, per_cut):
    out = {name: _aggregate(rows, pred) for name, pred in per_cut.items()}
    if rows[0].snapshot.horizon == "race":
        lengths = defaultdict(int)
        for row in rows:
            lengths[row.gp_id] = max(lengths[row.gp_id], row.snapshot.lap)
        split = defaultdict(list)
        for i, row in enumerate(rows):
            length = lengths[row.gp_id]
            split[RACE_BINS[min(2, 3 * (row.snapshot.lap - 1) // length)]].append(i)
    elif rows[0].snapshot.horizon == "pre_race":
        split = defaultdict(list)
        for i, row in enumerate(rows):
            split[row.snapshot.reference_name].append(i)
    else:
        split = {}
    for name, pred in per_cut.items():
        out[name]["segments"] = {key: {k: v for k, v in _aggregate(
            [rows[i] for i in ix], [pred[i] for i in ix]).items() if k in (*METRIC_NAMES, "gps", "cuts", "podium_ece")}
                                  for key, ix in split.items()}
    return out


def _active_drops(horizon, initial):
    return [f"lightgbm/drop-{group}" for group in HORIZON_GROUPS[horizon]
            if any(np.isfinite(r.snapshot.x[:, [FEATURES.index(k) for k in GROUPS[group]]]).any()
                   for r in initial)]


def _tuning_folds(rows):
    by_gp = defaultdict(list)
    for row in rows:
        by_gp[row.gp_id].append(row)
    groups = sorted(by_gp.values(), key=lambda group: (group[0].snapshot.gp["race"], group[0].gp_id))
    n = len(groups)
    if n < 4:
        raise ValueError("Rustuna needs at least four distinct training GPs")
    edges = (n // 2, 3 * n // 4, n)
    folds = []
    for start, end in zip(edges, edges[1:]):
        test = [r for group in groups[start:end] for r in group]
        cutoff = min(r.snapshot.cutoff for r in test)
        fit_rows = [r for group in groups[:start]
                    if all(max(r.label_at, r.snapshot.gp["race"]) < cutoff for r in group)
                    for r in group]
        if not fit_rows:
            raise ValueError(f"no embargo-eligible GPs for Rustuna fold at {utc(cutoff)}")
        folds.append((fit_rows, test))
    return folds


def _tune_window(rows, boundary, trials, cycles):
    from rustuna import create_study
    from rustuna.samplers import TPESampler

    folds = _tuning_folds(rows)
    oos = [r for _, test in folds for r in test]
    weights = _weight(oos)
    cycle = {"start": utc(boundary), "inner_folds": [
        {"cutoff": utc(min(r.snapshot.cutoff for r in test)),
         "fit_gps": sorted({r.gp_id for r in fit_rows}), "oos_gps": sorted({r.gp_id for r in test})}
        for fit_rows, test in folds], "studies": {}}
    cycles.append(cycle)
    results = {}
    for name in TUNED:
        study_start = perf_counter()
        history = []

        def objective(trial):
            started = perf_counter()
            params = {"learning_rate": trial.suggest_float("learning_rate", 0.025, 0.11, log=True)}
            if name == "lightgbm/rustuna":
                params.update(n_estimators=trial.suggest_int("n_estimators", 40, 100, step=5),
                              num_leaves=trial.suggest_int("num_leaves", 7, 23, step=2))
            elif name == "ngboost/rustuna":
                params.update(n_estimators=trial.suggest_int("n_estimators", 25, 65, step=5),
                              max_depth=trial.suggest_int("max_depth", 2, 4))
            else:
                params.update(iterations=trial.suggest_int("iterations", 50, 115, step=5),
                              depth=trial.suggest_int("depth", 3, 6))
            scores = []
            for fit_rows, test in folds:
                learner = fit("pre_race", name, fit_rows, params=params)
                scores.extend(_scores(name, test, {name: learner}))
            # Profile temperature for ranking trials only; recipes recalibrate on prior outer OOS.
            loss = float((weights @ _loss_grid(oos, scores)).min())
            history.append({"trial": trial.number, "params": params, "brier": loss,
                            "seconds": perf_counter() - started})
            return loss

        study = create_study(direction="minimize",
                             sampler=TPESampler(seed=SEED, n_startup_trials=TPE_STARTUP_TRIALS))
        study.optimize(objective, n_trials=trials)
        best = study.best_trial
        results[name] = best.params
        elapsed = perf_counter() - study_start
        cycle["studies"][name] = {"params": best.params, "brier": best.value,
                                  "best_trial": best.number, "seconds": elapsed, "trials": history}
        print(f"pre_race {utc(boundary)[:10]} {name}: {trials} trials, "
              f"inner Brier {best.value:.6f}, {elapsed:.2f}s", flush=True)
    return results


def _fit_horizon(horizon, examples, timings, *, trials=0):
    if not examples or any(r.snapshot.gp["year"] > 2025 for r in examples):
        raise ValueError("fit is restricted to the pre-2026 corpus")
    train_init = [r for r in examples if r.label_at < HALVES[0]]
    names = (*PRESETS, *_active_drops(horizon, train_init))
    cycles = []
    if trials:
        names += TUNED
    split_rows = []
    raw = {name: [] for name in (BASELINE, "elo", *names)}
    scopes = []
    for lower, upper in zip(HALVES, HALVES[1:]):
        train = [r for r in examples if r.label_at < lower and r.snapshot.gp["race"] < lower]
        test = [r for r in examples if lower <= r.snapshot.gp["race"] < upper]
        if not test or not train:
            raise ValueError(f"insufficient coverage for {horizon} {utc(lower)}")
        start = len(split_rows)
        split_rows.extend(test)
        scopes.append((lower, start, len(split_rows)))
        for name in (BASELINE, "elo"):
            raw[name].extend(_scores(name, test, {}))
        print(f"{horizon} {utc(lower)[:10]}: {len({r.gp_id for r in train})} GP train / {len({r.gp_id for r in test})} GP test", flush=True)
        tuned = _tune_window(train, lower, trials, cycles) if trials else {}
        for name in names:
            t0 = perf_counter()
            learner = fit(horizon, name, train, params=tuned.get(name))
            raw[name].extend(_scores(name, test, {name: learner}))
            timings[f"{horizon}:{name}"] += perf_counter() - t0
    losses = {name: _loss_grid(split_rows, s) for name, s in raw.items()}
    all_rows = []
    reports = defaultdict(list)
    abl = defaultdict(list)
    folds = []
    for lower, start, end in scopes[2:]:
        inner = [i for i in range(start) if split_rows[i].label_at < lower]
        policies, drops = _recipes(split_rows, raw, losses, inner)
        test = split_rows[start:end]
        per_cut = _per_cut(test, policies, {name: scores[start:end] for name, scores in raw.items()})
        for name, pred in per_cut.items():
            reports[name].extend(pred)
        for name, (temp, _) in drops.items():
            abl[name].extend(losses[name][start:end, TEMPERATURES.index(temp)])
        all_rows.extend(test)
        folds.append({"start": utc(lower), "inner_oos_gps": len({split_rows[i].gp_id for i in inner}),
                      "test_gps": len({r.gp_id for r in test}), "recipes": policies})
    validation = _report(all_rows, reports)
    selected = min(validation, key=lambda name: (validation[name]["brier"], validation[name]["winner_log_loss"]))
    base = "lightgbm/small"
    w = _weight(all_rows)
    ablations = {name.removeprefix("lightgbm/drop-"): float(w @ (np.array(loss) - abl[base]))
                 for name, loss in abl.items() if name != base}
    boundary = HALVES[-1]
    final_inner = [i for i, r in enumerate(split_rows) if r.label_at < boundary]
    final_recipes, _ = _recipes(split_rows, raw, losses, final_inner)
    train = [r for r in examples if max(r.label_at, r.snapshot.gp["race"]) < boundary]
    tuned = _tune_window(train, boundary, trials, cycles) if trials else {}
    learners = {}
    for recipe in final_recipes.values():
        for name, _, _ in recipe:
            if name in names and name not in learners:
                learners[name] = fit(horizon, name, train, params=tuned.get(name))
    report = {"validation": validation, "selected": selected, "recipes_for_final": final_recipes,
              "ablation_delta_brier_when_removed": ablations, "folds": folds,
              "coverage": counts({horizon: all_rows})[horizon]}
    if cycles:
        report["rustuna_cycles"] = cycles
    return {"selected": selected, "recipes": final_recipes, "learners": learners}, report


def digest(path: Path):
    with path.open("rb") as fh:
        return sha256(fh.read()).hexdigest()


def _store_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def train(by_horizon, corpus_path: Path, work: Path, *, trials: int = 0):
    """Seal the walk-forward winner, optionally tuning T−30 trials per family and window."""
    if trials < 0:
        raise ValueError("trials must be non-negative")
    models, reports = {}, {}
    timings = defaultdict(float)
    t0 = perf_counter()
    for horizon in HORIZON_GROUPS:
        models[horizon], reports[horizon] = _fit_horizon(
            horizon, by_horizon[horizon], timings, trials=trials if horizon == "pre_race" else 0)
    training_sha = digest(corpus_path)
    recipes = {h: (v["selected"], v["recipes"]) for h, v in models.items()}
    impl = {name: digest(Path(__file__).with_name(name)) for name in MODEL_SOURCES}
    identity = [training_sha, recipes, SEED, impl]
    if trials:
        cycles = reports["pre_race"]["rustuna_cycles"]
        tuning = {"backend": "rustuna", "version": package_version("rustuna"), "seed": SEED,
                  "sampler": "TPE", "startup_trials": TPE_STARTUP_TRIALS, "trials_per_family": trials,
                  "objective": "minimum GP-weighted podium Brier over the inner OOS temperature grid",
                  "temperatures": TEMPERATURES}
        identity.extend((tuning, {name: s["params"] for name, s in cycles[-1]["studies"].items()}))
    key = json.dumps(identity, sort_keys=True).encode()
    version = "pl-" + sha256(key).hexdigest()[:12]
    bundle = {"version": version, "horizons": models, "training_sha256": training_sha, "seed": SEED}
    work.mkdir(parents=True, exist_ok=True)
    model_path = work / "model.joblib"
    joblib.dump(bundle, model_path, compress=3)
    selection = {"version": version, "model_sha256": digest(model_path), "training_sha256": training_sha,
                 "seed": SEED, "implementation_sha256": impl, "fit_utc": utc(datetime.now(UTC).timestamp()),
                 "train_max_time_exclusive": utc(HALVES[-1]), "wall_seconds": perf_counter() - t0,
                 "model_seconds": dict(timings), "horizons": reports}
    if trials:
        selection["tuning"] = tuning
    _store_json(work / "selection.json", selection)
    return selection


def sealed_model(work):
    selection = json.loads((work / "selection.json").read_text(encoding="utf-8"))
    path = work / "model.joblib"
    if digest(path) != selection["model_sha256"]:
        raise ValueError("sealed model file does not match selection")
    return joblib.load(path), selection


def evaluate(by_horizon, bundle, model_sha, target):
    result = {"version": bundle["version"], "model_sha256": model_sha, "horizons": {}}
    for h, config in bundle["horizons"].items():
        rows = by_horizon[h]
        names = {name for specs in config["recipes"].values() for name, _, _ in specs}
        scores = {name: _scores(name, rows, config["learners"]) for name in names}
        result["horizons"][h] = {"selected": config["selected"], "coverage": counts({h: rows})[h],
                                 "metrics": _report(rows, _per_cut(rows, config["recipes"], scores))}
    _store_json(target, result)
    return result


def distribution(bundle, snapshot):
    """Serving uses the very same score, temperature and joint mixture as evaluation."""
    h = bundle["horizons"][snapshot.horizon]
    recipe = h["recipes"][h["selected"]]
    mass = np.zeros(len(snapshot.ids) * (len(snapshot.ids) - 1) * (len(snapshot.ids) - 2))
    for name, temp, w in recipe:
        if w <= 0:
            continue
        learner = h["learners"].get(name)
        scores = learner.predict(snapshot.x) if learner else _static(name, snapshot)
        mass += w * joint(scores / temp)
    return mass
