import pytest

from pvgc.score import GauntletResult, MatchupResult, wilson_interval


def test_wilson_midpoint_near_ratio():
    lo, hi = wilson_interval(50, 100)
    assert lo < 0.5 < hi
    assert 0.34 < lo < 0.42
    assert 0.58 < hi < 0.66


def test_wilson_zero_battles_is_full_range():
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_wilson_narrows_with_more_battles():
    narrow = wilson_interval(500, 1000)
    wide = wilson_interval(5, 10)
    assert (narrow[1] - narrow[0]) < (wide[1] - wide[0])


def test_wilson_stays_in_bounds_at_extremes():
    """The normal approximation goes out of range near 0 and 1; Wilson must not."""
    for wins, n in [(0, 20), (20, 20), (1, 200), (199, 200)]:
        lo, hi = wilson_interval(wins, n)
        assert 0.0 <= lo <= hi <= 1.0


def test_matchup_winrate_excludes_failed():
    m = MatchupResult(opponent_id=1, n=8, wins=6, losses=2, failed=2)
    assert m.winrate == 0.75
    assert m.n == 8


def test_matchup_rejects_bad_counts():
    with pytest.raises(ValueError):
        MatchupResult(opponent_id=1, n=10, wins=6, losses=2)


def test_matchup_zero_battles_is_zero_winrate():
    m = MatchupResult(opponent_id=1, n=0, wins=0, losses=0, failed=5)
    assert m.winrate == 0.0
    assert m.interval == (0.0, 1.0)


def test_bring_distribution_counts_and_sorts():
    m = MatchupResult(
        opponent_id=1, n=3, wins=3, losses=0,
        brings=[(0, 1, 2, 3), (0, 1, 2, 4), (0, 1, 2, 3)],
    )
    dist = m.bring_distribution()
    assert list(dist)[0] == (0, 1, 2, 3)
    assert dist[(0, 1, 2, 3)] == 2


def test_gauntlet_splits_train_and_holdout():
    matchups = [
        MatchupResult(opponent_id=i, n=10, wins=w, losses=10 - w)
        for i, w in enumerate([8, 8, 8, 8, 2, 2])
    ]
    result = GauntletResult(candidate_hash="x", matchups=matchups, holdout_ids={4, 5})
    assert result.train_winrate == pytest.approx(0.8)
    assert result.holdout_winrate == pytest.approx(0.2)
    assert result.overfit_gap == pytest.approx(0.6)


def test_gauntlet_overall_pools_battles():
    """Overall must pool battles, not average matchup rates — a 10-battle
    matchup should not weigh the same as a 90-battle one."""
    matchups = [
        MatchupResult(opponent_id=0, n=10, wins=10, losses=0),
        MatchupResult(opponent_id=1, n=90, wins=0, losses=90),
    ]
    result = GauntletResult(candidate_hash="x", matchups=matchups, holdout_ids=set())
    assert result.overall_winrate == pytest.approx(0.1)


def test_gauntlet_totals_failed():
    matchups = [
        MatchupResult(opponent_id=0, n=5, wins=3, losses=2, failed=1),
        MatchupResult(opponent_id=1, n=5, wins=2, losses=3, failed=4),
    ]
    result = GauntletResult(candidate_hash="x", matchups=matchups, holdout_ids=set())
    assert result.total_failed == 5


def test_gauntlet_empty_holdout_is_zero_not_error():
    matchups = [MatchupResult(opponent_id=0, n=4, wins=2, losses=2)]
    result = GauntletResult(candidate_hash="x", matchups=matchups, holdout_ids=set())
    assert result.holdout_winrate == 0.0
    assert result.holdout == []
