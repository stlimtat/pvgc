"""Showdown server lifecycle and battle execution.

Two corrections to poke-env's baseline behaviour live here, both load-bearing
for result validity:

1. `SimpleHeuristicsPlayer.teampreview` is *random*. Left alone, the bring-4
   decision is noise and the 6-mon design space is meaningless.
2. `SimpleHeuristicsPlayer` reads `battle.can_mega_evolve` and never acts on
   it, so it never Mega Evolves. In a format built around Mega Evolution that
   makes every Mega Stone a dead item.
"""
import asyncio
import subprocess
import time
from dataclasses import dataclass

from poke_env import AccountConfiguration, LocalhostServerConfiguration
from poke_env.battle.move import Move
from poke_env.player import DoubleBattleOrder, SimpleHeuristicsPlayer

from pvgc.bring import choose_bring
from pvgc.config import BRING_SIZE, FORMAT_ID, SHOWDOWN_DIR
from pvgc.team import Mon, Team

BATTLE_TIMEOUT = 180  # a hang must not stall the whole gauntlet
STARTUP_TIMEOUT = 90
READY_MARKER = "now listening on"


def _preview_command(indices) -> str:
    """Showdown team preview is 1-indexed."""
    return "/team " + "".join(str(i + 1) for i in indices)


def _startup_line_is_ready(line: str) -> bool:
    """The server prints non-fatal REPL `CRASH:` lines (blocked unix sockets)
    before it becomes ready, so readiness must be detected positively."""
    return READY_MARKER in line


def _apply_mega(order, can_mega_evolve):
    """Set `mega` on the first slot that is both eligible and using a move.

    Only one Mega per battle is legal, so at most one slot is marked. Switches
    cannot Mega Evolve.
    """
    if not isinstance(order, DoubleBattleOrder):
        return order
    slots = [order.first_order, order.second_order]
    for i, slot in enumerate(slots):
        eligible = i < len(can_mega_evolve) and can_mega_evolve[i]
        if eligible and slot is not None and isinstance(slot.order, Move):
            slot.mega = True
            break
    return order


@dataclass
class BattleOutcome:
    winner: str | None  # "a", "b", or None when the battle failed
    turns: int | None
    bring_a: tuple[int, ...] | None
    bring_b: tuple[int, ...] | None
    failed: bool = False


class ShowdownServer:
    """Context manager for a local Showdown server."""

    def __init__(self, port: int = 8000):
        self.port = port
        self.proc: subprocess.Popen | None = None

    def __enter__(self):
        self.proc = subprocess.Popen(
            ["node", "pokemon-showdown", "start", "--no-security", str(self.port)],
            cwd=SHOWDOWN_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        deadline = time.time() + STARTUP_TIMEOUT
        while time.time() < deadline:
            line = self.proc.stdout.readline()
            if not line and self.proc.poll() is not None:
                raise RuntimeError("Showdown server exited during startup")
            if _startup_line_is_ready(line):
                return self
        self.__exit__(None, None, None)
        raise TimeoutError(f"Showdown server not ready within {STARTUP_TIMEOUT}s")

    def __exit__(self, *exc):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


class GauntletPlayer(SimpleHeuristicsPlayer):
    """Heuristic player with an explicit bring-4 policy and Mega Evolution."""

    def __init__(self, *args, our_team: Team, **kwargs):
        super().__init__(*args, **kwargs)
        self.our_team = our_team
        self.last_bring: tuple[int, ...] | None = None

    def teampreview(self, battle) -> str:
        opponent = Team(mons=[
            Mon(species=p.species, item="", ability="", nature="", evs={}, moves=[])
            for p in battle.teampreview_opponent_team
        ])
        if len(opponent.mons) < 1 or len(self.our_team.mons) < BRING_SIZE:
            picks = tuple(range(BRING_SIZE))
        else:
            picks = choose_bring(self.our_team, opponent)
        self.last_bring = picks
        # poke-env requires selected mons to be marked, or its own tracking
        # of who is on the field goes out of sync with the server.
        team_members = list(battle.team.values())
        for i in picks:
            team_members[i]._selected_in_teampreview = True
        return _preview_command(picks)

    def choose_move(self, battle):
        order = super().choose_move(battle)
        return _apply_mega(order, getattr(battle, "can_mega_evolve", []))


def _make_player(name: str, team: Team) -> GauntletPlayer:
    return GauntletPlayer(
        account_configuration=AccountConfiguration(name, None),
        server_configuration=LocalhostServerConfiguration,
        battle_format=FORMAT_ID,
        team=team.to_paste(),
        max_concurrent_battles=1,
        # Reg M-B runs Open Team Sheets, but poke-env does not opt in by
        # default. Without this the bring policy sees a partial team.
        accept_open_team_sheet=True,
        our_team=team,
    )


async def run_battles(team_a: Team, team_b: Team, n: int,
                      tag: str = "pvgc") -> list[BattleOutcome]:
    """Run n battles between two teams.

    A battle that fails is recorded with failed=True and MUST be excluded from
    the denominator by the caller — counting a crashed battle as a loss
    corrupts every downstream number invisibly.
    """
    stamp = int(time.time() * 1000) % 1_000_000
    a = _make_player(f"{tag}a{stamp}", team_a)
    b = _make_player(f"{tag}b{stamp}", team_b)

    outcomes: list[BattleOutcome] = []
    for _ in range(n):
        before = a.n_finished_battles
        try:
            await asyncio.wait_for(
                a.battle_against(b, n_battles=1), timeout=BATTLE_TIMEOUT
            )
        except Exception:
            outcomes.append(BattleOutcome(None, None, None, None, failed=True))
            continue
        if a.n_finished_battles == before:
            outcomes.append(BattleOutcome(None, None, None, None, failed=True))
            continue
        battle = list(a.battles.values())[-1]
        outcomes.append(
            BattleOutcome(
                winner="a" if battle.won else "b",
                turns=battle.turn,
                bring_a=a.last_bring,
                bring_b=b.last_bring,
            )
        )
    return outcomes
