"""Retrospective T−30 study. No serving path, final-period input, or model artifact."""

import argparse
from collections import Counter
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from threadpoolctl import threadpool_limits

from . import training as sealed
from .dataset import Example, LABEL_EMBARGO, TRAIN_YEARS, load_corpus, records_for
from .engine import FEATURES, asof, epoch, utc
from .publication import pre_race_events


INPUTS = ('reference', 'driver_elo', 'team_elo', 'driver_form', 'team_form', 'qual_gap', 'practice_gap')
COLUMNS = [FEATURES.index(k) for k in INPUTS[1:]]
BASELINES = ('elo', 'reference')
ARMS = ('rank_pl', 'rank_pl_taf')
RIDGE = 0.05
DRAWS = 10_000


def _prefix(rank):
    prefix = []
    for pos in (1, 2, 3):
        ix = np.flatnonzero(rank == pos)
        if not len(ix):
            break
        prefix.append(int(ix.item()))
    return prefix


def _raw(row):
    s = row.snapshot
    return np.column_stack((s.reference, s.x[:, COLUMNS])).astype(float, copy=False)


def _scaler(matrices):
    x = np.concatenate(matrices)
    median = np.array([np.median(v[np.isfinite(v)]) if np.isfinite(v).any() else 0.0 for v in x.T])
    scale = np.where(np.isfinite(x), x, median).std(axis=0)
    return median, np.where(scale > 0, scale, 1.0)


def _design(raw, scaler, wet):
    median, scale = scaler
    x = (np.where(np.isfinite(raw), raw, median) - median) / scale
    # None selects the primary arm; False includes the neutral secondary interaction.
    return x if wet is None else np.column_stack((x, x[:, 0] * wet))


def _stages(matrices, prefixes):
    stages = []
    for x, prefix in zip(matrices, prefixes, strict=True):
        available = np.ones(len(x), dtype=bool)
        for winner in prefix:
            # Subtract observed choice: stage NLL = logsumexp of relative scores.
            stages.append((x[available] - x[winner], 1 / (len(prefixes) * len(prefix))))
            available[winner] = False
    return stages


def _objective(beta, stages):
    loss = RIDGE * (beta @ beta) / 2
    gradient = RIDGE * beta
    for dx, weight in stages:
        score = dx @ beta
        shift = score.max()
        ex = np.exp(score - shift)
        total = ex.sum()
        loss += weight * (shift + np.log(total))
        gradient += (weight / total) * (ex @ dx)
    return loss, gradient


def _scope(rows, lower, upper):
    test = [r for r in rows if lower <= r.snapshot.gp['race'] < upper]
    if not test:
        raise ValueError(f'empty semester: {utc(lower)}')
    cutoff = min(lower, *(r.snapshot.cutoff for r in test))
    train = [r for r in rows if max(r.snapshot.gp['race'], r.label_at) < cutoff]
    return train, test, cutoff


def _example(item, records, gp):
    cutoff = gp['race'] - 1800
    events = pre_race_events(item['events'], gp['id'], cutoff)
    snap = asof(gp, records, events, 'pre_race', cutoff)
    if snap is None:
        raise ValueError(f"{gp['id']}: insufficient entrant evidence at T−30")

    label = item['label']
    official = label['labels']
    podium = sorted((p, code) for code, p in official.items() if p in (1, 2, 3))
    if [p for p, _ in podium] != [1, 2, 3]:
        raise ValueError(f"{gp['id']}: incomplete official podium")
    at = label.get('available_at')
    label_at = at if isinstance(at, (int, float)) else gp['race'] + LABEL_EMBARGO
    return Example(snap, np.array([official.get(d, 0) for d in snap.ids], dtype=np.int16),
                   tuple(d for _, d in podium), tuple(official), label_at)


def _clock_examples(corpus, repository):
    from .study_clock import schedule_asof

    records = records_for(corpus)
    rows, audit = [], {}
    for item in corpus:
        gp = item['gp']
        try:
            clock = schedule_asof(gp, repository)
            race, revision = epoch(clock['race_utc']), epoch(clock['revision_utc'])
            if revision >= race - 1800 or clock['status'] not in ('confirmed', 'corrected'):
                raise ValueError('no eligible schedule revision')
        except (KeyError, ValueError) as exc:
            raise ValueError(f"{gp['id']}: {exc}") from exc
        evidence = {k: clock[k] for k in ('status', 'revision', 'revision_utc')}
        if race != gp['race']:
            evidence.update(original_race_utc=utc(gp['race']), corrected_race_utc=utc(race))
        audit[gp['id']] = evidence
        rows.append(_example(item, records, {**gp, 'race': race}))
    return rows, audit


