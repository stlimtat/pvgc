"""THE SEAM.

Everything above this module sees only the dataclasses defined here. No
poke-env type crosses this boundary, so the engine below — or the policy
driving it — can be replaced without touching the search layer.
"""
import math
from dataclasses import dataclass, field

Z = 1.96  # 95%


def wilson_interval(wins: int, n: int, z: float = Z) -> tuple[float, float]:
    """Wilson score interval.

    Correct near 0 and 1, unlike the normal approximation, which matters for
    lopsided matchups where the naive interval runs outside [0, 1].
    """
    if n == 0:
        return (0.0, 1.0)
    p = wins / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


@dataclass(frozen=True)
class MatchupResult:
    opponent_id: int
    n: int
    wins: int
    losses: int
    failed: int = 0
    brings: list[tuple[int, ...]] = field(default_factory=list)

    def __post_init__(self):
        if self.wins + self.losses != self.n:
            raise ValueError(
                f"n={self.n} but wins+losses={self.wins + self.losses}. "
                "n is derived from recorded battles, never the requested count."
            )

    @property
    def winrate(self) -> float:
        return self.wins / self.n if self.n else 0.0

    @property
    def interval(self) -> tuple[float, float]:
        return wilson_interval(self.wins, self.n)

    def bring_distribution(self) -> dict[tuple[int, ...], int]:
        counts: dict[tuple[int, ...], int] = {}
        for b in self.brings:
            counts[b] = counts.get(b, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


@dataclass(frozen=True)
class GauntletResult:
    candidate_hash: str
    matchups: list[MatchupResult]
    holdout_ids: set[int]

    @staticmethod
    def _pool(matchups) -> float:
        n = sum(m.n for m in matchups)
        return sum(m.wins for m in matchups) / n if n else 0.0

    @property
    def train(self) -> list[MatchupResult]:
        return [m for m in self.matchups if m.opponent_id not in self.holdout_ids]

    @property
    def holdout(self) -> list[MatchupResult]:
        return [m for m in self.matchups if m.opponent_id in self.holdout_ids]

    @property
    def overall_winrate(self) -> float:
        return self._pool(self.matchups)

    @property
    def train_winrate(self) -> float:
        return self._pool(self.train)

    @property
    def holdout_winrate(self) -> float:
        return self._pool(self.holdout)

    @property
    def overfit_gap(self) -> float:
        """train - holdout. A large positive gap means the search is fitting
        the gauntlet rather than finding a good team."""
        return self.train_winrate - self.holdout_winrate

    @property
    def overall_interval(self) -> tuple[float, float]:
        return wilson_interval(
            sum(m.wins for m in self.matchups), sum(m.n for m in self.matchups)
        )

    @property
    def total_failed(self) -> int:
        return sum(m.failed for m in self.matchups)
