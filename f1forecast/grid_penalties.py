"""Measure announced grid losses at T−30 without changing the sealed forecast."""

import argparse
from collections import Counter, defaultdict
from math import log
from pathlib import Path

import numpy as np

from . import training
from .dataset import TRAIN_YEARS, load_corpus
from .engine import epoch, utc
from .probability import joint
from .publication import read_file, write_file
from .rank_study import DRAWS, _clock_examples, _scope, _summary

ARMS = ('elo', 'sanctions')
KINDS = ('places', 'back')
TARGET = Path('reports/grid-penalties.json')


def _slot(n):
    return type(n) is int and 1 <= n <= 20  # 2024–25: 20 nominal grid slots; pit lane has none.


def _evidence(snap, revisions, board):
    if snap.horizon != 'pre_race' or snap.cutoff != snap.gp['race'] - 1800 or snap.gp['year'] not in (2024, 2025):
        raise ValueError('requires the scheduled 2024–25 main-race T−30')
    before = [w for w in revisions if w.get('available_utc') and epoch(w['available_utc']) < snap.cutoff]
    witness = max(before, key=lambda w: epoch(w['available_utc'])) if before else None
    drivers = witness['drivers'] if witness and witness['reason'] == 'ok' else {}
    published = epoch(witness['available_utc']) if witness else None
    per_driver = defaultdict(list)
    docs = []
    partial = board['reason'] == 'partial_drivers'
    unresolved = set(board.get('unresolved_drivers', ()))
    if partial and (not unresolved or any(len(code) != 3 or not code.isascii() or not code.isupper() for code in unresolved)):
        raise ValueError(f"{snap.gp['id']}: a partial board must identify the unresolved drivers")
    if board['reason'] == 'ok' or partial:
        for d in board['decisions']:
            at = epoch(d['available_utc'])
            if at >= snap.cutoff - snap.cutoff % 60:
                continue  # The board clock cannot establish publication within the cutoff minute.
            if not d.get('url') or type(d['document']) is not int or not d.get('driver'):
                raise ValueError(f"{snap.gp['id']}: missing FIA decision identity")
            if d['driver'] in unresolved:
                continue
            per_driver[d['driver']].append((at, d))
            docs.append({k: d[k] for k in ('driver', 'document', 'url', 'available_utc', 'kind', 'sha256') if k in d})
    changes = np.zeros(len(snap.ids))
    counts = Counter(gps=1, predicted_drivers=len(changes), witness_gps=int(any(
        _slot(v['qual']) and _slot(v['grid']) for v in drivers.values())), decision_gps=int(any(
        d['kind'] in (*KINDS, 'pit') for d in docs)), affected_gps=0, affected_drivers=0, places_lost=0,
        rows_changed=0, changed_gps=0, pit_excluded=0, fia_reviewed_gps=int(board['reason'] == 'ok'),
        fia_partial_gps=int(partial))
    exclusions = Counter()
    for code, decisions in per_driver.items():
        kinds = {d['kind'] for _, d in decisions}
        v = drivers.get(code)
        if 'pit' in kinds:
            counts['pit_excluded'] += 1
            exclusions['pit_lane_order'] += 1
            continue
        if not kinds.intersection(KINDS):
            continue
        if any(d['kind'] == 'other' and d.get('detail') != 'no_action' for _, d in decisions):
            exclusions['other_or_uncertain_order'] += 1
            continue
        if not v or v['kind'] != ('back' if 'back' in kinds else 'places'):
            exclusions['no_matching_annotated_grid_slot'] += 1
            continue
        # Multiple sanctions can share a driver. An older witness cannot confirm the updated net position.
        if any(at + 60 > published for at, d in decisions if d['kind'] in KINDS):
            exclusions['decision_minute_not_complete_by_revision'] += 1
            continue
        q, g = v['qual'], v['grid']
        if not (_slot(q) and _slot(g)):
            exclusions['no_two_numeric_slots'] += 1
            continue
        if g <= q:
            exclusions['no_net_loss'] += 1
            continue
        counts['affected_drivers'] += 1
        counts['places_lost'] += g - q
        if snap.reference_name == 'parrilla':
            exclusions['already_in_reference_grid'] += 1
        elif code not in snap.ids:
            exclusions['not_in_predicted_field'] += 1
        else:
            changes[snap.ids.index(code)] = -0.70 * 1.65 * log(g / q)
    counts['affected_gps'] = int(bool(counts['affected_drivers']))
    counts['rows_changed'] = int(np.count_nonzero(changes))
    counts['changed_gps'] = int(bool(counts['rows_changed']))
    audit = {'cutoff_utc': utc(snap.cutoff), 'reference': snap.reference_name, 'coverage': dict(counts),
             'exclusions_by_official_driver': dict(exclusions), 'fia_status': board['reason'],
             'listing_url': board.get('listing_url'), 'decisions_before_cut': docs,
             'unresolved_drivers': sorted(unresolved),
             'witness': {k: witness[k] for k in ('revision_url', 'available_utc', 'sha256', 'reason') if k in witness}
                       if witness else None}
    return changes, audit


