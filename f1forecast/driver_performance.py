"""Estudio local T−30: puesto oficial esperado, no probabilidad ni orden de llegada oficial."""

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
from statistics import median

from catboost import CatBoostRegressor
import numpy as np
from scipy.stats import rankdata

from . import training
from .dataset import TRAIN_YEARS, document_events, load_corpus, records_for
from .engine import FEATURES, asof, ordered, utc
from .publication import now, pre_race_events
from .rank_study import DRAWS, INPUTS, _clock_examples, _scope

BASE = (*INPUTS, 'grid_position', 'sprint_position')
EXTRA = ('qual_teammate_pct', 'fp1_run_field_pct')
COLS = [FEATURES.index(k) for k in BASE[1:]]
ARMS = ('base', 'extended')
PROB = (*ARMS, 'elo')
SETTINGS = dict(loss_function='RMSE', depth=4, iterations=100, learning_rate=0.05, l2_leaf_reg=5,
                random_seed=training.SEED, task_type='CPU', thread_count=1, verbose=False, allow_writing_files=False)
FP_RULE = ('Intervalos FP1 de TracingInsights depurados por archive._timing_events: sin vueltas sintéticas, '
           'boxes ni interrupción de sesión cruzada. Duración 45–155 s; mínimo 3 vueltas consecutivas '
           'del mismo piloto con numeración continua y |fin previo − inicio actual| ≤ 0.010 s. '
           'Excluir números duplicados. Mediana de sus vueltas aceptadas relativa a la mediana de las '
           'medianas individuales del campo FP1 (mínimo dos pilotos), en %. Sin evidencia: ausente.')
TIMING = re.compile(r'https://raw\.githubusercontent\.com/TracingInsights/(\d{4})/[0-9a-f]{40}/[^/]+/Practice%201/session_laptimes\.json')


def _long_run(laps):
    duplicate = Counter(n for _, n, _ in laps)
    run, accepted = [], []
    previous = None
    for at, number, seconds in sorted(laps):
        usable = duplicate[number] == 1 and 45 <= seconds <= 155
        if not usable or previous is None or number != previous[1] + 1 or abs(at - seconds - previous[0]) > 0.010:
            if len(run) >= 3:
                accepted.extend(run)
            run = []
        if usable:
            run.append(seconds)
        previous = (at, number) if usable else None
    if len(run) >= 3:
        accepted.extend(run)
    return median(accepted) if accepted else None


def matrix(snap, events, docs):
    if snap.horizon != 'pre_race' or snap.cutoff != snap.gp['race'] - 1800:
        raise ValueError('requires exactly the pre-race T−30 snapshot')
    best, laps, teams = defaultdict(dict), defaultdict(list), defaultdict(set)
    drivers = {d['id']: d['team'] for d in snap.drivers}
    for e in ordered(pre_race_events(events, snap.gp['id'], snap.cutoff)):
        kind, source = e['kind'], e['source']
        if (kind == 'roster' and docs.get('entries_status') == 'dated_fia_event_document'
                and e['at'] == docs.get('entries_at') and source == docs.get('entries_source')):
            teams.clear()
            for d in e['entries']:
                teams[d['team']].add(d['id'])
        if kind == 'qualifying' and e.get('session') == 'Qualifying' and e.get('segment') in (1, 2, 3):
            seconds, seg = e['seconds'], e['segment']
            if np.isfinite(seconds) and seconds > 0:
                series = best[e['driver']]
                series[seg] = min(series.get(seg, seconds), seconds)
        if kind == 'practice' and e.get('session') == 'Practice 1' and isinstance(source, str):
            match = TIMING.fullmatch(source)
            if match and int(match[1]) == snap.gp['year'] and type(e.get('lap')) is int and e['lap'] > 0:
                laps[e['driver']].append((e['at'], e['lap'], e['seconds']))
    pace = {d: p for d, series in laps.items() if (p := _long_run(series)) is not None}
    field = median(pace.values()) if len(pace) >= 2 else None
    x = np.full((len(drivers), len(BASE) + len(EXTRA)), np.nan)
    x[:, 0], x[:, 1:len(BASE)] = snap.reference, snap.x[:, COLS]
    for i, (d, team) in enumerate(drivers.items()):
        pair = teams[team]
        if len(pair) == 2 and d in pair and pair == {code for code, t in drivers.items() if t == team}:
            mate, = pair - {d}
            common = best[d].keys() & best[mate].keys()
            if common:
                seg = max(common)
                x[i, -2] = 100 * (best[d][seg] / best[mate][seg] - 1)
        if field and d in pace:
            x[i, -1] = 100 * (pace[d] / field - 1)
    return x