def _coverage(rows, weather):
    states = Counter()
    leads = []
    for r in rows:
        data = weather.get(r.gp_id, {})
        wet = data.get('wet')
        for key in ('issued_utc', 'available_utc'):
            if data.get(key) and epoch(data[key]) >= r.snapshot.cutoff:
                raise ValueError(f'future TAF for {r.gp_id}: {key}')
        if wet is None:
            states['unknown'] += 1
            continue
        if type(wet) is not bool or not all(data.get(k) for k in ('issued_utc', 'available_utc')):
            raise ValueError(f'invalid TAF for {r.gp_id}')
        lead = r.snapshot.cutoff - epoch(data['available_utc'])
        states['wet' if wet else 'dry'] += 1
        leads.append(lead / 60)
    return {**{k: states[k] for k in ('wet', 'dry', 'unknown')}, 'usable': len(leads),
            'gps': len(rows), 'minutes_before_cut_min': min(leads, default=None),
            'minutes_before_cut_max': max(leads, default=None)}


def _rank_scores(train, test, weather):
    fit = [(r, p) for r in train if (p := _prefix(r.rank))]
    if not fit:
        raise ValueError('no eligible ordered podium prefix')
    rows, prefixes = zip(*fit)
    raw = [_raw(r) for r in rows]
    scaler = _scaler(raw)
    test_raw = [_raw(r) for r in test]
    scores = {}
    for arm in ARMS:
        def design(r, x):
            wet = weather.get(r.gp_id, {}).get('wet') is True if arm == 'rank_pl_taf' else None
            return _design(x, scaler, wet)
        x = [design(r, m) for r, m in zip(rows, raw, strict=True)]
        solution = minimize(_objective, np.zeros(x[0].shape[1]), args=(_stages(x, prefixes),),
                            method='L-BFGS-B', jac=True, options={'ftol': 1e-12, 'gtol': 1e-8})
        if not solution.success:
            raise RuntimeError(f'{arm}: {solution.message}')
        scores[arm] = [design(r, m) @ solution.x for r, m in zip(test, test_raw, strict=True)]
    return scores, dict(sorted(Counter(map(len, prefixes)).items()))


def _summary(metrics):
    return {name: {'gps': len(values), **dict(zip(sealed.METRIC_NAMES, np.mean(values, axis=0).tolist(), strict=True))}
            for name, values in metrics.items()}


def _paired(metrics):
    n = len(metrics['elo'])  # Exactly one row per GP for every arm, in identical order.
    ix = np.random.default_rng(sealed.SEED).integers(n, size=(DRAWS, n))
    pairs = [(arm, base) for arm in ARMS for base in BASELINES] + [('rank_pl_taf', 'rank_pl')]
    out = {}
    for arm, base in pairs:
        delta = np.asarray(metrics[arm]) - metrics[base]
        interval = np.quantile(delta[ix].mean(axis=1), (0.025, 0.975), axis=0)
        out[f'{arm}_minus_{base}'] = {k: {'delta': float(d), 'ci95': ci.tolist()}
                                      for k, d, ci in zip(sealed.METRIC_NAMES, delta.mean(axis=0), interval.T, strict=True)}
    return out


