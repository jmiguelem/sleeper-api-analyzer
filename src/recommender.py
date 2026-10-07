"""Lineup optimization: build the best weekly lineup from your roster + free agents.

League format: read from the league's roster_positions (config.slots_from_league), e.g.
1 QB, 2 RB, 2 WR, 1 TE, 2 FLEX (WR/RB/TE), 1 K, 1 DEF.

Algorithm
---------
1. Starters first: fill the fixed slots (QB, RB, WR, TE, K, DEF) with the best players
   of that position (roster + free agents), FLEX not considered yet.
2. FLEX second: the best remaining eligible players (WR/RB/TE; QB too for SUPER_FLEX),
   compared against each other regardless of position.
3. Bench: every rostered player who did not make the lineup.
4. Moves: each free agent that made the lineup is paired with a rostered player to
   drop (lowest value first).

Ranking value (what players are compared by)
--------------------------------------------
* A free agent is ranked at ``score - swap_margin``: he only displaces a rostered player
  if he is better by at least that margin (per position, tier_cutoffs.json).
* A rostered, available Elite player is ranked at ``score + (elite_swap_margin -
  swap_margin)``: a free agent needs the larger elite_swap_margin to displace him.

Protection rules
----------------
* Unavailable (out / bye) Elite and Mid players are never dropped; they sit on the
  bench and are listed in ``watch`` so you know a slot is being covered.
* Only players at positions the lineup uses can be dropped, and never reserve/IR players.
* A free agent is only recommended if there is someone legal to drop for him.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import config
import player_tier
from analyzer import PlayerEval


@dataclass
class LineupSlot:
    position: str                   # "QB", "RB", "WR", "TE", "K", "DEF" or a flex name ("FLEX", ...)
    player: Optional[PlayerEval]

    @property
    def is_add(self) -> bool:
        return bool(self.player and self.player.is_free_agent)


@dataclass
class Move:
    drop: PlayerEval
    add: PlayerEval
    slot: str                       # slot the added player fills
    reason: str = ""


@dataclass
class Result:
    starters: list[LineupSlot]
    flex: list[LineupSlot]
    bench: list[PlayerEval]         # roster after the moves, minus the lineup
    moves: list[Move]
    watch: list[PlayerEval] = field(default_factory=list)  # protected unavailable players
    current_total: float = 0.0      # best lineup from the current roster alone
    optimal_total: float = 0.0      # best lineup after the moves

    @property
    def gain(self) -> float:
        return self.optimal_total - self.current_total


def value(p: PlayerEval) -> float:
    """Ranking value used to compare players (see module docstring)."""
    v = p.score
    base = config.swap_margin(p.position)
    if p.is_free_agent:
        v -= base
    elif p.tier == player_tier.ELITE and p.available:
        v += config.elite_swap_margin(p.position) - base
    return v


def _is_protected(p: PlayerEval) -> bool:
    """Unavailable Elite or Mid player: never dropped."""
    return p.tier in (player_tier.ELITE, player_tier.MID) and not p.available


def lineup_positions(slots: dict) -> set[str]:
    """Every position that can start somewhere in this lineup."""
    pos = {p for p in config.FIXED_SLOT_ORDER if slots.get(p)}
    for name, eligible in config.FLEX_SLOTS.items():
        if slots.get(name):
            pos |= set(eligible)
    return pos


def _fill(pool: list[PlayerEval], slots: dict) -> tuple[list[LineupSlot], list[LineupSlot]]:
    """Fixed-position starters first, then flex slots (most restrictive first) from what is left."""
    used: set[str] = set()
    starters: list[LineupSlot] = []
    for pos in config.FIXED_SLOT_ORDER:
        ranked = sorted((p for p in pool if p.position == pos and p.available), key=value, reverse=True)
        for i in range(slots.get(pos, 0)):
            player = ranked[i] if i < len(ranked) else None
            starters.append(LineupSlot(pos, player))
            if player:
                used.add(player.player_id)
    flex: list[LineupSlot] = []
    for name, eligible in config.FLEX_SLOTS.items():
        for _ in range(slots.get(name, 0)):
            left = [p for p in pool if p.position in eligible and p.available and p.player_id not in used]
            player = max(left, key=value, default=None)
            flex.append(LineupSlot(name, player))
            if player:
                used.add(player.player_id)
    return starters, flex


def _total(slots: list[LineupSlot]) -> float:
    return sum(s.player.score for s in slots if s.player)


def recommend(roster: list[PlayerEval], free_agents: list[PlayerEval], slots: dict | None = None) -> Result:
    slots_cfg = slots or config.LINEUP_SLOTS
    usable = lineup_positions(slots_cfg)
    active = [p for p in roster if not p.on_reserve]
    fas = [f for f in free_agents if f.available and f.position in usable]

    cur_starters, cur_flex = _fill(active, slots_cfg)
    current_total = _total(cur_starters + cur_flex)

    # Rebuild the lineup until every free agent in it has someone legal to drop.
    while True:
        starters, flex = _fill(active + fas, slots_cfg)
        slots = starters + flex
        in_lineup = {s.player.player_id for s in slots if s.player}
        adds = [s for s in slots if s.is_add]
        droppable = sorted(
            (p for p in active
             if p.player_id not in in_lineup and p.position in usable and not _is_protected(p)),
            key=value,
        )
        surplus = len(adds) - len(droppable)
        if surplus <= 0:
            break
        weakest = sorted((s.player for s in adds), key=value)[:surplus]
        drop_ids = {p.player_id for p in weakest}
        fas = [f for f in fas if f.player_id not in drop_ids]

    moves = []
    for slot, drop in zip(sorted(adds, key=lambda s: -value(s.player)), droppable):
        add = slot.player
        moves.append(Move(
            drop=drop, add=add, slot=slot.position,
            reason=(f"{add.name} ({add.matchup_note}, score {add.score:.1f}) takes a {slot.position} slot; "
                    f"{drop.name} (score {drop.score:.1f}) is the weakest droppable player"),
        ))

    dropped = {m.drop.player_id for m in moves}
    bench = sorted(
        (p for p in roster if p.player_id not in in_lineup and p.player_id not in dropped),
        key=lambda p: (not p.available, -p.score),
    )
    watch = [p for p in active if _is_protected(p)]
    return Result(starters, flex, bench, moves, watch, current_total, _total(slots))
