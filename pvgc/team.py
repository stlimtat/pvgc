"""Team model and Showdown paste serialisation.

Champions uses Stat Points (66 total, 32 per stat) rather than mainline EVs,
but the paste format still spells them `EVs:` because Showdown's importer
reads that line. The field keeps the `evs` name to match the wire format;
the limits in `validate()` are the Champions ones.
"""
import hashlib
import json
from dataclasses import dataclass, field

from pvgc.config import (
    LEVEL,
    MAX_STAT_POINTS_PER_STAT,
    MAX_STAT_POINTS_TOTAL,
    TEAM_SIZE,
)

STAT_KEYS = {
    "HP": "hp", "Atk": "atk", "Def": "def",
    "SpA": "spa", "SpD": "spd", "Spe": "spe",
}
STAT_ORDER = ["hp", "atk", "def", "spa", "spd", "spe"]
STAT_LABELS = {v: k for k, v in STAT_KEYS.items()}


@dataclass
class Mon:
    species: str
    item: str
    ability: str
    nature: str
    evs: dict[str, int] = field(default_factory=dict)
    ivs: dict[str, int] = field(default_factory=dict)
    moves: list[str] = field(default_factory=list)
    level: int = LEVEL

    def canonical(self) -> tuple:
        return (
            self.species,
            self.item,
            self.ability,
            self.nature,
            tuple(sorted(self.evs.items())),
            tuple(sorted(self.moves)),
        )


@dataclass
class Team:
    mons: list[Mon]

    @classmethod
    def from_paste(cls, paste: str) -> "Team":
        blocks = [b for b in paste.strip().split("\n\n") if b.strip()]
        return cls(mons=[_parse_mon(b) for b in blocks])

    def to_paste(self) -> str:
        return "\n\n".join(_render_mon(m) for m in self.mons) + "\n"

    def hash(self) -> str:
        """Canonical identity: order-independent, so a re-proposed team
        dedupes instead of being re-scored."""
        payload = json.dumps(sorted(m.canonical() for m in self.mons), default=list)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def __eq__(self, other) -> bool:
        return isinstance(other, Team) and sorted(
            m.canonical() for m in self.mons
        ) == sorted(m.canonical() for m in other.mons)

    def validate(self) -> list[str]:
        """Return every rule violation. Empty list means legal.

        Returns all errors rather than raising on the first so an LLM repair
        prompt can see every problem at once. A team that fails here must
        never reach the simulator: Showdown rejecting a team mid-gauntlet
        corrupts the run silently.
        """
        from pvgc.dex import load, to_id

        dex = load()
        errors: list[str] = []

        if len(self.mons) != TEAM_SIZE:
            errors.append(f"team size: expected {TEAM_SIZE}, got {len(self.mons)}")

        species_ids = [to_id(m.species) for m in self.mons]
        if len(set(species_ids)) != len(species_ids):
            errors.append("species clause: duplicate species on team")

        items = [to_id(m.item) for m in self.mons if m.item]
        if len(set(items)) != len(items):
            errors.append("item clause: duplicate items on team")

        for m in self.mons:
            sid = to_id(m.species)
            if sid not in dex.species:
                errors.append(f"species: {m.species!r} not in Reg M-B dex")
                continue

            if m.item and to_id(m.item) not in dex.items:
                errors.append(f"item: {m.item!r} not in Reg M-B item pool")

            if m.ability and to_id(m.ability) not in dex.abilities:
                errors.append(f"ability: {m.ability!r} not in Reg M-B dex")

            if m.nature and to_id(m.nature) not in dex.natures:
                errors.append(f"nature: {m.nature!r} does not exist")

            total = sum(m.evs.values())
            if total > MAX_STAT_POINTS_TOTAL:
                errors.append(
                    f"stat point total: {m.species} has {total} > {MAX_STAT_POINTS_TOTAL}"
                )
            for stat, val in m.evs.items():
                if val > MAX_STAT_POINTS_PER_STAT:
                    errors.append(
                        f"per-stat stat points: {m.species} {stat}={val} > "
                        f"{MAX_STAT_POINTS_PER_STAT}"
                    )

            if not 1 <= len(m.moves) <= 4:
                errors.append(f"moves: {m.species} has {len(m.moves)}, expected 1-4")
            if len({to_id(mv) for mv in m.moves}) != len(m.moves):
                errors.append(f"moves: {m.species} has duplicate moves")

            legal = dex.learnset(m.species) if sid in dex.learnsets else set()
            for mv in m.moves:
                if to_id(mv) not in dex.moves:
                    errors.append(f"move: {mv!r} not in Reg M-B move pool")
                elif legal and to_id(mv) not in legal:
                    errors.append(f"move: {m.species} cannot learn {mv!r}")

            if m.level != LEVEL:
                errors.append(f"level: {m.species} is {m.level}, expected {LEVEL}")

        return errors


def _parse_evs(value: str) -> dict[str, int]:
    out = {}
    for part in value.split("/"):
        part = part.strip()
        if not part:
            continue
        amount, label = part.split(maxsplit=1)
        out[STAT_KEYS[label.strip()]] = int(amount)
    return out


def _parse_mon(block: str) -> Mon:
    lines = [line.strip() for line in block.strip().splitlines() if line.strip()]
    header = lines[0]
    species, item = header.split(" @ ", 1) if " @ " in header else (header, "")
    mon = Mon(species=species.strip(), item=item.strip(), ability="", nature="")
    for line in lines[1:]:
        if line.startswith("- "):
            mon.moves.append(line[2:].strip())
        elif line.startswith("Ability: "):
            mon.ability = line[len("Ability: "):].strip()
        elif line.startswith("Level: "):
            mon.level = int(line[len("Level: "):].strip())
        elif line.startswith("EVs: "):
            mon.evs = _parse_evs(line[len("EVs: "):])
        elif line.startswith("IVs: "):
            mon.ivs = _parse_evs(line[len("IVs: "):])
        elif line.endswith(" Nature"):
            mon.nature = line[: -len(" Nature")].strip()
    return mon


def _render_stats(stats: dict[str, int]) -> str:
    return " / ".join(f"{stats[k]} {STAT_LABELS[k]}" for k in STAT_ORDER if stats.get(k))


def _render_mon(m: Mon) -> str:
    lines = [f"{m.species} @ {m.item}" if m.item else m.species]
    if m.ability:
        lines.append(f"Ability: {m.ability}")
    lines.append(f"Level: {m.level}")
    if m.evs:
        lines.append(f"EVs: {_render_stats(m.evs)}")
    if m.nature:
        lines.append(f"{m.nature} Nature")
    if m.ivs:
        lines.append(f"IVs: {_render_stats(m.ivs)}")
    lines.extend(f"- {mv}" for mv in m.moves)
    return "\n".join(lines)
