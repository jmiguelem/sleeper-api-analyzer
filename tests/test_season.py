"""Rest-of-season planner and playoff-odds simulator (no network)."""
import config
import nfl_data
import odds
import player_tier
import season
from analyzer import PlayerEval

WEEKS = [1, 2, 3]
SLOTS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1}
MARGIN = config.swap_margin("WR")


def ev(pid, pos, score, tier=player_tier.BENCH, fa=False, injury=None, reserve=False, played=False):
    """One week's evaluation. score=None means bye (unavailable)."""
    bye = score is None
    return PlayerEval(
        player_id=pid, name=pid, position=pos, team="AAA", opponent=None if bye else "XYZ", injury_status=injury,
        proj=0.0 if bye else score, trend=None, matchup_adj=0.0, matchup_note="BYE" if bye else "vs XYZ",
        score=0.0 if bye else score, tier=tier, pos_rank=None, available=not bye, on_reserve=reserve,
        is_free_agent=fa, played=played,
    )


def make_ctx(rows, my=None, weeks=WEEKS):
    """rows: {player_id: (pos, [score per week or None], kwargs)} -> Context whose roster is `my`."""
    pools = {w: {} for w in weeks}
    for pid, (pos, scores, kw) in rows.items():
        for w, sc in zip(weeks, scores):
            pools[w][pid] = ev(pid, pos, sc, **kw)
    mine = my if my is not None else [pid for pid, (_, _, kw) in rows.items() if not kw.get("fa")]
    return season.Context(meta={}, weeks=weeks, slots=SLOTS, pools=pools, my_id=1, my_active=mine,
                          teams={1: mine}, names={1: "me"}, records={1: (0, 0, 0)}, points_for={1: 0.0},
                          schedule=[[] for _ in weeks], sd=25.0, playoff_teams=1)


def core():
    """Lineup-filling roster; the weakest starter is FLEX WR 'w3' (20/wk), with bench WR 'bench' at 5/wk."""
    return {
        "qb": ("QB", [25, 25, 25], {}), "r1": ("RB", [20, 20, 20], {}), "r2": ("RB", [19, 19, 19], {}),
        "w1": ("WR", [22, 22, 22], {}), "w2": ("WR", [21, 21, 21], {}), "w3": ("WR", [20, 20, 20], {}),
        "te": ("TE", [12, 12, 12], {}), "bench": ("WR", [5, 5, 5], {}),
    }


def test_free_agent_that_helps_every_week_beats_a_one_week_spike():
    rows = core()
    rows["spike"] = ("WR", [20 + MARGIN * 3 + 2, 0, 0], {"fa": True})     # +17 in week 1 only: clears the bar, smaller total
    rows["steady"] = ("WR", [20 + MARGIN + 3] * 3, {"fa": True})            # +8 every week = +24
    plan = season.plan_moves(make_ctx(rows), max_moves=1)
    assert [m.add.player_id for m in plan.moves] == ["steady"]
    assert plan.moves[0].drop.player_id == "bench"                       # the bench WR never starts: free to drop
    assert plan.moves[0].starts == [1, 2, 3]
    assert plan.new_totals[0] > plan.base_totals[0]


def test_gain_below_margin_is_a_close_call_until_the_margin_is_scaled_down():
    rows = core()
    rows["fa"] = ("WR", [21, 21, 21], {"fa": True})                       # +1 per week over w3: under the margin
    ctx = make_ctx(rows)
    plan = season.plan_moves(ctx, max_moves=2)
    assert plan.moves == []
    assert [c.add.player_id for c in plan.close_calls] == ["fa"]
    assert round(plan.close_calls[0].gain, 6) == 3.0 and plan.close_calls[0].needed == MARGIN * 3
    assert [m.add.player_id for m in season.plan_moves(ctx, 2, margin_scale=0).moves] == ["fa"]


def test_bench_player_covering_a_bye_week_is_worth_more_than_the_scrub():
    rows = core()
    rows["w3"] = ("WR", [20, None, 20], {})                               # starter on bye in week 2
    rows["cover"] = ("WR", [8, 18, 8], {})                                 # covers the bye
    rows["fa"] = ("WR", [20 + MARGIN + 1] * 3, {"fa": True})
    plan = season.plan_moves(make_ctx(rows), max_moves=1)
    assert plan.moves[0].drop.player_id == "bench"                         # not 'cover'
    assert plan.moves[0].starts == [1, 2, 3]