def _fit(train, cutoff, matrices, arm):
    x, y, weights = [], [], []
    for r in train:
        if max(r.snapshot.gp['race'], r.snapshot.cutoff, r.label_at) >= cutoff:
            raise ValueError(f'future training race or label: {r.gp_id}')
        eligible = r.rank > 0
        if eligible.any():
            n = len(r.official_ids)
            if n < 2 or r.rank.max() > n:
                raise ValueError(f'invalid official rank: {r.gp_id}')
            x.append(matrices[r.gp_id][eligible, :len(BASE) if arm == 'base' else None])
            y.append((r.rank[eligible] - 1) / (n - 1))
            weights.append(np.full(eligible.sum(), 1 / eligible.sum()))
    model = CatBoostRegressor(**SETTINGS)
    return model.fit(np.concatenate(x), np.concatenate(y), sample_weight=np.concatenate(weights))


def _positions(model, x, arm):
    pred = model.predict(x[:, :len(BASE) if arm == 'base' else None])
    return 1 + (len(x) - 1) * np.clip(pred, 0, 1)


def _drivers(snap, values):
    return [{'id': snap.ids[i], 'name': snap.drivers[i]['name'], 'estimated_mean_position': float(values[i])}
            for i in sorted(range(len(values)), key=lambda j: (values[j], snap.ids[j]))]


def _summarize(values):
    return {arm: {metric: float(np.mean(v)) for metric, v in metrics.items()} for arm, metrics in values.items()}


def _coverage(rows, matrices):
    x = np.concatenate([matrices[r.gp_id] for r in rows])
    matched = int(sum(np.count_nonzero(r.rank) for r in rows))
    official = sum(len(r.official_ids) for r in rows)
    return {'gps': len(rows), 'predicted': len(x), 'official': official, 'matched': matched,
            'matched_fraction_of_predicted': matched / len(x), 'matched_fraction_of_official': matched / official,
            'feature_nonmissing': dict(zip((*BASE, *EXTRA), np.isfinite(x).sum(axis=0).tolist(), strict=True))}


def _contrasts(values):
    n = len(values['base']['mae'])
    ix = np.random.default_rng(training.SEED).integers(n, size=(DRAWS, n))
    def paired(arm, control, metric):
        delta = np.subtract(values[arm][metric], values[control][metric])
        return {'delta': float(delta.mean()), 'ci95': np.quantile(delta[ix].mean(axis=1), [.025, .975]).tolist()}
    return {'mae': {c: paired('extended', c, 'mae') for c in ('base', 'reference')},
            'brier_vs_elo': {a: paired(a, 'elo', 'brier') for a in ARMS},
            'winner_log_loss_vs_elo': {a: paired(a, 'elo', 'winner_log_loss') for a in ARMS}}


