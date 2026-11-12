"""Bounded, deterministic combinatorics: Hungarian assignment (T4) and unique subset-sum (T5)."""

from collections.abc import Sequence
from decimal import Decimal
from typing import Literal

_INF = 1 << 120


def maximise_assignment(weights: Sequence[Sequence[int]]) -> list[tuple[int, int]]:
    """Maximum-weight assignment (Hungarian, O(n^2 m)) over an integer matrix; returns ``(row, col)`` pairs with weight > 0.

    Deterministic: ties are resolved by row/column index order (the caller sorts rows/cols by id).
    """
    orig = weights
    n = len(weights)
    m = len(weights[0]) if n else 0
    if n == 0 or m == 0:
        return []
    transposed = n > m
    if transposed:
        weights = [[weights[i][j] for i in range(n)] for j in range(m)]
        n, m = m, n
    u = [0] * (n + 1)
    v = [0] * (m + 1)
    p = [0] * (m + 1)
    way = [0] * (m + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [_INF] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], _INF, 0
            for j in range(1, m + 1):
                if not used[j]:
                    cur = -weights[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
    pairs = [(p[j] - 1, j - 1) for j in range(1, m + 1) if p[j]]
    if transposed:
        pairs = [(c, r) for r, c in pairs]
    return sorted((r, c) for r, c in pairs if orig[r][c] > 0)


SubsetStatus = Literal["unique", "none", "ambiguous", "limit"]


def unique_subset_sum(
    amounts: Sequence[Decimal], target: Decimal, max_size: int, max_combinations: int
) -> tuple[SubsetStatus, tuple[int, ...]]:
    """Find the subset (size <= ``max_size``) of strictly positive ``amounts`` summing exactly to ``target``.

    Returns ``("unique", indices)``, ``("none", ())``, ``("ambiguous", ())`` (>= 2 solutions) or ``("limit", ())``
    when more than ``max_combinations`` candidate subsets were examined (no guess is made). Deterministic: indices
    refer to the caller's order; the search order is ascending (amount, index).
    """
    order = sorted((i for i, a in enumerate(amounts) if a > 0), key=lambda i: (amounts[i], i))
    found: list[tuple[int, ...]] = []
    examined = 0
    chosen: list[int] = []

    def dfs(start: int, total: Decimal) -> bool:
        """Return False to abort the whole search (limit hit or ambiguity found)."""
        nonlocal examined
        for k in range(start, len(order)):
            idx = order[k]
            s = total + amounts[idx]
            if s > target:
                break  # ascending order: later items are not smaller
            examined += 1
            if examined > max_combinations:
                return False
            chosen.append(idx)
            if s == target:
                found.append(tuple(sorted(chosen)))
                if len(found) > 1:
                    chosen.pop()
                    return False
            elif len(chosen) < max_size and not dfs(k + 1, s):
                chosen.pop()
                return False
            chosen.pop()
        return True

    completed = dfs(0, Decimal(0))
    if len(found) > 1:
        return "ambiguous", ()
    if not completed:
        return "limit", ()
    return ("unique", found[0]) if found else ("none", ())
