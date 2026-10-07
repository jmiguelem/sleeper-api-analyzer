#!/usr/bin/env python3
"""Primary analysis entry point.

Orchestrates data fetching from Sleeper and ESPN APIs, evaluates players, and
generates optimal lineup recommendations with move suggestions.

Usage:
  python src/main.py              # Analyze current NFL week
  python src/main.py --week 6     # Analyze specific week
  python src/main.py --show-all   # Include top free agents per position
  python src/main.py --refresh    # Skip cache, fetch fresh data
"""
from __future__ import annotations

import argparse
import sys

import analyzer
import config
import report
import nfl_data
import player_tier
import recommender
from sleeper_client import DataError, SleeperClient


def build(args) -> tuple:
    """Orchestrate analysis pipeline.

    Fetches data from Sleeper and ESPN APIs, evaluates all players, generates
    optimal lineup, recommends moves, and produces outputs.

    Returns:
      Tuple of (Result, roster, metadata) for reporting
    """
    client = SleeperClient(config.LEAGUE_ID, config.USER_ID, refresh=args.refresh)
    warnings: list[str] = []

    state = client.get_state()
    season = str(state.get("season") or state.get("league_season"))
    week = args.week or int(state.get("week") or state.get("display_week"))
    league = client.get_league()
    scoring = league.get("scoring_settings") or {}
    slots = config.slots_from_league(league.get("roster_positions"))
    if not league.get("roster_positions"):
        warnings.append("League roster_positions missing; using default lineup slots from config.")
    rosters = client.get_rosters()
    mine = client.get_my_roster(rosters)

    players = client.get_players()
    pos_ranks = player_tier.build_position_ranks(players)

    proj_rows = {str(r["player_id"]): r for r in client.get_projections(season, week)}
    if not proj_rows:
        raise DataError(f"Sleeper has no projections for week {week} of {season}.")

    try:
        trends = client.recent_scores(season, week, scoring)
    except DataError as exc:
        trends = {}
        warnings.append(f"Recent-form data unavailable ({exc}); using projections only.")

    try:
        teams = nfl_data.fetch_team_contexts(refresh=args.refresh)
    except (DataError, KeyError, IndexError) as exc:
        teams = None
        warnings.append(f"ESPN defensive stats unavailable ({exc}); matchup adjustment disabled.")

    reserve = set(map(str, mine.get("reserve") or [])) | set(map(str, mine.get("taxi") or []))
    my_ids = [str(p) for p in mine.get("players") or []]
    roster = []
    for pid in my_ids:
        ev = analyzer.evaluate(pid, players, proj_rows, scoring, trends, teams, pos_ranks,
                               on_reserve=pid in reserve)
        if ev:
            roster.append(ev)

    rostered = {str(p) for r in rosters for p in (r.get("players") or [])}
    free_agents = []
    for pid, row in proj_rows.items():
        if pid in rostered:
            continue
        info = players.get(pid) or {}
        if info.get("status") not in (None, "Active") and info.get("position") != "DEF":
            continue
        ev = analyzer.evaluate(pid, players, proj_rows, scoring, trends, teams, pos_ranks, is_free_agent=True)
        if ev and ev.available and ev.proj > 0:
            free_agents.append(ev)

    needed = recommender.lineup_positions(slots)
    for pos in sorted(needed):
        mine = [p for p in roster if p.position == pos]
        if mine and not any(p.proj > 0 for p in mine) or not any(f.position == pos for f in free_agents):
            warnings.append(f"No usable {pos} projections found (check Sleeper projections / league scoring keys); "
                            f"{pos} recommendations may be missing.")
    meta = {
        "league_name": league.get("name", "League"),
        "season": season,
        "week": week,
        "generated_at": report.now_iso(),
        "slots": slots,
        "warnings": warnings,
    }
    return roster, free_agents, meta


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Sleeper fantasy roster optimizer")
    parser.add_argument("--week", type=int, help="NFL week to analyze (default: current)")
    parser.add_argument("--show-all", action="store_true", help="also list top free agents per position")
    parser.add_argument("--refresh", action="store_true", help="ignore cached API responses")
    args = parser.parse_args(argv)

    try:
        roster, free_agents, meta = build(args)
    except DataError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    result = recommender.recommend(roster, free_agents, meta["slots"])
    print(report.render_text(result, roster, meta, args.show_all, free_agents))
    md_path = report.save_markdown(result, roster, meta)
    print(f"\nSaved: {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())