def _study(rows, matrices, requested=None):
    folds, scored, prior = [], [], []
    totals = defaultdict(lambda: defaultdict(list))
    for lower, upper in zip(training.HALVES, training.HALVES[1:]):
        train, test, cutoff = _scope(rows, lower, upper)
        if requested and not any(r.gp_id == requested for r in test):
            continue
        positions = {}
        for a in ('extended',) if requested else ARMS:
            model = _fit(train, cutoff, matrices, a)
            positions[a] = [_positions(model, matrices[r.gp_id], a) for r in test]
        if requested:
            i, r = next((i, r) for i, r in enumerate(test) if r.gp_id == requested)
            return {'gp': requested, 'origin': 'backtest_oos', 'cutoff_utc': utc(r.snapshot.cutoff),
                    'training_deadline_exclusive': utc(cutoff), 'arm': 'extended', 'production': False,
                    'measure': 'Puesto medio estimado, no orden oficial ni probabilidad.',
                    'forecast': _drivers(r.snapshot, positions['extended'][i]),
                    'evaluation_only': {'official_position': {d: int(p) for d, p in zip(r.snapshot.ids, r.rank) if p}}}
        scores = {a: [-v for v in positions[a]] for a in ARMS}
        scores['elo'] = training._scores('elo', test, {})
        if lower >= training.HALVES[2]:
            history = [(r, s) for r, s in prior if max(r.snapshot.gp['race'], r.label_at) < cutoff]
            inner = [r for r, _ in history]
            temps = {}
            for a in PROB:
                losses = training._loss_grid(inner, [s[a] for _, s in history])
                _, temps[a], _ = training._best(a, training._weight(inner), {a: losses}, list(range(len(inner))))
            prob = training._per_cut(test, {a: [(a, temps[a], 1.)] for a in PROB}, scores)
            block = defaultdict(lambda: defaultdict(list))
            positions['reference'] = [rankdata(-r.snapshot.reference, method='average') for r in test]
            for i, r in enumerate(test):
                mask = r.rank > 0
                if not mask.any():
                    raise ValueError(f'GP without matched labels: {r.gp_id}')
                for a, p in positions.items():
                    for key, value in [('mae', p[i]), ('ordinal_mae', rankdata(p[i], method='average'))]:
                        block[a][key].append(float(np.abs(value[mask] - r.rank[mask]).mean()))
                for a in PROB:
                    for k, v in zip(training.METRIC_NAMES[:2], prob[a][i][0][:2], strict=True):
                        block[a][k].append(float(v))
            for a, metrics in block.items():
                for k, v in metrics.items():
                    totals[a][k].extend(v)
            folds.append({'start': utc(lower), 'until_exclusive': utc(upper), 'training_deadline_exclusive': utc(cutoff),
                          'latest_train_label': utc(max(r.label_at for r in train)), 'train_gps': len(train),
                          'inner_oos_gps': len(inner), 'latest_calibration_label': utc(max(r.label_at for r in inner)),
                          'temperatures': temps, 'test_gp_ids': [r.gp_id for r in test],
                          'train_coverage': _coverage(train, matrices),
                          'metrics': _summarize(block), 'coverage': _coverage(test, matrices)})
            scored.extend(test)
        prior.extend((r, {a: scores[a][i] for a in PROB}) for i, r in enumerate(test))
    if requested:
        raise ValueError(f'{requested}: no OOS forecast in 2024–25')
    contrasts = _contrasts(totals)
    passed = len(scored) == 48 and all(v['ci95'][1] < 0 for v in contrasts['mae'].values())
    return {'scope': 'retrospective_validation_only_T-30', 'target': '(official_rank−1)/(N_official−1)',
            'estimated_mean_position': '1 + clip(percentile,0,1) × (N_predicted−1); not official order or probability',
            'ordinal_mae': 'MAE of within-field ranks of predicted means (average ties), not magnitude MAE',
            'pl_score': 'negative estimated mean position; temperature fitted on past OOS podium Brier only',
            'features': [*BASE, *EXTRA], 'regressor': SETTINGS, 'fp1_rule': FP_RULE,
            'qual_rule': '100 × (propio/compañero−1) con sus mejores intervalos en la última fase común y pareja FIA fechada.',
            'missing_policy': 'Sin pareja FIA o fase común y sin tanda FP1 demostrable: NaN nativo, incluso columnas enteras.',
            'limits': 'Proxy histórico: reloj de cruce reconstruido, no recepción en vivo. No controla neumático, combustible o tráfico.',
            'bootstrap': {'unit': 'GP', 'paired': True, 'draws': DRAWS, 'seed': training.SEED, 'ci': 'percentile_95'},
            'folds': folds, 'coverage': _coverage(scored, matrices), 'metrics': _summarize(totals),
            'contrasts': contrasts, 'decision': 'sombra' if passed else 'no demuestra mejora de MAE frente a ambos controles',
            'promoted_to_production': False}


