#!/usr/bin/env python3
"""Rest-of-regular-season planner: one set of add/drops judged over every remaining week.

main.py answers "who do I start this week?". This answers "which moves help most across all the
weeks left before the playoffs?" - use it when this is your last chance to change the roster.

For each remaining week it scores every rostered player and free agent (projection, that week's
opponent, recent form), then greedily picks the add/drop that raises the SUM of optimal lineup scores
the most, until no move clears the position's swap margin per week. It then simulates the standings
to show what the moves do to your playoff odds.

Usage:
  python src/season.py                  # current week through the last regular-season week
  python src/season.py --week 12 --through 14
  python src/season.py --max-moves 3 --show-all --refresh
"""
from __future__ import annotations

import argparse
import statistics
import sys
from dataclasses import dataclass, field
from typing import Optional

import analyzer
import config
import nfl_data
import odds
import player_tier
import recommender
import report
from analyzer import PlayerEval
from main import lock_status
from sleeper_client import DataError, SleeperClient

# Statuses that are assumed to last into future weeks. "Doubtful" is a this-week designation only.
PERSISTENT_OUT = {"IR", "PUP", "Sus", "NA", "Out"}


def _score(p: PlayerEval) -> float:
    return p.score


@dataclass
class Context:
    meta: dict
    weeks: list[int]
    slots: dict
    pools: dict[int, dict[str, PlayerEval]]      # week -> player_id -> evaluation
    my_id: int
    my_active: list[str]                         # my non-reserve player ids
    teams: dict[int, list[str]]                  # roster_id -> active player ids (every team)
    names: dict[int, str]
    records: dict[int, tuple[int, int, int]]
    points_for: dict[int, float]
    schedule: list[list[tuple[int, int]]]        # per horizon week: [(roster_a, roster_b)]
    sd: float
    playoff_teams: int


@dataclass
class PlannedMove:
    drop: PlayerEval
    add: PlayerEval
    gain: float                                  # extra lineup score summed over the horizon
    per_week: list[float]
    starts: list[int]                            # weeks in which the add is in the lineup
    backups: list[tuple[PlayerEval, float]] = field(default_factory=list)  # same drop, next-best adds


@dataclass
class CloseCall:
    """A swap that raises the lineup score but not by the full swap margin."""
    drop: PlayerEval
    add: PlayerEval
    gain: float
    per_week: list[float]
    needed: float


@dataclass
class Plan:
    moves: list[PlannedMove]
    roster: list[str]                            # my active ids after the moves
    base_totals: list[float]
    new_totals: list[float]
    close_calls: list[CloseCall] = field(default_factory=list)


# ------------------------------------------------------------------ lineup maths
def weekly_lineups(pids, ctx_pools, weeks, slots) -> list[list[recommender.LineupSlot]]:
    """Best lineup (by raw score) for each week from these player ids."""
    out = []
    for w in weeks:
        players = [ctx_pools[w][p] for p in pids if p in ctx_pools[w]]
        starters, flex = recommender._fill(players, slots, key=_score)
        out.append(starters + flex)
    return out


def horizon_totals(pids, pools, weeks, slots) -> list[float]:
    return [recommender._total(l) for l in weekly_lineups(pids, pools, weeks, slots)]


def _is_protected(p: PlayerEval) -> bool:
    """Elite/Mid player who is injured (not merely on bye): never dropped."""
    return p.tier in (player_tier.ELITE, player_tier.MID) and p.injury_status in config.OUT_STATUSES


def move_threshold(drop: PlayerEval, add: PlayerEval) -> float:
    """Points per week the move must add. Dropping an available Elite player needs the elite margin."""
    if drop.tier == player_tier.ELITE and drop.available:
        return config.elite_swap_margin(drop.position)
    return config.swap_margin(add.position)


def any_eval(pools, weeks, pid) -> Optional[PlayerEval]:
    """The player's evaluation from the first week he has one (a free agent on bye in week 1 has none there)."""
    return next((pools[w][pid] for w in weeks if pid in pools[w]), None)


