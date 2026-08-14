import json
from pathlib import Path

import pytest

from pvgc.teamgen import generate_gauntlet, generate_team
from pvgc.usage import Usage

FIXTURE = Path(__file__).parent / "fixtures" / "chaos_sample.json"


@pytest.fixture
def usage():
    return Usage(json.loads(FIXTURE.read_text()))


def test_team_has_six_distinct_species(usage):
    team = generate_team(usage, seed=1)
    assert len(team.mons) == 6
    assert len({m.species for m in team.mons}) == 6


def test_items_are_distinct(usage):
    team = generate_team(usage, seed=2)
    items = [m.item for m in team.mons if m.item]
    assert len(set(items)) == len(items)


def test_deterministic_for_same_seed(usage):
    assert generate_team(usage, seed=7) == generate_team(usage, seed=7)


def test_different_seeds_differ(usage):
    hashes = {generate_team(usage, seed=s).hash() for s in range(8)}
    assert len(hashes) > 1


def test_generated_teams_are_legal(usage):
    """The highest-value test here: it wires the generator to the validator,
    so a generator bug surfaces as a failure rather than a corrupted gauntlet."""
    for seed in range(10):
        team = generate_team(usage, seed=seed)
        assert team.validate() == [], f"seed {seed}: {team.validate()}"


def test_gauntlet_size_and_uniqueness(usage):
    teams = generate_gauntlet(usage, n=6, seed=0)
    assert len(teams) == 6
    assert len({t.hash() for t in teams}) == 6


def test_gauntlet_teams_all_legal(usage):
    for team in generate_gauntlet(usage, n=6, seed=0):
        assert team.validate() == []


def test_uses_display_names_not_showdown_ids(usage):
    team = generate_team(usage, seed=3)
    for mon in team.mons:
        assert mon.moves == [m.strip() for m in mon.moves]
        # Display names are capitalised; Showdown ids are lowercase-alnum.
        assert any(c.isupper() for c in mon.moves[0])
        if mon.ability:
            assert any(c.isupper() for c in mon.ability)


@pytest.mark.slow
def test_generated_teams_pass_showdown_oracle():
    """Our validator agreeing with itself proves nothing. Generated teams must
    survive Showdown's own TeamValidator, which is what the simulator uses."""
    from tests.test_validate import _showdown_validate

    from pvgc.usage import fetch

    real_usage, _ = fetch()
    for team in generate_gauntlet(real_usage, n=8, seed=0):
        assert team.validate() == []
        assert _showdown_validate(team.to_paste()) == "LEGAL", team.to_paste()


def test_stat_points_within_champions_limits(usage):
    from pvgc.config import MAX_STAT_POINTS_PER_STAT, MAX_STAT_POINTS_TOTAL

    for seed in range(5):
        for mon in generate_team(usage, seed=seed).mons:
            assert sum(mon.evs.values()) <= MAX_STAT_POINTS_TOTAL
            assert max(mon.evs.values(), default=0) <= MAX_STAT_POINTS_PER_STAT