def test_protected_and_played_players_are_never_dropped():
    rows = core()
    rows["bench"] = ("WR", [5, 5, 5], {"tier": player_tier.ELITE, "injury": "IR"})   # elite + injured: protected
    rows["other"] = ("WR", [6, 6, 6], {"played": True})                                # already has points: locked
    rows["fa"] = ("WR", [60, 60, 60], {"fa": True})
    plan = season.plan_moves(make_ctx(rows), max_moves=1)
    dropped = {m.drop.player_id for m in plan.moves}
    assert dropped.isdisjoint({"bench", "other"})


def test_free_agent_on_bye_in_week_one_is_still_a_candidate():
    rows = core()
    rows["late"] = ("WR", [None, 30, 30], {"fa": True})
    plan = season.plan_moves(make_ctx(rows), max_moves=1)
    assert plan.moves and plan.moves[0].add.player_id == "late"
    assert plan.moves[0].starts == [2, 3]


def test_max_moves_is_respected_and_each_add_is_used_once():
    rows = core()
    rows["fa1"] = ("WR", [40, 40, 40], {"fa": True})
    rows["fa2"] = ("WR", [39, 39, 39], {"fa": True})
    rows["fa3"] = ("WR", [38, 38, 38], {"fa": True})
    plan = season.plan_moves(make_ctx(rows), max_moves=2)
    assert len(plan.moves) == 2
    assert len({m.add.player_id for m in plan.moves}) == 2 == len({m.drop.player_id for m in plan.moves})


# ------------------------------------------------------------------ odds
def test_win_probability_is_symmetric_and_monotonic():
    assert odds.win_probability(100, 100, 25) == 0.5
    assert odds.win_probability(120, 100, 25) > 0.5 > odds.win_probability(100, 120, 25)
    assert abs(odds.win_probability(120, 100, 25) + odds.win_probability(100, 120, 25) - 1) < 1e-12


def tiny_league(my_mean):
    means = {1: [my_mean] * 2, 2: [100.0] * 2, 3: [100.0] * 2, 4: [100.0] * 2}
    schedule = [[(1, 2), (3, 4)], [(1, 3), (2, 4)]]
    records = {r: (0, 0, 0) for r in means}
    return means, schedule, records, {r: 0.0 for r in means}


def test_playoff_slots_are_conserved_and_strength_matters():
    means, schedule, records, pf = tiny_league(130.0)
    strong = odds.simulate(means, schedule, records, pf, 25.0, focus=1, playoff_teams=2, n=3000)
    assert abs(sum(strong.playoff.values()) - 2) < 1e-9
    means, *_ = tiny_league(70.0)
    weak = odds.simulate(means, schedule, records, pf, 25.0, focus=1, playoff_teams=2, n=3000)
    assert strong.playoff[1] > 0.9 > 0.2 > weak.playoff[1]


def test_swing_and_finish_table_describe_the_focus_team():
    means, schedule, records, pf = tiny_league(100.0)
    res = odds.simulate(means, schedule, records, pf, 25.0, focus=1, playoff_teams=2, n=4000)
    assert all(s > 0 for s in res.swing)                                 # winning a game always helps
    assert abs(sum(v[0] for v in res.by_wins.values()) - 1) < 1e-9
    assert res.by_wins[2][1] > res.by_wins[0][1]                         # more wins -> better odds
    assert all(0 < w < 1 for w in res.win_prob[1])


def test_same_seed_gives_identical_results():
    args = tiny_league(110.0)
    a = odds.simulate(*args, 25.0, focus=1, playoff_teams=2, n=500)
    b = odds.simulate(*args, 25.0, focus=1, playoff_teams=2, n=500)
    assert a.playoff == b.playoff


# ------------------------------------------------------------------ end to end
SCORING = {"rec_yd": 0.1, "rec": 1.0, "rush_yd": 0.1}


