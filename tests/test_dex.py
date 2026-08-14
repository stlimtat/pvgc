import pytest

from pvgc import dex


def test_dex_loads_megas():
    d = dex.load()
    megas = [s for s in d.species.values() if s["isMega"]]
    assert len(megas) > 0


def test_known_species_present():
    d = dex.load()
    assert "garchomp" in d.species
    assert d.species["garchomp"]["types"] == ["Dragon", "Ground"]


def test_learnset_includes_prevo_moves():
    """Garchomp learns Dragon Rush only via Gible, so a learnset built from
    the species' own data alone would wrongly reject it."""
    d = dex.load()
    ls = d.learnset("garchomp")
    assert "earthquake" in ls
    assert "dragonrush" in ls


def test_unknown_species_raises():
    d = dex.load()
    with pytest.raises(KeyError):
        d.learnset("notarealpokemon")


def test_mega_stones_present():
    d = dex.load()
    stones = [i for i in d.items.values() if i["megaStone"]]
    assert len(stones) > 0


def test_illegal_mainline_items_absent():
    """Champions has a restricted item pool — these mainline staples are out."""
    d = dex.load()
    for item in ("choiceband", "assaultvest", "rockyhelmet"):
        assert item not in d.items


def test_to_id_normalisation():
    assert dex.to_id("Choice Scarf") == "choicescarf"
    assert dex.to_id("Charizard-Mega-Y") == "charizardmegay"
    assert dex.to_id("Farfetch'd") == "farfetchd"
