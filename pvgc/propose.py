"""LLM team proposer.

The model does not know Reg M-B — the format post-dates every training
cutoff and 11 of its Megas exist in no mainline game. Everything factual
comes from pvgc/context.py; the model contributes reasoning over it.

Every proposal carries a falsifiable hypothesis, stored alongside the team
so that when the gauntlet scores it the claim sits next to its verdict.
"""
from pydantic import BaseModel, Field

from pvgc.config import (
    CANDIDATES_PER_ROUND,
    LLM_EFFORT,
    LLM_MAX_REPAIRS,
    LLM_MAX_TOKENS,
    LLM_MODEL,
)
from pvgc.context import format_prior_results, stable_prompt
from pvgc.team import Team
from pvgc.usage import Usage

MODES = {
    "seed": (
        "Cold start. Propose {k} DIVERSE teams drawn from the usage priors. "
        "Cover genuinely different game plans (e.g. different weather, speed "
        "control, or Mega choices) rather than {k} variations of one core."
    ),
    "mutate": (
        "Take the highest-scoring team from the prior results and propose {k} "
        "variations, each changing only 1-2 slots and holding the rest fixed. "
        "Set changed_from to that team's hash."
    ),
    "probe": (
        "Propose {k} teams that test a specific strategic hypothesis against "
        "the current metagame. State the hypothesis precisely enough that the "
        "matchup table can falsify it."
    ),
}

SYSTEM_INSTRUCTIONS = """\
You are proposing competitive teams for a Pokemon format you have never seen.

Everything you need is in this prompt. Do not rely on recalled knowledge of
Pokemon VGC: this format uses a different stat system (stat points, not EVs),
a restricted item pool, and Mega Evolutions whose stats were invented for this
game. If a species, item, move, or ability is not listed in the data below, it
does not exist here.

For each team, state a falsifiable hypothesis: a specific claim about why the
team should perform, phrased so the resulting matchup table could prove it
wrong. "This team is strong" is not falsifiable. "This Garchomp spread
outspeeds Mega Charizard Y after Tailwind" is.

Write each team as a standard Showdown paste:

    Garchomp @ Life Orb
    Ability: Rough Skin
    Level: 50
    EVs: 2 HP / 32 Atk / 32 Spe
    Jolly Nature
    - Earthquake
    - Dragon Claw
    - Protect
    - Rock Slide

Six Pokemon per team, one blank line between each.
"""


class Proposal(BaseModel):
    paste: str = Field(description="Showdown team paste, 6 Pokemon")
    hypothesis: str = Field(description="A falsifiable claim about this team")
    changed_from: str | None = Field(
        default=None, description="Hash of the parent team, for mutate mode"
    )


class ProposalBatch(BaseModel):
    proposals: list[Proposal]


def build_messages(usage: Usage, prior_rows: list[dict], mode: str, k: int):
    """Return (system_blocks, messages).

    The stable block carries the cache breakpoint; prior results are volatile
    and go in the user turn after it. Putting results in the system prompt
    would invalidate the cache on every round.
    """
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; expected one of {sorted(MODES)}")

    system = [
        {"type": "text", "text": SYSTEM_INSTRUCTIONS},
        {
            "type": "text",
            "text": stable_prompt(usage),
            "cache_control": {"type": "ephemeral"},
        },
    ]
    user_text = "\n\n".join([
        format_prior_results(prior_rows),
        f"# Task ({mode})",
        "",
        MODES[mode].format(k=k),
        "",
        f"Return exactly {k} proposals.",
    ])
    messages = [{"role": "user", "content": [{"type": "text", "text": user_text}]}]
    return system, messages


def build_repair_message(rejected: list[tuple[Proposal, list[str]]]) -> dict:
    """One repair attempt, naming every violation so the model can see them
    all at once rather than fixing them one per round trip."""
    lines = [
        "These teams are illegal in this format. Fix them and return the same "
        "number of proposals.",
        "",
    ]
    for proposal, errors in rejected:
        lines.append(f"Team (hypothesis: {proposal.hypothesis}):")
        for e in errors:
            lines.append(f"  - {e}")
        lines.append("")
    return {"role": "user", "content": [{"type": "text", "text": "\n".join(lines)}]}


def parse_and_validate(
    batch: ProposalBatch,
) -> tuple[list[tuple[Team, Proposal]], list[tuple[Proposal, list[str]]]]:
    """Partition proposals into (accepted, rejected).

    A team that fails here never reaches the simulator — Showdown rejecting a
    team mid-gauntlet corrupts the run silently.
    """
    accepted: list[tuple[Team, Proposal]] = []
    rejected: list[tuple[Proposal, list[str]]] = []
    for proposal in batch.proposals:
        try:
            team = Team.from_paste(proposal.paste)
        except Exception as exc:
            rejected.append((proposal, [f"unparseable paste: {exc}"]))
            continue
        errors = team.validate()
        if errors:
            rejected.append((proposal, errors))
        else:
            accepted.append((team, proposal))
    return accepted, rejected


def _client():
    import anthropic

    return anthropic.Anthropic()


def _complete(system, messages) -> ProposalBatch:
    """The provider seam. Swapping to Gemini means reimplementing this
    function and nothing else."""
    response = _client().messages.parse(
        model=LLM_MODEL,
        max_tokens=LLM_MAX_TOKENS,
        output_config={"effort": LLM_EFFORT},
        system=system,
        messages=messages,
        output_format=ProposalBatch,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError(f"model declined: {response.stop_details}")
    return response.parsed_output


def propose(
    usage: Usage,
    prior_rows: list[dict],
    mode: str = "seed",
    k: int = CANDIDATES_PER_ROUND,
) -> tuple[list[tuple[Team, Proposal]], list[tuple[Proposal, list[str]]]]:
    """Propose k teams. Returns (accepted, discarded).

    One repair attempt on illegal teams, then discard — an unbounded retry
    loop against a model that has the rules in front of it wastes tokens
    without converging.
    """
    system, messages = build_messages(usage, prior_rows, mode, k)
    batch = _complete(system, messages)
    accepted, rejected = parse_and_validate(batch)

    for _ in range(LLM_MAX_REPAIRS):
        if not rejected:
            break
        repair_messages = messages + [build_repair_message(rejected)]
        repaired = _complete(system, repair_messages)
        more_accepted, rejected = parse_and_validate(repaired)
        accepted.extend(more_accepted)

    return accepted, rejected
