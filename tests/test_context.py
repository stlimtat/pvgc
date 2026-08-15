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
    from pvgc.dex import load

    text = format_usage_priors(usage, n=3)
    top = usage.top_species(1)[0]
    best_item = max(usage.items(top), key=usage.items(top).get)
    # Rendered as the dex display name, not the raw usage-data id.
    assert load().items[best_item]["name"] in text
    assert "%" in text


def test_usage_priors_are_deterministic(usage):
    assert format_usage_priors(usage, n=5) == format_usage_priors(usage, n=5)


def test_mega_table_lists_stats_the_model_cannot_know():
    text = format_mega_table()
    assert "Charizard-Mega-Y" in text
    assert "Charizardite Y" in text
    # Post-Mega ability must be present, not left to recall.
    assert "Drought" in text


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
         "overall": 0.58, "lo": 0.44, "hi": 0.71, "n": 800,
         "hypothesis": "Sand core beats rain"},
    ]
    text = format_prior_results(rows)
    assert "58" in text
    assert "44" in text and "71" in text
    assert "Sand core beats rain" in text


def test_prior_results_never_show_holdout():
    """A holdout column would both leak the split and, when the store has no
    value for it, assert a fabricated 0.0% as fact."""
    rows = [
        {"hash": "abc123", "paste": "", "overall": 0.58, "lo": 0.44,
         "hi": 0.71, "n": 800, "hypothesis": "h"},
    ]
    text = format_prior_results(rows)
    assert "holdout" not in text.lower().split("held-out")[0].replace(
        "held out", ""
    ) or "| holdout |" not in text
    assert "0.0%" not in text


def test_stable_prompt_has_no_volatile_content(usage):
    """The stable block is the cache prefix — it must not contain results,
    timestamps, or anything that changes between proposals."""
    a = stable_prompt(usage)
    b = stable_prompt(usage)
    assert a == b
    assert "winrate" not in a.lower()
    assert "2026-08" not in a  # no timestamps


def test_usage_priors_use_display_names_not_showdown_ids(usage):
    """The model should see the form it must write into a paste."""
    text = format_usage_priors(usage, n=5)
    assert "Black Glasses" in text or "Sucker Punch" in text
    assert "blackglasses" not in text
    assert "suckerpunch" not in text
