"""Local search: 2-opt and or-opt within a route; relocate, swap and 2-opt* between routes.

Every move is judged by caller-supplied ``score`` and ``better`` functions, so the same
code serves a distance-only baseline and the full feasibility-aware objective. A move is
applied only if it is strictly better, so these searches can never worsen a solution.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

Seq = tuple[str, ...]
Assignment = dict[str, Seq]
S = TypeVar("S")
Better = Callable[[S, S], bool]
CanServe = Callable[[str, str], bool]


def two_opt(seq: Seq, score: Callable[[Seq], S], better: Better[S], max_passes: int = 100) -> Seq:
    """Reverse a sub-sequence whenever that improves the score (first improvement)."""
    best, best_score = seq, score(seq)
    n = len(seq)
    for _ in range(max_passes):
        improved = False
        for i in range(n - 1):
            for j in range(i + 1, n):
                candidate = best[:i] + best[i : j + 1][::-1] + best[j + 1 :]
                value = score(candidate)
                if better(value, best_score):
                    best, best_score, improved = candidate, value, True
                    break
            if improved:
                break
        if not improved:
            break
    return best


def or_opt(
    seq: Seq,
    score: Callable[[Seq], S],
    better: Better[S],
    max_segment: int = 3,
    max_passes: int = 100,
) -> Seq:
    """Move a segment of up to ``max_segment`` stops to another position."""
    best, best_score = seq, score(seq)
    n = len(seq)
    for _ in range(max_passes):
        improved = False
        for length in range(1, max_segment + 1):
            for i in range(n - length + 1):
                segment = best[i : i + length]
                rest = best[:i] + best[i + length :]
                for position in range(len(rest) + 1):
                    if position == i:
                        continue
                    candidate = rest[:position] + segment + rest[position:]
                    value = score(candidate)
                    if better(value, best_score):
                        best, best_score, improved = candidate, value, True
                        break
                if improved:
                    break
            if improved:
                break
        if not improved:
            break
    return best


def _relocate(
    assignment: Assignment,
    current: S,
    score: Callable[[Assignment], S],
    better: Better[S],
    can_serve: CanServe,
) -> tuple[Assignment, S] | None:
    uavs = sorted(assignment)
    for donor in uavs:
        route = assignment[donor]
        for i, task in enumerate(route):
            shortened = route[:i] + route[i + 1 :]
            for receiver in uavs:
                if receiver == donor or not can_serve(receiver, task):
                    continue
                target = assignment[receiver]
                for position in range(len(target) + 1):
                    trial = dict(assignment)
                    trial[donor] = shortened
                    trial[receiver] = target[:position] + (task,) + target[position:]
                    value = score(trial)
                    if better(value, current):
                        return trial, value
    return None


def _swap(
    assignment: Assignment,
    current: S,
    score: Callable[[Assignment], S],
    better: Better[S],
    can_serve: CanServe,
) -> tuple[Assignment, S] | None:
    uavs = sorted(assignment)
    for a_index, a in enumerate(uavs):
        for b in uavs[a_index + 1 :]:
            for i, task_a in enumerate(assignment[a]):
                if not can_serve(b, task_a):
                    continue
                for j, task_b in enumerate(assignment[b]):
                    if not can_serve(a, task_b):
                        continue
                    trial = dict(assignment)
                    trial[a] = assignment[a][:i] + (task_b,) + assignment[a][i + 1 :]
                    trial[b] = assignment[b][:j] + (task_a,) + assignment[b][j + 1 :]
                    value = score(trial)
                    if better(value, current):
                        return trial, value
    return None


def _two_opt_star(
    assignment: Assignment,
    current: S,
    score: Callable[[Assignment], S],
    better: Better[S],
    can_serve: CanServe,
) -> tuple[Assignment, S] | None:
    uavs = sorted(assignment)
    for a_index, a in enumerate(uavs):
        for b in uavs[a_index + 1 :]:
            route_a, route_b = assignment[a], assignment[b]
            for i in range(len(route_a) + 1):
                tail_a = route_a[i:]
                if not all(can_serve(b, t) for t in tail_a):
                    continue
                for j in range(len(route_b) + 1):
                    tail_b = route_b[j:]
                    if not tail_a and not tail_b:
                        continue
                    if not all(can_serve(a, t) for t in tail_b):
                        continue
                    trial = dict(assignment)
                    trial[a] = route_a[:i] + tail_b
                    trial[b] = route_b[:j] + tail_a
                    value = score(trial)
                    if better(value, current):
                        return trial, value
    return None


def inter_route_search(
    assignment: Assignment,
    score: Callable[[Assignment], S],
    better: Better[S],
    can_serve: CanServe,
    max_moves: int = 500,
) -> Assignment:
    """Apply relocate, swap and 2-opt* moves until none improves or the budget runs out."""
    current, current_score = dict(assignment), score(assignment)
    for _ in range(max_moves):
        for move in (_relocate, _swap, _two_opt_star):
            found = move(current, current_score, score, better, can_serve)
            if found is not None:
                current, current_score = found
                break
        else:
            break
    return current
