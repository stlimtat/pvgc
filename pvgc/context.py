"""Prompt construction for the LLM proposer.

Reg M-B went live 2026-06-17 and 11 of its Mega Evolutions exist in no
mainline game, so no model has memorised this metagame. Every fact the
proposer sees is built here from `data/champions_dex.json` and the Smogon
chaos JSON. If it is not in these blocks, it is not in the prompt.

The split matters for prompt caching: `stable_prompt()` is byte-identical
across proposals in a run and carries the cache breakpoint; results are
volatile and go after it.
"""
from pvgc.config import (
    BRING_SIZE,
    FORMAT_ID,
    MAX_STAT_POINTS_PER_STAT,
    MAX_STAT_POINTS_TOTAL,
    STATS_MONTH,
    TEAM_SIZE,
    TOP_SPECIES_IN_PROMPT,
)
from pvgc.dex import load as load_dex
from pvgc.usage import Usage


def format_rules() -> str:
    return f"""\
# Format: {FORMAT_ID} (Pokemon Champions VGC 2026 Regulation M-B)

- Double battles. Teams are {TEAM_SIZE} Pokemon; you bring {BRING_SIZE} to each battle.
- All Pokemon are auto-levelled to 50.
- Champions uses STAT POINTS, not the mainline EV system.
  Maximum {MAX_STAT_POINTS_TOTAL} total per Pokemon, maximum {MAX_STAT_POINTS_PER_STAT} in any single stat.
  A spread is written on the EVs line, e.g. "EVs: 2 HP / 32 Atk / 32 Spe" (sums to 66).
- Item Clause: no two Pokemon may hold the same item.
- Species Clause: no two Pokemon of the same species.
- Mega Evolution is triggered by holding the corresponding Mega Stone.
  Multiple stone-holders on a team are legal; only one may Mega Evolve per battle.
- There is no Terastallisation in this format.
- Open Team Sheets: both players see full teams at preview.
"""


def format_mega_table() -> str:
    """Post-Mega stats, types, and abilities. Several of these Megas exist in
    no mainline game, so this table cannot be recalled — only read."""
    dex = load_dex()
    lines = [
        "# Mega Evolutions legal in Reg M-B",
        "",
        "| Mega forme | Stone | Types | HP/Atk/Def/SpA/SpD/Spe | Ability |",
        "|---|---|---|---|---|",
    ]
    for s in sorted(dex.mega_forms(), key=lambda x: x["name"]):
        stats = s["baseStats"]
        statline = "/".join(
            str(stats[k]) for k in ("hp", "atk", "def", "spa", "spd", "spe")
        )
        abilities = "/".join(str(a) for a in s["abilities"].values())
        lines.append(
            f"| {s['name']} | {s['requiredItem'] or '-'} | {'/'.join(s['types'])} "
            f"| {statline} | {abilities} |"
        )
    return "\n".join(lines)


def _display(name: str, table: str) -> str:
    """Usage data carries Showdown ids ("blackglasses"); the dex carries
    display names ("Black Glasses"). Show the model the form it should
    actually write into a paste."""
    from pvgc.dex import to_id

    dex = load_dex()
    entry = getattr(dex, table).get(to_id(name))
    return entry["name"] if entry else name


def _top(dist: dict, n: int, table: str | None = None) -> str:
    items = sorted(dist.items(), key=lambda kv: -kv[1])[:n]
    return ", ".join(
        f"{_display(k, table) if table else k} {v:.0%}" for k, v in items
    )


def format_usage_priors(usage: Usage, n: int = TOP_SPECIES_IN_PROMPT) -> str:
    """Top species with their own weighted distributions and teammates."""
    lines = [
        f"# Usage statistics — {STATS_MONTH}, 1760+ rating, "
        f"{usage.battle_count:,} battles",
        "",
    ]
    for name in usage.top_species(n):
        spreads = usage.spreads(name)
        top_spread = max(spreads, key=spreads.get) if spreads else None
        spread_text = (
            f"{top_spread[0]} {'/'.join(str(x) for x in top_spread[1])}"
            if top_spread
            else "unknown"
        )
        lines.append(f"## {name} — {usage.usage_rate(name):.1%} usage")
        lines.append(f"- Items: {_top(usage.items(name), 4, 'items')}")
        lines.append(f"- Abilities: {_top(usage.abilities(name), 2, 'abilities')}")
        lines.append(f"- Moves: {_top(usage.moves(name), 8, 'moves')}")
        lines.append(f"- Common spread: {spread_text}")
        lines.append(f"- Frequent teammates: {_top(usage.teammates(name), 6)}")
        lines.append("")
    return "\n".join(lines)


def format_prior_results(rows: list[dict]) -> str:
    """Scored candidates from this run. Every rate carries its interval —
    a bare percentage invites confident narration of noise."""
    if not rows:
        return "# Prior results\n\nNo prior results yet — this is the first round.\n"
    lines = [
        "# Prior results (train split only)",
        "",
        "| team | overall | 95% CI | train | holdout | n | hypothesis |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['hash']} | {r['overall']:.1%} | "
            f"[{r['lo']:.1%}, {r['hi']:.1%}] | {r['train']:.1%} | "
            f"{r['holdout']:.1%} | {r['n']} | {r.get('hypothesis', '') or '-'} |"
        )
    lines.append("")
    lines.append(
        "Intervals are 95% Wilson. Two teams whose intervals overlap heavily "
        "are not distinguishable — do not treat a 2-3 point gap as a real "
        "difference."
    )
    return "\n".join(lines)


def stable_prompt(usage: Usage) -> str:
    """Rules + megas + usage priors. Byte-identical across proposals in a run,
    so it can carry the prompt-cache breakpoint."""
    return "\n\n".join(
        [format_rules(), format_mega_table(), format_usage_priors(usage)]
    )