def candidate_free_agents(pools, weeks, usable, per_position) -> list[str]:
    """Top free agents per lineup position by score summed over the horizon."""
    totals: dict[str, float] = {}
    pos_of: dict[str, str] = {}
    for w in weeks:
        for pid, p in pools[w].items():
            if p.is_free_agent and p.position in usable:
                totals[pid] = totals.get(pid, 0.0) + p.score
                pos_of[pid] = p.position
    keep: list[str] = []
    for pos in sorted(usable):
        keep += sorted((pid for pid in totals if pos_of[pid] == pos), key=lambda pid: -totals[pid])[:per_position]
    return keep


def _all_swaps(ctx: Context, roster, totals, cands, margin_scale):
    """Every legal single swap with a positive gain: (gain, drop_id, add_id, new_totals, points needed)."""
    pools, weeks, slots = ctx.pools, ctx.weeks, ctx.slots
    first, usable = pools[weeks[0]], recommender.lineup_positions(slots)
    cur, out = sum(totals), []
    for drop_id in roster:
        drop = first.get(drop_id)
        if not drop or drop.played or drop.position not in usable or _is_protected(drop):
            continue
        rest = [p for p in roster if p != drop_id]
        for add_id in cands:
            if add_id in roster:
                continue
            new = horizon_totals(rest + [add_id], pools, weeks, slots)
            gain = sum(new) - cur
            if gain > 1e-9:
                need = move_threshold(drop, any_eval(pools, weeks, add_id)) * len(weeks) * margin_scale
                out.append((gain, drop_id, add_id, new, need))
    # Equal lineup gain: drop the player who is worth least over the horizon (keeps the better depth).
    keep_value = lambda pid: sum(pools[w][pid].score for w in weeks if pid in pools[w])
    return sorted(out, key=lambda t: (-round(t[0], 6), keep_value(t[1])))


def plan_moves(ctx: Context, max_moves: int, per_position: int = config.CANDIDATES_PER_POSITION,
               margin_scale: float = 1.0) -> Plan:
    """Greedy: take the swap with the biggest horizon gain that clears its margin, repeat."""
    pools, weeks, slots = ctx.pools, ctx.weeks, ctx.slots
    first = pools[weeks[0]]
    cands = candidate_free_agents(pools, weeks, recommender.lineup_positions(slots), per_position)
    roster = list(ctx.my_active)
    totals = horizon_totals(roster, pools, weeks, slots)
    base_totals = list(totals)
    moves: list[PlannedMove] = []

    for _ in range(max_moves):
        swaps = _all_swaps(ctx, roster, totals, cands, margin_scale)
        passing = [t for t in swaps if t[0] >= t[4]]
        if not passing:
            break
        gain, drop_id, add_id, new, _need = passing[0]
        backups = [(any_eval(pools, weeks, a), g) for g, d, a, _, need in passing[1:] if d == drop_id][:3]
        kept = [p for p in roster if p != drop_id] + [add_id]
        starts = [w for w, l in zip(weeks, weekly_lineups(kept, pools, weeks, slots))
                  if any(s.player and s.player.player_id == add_id for s in l)]
        moves.append(PlannedMove(first[drop_id], any_eval(pools, weeks, add_id), gain,
                                 [b - a for a, b in zip(totals, new)], starts, backups))
        roster, totals = kept, new

    close, seen = [], set()
    for gain, drop_id, add_id, new, need in _all_swaps(ctx, roster, totals, cands, margin_scale):
        if gain < need and add_id not in seen and len(close) < 5:
            seen.add(add_id)
            close.append(CloseCall(first[drop_id], any_eval(pools, weeks, add_id), gain,
                                   [b - a for a, b in zip(totals, new)], need))
    return Plan(moves, roster, base_totals, totals, close)


# ------------------------------------------------------------------ data loading
def horizon_weeks(args, state, league) -> list[int]:
    start = args.week or int(state.get("week") or state.get("display_week"))
    settings = league.get("settings") or {}
    last_regular = int(settings.get("playoff_week_start") or 15) - 1
    end = args.through or last_regular
    if end < start:
        raise DataError(f"Nothing to plan: week {start} is after the last regular-season week ({end}).")
    return list(range(start, end + 1))


