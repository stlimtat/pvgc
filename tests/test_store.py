import pytest

from pvgc.store import Store
from pvgc.team import Mon, Team


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "test.sqlite3")


def _team(tag: str, n: int = 6) -> Team:
    return Team(mons=[
        Mon(species=f"{tag}{i}", item=f"Item{tag}{i}", ability="A", nature="Jolly",
            evs={"spe": 32}, moves=["Tackle"])
        for i in range(n)
    ])


@pytest.fixture
def team():
    return _team("c")


def test_add_team_is_idempotent(store, team):
    a = store.add_team(team, role="candidate", source="test")
    b = store.add_team(team, role="candidate", source="test")
    assert a == b


def test_add_team_distinguishes_different_teams(store, team):
    a = store.add_team(team, role="candidate", source="test")
    b = store.add_team(_team("g"), role="gauntlet", source="test")
    assert a != b


def test_run_and_matchup_roundtrip(store, team):
    tid = store.add_team(team, role="candidate", source="test")
    oid = store.add_team(_team("g"), role="gauntlet", source="test")
    run_id = store.start_run(gauntlet_hash="abc", n_battles=10, from_cache=False)
    mid = store.add_matchup(run_id, tid, oid, n=10, wins=7, losses=3)
    rows = store.matchups_for(run_id, tid)
    assert len(rows) == 1
    assert rows[0]["wins"] == 7
    assert rows[0]["n"] == 10
    assert mid > 0


def test_matchup_rejects_inconsistent_counts(store, team):
    tid = store.add_team(team, role="candidate", source="test")
    run_id = store.start_run(gauntlet_hash="abc", n_battles=10, from_cache=False)
    with pytest.raises(ValueError):
        store.add_matchup(run_id, tid, tid, n=10, wins=7, losses=2)


def test_matchup_accepts_failed_battles_outside_n(store, team):
    """failed battles are recorded but excluded from n."""
    tid = store.add_team(team, role="candidate", source="test")
    run_id = store.start_run(gauntlet_hash="abc", n_battles=10, from_cache=False)
    mid = store.add_matchup(run_id, tid, tid, n=8, wins=5, losses=3, failed=2)
    assert store.matchups_for(run_id, tid)[0]["failed"] == 2
    assert mid > 0


def test_battle_rows_recorded(store, team):
    tid = store.add_team(team, role="candidate", source="test")
    run_id = store.start_run(gauntlet_hash="abc", n_battles=1, from_cache=False)
    mid = store.add_matchup(run_id, tid, tid, n=1, wins=1, losses=0)
    store.add_battle(mid, seed=42, winner="a", turns=12,
                     bring_a=[0, 1, 2, 3], bring_b=[1, 2, 3, 4])
    battles = store.battles_for(mid)
    assert len(battles) == 1
    assert battles[0]["bring_a"] == [0, 1, 2, 3]
    assert battles[0]["turns"] == 12


def test_run_records_stats_provenance(store):
    run_id = store.start_run(gauntlet_hash="abc", n_battles=5, from_cache=True)
    run = store.get_run(run_id)
    assert run["stats_from_cache"] == 1
    assert run["format_id"] == "gen9championsvgc2026regmb"
    assert run["stats_month"] == "2026-07"


def test_team_paste_roundtrip(store, team):
    tid = store.add_team(team, role="candidate", source="test")
    assert Team.from_paste(store.team_paste(tid)) == team


