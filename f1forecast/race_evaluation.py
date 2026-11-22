"""Score only frozen, operator-received race predictions against a verified final result."""

from datetime import datetime, timedelta
from math import fsum, isclose, log
import re

POSITIONS = ('first', 'second', 'third')
CUT_FIELDS = {'horizon', 'origin', 'cutoff_utc', 'published_utc', 'lap', 'model_version', 'method',
              'reference', 'podium', 'drivers'}


def utc_time(value):
    if not isinstance(value, str):
        raise ValueError('expected an explicit UTC timestamp')
    stamp = datetime.fromisoformat(value)
    if stamp.utcoffset() != timedelta(0):
        raise ValueError('expected an explicit UTC timestamp')
    return stamp


def driver_id(value):
    return isinstance(value, str) and re.fullmatch('[A-Z]{3}', value) is not None


def probability(value):
    if type(value) not in (int, float) or not 0 <= value <= 1:
        raise ValueError('expected a probability between 0 and 1')
    return value


def distribution(cut, *, private=False):
    if not isinstance(cut, dict) or set(cut) - ({'race_context'} if private else set()) != CUT_FIELDS:
        raise ValueError('unexpected prediction fields')
    if not all(isinstance(cut[k], str) and cut[k].strip() for k in ('model_version', 'method', 'reference')):
        raise ValueError('missing model provenance')
    drivers = cut['drivers']
    if not isinstance(drivers, list) or len(drivers) < 3:
        raise ValueError('invalid prediction frame')
    by_id = {}
    for d in drivers:
        if (not isinstance(d, dict) or set(d) != {'id', 'name', 'team', 'podium', *POSITIONS}
                or not driver_id(d['id']) or d['id'] in by_id
                or not all(isinstance(d[k], str) and d[k].strip() for k in ('name', 'team'))):
            raise ValueError('invalid or duplicate entrant')
        if not isclose(probability(d['podium']), sum(probability(d[k]) for k in POSITIONS), rel_tol=0, abs_tol=1e-12):
            raise ValueError('incoherent podium probability')
        by_id[d['id']] = d
    if any(not isclose(sum(d[k] for d in drivers), 1.0, rel_tol=0, abs_tol=1e-12) for k in POSITIONS):
        raise ValueError('position probabilities must sum to 1')
    podium = cut['podium']
    if not isinstance(podium, dict) or set(podium) != {'drivers', 'probability'}:
        raise ValueError('invalid ordered podium frame')
    order = podium['drivers']
    if (not isinstance(order, list) or len(order) != 3 or not all(driver_id(d) for d in order)
            or len(set(order)) != 3 or not set(order) <= by_id.keys()):
        raise ValueError('invalid ordered podium')
    joint = probability(podium['probability'])
    if joint <= 0 or any(joint > by_id[d][k] + 1e-12 for k, d in zip(POSITIONS, order)):
        raise ValueError('joint podium probability disagrees with its marginals')
    return by_id


def race_evaluation(feed, gp, status, official, participants, distance, classification_utc):
    if (status != 'final' or not feed or feed.get('mode') != 'live' or feed.get('complete') is not True
            or feed.get('event', {}).get('id') != gp['id'] or not classification_utc or distance is None):
        return None
    if (type(distance) is not int or distance < 1 or not isinstance(official, list) or len(official) != 3
            or not all(driver_id(d) for d in official) or not isinstance(participants, (list, tuple, set))
            or not all(driver_id(d) for d in participants) or len(set(official)) != 3
            or len(set(participants)) != len(participants) or not set(official) <= set(participants)):
        raise ValueError('invalid official race frame')
    start = utc_time(feed['event']['race_utc'])
    updated = utc_time(feed['updated_utc'])
    classified = utc_time(classification_utc)
    if start.timestamp() != gp['race'] or not start <= updated or not start < classified:
        raise ValueError('feed and classification refer to different race frames')
    finish = utc_time(feed['finish_utc']) if feed.get('finish_utc') is not None else None
    if finish is not None and not start <= finish <= min(updated, classified):
        raise ValueError('invalid finish receipt time')
    if not isinstance(feed['predictions'], list):
        raise ValueError('invalid capture frame')
    race = []
    for cut in feed['predictions']:
        if cut['horizon'] != 'race' or cut['origin'] != 'published':
            continue
        lap = cut['lap']
        at, issued = (utc_time(cut[k]) for k in ('cutoff_utc', 'published_utc'))
        if (type(lap) is not int or lap < 1 or not start <= at <= issued <= updated
                or race and (lap <= race[-1][0] or at <= race[-1][1])):
            raise ValueError('invalid or duplicate race cut')
        pred = distribution(cut, private=True)
        if lap == distance and issued - at <= timedelta(seconds=120):
            finish = at if finish is None else min(finish, at)
        race.append((lap, at, issued, cut, pred))
    if finish is None or finish > classified:
        return None  # Scheduled laps and a completed file are not timestamps of the flag.
    cuts = []
    excluded = 0
    brier = loss = 0.0
    wins = exact = 0
    model = None
    entrants = set(participants)
    for i, (lap, at, issued, cut, pred) in enumerate(race):
        if lap >= distance or issued >= finish:
            excluded += 1
            continue
        # Do not count delayed writes from an earlier lap or predictions reconstructed during a gap.
        if issued - at > timedelta(seconds=120) or (i + 1 < len(race) and issued >= race[i + 1][1]):
            continue
        if model is not None and model != cut['model_version']:
            raise ValueError('mixed models in a race evaluation')
        model = cut['model_version']
        frame = pred.keys() | entrants
        brier += fsum(((pred[d]['podium'] if d in pred else 0) - (d in official)) ** 2 for d in frame) / len(frame)
        loss -= log(max(pred[official[0]]['first'] if official[0] in pred else 0, 1e-15))
        wins += max(pred, key=lambda d: pred[d]['first']) == official[0]
        exact += cut['podium']['drivers'] == official
        cuts.append(lap)
    if not cuts:
        return None
    return dict(cuts=len(cuts), first_lap=cuts[0], last_lap=cuts[-1], excluded_finish=excluded,
                brier_podium=brier / len(cuts), winner_log_loss=loss / len(cuts),
                winner_hits=wins, exact_hits=exact, model=model)
