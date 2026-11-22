from itertools import permutations
from types import SimpleNamespace
import unittest

import numpy as np

from f1forecast.engine import utc
from f1forecast.finish_order import expected_positions


class FinishOrderTest(unittest.TestCase):
    def test_mixture_matches_complete_finishes_and_refuses_another_forecast(self):
        ids = ('AAA', 'BBB', 'CCC', 'DDD')
        scores = {'a': np.log([3., 1., 1., 1.]), 'b': np.log([1., 4., 2., 1.])}
        mix = [('a', 1., .25), ('b', 1., .75)]
        dist = {}
        for order in permutations(range(4)):
            p = 0.
            for name, _, weight in mix:
                rate = np.exp(scores[name][list(order)])
                p += weight * np.prod(rate / np.cumsum(rate[::-1])[::-1])
            dist[order] = p
        truth = [sum(p * (1 + order.index(i)) for order, p in dist.items()) for i in range(4)]
        top = max(dist, key=dist.get)
        cut = {'horizon': 'fp1', 'origin': 'published', 'method': 'average', 'model_version': 'fixed',
               'cutoff_utc': utc(0), 'published_utc': utc(1), 'podium': {'drivers': [ids[i] for i in top[:3]],
               'probability': dist[top]}, 'drivers': [{'id': code, **{k: sum(p for order, p in dist.items() if order[j] == i)
               for j, k in enumerate(('first', 'second', 'third'))}} for i, code in enumerate(ids)]}
        config = {'selected': 'average', 'recipes': {'average': mix},
                  'learners': {name: SimpleNamespace(predict=lambda _x, v=v: v) for name, v in scores.items()}}
        bundle = {'version': 'fixed', 'horizons': {'fp1': config}}
        snap = SimpleNamespace(horizon='fp1', cutoff=0., ids=ids, x=None)
        np.testing.assert_allclose(expected_positions(bundle, snap, cut), truth, rtol=0, atol=1e-12)
        cut['drivers'][0]['first'] -= .01
        with self.assertRaisesRegex(ValueError, 'no reproducen'):
            expected_positions(bundle, snap, cut)
