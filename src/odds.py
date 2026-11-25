"""Playoff-odds Monte Carlo for the remaining regular-season weeks.

Every team's weekly score is Normal(mean, sd). Head-to-head results, the standings and the
tiebreaker (wins, then points for - Sleeper's default) are simulated `n` times. Pure Python:
no numpy dependency.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field


@dataclass
class Odds:
    playoff: dict[int, float]                       # roster_id -> P(makes playoffs)
    win_prob: dict[int, list[float]]                # roster_id -> P(win) in each simulated week
    swing: list[float]                              # for `focus`: P(playoffs | win wk) - P(playoffs | lose wk)
    by_wins: dict[int, tuple[float, float]] = field(default_factory=dict)  # extra wins -> (P(finish), P(playoffs | finish))


def win_probability(mean_a: float, mean_b: float, sd: float) -> float:
    """P(A outscores B) when both scores are independent Normal(., sd)."""
    z = (mean_a - mean_b) / (sd * math.sqrt(2))
    return 0.5 * (1 + math.erf(z))


def simulate(means: dict[int, list[float]], schedule: list[list[tuple[int, int]]], records: dict[int, tuple[int, int, int]],
             points_for: dict[int, float], sd: float, focus: int, playoff_teams: int = 6,
             n: int = 20000, seed: int = 7) -> Odds:
    """means[roster_id][i] = expected score in week i; schedule[i] = [(roster_a, roster_b), ...].

    Records are (wins, losses, ties) so far. `focus` is the roster whose swing / finish table is returned."""
    rng = random.Random(seed)
    ids = list(means)
    made = {r: 0 for r in ids}
    week_wins = {r: [0] * len(schedule) for r in ids}
    made_if = [[0, 0] for _ in schedule]        # focus: [made | lost, made | won]
    count_if = [[0, 0] for _ in schedule]
    finish = {}                                  # focus extra wins -> [sims, made]
    for _ in range(n):
        wins = {r: records[r][0] + 0.5 * records[r][2] for r in ids}
        pts = dict(points_for)
        focus_results = []
        for i, games in enumerate(schedule):
            score = {r: rng.gauss(means[r][i], sd) for r in ids}
            for r in ids:
                pts[r] += score[r]
            for a, b in games:
                won_a = score[a] > score[b]
                wins[a if won_a else b] += 1
                for r, won in ((a, won_a), (b, not won_a)):
                    week_wins[r][i] += won
                    if r == focus:
                        focus_results.append(won)
        order = sorted(ids, key=lambda r: (wins[r], pts[r]), reverse=True)
        in_playoffs = set(order[:playoff_teams])
        for r in in_playoffs:
            made[r] += 1
        extra = sum(focus_results)
        cell = finish.setdefault(extra, [0, 0])
        cell[0] += 1
        cell[1] += focus in in_playoffs
        for i, won in enumerate(focus_results):
            count_if[i][won] += 1
            made_if[i][won] += focus in in_playoffs
    swing = [(made_if[i][1] / max(count_if[i][1], 1)) - (made_if[i][0] / max(count_if[i][0], 1))
             for i in range(len(schedule))]
    return Odds(
        playoff={r: made[r] / n for r in ids},
        win_prob={r: [w / n for w in week_wins[r]] for r in ids},
        swing=swing,
        by_wins={k: (v[0] / n, v[1] / max(v[0], 1)) for k, v in sorted(finish.items())},
    )