def _paired(control, candidate):
    delta = np.asarray(candidate)[:, :2] - np.asarray(control)[:, :2]
    draws = np.random.default_rng(training.SEED).integers(len(delta), size=(DRAWS, len(delta)))
    ci = np.quantile(delta[draws].mean(axis=1), [.025, .975], axis=0)
    return {k: {'delta': float(d), 'ci95': interval.tolist()} for k, d, interval in zip(
        training.METRIC_NAMES[:2], delta.mean(axis=0), ci.T, strict=True)}


def _late_check(snap, witness, board, base, expected, temperature):
    # Falsify both the cause and the grid at the next minute; neither may reach a pre-cut joint distribution.
    late = utc(snap.cutoff + 60)
    driver = snap.ids[0]
    revisions = [witness, {'available_utc': late, 'drivers': {driver: {'qual': 1, 'grid': 20, 'kind': 'places'}}}]
    changed = {**board, 'decisions': [*board['decisions'], {'driver': driver, 'kind': 'pit', 'available_utc': late}]}
    after, _ = _evidence(snap, revisions, changed)
    p, q = joint((base + expected) / temperature), joint((base + after) / temperature)
    error = float(np.max(np.abs(p - q)))
    if not np.allclose(p, q, atol=1e-12, rtol=0):
        raise ValueError(f"{snap.gp['id']}: post-cut evidence changed the joint distribution by {error}")
    return error


def _study(rows, evidence):
    rows = sorted(rows, key=lambda r: (r.snapshot.gp['race'], r.gp_id))
    ids = [r.gp_id for r in rows if r.snapshot.gp['year'] in (2024, 2025)]
    expected = {f'{year}-{round_:02d}' for year in (2024, 2025) for round_ in range(1, 25)}
    if set(ids) != expected or len(ids) != len(expected) or set(evidence) != expected:
        raise ValueError('every one of the 48 validation GPs needs evidence or an explicit missing status')
    metrics = {arm: [] for arm in ARMS}
    changed_metrics = {arm: [] for arm in ARMS}
    errors = {arm: [] for arm in ARMS}
    scored_drivers, peak = 0, 0.
    total, exclusions, audit = Counter(), Counter(), {}
    folds, oos = [], []
    for lower, upper in zip(training.HALVES, training.HALVES[1:]):
        _, test, deadline = _scope(rows, lower, upper)
        if lower < training.HALVES[2]:
            oos.extend(test)
            continue
        inner = [r for r in oos if max(r.label_at, r.snapshot.gp['race'], r.snapshot.cutoff) < deadline]
        loss = training._loss_grid(inner, training._scores('elo', inner, {}))
        _, temp, _ = training._best('elo', training._weight(inner), {'elo': loss}, list(range(len(inner))))
        base = training._scores('elo', test, {})
        offsets, counts = [], Counter()
        for r, score in zip(test, base, strict=True):
            witness, board = evidence[r.gp_id]
            delta, facts = _evidence(r.snapshot, [witness], board)
            facts['witness_status'] = witness['reason']
            audit[r.gp_id] = facts
            counts.update(facts['coverage'])
            exclusions.update(facts['exclusions_by_official_driver'])
            offsets.append(delta)
            peak = max(peak, _late_check(r.snapshot, witness, board, score, delta, temp))
        scores = {'elo': base, 'sanctions': [s + offset for s, offset in zip(base, offsets, strict=True)]}
        pred = training._per_cut(test, {a: [(a, temp, 1.)] for a in ARMS}, scores)
        block = {a: [p[0] for p in pred[a]] for a in ARMS}
        for i, (r, delta) in enumerate(zip(test, offsets, strict=True)):
            ix = np.flatnonzero((delta != 0) & (r.rank > 0))
            scored_drivers += len(ix)
            for arm in ARMS:
                if np.any(delta):
                    changed_metrics[arm].append(block[arm][i])
                if len(ix):
                    # Only the sanctioned drivers: exact PL means from pairwise first-place rates.
                    rate = pred[arm][i][2][0][:len(r.rank)]
                    pos = .5 + (rate / (rate[ix, None] + rate)).sum(axis=1)
                    errors[arm].append(float(np.abs(pos - r.rank[ix]).mean()))
        for arm in ARMS:
            metrics[arm].extend(block[arm])
        total.update(counts)
        folds.append({'start': utc(lower), 'until_exclusive': utc(upper), 'deadline_exclusive': utc(deadline),
                      'last_calibration_label': utc(max(r.label_at for r in inner)), 'calibration_gps': len(inner),
                      'temperature': temp, 'test_gp_ids': [r.gp_id for r in test], 'coverage': dict(counts),
                      'metrics': _summary(block)})
        oos.extend(test)
    if total['gps'] != 48:
        raise ValueError(f"Incomplete cohort: {total['gps']}/48 GP")
    contrast = _paired(metrics['elo'], metrics['sanctions'])
    enough = total['changed_gps'] >= 8 and sum(f['coverage']['changed_gps'] > 0 for f in folds) >= 3
    passed = enough and contrast['brier']['ci95'][1] < 0 and contrast['winner_log_loss']['delta'] <= 0
    return {'scope': 'retrospective_validation_only_T-30', 'coverage': dict(total), 'folds': folds,
            'metrics': _summary(metrics), 'contrasts': contrast,
            'metrics_changed_gps_only': _summary(changed_metrics) if total['changed_gps'] else None,
            'sanctioned_finish_mae': {'matched_drivers': scored_drivers, 'gps': len(errors['elo']),
                                      'mae': {a: float(np.mean(v)) if v else None for a, v in errors.items()}},
            'exclusions_by_official_driver': dict(exclusions), 'evidence_by_gp': audit,
            'leakage': {'gps': total['gps'], 'atol': 1e-12, 'rtol': 0, 'max_joint_delta': peak},
            'bootstrap': {'unit': 'GP', 'draws': DRAWS, 'seed': training.SEED, 'ci': 'percentile_95'},
            'decision': 'Apto solo para seguimiento prospectivo en sombra.' if passed else
                        ('Conservar Elo: cobertura efectiva insuficiente.' if not enough else
                         'Conservar Elo: no supera la regla conjunta de Brier y log loss.'),
            'promoted_to_production': False}


