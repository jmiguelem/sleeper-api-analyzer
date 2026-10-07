"""End-to-end run of main.main() against a fake Sleeper/ESPN (no network)."""
import config
import main
import nfl_data

SCORING = {"pass_yd": 0.04, "pass_td": 6.0, "rush_yd": 0.1, "rec_yd": 0.1, "rec": 1.0, "rec_td": 6.0}


class FakeClient:
    def __init__(self, *a, **k):
        pass

    def get_state(self):
        return {"season": "2026", "week": 5}

    def get_league(self):
        return {"name": "National Gooning Association", "scoring_settings": SCORING,
                "roster_positions": ["RB", "RB", "WR", "WR", "TE", "FLEX", "FLEX", "BN", "BN"]}

    def get_rosters(self):
        return [{"owner_id": "1395864061605855232", "players": ["jj", "wr2", "wr3", "wr4", "wr5", "rb1"], "reserve": []},
                {"owner_id": "other", "players": ["taken"]}]

    def get_my_roster(self, rosters=None):
        return self.get_rosters()[0]

    def get_players(self):
        def p(pos, rank, name, inj=None):
            return {"position": pos, "team": "MIN", "search_rank": rank, "full_name": name,
                    "status": "Active", "injury_status": inj}
        return {"jj": p("WR", 1, "Justin Jefferson", "Out"), "wr2": p("WR", 80, "Scrub WR"), "wr3": p("WR", 81, "Scrub WR3"),
                "wr4": p("WR", 82, "Scrub WR4"), "wr5": p("WR", 83, "Scrub WR5"),
                "rb1": p("RB", 2, "Star RB"), "fa_wr": p("WR", 60, "Hot FA WR"),
                "fa_rb": p("RB", 70, "Meh FA RB"), "taken": p("WR", 30, "Rostered Elsewhere")}

    def get_projections(self, season, week):
        def row(pid, rec_yd, opp="DAL"):
            return {"player_id": pid, "opponent": opp, "stats": {"rec_yd": rec_yd, "rec": rec_yd / 10}}
        return [row("jj", 100), row("wr2", 20), row("wr3", 30), row("wr4", 40), row("wr5", 50), row("rb1", 120), row("fa_wr", 110), row("fa_rb", 30),
                row("taken", 150)]

    def recent_scores(self, season, week, scoring):
        return {"wr2": 4.0}


def fake_teams(refresh=False, session=None):
    teams = {f"T{i}": nfl_data.TeamContext(f"T{i}", 180 + i, 90 + i, 300 + i) for i in range(32)}
    teams["DAL"] = nfl_data.TeamContext("DAL", 260, 140, 340)
    nfl_data.assign_ranks(teams)
    return teams


def test_main_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(main, "SleeperClient", FakeClient)
    monkeypatch.setattr(main.nfl_data, "fetch_team_contexts", fake_teams)
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    assert main.main(["--week", "5", "--show-all"]) == 0
    out = capsys.readouterr().out
    assert "Lineup: 2 RB, 2 WR, 1 TE, 2 FLEX" in out  # slots read from the league settings
    assert "RECOMMENDED MOVES (1)" in out
    assert "ADD Hot FA WR" in out and "DROP Scrub WR " in out  # weakest droppable WR goes
    assert "Rostered Elsewhere" not in out.split("TOP FREE AGENTS")[0]
    assert "KEEP Justin Jefferson" in out  # elite + Out -> protected, never dropped
    md = (tmp_path / "week_5_recommendations.md").read_text()
    assert md.startswith("# Week 5 Recommendations")
    assert "**Drop Scrub WR** (WR" in md and "**add Hot FA WR**" in md
    assert "- **Justin Jefferson**" in md.split("## Injury / bye watch")[1]
    assert "current roster (+" in md  # positive gain
    assert not list(tmp_path.glob("*.json"))
