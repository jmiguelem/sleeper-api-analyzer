#!/usr/bin/env python3
"""Rank one position with the same scoring and lineup logic as the main recommender.

    python src/position.py WR            # your WRs + the top 5 free-agent WRs
    python src/position.py RB --top 6    # only the 6 best of that list
    python src/position.py FLEX          # WR/RB/TE together
    python src/position.py WR --fa-only  # only free agents (top 5, or --top N)
    python src/position.py K --week 6 --refresh
"""
from __future__ import annotations

import argparse
import sys

import config
import main as pipeline
import recommender
import report
from analyzer import PlayerEval

FREE_AGENTS_SHOWN = 5
CHOICES = list(config.POSITIONS) + ["FLEX"]
HEADERS = ["#", "Player", "Owner", "Pos", "Tier", "Rank", "Matchup", "Proj", "Form", "Score", "Status"]


def eligible(position: str) -> tuple[str, ...]:
    return config.FLEX_ELIGIBLE if position == "FLEX" else (position,)


def status_map(result: recommender.Result, roster: list[PlayerEval]) -> dict[str, str]:
    """player_id -> what the full recommender does with that player."""
    status: dict[str, str] = {}
    for p in result.bench:
        status[p.player_id] = "Bench"
    for p in result.watch:
        status[p.player_id] = "Bench - protected, out this week"
    for p in roster:
        if p.on_reserve:
            status[p.player_id] = "Reserve / IR"
    for s in result.starters + result.flex:
        if s.player:
            status[s.player.player_id] = f"{'ADD to ' if s.is_add else 'Starter: '}{s.position}"
    for m in result.moves:
        status[m.drop.player_id] = f"DROP (for {m.add.name})"
    return status


def build_list(position: str, roster, free_agents, result, top: int | None, fa_only: bool = False) -> list[list[str]]:
    kinds = eligible(position)
    mine = [] if fa_only else [p for p in roster if p.position in kinds]
    # --fa-only with --top N lists the N best free agents; otherwise the usual 5.
    shown = top if (fa_only and top) else FREE_AGENTS_SHOWN
    fas = sorted((f for f in free_agents if f.position in kinds and f.available),
                 key=lambda f: -f.score)[:shown]
    # Plain score order, owner ignored, so mine and free agents compare directly.
    pool = sorted(mine + fas, key=lambda p: p.score, reverse=True)
    if top:
        pool = pool[:top]
    status = status_map(result, roster)
    rows = []
    for i, p in enumerate(pool, 1):
        rows.append([
            str(i), p.name, "FA" if p.is_free_agent else "Mine", p.position, p.tier,
            "-" if p.pos_rank is None else str(p.pos_rank), p.matchup_note,
            f"{p.proj:.1f}", report._fmt_trend(p), f"{p.score:.1f}",
            status.get(p.player_id, "Free agent" if p.is_free_agent else "Bench"),
        ])
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Rank one position: your players + top free agents")
    parser.add_argument("position", type=str.upper, choices=CHOICES, metavar="POSITION",
                        help="one of: " + ", ".join(CHOICES))
    parser.add_argument("--top", type=int, help="show only the N best (default: your players + 5 free agents; with --fa-only, the N best free agents)")
    parser.add_argument("--fa-only", action="store_true", help="show only free agents, not your players")
    parser.add_argument("--week", type=int, help="NFL week to analyze (default: current)")
    parser.add_argument("--refresh", action="store_true", help="ignore cached API responses")
    args = parser.parse_args(argv)
    args.show_all = False

    try:
        roster, free_agents, meta = pipeline.build(args)
    except pipeline.DataError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    result = recommender.recommend(roster, free_agents, meta["slots"])
    rows = build_list(args.position, roster, free_agents, result, args.top, args.fa_only)
    print(f"{meta['league_name']} - Week {meta['week']} ({meta['season']}) - {args.position}")
    print(f"Lineup: {report._lineup_label(meta['slots'])}")
    print("Ranked by score (owner ignored). Status shows what the full recommender does, which also needs the margin.")
    print()
    print(report._table(HEADERS, rows) if rows else f"No {args.position} players found.")
    for w in meta.get("warnings", []):
        print(f"WARNING: {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