def _study(rows, weather):
    rows = sorted(rows, key=lambda r: (r.snapshot.gp['race'], r.gp_id))
    ids = {r.gp_id for r in rows}
    if (len(ids) != len(rows) or set(weather) - ids or any(
            r.snapshot.gp['year'] not in TRAIN_YEARS or r.snapshot.horizon != 'pre_race'
            or r.snapshot.cutoff != r.snapshot.gp['race'] - 1800 for r in rows)):
        raise ValueError('requires unique validation T−30 GPs only')
    _coverage(rows, weather)  # Validate weather before either fitting or scoring.
    oos, scored, folds = [], [], []
    metrics = {name: [] for name in (*BASELINES, *ARMS)}
    for lower, upper in zip(sealed.HALVES, sealed.HALVES[1:]):
        train, test, cutoff = _scope(rows, lower, upper)
        if lower < sealed.HALVES[2]:
            oos.extend(test)  # Static baselines need no fit to generate calibration predictions.
            continue
        inner = [r for r in oos if max(r.label_at, r.snapshot.gp['race']) < cutoff]
        if not inner:
            raise ValueError('empty prior OOS calibration window')
        scores, prefixes = _rank_scores(train, test, weather)
        temperatures = dict.fromkeys(ARMS, 1.0)
        for base in BASELINES:
            ls = sealed._loss_grid(inner, sealed._scores(base, inner, {}))
            _, temperatures[base], _ = sealed._best(base, sealed._weight(inner), {base: ls}, list(range(len(inner))))
            scores[base] = sealed._scores(base, test, {})
        recipes = {k: [(k, t, 1.0)] for k, t in temperatures.items()}
        pred = sealed._per_cut(test, recipes, scores)
        block = {k: [p[0] for p in v] for k, v in pred.items()}
        for k in metrics:
            metrics[k].extend(block[k])
        folds.append({'start': utc(lower), 'until_exclusive': utc(upper), 'label_deadline_exclusive': utc(cutoff),
                      'latest_train_label': utc(max(r.label_at for r in train)), 'train_gps': len(train),
                      'fit_prefix_lengths': prefixes, 'inner_oos_gps': len(inner),
                      'latest_calibration_label': utc(max(r.label_at for r in inner)),
                      'test_gp_ids': [r.gp_id for r in test], 'temperatures': temperatures,
                      'train_taf': _coverage(train, weather), 'test_taf': _coverage(test, weather), 'metrics': _summary(block)})
        scored.extend(test)
        oos.extend(test)
    contrasts = _paired(metrics)
    primary = contrasts['rank_pl_minus_elo']
    shadow = (len(scored) == 48 and primary['brier']['ci95'][1] < 0 and primary['winner_log_loss']['delta'] <= 0)
    return {'scope': 'retrospective_validation_only_T-30', 'features': list(INPUTS), 'ridge': RIDGE,
            'taf_feature': 'wet_at_start_from_pre_cut_TAF × train_standardized_reference; dry/unknown=0',
            'bootstrap': {'unit': 'GP', 'paired': True, 'draws': DRAWS, 'seed': sealed.SEED, 'ci': 'percentile_95'},
            'folds': folds, 'metrics': _summary(metrics), 'contrasts': contrasts, 'taf': _coverage(scored, weather),
            'decision_primary_only': 'sombra' if shadow else 'conservar', 'promoted_to_production': False}


def main():
    from .taf import load_pilot

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, default=Path('.local'))
    parser.add_argument('--offline', action='store_true', help='Use cached TAFs only')
    args = parser.parse_args()
    path = args.work / 'validation-data.joblib'
    corpus = load_corpus(path)
    for item in corpus:
        gp = item['gp']
        if (gp['year'] not in TRAIN_YEARS or gp['id'] != f"{gp['year']}-{gp['round']:02d}"
                or not utc(gp['race']).startswith(f"{gp['year']}-")):
            raise ValueError('foreign GP in validation corpus')
    if {item['gp']['year'] for item in corpus} != set(TRAIN_YEARS):
        raise ValueError('requires 2022–25 validation corpus')
    with threadpool_limits(limits=1):
        rows, audit = _clock_examples(corpus, args.work / 'f1schedule')
        weather = load_pilot(args.work, [r.snapshot.gp for r in rows], offline=args.offline)
        absent = [gp for gp, row in weather.items() if row['reason'] not in ('outside_pilot', 'published_schedule_mismatch')
                  and (not row.get('archive_sha256') or row['reason'] == 'BadZipFile')]
        if absent:
            raise ValueError(f"Missing or damaged TAF archive for {', '.join(absent)}; restore the cache before scoring")
        report = _study(rows, weather)
    report['corpus_sha256'] = sealed.digest(path)
    report['clock_audit'] = audit
    report['control_scope'] = 'Sealed scoring and calibration reapplied to corrected pre-race clocks; not the original report.'
    report['taf_outside_pilot_gps'] = sum(record.get('reason') == 'outside_pilot' for record in weather.values())
    report['taf_source'] = {gp_id: {k: v for k, v in record.items() if k in (
        'available_utc', 'archive_sha256', 'bulletin_sha256', 'cutoff_utc', 'target_utc', 'issuer',
        'schedule_source', 'issued_utc', 'wet', 'station', 'source', 'reason')}
        for gp_id, record in sorted(weather.items()) if record.get('reason') != 'outside_pilot'}
    target = Path('reports/rank-study.json')
    target.parent.mkdir(parents=True, exist_ok=True)
    sealed._store_json(target, report)


if __name__ == '__main__':
    main()