def main():
    from .grid_decisions import load_decisions
    from .grid_witness import load_witness

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, default=Path('.local'))
    parser.add_argument('--offline', action='store_true', help='Solo revisiones y resoluciones locales')
    parser.add_argument('--out', type=Path, default=TARGET)
    args = parser.parse_args()
    path = args.work / 'validation-data.joblib'
    corpus = load_corpus(path)
    gps = [item['gp'] for item in corpus]
    if ({g['year'] for g in gps} != set(TRAIN_YEARS) or len({g['id'] for g in gps}) != len(gps)
            or any(g['id'] != f"{g['year']}-{g['round']:02d}" or not utc(g['race']).startswith(f"{g['year']}-") for g in gps)):
        raise ValueError('requires unique development GPs from 2022–25 only')
    rows, clocks = _clock_examples(corpus, args.work / 'f1schedule')
    evidence = {}
    # A deleted or replaced revision must not silently change the published historical comparison.
    pins = read_file(TARGET)['evidence_by_gp'] if TARGET.exists() else {}
    for r in rows:
        gp = r.snapshot.gp
        if gp['year'] in (2024, 2025):
            witness = load_witness(gp, args.work, offline=args.offline)
            if witness['reason'].startswith(('cache_missing', 'cache_invalid', 'transport_error', 'api_error')):
                raise ValueError(f"{r.gp_id}: {witness['reason']}; no se ha sustituido el informe.")
            if pins:
                saved = pins[r.gp_id]['witness'] or {}
                if any(saved.get(k) != witness.get(k) for k in ('revision_url', 'available_utc', 'sha256')):
                    raise ValueError(f'{r.gp_id}: la revisión no coincide con la evaluación publicada.')
            board = load_decisions(gp)
            if board['reason'] not in ('ok', 'partial_drivers'):
                raise ValueError(f"{r.gp_id}: sin revisión disciplinaria para este corte ({board['reason']}).")
            evidence[r.gp_id] = witness, board
    report = _study(rows, evidence)
    report.update(corpus_sha256=training.digest(path), clock_audit=clocks,
                  preregistration='a57ee7f33fdb89251015616f5421bf2f80383693',
                  attribution='English Wikipedia contributors (https://creativecommons.org/licenses/by-sa/4.0/); FIA stewards, cited public decisions. No grids redistributed.')
    write_file(args.out, report)
    c = report['coverage']
    print(f"{c['affected_drivers']} pilotos-GP con {c['places_lost']} puestos netos perdidos. "
          f"Ajuste en {c['changed_gps']}/{c['gps']} GP. Menor Brier es mejor.")
    for a, m in report['metrics'].items():
        print(f"{a}: Brier {m['brier']:.6f}; log loss {m['winner_log_loss']:.6f}")
    print(report['decision'], '|', args.out)


if __name__ == '__main__':
    main()