def _future_inputs(gp_id, work, event_file):
    from .__main__ import _read_events, lookup
    from .results import get_documents

    gp = lookup(gp_id, work, refresh=True)
    cutoff = gp['race'] - 1800
    if now() < cutoff:
        raise ValueError(f'T−30 aún no ocurrió: {utc(cutoff)}. No se simula la carrera futura.')
    if now() > cutoff + 120:
        raise ValueError(f'Ventana real de T−30 cerrada: {utc(cutoff)}; no se reconstruye como previsión emitida.')
    docs = get_documents(gp, work, as_of=cutoff, refresh=True)
    supplied = _read_events(event_file)
    if any(e.get('gp_id') != gp_id or not e.get('source') or not np.isfinite(e['at'])
           or e.get('source_at', e['at']) > e['at'] for e in supplied):
        raise ValueError('Cada evento autorizado necesita gp_id coincidente, fuente y at de recepción >= source_at.')
    dated = [e for e in document_events(docs)
             if e['kind'] != 'roster' or docs.get('entries_status') == 'dated_fia_event_document']
    events = list(pre_race_events([*dated, *supplied], gp_id, cutoff))
    if not any(e['kind'] == 'roster' and len(e['entries']) >= 3 for e in events):
        raise ValueError('Sin alineación fechada de este GP; no se usa la del GP anterior.')
    if now() > cutoff + 120:
        raise ValueError('Fuentes recibidas fuera de la ventana real de T−30.')
    return gp, docs, events


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, default=Path('.local'))
    parser.add_argument('--gp', help='YYYY-RR: OOS 2024–25, o futuro en ventana real T−30')
    parser.add_argument('--events', type=Path, help='inferencia futura: JSONL autorizado con gp_id, at de recepción UTC, source y kind')
    args = parser.parse_args(argv)
    if args.gp and not re.fullmatch(r'\d{4}-(?:0[1-9]|[1-9]\d)', args.gp):
        parser.error('--gp debe usar YYYY-RR')
    future = bool(args.gp and int(args.gp[:4]) > max(TRAIN_YEARS))
    if args.events and not future or args.gp and not future and int(args.gp[:4]) not in (2024, 2025):
        parser.error('OOS disponible solo en 2024–25; --events solo en inferencia futura')
    future_inputs = _future_inputs(args.gp, args.work, args.events) if future else None
    path = args.work / 'validation-data.joblib'
    corpus = load_corpus(path)
    gps = [item['gp'] for item in corpus]
    if ({g['year'] for g in gps} != set(TRAIN_YEARS) or len({g['id'] for g in gps}) != len(gps)
            or any(g['id'] != f"{g['year']}-{g['round']:02d}" or not utc(g['race']).startswith(str(g['year']) + '-') for g in gps)):
        raise ValueError('requires unique validation GPs from 2022–25 only')
    rows, audit = _clock_examples(corpus, args.work / 'f1schedule')
    matrices = {r.gp_id: matrix(r.snapshot, item['events'], item['documents']) for r, item in zip(rows, corpus, strict=True)}
    rows.sort(key=lambda r: (r.snapshot.gp['race'], r.gp_id))
    if future_inputs:
        gp, docs, events = future_inputs
        cutoff = gp['race'] - 1800
        snap = asof(gp, records_for(corpus), events, 'pre_race', cutoff)
        if snap is None:
            raise ValueError('Sin parrilla fechada suficiente en T−30.')
        train = [r for r in rows if max(r.snapshot.gp['race'], r.label_at) < cutoff]
        model = _fit(train, cutoff, matrices, 'extended')
        x = matrix(snap, events, docs)
        positions = _drivers(snap, _positions(model, x, 'extended'))
        calculated = now()
        if calculated > cutoff + 120:
            raise ValueError('Cálculo terminado fuera de la ventana real de T−30; no se emite.')
        report = {'gp': args.gp, 'origin': 'research_only_real_T-30', 'production': False, 'cutoff_utc': utc(cutoff),
                  'calculated_utc': utc(calculated), 'history_through_year': max(TRAIN_YEARS), 'sources': sorted(snap.sources),
                  'roster_status': 'dated_fia_event_document' if docs.get('entries_status') == 'dated_fia_event_document' else 'received_roster',
                  'measure': 'Puesto medio estimado, no orden oficial ni probabilidad.',
                  'available_by_feature': dict(zip((*BASE, *EXTRA), np.isfinite(x).sum(axis=0).tolist(), strict=True)),
                  'forecast': positions}
    else:
        report = _study(rows, matrices, args.gp)
    if args.gp:
        print(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2))
        return
    report.update(corpus_sha256=training.digest(path), clock_audit=audit,
                  sources={'calendar': 'https://github.com/theOehrly/f1schedule',
                           'timing': sorted({item['source'] for item in corpus if item.get('source')}),
                           'labels': sorted({item['label']['source'] for item in corpus}),
                           'documents': sorted({s for item in corpus for s in item['documents'].get('source', [])})})
    target = Path('reports/driver-performance.json')
    target.parent.mkdir(parents=True, exist_ok=True)
    training._store_json(target, report)
    print('Brazo          MAE     MAE ordinal     Brier podio     Log loss ganador')
    for a, m in report['metrics'].items():
        print(a.ljust(12), ' '.join(f'{m[k]:14.5f}' if k in m else '             —' for k in ('mae', 'ordinal_mae', 'brier', 'winner_log_loss')))
    print(report['decision'], '|', target)


if __name__ == '__main__':
    main()
