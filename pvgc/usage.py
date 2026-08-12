"""Smogon chaos-JSON usage statistics.

Chaos format:
    {"info": {...}, "data": {"<Species>": {
        "usage": float, "Abilities": {...}, "Items": {...},
        "Spreads": {"<Nature>:<hp>/<atk>/<def>/<spa>/<spd>/<spe>": weight},
        "Moves": {...}, "Teammates": {...}, "Checks and Counters": {...}}}}

Weights are raw counts, not normalised. Teammate weights may be negative
(chaos encodes them as a correlation delta), so they are clamped.

Spreads are Champions stat points (66 total, 32 per stat), not mainline EVs.
"""
import json
import urllib.request
from dataclasses import dataclass

from pvgc.config import (
    MAX_STAT_POINTS_PER_STAT,
    MAX_STAT_POINTS_TOTAL,
    STATS_CUTOFF,
    STATS_DIR,
    STATS_MONTH,
    STATS_URL,
)

STAT_ORDER = ["hp", "atk", "def", "spa", "spd", "spe"]


def _normalise(d: dict) -> dict:
    total = sum(v for v in d.values() if v > 0)
    if total <= 0:
        return {}
    return {k: v / total for k, v in d.items() if v > 0}


def spread_to_stat_points(points: tuple[int, ...]) -> dict[str, int]:
    return {k: v for k, v in zip(STAT_ORDER, points) if v}


@dataclass
class Usage:
    raw: dict

    @property
    def data(self) -> dict:
        return self.raw["data"]

    @property
    def battle_count(self) -> int:
        return self.raw.get("info", {}).get("number of battles", 0)

    def top_species(self, n: int) -> list[str]:
        ranked = sorted(self.data.items(), key=lambda kv: -kv[1]["usage"])
        return [name for name, _ in ranked[:n]]

    def usage_rate(self, species: str) -> float:
        return self.data[species]["usage"]

    def items(self, species: str) -> dict[str, float]:
        return _normalise(self.data[species]["Items"])

    def abilities(self, species: str) -> dict[str, float]:
        return _normalise(self.data[species]["Abilities"])

    def moves(self, species: str) -> dict[str, float]:
        # The empty-string key is chaos's "no move in this slot".
        return _normalise({k: v for k, v in self.data[species]["Moves"].items() if k})

    def teammates(self, species: str) -> dict[str, float]:
        mates = {
            k: v
            for k, v in self.data[species]["Teammates"].items()
            if k != species and v > 0 and k in self.data
        }
        return _normalise(mates)

    def spreads(self, species: str) -> dict[tuple[str, tuple[int, ...]], float]:
        """Keyed by (nature, (hp, atk, def, spa, spd, spe)).

        Spreads violating the Champions stat-point limits are dropped rather
        than trusted — a malformed spread would produce a team the simulator
        silently rejects.
        """
        out: dict[tuple[str, tuple[int, ...]], float] = {}
        for key, weight in self.data[species]["Spreads"].items():
            if weight <= 0 or ":" not in key:
                continue
            nature, point_str = key.split(":", 1)
            try:
                points = tuple(int(x) for x in point_str.split("/"))
            except ValueError:
                continue
            if len(points) != 6:
                continue
            if sum(points) > MAX_STAT_POINTS_TOTAL:
                continue
            if max(points) > MAX_STAT_POINTS_PER_STAT:
                continue
            out[(nature, points)] = out.get((nature, points), 0.0) + weight
        return _normalise(out)


def cache_path():
    return STATS_DIR / STATS_MONTH / f"chaos-{STATS_CUTOFF}.json"


def fetch(force: bool = False) -> tuple[Usage, bool]:
    """Return (usage, from_cache).

    Never silently serves stale data: the caller records `from_cache` on the
    run row so a result set can always be traced to the stats behind it.
    """
    path = cache_path()
    if path.exists() and not force:
        return Usage(json.loads(path.read_text())), True
    try:
        raw = json.loads(urllib.request.urlopen(STATS_URL, timeout=120).read())
    except Exception as exc:
        if path.exists():
            return Usage(json.loads(path.read_text())), True
        raise RuntimeError(f"fetch failed and no cache at {path}: {exc}") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw))
    return Usage(raw), False
