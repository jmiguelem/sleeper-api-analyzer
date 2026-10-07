"""Opponent defensive/offensive context from ESPN's public site API.

Rank convention everywhere: 1 = best unit, N = worst unit.
For a defense, "best" = fewest yards allowed per game. A rank near N therefore
means an EASY matchup for the offensive player facing it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import config
from sleeper_client import DataError, cached, http_get_json

ESPN = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"


@dataclass
class TeamContext:
    abbr: str
    pass_allowed_ypg: float
    rush_allowed_ypg: float
    off_ypg: float
    pass_def_rank: int = 0
    rush_def_rank: int = 0
    off_rank: int = 0


def _collect(node, path, own: dict, opp: dict) -> None:
    """Walk an ESPN statistics payload gathering 'category.stat' -> value.
    Anything nested under a key containing 'opponent' is the team's defense."""
    if isinstance(node, dict):
        if isinstance(node.get("stats"), list) and "name" in node:
            target = opp if any("opponent" in p.lower() for p in path) else own
            for s in node["stats"]:
                name, value = s.get("name"), s.get("value")
                if name is not None and isinstance(value, (int, float)):
                    target.setdefault(f"{node['name']}.{name}", float(value))
        for key, val in node.items():
            _collect(val, path + [str(key)], own, opp)
    elif isinstance(node, list):
        for item in node:
            _collect(item, path, own, opp)


def _find(stats: dict, *suffixes: str) -> Optional[float]:
    for suffix in suffixes:
        for key, val in stats.items():
            if key.endswith("." + suffix):
                return val
    return None


def parse_team_stats(abbr: str, payload: dict) -> Optional[TeamContext]:
    own: dict = {}
    opp: dict = {}
    _collect(payload, [], own, opp)
    pass_allowed = _find(opp, "netPassingYardsPerGame", "passingYardsPerGame")
    rush_allowed = _find(opp, "rushingYardsPerGame")
    off_pass = _find(own, "netPassingYardsPerGame", "passingYardsPerGame")
    off_rush = _find(own, "rushingYardsPerGame")
    if None in (pass_allowed, rush_allowed, off_pass, off_rush):
        return None
    return TeamContext(abbr, pass_allowed, rush_allowed, off_pass + off_rush)


def assign_ranks(teams: dict[str, TeamContext]) -> None:
    for rank, t in enumerate(sorted(teams.values(), key=lambda t: t.pass_allowed_ypg), 1):
        t.pass_def_rank = rank
    for rank, t in enumerate(sorted(teams.values(), key=lambda t: t.rush_allowed_ypg), 1):
        t.rush_def_rank = rank
    for rank, t in enumerate(sorted(teams.values(), key=lambda t: -t.off_ypg), 1):
        t.off_rank = rank


def fetch_team_contexts(refresh: bool = False, session=None) -> dict[str, TeamContext]:
    """Season-to-date defensive stats for every NFL team, keyed by Sleeper abbreviation."""
    def load() -> dict:
        listing = http_get_json(f"{ESPN}/teams", session=session)
        teams = listing["sports"][0]["leagues"][0]["teams"]
        out = {}
        for entry in teams:
            team = entry["team"]
            abbr = config.ESPN_TO_SLEEPER.get(team["abbreviation"], team["abbreviation"])
            out[abbr] = http_get_json(f"{ESPN}/teams/{team['id']}/statistics", session=session)
        return out

    raw = cached("espn_team_stats", 12 * 3600, load, refresh)
    contexts: dict[str, TeamContext] = {}
    for abbr, payload in raw.items():
        ctx = parse_team_stats(abbr, payload)
        if ctx:
            contexts[abbr] = ctx
    if len(contexts) < 8:
        raise DataError(
            f"ESPN returned usable defensive stats for only {len(contexts)} teams; "
            "response format may have changed."
        )
    assign_ranks(contexts)
    return contexts


def matchup(position: str, opponent: Optional[str], teams: dict[str, TeamContext]):
    """Return (adjustment in [-1, +1], human note). +1 = easiest possible matchup."""
    if opponent is None:
        return 0.0, "BYE"
    ctx = teams.get(opponent)
    if ctx is None or position == "K":
        return 0.0, f"vs {opponent}"
    n = len(teams)
    if position in ("QB", "WR", "TE"):
        rank, label, value = ctx.pass_def_rank, "Pass D", f"{ctx.pass_allowed_ypg:.0f} yd/g allowed"
    elif position == "RB":
        rank, label, value = ctx.rush_def_rank, "Run D", f"{ctx.rush_allowed_ypg:.0f} yd/g allowed"
    else:  # DEF faces the opponent's offense; rank N = worst offense = easiest
        rank, label, value = ctx.off_rank, "Offense", f"{ctx.off_ypg:.0f} yd/g"
    adj = (rank - (n + 1) / 2) / ((n - 1) / 2)
    return adj, f"vs {opponent} {label} #{rank}/{n} ({value})"
