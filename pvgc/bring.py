"""Bring-4-of-6 selection.

ponytail: crude type-and-speed heuristic scored over all 15 combinations.
This is the weakest link in fitness validity, so it lives in its own file
and can be replaced by a learned preview policy without touching sim.py.

Reg M-B includes Open Team Sheets, so reading the opponent's full team here
is legal information, not cheating.

Without an explicit policy the base heuristic player brings the first four
mons, which collapses the 6-mon design space to 4 and makes every result
meaningless — that is why this exists at all.
"""
import itertools
from functools import lru_cache

from pvgc.config import BRING_SIZE
from pvgc.dex import load, to_id
from pvgc.team import Team

NEUTRAL_TYPES = ["Normal"]


@lru_cache(maxsize=1)
def _typechart() -> dict:
    """poke-env's chart is keyed [DEFENDER][ATTACKER] -> multiplier."""
    from poke_env.data import GenData

    from pvgc.config import FORMAT_ID

    return GenData.from_format(FORMAT_ID).type_chart


def type_advantage(attacker_types: list[str], defender_types: list[str]) -> float:
    """Best multiplier the attacker's STAB types achieve into the defender."""
    chart = _typechart()
    best = 0.0
    for atk in attacker_types:
        mult = 1.0
        for dfn in defender_types:
            mult *= chart.get(dfn.upper(), {}).get(atk.upper(), 1.0)
        best = max(best, mult)
    return best


def _types(species: str) -> list[str]:
    dex = load()
    sid = to_id(species)
    if sid not in dex.species:
        return NEUTRAL_TYPES
    return dex.species[sid]["types"]


def _speed(species: str) -> int:
    dex = load()
    sid = to_id(species)
    if sid not in dex.species:
        return 0
    return dex.species[sid]["baseStats"]["spe"]


def _mon_score(species: str, opponent: Team) -> float:
    mine = _types(species)
    offence = sum(type_advantage(mine, _types(o.species)) for o in opponent.mons)
    defence = sum(type_advantage(_types(o.species), mine) for o in opponent.mons)
    # Speed normalised to roughly the same scale as one type multiplier.
    return offence - defence + _speed(species) / 200.0


def choose_bring(ours: Team, theirs: Team) -> tuple[int, ...]:
    """Indices into ours.mons. Deterministic: ties break toward lower indices."""
    if len(ours.mons) < BRING_SIZE:
        raise ValueError(
            f"cannot bring {BRING_SIZE} from a team of {len(ours.mons)}"
        )
    scores = [_mon_score(m.species, theirs) for m in ours.mons]
    return max(
        itertools.combinations(range(len(ours.mons)), BRING_SIZE),
        key=lambda combo: (sum(scores[i] for i in combo), tuple(-i for i in combo)),
    )