def test_scored_candidates_returns_ranked_rows(store, team):
    tid = store.add_team(team, role="candidate", source="llm",
                         meta={"hypothesis": "Sand beats rain"})
    oid = store.add_team(_team("g"), role="gauntlet", source="usage")
    run_id = store.start_run(gauntlet_hash="abc", n_battles=10, from_cache=True)
    store.add_matchup(run_id, tid, oid, n=10, wins=7, losses=3)

    rows = store.scored_candidates(run_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["hash"] == team.hash()
    assert row["n"] == 10
    assert row["overall"] == pytest.approx(0.7)
    assert row["lo"] < 0.7 < row["hi"]
    assert row["hypothesis"] == "Sand beats rain"


def test_scored_candidates_ranks_by_winrate(store):
    a, b = _team("a"), _team("b")
    aid = store.add_team(a, role="candidate", source="llm")
    bid = store.add_team(b, role="candidate", source="llm")
    oid = store.add_team(_team("g"), role="gauntlet", source="usage")
    run_id = store.start_run(gauntlet_hash="abc", n_battles=10, from_cache=True)
    store.add_matchup(run_id, aid, oid, n=10, wins=2, losses=8)
    store.add_matchup(run_id, bid, oid, n=10, wins=9, losses=1)

    rows = store.scored_candidates(run_id)
    assert [r["hash"] for r in rows] == [b.hash(), a.hash()]


def test_scored_candidates_pools_across_matchups(store, team):
    """Overall must pool battles, matching GauntletResult.overall_winrate."""
    tid = store.add_team(team, role="candidate", source="llm")
    o1 = store.add_team(_team("g"), role="gauntlet", source="usage")
    o2 = store.add_team(_team("h"), role="gauntlet", source="usage")
    run_id = store.start_run(gauntlet_hash="abc", n_battles=10, from_cache=True)
    store.add_matchup(run_id, tid, o1, n=10, wins=10, losses=0)
    store.add_matchup(run_id, tid, o2, n=90, wins=0, losses=90)
    assert store.scored_candidates(run_id)[0]["overall"] == pytest.approx(0.1)


def test_scored_candidates_empty_run(store):
    run_id = store.start_run(gauntlet_hash="abc", n_battles=10, from_cache=True)
    assert store.scored_candidates(run_id) == []


def test_scored_candidates_excludes_holdout_opponents(store, team):
    """The proposer must see train results only — a holdout matchup leaking
    into its feedback defeats the point of the split."""
    tid = store.add_team(team, role="candidate", source="llm")
    train_op = store.add_team(_team("g"), role="gauntlet", source="usage")
    hold_op = store.add_team(_team("h"), role="gauntlet", source="usage")
    run_id = store.start_run(gauntlet_hash="abc", n_battles=10, from_cache=True)
    store.add_matchup(run_id, tid, train_op, n=10, wins=8, losses=2)
    store.add_matchup(run_id, tid, hold_op, n=10, wins=0, losses=10)

    both = store.scored_candidates(run_id)[0]
    assert both["overall"] == pytest.approx(0.4)

    train_only = store.scored_candidates(run_id, holdout_team_ids={hold_op})[0]
    assert train_only["overall"] == pytest.approx(0.8)
    assert train_only["n"] == 10


def test_scored_candidates_drops_candidates_with_only_holdout_matchups(store, team):
    tid = store.add_team(team, role="candidate", source="llm")
    hold_op = store.add_team(_team("h"), role="gauntlet", source="usage")
    run_id = store.start_run(gauntlet_hash="abc", n_battles=10, from_cache=True)
    store.add_matchup(run_id, tid, hold_op, n=10, wins=5, losses=5)
    assert store.scored_candidates(run_id, holdout_team_ids={hold_op}) == []


def test_scored_candidates_reports_no_fabricated_holdout(store, team):
    """holdout must not be reported at all — a 0.0 placeholder ends up in
    the prompt as a fact."""
    tid = store.add_team(team, role="candidate", source="llm")
    oid = store.add_team(_team("g"), role="gauntlet", source="usage")
    run_id = store.start_run(gauntlet_hash="abc", n_battles=10, from_cache=True)
    store.add_matchup(run_id, tid, oid, n=10, wins=7, losses=3)
    row = store.scored_candidates(run_id)[0]
    assert "holdout" not in row
    assert "train" not in row