def build(args, client: Optional[SleeperClient] = None) -> Context:
    client = client or SleeperClient(config.LEAGUE_ID, config.USER_ID, refresh=args.refresh)
    warnings: list[str] = []
    state = client.get_state()
    season = str(state.get("season") or state.get("league_season"))
    league = client.get_league()
    weeks = horizon_weeks(args, state, league)
    scoring = league.get("scoring_settings") or {}
    slots = config.slots_from_league(league.get("roster_positions"))
    rosters = client.get_rosters()
    mine = client.get_my_roster(rosters)
    players = client.get_players()
    pos_ranks = player_tier.build_position_ranks(players)

    try:
        trends = client.recent_scores(season, weeks[0], scoring)   # same form figure for every week
    except DataError as exc:
        trends = {}
        warnings.append(f"Recent-form data unavailable ({exc}); using projections only.")
    try:
        teams_ctx = nfl_data.fetch_team_contexts(refresh=args.refresh)
    except (DataError, KeyError, IndexError) as exc:
        teams_ctx = None
        warnings.append(f"ESPN defensive stats unavailable ({exc}); matchup adjustment disabled.")

    played: set[str] = set()
    locked: dict[str, str] = {}
    if league.get("roster_positions"):
        try:
            played, locked = lock_status(client.get_my_matchup(weeks[0], mine.get("roster_id")),
                                         league["roster_positions"])
        except DataError as exc:
            warnings.append(f"Matchup data unavailable ({exc}); players who already played are not locked.")

    owner: dict[str, int] = {}
    reserve_of: dict[int, set[str]] = {}
    for r in rosters:
        rid = int(r["roster_id"])
        reserve_of[rid] = set(map(str, r.get("reserve") or [])) | set(map(str, r.get("taxi") or []))
        for pid in map(str, r.get("players") or []):
            owner[pid] = rid

    pools: dict[int, dict[str, PlayerEval]] = {}
    for w in weeks:
        rows = {str(r["player_id"]): r for r in client.get_projections(season, w)}
        if not rows:
            raise DataError(f"Sleeper has no projections for week {w} of {season}.")
        statuses = config.OUT_STATUSES if w == weeks[0] else PERSISTENT_OUT
        pool: dict[str, PlayerEval] = {}
        for pid in set(owner) | set(rows):
            rid = owner.get(pid)
            info = players.get(pid) or {}
            if rid is None and info.get("status") not in (None, "Active") and info.get("position") != "DEF":
                continue
            ev = analyzer.evaluate(pid, players, rows, scoring, trends, teams_ctx, pos_ranks,
                                   on_reserve=rid is not None and pid in reserve_of[rid],
                                   is_free_agent=rid is None, out_statuses=statuses)
            if ev is None or (rid is None and not (ev.available and ev.proj > 0)):
                continue
            if w == weeks[0] and rid == mine["roster_id"]:
                ev.played = pid in played
                ev.locked_slot = locked.get(pid)
            pool[pid] = ev
        pools[w] = pool

    my_id = int(mine["roster_id"])
    names = {}
    try:
        users = {str(u["user_id"]): u.get("display_name") or str(u["user_id"]) for u in client.get_users()}
    except DataError:
        users = {}
    for r in rosters:
        names[int(r["roster_id"])] = users.get(str(r.get("owner_id")), f"Team {r['roster_id']}")

    records, points_for, team_ids = {}, {}, {}
    for r in rosters:
        rid, s = int(r["roster_id"]), r.get("settings") or {}
        records[rid] = (int(s.get("wins", 0)), int(s.get("losses", 0)), int(s.get("ties", 0)))
        points_for[rid] = float(s.get("fpts", 0)) + float(s.get("fpts_decimal", 0)) / 100
        team_ids[rid] = [str(p) for p in r.get("players") or [] if str(p) not in reserve_of[rid]]

    schedule = []
    for w in weeks:
        groups: dict = {}
        for row in client.get_matchups(w):
            groups.setdefault(row.get("matchup_id"), []).append(int(row["roster_id"]))
        schedule.append([tuple(g) for k, g in groups.items() if k is not None and len(g) == 2])

    sd, played_weeks = config.ODDS_DEFAULT_SD, {}
    last_scored = int((league.get("settings") or {}).get("last_scored_leg") or weeks[0] - 1)
    for w in range(1, min(last_scored, weeks[0] - 1) + 1):
        try:
            for row in client.get_matchups(w, final=True):
                if row.get("points"):
                    played_weeks.setdefault(int(row["roster_id"]), []).append(float(row["points"]))
        except DataError:
            continue
    resid = [p - statistics.mean(v) for v in played_weeks.values() if len(v) > 1 for p in v]
    if len(resid) > 20:
        dof = len(resid) - len(played_weeks)
        sd = max(10.0, (sum(e * e for e in resid) / dof) ** 0.5)
    else:
        warnings.append(f"Not enough scoring history; using a default weekly spread of {sd:g} points.")

    meta = {"league_name": league.get("name", "League"), "season": season, "generated_at": report.now_iso(),
            "warnings": warnings, "week_range": f"{weeks[0]}-{weeks[-1]}"}
    return Context(meta, weeks, slots, pools, my_id, [p for p in map(str, mine.get("players") or [])
                   if p not in reserve_of[my_id]], team_ids, names, records, points_for, schedule, sd,
                   int((league.get("settings") or {}).get("playoff_teams") or 6))


