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
