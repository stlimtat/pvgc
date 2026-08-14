"""Generate gauntlet teams from usage statistics.

A lead is sampled by usage weight, then five more members are added by
teammate correlation rather than raw usage — that is where the synergy
signal lives. Sets are sampled from each species' own distributions.

These are statistical composites, not real tournament teams. The teammate
correlation is what keeps them from being six unrelated top-usage mons.
"""
import random

from pvgc.config import TEAM_SIZE
from pvgc.dex import load as load_dex
from pvgc.dex import to_id
from pvgc.team import Mon, Team
from pvgc.usage import Usage, spread_to_stat_points

MAX_ATTEMPTS = 60


def _weighted_choice(rng: random.Random, dist: dict, exclude=frozenset()):
    pool = {k: v for k, v in dist.items() if k not in exclude and v > 0}
    if not pool:
        return None
    keys = list(pool)
    return rng.choices(keys, weights=[pool[k] for k in keys], k=1)[0]


def _usage_dist(usage: Usage) -> dict[str, float]:
    return {s: usage.usage_rate(s) for s in usage.data}


def _pick_species(rng: random.Random, usage: Usage, chosen: list[str]) -> str | None:
    if not chosen:
        return _weighted_choice(rng, _usage_dist(usage))
    # Blend teammate correlation across everyone already on the team.
    scores: dict[str, float] = {}
    for member in chosen:
        for mate, weight in usage.teammates(member).items():
            scores[mate] = scores.get(mate, 0.0) + weight
    pick = _weighted_choice(rng, scores, exclude=set(chosen))
    if pick is not None:
        return pick
    # Correlation exhausted (thin data) — fall back to raw usage.
    return _weighted_choice(rng, _usage_dist(usage), exclude=set(chosen))


def _build_mon(
    rng: random.Random, usage: Usage, species: str, used_items: set[str]
) -> Mon | None:
    dex = load_dex()
    sid = to_id(species)
    if sid not in dex.species:
        return None

    item_dist = {
        i: w
        for i, w in usage.items(species).items()
        if to_id(i) in dex.items and to_id(i) not in used_items
    }
    item = _weighted_choice(rng, item_dist) or ""

    ability_dist = {
        a: w for a, w in usage.abilities(species).items() if to_id(a) in dex.abilities
    }
    ability = _weighted_choice(rng, ability_dist) or ""

    spread = _weighted_choice(rng, usage.spreads(species))
    if spread is None:
        nature, stat_points = "Serious", {}
    else:
        nature, points = spread
        stat_points = spread_to_stat_points(points)
    if to_id(nature) not in dex.natures:
        nature = "Serious"

    legal = dex.learnset(species) if sid in dex.learnsets else set()
    move_dist = {
        m: w
        for m, w in usage.moves(species).items()
        if to_id(m) in dex.moves and (not legal or to_id(m) in legal)
    }
    moves: list[str] = []
    while len(moves) < 4:
        pick = _weighted_choice(rng, move_dist, exclude=set(moves))
        if pick is None:
            break
        moves.append(pick)
    if not moves:
        return None

    # Usage data uses Showdown ids ("rockslide"); render display names so
    # pastes and the CLI matchup table are readable.
    return Mon(
        species=dex.species[sid]["name"],
        item=dex.items[to_id(item)]["name"] if item else "",
        ability=dex.abilities[to_id(ability)]["name"] if ability else "",
        nature=dex.natures[to_id(nature)]["name"],
        evs=stat_points,
        moves=[dex.moves[to_id(m)]["name"] for m in moves],
    )


def generate_team(usage: Usage, seed: int) -> Team:
    """Deterministic for a given seed. Raises if no legal team can be built."""
    rng = random.Random(seed)
    last_errors: list[str] = []
    for _ in range(MAX_ATTEMPTS):
        mons: list[Mon] = []
        chosen: list[str] = []
        used_items: set[str] = set()
        for _ in range(TEAM_SIZE):
            species = _pick_species(rng, usage, chosen)
            if species is None:
                break
            mon = _build_mon(rng, usage, species, used_items)
            if mon is None:
                break
            chosen.append(species)
            mons.append(mon)
            if mon.item:
                used_items.add(to_id(mon.item))
        if len(mons) != TEAM_SIZE:
            continue
        team = Team(mons=mons)
        last_errors = team.validate()
        if not last_errors:
            return team
    raise RuntimeError(
        f"could not build a legal team from seed {seed}; last errors: {last_errors}"
    )


def generate_gauntlet(usage: Usage, n: int, seed: int = 0) -> list[Team]:
    """n distinct legal teams, deterministic for a given seed."""
    teams: list[Team] = []
    seen: set[str] = set()
    s = seed
    limit = seed + n * 100
    while len(teams) < n:
        if s > limit:
            raise RuntimeError(f"only produced {len(teams)} of {n} distinct teams")
        try:
            team = generate_team(usage, seed=s)
        except RuntimeError:
            s += 1
            continue
        if team.hash() not in seen:
            seen.add(team.hash())
            teams.append(team)
        s += 1
    return teams