# ------------------------------------------------------------------ simulation
def lineup_proj(pids, ctx: Context) -> list[float]:
    """Projected points of each week's best lineup (lineup chosen by score, summed in plain projection)."""
    return [sum(s.player.proj for s in l if s.player)
            for l in weekly_lineups(pids, ctx.pools, ctx.weeks, ctx.slots)]


def team_means(ctx: Context, my_roster: list[str], base_roster: list[str]) -> dict[int, list[float]]:
    """Expected weekly score per team: shrink each projection toward that team's season average.
    My team additionally gets the full projection change from the moves."""
    wgt = config.ODDS_PROJ_WEIGHT
    means = {}
    for rid, pids in ctx.teams.items():
        w, l, t = ctx.records[rid]
        games = max(w + l + t, 1)
        avg = ctx.points_for[rid] / games if ctx.points_for[rid] else None
        proj = lineup_proj(pids, ctx)
        means[rid] = [wgt * p + (1 - wgt) * avg if avg else p for p in proj]
    if my_roster != base_roster:
        before, after = lineup_proj(base_roster, ctx), lineup_proj(my_roster, ctx)
        means[ctx.my_id] = [m + (a - b) for m, b, a in zip(means[ctx.my_id], before, after)]
    return means


def run_odds(ctx: Context, means, sims: int = config.ODDS_SIMS) -> odds.Odds:
    return odds.simulate(means, ctx.schedule, ctx.records, ctx.points_for, ctx.sd, ctx.my_id,
                         ctx.playoff_teams, sims)


# ------------------------------------------------------------------ reporting
Section = tuple  # (title, headers, rows)


def _record(r) -> str:
    return f"{r[0]}-{r[1]}" + (f"-{r[2]}" if r[2] else "")


def _pct(x: float) -> str:
    return f"{100 * x:.0f}%"


