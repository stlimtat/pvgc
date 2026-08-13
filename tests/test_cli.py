import pytest

from pvgc.cli import _holdout_ids, _label
from pvgc.config import HOLDOUT_SIZE
from pvgc.team import Mon, Team


def test_holdout_is_the_tail():
    assert _holdout_ids(16) == {12, 13, 14, 15}


def test_holdout_rejects_all_holdout_gauntlet():
    """A gauntlet with no train split reports a meaningless 0.0% train rate
    and a nonsense overfit gap, so it must fail loudly instead."""
    with pytest.raises(SystemExit):
        _holdout_ids(HOLDOUT_SIZE)
    with pytest.raises(SystemExit):
        _holdout_ids(HOLDOUT_SIZE - 1)


def test_holdout_accepts_minimum_valid_size():
    ids = _holdout_ids(HOLDOUT_SIZE + 1)
    assert len(ids) == HOLDOUT_SIZE
    assert 0 not in ids


def test_label_truncates():
    team = Team(mons=[
        Mon(species=f"Mon{i}", item="", ability="", nature="", evs={}, moves=[])
        for i in range(6)
    ])
    assert _label(team) == "Mon0, Mon1, Mon2"
