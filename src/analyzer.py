"""Player evaluation and scoring.

Converts raw Sleeper projections and ESPN matchup data into scored PlayerEval objects
for comparison and ranking in lineup optimization.

Composite score formula:
  score = 0.50 × projection + 0.40 × matchup_adjusted + 0.10 × trend

Where:
  - projection: Sleeper weekly projection, re-scored with league settings
  - matchup_adjusted: projection ± (30% × matchup strength factor)
  - trend: average points over last 3 weeks (or None if unavailable)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import config
import nfl_data
import player_tier
from sleeper_client import fantasy_points


@dataclass
class PlayerEval:
    player_id: str
    name: str
    position: str
    team: str
    opponent: Optional[str]          # None = bye / no game
    injury_status: Optional[str]
    proj: float                      # projected points (league scoring)
    trend: Optional[float]           # avg points, last completed weeks
    matchup_adj: float               # -1 (brutal) .. +1 (soft)
    matchup_note: str
    score: float                     # composite used for comparisons
    tier: str
    pos_rank: Optional[int]
    available: bool                  # False if bye or ruled out
    on_reserve: bool = False
    is_free_agent: bool = False


def composite_score(proj: float, trend: Optional[float], adj: float) -> float:
    """50% projection + 40% matchup-adjusted projection + 10% recent form."""
    matchup_proj = proj * (1 + config.MATCHUP_SWING * adj)
    trend_val = trend if trend is not None else proj
    return config.W_PROJECTION * proj + config.W_MATCHUP * matchup_proj + config.W_TREND * trend_val


def projected_points(row: dict, scoring: dict) -> float:
    """Project with the league's scoring; fall back to Sleeper's PPR total if the
    stat keys don't line up (e.g. unusual K/DEF categories)."""
    stats = row.get("stats") or {}
    pts = fantasy_points(stats, scoring)
    if pts <= 0 and stats.get("pts_ppr", 0) > 0:
        pts = float(stats["pts_ppr"])
    return max(pts, 0.0)


def display_name(p: dict, pid: str) -> str:
    if p.get("position") == "DEF":
        return f"{p.get('first_name', '')} {p.get('last_name', pid)} D/ST".strip()
    return p.get("full_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() or pid


def evaluate(
    pid: str,
    players: dict[str, dict],
    proj_rows: dict[str, dict],
    scoring: dict,
    trends: dict[str, float],
    teams: Optional[dict[str, nfl_data.TeamContext]],
    pos_ranks: dict[str, int],
    on_reserve: bool = False,
    is_free_agent: bool = False,
) -> Optional[PlayerEval]:
    info = players.get(pid)
    if not info:
        return None
    pos = info.get("position")
    if pos not in config.POSITIONS:
        return None
    row = proj_rows.get(pid)
    opponent = (row or {}).get("opponent") or None
    proj = projected_points(row, scoring) if row else 0.0
    injury = info.get("injury_status")
    out = injury in config.OUT_STATUSES
    available = bool(opponent) and not out

    if teams:
        adj, note = nfl_data.matchup(pos, opponent, teams)
    else:
        adj, note = 0.0, ("BYE" if not opponent else f"vs {opponent} (no defense data)")
    if out:
        note = f"{injury.upper()} - {note}"

    trend = trends.get(pid)
    score = composite_score(proj, trend, adj) if available else 0.0
    rank = pos_ranks.get(pid)
    return PlayerEval(
        player_id=pid,
        name=display_name(info, pid),
        position=pos,
        team=info.get("team") or "FA",
        opponent=opponent,
        injury_status=injury,
        proj=proj,
        trend=trend,
        matchup_adj=adj,
        matchup_note=note,
        score=score,
        tier=player_tier.classify(pos, rank),
        pos_rank=rank,
        available=available,
        on_reserve=on_reserve,
        is_free_agent=is_free_agent,
    )