def sections(ctx: Context, plan: Plan, before: odds.Odds, after: odds.Odds, means_after, show_all: bool) -> list[Section]:
    me, weeks, first = ctx.my_id, ctx.weeks, ctx.pools[ctx.weeks[0]]
    out: list[Section] = []

    order = sorted(ctx.records, key=lambda r: (-(ctx.records[r][0] + 0.5 * ctx.records[r][2]), -ctx.points_for[r]))
    rows = [[str(i), ctx.names[r] + (" (you)" if r == me else ""), _record(ctx.records[r]), f"{ctx.points_for[r]:.0f}",
             f"{sum(means_after[r]) / len(weeks):.0f}", _pct(before.playoff[r]), _pct(after.playoff[r])]
            for i, r in enumerate(order, 1)]
    out.append((f"PLAYOFF RACE (top {ctx.playoff_teams} advance; ties broken by points for)",
                ["#", "Team", "Record", "PF", "Proj/wk", "Odds now", "Odds after moves"], rows))

    opp_rows = []
    for i, w in enumerate(weeks):
        pair = next((g for g in ctx.schedule[i] if me in g), None)
        if not pair:
            opp_rows.append([str(w), "(bye)", "", "", "", ""])
            continue
        opp = pair[0] if pair[1] == me else pair[1]
        opp_rows.append([str(w), ctx.names[opp], _record(ctx.records[opp]), _pct(before.win_prob[me][i]),
                         _pct(after.win_prob[me][i]), f"{100 * after.swing[i]:+.0f} pts"])
    out.append(("YOUR GAMES", ["Week", "Opponent", "Record", "Win% now", "Win% after moves", "Playoff odds if you win vs lose"], opp_rows))

    out.append(("HOW YOUR FINISH MAPS TO THE PLAYOFFS (after moves)", ["Wins from here", "Chance", "Make playoffs"],
                [[str(k), _pct(a), _pct(b)] for k, (a, b) in after.by_wins.items()]))

    if plan.moves:
        mrows = []
        for i, m in enumerate(plan.moves, 1):
            backups = ", ".join(f"{b.name} ({g:+.0f})" for b, g in m.backups) or "-"
            mrows.append([str(i), f"{m.drop.name} ({m.drop.position}, {m.drop.tier})", f"{m.add.name} ({m.add.position})",
                          ", ".join(map(str, m.starts)) or "bench", f"{m.gain:+.1f}",
                          "/".join(f"{g:+.1f}" for g in m.per_week), backups])
        out.append(("RECOMMENDED MOVES", ["#", "Drop", "Add", "Starts in wk", f"Gain ({weeks[0]}-{weeks[-1]})",
                                          "Gain by wk", "If claimed: backups (gain)"], mrows))
    if plan.close_calls:
        out.append(("CLOSE CALLS (raise your lineup score, but by less than the swap margin)",
                    ["Drop", "Add", f"Gain ({weeks[0]}-{weeks[-1]})", "Gain by wk", "Margin asks"],
                    [[f"{c.drop.name} ({c.drop.position})", f"{c.add.name} ({c.add.position})", f"{c.gain:+.1f}",
                      "/".join(f"{g:+.1f}" for g in c.per_week), f"{c.needed:.0f}"] for c in plan.close_calls]))
    srows = [[str(w), f"{a:.0f}", f"{b:.0f}", f"{b - a:+.0f}"] for w, a, b in zip(weeks, plan.base_totals, plan.new_totals)]
    srows.append(["Total", f"{sum(plan.base_totals):.0f}", f"{sum(plan.new_totals):.0f}",
                  f"{sum(plan.new_totals) - sum(plan.base_totals):+.0f}"])
    out.append(("LINEUP SCORE", ["Week", "Current roster", "After moves", "Gain"], srows))

    for w, lineup in zip(weeks, weekly_lineups(plan.roster, ctx.pools, weeks, ctx.slots)):
        rows = []
        for s in lineup:
            p = s.player
            tag = " (ADD)" if p and p.is_free_agent else ""
            rows.append([s.position, p.name + tag if p else "(empty)", p.matchup_note if p else "",
                         f"{p.proj:.1f}" if p else "", f"{p.score:.1f}" if p else ""])
        out.append((f"WEEK {w} LINEUP", ["Slot", "Player", "Matchup", "Proj", "Score"], rows))

    watch = []
    for pid in plan.roster:
        for w in weeks:
            p = ctx.pools[w].get(pid)
            if p and not p.available:
                note = "BYE" if not p.opponent else (p.injury_status or "out")
                watch.append([p.name, p.position, str(w), note])
    if watch:
        out.append(("BYE / INJURY WATCH (your roster after moves)", ["Player", "Pos", "Week", "Why"], watch))

    if show_all:
        arows = []
        for pos in config.POSITIONS:
            ids = {pid for w in weeks for pid, p in ctx.pools[w].items() if p.is_free_agent and p.position == pos}
            ranked = sorted(ids, key=lambda pid: -sum(ctx.pools[w][pid].score for w in weeks if pid in ctx.pools[w]))
            for pid in ranked[:5]:
                arows.append([pos, any_eval(ctx.pools, weeks, pid).name] +
                             [f"{ctx.pools[w][pid].score:.1f}" if pid in ctx.pools[w] and ctx.pools[w][pid].available else "-"
                              for w in weeks])
        out.append(("TOP FREE AGENTS BY POSITION (score per week)", ["Pos", "Player"] + [f"Wk {w}" for w in weeks], arows))
    return out


