import config
import player_tier
import recommender
from analyzer import PlayerEval


def P(pid, pos, score, tier=player_tier.BENCH, available=True, opp="XYZ", injury=None, reserve=False, fa=False):
    return PlayerEval(
        player_id=pid, name=f"P{pid}", position=pos, team="AAA", opponent=opp if available or injury else None,
        injury_status=injury, proj=score, trend=None, matchup_adj=0.0, matchup_note="vs XYZ",
        score=score if available else 0.0, tier=tier, pos_rank=None, available=available,
        on_reserve=reserve, is_free_agent=fa,
    )


def base_roster():
    """Fills every slot; weakest lineup player is RB 'r3' (17, in FLEX); 'b' (5) is on the bench."""
    return [
        P("w1", "WR", 20), P("w2", "WR", 19), P("r1", "RB", 20), P("r2", "RB", 19), P("t1", "TE", 15),
        P("w3", "WR", 18), P("r3", "RB", 17), P("b", "WR", 5),
    ]


def margin(pos):
    return config.swap_margin(pos)


def strong(pos):
    """A free-agent score that clears any rostered player in base_roster/full_roster by the position's margin."""
    return 40 + margin(pos)


def names(slots):
    return [s.player.player_id for s in slots if s.player]


def test_no_move_when_free_agent_does_not_clear_threshold():
    res = recommender.recommend(base_roster(), [P("fa", "WR", 17 + margin("WR") - 0.1, fa=True)])  # needs > 17 + margin
    assert res.moves == []
    assert res.optimal_total == res.current_total


def test_free_agent_clearing_threshold_replaces_weakest_bench_player():
    res = recommender.recommend(base_roster(), [P("fa", "WR", 17 + margin("WR") + 0.1, fa=True)])
    assert len(res.moves) == 1
    m = res.moves[0]
    assert (m.add.player_id, m.drop.player_id) == ("fa", "b")
    assert "fa" in names(res.starters + res.flex)
    assert res.gain > 0


def test_flex_compares_across_positions():
    # An RB free agent upgrades the FLEX slot even though WRs fill the WR slots.
    res = recommender.recommend(base_roster(), [P("fa", "RB", 20 + margin("RB") + 1, fa=True)])
    assert res.moves[0].add.player_id == "fa"
    assert "fa" in names(res.starters)  # beats r1 (20) by the margin: becomes an RB starter, pushing r2/r3 down
    assert {"r1", "r2"} <= set(names(res.starters + res.flex)) or "r3" in {p.player_id for p in res.bench}


def test_starters_filled_by_position_before_flex():
    res = recommender.recommend(base_roster(), [])
    assert names(res.starters) == ["r1", "r2", "w1", "w2", "t1"]  # fixed order QB, RB, WR, TE, K, DEF
    assert set(names(res.flex)) == {"w3", "r3"}


def test_elite_needs_larger_margin():
    roster = base_roster()
    roster[0] = P("w1", "WR", 20, tier=player_tier.ELITE)
    roster[1] = P("w2", "WR", 19, tier=player_tier.ELITE)
    roster[5] = P("w3", "WR", 18, tier=player_tier.ELITE)
    # free agent clears the normal margin over the weakest lineup player (RB 17) but not elite protection of WRs
    res = recommender.recommend(roster, [P("fa", "WR", 17 + margin("WR") + 0.5, fa=True)])
    assert len(res.moves) == 1  # still displaces the non-elite RB 17 in the flex slot


def test_unavailable_elite_is_never_dropped_and_is_on_watch():
    jj = P("jj", "WR", 0, tier=player_tier.ELITE, available=False, injury="Out")
    roster = [p for p in base_roster() if p.player_id != "b"] + [jj]
    res = recommender.recommend(roster, [P("fa", "TE", strong("TE"), fa=True)])
    assert all(m.drop.player_id != "jj" for m in res.moves)
    assert "jj" in {p.player_id for p in res.watch}
    assert "jj" in {p.player_id for p in res.bench}


def test_unavailable_mid_is_protected_too():
    mid = P("m", "RB", 0, tier=player_tier.MID, available=False, injury="Out")
    roster = [p for p in base_roster() if p.player_id != "b"] + [mid]
    res = recommender.recommend(roster, [P("fa", "TE", strong("TE"), fa=True)])
    assert all(m.drop.player_id != "m" for m in res.moves)


def test_unavailable_bench_tier_player_can_be_dropped():
    scrub = P("s", "WR", 0, available=False, injury="Out")
    roster = [p for p in base_roster() if p.player_id != "b"] + [scrub]
    res = recommender.recommend(roster, [P("fa", "WR", strong("WR"), fa=True)])
    assert res.moves and res.moves[0].drop.player_id == "s"


def test_free_agent_skipped_when_nobody_can_be_dropped():
    # Roster has no TE and its only bench player is protected: filling TE would need an illegal drop.
    jj = P("jj", "WR", 0, tier=player_tier.ELITE, available=False, injury="Out")
    roster = [P("w1", "WR", 20), P("w2", "WR", 19), P("r1", "RB", 20), P("r2", "RB", 19), jj]
    res = recommender.recommend(roster, [P("fa", "TE", strong("TE"), fa=True)])
    assert res.moves == []
    assert "fa" not in names(res.starters + res.flex)


