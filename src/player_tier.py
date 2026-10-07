"""Player importance tiers.

Tier is derived from position rank, using Sleeper's `search_rank` (lower = more
prominent player) among active players as a proxy for ADP/value.

Tier definitions (cutoffs and margins per position are in tier_cutoffs.json)
---------------------------------------------------------------------------
- **Elite** (rank <= elite cutoff): unavailable Elite players are never dropped, and a free
  agent needs the larger elite_swap_margin to displace an available one.
- **Mid** (rank <= mid cutoff): unavailable Mid players are never dropped.
- **Bench** (below that): droppable; free agents need the normal swap_margin.
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
