"""Exact ordered top-three Plackett–Luce distributions and their mixtures."""

from functools import lru_cache
from itertools import permutations

import numpy as np


@lru_cache(maxsize=32)
def triples(n: int) -> np.ndarray:
    if n < 3:
        raise ValueError("a podium needs at least three entrants")
    out = np.array(list(permutations(range(n), 3)), dtype=np.intp).T
    out.flags.writeable = False
    return out


def _stages(scores: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    scores = np.asarray(scores, dtype=np.float64)
    if scores.shape[-1] < 3 or not np.isfinite(scores).all():
        raise ValueError("non-finite strength or fewer than three entrants")
    # Keep all orders in support and denominators well conditioned. The same
    # log-strength floor is part of the distribution in training and serving.
    w = np.exp(np.maximum(scores - scores.max(axis=-1, keepdims=True), -14.0))
    # Avoid cancellation when almost all mass is on the two favourites.
    top = np.argpartition(w, -2, axis=-1)[..., -2:]
    top_w = np.take_along_axis(w, top, axis=-1)
    tail_w = w.copy()
    np.put_along_axis(tail_w, top, 0.0, axis=-1)
    tail = tail_w.sum(axis=-1, keepdims=True)
    total = tail + top_w.sum(axis=-1, keepdims=True)
    rest = total - w
    np.put_along_axis(rest, top, tail + top_w[..., ::-1], axis=-1)
    pair = (w / total)[..., :, None] * w[..., None, :] / rest[..., :, None]
    diag = np.arange(w.shape[-1])
    pair[..., diag, diag] = 0.0
    rest2 = rest[..., :, None] - w[..., None, :]
    top_mask = tail_w == 0
    rest2 = np.where(top_mask[..., :, None] & top_mask[..., None, :], tail[..., None], rest2)
    rest2[..., diag, diag] = 1.0
    return w, pair, rest2


def marginals(scores: np.ndarray) -> np.ndarray:
    """Vectorized P(1st), P(2nd), P(3rd) for batches with the same entrant count."""
    w, pair, rest2 = _stages(scores)
    rate = pair / rest2
    p3 = w * (rate.sum(axis=(-1, -2))[..., None] - rate.sum(axis=-1) - rate.sum(axis=-2))
    return np.stack((pair.sum(axis=-1), pair.sum(axis=-2), p3), axis=-1)


def joint(scores: np.ndarray) -> np.ndarray:
    """Return a probability for every distinct (first, second, third) tuple."""
    w, pair, rest2 = _stages(scores)
    i, j, k = triples(w.shape[-1])
    return pair[..., i, j] * w[..., k] / rest2[..., i, j]


def summarize(mass: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Marginals and MAP directly from a normalized (possibly mixed) joint."""
    ix = triples(n)
    single = mass.ndim == 1
    mass = np.atleast_2d(mass)
    # Index each row into its own set of n bins, avoiding a loop per driver.
    offset = np.arange(len(mass), dtype=np.intp)[:, None] * n
    flat = mass.ravel()
    result = np.stack(
        [np.bincount((offset + indices).ravel(), flat, minlength=len(mass) * n).reshape(-1, n)
         for indices in ix], axis=-1,
    )
    mode = mass.argmax(axis=-1)
    order = ix[:, mode].T
    prob = mass[np.arange(len(mass)), mode]
    return (result[0], order[0], prob[0]) if single else (result, order, prob)
