"""Validator tests.

The fast tests pin each rejection rule. The slow test cross-checks our
validator against Showdown's own TeamValidator, which is the only real
oracle for legality — our validator existing at all is just to keep
illegal teams from reaching the simulator, where rejection is silent.
"""
import copy
import subprocess

import pytest

from pvgc.config import ROOT, SHOWDOWN_DIR
from pvgc.dex import load, to_id
from pvgc.team import Team

SPIKE = ROOT / "tests" / "fixtures" / "spike_team.txt"


@pytest.fixture
def legal_team() -> Team:
    """The team M0 built and Showdown validated as LEGAL."""
    return Team.from_paste(SPIKE.read_text())


def test_legal_team_passes(legal_team):
    assert legal_team.validate() == []


def test_rejects_duplicate_item(legal_team):
    t = copy.deepcopy(legal_team)
    t.mons[1].item = t.mons[0].item
    assert any("item clause" in e.lower() for e in t.validate())


def test_rejects_duplicate_species(legal_team):
    t = copy.deepcopy(legal_team)
    t.mons[1].species = t.mons[0].species
    assert any("species clause" in e.lower() for e in t.validate())


def test_rejects_stat_point_total_overflow(legal_team):
    t = copy.deepcopy(legal_team)
    t.mons[0].evs = {"hp": 32, "atk": 32, "spe": 32}  # 96 > 66
    assert any("stat point total" in e.lower() for e in t.validate())


def test_rejects_per_stat_overflow(legal_team):
    t = copy.deepcopy(legal_team)
    t.mons[0].evs = {"hp": 33}  # > 32
    assert any("per-stat" in e.lower() for e in t.validate())


def test_mainline_ev_spread_is_rejected(legal_team):
    """A spread that would be legal in mainline VGC must fail here."""
    t = copy.deepcopy(legal_team)
    t.mons[0].evs = {"hp": 4, "atk": 252, "spe": 252}
    errors = t.validate()
    assert any("stat point total" in e.lower() for e in errors)
    assert any("per-stat" in e.lower() for e in errors)


def test_rejects_nonexistent_move(legal_team):
    t = copy.deepcopy(legal_team)
    t.mons[0].moves = ["Blast Burn Supreme"]
    assert any("move pool" in e.lower() for e in t.validate())


def test_rejects_unlearnable_move(legal_team):
    t = copy.deepcopy(legal_team)
    dex = load()
    sid = to_id(t.mons[0].species)
    learnset = set(dex.learnsets[sid])
    unlearnable = next(m for m in dex.moves if m not in learnset)
    t.mons[0].moves = [dex.moves[unlearnable]["name"]]
    assert any("cannot learn" in e.lower() for e in t.validate())


def test_rejects_unknown_species(legal_team):
    t = copy.deepcopy(legal_team)
    t.mons[0].species = "Notarealmon"
    assert any("not in reg m-b dex" in e.lower() for e in t.validate())


def test_rejects_item_outside_champions_pool(legal_team):
    """Assault Vest is legal in mainline VGC and absent from Champions."""
    t = copy.deepcopy(legal_team)
    t.mons[0].item = "Assault Vest"
    assert any("item pool" in e.lower() for e in t.validate())


def test_rejects_wrong_team_size(legal_team):
    t = Team(mons=legal_team.mons[:5])
    assert any("team size" in e.lower() for e in t.validate())


def test_rejects_too_many_moves(legal_team):
    t = copy.deepcopy(legal_team)
    t.mons[0].moves = t.mons[0].moves + ["Protect", "Substitute"]
    assert any("expected 1-4" in e.lower() for e in t.validate())


def test_rejects_duplicate_moves(legal_team):
    t = copy.deepcopy(legal_team)
    t.mons[0].moves = [t.mons[0].moves[0]] * 2
    assert any("duplicate moves" in e.lower() for e in t.validate())


def test_rejects_wrong_level(legal_team):
    t = copy.deepcopy(legal_team)
    t.mons[0].level = 100
    assert any("level" in e.lower() for e in t.validate())


# --- Oracle cross-check -------------------------------------------------


def _showdown_validate(paste: str) -> str:
    script = ROOT / "scripts" / "validate_team.js"
    dest = SHOWDOWN_DIR / "validate_team.js"
    dest.write_text(script.read_text())
    try:
        proc = subprocess.run(
            ["node", "validate_team.js"], cwd=SHOWDOWN_DIR, input=paste,
            capture_output=True, text=True, timeout=120,
        )
    finally:
        dest.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)
    return proc.stdout.strip()


@pytest.mark.slow
def test_agrees_with_showdown_on_legal_team(legal_team):
    assert _showdown_validate(legal_team.to_paste()) == "LEGAL"
    assert legal_team.validate() == []


@pytest.mark.slow
@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda t: setattr(t.mons[1], "item", t.mons[0].item), id="dup-item"),
        pytest.param(lambda t: setattr(t.mons[0], "evs", {"hp": 4, "atk": 252, "spe": 252}), id="mainline-evs"),
        pytest.param(lambda t: setattr(t.mons[0], "item", "Assault Vest"), id="illegal-item"),
        pytest.param(lambda t: setattr(t.mons[1], "species", t.mons[0].species), id="dup-species"),
    ],
)
def test_agrees_with_showdown_on_illegal_teams(legal_team, mutate):
    """Both validators must reject. Ours may word it differently, but a team
    we accept and Showdown rejects is the silent-corruption bug."""
    t = copy.deepcopy(legal_team)
    mutate(t)
    ours = t.validate()
    theirs = _showdown_validate(t.to_paste())
    assert theirs != "LEGAL", "Showdown accepted it; test premise is wrong"
    assert ours, f"we accepted a team Showdown rejects:\n{theirs}"
