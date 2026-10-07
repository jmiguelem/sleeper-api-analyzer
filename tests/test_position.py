import config
import main
import nfl_data
import position
import recommender
from test_pipeline import FakeClient, fake_teams


def run(monkeypatch, argv):
    monkeypatch.setattr(main, "SleeperClient", FakeClient)
    monkeypatch.setattr(main.nfl_data, "fetch_team_contexts", fake_teams)
    return position.main(argv)


def test_wr_list_has_my_players_and_free_agent_with_status(monkeypatch, capsys):
    assert run(monkeypatch, ["wr", "--week", "5"]) == 0
    out = capsys.readouterr().out
    assert "Justin Jefferson" in out and "Hot FA WR" in out
    assert "Rostered Elsewhere" not in out          # on another team, not a free agent
    assert "Meh FA RB" not in out and "Star RB" not in out  # other positions excluded
    assert "ADD to" in out and "DROP (for Hot FA WR)" in out and "protected" in out


def test_top_limits_rows_and_flex_spans_positions(monkeypatch, capsys):
    assert run(monkeypatch, ["WR", "--top", "2"]) == 0
    rows = [l for l in capsys.readouterr().out.splitlines() if l[:1].isdigit()]
    assert len(rows) == 2
    assert run(monkeypatch, ["FLEX"]) == 0
    out = capsys.readouterr().out
    assert "Star RB" in out and "Hot FA WR" in out


def test_free_agents_capped_at_five_and_sorted_by_score_regardless_of_owner():
    def fa(i, score):
        p = recommender  # silence linters
        from analyzer import PlayerEval
        return PlayerEval(f"f{i}", f"FA{i}", "WR", "AAA", "XYZ", None, score, None, 0.0, "vs XYZ", score,
                          "Bench", None, True, False, True)
    fas = [fa(i, 10 + i) for i in range(8)]
    result = recommender.recommend([], fas)
    rows = position.build_list("WR", [], fas, result, None)
    assert len(rows) == position.FREE_AGENTS_SHOWN
    assert [r[1] for r in rows] == ["FA7", "FA6", "FA5", "FA4", "FA3"]


def test_owner_does_not_affect_order():
    from analyzer import PlayerEval

    def pe(pid, score, fa):
        return PlayerEval(pid, pid, "TE", "AAA", "XYZ", None, score, None, 0.0, "vs XYZ", score,
                          "Bench", None, True, False, fa)
    mine = [pe("m1", 9.0, False), pe("m2", 12.0, False)]
    fas = [pe("f1", 10.5, True)]
    rows = position.build_list("TE", mine, fas, recommender.recommend(mine, fas), None)
    assert [r[1] for r in rows] == ["m2", "f1", "m1"]


def test_fa_only_hides_my_players_and_top_sets_how_many_free_agents(monkeypatch, capsys):
    assert run(monkeypatch, ["WR", "--fa-only"]) == 0
    out = capsys.readouterr().out
    assert "Hot FA WR" in out and "Justin Jefferson" not in out and "Scrub WR" not in out
    assert "Mine" not in out

    from analyzer import PlayerEval
    fas = [PlayerEval(f"f{i}", f"FA{i}", "WR", "AAA", "XYZ", None, 10 + i, None, 0.0, "vs XYZ", 10 + i,
                      "Bench", None, True, False, True) for i in range(9)]
    result = recommender.recommend([], fas)
    assert len(position.build_list("WR", [], fas, result, None, fa_only=True)) == 5
    assert len(position.build_list("WR", [], fas, result, 8, fa_only=True)) == 8
