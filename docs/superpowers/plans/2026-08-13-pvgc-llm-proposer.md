# PVGC LLM Proposer Implementation Plan (M5)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the search loop — an LLM proposes Reg M-B teams grounded entirely in fetched data, the existing gauntlet scores them, and results feed the next round with a falsifiable hypothesis attached to every candidate.

**Architecture:** `propose.py` builds a prompt from `usage.py` + `dex.py` + `store.py`, calls Claude with a Pydantic output schema, and hands the resulting paste to the existing `team.validate()` gate before it ever reaches `score_team()`. The proposer sits strictly above the M4 seam and imports nothing from `sim.py`.

**Tech Stack:** Python 3.12, `anthropic` SDK, `claude-opus-5`, Pydantic, plus the existing M0–M4 stack.

**Spec:** `docs/superpowers/specs/2026-08-11-pvgc-champions-team-search-design.md` (§ LLM proposer)
**Depends on:** M0–M4, merged from `feat/harness-gauntlet` (PR #1)

---

## Provider decision

The spec says "anthropic and/or google-genai" and the original ask said "gemini or claude". **This plan implements Claude only**, behind a one-function seam (`propose._complete`). Rationale: two half-integrated providers is worse than one working one, and the swap is a single function. Adding Gemini later means implementing `_complete` against `google-genai` and nothing else.

`ANTHROPIC_API_KEY` is not currently set in the environment — Task 1 Step 3 checks for it and fails with a clear message rather than at the first API call.

## Why the model does not know this format

Reg M-B went live 2026-06-17 and 11 of its Megas exist in no mainline game. **Anything the model recalls about this metagame is reconstruction.** Every fact in the prompt comes from `data/champions_dex.json` or the Smogon chaos JSON. This is not a stylistic choice — it is the reason `context.py` exists as a separate module with its own tests.

---

## File Structure

| File | Responsibility |
|---|---|
| `pvgc/config.py` (modify) | model id, effort, proposal counts |
| `pvgc/context.py` | build the grounded prompt blocks (stable + volatile) |
| `pvgc/propose.py` | Pydantic schema, Claude call, validate/repair loop |
| `pvgc/store.py` (modify) | query prior results for the feedback block |
| `pvgc/cli.py` (modify) | `pvgc propose` subcommand |
| `tests/test_context.py`, `tests/test_propose.py` | |

`context.py` is split from `propose.py` because prompt construction is pure, deterministic, and testable without an API key — and it is where the grounding guarantee lives.

---

## Task 1: Dependencies and config

**Files:**
- Modify: `pyproject.toml`, `pvgc/config.py`

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`, change the `dependencies` list to:

```toml
dependencies = [
    "poke-env==0.15.0",
    "anthropic>=0.116.0",
]
```

- [ ] **Step 2: Add proposer constants**

Append to `pvgc/config.py`:

```python
# --- LLM proposer (M5) -------------------------------------------------

# Reg M-B post-dates every model's training cutoff, so the proposer is
# grounded entirely in fetched data. See pvgc/context.py.
LLM_MODEL = "claude-opus-5"
LLM_EFFORT = "high"          # low | medium | high | xhigh | max
LLM_MAX_TOKENS = 16000       # non-streaming; keeps us under SDK HTTP timeouts
LLM_MAX_REPAIRS = 1          # one repair attempt, then discard

TOP_SPECIES_IN_PROMPT = 40
CANDIDATES_PER_ROUND = 4
```

- [ ] **Step 3: Install and verify credentials**

```bash
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -c "
import os, anthropic
print('anthropic', anthropic.__version__)
key = os.environ.get('ANTHROPIC_API_KEY')
print('ANTHROPIC_API_KEY:', 'set' if key else 'MISSING')
"
```

Expected: a version line, and `set`. If it prints `MISSING`, export the key (or run `ant auth login`, which the SDK also reads) before Task 5 — Tasks 2–4 do not need it.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml pvgc/config.py
git commit -m "chore: add anthropic dependency and proposer config"
```

---

## Task 2: Grounded prompt context

Pure functions. No API key, no network — every input comes from files already on disk.

**Files:**
- Create: `pvgc/context.py`, `tests/test_context.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_context.py`:

```python
import json
from pathlib import Path

import pytest

from pvgc.context import (
    format_mega_table,
    format_prior_results,
    format_rules,
    format_usage_priors,
    stable_prompt,
)
from pvgc.usage import Usage

FIXTURE = Path(__file__).parent / "fixtures" / "chaos_sample.json"


@pytest.fixture
def usage():
    return Usage(json.loads(FIXTURE.read_text()))


def test_usage_priors_include_top_species(usage):
    text = format_usage_priors(usage, n=5)
    for name in usage.top_species(5):
        assert name in text


def test_usage_priors_include_distributions(usage):
    text = format_usage_priors(usage, n=3)
    top = usage.top_species(1)[0]
    best_item = max(usage.items(top), key=usage.items(top).get)
    assert best_item in text
    assert "%" in text


def test_usage_priors_are_deterministic(usage):
    assert format_usage_priors(usage, n=5) == format_usage_priors(usage, n=5)


def test_mega_table_lists_stats_the_model_cannot_know():
    text = format_mega_table()
    assert "Charizard-Mega-Y" in text
    assert "Charizardite Y" in text
    # Post-Mega base stats must be present, not left to recall.
    assert "159" in text or "Drought" in text


def test_rules_state_champions_stat_points_not_evs():
    text = format_rules()
    assert "66" in text
    assert "32" in text
    assert "508" not in text, "mainline EV limits must not appear"
    assert "4" in text and "6" in text  # bring 4 of 6


def test_prior_results_empty_is_explicit():
    text = format_prior_results([])
    assert "no prior results" in text.lower()


def test_prior_results_include_intervals():
    rows = [
        {"hash": "abc123", "paste": "Garchomp @ Life Orb\n",
         "overall": 0.58, "lo": 0.44, "hi": 0.71, "train": 0.66,
         "holdout": 0.50, "n": 800, "hypothesis": "Sand core beats rain"},
    ]
    text = format_prior_results(rows)
    assert "58" in text
    assert "44" in text and "71" in text
    assert "Sand core beats rain" in text


def test_stable_prompt_has_no_volatile_content(usage):
    """The stable block is the cache prefix — it must not contain results,
    timestamps, or anything that changes between proposals."""
    a = stable_prompt(usage)
    b = stable_prompt(usage)
    assert a == b
    assert "winrate" not in a.lower()
    assert "2026-08" not in a  # no timestamps
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_context.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pvgc.context'`

- [ ] **Step 3: Implement `pvgc/context.py`**

```python
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
    lines = ["# Mega Evolutions legal in Reg M-B", ""]
    lines.append("| Mega forme | Stone | Types | HP/Atk/Def/SpA/SpD/Spe | Ability |")
    lines.append("|---|---|---|---|---|")
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


def _top(dist: dict, n: int) -> str:
    items = sorted(dist.items(), key=lambda kv: -kv[1])[:n]
    return ", ".join(f"{k} {v:.0%}" for k, v in items)


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
        lines.append(f"- Items: {_top(usage.items(name), 4)}")
        lines.append(f"- Abilities: {_top(usage.abilities(name), 2)}")
        lines.append(f"- Moves: {_top(usage.moves(name), 8)}")
        lines.append(f"- Common spread: {spread_text}")
        lines.append(f"- Frequent teammates: {_top(usage.teammates(name), 6)}")
        lines.append("")
    return "\n".join(lines)


def format_prior_results(rows: list[dict]) -> str:
    """Scored candidates from this run. Every rate carries its interval —
    a bare percentage invites confident narration of noise."""
    if not rows:
        return (
            "# Prior results\n\nNo prior results yet — this is the first round.\n"
        )
    lines = ["# Prior results (train split only)", ""]
    lines.append("| team | overall | 95% CI | train | holdout | n | hypothesis |")
    lines.append("|---|---|---|---|---|---|---|")
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
    return "\n\n".join([format_rules(), format_mega_table(), format_usage_priors(usage)])
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_context.py -q`
Expected: 9 passed

- [ ] **Step 5: Eyeball the real prompt and check its size**

```bash
.venv/bin/python -c "
from pvgc.context import stable_prompt
from pvgc.usage import fetch
text = stable_prompt(fetch()[0])
print(text[:1500])
print('...')
print('CHARS:', len(text))
"
```

Expected: readable Markdown, roughly 15–40K characters. If it exceeds ~120K characters, lower `TOP_SPECIES_IN_PROMPT`. Note the size — Task 3 caches this block, and the minimum cacheable prefix on `claude-opus-5` is 512 tokens, which this comfortably exceeds.

- [ ] **Step 6: Commit**

```bash
git add pvgc/context.py tests/test_context.py
git commit -m "feat: build grounded prompt context from dex and usage data"
```

---

## Task 3: Store queries for the feedback block

**Files:**
- Modify: `pvgc/store.py`
- Modify: `tests/test_store.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_store.py`:

```python
def test_scored_candidates_returns_ranked_rows(store, team):
    tid = store.add_team(team, role="candidate", source="llm",
                         meta={"hypothesis": "Sand beats rain"})
    oid = store.add_team(_team("g"), role="gauntlet", source="usage")
    run_id = store.start_run(gauntlet_hash="abc", n_battles=10, from_cache=True)
    store.add_matchup(run_id, tid, oid, n=10, wins=7, losses=3)

    rows = store.scored_candidates(run_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["hash"] == team.hash()
    assert row["n"] == 10
    assert row["overall"] == pytest.approx(0.7)
    assert row["lo"] < 0.7 < row["hi"]
    assert row["hypothesis"] == "Sand beats rain"


def test_scored_candidates_ranks_by_winrate(store):
    a, b = _team("a"), _team("b")
    aid = store.add_team(a, role="candidate", source="llm")
    bid = store.add_team(b, role="candidate", source="llm")
    oid = store.add_team(_team("g"), role="gauntlet", source="usage")
    run_id = store.start_run(gauntlet_hash="abc", n_battles=10, from_cache=True)
    store.add_matchup(run_id, aid, oid, n=10, wins=2, losses=8)
    store.add_matchup(run_id, bid, oid, n=10, wins=9, losses=1)

    rows = store.scored_candidates(run_id)
    assert [r["hash"] for r in rows] == [b.hash(), a.hash()]


def test_scored_candidates_empty_run(store):
    run_id = store.start_run(gauntlet_hash="abc", n_battles=10, from_cache=True)
    assert store.scored_candidates(run_id) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_store.py -q`
Expected: FAIL — `AttributeError: 'Store' object has no attribute 'scored_candidates'`

- [ ] **Step 3: Implement `scored_candidates`**

Add to the `Store` class in `pvgc/store.py`:

```python
    def scored_candidates(self, run_id: int, limit: int = 20) -> list[dict]:
        """Candidates scored in this run, best first, with Wilson intervals.

        Rates are pooled across matchups rather than averaged, matching
        GauntletResult.overall_winrate.
        """
        from pvgc.score import wilson_interval

        rows = self.conn.execute(
            "SELECT t.hash AS hash, t.paste AS paste, t.meta_json AS meta_json,"
            "       SUM(m.n) AS n, SUM(m.wins) AS wins"
            "  FROM matchup m JOIN team t ON t.id = m.candidate_id"
            " WHERE m.run_id = ? GROUP BY t.id",
            (run_id,),
        ).fetchall()

        out = []
        for r in rows:
            n, wins = r["n"] or 0, r["wins"] or 0
            lo, hi = wilson_interval(wins, n)
            meta = json.loads(r["meta_json"])
            out.append({
                "hash": r["hash"],
                "paste": r["paste"],
                "n": n,
                "overall": wins / n if n else 0.0,
                "lo": lo,
                "hi": hi,
                # train/holdout are filled by the caller, which knows the split
                "train": wins / n if n else 0.0,
                "holdout": 0.0,
                "hypothesis": meta.get("hypothesis", ""),
            })
        out.sort(key=lambda d: -d["overall"])
        return out[:limit]
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_store.py -q`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add pvgc/store.py tests/test_store.py
git commit -m "feat: query scored candidates with intervals for proposer feedback"
```

---

## Task 4: The proposer

**Files:**
- Create: `pvgc/propose.py`, `tests/test_propose.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_propose.py`:

```python
import json
from pathlib import Path

import pytest

from pvgc.propose import (
    Proposal,
    ProposalBatch,
    build_messages,
    parse_and_validate,
)
from pvgc.usage import Usage

FIXTURE = Path(__file__).parent / "fixtures" / "chaos_sample.json"
SPIKE = Path(__file__).parent / "fixtures" / "spike_team.txt"


@pytest.fixture
def usage():
    return Usage(json.loads(FIXTURE.read_text()))


def test_build_messages_marks_cache_breakpoint(usage):
    system, messages = build_messages(usage, prior_rows=[], mode="seed", k=3)
    assert isinstance(system, list)
    assert any("cache_control" in block for block in system)


def test_build_messages_puts_volatile_content_after_cache(usage):
    """Results must come after the cached prefix or every round pays a
    full cache write."""
    rows = [{"hash": "abc", "paste": "", "overall": 0.5, "lo": 0.4,
             "hi": 0.6, "train": 0.5, "holdout": 0.5, "n": 100,
             "hypothesis": "x"}]
    system, messages = build_messages(usage, prior_rows=rows, mode="seed", k=3)
    system_text = " ".join(b["text"] for b in system)
    user_text = " ".join(
        c["text"] for m in messages for c in m["content"] if c["type"] == "text"
    )
    assert "abc" not in system_text
    assert "abc" in user_text


def test_build_messages_includes_mode_and_count(usage):
    _, messages = build_messages(usage, prior_rows=[], mode="mutate", k=5)
    text = " ".join(
        c["text"] for m in messages for c in m["content"] if c["type"] == "text"
    )
    assert "5" in text
    assert "mutate" in text.lower()


def test_build_messages_rejects_unknown_mode(usage):
    with pytest.raises(ValueError):
        build_messages(usage, prior_rows=[], mode="freestyle", k=3)


def test_parse_and_validate_accepts_legal_team():
    paste = SPIKE.read_text()
    batch = ProposalBatch(proposals=[
        Proposal(paste=paste, hypothesis="test", changed_from=None)
    ])
    accepted, rejected = parse_and_validate(batch)
    assert len(accepted) == 1
    assert rejected == []
    assert accepted[0][1].hypothesis == "test"


def test_parse_and_validate_rejects_illegal_team():
    batch = ProposalBatch(proposals=[
        Proposal(paste="Notarealmon @ Fake Item\nLevel: 50\n- Tackle\n",
                 hypothesis="bad", changed_from=None)
    ])
    accepted, rejected = parse_and_validate(batch)
    assert accepted == []
    assert len(rejected) == 1
    proposal, errors = rejected[0]
    assert errors
    assert proposal.hypothesis == "bad"


def test_parse_and_validate_rejects_mainline_ev_spread():
    """The most likely model error: writing a 4/252/252 spread from memory."""
    paste = SPIKE.read_text().replace(
        "EVs: 2 HP / 32 Atk / 32 Spe", "EVs: 4 HP / 252 Atk / 252 Spe"
    )
    batch = ProposalBatch(proposals=[
        Proposal(paste=paste, hypothesis="mainline evs", changed_from=None)
    ])
    accepted, rejected = parse_and_validate(batch)
    assert accepted == []
    assert any("stat point" in e.lower() for _, errs in rejected for e in errs)


def test_parse_and_validate_partitions_mixed_batch():
    batch = ProposalBatch(proposals=[
        Proposal(paste=SPIKE.read_text(), hypothesis="good", changed_from=None),
        Proposal(paste="Notarealmon\nLevel: 50\n- Tackle\n",
                 hypothesis="bad", changed_from=None),
    ])
    accepted, rejected = parse_and_validate(batch)
    assert len(accepted) == 1 and len(rejected) == 1


def test_proposal_schema_requires_hypothesis():
    with pytest.raises(Exception):
        Proposal(paste="x")


def test_repair_message_names_the_errors():
    from pvgc.propose import build_repair_message

    proposal = Proposal(paste="bad", hypothesis="h", changed_from=None)
    msg = build_repair_message([(proposal, ["item clause: duplicate items"])])
    text = " ".join(c["text"] for c in msg["content"])
    assert "item clause" in text
    assert msg["role"] == "user"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_propose.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pvgc.propose'`

- [ ] **Step 3: Implement `pvgc/propose.py`**

```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_propose.py -q`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add pvgc/propose.py tests/test_propose.py
git commit -m "feat: add grounded LLM proposer with validation gate"
```

---

## Task 5: First real API call

The first task that needs `ANTHROPIC_API_KEY`. Cheap and bounded — one call, `k=2`.

**Files:**
- Modify: `tests/test_propose.py`

- [ ] **Step 1: Add the slow test**

Append to `tests/test_propose.py`:

```python
@pytest.mark.slow
def test_live_proposal_returns_legal_teams():
    """One real API call. Costs a few cents; needs ANTHROPIC_API_KEY."""
    import os

    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")

    from pvgc.propose import propose
    from pvgc.usage import fetch

    real_usage, _ = fetch()
    accepted, rejected = propose(real_usage, prior_rows=[], mode="seed", k=2)

    print(f"\naccepted={len(accepted)} rejected={len(rejected)}")
    for _, proposal in accepted:
        print("HYPOTHESIS:", proposal.hypothesis)
    for proposal, errors in rejected:
        print("REJECTED:", errors[:3])

    assert accepted, f"no legal teams; errors: {[e for _, e in rejected]}"
    for team, proposal in accepted:
        assert team.validate() == []
        assert len(team.mons) == 6
        assert proposal.hypothesis.strip()
```

- [ ] **Step 2: Run it**

Run: `.venv/bin/pytest tests/test_propose.py -q -m slow -s`
Expected: 1 passed, with printed hypotheses.

**This is the moment of truth for the grounding design.** Read the output:

- If teams come back legal, the grounding works.
- If they are rejected for stat-point violations, the rules block is not prominent enough — move `format_rules()` after the usage priors so it is the last thing before the task.
- If they are rejected for unknown species or items, the model is recalling rather than reading — strengthen the "if it is not listed, it does not exist" line.
- **Record the rejection reasons.** The failure log is the primary signal about what the model gets wrong, and it feeds Task 7.

- [ ] **Step 3: Verify the cache is working**

```bash
.venv/bin/python -c "
from pvgc.context import stable_prompt
from pvgc.propose import build_messages, _client
from pvgc.usage import fetch
u, _ = fetch()
system, messages = build_messages(u, [], 'seed', 2)
for i in range(2):
    r = _client().messages.create(
        model='claude-opus-5', max_tokens=64,
        system=system, messages=messages)
    print(i, 'write:', r.usage.cache_creation_input_tokens,
             'read:', r.usage.cache_read_input_tokens)
"
```

Expected: the first call shows a non-zero `write`, the second a non-zero `read`. If `read` is 0 on the second call, something volatile leaked into the stable block — diff `stable_prompt()` across two invocations.

- [ ] **Step 4: Commit**

```bash
git add tests/test_propose.py
git commit -m "test: add live proposal smoke test"
```

---

## Task 6: CLI search loop

**Files:**
- Modify: `pvgc/cli.py`

- [ ] **Step 1: Implement `cmd_propose`**

Add to `pvgc/cli.py`:

```python
def cmd_propose(args) -> int:
    """Propose -> score -> feed back. The M5 loop."""
    import asyncio

    from pvgc.propose import propose
    from pvgc.score import score_team
    from pvgc.sim import ShowdownServer

    gauntlet, from_cache = _load_gauntlet(args.size)
    holdout = _holdout_ids(args.size)
    usage, _ = fetch()

    store = Store()
    gauntlet_hash = "".join(t.hash()[:4] for t in gauntlet)
    run_id = store.start_run(
        gauntlet_hash=gauntlet_hash, n_battles=args.n, from_cache=from_cache,
        notes=f"propose x{args.rounds}",
    )
    print(f"run {run_id}: {args.rounds} rounds x {args.k} candidates, "
          f"{args.n} battles per matchup", file=sys.stderr)

    with ShowdownServer(port=args.port):
        for rnd in range(args.rounds):
            # The proposer sees train results only. Holdout stays unseen so
            # the overfit gap means something.
            prior = store.scored_candidates(run_id)
            mode = args.mode if rnd == 0 else "mutate"
            print(f"\n=== round {rnd + 1}/{args.rounds} ({mode}) ===",
                  file=sys.stderr)

            accepted, rejected = propose(usage, prior, mode=mode, k=args.k)
            for proposal, errors in rejected:
                print(f"  discarded: {errors[0]}", file=sys.stderr)

            for team, proposal in accepted:
                candidate_id = store.add_team(
                    team, role="candidate", source="llm",
                    meta={"hypothesis": proposal.hypothesis,
                          "changed_from": proposal.changed_from,
                          "round": rnd},
                )
                result = asyncio.run(
                    score_team(team, gauntlet, holdout, n=args.n)
                )
                for m in result.matchups:
                    opponent_id = store.add_team(
                        gauntlet[m.opponent_id], role="gauntlet", source="usage"
                    )
                    matchup_id = store.add_matchup(
                        run_id, candidate_id, opponent_id, n=m.n,
                        wins=m.wins, losses=m.losses, failed=m.failed,
                    )
                    for winner, turns, bring_a, bring_b in m.battles:
                        store.add_battle(
                            matchup_id, seed=None, winner=winner, turns=turns,
                            bring_a=list(bring_a or []),
                            bring_b=list(bring_b or []),
                        )
                lo, hi = result.overall_interval
                print(f"  {result.candidate_hash}  "
                      f"{result.overall_winrate:6.1%} [{lo:.1%}, {hi:.1%}]  "
                      f"gap {result.overfit_gap:+.1%}  "
                      f"| {proposal.hypothesis[:60]}")

    print(f"\n=== run {run_id} leaderboard ===")
    print(f"{'team':>17}  {'overall':>8}  {'95% CI':>18}  {'n':>5}  hypothesis")
    for r in store.scored_candidates(run_id):
        print(f"{r['hash']:>17}  {r['overall']:7.1%}  "
              f"[{r['lo']:6.1%},{r['hi']:6.1%}]  {r['n']:5d}  "
              f"{r['hypothesis'][:60]}")
    return 0
```

- [ ] **Step 2: Register the subcommand**

In `main()`, after the `score` parser:

```python
    p_prop = sub.add_parser("propose", help="run the LLM search loop")
    p_prop.add_argument("--rounds", type=int, default=3)
    p_prop.add_argument("-k", type=int, default=CANDIDATES_PER_ROUND)
    p_prop.add_argument("-n", type=int, default=BATTLES_PER_MATCHUP)
    p_prop.add_argument("--mode", choices=["seed", "probe"], default="seed")
    p_prop.add_argument("--port", type=int, default=8000)
```

Add `"propose": cmd_propose` to the dispatch dict, and add `CANDIDATES_PER_ROUND` to the `pvgc.config` import at the top of the file.

- [ ] **Step 3: Run a small loop**

```bash
.venv/bin/pvgc --size 8 propose --rounds 2 -k 2 -n 6
```

Expected: two rounds, each proposing 2 teams, scoring each against 8 opponents, then a leaderboard with hypotheses attached. Budget roughly `rounds x k x size x n` battles — here 192, a few minutes.

- [ ] **Step 4: Commit**

```bash
git add pvgc/cli.py
git commit -m "feat: add propose subcommand running the search loop"
```

---

## Task 7: README and honest limits

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Document the loop**

Add to `README.md` after the `## Use` section:

```markdown
### Search loop

    export ANTHROPIC_API_KEY=...
    .venv/bin/pvgc propose --rounds 5 -k 4

An LLM proposes teams, the gauntlet scores them, results feed the next round.
Each team carries a falsifiable hypothesis, stored beside its result — the
point is testing strategic ideas, not just ranking teams.

**The model does not know this format.** Reg M-B post-dates every training
cutoff and several of its Megas exist in no mainline game, so every fact in
the prompt is read from `data/champions_dex.json` and the Smogon statistics.
Nothing is recalled. Illegal teams are rejected by the same validator the
`score` command uses, with one repair attempt before being discarded.

Budget roughly `rounds x k x gauntlet_size x n` battles.
```

Add to `## Limitations`:

```markdown
- **The search optimises against the heuristic agent.** A better team here
  means a team that beats the gauntlet when played by `SimpleHeuristicsPlayer`.
  Watch the overfit gap, and treat a strong holdout score as the real signal.
- **Hypotheses are the model's, and the verdict is only as good as the
  fitness function.** A confirmed hypothesis means the matchup table agreed,
  not that the claim is true of the format.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: document the search loop and its limits"
```

---

## Self-Review

**Spec coverage (§ LLM proposer):**

| Spec requirement | Task |
|---|---|
| Top ~40 species with usage, items, abilities, moves, spreads, teammates | 2 |
| Mega-capable species with post-Mega stats | 2 |
| Reg M-B rules injected, not assumed | 2 |
| Prior results with per-matchup CIs | 2, 3 |
| Output contract `{paste, hypothesis, changed_from}` | 4 |
| Hypothesis stored on `team.meta_json` | 6 |
| Hard validation gate, one repair, then discard | 4 |
| Modes: seed / mutate / probe | 4, 6 |
| Proposer sees train results only | 6 |
| No agent framework, no tool loop, no vector store | 4 |

**Deliberately not built:** adaptive battle allocation and paired seeds remain upgrade paths from the M4 spec, not v1. Gemini is a seam (`_complete`), not an implementation — stated in the header.

**Type consistency:** `Proposal(paste, hypothesis, changed_from)` and `ProposalBatch(proposals)` match across Tasks 4–6. `build_messages(usage, prior_rows, mode, k) -> (system, messages)` matches its two call sites. `store.scored_candidates(run_id) -> list[dict]` keys (`hash`, `overall`, `lo`, `hi`, `train`, `holdout`, `n`, `hypothesis`) match `format_prior_results()` in Task 2 and the leaderboard in Task 6. `MatchupResult.battles` tuples match the M4 definition.

**Known gaps carried into execution:**

1. `scored_candidates` reports `train` equal to `overall` and `holdout` as `0.0` — the store does not know the holdout split, which lives in the CLI. The proposer sees an honest overall figure and the CLI prints the true gap from `GauntletResult`. If the split matters inside the prompt, pass `holdout_ids` into `scored_candidates` and partition there.
2. Task 5 Step 2 is a genuine decision point, not a formality. If the model returns illegal teams, the fix is in the prompt, and the specific rejection reasons say which part.
3. `messages.parse()` with `output_config` and a cached system block is the shape documented for `claude-opus-5`; if the SDK rejects the combination, drop `output_config` first (effort defaults to `high`, which is what we want anyway).
