import analyzer
import config
import nfl_data
import player_tier
from sleeper_client import fantasy_points


def test_fantasy_points_uses_league_scoring():
    scoring = {"pass_yd": 0.04, "pass_td": 6.0, "pass_int": -2.0}
    stats = {"pass_yd": 300, "pass_td": 2, "pass_int": 1, "pts_ppr": 99}
    assert round(fantasy_points(stats, scoring), 2) == 22.0


def test_projected_points_falls_back_to_ppr_when_keys_mismatch():
    row = {"stats": {"weird_key": 5, "pts_ppr": 7.5}}
    assert analyzer.projected_points(row, {"pass_yd": 0.04}) == 7.5


def test_composite_weights_and_matchup_direction():
    flat = analyzer.composite_score(20, 20, 0.0)
    assert flat == 20
    easy = analyzer.composite_score(20, 20, 1.0)
    hard = analyzer.composite_score(20, 20, -1.0)
    assert hard < flat < easy
    # 40% weight * 30% swing = 12% max movement
    assert round(easy / flat, 3) == 1.12


def test_trend_defaults_to_projection():
    assert analyzer.composite_score(10, None, 0.0) == 10


def test_tiers():
    assert player_tier.classify("WR", 1) == player_tier.ELITE
    assert player_tier.classify("WR", config.elite_rank("WR")) == player_tier.ELITE
    assert player_tier.classify("WR", config.elite_rank("WR") + 1) == player_tier.MID
    assert player_tier.classify("WR", config.mid_rank("WR")) == player_tier.MID
    assert player_tier.classify("WR", config.mid_rank("WR") + 1) == player_tier.BENCH
    assert player_tier.classify("K", None) == player_tier.NA


def test_each_position_uses_its_own_cutoffs():
    for pos in config.TIERED_POSITIONS:
        elite, mid = config.elite_rank(pos), config.mid_rank(pos)
        assert player_tier.classify(pos, elite) == player_tier.ELITE
        assert player_tier.classify(pos, mid + 1) == player_tier.BENCH
        if mid > elite:
            assert player_tier.classify(pos, elite + 1) == player_tier.MID
            assert player_tier.classify(pos, mid) == player_tier.MID


def test_cutoffs_are_read_per_position(monkeypatch):
    import copy
    tiers = copy.deepcopy(config.TIERS)
    tiers["TE"].update(elite_rank=1, mid_rank=2)
    tiers["WR"].update(elite_rank=10, mid_rank=20)
    monkeypatch.setattr(config, "TIERS", tiers)
    assert player_tier.classify("TE", 5) == player_tier.BENCH
    assert player_tier.classify("WR", 5) == player_tier.ELITE


def _write(tmp_path, mutate=None):
    import json
    data = json.loads((config.ROOT / "tier_cutoffs.json").read_text())
    if mutate:
        mutate(data)
    f = tmp_path / "t.json"
    f.write_text(json.dumps(data))
    return f


def test_shipped_file_has_explicit_values_for_every_position():
    import json
    raw = json.loads(config.TIER_FILE.read_text())
    t = config.load_tiers()
    assert set(t) == {"QB", "RB", "WR", "TE", "K", "DEF"}
    assert t == {k: v for k, v in raw.items() if not k.startswith("_")}  # values come straight from the file
    for pos in ("K", "DEF"):
        assert (t[pos]["elite_rank"], t[pos]["mid_rank"], t[pos]["elite_swap_margin"]) == (None, None, None)
    assert config.TIERED_POSITIONS == ("QB", "RB", "WR", "TE")


def test_help_notes_are_ignored_and_values_come_only_from_the_file(tmp_path):
    def edit(d):
        d["TE"]["swap_margin"] = d["TE"]["elite_swap_margin"] / 2
        d["_note"] = "anything"
    expected = config.elite_swap_margin("TE") / 2
    assert config.load_tiers(_write(tmp_path, edit))["TE"]["swap_margin"] == expected