def test_displaced_starter_becomes_droppable():
    # A strong free agent pushes the weakest lineup player to the bench, where he can be dropped.
    jj = P("jj", "WR", 0, tier=player_tier.ELITE, available=False, injury="Out")
    roster = [p for p in base_roster() if p.player_id != "b"] + [jj]
    res = recommender.recommend(roster, [P("fa", "WR", strong("WR"), fa=True)])
    assert [m.drop.player_id for m in res.moves] == ["r3"]


def test_reserve_and_non_lineup_positions_untouched():
    ir = P("ir", "RB", 0, available=False, injury="IR", reserve=True)
    qb = P("qb", "QB", 12)
    roster = base_roster() + [ir, qb]
    res = recommender.recommend(roster, [P("fa", "WR", strong("WR"), fa=True)])
    assert all(m.drop.player_id not in ("ir", "qb") for m in res.moves)


def test_each_free_agent_used_once_and_unavailable_fas_ignored():
    fas = [P("fa1", "WR", strong("WR"), fa=True), P("fa2", "WR", strong("WR") - 1, fa=True),
           P("fa3", "WR", 0, fa=True, available=False, injury="Out")]
    res = recommender.recommend(base_roster(), fas)
    adds = [m.add.player_id for m in res.moves]
    assert len(adds) == len(set(adds)) and "fa3" not in adds
    assert len({m.drop.player_id for m in res.moves}) == len(res.moves)


def full_roster():
    return base_roster() + [P("q1", "QB", 18), P("k1", "K", 8), P("d1", "DEF", 9)]


def test_qb_k_def_are_starters_and_empty_slots_are_visible():
    res = recommender.recommend(base_roster(), [])
    empty = [s.position for s in res.starters if s.player is None]
    assert empty == ["QB", "K", "DEF"]
    res = recommender.recommend(full_roster(), [])
    assert {"q1", "k1", "d1"} <= set(names(res.starters))


def test_free_agent_defense_replaces_weak_defense_only_with_margin():
    roster = full_roster() + [P("d2", "DEF", 2)]  # benched DEF is the drop candidate; starter d1 is 9
    assert recommender.recommend(roster, [P("fd", "DEF", 9 + margin("DEF") - 0.1, fa=True)]).moves == []
    res = recommender.recommend(roster, [P("fd", "DEF", 9 + margin("DEF") + 0.1, fa=True)])
    assert [(m.add.player_id, m.drop.player_id, m.slot) for m in res.moves] == [("fd", "d2", "DEF")]


def test_k_on_bye_is_replaced_by_free_agent_kicker():
    roster = [p for p in full_roster() if p.player_id != "k1"] + [P("k1", "K", 0, available=False, opp=None)]
    res = recommender.recommend(roster, [P("fk", "K", margin("K") + 1, fa=True)])
    assert [(m.add.player_id, m.drop.player_id) for m in res.moves] == [("fk", "k1")]


def test_unavailable_elite_qb_is_protected():
    qb = P("q1", "QB", 0, tier=player_tier.ELITE, available=False, injury="Out")
    roster = [p for p in full_roster() if p.player_id != "q1"] + [qb]
    res = recommender.recommend(roster, [P("fq", "QB", strong("QB"), fa=True)])
    assert all(m.drop.player_id != "q1" for m in res.moves)
    assert "q1" in {p.player_id for p in res.watch}


def test_slots_come_from_league_roster_positions():
    positions = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "FLEX", "K", "DEF", "BN", "BN", "IR"]
    assert config.slots_from_league(positions) == {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 2, "K": 1, "DEF": 1}
    assert config.slots_from_league(None) == config.LINEUP_SLOTS


def test_super_flex_accepts_a_second_qb():
    slots = {"QB": 1, "SUPER_FLEX": 1}
    roster = [P("q1", "QB", 20), P("q2", "QB", 18), P("w", "WR", 10)]
    res = recommender.recommend(roster, [], slots)
    assert names(res.starters + res.flex) == ["q1", "q2"]


def test_kicker_uses_its_own_margin():
    roster = full_roster() + [P("k2", "K", 1)]  # starter K is 8
    assert recommender.recommend(roster, [P("fk", "K", 8 + margin("K") - 0.1, fa=True)]).moves == []
    res = recommender.recommend(roster, [P("fk", "K", 8 + margin("K") + 0.1, fa=True)])
    assert [(m.add.player_id, m.drop.player_id) for m in res.moves] == [("fk", "k2")]


def test_margins_come_from_the_tier_file(monkeypatch):
    # Raising the WR margin in the loaded tiers stops an upgrade that normally triggers a move.
    import copy
    fa = P("fa", "WR", 17 + margin("WR") + 1, fa=True)
    tiers = copy.deepcopy(config.TIERS)
    tiers["WR"]["swap_margin"] += 2
    tiers["WR"]["elite_swap_margin"] += 2
    assert recommender.recommend(base_roster(), [fa]).moves  # shipped margin: moves
    monkeypatch.setattr(config, "TIERS", tiers)
    assert recommender.recommend(base_roster(), [fa]).moves == []
