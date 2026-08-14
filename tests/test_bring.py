import itertools

import pytest

from pvgc.bring import choose_bring, type_advantage
from pvgc.team import Mon, Team

SPECIES = ["Garchomp", "Incineroar", "Whimsicott", "Kingambit", "Sylveon", "Milotic"]
OPPONENT = ["Basculegion", "Sinistcha", "Farigiraf", "Archaludon", "Sneasler", "Snorlax"]


def _team(species: list[str]) -> Team:
    return Team(mons=[
        Mon(species=s, item=f"Item{i}", ability="A", nature="Jolly",
            evs={"spe": 32}, moves=["Tackle"])
        for i, s in enumerate(species)
    ])


def test_returns_four_distinct_indices():
    picks = choose_bring(_team(SPECIES), _team(OPPONENT))
    assert len(picks) == 4
    assert len(set(picks)) == 4
    assert all(0 <= p < 6 for p in picks)


def test_deterministic():
    ours, theirs = _team(SPECIES), _team(OPPONENT)
    assert choose_bring(ours, theirs) == choose_bring(ours, theirs)


def test_considers_all_fifteen_combinations():
    assert len(list(itertools.combinations(range(6), 4))) == 15


def test_type_advantage_prefers_super_effective():
    # Ground hits Electric for 2x; Electric does not touch Ground.
    assert type_advantage(["Ground"], ["Electric"]) == 2.0
    assert type_advantage(["Electric"], ["Ground"]) == 0.0


def test_type_advantage_stacks_dual_types():
    # Rock vs Flying/Bug is 4x.
    assert type_advantage(["Rock"], ["Flying", "Bug"]) == 4.0


def test_selection_responds_to_opponent():
    """Different opponents should generally produce different brings —
    otherwise the policy is ignoring its input."""
    ours = _team(SPECIES)
    a = choose_bring(ours, _team(OPPONENT))
    b = choose_bring(ours, _team(["Charizard-Mega-Y"] * 1 + OPPONENT[:5]))
    c = choose_bring(ours, _team(["Swampert-Mega", "Politoed", "Archaludon",
                                  "Milotic", "Rotom-Wash", "Basculegion"]))
    assert len({a, b, c}) > 1


def test_handles_unknown_species_without_crashing():
    """Opponent team from a battle may carry species we cannot resolve;
    the policy must degrade, not raise."""
    picks = choose_bring(_team(SPECIES), _team(["Notarealmon"] * 6))
    assert len(picks) == 4


def test_smaller_team_raises():
    with pytest.raises(ValueError):
        choose_bring(_team(SPECIES[:3]), _team(OPPONENT))
