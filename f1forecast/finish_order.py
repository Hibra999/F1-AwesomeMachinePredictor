"""Derive mean finishing positions from the exact components of an issued pre-race forecast."""

import numpy as np

from .engine import epoch, utc
from .probability import joint, summarize, triples
from .training import _static


def expected_positions(bundle, snap, cut):
    config = bundle['horizons'][snap.horizon]
    if (cut['model_version'] != bundle['version'] or cut['method'] != config['selected']
            or cut['horizon'] != snap.horizon or epoch(cut['cutoff_utc']) != snap.cutoff
            or cut['origin'] != 'published' or cut['published_utc'] is None):
        raise ValueError('No coincide el modelo, el método o el corte emitido.')
    n = len(snap.ids)
    first = triples(n)[0]
    mass = np.zeros(len(first))
    mean = np.zeros(n)
    for name, temp, weight in config['recipes'][config['selected']]:
        if weight <= 0:
            continue
        learner = config['learners'].get(name)
        score = learner.predict(snap.x) if learner is not None else _static(name, snap)
        part = joint(score / temp)
        mass += weight * part
        rate = np.bincount(first, weights=part, minlength=n)
        # E[rank_i] = 1 + sum(j != i) P(j beats i), within each PL component, not after mixing winner odds.
        mean += weight * (0.5 + (rate / (rate[:, None] + rate)).sum(axis=1))
    prob, top, p_top = summarize(mass, n)
    issued = {d['id']: d for d in cut['drivers']}
    if (set(issued) != set(snap.ids) or [snap.ids[i] for i in top] != cut['podium']['drivers']
            or abs(p_top - cut['podium']['probability']) > 1e-12 or not np.allclose(prob,
                [[issued[d][p] for p in ('first', 'second', 'third')] for d in snap.ids], rtol=0, atol=1e-12)):
        raise ValueError('Las probabilidades no reproducen la previa emitida; no se estima el puesto.')
    return mean


def print_positions(args, work, bundle, records):
    from .__main__ import _read_events, lookup
    from .dataset import document_events
    from .engine import asof
    from .pages import verify_forecast
    from .publication import now, pre_race_events, read_file
    from .results import get_documents

    feed = read_file(args.feed)
    verify_forecast(feed)
    cut = max(feed['predictions'], key=lambda p: epoch(p['cutoff_utc']))
    gp = lookup(feed['event']['id'], work)
    at = epoch(cut['cutoff_utc'])
    if gp['race'] != epoch(feed['event']['race_utc']) or gp['fp1'] != epoch(feed['event']['fp1_utc']):
        raise ValueError('El calendario no coincide con el de la previa emitida.')
    docs = get_documents(gp, work, as_of=at)
    events = [*document_events(docs), *_read_events(args.events)]
    snap = asof(gp, records, pre_race_events(events, gp['id'], at), cut['horizon'], at)
    if snap is None:
        raise ValueError('Sin alineación verificable al corte original.')
    mean = expected_positions(bundle, snap, cut)
    print(f"{feed['event']['name']} | {cut['horizon']} | corte {cut['cutoff_utc']} | {cut['model_version']}")
    print(f"Derivado {utc(now())}; puesto medio entre los {len(mean)} inscritos previstos, no orden oficial. "
          "El modelo se calibró para podio, no para toda la clasificación.")
    print('Piloto                          Puesto medio')
    for i in np.argsort(mean, kind='stable'):
        d = snap.drivers[i]
        print(f"{d['id']}  {d['name']:<27} {mean[i]:.2f}")