NOTES = [
    "Weeks beyond the current one use Sleeper's projections and each week's opponent, with this week's recent form.",
    "A player ruled Out/IR now is assumed unavailable for every remaining week; 'Doubtful' only affects this week.",
    "A move must add at least the position's swap margin (tier_cutoffs.json) per week, on average over the weeks.",
    "Only lineup score is counted: bench depth for in-season injuries is not valued.",
    "Odds: 20,000 simulated seasons; weekly scores ~ Normal(projection blended with season average, league spread).",
    "Waiver claims can fail; each move lists backups for the same drop.",
]


def render_text(secs, ctx: Context) -> str:
    out = [f"{ctx.meta['league_name']} - rest of regular season, weeks {ctx.meta['week_range']} ({ctx.meta['season']})",
           "Lineup: " + report._lineup_label(ctx.slots)]
    for title, headers, rows in secs:
        out += ["", title, report._table(headers, rows) if rows else "  (none)"]
    out += [""] + [f"NOTE: {n}" for n in NOTES] + [f"WARNING: {w}" for w in ctx.meta["warnings"]]
    return "\n".join(out)


def render_markdown(secs, ctx: Context, plan: Plan) -> str:
    md = [f"# Rest of season (weeks {ctx.meta['week_range']}) - {ctx.meta['league_name']}",
          f"_Generated {ctx.meta['generated_at']} - season {ctx.meta['season']}_", ""]
    if not plan.moves:
        md += ["**No move clears the swap margin over the remaining weeks.**", ""]
    for title, headers, rows in secs:
        md += [f"## {title.title() if title.isupper() else title}"] + (report._md_table(headers, rows) if rows else ["None"]) + [""]
    md += ["## Assumptions"] + [f"- {n}" for n in NOTES] + [f"\n> WARNING: {w}" for w in ctx.meta["warnings"]]
    return "\n".join(md) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Rest-of-regular-season roster planner")
    parser.add_argument("--week", type=int, help="first week to plan (default: current)")
    parser.add_argument("--through", type=int, help="last week to plan (default: last regular-season week)")
    parser.add_argument("--max-moves", type=int, default=4, help="most add/drops to recommend (default 4)")
    parser.add_argument("--margin-scale", type=float, default=1.0,
                        help="multiply every swap margin (0 = take any lineup improvement; default 1)")
    parser.add_argument("--sims", type=int, default=config.ODDS_SIMS, help="playoff simulations (default 20000)")
    parser.add_argument("--show-all", action="store_true", help="also list top free agents per position")
    parser.add_argument("--refresh", action="store_true", help="ignore cached API responses")
    args = parser.parse_args(argv)
    try:
        ctx = build(args)
    except DataError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    plan = plan_moves(ctx, args.max_moves, margin_scale=args.margin_scale)
    base_means = team_means(ctx, ctx.my_active, ctx.my_active)
    new_means = team_means(ctx, plan.roster, ctx.my_active)
    before, after = run_odds(ctx, base_means, args.sims), run_odds(ctx, new_means, args.sims)
    secs = sections(ctx, plan, before, after, new_means, args.show_all)
    print(render_text(secs, ctx))
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = config.OUTPUT_DIR / f"rest_of_season_weeks_{ctx.meta['week_range']}.md"
    path.write_text(render_markdown(secs, ctx, plan))
    print(f"\nSaved: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
