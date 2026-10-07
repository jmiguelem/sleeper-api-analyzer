"""Player tier classification.

Assigns players to tiers (Elite, Mid, Bench, n/a) based on position rank derived from
Sleeper's search_rank field (popularity metric, lower rank = more prominent player).

Tier definitions (from tier_cutoffs.json):
  - Elite: rank <= elite_rank
    * Unavailable Elite players are protected (never dropped)
    * Available Elite players require elite_swap_margin to replace
  - Mid: rank <= mid_rank
    * Unavailable Mid players are protected (never dropped)
    * Available Mid players require swap_margin to replace
  - Bench: rank > mid_rank
    * Droppable; requires only swap_margin to replace
  - n/a: positions with no tier definitions (K, DEF)
"""
from __future__ import annotations

import config

ELITE, MID, BENCH, NA = "Elite", "Mid", "Bench", "n/a"


def build_position_ranks(players: dict[str, dict]) -> dict[str, int]:
    """player_id -> rank within position, for tiered positions only."""
    by_pos: dict[str, list[tuple[int, str]]] = {p: [] for p in config.TIERED_POSITIONS}
    for pid, p in players.items():
        pos = p.get("position")
        rank = p.get("search_rank")
        if pos in by_pos and p.get("team") and isinstance(rank, int) and rank < 9_999_999:
            by_pos[pos].append((rank, pid))
    ranks: dict[str, int] = {}
    for entries in by_pos.values():
        for i, (_, pid) in enumerate(sorted(entries), 1):
            ranks[pid] = i
    return ranks


def classify(position: str, pos_rank: int | None) -> str:
    if position not in config.TIERED_POSITIONS or pos_rank is None:
        return NA if position not in config.TIERED_POSITIONS else BENCH
    if pos_rank <= config.elite_rank(position):
        return ELITE
    if pos_rank <= config.mid_rank(position):
        return MID
    return BENCH