def test_missing_or_invalid_entries_raise_clear_errors(tmp_path):
    def drop_pos(d): del d["K"]
    def drop_field(d): del d["WR"]["mid_rank"]
    def typo(d): d["WR"]["elit_rank"] = 3
    def inverted(d): d["QB"]["elite_rank"] = d["QB"]["mid_rank"] + 1
    def tiered_k(d): d["K"]["elite_rank"] = 5
    def small_elite_margin(d): d["RB"]["elite_swap_margin"] = d["RB"]["swap_margin"] - 1
    def unknown_pos(d): d["FLEX"] = d["WR"]
    for mutate, needle in [(drop_pos, "K is missing"), (drop_field, "WR needs exactly"), (typo, "WR needs exactly"),
                           (inverted, "QB needs integer ranks"), (tiered_k, "K has no tiers"),
                           (small_elite_margin, "RB.elite_swap_margin"), (unknown_pos, "unknown position")]:
        try:
            config.load_tiers(_write(tmp_path, mutate))
        except ValueError as exc:
            assert needle in str(exc), (needle, str(exc))
        else:
            raise AssertionError(f"expected ValueError containing {needle!r}")
    try:
        config.load_tiers(tmp_path / "missing.json")
    except ValueError as exc:
        assert "not found" in str(exc)
    else:
        raise AssertionError("expected ValueError for missing file")


def test_position_ranks_use_search_rank():
    players = {
        "1": {"position": "WR", "team": "MIN", "search_rank": 5},
        "2": {"position": "WR", "team": "DAL", "search_rank": 1},
        "3": {"position": "WR", "team": None, "search_rank": 2},  # no team -> ignored
    }
    ranks = player_tier.build_position_ranks(players)
    assert ranks == {"2": 1, "1": 2}


def _teams(n=32):
    teams = {}
    for i in range(n):
        teams[f"T{i}"] = nfl_data.TeamContext(f"T{i}", 180 + i * 3, 90 + i * 2, 300 + i)
    nfl_data.assign_ranks(teams)
    return teams


def test_matchup_rank_and_direction():
    teams = _teams()
    # T0 allows fewest pass yards -> rank 1 -> toughest for a QB
    adj_hard, note = nfl_data.matchup("QB", "T0", teams)
    adj_easy, _ = nfl_data.matchup("QB", "T31", teams)
    assert adj_hard == -1.0 and adj_easy == 1.0
    assert "Pass D #1/32" in note
    assert nfl_data.matchup("RB", "T31", teams)[0] == 1.0
    assert "Run D" in nfl_data.matchup("RB", "T5", teams)[1]
    # DEF: opponent with the worst offense is the easiest draw (T0 has lowest off_ypg)
    assert nfl_data.matchup("DEF", "T0", teams)[0] == 1.0
    assert nfl_data.matchup("K", "T0", teams)[0] == 0.0
    assert nfl_data.matchup("QB", None, teams) == (0.0, "BYE")


def test_parse_espn_team_stats_separates_own_and_opponent():
    payload = {"results": {
        "stats": {"categories": [
            {"name": "passing", "stats": [{"name": "netPassingYardsPerGame", "value": 250.0}]},
            {"name": "rushing", "stats": [{"name": "rushingYardsPerGame", "value": 110.0}]},
        ]},
        "opponent": [
            {"name": "passing", "stats": [{"name": "netPassingYardsPerGame", "value": 218.0}]},
            {"name": "rushing", "stats": [{"name": "rushingYardsPerGame", "value": 173.5}]},
        ],
    }}
    ctx = nfl_data.parse_team_stats("DAL", payload)
    assert (ctx.pass_allowed_ypg, ctx.rush_allowed_ypg, ctx.off_ypg) == (218.0, 173.5, 360.0)


def test_parse_returns_none_when_opponent_stats_missing():
    payload = {"stats": {"categories": [
        {"name": "passing", "stats": [{"name": "netPassingYardsPerGame", "value": 250.0}]}]}}
    assert nfl_data.parse_team_stats("DAL", payload) is None
