import json
from pathlib import Path

import pytest

from pvgc.config import MAX_STAT_POINTS_TOTAL
from pvgc.usage import Usage, spread_to_stat_points

FIXTURE = Path(__file__).parent / "fixtures" / "chaos_sample.json"


@pytest.fixture
def usage():
    return Usage(json.loads(FIXTURE.read_text()))


def test_species_sorted_by_usage(usage):
    names = usage.top_species(5)
    assert len(names) == 5
    rates = [usage.usage_rate(n) for n in names]
    assert rates == sorted(rates, reverse=True)


def test_distributions_are_normalised(usage):
    top = usage.top_species(1)[0]
    for dist in (usage.items(top), usage.abilities(top), usage.moves(top)):
        assert dist
        assert abs(sum(dist.values()) - 1.0) < 1e-6


def test_teammates_excludes_self(usage):
    for name in usage.top_species(5):
        assert name not in usage.teammates(name)


def test_teammates_normalised(usage):
    top = usage.top_species(1)[0]
    mates = usage.teammates(top)
    assert mates
    assert abs(sum(mates.values()) - 1.0) < 1e-6


def test_spread_parsing(usage):
    top = usage.top_species(1)[0]
    (nature, points), weight = next(iter(usage.spreads(top).items()))
    assert isinstance(nature, str)
    assert len(points) == 6
    assert weight > 0


def test_all_spreads_respect_champions_stat_point_limit(usage):
    """Champions caps total stat points at 66. If real usage data contained
    mainline-sized spreads, our M0 finding would be wrong."""
    for name in usage.top_species(12):
        for (_, points) in usage.spreads(name):
            assert sum(points) <= MAX_STAT_POINTS_TOTAL
            assert max(points) <= 32


def test_spread_to_stat_points_drops_zeroes():
    assert spread_to_stat_points((2, 32, 0, 0, 0, 32)) == {
        "hp": 2, "atk": 32, "spe": 32
    }


def test_unknown_species_raises(usage):
    with pytest.raises(KeyError):
        usage.items("Notarealmon")
