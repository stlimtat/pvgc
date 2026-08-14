import pytest
from poke_env.battle.move import Move
from poke_env.player import DoubleBattleOrder, SingleBattleOrder

from pvgc.sim import _apply_mega, _preview_command, _startup_line_is_ready


def _move_order(move_id: str = "earthquake") -> SingleBattleOrder:
    return SingleBattleOrder(order=Move(move_id, gen=9))


def _switch_order() -> SingleBattleOrder:
    return SingleBattleOrder(order="/choose switch Garchomp")


# --- team preview -------------------------------------------------------


def test_preview_command_is_one_indexed():
    assert _preview_command((0, 2, 3, 5)) == "/team 1346"


def test_preview_command_length():
    assert len(_preview_command((0, 1, 2, 3)).split()[1]) == 4


# --- mega injection -----------------------------------------------------


def test_mega_applied_to_first_eligible_slot():
    order = DoubleBattleOrder(_move_order(), _move_order("rockslide"))
    out = _apply_mega(order, [True, True])
    assert out.first_order.mega is True
    assert out.second_order.mega is False, "only one Mega per battle is legal"


def test_mega_skips_ineligible_first_slot():
    order = DoubleBattleOrder(_move_order(), _move_order("rockslide"))
    out = _apply_mega(order, [False, True])
    assert out.first_order.mega is False
    assert out.second_order.mega is True


def test_mega_not_applied_to_switch():
    order = DoubleBattleOrder(_switch_order(), _move_order())
    out = _apply_mega(order, [True, True])
    assert out.first_order.mega is False
    assert out.second_order.mega is True


def test_no_mega_when_unavailable():
    order = DoubleBattleOrder(_move_order(), _move_order("rockslide"))
    out = _apply_mega(order, [False, False])
    assert out.first_order.mega is False
    assert out.second_order.mega is False


def test_mega_message_contains_mega_keyword():
    order = DoubleBattleOrder(_move_order(), _move_order("rockslide"))
    assert " mega" in _apply_mega(order, [True, False]).message


def test_apply_mega_passes_through_non_double_orders():
    single = _move_order()
    assert _apply_mega(single, [True, True]) is single


# --- server startup detection -------------------------------------------


def test_startup_ignores_crash_lines():
    """The server emits non-fatal REPL CRASH lines before it is ready."""
    assert not _startup_line_is_ready("[123] CRASH: Error: listen EPERM")
    assert not _startup_line_is_ready("RESTORE CHATROOM: lobby")


def test_startup_detects_ready_line():
    assert _startup_line_is_ready("Worker 1 now listening on 0.0.0.0:8000")


# --- integration --------------------------------------------------------


@pytest.mark.slow
async def test_single_battle_completes():
    """Requires a Showdown server: cd data/pokemon-showdown && node pokemon-showdown start --no-security"""
    from pvgc.sim import run_battles
    from pvgc.teamgen import generate_gauntlet
    from pvgc.usage import fetch

    usage, _ = fetch()
    teams = generate_gauntlet(usage, n=2, seed=0)
    outcomes = await run_battles(teams[0], teams[1], n=2, tag="pvgctest")
    assert len(outcomes) == 2
    for o in outcomes:
        assert o.failed or o.winner in ("a", "b")
        if not o.failed:
            assert o.turns and o.turns > 0
            assert o.bring_a is not None and len(o.bring_a) == 4
