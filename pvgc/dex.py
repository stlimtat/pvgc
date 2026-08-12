"""Champions dex data, loaded from the JSON produced by scripts/gen_champions_data.py.

Only standard (non-`isNonstandard`) entries are extracted, so membership in
these tables IS Reg M-B legality for species, moves, items, and abilities.
"""
import json
from dataclasses import dataclass
from functools import lru_cache

from pvgc.config import CHAMPIONS_DEX


def to_id(s: str) -> str:
    """Showdown's id normalisation: lowercase alphanumerics only."""
    return "".join(c for c in s.lower() if c.isalnum())


@dataclass(frozen=True)
class Dex:
    species: dict
    moves: dict
    learnsets: dict
    items: dict
    abilities: dict
    natures: dict

    def learnset(self, species: str) -> set[str]:
        """Move ids this species may legally know, including moves inherited
        from its pre-evolutions."""
        return set(self.learnsets[to_id(species)])

    def is_mega(self, species: str) -> bool:
        return self.species[to_id(species)]["isMega"]

    def mega_forms(self) -> list[dict]:
        return [s for s in self.species.values() if s["isMega"]]

    def types(self, species: str) -> list[str]:
        return self.species[to_id(species)]["types"]

    def base_stats(self, species: str) -> dict[str, int]:
        return self.species[to_id(species)]["baseStats"]


@lru_cache(maxsize=1)
def load() -> Dex:
    if not CHAMPIONS_DEX.exists():
        raise FileNotFoundError(
            f"{CHAMPIONS_DEX} missing. Run: python scripts/gen_champions_data.py"
        )
    raw = json.loads(CHAMPIONS_DEX.read_text())
    return Dex(
        species=raw["species"],
        moves=raw["moves"],
        learnsets=raw["learnsets"],
        items=raw["items"],
        abilities=raw["abilities"],
        natures=raw["natures"],
    )
