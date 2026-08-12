from pvgc.team import Mon, Team

# Champions uses Stat Points (66 total, 32 per stat), not mainline EVs.
PASTE = """Garchomp @ Life Orb
Ability: Rough Skin
Level: 50
EVs: 2 HP / 32 Atk / 32 Spe
Jolly Nature
- Earthquake
- Dragon Claw
- Protect
- Rock Slide

Charizard @ Charizardite Y
Ability: Blaze
Level: 50
EVs: 2 HP / 32 SpA / 32 Spe
Timid Nature
- Heat Wave
- Air Slash
- Protect
- Solar Beam
"""


def test_parse_basic():
    team = Team.from_paste(PASTE)
    assert len(team.mons) == 2
    chomp = team.mons[0]
    assert chomp.species == "Garchomp"
    assert chomp.item == "Life Orb"
    assert chomp.ability == "Rough Skin"
    assert chomp.nature == "Jolly"
    assert chomp.level == 50
    assert chomp.evs == {"hp": 2, "atk": 32, "spe": 32}
    assert chomp.moves == ["Earthquake", "Dragon Claw", "Protect", "Rock Slide"]


def test_parse_second_mon_spa():
    team = Team.from_paste(PASTE)
    assert team.mons[1].evs == {"hp": 2, "spa": 32, "spe": 32}
    assert team.mons[1].item == "Charizardite Y"


def test_roundtrip():
    team = Team.from_paste(PASTE)
    assert Team.from_paste(team.to_paste()) == team


def test_roundtrip_is_byte_stable():
    once = Team.from_paste(PASTE).to_paste()
    twice = Team.from_paste(once).to_paste()
    assert once == twice


def test_hash_is_order_independent():
    team = Team.from_paste(PASTE)
    reversed_team = Team(mons=list(reversed(team.mons)))
    assert team.hash() == reversed_team.hash()


def test_hash_changes_with_item():
    team = Team.from_paste(PASTE)
    other = Team.from_paste(PASTE)
    other.mons[0].item = "Choice Scarf"
    assert team.hash() != other.hash()


def test_hash_changes_with_spread():
    team = Team.from_paste(PASTE)
    other = Team.from_paste(PASTE)
    other.mons[0].evs = {"hp": 32, "atk": 32, "spe": 2}
    assert team.hash() != other.hash()


def test_mon_without_item_parses():
    team = Team.from_paste("Garchomp\nAbility: Rough Skin\nLevel: 50\n- Earthquake\n")
    assert team.mons[0].species == "Garchomp"
    assert team.mons[0].item == ""


def test_parses_real_spike_team():
    from pvgc.config import ROOT

    paste = (ROOT / "tests" / "fixtures" / "spike_team.txt").read_text()
    team = Team.from_paste(paste)
    assert len(team.mons) == 6
    assert all(m.moves for m in team.mons)
    assert len({m.item for m in team.mons}) == 6
