import json
from pathlib import Path

import pytest

from pvgc.propose import (
    Proposal,
    ProposalBatch,
    build_messages,
    build_repair_message,
    parse_and_validate,
)
from pvgc.usage import Usage

FIXTURE = Path(__file__).parent / "fixtures" / "chaos_sample.json"
SPIKE = Path(__file__).parent / "fixtures" / "spike_team.txt"


@pytest.fixture
def usage():
    return Usage(json.loads(FIXTURE.read_text()))


def _text(blocks) -> str:
    return " ".join(b["text"] for b in blocks)


def _user_text(messages) -> str:
    return " ".join(
        c["text"] for m in messages for c in m["content"] if c["type"] == "text"
    )


# --- prompt assembly ----------------------------------------------------


def test_build_messages_marks_cache_breakpoint(usage):
    system, _ = build_messages(usage, prior_rows=[], mode="seed", k=3)
    assert isinstance(system, list)
    assert any("cache_control" in block for block in system)


def test_build_messages_puts_volatile_content_after_cache(usage):
    """Results must come after the cached prefix or every round pays a
    full cache write."""
    rows = [{"hash": "abc", "paste": "", "overall": 0.5, "lo": 0.4,
             "hi": 0.6, "train": 0.5, "holdout": 0.5, "n": 100,
             "hypothesis": "x"}]
    system, messages = build_messages(usage, prior_rows=rows, mode="seed", k=3)
    assert "abc" not in _text(system)
    assert "abc" in _user_text(messages)


def test_build_messages_includes_mode_and_count(usage):
    _, messages = build_messages(usage, prior_rows=[], mode="mutate", k=5)
    text = _user_text(messages)
    assert "5" in text
    assert "mutate" in text.lower()


def test_build_messages_rejects_unknown_mode(usage):
    with pytest.raises(ValueError):
        build_messages(usage, prior_rows=[], mode="freestyle", k=3)


def test_stable_block_carries_the_breakpoint_not_the_instructions(usage):
    """The breakpoint must sit on the last stable block so tools+system
    cache together."""
    system, _ = build_messages(usage, prior_rows=[], mode="seed", k=3)
    assert "cache_control" in system[-1]
    assert "66" in system[-1]["text"]  # the stat-point rules are in there


# --- validation gate ----------------------------------------------------


def test_parse_and_validate_accepts_legal_team():
    batch = ProposalBatch(proposals=[
        Proposal(paste=SPIKE.read_text(), hypothesis="test", changed_from=None)
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
    proposal = Proposal(paste="bad", hypothesis="h", changed_from=None)
    msg = build_repair_message([(proposal, ["item clause: duplicate items"])])
    assert msg["role"] == "user"
    assert "item clause" in " ".join(c["text"] for c in msg["content"])


# --- loop control -------------------------------------------------------


def test_propose_bounds_repair_attempts(monkeypatch, usage):
    """An unbounded retry loop against a model that already has the rules
    in front of it burns tokens without converging."""
    from pvgc import propose as mod

    calls = []
    bad = ProposalBatch(proposals=[
        Proposal(paste="Notarealmon\nLevel: 50\n- Tackle\n",
                 hypothesis="always bad", changed_from=None)
    ])

    def fake_complete(system, messages):
        calls.append(messages)
        return bad

    monkeypatch.setattr(mod, "_complete", fake_complete)
    accepted, rejected = mod.propose(usage, prior_rows=[], mode="seed", k=1)

    assert accepted == []
    assert len(rejected) == 1
    assert len(calls) == 1 + mod.LLM_MAX_REPAIRS


def test_propose_stops_early_when_all_legal(monkeypatch, usage):
    from pvgc import propose as mod

    calls = []
    good = ProposalBatch(proposals=[
        Proposal(paste=SPIKE.read_text(), hypothesis="fine", changed_from=None)
    ])

    def fake_complete(system, messages):
        calls.append(messages)
        return good

    monkeypatch.setattr(mod, "_complete", fake_complete)
    accepted, rejected = mod.propose(usage, prior_rows=[], mode="seed", k=1)

    assert len(accepted) == 1
    assert rejected == []
    assert len(calls) == 1, "no repair call should be made"


# --- live smoke test ----------------------------------------------------


@pytest.mark.slow
def test_live_proposal_returns_legal_teams():
    """One real API call. Costs a few cents; needs ANTHROPIC_API_KEY.

    This is the moment the grounding design meets an actual model. Read the
    rejection reasons on failure — they say which part of the prompt is not
    landing (stat points, item pool, species pool).
    """
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