class FakeClient:
    """Two teams, three-week horizon (weeks 5-7). 'me' has a scrub WR a free agent clearly beats."""
    def __init__(self, *a, **k):
        pass

    def get_state(self):
        return {"season": "2026", "week": 5}

    def get_league(self):
        return {"name": "Fake League", "scoring_settings": SCORING,
                "roster_positions": ["RB", "WR", "WR", "FLEX", "BN", "BN"],
                "settings": {"playoff_week_start": 8, "playoff_teams": 1, "last_scored_leg": 4}}

    def get_rosters(self):
        return [{"roster_id": 1, "owner_id": "1395864061605855232", "players": ["rb1", "wr1", "wr2", "scrub", "ir"],
                 "reserve": ["ir"], "settings": {"wins": 1, "losses": 3, "fpts": 300}},
                {"roster_id": 2, "owner_id": "o2", "players": ["orb", "owr1", "owr2", "owr3"], "reserve": [],
                 "settings": {"wins": 3, "losses": 1, "fpts": 420}}]

    def get_my_roster(self, rosters=None):
        return self.get_rosters()[0]

    def get_my_matchup(self, week, roster_id):
        return {}

    def get_users(self):
        return [{"user_id": "1395864061605855232", "display_name": "me"}, {"user_id": "o2", "display_name": "rival"}]

    def get_matchups(self, week, final=False):
        pts = {1: 80.0 + week, 2: 110.0 - week} if week < 5 else {1: 0, 2: 0}
        return [{"roster_id": r, "matchup_id": 1, "points": p} for r, p in pts.items()]

    def get_players(self):
        def p(pos, rank, name, inj=None):
            return {"position": pos, "team": "MIN", "search_rank": rank, "full_name": name, "status": "Active",
                    "injury_status": inj}
        return {"rb1": p("RB", 3, "Star RB"), "wr1": p("WR", 5, "Star WR"), "wr2": p("WR", 8, "Good WR"),
                "scrub": p("WR", 90, "Scrub WR"), "ir": p("WR", 1, "Hurt WR", "IR"), "orb": p("RB", 2, "Their RB"),
                "owr1": p("WR", 4, "Their WR1"), "owr2": p("WR", 6, "Their WR2"), "owr3": p("WR", 7, "Their WR3"),
                "fa": p("WR", 60, "Hot FA WR")}

    def get_projections(self, season, week):
        def row(pid, yd):
            return {"player_id": pid, "opponent": "DAL", "stats": {"rec_yd": yd, "rec": yd / 10}}
        return [row("rb1", 100), row("wr1", 90), row("wr2", 80), row("scrub", 20), row("ir", 150), row("orb", 100),
                row("owr1", 90), row("owr2", 80), row("owr3", 70), row("fa", 85)]

    def recent_scores(self, season, week, scoring):
        return {}


def fake_teams(refresh=False, session=None):
    teams = {f"T{i}": nfl_data.TeamContext(f"T{i}", 180 + i, 90 + i, 300 + i) for i in range(32)}
    teams["DAL"] = nfl_data.TeamContext("DAL", 260, 140, 340)
    nfl_data.assign_ranks(teams)
    return teams


def test_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(season, "SleeperClient", FakeClient)
    monkeypatch.setattr(season.nfl_data, "fetch_team_contexts", fake_teams)
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    assert season.main(["--sims", "500", "--show-all"]) == 0
    out = capsys.readouterr().out
    assert "weeks 5-7" in out
    assert "RECOMMENDED MOVES" in out and "Hot FA WR" in out and "Scrub WR" in out
    assert "Hurt WR" not in out.split("WEEK 5 LINEUP")[0].split("RECOMMENDED MOVES")[1]  # IR slot is never touched
    assert "PLAYOFF RACE" in out and "(you)" in out
    md = (tmp_path / "rest_of_season_weeks_5-7.md").read_text()
    assert md.startswith("# Rest of season (weeks 5-7)") and "## Assumptions" in md


def test_build_rejects_a_horizon_that_has_already_ended(monkeypatch):
    monkeypatch.setattr(season.nfl_data, "fetch_team_contexts", fake_teams)
    import argparse
    args = argparse.Namespace(week=9, through=None, refresh=False)
    try:
        season.build(args, FakeClient())
    except season.DataError as exc:
        assert "Nothing to plan" in str(exc)
    else:
        raise AssertionError("expected DataError")
